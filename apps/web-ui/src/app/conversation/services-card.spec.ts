import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { projectFromOverview, type ProjectOverview, type ServiceRow } from '@desk/client';
import { FakeDeskBridge, type FakeHandlers } from '../testing/fake-bridge';
import { openableUrl, serviceState, ServicesCard } from './services-card';

const NOW = Date.parse('2026-09-24T20:30:00Z');
const svc = (over: Partial<ServiceRow>): ServiceRow => ({
  id: 's1',
  project_id: 'p',
  name: 'web',
  command: 'npm run dev',
  cwd: '.',
  agent_id: 't1',
  source_id: null,
  status: 'running',
  pid: 42,
  exit_code: null,
  exit_signal: null,
  stop_reason: null,
  url: 'http://localhost:5173',
  started_by: 'agent:d',
  started_at: '2026-09-24T20:18:00Z',
  ended_at: null,
  ...over,
});

const state = (services: ServiceRow[]) =>
  projectFromOverview({
    project: { id: 'p', name: 'App', goal: '', instructions: '', settings: {}, created_at: 't', updated_at: 't', archived_at: null },
    desk: null,
    sources: [{ id: 'src1', label: 'anyfight', path: '/Users/me/anyfight' }],
    plan: null,
    threads: [{ id: 't1', title: 'Renderer R1' }],
    approvals: [],
    services,
    last_seq: 0,
  } as unknown as ProjectOverview);

async function show(services: ServiceRow[], handlers: FakeHandlers = {}) {
  const bridge = new FakeDeskBridge(handlers);
  const view = await render(`<section deskServicesCard [project]="project"></section>`, {
    imports: [ServicesCard],
    componentProperties: { project: state(services) },
    providers: bridge.providers,
  });
  return { bridge, view };
}

describe('service state', () => {
  it('describes running, failed, clean exits and stops', () => {
    expect(serviceState(svc({}), NOW)).toEqual({ tone: 'run', text: 'running · 12m' });
    expect(serviceState(svc({ status: 'exited', exit_code: 1, ended_at: '2026-09-24T20:28:00Z' }), NOW)).toEqual({ tone: 'fail', text: 'exited (1) · 2m ago' });
    expect(serviceState(svc({ status: 'exited', exit_code: 0, ended_at: '2026-09-24T20:29:50Z' }), NOW)).toEqual({ tone: 'off', text: 'exited (0) · just now' });
    expect(serviceState(svc({ status: 'stopped', stop_reason: 'daemon_restart', ended_at: '2026-09-24T19:30:00Z' }), NOW).text).toBe('stopped · deskd restarted · 1h ago');
  });

  it('offers to open loopback web URLs only', () => {
    expect(openableUrl('http://localhost:5173')).toBe('http://localhost:5173/');
    expect(openableUrl('http://127.0.0.1:8000/docs')).toBe('http://127.0.0.1:8000/docs');
    expect(openableUrl('http://evil.example:5173')).toBeNull();
    expect(openableUrl('file:///etc/passwd')).toBeNull();
    expect(openableUrl(null)).toBeNull();
  });
});

describe('ServicesCard', () => {
  it('is hidden without services', async () => {
    const { view } = await show([]);
    expect(screen.queryByRole('region', { name: 'Services' })).toBeNull();
    const host = view.container.querySelector('section')!;
    expect(host.hidden).toBe(true);
    expect(host.className).toBe('');
    expect(host.childElementCount).toBe(0);
    expect(view.container.textContent).toBe('');
  });

  it('lists services with their thread, opens the URL, stops a running one and starts a stopped one', async () => {
    const { bridge } = await show(
      [svc({}), svc({ id: 's3', name: 'lab', source_id: 'src1', url: 'http://127.0.0.1:4317' }), svc({ id: 's2', name: 'api', status: 'exited', exit_code: 1, url: 'http://localhost:8000', ended_at: '2026-09-24T20:28:00Z' })],
      { 'services.stop': () => svc({ status: 'stopped' }), 'services.start': () => svc({ id: 's2' }), 'app.openExternal': () => ({ ok: true }) },
    );
    const card = screen.getByRole('region', { name: 'Services' });
    expect(card.className).toBe('card services-card');
    expect(within(card).getByText('2 running')).toBeTruthy();
    const web = within(card).getByRole('listitem', { name: /^web, running/ });
    expect(web.textContent).toContain(':5173');
    expect(web.textContent).toContain('from Renderer R1');
    expect(within(card).getByRole('listitem', { name: /^lab, running/ }).textContent).toContain('from anyfight');
    const api = within(card).getByRole('listitem', { name: /^api, exited \(1\)/ });
    expect(within(api).queryByRole('button', { name: 'Open' })).toBeNull();

    fireEvent.click(within(web).getByRole('button', { name: 'Open' }));
    fireEvent.click(within(web).getByRole('button', { name: 'Stop web' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'services.stop')?.input).toEqual({ id: 's1' }));
    // One action at a time: Start is enabled again once the stop has answered.
    await waitFor(() => expect((within(api).getByRole('button', { name: 'Start api' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(api).getByRole('button', { name: 'Start api' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'services.start')?.input).toEqual({ id: 's2' }));
    expect(bridge.calls.find((c) => c.channel === 'app.openExternal')?.input).toEqual({ url: 'http://localhost:5173/' });
  });

  it('shows the log tail as plain text in a sheet', async () => {
    const { bridge } = await show([svc({})], { 'services.logs': () => ({ text: '<b>not html</b>\n  ➜  Local: http://localhost:5173/', truncated: false }) });
    fireEvent.click(screen.getByRole('button', { name: 'Logs' }));
    const log = await screen.findByLabelText('web log');
    await waitFor(() => expect(log.textContent).toContain('Local: http://localhost:5173/'));
    expect(log.querySelector('b')).toBeNull();
    expect(bridge.calls.find((c) => c.channel === 'services.logs')?.input).toEqual({ id: 's1', lines: 500 });
    expect(screen.getByRole('dialog', { name: 'web · logs' }).textContent).toContain('npm run dev');
  });
});
