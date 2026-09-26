import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { ProjectOverview } from '@desk/client';
import { ev } from '@desk/client/testing';
import type { AttentionItem, StoredEvent } from '@desk/protocol';
import { clearAttachmentCache } from '../components/image-thumbs';
import { SESSION_RELEASE_DELAY } from '../core/session.service';
import { screenFor } from '../screen-for';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../testing/fake-bridge';
import { ThreadsScreen } from './threads-screen';

/** The attention items in global state for the next setup (the React test's globalStore.set). */
let attention: AttentionItem[] = [];
/** The model proxy's state in global state for the next setup. */
let proxy: 'up' | 'down' | 'unknown' = 'unknown';

beforeEach(() => {
  clearAttachmentCache();
  localStorage.clear();
  attention = [];
  proxy = 'unknown';
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

/** Renders project p's Threads screen (thread t unless `threadId` is null) with a bridge whose watch backfills `events`. */
async function setup(events: StoredEvent[] = base, extra: FakeHandlers = {}, threadId: string | null = 't', at?: number) {
  const bridge: FakeDeskBridge = new FakeDeskBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    ...extra,
  });
  await render(ThreadsScreen, {
    inputs: { projectId: 'p', ...(threadId ? { threadId } : {}), ...(at !== undefined ? { at } : {}) },
    providers: [...bridge.providers, provideGlobal({ ...initialGlobalState(), connection: { status: 'live' }, attention, system: { ...initialGlobalState().system, proxy } }), { provide: SESSION_RELEASE_DELAY, useValue: 0 }],
  });
  return bridge;
}

describe('ThreadsScreen', () => {
  it('lists threads as cards', async () => {
    await setup([...base, ev(6, 'tool.call', { run_id: 'r1', tool_call_id: 'c2', name: 'bash', arguments: '{"command":"wc -w emails/*.md"}' }, t)], {}, null);
    await screen.findByRole('heading', { name: 'Threads' });
    const card = await screen.findByRole('link', { name: /Welcome emails/ });
    expect(card.getAttribute('href')).toBe('#/p/p/threads/t');
    expect(card.textContent).toContain('bash');
    expect(card.textContent).toContain('brand-voice');
    expect(card.textContent).toContain('Running');
  });

  it('shows the route, the numbered transcript and steers', async () => {
    const bridge = await setup(base, { 'threads.send': () => ({ ok: true }) });
    await screen.findByRole('heading', { name: 'Welcome emails', level: 1 });
    expect(screen.getByRole('button', { name: /^Stop 1: Brief from Desk/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Stop 2: Used 1 tool/ })).toBeTruthy();
    const tr = screen.getByRole('complementary', { name: 'Transcript' });
    expect(within(tr).getByLabelText('Stop 1')).toBeTruthy();
    fireEvent.click(within(tr).getByRole('button', { name: 'Every step' }));
    expect(await within(tr).findByText('the brief')).toBeTruthy();
    fireEvent.input(within(tr).getByLabelText('Steer this thread'), { target: { value: 'Keep it short' } });
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
    const bridge = await setup(viewed, { 'attachments.get': () => png });
    const tr = await screen.findByRole('complementary', { name: 'Transcript' });
    const thumb = await within(tr).findByRole('button', { name: 'Open page-1.png' });
    await waitFor(() => expect(within(thumb).getByRole('img', { name: 'page-1.png' }).getAttribute('src')).toBe(png));
    // The thumbnail is scaled in the browser (here, without a canvas, it is the image itself); opening loads the image.
    const loads = () => bridge.calls.filter((c) => c.channel === 'attachments.get').map((c) => c.input);
    expect(loads()).toEqual([{ sha256: image.sha256 }]);
    fireEvent.click(thumb);
    const dialog = await screen.findByRole('dialog', { name: 'page-1.png' });
    await waitFor(() => expect(loads()).toEqual([{ sha256: image.sha256 }, { sha256: image.sha256 }]));
    expect(dialog.textContent).toContain('1240×1754');
    await waitFor(() => expect(within(dialog).getByRole('img', { name: 'page-1.png' }).getAttribute('src')).toBe(png));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    // Every step shows them under the result too.
    fireEvent.click(within(tr).getByRole('button', { name: 'Every step' }));
    expect(await within(tr).findByRole('button', { name: 'Open page-1.png' })).toBeTruthy();
  });

  it('stops a running thread after confirming', async () => {
    const bridge = await setup(base, { 'threads.stop': () => ({ ok: true }) });
    fireEvent.click(await screen.findByRole('button', { name: 'Stop' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Stop thread' }));
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'threads.stop')).toBe(true));
  });

  it('archives a finished thread, asks Desk about drafts, and shows usage', async () => {
    const bridge = await setup([...base, ...finished], { 'threads.archive': () => ({ ok: true }), 'projects.send': () => ({ ok: true }) });
    fireEvent.click(await screen.findByRole('button', { name: 'Archive' }));
    expect(await screen.findByText(/Files it published stay in the Library/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Archive' }).at(-1)!);
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'threads.archive')).toBe(true));

    fireEvent.click(screen.getByRole('tab', { name: 'Result' }));
    expect((await within(screen.getByRole('tabpanel')).findByRole('link', { name: 'emails/01.md' })).getAttribute('href')).toBe('#/p/p/library?file=emails%2F01.md');
    fireEvent.click(screen.getByRole('tab', { name: /Skill drafts/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Ask Desk to review and install' }));
    await waitFor(() => expect((bridge.calls.find((c) => c.channel === 'projects.send')?.input as { text: string }).text).toContain('drafts/email-sequence'));
    fireEvent.click(screen.getByRole('tab', { name: 'Usage' }));
    expect((await screen.findByRole('table')).textContent).toContain('1,200');
    fireEvent.click(screen.getByRole('button', { name: 'Turn into a skill' }));
    await waitFor(() => expect(bridge.calls.filter((c) => c.channel === 'projects.send')).toHaveLength(2));
  });

  it('explains scratch threads on Diff and browses Files', async () => {
    await setup(base, {
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
    await setup([
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
    // Frontend's question is a card in the stop before the run.
    expect(screen.getByRole('button', { name: /^Stop 4: Messages, .*, 1 message$/ })).toBeTruthy();
  });

  it("opens an archived thread's link after a reload", async () => {
    const archivedAt = '2026-09-24T11:00:00.000Z';
    // The overview includes archived threads and every event (last_seq 8): reduceProject adds nothing from the backfill.
    await setup([...base, ...finished, ev(8, 'agent.archived', {}, { ...t, ts: archivedAt })], {
      'projects.get': () => ({ ...overview(), threads: [agent('t', { status: 'done', brief: 'Draft five emails.', archived_at: archivedAt })], last_seq: 8 }),
    });
    expect(await screen.findByRole('heading', { name: 'Welcome emails' })).toBeTruthy();
    expect(screen.queryByText("This thread isn't here")).toBeNull();
    expect(screen.getByText('Archived')).toBeTruthy();
    expect(screen.getByText('This thread is archived.')).toBeTruthy();
    expect(within(screen.getByRole('complementary', { name: 'Transcript' })).getByText('Draft five emails.')).toBeTruthy();
  });

  it('opens a thread at the stop holding a message (?at=), once', async () => {
    const bridge = await setup(
      [
        ...base,
        ev(6, 'message.agent', { from_agent_id: 'd', from_label: 'Desk', kind: 'note', text: 'Use the EU subject lines.' }, t),
        ev(7, 'tool.call', { run_id: 'r1', tool_call_id: 'c2', name: 'read_file', arguments: '{"path":"subjects.md"}' }, t),
        ev(8, 'tool.result', { run_id: 'r1', tool_call_id: 'c2', name: 'read_file', status: 'ok', content: 'subjects' }, t),
      ],
      {},
      't',
      6,
    );
    const asked = await screen.findByRole('button', { name: /^Stop 3: Desk: note/ });
    await waitFor(() => expect(asked.getAttribute('aria-pressed')).toBe('true'));
    expect(screen.getByRole('button', { name: /^Stop 4: Used 1 tool/ }).getAttribute('aria-pressed')).toBe('false');
    expect(document.getElementById('tr-stop-3')!.className).toContain('selected');
    // Selected once: the user's own choice holds when the thread moves on.
    fireEvent.click(screen.getByRole('button', { name: /^Stop 2:/ }));
    bridge.emit('desk:event', ev(9, 'tool.call', { run_id: 'r1', tool_call_id: 'c3', name: 'read_file', arguments: '{"path":"footer.md"}' }, t));
    await screen.findByRole('button', { name: /^Stop 4: Using 2 tools/ });
    expect(screen.getByRole('button', { name: /^Stop 2:/ }).getAttribute('aria-pressed')).toBe('true');
  });

  it("shows messages with other threads as cards in their stop, in place of the sends' tool rows, in both depths", async () => {
    await setup([
      ...base,
      ev(6, 'agent.created', { role: 'thread', model: 'claude-opus-5-5', title: 'Frontend', brief: 'Build the page', workspace_path: '/w/f', parent_id: 'd' }, f),
      ev(7, 'tool.call', { run_id: 'r1', tool_call_id: 'c2', name: 'message_thread', arguments: '{"thread_id":"Frontend","kind":"question","text":"Which subject lines?"}' }, t),
      // The question is stored on Frontend's stream, with the call that sent it.
      ev(8, 'message.agent', { from_agent_id: 't', from_label: 'thread "Welcome emails" (t)', kind: 'question', text: 'Which subject lines?', tracked: true, tool_call_id: 'c2' }, f),
      ev(9, 'tool.result', { run_id: 'r1', tool_call_id: 'c2', name: 'message_thread', status: 'ok', content: 'Sent question #8 to "Frontend".' }, t),
      ev(10, 'message.agent', { from_agent_id: 'f', from_label: 'thread "Frontend" (f)', kind: 'answer', text: 'The EU ones.', reply_to: 8 }, t),
      // A failed send stored nothing: it stays a tool row.
      ev(11, 'tool.call', { run_id: 'r1', tool_call_id: 'c3', name: 'message_thread', arguments: '{"thread_id":"Nobody","text":"Hi"}' }, t),
      ev(12, 'tool.result', { run_id: 'r1', tool_call_id: 'c3', name: 'message_thread', status: 'error', content: 'Unknown thread: Nobody' }, t),
    ]);
    const stop = await screen.findByRole('button', { name: /^Stop 2: Used 2 tools, .*, 2 messages$/ });
    expect(stop.parentElement!.querySelector('.route-cards')!.textContent).toBe('2 ✉');
    const tr = screen.getByRole('complementary', { name: 'Transcript' });
    const body = tr.querySelector('#tr-stop-2')!;
    const heads = () => [...tr.querySelectorAll('.tr-card-head')].map((h) => h.textContent!.replace(/ · \d\d:\d\d$/, ''));
    expect(heads()).toEqual(['Asked Frontend', 'Answer from Frontend']);
    expect([...body.querySelectorAll('.toolgroup-names')].map((n) => n.textContent)).toEqual(['read_file', 'message_thread']);
    fireEvent.click(within(tr).getByRole('button', { name: 'Every step' }));
    await waitFor(() => expect(heads()).toEqual(['Asked Frontend', 'Answer from Frontend']));
    expect(within(tr).getAllByText('message_thread')).toHaveLength(1);

    // The counterpart's name opens the pair sheet, which links each message to where it shows.
    fireEvent.click(within(tr).getAllByRole('button', { name: 'Frontend' })[0]!);
    const sheet = await screen.findByRole('dialog', { name: 'Welcome emails ⇄ Frontend' });
    expect(within(sheet).getByRole('link', { name: 'show in Frontend transcript' }).getAttribute('href')).toBe('#/p/p/threads/f?at=8');
    expect(within(sheet).getByRole('link', { name: 'show in Welcome emails transcript' }).getAttribute('href')).toBe('#/p/p/threads/t?at=10');
  });

  it('opens a thread at the stop holding a message it sent (?at=)', async () => {
    await setup(
      [
        ...base,
        ev(6, 'agent.created', { role: 'thread', model: 'claude-opus-5-5', title: 'Frontend', brief: 'Build the page', workspace_path: '/w/f', parent_id: 'd' }, f),
        ev(7, 'tool.call', { run_id: 'r1', tool_call_id: 'c2', name: 'message_thread', arguments: '{"thread_id":"Frontend","text":"Renamed the field."}' }, t),
        ev(8, 'message.agent', { from_agent_id: 't', from_label: 'thread "Welcome emails" (t)', kind: 'note', text: 'Renamed the field.', tool_call_id: 'c2' }, f),
        ev(9, 'tool.result', { run_id: 'r1', tool_call_id: 'c2', name: 'message_thread', status: 'ok', content: 'Sent note #8 to "Frontend".' }, t),
        ev(10, 'message.agent', { from_agent_id: 'd', from_label: 'Desk', kind: 'note', text: 'Wrap up.' }, t),
      ],
      {},
      't',
      8,
    );
    const sent = await screen.findByRole('button', { name: /^Stop 2: Used 1 tool, .*, 1 message$/ });
    await waitFor(() => expect(sent.getAttribute('aria-pressed')).toBe('true'));
    expect(screen.getByRole('button', { name: /^Stop 3: Desk: note/ }).getAttribute('aria-pressed')).toBe('false');
  });

  it('opens a thread at the answer run that wrote its answer to Desk (?at=), a reply sent with no tool call', async () => {
    const d = { agent: 'd' };
    await setup(
      [
        ...base,
        ...finished,
        ev(8, 'agent.created', { role: 'desk', model: 'claude-opus-5-5', title: 'Desk', brief: null, workspace_path: '/w/d', parent_id: null }, d),
        ev(9, 'message.agent', { from_agent_id: 'd', from_label: 'Desk', kind: 'question', text: 'Which currency?', tracked: true }, t),
        ev(10, 'run.started', { run_id: 'r2', model: 'claude-opus-5-5', answering: 9 }, t),
        // Desk's note during the run is a stop of its own, after the run's.
        ev(11, 'message.agent', { from_agent_id: 'd', from_label: 'Desk', kind: 'note', text: 'EUR only.' }, t),
        ev(12, 'assistant.message', { run_id: 'r2', content: 'Euros.', tool_calls: [] }, t),
        ev(13, 'message.agent', { from_agent_id: 't', from_label: 'thread "Welcome emails" (t)', kind: 'answer', text: 'Euros.', reply_to: 9 }, d),
        ev(14, 'run.finished', { run_id: 'r2', reason: 'no_tool_calls' }, t),
      ],
      {},
      't',
      13,
    );
    const run = await screen.findByRole('button', { name: /^Stop 5: Answered Desk/ });
    await waitFor(() => expect(run.getAttribute('aria-pressed')).toBe('true'));
    expect(screen.getByRole('button', { name: /^Stop 6: Desk: note/ }).getAttribute('aria-pressed')).toBe('false');
  });

  it('shows "answering" on a done thread, which keeps its status, Archive and Skill actions and gets no Stop', async () => {
    await setup([...base, ...finished, ...answeringFrontend(8)]);
    const head = (await screen.findByRole('heading', { name: 'Welcome emails', level: 1 })).closest('.thread-head') as HTMLElement;
    expect(await within(head).findByText('answering Frontend')).toBeTruthy();
    expect(head.querySelector('.thread-status-line')!.textContent).toMatch(/^Done/);
    expect(screen.getByRole('button', { name: 'Archive' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Turn into a skill' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull();
  });

  it('marks an answering thread on its roster card', async () => {
    await setup([...base, ...finished, ...answeringFrontend(8)], {}, null);
    const card = await screen.findByRole('link', { name: /Welcome emails/ });
    expect(await within(card).findByText('answering Frontend')).toBeTruthy();
    expect(card.textContent).toContain('Done');
  });

  it('keeps Stop on a waiting thread while it answers, and its dialog says Desk is told', async () => {
    const bridge = await setup([...base, ev(6, 'agent.status_changed', { status: 'waiting', reason: 'Waiting on Desk or the user' }, t), ...answeringFrontend(7)], {
      'threads.stop': () => ({ ok: true }),
    });
    await screen.findByText('answering Frontend');
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect((await screen.findByRole('dialog', { name: 'Stop this thread?' })).textContent).toContain('Desk is told');
    fireEvent.click(screen.getByRole('button', { name: 'Stop thread' }));
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'threads.stop')).toBe(true));
  });

  it('says what a waiting thread waits on, from the fold rather than the status reason', async () => {
    await setup([
      ...base,
      ev(6, 'agent.created', { role: 'thread', model: 'claude-opus-5-5', title: 'Frontend', brief: 'Build the page', workspace_path: '/w/f', parent_id: 'd' }, f),
      ev(7, 'message.agent', { from_agent_id: 't', from_label: 'thread "Welcome emails" (t)', kind: 'question', text: 'Which subject lines?', tracked: true }, { ...f, ts: minutesAgo(4) }),
      ev(8, 'agent.status_changed', { status: 'waiting', reason: 'Waiting on "Frontend"' }, t),
    ]);
    const line = (await screen.findByText('Waiting on Frontend · 4m')).closest('.thread-status-line')!;
    expect(line.textContent).not.toContain('Waiting on "Frontend"');
  });

  it('says a waiting thread waits on you only when it has an attention item', async () => {
    attention = [{ id: 'approval:a1', kind: 'approval', project_id: 'p', project_name: 'Onboarding', agent_id: 't', title: 'Welcome emails wants to run bash', detail: '', created_at: minutesAgo(2), ref: { approval_id: 'a1', thread_id: 't' } }];
    await setup([...base, ev(6, 'agent.status_changed', { status: 'waiting', reason: 'Waiting for approval' }, t)]);
    expect(await screen.findByText('Waiting on you · 2m')).toBeTruthy();
  });

  it('asks a done thread, or reopens it with a message after confirming', async () => {
    const bridge = await setup([...base, ...finished], { 'threads.send': () => ({ ok: true }) });
    const tr = await screen.findByRole('complementary', { name: 'Transcript' });
    const box = await within(tr).findByLabelText('Ask this thread');
    expect(within(tr).queryByRole('button', { name: 'Steer' })).toBeNull();
    expect(within(tr).getByText('It answers from its context; its result stays as it is.')).toBeTruthy();
    fireEvent.input(box, { target: { value: 'How long are the emails?' } });
    fireEvent.click(within(tr).getByRole('button', { name: 'Ask' }));
    await waitFor(() => expect(bridge.calls.filter((c) => c.channel === 'threads.send').map((c) => c.input)).toEqual([{ id: 't', text: 'How long are the emails?', question: true }]));
    expect(await within(tr).findByText('You · asking…')).toBeTruthy();
    bridge.emit('desk:event', ev(8, 'message.user', { text: 'How long are the emails?', question: true }, t));
    await waitFor(() => expect(within(tr).queryByText('You · asking…')).toBeNull());
    expect(screen.getByRole('button', { name: /^Stop \d+: You asked/ })).toBeTruthy();

    fireEvent.input(box, { target: { value: 'Add a sixth email.' } });
    fireEvent.click(within(tr).getByRole('button', { name: 'Reopen with this…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reopen this thread?' });
    expect(dialog.textContent).toContain('Reopening lets it change its work; its result and branch may change.');
    expect(bridge.calls.filter((c) => c.channel === 'threads.send')).toHaveLength(1);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reopen' }));
    await waitFor(() => expect(bridge.calls.filter((c) => c.channel === 'threads.send').at(-1)?.input).toEqual({ id: 't', text: 'Add a sixth email.' }));
    expect(await within(tr).findByText('You · steering…')).toBeTruthy();
  });

  it('resumes an idle thread with a message after confirming', async () => {
    const bridge = await setup([...base, ev(6, 'agent.status_changed', { status: 'idle' }, t)], { 'threads.send': () => ({ ok: true }) });
    const tr = await screen.findByRole('complementary', { name: 'Transcript' });
    fireEvent.input(await within(tr).findByLabelText('Ask this thread'), { target: { value: 'Carry on with the footer.' } });
    fireEvent.click(within(tr).getByRole('button', { name: 'Resume with this…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Resume this thread?' });
    expect(dialog.textContent).toContain('Resuming lets it change its work; its result and branch may change.');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resume' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'threads.send')?.input).toEqual({ id: 't', text: 'Carry on with the footer.' }));
  });

  it("counts a thread's question on its stop's disc and shows a live answer run with its question and tools", async () => {
    await setup([
      ...base,
      ...finished,
      ...answeringFrontend(8),
      ev(11, 'tool.call', { run_id: 'r2', tool_call_id: 'c9', name: 'read_file', arguments: '{"path":"emails/01.md"}' }, t),
    ]);
    // Frontend's question is a card in the stop before the run: its disc shows ✉, and the badge counts it.
    const asked = await screen.findByRole('button', { name: /^Stop 4: Messages, .*, 1 message$/ });
    expect(asked.textContent).toBe('✉');
    expect(asked.parentElement!.querySelector('.route-cards')!.textContent).toBe('1 ✉');
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
    await setup([
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
    expect(await within(tr).findByText(/^Woke to answer message #9 · /)).toBeTruthy();
  });

  it("follows a waiting thread's wait one hop to what needs you, on its status line", async () => {
    attention = [{ id: 'approval:a1', kind: 'approval', project_id: 'p', project_name: 'Onboarding', agent_id: 'f', title: 'Frontend wants to run bash', detail: '', created_at: minutesAgo(1), ref: { approval_id: 'a1', thread_id: 'f' } }];
    await setup([
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

  it("gives a waiting thread's card its wait, which opens the pair sheet, and the hop to what needs you", async () => {
    attention = [{ id: 'approval:a1', kind: 'approval', project_id: 'p', project_name: 'Onboarding', agent_id: 'f', title: 'Frontend wants to run bash', detail: '', created_at: minutesAgo(1), ref: { approval_id: 'a1', thread_id: 'f' } }];
    await setup(
      [
        ...base,
        ev(6, 'agent.created', { role: 'thread', model: 'claude-opus-5-5', title: 'Frontend', brief: 'Build the page', workspace_path: '/w/f', parent_id: 'd' }, f),
        ev(7, 'message.agent', { from_agent_id: 't', from_label: 'thread "Welcome emails" (t)', kind: 'question', text: 'Which subject lines?', tracked: true }, { ...f, ts: minutesAgo(4) }),
        ev(8, 'agent.status_changed', { status: 'waiting', reason: 'Waiting on "Frontend"' }, t),
      ],
      {},
      null,
    );
    const wait = await screen.findByRole('button', { name: 'waiting on Frontend · 4m' });
    // The fold says what it waits on; the status reason is gone from the card.
    expect(screen.getByRole('link', { name: /Welcome emails/ }).textContent).not.toContain('Waiting on "Frontend"');
    expect(screen.getByRole('link', { name: 'Frontend needs your approval' }).getAttribute('href')).toBe('#/attention?item=approval%3Aa1');
    fireEvent.click(wait);
    const sheet = await screen.findByRole('dialog', { name: 'Welcome emails ⇄ Frontend' });
    expect(within(sheet).getByText('Which subject lines?')).toBeTruthy();
    expect(sheet.textContent).toContain('open · 4m');
  });

  it('says "waiting on you" on the card of a thread with an attention item, as plain text', async () => {
    attention = [{ id: 'approval:a2', kind: 'approval', project_id: 'p', project_name: 'Onboarding', agent_id: 't', title: 'Welcome emails wants to run bash', detail: '', created_at: minutesAgo(2), ref: { approval_id: 'a2', thread_id: 't' } }];
    await setup([...base, ev(6, 'agent.status_changed', { status: 'waiting', reason: 'Waiting for approval' }, t)], {}, null);
    const card = await screen.findByRole('link', { name: /Welcome emails/ });
    const line = card.parentElement!.querySelector<HTMLElement>('.thread-card-wait')!;
    expect(line.textContent).toBe('waiting on you · 2m');
    expect(within(line).queryByRole('button')).toBeNull();
  });

  it("keeps a stalled waiting thread's wait a button: a stall asks nothing of the user", async () => {
    attention = [{ id: 'stalled:t', kind: 'stalled', project_id: 'p', project_name: 'Onboarding', agent_id: 't', title: 'Welcome emails is stalled', detail: '', created_at: minutesAgo(1), ref: { thread_id: 't' } }];
    await setup(
      [
        ...base,
        ev(6, 'agent.created', { role: 'thread', model: 'claude-opus-5-5', title: 'Frontend', brief: 'Build the page', workspace_path: '/w/f', parent_id: 'd' }, f),
        ev(7, 'message.agent', { from_agent_id: 't', from_label: 'thread "Welcome emails" (t)', kind: 'question', text: 'Which subject lines?', tracked: true }, { ...f, ts: minutesAgo(4) }),
        ev(8, 'agent.status_changed', { status: 'waiting', reason: 'Waiting on "Frontend"' }, t),
      ],
      {},
      null,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'waiting on Frontend · 4m' }));
    expect(await screen.findByRole('dialog', { name: 'Welcome emails ⇄ Frontend' })).toBeTruthy();
  });

  it('says a running thread will resume on its card while the model proxy is down', async () => {
    proxy = 'down';
    await setup(base, {}, null);
    const card = await screen.findByRole('link', { name: /Welcome emails/ });
    const chip = within(card).getByText('Paused, will resume');
    expect(chip.className).toBe('chip chip-wait');
    expect(card.textContent).not.toContain('Running');
  });

  it('says a running thread will resume on its status line while the model proxy is down', async () => {
    proxy = 'down';
    await setup();
    const head = (await screen.findByRole('heading', { name: 'Welcome emails', level: 1 })).closest('.thread-head') as HTMLElement;
    const status = head.querySelector('.thread-status-line > span')!;
    expect(status.textContent).toBe('Paused, will resume');
    expect(status.className).toBe('tone-wait');
  });

  it("shows an approval's stop with the tool, the policy's reason and a link to review it, then who decided", async () => {
    const bridge = await setup([
      ...base,
      ev(6, 'tool.call', { run_id: 'r1', tool_call_id: 'c2', name: 'bash', arguments: '{"command":"rm -rf build"}' }, t),
      ev(7, 'approval.requested', { approval_id: 'a1', run_id: 'r1', tool_call_id: 'c2', tool: 'bash', arguments: '{"command":"rm -rf build"}', reason: 'Policy rule {"tool":"bash"} → ask', delegate_to_desk: false }, t),
    ]);
    const disc = await screen.findByRole('button', { name: /^Stop \d+: Waiting for your approval/ });
    const n = disc.getAttribute('aria-label')!.match(/^Stop (\d+)/)![1]!;
    const tr = screen.getByRole('complementary', { name: 'Transcript' });
    const body = () => tr.querySelector<HTMLElement>(`#tr-stop-${n} .tr-approval`)!;
    expect(body().textContent).toContain('bash {"command":"rm -rf build"}');
    expect(within(body()).getByText('Your policy asks before every bash call.')).toBeTruthy();
    expect(within(body()).getByRole('link', { name: 'Review in Attention' }).getAttribute('href')).toBe('#/attention?item=approval%3Aa1');
    bridge.emit('desk:event', ev(8, 'approval.resolved', { approval_id: 'a1', decision: 'approved', resolved_by: 'user', note: 'Fine once' }, t));
    expect(await screen.findByRole('button', { name: new RegExp(`^Stop ${n}: You approved`) })).toBeTruthy();
    await waitFor(() => expect(within(body()).queryByRole('link')).toBeNull());
    expect(within(body()).getByText('Approved by you · “Fine once”')).toBeTruthy();
  });

  it("shows a detour and Desk's revision as stops with their bodies, and the revision round on the status line", async () => {
    await setup([
      ...base,
      ev(6, 'agent.model_switched', { from: 'claude-opus-5-5', to: 'claude-sonnet-5', reason: 'rate_limited', scope: 'run' }, t),
      ev(7, 'agent.result', { summary: 'Five drafts published', artifacts: [] }, t),
      ev(8, 'agent.revision', { round: 1, feedback: 'Shorter subject lines, please.' }, t),
    ]);
    const detour = await screen.findByRole('button', { name: /^Stop \d+: Rate limited · a short detour/ });
    const revision = screen.getByRole('button', { name: /^Stop \d+: Desk sent it back/ });
    const entry = (disc: HTMLElement) => document.getElementById(`tr-stop-${disc.getAttribute('aria-label')!.match(/^Stop (\d+)/)![1]}`)!;
    expect(entry(detour).querySelector('.tr-text')!.textContent).toBe('Continued on claude-sonnet-5 for the rest of the run. Nothing was lost.');
    expect(entry(detour).querySelector('.tr-text .mono')!.textContent).toBe('claude-sonnet-5');
    expect(within(entry(revision)).getByText('Shorter subject lines, please.')).toBeTruthy();
    expect(document.querySelector('.thread-status-line')!.textContent).toContain(' · revision round 1 of 2 · ');
  });

  it('selects a stop by clicking its entry in Every step, on the route too', async () => {
    await setup([...base, ev(6, 'assistant.message', { run_id: 'r1', content: 'Drafting the emails now.', tool_calls: [] }, t)]);
    await screen.findByRole('heading', { name: 'Welcome emails', level: 1 });
    const disc = (n: number) => screen.getByRole('button', { name: new RegExp(`^Stop ${n}:`) });
    expect(disc(2).getAttribute('aria-pressed')).toBe('true');
    const tr = screen.getByRole('complementary', { name: 'Transcript' });
    fireEvent.click(within(tr).getByRole('button', { name: 'Every step' }));
    // Both depths render #tr-stop-1 with a .tr-num; only Every step has the pressed button and unnumbered entries.
    await waitFor(() => {
      expect(within(tr).getByRole('button', { name: 'Every step' }).getAttribute('aria-pressed')).toBe('true');
      expect(tr.querySelector('.tr-num-blank')).toBeTruthy();
    });
    const brief = document.getElementById('tr-stop-1')!;
    expect(brief.className).toBe('tr-entry');
    fireEvent.click(brief);
    await waitFor(() => expect(disc(1).getAttribute('aria-pressed')).toBe('true'));
    expect(disc(2).getAttribute('aria-pressed')).toBe('false');
    expect(document.getElementById('tr-stop-1')!.className).toBe('tr-entry selected');
    // An entry without a number (the run's text after its tools) selects the stop it belongs to.
    const text = within(tr).getByText('Drafting the emails now.').closest('.tr-entry') as HTMLElement;
    expect(text.querySelector('.tr-num-blank')).toBeTruthy();
    fireEvent.click(text);
    await waitFor(() => expect(disc(2).getAttribute('aria-pressed')).toBe('true'));
    expect(document.getElementById('tr-stop-2')!.className).toBe('tr-entry selected');
  });

  it('scrolls the selected stop into view: the last one first, then the one picked on the route', async () => {
    const scrolled: Array<[string, unknown]> = [];
    const had = Object.hasOwn(Element.prototype, 'scrollIntoView');
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = vi.fn(function (this: Element, opts?: boolean | ScrollIntoViewOptions) {
      scrolled.push([this.id, opts]);
    });
    try {
      await setup();
      await screen.findByRole('heading', { name: 'Welcome emails', level: 1 });
      await waitFor(() => expect(scrolled.at(-1)).toEqual(['tr-stop-2', { block: 'nearest', behavior: 'smooth' }]));
      fireEvent.click(screen.getByRole('button', { name: /^Stop 1:/ }));
      await waitFor(() => expect(scrolled.at(-1)).toEqual(['tr-stop-1', { block: 'nearest', behavior: 'smooth' }]));
    } finally {
      if (had) Element.prototype.scrollIntoView = original;
      else delete (Element.prototype as Partial<Element>).scrollIntoView;
    }
  });

  it("says a thread that is not in this project isn't here, with a way back to its threads", async () => {
    await setup(base, {}, 'nope');
    const heading = await screen.findByRole('heading', { name: "This thread isn't here" });
    expect(screen.getByText('It may belong to another project.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'All threads' }).getAttribute('href')).toBe('#/p/p/threads');
    // The screen's host is the page itself here, as the desktop's root div.page.
    expect(heading.closest('.page')!.className).toBe('page');
    expect((heading.closest('.page') as HTMLElement).style.display).toBe('');
  });

  it("says a project that is not there isn't here, with a way back to the map", async () => {
    await setup(base, {
      'projects.get': () => {
        throw { code: 'not_found', message: 'Project p not found', status: 404 };
      },
    });
    expect(await screen.findByRole('heading', { name: "This project isn't here" })).toBeTruthy();
    expect(screen.getByText('Project p not found')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Back to the map' }).getAttribute('href')).toBe('#/map');
  });

  it('is what #/p/<id>/threads shows, with the thread and the message the route names', () => {
    expect(screenFor({ name: 'project', id: 'p', tab: 'threads', threadId: 't', at: 7 })).toEqual({ component: ThreadsScreen, inputs: { projectId: 'p', threadId: 't', at: 7 } });
    expect(screenFor({ name: 'project', id: 'p', tab: 'threads' })).toEqual({ component: ThreadsScreen, inputs: { projectId: 'p', threadId: undefined, at: undefined } });
  });
});
