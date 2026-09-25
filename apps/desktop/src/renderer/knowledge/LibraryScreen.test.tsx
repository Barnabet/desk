// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import type { StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { initialGlobalState } from '@desk/bff/contract';
import { libraryFromEvents } from '@desk/ui-core';
import { useRoute } from '../router';
import { globalStore } from '../state/global';
import { resetSessions, setReleaseDelay, startSessionRouting } from '../state/session';
import { installBridge } from '../test/bridge';
import { LibraryScreen } from './LibraryScreen';

afterEach(cleanup);
beforeEach(() => {
  resetSessions();
  setReleaseDelay(0);
  window.location.hash = '#/p/p/library';
  globalStore.set({ ...initialGlobalState(), connection: { status: 'live' } });
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

function Routed() {
  const r = useRoute();
  return <LibraryScreen projectId="p" {...(r.name === 'project' && r.file ? { file: r.file } : {})} />;
}

function setup(extra: Record<string, (input: any) => unknown> = {}) {
  const bridge = installBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    ...extra,
  });
  startSessionRouting();
  render(<Routed />);
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
    setup({ 'library.file': () => new TextEncoder().encode('# Findings\n\n<img src="https://x/y.png">') });
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
    const bridge = setup({ 'library.upload': ({ file }: { file: { name: string } }) => ({ path: `uploads/${file.name}` }), 'library.file': () => new Uint8Array([1, 0, 2]) });
    await screen.findByRole('button', { name: /Competitor onboarding v2/ });
    fireEvent.change(screen.getByTestId('library-input'), { target: { files: [new File(['hi'], 'brief.pdf')] } });
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'library.upload')?.input).toEqual({ projectId: 'p', file: { name: 'brief.pdf', content_base64: 'aGk=' } }));
    await waitFor(() => expect(window.location.hash).toBe('#/p/p/library?file=uploads%2Fbrief.pdf'));
  });
});
