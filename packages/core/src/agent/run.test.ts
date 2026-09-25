import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { z } from 'zod';
import { call, error, hang, text, tools } from '@desk/fake-model';
import type { EphemeralEvent, EventInput } from '@desk/protocol';
import { getAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, noSleep, seedThread, type Harness } from '../testing/harness';
import { fileTools } from '../tools/fs';
import { JobManager } from '../tools/jobs';
import { buildToolContext } from './context';

const jobs = new JobManager();
import { completeTool } from '../tools/thread';
import { defineTool } from '../tools/types';
import { threadSystemPrompt } from './prompts';
import { runAgent, SHUTDOWN_REASON, type RunDeps } from './run';
import type { ModelAdapter } from '../model/types';

let h: Harness;
afterEach(async () => h?.cleanup());

const deps = (extra: Partial<RunDeps> = {}): RunDeps => ({
  store: h.store,
  adapter: h.adapter,
  tools: [...fileTools, completeTool],
  systemPrompt: (agent, project) => threadSystemPrompt({ db: h.store.db, agent, project, libraryDir: h.dir }),
  maxSteps: 20,
  retry: noSleep,
  gate: () => ({ action: 'auto', delegateToDesk: false, reason: 'test' }),
  toolContext: (a, runId, toolCallId, signal) => buildToolContext(a, runId, toolCallId, signal, { sandboxEnabled: false, jobs, services: newRuntime(h).services }),
  ...extra,
});
const say = (agentId: string, projectId: string, text: string) =>
  h.store.append({ project_id: projectId, agent_id: agentId, type: 'message.user', payload: { text } });
const types = (agentId: string) => h.store.list({ agentId }).map((e) => e.type);

describe('runAgent', () => {
  it('handles a text-only reply', async () => {
    h = await createHarness({ script: [text('Hello!', { usage: { prompt_tokens: 50, completion_tokens: 5 } })] });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'hi');
    const outcome = await runAgent(deps(), agentId, new AbortController().signal);
    expect(outcome).toEqual({ reason: 'no_tool_calls', status: 'idle' });
    expect(types(agentId)).toEqual([
      'agent.created', 'message.user', 'run.started', 'agent.status_changed', 'inbox.drained',
      'assistant.message', 'usage', 'run.finished', 'agent.status_changed',
    ]);
    expect(getAgent(h.store.db, agentId)?.status).toBe('idle');
    const req = h.fake.requests[0]!;
    expect(req.messages[0]).toMatchObject({ role: 'system', content: expect.stringContaining('Do the test task') });
    expect(req.messages[1]).toEqual({ role: 'user', content: 'hi' });
  });

  it('executes tools and yields on complete', async () => {
    h = await createHarness({
      script: [
        tools(call('write_file', { path: 'out.txt', content: 'result' })),
        tools(call('complete', { summary: 'Wrote out.txt' })),
      ],
    });
    const { agentId, projectId, workspace } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'go');
    const outcome = await runAgent(deps(), agentId, new AbortController().signal);
    expect(outcome).toEqual({ reason: 'yielded', status: 'done' });
    expect(await readFile(join(workspace, 'out.txt'), 'utf8')).toBe('result');
    const results = h.store.list({ agentId, types: ['tool.result'] });
    expect(results.map((r) => r.type === 'tool.result' && r.payload.status)).toEqual(['ok', 'ok']);
    const finished = h.store.list({ agentId, types: ['agent.status_changed'] }).at(-1);
    expect(finished?.type === 'agent.status_changed' && finished.payload).toEqual({ status: 'done', reason: 'Wrote out.txt' });
  });

  it('runs parallel tool calls and sends results in call order', async () => {
    h = await createHarness({
      script: [tools(call('write_file', { path: 'a.txt', content: 'A' }, 'c1'), call('write_file', { path: 'b.txt', content: 'B' }, 'c2')), text('both written')],
    });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'go');
    await runAgent(deps(), agentId, new AbortController().signal);
    const second = h.fake.requests[1]!.messages;
    expect(second.slice(-3).map((m) => [m.role, m.tool_call_id])).toEqual([
      ['assistant', undefined],
      ['tool', 'c1'],
      ['tool', 'c2'],
    ]);
  });

  it('injects a message that arrives mid-run at the next step', async () => {
    h = await createHarness({ script: [tools(call('poke', {})), text('noted')] });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    const poke = defineTool({
      name: 'poke',
      description: 'p',
      input: z.object({}),
      async execute() {
        say(agentId, projectId, 'change of plan');
        return 'poked';
      },
    });
    say(agentId, projectId, 'go');
    await runAgent(deps({ tools: [poke] }), agentId, new AbortController().signal);
    const second = h.fake.requests[1]!.messages;
    expect(second.at(-1)).toEqual({ role: 'user', content: 'change of plan' });
    expect(second.at(-2)).toMatchObject({ role: 'tool', content: 'poked' });
  });

  it('stops at the step limit', async () => {
    h = await createHarness({ script: () => tools(call('list_dir', {})) });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'loop');
    expect(await runAgent(deps({ maxSteps: 3 }), agentId, new AbortController().signal)).toEqual({ reason: 'max_steps', status: 'idle' });
    expect(h.fake.requests).toHaveLength(3);
  });

  it('stops when aborted', async () => {
    h = await createHarness({ script: [hang()] });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'go');
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 50);
    expect(await runAgent(deps(), agentId, controller.signal)).toEqual({ reason: 'stopped', status: 'cancelled' });
  });

  it('retries rate limits then succeeds', async () => {
    h = await createHarness({ script: [error(429, 'rate_limit_error'), text('ok')] });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'go');
    expect((await runAgent(deps(), agentId, new AbortController().signal)).reason).toBe('no_tool_calls');
    expect(h.fake.requests).toHaveLength(2);
  });

  it('fails on fatal model errors', async () => {
    h = await createHarness({ script: [error(401, 'authentication_error', 'bad key')] });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'go');
    expect(await runAgent(deps(), agentId, new AbortController().signal)).toEqual({ reason: 'error', status: 'failed' });
    const fin = h.store.list({ agentId, types: ['run.finished'] })[0];
    expect(fin?.type === 'run.finished' && fin.payload.detail).toContain('bad key');
  });

  it('publishes streamed text as ephemeral deltas', async () => {
    h = await createHarness({ script: [text('a fairly long streamed reply')] });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    const deltas: EphemeralEvent[] = [];
    h.store.subscribe((i) => i.kind === 'ephemeral' && deltas.push(i.event));
    say(agentId, projectId, 'go');
    await runAgent(deps(), agentId, new AbortController().signal);
    expect(deltas.map((d) => (d.type === 'assistant.delta' ? d.payload.text : '')).join('')).toBe('a fairly long streamed reply');
  });
});

describe('system prompt snapshot', () => {
  it('builds the system prompt once per run, not per step', async () => {
    h = await createHarness({ script: [tools(call('list_dir', {})), tools(call('list_dir', {})), text('done')] });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    let builds = 0;
    say(agentId, projectId, 'go');
    await runAgent(deps({ systemPrompt: () => `prompt ${++builds}` }), agentId, new AbortController().signal);
    expect(builds).toBe(1);
    expect(h.fake.requests.map((r) => r.messages[0]!.content)).toEqual(['prompt 1', 'prompt 1', 'prompt 1']);
  });
});

describe('runAgent stopped in the middle of a step', () => {
  /** A tool that aborts the run's signal while the step's other calls run: a stop landing in the tool phase. */
  const stopper = (controller: AbortController, reason?: unknown) =>
    defineTool({
      name: 'stop_me',
      description: 'Aborts the run',
      input: z.object({}),
      async execute() {
        controller.abort(reason);
        return 'stopping';
      },
    });

  it('ends cancelled instead of honouring the yield, and keeps the recorded result', async () => {
    h = await createHarness({ script: () => tools(call('stop_me', {}), call('complete', { summary: 'Done early' })) });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'go');
    const controller = new AbortController();
    const outcome = await runAgent(deps({ tools: [stopper(controller), completeTool] }), agentId, controller.signal);
    expect(outcome).toEqual({ reason: 'stopped', status: 'cancelled' });
    expect(getAgent(h.store.db, agentId)).toMatchObject({ status: 'cancelled', result_summary: 'Done early' });
  });

  it('still honours the yield when the daemon shuts down', async () => {
    h = await createHarness({ script: () => tools(call('stop_me', {}), call('complete', { summary: 'Done' })) });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'go');
    const controller = new AbortController();
    const outcome = await runAgent(deps({ tools: [stopper(controller, SHUTDOWN_REASON), completeTool] }), agentId, controller.signal);
    expect(outcome).toEqual({ reason: 'yielded', status: 'done' });
  });

  it('denies the calls that would have asked for approval, so the conversation stays well formed', async () => {
    h = await createHarness({ script: () => tools(call('stop_me', {}, 'c1'), call('write_file', { path: 'x.txt', content: 'x' }, 'c2')) });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'go');
    const controller = new AbortController();
    const gate: RunDeps['gate'] = (tool) =>
      tool.name === 'write_file' ? { action: 'ask', delegateToDesk: false, reason: 'writes need approval' } : { action: 'auto', delegateToDesk: false, reason: 'test' };
    const outcome = await runAgent(deps({ tools: [stopper(controller), ...fileTools], gate }), agentId, controller.signal);
    expect(outcome).toEqual({ reason: 'stopped', status: 'cancelled' });
    expect(h.store.list({ agentId, types: ['approval.requested'] })).toEqual([]);
    const results = h.store.list({ agentId, types: ['tool.result'] }).map((e) => (e.type === 'tool.result' ? [e.payload.tool_call_id, e.payload.status, e.payload.content] : []));
    expect(results).toEqual([
      ['c1', 'ok', 'stopping'],
      ['c2', 'denied', 'Denied: the agent was stopped'],
    ]);
  });

  it('ends cancelled when the stop lands as the model replies without tools', async () => {
    h = await createHarness({ script: () => text('All done.') });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'go');
    const controller = new AbortController();
    // The stop lands between the model's reply and the end of the step.
    const adapter: ModelAdapter = {
      complete: (req, opts) =>
        h.adapter.complete(req, opts).then((result) => {
          controller.abort();
          return result;
        }),
    };
    expect(await runAgent(deps({ adapter }), agentId, controller.signal)).toEqual({ reason: 'stopped', status: 'cancelled' });
  });

  it('runs none of the calls when the stop lands as the model replies with them', async () => {
    h = await createHarness({ script: () => tools(call('push', {}, 'c1')) });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'go');
    let pushed = false;
    const push = defineTool({
      name: 'push',
      description: 'Push (ignores the signal)',
      input: z.object({}),
      async execute() {
        pushed = true;
        return 'pushed';
      },
    });
    const controller = new AbortController();
    const adapter: ModelAdapter = {
      complete: (req, opts) =>
        h.adapter.complete(req, opts).then((result) => {
          controller.abort();
          return result;
        }),
    };
    expect(await runAgent(deps({ adapter, tools: [push] }), agentId, controller.signal)).toEqual({ reason: 'stopped', status: 'cancelled' });
    expect(pushed).toBe(false);
    const results = h.store.list({ agentId, types: ['tool.call', 'tool.result'] }).map((e) => [e.type, e.type === 'tool.result' ? e.payload.status : '']);
    expect(results).toEqual([
      ['tool.call', ''],
      ['tool.result', 'denied'],
    ]);
  });
});

describe('runAgent in answer mode', () => {
  /** An `ending` that records what the run ended with (the runtime turns it into the answer or the closure). */
  function recorder() {
    const endings: string[] = [];
    const ending = (text: string, auto?: boolean): EventInput[] => {
      endings.push(`${auto ? 'closure' : 'answer'}: ${text}`);
      return [];
    };
    return { endings, ending };
  }

  /** A done thread with the user's question and a later message pending; the run answers the question. */
  async function pendingQuestion() {
    const { agentId, projectId, workspace } = await seedThread(h.store, h.dir);
    await writeFile(join(workspace, 'notes.md'), 'We charge per seat.');
    h.store.append({ project_id: projectId, agent_id: agentId, type: 'agent.status_changed', payload: { status: 'done' } });
    const [q] = say(agentId, projectId, 'How do we charge?');
    const [later] = say(agentId, projectId, 'Also: which currency?');
    const { endings, ending } = recorder();
    const answer: NonNullable<RunDeps['answer']> = { question: q!.id, asker: 'user', upTo: q!.id, ending };
    return { agentId, projectId, q: q!.id, later: later!.id, answer, endings };
  }

  it('starts with its only drain in one append, keeps the status, and ends with its text in one append', async () => {
    h = await createHarness({ script: (req) => (req.messages.at(-1)?.role === 'tool' ? text('Per seat.') : tools(call('read_file', { path: 'notes.md' }))) });
    const { agentId, q, later, answer, endings } = await pendingQuestion();
    const outcome = await runAgent(deps({ maxSteps: 4, answer }), agentId, new AbortController().signal);
    expect(outcome).toEqual({ reason: 'no_tool_calls', status: 'done', text: 'Per seat.' });
    expect(endings).toEqual(['answer: Per seat.']);
    const run = h.store.list({ agentId, after: later });
    expect(run.map((e) => e.type)).toEqual([
      'run.started', 'inbox.drained', 'assistant.message', 'usage', 'tool.call', 'tool.result', 'assistant.message', 'usage', 'run.finished',
    ]);
    expect(run[0]!.payload).toMatchObject({ answering: q });
    expect(run[1]!.payload).toMatchObject({ up_to: q });
    // The later message stays pending: it is decided after the run.
    expect(getAgent(h.store.db, agentId)).toMatchObject({ status: 'done', inbox_cursor: q });
    expect(String(h.fake.requests[0]!.messages.at(-1)?.content)).toMatch(/^How do we charge\?\n\n\[Desk runtime — answer mode\] You were woken only to answer the user's question above\./);
  });

  it('passes an empty reply to its ending as the text (the runtime closes the question)', async () => {
    h = await createHarness({ script: [text('')] });
    const { agentId, answer, endings } = await pendingQuestion();
    expect(await runAgent(deps({ maxSteps: 4, answer }), agentId, new AbortController().signal)).toEqual({ reason: 'no_tool_calls', status: 'done', text: '' });
    expect(endings).toEqual(['answer: ']);
  });

  it.each([
    { on: 'the step limit', reply: 'read', abort: undefined, reason: 'max_steps', ending: 'closure: did not finish answering. Ask again or use read_thread' },
    { on: 'a model error', reply: 'error', abort: undefined, reason: 'error', ending: /^closure: could not answer: .*bad key$/ },
    { on: 'a stop', reply: 'hang', abort: 'stop', reason: 'stopped', ending: 'closure: was stopped before answering' },
    { on: 'a shutdown', reply: 'hang', abort: SHUTDOWN_REASON, reason: 'error', ending: 'closure: could not answer: Desk was restarting. Ask again if you still need to know' },
  ])('closes the question on $on, without a status change', async ({ reply, abort, reason, ending }) => {
    const controller = new AbortController();
    h = await createHarness({
      script: () => {
        if (reply === 'read') return tools(call('read_file', { path: 'notes.md' }));
        if (reply === 'error') return error(401, 'authentication_error', 'bad key');
        // The stop lands while the model call is in flight.
        controller.abort(abort === 'stop' ? undefined : abort);
        return hang();
      },
    });
    const { agentId, answer, endings } = await pendingQuestion();
    expect(await runAgent(deps({ maxSteps: 4, answer }), agentId, controller.signal)).toEqual({ reason, status: 'done' });
    expect(endings).toEqual([typeof ending === 'string' ? ending : expect.stringMatching(ending)]);
    expect(h.store.list({ agentId, types: ['agent.status_changed'] })).toHaveLength(1);
    expect(h.store.list({ agentId, types: ['inbox.drained'] })).toHaveLength(1);
    if (reply === 'read') expect(h.fake.requests).toHaveLength(4);
  });

  it('closes the question when the workspace is missing', async () => {
    h = await createHarness();
    const { projectId } = await seedThread(h.store, h.dir);
    const agentId = 'no-workspace';
    h.store.append({
      project_id: projectId,
      agent_id: agentId,
      type: 'agent.created',
      payload: { role: 'thread', model: FAKE_MODEL.id, title: 'T', brief: 'b', workspace_path: null, parent_id: null },
    });
    const [q] = say(agentId, projectId, 'How do we charge?');
    const { endings, ending } = recorder();
    const outcome = await runAgent(deps({ maxSteps: 4, answer: { question: q!.id, asker: 'user', upTo: q!.id, ending } }), agentId, new AbortController().signal);
    expect(outcome.reason).toBe('error');
    expect(endings).toEqual(['closure: could not answer: its workspace is missing']);
    expect(types(agentId)).toEqual(['agent.created', 'message.user', 'run.started', 'inbox.drained', 'run.finished']);
  });

  it("ends as soon as a message tool recorded the answer to an agent's question", async () => {
    h = await createHarness({ script: () => tools(call('reply', {}, 'c1')) });
    const { agentId, projectId, q, answer, endings } = await pendingQuestion();
    const replyTool = defineTool({
      name: 'reply',
      description: 'Sends the answer to the asker',
      input: z.object({}),
      async execute() {
        h.store.append({
          project_id: projectId,
          agent_id: 'asker',
          type: 'message.agent',
          payload: { from_agent_id: agentId, from_label: 'thread "Test thread" (x)', kind: 'answer', text: 'Per seat.', reply_to: q },
        });
        return 'Sent.';
      },
    });
    const outcome = await runAgent(deps({ maxSteps: 4, tools: [replyTool], answer: { ...answer, asker: 'asker' } }), agentId, new AbortController().signal);
    expect(outcome).toEqual({ reason: 'yielded', status: 'done' });
    expect(endings).toEqual([]);
    expect(h.fake.requests).toHaveLength(1);
  });
});
