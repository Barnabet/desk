import { agentTitle, pair, type MessagesState, type MessageView } from '@desk/client';

/** One message of the pair sheet, and the thread whose transcript shows it. */
export type PairEntry = {
  message: MessageView;
  /**
   * Its recipient when that is a thread (the message is on its stream); otherwise, for a message to Desk, the thread
   * that sent it (its transcript shows the send as a card, or the stop it came from: see `stopAt`). Null when neither
   * is a thread.
   */
  shownIn: string | null;
};
/** A row of the sheet: a message and, for a question, the answers nested under it (the first one settled it). */
export type PairRow = PairEntry & { answers: PairEntry[] };
export type PairView = { title: string; rows: PairRow[] };

const isThread = (m: MessagesState, id: string) => m.agents[id]?.role === 'thread';

function entry(m: MessagesState, message: MessageView): PairEntry {
  return { message, shownIn: isThread(m, message.to) ? message.to : isThread(m, message.from) ? message.from : null };
}

/**
 * The messages between `a` and `b`, oldest first (design spec §8 item 8): each answer nested under its question, and
 * `start` hidden (the brief already says it). Desk is named second, "Auth API ⇄ Desk"; two threads keep the order given.
 */
export function pairView(m: MessagesState, a: string, b: string): PairView {
  const [x, y] = m.agents[a]?.role === 'desk' ? [b, a] : [a, b];
  const rows: PairRow[] = [];
  const questions = new Map<number, PairRow>();
  for (const message of pair(m, x, y)) {
    if (message.kind === 'start') continue;
    const asked = message.kind === 'answer' && message.replyTo !== undefined ? questions.get(message.replyTo) : undefined;
    if (asked) {
      asked.answers.push(entry(m, message));
      continue;
    }
    const row: PairRow = { ...entry(m, message), answers: [] };
    if (message.kind === 'question') questions.set(message.id, row);
    rows.push(row);
  }
  return { title: `${agentTitle(m, x)} ⇄ ${agentTitle(m, y)}`, rows };
}
