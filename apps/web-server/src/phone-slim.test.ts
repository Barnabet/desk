import { describe, expect, it } from 'vitest';
import type { StoredEvent } from '@desk/protocol';
import { PHONE_TEXT_LIMIT, PHONE_TOOL_LIMIT, slimForPhone, slimPushForPhone, slimResultForPhone } from './phone-slim';

const long = 'x'.repeat(PHONE_TEXT_LIMIT + 500);
const over = PHONE_TEXT_LIMIT + 500 - PHONE_TOOL_LIMIT;
const base = { id: 7, ts: '2026-10-02T19:00:00Z', project_id: 'P1', agent_id: 'A1' };
const result = { ...base, type: 'tool.result', payload: { run_id: 'r', tool_call_id: 't', name: 'bash', status: 'ok', content: long } } as unknown as StoredEvent;
const call = { ...base, type: 'tool.call', payload: { run_id: 'r', tool_call_id: 't', name: 'write_file', arguments: JSON.stringify({ path: 'a.txt', content: long }) } } as unknown as StoredEvent;
const message = { ...base, type: 'message.user', payload: { text: long } } as unknown as StoredEvent;

describe('slimForPhone', () => {
  it('cuts long tool results and says how much is left', () => {
    const slim = slimForPhone(result) as typeof result & { payload: { content: string } };
    expect(slim.payload.content.startsWith('x'.repeat(PHONE_TOOL_LIMIT))).toBe(true);
    expect(slim.payload.content).toContain(`… [${over} more characters: open this on your Mac to see all of it]`);
    expect(slim.id).toBe(7);
  });

  it('cuts long tool arguments and keeps them valid JSON', () => {
    const args = JSON.parse((slimForPhone(call) as typeof call & { payload: { arguments: string } }).payload.arguments) as { path: string; content: string };
    expect(args.path).toBe('a.txt');
    expect(args.content).toContain(`${over} more characters`);
    const broken = { ...call, payload: { ...(call as unknown as { payload: object }).payload, arguments: `{${long}` } } as unknown as StoredEvent;
    expect((slimForPhone(broken) as typeof call & { payload: { arguments: string } }).payload.arguments.length).toBeLessThan(PHONE_TOOL_LIMIT + 100);
  });

  it("cuts the tool calls inside an assistant message, never what the agent says", () => {
    const said = { ...base, type: 'assistant.message', payload: { run_id: 'r', content: long, tool_calls: [{ id: 't', name: 'write_file', arguments: JSON.stringify({ path: 'a.txt', content: long }) }] } } as unknown as StoredEvent;
    const slim = slimForPhone(said) as unknown as { payload: { content: string; tool_calls: Array<{ id: string; arguments: string }> } };
    expect(slim.payload.content).toBe(long);
    expect(slim.payload.tool_calls[0]!.id).toBe('t');
    expect((JSON.parse(slim.payload.tool_calls[0]!.arguments) as { path: string; content: string }).content).toContain(`${over} more characters`);
    const quiet = { ...said, payload: { run_id: 'r', content: long, tool_calls: [] } } as unknown as StoredEvent;
    expect(slimForPhone(quiet)).toBe(quiet);
  });

  it('leaves short tool texts and every other event as they are', () => {
    const short = { ...result, payload: { ...(result as unknown as { payload: object }).payload, content: 'ok' } } as unknown as StoredEvent;
    expect(slimForPhone(short)).toBe(short);
    expect(slimForPhone(message)).toBe(message);
  });

  it('slims event pushes and event pages only', () => {
    expect(((slimPushForPhone('desk:events', [result]) as StoredEvent[])[0] as unknown as { payload: { content: string } }).payload.content.length).toBeLessThan(long.length);
    expect((slimPushForPhone('desk:event', result) as unknown as { payload: { content: string } }).payload.content.length).toBeLessThan(long.length);
    const global = { overview: [] };
    expect(slimPushForPhone('desk:global', global)).toBe(global);
    const page = slimResultForPhone('threads.transcript', { events: [result], next_after: 7 }) as { events: Array<{ payload: { content: string } }>; next_after: number };
    expect(page.next_after).toBe(7);
    expect(page.events[0]!.payload.content.length).toBeLessThan(long.length);
    const other = { events: [result] };
    expect(slimResultForPhone('projects.list', other)).toBe(other);
  });
});
