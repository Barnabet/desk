import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { ProjectOverview } from '@desk/client';
import { ev } from '@desk/client/testing';
import type { StoredEvent } from '@desk/protocol';
import { activeEntries, chainOf, memoryFromEvents } from '@desk/ui-core';
import { SESSION_RELEASE_DELAY } from '../core/session.service';
import { screenFor } from '../screen-for';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../testing/fake-bridge';
import { MemoryScreen } from './memory-screen';

beforeEach(() => {
  window.location.hash = '#/p/p/memory';
});

const overview = () =>
  ({ project: { id: 'p', name: 'P', goal: '', instructions: '', settings: {}, created_at: 't', updated_at: 't', archived_at: null }, desk: null, sources: [], plan: null, threads: [], approvals: [], last_seq: 0 }) as unknown as ProjectOverview;

const events: StoredEvent[] = [
  ev(1, 'agent.created', { role: 'thread', model: 'm', title: 'Pricing research', brief: 'b', workspace_path: '/w', parent_id: 'd' }, { agent: 't' }),
  ev(2, 'memory.written', { memory_id: 'm1', kind: 'decision', content: 'Launch on 10 October', source: 'user' }),
  ev(3, 'memory.written', { memory_id: 'm2', kind: 'decision', content: 'Launch on 17 October', source: 'agent:t', supersedes: 'm1' }, { agent: 't' }),
  ev(4, 'memory.written', { memory_id: 'm3', kind: 'fact', content: 'Churn is 4% monthly', source: 'agent:t' }, { agent: 't' }),
  ev(5, 'memory.written', { memory_id: 'm4', kind: 'note', content: 'Scratch', source: 'user' }),
  ev(6, 'memory.deleted', { memory_id: 'm4' }),
];

/** Renders project p's memory (with the route's search `q` when given) on a bridge whose watch backfills `events`. */
async function setup(extra: FakeHandlers = {}, q?: string) {
  const bridge: FakeDeskBridge = new FakeDeskBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    ...extra,
  });
  await render(MemoryScreen, {
    inputs: { projectId: 'p', ...(q ? { q } : {}) },
    providers: [...bridge.providers, provideGlobal({ ...initialGlobalState(), connection: { status: 'live' } }), { provide: SESSION_RELEASE_DELAY, useValue: 0 }],
  });
  return bridge;
}

describe('memory fold', () => {
  it('tracks supersession and deletion', () => {
    const all = memoryFromEvents(events);
    expect(activeEntries(all).map((m) => m.id)).toEqual(['m3', 'm2']);
    expect(chainOf(all, 'm2').map((m) => m.content)).toEqual(['Launch on 10 October']);
  });
});

describe('MemoryScreen', () => {
  it('groups entries by kind with sources and the correction chain', async () => {
    await setup();
    const decisions = await screen.findByRole('region', { name: /Decisions/ });
    const entry = within(decisions).getByRole('listitem', { name: /Launch on 17 October/ });
    expect(within(entry).getByRole('link', { name: 'Pricing research' }).getAttribute('href')).toBe('#/p/p/threads/t');
    fireEvent.click(within(entry).getByRole('button', { name: 'Corrected 1 time' }));
    expect(within(entry).getByRole('list', { name: 'Earlier versions' }).textContent).toContain('Launch on 10 October');
    expect(screen.getByRole('region', { name: /Facts/ })).toBeTruthy();
    expect(screen.queryByText('Scratch')).toBeNull();
  });

  it('adds, corrects, deletes and searches', async () => {
    const bridge = await setup({
      'memory.add': () => ({}),
      'memory.correct': () => ({}),
      'memory.remove': () => ({ ok: true }),
      'memory.list': () => [{ id: 'm3' }],
    });
    await screen.findByRole('region', { name: /Facts/ });
    fireEvent.input(screen.getByLabelText('Remember something'), { target: { value: 'Prefer email over calls' } });
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'preference' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'memory.add')?.input).toEqual({ projectId: 'p', entry: { kind: 'preference', content: 'Prefer email over calls' } }));

    const fact = screen.getByRole('listitem', { name: /Churn is 4%/ });
    fireEvent.click(within(fact).getByRole('button', { name: 'Correct' }));
    fireEvent.input(within(fact).getByLabelText('Correct this entry'), { target: { value: 'Churn is 3.5% monthly' } });
    fireEvent.click(within(fact).getByRole('button', { name: 'Save correction' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'memory.correct')?.input).toEqual({ projectId: 'p', memoryId: 'm3', update: { content: 'Churn is 3.5% monthly' } }));

    fireEvent.click(within(screen.getByRole('listitem', { name: /Launch on 17/ })).getByRole('button', { name: 'Delete' }));
    const confirm = await screen.findByRole('dialog', { name: 'Delete this memory?' });
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'memory.remove')?.input).toEqual({ projectId: 'p', memoryId: 'm2' }));

    fireEvent.input(screen.getByLabelText('Search memory'), { target: { value: 'churn' } });
    const results = await screen.findByRole('list', { name: 'Search results' });
    expect(within(results).getAllByRole('listitem')).toHaveLength(1);
    expect(bridge.calls.find((c) => c.channel === 'memory.list')?.input).toEqual({ projectId: 'p', q: 'churn' });
  });

  it("starts from the route's search, and keeps the route in step with the box", async () => {
    const bridge = await setup({ 'memory.list': () => [{ id: 'm2' }] }, 'launch');
    const box = (await screen.findByLabelText('Search memory')) as HTMLInputElement;
    expect(box.value).toBe('launch');
    const results = await screen.findByRole('list', { name: 'Search results' });
    expect(within(results).getByRole('listitem', { name: /Launch on 17 October/ })).toBeTruthy();
    expect(bridge.calls.find((c) => c.channel === 'memory.list')?.input).toEqual({ projectId: 'p', q: 'launch' });
    fireEvent.input(box, { target: { value: 'churn' } });
    expect(window.location.hash).toBe('#/p/p/memory?q=churn');
    fireEvent.input(box, { target: { value: '  ' } });
    expect(window.location.hash).toBe('#/p/p/memory');
    await waitFor(() => expect(screen.queryByRole('list', { name: 'Search results' })).toBeNull());
    expect(screen.getByRole('region', { name: /Decisions/ })).toBeTruthy();
  });

  it('is what #/p/<id>/memory shows, with the search the route names', () => {
    expect(screenFor({ name: 'project', id: 'p', tab: 'memory', q: 'auth' })).toEqual({ component: MemoryScreen, inputs: { projectId: 'p', q: 'auth' } });
    expect(screenFor({ name: 'project', id: 'p', tab: 'memory' })).toEqual({ component: MemoryScreen, inputs: { projectId: 'p', q: undefined } });
  });
});
