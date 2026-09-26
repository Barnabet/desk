import { render, screen, waitFor } from '@testing-library/angular';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { initialGlobalState, type GlobalState } from '@desk/bff/contract';
import type { AttentionItem } from '@desk/protocol';
import { App } from './app';
import { provideErrorBoundaries } from './components/error-boundary';
import { FakeDeskBridge } from './testing/fake-bridge';

const item = (id: string): AttentionItem => ({ id, kind: 'approval', project_id: 'p', project_name: 'P', agent_id: null, title: 't', detail: '', created_at: '', ref: {} });
const go = (hash: string) => history.replaceState(null, '', hash);

async function renderApp(bridge = new FakeDeskBridge()) {
  const view = await render(App, { providers: [...bridge.providers, ...provideErrorBoundaries()] });
  return { bridge, view };
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('desk.onboarded', '1');
});
afterEach(() => history.replaceState(null, '', window.location.pathname));

describe('App', () => {
  it('shows only how to sign in while this browser has no session', async () => {
    const bridge = new FakeDeskBridge();
    bridge.signOut();
    go('#/map');
    await renderApp(bridge);
    expect(screen.getByRole('heading', { name: 'Open Desk from your terminal' })).toBeTruthy();
    expect(screen.getByText(/Run desk web on this computer/)).toBeTruthy();
    expect(screen.queryByRole('navigation', { name: 'Places' })).toBeNull();
    expect(window.location.hash).toBe('#/map');
  });

  it('sends a first-time viewer to onboarding, which shows without the shell', async () => {
    localStorage.removeItem('desk.onboarded');
    go('#/map');
    await renderApp();
    await waitFor(() => expect(window.location.hash).toBe('#/onboarding'));
    expect(await screen.findByText('Onboarding is not in the web UI yet')).toBeTruthy();
    expect(screen.queryByRole('navigation', { name: 'Places' })).toBeNull();
    expect(screen.getByRole('status')).toBeTruthy();
  });

  it('shows the shell, the project tabs and the screen for the route', async () => {
    go('#/p/p1/threads');
    const { view } = await renderApp();
    expect(screen.getByRole('navigation', { name: 'Places' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Threads' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByText('Threads is not in the web UI yet')).toBeTruthy();
    const app = (view.fixture.nativeElement as HTMLElement).querySelector('.app')!;
    expect([...app.children].map((c) => c.tagName.toLowerCase() + (c.className ? `.${c.className.split(' ').join('.')}` : ''))).toEqual([
      'header.titlebar',
      'nav.subnav',
      'main.screen',
      'div.toaster',
    ]);
  });

  it('follows desk:global and desk:navigate', async () => {
    go('#/map');
    const { bridge, view } = await renderApp();
    bridge.emit('desk:global', { ...initialGlobalState(), connection: { status: 'offline' } } satisfies GlobalState);
    view.detectChanges();
    expect(screen.getByRole('alertdialog', { name: 'Desk isn’t running' })).toBeTruthy();
    expect(screen.getByText('deskd not running')).toBeTruthy();
    bridge.emit('desk:navigate', '#/system');
    await waitFor(() => expect(screen.getByText('System is not in the web UI yet')).toBeTruthy());
    expect(window.location.hash).toBe('#/system');
  });

  it('seeds the global state from broker.snapshot', async () => {
    go('#/map');
    await renderApp(new FakeDeskBridge({ 'broker.snapshot': () => ({ ...initialGlobalState(), attention: [item('a'), item('b')] }) }));
    expect(await screen.findByRole('link', { name: '2 need you' })).toBeTruthy();
  });

  it('sends the desktop-only tray route to the map', async () => {
    go('#/tray');
    await renderApp();
    await waitFor(() => expect(window.location.hash).toBe('#/map'));
    expect(await screen.findByText('The map is not in the web UI yet')).toBeTruthy();
  });
});
