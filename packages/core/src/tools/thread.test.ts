import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type ChatRequest, type FakeReply } from '@desk/fake-model';
import type { AgentStatus, EventOf, ProjectSettingsPatch } from '@desk/protocol';
import { buildToolContext } from '../agent/context';
import type { Runtime } from '../runtime/runtime';
import { deskToolsFor, threadToolsFor } from '../runtime/toolsets';
import { getAgent, getDeskAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';
import { listThreadsTool } from './desk';
import { toToolSpecs } from './registry';
import { messageDeskTool, threadMessageThreadTool, threadReadThreadTool, waitForReplyTool } from './thread';
import type { Tool } from './types';

let h: Harness;
let rt: Runtime;
afterEach(async () => h?.cleanup());

const systemOf = (req: ChatRequest) => String(req.messages[0]?.content ?? '');
const isDesk = (req: ChatRequest) => systemOf(req).startsWith('You are Desk');
const titleOf = (req: ChatRequest) => /## Your assignment: (.+)/.exec(systemOf(req))?.[1];
/** Model replies already in the conversation: 0 on an agent's first call. */
const turns = (req: ChatRequest) => req.messages.filter((m) => m.role === 'assistant').length;
const deskRequests = () => h.fake.requests.filter(isDesk);

/** Design spec §2.1, exact. */
const THREAD_MESSAGE_THREAD =
  "Send a message to another thread of this project (thread_id: its id or exact title). `question`: ask about its own work (an interface, file, format or finding it owns); it may be woken just to answer you, so ask only what its brief, its result or read_thread don't tell you. `note`: tell it something that changes its work. If that thread asked you a question, your next message to it is recorded as the answer. At most 4000 characters: publish long content with library_publish and send the path.";
const MESSAGE_DESK =
  'Send a message to Desk, the project coordinator: a progress `update`, a `question`, or a `blocker`. If Desk asked you a question, your next update is recorded as the answer. After a question or blocker, call wait_for_reply unless you can keep working meanwhile. At most 4000 characters.';
const WAIT_FOR_REPLY =
  'Pause until an answer to a question you asked arrives, or Desk or the user writes to you. Notes from other threads do not end the wait.';

/**
 * A project where every agent answers "noted" unless `script` says otherwise. Threads are put in a status directly: a
 * `running` thread has no job, so what it is sent waits for a next step that never comes.
 */
async function setup(script: (req: ChatRequest) => FakeReply = () => text('noted'), settings: ProjectSettingsPatch = {}) {
  h = await createHarness({ script });
  rt = newRuntime(h);
  const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id, ...settings } });
  const desk = getDeskAgent(h.store.db, projectId)!;
  let n = 0;
  const thread = (title: string, status?: AgentStatus) => {
    const id = rt.createThread(projectId, { title, brief: `Do the ${title} part`, workspacePath: join(h.dir, `ws-${++n}`) });
    if (status) h.store.append({ project_id: projectId, agent_id: id, type: 'agent.status_changed', payload: { status } });
    return id;
  };
  /** The context of a tool call by `agentId`, as its runs build it (tool call id `tc`). */
  const ctxOf = (agentId: string) =>
    buildToolContext(getAgent(h.store.db, agentId)!, 'run', 'tc', new AbortController().signal, { sandboxEnabled: false, jobs: rt.jobs, services: rt.services });
  return { projectId, desk, thread, ctxOf };
}

describe('the thread toolset (design spec §2.1, §2.3)', () => {
  it('gives threads the sibling tools: a gated message_thread and a summary-only read_thread', async () => {
    const { desk, thread } = await setup();
    const own = threadToolsFor(getAgent(h.store.db, thread('Auth API'))!);
    const tool = (name: string) => own.find((x) => x.name === name)!;
    const params = (x: Tool) => Object.keys((toToolSpecs([x])[0]!.function.parameters as { properties: Record<string, unknown> }).properties);
    expect(own.map((x) => x.name)).toEqual(expect.arrayContaining(['list_threads', 'read_thread', 'message_thread', 'message_desk', 'wait_for_reply', 'complete']));
    expect(tool('list_threads')).toBe(listThreadsTool);
    expect(params(tool('read_thread'))).toEqual(['thread_id']);
    expect(tool('read_thread').description).toBe('Inspect another thread of this project: status, brief, result, artifacts, branch and its last message.');
    expect(params(tool('message_thread'))).toEqual(['thread_id', 'kind', 'text']);
    expect(tool('message_thread').description).toBe(THREAD_MESSAGE_THREAD);
    expect(tool('message_thread').gate).toMatchObject({ unmatched: 'auto' });
    expect(tool('message_thread').gate!.subject({ thread_id: 'x', kind: 'note', text: 'y' }, {})).toEqual({});
    expect(tool('message_desk').description).toBe(MESSAGE_DESK);
    expect(tool('wait_for_reply').description).toBe(WAIT_FOR_REPLY);
    expect(deskToolsFor(desk).find((x) => x.name === 'message_thread')!.gate).toBeUndefined();
    expect(params(deskToolsFor(desk).find((x) => x.name === 'read_thread')!)).toEqual(['thread_id', 'mode', 'since']);
  });
});

describe('message_thread and read_thread for threads', () => {
  it('sends to another thread by id or title, never to Desk or itself', async () => {
    const { desk, thread, ctxOf } = await setup();
    const a = thread('Auth API', 'running');
    const f = thread('Frontend', 'running');
    const send = (thread_id: string, kind: 'note' | 'question' = 'note') =>
      threadMessageThreadTool.execute({ thread_id, kind, text: 'Renamed userId to user_id.' }, ctxOf(a));
    const out = await send('Frontend');
    const [n] = h.store.list({ agentId: f, types: ['message.agent'] }) as Array<EventOf<'message.agent'>>;
    expect(out).toBe(`Sent note #${n!.id} to "Frontend" (running: it sees this at its next step).`);
    expect(n!.payload).toMatchObject({ from_agent_id: a, kind: 'note', tool_call_id: 'tc' });
    await expect(send(desk.id)).rejects.toThrow('Use message_desk to reach Desk.');
    await expect(send('Desk', 'question')).rejects.toThrow('Use message_desk to reach Desk.');
    await expect(send('desk')).rejects.toThrow('Use message_desk to reach Desk.');
    await expect(send(a)).rejects.toThrow('That is you.');
    await expect(send('Nobody')).rejects.toThrow('Unknown thread: Nobody');
    expect(h.store.list({ agentId: desk.id, types: ['message.agent'] })).toEqual([]);
  });

  it("reads another thread's summary by title, but not Desk", async () => {
    const { projectId, desk, thread, ctxOf } = await setup();
    const a = thread('Auth API');
    const f = thread('Frontend');
    h.store.append({ project_id: projectId, agent_id: f, type: 'agent.result', payload: { summary: 'Form built', artifacts: [] } });
    const out = String(await threadReadThreadTool.execute({ thread_id: 'Frontend' }, ctxOf(a)));
    expect(out).toContain(`Thread ${f} "Frontend"`);
    expect(out).toContain('Result:\n> Form built');
    await expect(threadReadThreadTool.execute({ thread_id: desk.id }, ctxOf(a))).rejects.toThrow(`Unknown thread: ${desk.id}`);
    expect(String(await listThreadsTool.execute({}, ctxOf(a)))).toContain(`- ${f} "Frontend" [idle]`);
  });
});

describe('wait_for_reply (design spec §2.1)', () => {
  it('names what the thread waits on, from its open questions', async () => {
    const { desk, thread, ctxOf } = await setup();
    const a = thread('Auth API', 'running');
    const f = thread('Frontend', 'running');
    const c = thread('Checkout', 'running');
    const reason = async () => {
      const out = await waitForReplyTool.execute({}, ctxOf(a));
      return typeof out === 'string' ? out : out.yield?.reason;
    };
    expect(await waitForReplyTool.execute({}, ctxOf(a))).toEqual({
      content: 'Waiting on Desk or the user.',
      yield: { status: 'waiting', reason: 'Waiting on Desk or the user' },
    });
    const qf = rt.send({ from: a, to: f, kind: 'question', text: 'Which token format?' });
    expect(await reason()).toBe('Waiting on "Frontend"');
    rt.send({ from: a, to: desk.id, kind: 'question', text: 'EU or US?' });
    expect(await reason()).toBe('Waiting on "Frontend" and Desk');
    const qc = rt.send({ from: a, to: c, kind: 'question', text: 'Which currency?' });
    expect(await reason()).toBe('Waiting on "Frontend", "Checkout" and Desk');
    rt.answer(qf.id, f, 'JWT.');
    rt.answer(qc.id, c, 'EUR.');
    expect(await reason()).toBe('Waiting on Desk');
    await rt.whenIdle();
  });
});

describe('message_desk', () => {
  it("stores a stopped Desk's question and blocker without running it, until the user writes to it", async () => {
    const { projectId, desk, thread, ctxOf } = await setup();
    rt.stop(desk.id);
    const t = thread('Research');
    const q = await messageDeskTool.execute({ kind: 'question', text: 'Which region?' }, ctxOf(t));
    const b = await messageDeskTool.execute({ kind: 'blocker', text: 'No API key.' }, ctxOf(t));
    const [qe, be] = h.store.list({ agentId: desk.id, types: ['message.agent'] }) as Array<EventOf<'message.agent'>>;
    expect(q).toBe(`Sent question #${qe!.id} to Desk (stopped by the user: it reads this when the user resumes it).`);
    expect(b).toBe(`Sent blocker #${be!.id} to Desk (stopped by the user: it reads this when the user resumes it).`);
    expect(qe!.payload).toMatchObject({ kind: 'question', tracked: true, tool_call_id: 'tc' });
    await rt.whenIdle();
    expect(deskRequests()).toHaveLength(0);

    rt.sendToDesk(projectId, 'Carry on.');
    await rt.whenIdle();
    expect(deskRequests()).toHaveLength(1);
    const batch = String(deskRequests()[0]!.messages.at(-1)!.content);
    expect(batch).toContain(
      `[message #${qe!.id} from thread "Research" (${t}) — question; they may be waiting on you: answer with message_thread to "Research"]\n> Which region?`,
    );
    expect(batch).toContain(`[message #${be!.id} from thread "Research" (${t}) — blocker]\n> No API key.`);
    expect(batch.endsWith('Carry on.')).toBe(true);
  });
});

describe('policy on messages between threads (design spec §5.5)', () => {
  it('lets a rule on message_thread deny what threads send, not what Desk sends', async () => {
    const { projectId, desk, thread } = await setup(
      (req) => {
        if (isDesk(req)) return turns(req) === 0 ? tools(call('message_thread', { thread_id: 'Pricing', text: 'Use EU prices.' }, 'desk-send')) : text('noted');
        if (titleOf(req) === 'Scout' && turns(req) === 0) return tools(call('message_thread', { thread_id: 'Pricing', text: 'EU prices, FYI.' }, 'scout-send'));
        return text('noted');
      },
      { policy: [{ tool: 'message_thread', action: 'deny' }] },
    );
    const pricing = thread('Pricing');
    const scout = thread('Scout');
    rt.deliver(desk.id, scout, 'start', 'Begin your assignment.');
    rt.sendToDesk(projectId, 'Tell Pricing to use EU prices.');
    await rt.whenIdle();
    const result = (id: string) =>
      h.store.list({ types: ['tool.result'] }).find((e): e is EventOf<'tool.result'> => e.type === 'tool.result' && e.payload.tool_call_id === id)!.payload;
    expect(result('scout-send')).toMatchObject({ status: 'denied', content: expect.stringContaining('Denied by policy.') });
    expect(result('desk-send')).toMatchObject({ status: 'ok', content: expect.stringMatching(/^Sent note #\d+ to "Pricing"/) });
    const senders = (h.store.list({ agentId: pricing, types: ['message.agent'] }) as Array<EventOf<'message.agent'>>).map((e) => e.payload.from_agent_id);
    expect(senders).toEqual([desk.id]);
  });
});
