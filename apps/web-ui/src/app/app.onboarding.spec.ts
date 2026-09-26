import { render, screen, waitFor, within } from '@testing-library/angular';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { App } from './app';
import { provideErrorBoundaries } from './components/error-boundary';
import { FakeDeskBridge, type FakeHandlers } from './testing/fake-bridge';

beforeEach(() => localStorage.clear());
afterEach(() => history.replaceState(null, '', window.location.pathname));

async function setup(hash: string, handlers: FakeHandlers = {}) {
  history.replaceState(null, '', hash);
  const bridge = new FakeDeskBridge(handlers);
  const view = await render(App, { providers: [...bridge.providers, ...provideErrorBoundaries()] });
  return { bridge, view, user: userEvent.setup() };
}

const HOME = { path: '/Users/me', parent: null, dirs: [{ name: 'code', path: '/Users/me/code' }] };

describe('App: onboarding, the map and the folder browser', () => {
  it('sends a browser that has not been through onboarding there, without the shell', async () => {
    await setup('#/map');
    expect(await screen.findByText('Welcome to Desk')).toBeTruthy();
    await waitFor(() => expect(window.location.hash).toBe('#/onboarding'));
    expect(screen.queryByRole('heading', { name: 'Projects' })).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'Places' })).toBeNull();
  });

  it('shows the map once onboarded', async () => {
    localStorage.setItem('desk.onboarded', '1');
    await setup('#/map');
    expect(await screen.findByRole('heading', { name: 'Projects', level: 1 })).toBeTruthy();
    expect(window.location.hash).toBe('#/map');
    expect(screen.queryByText('Welcome to Desk')).toBeNull();
  });

  it('opens the new-project sheet for #/map?new=1', async () => {
    localStorage.setItem('desk.onboarded', '1');
    await setup('#/map?new=1');
    expect(await screen.findByRole('dialog', { name: 'New project' })).toBeTruthy();
  });

  it('answers app.pickFolder with the folder browser during onboarding', async () => {
    const { bridge, user } = await setup('#/onboarding', { 'fs.listDirs': () => HOME });
    await screen.findByText('Welcome to Desk');
    const chosen = bridge.call('app.pickFolder', { purpose: 'source' });
    const dialog = await screen.findByRole('dialog', { name: 'Choose a folder' });
    await within(dialog).findByRole('button', { name: 'code' });
    await user.click(within(dialog).getByRole('button', { name: 'Choose this folder' }));
    await expect(chosen).resolves.toBe('/Users/me');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Choose a folder' })).toBeNull());
    expect(bridge.folderRequest()).toBeNull();
    expect(screen.getByText('Welcome to Desk')).toBeTruthy();
  });

  it('shows the folder browser over the shell, outside .app, and Cancel answers null', async () => {
    localStorage.setItem('desk.onboarded', '1');
    const { bridge, view, user } = await setup('#/map', { 'fs.listDirs': () => HOME });
    await screen.findByRole('heading', { name: 'Projects', level: 1 });
    const chosen = bridge.call('app.pickFolder', { purpose: 'source' });
    const dialog = await screen.findByRole('dialog', { name: 'Choose a folder' });
    const root = view.fixture.nativeElement as HTMLElement;
    expect(root.querySelector('.app [deskFolderBrowser]')).toBeNull();
    expect(root.querySelector('[deskFolderBrowser]')).not.toBeNull();
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await expect(chosen).resolves.toBeNull();
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Choose a folder' })).toBeNull());
  });
});
