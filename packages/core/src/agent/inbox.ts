import { INBOX_EVENT_TYPES } from '@desk/protocol';
import type { EventStore } from '../events/store';
import { getAgent } from '../state/queries';

function pending(store: EventStore, agentId: string) {
  const agent = getAgent(store.db, agentId);
  if (!agent) throw new Error(`Unknown agent: ${agentId}`);
  return { agent, events: store.list({ agentId, after: agent.inbox_cursor, types: INBOX_EVENT_TYPES }) };
}

export function hasPendingInbox(store: EventStore, agentId: string): boolean {
  return pending(store, agentId).events.length > 0;
}

/** Marks all pending inbox events as delivered into the conversation at this point. */
export function drainInbox(store: EventStore, agentId: string, runId: string): number {
  const { agent, events } = pending(store, agentId);
  const last = events.at(-1);
  if (!last) return 0;
  store.append({ project_id: agent.project_id, agent_id: agentId, type: 'inbox.drained', payload: { run_id: runId, up_to: last.id } });
  return events.length;
}
