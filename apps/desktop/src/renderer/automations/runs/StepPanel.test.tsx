// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { ProjectState } from '@desk/client';
import { ev } from '@desk/client/testing';
import { runDetail, stepRun } from '@desk/ui-core/testing';
import { installBridge } from '../../test/bridge';
import { sessionOf } from '../../test/session';
import { StepPanel } from './StepPanel';

afterEach(cleanup);

const waitingAsk = () =>
  runDetail({
    status: 'waiting',
    steps: [
      ...runDetail().steps.slice(0, 1),
      stepRun('sum', { status: 'succeeded', summary: 'Robots everywhere', outputs: { headline: 'Robots' } }),
      stepRun('ok', { status: 'waiting', question: { text: 'Publish "Robots"?', files: ['/d/automation-runs/r14/steps/sum/digest.md'], approve_label: 'Publish' } }),
    ],
  });

describe('StepPanel', () => {
  it('answers an Ask me step with a note, after previewing its file', async () => {
    const bridge = installBridge({ 'automations.file': () => new TextEncoder().encode('# Digest\n\nRobots.'), 'automations.answer': () => ({ ok: true }), 'automations.files': () => [] });
    render(<StepPanel projectId="p" s={sessionOf()} run={waitingAsk()} stepId="ok" grantsSuspended={false} />);
    const answer = screen.getByRole('region', { name: 'Your answer' });
    expect(within(answer).getByText('Publish "Robots"?')).toBeTruthy();
    fireEvent.click(within(answer).getByRole('button', { name: 'digest.md' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.file')?.input).toEqual({ runId: 'r14', path: 'steps/sum/digest.md' }));
    fireEvent.change(within(answer).getByLabelText('Note (optional)'), { target: { value: 'ship it' } });
    fireEvent.click(within(answer).getByRole('button', { name: 'Publish' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.answer')?.input).toEqual({ runId: 'r14', stepId: 'ok', req: { decision: 'approve', note: 'ship it' } }));
  });

  it('offers remember on a script gate, unless grants are suspended', async () => {
    const gated = runDetail({ status: 'waiting', steps: [stepRun('fetch', { status: 'waiting', gate: { tool: 'skill_run', subject: 'digest/fetch.py robots', reason: 'ask: skill_run' } }), ...runDetail().steps.slice(1)] });
    const bridge = installBridge({ 'automations.answer': () => ({ ok: true }), 'automations.files': () => [], 'automations.log': () => '' });
    const { unmount } = render(<StepPanel projectId="p" s={sessionOf()} run={gated} stepId="fetch" grantsSuspended={false} />);
    expect(screen.getByText('digest/fetch.py robots')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Approve and remember' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.answer')?.input).toEqual({ runId: 'r14', stepId: 'fetch', req: { decision: 'approve', remember: true } }));
    unmount();
    render(<StepPanel projectId="p" s={sessionOf()} run={gated} stepId="fetch" grantsSuspended />);
    expect(screen.queryByRole('button', { name: 'Approve and remember' })).toBeNull();
    expect(screen.getByText(/grants are suspended/i)).toBeTruthy();
  });

  it('shows a running agent step: live output, tokens, its approvals (with remember), transcript and Stop step', async () => {
    const bridge = installBridge({ 'approvals.resolve': () => ({ ok: true }), 'automations.stopStep': () => ({ ok: true }), 'automations.files': () => [] });
    const events = [
      ev(1, 'assistant.message', { run_id: 'x', content: 'Reading acme.md now.', tool_calls: [] }, { agent: 'ag1' }),
      ev(2, 'usage', { run_id: 'x', model: 'm', prompt_tokens: 1200, completion_tokens: 300, estimated: false }, { agent: 'ag1' }),
    ];
    const project = { approvals: [{ id: 'ap1', project_id: 'p', agent_id: 'ag1', run_id: 'x', tool_call_id: 't', tool: 'bash', arguments: '{"command":"ls"}', reason: 'ask', delegate_to_desk: false, status: 'pending', resolved_by: null, note: null, created_at: 't', resolved_at: null }] } as unknown as ProjectState;
    render(<StepPanel projectId="p" s={sessionOf(events, { project })} run={runDetail()} stepId="sum" grantsSuspended={false} />);
    expect(screen.getByText('Reading acme.md now.')).toBeTruthy();
    expect(screen.getByText('1.5k tokens')).toBeTruthy();
    const approval = screen.getByRole('region', { name: 'Approval: bash' });
    expect(within(approval).getByText('ls')).toBeTruthy();
    fireEvent.click(within(approval).getByRole('button', { name: 'Approve and remember' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'approvals.resolve')?.input).toEqual({ id: 'ap1', decision: 'approved', remember: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Open transcript' }));
    expect(screen.getByRole('dialog', { name: 'Summarise · transcript' })).toBeTruthy();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Summarise · transcript' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Stop step…' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Stop this step?' })).getByRole('button', { name: 'Stop step' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.stopStep')?.input).toEqual({ runId: 'r14', stepId: 'sum' }));
  });

  it('shows a finished script step’s summary, route, outputs, log and files', async () => {
    installBridge({
      'automations.log': () => 'fetched 5 pages\n',
      'automations.files': () => [{ name: 'out.json', path: 'steps/fetch/out.json', type: 'file', size: 10 }],
      'automations.file': () => new TextEncoder().encode('{}'),
    });
    const run = runDetail({ steps: [stepRun('fetch', { status: 'succeeded', route: 'changed', summary: '3 of 5 sites differ', outputs: { count: 3, sites: ['a.com', 'b.com'] }, started_at: '2026-09-28T06:00:00.000Z', finished_at: '2026-09-28T06:00:12.000Z' }), ...runDetail().steps.slice(1)] });
    render(<StepPanel projectId="p" s={sessionOf()} run={run} stepId="fetch" grantsSuspended={false} />);
    expect(screen.getByText('3 of 5 sites differ')).toBeTruthy();
    expect(screen.getByText('changed')).toBeTruthy();
    expect(screen.getByText('count').nextSibling?.textContent).toBe('3');
    expect(screen.getByText('sites').nextSibling?.textContent).toBe('a.com\nb.com');
    expect(await screen.findByText(/fetched 5 pages/)).toBeTruthy();
    expect(await screen.findByRole('button', { name: 'out.json' })).toBeTruthy();
  });

  it('asks Desk to fix a failed step', async () => {
    const bridge = installBridge({ 'projects.send': () => ({ ok: true }), 'automations.files': () => [], 'automations.log': () => '' });
    const run = runDetail({ status: 'failed', steps: [stepRun('fetch', { status: 'failed', error: 'exit 1\nTraceback' }), ...runDetail().steps.slice(1)] });
    render(<StepPanel projectId="p" s={sessionOf()} run={run} stepId="fetch" grantsSuspended={false} />);
    expect(screen.getByText(/exit 1/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Ask Desk to fix' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.send')?.input).toEqual({ id: 'p', text: 'Please fix digest: run #14 failed at Fetch pages. exit 1' }));
  });
});
