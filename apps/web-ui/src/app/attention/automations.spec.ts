import { Component, computed, inject } from '@angular/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { ProjectOverview } from '@desk/client';
import { ev } from '@desk/client/testing';
import type { AttentionItem, StoredEvent } from '@desk/protocol';
import { automationDetail, runDetail, stepRun } from '@desk/ui-core/testing';
import { RouteService } from '../core/route.service';
import { SESSION_RELEASE_DELAY } from '../core/session.service';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../testing/fake-bridge';
import { AttentionScreen } from './attention-screen';

beforeEach(() => void (window.location.hash = '#/attention'));

const recent = new Date(Date.now() - 4 * 60_000).toISOString();
const base = { project_id: 'p', project_name: 'Tax 2026', detail: '', created_at: recent };
const stepApproval: AttentionItem = { ...base, id: 'approval:ap9', kind: 'approval', agent_id: 'ag1', title: 'Weekly digest · Summarise wants to run bash', detail: 'Policy rule {"tool":"bash"} → ask', ref: { approval_id: 'ap9', automation_id: 'a1', run_id: 'r14', step_id: 'sum' } };
const ask: AttentionItem = { ...base, id: 'automation_ask:r14:ok', kind: 'automation_ask', agent_id: null, title: 'Weekly digest: Publish "Robots"?', ref: { automation_id: 'a1', run_id: 'r14', step_id: 'ok' } };
const failed: AttentionItem = { ...base, id: 'automation_failed:r14', kind: 'automation_failed', agent_id: null, title: 'Weekly digest failed', detail: 'step fetch failed', ref: { automation_id: 'a1', run_id: 'r14' } };
const enable: AttentionItem = { ...base, id: 'automation_enable:a1:t', kind: 'automation_enable_request', agent_id: null, title: 'Desk proposes turning on Weekly digest', detail: '0 8 * * 1 (Europe/Paris); 0 proposed grant(s). Tested twice.', ref: { automation_id: 'a1' } };
const suspended: AttentionItem = { ...base, id: 'automation_grants:a1:7', kind: 'automation_grants_suspended', agent_id: null, title: 'Weekly digest changed: its grants are suspended', detail: 'Desk saved v7.', ref: { automation_id: 'a1' } };

const overview = () =>
  ({
    project: { id: 'p', name: 'Tax 2026', goal: 'g', instructions: '', settings: { desk_model: 'm', thread_model: 'm', fallback_model: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, policy: [] }, created_at: 't', updated_at: 't', archived_at: null },
    desk: null,
    sources: [],
    plan: null,
    threads: [],
    approvals: [],
    last_seq: 0,
  }) as unknown as ProjectOverview;

const events: StoredEvent[] = [
  ev(1, 'assistant.message', { run_id: 'x', content: 'Checking the pages now.', tool_calls: [] }, { agent: 'ag1' }),
  ev(2, 'approval.requested', { approval_id: 'ap9', run_id: 'x', tool_call_id: 'c', tool: 'bash', arguments: '{"command":"ls steps"}', reason: 'Policy rule {"tool":"bash"} → ask', delegate_to_desk: false }, { agent: 'ag1' }),
];

const waitingAsk = () =>
  runDetail({
    status: 'waiting',
    steps: [
      ...runDetail().steps.slice(0, 1),
      stepRun('sum', { status: 'succeeded', summary: 'Robots everywhere', outputs: { headline: 'Robots' } }),
      stepRun('ok', { status: 'waiting', question: { text: 'Publish "Robots"?', files: [], approve_label: 'Publish' } }),
    ],
  });

/**
 * The desktop test's Routed, shown like App shows it: only while the route is #/attention. (The web route is a signal that
 * change detection reads at once, so an AttentionScreen left on another route would put its item back in the route.)
 */
@Component({
  selector: 'desk-routed',
  imports: [AttentionScreen],
  template: `@if (route().name === 'attention') {<div deskAttentionScreen [itemId]="item()"></div>}`,
})
class Routed {
  protected readonly route = inject(RouteService).route;
  protected readonly item = computed(() => {
    const r = this.route();
    return r.name === 'attention' ? r.item : undefined;
  });
}

async function setup(list: AttentionItem[], extra: FakeHandlers = {}) {
  const bridge: FakeDeskBridge = new FakeDeskBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    'automations.get': () => automationDetail(),
    'automations.getRun': () => runDetail(),
    ...extra,
  });
  await render(Routed, {
    providers: [...bridge.providers, provideGlobal({ ...initialGlobalState(), connection: { status: 'live' }, attention: list }), { provide: SESSION_RELEASE_DELAY, useValue: 0 }],
  });
  return bridge;
}

const input = (bridge: FakeDeskBridge, channel: string) => bridge.calls.find((c) => c.channel === channel)?.input;

describe('Attention: automations', () => {
  it('names a step agent’s approval by automation and step, and approves it with remember', async () => {
    const bridge = await setup([stepApproval], { 'approvals.resolve': () => ({ ok: true }) });
    expect(screen.getByRole('button', { name: /Automation Weekly digest\./ })).toBeTruthy();
    const insp = await screen.findByRole('article', { name: /clearance request/ });
    const link = await within(insp).findByRole('link', { name: 'Weekly digest · Summarise' });
    expect(link.getAttribute('href')).toBe('#/p/p/automations/a1/runs/r14');
    expect(within(insp).getByText('What the step agent said')).toBeTruthy();
    fireEvent.click(await within(insp).findByRole('button', { name: 'Approve and remember for this automation' }));
    await waitFor(() => expect(input(bridge, 'approvals.resolve')).toEqual({ id: 'ap9', decision: 'approved', remember: true }));
  });

  it('offers no remember while the automation’s grants are suspended', async () => {
    await setup([stepApproval], { 'automations.get': () => automationDetail({ grants_suspended: true }) });
    const insp = await screen.findByRole('article', { name: /clearance request/ });
    await within(insp).findByRole('link', { name: 'Weekly digest · Summarise' });
    expect(within(insp).queryByRole('button', { name: 'Approve and remember for this automation' })).toBeNull();
    expect(within(insp).getByText(/grants are suspended/)).toBeTruthy();
  });

  it('answers an Ask me step in place, and E opens the run', async () => {
    const bridge = await setup([ask], { 'automations.getRun': () => waitingAsk(), 'automations.answer': () => ({ ok: true }) });
    const insp = await screen.findByRole('article', { name: /automation question/ });
    expect(await within(insp).findByRole('heading', { name: 'Weekly digest · Publish?' })).toBeTruthy();
    fireEvent.click(within(within(insp).getByRole('region', { name: 'Your answer' })).getByRole('button', { name: 'Publish' }));
    await waitFor(() => expect(input(bridge, 'automations.answer')).toEqual({ runId: 'r14', stepId: 'ok', req: { decision: 'approve' } }));
    fireEvent.keyDown(window, { key: 'e' });
    expect(window.location.hash).toBe('#/p/p/automations/a1/runs/r14');
  });

  it('asks Desk to fix a failed run, and dismisses it', async () => {
    const run = runDetail({ status: 'failed', steps: [stepRun('fetch', { status: 'failed', error: 'exit 1\nTraceback' }), ...runDetail().steps.slice(1)] });
    const bridge = await setup([failed], { 'automations.getRun': () => run, 'projects.send': () => ({ ok: true }), 'attention.dismiss': () => ({ ok: true }) });
    const insp = await screen.findByRole('article', { name: /failed automation/ });
    expect(within(insp).getByText('step fetch failed')).toBeTruthy();
    const fix = within(insp).getByRole('button', { name: 'Ask Desk to fix' });
    await waitFor(() => expect((fix as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(fix);
    await waitFor(() => expect(input(bridge, 'projects.send')).toEqual({ id: 'p', text: 'Please fix digest: run #14 failed at Fetch pages. exit 1' }));
    fireEvent.click(within(insp).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(input(bridge, 'attention.dismiss')).toEqual({ id: 'automation_failed:r14' }));
  });

  it('opens the Turn-on dialog from Desk’s request', async () => {
    await setup([enable], {
      'automations.get': () => automationDetail({ enabled: false, enable_requested: true }),
      'automations.validate': () => ({ errors: [], warnings: [], next_times: {} }),
    });
    const insp = await screen.findByRole('article', { name: /automation to turn on/ });
    expect(within(insp).getByText(/Tested twice\./)).toBeTruthy();
    const turnOn = within(insp).getByRole('button', { name: 'Turn on…' });
    await waitFor(() => expect((turnOn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(turnOn);
    expect(await screen.findByRole('dialog', { name: 'Turn on Weekly digest' })).toBeTruthy();
  });

  it('opens the Grants tab for suspended grants, and the legend names what automations share', async () => {
    await setup([suspended]);
    await screen.findByRole('article', { name: /grants suspended/ });
    expect(document.querySelector('.strip-legend')!.textContent).toContain('FLDFailed thread or automation');
    fireEvent.keyDown(window, { key: 'e' });
    expect(window.location.hash).toBe('#/p/p/automations/a1/grants');
  });
});
