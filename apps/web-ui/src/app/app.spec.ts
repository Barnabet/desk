import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { initialGlobalState, type GlobalState } from '@desk/bff/contract';
import { ev } from '@desk/client/testing';
import type { AttentionItem } from '@desk/protocol';
import { App } from './app';
import { PaletteToggle } from './components/command-palette';
import { ErrorBoundaries, provideErrorBoundaries } from './components/error-boundary';
import { LineDiagram } from './conversation/line-diagram';
import { FakeDeskBridge } from './testing/fake-bridge';

const item = (id: string): AttentionItem => ({ id, kind: 'approval', project_id: 'p', project_name: 'P', agent_id: null, title: 't', detail: '', created_at: '', ref: {} });
const go = (hash: string) => history.replaceState(null, '', hash);
/** Presses ⌘K on the window as a browser does (a cancelable keydown) and returns the event, to see whether it was prevented. */
function pressCmdK(): KeyboardEvent {
  const e = new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true, cancelable: true });
  window.dispatchEvent(e);
  return e;
}
/** The element the screen boundary renders: the screen's host while healthy (inside the project frame on conversation and threads). */
const screenHost = () => document.querySelector('main.screen > [deskErrorBoundary] > *, main.screen .project-frame-body > [deskErrorBoundary] > *');
/** A drag event as a browser sends it (jsdom has no DragEvent): `types` holds 'Files' when files are dragged. */
function drag(type: 'dragover' | 'drop', types: string[], files: File[] = []): Event {
  const e = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(e, 'dataTransfer', { value: { types, files, dropEffect: 'copy' } });
  return e;
}

/** App on a bridge; `rethrow: false` lets render errors reach DeskErrorHandler as they do in the browser. */
async function renderApp(bridge = new FakeDeskBridge(), rethrow = true) {
  const view = await render(App, {
    providers: [...bridge.providers, ...provideErrorBoundaries()],
    configureTestBed: (testBed) => testBed.configureTestingModule({ rethrowApplicationErrors: rethrow }),
  });
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
    expect(await screen.findByText('Welcome to Desk')).toBeTruthy();
    expect(screen.queryByRole('navigation', { name: 'Places' })).toBeNull();
    expect(screen.getByRole('status')).toBeTruthy();
  });

  it('shows the shell, the project tabs and the screen for the route', async () => {
    go('#/p/p1/threads');
    const { view } = await renderApp();
    expect(screen.getByRole('navigation', { name: 'Places' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Threads' }).getAttribute('aria-current')).toBe('page');
    // The fake has no session operations, so the screen settles on its failed state (not the loading page).
    expect(await screen.findByRole('heading', { name: "Couldn't load this project" })).toBeTruthy();
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

  it("keeps one project frame across the conversation and threads tabs, and none on the project's other tabs", async () => {
    go('#/p/p1/conversation');
    const { bridge, view } = await renderApp();
    const frame = document.querySelector('main.screen > .project-frame');
    expect(frame).not.toBeNull();
    expect(frame!.querySelector('.project-frame-body > [deskErrorBoundary]')).not.toBeNull();
    bridge.emit('desk:navigate', '#/p/p1/threads/t1');
    await view.fixture.whenStable();
    // The same frame, so its timeline unfolds in place.
    expect(document.querySelector('main.screen > .project-frame')).toBe(frame);
    bridge.emit('desk:navigate', '#/p/p1/library');
    await view.fixture.whenStable();
    expect(document.querySelector('.project-frame')).toBeNull();
    expect(screenHost()).not.toBeNull();
    bridge.emit('desk:navigate', '#/p/p2/conversation');
    await view.fixture.whenStable();
    expect(document.querySelector('main.screen > .project-frame')).not.toBeNull();
  });

  it('renders a crashed screen again when the viewer comes back to it', async () => {
    go('#/p/p1/threads');
    const { bridge, view } = await renderApp();
    // What DeskErrorHandler does with the screen's render error, minus its console.error: the screen's boundary takes it.
    TestBed.inject(ErrorBoundaries).report(new Error('boom'));
    await view.fixture.whenStable();
    expect(screen.getByText('This screen hit an error')).toBeTruthy();
    // The screen's boundary, not the line diagram's (which registers below it, with the frame).
    expect(document.querySelector('.project-frame-body [role="alert"]')).not.toBeNull();
    bridge.emit('desk:navigate', '#/p/p2/threads');
    await view.fixture.whenStable();
    expect(screen.queryByText('This screen hit an error')).toBeNull();
    bridge.emit('desk:navigate', '#/p/p1/threads');
    await view.fixture.whenStable();
    expect(screen.queryByText('This screen hit an error')).toBeNull();
    expect(screenHost()).not.toBeNull();
  });

  it('keeps a line diagram that fails on every render off the whole page, and the screen comes back', async () => {
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    // Each lane's fold delay is read while the diagram renders: a diagram that throws whenever it renders a lane.
    vi.spyOn(LineDiagram.prototype as unknown as { delay(row: number): string }, 'delay').mockImplementation(() => {
      throw new Error('diagram boom');
    });
    go('#/p/p/conversation');
    const desk = { id: 'd', project_id: 'p', role: 'desk', status: 'idle', model: 'm', reasoning_effort: null, title: 'Desk', brief: null, workspace_path: '/w', parent_id: null, inbox_cursor: 0, review_round: 0, acceptance: 'none', accepted_submission_id: null, reviews_submission_id: null, result_summary: null, result_artifacts: null, active_skills: [], git_source_id: null, git_branch: null, git_base: null, git_common_dir: null, automation_run_id: null, automation_step_id: null, archived_at: null, created_at: 't', updated_at: 't' };
    const overview = { project: { id: 'p', name: 'P', goal: '', instructions: '', settings: {}, created_at: 't', updated_at: 't', archived_at: null }, desk, sources: [], plan: null, threads: [], approvals: [], last_seq: 0 };
    const bridge: FakeDeskBridge = new FakeDeskBridge({
      'broker.snapshot': () => ({ ...initialGlobalState(), connection: { status: 'live' } }),
      'projects.get': () => overview,
      'broker.watch': () => {
        bridge.emit('desk:event', ev(1, 'agent.created', { role: 'thread', model: 'm', title: 'Signup checklist', brief: 'b', workspace_path: '/w/t', parent_id: 'd' }, { agent: 't' }));
        return { ok: true };
      },
      'broker.unwatch': () => ({ ok: true }),
    });
    const { view } = await renderApp(bridge, false);
    // vi.waitFor and whenStable, not testing-library's waitFor or find*: those run fixture.detectChanges, whose render
    // errors throw at the caller instead of reaching DeskErrorHandler as the app's own change detection does.
    await vi.waitFor(() => expect(document.querySelector('.project-frame [role="alert"]')).not.toBeNull());
    // The diagram renders again (a new event here, the 15 s "now" tick in the app) and throws again.
    expect(logged).toHaveBeenCalledTimes(1);
    bridge.emit('desk:event', ev(2, 'agent.status_changed', { status: 'running' }, { agent: 't' }));
    await vi.waitFor(() => expect(logged).toHaveBeenCalledTimes(2));
    await view.fixture.whenStable();
    expect(screen.queryByText('Desk hit an error')).toBeNull();
    expect(screen.getByRole('navigation', { name: 'Places' })).toBeTruthy();
    // The first error took the screen's boundary (the later one); the diagram's own holds the next in the diagram's place,
    // and the screen comes back with Try again.
    const frame = document.querySelector('.project-frame')!;
    expect(frame.querySelector(':scope > [role="alert"]')!.textContent).toContain('diagram boom');
    const screenError = frame.querySelector<HTMLElement>('.project-frame-body [role="alert"]')!;
    within(screenError).getByRole('button', { name: 'Try again' }).click();
    await view.fixture.whenStable();
    await vi.waitFor(() => expect(screen.getByLabelText('Message Desk')).toBeTruthy());
    expect(screen.queryByText('Desk hit an error')).toBeNull();
  });

  it('follows desk:global and desk:navigate', async () => {
    go('#/map');
    const { bridge, view } = await renderApp();
    bridge.emit('desk:global', { ...initialGlobalState(), connection: { status: 'offline' } } satisfies GlobalState);
    view.detectChanges();
    expect(screen.getByRole('alertdialog', { name: 'Desk isn’t running' })).toBeTruthy();
    expect(screen.getByText('deskd not running')).toBeTruthy();
    bridge.emit('desk:navigate', '#/system');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'System', level: 1 })).toBeTruthy());
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
    expect(await screen.findByRole('heading', { name: 'Projects', level: 1 })).toBeTruthy();
  });

  it('keeps a file dropped outside a drop zone from replacing the page, and lets other drags be', async () => {
    go('#/map');
    const { view } = await renderApp();
    const over = drag('dragover', ['Files']);
    document.body.dispatchEvent(over);
    expect(over.defaultPrevented).toBe(true);
    expect((over as Event & { dataTransfer: DataTransfer }).dataTransfer.dropEffect).toBe('none');
    const drop = drag('drop', ['Files'], [new File(['x'], 'x.txt')]);
    screen.getByRole('heading', { name: 'Projects', level: 1 }).dispatchEvent(drop);
    expect(drop.defaultPrevented).toBe(true);
    const text = drag('dragover', ['text/plain']);
    document.body.dispatchEvent(text);
    expect(text.defaultPrevented).toBe(false);
    view.fixture.destroy();
    const after = drag('drop', ['Files']);
    document.body.dispatchEvent(after);
    expect(after.defaultPrevented).toBe(false);
  });

  it('still hands files dropped on the Library to it', async () => {
    go('#/p/p/library');
    const overview = { project: { id: 'p', name: 'P', goal: '', instructions: '', settings: {}, created_at: 't', updated_at: 't', archived_at: null }, desk: null, sources: [], plan: null, threads: [], approvals: [], last_seq: 0 };
    const bridge = new FakeDeskBridge({
      'broker.snapshot': () => ({ ...initialGlobalState(), connection: { status: 'live' } }),
      'projects.get': () => overview,
      'broker.watch': () => ({ ok: true }),
      'broker.unwatch': () => ({ ok: true }),
      'library.upload': ({ file }: { file: { name: string } }) => ({ path: file.name }),
      'library.file': () => new Uint8Array([104, 105]),
    });
    await renderApp(bridge);
    await screen.findByRole('heading', { name: 'Library', level: 1 });
    const library = document.querySelector<HTMLElement>('.library')!;
    const over = drag('dragover', ['Files']);
    library.dispatchEvent(over);
    expect(over.defaultPrevented).toBe(true);
    expect((over as Event & { dataTransfer: DataTransfer }).dataTransfer.dropEffect).toBe('copy');
    library.dispatchEvent(drag('drop', ['Files'], [new File(['hi'], 'notes.txt')]));
    await waitFor(() => expect(bridge.calls.filter((c) => c.channel === 'library.upload')).toHaveLength(1));
    await waitFor(() => expect(window.location.hash).toBe('#/p/p/library?file=notes.txt'));
  });

  it("still hands files dropped on the conversation's chat to its composer", async () => {
    go('#/p/p/conversation');
    const overview = { project: { id: 'p', name: 'P', goal: '', instructions: '', settings: {}, created_at: 't', updated_at: 't', archived_at: null }, desk: null, sources: [], plan: null, threads: [], approvals: [], last_seq: 0 };
    const bridge = new FakeDeskBridge({
      'broker.snapshot': () => ({ ...initialGlobalState(), connection: { status: 'live' } }),
      'projects.get': () => overview,
      'broker.watch': () => ({ ok: true }),
      'broker.unwatch': () => ({ ok: true }),
      'library.upload': ({ file }: { file: { name: string } }) => ({ path: `uploads/${file.name}` }),
    });
    await renderApp(bridge);
    const box = (await screen.findByLabelText('Message Desk')) as HTMLTextAreaElement;
    const chat = screen.getByRole('region', { name: 'Conversation with Desk' });
    const over = drag('dragover', ['Files']);
    chat.dispatchEvent(over);
    expect(over.defaultPrevented).toBe(true);
    expect((over as Event & { dataTransfer: DataTransfer }).dataTransfer.dropEffect).toBe('copy');
    const drop = drag('drop', ['Files'], [new File(['hi'], 'notes.txt')]);
    chat.dispatchEvent(drop);
    expect(drop.defaultPrevented).toBe(true);
    await waitFor(() => expect(box.value).toBe('Attached: uploads/notes.txt\n'));
    expect(bridge.calls.filter((c) => c.channel === 'library.upload')).toHaveLength(1);
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

  it('opens the command palette with ⌘K and the project switcher with ⌘P, and adds no palette element while it is closed', async () => {
    go('#/map');
    const { view } = await renderApp();
    const app = (view.fixture.nativeElement as HTMLElement).querySelector('.app')!;
    expect(app.querySelector('.palette-backdrop')).toBeNull();
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    const palette = await screen.findByRole('dialog', { name: 'Search Desk' });
    expect(palette.parentElement?.parentElement).toBe(app);
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Search Desk' })).toBeNull());
    expect(app.querySelector('.palette-backdrop')).toBeNull();
    fireEvent.keyDown(window, { key: 'p', metaKey: true });
    expect(await screen.findByRole('dialog', { name: 'Switch project' })).toBeTruthy();
  });

  it('leaves ⌘K to the browser during onboarding, and opens no palette once onboarding is done', async () => {
    localStorage.removeItem('desk.onboarded');
    go('#/onboarding');
    const { bridge, view } = await renderApp();
    expect(await screen.findByText('Welcome to Desk')).toBeTruthy();
    expect(pressCmdK().defaultPrevented).toBe(false);
    localStorage.setItem('desk.onboarded', '1');
    bridge.emit('desk:navigate', '#/map');
    expect(await screen.findByRole('heading', { name: 'Projects', level: 1 })).toBeTruthy();
    await view.fixture.whenStable();
    expect(screen.queryByRole('dialog', { name: 'Search Desk' })).toBeNull();
    expect(TestBed.inject(PaletteToggle).open()).toBe(false);
  });

  it('leaves ⌘K to the browser while signed out', async () => {
    const bridge = new FakeDeskBridge();
    bridge.signOut();
    go('#/map');
    await renderApp(bridge);
    expect(pressCmdK().defaultPrevented).toBe(false);
    expect(TestBed.inject(PaletteToggle).open()).toBe(false);
  });

  it('closes an open palette when this browser signs out', async () => {
    go('#/map');
    const { bridge, view } = await renderApp();
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    await screen.findByRole('dialog', { name: 'Search Desk' });
    bridge.signOut();
    await view.fixture.whenStable();
    expect(screen.getByRole('heading', { name: 'Open Desk from your terminal' })).toBeTruthy();
    // Closed, not only hidden: signing in again shows no palette nobody asked for.
    expect(TestBed.inject(PaletteToggle).open()).toBe(false);
  });

  it('leaves ⌘K to the browser while the page shows its error fallback, and closes an open palette', async () => {
    go('#/map');
    const { view } = await renderApp();
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    await screen.findByRole('dialog', { name: 'Search Desk' });
    // What DeskErrorHandler does with two render errors: the screen's boundary takes the first, the whole page's the second.
    const boundaries = TestBed.inject(ErrorBoundaries);
    boundaries.report(new Error('screen boom'));
    boundaries.report(new Error('page boom'));
    await view.fixture.whenStable();
    expect(screen.getByText('Desk hit an error')).toBeTruthy();
    expect(TestBed.inject(PaletteToggle).open()).toBe(false);
    expect(pressCmdK().defaultPrevented).toBe(false);
    expect(TestBed.inject(PaletteToggle).open()).toBe(false);
  });
});
