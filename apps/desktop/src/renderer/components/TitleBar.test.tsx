// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { AttentionItem } from '@desk/protocol';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { markSeen, resetSeen } from '../state/unread';
import { installBridge } from '../test/bridge';
import { TitleBar } from './TitleBar';

afterEach(cleanup);
beforeEach(() => installBridge());

const item = (id: string): AttentionItem => ({ id, kind: 'approval', project_id: 'p', project_name: 'P', agent_id: null, title: 't', detail: '', created_at: '', ref: {} });

describe('TitleBar', () => {
  it('shows places, the daemon state and the attention pill', () => {
    globalStore.set({
      ...initialGlobalState(),
      connection: { status: 'live' },
      system: { proxy: 'up', notices: [], lastSeq: 0 },
      attention: [item('a'), item('b'), item('c')],
      overview: [{ project: { id: 'p1', name: 'Onboarding revamp', goal: '', updated_at: '' }, desk_status: 'idle', threads: [], latest_report: null, plan_progress: { done: 0, total: 0 }, attention_count: 0 }],
    });
    render(<TitleBar route={{ name: 'project', id: 'p1', tab: 'conversation' }} />);
    const nav = screen.getByRole('navigation', { name: 'Places' });
    expect(nav.textContent).toContain('Map');
    expect(nav.textContent).toContain('Onboarding revamp');
    expect(screen.getByRole('link', { name: 'Onboarding revamp' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByText('deskd · proxy up')).toBeTruthy();
    expect(screen.getByRole('link', { name: '3 need you' }).getAttribute('href')).toBe('#/attention');
  });

  it('says when nothing needs you and when deskd is down', () => {
    globalStore.set({ ...initialGlobalState(), connection: { status: 'offline' } });
    render(<TitleBar route={{ name: 'map' }} />);
    expect(screen.getByText('deskd not running')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'All clear' })).toBeTruthy();
  });

  it('switches projects with ⌘P, filtering and the keyboard, and marks unread ones', () => {
    resetSeen();
    const summary = (id: string, name: string, updated: string, attention = 0) => ({
      project: { id, name, goal: `${name} goal`, updated_at: updated },
      desk_status: 'idle' as const,
      threads: [{ id: `${id}t`, title: null, status: 'running' as const, reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: updated, updated_at: updated }],
      latest_report: null,
      plan_progress: { done: 0, total: 0 },
      attention_count: attention,
    });
    globalStore.set({ ...initialGlobalState(), overview: [summary('a', 'Alpha', '2026-09-24T10:00:00.000Z'), summary('b', 'Beta', '2026-09-24T11:00:00.000Z', 2)] });
    markSeen('a', '2026-09-24T12:00:00.000Z');
    render(<TitleBar route={{ name: 'project', id: 'a', tab: 'conversation' }} />);
    expect(screen.getByLabelText('Another project has news')).toBeTruthy();
    fireEvent.keyDown(window, { key: 'p', metaKey: true });
    const pop = screen.getByRole('dialog', { name: 'Switch project' });
    const options = within(pop).getAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual([expect.stringContaining('Beta'), expect.stringContaining('Alpha'), '+ New project']);
    expect(within(options[0]!).getByLabelText('unread')).toBeTruthy();
    const box = within(pop).getByRole('combobox', { name: 'Find a project' });
    fireEvent.change(box, { target: { value: 'alp' } });
    expect(within(pop).getAllByRole('option')).toHaveLength(2);
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(window.location.hash).toBe('#/p/a/conversation');
    expect(screen.queryByRole('dialog', { name: 'Switch project' })).toBeNull();
  });
});
