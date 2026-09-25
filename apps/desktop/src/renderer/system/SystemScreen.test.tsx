// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ModelInfo, ProjectSummary } from '@desk/protocol';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { installBridge } from '../test/bridge';
import { modelProblems } from './ModelsEditor';
import { SystemScreen } from './SystemScreen';

afterEach(cleanup);
beforeEach(() => {
  globalStore.set({
    ...initialGlobalState(),
    connection: { status: 'live' },
    overview: [{ project: { id: 'p1', name: 'Onboarding', goal: '', updated_at: 't' }, desk_status: 'idle', threads: [], latest_report: null, plan_progress: { done: 0, total: 0 }, attention_count: 0 } as unknown as ProjectSummary],
    system: { proxy: 'down', lastSeq: 3, notices: [{ eventId: 3, ts: new Date().toISOString(), projectId: 'p1', level: 'warning', code: 'proxy_down', message: 'The model proxy is unreachable.' }] },
  });
});

const status = { running: true, version: '1.0.0', pid: 42, uptime_s: 3700, proxy: 'down', mode: 'packaged', bundledVersion: '1.0.0', build: 'b1', bundledBuild: 'b1', agent: 'installed' };
const model = (id: string): ModelInfo => ({ id, family: 'claude', context_window: 200000, max_output_tokens: 32000, reasoning_efforts: ['low', 'medium', 'high'], default_reasoning_effort: null, concurrency: 4, vision: true });

function setup(extra: Record<string, (input: any) => unknown> = {}) {
  const bridge = installBridge({
    'daemon.status': () => status,
    'daemon.restart': () => ({ ...status, pid: 43 }),
    'daemon.stop': () => ({ ...status, running: false }),
    'daemon.repair': () => status,
    'config.endpoint': () => ({ configured: true, source: 'keychain', base_url: 'http://127.0.0.1:8317/v1' }),
    'config.get': () => ({ notifications: 'auto' }),
    'config.patch': (p: { notifications: string }) => p,
    'app.settings': () => ({ notifications: true, appearance: 'system' }),
    'app.updateSettings': (p: { notifications?: boolean; appearance?: string }) => ({ notifications: true, appearance: 'system', ...p }),
    'app.info': () => ({ version: '1.0.0', platform: 'darwin', packaged: true, dataDir: '/Users/me/Library/Application Support/Desk' }),
    'app.revealLogs': () => undefined,
    'models.list': () => [model('claude-opus-5-5'), model('claude-fable-5-1')],
    'models.replace': ({ models }: { models: unknown[] }) => models,
    usage: () => ({ rows: [{ project_id: 'p1', model: 'claude-opus-5-5', prompt_tokens: 12000, completion_tokens: 3000 }], totals: { prompt_tokens: 12000, completion_tokens: 3000 } }),
    ...extra,
  });
  render(<SystemScreen />);
  return bridge;
}

describe('modelProblems', () => {
  it('explains what the daemon would reject', () => {
    expect(modelProblems([])).toEqual(['Keep at least one model.']);
    expect(modelProblems([model('a'), model('a')])).toEqual(['a is listed twice.']);
    expect(modelProblems([{ ...model(''), concurrency: 0 }])).toEqual(['Every model needs an id.', 'Token limits must be positive and concurrency at least 1.']);
  });
});

describe('SystemScreen', () => {
  it('shows deskd and controls it', async () => {
    const bridge = setup();
    const d = screen.getByRole('region', { name: 'deskd' });
    expect(await within(d).findByText(/v1\.0\.0 · pid 42 · up 1h/)).toBeTruthy();
    expect(d.textContent).toContain('Unreachable. Threads pause');
    fireEvent.click(within(d).getByRole('button', { name: 'Restart' }));
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'daemon.restart')).toBe(true));
    fireEvent.click(await within(d).findByRole('button', { name: 'Repair LaunchAgent' }));
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'daemon.repair')).toBe(true));
    fireEvent.click(await within(d).findByRole('button', { name: 'Stop' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Stop' }).at(-1)!);
    expect(await within(d).findByRole('button', { name: 'Start' })).toBeTruthy();
  });

  it('shows the endpoint without the key, and both notification switches', async () => {
    const bridge = setup();
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

  it('switches the appearance', async () => {
    const bridge = setup();
    const a = screen.getByRole('region', { name: 'Appearance' });
    await waitFor(() => expect(within(a).getByRole('button', { name: 'System' }).getAttribute('aria-pressed')).toBe('true'));
    fireEvent.click(within(a).getByRole('button', { name: 'Dark' }));
    await waitFor(() => expect(within(a).getByRole('button', { name: 'Dark' }).getAttribute('aria-pressed')).toBe('true'));
    expect(within(a).getByRole('button', { name: 'System' }).getAttribute('aria-pressed')).toBe('false');
    expect(bridge.calls.find((c) => c.channel === 'app.updateSettings')?.input).toEqual({ appearance: 'dark' });
  });

  it('edits the model registry', async () => {
    const bridge = setup();
    const reg = screen.getByRole('region', { name: 'Model registry' });
    fireEvent.click(await within(reg).findByRole('button', { name: 'Add model' }));
    fireEvent.change(within(reg).getByLabelText('Model 3 id'), { target: { value: 'claude-opus-5-5' } });
    expect(within(reg).getByRole('alert').textContent).toContain('claude-opus-5-5 is listed twice.');
    expect((within(reg).getByRole('button', { name: 'Save registry' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(reg).getByLabelText('Model 3 id'), { target: { value: 'gpt-6-sol' } });
    fireEvent.change(within(reg).getByLabelText('Model 3 family'), { target: { value: 'gpt' } });
    // Reasoning levels: chips per level, and a default drawn from the chosen levels.
    const pressed = (i: number) => within(within(reg).getByRole('group', { name: `Model ${i} reasoning levels` })).getAllByRole('button').filter((b) => b.getAttribute('aria-pressed') === 'true').map((b) => b.textContent);
    expect(pressed(1)).toEqual(['low', 'medium', 'high']);
    expect((within(reg).getByLabelText('Model 3 default reasoning effort') as HTMLSelectElement).disabled).toBe(true);
    const levels3 = within(reg).getByRole('group', { name: 'Model 3 reasoning levels' });
    fireEvent.click(within(levels3).getByRole('button', { name: 'max' }));
    fireEvent.click(within(levels3).getByRole('button', { name: 'high' }));
    expect(pressed(3)).toEqual(['high', 'max']);
    fireEvent.change(within(reg).getByLabelText('Model 3 default reasoning effort'), { target: { value: 'max' } });
    fireEvent.click(within(levels3).getByRole('button', { name: 'max' }));
    expect((within(reg).getByLabelText('Model 3 default reasoning effort') as HTMLSelectElement).value).toBe('');
    fireEvent.change(within(reg).getByLabelText('Model 3 default reasoning effort'), { target: { value: 'high' } });
    // Vision: on for new models; turning it off makes view_image refuse for that model.
    expect((within(reg).getByLabelText('Model 3 sees images') as HTMLInputElement).checked).toBe(true);
    fireEvent.click(within(reg).getByLabelText('Model 3 sees images'));
    fireEvent.click(within(reg).getByRole('button', { name: 'Remove model 2' }));
    fireEvent.click(within(reg).getByRole('button', { name: 'Save registry' }));
    await waitFor(() =>
      expect((bridge.calls.find((c) => c.channel === 'models.replace')?.input as { models: ModelInfo[] }).models.map((m) => [m.id, m.reasoning_efforts, m.default_reasoning_effort, m.vision])).toEqual([
        ['claude-opus-5-5', ['low', 'medium', 'high'], null, true],
        ['gpt-6-sol', ['high'], 'high', false],
      ]),
    );
  });

  it('shows usage by model and project, notices, and the data directory', async () => {
    const bridge = setup();
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
    const bridge = setup({
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
