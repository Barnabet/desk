import type { AgentMessageKind, AgentRole, AgentStatus } from '@desk/protocol';

/**
 * One inbox item stored after the agent's cursor: who sent it (the user, Desk or a thread) and its kind. The user's
 * Ask to a thread is `user_question`.
 */
export type Item = { id: number; from: 'user' | 'desk' | 'thread'; kind: AgentMessageKind | 'user' | 'user_question' };

/** Everything wakeDecision reads about one agent. Runtime builds it from the store and its in-memory state. */
export type WakeState = {
  /** `archived`: archived, or being archived (Runtime's `archiving` set). */
  agent: { role: AgentRole; status: AgentStatus; archived: boolean };
  /**
   * Set while the agent is cancelled: where the stop happened. That is the id of the `agent.status_changed` that
   * cancelled it, or, for a run stopped while running, the agent's last event id when the stop landed.
   */
  cancelledAt?: number;
  projectArchived: boolean;
  /** Approvals pending in the store, plus approved calls that resolveApproval is still running. */
  pendingApprovals: number;
  /** Inbox items after the agent's inbox_cursor, oldest first. */
  pending: Item[];
  /** Open tracked questions to the agent (design spec §1.3), oldest first; `seen` once its cursor has passed them. */
  open: { id: number; seen: boolean }[];
};

/**
 * What a run answers to: the user, a job that was already queued, a lifecycle notice or a thread's start, or another
 * agent. The wake budgets count `agent` and `lifecycle` runs only.
 */
export type Trigger = 'user' | 'queued' | 'lifecycle' | 'agent';

/** Nothing; a full run; or an answer run for one question, which never changes the agent's status (design spec §4). */
export type Wake = { kind: 'none' } | { kind: 'run'; trigger: Trigger } | { kind: 'answer'; question: number; trigger: Trigger };

/** Runtime notices about threads, a thread's start, and the runtime's What's up reminder to Desk. */
export const LIFECYCLE_KINDS: ReadonlySet<string> = new Set(['start', 'completed', 'failed', 'cancelled', 'approval', 'stalled', 'reminder']);

/** Statuses a thread answers from without reopening: the user's Ask to it gets an answer run instead of a run. */
const ANSWERS_FROM: ReadonlySet<AgentStatus> = new Set<AgentStatus>(['idle', 'done', 'failed']);

const NONE: Wake = { kind: 'none' };
const run = (trigger: Trigger): Wake => ({ kind: 'run', trigger });
/** `lifecycle` when every item is a lifecycle item, `agent` otherwise. */
const triggerOf = (items: Item[]): Trigger => (items.every((i) => LIFECYCLE_KINDS.has(i.kind)) ? 'lifecycle' : 'agent');

/**
 * Whether an agent should run now, answer one question, or neither, and why (design spec §3.2, delivery table §3.3).
 * Pure: deliveries, the end of every job, recovery, approval resolutions and an answer job's start all decide
 * through it.
 */
export function wakeDecision(s: WakeState): Wake {
  const { agent, pending } = s;
  // 1. Archived (or being archived), in an archived project, or blocked on an approval (or on an approved call still running).
  if (agent.archived || s.projectArchived || s.pendingApprovals > 0) return NONE;
  // 2. A running agent reads new items at its next step; a queued one already has its job.
  if (agent.status === 'running') return NONE;
  if (agent.status === 'queued') return run('queued');
  /** A stop is final for everything that arrived before it: only the user's later messages count. */
  const afterStop = (i: Item) => s.cancelledAt === undefined || i.id > s.cancelledAt;
  // 3. Desk runs for anything pending, unless the user stopped it. It never gets answer runs.
  if (agent.role === 'desk') {
    const userWrote = pending.some((i) => i.from === 'user' && afterStop(i));
    if (agent.status === 'cancelled') return userWrote ? run('user') : NONE;
    if (!pending.length) return NONE;
    return userWrote ? run('user') : run(triggerOf(pending));
  }
  const answersFrom = ANSWERS_FROM.has(agent.status);
  // 4.1 The user's message runs a thread whatever its status (a finished thread reopens). An Ask is a message too,
  // except to an idle, done or failed thread, which answers it in an answer run (4.6).
  if (pending.some((i) => i.from === 'user' && (i.kind === 'user' || !answersFrom) && afterStop(i))) return run('user');
  // 4.2 A stopped thread waits for the user.
  if (agent.status === 'cancelled') return NONE;
  const fromDesk = pending.filter((i) => i.from === 'desk');
  // 4.3 A revision reopens finished work.
  if (fromDesk.some((i) => i.kind === 'revision')) return run('agent');
  // 4.4 A thread waiting on a reply runs for anything from Desk, and for an answer.
  if (agent.status === 'waiting' && (fromDesk.length || pending.some((i) => i.kind === 'answer'))) return run('agent');
  // 4.5 An idle thread runs for an answer (it asked, then ended its turn in text) and for a Desk message other than a
  // question. Its start is a lifecycle wake.
  if (agent.status === 'idle') {
    const wakers = pending.filter((i) => i.kind === 'answer' || (i.from === 'desk' && i.kind !== 'question'));
    if (wakers.length) return run(triggerOf(wakers));
  }
  // 4.6 The oldest open question to it (pending or already read: an orphan), or the user's pending Ask to an idle,
  // done or failed thread, gets an answer run.
  const asks = answersFrom ? pending.filter((i) => i.kind === 'user_question') : [];
  const next = [...s.open.map((q) => ({ id: q.id, user: false })), ...asks.map((i) => ({ id: i.id, user: true }))].sort((a, b) => a.id - b.id)[0];
  if (next) return { kind: 'answer', question: next.id, trigger: next.user ? 'user' : 'agent' };
  // 4.7 Otherwise the items stay pending: notes from other threads, answers to finished threads, and Desk notes that
  // raced a completion.
  return NONE;
}
