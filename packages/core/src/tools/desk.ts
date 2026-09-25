import { z } from 'zod';
import { PlanItemStatus, ReasoningEffort, SkillName, type EventInput } from '@desk/protocol';
import { formatThreadLine, formatThreadSummary, renderTranscript } from '../coordination/render';
import { newId } from '../ids';
import { getAgent, getApproval, getProject, lastEvent, listThreads, pendingApprovalsFor, type AgentRow } from '../state/queries';
import { git } from '../workspaces/workspaces';
import { defineTool, type Tool, type ToolContext } from './types';

function requireThread(ctx: ToolContext, threadId: string): AgentRow {
  const t = getAgent(ctx.services.store.db, threadId);
  if (!t || t.role !== 'thread' || t.project_id !== ctx.projectId) throw new Error(`Unknown thread: ${threadId}`);
  return t;
}

/** Why Desk may not send `kind` to thread `t` now (§2.4 checks 3–4), or null. */
function refusal(t: AgentRow, kind: 'note' | 'revision'): string | null {
  const name = `"${t.title ?? 'untitled'}"`;
  if (t.archived_at) return `${name} is archived.`;
  if (t.status === 'cancelled') return `${name} was stopped; it cannot receive messages.`;
  if (kind === 'note' && (t.status === 'done' || t.status === 'failed')) {
    return `${name} has finished; its result is final. Send kind "question" to ask about its work, "revision" if it fell short of its brief, or spawn a new thread whose brief points at its result or branch.`;
  }
  return null;
}

const emit = (ctx: ToolContext, e: EventInput) => ctx.services.store.append(e);

export const spawnThreadTool = defineTool({
  name: 'spawn_thread',
  description:
    'Start a new thread (a full agent with its own workspace) on a self-contained assignment. Write the brief so it stands alone: objective, context, constraints, definition of done, what to return. Pass git_source_id to work on a repository (gets its own branch).',
  input: z.object({
    title: z.string().min(1).max(80),
    brief: z.string().min(1),
    git_source_id: z.string().optional(),
    model: z.string().optional(),
    reasoning_effort: ReasoningEffort.optional().describe(
      "How hard the thread's model thinks: low for quick lookups and mechanical edits, high/xhigh/max for hard analysis, design or debugging. Omit to use the project's thread setting. Must be a level the model accepts.",
    ),
    skills: z.array(SkillName).optional().describe('Skills to activate on the thread from the start (their instructions join its context)'),
  }),
  async execute({ title, brief, git_source_id, model, reasoning_effort, skills = [] }, ctx) {
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
    return `Spawned thread ${id} "${title}" (${extras.join(', ')}).`;
  },
});

export const messageThreadTool = defineTool({
  name: 'message_thread',
  description:
    'Send a message to a thread: a `note` (answer, extra context, redirection) or a `revision` (send finished work back with specific feedback; reopens the thread, limited by the project review-round setting). Pass skills to activate more skills on it.',
  input: z.object({
    thread_id: z.string(),
    text: z.string().min(1),
    kind: z.enum(['note', 'revision']).default('note'),
    skills: z.array(SkillName).optional().describe('Skills to activate on the thread (effective from its next turn)'),
  }),
  async execute({ thread_id, text, kind, skills = [] }, ctx) {
    const t = requireThread(ctx, thread_id);
    const refused = refusal(t, kind);
    if (refused) throw new Error(refused);
    if (skills.length) ctx.services.activateSkills(t.id, skills);
    if (kind === 'revision') {
      const limit = getProject(ctx.services.store.db, ctx.projectId)!.settings.review_rounds;
      if (t.review_round >= limit) {
        throw new Error(`Review round limit (${limit}) reached for ${thread_id}: accept the work with noted caveats or escalate to the user.`);
      }
      emit(ctx, { project_id: ctx.projectId, agent_id: t.id, type: 'agent.revision', payload: { round: t.review_round + 1, feedback: text } });
    }
    ctx.services.sendAgentMessage(ctx.agentId, t.id, kind, text);
    return kind === 'revision' ? `Sent revision ${t.review_round + 1} to ${thread_id}; it has been reopened.` : `Sent to ${thread_id}.`;
  },
});

export const stopThreadTool = defineTool({
  name: 'stop_thread',
  description: 'Stop a thread (kills its processes and cancels it).',
  input: z.object({ thread_id: z.string(), reason: z.string().min(1) }),
  async execute({ thread_id, reason }, ctx) {
    requireThread(ctx, thread_id);
    ctx.services.stopAgent(thread_id, { by: ctx.agentId, reason });
    return `Stopped ${thread_id}.`;
  },
});

export const listThreadsTool = defineTool({
  name: 'list_threads',
  description: 'List this project’s threads with status, model, branch and result summary.',
  input: z.object({ status: z.enum(['idle', 'queued', 'running', 'waiting', 'done', 'failed', 'cancelled']).optional() }),
  async execute({ status }, ctx) {
    const threads = listThreads(ctx.services.store.db, ctx.projectId, status).filter((t) => !t.archived_at);
    return threads.length ? threads.map(formatThreadLine).join('\n') : 'No threads';
  },
});

export const readThreadTool = defineTool({
  name: 'read_thread',
  description: 'Inspect a thread: `summary` (status, brief, result, last message) or `full` transcript (optionally only events after `since`).',
  input: z.object({ thread_id: z.string(), mode: z.enum(['summary', 'full']).default('summary'), since: z.number().int().optional() }),
  async execute({ thread_id, mode, since }, ctx) {
    const t = requireThread(ctx, thread_id);
    const { store } = ctx.services;
    if (mode === 'full') return renderTranscript(store.list({ agentId: t.id, ...(since !== undefined ? { after: since } : {}) }));
    const last = lastEvent(store.db, t.id, 'assistant.message');
    return formatThreadSummary(t, pendingApprovalsFor(store.db, t.id), last?.type === 'assistant.message' ? last.payload.content : null);
  },
});

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
    'Change project settings when the user asks (check-in cadence, autonomy, models, reasoning effort, concurrency, review rounds). Also record the preference in memory.',
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
  }),
  async execute(patch, ctx) {
    ctx.services.updateSettings(ctx.projectId, patch);
    return `Settings updated: ${JSON.stringify(patch)}`;
  },
});

export const updateWhatsUpTool = defineTool({
  name: 'update_whats_up',
  description:
    "Replace the project's What's up, the first thing the user reads in the project: 1–3 short sentences saying what is happening now, what comes next, and anything waiting on the user. Keep it current: update it after you dispatch, redirect or stop threads, when a thread reports, and before you wait or end your turn.",
  input: z.object({ text: z.string().trim().min(1).max(600) }),
  async execute({ text }, ctx) {
    emit(ctx, { project_id: ctx.projectId, agent_id: ctx.agentId, type: 'whats_up.updated', payload: { text } });
    return "What's up updated.";
  },
});

export const deskCoordinationTools: Tool[] = [
  spawnThreadTool,
  messageThreadTool,
  stopThreadTool,
  listThreadsTool,
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
