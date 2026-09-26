import { TestBed } from '@angular/core/testing';
import { render, screen, waitFor } from '@testing-library/angular';
import userEvent from '@testing-library/user-event';
import { initialGlobalState, type GlobalState } from '@desk/bff/contract';
import { beforeEach, describe, expect, it } from 'vitest';
import { DeskBridge } from '../core/desk-bridge';
import { GlobalStore } from '../core/global.store';
import { isOnboarded } from '../core/onboarded';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { Onboarding } from './onboarding';

beforeEach(() => {
  localStorage.clear();
  window.location.hash = '#/onboarding';
});

const live = (): GlobalState => ({ ...initialGlobalState(), connection: { status: 'live' }, health: { version: '1.0.0', protocol_version: 1, proxy: 'up', uptime_s: 1 } });
const setGlobal = (s: GlobalState) => TestBed.inject(GlobalStore).set(s);

async function setup(state: GlobalState, handlers: ConstructorParameters<typeof FakeDeskBridge>[0]) {
  const bridge = new FakeDeskBridge(handlers);
  const view = await render(Onboarding, { providers: [{ provide: DeskBridge, useValue: bridge }] });
  setGlobal(state);
  view.detectChanges();
  return { bridge, user: userEvent.setup() };
}

describe('Onboarding', () => {
  it('starts the daemon when it is not running', async () => {
    const { bridge, user } = await setup({ ...initialGlobalState(), connection: { status: 'offline' } }, { 'daemon.start': () => (setGlobal(live()), { running: true }) });
    expect(screen.getByText('deskd is not running yet.')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Start Desk' }));
    expect(await screen.findByText('deskd 1.0.0 is running.')).toBeTruthy();
    expect(bridge.calls.map((c) => c.channel)).toEqual(['daemon.start']);
  });

  it('saves and tests a new endpoint without keeping the key, then creates the first project', async () => {
    let configured = false;
    const { bridge, user } = await setup(live(), {
      'config.endpoint': () => ({ configured, source: configured ? 'keychain' : null, base_url: configured ? 'http://127.0.0.1:8317/v1' : null }),
      'config.saveEndpoint': () => ((configured = true), { configured: true, source: 'keychain', base_url: 'http://127.0.0.1:8317/v1' }),
      'config.testEndpoint': () => ({ ok: true, models: ['claude-opus-5-5', 'claude-sonnet-5'] }),
      'app.pickFolder': () => '/Users/me/repo',
      'projects.create': (input: { name: string }) => ({ project: { id: 'p1', name: input.name } }),
    });
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    const key = (await screen.findByLabelText('API key')) as HTMLInputElement;
    expect(key.type).toBe('password');
    await user.type(key, 'sk-secret');
    await user.click(screen.getByRole('button', { name: 'Save and test' }));
    expect(await screen.findByText('Connected. 2 models available.')).toBeTruthy();
    expect(screen.queryByDisplayValue('sk-secret')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await user.type(await screen.findByLabelText('Name'), 'Launch');
    await user.click(screen.getByRole('button', { name: 'Add folder…' }));
    expect(await screen.findByText('/Users/me/repo')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Create project' }));
    await waitFor(() => expect(window.location.hash).toBe('#/map'));
    expect(isOnboarded()).toBe(true);
    const create = bridge.calls.find((c) => c.channel === 'projects.create');
    expect(create?.input).toEqual({ name: 'Launch', goal: '', sources: [{ path: '/Users/me/repo' }] });
    expect(bridge.calls.find((c) => c.channel === 'config.saveEndpoint')?.input).toEqual({ base_url: 'http://127.0.0.1:8317/v1', api_key: 'sk-secret' });
  });

  it('continues when this deskd manages its own endpoint', async () => {
    const { user } = await setup(live(), { 'config.endpoint': () => Promise.reject({ code: 'unsupported', message: 'Model endpoint setup is not available', status: 501 }) });
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByText(/manages its model endpoint itself/)).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByRole('heading', { name: 'Create your first project' })).toBeTruthy();
  });

  it('lets someone who already has projects skip to the map', async () => {
    const overview = [{ project: { id: 'p1' } }, { project: { id: 'p2' } }] as unknown as GlobalState['overview'];
    const { user } = await setup({ ...live(), overview }, { 'config.endpoint': () => ({ configured: false, source: null, base_url: null }) });
    const steps = screen.getByRole('list', { name: 'Setup steps' });
    expect(steps.querySelector('[aria-current="step"]')?.textContent).toBe('Daemon');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await user.click(await screen.findByRole('button', { name: 'Skip for now' }));
    expect(await screen.findByText('You already have 2 projects.')).toBeTruthy();
    expect(steps.querySelector('[aria-current="step"]')?.textContent).toBe('First project');
    expect(Array.from(steps.querySelectorAll('li.done')).map((li) => li.textContent)).toEqual(['Daemon', 'Model endpoint']);
    await user.click(screen.getByRole('button', { name: 'Skip to the map' }));
    await waitFor(() => expect(window.location.hash).toBe('#/map'));
    expect(isOnboarded()).toBe(true);
  });
});
