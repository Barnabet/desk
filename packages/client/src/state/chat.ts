import type { AgentMessageKind, EphemeralEvent, StoredEvent, ToolImage, ToolResultStatus } from '@desk/protocol';

/** One tool call and its result; `images` are the stored images a view_image result showed the model. */
export type ToolCallView = { id: string; name: string; arguments: string; status: 'running' | ToolResultStatus; content: string | null; images?: ToolImage[] };

export type ChatItem =
  | { kind: 'user'; id: string; ts: string; text: string }
  | { kind: 'assistant'; id: string; ts: string; runId: string; text: string; streaming: boolean; interrupted?: boolean }
  | { kind: 'tools'; id: string; ts: string; calls: ToolCallView[] }
  | { kind: 'agent'; id: string; ts: string; fromAgentId: string; fromLabel: string; messageKind: AgentMessageKind; text: string }
  | { kind: 'report'; id: string; ts: string; eventId: number; headline: string; progress: string; needsYou: string[]; results: string[] }
  | { kind: 'question'; id: string; ts: string; eventId: number; question: string; options: string[]; answered: boolean }
  | { kind: 'notice'; id: string; ts: string; level: 'info' | 'warning' | 'error'; code: string; message: string }
  | { kind: 'compacted'; id: string; ts: string };

/** The conversation of one agent (Desk): its messages, streamed replies, tool groups, reports, questions, and project notices. */
export type ChatState = { agentId: string; items: ChatItem[]; lastSeq: number };

export const emptyChat = (agentId: string): ChatState => ({ agentId, items: [], lastSeq: 0 });

const streamId = (runId: string) => `run:${runId}`;

/** Shared by chat and transcript: tool call grouping and streamed text. */
export function pushToolCall<T extends { kind: string; id: string }>(items: T[], call: ToolCallView, ts: string, eventId: number): T[] {
  const last = items.at(-1) as (T & { kind: 'tools'; calls: ToolCallView[] }) | undefined;
  if (last?.kind === 'tools') return [...items.slice(0, -1), { ...last, calls: [...last.calls, call] }];
  return [...items, { kind: 'tools', id: `tools:${eventId}`, ts, calls: [call] } as unknown as T];
}

export function resolveToolCall<T extends { kind: string }>(items: T[], id: string, status: ToolResultStatus, content: string, images?: ToolImage[]): T[] {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i] as T & { calls?: ToolCallView[] };
    if (it.kind !== 'tools' || !it.calls?.some((c) => c.id === id)) continue;
    const next = items.slice();
    next[i] = { ...it, calls: it.calls.map((c) => (c.id === id ? { ...c, status, content, ...(images?.length ? { images } : {}) } : c)) } as T;
    return next;
  }
  return items;
}

export function appendDelta<T extends { kind: string; id: string }>(items: T[], runId: string, text: string): T[] {
  const id = streamId(runId);
  const i = items.findIndex((x) => x.id === id);
  if (i < 0) return [...items, { kind: 'assistant', id, ts: '', runId, text, streaming: true } as unknown as T];
  const next = items.slice();
  const cur = next[i] as T & { text: string };
  next[i] = { ...cur, text: cur.text + text };
  return next;
}

export function finalizeRun<T extends { kind: string; id: string }>(items: T[], e: StoredEvent & { type: 'assistant.message' }): T[] {
  const id = streamId(e.payload.run_id);
  const i = items.findIndex((x) => x.id === id);
  const content = e.payload.content?.trim() ? e.payload.content : null;
  const final = content ? ({ kind: 'assistant', id: `e:${e.id}`, ts: e.ts, runId: e.payload.run_id, text: content, streaming: false } as unknown as T) : null;
  if (i < 0) return final ? [...items, final] : items;
  const next = items.slice();
  if (final) next[i] = final;
  else next.splice(i, 1);
  return next;
}

export function interruptRun<T extends { kind: string; id: string }>(items: T[], runId: string): T[] {
  const i = items.findIndex((x) => x.id === streamId(runId));
  if (i < 0) return items;
  const next = items.slice();
  next[i] = { ...(next[i] as T), streaming: false, interrupted: true } as T;
  return next;
}

export function reduceChat(prev: ChatState, e: StoredEvent): ChatState {
  if (e.id <= prev.lastSeq) return prev;
  const s: ChatState = { ...prev, lastSeq: e.id };
  const mine = e.agent_id === s.agentId;
  const id = `e:${e.id}`;
  switch (e.type) {
    case 'message.user':
      if (!mine) return s;
      return {
        ...s,
        items: [...s.items.map((it) => (it.kind === 'question' && !it.answered ? { ...it, answered: true } : it)), { kind: 'user', id, ts: e.ts, text: e.payload.text }],
      };
    case 'assistant.message':
      return mine ? { ...s, items: finalizeRun(s.items, e) } : s;
    case 'run.finished':
      return mine && e.payload.reason === 'error' ? { ...s, items: interruptRun(s.items, e.payload.run_id) } : s;
    case 'tool.call':
      return mine
        ? { ...s, items: pushToolCall(s.items, { id: e.payload.tool_call_id, name: e.payload.name, arguments: e.payload.arguments, status: 'running', content: null }, e.ts, e.id) }
        : s;
    case 'tool.result':
      return mine ? { ...s, items: resolveToolCall(s.items, e.payload.tool_call_id, e.payload.status, e.payload.content, e.payload.images) } : s;
    case 'message.agent':
      // The runtime's reminders to Desk are for Desk alone.
      return mine && e.payload.kind !== 'reminder'
        ? { ...s, items: [...s.items, { kind: 'agent', id, ts: e.ts, fromAgentId: e.payload.from_agent_id, fromLabel: e.payload.from_label, messageKind: e.payload.kind, text: e.payload.text }] }
        : s;
    case 'report':
      return mine
        ? { ...s, items: [...s.items, { kind: 'report', id, ts: e.ts, eventId: e.id, headline: e.payload.headline, progress: e.payload.progress, needsYou: e.payload.needs_you, results: e.payload.results }] }
        : s;
    case 'question.asked':
      return mine
        ? { ...s, items: [...s.items, { kind: 'question', id, ts: e.ts, eventId: e.id, question: e.payload.question, options: e.payload.options ?? [], answered: false }] }
        : s;
    case 'system.notice':
      return { ...s, items: [...s.items, { kind: 'notice', id, ts: e.ts, level: e.payload.level, code: e.payload.code, message: e.payload.message }] };
    case 'context.compacted':
      return mine ? { ...s, items: [...s.items, { kind: 'compacted', id, ts: e.ts }] } : s;
    default:
      return s;
  }
}

/** Applies a streamed text chunk (ephemeral `assistant.delta`) for this agent. */
export function applyChatDelta(s: ChatState, e: EphemeralEvent): ChatState {
  if (e.type !== 'assistant.delta' || e.agent_id !== s.agentId) return s;
  return { ...s, items: appendDelta(s.items, e.payload.run_id, e.payload.text) };
}
