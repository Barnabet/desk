import type { AgentMessageKind, AgentRole, AgentStatus } from './domain';
import type { EventOf, EventType, StoredEvent } from './events';

/**
 * A message: a `message.agent` event (between agents, or a runtime notice), or the user's `message.user` to a thread.
 * Its id is the event id; it is stored on the recipient's stream (design spec §1.1).
 */
export type MessageView = {
  id: number;
  ts: string;
  /** The sender's agent id, or 'user'. */
  from: string;
  /** The recipient's agent id. */
  to: string;
  kind: AgentMessageKind | 'user' | 'user_question';
  text: string;
  /** For an answer: the question it answers. */
  replyTo?: number;
  /** An answer the runtime wrote for the question's recipient (a closure), not the recipient itself. */
  auto?: true;
  /** A question sent through the send path: only tracked questions have a state. */
  tracked?: true;
  /** The tool call that sent it (absent on runtime notices and closures). */
  toolCallId?: string;
  /** A tracked question's state: `open` first, then changed once and never back (design spec §1.3). */
  state?: 'open' | 'answered' | 'closed' | 'withdrawn';
  /** A question's first answer, even one that arrived after the question was withdrawn. */
  answerId?: number;
  /** When a tracked question left `open`. */
  stateAt?: string;
};

/** An agent of the project. Archived threads stay, so old messages keep their sender's title. */
export type AgentEntry = { id: string; role: AgentRole; title: string | null; status: AgentStatus; archived: boolean };

/** A thread's answer run in progress: from its `run.started{answering}` to that run's `run.finished`. */
export type AnswerRun = {
  runId: string;
  /** The question it answers. */
  question: number;
  /** Who asked: an agent id, or 'user'. */
  asker: string;
  since: string;
};

/** The project's messages as the fold sees them (design spec §1.4). */
export type MessagesState = {
  /** The id of the last event folded in: later folds skip events at or below it. */
  lastId: number;
  agents: Record<string, AgentEntry>;
  /** Oldest first. */
  messages: MessageView[];
  /** Each message's index in `messages`, by message id. */
  byId: Record<number, number>;
  /** Answer runs in progress, by thread id. */
  answering: Record<string, AnswerRun>;
};

/** The event types the fold reads: folding a project needs only these (the `events_project_type_idx` index serves the query). */
export const MESSAGE_FOLD_TYPES = [
  'agent.created',
  'agent.status_changed',
  'agent.archived',
  'project.archived',
  'message.agent',
  'message.user',
  'run.started',
  'run.finished',
] as const satisfies readonly EventType[];

const FOLDED: ReadonlySet<string> = new Set(MESSAGE_FOLD_TYPES);

export function emptyMessages(): MessagesState {
  return { lastId: 0, agents: {}, messages: [], byId: {}, answering: {} };
}

/** A state being folded. Each part is copied the first time the fold changes it, so the input state is never mutated. */
type Draft = {
  readonly s: MessagesState;
  agents(): Record<string, AgentEntry>;
  messages(): MessageView[];
  byId(): Record<number, number>;
  answering(): Record<string, AnswerRun>;
};

function draft(from: MessagesState): Draft {
  const s: MessagesState = { ...from };
  const copied = new Set<string>();
  /** Runs `copy` the first time `part` changes. */
  const own = (part: string, copy: () => void) => {
    if (copied.has(part)) return;
    copied.add(part);
    copy();
  };
  return {
    s,
    agents() {
      own('agents', () => (s.agents = { ...s.agents }));
      return s.agents;
    },
    messages() {
      own('messages', () => (s.messages = [...s.messages]));
      return s.messages;
    },
    byId() {
      own('byId', () => (s.byId = { ...s.byId }));
      return s.byId;
    },
    answering() {
      own('answering', () => (s.answering = { ...s.answering }));
      return s.answering;
    },
  };
}

/** A message by id. */
export function messageById(s: MessagesState, id: number): MessageView | undefined {
  const i = s.byId[id];
  return i === undefined ? undefined : s.messages[i];
}

function add(d: Draft, m: MessageView): void {
  const messages = d.messages();
  d.byId()[m.id] = messages.length;
  messages.push(m);
}

/** Withdraws the open questions `which` selects: their asker completed or was archived, or the project was archived. */
function withdraw(d: Draft, ts: string, which: (m: MessageView) => boolean): void {
  d.s.messages.forEach((m, i) => {
    if (m.state === 'open' && which(m)) d.messages()[i] = { ...m, state: 'withdrawn', stateAt: ts };
  });
}

/** Records an answer on its question: the first answer becomes `answerId`, and settles the question if it is still open. */
function settle(d: Draft, answer: EventOf<'message.agent'>): void {
  const questionId = answer.payload.reply_to;
  const i = questionId === undefined ? undefined : d.s.byId[questionId];
  const q = i === undefined ? undefined : d.s.messages[i];
  if (i === undefined || q?.kind !== 'question' || q.answerId !== undefined) return;
  const next: MessageView = { ...q, answerId: answer.id };
  if (q.state === 'open') {
    next.state = answer.payload.auto ? 'closed' : 'answered';
    next.stateAt = answer.ts;
  }
  d.messages()[i] = next;
}

/** Applies one event of MESSAGE_FOLD_TYPES. */
function step(d: Draft, e: StoredEvent): void {
  switch (e.type) {
    case 'agent.created':
      if (e.agent_id) d.agents()[e.agent_id] = { id: e.agent_id, role: e.payload.role, title: e.payload.title, status: 'idle', archived: false };
      return;
    case 'agent.status_changed': {
      const a = e.agent_id ? d.s.agents[e.agent_id] : undefined;
      if (!a) return;
      d.agents()[a.id] = { ...a, status: e.payload.status };
      // Only completion withdraws: a failed or cancelled asker keeps its questions open (both are usually temporary).
      if (e.payload.status === 'done') withdraw(d, e.ts, (m) => m.from === a.id);
      return;
    }
    case 'agent.archived': {
      const a = e.agent_id ? d.s.agents[e.agent_id] : undefined;
      if (!a) return;
      d.agents()[a.id] = { ...a, archived: true };
      withdraw(d, e.ts, (m) => m.from === a.id);
      return;
    }
    case 'project.archived':
      withdraw(d, e.ts, () => true);
      return;
    case 'message.agent': {
      const p = e.payload;
      // The runtime's reminders to Desk are for Desk alone: never traffic.
      if (!e.agent_id || p.kind === 'reminder') return;
      const m: MessageView = { id: e.id, ts: e.ts, from: p.from_agent_id, to: e.agent_id, kind: p.kind, text: p.text };
      if (p.reply_to !== undefined) m.replyTo = p.reply_to;
      if (p.auto) m.auto = true;
      if (p.tracked && p.kind === 'question') {
        m.tracked = true;
        m.state = 'open';
      }
      if (p.tool_call_id) m.toolCallId = p.tool_call_id;
      add(d, m);
      if (p.kind === 'answer') settle(d, e);
      return;
    }
    case 'message.user':
      // The user's messages to threads only: the user's conversation with Desk is not traffic.
      if (e.agent_id && d.s.agents[e.agent_id]?.role === 'thread') add(d, { id: e.id, ts: e.ts, from: 'user', to: e.agent_id, kind: 'user', text: e.payload.text });
      return;
    case 'run.started': {
      const q = e.payload.answering === undefined ? undefined : messageById(d.s, e.payload.answering);
      if (e.agent_id && q) d.answering()[e.agent_id] = { runId: e.payload.run_id, question: q.id, asker: q.from, since: e.ts };
      return;
    }
    case 'run.finished':
      if (e.agent_id && d.s.answering[e.agent_id]?.runId === e.payload.run_id) delete d.answering()[e.agent_id];
      return;
    default:
      return;
  }
}

/**
 * Folds events, in id order, into `from` (an empty state by default). Pure: `from` is never changed, events at or
 * below its `lastId` and of types the fold does not read are skipped, and `from` itself comes back when nothing was
 * folded. So folding in two halves equals folding at once, and the core can fold a project incrementally.
 */
export function foldMessages(events: Iterable<StoredEvent>, from: MessagesState = emptyMessages()): MessagesState {
  let d: Draft | undefined;
  for (const e of events) {
    const lastId = d ? d.s.lastId : from.lastId;
    if (e.id <= lastId || !FOLDED.has(e.type)) continue;
    d ??= draft(from);
    d.s.lastId = e.id;
    step(d, e);
  }
  return d ? d.s : from;
}

/** Folds one event (a session's `apply()`): the same state when the fold does not read the event. */
export function reduceMessages(s: MessagesState, e: StoredEvent): MessagesState {
  return foldMessages([e], s);
}

/** Open tracked questions `agentId` asked, oldest first. */
export function openFrom(s: MessagesState, agentId: string): MessageView[] {
  return s.messages.filter((m) => m.state === 'open' && m.from === agentId);
}

/** Open tracked questions to `agentId`, oldest first. */
export function openTo(s: MessagesState, agentId: string): MessageView[] {
  return s.messages.filter((m) => m.state === 'open' && m.to === agentId);
}

/** The messages between `a` and `b`, in either direction, oldest first. */
export function pair(s: MessagesState, a: string, b: string): MessageView[] {
  return s.messages.filter((m) => (m.from === a && m.to === b) || (m.from === b && m.to === a));
}

/** The thread's answer run in progress, if any. */
export function answeringOf(s: MessagesState, agentId: string): AnswerRun | undefined {
  return s.answering[agentId];
}

const isThread = (s: MessagesState, id: string) => s.agents[id]?.role === 'thread';

/** The latest `n` messages threads sent each other and the user sent threads, oldest first (Desk's Thread traffic). */
export function traffic(s: MessagesState, n: number): MessageView[] {
  if (n <= 0) return [];
  return s.messages.filter((m) => isThread(s, m.to) && (m.from === 'user' || isThread(s, m.from))).slice(-n);
}

/** The questions and notes `agentId` sent other threads at or after `sinceIso` (the sender cap; answers are exempt). */
export function sentSince(s: MessagesState, agentId: string, sinceIso: string): MessageView[] {
  return s.messages.filter((m) => m.from === agentId && m.ts >= sinceIso && (m.kind === 'question' || m.kind === 'note') && isThread(s, m.to));
}
