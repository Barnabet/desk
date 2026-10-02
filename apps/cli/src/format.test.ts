import { describe, expect, it } from 'vitest';
import type { StoredEvent, StreamServerMessage } from '@desk/protocol';
import type { ThreadReview } from '@desk/client';
import { acceptanceLabel, automationLine, createRenderer, reviewText, runLine, stepLine } from './format';

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

describe('automation lines', () => {
  it('formats summaries, runs and steps', () => {
    const summary = {
      id: 'a',
      project_id: 'p',
      name: 'digest',
      title: 'Digest',
      description: '',
      version: 3,
      tested_version: 2,
      enabled: true,
      grants_suspended: true,
      schedules: [{ cron: '0 8 * * 1', timezone: 'Europe/Paris' }],
      last_run: { id: 'r', number: 3, status: 'failed', trigger: 'schedule', test: false, started_at: '2026-09-28T06:00:00.000Z', finished_at: null, summary: null, waiting_on: null },
      next_due: '2026-10-05T06:00:00.000Z',
      enable_requested: false,
      updated_at: '',
    } as const;
    expect(automationLine(summary as never)).toBe('digest "Digest" — on · v3 (tested in v2) · 0 8 * * 1 (Europe/Paris) · last: failed 2026-09-28T06:00:00.000Z · next 2026-10-05T06:00:00.000Z · grants suspended');
    expect(runLine({ kind: 'skipped', automation_id: 'a', trigger_index: 0, due_at: '2026-09-28T06:00:00.000Z', reason: 'missed', ts: '' })).toBe('—  skipped (missed)  2026-09-28T06:00:00.000Z');
    const step = { step_id: 's', attempt: 2, status: 'failed', route: 'error', outputs: {}, summary: null, error: 'Exit code 1: boom', agent_id: null, child_run_id: null, resume_at: null, gate: null, question: null, note: null, started_at: null, finished_at: null } as const;
    expect(stepLine('Fetch', step as never)).toBe('  • Fetch → failed (attempt 2) · route error: Exit code 1: boom');
  });

  it('renders automation questions, gates and run ends in the stream', () => {
    let out = '';
    const render = createRenderer((s) => (out += s), { deskId: 'd' });
    const ev = (type: string, payload: object) => ({ kind: 'event', event: { id: 1, project_id: 'p', agent_id: null, type, payload, ts: '' } }) as never;
    render(ev('automation.step_changed', { run_id: 'r1', step_id: 'ok', attempt: 1, status: 'waiting', question: { text: 'Publish?', files: [] } }));
    render(ev('automation.step_changed', { run_id: 'r1', step_id: 'fetch', attempt: 1, status: 'waiting', gate: { tool: 'skill_run', subject: 'web/fetch.py x', reason: 'r' } }));
    render(ev('automation.run_finished', { run_id: 'r1', status: 'failed', summary: 'Fetch failed' }));
    expect(out).toBe(
      [
        '  ? automation run r1 asks: Publish? (desk automation answer r1 ok approve|reject)',
        '  ! automation run r1 wants to run web/fetch.py x (desk automation answer r1 fetch approve|reject [--remember])',
        '  ◼ automation run r1 failed: Fetch failed',
        '',
      ].join('\n'),
    );
  });
});

describe('reviews', () => {
  const sub = (seq: number, extra = {}) => ({ id: `s${seq}`, seq, commit: `${seq}abcdef0123456`, claims: [], limitations: [], evidence: '', artifacts: [], superseded_by: null, ...extra });
  const review = {
    acceptance: 'reviewed',
    accepted_submission_id: null,
    submissions: [sub(1, { superseded_by: 's2' }), sub(2, { claims: ['Totals match'], limitations: ['EUR only'], evidence: 'pytest: 12 passed', artifacts: [{ path: 'r.md', sha256: 'ab'.repeat(32) }] })],
    reviews: [{ id: 'rv', reviewer_id: 'r', submission_id: 's1', phase: 'final', verdict: 'issues_found', requirements: [{ criterion: 'Totals match', met: 'no', note: 'March' }] }],
    findings: [
      { id: 'f1', title: 'March is off', blocking: true, reproducer: 'python c.py', state: 'open', reason: null },
      { id: 'f2', title: 'Typo', blocking: false, reproducer: '', state: 'waived', reason: 'Cosmetic' },
    ],
    reviewing: null,
  } as unknown as ThreadReview;

  it('labels acceptance for desk threads', () => {
    expect(acceptanceLabel({ acceptance: 'accepted_with_limitations', reviews_submission_id: null })).toBe('accepted with limitations');
    expect(acceptanceLabel({ acceptance: 'none', reviews_submission_id: null })).toBe('');
    expect(acceptanceLabel({ acceptance: 'none', reviews_submission_id: 's1' })).toBe('reviewer');
  });

  it('prints the current submission, open findings with reproducers, reviews and older submissions', () => {
    expect(reviewText(review, (id) => (id === 'r' ? 'Review: Comparator' : id))).toBe(
      [
        'Acceptance: reviewed',
        'Submission 2 commit 2abcdef012',
        '  claims: Totals match',
        '  limitation: EUR only',
        '  checked: pytest: 12 passed',
        `  file: r.md ${'ab'.repeat(6)}`,
        'Open findings:',
        '- f1 (blocking) March is off',
        '    reproduce: python c.py',
        'Reviews:',
        '- Review: Comparator on submission 1: issues found',
        '    [no] Totals match: March',
        'Resolved findings:',
        '- f2 Typo [waived: Cosmetic]',
        'Submission 1 commit 1abcdef012 [superseded]',
      ].join('\n'),
    );
    expect(reviewText({ ...review, submissions: [] }, String)).toBe('Nothing submitted yet.');
  });
});
