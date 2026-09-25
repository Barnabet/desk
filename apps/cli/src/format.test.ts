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

  it('shows the images a tool result carried, for Desk and in verbose mode', () => {
    let out = '';
    const image = { sha256: 'a'.repeat(64), media_type: 'image/png' as const, width: 1240, height: 1754, bytes: 9, name: 'page-1.png' };
    const result = (agent: string) => ev(agent, { type: 'tool.result', payload: { run_id: 'r', tool_call_id: 'c', name: 'view_image', status: 'ok', content: 'page-1.png', images: [image] } });
    const render = createRenderer((s) => (out += s), { deskId: 'D' });
    render(ev('D', { type: 'assistant.message', payload: { run_id: 'r', content: null, tool_calls: [{ id: 'c', name: 'view_image', arguments: '{"paths":["page-1.png","page-2.png"]}' }] } }));
    render(result('D'));
    render(result('T1'));
    expect(out).toContain('⚙ view_image page-1.png page-2.png');
    expect(out.match(/\[image: page-1\.png 1240×1754\]/g)).toHaveLength(1);
    let verbose = '';
    createRenderer((s) => (verbose += s), { deskId: 'D', verbose: true })(result('T1'));
    expect(verbose).toBe('    [image: page-1.png 1240×1754]\n');
  });

  it('says when images are no longer sent to the model because the endpoint refused them', () => {
    let out = '';
    const render = createRenderer((s) => (out += s), { deskId: 'D' });
    const withheld = (agent: string) =>
      ev(agent, { type: 'images.withheld', payload: { run_id: 'r', reason: '400 Could not process image', images: [{ tool_call_id: 'c', sha256: 'a'.repeat(64), name: 'page-3.png' }] } });
    render(withheld('D'));
    render(withheld('T1'));
    expect(out).toBe('  ⚠ no longer sent to the model: page-3.png (400 Could not process image)\n');
  });

  it('prints non-streamed assistant messages', () => {
    let out = '';
    const render = createRenderer((s) => (out += s), { deskId: 'D' });
    render(ev('D', { type: 'assistant.message', payload: { run_id: 'r2', content: 'Replayed answer', tool_calls: [] } }));
    expect(out).toContain('desk › Replayed answer');
  });

  it("labels a message between threads with the sender's title", () => {
    let out = '';
    const render = createRenderer((s) => (out += s), { deskId: 'D', verbose: true });
    render(ev('A', { type: 'agent.created', payload: { role: 'thread', model: 'm', title: 'Auth API', brief: 'x', workspace_path: '/a', parent_id: 'D' } }));
    render(ev('F', { type: 'agent.created', payload: { role: 'thread', model: 'm', title: 'Frontend', brief: 'y', workspace_path: '/f', parent_id: 'D' } }));
    render(ev('F', { type: 'message.agent', payload: { from_agent_id: 'A', from_label: 'thread "Auth API" (A)', kind: 'question', text: 'Which token format?', tracked: true } }));
    render(ev('F', { type: 'message.agent', payload: { from_agent_id: 'D', from_label: 'Desk', kind: 'note', text: 'Use EU.' } }));
    expect(out).toContain('↦ "Auth API" → "Frontend" [question] Which token format?');
    expect(out).toContain('↦ Desk → "Frontend" [note] Use EU.');
  });

  it("shows who answered whom, closures and the user's Ask, and hides start", () => {
    let out = '';
    const render = createRenderer((s) => (out += s), { deskId: 'D', verbose: true });
    render(ev('A', { type: 'agent.created', payload: { role: 'thread', model: 'm', title: 'Auth API', brief: 'x', workspace_path: '/a', parent_id: 'D' } }));
    render(ev('F', { type: 'agent.created', payload: { role: 'thread', model: 'm', title: 'Frontend', brief: 'y', workspace_path: '/f', parent_id: 'D' } }));
    render(ev('F', { type: 'message.agent', payload: { from_agent_id: 'D', from_label: 'Desk', kind: 'start', text: 'Begin your assignment.' } }));
    render(ev('A', { type: 'message.agent', payload: { from_agent_id: 'F', from_label: 'thread "Frontend" (F)', kind: 'answer', text: 'JWT, RS256.', reply_to: 123 } }));
    render(ev('A', { type: 'message.agent', payload: { from_agent_id: 'F', from_label: 'thread "Frontend" (F)', kind: 'answer', text: '(Frontend was stopped before answering.)', reply_to: 124, auto: true } }));
    render(ev('D', { type: 'message.agent', payload: { from_agent_id: 'A', from_label: 'thread "Auth API" (A)', kind: 'answer', text: 'Done by Friday.', reply_to: 125 } }));
    render(ev('F', { type: 'message.user', payload: { text: 'How did you price it?', question: true } }));
    expect(out).not.toContain('Begin your assignment');
    expect(out).toContain('↩ "Frontend" → "Auth API" (answer to #123) JWT, RS256.');
    expect(out).toContain('↩ "Frontend" → "Auth API" (#124 closed) (Frontend was stopped before answering.)');
    expect(out).toContain('↩ "Auth API" → Desk (answer to #125) Done by Friday.');
    expect(out).toContain('you asked "Frontend": How did you price it?');
  });

  it("names senders it has not seen from their labels, as tail does", () => {
    let out = '';
    const render = createRenderer((s) => (out += s), { deskId: 'A', label: '"Auth API"', verbose: true });
    render(ev('A', { type: 'message.agent', payload: { from_agent_id: 'F', from_label: 'thread "Frontend" (F)', kind: 'answer', text: 'JWT.', reply_to: 7 } }));
    render(ev('A', { type: 'message.agent', payload: { from_agent_id: 'D', from_label: 'Desk', kind: 'question', text: 'Status?', tracked: true } }));
    render(ev('A', { type: 'message.agent', payload: { from_agent_id: 'D', from_label: 'Desk', kind: 'start', text: 'Begin your assignment.' } }));
    render(ev('A', { type: 'message.user', payload: { text: 'Which format did you pick?', question: true } }));
    expect(out).toContain('↩ "Frontend" → "Auth API" (answer to #7) JWT.');
    expect(out).toContain('↳ [Desk — question] Status?');
    expect(out).toContain('you asked "Auth API": Which format did you pick?');
    expect(out).not.toContain('Begin');
  });
});
