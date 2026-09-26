import { afterEveryRender, Component, computed, inject, Injector } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { ProjectOverview } from '@desk/client';
import { ev } from '@desk/client/testing';
import type { StoredEvent } from '@desk/protocol';
import { libraryFromEvents, MAX_UPLOAD } from '@desk/ui-core';
import { ToastService } from '../components/toast';
import { RouteService } from '../core/route.service';
import { SESSION_RELEASE_DELAY } from '../core/session.service';
import { screenFor } from '../screen-for';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../testing/fake-bridge';
import { LibraryScreen } from './library-screen';

beforeEach(() => {
  window.location.hash = '#/p/p/library';
});

const overview = () =>
  ({
    project: { id: 'p', name: 'P', goal: '', instructions: '', settings: {}, created_at: 't', updated_at: 't', archived_at: null },
    desk: null,
    sources: [],
    plan: null,
    threads: [],
    approvals: [],
    last_seq: 0,
  }) as unknown as ProjectOverview;

const events: StoredEvent[] = [
  ev(1, 'agent.created', { role: 'thread', model: 'm', title: 'Research', brief: 'b', workspace_path: '/w', parent_id: 'd' }, { agent: 't' }),
  ev(2, 'artifact.published', { artifact_id: 'a1', path: 'competitors.md', title: 'Competitor onboarding', kind: 'report', origin: 'agent:t', description: 'Three competitors' }, { agent: 't' }),
  ev(3, 'artifact.published', { artifact_id: 'a2', path: 'uploads/notes.txt', title: 'notes.txt', kind: 'file', origin: 'user', description: '' }),
  ev(4, 'artifact.published', { artifact_id: 'a3', path: 'competitors.md', title: 'Competitor onboarding v2', kind: 'report', origin: 'agent:t', description: '' }, { agent: 't' }),
];

/** The React test's Routed: project p's library with the file the route names, as App passes it; another route unmounts it. */
@Component({
  selector: 'desk-routed-library',
  imports: [LibraryScreen],
  template: `@if (onLibrary()) {<div deskLibraryScreen [projectId]="'p'" [file]="file()"></div>}`,
})
class Routed {
  private readonly route = inject(RouteService).route;
  protected readonly onLibrary = computed(() => {
    const r = this.route();
    return r.name === 'project' && r.tab === 'library';
  });
  protected readonly file = computed(() => {
    const r = this.route();
    return r.name === 'project' ? r.file : undefined;
  });
}

/** A drag event as a browser sends it (jsdom has no DragEvent), dispatched plainly: fireEvent would run change detection itself. */
function drag(target: Element, type: 'dragover' | 'dragleave' | 'drop', init: { files?: File[]; relatedTarget?: Element | null } = {}): void {
  const e = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(e, 'dataTransfer', { value: { types: ['Files'], files: init.files ?? [], dropEffect: 'copy' } });
  Object.defineProperty(e, 'relatedTarget', { value: init.relatedTarget ?? null });
  target.dispatchEvent(e);
}

const toasts = () => TestBed.inject(ToastService).list().map((t) => t.message);
const uploads = (bridge: FakeDeskBridge) => bridge.calls.filter((c) => c.channel === 'library.upload').map((c) => (c.input as { file: { name: string } }).file.name);

async function setup(extra: FakeHandlers = {}) {
  const bridge: FakeDeskBridge = new FakeDeskBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    ...extra,
  });
  const view = await render(Routed, {
    providers: [...bridge.providers, provideGlobal({ ...initialGlobalState(), connection: { status: 'live' } }), { provide: SESSION_RELEASE_DELAY, useValue: 0 }],
  });
  return Object.assign(bridge, { fixture: view.fixture });
}

describe('libraryFromEvents', () => {
  it('keeps the latest publication of each path, newest first', () => {
    expect(libraryFromEvents(events).map((i) => [i.path, i.title])).toEqual([
      ['competitors.md', 'Competitor onboarding v2'],
      ['uploads/notes.txt', 'notes.txt'],
    ]);
  });
});

describe('LibraryScreen', () => {
  it('lists files with their origin, filters by kind, and previews Markdown safely', async () => {
    await setup({ 'library.file': () => new TextEncoder().encode('# Findings\n\n<img src="https://x/y.png">') });
    const card = await screen.findByRole('button', { name: /Competitor onboarding v2/ });
    expect(card.textContent).toContain('Research');
    fireEvent.click(screen.getByRole('button', { name: 'File' }));
    expect(screen.queryByRole('button', { name: /Competitor onboarding v2/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'All' }));
    fireEvent.click(screen.getByRole('button', { name: /Competitor onboarding v2/ }));
    await waitFor(() => expect(window.location.hash).toBe('#/p/p/library?file=competitors.md'));
    const preview = await screen.findByRole('complementary', { name: 'Preview' });
    expect(await within(preview).findByRole('heading', { name: 'Findings' })).toBeTruthy();
    expect(preview.querySelector('img')).toBeNull();
    expect(within(preview).getByRole('link', { name: 'Research' }).getAttribute('href')).toBe('#/p/p/threads/t');
  });

  it('uploads through the picker and opens the new file', async () => {
    const bridge = await setup({ 'library.upload': ({ file }: { file: { name: string } }) => ({ path: `uploads/${file.name}` }), 'library.file': () => new Uint8Array([1, 0, 2]) });
    await screen.findByRole('button', { name: /Competitor onboarding v2/ });
    fireEvent.change(screen.getByTestId('library-input'), { target: { files: [new File(['hi'], 'brief.pdf')] } });
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'library.upload')?.input).toEqual({ projectId: 'p', file: { name: 'brief.pdf', content_base64: 'aGk=' } }));
    await waitFor(() => expect(window.location.hash).toBe('#/p/p/library?file=uploads%2Fbrief.pdf'));
  });

  it('takes files dropped anywhere on it, marks it while dragging, and refuses a file over 25 MB', async () => {
    const bridge = await setup({ 'library.upload': ({ file }: { file: { name: string } }) => ({ path: file.name }) });
    const card = await screen.findByRole('button', { name: /Competitor onboarding v2/ });
    const page = card.closest('.library') as HTMLElement;
    expect(screen.getByText('Drop files anywhere to upload them.')).toBeTruthy();
    fireEvent.dragOver(page, { dataTransfer: { files: [] } });
    expect(page.classList.contains('dragging')).toBe(true);
    expect(screen.getByText('Drop to upload')).toBeTruthy();
    const big = new File(['x'], 'big.bin');
    Object.defineProperty(big, 'size', { value: MAX_UPLOAD + 1 });
    fireEvent.drop(page, { dataTransfer: { files: [big, new File(['a,b'], 'totals.csv')] } });
    expect(page.classList.contains('dragging')).toBe(false);
    await waitFor(() => expect(toasts()).toEqual(['big.bin is larger than 25 MB.', 'Uploaded 1 file.']));
    expect(uploads(bridge)).toEqual(['totals.csv']);
    expect(window.location.hash).toBe('#/p/p/library');
  });

  it('counts only the files that uploaded, and says why each other one did not', async () => {
    const bridge = await setup({
      'library.upload': ({ file }: { file: { name: string } }) => {
        if (file.name === 'locked.xlsx') throw { code: 'internal', message: 'locked.xlsx could not be stored' };
        return { path: file.name };
      },
    });
    const page = (await screen.findByRole('button', { name: /Competitor onboarding v2/ })).closest('.library') as HTMLElement;
    const big = new File(['x'], 'big.bin');
    Object.defineProperty(big, 'size', { value: MAX_UPLOAD + 1 });
    fireEvent.drop(page, { dataTransfer: { files: [big, new File(['a'], 'locked.xlsx'), new File(['b'], 'a.csv'), new File(['c'], 'b.csv')] } });
    await waitFor(() => expect(toasts()).toEqual(['big.bin is larger than 25 MB.', 'locked.xlsx could not be stored', 'Uploaded 2 files.']));
    expect(uploads(bridge)).toEqual(['locked.xlsx', 'a.csv', 'b.csv']);
  });

  it('renders once when a drag starts, not on every dragover', async () => {
    const bridge = await setup();
    const page = (await screen.findByRole('button', { name: /Competitor onboarding v2/ })).closest('.library') as HTMLElement;
    let renders = 0;
    afterEveryRender(() => renders++, { injector: TestBed.inject(Injector) });
    await bridge.fixture.whenStable(); // adding a render hook schedules a render itself: let it run before counting
    renders = 0;
    for (let i = 0; i < 5; i++) {
      drag(page, 'dragover');
      await bridge.fixture.whenStable(); // a render this dragover scheduled has run by now
    }
    expect(page.classList.contains('dragging')).toBe(true);
    expect(renders).toBe(1);
  });

  it('lets go of the drag when it leaves the window, from over a card or from the page itself', async () => {
    const { fixture } = await setup();
    const card = await screen.findByRole('button', { name: /Competitor onboarding v2/ });
    const page = card.closest('.library') as HTMLElement;
    fireEvent.dragOver(page, { dataTransfer: { files: [] } });
    // Into one of its own children: still over the Library.
    drag(page, 'dragleave', { relatedTarget: card });
    await fixture.whenStable();
    expect(page.classList.contains('dragging')).toBe(true);
    expect(screen.getByText('Drop to upload')).toBeTruthy();
    // Off the window from over a card: nothing to enter, so no related target.
    drag(card, 'dragleave');
    await fixture.whenStable();
    expect(page.classList.contains('dragging')).toBe(false);
    expect(screen.getByText('Drop files anywhere to upload them.')).toBeTruthy();
    fireEvent.dragOver(page, { dataTransfer: { files: [] } });
    expect(page.classList.contains('dragging')).toBe(true);
    // Onto something outside it (the project tabs, say).
    drag(page, 'dragleave', { relatedTarget: document.body });
    await fixture.whenStable();
    expect(page.classList.contains('dragging')).toBe(false);
  });

  it('says why an upload failed and lets the button go', async () => {
    await setup({
      'library.upload': () => {
        throw { code: 'internal', message: 'disk full' };
      },
    });
    await screen.findByRole('button', { name: /Competitor onboarding v2/ });
    fireEvent.change(screen.getByTestId('library-input'), { target: { files: [new File(['hi'], 'brief.pdf')] } });
    await waitFor(() => expect(toasts()).toEqual(['disk full']));
    const button = screen.getByRole('button', { name: 'Upload…' }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    expect(button.getAttribute('aria-busy')).toBeNull();
    expect(window.location.hash).toBe('#/p/p/library');
  });

  it('stays where the viewer went when an upload finishes after they left the Library', async () => {
    let finish!: (v: { path: string }) => void;
    const bridge = await setup({ 'library.upload': () => new Promise((resolve) => (finish = resolve)) });
    await screen.findByRole('button', { name: /Competitor onboarding v2/ });
    fireEvent.change(screen.getByTestId('library-input'), { target: { files: [new File(['hi'], 'brief.pdf')] } });
    await waitFor(() => expect(uploads(bridge)).toEqual(['brief.pdf']));
    TestBed.inject(RouteService).navigate('#/p/p/memory');
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Library' })).toBeNull());
    finish({ path: 'uploads/brief.pdf' });
    await new Promise((r) => setTimeout(r, 20));
    expect(window.location.hash).toBe('#/p/p/memory');
  });

  it("says so when a file can't be opened", async () => {
    window.location.hash = '#/p/p/library?file=competitors.md';
    await setup({
      'library.file': () => {
        throw { code: 'not_found', message: 'competitors.md is gone' };
      },
    });
    const preview = await screen.findByRole('complementary', { name: 'Preview' });
    expect(await within(preview).findByText("Couldn't open this file")).toBeTruthy();
    expect(within(preview).getByText('competitors.md is gone')).toBeTruthy();
  });

  it('selects and closes the preview in place, adding no history entries', async () => {
    window.location.hash = '#/p/p/library?file=uploads%2Fnotes.txt';
    await setup({ 'library.file': () => new TextEncoder().encode('plain notes') });
    const preview = await screen.findByRole('complementary', { name: 'Preview' });
    await within(preview).findByRole('button', { name: 'Close preview' });
    const entries = history.length;
    fireEvent.click(screen.getByRole('button', { name: /Competitor onboarding v2/ }));
    await waitFor(() => expect(window.location.hash).toBe('#/p/p/library?file=competitors.md'));
    fireEvent.click(await within(preview).findByRole('button', { name: 'Close preview' }));
    await waitFor(() => expect(window.location.hash).toBe('#/p/p/library'));
    expect(screen.queryByRole('complementary', { name: 'Preview' })).toBeNull();
    expect(history.length).toBe(entries);
  });

  it('is what #/p/<id>/library shows, with the file the route names', () => {
    expect(screenFor({ name: 'project', id: 'p', tab: 'library', file: 'competitors.md' })).toEqual({ component: LibraryScreen, inputs: { projectId: 'p', file: 'competitors.md' } });
    expect(screenFor({ name: 'project', id: 'p', tab: 'library' })).toEqual({ component: LibraryScreen, inputs: { projectId: 'p', file: undefined } });
  });
});
