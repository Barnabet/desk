import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { initialGlobalState, type GlobalState } from '@desk/bff/contract';
import type { ModelInfo, ProjectSummary } from '@desk/protocol';
import { ToastService } from '../components/toast';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../testing/fake-bridge';
import { SystemScreen } from './system-screen';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const state = (): GlobalState => ({
  ...initialGlobalState(),
  connection: { status: 'live' },
  overview: [{ project: { id: 'p1', name: 'Onboarding', goal: '', updated_at: 't' }, desk_status: 'idle', threads: [], latest_report: null, plan_progress: { done: 0, total: 0 }, attention_count: 0 } as unknown as ProjectSummary],
  system: { proxy: 'down', lastSeq: 3, notices: [{ eventId: 3, ts: new Date().toISOString(), projectId: 'p1', level: 'warning', code: 'proxy_down', message: 'The model proxy is unreachable.' }] },
});

/** What desk web's DaemonManager reports: web mode, with the Desk app's LaunchAgent installed. */
const status = { running: true, version: '1.0.0', pid: 42, uptime_s: 3700, proxy: 'down', mode: 'web', bundledVersion: '1.0.0', build: null, bundledBuild: null, agent: 'installed' };
const model = (id: string): ModelInfo => ({ id, family: 'claude', context_window: 200000, max_output_tokens: 32000, reasoning_efforts: ['low', 'medium', 'high'], default_reasoning_effort: null, concurrency: 4, vision: true });

async function setup(extra: FakeHandlers = {}) {
  const bridge = new FakeDeskBridge({
    'daemon.status': () => status,
    'daemon.restart': () => ({ ...status, pid: 43 }),
    'daemon.stop': () => ({ ...status, running: false }),
    'config.endpoint': () => ({ configured: true, source: 'keychain', base_url: 'http://127.0.0.1:8317/v1' }),
    'config.get': () => ({ notifications: 'auto' }),
    'config.patch': (p: { notifications: string }) => p,
    'app.settings': () => ({ notifications: true, appearance: 'system' }),
    'app.updateSettings': (p: { notifications?: boolean; appearance?: string }) => ({ notifications: true, appearance: 'system', ...p }),
    'app.info': () => ({ version: '1.0.0', platform: 'darwin', packaged: false, dataDir: '/Users/me/Library/Application Support/Desk' }),
    'app.revealLogs': () => ({ ok: true }),
    'models.list': () => [model('claude-opus-5-5'), model('claude-fable-5-1')],
    'models.replace': ({ models }: { models: unknown[] }) => models,
    usage: () => ({ rows: [{ project_id: 'p1', model: 'claude-opus-5-5', prompt_tokens: 12000, completion_tokens: 3000 }], totals: { prompt_tokens: 12000, completion_tokens: 3000 } }),
    ...extra,
  });
  await render(SystemScreen, { providers: [...bridge.providers, provideGlobal(state())] });
  return bridge;
}

describe('SystemScreen', () => {
  it('shows deskd and controls it, without the LaunchAgent repair desk web does not offer', async () => {
    const bridge = await setup();
    const d = screen.getByRole('region', { name: 'deskd' });
    expect(await within(d).findByText(/v1\.0\.0 · pid 42 · up 1h/)).toBeTruthy();
    expect(d.textContent).toContain('Unreachable. Threads pause');
    expect(d.textContent).toContain('The Desk app’s deskd, through its LaunchAgent');
    expect(d.textContent).toContain('Yes, as a LaunchAgent');
    expect(within(d).queryByRole('button', { name: /LaunchAgent/ })).toBeNull();
    fireEvent.click(within(d).getByRole('button', { name: 'Restart' }));
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'daemon.restart')).toBe(true));
    const stop = within(d).getByRole('button', { name: 'Stop' }) as HTMLButtonElement;
    await waitFor(() => expect(stop.disabled).toBe(false));
    fireEvent.click(stop);
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Stop deskd?' })).getByRole('button', { name: 'Stop' }));
    expect(await within(d).findByRole('button', { name: 'Start' })).toBeTruthy();
    expect(bridge.calls.map((c) => c.channel)).not.toContain('daemon.repair');
  });

  it('keeps what Stop answered when a status read from before it lands later', async () => {
    // The 10-second poll, run by hand: its read waits until the test answers it.
    const polls: Array<() => void> = [];
    const realSetInterval = globalThis.setInterval;
    vi.spyOn(globalThis, 'setInterval').mockImplementation(((fn: () => void, ms?: number, ...rest: unknown[]) => {
      if (ms !== 10_000) return realSetInterval(fn, ms, ...rest);
      polls.push(fn);
      return 0;
    }) as typeof setInterval);
    let answer: (v: unknown) => void = () => {};
    let reads = 0;
    const bridge = await setup({ 'daemon.status': () => (++reads === 1 ? status : new Promise((resolve) => (answer = resolve))) });
    const d = screen.getByRole('region', { name: 'deskd' });
    await within(d).findByText(/pid 42/);
    expect(polls).toHaveLength(1);
    polls[0]!();
    polls[0]!();
    fireEvent.click(within(d).getByRole('button', { name: 'Stop' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Stop deskd?' })).getByRole('button', { name: 'Stop' }));
    expect(await within(d).findByRole('button', { name: 'Start' })).toBeTruthy();
    // The read from before Stop says deskd runs: it is dropped. The second poll waited for it and asks again.
    answer(status);
    await waitFor(() => expect(reads).toBe(3));
    expect(bridge.calls.filter((c) => c.channel === 'daemon.status')).toHaveLength(3);
    expect(within(d).getByRole('button', { name: 'Start' })).toBeTruthy();
    expect(within(d).queryByRole('button', { name: 'Restart' })).toBeNull();
    answer({ ...status, running: false });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(within(d).getByRole('button', { name: 'Start' })).toBeTruthy();
  });

  it('puts a notification switch back when its write fails', async () => {
    await setup({
      'config.patch': () => {
        throw { code: 'daemon_down', message: 'deskd is not running.' };
      },
    });
    const n = screen.getByRole('region', { name: 'Notifications' });
    const fromDeskd = within(n).getByLabelText(/From deskd/) as HTMLInputElement;
    await waitFor(() => expect(fromDeskd.checked).toBe(true));
    fireEvent.click(fromDeskd);
    await waitFor(() => expect(fromDeskd.checked).toBe(true));
    expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['deskd is not running.']);
  });

  it('shows the value a notification write answered, even when it differs from the click', async () => {
    const bridge = await setup({ 'config.patch': () => ({ notifications: 'auto' }) });
    const n = screen.getByRole('region', { name: 'Notifications' });
    const fromDeskd = within(n).getByLabelText(/From deskd/) as HTMLInputElement;
    await waitFor(() => expect(fromDeskd.checked).toBe(true));
    fireEvent.click(fromDeskd);
    expect(fromDeskd.checked).toBe(false);
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'config.patch')?.input).toEqual({ notifications: 'off' }));
    await waitFor(() => expect(fromDeskd.checked).toBe(true));
  });

  it('takes no second click on a notification switch while its permission or write is pending', async () => {
    let grant: (p: NotificationPermission) => void = () => {};
    const requestPermission = vi.fn(() => new Promise<NotificationPermission>((resolve) => (grant = resolve)));
    vi.stubGlobal('Notification', { permission: 'default', requestPermission });
    const patches: Array<() => void> = [];
    const bridge = await setup({
      'app.settings': () => ({ notifications: false }),
      'config.patch': (p: { notifications: string }) => new Promise((resolve) => patches.push(() => resolve(p))),
    });
    const n = screen.getByRole('region', { name: 'Notifications' });
    const fromApp = within(n).getByLabelText(/From the app/) as HTMLInputElement;
    const fromDeskd = within(n).getByLabelText(/From deskd/) as HTMLInputElement;
    await waitFor(() => expect(fromApp.disabled).toBe(false));
    await waitFor(() => expect(fromDeskd.disabled).toBe(false));
    // On opens the browser's prompt; an Off click before it is answered would be overtaken by the On.
    fireEvent.click(fromApp);
    expect(requestPermission).toHaveBeenCalledTimes(1);
    expect(fromApp.disabled).toBe(true);
    // jsdom still toggles a disabled box on a dispatched click, which a browser never delivers: the switch refuses it too.
    fireEvent.click(fromApp);
    await new Promise((resolve) => setTimeout(resolve, 20));
    grant('granted');
    await waitFor(() => expect(fromApp.disabled).toBe(false));
    expect(bridge.calls.filter((c) => c.channel === 'app.updateSettings').map((c) => c.input)).toEqual([{ notifications: true }]);
    expect(fromApp.checked).toBe(true);
    // deskd's switch: a second write while the first is pending could answer first and be undone by the first's answer.
    fireEvent.click(fromDeskd);
    expect(fromDeskd.disabled).toBe(true);
    fireEvent.click(fromDeskd);
    await waitFor(() => expect(patches.length).toBeGreaterThan(0));
    await new Promise((resolve) => setTimeout(resolve, 20));
    for (const answer of patches.slice().reverse()) answer();
    await waitFor(() => expect(fromDeskd.disabled).toBe(false));
    expect(bridge.calls.filter((c) => c.channel === 'config.patch').map((c) => c.input)).toEqual([{ notifications: 'off' }]);
    expect(fromDeskd.checked).toBe(false);
  });

  it("says when desk web runs this repository's deskd", async () => {
    await setup({ 'daemon.status': () => ({ ...status, agent: 'unsupported' }) });
    const d = screen.getByRole('region', { name: 'deskd' });
    await within(d).findByText('Development (runs from this repository)');
    expect(d.textContent).toContain('No. desk web starts it from this repository.');
  });

  it('shows the endpoint without the key, and both notification switches', async () => {
    const bridge = await setup();
    const ep = screen.getByRole('region', { name: 'Model endpoint' });
    expect(await within(ep).findByText('http://127.0.0.1:8317/v1')).toBeTruthy();
    fireEvent.click(within(ep).getByRole('button', { name: 'Change key' }));
    expect((within(ep).getByLabelText('API key') as HTMLInputElement).type).toBe('password');
    const n = screen.getByRole('region', { name: 'Notifications' });
    await waitFor(() => expect((within(n).getByLabelText(/From deskd/) as HTMLInputElement).checked).toBe(true));
    fireEvent.click(within(n).getByLabelText(/From the app/));
    fireEvent.click(within(n).getByLabelText(/From deskd/));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'app.updateSettings')?.input).toEqual({ notifications: false }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'config.patch')?.input).toEqual({ notifications: 'off' }));
  });

  it('asks this browser for permission inside the click that turns app notifications on', async () => {
    const requestPermission = vi.fn(async (): Promise<NotificationPermission> => 'granted');
    vi.stubGlobal('Notification', { permission: 'default', requestPermission });
    const bridge = await setup({ 'app.settings': () => ({ notifications: false }) });
    const n = screen.getByRole('region', { name: 'Notifications' });
    const fromApp = within(n).getByLabelText(/From the app/) as HTMLInputElement;
    await waitFor(() => expect(fromApp.disabled).toBe(false));
    fireEvent.click(fromApp);
    expect(requestPermission).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'app.updateSettings')?.input).toEqual({ notifications: true }));
    expect(bridge.notifyPermissions).toEqual(['granted']);
    expect(n.textContent).not.toContain('notifications from Desk');
  });

  it('says when this browser has not allowed notifications, and when it blocks them', async () => {
    const requestPermission = vi.fn(async (): Promise<NotificationPermission> => 'denied');
    vi.stubGlobal('Notification', { permission: 'default', requestPermission });
    await setup();
    const n = screen.getByRole('region', { name: 'Notifications' });
    expect(await within(n).findByText(/has not allowed notifications from Desk yet/)).toBeTruthy();
    fireEvent.click(within(n).getByRole('button', { name: 'Allow notifications' }));
    expect(requestPermission).toHaveBeenCalledTimes(1);
    expect(await within(n).findByText(/blocks notifications from Desk/)).toBeTruthy();
    expect(within(n).queryByRole('button', { name: 'Allow notifications' })).toBeNull();
  });

  it("shows the appearance as the system's: a browser follows it, and only the Desk app chooses", async () => {
    const bridge = await setup();
    const a = screen.getByRole('region', { name: 'Appearance' });
    const choice = within(a).getByRole('group', { name: 'Appearance' });
    expect(within(choice).getAllByRole('button').map((b) => [b.textContent, b.getAttribute('aria-pressed'), (b as HTMLButtonElement).disabled])).toEqual([
      ['System', 'true', true],
      ['Light', 'false', true],
      ['Dark', 'false', true],
    ]);
    expect(a.textContent).toContain('The web UI follows your system');
    expect(bridge.calls.filter((c) => c.channel === 'app.updateSettings')).toEqual([]);
  });

  it('shows the model registry under its hint', async () => {
    await setup();
    const reg = screen.getByRole('region', { name: 'Model registry' });
    expect(reg.textContent).toContain('Concurrency caps how many calls to a model run at once across all projects.');
    expect(((await within(reg).findByLabelText('Model 1 id')) as HTMLInputElement).value).toBe('claude-opus-5-5');
  });

  it('shows usage by model and project, notices, and the data directory', async () => {
    const bridge = await setup();
    const u = screen.getByRole('region', { name: 'Usage' });
    expect(await within(u).findByRole('link', { name: 'Onboarding' })).toBeTruthy();
    expect(within(u).getByText('claude-opus-5-5')).toBeTruthy();
    expect((bridge.calls.find((c) => c.channel === 'usage')?.input as { since?: string }).since).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    fireEvent.click(within(u).getByRole('button', { name: 'All time' }));
    await waitFor(() => expect(bridge.calls.filter((c) => c.channel === 'usage').at(-1)?.input).toEqual({}));
    expect(screen.getByRole('region', { name: 'System notices' }).textContent).toContain('The model proxy is unreachable.');
    const data = screen.getByRole('region', { name: 'Data' });
    expect(await within(data).findByText('/Users/me/Library/Application Support/Desk')).toBeTruthy();
    fireEvent.click(within(data).getByRole('button', { name: 'Reveal logs' }));
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'app.revealLogs')).toBe(true));
  });

  it('reports skill environments and cleans up the unused ones', async () => {
    let envs = [
      { scope: 'global', project_id: null, name: 'paper-lookup', bytes: 60 * 1024 * 1024, orphan: false },
      { scope: 'global', project_id: null, name: 'old-skill', bytes: 40 * 1024 * 1024, orphan: true },
    ];
    const bridge = await setup({
      'system.runtimes': () => ({ bytes: envs.reduce((n, e) => n + e.bytes, 0), envs }),
      'system.runtimesCleanup': () => {
        envs = envs.filter((e) => !e.orphan);
        return { removed: 1, bytes: 40 * 1024 * 1024 };
      },
    });
    const data = screen.getByRole('region', { name: 'Data' });
    expect(await within(data).findByText(/100\.0 MB for 2 skills/)).toBeTruthy();
    fireEvent.click(within(data).getByRole('button', { name: 'Clean up unused (40.0 MB)' }));
    expect(await within(data).findByText(/60\.0 MB for 1 skill$/)).toBeTruthy();
    expect(within(data).queryByRole('button', { name: /Clean up/ })).toBeNull();
    expect(bridge.calls.filter((c) => c.channel === 'system.runtimesCleanup')).toHaveLength(1);
  });
});
