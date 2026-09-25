import type { AgentMessageKind, AgentRole, AgentStatus } from '@desk/protocol';

/** One inbox item stored after the agent's cursor: who sent it (the user, Desk or a thread) and its kind. */
export type Item = { id: number; from: 'user' | 'desk' | 'thread'; kind: AgentMessageKind | 'user' };

/** Everything wakeDecision reads about one agent. Runtime builds it from the store and its in-memory state. */
export type WakeState = {
  agent: { role: AgentRole; status: AgentStatus; archived: boolean };
  /** Set while the agent is cancelled: the id of the `agent.status_changed` that cancelled it. */
  cancelledAt?: number;
  projectArchived: boolean;
  /** Approvals pending in the store, plus approved calls that resolveApproval is still running. */
  pendingApprovals: number;
  /** Inbox items after the agent's inbox_cursor, oldest first. */
  pending: Item[];
};

/**
 * What a run answers to: the user, a job that was already queued, a lifecycle notice or a thread's start, or another
 * agent. The wake budgets count `agent` and `lifecycle` runs only.
 */
export type Trigger = 'user' | 'queued' | 'lifecycle' | 'agent';

export type Wake = { kind: 'none' } | { kind: 'run'; trigger: Trigger };

/** Runtime notices about threads, a thread's start, and the runtime's What's up reminder to Desk. */
export const LIFECYCLE_KINDS: ReadonlySet<string> = new Set(['start', 'completed', 'failed', 'cancelled', 'approval', 'stalled', 'reminder']);

const NONE: Wake = { kind: 'none' };
const run = (trigger: Trigger): Wake => ({ kind: 'run', trigger });
/** `lifecycle` when every item is a lifecycle item, `agent` otherwise. */
const triggerOf = (items: Item[]): Trigger => (items.every((i) => LIFECYCLE_KINDS.has(i.kind)) ? 'lifecycle' : 'agent');

/**
 * Whether an agent should run now, and why (design spec §3.2, delivery table §3.3). Pure: deliveries, the end of every
 * run, recovery and approval resolutions all decide through it.
 */
export function wakeDecision(s: WakeState): Wake {
  const { agent, pending } = s;
  // 1. Archived, in an archived project, or blocked on an approval (or on an approved call still running).
  if (agent.archived || s.projectArchived || s.pendingApprovals > 0) return NONE;
  // 2. A running agent reads new items at its next step; a queued one already has its job.
  if (agent.status === 'running') return NONE;
  if (agent.status === 'queued') return run('queued');
  // A stop is final for everything that arrived before it: only the user's later message counts.
  const userWrote = pending.some((i) => i.from === 'user' && (s.cancelledAt === undefined || i.id > s.cancelledAt));
  // 3. Desk runs for anything pending, unless the user stopped it.
  if (agent.role === 'desk') {
    if (agent.status === 'cancelled') return userWrote ? run('user') : NONE;
    if (!pending.length) return NONE;
    return userWrote ? run('user') : run(triggerOf(pending));
  }
  // 4.1 The user's message runs a thread whatever its status (a finished thread reopens).
  if (userWrote) return run('user');
  // 4.2 A stopped thread waits for the user.
  if (agent.status === 'cancelled') return NONE;
  const fromDesk = pending.filter((i) => i.from === 'desk');
  // 4.3 A revision reopens finished work.
  if (fromDesk.some((i) => i.kind === 'revision')) return run('agent');
  // 4.4 A thread waiting on a reply runs for anything from Desk.
  if (agent.status === 'waiting' && fromDesk.length) return run('agent');
  // 4.5 An idle thread runs for a Desk message other than a question.
  if (agent.status === 'idle') {
    const wakers = fromDesk.filter((i) => i.kind !== 'question');
    if (wakers.length) return run(triggerOf(wakers));
  }
  // Otherwise the items stay pending: notes from other threads, and Desk notes that raced a completion.
  return NONE;
}
