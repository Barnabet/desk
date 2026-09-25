// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import { WAKES_PAUSED, type AgentMessageKind, type AttentionItem, type EventOf, type StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { resetSessions, setReleaseDelay, startSessionRouting } from '../state/session';
import { clearAttachmentCache } from '../components/ImageThumbs';
import { clock } from '../format';
import { installBridge } from '../test/bridge';
import { chatDomId } from './ChatItems';
import { CHAT_PAGE, ConversationScreen } from './ConversationScreen';

afterEach(cleanup);
beforeEach(() => {
  clearAttachmentCache();
  resetSessions();
  setReleaseDelay(0);
  localStorage.clear();
});

const overview = (): ProjectOverview => ({
  project: { id: 'p', name: 'Onboarding revamp', goal: 'g', instructions: '', settings: { desk_model: 'claude-opus-5-5', thread_model: 'm', fallback_model: null, desk_reasoning_effort: null, thread_reasoning_effort: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, policy: [] }, created_at: 't', updated_at: 't', archived_at: null },
  desk: { id: 'd', project_id: 'p', role: 'desk', status: 'idle', model: 'claude-opus-5-5', reasoning_effort: null, title: 'Desk', brief: null, workspace_path: '/w', parent_id: null, inbox_cursor: 0, review_round: 0, result_summary: null, result_artifacts: null, active_skills: [], git_source_id: null, git_branch: null, git_base: null, git_common_dir: null, archived_at: null, created_at: 't', updated_at: 't' },
  sources: [],
  plan: { project_id: 'p', items: [{ id: '1', title: 'Add a setup checklist', status: 'in_progress', thread_ids: ['t'], notes: '' }], updated_at: 't' },
  threads: [],
  approvals: [],
  last_seq: 1,
});

const events = [
  ev(1, 'project.created', { name: 'Onboarding revamp', goal: 'g', instructions: '' }),
  ev(2, 'message.user', { text: 'Relaunch onboarding next month' }, { agent: 'd' }),
  ev(3, 'agent.created', { role: 'thread', model: 'm', title: 'Signup checklist', brief: 'Build it', workspace_path: '/w/t', parent_id: 'd' }, { agent: 't' }),
  ev(4, 'agent.status_changed', { status: 'running' }, { agent: 't' }),
  ev(5, 'report', { headline: 'Research is in', progress: 'All three competitors lead with one first win.', needs_you: ['Approve installing bun'], results: ['competitor-onboarding.md'] }, { agent: 'd' }),
  ev(6, 'question.asked', { question: 'Data source or teammate invite first?', options: ['Connect a data source', 'Invite a teammate'] }, { agent: 'd' }),
];

function setup(extra: Record<string, (input: any) => unknown> = {}, list: StoredEvent[] = events) {
  globalStore.set({ ...initialGlobalState(), connection: { status: 'live' }, attention: [{ id: 'report:5:0', kind: 'needs_you', project_id: 'p', project_name: 'Onboarding revamp', agent_id: 'd', title: 'Approve installing bun', detail: '', created_at: '', ref: { event_id: 5 } }] });
  const bridge = installBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of list) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    'projects.send': () => ({ ok: true }),
    ...extra,
  });
  startSessionRouting();
  render(<ConversationScreen projectId="p" />);
  return bridge;
}

describe('ConversationScreen', () => {
  it('shows the line diagram, the chat, the report and the plan', async () => {
    setup();
    await screen.findByRole('heading', { name: 'Research is in' });
    expect(screen.getAllByRole('link', { name: /Signup checklist/ })[0]!.getAttribute('href')).toBe('#/p/p/threads/t');
    expect(screen.getByRole('button', { name: /Your brief|Relaunch onboarding/ })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'competitor-onboarding.md' }).getAttribute('href')).toBe('#/p/p/library?file=competitor-onboarding.md');
    expect(screen.getByRole('link', { name: 'Approve installing bun' }).getAttribute('href')).toBe('#/attention?item=report%3A5%3A0');
    const plan = screen.getByRole('complementary', { name: 'Plan and Desk' });
    expect(plan.textContent).toContain('Add a setup checklist');
    expect(plan.textContent).toContain('claude-opus-5-5');
  });

  it('answers a question with an option and shows the message as sending', async () => {
    const bridge = setup();
    fireEvent.click(await screen.findByRole('button', { name: 'Connect a data source' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.send')?.input).toEqual({ id: 'p', text: 'Connect a data source' }));
    expect(await screen.findByText('You · sending…')).toBeTruthy();
    bridge.emit('desk:event', ev(7, 'message.user', { text: 'Connect a data source' }, { agent: 'd' }));
    await waitFor(() => expect(screen.queryByText('You · sending…')).toBeNull());
    expect(screen.queryByRole('button', { name: 'Connect a data source' })).toBeNull();
  });

  it('sends with Enter and attaches files through the Library', async () => {
    const bridge = setup({ 'library.upload': ({ file }: { file: { name: string } }) => ({ id: 'a1', path: `uploads/${file.name}` }) });
    const box = (await screen.findByLabelText('Message Desk')) as HTMLTextAreaElement;
    fireEvent.change(screen.getByTestId('attach-input'), { target: { files: [new File(['hi'], 'notes.md')] } });
    await waitFor(() => expect(box.value).toContain('Attached: uploads/notes.md'));
    expect(bridge.calls.find((c) => c.channel === 'library.upload')?.input).toEqual({ projectId: 'p', file: { name: 'notes.md', content_base64: 'aGk=' } });
    fireEvent.change(box, { target: { value: 'Use these notes' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(bridge.calls.filter((c) => c.channel === 'projects.send').map((c) => c.input)).toEqual([{ id: 'p', text: 'Use these notes' }]));
    await waitFor(() => expect(box.value).toBe(''));
  });

  it('renders a long conversation from the newest messages at once, and older ones on demand', async () => {
    const long = [events[0]!, ...Array.from({ length: 150 }, (_, i) => ev(i + 2, 'message.user', { text: `Message ${i + 1}` }, { agent: 'd' }))];
    const bridge = installBridge({
      'projects.get': () => overview(),
      'broker.watch': () => {
        bridge.emit('desk:events', long);
        return { ok: true };
      },
      'broker.unwatch': () => ({ ok: true }),
    });
    globalStore.set({ ...initialGlobalState(), connection: { status: 'live' } });
    startSessionRouting();
    render(<ConversationScreen projectId="p" />);
    await screen.findByText('Message 150');
    const chat = screen.getByRole('region', { name: 'Conversation with Desk' });
    expect(chat.querySelectorAll('.chat-item')).toHaveLength(CHAT_PAGE);
    expect(screen.queryByText('Message 1')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: `Show earlier messages (${150 - CHAT_PAGE})` }));
    expect(chat.querySelectorAll('.chat-item')).toHaveLength(2 * CHAT_PAGE);
    fireEvent.click(screen.getByRole('button', { name: /Show earlier messages/ }));
    expect(screen.getByText('Message 1')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Show earlier messages/ })).toBeNull();
  });

  it('shows thumbnails of the images Desk looked at under its tool calls, and opens one larger', async () => {
    const image = { sha256: 'b'.repeat(64), media_type: 'image/png' as const, width: 800, height: 600, bytes: 52_000, name: 'library/mockup.png' };
    const viewed: StoredEvent[] = [
      ...events,
      ev(7, 'assistant.message', { run_id: 'r', content: null, tool_calls: [{ id: 'v1', name: 'view_image', arguments: '{"paths":["library/mockup.png"]}' }] }, { agent: 'd' }),
      ev(8, 'tool.call', { run_id: 'r', tool_call_id: 'v1', name: 'view_image', arguments: '{"paths":["library/mockup.png"]}' }, { agent: 'd' }),
      ev(9, 'tool.result', { run_id: 'r', tool_call_id: 'v1', name: 'view_image', status: 'ok', content: 'library/mockup.png · 800x600 · PNG · 51 KB', images: [image] }, { agent: 'd' }),
    ];
    const bridge = setup({ 'attachments.get': () => 'data:image/png;base64,ZnVsbA==' }, viewed);
    const chat = await screen.findByRole('region', { name: 'Conversation with Desk' });
    const thumb = await within(chat).findByRole('button', { name: 'Open mockup.png' });
    await waitFor(() => expect(within(thumb).getByRole('img', { name: 'mockup.png' }).getAttribute('src')).toBe('data:image/png;base64,ZnVsbA=='));
    fireEvent.click(thumb);
    const dialog = await screen.findByRole('dialog', { name: 'mockup.png' });
    await waitFor(() => expect(within(dialog).getByRole('img', { name: 'mockup.png' }).getAttribute('src')).toBe('data:image/png;base64,ZnVsbA=='));
    expect(bridge.calls.filter((c) => c.channel === 'attachments.get').map((c) => c.input)).toEqual([{ sha256: image.sha256 }, { sha256: image.sha256 }]);
  });
});

/** A time `m` minutes ago, 20 s past the minute so it reads "<m>m" on either side of the shared clock's 15 s step. */
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000 - 20_000).toISOString();
const TITLES: Record<string, string> = { d: 'Desk', a: 'Auth API', f: 'Frontend', b: 'Billing' };
const created = (id: number, agent: string, ts: string) =>
  ev(
    id,
    'agent.created',
    { role: agent === 'd' ? 'desk' : 'thread', model: 'm', title: TITLES[agent]!, brief: agent === 'd' ? null : `Own ${TITLES[agent]}`, workspace_path: `/w/${agent}`, parent_id: agent === 'd' ? null : 'd' },
    { agent, ts },
  );
const msg = (id: number, from: string, to: string, kind: AgentMessageKind, text: string, ts: string, extra: Partial<EventOf<'message.agent'>['payload']> = {}) =>
  ev(id, 'message.agent', { from_agent_id: from, from_label: from === 'd' ? 'Desk' : `thread "${TITLES[from]}" (${from})`, kind, text, ...extra }, { agent: to, ts });
/** Desk d and the threads a ("Auth API") and f ("Frontend"), created in the last quarter hour (events 1–4). */
const team = (): StoredEvent[] => [
  ev(1, 'project.created', { name: 'Onboarding revamp', goal: 'g', instructions: '' }, { ts: minutesAgo(12) }),
  created(2, 'd', minutesAgo(12)),
  created(3, 'a', minutesAgo(10)),
  created(4, 'f', minutesAgo(9)),
];
/** Renders the conversation over `list`, with `attention` as the app's attention list. */
function show(list: StoredEvent[], attention: AttentionItem[] = [], extra: Record<string, (input: any) => unknown> = {}) {
  globalStore.set({ ...initialGlobalState(), connection: { status: 'live' }, attention });
  const bridge = installBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of list) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    ...extra,
  });
  startSessionRouting();
  render(<ConversationScreen projectId="p" />);
  return bridge;
}
/** The chat row of item `id` (its wrapper, which flashes when jumped to). */
const row = (id: string) => document.getElementById(chatDomId(id));

describe('messages in the conversation', () => {
  it("shows Desk's question to a thread as waiting, then answered, and a digest's open count, from the fold", async () => {
    const asked = minutesAgo(4);
    const bridge = show([
      ...team(),
      ev(5, 'run.started', { run_id: 'r1', model: 'm' }, { agent: 'd', ts: minutesAgo(5) }),
      msg(6, 'd', 'a', 'question', 'Which token format?', asked, { tracked: true }),
      msg(7, 'a', 'f', 'question', 'Is the login page ready?', minutesAgo(3), { tracked: true }),
      // Desk's next turn freezes the digest: the answer that ends its open question lands in a later one.
      ev(8, 'run.started', { run_id: 'r2', model: 'm' }, { agent: 'd', ts: minutesAgo(2) }),
    ]);
    await waitFor(() => expect(row('e:6')?.textContent).toContain('waiting for an answer · 4m'));
    expect(row('e:6')!.textContent).toContain(`Desk → Auth API · question · ${clock(asked)}`);
    expect(within(row('e:6')!).getByRole('link', { name: 'Auth API' }).getAttribute('href')).toBe('#/p/p/threads/a');
    expect(within(row('e:7')!).getByRole('button', { name: 'Between threads · 1 message · 1 open question' })).toBeTruthy();

    // Auth API answers Desk on Desk's stream: the question row reads "answered", and the answer quotes the question.
    const answered = minutesAgo(1);
    bridge.emit('desk:event', msg(9, 'a', 'd', 'answer', 'JWT, RS256.', answered, { reply_to: 6 }));
    const done = await within(row('e:6')!).findByRole('button', { name: `answered ${clock(answered)} ↓` });
    expect(row('e:6')!.textContent).not.toContain('waiting for an answer');
    fireEvent.click(done);
    await waitFor(() => expect(row('e:9')!.classList.contains('flash')).toBe(true));
    fireEvent.click(within(row('e:9')!).getByRole('button', { name: "↩ Desk's question: “Which token format?”" }));
    await waitFor(() => expect(row('e:6')!.classList.contains('flash')).toBe(true));

    // Frontend answers Auth API on Auth API's stream: the frozen digest's item is unchanged, but its open count drops.
    bridge.emit('desk:event', msg(10, 'f', 'a', 'answer', 'Yes, merged.', minutesAgo(0), { reply_to: 7 }));
    expect(await within(row('e:7')!).findByRole('button', { name: 'Between threads · 1 message' })).toBeTruthy();
    expect(within(row('e:10')!).getByRole('button', { name: 'Between threads · 1 message' })).toBeTruthy();
  });

  it('lists a digest pair by pair when expanded, each line opening the recipient of its latest message there', async () => {
    show([...team(), msg(5, 'a', 'f', 'question', 'Is the login page ready?', minutesAgo(3), { tracked: true }), msg(6, 'f', 'a', 'note', 'Renamed the token field.', minutesAgo(2))]);
    const toggle = await screen.findByRole('button', { name: 'Between threads · 2 messages · 1 open question' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('link', { name: /⇄/ })).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByRole('link', { name: 'Auth API ⇄ Frontend · 2 · Auth API waiting 3m' }).getAttribute('href')).toBe('#/p/p/threads/a?at=6');
  });

  it('shows what Desk and the user sent threads, hides start, clamps long text and mutes a closure', async () => {
    const at = { note: minutesAgo(7), steer: minutesAgo(6), ask: minutesAgo(5), q: minutesAgo(4), closed: minutesAgo(3) };
    const long = Array.from({ length: 6 }, (_, i) => `Step ${i + 1} of the plan.`).join('\n');
    show([
      ...team(),
      msg(5, 'd', 'a', 'start', 'Begin your assignment.', minutesAgo(8)),
      msg(6, 'd', 'a', 'note', long, at.note),
      ev(7, 'message.user', { text: 'Use the v2 API.' }, { agent: 'f', ts: at.steer }),
      ev(8, 'message.user', { text: 'How did you price it?', question: true }, { agent: 'f', ts: at.ask }),
      msg(9, 'd', 'f', 'question', 'Done yet?', at.q, { tracked: true }),
      msg(10, 'f', 'd', 'answer', '(Frontend was stopped before answering.)', at.closed, { reply_to: 9, auto: true }),
      msg(11, 'd', 'a', 'question', 'Ready?', at.closed, { tracked: true }),
      msg(12, 'a', 'd', 'answer', '(Auth API could not answer: Desk was restarting. Ask again if you still need to know.)', at.closed, { reply_to: 11, auto: true }),
    ]);
    await waitFor(() => expect(row('e:10')?.textContent).toContain('Frontend could not answer: it was stopped before answering.'));
    // A closure that already says it could not answer is not said twice.
    expect(row('e:12')!.textContent).toContain('Auth API could not answer: Desk was restarting. Ask again if you still need to know.');
    expect(row('e:12')!.textContent).not.toContain('could not answer: Auth API');
    expect(row('e:5')).toBeNull();
    expect(row('e:6')!.textContent).toContain(`Desk → Auth API · note · ${clock(at.note)}`);
    fireEvent.click(within(row('e:6')!).getByRole('button', { name: 'more' }));
    expect(within(row('e:6')!).getByRole('button', { name: 'less' })).toBeTruthy();
    expect(row('e:7')!.textContent).toContain(`You → Frontend · ${clock(at.steer)}`);
    expect(row('e:8')!.textContent).toContain(`You asked Frontend · ${clock(at.ask)}`);
    expect(row('e:9')!.textContent).toContain('closed');
    expect(row('e:10')!.textContent).not.toContain('Answer');
    expect(within(row('e:10')!).getByRole('button', { name: "↩ Desk's question: “Done yet?”" })).toBeTruthy();
  });

  it("drops Desk's successful sends from its tool groups and keeps the failed ones", async () => {
    const d = { agent: 'd' };
    const toolCall = (id: number, callId: string, name: string, args: string) => ev(id, 'tool.call', { run_id: 'r', tool_call_id: callId, name, arguments: args }, d);
    const toolResult = (id: number, callId: string, name: string, status: 'ok' | 'error', content: string) => ev(id, 'tool.result', { run_id: 'r', tool_call_id: callId, name, status, content }, d);
    show([
      ...team(),
      toolCall(5, 'c1', 'message_thread', '{"thread_id":"a","text":"Use JWT."}'),
      msg(6, 'd', 'a', 'note', 'Use JWT.', minutesAgo(3)),
      toolResult(7, 'c1', 'message_thread', 'ok', 'Sent note #6 to "Auth API".'),
      ev(8, 'assistant.message', { run_id: 'r', content: 'Told Auth API.', tool_calls: [] }, d),
      toolCall(9, 'c2', 'message_thread', '{"thread_id":"Nobody","text":"Hi"}'),
      toolResult(10, 'c2', 'message_thread', 'error', 'Unknown thread: Nobody'),
      toolCall(11, 'c3', 'read_thread', '{"thread_id":"a"}'),
      toolResult(12, 'c3', 'read_thread', 'ok', 'Auth API: running'),
    ]);
    await waitFor(() => expect(row('tools:9')?.textContent).toContain('Desk used 2 tools'));
    expect(row('tools:9')!.textContent).toContain('message_thread · read_thread');
    expect(row('tools:5')!.textContent).toBe('');
    expect(row('e:6')!.textContent).toContain('Desk → Auth API · note');
  });

  it('labels lanes and plan stops with what threads wait on, "you" only for an attention item, and who is answering', async () => {
    const asked = minutesAgo(4);
    const approved = minutesAgo(2);
    const approval: AttentionItem = { id: 'approval:x1', kind: 'approval', project_id: 'p', project_name: 'Onboarding revamp', agent_id: 'f', title: 'Frontend wants to run bash', detail: '', created_at: approved, ref: { approval_id: 'x1', thread_id: 'f' } };
    const plan = {
      project_id: 'p',
      items: [
        { id: '1', title: 'Login API', status: 'in_progress', thread_ids: ['a'], notes: '' },
        { id: '2', title: 'Login page', status: 'in_progress', thread_ids: ['f'], notes: '' },
      ],
      updated_at: 't',
    };
    show(
      [
        ...team(),
        created(5, 'b', minutesAgo(8)),
        ev(6, 'agent.status_changed', { status: 'done' }, { agent: 'b', ts: minutesAgo(7) }),
        msg(7, 'a', 'f', 'question', 'Which token format?', asked, { tracked: true }),
        ev(8, 'agent.status_changed', { status: 'waiting', reason: 'Waiting on "Frontend"' }, { agent: 'a', ts: asked }),
        ev(9, 'agent.status_changed', { status: 'waiting', reason: 'Waiting for approval' }, { agent: 'f', ts: approved }),
        // Billing is done and answers Desk: its status stays done.
        msg(10, 'd', 'b', 'question', 'Which currency?', minutesAgo(1), { tracked: true }),
        ev(11, 'run.started', { run_id: 'rb', model: 'm', answering: 10 }, { agent: 'b', ts: minutesAgo(1) }),
      ],
      [approval],
      { 'projects.get': () => ({ ...overview(), plan }) },
    );
    expect(await screen.findByText('waiting on Frontend · 4m')).toBeTruthy();
    expect(screen.getByText('waiting on you · 2m')).toBeTruthy();
    expect(screen.getByText('answering Desk').closest('.line-label-sub')!.textContent).toBe('done · answering Desk');
    const panel = screen.getByRole('complementary', { name: 'Plan and Desk' });
    expect(within(panel).getByText('Login API').closest('li')!.textContent).not.toContain('waiting on you');
    expect(within(panel).getByText('Login page').closest('li')!.textContent).toContain('waiting on you');
  });

  it("offers Resume on the pause notice while the project's paused item exists", async () => {
    const notice = ev(5, 'system.notice', {
      level: 'warning',
      code: WAKES_PAUSED,
      message: 'Agents woke each other 60 times in the last hour, so automatic wakes are paused. Their messages are kept. Write to any agent of this project, or press Resume, to continue.',
    }, { ts: minutesAgo(1) });
    const paused: AttentionItem = {
      id: 'paused:5',
      kind: 'paused',
      project_id: 'p',
      project_name: 'Onboarding revamp',
      agent_id: null,
      title: 'Agents in Onboarding revamp are paused: too many automatic wakes this hour',
      detail: 'Their messages are kept. Resume, or write to any agent.',
      created_at: minutesAgo(1),
      ref: { event_id: 5 },
    };
    const bridge = show([...team(), notice], [paused], { 'attention.dismiss': () => ({ ok: true }) });
    const note = await waitFor(() => {
      expect(row('e:5')).toBeTruthy();
      return row('e:5')!;
    });
    fireEvent.click(within(note).getByRole('button', { name: 'Resume' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'attention.dismiss')?.input).toEqual({ id: 'paused:5' }));
    // Once the item is gone (resumed, or the user wrote to an agent), the notice stays as history without the button.
    globalStore.set((g) => ({ ...g, attention: [] }));
    await waitFor(() => expect(within(note).queryByRole('button', { name: 'Resume' })).toBeNull());
    expect(note.textContent).toContain('automatic wakes are paused');
  });

  it("names threads by title in Desk's tool rows", async () => {
    const d = { agent: 'd' };
    show([
      ...team(),
      ev(5, 'tool.call', { run_id: 'r', tool_call_id: 'c1', name: 'read_thread', arguments: '{"thread_id":"a"}' }, d),
      ev(6, 'tool.result', { run_id: 'r', tool_call_id: 'c1', name: 'read_thread', status: 'ok', content: 'Auth API: running' }, d),
    ]);
    await waitFor(() => expect(row('tools:5')?.textContent).toContain('read_thread Auth API'));
  });
});
