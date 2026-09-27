// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import type { AutomationSummary, StoredEvent } from '@desk/protocol';
import { initialGlobalState } from '@desk/bff/contract';
import { automationDetail, automationSummary } from '@desk/ui-core/testing';
import { globalStore } from '../state/global';
import { resetSessions, setReleaseDelay, startSessionRouting } from '../state/session';
import { installBridge } from '../test/bridge';
import { installReactFlowShims } from '../test/reactflow';
import { AutomationsScreen } from './AutomationsScreen';

beforeAll(installReactFlowShims);

afterEach(cleanup);
beforeEach(() => {
  resetSessions();
  setReleaseDelay(0);
  localStorage.clear();
  window.location.hash = '#/p/p/automations';
  globalStore.set({ ...initialGlobalState(), connection: { status: 'live' } });
});

const overview = () =>
  ({
    project: { id: 'p', name: 'P', goal: '', instructions: '', settings: {}, created_at: 't', updated_at: 't', archived_at: null },
    desk: null,
    sources: [],
    plan: null,
    threads: [],
    approvals: [],
    last_seq: 0,
  }) as unknown as ProjectOverview;

function setup(handlers: Record<string, (input: any) => unknown>, events: StoredEvent[] = []) {
  const bridge = installBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    ...handlers,
  });
  startSessionRouting();
  return bridge;
}

const run = (over: Partial<NonNullable<AutomationSummary['last_run']>>): NonNullable<AutomationSummary['last_run']> => ({
  id: 'r1',
  number: 1,
  status: 'succeeded',
  trigger: 'schedule',
  test: false,
  started_at: '2026-09-24T06:00:00.000Z',
  finished_at: '2026-09-24T06:09:00.000Z',
  summary: 'ok',
  waiting_on: null,
  ...over,
});

const LIST = [
  automationSummary({ last_run: run({ id: 'r13', number: 13 }) }),
  automationSummary({ id: 'a2', name: 'deps', title: 'Nightly dependency check', version: 3, tested_version: 3, schedules: [{ cron: '0 2 * * *', timezone: 'Europe/Paris' }], last_run: run({ id: 'r2', number: 2, status: 'waiting', finished_at: null, waiting_on: 'Ask me: Open the PR?' }) }),
  automationSummary({ id: 'a3', name: 'invoices', title: 'Invoice intake', version: 2, tested_version: 2, enabled: false, schedules: [], next_due: null }),
];

describe('Automations list', () => {
  it('lists each automation with its switch, schedule, last run and next run', async () => {
    setup({ 'automations.list': () => LIST });
    render(<AutomationsScreen projectId="p" />);
    const deps = (await screen.findByRole('link', { name: 'Nightly dependency check' })).closest('tr')!;
    expect(deps.textContent).toContain('waiting on you: Ask me: Open the PR?');
    expect(deps.textContent).toContain('Daily 02:00');
    const digest = screen.getByRole('link', { name: 'Weekly digest' }).closest('tr')!;
    expect(digest.textContent).toContain('v7 · untested changes');
    expect(digest.textContent).toContain('succeeded');
    const invoices = screen.getByRole('link', { name: 'Invoice intake' }).closest('tr')!;
    expect(invoices.textContent).toContain('v2 · run now only');
    expect(within(invoices).getByRole('switch').getAttribute('aria-checked')).toBe('false');
    expect(screen.getByRole('link', { name: 'Weekly digest' }).getAttribute('href')).toBe('#/p/p/automations/a1');
  });

  it('turns an automation off at once, and on through the Turn-on dialog', async () => {
    const bridge = setup({
      'automations.list': () => LIST,
      'automations.setEnabled': () => automationDetail({ enabled: false }),
      'automations.get': () => automationDetail({ id: 'a3', title: 'Invoice intake', enabled: false }),
      'automations.validate': () => ({ errors: [], warnings: [], next_times: {} }),
    });
    render(<AutomationsScreen projectId="p" />);
    fireEvent.click(await screen.findByRole('switch', { name: 'Turn off Weekly digest' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.setEnabled')?.input).toEqual({ id: 'a1', enabled: false }));
    fireEvent.click(screen.getByRole('switch', { name: 'Turn on Invoice intake' }));
    expect(await screen.findByRole('dialog', { name: 'Turn on Invoice intake' })).toBeTruthy();
  });

  it('primes the composer for Desk, starts a blank draft, and imports an export (asking for a name when taken)', async () => {
    const bridge = setup({
      'automations.list': () => LIST,
      'automations.import': ({ exp }: { exp: { name: string } }) => {
        if (exp.name === 'digest') throw { code: 'conflict', message: 'taken', status: 409 };
        return { automation: automationDetail({ id: 'a9', name: exp.name }), warnings: [] };
      },
    });
    render(<AutomationsScreen projectId="p" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Describe one to Desk' }));
    expect(localStorage.getItem('desk.draft.p')).toBe("I'd like to automate: ");
    expect(window.location.hash).toBe('#/p/p/conversation');

    fireEvent.click(screen.getByRole('button', { name: 'Blank automation' }));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'weekly-note' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(window.location.hash).toBe('#/p/p/automations/new?name=weekly-note');

    const exp = { format: 'desk-automation/1', name: 'digest', definition: automationDetail().definition };
    fireEvent.change(screen.getByTestId('automation-import'), { target: { files: [new File([JSON.stringify(exp)], 'digest.json', { type: 'application/json' })] } });
    const dialog = await screen.findByRole('dialog', { name: 'Import as…' });
    expect((within(dialog).getByLabelText('Name') as HTMLInputElement).value).toBe('digest-2');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(window.location.hash).toBe('#/p/p/automations/a9'));
    expect(bridge.calls.filter((c) => c.channel === 'automations.import').map((c) => (c.input as { exp: { name: string } }).exp.name)).toEqual(['digest', 'digest-2']);
  });
});

describe('Automation header', () => {
  it('shows the version badge, run count and switch, and runs, exports and deletes', async () => {
    const d = automationDetail({ last_run: run({ id: 'r14', number: 14, status: 'running', finished_at: null }), grants: [{ tool: 'web_fetch', match: { domain: 'acme.com' }, action: 'allow' }] });
    const bridge = setup({
      'automations.get': () => d,
      'automations.validate': () => ({ errors: [], warnings: [], next_times: {} }),
      'automations.setEnabled': () => ({ ...d, enabled: false }),
      'automations.export': () => ({ format: 'desk-automation/1', name: 'digest', definition: d.definition }),
      'app.saveFile': () => true,
      'automations.remove': () => ({ ok: true }),
    });
    render(<AutomationsScreen projectId="p" automationId="a1" view="design" />);
    expect(await screen.findByRole('heading', { name: 'Weekly digest' })).toBeTruthy();
    expect(screen.getByText('v7 · tested in v6')).toBeTruthy();
    const tabs = screen.getByRole('navigation', { name: 'Automation' });
    expect(within(tabs).getByRole('link', { name: 'Runs (14)' }).getAttribute('href')).toBe('#/p/p/automations/a1/runs');
    expect(within(tabs).getByRole('link', { name: 'Grants (1)' })).toBeTruthy();
    fireEvent.click(screen.getByRole('switch', { name: 'Turn off Weekly digest' }));
    await screen.findByRole('switch', { name: 'Turn on Weekly digest' });
    fireEvent.click(screen.getByRole('button', { name: 'Run now…' }));
    expect(screen.getByRole('dialog', { name: 'Run Weekly digest' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Export…' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'app.saveFile')?.input).toMatchObject({ name: 'digest.desk-automation.json' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete…' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Delete Weekly digest?' })).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(window.location.hash).toBe('#/p/p/automations'));
  });
});
