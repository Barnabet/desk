import { messageById, openFrom, type AttentionItem, type MessagesState, type MessageView } from '@desk/protocol';

// The message fold is the protocol's, shared with the core (design spec §1.4). The selectors below are the views'.
export {
  answeringOf,
  emptyMessages,
  foldMessages,
  messageById,
  MESSAGE_FOLD_TYPES,
  openFrom,
  openTo,
  pair,
  reduceMessages,
  sentSince,
  traffic,
} from '@desk/protocol';
export type { AgentEntry, AnswerRun, MessagesState, MessageView } from '@desk/protocol';

/** How the UI names an agent: "Desk", a thread's title (archived threads keep theirs), or "a thread". */
export function agentTitle(s: MessagesState, id: string): string {
  const a = s.agents[id];
  if (a?.role === 'desk') return 'Desk';
  return a?.title ?? 'a thread';
}

const isThread = (s: MessagesState, id: string) => s.agents[id]?.role === 'thread';

/** What an agent waits on: the recipient of one of its open questions (design spec §7, §8 items 4 and 11). */
export type WaitTarget = {
  agentId: string;
  /** The agent's oldest open question to it. */
  question: number;
  since: string;
  /** The target's own attention item: the target needs the user. */
  attention?: AttentionItem;
  /** Otherwise one hop: the first agent the target asked that needs the user. */
  via?: { agentId: string; question: number; since: string; attention: AttentionItem };
};

const itemOf = (attention: readonly AttentionItem[], agentId: string) => attention.find((i) => i.agent_id === agentId);

/**
 * The recipients of `agentId`'s open questions, oldest first and each once, with the question's time. A recipient that
 * needs the user carries its attention item; otherwise `via` names the first agent it asked that does. The hop never
 * leads back to `agentId` (the cycle guard).
 */
export function waitingOn(s: MessagesState, agentId: string, attention: readonly AttentionItem[]): WaitTarget[] {
  const targets: WaitTarget[] = [];
  for (const q of openFrom(s, agentId)) {
    if (targets.some((t) => t.agentId === q.to)) continue;
    const target: WaitTarget = { agentId: q.to, question: q.id, since: q.ts };
    const own = itemOf(attention, q.to);
    if (own) target.attention = own;
    else {
      for (const next of openFrom(s, q.to)) {
        const item = next.to === agentId ? undefined : itemOf(attention, next.to);
        if (item) {
          target.via = { agentId: next.to, question: next.id, since: next.ts, attention: item };
          break;
        }
      }
    }
    targets.push(target);
  }
  return targets;
}

/** The messages threads sent each other, and the user sent threads, after event `eventId`, oldest first. */
export function trafficSince(s: MessagesState, eventId: number): MessageView[] {
  return s.messages.filter((m) => m.id > eventId && isThread(s, m.to) && (m.from === 'user' || isThread(s, m.from)));
}

/** A tracked question's state line (design spec §8 item 1). Derived at render time: its answer lands on another stream. */
export type QuestionView = { state: 'open' | 'answered' | 'closed' | 'withdrawn'; answerId?: number; answeredAt?: string; since: string; toTitle: string };

/** The state line of tracked question `id`; undefined for anything else (untracked questions have no state). */
export function questionView(s: MessagesState, id: number): QuestionView | undefined {
  const q = messageById(s, id);
  if (!q?.tracked || !q.state) return undefined;
  const view: QuestionView = { state: q.state, since: q.ts, toTitle: agentTitle(s, q.to) };
  const answer = q.answerId === undefined ? undefined : messageById(s, q.answerId);
  if (answer) {
    view.answerId = answer.id;
    view.answeredAt = answer.ts;
  }
  return view;
}

/** One pair of threads in a digest; `a` sent the pair's first message. */
export type DigestPair = {
  a: string;
  b: string;
  count: number;
  /** The pair's latest message and its recipient: where the digest's pair line jumps (design spec §8 item 2). */
  latest: number;
  latestTo: string;
  /** The asker of the pair's oldest question that is still open. */
  waiting?: { agentId: string; since: string };
};
export type DigestView = { messages: number; open: number; pairs: DigestPair[] };

/** A between-threads digest's counts, from the fold at render time, so the open count drops when an answer lands elsewhere. */
export function digestView(s: MessagesState, ids: readonly number[]): DigestView {
  const pairs = new Map<string, DigestPair>();
  let messages = 0;
  let open = 0;
  for (const id of ids) {
    const m = messageById(s, id);
    if (!m) continue;
    messages++;
    const key = [m.from, m.to].sort().join(' ');
    let p = pairs.get(key);
    if (!p) pairs.set(key, (p = { a: m.from, b: m.to, count: 0, latest: m.id, latestTo: m.to }));
    p.count++;
    if (m.id >= p.latest) {
      p.latest = m.id;
      p.latestTo = m.to;
    }
    if (m.state === 'open') {
      open++;
      p.waiting ??= { agentId: m.from, since: m.ts };
    }
  }
  return { messages, open, pairs: [...pairs.values()] };
}
