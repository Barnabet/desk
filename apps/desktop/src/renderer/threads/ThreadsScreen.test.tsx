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
/** A time `m` minutes ago, 20 s past the minute so it reads "<m>m" on either side of the shared clock's 15 s step. */
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000 - 20_000).toISOString();
const f = { agent: 'f' };
/** Frontend (f) asks the thread t a tracked question (event `from + 1`), and t starts an answer run for it. */
const answeringFrontend = (from: number): StoredEvent[] => [
  ev(from, 'agent.created', { role: 'thread', model: 'claude-opus-5-5', title: 'Frontend', brief: 'Build the page', workspace_path: '/w/f', parent_id: 'd' }, f),
  ev(from + 1, 'message.agent', { from_agent_id: 'f', from_label: 'thread "Frontend" (f)', kind: 'question', text: 'Which currency?', tracked: true }, t),
  ev(from + 2, 'run.started', { run_id: 'r2', model: 'claude-opus-5-5', answering: from + 1 }, t),
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

  it('shows "answering" on a done thread, which keeps its status, Archive and Skill actions and gets no Stop', async () => {
    setup([...base, ...finished, ...answeringFrontend(8)]);
    const head = (await screen.findByRole('heading', { name: 'Welcome emails', level: 1 })).closest('.thread-head') as HTMLElement;
    expect(within(head).getByText('answering Frontend')).toBeTruthy();
    expect(head.querySelector('.thread-status-line')!.textContent).toMatch(/^Done/);
    expect(screen.getByRole('button', { name: 'Archive' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Turn into a skill' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull();
  });

  it('marks an answering thread on its roster card', async () => {
    setup([...base, ...finished, ...answeringFrontend(8)], {}, null);
    const card = await screen.findByRole('link', { name: /Welcome emails/ });
    expect(within(card).getByText('answering Frontend')).toBeTruthy();
    expect(card.textContent).toContain('Done');
  });

  it('keeps Stop on a waiting thread while it answers, and its dialog says Desk is told', async () => {
    const bridge = setup([...base, ev(6, 'agent.status_changed', { status: 'waiting', reason: 'Waiting on Desk or the user' }, t), ...answeringFrontend(7)], {
      'threads.stop': () => ({ ok: true }),
    });
    await screen.findByText('answering Frontend');
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect(screen.getByRole('dialog', { name: 'Stop this thread?' }).textContent).toContain('Desk is told');
    fireEvent.click(screen.getByRole('button', { name: 'Stop thread' }));
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'threads.stop')).toBe(true));
  });

  it('says what a waiting thread waits on, from the fold rather than the status reason', async () => {
    setup([
      ...base,
      ev(6, 'agent.created', { role: 'thread', model: 'claude-opus-5-5', title: 'Frontend', brief: 'Build the page', workspace_path: '/w/f', parent_id: 'd' }, f),
      ev(7, 'message.agent', { from_agent_id: 't', from_label: 'thread "Welcome emails" (t)', kind: 'question', text: 'Which subject lines?', tracked: true }, { ...f, ts: minutesAgo(4) }),
      ev(8, 'agent.status_changed', { status: 'waiting', reason: 'Waiting on "Frontend"' }, t),
    ]);
    const line = (await screen.findByText('Waiting on Frontend · 4m')).closest('.thread-status-line')!;
    expect(line.textContent).not.toContain('Waiting on "Frontend"');
  });

  it('says a waiting thread waits on you only when it has an attention item', async () => {
    globalStore.set({
      ...initialGlobalState(),
      connection: { status: 'live' },
      attention: [{ id: 'approval:a1', kind: 'approval', project_id: 'p', project_name: 'Onboarding', agent_id: 't', title: 'Welcome emails wants to run bash', detail: '', created_at: minutesAgo(2), ref: { approval_id: 'a1', thread_id: 't' } }],
    });
    setup([...base, ev(6, 'agent.status_changed', { status: 'waiting', reason: 'Waiting for approval' }, t)]);
    expect(await screen.findByText('Waiting on you · 2m')).toBeTruthy();
  });

  it('asks a done thread, or reopens it with a message after confirming', async () => {
    const bridge = setup([...base, ...finished], { 'threads.send': () => ({ ok: true }) });
    const tr = await screen.findByRole('complementary', { name: 'Transcript' });
    const box = await within(tr).findByLabelText('Ask this thread');
    expect(within(tr).queryByRole('button', { name: 'Steer' })).toBeNull();
    expect(within(tr).getByText('It answers from its context; its result stays as it is.')).toBeTruthy();
    fireEvent.change(box, { target: { value: 'How long are the emails?' } });
    fireEvent.click(within(tr).getByRole('button', { name: 'Ask' }));
    await waitFor(() => expect(bridge.calls.filter((c) => c.channel === 'threads.send').map((c) => c.input)).toEqual([{ id: 't', text: 'How long are the emails?', question: true }]));
    expect(await within(tr).findByText('You · asking…')).toBeTruthy();
    bridge.emit('desk:event', ev(8, 'message.user', { text: 'How long are the emails?', question: true }, t));
    await waitFor(() => expect(within(tr).queryByText('You · asking…')).toBeNull());
    expect(screen.getByRole('button', { name: /^Stop \d+: You asked/ })).toBeTruthy();

    fireEvent.change(box, { target: { value: 'Add a sixth email.' } });
    fireEvent.click(within(tr).getByRole('button', { name: 'Reopen with this…' }));
    const dialog = screen.getByRole('dialog', { name: 'Reopen this thread?' });
    expect(dialog.textContent).toContain('Reopening lets it change its work; its result and branch may change.');
    expect(bridge.calls.filter((c) => c.channel === 'threads.send')).toHaveLength(1);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reopen' }));
    await waitFor(() => expect(bridge.calls.filter((c) => c.channel === 'threads.send').at(-1)?.input).toEqual({ id: 't', text: 'Add a sixth email.' }));
    expect(await within(tr).findByText('You · steering…')).toBeTruthy();
  });

  it('resumes an idle thread with a message after confirming', async () => {
    const bridge = setup([...base, ev(6, 'agent.status_changed', { status: 'idle' }, t)], { 'threads.send': () => ({ ok: true }) });
    const tr = await screen.findByRole('complementary', { name: 'Transcript' });
    fireEvent.change(await within(tr).findByLabelText('Ask this thread'), { target: { value: 'Carry on with the footer.' } });
    fireEvent.click(within(tr).getByRole('button', { name: 'Resume with this…' }));
    const dialog = screen.getByRole('dialog', { name: 'Resume this thread?' });
    expect(dialog.textContent).toContain('Resuming lets it change its work; its result and branch may change.');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resume' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'threads.send')?.input).toEqual({ id: 't', text: 'Carry on with the footer.' }));
  });

  it("puts the sender's initials on a message's disc and shows a live answer run with its question and tools", async () => {
    setup([
      ...base,
      ...finished,
      ...answeringFrontend(8),
      ev(11, 'tool.call', { run_id: 'r2', tool_call_id: 'c9', name: 'read_file', arguments: '{"path":"emails/01.md"}' }, t),
    ]);
    const asked = await screen.findByRole('button', { name: /^Stop \d+: Frontend asked/ });
    expect(asked.textContent).toBe('Fr');
    expect(screen.getByRole('button', { name: /^Stop 1: Brief from Desk/ }).textContent).toBe('Brief');
    const run = screen.getByRole('button', { name: /^Stop \d+: Answering Frontend/ });
    expect(run.className).toContain('live');
    expect(run.parentElement!.querySelector('.route-label-title .live-dot')).toBeTruthy();
    // An answer run never gets the "Now" tail or "Next: report to Desk".
    expect(screen.queryByText('Next: report to Desk')).toBeNull();
    const tr = screen.getByRole('complementary', { name: 'Transcript' });
    const entry = within(tr).getByText(/^Answering Frontend/).closest('.tr-entry') as HTMLElement;
    expect(entry.querySelector('.tr-title .live-dot')).toBeTruthy();
    expect(entry.querySelector('.tr-asked')!.textContent).toBe('Frontend asked: “Which currency?”');
    expect(entry.textContent).toContain('read_file');
  });

  it("mutes an answer run that could not answer and shows the runtime's closure", async () => {
    setup([
      ...base,
      ...finished,
      ...answeringFrontend(8),
      // The closure lands on the asker's stream; only the fold links it to the run.
      ev(11, 'message.agent', { from_agent_id: 't', from_label: 'thread "Welcome emails" (t)', kind: 'answer', text: '(Welcome emails was stopped before answering.)', reply_to: 9, auto: true }, f),
      ev(12, 'run.finished', { run_id: 'r2', reason: 'stopped' }, t),
    ]);
    const run = await screen.findByRole('button', { name: /^Stop \d+: Couldn't answer Frontend/ });
    expect(run.className).toContain('muted');
    expect(run.className).not.toContain('live');
    expect(run.parentElement!.querySelector('.route-label-title.muted')).toBeTruthy();
    const tr = screen.getByRole('complementary', { name: 'Transcript' });
    const entry = within(tr).getByText(/^Couldn't answer Frontend/).closest('.tr-entry') as HTMLElement;
    expect(entry.querySelector('.tr-title.muted')).toBeTruthy();
    expect(entry.querySelector('.tr-title .live-dot')).toBeNull();
    expect(entry.textContent).toContain('Frontend asked: “Which currency?”');
    expect(entry.querySelector('.tr-text.muted')!.textContent).toBe('(Welcome emails was stopped before answering.)');
    fireEvent.click(within(tr).getByRole('button', { name: 'Every step' }));
    expect(within(tr).getByText(/^Woke to answer message #9 · /)).toBeTruthy();
  });

  it("follows a waiting thread's wait one hop to what needs you, on its status line", async () => {
    globalStore.set({
      ...initialGlobalState(),
      connection: { status: 'live' },
      attention: [{ id: 'approval:a1', kind: 'approval', project_id: 'p', project_name: 'Onboarding', agent_id: 'f', title: 'Frontend wants to run bash', detail: '', created_at: minutesAgo(1), ref: { approval_id: 'a1', thread_id: 'f' } }],
    });
    setup([
      ...base,
      ev(6, 'agent.created', { role: 'thread', model: 'claude-opus-5-5', title: 'Frontend', brief: 'Build the page', workspace_path: '/w/f', parent_id: 'd' }, f),
      ev(7, 'message.agent', { from_agent_id: 't', from_label: 'thread "Welcome emails" (t)', kind: 'question', text: 'Which subject lines?', tracked: true }, { ...f, ts: minutesAgo(4) }),
      ev(8, 'agent.status_changed', { status: 'waiting', reason: 'Waiting on "Frontend"' }, t),
    ]);
    const line = (await screen.findByText('Waiting on Frontend · 4m')).closest('.thread-status-line') as HTMLElement;
    const hop = within(line).getByRole('link', { name: 'Frontend needs your approval' });
    expect(hop.getAttribute('href')).toBe('#/attention?item=approval%3Aa1');
    expect(hop.textContent).toBe('→ needs your approval');
  });
});
