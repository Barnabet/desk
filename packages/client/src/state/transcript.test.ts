import { describe, expect, it } from 'vitest';
import { ev } from '../testing';
import { applyTranscriptDelta, emptyTranscript, reduceTranscript } from './transcript';

describe('reduceTranscript', () => {
  it('narrates a thread: brief, text, tools, detour, compaction, result, revision, steering, approval', () => {
    const t = { agent: 't' };
    const events = [
      ev(1, 'agent.created', { role: 'thread', model: 'm', title: 'Emails', brief: 'Draft five emails', workspace_path: '/w', parent_id: 'd' }, t),
      ev(2, 'agent.status_changed', { status: 'running' }, t),
      ev(3, 'assistant.message', { run_id: 'r1', content: 'Starting from brand-voice.', tool_calls: [] }, t),
      ev(4, 'tool.call', { run_id: 'r1', tool_call_id: 'c1', name: 'read_file', arguments: '{"path":"a"}' }, t),
      ev(5, 'tool.result', { run_id: 'r1', tool_call_id: 'c1', name: 'read_file', status: 'ok', content: 'A' }, t),
      ev(6, 'agent.model_switched', { from: 'opus', to: 'fable', reason: 'rate limited', scope: 'run' }, t),
      ev(7, 'context.compacted', { run_id: 'r1', checkpoint: 'x', up_to: 5, trigger: 'threshold' }, t),
      ev(8, 'agent.result', { summary: 'Five drafts', artifacts: ['emails/01.md'] }, t),
      ev(9, 'agent.status_changed', { status: 'done' }, t),
      ev(10, 'agent.revision', { round: 1, feedback: 'Less salesy' }, t),
      ev(11, 'message.agent', { from_agent_id: 'd', from_label: 'Desk', kind: 'revision', text: 'Less salesy' }, t),
      ev(12, 'message.user', { text: 'Keep email 5 short' }, t),
      ev(13, 'message.agent', { from_agent_id: 'd', from_label: 'Desk', kind: 'note', text: 'FYI' }, t),
      ev(14, 'approval.requested', { approval_id: 'a1', run_id: 'r2', tool_call_id: 'c9', tool: 'bash', arguments: '{"command":"x"}', reason: 'rule 1', delegate_to_desk: false }, t),
      ev(15, 'approval.resolved', { approval_id: 'a1', decision: 'denied', resolved_by: 'user', note: 'no' }, t),
      ev(16, 'tool.result', { run_id: 'r2', tool_call_id: 'c9', name: 'bash', status: 'denied', content: 'Denied: no' }, t),
      ev(17, 'message.user', { text: 'ignored: another agent' }, { agent: 'x' }),
    ];
    const s = events.reduce(reduceTranscript, emptyTranscript('t'));
    expect(s.entries.map((e) => e.kind)).toEqual(['brief', 'status', 'assistant', 'tools', 'detour', 'compacted', 'result', 'status', 'revision', 'steer', 'incoming', 'approval']);
    expect(s.entries.find((e) => e.kind === 'approval')).toMatchObject({ approvalId: 'a1', state: 'denied', resolvedBy: 'user', note: 'no' });
    expect(s.entries.find((e) => e.kind === 'detour')).toMatchObject({ from: 'opus', to: 'fable' });
    expect(s.entries.find((e) => e.kind === 'tools')).toMatchObject({ calls: [{ id: 'c1', status: 'ok' }] });
  });

  it('exposes the images a tool result showed the model', () => {
    const image = { sha256: 'a'.repeat(64), media_type: 'image/png' as const, width: 1240, height: 1754, bytes: 9, name: 'page-1.png' };
    let s = emptyTranscript('t');
    s = reduceTranscript(s, ev(1, 'tool.call', { run_id: 'r', tool_call_id: 'c', name: 'view_image', arguments: '{"paths":["page-1.png"]}' }, { agent: 't' }));
    s = reduceTranscript(s, ev(2, 'tool.call', { run_id: 'r', tool_call_id: 'd', name: 'read_file', arguments: '{}' }, { agent: 't' }));
    s = reduceTranscript(s, ev(3, 'tool.result', { run_id: 'r', tool_call_id: 'c', name: 'view_image', status: 'ok', content: 'page-1.png · 1240x1754', images: [image] }, { agent: 't' }));
    s = reduceTranscript(s, ev(4, 'tool.result', { run_id: 'r', tool_call_id: 'd', name: 'read_file', status: 'ok', content: 'x' }, { agent: 't' }));
    expect(s.entries).toEqual([expect.objectContaining({ kind: 'tools', calls: [expect.objectContaining({ id: 'c', status: 'ok', images: [image] }), expect.not.objectContaining({ images: expect.anything() })] })]);
  });

  it('streams text for the thread and marks interrupted tool calls', () => {
    let s = emptyTranscript('t');
    s = applyTranscriptDelta(s, { type: 'assistant.delta', project_id: 'p', agent_id: 't', payload: { run_id: 'r', text: 'Rewri' } });
    s = applyTranscriptDelta(s, { type: 'assistant.delta', project_id: 'p', agent_id: 't', payload: { run_id: 'r', text: 'ting' } });
    expect(s.entries).toEqual([expect.objectContaining({ kind: 'assistant', text: 'Rewriting', streaming: true })]);
    s = reduceTranscript(s, ev(1, 'tool.call', { run_id: 'r', tool_call_id: 'c', name: 'bash', arguments: '{}' }, { agent: 't' }));
    s = reduceTranscript(s, ev(2, 'tool.result', { run_id: 'r', tool_call_id: 'c', name: 'bash', status: 'interrupted', content: 'Interrupted by a daemon restart' }, { agent: 't' }));
    expect(s.entries.at(-1)).toMatchObject({ kind: 'tools', calls: [{ status: 'interrupted' }] });
  });
});
