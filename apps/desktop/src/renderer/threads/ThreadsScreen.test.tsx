// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import type { StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { resetSessions, setReleaseDelay, startSessionRouting } from '../state/session';
import { installBridge } from '../test/bridge';
import { ThreadsScreen } from './ThreadsScreen';

afterEach(cleanup);
beforeEach(() => {
  resetSessions();
  setReleaseDelay(0);
  localStorage.clear();
  globalStore.set({ ...initialGlobalState(), connection: { status: 'live' } });
});

const agent = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  project_id: 'p',
  role: id === 'd' ? 'desk' : 'thread',
  status: 'idle',
  model: 'claude-opus-5-5',
  title: id === 'd' ? 'Desk' : 'Welcome emails',
  brief: null,
  workspace_path: '/w',
  parent_id: id === 'd' ? null : 'd',
  inbox_cursor: 0,
  review_round: 0,
  result_summary: null,
  result_artifacts: null,
  active_skills: [],
  git_source_id: null,
  git_branch: null,
  git_base: null,
  git_common_dir: null,
  archived_at: null,
  created_at: '2026-09-24T10:18:00.000Z',
  updated_at: 't',
  ...extra,
});

const overview = (): ProjectOverview =>
  ({
    project: { id: 'p', name: 'Onboarding', goal: 'g', instructions: '', settings: { desk_model: 'm', thread_model: 'm', fallback_model: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, policy: [] }, created_at: 't', updated_at: 't', archived_at: null },
    desk: agent('d'),
    sources: [],
    plan: null,
    threads: [],
    approvals: [],
    last_seq: 0,
  }) as unknown as ProjectOverview;

const t = { agent: 't' };
const base: StoredEvent[] = [
  ev(1, 'agent.created', { role: 'thread', model: 'claude-opus-5-5', title: 'Welcome emails', brief: 'Draft five emails.', workspace_path: '/w/t', parent_id: 'd', skills: ['brand-voice'] }, { ...t, ts: '2026-09-24T10:18:00.000Z' }),
  ev(2, 'agent.status_changed', { status: 'running' }, t),
  ev(3, 'tool.call', { run_id: 'r1', tool_call_id: 'c1', name: 'read_file', arguments: '{"path":"brief.md"}' }, t),
  ev(4, 'tool.result', { run_id: 'r1', tool_call_id: 'c1', name: 'read_file', status: 'ok', content: 'the brief' }, t),
  ev(5, 'usage', { run_id: 'r1', model: 'claude-opus-5-5', prompt_tokens: 1200, completion_tokens: 300, estimated: false }, t),
];
const finished: StoredEvent[] = [
  ev(6, 'agent.result', { summary: 'Five drafts published', artifacts: ['emails/01.md'], skill_drafts: ['drafts/email-sequence'] }, t),
  ev(7, 'agent.status_changed', { status: 'done' }, t),
];

function setup(events = base, extra: Record<string, (input: any) => unknown> = {}, threadId: string | null = 't') {
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
  render(<ThreadsScreen projectId="p" {...(threadId ? { threadId } : {})} />);
  return bridge;
}

describe('ThreadsScreen', () => {
  it('lists threads as cards', async () => {
    setup([...base, ev(6, 'tool.call', { run_id: 'r1', tool_call_id: 'c2', name: 'bash', arguments: '{"command":"wc -w emails/*.md"}' }, t)], {}, null);
    await screen.findByRole('heading', { name: 'Threads' });
    const card = await screen.findByRole('link', { name: /Welcome emails/ });
    expect(card.getAttribute('href')).toBe('#/p/p/threads/t');
    expect(card.textContent).toContain('bash');
    expect(card.textContent).toContain('brand-voice');
    expect(card.textContent).toContain('Running');
  });

  it('shows the route, the numbered transcript and steers', async () => {
    const bridge = setup(base, { 'threads.send': () => ({ ok: true }) });
    await screen.findByRole('heading', { name: 'Welcome emails', level: 1 });
    expect(screen.getByRole('button', { name: /^Stop 1: Brief from Desk/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Stop 2: Used 1 tool/ })).toBeTruthy();
    const tr = screen.getByRole('complementary', { name: 'Transcript' });
    expect(within(tr).getByLabelText('Stop 1')).toBeTruthy();
    fireEvent.click(within(tr).getByRole('button', { name: 'Every step' }));
    expect(within(tr).getByText('the brief')).toBeTruthy();
    fireEvent.change(within(tr).getByLabelText('Steer this thread'), { target: { value: 'Keep it short' } });
    fireEvent.click(within(tr).getByRole('button', { name: 'Steer' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'threads.send')?.input).toEqual({ id: 't', text: 'Keep it short' }));
    expect(await within(tr).findByText('You · steering…')).toBeTruthy();
    bridge.emit('desk:event', ev(8, 'message.user', { text: 'Keep it short' }, t));
    await waitFor(() => expect(within(tr).queryByText('You · steering…')).toBeNull());
  });

  it('stops a running thread after confirming', async () => {
    const bridge = setup(base, { 'threads.stop': () => ({ ok: true }) });
    fireEvent.click(await screen.findByRole('button', { name: 'Stop' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Stop thread' }));
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'threads.stop')).toBe(true));
  });

  it('archives a finished thread, asks Desk about drafts, and shows usage', async () => {
    const bridge = setup([...base, ...finished], { 'threads.archive': () => ({ ok: true }), 'projects.send': () => ({ ok: true }) });
    fireEvent.click(await screen.findByRole('button', { name: 'Archive' }));
    expect(screen.getByText(/Files it published stay in the Library/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Archive' }).at(-1)!);
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'threads.archive')).toBe(true));

    fireEvent.click(screen.getByRole('tab', { name: 'Result' }));
    expect(within(screen.getByRole('tabpanel')).getByRole('link', { name: 'emails/01.md' }).getAttribute('href')).toBe('#/p/p/library?file=emails%2F01.md');
    fireEvent.click(screen.getByRole('tab', { name: /Skill drafts/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Ask Desk to review and install' }));
    await waitFor(() => expect((bridge.calls.find((c) => c.channel === 'projects.send')?.input as { text: string }).text).toContain('drafts/email-sequence'));
    fireEvent.click(screen.getByRole('tab', { name: 'Usage' }));
    expect(screen.getByRole('table').textContent).toContain('1,200');
    fireEvent.click(screen.getByRole('button', { name: 'Turn into a skill' }));
    await waitFor(() => expect(bridge.calls.filter((c) => c.channel === 'projects.send')).toHaveLength(2));
  });

  it('explains scratch threads on Diff and browses Files', async () => {
    setup(base, {
      'threads.diff': () => {
        throw { code: 'conflict', message: 'no worktree', status: 409 };
      },
      'threads.files': ({ path }: { path?: string }) => (path ? [{ name: 'a.md', path: 'emails/a.md', type: 'file', size: 5 }] : [{ name: 'emails', path: 'emails', type: 'dir', size: 0 }]),
      'threads.file': () => new TextEncoder().encode('# Hi'),
    });
    fireEvent.click(await screen.findByRole('tab', { name: 'Diff' }));
    expect(await screen.findByText('No diff for this thread')).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: 'Files' }));
    fireEvent.click(await screen.findByRole('button', { name: /emails/ }));
    fireEvent.click(await screen.findByRole('button', { name: /a\.md/ }));
    expect(await screen.findByRole('heading', { name: 'Hi' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save a copy…' })).toBeTruthy();
  });
});
