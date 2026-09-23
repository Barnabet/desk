import { describe, expect, it } from 'vitest';
import { EventBody, EphemeralEvent, INBOX_EVENT_TYPES } from '@desk/protocol';

describe('EventBody', () => {
  it('parses a tool.result event', () => {
    const parsed = EventBody.parse({
      type: 'tool.result',
      payload: { run_id: 'r1', tool_call_id: 'toolu_1', name: 'read_file', status: 'ok', content: 'hi' },
    });
    expect(parsed.type).toBe('tool.result');
  });

  it('parses an assistant.message with tool calls', () => {
    const parsed = EventBody.parse({
      type: 'assistant.message',
      payload: { run_id: 'r1', content: null, tool_calls: [{ id: 'call_1', name: 'bash', arguments: '{}' }] },
    });
    expect(parsed.type === 'assistant.message' && parsed.payload.tool_calls).toHaveLength(1);
  });

  it('rejects unknown event types', () => {
    expect(() => EventBody.parse({ type: 'nope', payload: {} })).toThrow();
  });

  it('rejects an empty user message', () => {
    expect(() => EventBody.parse({ type: 'message.user', payload: { text: '' } })).toThrow();
  });

  it('rejects an invalid agent status', () => {
    expect(() => EventBody.parse({ type: 'agent.status_changed', payload: { status: 'sleeping' } })).toThrow();
  });
});

describe('EphemeralEvent', () => {
  it('parses assistant.delta', () => {
    const e = EphemeralEvent.parse({ type: 'assistant.delta', project_id: 'p', agent_id: 'a', payload: { run_id: 'r', text: 'he' } });
    expect(e.payload.text).toBe('he');
  });
});

describe('INBOX_EVENT_TYPES', () => {
  it('contains message.user', () => {
    expect(INBOX_EVENT_TYPES).toContain('message.user');
  });
});
