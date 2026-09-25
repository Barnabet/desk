// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import type { StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { resetSessions, setReleaseDelay, startSessionRouting } from '../state/session';
import { clearAttachmentCache } from '../components/ImageThumbs';
import { installBridge } from '../test/bridge';
import { ThreadsScreen } from './ThreadsScreen';

afterEach(cleanup);
beforeEach(() => {
  clearAttachmentCache();
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

function setup(events = base, extra: Record<string, (input: any) => unknown> = {}, threadId: string | null = 't', at?: number) {
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
  render(<ThreadsScreen projectId="p" {...(threadId ? { threadId } : {})} {...(at !== undefined ? { at } : {})} />);
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

  it('shows thumbnails of the images a thread looked at, and opens one larger', async () => {
    const png = 'data:image/png;base64,iVBORw0KGgo=';
    const image = { sha256: 'a'.repeat(64), media_type: 'image/png' as const, width: 1240, height: 1754, bytes: 312_000, name: 'renders/page-1.png' };
    const viewed = [
      ...base,
      ev(6, 'tool.call', { run_id: 'r1', tool_call_id: 'c2', name: 'view_image', arguments: '{"paths":["renders/page-1.png"]}' }, t),
      ev(7, 'tool.result', { run_id: 'r1', tool_call_id: 'c2', name: 'view_image', status: 'ok', content: 'renders/page-1.png · 1240x1754 · PNG · 305 KB', images: [image] }, t),
    ];
    const bridge = setup(viewed, { 'attachments.get': () => png });
    const tr = await screen.findByRole('complementary', { name: 'Transcript' });
    const thumb = await within(tr).findByRole('button', { name: 'Open page-1.png' });
    await waitFor(() => expect(within(thumb).getByRole('img', { name: 'page-1.png' }).getAttribute('src')).toBe(png));
    // The thumbnail is scaled in the renderer (here, without a canvas, it is the image itself); opening loads the image.
    const loads = () => bridge.calls.filter((c) => c.channel === 'attachments.get').map((c) => c.input);
    expect(loads()).toEqual([{ sha256: image.sha256 }]);
    fireEvent.click(thumb);
    const dialog = await screen.findByRole('dialog', { name: 'page-1.png' });
    expect(loads()).toEqual([{ sha256: image.sha256 }, { sha256: image.sha256 }]);
    expect(dialog.textContent).toContain('1240×1754');
    expect(within(dialog).getByRole('img', { name: 'page-1.png' }).getAttribute('src')).toBe(png);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    // Every step shows them under the result too.
    fireEvent.click(within(tr).getByRole('button', { name: 'Every step' }));
    expect(await within(tr).findByRole('button', { name: 'Open page-1.png' })).toBeTruthy();
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

  it("titles an answer run's stop from the session's message fold", async () => {
    const f = { agent: 'f' };
    setup([
      ...base,
      ...finished,
      ev(8, 'agent.created', { role: 'thread', model: 'claude-opus-5-5', title: 'Frontend', brief: 'Build the page', workspace_path: '/w/f', parent_id: 'd' }, f),
      ev(9, 'message.agent', { from_agent_id: 'f', from_label: 'thread "Frontend" (f)', kind: 'question', text: 'Which currency?', tracked: true }, t),
      ev(10, 'run.started', { run_id: 'r2', model: 'claude-opus-5-5', answering: 9 }, t),
      ev(11, 'assistant.message', { run_id: 'r2', content: 'EUR.', tool_calls: [] }, t),
      // The answer lands on the asker's stream: only the fold links it to the run.
      ev(12, 'message.agent', { from_agent_id: 't', from_label: 'thread "Welcome emails" (t)', kind: 'answer', text: 'EUR.', reply_to: 9 }, f),
      ev(13, 'run.finished', { run_id: 'r2', reason: 'no_tool_calls' }, t),
    ]);
    expect(await screen.findByRole('button', { name: /^Stop \d+: Answered Frontend/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Stop \d+: Frontend asked/ })).toBeTruthy();
  });

  it("opens an archived thread's link after a reload", async () => {
    const archivedAt = '2026-09-24T11:00:00.000Z';
    // The overview includes archived threads and every event (last_seq 8): reduceProject adds nothing from the backfill.
    setup([...base, ...finished, ev(8, 'agent.archived', {}, { ...t, ts: archivedAt })], {
      'projects.get': () => ({ ...overview(), threads: [agent('t', { status: 'done', brief: 'Draft five emails.', archived_at: archivedAt })], last_seq: 8 }),
    });
    expect(await screen.findByRole('heading', { name: 'Welcome emails' })).toBeTruthy();
    expect(screen.queryByText("This thread isn't here")).toBeNull();
    expect(screen.getByText('Archived')).toBeTruthy();
    expect(screen.getByText('This thread is archived.')).toBeTruthy();
    expect(within(screen.getByRole('complementary', { name: 'Transcript' })).getByText('Draft five emails.')).toBeTruthy();
  });

  it('opens a thread at the stop holding a message (?at=), once', async () => {
    const bridge = setup(
      [
        ...base,
        ev(6, 'message.agent', { from_agent_id: 'x', from_label: 'thread "Frontend" (x)', kind: 'question', text: 'Which subject lines?', tracked: true }, t),
        ev(7, 'tool.call', { run_id: 'r1', tool_call_id: 'c2', name: 'read_file', arguments: '{"path":"subjects.md"}' }, t),
        ev(8, 'tool.result', { run_id: 'r1', tool_call_id: 'c2', name: 'read_file', status: 'ok', content: 'subjects' }, t),
      ],
      {},
      't',
      6,
    );
    const asked = await screen.findByRole('button', { name: /^Stop 3: Frontend asked/ });
    await waitFor(() => expect(asked.getAttribute('aria-pressed')).toBe('true'));
    expect(screen.getByRole('button', { name: /^Stop 4: Used 1 tool/ }).getAttribute('aria-pressed')).toBe('false');
    expect(document.getElementById('tr-stop-3')!.className).toContain('selected');
    // Selected once: the user's own choice holds when the thread moves on.
    fireEvent.click(screen.getByRole('button', { name: /^Stop 2:/ }));
    bridge.emit('desk:event', ev(9, 'tool.call', { run_id: 'r1', tool_call_id: 'c3', name: 'read_file', arguments: '{"path":"footer.md"}' }, t));
    await screen.findByRole('button', { name: /^Stop 4: Using 2 tools/ });
    expect(screen.getByRole('button', { name: /^Stop 2:/ }).getAttribute('aria-pressed')).toBe('true');
  });

  it("keeps a thread's own sends in its Narrative and Every-step views", async () => {
    setup([
      ...base,
      ev(6, 'tool.call', { run_id: 'r1', tool_call_id: 'c2', name: 'message_thread', arguments: '{"thread_id":"Frontend","kind":"question","text":"Which currency?"}' }, t),
      ev(7, 'tool.result', { run_id: 'r1', tool_call_id: 'c2', name: 'message_thread', status: 'ok', content: 'Sent question #9 to "Frontend".' }, t),
    ]);
    const tr = await screen.findByRole('complementary', { name: 'Transcript' });
    expect(tr.querySelector('#tr-stop-2 .toolgroup-names')!.textContent).toBe('read_file · message_thread');
    fireEvent.click(within(tr).getByRole('button', { name: 'Every step' }));
    expect(within(tr).getAllByText('message_thread').length).toBeGreaterThan(0);
  });
});
