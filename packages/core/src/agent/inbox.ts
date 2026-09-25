import { INBOX_EVENT_TYPES, type EventInput, type StoredEvent } from '@desk/protocol';
import type { EventStore } from '../events/store';
import { NotFoundError } from '../errors';
import { getAgent } from '../state/queries';

function pending(store: EventStore, agentId: string) {
  const agent = getAgent(store.db, agentId);
  if (!agent) throw new NotFoundError(`Unknown agent: ${agentId}`);
  return { agent, events: store.list({ agentId, after: agent.inbox_cursor, types: INBOX_EVENT_TYPES }) };
}

export function hasPendingInbox(store: EventStore, agentId: string): boolean {
  return pending(store, agentId).events.length > 0;
}

/** Inbox events stored after the agent's cursor (not yet in its conversation), oldest first. */
export function pendingInbox(store: EventStore, agentId: string): StoredEvent[] {
  return pending(store, agentId).events;
}

/** Marks all pending inbox events as delivered into the conversation at this point. */
export function drainInbox(store: EventStore, agentId: string, runId: string): number {
  const { agent, events } = pending(store, agentId);
  const last = events.at(-1);
  if (!last) return 0;
  store.append({ project_id: agent.project_id, agent_id: agentId, type: 'inbox.drained', payload: { run_id: runId, up_to: last.id } });
  return events.length;
}

/**
 * The `inbox.drained` event that delivers the items pending up to `upTo` (the cursor never moves back), returned for a
 * caller that appends it together with other events: an answer run's start (design spec §4.3). It is returned even
 * when it delivers nothing new, so the runtime line after the batch has a position. Null for an unknown agent.
 */
export function drainEvent(store: EventStore, agentId: string, runId: string, upTo: number): EventInput | null {
  const agent = getAgent(store.db, agentId);
  if (!agent) return null;
  return { project_id: agent.project_id, agent_id: agentId, type: 'inbox.drained', payload: { run_id: runId, up_to: Math.max(upTo, agent.inbox_cursor) } };
}
