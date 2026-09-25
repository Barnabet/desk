import { groupAttention, type AttentionBays } from '@desk/client';
import type { AttentionItem } from '@desk/protocol';
import { STRIP_CODE } from '@desk/bff/contract';
import { duration } from './format';

export const BAYS: Array<{ key: keyof AttentionBays; name: string; sub: string }> = [
  { key: 'clearance', name: 'CLEARANCE', sub: 'Approvals' },
  { key: 'queries', name: 'QUERIES', sub: 'Questions' },
  { key: 'handoffs', name: 'HANDOFFS', sub: 'From reports' },
  { key: 'holding', name: 'HOLDING', sub: 'Stalled, failed or paused' },
];

/** Items in rack order (bay by bay, oldest first within a bay), which is also the J/K order. */
export function rackOrder(items: AttentionItem[]): { bays: AttentionBays; flat: AttentionItem[] } {
  const g = groupAttention(items);
  const bays = Object.fromEntries(Object.entries(g).map(([k, v]) => [k, [...v].sort((a, b) => a.created_at.localeCompare(b.created_at))])) as AttentionBays;
  return { bays, flat: BAYS.flatMap((b) => bays[b.key]) };
}

const TWO_HOURS = 2 * 3_600_000;

/** Fraction of the 0–2h wait gauge, never quite empty so a fresh item still shows a sliver. */
export const gauge = (createdAt: string, now: number) => Math.min(1, Math.max(0.04, (now - Date.parse(createdAt)) / TWO_HOURS));

export const waited = (createdAt: string, now: number) => duration(Math.max(0, now - Date.parse(createdAt)));

/** The strip's third column: who is involved and a short tag. */
export function stripWho(i: AttentionItem, threadTitle: (id: string) => string | null): { label: string; name: string; tag: string } {
  switch (i.kind) {
    case 'approval': {
      const tool = /wants to run (.+)$/.exec(i.title)?.[1] ?? '';
      return i.ref.thread_id ? { label: 'Thread', name: threadTitle(i.ref.thread_id) ?? 'A thread', tag: tool } : { label: 'Asked by', name: 'Desk', tag: tool };
    }
    case 'question':
      return { label: 'Asked by', name: 'Desk', tag: i.ref.options?.length ? `${i.ref.options.length} options` : 'free answer' };
    case 'needs_you':
      return { label: 'From', name: "Desk's report", tag: 'needs_you' };
    case 'paused':
      return { label: 'Project', name: i.project_name, tag: 'paused' };
    default:
      return { label: 'Thread', name: (i.ref.thread_id && threadTitle(i.ref.thread_id)) || i.title.replace(/ (has stalled|failed)$/, ''), tag: i.kind };
  }
}

export const KIND_NAME: Record<AttentionItem['kind'], string> = {
  approval: 'Clearance request',
  question: 'Question from Desk',
  needs_you: 'From a report',
  stalled: 'Stalled thread',
  failed: 'Failed thread',
  paused: 'Paused project',
};

export { STRIP_CODE };
