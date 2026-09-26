import { Component, computed, inject } from '@angular/core';
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

/** The React test's Routed: project p's library with the file the route names, as App passes it. */
@Component({
  selector: 'desk-routed-library',
  imports: [LibraryScreen],
  template: `<div deskLibraryScreen [projectId]="'p'" [file]="file()"></div>`,
})
class Routed {
  private readonly route = inject(RouteService).route;
  protected readonly file = computed(() => {
    const r = this.route();
    return r.name === 'project' ? r.file : undefined;
  });
}

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
  await render(Routed, {
    providers: [...bridge.providers, provideGlobal({ ...initialGlobalState(), connection: { status: 'live' } }), { provide: SESSION_RELEASE_DELAY, useValue: 0 }],
  });
  return bridge;
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
    await waitFor(() => expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['big.bin is larger than 25 MB.', 'Uploaded 2 files.']));
    expect(bridge.calls.filter((c) => c.channel === 'library.upload').map((c) => (c.input as { file: { name: string } }).file.name)).toEqual(['totals.csv']);
    expect(window.location.hash).toBe('#/p/p/library');
  });

  it('is what #/p/<id>/library shows, with the file the route names', () => {
    expect(screenFor({ name: 'project', id: 'p', tab: 'library', file: 'competitors.md' })).toEqual({ component: LibraryScreen, inputs: { projectId: 'p', file: 'competitors.md' } });
    expect(screenFor({ name: 'project', id: 'p', tab: 'library' })).toEqual({ component: LibraryScreen, inputs: { projectId: 'p', file: undefined } });
  });
});
