import type { StoredEvent } from '@desk/protocol';

/**
 * How much of a long tool text a paired phone receives. A project's history goes to the page in full when it opens, and
 * tool calls and results (file contents, command output) make most of it: 45 MB for one long project, which an iPhone
 * never finishes loading. Phones get the start of each long text and a note; the Mac's apps still get everything.
 */
export const PHONE_TEXT_LIMIT = 2000;

const cut = (s: string): string =>
  s.length <= PHONE_TEXT_LIMIT ? s : `${s.slice(0, PHONE_TEXT_LIMIT)}\n… [${s.length - PHONE_TEXT_LIMIT} more characters: open this on your Mac to see all of it]`;

/** Every string in a parsed JSON value, cut to PHONE_TEXT_LIMIT. */
function cutDeep(v: unknown): unknown {
  if (typeof v === 'string') return cut(v);
  if (Array.isArray(v)) return v.map(cutDeep);
  if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, cutDeep(x)]));
  return v;
}

/** A tool call's arguments (a JSON string) with long values cut, still valid JSON when it was. */
function cutArguments(args: string): string {
  if (args.length <= PHONE_TEXT_LIMIT) return args;
  try {
    return JSON.stringify(cutDeep(JSON.parse(args) as unknown));
  } catch {
    return cut(args);
  }
}

/** The event as a paired phone receives it: tool calls, results and compaction summaries with long texts cut. */
export function slimForPhone(e: StoredEvent): StoredEvent {
  switch (e.type) {
    case 'tool.call':
      return e.payload.arguments.length <= PHONE_TEXT_LIMIT ? e : { ...e, payload: { ...e.payload, arguments: cutArguments(e.payload.arguments) } };
    case 'tool.result':
      return e.payload.content.length <= PHONE_TEXT_LIMIT ? e : { ...e, payload: { ...e.payload, content: cut(e.payload.content) } };
    case 'context.compacted':
      return e.payload.checkpoint.length <= PHONE_TEXT_LIMIT ? e : { ...e, payload: { ...e.payload, checkpoint: cut(e.payload.checkpoint) } };
    default:
      return e;
  }
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
