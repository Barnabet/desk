import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { z } from 'zod';
import { call, hang, text, tools, type ChatRequest, type FakeReply } from '@desk/fake-model';
import { ConflictError } from '../errors';
import { getAgent, getDeskAgent, listApprovals, type AgentRow } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';
import { completeTool } from '../tools/thread';
import { defineTool, type Tool } from '../tools/types';
import type { RuntimeOptions } from './runtime';
import { toolsForRole } from './toolsets';

let h: Harness;
/** Runs inside a thread's tool phase when it calls `act`: a stop, a steer, a note or an archive landing at that moment. */
let during: (agentId: string) => void = () => {};
afterEach(async () => {
  during = () => {};
  await h?.cleanup();
});

const actTool = defineTool({
  name: 'act',
  description: "Test hook: runs the test's `during` callback.",
  input: z.object({}),
  async execute(_input, ctx) {
    during(ctx.agentId);
    return 'acted';
  },
});
const withAct = (a: AgentRow): Tool[] => (a.role === 'thread' ? [...toolsForRole(a), actTool] : toolsForRole(a));

const systemOf = (req: ChatRequest) => String(req.messages[0]?.content ?? '');
const isDesk = (req: ChatRequest) => systemOf(req).startsWith('You are Desk');
/** The thread a request comes from, by the title in its system prompt. */
const titleOf = (req: ChatRequest) => /## Your assignment: (.+)/.exec(systemOf(req))?.[1];
/** Model replies already in the conversation: 0 on a thread's first call. */
const turns = (req: ChatRequest) => req.messages.filter((m) => m.role === 'assistant').length;
const lastText = (req: ChatRequest) => String(req.messages.at(-1)?.content ?? '');
const threadRequests = (title: string) => h.fake.requests.filter((r) => titleOf(r) === title);

type Scripts = Record<string, (req: ChatRequest) => FakeReply>;

/** Desk answers "noted" to everything; each thread follows the script under its title. */
async function setup(threads: Scripts, extra: Partial<RuntimeOptions> = {}) {
  h = await createHarness({ script: (req) => (isDesk(req) ? text('noted') : (threads[titleOf(req) ?? '']?.(req) ?? text('(no script)'))) });
  const rt = newRuntime(h, { toolsFor: withAct, ...extra });
  const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { thread_model: FAKE_MODEL.id } });
  const desk = getDeskAgent(h.store.db, projectId)!;
  const thread = (title: string) => rt.createThread(projectId, { title, brief: `Do the ${title} part`, workspacePath: join(h.dir, title) });
  /** Starts a thread the way spawn_thread does. */
  const begin = (id: string) => rt.sendAgentMessage(desk.id, id, 'note', 'Begin your assignment.');
  return { rt, projectId, desk, thread, begin };
}

const runs = (agentId: string) => h.store.list({ agentId, types: ['run.started'] });
const status = (agentId: string) => getAgent(h.store.db, agentId)?.status;
/** Texts of the `kind` notices a thread sent its Desk, oldest first. */
const notices = (deskId: string, from: string, kind: string) =>
  h.store
    .list({ agentId: deskId, types: ['message.agent'] })
    .flatMap((e) => (e.type === 'message.agent' && e.payload.from_agent_id === from && e.payload.kind === kind ? [e.payload.text] : []));
const until = async (done: () => boolean) => {
  while (!done()) await new Promise((r) => setTimeout(r, 5));
};

describe('wakes in the runtime', () => {
  it('a Desk note to a done thread starts no run, not even after a restart', async () => {
    const { rt, desk, thread, begin } = await setup({ Writer: () => tools(call('complete', { summary: 'Draft written' })) });
    const t = thread('Writer');
    begin(t);
    await rt.whenIdle();
    expect(status(t)).toBe('done');
    const requests = h.fake.requests.length;

    rt.sendAgentMessage(desk.id, t, 'note', 'Also cover the footer.');
    await rt.whenIdle();
    expect(status(t)).toBe('done');
    expect(h.fake.requests.length).toBe(requests);

    const next = newRuntime(h, { toolsFor: withAct });
    expect(next.recover()).toEqual([]);
    await next.whenIdle();
    expect(h.fake.requests.length).toBe(requests);
    expect(runs(t)).toHaveLength(1);
  });

  it('a revision still reopens a done thread', async () => {
    const { rt, desk, thread, begin } = await setup({ Draft: (req) => tools(call('complete', { summary: turns(req) === 0 ? 'v1' : 'v2' })) });
    const t = thread('Draft');
    begin(t);
    await rt.whenIdle();
    rt.sendAgentMessage(desk.id, t, 'revision', 'Add sources.');
    await rt.whenIdle();
    expect(runs(t)).toHaveLength(2);
    expect(getAgent(h.store.db, t)).toMatchObject({ status: 'done', result_summary: 'v2' });
  });

  it('a thread stopped with a note pending stays stopped through afterRun and a restart; a user message resumes it', async () => {
    const { rt, desk, thread, begin } = await setup({ Waiter: (req) => (lastText(req).includes('Resume please.') ? text('Resumed.') : hang()) });
    const t = thread('Waiter');
    begin(t);
    await until(() => threadRequests('Waiter').length === 1);
    rt.sendAgentMessage(desk.id, t, 'note', 'Extra context.');
    rt.stop(t);
    await rt.whenIdle();
    expect(status(t)).toBe('cancelled');

    const next = newRuntime(h, { toolsFor: withAct });
    expect(next.recover()).not.toContain(t);
    await next.whenIdle();
    expect(status(t)).toBe('cancelled');
    expect(runs(t)).toHaveLength(1);

    next.sendMessage(t, 'Resume please.');
    await next.whenIdle();
    expect(status(t)).toBe('idle');
    const batch = lastText(threadRequests('Waiter').at(-1)!);
    expect(batch).toContain('Extra context.');
    expect(batch).toContain('Resume please.');
  });

  it.each(['complete', 'wait_for_reply'] as const)(
    'a stop during a %s step ends the run cancelled, and only a user message sent after it resumes the thread',
    async (yieldTool) => {
      const { rt, desk, thread, begin } = await setup({
        Stoppable: (req) =>
          turns(req) === 0 ? tools(call('act'), call(yieldTool, yieldTool === 'complete' ? { summary: 'Finished early' } : {})) : text('Going on.'),
      });
      const t = thread('Stoppable');
      // The user writes to the running thread, then presses Stop, both before its next step.
      during = (id) => {
        rt.sendMessage(id, 'Actually, use the v2 API.');
        rt.stop(id);
      };
      begin(t);
      await rt.whenIdle();
      expect(status(t)).toBe('cancelled');
      expect(notices(desk.id, t, 'cancelled')).toHaveLength(1);
      if (yieldTool === 'complete') expect(getAgent(h.store.db, t)?.result_summary).toBe('Finished early');

      // Neither a later Desk note, the stale user message, nor a restart runs it again.
      rt.sendAgentMessage(desk.id, t, 'note', 'Late note.');
      await rt.whenIdle();
      const next = newRuntime(h, { toolsFor: withAct });
      expect(next.recover()).not.toContain(t);
      await next.whenIdle();
      expect(status(t)).toBe('cancelled');
      expect(runs(t)).toHaveLength(1);

      next.sendMessage(t, 'Go on.');
      await next.whenIdle();
      expect(runs(t)).toHaveLength(2);
      const batch = lastText(threadRequests('Stoppable').at(-1)!);
      for (const said of ['Actually, use the v2 API.', 'Late note.', 'Go on.']) expect(batch).toContain(said);
    },
  );

  it('a stopped Desk still receives completed, and reads it when the user writes', async () => {
    const { rt, projectId, desk, thread, begin } = await setup({ Ship: () => tools(call('complete', { summary: 'Shipped' })) });
    rt.stop(desk.id);
    const t = thread('Ship');
    begin(t);
    await rt.whenIdle();
    expect(status(desk.id)).toBe('cancelled');
    expect(notices(desk.id, t, 'completed')).toEqual(['Summary: Shipped']);
    expect(runs(desk.id)).toHaveLength(0);

    rt.sendToDesk(projectId, 'What happened?');
    await rt.whenIdle();
    expect(runs(desk.id)).toHaveLength(1);
    const batch = lastText(h.fake.requests.filter(isDesk).at(-1)!);
    expect(batch).toContain('Summary: Shipped');
    expect(batch).toContain('What happened?');
  });
});

describe('archives', () => {
  it("never runs an archived thread, and refuses the user's messages to archived threads and projects", async () => {
    const { rt, projectId, desk, thread, begin } = await setup({ Old: () => tools(call('complete', { summary: 'Done' })) });
    const old = thread('Old');
    begin(old);
    await rt.whenIdle();
    await rt.archiveThread(old);
    rt.sendAgentMessage(desk.id, old, 'note', 'Anything else?');
    await rt.whenIdle();
    expect(runs(old)).toHaveLength(1);
    expect(() => rt.sendMessage(old, 'Hello?')).toThrow(ConflictError);

    const live = thread('Live');
    rt.archiveProject(projectId);
    await rt.whenIdle();
    expect(() => rt.sendMessage(live, 'Hello?')).toThrow(ConflictError);
    expect(h.store.list({ agentId: live, types: ['message.user'] })).toEqual([]);
  });

  it('archiving a project with a waiting and a running thread schedules nothing afterwards', async () => {
    const { rt, projectId, thread, begin } = await setup({
      Waiter: () => tools(call('wait_for_reply', {})),
      Runner: () => tools(call('act'), call('complete', { summary: 'Finished' })),
    });
    const waiter = thread('Waiter');
    begin(waiter);
    await rt.whenIdle();
    expect(status(waiter)).toBe('waiting');

    // The user archives the project while Runner is in its tool phase.
    during = () => rt.archiveProject(projectId);
    const runner = thread('Runner');
    begin(runner);
    await rt.whenIdle();

    const [archived] = h.store.list({ projectId, types: ['project.archived'] });
    const after = h.store.list({ projectId, after: archived!.id });
    expect(after.filter((e) => e.type === 'run.started')).toEqual([]);
    expect(after.filter((e) => e.type === 'agent.status_changed' && e.payload.status === 'queued')).toEqual([]);
    const cancels = h.store.list({ projectId, types: ['agent.status_changed'] }).filter((e) => e.type === 'agent.status_changed' && e.payload.status === 'cancelled');
    expect(cancels.length).toBeGreaterThan(0);
    expect(cancels.every((e) => e.id > archived!.id)).toBe(true);
    expect([status(waiter), status(runner)]).toEqual(['cancelled', 'cancelled']);
  });
});

describe('silent stops', () => {
  it("a stop by Desk on a done thread does not silence the user's later stop", async () => {
    const { rt, desk, thread, begin } = await setup({
      Pricing: (req) => (turns(req) === 0 ? tools(call('complete', { summary: 'Found pricing' })) : tools(call('act'))),
    });
    const t = thread('Pricing');
    begin(t);
    await rt.whenIdle();
    rt.stopAgent(t, { by: desk.id, reason: 'No longer needed' });
    expect(status(t)).toBe('done');

    // The user reopens it, then stops it while it runs.
    during = (id) => rt.stop(id);
    rt.sendMessage(t, 'Keep going: add the enterprise tier.');
    await rt.whenIdle();
    expect(status(t)).toBe('cancelled');
    const cancelled = notices(desk.id, t, 'cancelled');
    expect(cancelled).toHaveLength(1);
    expect(cancelled[0]).toMatch(/Stopped by the user\.$/);
  });
});
