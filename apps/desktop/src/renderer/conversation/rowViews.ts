import { agentTitle, digestView, messageById, questionView, type ChatItem, type MessagesState, type QuestionView, type ToolCallView } from '@desk/client';
import { clip } from '@desk/protocol';
import { chatEventId } from './ChatItems';

/** The question an answer answers, quoted on one line: "↩ Auth API's question: “Which token format…”". */
export type AnswerQuote = { question: number; asker: string; text: string };

/** One pair of a between-threads digest, ready to show; the line opens `to`'s thread at message `at` (its latest). */
export type PairLine = { key: string; label: string; count: number; waiting?: { who: string; since: string }; to: string; at: number };

/**
 * What a chat row shows from the session's message fold (design spec §7 "Chat rows"). A question's state and a
 * digest's counts change when an answer lands on another agent's stream, which leaves the row's ChatItem unchanged, so
 * ConversationScreen derives these and passes them to the row as a prop.
 */
export type RowView = {
  /** The recipient of a message or steer row. */
  toTitle?: string;
  /** A tracked question's state line. */
  question?: QuestionView;
  /** An answer's quote of its question. */
  answers?: AnswerQuote;
  /** A digest's counts and pair lines. */
  digest?: { messages: number; open: number; pairs: PairLine[] };
  /** A tool row: the titles of the threads its calls name by id (ToolGroup's titleOf). */
  titles?: Record<string, string>;
};

/** The titles of the known threads that tool calls name in a `thread_id` argument (read_thread, stop_thread, review_diff…). */
function threadTitles(calls: readonly ToolCallView[], m: MessagesState): Record<string, string> | undefined {
  const titles: Record<string, string> = {};
  for (const c of calls) {
    let id: unknown;
    try {
      id = (JSON.parse(c.arguments) as { thread_id?: unknown } | null)?.thread_id;
    } catch {
      continue;
    }
    if (typeof id === 'string' && m.agents[id]) titles[id] = agentTitle(m, id);
  }
  return Object.keys(titles).length ? titles : undefined;
}

const oneLine = (text: string) => clip(text.replace(/\s+/g, ' ').trim(), 120);

/** A message's question state (tracked questions only) and, for an answer, the quote of its question. */
function exchange(m: MessagesState, id: number, replyTo: number | undefined): RowView {
  const v: RowView = {};
  const q = questionView(m, id);
  if (q) v.question = q;
  const asked = replyTo === undefined ? undefined : messageById(m, replyTo);
  if (asked) v.answers = { question: asked.id, asker: agentTitle(m, asked.from), text: oneLine(asked.text) };
  return v;
}

function rowView(item: ChatItem, m: MessagesState): RowView | undefined {
  switch (item.kind) {
    case 'message':
      return { toTitle: agentTitle(m, item.to), ...exchange(m, item.eventId, item.replyTo) };
    case 'agent': {
      const v = exchange(m, chatEventId(item), item.replyTo);
      return v.question || v.answers ? v : undefined;
    }
    case 'steer':
      return { toTitle: agentTitle(m, item.to) };
    case 'tools': {
      const titles = threadTitles(item.calls, m);
      return titles ? { titles } : undefined;
    }
    case 'digest': {
      const d = digestView(m, item.messageIds);
      return {
        digest: {
          messages: d.messages,
          open: d.open,
          pairs: d.pairs.map((p) => ({
            key: `${p.a} ${p.b}`,
            label: `${agentTitle(m, p.a)} ⇄ ${agentTitle(m, p.b)}`,
            count: p.count,
            ...(p.waiting ? { waiting: { who: agentTitle(m, p.waiting.agentId), since: p.waiting.since } } : {}),
            to: p.latestTo,
            at: p.latest,
          })),
        },
      };
    }
    default:
      return undefined;
  }
}

/** The view of every chat row that has one, by item id. */
export function rowViews(items: readonly ChatItem[], m: MessagesState): Map<string, RowView> {
  const views = new Map<string, RowView>();
  for (const item of items) {
    const v = rowView(item, m);
    if (v) views.set(item.id, v);
  }
  return views;
}

/** Keeps last render's object for every row whose view is unchanged, so memoised rows skip rendering. Views are small. */
export function keepStable(prev: ReadonlyMap<string, RowView>, next: Map<string, RowView>): Map<string, RowView> {
  for (const [id, v] of next) {
    const old = prev.get(id);
    if (old && JSON.stringify(old) === JSON.stringify(v)) next.set(id, old);
  }
  return next;
}

/** Whether a row shows a running age (an open question, or a digest holding one): only such rows get `now`. */
export const ticks = (v: RowView | undefined): boolean => v?.question?.state === 'open' || (v?.digest?.open ?? 0) > 0;
