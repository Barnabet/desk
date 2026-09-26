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
/** Home (with .claude when hidden folders show) and ~/code. */
const listDirs = (input: { path?: string; hidden?: boolean }) =>
  input.path === '/Users/me/code'
    ? { path: '/Users/me/code', parent: '/Users/me', dirs: [{ name: 'app', path: '/Users/me/code/app' }] }
    : { ...HOME, dirs: input.hidden ? [{ name: '.claude', path: '/Users/me/.claude' }, ...HOME.dirs] : HOME.dirs };

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

  it('closes only the folder browser on Escape, not the new-project sheet under it', async () => {
    localStorage.setItem('desk.onboarded', '1');
    const { bridge, user } = await setup('#/map?new=1', { 'fs.listDirs': () => HOME });
    await screen.findByRole('dialog', { name: 'New project' });
    await user.type(screen.getByLabelText('Name'), 'Launch');
    await user.click(screen.getByRole('button', { name: 'Add folder…' }));
    const browser = await screen.findByRole('dialog', { name: 'Choose a folder' });
    await within(browser).findByRole('button', { name: 'code' });
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Choose a folder' })).toBeNull());
    expect(bridge.folderRequest()).toBeNull();
    expect(screen.getByRole('dialog', { name: 'New project' })).toBeTruthy();
    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Launch');
    expect(window.location.hash).toBe('#/map?new=1');
  });

  it('opens a fresh folder browser for a second request, at its own purpose and listing', async () => {
    localStorage.setItem('desk.onboarded', '1');
    const { bridge, user } = await setup('#/map', { 'fs.listDirs': listDirs });
    await screen.findByRole('heading', { name: 'Projects', level: 1 });
    const first = bridge.call('app.pickFolder', { purpose: 'source' });
    const dialog = await screen.findByRole('dialog', { name: 'Choose a folder' });
    await user.click(await within(dialog).findByRole('button', { name: 'code' }));
    await within(dialog).findByRole('button', { name: 'app' });
    const second = bridge.call('app.pickFolder', { purpose: 'skill-import' });
    await expect(first).resolves.toBeNull();
    const again = await screen.findByRole('dialog', { name: 'Choose a skill folder' });
    await within(again).findByRole('button', { name: '.claude' });
    expect(dialog.isConnected).toBe(false);
    expect((within(again).getByRole('checkbox', { name: 'Show hidden folders' }) as HTMLInputElement).checked).toBe(true);
    expect((within(again).getByLabelText('Folder') as HTMLInputElement).value).toBe('/Users/me');
    expect(bridge.calls.filter((c) => c.channel === 'fs.listDirs').map((c) => c.input)).toEqual([{ hidden: false }, { path: '/Users/me/code', hidden: false }, { hidden: true }]);
    await user.click(within(again).getByRole('button', { name: 'Choose this folder' }));
    await expect(second).resolves.toBe('/Users/me');
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
