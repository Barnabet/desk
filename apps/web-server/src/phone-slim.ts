import type { StoredEvent } from '@desk/protocol';

/**
 * How much of a long text a paired phone receives. A project's history goes to the page in full when it opens, and
 * tool calls and results (file contents, command output, repeated in each assistant message's tool calls) make most of
 * it: 45 MB for one long project, which an iPhone never finishes loading. Phones get the start of each long text and a
 * note; the Mac's apps still get everything.
 */
export const PHONE_TEXT_LIMIT = 2000;
/** Tool texts are cut shorter: a phone shows a glimpse of them, and there are thousands. */
export const PHONE_TOOL_LIMIT = 500;

const cut = (s: string, limit: number): string =>
  s.length <= limit ? s : `${s.slice(0, limit)}\n… [${s.length - limit} more characters: open this on your Mac to see all of it]`;

/** Every string in a parsed JSON value, cut to `limit`. */
function cutDeep(v: unknown, limit: number): unknown {
  if (typeof v === 'string') return cut(v, limit);
  if (Array.isArray(v)) return v.map((x) => cutDeep(x, limit));
  if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, cutDeep(x, limit)]));
  return v;
}

/** A tool call's arguments (a JSON string) with long values cut, still valid JSON when it was. */
function cutArguments(args: string): string {
  if (args.length <= PHONE_TOOL_LIMIT) return args;
  try {
    return JSON.stringify(cutDeep(JSON.parse(args) as unknown, PHONE_TOOL_LIMIT));
  } catch {
    return cut(args, PHONE_TOOL_LIMIT);
  }
}

/**
 * The event as a paired phone receives it: tool calls (also those inside assistant messages), tool results and
 * compaction summaries with long texts cut. What an agent says, and what an approval asks, stay whole.
 */
export function slimForPhone(e: StoredEvent): StoredEvent {
  switch (e.type) {
    case 'assistant.message':
      return e.payload.tool_calls.every((c) => c.arguments.length <= PHONE_TOOL_LIMIT)
        ? e
        : { ...e, payload: { ...e.payload, tool_calls: e.payload.tool_calls.map((c) => ({ ...c, arguments: cutArguments(c.arguments) })) } };
    case 'tool.call':
      return e.payload.arguments.length <= PHONE_TOOL_LIMIT ? e : { ...e, payload: { ...e.payload, arguments: cutArguments(e.payload.arguments) } };
    case 'tool.result':
      return e.payload.content.length <= PHONE_TOOL_LIMIT ? e : { ...e, payload: { ...e.payload, content: cut(e.payload.content, PHONE_TOOL_LIMIT) } };
    case 'context.compacted':
      return e.payload.checkpoint.length <= PHONE_TEXT_LIMIT ? e : { ...e, payload: { ...e.payload, checkpoint: cut(e.payload.checkpoint, PHONE_TEXT_LIMIT) } };
    default:
      return e;
  }
}

/** What only an agent's transcript shows: the bulk of a project's history, left out of a phone's backfill for threads. */
const TRANSCRIPT_ONLY = new Set<string>(['assistant.message', 'tool.call', 'tool.result', 'usage', 'context.compacted']);

/**
 * A backfill page as a paired phone receives it: without the threads' transcripts (everything but Desk's own), which the
 * phone fetches with `threads.transcript` when it opens a thread. Desk's conversation, the threads' states, questions,
 * reports and approvals all stay. `deskId` unknown (undefined) leaves the page whole.
 */
export function trimBackfillForPhone(events: StoredEvent[], deskId: string | undefined): StoredEvent[] {
  if (deskId === undefined) return events;
  return events.filter((e) => !(e.agent_id && e.agent_id !== deskId && TRANSCRIPT_ONLY.has(e.type)));
}

/** A push payload as a paired phone receives it: `desk:events` batches and `desk:event`s slimmed, others as they are. */
export function slimPushForPhone(channel: string, payload: unknown): unknown {
  if (channel === 'desk:events' && Array.isArray(payload)) return (payload as StoredEvent[]).map(slimForPhone);
  if (channel === 'desk:event' && payload && typeof payload === 'object') return slimForPhone(payload as StoredEvent);
  return payload;
}

/** Operations whose result is an event page (`{ events, next_after }`), slimmed for phones like pushes. */
const EVENT_PAGES = new Set(['projects.events', 'projects.chat', 'threads.transcript']);

/** An /rpc result as a paired phone receives it. */
export function slimResultForPhone(op: string, value: unknown): unknown {
  if (!EVENT_PAGES.has(op) || !value || typeof value !== 'object' || !Array.isArray((value as { events?: unknown }).events)) return value;
  const page = value as { events: StoredEvent[] };
  return { ...page, events: page.events.map(slimForPhone) };
}
