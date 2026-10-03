import { z } from 'zod';
import { messageById, PlanItemStatus, quoteLines, ReasoningEffort, sanitizeLabel, SkillName, snippet, type EventInput } from '@desk/protocol';
import { formatThreadLine, formatThreadSummary, renderTranscript } from '../coordination/render';
import { newId } from '../ids';
import { currentReviewOf, reviewSummary } from '../reviews/reviews';
import { getAgent, getApproval, lastEvent, listThreads, pendingApprovalsFor, threadsByRef, type AgentRow } from '../state/queries';
import { openWatches } from '../watches/watches';
import { git } from '../workspaces/workspaces';
import { defineTool, type Tool, type ToolContext } from './types';

/**
 * A thread of this project by id or exact title (design spec §2.2). Refuses a reference that names no thread or
 * several live ones, and an archived thread.
 */
export function requireThread(ctx: ToolContext, ref: string): AgentRow {
  const found = threadsByRef(ctx.services.store.db, ctx.projectId, ref);
  if (found.length > 1) throw new Error(`Several threads are titled "${ref}"; use its id.`);
  const t = found[0];
  if (!t) throw new Error(`Unknown thread: ${ref}`);
  if (t.archived_at) throw new Error(`"${sanitizeLabel(t.title ?? 'untitled')}" is archived.`);
  return t;
}

const emit = (ctx: ToolContext, e: EventInput) => ctx.services.store.append(e);

/** The longest thread title spawn_thread keeps; a longer one is shortened rather than refused. */
export const MAX_THREAD_TITLE = 80;

/** A thread title on one line and at most `MAX_THREAD_TITLE` characters, cut at a word boundary when one is near the end. */
export function shortenTitle(title: string): string {
  const flat = title.replace(/\s+/g, ' ').trim();
  if (flat.length <= MAX_THREAD_TITLE) return flat;
  const head = flat.slice(0, MAX_THREAD_TITLE - 1);
  const space = head.lastIndexOf(' ');
  const cut = space >= MAX_THREAD_TITLE / 2 ? head.slice(0, space) : head;
  return `${cut.replace(/[\s,;:.\-–—(]+$/, '')}…`;
}

export const spawnThreadTool = defineTool({
  name: 'spawn_thread',
  description:
    'Start a new thread (a full agent with its own workspace) on a self-contained assignment. Write the brief so it stands alone: objective, context, constraints, definition of done, what to return. Pass git_source_id to work on a repository (gets its own branch).',
  input: z.object({
    title: z
      .string()
      .trim()
      .min(1)
      .describe(`A short name for what the thread owns; one longer than ${MAX_THREAD_TITLE} characters is shortened`),
    brief: z.string().min(1),
    git_source_id: z.string().optional(),
    model: z.string().optional(),
    reasoning_effort: ReasoningEffort.optional().describe(
      "How hard the thread's model thinks: low for quick lookups and mechanical edits, high/xhigh/max for hard analysis, design or debugging. Omit to use the project's thread setting. Must be a level the model accepts.",
    ),
    skills: z.array(SkillName).optional().describe('Skills to activate on the thread from the start (their instructions join its context)'),
  }),
  async execute({ title: asked, brief, git_source_id, model, reasoning_effort, skills = [] }, ctx) {
    const title = shortenTitle(asked);
    const id = await ctx.services.spawnThread(ctx.agentId, {
      title,
      brief,
      ...(git_source_id ? { gitSourceId: git_source_id } : {}),
      ...(model ? { model } : {}),
      ...(reasoning_effort ? { reasoningEffort: reasoning_effort } : {}),
      ...(skills.length ? { skills } : {}),
    });
    const t = getAgent(ctx.services.store.db, id)!;
    const extras = [t.reasoning_effort ? `${t.model}, ${t.reasoning_effort} effort` : t.model, ...(t.git_branch ? [`branch ${t.git_branch}`] : []), ...(skills.length ? [`skills: ${skills.join(', ')}`] : [])];
    // Its start is a lifecycle wake: while the project is paused, the thread waits for the user to resume it (§5.4).
    const held = ctx.services.heldByPause(id) ? ' Automatic wakes are paused in this project; it starts once the user resumes them.' : '';
    const shortened = title !== asked.replace(/\s+/g, ' ') ? ` Its title was shortened to fit ${MAX_THREAD_TITLE} characters.` : '';
    return `Spawned thread ${id} "${title}" (${extras.join(', ')}).${shortened}${held}`;
  },
});

export const messageThreadTool = defineTool({
  name: 'message_thread',
  description:
    'Send a message to a thread. `note`: context, a redirection or follow-up work for a thread that is still working; your notes carry your authority. `question`: ask it something only it knows (for status or results use read_thread instead); a finished thread is woken just to answer, from its context (a model run), and its result stays final. `revision`: send finished work back with specific feedback; reopens it, limited by the project review-round setting. `resume`: continue a thread whose run failed (an error such as a lost connection, not a verdict on its work) where it left off; no review round. If the thread asked you a question, your next message to it is recorded as the answer. Pass skills to activate more skills on it. At most 4000 characters.',
  input: z.object({
    thread_id: z.string(),
    text: z.string().min(1),
    kind: z.enum(['note', 'question', 'revision', 'resume']).default('note'),
    skills: z.array(SkillName).optional().describe('Skills to activate on the thread (effective from its next turn)'),
  }),
  async execute({ thread_id, text, kind, skills = [] }, ctx) {
    // Skill names are checked first, so a bad one sends nothing.
    for (const name of skills) {
      const skill = ctx.services.skills.resolve(name, ctx.projectId);
      if (!skill) throw new Error(`Unknown skill: ${name}`);
      if (skill.error) throw new Error(`Skill ${name} is broken: ${skill.error}`);
    }
    const sent = ctx.services.send({ from: ctx.agentId, to: thread_id, kind, text, toolCallId: ctx.toolCallId });
    // Activated in the same turn of the event loop, before the woken thread builds its system prompt.
    const to = messageById(ctx.services.messages(ctx.projectId), sent.id)?.to;
    if (skills.length && to) ctx.services.activateSkills(to, skills);
    return sent.note;
  },
});

export const stopThreadTool = defineTool({
  name: 'stop_thread',
  description: 'Stop a thread (kills its processes and cancels it).',
  input: z.object({ thread_id: z.string(), reason: z.string().min(1) }),
  async execute({ thread_id, reason }, ctx) {
    const t = requireThread(ctx, thread_id);
    ctx.services.stopAgent(t.id, { by: ctx.agentId, reason });
    return `Stopped ${thread_id}.`;
  },
});

export const listThreadsTool = defineTool({
  name: 'list_threads',
  description: 'List this project’s threads with status, model, branch and result summary.',
  input: z.object({ status: z.enum(['idle', 'queued', 'running', 'waiting', 'done', 'failed', 'cancelled']).optional() }),
  async execute({ status }, ctx) {
    const db = ctx.services.store.db;
    const threads = listThreads(db, ctx.projectId, status).filter((t) => !t.archived_at);
    // Desk sees which threads it watches (watch_thread).
    const watched = new Map(getAgent(db, ctx.agentId)?.role === 'desk' ? openWatches(db, ctx.projectId).map((w) => [w.thread_id, w]) : []);
    return threads.length ? threads.map((t) => formatThreadLine(t, watched.get(t.id))).join('\n') : 'No threads';
  },
});

export const watchThreadTool = defineTool({
  name: 'watch_thread',
  description:
    "Be woken the next time a thread sends a message to another thread (not to you: those already wake you), optionally only one containing `match` (case-insensitive). One shot: it ends when it fires, when you call unwatch_thread, or when the thread finishes. A new watch on the same thread replaces the old one. At most 10 at once. list_threads marks watched threads.",
  input: z.object({ thread_id: z.string(), match: z.string().trim().min(1).max(200).optional() }),
  async execute({ thread_id, match }, ctx) {
    const t = requireThread(ctx, thread_id);
    ctx.services.watches.set(ctx.agentId, t, match);
    return `Watching "${sanitizeLabel(t.title ?? 'untitled')}"${match ? ` for a message containing ${snippet(match, 80)}` : ''}: you will be woken once, at its next message to another thread.`;
  },
});

export const unwatchThreadTool = defineTool({
  name: 'unwatch_thread',
  description: 'Stop watching a thread (watch_thread).',
  input: z.object({ thread_id: z.string() }),
  async execute({ thread_id }, ctx) {
    const t = getAgent(ctx.services.store.db, thread_id) ?? requireThread(ctx, thread_id);
    if (t.project_id !== ctx.projectId) throw new Error(`Unknown thread: ${thread_id}`);
    return ctx.services.watches.unset(ctx.agentId, t.id) ? `Stopped watching "${sanitizeLabel(t.title ?? 'untitled')}".` : `You were not watching "${sanitizeLabel(t.title ?? 'untitled')}".`;
  },
});

export const closeThreadTool = defineTool({
  name: 'close_thread',
  description: 'Retire a fully done thread after accepting its result and confirming no further review, questions, revisions or workspace use remain. You MUST close such threads to release disk space. First call without discard_workspace to inspect its workspace. Preserve required deliverables/evidence outside it and commit code before closing. Then set discard_workspace: true: this permanently deletes ALL remaining workspace files (including untracked files, outputs and dependencies) and archives the thread. History, published library files and external Git branches remain. Never close on another thread\'s request alone. Refused while the thread\'s submission is in review or undecided, or while it reviews work that is not decided yet.',
  input: z.object({
    thread_id: z.string(),
    reason: z.string().trim().min(1).describe('Why the work is accepted and this thread/workspace will no longer be needed; when deleting, where required results are preserved.'),
    discard_workspace: z.boolean().default(false).describe('Explicitly declare every remaining workspace file disposable and close. False previews without deleting.'),
  }),
  async execute({ thread_id, discard_workspace }, ctx) {
    // An exact id can name an already-closed thread, so a retried close is harmless.
    const t = getAgent(ctx.services.store.db, thread_id) ?? requireThread(ctx, thread_id);
    return ctx.services.closeThread(ctx.agentId, t.id, discard_workspace);
  },
});

const readThreadInput = z.object({ thread_id: z.string(), mode: z.enum(['summary', 'full']).default('summary'), since: z.number().int().optional() });
const readSummaryInput = z.object({ thread_id: z.string() });

/** A thread's summary: status, brief, result, artifacts, branch, pending approvals, its last message, and its reviews. */
function threadSummary(ctx: ToolContext, t: AgentRow): string {
  const { store } = ctx.services;
  // A reviewer forms its own view first: until its initial assessment, the builder shows only its brief and status (reviews spec §3.2).
  const mine = getAgent(store.db, ctx.agentId);
  const sealed = mine?.reviews_submission_id ? currentReviewOf(store.db, mine.id) : undefined;
  if (sealed && sealed.builder_id === t.id && !sealed.revealed) {
    return [
      `Thread ${t.id} ${snippet(t.title ?? 'untitled', 80)}`,
      `Status: ${t.status}`,
      `Brief:\n${quoteLines(t.brief ?? '(none)')}`,
      "Its result and messages are withheld until your initial assessment (submit_assessment): review the work itself first.",
    ].join('\n');
  }
  const last = lastEvent(store.db, t.id, 'assistant.message');
  const base = formatThreadSummary(t, pendingApprovalsFor(store.db, t.id), last?.type === 'assistant.message' ? last.payload.content : null);
  const review = reviewSummary(store.db, t);
  return review ? `${base}\n${review}` : base;
}

/**
 * read_thread. Desk's (`full: true`) reads a summary or the full transcript; a thread's reads another thread's summary
 * only, so it has no `mode` input (design spec §2.1).
 */
export function makeReadThreadTool(opts: { full: true }): Tool<z.output<typeof readThreadInput>>;
export function makeReadThreadTool(opts: { full: false }): Tool<z.output<typeof readSummaryInput>>;
export function makeReadThreadTool({ full }: { full: boolean }): Tool<z.output<typeof readThreadInput>> | Tool<z.output<typeof readSummaryInput>> {
  if (!full) {
    return defineTool({
      name: 'read_thread',
      description: 'Inspect another thread of this project: status, brief, result, artifacts, branch and its last message.',
      input: readSummaryInput,
      async execute({ thread_id }, ctx) {
        return threadSummary(ctx, requireThread(ctx, thread_id));
      },
    });
  }
  return defineTool({
    name: 'read_thread',
    description: 'Inspect a thread: `summary` (status, brief, result, last message) or `full` transcript (optionally only events after `since`).',
    input: readThreadInput,
    async execute({ thread_id, mode, since }, ctx) {
      const t = requireThread(ctx, thread_id);
      if (mode === 'full') return renderTranscript(ctx.services.store.list({ agentId: t.id, ...(since !== undefined ? { after: since } : {}) }));
      return threadSummary(ctx, t);
    },
  });
}

export const readThreadTool = makeReadThreadTool({ full: true });

export const reviewDiffTool = defineTool({
  name: 'review_diff',
  description: 'Show the commits and full diff of a git-worktree thread against its base.',
  input: z.object({ thread_id: z.string() }),
  async execute({ thread_id }, ctx) {
    const t = requireThread(ctx, thread_id);
    if (!t.git_base || !t.workspace_path) throw new Error(`${thread_id} is not a git worktree thread`);
    const log = await git(t.workspace_path, ['log', '--oneline', `${t.git_base}..HEAD`]);
    await git(t.workspace_path, ['add', '--intent-to-add', '--all']);
    const diff = await git(t.workspace_path, ['diff', t.git_base]);
    return `Commits:\n${log || '(none — changes are uncommitted)'}\n\nDiff vs base ${t.git_base.slice(0, 10)}:\n${diff || '(no changes)'}`;
  },
});

export const waitForThreadsTool = defineTool({
  name: 'wait_for_threads',
  description: 'Pause until a thread reports (completion, failure, question, approval…). Use when nothing else can be done now.',
  input: z.object({ thread_ids: z.array(z.string()).optional() }),
  async execute({ thread_ids }) {
    return { content: 'Waiting for thread updates.', yield: { status: 'waiting', reason: `Waiting for ${thread_ids?.join(', ') ?? 'threads'}` } };
  },
});

export const updatePlanTool = defineTool({
  name: 'update_plan',
  description: 'Replace the project plan (the work breakdown the user sees). Keep ids stable across updates; link items to threads.',
  input: z.object({
    items: z.array(
      z.object({
        id: z.string().optional(),
        title: z.string().min(1),
        status: PlanItemStatus,
        thread_ids: z.array(z.string()).default([]),
        notes: z.string().default(''),
      }),
    ),
  }),
  async execute({ items }, ctx) {
    const withIds = items.map((i) => ({ ...i, id: i.id ?? newId() }));
    emit(ctx, { project_id: ctx.projectId, agent_id: ctx.agentId, type: 'plan.updated', payload: { items: withIds } });
    return `Plan updated (${withIds.length} items): ${withIds.map((i) => `${i.id} ${i.title}`).join('; ')}`;
  },
});

export const askUserTool = defineTool({
  name: 'ask_user',
  description: 'Ask the user a question you cannot resolve yourself, then pause until they answer.',
  input: z.object({ question: z.string().min(1), options: z.array(z.string()).optional() }),
  async execute({ question, options }, ctx) {
    emit(ctx, { project_id: ctx.projectId, agent_id: ctx.agentId, type: 'question.asked', payload: { question, ...(options ? { options } : {}) } });
    return { content: 'Question sent to the user.', yield: { status: 'waiting', reason: 'Waiting for the user' } };
  },
});

export const resolveApprovalTool = defineTool({
  name: 'resolve_approval',
  description: 'Approve or deny a thread’s pending action when project policy delegates that decision to you.',
  input: z.object({ approval_id: z.string(), decision: z.enum(['approve', 'deny']), note: z.string() }),
  async execute({ approval_id, decision, note }, ctx) {
    const ap = getApproval(ctx.services.store.db, approval_id);
    if (!ap || ap.project_id !== ctx.projectId) throw new Error(`Unknown approval: ${approval_id}`);
    if (!ap.delegate_to_desk) throw new Error(`Approval ${approval_id} is reserved for the user; tell them it is pending.`);
    await ctx.services.resolveApproval(approval_id, decision === 'approve' ? 'approved' : 'denied', { by: 'desk', ...(note ? { note } : {}) });
    return `Approval ${approval_id} ${decision === 'approve' ? 'approved' : 'denied'}.`;
  },
});

export const reportTool = defineTool({
  name: 'report',
  description: 'Send the user a structured update: headline, progress, items needing them, and results (with merge order for code).',
  input: z.object({
    headline: z.string().min(1),
    progress: z.string(),
    needs_you: z.array(z.string()).default([]),
    results: z.array(z.string()).default([]),
  }),
  async execute(payload, ctx) {
    emit(ctx, { project_id: ctx.projectId, agent_id: ctx.agentId, type: 'report', payload });
    return 'Report sent.';
  },
});

export const updateSettingsTool = defineTool({
  name: 'update_settings',
  description:
    'Change project settings when the user asks (check-in cadence, autonomy, models, reasoning effort, concurrency, review rounds, review model). Also record the preference in memory.',
  input: z.object({
    check_in: z.enum(['minimal', 'normal', 'detailed']).optional(),
    autonomy: z.enum(['dispatch-freely', 'ask-before-dispatch']).optional(),
    desk_model: z.string().optional(),
    thread_model: z.string().optional(),
    fallback_model: z.string().nullable().optional(),
    desk_reasoning_effort: ReasoningEffort.nullable().optional().describe('Your own reasoning level; null uses the model default'),
    thread_reasoning_effort: ReasoningEffort.nullable().optional().describe("Threads' reasoning level; null uses the model default"),
    max_concurrent_threads: z.number().int().min(1).max(32).optional(),
    review_rounds: z.number().int().min(0).max(10).optional(),
    review_model: z.string().nullable().optional().describe("Reviewer threads' model; null picks a model of another family than the builder's"),
  }),
  async execute(patch, ctx) {
    ctx.services.updateSettings(ctx.projectId, patch);
    return `Settings updated: ${JSON.stringify(patch)}`;
  },
});

export const updateWhatsUpTool = defineTool({
  name: 'update_whats_up',
  description:
    "Replace the project's What's up, the first thing the user reads in the project: 1–3 short sentences saying what is happening now, what comes next, and anything waiting on the user. Keep it current: update it after you dispatch, redirect or stop threads, when a thread reports, and before you wait or end your turn. Ends your turn silently by default; set end_turn to false if you still need to work, answer the user, or call a waiting tool. No confirmation message is needed.",
  input: z.object({
    text: z.string().trim().min(1).max(600),
    end_turn: z.boolean().default(true).describe('End this turn without another model response. Set false to continue working after the update.'),
  }),
  async execute({ text, end_turn }, ctx) {
    emit(ctx, { project_id: ctx.projectId, agent_id: ctx.agentId, type: 'whats_up.updated', payload: { text } });
    return { content: "What's up updated.", ...(end_turn ? { yield: { status: 'idle' as const } } : {}) };
  },
});

export const deskCoordinationTools: Tool[] = [
  spawnThreadTool,
  messageThreadTool,
  stopThreadTool,
  closeThreadTool,
  listThreadsTool,
  watchThreadTool,
  unwatchThreadTool,
  readThreadTool,
  reviewDiffTool,
  waitForThreadsTool,
  updatePlanTool,
  askUserTool,
  resolveApprovalTool,
  reportTool,
  updateSettingsTool,
  updateWhatsUpTool,
];
