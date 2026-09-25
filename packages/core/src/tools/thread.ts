import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import { parseSkillMd } from '../skills/store';
import { readAgentFile } from './agent-files';
import { resolveInside } from './paths';
import { listArtifacts } from '../library/library';
import { openFrom, sanitizeLabel, type MessagesState } from '@desk/protocol';
import { getAgent, getDeskAgent, threadsByRef } from '../state/queries';
import { listThreadsTool, makeReadThreadTool } from './desk';
import { defineTool, type ToolContext } from './types';

function parentOf(ctx: ToolContext): string {
  const parent = getAgent(ctx.services.store.db, ctx.agentId)?.parent_id;
  if (!parent) throw new Error('This agent has no Desk to report to');
  return parent;
}

export const completeTool = defineTool({
  name: 'complete',
  description:
    'Finish your assignment. Call exactly once, when the work is done or cannot be done, with an honest summary: what was done, what was not, and how it was verified. List library paths of artifacts you published, and skill draft directories you prepared.',
  input: z.object({
    summary: z.string().min(1),
    artifacts: z.array(z.string()).default([]),
    skill_drafts: z.array(z.string()).optional().describe('Skill draft directories in your workspace (each with a SKILL.md) for Desk to review and install'),
  }),
  async execute({ summary, artifacts, skill_drafts = [] }, ctx) {
    if (artifacts.length) {
      const known = new Set(listArtifacts(ctx.services.store.db, ctx.projectId).map((a) => a.path));
      const missing = artifacts.filter((a) => !known.has(a));
      if (missing.length) throw new Error(`Not in the library: ${missing.join(', ')}. Publish them with library_publish first.`);
    }
    const drafts = await Promise.all(
      skill_drafts.map(async (d) => {
        const dir = await resolveInside(d, [ctx.workspace], ctx.workspace);
        const md = join(dir, 'SKILL.md');
        if (!existsSync(md)) throw new Error(`${d} has no SKILL.md`);
        parseSkillMd((await readAgentFile(md, ctx.sandbox.guard)).toString('utf8'));
        return dir;
      }),
    );
    ctx.services.store.append({
      project_id: ctx.projectId,
      agent_id: ctx.agentId,
      type: 'agent.result',
      payload: { summary, artifacts, ...(drafts.length ? { skill_drafts: drafts } : {}) },
    });
    return { content: 'Completion recorded.', yield: { status: 'done', reason: summary.split('\n')[0]!.slice(0, 200) } };
  },
});

/** read_thread for threads: another thread's summary (design spec §2.1). */
export const threadReadThreadTool = makeReadThreadTool({ full: false });

/** Whether a message_thread target names Desk: its id, or "Desk" (any case) when no thread has that exact title. */
function namesDesk(ctx: ToolContext, ref: string): boolean {
  const { db } = ctx.services.store;
  const desk = getDeskAgent(db, ctx.projectId);
  if (!desk) return false;
  return ref === desk.id || (ref.trim().toLowerCase() === 'desk' && threadsByRef(db, ctx.projectId, ref).length === 0);
}

export const threadMessageThreadTool = defineTool({
  name: 'message_thread',
  description:
    "Send a message to another thread of this project (thread_id: its id or exact title). `question`: ask about its own work (an interface, file, format or finding it owns); it may be woken just to answer you, so ask only what its brief, its result or read_thread don't tell you. `note`: tell it something that changes its work. If that thread asked you a question, your next message to it is recorded as the answer. At most 4000 characters: publish long content with library_publish and send the path.",
  input: z.object({ thread_id: z.string(), kind: z.enum(['note', 'question']).default('note'), text: z.string().min(1) }),
  // A project rule can deny messages between threads, require approval for them, or delegate them to Desk (§5.5).
  gate: { subject: () => ({}), unmatched: 'auto' },
  async execute({ thread_id, kind, text }, ctx) {
    // §2.4 check 2. Only this tool knows that a thread aimed message_thread at Desk.
    if (namesDesk(ctx, thread_id)) throw new Error('Use message_desk to reach Desk.');
    return ctx.services.send({ from: ctx.agentId, to: thread_id, kind, text, toolCallId: ctx.toolCallId }).note;
  },
});

export const messageDeskTool = defineTool({
  name: 'message_desk',
  description:
    'Send a message to Desk, the project coordinator: a progress `update`, a `question`, or a `blocker`. If Desk asked you a question, your next update is recorded as the answer. After a question or blocker, call wait_for_reply unless you can keep working meanwhile. At most 4000 characters.',
  input: z.object({ kind: z.enum(['update', 'question', 'blocker']), text: z.string().min(1) }),
  async execute({ kind, text }, ctx) {
    return ctx.services.send({ from: ctx.agentId, to: parentOf(ctx), kind, text, toolCallId: ctx.toolCallId }).note;
  },
});

/**
 * What a waiting thread waits on, for its status reason (design spec §2.1; the CLI shows it, list_threads shows only
 * the status): the recipients of its open questions, threads first and Desk last, or "Desk or the user" when it asked
 * nothing.
 */
export function waitingReason(s: MessagesState, agentId: string): string {
  const to = [...new Set(openFrom(s, agentId).map((q) => q.to))];
  const threads = to.filter((id) => s.agents[id]?.role === 'thread').map((id) => `"${sanitizeLabel(s.agents[id]?.title ?? 'untitled')}"`);
  const names = to.some((id) => s.agents[id]?.role === 'desk') ? [...threads, 'Desk'] : threads;
  if (!names.length) return 'Waiting on Desk or the user';
  return `Waiting on ${names.length === 1 ? names[0] : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`}`;
}

export const waitForReplyTool = defineTool({
  name: 'wait_for_reply',
  description: 'Pause until an answer to a question you asked arrives, or Desk or the user writes to you. Notes from other threads do not end the wait.',
  input: z.object({}),
  async execute(_input, ctx) {
    const reason = waitingReason(ctx.services.messages(ctx.projectId), ctx.agentId);
    return { content: `${reason}.`, yield: { status: 'waiting', reason } };
  },
});

export const threadCoordinationTools = [listThreadsTool, threadReadThreadTool, threadMessageThreadTool, messageDeskTool, waitForReplyTool, completeTool];
