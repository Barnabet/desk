import { agentTitle, answeringOf, waitingOn, type MessagesState } from '@desk/client';
import type { AttentionItem } from '@desk/protocol';
import { duration } from './format';

const age = (iso: string, now: number) => duration(Math.max(0, now - Date.parse(iso)));

/**
 * Whether an attention item holds its agent until the user acts: an approval, or Desk's question. A stall, a report's
 * hand-off or a failure asks the user to look, but the agent does not wait on it.
 */
export const waitsOnYou = (i: AttentionItem) => i.kind === 'approval' || i.kind === 'question';

/**
 * What a waiting agent waits on, from the session's message fold (design spec §8 item 4): "waiting on you · 3m" when it
 * has an attention item that waits on the user (only then: vermilion means an attention item), "waiting on Frontend ·
 * 4m" or "waiting on Frontend and Desk · 4m" from its open questions (the oldest one's age), or null when neither says
 * anything.
 */
export function waitLabel(m: MessagesState, agentId: string, attention: readonly AttentionItem[], now: number): string | null {
  const own = attention.find((i) => i.agent_id === agentId && waitsOnYou(i));
  if (own) return `waiting on you · ${age(own.created_at, now)}`;
  const targets = waitingOn(m, agentId, attention);
  if (!targets.length) return null;
  const names = targets.map((t) => agentTitle(m, t.agentId));
  const list = names.length > 2 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names.join(' and ');
  return `waiting on ${list} · ${age(targets[0]!.since, now)}`;
}

/** "answering Frontend", "answering Desk" or "answering you" while a thread's answer run is in progress; else null. */
export function answeringLabel(m: MessagesState, threadId: string): string | null {
  const run = answeringOf(m, threadId);
  if (!run) return null;
  return `answering ${run.asker === 'user' ? 'you' : agentTitle(m, run.asker)}`;
}

/** What the agent one hop away needs from the user, by the kind of its attention item. */
const NEEDS: Record<AttentionItem['kind'], string> = {
  approval: 'needs your approval',
  question: 'needs your answer',
  needs_you: 'needs you',
  stalled: 'is stalled',
  failed: 'failed',
  paused: 'is paused',
};

/**
 * One hop past a wait (design spec §8 item 11): `text` follows the wait label ("→ needs your approval" when the agent
 * waited on needs the user, "→ Billing needs your approval" when an agent it asked does), `label` names it whole for the
 * link ("Frontend needs your approval"), and `item` is the attention item the link opens.
 */
export type WaitHop = { text: string; label: string; item: AttentionItem };

/**
 * The first of `agentId`'s wait targets (waitingOn order) that leads to an attention item, or null. An agent with an item
 * of its own that waits on the user waits on you (waitLabel), which is not a hop.
 */
export function waitHop(m: MessagesState, agentId: string, attention: readonly AttentionItem[]): WaitHop | null {
  if (attention.some((i) => i.agent_id === agentId && waitsOnYou(i))) return null;
  for (const t of waitingOn(m, agentId, attention)) {
    const who = t.attention ? t.agentId : t.via?.agentId;
    const item = t.attention ?? t.via?.attention;
    if (!who || !item) continue;
    const name = agentTitle(m, who);
    const needs = NEEDS[item.kind];
    return { text: t.attention ? `→ ${needs}` : `→ ${name} ${needs}`, label: `${name} ${needs}`, item };
  }
  return null;
}
