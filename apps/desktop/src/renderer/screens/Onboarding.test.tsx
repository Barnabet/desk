// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { installBridge } from '../test/bridge';
import { isOnboarded, Onboarding } from './Onboarding';

afterEach(cleanup);
beforeEach(() => {
  localStorage.clear();
  window.location.hash = '#/onboarding';
});

const live = () => globalStore.set({ ...initialGlobalState(), connection: { status: 'live' }, health: { version: '1.0.0', protocol_version: 1, proxy: 'up', uptime_s: 1 } });

describe('Onboarding', () => {
  it('starts the daemon when it is not running', async () => {
    globalStore.set({ ...initialGlobalState(), connection: { status: 'offline' } });
    const bridge = installBridge({ 'daemon.start': () => (live(), { running: true }) });
    render(<Onboarding />);
    fireEvent.click(screen.getByRole('button', { name: 'Start Desk' }));
    await waitFor(() => expect(screen.getByText('deskd 1.0.0 is running.')).toBeTruthy());
    expect(bridge.calls.map((c) => c.channel)).toEqual(['daemon.start']);
  });

  it('saves and tests a new endpoint without keeping the key, then creates the first project', async () => {
    live();
    let configured = false;
    const bridge = installBridge({
      'config.endpoint': () => ({ configured, source: configured ? 'keychain' : null, base_url: configured ? 'http://127.0.0.1:8317/v1' : null }),
      'config.saveEndpoint': () => ((configured = true), { configured: true, source: 'keychain', base_url: 'http://127.0.0.1:8317/v1' }),
      'config.testEndpoint': () => ({ ok: true, models: ['claude-opus-5-5', 'claude-sonnet-5'] }),
      'app.pickFolder': () => '/Users/me/repo',
      'projects.create': (input: { name: string }) => ({ project: { id: 'p1', name: input.name } }),
    });
    render(<Onboarding />);
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    const key = (await screen.findByLabelText('API key')) as HTMLInputElement;
    expect(key.type).toBe('password');
    fireEvent.change(key, { target: { value: 'sk-secret' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save and test' }));
    await waitFor(() => expect(screen.getByText('Connected. 2 models available.')).toBeTruthy());
    expect(screen.queryByDisplayValue('sk-secret')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Launch' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add folder…' }));
    await waitFor(() => expect(screen.getByText('/Users/me/repo')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Create project' }));
    await waitFor(() => expect(window.location.hash).toBe('#/map'));
    expect(isOnboarded()).toBe(true);
    const create = bridge.calls.find((c) => c.channel === 'projects.create');
    expect(create?.input).toEqual({ name: 'Launch', goal: '', sources: [{ path: '/Users/me/repo' }] });
    expect(bridge.calls.find((c) => c.channel === 'config.saveEndpoint')?.input).toEqual({ base_url: 'http://127.0.0.1:8317/v1', api_key: 'sk-secret' });
  });

  it('continues when this deskd manages its own endpoint', async () => {
    live();
    installBridge({ 'config.endpoint': () => Promise.reject({ code: 'unsupported', message: 'Model endpoint setup is not available', status: 501 }) });
    render(<Onboarding />);
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    await waitFor(() => expect(screen.getByText(/manages its model endpoint itself/)).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByRole('heading', { name: 'Create your first project' })).toBeTruthy();
  });
});
