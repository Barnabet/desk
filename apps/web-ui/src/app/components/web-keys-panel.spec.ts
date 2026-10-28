import { render, screen, waitFor, within } from '@testing-library/angular';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { DeskBridge } from '../core/desk-bridge';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { WebKeysPanel } from './web-keys-panel';

const none = { serper: { set: false, source: null }, firecrawl: { set: false, source: null } };

async function setup(handlers: ConstructorParameters<typeof FakeDeskBridge>[0]) {
  const bridge = new FakeDeskBridge(handlers);
  await render(WebKeysPanel, { providers: [{ provide: DeskBridge, useValue: bridge }] });
  return { bridge, user: userEvent.setup() };
}

const row = async (name: string) => within(await screen.findByRole('group', { name }));

describe('WebKeysPanel', () => {
  it('shows each provider with what it powers and whether its key is set', async () => {
    await setup({ 'config.webKeys': () => ({ ...none, firecrawl: { set: true, source: 'keychain' } }) });
    const serper = await row('Serper');
    expect(serper.getByText('Search')).toBeTruthy();
    expect(serper.getByText('Not set')).toBeTruthy();
    const firecrawl = await row('Firecrawl');
    expect(firecrawl.getByText('Reading pages, maps, crawls and extraction')).toBeTruthy();
    expect(firecrawl.getByText('Set')).toBeTruthy();
  });

  it('saves a typed key, then clears the field', async () => {
    let saved: unknown;
    const { bridge, user } = await setup({
      'config.webKeys': () => none,
      'config.saveWebKey': (i: unknown) => ((saved = i), { set: true, source: 'keychain' }),
    });
    const serper = await row('Serper');
    const input = serper.getByLabelText('Serper API key') as HTMLInputElement;
    expect((serper.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    await user.type(input, 'my-serper-key-1');
    await user.click(serper.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(saved).toEqual({ provider: 'serper', key: 'my-serper-key-1' }));
    await waitFor(() => expect(input.value).toBe(''));
    expect(await serper.findByText('Set')).toBeTruthy();
    expect(bridge.calls.some((c) => c.channel === 'config.saveWebKey')).toBe(true);
  });

  it('tests a stored key and says whether it works', async () => {
    let ok = true;
    const { user } = await setup({
      'config.webKeys': () => ({ ...none, serper: { set: true, source: 'keychain' } }),
      'config.testWebKey': () => (ok ? { ok: true } : { ok: false, error: 'HTTP 403: Unauthorized.' }),
    });
    const serper = await row('Serper');
    await user.click(serper.getByRole('button', { name: 'Test' }));
    expect(await serper.findByText('Key works.')).toBeTruthy();
    ok = false;
    await user.click(serper.getByRole('button', { name: 'Test' }));
    expect((await serper.findByRole('alert')).textContent).toContain('Key test failed: HTTP 403: Unauthorized.');
  });

  it('removes a key after a confirmation', async () => {
    let removed: unknown;
    const { user } = await setup({
      'config.webKeys': () => ({ ...none, firecrawl: { set: true, source: 'keychain' } }),
      'config.removeWebKey': (i: unknown) => ((removed = i), { set: false, source: null }),
    });
    const firecrawl = await row('Firecrawl');
    await user.click(firecrawl.getByRole('button', { name: 'Remove' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Remove the Firecrawl key?')).toBeTruthy();
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(removed).toEqual({ provider: 'firecrawl' }));
    expect(await firecrawl.findByText('Not set')).toBeTruthy();
  });

  it('leaves an environment key alone', async () => {
    await setup({ 'config.webKeys': () => ({ ...none, serper: { set: true, source: 'env' } }) });
    const serper = await row('Serper');
    expect(serper.getByText('Set from environment')).toBeTruthy();
    expect(serper.queryByLabelText('Serper API key')).toBeNull();
    expect(serper.queryByRole('button', { name: 'Remove' })).toBeNull();
    expect(serper.getByText(/DESK_SERPER_API_KEY/)).toBeTruthy();
  });

  it('opens the dashboard link only after a confirmation', async () => {
    const { bridge, user } = await setup({ 'config.webKeys': () => none, 'app.openExternal': () => undefined });
    const serper = await row('Serper');
    await user.click(serper.getByRole('button', { name: 'Get a key' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Open in browser' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'app.openExternal')?.input).toEqual({ url: 'https://serper.dev/api-key' }));
  });

  it('says when this deskd cannot store web keys', async () => {
    await setup({
      'config.webKeys': () => {
        throw { code: 'unsupported', message: 'Web key setup is not available', status: 501 };
      },
    });
    expect(await screen.findByText(/This deskd takes its web keys from its environment/)).toBeTruthy();
  });
});
