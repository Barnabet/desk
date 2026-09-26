import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { initialGlobalState, type GlobalState } from '@desk/bff/contract';
import type { AttentionItem, ProjectSummary } from '@desk/protocol';
import type { Route } from '@desk/ui-core';
import { FakeDeskBridge, provideGlobal } from '../testing/fake-bridge';
import { TitleBar } from './title-bar';

beforeEach(() => localStorage.clear());
afterEach(() => history.replaceState(null, '', window.location.pathname));

const item = (id: string): AttentionItem => ({ id, kind: 'approval', project_id: 'p', project_name: 'P', agent_id: null, title: 't', detail: '', created_at: '', ref: {} });

const renderBar = (route: Route, state: GlobalState) => render(TitleBar, { inputs: { route }, providers: [...new FakeDeskBridge().providers, provideGlobal(state)] });

describe('TitleBar', () => {
  it('shows places, the daemon state and the attention pill', async () => {
    await renderBar(
      { name: 'project', id: 'p1', tab: 'conversation' },
      {
        ...initialGlobalState(),
        connection: { status: 'live' },
        system: { proxy: 'up', notices: [], lastSeq: 0 },
        attention: [item('a'), item('b'), item('c')],
        overview: [{ project: { id: 'p1', name: 'Onboarding revamp', goal: '', updated_at: '' }, desk_status: 'idle', threads: [], latest_report: null, plan_progress: { done: 0, total: 0 }, attention_count: 0 }],
      },
    );
    const nav = screen.getByRole('navigation', { name: 'Places' });
    expect(nav.textContent).toContain('Map');
    expect(nav.textContent).toContain('Onboarding revamp');
    expect(screen.getByRole('link', { name: 'Onboarding revamp' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByText('deskd · proxy up')).toBeTruthy();
    expect(screen.getByRole('link', { name: '3 need you' }).getAttribute('href')).toBe('#/attention');
  });

  it('says when nothing needs you and when deskd is down', async () => {
    await renderBar({ name: 'map' }, { ...initialGlobalState(), connection: { status: 'offline' } });
    expect(screen.getByText('deskd not running')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'All clear' })).toBeTruthy();
  });

  it('switches projects with ⌘P, filtering and the keyboard, and marks unread ones', async () => {
    const summary = (id: string, name: string, updated: string, attention = 0): ProjectSummary => ({
      project: { id, name, goal: `${name} goal`, updated_at: updated },
      desk_status: 'idle',
      threads: [{ id: `${id}t`, title: null, status: 'running', reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: updated, updated_at: updated }],
      latest_report: null,
      plan_progress: { done: 0, total: 0 },
      attention_count: attention,
    });
    localStorage.setItem('desk.seen', JSON.stringify({ a: '2026-09-24T12:00:00.000Z' }));
    await renderBar(
      { name: 'project', id: 'a', tab: 'conversation' },
      { ...initialGlobalState(), overview: [summary('a', 'Alpha', '2026-09-24T10:00:00.000Z'), summary('b', 'Beta', '2026-09-24T11:00:00.000Z', 2)] },
    );
    expect(screen.getByLabelText('Another project has news')).toBeTruthy();
    fireEvent.keyDown(window, { key: 'p', metaKey: true });
    const pop = screen.getByRole('dialog', { name: 'Switch project' });
    const options = within(pop).getAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual([expect.stringContaining('Beta'), expect.stringContaining('Alpha'), '+ New project']);
    expect(within(options[0]!).getByLabelText('unread')).toBeTruthy();
    const box = within(pop).getByRole('combobox', { name: 'Find a project' });
    fireEvent.input(box, { target: { value: 'alp' } });
    expect(within(pop).getAllByRole('option')).toHaveLength(2);
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(window.location.hash).toBe('#/p/a/conversation');
    expect(screen.queryByRole('dialog', { name: 'Switch project' })).toBeNull();
  });

  it('keeps the last project in the bar elsewhere (not selected), and opens the list by hovering', async () => {
    const summary = (id: string, name: string): ProjectSummary => ({ project: { id, name, goal: '', updated_at: '' }, desk_status: 'idle', threads: [], latest_report: null, plan_progress: { done: 0, total: 0 }, attention_count: 0 });
    const view = await renderBar({ name: 'map' }, { ...initialGlobalState(), overview: [summary('a', 'anyfight'), summary('b', 'P1')] });
    // No project yet: "Projects", and hovering it opens the list.
    const trigger = screen.getByRole('button', { name: 'Switch project (⌘P)' });
    expect(trigger.textContent).toContain('Projects');
    fireEvent.mouseEnter(trigger);
    expect(screen.getByRole('dialog', { name: 'Switch project' })).toBeTruthy();
    fireEvent.mouseLeave(trigger.parentElement!);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Switch project' })).toBeNull());

    await view.rerender({ inputs: { route: { name: 'project', id: 'a', tab: 'threads' } } });
    // The effect that remembers the project runs on the next tick (React's rerender flushes effects in act()).
    await view.fixture.whenStable();
    await view.rerender({ inputs: { route: { name: 'skills' } } });
    const name = screen.getByRole('link', { name: 'anyfight' });
    expect(name.getAttribute('href')).toBe('#/p/a/conversation');
    expect(name.getAttribute('aria-current')).toBeNull();
    expect(screen.getByRole('link', { name: 'Skills' }).getAttribute('aria-current')).toBe('page');
    const arrow = screen.getByRole('button', { name: 'Switch project (⌘P)' });
    expect(arrow.textContent).not.toContain('Projects');
    fireEvent.mouseEnter(name);
    expect(screen.queryByRole('dialog', { name: 'Switch project' })).toBeNull();
    fireEvent.mouseEnter(arrow);
    const pop = screen.getByRole('dialog', { name: 'Switch project' });
    fireEvent.mouseEnter(pop);
    fireEvent.click(within(pop).getByRole('option', { name: /P1/ }));
    expect(window.location.hash).toBe('#/p/b/conversation');
  });

  it('keeps a list that ⌘P opened when the pointer leaves, even after hovering had opened it', async () => {
    const view = await renderBar({ name: 'map' }, initialGlobalState());
    const trigger = screen.getByRole('button', { name: 'Switch project (⌘P)' });
    const dialog = () => screen.queryByRole('dialog', { name: 'Switch project' });
    const afterHoverClose = () => new Promise((r) => setTimeout(r, 300)).then(() => view.fixture.whenStable());
    // Hovered open, closed with ⌘P, left, then ⌘P again: the leave arms no close.
    fireEvent.mouseEnter(trigger);
    fireEvent.keyDown(window, { key: 'p', metaKey: true });
    expect(dialog()).toBeNull();
    fireEvent.mouseLeave(trigger.parentElement!);
    fireEvent.keyDown(window, { key: 'p', metaKey: true });
    await afterHoverClose();
    expect(dialog()).not.toBeNull();
    // Hovered open and left (a close is armed), then ⌘P twice before it fires: the reopened list stays.
    fireEvent.keyDown(window, { key: 'p', metaKey: true });
    fireEvent.mouseEnter(trigger);
    fireEvent.mouseLeave(trigger.parentElement!);
    fireEvent.keyDown(window, { key: 'p', metaKey: true });
    fireEvent.keyDown(window, { key: 'p', metaKey: true });
    await afterHoverClose();
    expect(dialog()).not.toBeNull();
  });
});
