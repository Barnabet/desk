import type { EventOf, StoredEvent } from '@desk/protocol';
import type { ChatMessage } from '../model/types';

function renderInboxItem(ev: EventOf<'message.user'> | EventOf<'message.agent'>): string {
  if (ev.type === 'message.user') return ev.payload.text;
  return `[from ${ev.payload.from_label} — ${ev.payload.kind}] ${ev.payload.text}`;
}

/** Rebuilds the model conversation for one agent from its events (in id order). */
export function buildConversation(events: StoredEvent[]): ChatMessage[] {
  const out: ChatMessage[] = [];
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
        if (batch.length) out.push({ role: 'user', content: batch.join('\n\n') });
        break;
      }
      case 'assistant.message': {
        const { content, tool_calls } = ev.payload;
        if (!content && tool_calls.length === 0) break;
        out.push(
          tool_calls.length
            ? {
                role: 'assistant',
                content,
                tool_calls: tool_calls.map((tc) => ({ id: tc.id, type: 'function' as const, function: { name: tc.name, arguments: tc.arguments } })),
              }
            : { role: 'assistant', content },
        );
        break;
      }
      case 'tool.result':
        out.push({ role: 'tool', tool_call_id: ev.payload.tool_call_id, content: ev.payload.content });
        break;
      default:
        break;
    }
  }
  return out;
}
