import { describe, expect, it } from 'vitest';
import type { EventBody, StoredEvent } from '@desk/protocol';
import { buildConversation } from './transcript';

let seq = 0;
const ev = (body: EventBody): StoredEvent => ({ ...body, id: ++seq, project_id: 'p', agent_id: 'a', ts: 't' }) as StoredEvent;

describe('buildConversation', () => {
  it('orders a full tool loop correctly', () => {
    seq = 0;
    const events = [
      ev({ type: 'message.user', payload: { text: 'do it' } }),
      ev({ type: 'inbox.drained', payload: { run_id: 'r', up_to: 1 } }),
      ev({ type: 'assistant.message', payload: { run_id: 'r', content: null, tool_calls: [{ id: 'c1', name: 'bash', arguments: '{"command":"ls"}' }] } }),
      ev({ type: 'tool.call', payload: { run_id: 'r', tool_call_id: 'c1', name: 'bash', arguments: '{"command":"ls"}' } }),
      ev({ type: 'tool.result', payload: { run_id: 'r', tool_call_id: 'c1', name: 'bash', status: 'ok', content: 'a.txt' } }),
      ev({ type: 'assistant.message', payload: { run_id: 'r', content: 'done', tool_calls: [] } }),
    ];
    expect(buildConversation(events)).toEqual([
      { role: 'user', content: 'do it' },
      { role: 'assistant', content: null, tool_calls: [{ id: 'c1', type: 'function', function: { name: 'bash', arguments: '{"command":"ls"}' } }] },
      { role: 'tool', tool_call_id: 'c1', content: 'a.txt' },
      { role: 'assistant', content: 'done' },
    ]);
  });

  it('places a mid-run message where it was drained, after tool results', () => {
    seq = 0;
    const events = [
      ev({ type: 'message.user', payload: { text: 'start' } }),
      ev({ type: 'inbox.drained', payload: { run_id: 'r', up_to: 1 } }),
      ev({ type: 'assistant.message', payload: { run_id: 'r', content: null, tool_calls: [{ id: 'c1', name: 'x', arguments: '{}' }] } }),
      ev({ type: 'message.user', payload: { text: 'also do Y' } }),
      ev({ type: 'message.user', payload: { text: 'and Z' } }),
      ev({ type: 'tool.result', payload: { run_id: 'r', tool_call_id: 'c1', name: 'x', status: 'ok', content: 'ok' } }),
      ev({ type: 'inbox.drained', payload: { run_id: 'r', up_to: 5 } }),
    ];
    expect(buildConversation(events).map((m) => m.role)).toEqual(['user', 'assistant', 'tool', 'user']);
    expect(buildConversation(events).at(-1)).toEqual({ role: 'user', content: 'also do Y\n\nand Z' });
  });

  it('excludes undrained messages and empty assistant messages', () => {
    seq = 0;
    const events = [
      ev({ type: 'message.user', payload: { text: 'a' } }),
      ev({ type: 'inbox.drained', payload: { run_id: 'r', up_to: 1 } }),
      ev({ type: 'assistant.message', payload: { run_id: 'r', content: null, tool_calls: [] } }),
      ev({ type: 'message.user', payload: { text: 'later' } }),
    ];
    expect(buildConversation(events)).toEqual([{ role: 'user', content: 'a' }]);
  });
});
