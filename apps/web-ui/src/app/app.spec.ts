import { TestBed } from '@angular/core/testing';
import { render, screen, waitFor } from '@testing-library/angular';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { initialGlobalState, type GlobalState } from '@desk/bff/contract';
import type { AttentionItem } from '@desk/protocol';
import { App } from './app';
import { ErrorBoundaries, provideErrorBoundaries } from './components/error-boundary';
import { FakeDeskBridge } from './testing/fake-bridge';

const item = (id: string): AttentionItem => ({ id, kind: 'approval', project_id: 'p', project_name: 'P', agent_id: null, title: 't', detail: '', created_at: '', ref: {} });
const go = (hash: string) => history.replaceState(null, '', hash);
/** The element the screen boundary renders: the screen's host while healthy. */
const screenHost = () => document.querySelector('main.screen > [deskErrorBoundary] > *');

async function renderApp(bridge = new FakeDeskBridge()) {
  const view = await render(App, { providers: [...bridge.providers, ...provideErrorBoundaries()] });
  return { bridge, view };
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('desk.onboarded', '1');
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  history.replaceState(null, '', window.location.pathname);
});

describe('App', () => {
  it('shows only how to sign in while this browser has no session', async () => {
    const bridge = new FakeDeskBridge();
    bridge.signOut();
    // Not onboarded either, so only the signed-out guard keeps this deep link from being sent to #/onboarding.
    localStorage.removeItem('desk.onboarded');
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

  it('keeps the screen while the route stays on it, and mounts a fresh one for another screen key', async () => {
    go('#/p/p1/threads');
    const { bridge, view } = await renderApp();
    const first = screenHost();
    expect(first).not.toBeNull();
    bridge.emit('desk:navigate', '#/p/p1/threads/t1');
    await view.fixture.whenStable();
    expect(window.location.hash).toBe('#/p/p1/threads/t1');
    expect(screenHost()).toBe(first);
    bridge.emit('desk:navigate', '#/p/p2/threads');
    await view.fixture.whenStable();
    expect(screenHost()).not.toBeNull();
    expect(screenHost()).not.toBe(first);
  });

  it('renders a crashed screen again when the viewer comes back to it', async () => {
    go('#/p/p1/threads');
    const { bridge, view } = await renderApp();
    // What DeskErrorHandler does with the screen's render error, minus its console.error: the screen's boundary takes it.
    TestBed.inject(ErrorBoundaries).report(new Error('boom'));
    await view.fixture.whenStable();
    expect(screen.getByText('This screen hit an error')).toBeTruthy();
    bridge.emit('desk:navigate', '#/p/p2/threads');
    await view.fixture.whenStable();
    expect(screen.queryByText('This screen hit an error')).toBeNull();
    bridge.emit('desk:navigate', '#/p/p1/threads');
    await view.fixture.whenStable();
    expect(screen.queryByText('This screen hit an error')).toBeNull();
    expect(screenHost()).not.toBeNull();
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

  it('shows desk:notify items as browser notifications while the page is in the background', async () => {
    const titles: string[] = [];
    class BrowserNotification {
      static permission = 'granted';
      onclick: (() => void) | null = null;
      constructor(title: string) {
        titles.push(title);
      }
      close(): void {}
    }
    vi.stubGlobal('Notification', BrowserNotification);
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    go('#/map');
    const { bridge } = await renderApp();
    bridge.emit('desk:notify', [{ tag: 'question:1', title: 'Launch: Desk has a question', body: 'Which first?', route: '#/attention?item=question%3A1' }]);
    expect(titles).toEqual(['Launch: Desk has a question']);
  });
});
