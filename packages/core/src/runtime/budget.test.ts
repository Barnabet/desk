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
