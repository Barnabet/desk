import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it } from 'vitest';
import { automationDetail, runDetail } from '@desk/ui-core/testing';
import { FakeDeskBridge } from '../../testing/fake-bridge';
import { sessionOf } from '../../testing/session';
import { RunsList } from './runs-list';
import { RunView } from './run-view';

beforeEach(() => void (window.location.hash = '#/p/p/automations/a1/runs'));

describe('RunsList', () => {
  it('lists runs with number, trigger, status and took, and skipped times as lines of their own', async () => {
    const bridge = new FakeDeskBridge({
      'automations.runs': () => [
        { kind: 'run', run: runDetail() },
        { kind: 'skipped', automation_id: 'a1', trigger_index: 0, due_at: '2026-09-27T06:00:00.000Z', reason: 'still_running', ts: '2026-09-27T06:00:00.000Z' },
        { kind: 'run', run: { ...runDetail({ id: 'r13', number: 13, trigger: 'test', test: true, version: 6, status: 'failed', at_step: 'Fetch pages', finished_at: '2026-09-27T05:10:00.000Z', started_at: '2026-09-27T05:00:00.000Z', summary: 'exit 1' }) } },
      ],
    });
    await render(RunsList, { inputs: { projectId: 'p', s: sessionOf(), detail: automationDetail() }, providers: bridge.providers });
    const r14 = (await screen.findByRole('link', { name: '#14' })).closest('tr')!;
    expect(r14.textContent).toContain('running · Summarise');
    expect(r14.textContent).toContain('schedule');
    expect(screen.getByRole('link', { name: '#14' }).getAttribute('href')).toBe('#/p/p/automations/a1/runs/r14');
    const r13 = screen.getByRole('link', { name: '#13' }).closest('tr')!;
    expect(r13.textContent).toContain('test · v6');
    expect(r13.textContent).toContain('failed at Fetch pages');
    expect(r13.textContent).toContain('10m');
    expect(r13.textContent).toContain('exit 1');
    expect(screen.getByText(/skipped: the previous run was still going/)).toBeTruthy();
  });
});

describe('RunView', () => {
  it('lights the graph, opens the running step, opens the folder and cancels the run', async () => {
    const bridge = new FakeDeskBridge({ 'automations.getRun': () => runDetail(), 'app.revealPath': () => ({ ok: true }), 'automations.cancelRun': () => ({ ok: true }), 'automations.files': () => [] });
    await render(RunView, { inputs: { projectId: 'p', s: sessionOf(), detail: automationDetail(), runId: 'r14' }, providers: bridge.providers });
    expect(await screen.findByRole('heading', { name: 'Run #14' })).toBeTruthy();
    expect(screen.getByText('running · Summarise')).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId('node-fetch').className).toContain('run-ok'));
    expect(screen.getByTestId('node-fetch').textContent).toContain('✓ 12s');
    expect(screen.getByTestId('node-sum').className).toContain('run-run');
    expect(screen.getByTestId('node-ok').className).toContain('run-pending');
    expect(screen.getByRole('complementary', { name: 'Summarise' })).toBeTruthy();
    fireEvent.click(screen.getByTestId('node-start'));
    const panel = screen.getByRole('complementary', { name: 'Run #14' });
    expect(within(panel).getByText('robots')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Open folder' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'app.revealPath')?.input).toEqual({ runId: 'r14' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel run…' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Cancel run #14?' })).getByRole('button', { name: 'Cancel run' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.cancelRun')?.input).toEqual({ runId: 'r14' }));
  });
});
