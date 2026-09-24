import { describe, expect, it } from 'vitest';
import { ev } from '../testing';
import { applyChatDelta, emptyChat, reduceChat } from './chat';

const delta = (run: string, text: string) => ({ type: 'assistant.delta' as const, project_id: 'p', agent_id: 'd', payload: { run_id: run, text } });

describe('reduceChat', () => {
  it('builds the Desk conversation with streaming, tools, reports, questions and notices', () => {
    let s = emptyChat('d');
    s = reduceChat(s, ev(1, 'message.user', { text: 'Relaunch onboarding' }, { agent: 'd' }));
    s = applyChatDelta(s, delta('r1', 'Got '));
    s = applyChatDelta(s, delta('r1', 'it.'));
    expect(s.items.at(-1)).toMatchObject({ kind: 'assistant', runId: 'r1', text: 'Got it.', streaming: true });
    s = reduceChat(s, ev(2, 'assistant.message', { run_id: 'r1', content: 'Got it. Four threads.', tool_calls: [] }, { agent: 'd' }));
    s = reduceChat(s, ev(3, 'tool.call', { run_id: 'r1', tool_call_id: 'c1', name: 'spawn_thread', arguments: '{"title":"A"}' }, { agent: 'd' }));
    s = reduceChat(s, ev(4, 'tool.call', { run_id: 'r1', tool_call_id: 'c2', name: 'spawn_thread', arguments: '{"title":"B"}' }, { agent: 'd' }));
    s = reduceChat(s, ev(5, 'tool.result', { run_id: 'r1', tool_call_id: 'c1', name: 'spawn_thread', status: 'ok', content: 'spawned' }, { agent: 'd' }));
    s = reduceChat(s, ev(6, 'message.agent', { from_agent_id: 't', from_label: 'thread "A"', kind: 'completed', text: 'Done' }, { agent: 'd' }));
    s = reduceChat(s, ev(7, 'report', { headline: 'Research is in', progress: 'p', needs_you: ['Approve bun'], results: ['a.md'] }, { agent: 'd' }));
    s = reduceChat(s, ev(8, 'question.asked', { question: 'Data source first?', options: ['Yes', 'No'] }, { agent: 'd' }));
    s = reduceChat(s, ev(9, 'system.notice', { level: 'error', code: 'proxy_down', message: 'paused' }));
    s = reduceChat(s, ev(10, 'tool.call', { run_id: 'r2', tool_call_id: 'x', name: 'bash', arguments: '{}' }, { agent: 'other' }));
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'assistant', 'tools', 'agent', 'report', 'question', 'notice']);
    expect(s.items[1]).toMatchObject({ text: 'Got it. Four threads.', streaming: false, id: 'e:2' });
    expect(s.items[2]).toMatchObject({ kind: 'tools', calls: [{ id: 'c1', status: 'ok', content: 'spawned' }, { id: 'c2', status: 'running' }] });
    expect(s.items[5]).toMatchObject({ kind: 'question', answered: false, options: ['Yes', 'No'] });
    s = reduceChat(s, ev(11, 'message.user', { text: 'Yes' }, { agent: 'd' }));
    expect(s.items[5]).toMatchObject({ answered: true });
  });

  it('drops an empty streamed run and keeps a stream cut off by an error', () => {
    let s = applyChatDelta(emptyChat('d'), delta('r1', 'partial'));
    s = reduceChat(s, ev(1, 'assistant.message', { run_id: 'r1', content: null, tool_calls: [{ id: 'c', name: 'x', arguments: '{}' }] }, { agent: 'd' }));
    expect(s.items).toEqual([]);
    s = applyChatDelta(s, delta('r2', 'half a sent'));
    s = reduceChat(s, ev(2, 'run.finished', { run_id: 'r2', reason: 'error', detail: 'boom' }, { agent: 'd' }));
    expect(s.items).toEqual([expect.objectContaining({ kind: 'assistant', text: 'half a sent', streaming: false, interrupted: true })]);
    s = reduceChat(s, ev(3, 'context.compacted', { run_id: 'r3', checkpoint: 'x', up_to: 2, trigger: 'threshold' }, { agent: 'd' }));
    expect(s.items.at(-1)).toMatchObject({ kind: 'compacted' });
  });
});
