import type { AttentionItem, EventType, StoredEvent } from '@desk/protocol';

/** The four bays of the attention strip rack. */
export type AttentionBays = { clearance: AttentionItem[]; queries: AttentionItem[]; handoffs: AttentionItem[]; holding: AttentionItem[] };

export function groupAttention(items: AttentionItem[]): AttentionBays {
  const bays: AttentionBays = { clearance: [], queries: [], handoffs: [], holding: [] };
  for (const i of items) {
    if (i.kind === 'approval') bays.clearance.push(i);
    else if (i.kind === 'question') bays.queries.push(i);
    else if (i.kind === 'needs_you') bays.handoffs.push(i);
    else bays.holding.push(i);
  }
  return bays;
}

const ATTENTION_TYPES = new Set<EventType>([
  'approval.requested',
  'approval.resolved',
  'question.asked',
  'message.user',
  'report',
  'message.agent',
  'agent.status_changed',
  'agent.archived',
  'attention.dismissed',
  'project.created',
  'project.archived',
  'tool.call',
  // A pause (the `wakes_paused` notice) may be the only news: nothing else nearby changes attention (design spec §5.4).
  'system.notice',
]);

const OVERVIEW_TYPES = new Set<EventType>([
  ...ATTENTION_TYPES,
  'project.updated',
  'agent.created',
  'agent.skills_changed',
  'agent.revision',
  'plan.updated',
  'tool.result',
]);

/** Whether an event can change the attention list (the broker refetches, debounced). */
export const affectsAttention = (e: StoredEvent): boolean => ATTENTION_TYPES.has(e.type);
/** Whether an event can change the cross-project overview. */
export const affectsOverview = (e: StoredEvent): boolean => OVERVIEW_TYPES.has(e.type);
