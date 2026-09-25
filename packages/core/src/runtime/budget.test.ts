import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, error, text, tools, type ChatRequest, type FakeReply } from '@desk/fake-model';
import { WAKES_PAUSED, type AgentStatus, type EventOf } from '@desk/protocol';
import { listAttention } from '../state/attention';
import { getAgent, getDeskAgent, listThreads } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';
import type { Runtime, RuntimeOptions } from './runtime';

let h: Harness;
/** The runtime under test. */
let rt: Runtime;
afterEach(async () => h?.cleanup());

type Reply = (req: ChatRequest) => FakeReply;
const systemOf = (req: ChatRequest) => String(req.messages[0]?.content ?? '');
const isDesk = (req: ChatRequest) => systemOf(req).startsWith('You are Desk');
/** The thread a request comes from, by the title in its system prompt. */
const titleOf = (req: ChatRequest) => /## Your assignment: (.+)/.exec(systemOf(req))?.[1];
/** The newest message of a request: the previous step's tool result, or the batch that started the turn. */
const last = (req: ChatRequest) => req.messages.at(-1)!;
/** How many times the conversation already called `name`: a script counts its own rounds from the request. */
const callsOf = (req: ChatRequest, name: string) =>
  req.messages
    .filter((m) => m.role === 'assistant')
    .flatMap((m) => m.tool_calls ?? [])
    .filter((c) => c.function?.name === name).length;
/** The batch that started a turn, as the model read it (the newest message of the turn's first request). */
const batchOf = (req: ChatRequest) => String(last(req).content ?? '');
const deskRequests = () => h.fake.requests.filter(isDesk);
const threadRequests = (title: string) => h.fake.requests.filter((r) => titleOf(r) === title);

/**
 * A project whose Desk and threads all use the fake model, which takes one call at a time, so runs follow each other
 * in a fixed order. Desk follows `desk` (by default it answers "noted"); each thread follows the script under its title.
 */
async function setup(opts: Partial<RuntimeOptions>, threads: Record<string, Reply> = {}, desk: Reply = () => text('noted')) {
  h = await createHarness({ concurrency: 1, script: (req) => (isDesk(req) ? desk(req) : (threads[titleOf(req) ?? '']?.(req) ?? text('(no script)'))) });
  rt = newRuntime(h, opts);
  const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id } });
  const deskAgent = getDeskAgent(h.store.db, projectId)!;
  const thread = (title: string) => rt.createThread(projectId, { title, brief: `Do the ${title} part`, workspacePath: join(h.dir, title) });
  /** Puts an agent in a status directly: a `running` thread has no job, so what it is sent waits for a next step. */
  const setStatus = (agentId: string, status: AgentStatus) =>
    h.store.append({ project_id: projectId, agent_id: agentId, type: 'agent.status_changed', payload: { status } });
  /** Marks the agent's stream as read up to `upTo`, as a run's drain does. */
  const drained = (agentId: string, upTo: number) =>
    h.store.append({ project_id: projectId, agent_id: agentId, type: 'inbox.drained', payload: { run_id: 'test-drain', up_to: upTo } });
  return { projectId, desk: deskAgent, thread, setStatus, drained };
}

const runs = (agentId: string) => h.store.list({ agentId, types: ['run.started'] });
const status = (agentId: string) => getAgent(h.store.db, agentId)?.status;
/** The kinds of the messages on an agent's stream that it has not read yet, oldest first. */
const unread = (agentId: string) =>
  h.store
    .list({ agentId, after: getAgent(h.store.db, agentId)!.inbox_cursor, types: ['message.agent'] })
    .map((e) => (e.type === 'message.agent' ? e.payload.kind : e.type));
/** The project's wakes_paused notices, oldest first. */
const pauses = (projectId: string) =>
  h.store
    .list({ projectId, types: ['system.notice'] })
    .filter((e): e is EventOf<'system.notice'> => e.type === 'system.notice' && e.payload.code === WAKES_PAUSED);
const pausedItems = () => listAttention(h.store.db).filter((i) => i.kind === 'paused');
const ENDING = ', so automatic wakes are paused. Their messages are kept. Write to any agent of this project, or press Resume, to continue.';
const agentPause = (budget: number) => `Agents woke each other ${budget} times in the last hour${ENDING}`;
const lifecyclePause = (budget: number) => `Agents were woken by thread starts and notices ${budget} times in the last hour${ENDING}`;

describe('the wake budgets', () => {
  it('pause a Desk↔thread ping-pong after the agent-triggered budget, with one notice and one attention item', async () => {
    const { projectId, desk, thread } = await setup(
      { wakeBudget: 3 },
      { Echo: () => text('Pong.') },
      // Desk answers every new batch with a note to Echo (four at most), and ends its turn once the note is sent.
      (req) =>
        last(req).role === 'tool' || callsOf(req, 'message_thread') >= 4 ? text('Sent.') : tools(call('message_thread', { thread_id: 'Echo', text: 'Ping.' })),
    );
    const echo = thread('Echo');
    rt.sendToDesk(projectId, 'Keep Echo busy.');
    await rt.whenIdle();

    // Counted: Desk's note runs Echo (1), Echo's update runs Desk (2), Desk's next note runs Echo (3). Echo's second
    // update finds the hour full: the project pauses and Desk does not run.
    expect(runs(echo)).toHaveLength(2);
    expect(runs(desk.id)).toHaveLength(2);
    expect(unread(desk.id)).toEqual(['update']);
    const [notice, ...more] = pauses(projectId);
    expect(more).toEqual([]);
    expect(notice!.payload).toEqual({ level: 'warning', code: 'wakes_paused', message: agentPause(3) });
    expect(pausedItems()).toEqual([
      {
        id: `paused:${notice!.id}`,
        kind: 'paused',
        project_id: projectId,
        project_name: 'P',
        agent_id: null,
        title: 'Agents in P are paused: too many automatic wakes this hour',
        detail: 'Their messages are kept. Resume, or write to any agent.',
        created_at: notice!.ts,
        ref: { event_id: notice!.id },
      },
    ]);

    // While paused nothing agent-triggered starts, and the pause is announced once.
    rt.deliver(echo, desk.id, 'update', 'Still here.');
    rt.deliver(desk.id, echo, 'note', 'Ping again.');
    await rt.whenIdle();
    expect(runs(desk.id)).toHaveLength(2);
    expect(runs(echo)).toHaveLength(2);
    expect(pauses(projectId)).toHaveLength(1);
  });

  it('pause a Desk that respawns a thread failing at once after the lifecycle budget, with one notice', async () => {
    const { projectId, desk } = await setup(
      { lifecycleBudget: 5 },
      { Fetcher: () => error(401, 'authentication_error', 'bad key') },
      // Desk spawns a Fetcher for every new batch (the user's request, then each failure; four at most), and ends its
      // turn once it has.
      (req) =>
        last(req).role === 'tool' || callsOf(req, 'spawn_thread') >= 4
          ? text('Spawned.')
          : tools(call('spawn_thread', { title: 'Fetcher', brief: 'Fetch the data.' })),
    );
    rt.sendToDesk(projectId, 'Get the data.');
    await rt.whenIdle();

    // Counted: each start (1, 3, 5) and each failure reported to Desk (2, 4). The third failure finds the hour full.
    expect(listThreads(h.store.db, projectId).map((t) => t.status)).toEqual(['failed', 'failed', 'failed']);
    expect(runs(desk.id)).toHaveLength(3);
    expect(unread(desk.id)).toEqual(['failed']);
    expect(pauses(projectId).map((e) => e.payload.message)).toEqual([lifecyclePause(5)]);
    expect(pausedItems()).toHaveLength(1);
  });
});

/**
 * A project paused the way agents pause it: with a budget of 2 agent-triggered wakes, Desk's notes run Writer and
 * Editor, and the third note, to Reader, finds the hour full. Writer and Editor complete; their `completed` notices
 * wait on Desk's stream, since a notice to Desk is a lifecycle wake and nothing automatic starts while paused.
 */
async function pausedProject() {
  const s = await setup(
    { wakeBudget: 2 },
    {
      Writer: () => tools(call('complete', { summary: 'Draft written' })),
      Editor: () => tools(call('complete', { summary: 'Draft edited' })),
      Reader: () => text('On it.'),
    },
  );
  const writer = s.thread('Writer');
  const editor = s.thread('Editor');
  const reader = s.thread('Reader');
  rt.deliver(s.desk.id, writer, 'note', 'Write the draft.');
  rt.deliver(s.desk.id, editor, 'note', 'Edit the draft.');
  rt.deliver(s.desk.id, reader, 'note', 'Read the draft.');
  await rt.whenIdle();
  const [notice] = pauses(s.projectId);
  return { ...s, writer, editor, reader, notice: notice! };
}

describe('while paused', () => {
  it('holds notices to Desk and stall reports; a user message goes through, resumes the project, and Desk reads what was held', async () => {
    const { projectId, desk, thread, setStatus, writer, editor, reader, notice } = await pausedProject();
    expect(notice.payload.message).toBe(agentPause(2));
    expect([status(writer), status(editor), status(reader)]).toEqual(['done', 'done', 'idle']);
    // A thread's completed does not wake Desk.
    expect(unread(desk.id)).toEqual(['completed', 'completed']);
    expect(runs(desk.id)).toHaveLength(0);
    expect(runs(reader)).toHaveLength(0);
    // checkStalls skips the project: a thread quiet for 16 minutes raises nothing.
    const idler = thread('Idler');
    setStatus(idler, 'waiting');
    const later = Date.now() + 16 * 60_000;
    expect(rt.checkStalls(later)).toEqual([]);

    // The user's message runs Reader, resumes the project, and every agent decides again.
    rt.sendMessage(reader, 'Start with chapter 2.');
    await rt.whenIdle();
    expect(pausedItems()).toEqual([]);
    expect(runs(reader)).toHaveLength(1);
    const readerBatch = batchOf(threadRequests('Reader')[0]!);
    expect(readerBatch).toContain('Read the draft.');
    expect(readerBatch).toContain('Start with chapter 2.');
    const deskBatch = batchOf(deskRequests()[0]!);
    expect(deskBatch).toContain('Summary: "Draft written"');
    expect(deskBatch).toContain('Summary: "Draft edited"');
    // Stalls are checked again.
    expect(rt.checkStalls(later)).toEqual([idler]);
    await rt.whenIdle();
    expect(pauses(projectId)).toHaveLength(1);
  });

  it('resumes the same way when the user dismisses the paused item', async () => {
    const { projectId, reader, notice } = await pausedProject();
    rt.dismissAttention(`paused:${notice.id}`);
    await rt.whenIdle();
    // Reader's held note runs it and Desk reads the held notices. Both windows start over, so these wakes do not pause
    // the project again at once.
    expect(runs(reader)).toHaveLength(1);
    expect(batchOf(threadRequests('Reader')[0]!)).toContain('Read the draft.');
    const deskBatch = batchOf(deskRequests()[0]!);
    expect(deskBatch).toContain('Summary: "Draft written"');
    expect(deskBatch).toContain('Summary: "Draft edited"');
    expect(pausedItems()).toEqual([]);
    expect(h.store.list({ projectId, types: ['attention.dismissed'] }).map((e) => e.payload)).toEqual([{ item_id: `paused:${notice.id}` }]);
    expect(pauses(projectId)).toHaveLength(1);
  });

  it('stays paused across a restart until the user writes', async () => {
    const { projectId, desk, thread, setStatus, reader } = await pausedProject();
    const idler = thread('Idler');
    setStatus(idler, 'waiting');
    const later = Date.now() + 16 * 60_000;

    const next = newRuntime(h, { wakeBudget: 2 });
    expect(next.recover()).toEqual([]);
    await next.whenIdle();
    expect(runs(desk.id)).toHaveLength(0);
    expect(runs(reader)).toHaveLength(0);
    expect(next.checkStalls(later)).toEqual([]);
    expect(pausedItems()).toHaveLength(1);
    expect(pauses(projectId)).toHaveLength(1);

    next.sendToDesk(projectId, 'Carry on.');
    await next.whenIdle();
    const deskBatch = batchOf(deskRequests()[0]!);
    expect(deskBatch).toContain('Summary: "Draft written"');
    expect(deskBatch).toContain('Carry on.');
    expect(runs(reader)).toHaveLength(1);

    // Resumed for good: the next restart finds no pause, and Desk's note runs Reader again.
    const third = newRuntime(h, { wakeBudget: 2 });
    third.recover();
    third.deliver(desk.id, reader, 'note', 'One more pass.');
    await third.whenIdle();
    expect(runs(reader)).toHaveLength(2);
    expect(pauses(projectId)).toHaveLength(1);
  });
});

describe('send results while paused', () => {
  it('tell the sender when the pause holds its message, and only then', async () => {
    const { desk, thread, setStatus, drained, writer } = await pausedProject();
    const held = 'automatic wakes are paused in this project; it reads this once the user resumes them';
    const busy = thread('Busy');
    setStatus(busy, 'running');
    const asker = thread('Asker');
    setStatus(asker, 'waiting');

    // Desk would run for a thread's update: the pause holds it.
    const u = rt.send({ from: busy, to: desk.id, kind: 'update', text: 'Halfway.' });
    expect(u.note).toBe(`Sent update #${u.id} to Desk (${held}).`);
    // A running thread reads a note at its next step, paused or not.
    const n = rt.send({ from: desk.id, to: busy, kind: 'note', text: 'Carry on.' });
    expect(n.note).toBe(`Sent note #${n.id} to "Busy" (running: it sees this at its next step).`);
    // The answer to a waiting asker is held.
    const q = rt.send({ from: asker, to: busy, kind: 'question', text: 'Which port?' });
    drained(busy, q.id);
    const a = rt.send({ from: busy, to: 'Asker', kind: 'note', text: '8080.' });
    expect(a).toEqual({ id: expect.any(Number), kind: 'answer', replyTo: q.id, note: `Sent #${a.id} to "Asker" as the answer to its question #${q.id} (${held}).` });
    // A held revision does not claim the thread was reopened.
    const r = rt.send({ from: desk.id, to: writer, kind: 'revision', text: 'Add sources.' });
    expect(r.note).toBe(`Sent revision 1 to "Writer" (${held}).`);

    await rt.whenIdle();
    expect(runs(desk.id)).toHaveLength(0);
    expect(runs(asker)).toHaveLength(0);
    expect(runs(writer)).toHaveLength(1);
  });
});
