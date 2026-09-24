import type { Db } from '../db/open';
import type { EventStore } from '../events/store';
import { lastEvent } from '../state/queries';

/** Desk tools that change the project; after one, What's up is out of date until Desk rewrites it. */
const CHANGING_TOOLS = new Set([
  'spawn_thread',
  'message_thread',
  'stop_thread',
  'update_plan',
  'report',
  'resolve_approval',
  'skill_write',
  'service_start',
  'service_stop',
  'service_restart',
  'update_settings',
]);
/** Thread notices after which What's up is out of date. */
const CHANGING_NOTICES = new Set(['completed', 'failed', 'cancelled']);

/** How the runtime's reminders to Desk are labelled in its conversation: `[from the Desk runtime — reminder] …`. */
export const REMINDER_LABEL = 'the Desk runtime';
export const WHATS_UP_REMINDER =
  "Update What's up with update_whats_up: things changed this turn, and it is the first thing the user reads in the project. Then end your turn without writing anything else.";

/** The project's current What's up, as Desk last wrote it. */
export function latestWhatsUp(db: Db, deskId: string): { text: string; ts: string } | null {
  const e = lastEvent(db, deskId, 'whats_up.updated');
  return e?.type === 'whats_up.updated' ? { text: e.payload.text, ts: e.ts } : null;
}

/**
 * Whether Desk changed the project (or heard a thread finish) since it last wrote What's up and since it was last
 * reminded to, so each change gets at most one reminder.
 */
export function whatsUpStale(store: EventStore, deskId: string): boolean {
  const since = lastEvent(store.db, deskId, 'whats_up.updated')?.id ?? 0;
  const recent = store.list({ agentId: deskId, after: since, types: ['tool.call', 'message.agent'] });
  const lastReminder = recent.findLastIndex((e) => e.type === 'message.agent' && e.payload.kind === 'reminder');
  return recent
    .slice(lastReminder + 1)
    .some(
      (e) =>
        (e.type === 'tool.call' && CHANGING_TOOLS.has(e.payload.name)) ||
        (e.type === 'message.agent' && e.payload.from_agent_id !== deskId && CHANGING_NOTICES.has(e.payload.kind)),
    );
}
