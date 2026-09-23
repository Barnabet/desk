import { describe, expect, it } from 'vitest';
import type { StoredEvent, StreamServerMessage } from '@desk/protocol';
import { createRenderer } from './format';

let seq = 0;
const ev = (agent_id: string | null, body: Pick<StoredEvent, 'type' | 'payload'>): StreamServerMessage => ({
  kind: 'event',
  event: { ...body, id: ++seq, project_id: 'p', agent_id, ts: 't' } as StoredEvent,
});

describe('renderer', () => {
  it('renders a Desk conversation with streamed text, tools, threads, approvals and reports', () => {
    let out = '';
    const render = createRenderer((s) => (out += s), { deskId: 'D' });
    render(ev('D', { type: 'message.user', payload: { text: 'Plan the launch' } }));
    render({ kind: 'ephemeral', event: { type: 'assistant.delta', project_id: 'p', agent_id: 'D', payload: { run_id: 'r', text: 'On ' } } });
    render({ kind: 'ephemeral', event: { type: 'assistant.delta', project_id: 'p', agent_id: 'D', payload: { run_id: 'r', text: 'it.' } } });
    render(ev('D', { type: 'assistant.message', payload: { run_id: 'r', content: 'On it.', tool_calls: [{ id: 'c', name: 'spawn_thread', arguments: '{"title":"Research","brief":"x"}' }] } }));
    render(ev('T1', { type: 'agent.created', payload: { role: 'thread', model: 'm', title: 'Research', brief: 'x', workspace_path: '/w', parent_id: 'D' } }));
    render(ev('T1', { type: 'approval.requested', payload: { approval_id: 'A1', run_id: 'r', tool_call_id: 'c', tool: 'bash', arguments: '{"command":"sudo ls"}', reason: 'risky', delegate_to_desk: false } }));
    render(ev('T1', { type: 'agent.status_changed', payload: { status: 'done' } }));
    render(ev('D', { type: 'message.agent', payload: { from_agent_id: 'T1', from_label: 'thread "Research" (T1)', kind: 'completed', text: 'Summary: done' } }));
    render(ev('D', { type: 'report', payload: { headline: 'Launch plan ready', progress: '3/3', needs_you: ['Pick a date'], results: ['plan.md'] } }));
    render(ev('D', { type: 'question.asked', payload: { question: 'Which date?', options: ['Mon', 'Fri'] } }));
    expect(out).toContain('you › Plan the launch');
    expect(out).toContain('desk › On it.');
    expect(out.match(/On it\./g)).toHaveLength(1);
    expect(out).toContain('⚙ spawn_thread "Research"');
    expect(out).toContain('+ thread "Research" (T1)');
    expect(out).toContain('! approval A1: bash sudo ls — risky');
    expect(out).toContain('• "Research" → done');
    expect(out).toContain('↳ [thread "Research" (T1) — completed] Summary: done');
    expect(out).toContain('Launch plan ready');
    expect(out).toContain('needs you: Pick a date');
    expect(out).toContain('? Which date? [Mon / Fri]');
  });

  it('prints non-streamed assistant messages', () => {
    let out = '';
    const render = createRenderer((s) => (out += s), { deskId: 'D' });
    render(ev('D', { type: 'assistant.message', payload: { run_id: 'r2', content: 'Replayed answer', tool_calls: [] } }));
    expect(out).toContain('desk › Replayed answer');
  });
});
