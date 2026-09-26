import { render, screen, waitFor } from '@testing-library/angular';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { DeskBridge } from '../core/desk-bridge';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { EndpointPanel, type EndpointState } from './endpoint-panel';

const env = { configured: true, source: 'env', base_url: 'http://127.0.0.1:8317/v1' } as const;
const keychain = { configured: true, source: 'keychain', base_url: 'http://127.0.0.1:8317/v1' } as const;

async function setup(handlers: ConstructorParameters<typeof FakeDeskBridge>[0]) {
  const bridge = new FakeDeskBridge(handlers);
  const statuses: EndpointState[] = [];
  await render(EndpointPanel, { providers: [{ provide: DeskBridge, useValue: bridge }], on: { statusChanged: (s: EndpointState) => statuses.push(s) } });
  return { bridge, statuses, user: userEvent.setup() };
}

describe('EndpointPanel', () => {
  it('says where a configured endpoint comes from, and tests the connection', async () => {
    const { statuses, user } = await setup({ 'config.endpoint': () => env, 'config.testEndpoint': () => ({ ok: true, models: ['claude-opus-5-5'] }) });
    expect(await screen.findByText(/from the DESK_OPENAI_\* environment variables\./)).toBeTruthy();
    expect(screen.getByText('http://127.0.0.1:8317/v1')).toBeTruthy();
    expect(statuses).toEqual([env]);
    await user.click(screen.getByRole('button', { name: 'Test connection' }));
    expect(await screen.findByText('Connected. 1 model available.')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'Use a different endpoint' }));
    expect(await screen.findByText(/Saving stores this endpoint in your Keychain/)).toBeTruthy();
  });

  it('says why a connection test failed', async () => {
    const { user } = await setup({ 'config.endpoint': () => env, 'config.testEndpoint': () => ({ ok: false, error: '401 Unauthorized' }) });
    await user.click(await screen.findByRole('button', { name: 'Test connection' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Couldn’t connect: 401 Unauthorized');
  });

  it('changes a Keychain key, saving only with a key, and forgets what was typed on Cancel', async () => {
    const { user } = await setup({ 'config.endpoint': () => keychain });
    await user.click(await screen.findByRole('button', { name: 'Change key' }));
    expect(screen.queryByText(/Saving stores this endpoint in your Keychain/)).toBeNull();
    expect((screen.getByRole('button', { name: 'Save and test' }) as HTMLButtonElement).disabled).toBe(true);
    const key = screen.getByLabelText('API key') as HTMLInputElement;
    expect(key.type).toBe('password');
    await user.type(key, 'sk-new');
    expect((screen.getByRole('button', { name: 'Save and test' }) as HTMLButtonElement).disabled).toBe(false);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByLabelText('API key')).toBeNull());
    await user.click(screen.getByRole('button', { name: 'Change key' }));
    expect(((await screen.findByLabelText('API key')) as HTMLInputElement).value).toBe('');
  });

  it('reports a deskd that manages its own endpoint', async () => {
    const { statuses } = await setup({ 'config.endpoint': () => Promise.reject({ code: 'unsupported', message: 'Model endpoint setup is not available', status: 501 }) });
    expect(await screen.findByText('This deskd manages its model endpoint itself.')).toBeTruthy();
    expect(statuses).toEqual(['unsupported']);
    expect(screen.queryByLabelText('API key')).toBeNull();
  });

  it('shows any other failure to load', async () => {
    const { statuses } = await setup({ 'config.endpoint': () => Promise.reject({ code: 'daemon_not_running', message: 'Desk is not running.', status: 503 }) });
    expect((await screen.findByRole('alert')).textContent).toContain('Desk is not running.');
    expect(statuses).toEqual([]);
  });
});
