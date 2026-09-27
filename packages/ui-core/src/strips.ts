import { groupAttention, type AttentionBays } from '@desk/client';
import type { AttentionItem } from '@desk/protocol';
import { STRIP_CODE } from '@desk/bff/contract';
import { duration } from './format';
import type { Route } from './router';

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

/** The automation an item is about, read from the daemon's titles (`packages/core/src/state/attention.ts`). */
function automationName(i: AttentionItem): string {
  switch (i.kind) {
    case 'automation_failed':
      return i.title.replace(/ failed$/, '');
    case 'automation_enable_request':
      return i.title.replace(/^Desk proposes turning on /, '');
    case 'automation_grants_suspended':
      return i.title.replace(/ changed: its grants are suspended$/, '');
    default:
      // Asks ("<title>: <question>", "<title> · <step> wants to run …") and step agents' approvals ("<title> · <step> wants to run …").
      return /^(.*?)(?: · |: )/.exec(i.title)?.[1] ?? i.title;
  }
}

/**
 * Where an automation item opens (spec §8.4): the run for asks, failures and step agents' approvals, the Grants tab
 * for suspended grants, the automation for Desk's turn-on request. Null for items about no automation.
 */
export function automationTarget(i: AttentionItem): Route | null {
  const automationId = i.ref.automation_id;
  if (!automationId) return null;
  const at = { name: 'project' as const, id: i.project_id, tab: 'automations' as const, automationId };
  if (i.kind === 'automation_grants_suspended') return { ...at, view: 'grants' };
  if (i.ref.run_id && i.kind !== 'automation_enable_request') return { ...at, view: 'runs', runId: i.ref.run_id };
  return { ...at, view: 'design' };
}

/** The strip's third column: who is involved and a short tag. */
export function stripWho(i: AttentionItem, threadTitle: (id: string) => string | null): { label: string; name: string; tag: string } {
  switch (i.kind) {
    case 'approval': {
      const tool = /wants to run (.+)$/.exec(i.title)?.[1] ?? '';
      if (i.ref.automation_id) return { label: 'Automation', name: automationName(i), tag: tool };
      return i.ref.thread_id ? { label: 'Thread', name: threadTitle(i.ref.thread_id) ?? 'A thread', tag: tool } : { label: 'Asked by', name: 'Desk', tag: tool };
    }
    case 'question':
      return { label: 'Asked by', name: 'Desk', tag: i.ref.options?.length ? `${i.ref.options.length} options` : 'free answer' };
    case 'needs_you':
      return { label: 'From', name: "Desk's report", tag: 'needs_you' };
    case 'paused':
      return { label: 'Project', name: i.project_name, tag: 'paused' };
    case 'automation_ask':
      return { label: 'Automation', name: automationName(i), tag: / wants to run /.test(i.title) ? 'script' : 'question' };
    case 'automation_failed':
      return { label: 'Automation', name: automationName(i), tag: 'failed' };
    case 'automation_enable_request':
      return { label: 'Automation', name: automationName(i), tag: 'turn on?' };
    case 'automation_grants_suspended':
      return { label: 'Automation', name: automationName(i), tag: 'grants' };
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
  automation_ask: 'Automation question',
  automation_failed: 'Failed automation',
  automation_enable_request: 'Automation to turn on',
  automation_grants_suspended: 'Grants suspended',
};

export { STRIP_CODE };
