import { agentTitle, answeringOf, waitingOn, type MessagesState } from '@desk/client';
import type { AttentionItem } from '@desk/protocol';
import { duration } from './format';

const age = (iso: string, now: number) => duration(Math.max(0, now - Date.parse(iso)));

/**
 * What a waiting agent waits on, from the session's message fold (design spec §8 item 4): "waiting on you · 3m" when it
 * has an attention item (only then: vermilion means an attention item), "waiting on Frontend · 4m" or "waiting on
 * Frontend and Desk · 4m" from its open questions (the oldest one's age), or null when neither says anything.
 */
export function waitLabel(m: MessagesState, agentId: string, attention: readonly AttentionItem[], now: number): string | null {
  const own = attention.find((i) => i.agent_id === agentId);
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
