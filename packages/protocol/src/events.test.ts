import { describe, expect, it } from 'vitest';
import { EventBody, EphemeralEvent, INBOX_EVENT_TYPES, ModelInfo } from '@desk/protocol';

describe('EventBody', () => {
  it('parses a tool.result event', () => {
    const parsed = EventBody.parse({
      type: 'tool.result',
      payload: { run_id: 'r1', tool_call_id: 'toolu_1', name: 'read_file', status: 'ok', content: 'hi' },
    });
    expect(parsed.type).toBe('tool.result');
  });

  it('parses a tool.result carrying images, and rejects a malformed digest', () => {
    const image = { sha256: 'a'.repeat(64), media_type: 'image/png', width: 1240, height: 1754, bytes: 312_000, name: 'page-1.png' };
    const parsed = EventBody.parse({
      type: 'tool.result',
      payload: { run_id: 'r1', tool_call_id: 'c1', name: 'view_image', status: 'ok', content: 'page-1.png', images: [image] },
    });
    expect(parsed.type === 'tool.result' && parsed.payload.images).toEqual([image]);
    const bad = { type: 'tool.result', payload: { run_id: 'r1', tool_call_id: 'c1', name: 'view_image', status: 'ok', content: '', images: [{ ...image, sha256: '../x' }] } };
    expect(() => EventBody.parse(bad)).toThrow();
    expect(() => EventBody.parse({ ...bad, payload: { ...bad.payload, images: [{ ...image, media_type: 'image/svg+xml' }] } })).toThrow();
  });

  it('parses images.withheld, which names at least one image by its tool result and digest', () => {
    const images = [{ tool_call_id: 'c1', sha256: 'b'.repeat(64), name: 'page-3.png' }];
    const parsed = EventBody.parse({ type: 'images.withheld', payload: { run_id: 'r1', images, reason: '400 Could not process image' } });
    expect(parsed.type === 'images.withheld' && parsed.payload.images).toEqual(images);
    expect(() => EventBody.parse({ type: 'images.withheld', payload: { run_id: 'r1', images: [], reason: 'x' } })).toThrow();
    expect(() => EventBody.parse({ type: 'images.withheld', payload: { run_id: 'r1', images: [{ ...images[0], sha256: 'x' }], reason: 'x' } })).toThrow();
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

  it('parses the messaging fields of message.agent and run.started', () => {
    const answer = { from_agent_id: 'T1', from_label: 'thread "Auth API" (T1)', kind: 'answer', text: 'JWT.', reply_to: 12, auto: true, tool_call_id: 'call_1' };
    expect(EventBody.parse({ type: 'message.agent', payload: answer }).payload).toEqual(answer);
    const question = { from_agent_id: 'T1', from_label: 'thread "Auth API" (T1)', kind: 'question', text: 'Which format?', tracked: true };
    expect(EventBody.parse({ type: 'message.agent', payload: question }).payload).toEqual(question);
    expect(EventBody.parse({ type: 'message.agent', payload: { from_agent_id: 'D', from_label: 'Desk', kind: 'start', text: 'Begin your assignment.' } }).type).toBe('message.agent');
    expect(() => EventBody.parse({ type: 'message.agent', payload: { ...question, tracked: false } })).toThrow();
    expect(() => EventBody.parse({ type: 'message.agent', payload: { ...answer, reply_to: 1.5 } })).toThrow();
    const started = { run_id: 'r1', model: 'm', answering: 12 };
    expect(EventBody.parse({ type: 'run.started', payload: started }).payload).toEqual(started);
  });
});

describe('EphemeralEvent', () => {
  it('parses assistant.delta', () => {
    const e = EphemeralEvent.parse({ type: 'assistant.delta', project_id: 'p', agent_id: 'a', payload: { run_id: 'r', text: 'he' } });
    expect(e.type === 'assistant.delta' && e.payload.text).toBe('he');
  });
});

describe('INBOX_EVENT_TYPES', () => {
  it('contains message.user', () => {
    expect(INBOX_EVENT_TYPES).toContain('message.user');
  });
});

describe('attention.dismissed', () => {
  it('is a valid event body', () => {
    expect(EventBody.parse({ type: 'attention.dismissed', payload: { item_id: 'report:12:0' } })).toMatchObject({ type: 'attention.dismissed' });
    expect(() => EventBody.parse({ type: 'attention.dismissed', payload: { item_id: '' } })).toThrow();
  });
});

describe('ModelInfo', () => {
  it('defaults vision to true and keeps an explicit false', () => {
    const base = { id: 'm', family: 'gpt', context_window: 1000, max_output_tokens: 100, concurrency: 1 };
    expect(ModelInfo.parse(base).vision).toBe(true);
    expect(ModelInfo.parse({ ...base, vision: false }).vision).toBe(false);
  });
});
