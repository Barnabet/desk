import type { EventOf, StoredEvent } from '@desk/protocol';
import type { ChatMessage } from '../model/types';

/** A conversation message and the id of the event that produced it (ids increase along the conversation). */
export type TaggedMessage = { message: ChatMessage; eventId: number };

export const CHECKPOINT_HEADER = '[Checkpoint — summary of the earlier conversation]';

function renderInboxItem(ev: EventOf<'message.user'> | EventOf<'message.agent'>): string {
  if (ev.type === 'message.user') return ev.payload.text;
  return `[from ${ev.payload.from_label} — ${ev.payload.kind}] ${ev.payload.text}`;
}

/** Rebuilds the full model conversation for one agent from its events (in id order), ignoring checkpoints. */
export function buildTaggedConversation(events: StoredEvent[]): TaggedMessage[] {
  const out: TaggedMessage[] = [];
  const pending: Array<EventOf<'message.user'> | EventOf<'message.agent'>> = [];

  for (const ev of events) {
    switch (ev.type) {
      case 'message.user':
      case 'message.agent':
        pending.push(ev);
        break;
      case 'inbox.drained': {
        const batch: string[] = [];
        while (pending.length && pending[0]!.id <= ev.payload.up_to) batch.push(renderInboxItem(pending.shift()!));
        if (batch.length) out.push({ message: { role: 'user', content: batch.join('\n\n') }, eventId: ev.id });
        break;
      }
      case 'assistant.message': {
        const { content, tool_calls } = ev.payload;
        if (!content && tool_calls.length === 0) break;
        out.push({
          eventId: ev.id,
          message: tool_calls.length
            ? {
                role: 'assistant',
                content,
                tool_calls: tool_calls.map((tc) => ({ id: tc.id, type: 'function' as const, function: { name: tc.name, arguments: tc.arguments } })),
              }
            : { role: 'assistant', content },
        });
        break;
      }
      case 'tool.result':
        out.push({ message: { role: 'tool', tool_call_id: ev.payload.tool_call_id, content: ev.payload.content }, eventId: ev.id });
        break;
      default:
        break;
    }
  }
  return out;
}

/** Applies a checkpoint: drops the messages it summarises and puts the summary in front of the rest. */
export function applyCheckpoint(messages: TaggedMessage[], checkpoint: EventOf<'context.compacted'> | undefined): TaggedMessage[] {
  if (!checkpoint) return messages;
  const tail = messages.filter((m) => m.eventId > checkpoint.payload.up_to);
  const summary = `${CHECKPOINT_HEADER}\n${checkpoint.payload.checkpoint}`;
  const first = tail[0];
  if (first?.message.role === 'user') {
    return [{ eventId: first.eventId, message: { role: 'user', content: `${summary}\n\n${first.message.content}` } }, ...tail.slice(1)];
  }
  return [{ eventId: checkpoint.payload.up_to, message: { role: 'user', content: summary } }, ...tail];
}

export function latestCheckpoint(events: StoredEvent[]): EventOf<'context.compacted'> | undefined {
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i]!;
    if (ev.type === 'context.compacted') return ev;
  }
  return undefined;
}

/** The conversation the model sees: the latest checkpoint (if any) followed by everything after it. */
export function buildCurrentConversation(events: StoredEvent[]): TaggedMessage[] {
  return applyCheckpoint(buildTaggedConversation(events), latestCheckpoint(events));
}

export function buildConversation(events: StoredEvent[]): ChatMessage[] {
  return buildCurrentConversation(events).map((m) => m.message);
}
