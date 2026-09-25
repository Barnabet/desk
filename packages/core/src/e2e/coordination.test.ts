import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type ChatRequest, type FakeReply } from '@desk/fake-model';
import type { EventOf } from '@desk/protocol';
import { getPlan } from '../coordination/plan';
import { listArtifacts } from '../library/library';
import { activeMemory } from '../memory/memory';
import { getAgent, getDeskAgent, listThreads } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';

let h: Harness;
afterEach(async () => h?.cleanup());

const all = (req: ChatRequest) => req.messages.map((m) => (typeof m.content === 'string' ? m.content : '')).join('\n');
const called = (req: ChatRequest, name: string, pred: (args: any) => boolean = () => true) =>
  req.messages.some((m) => m.role === 'assistant' && (m.tool_calls ?? []).some((tc: any) => tc.function.name === name && pred(JSON.parse(tc.function.arguments))));
const threadId = (req: ChatRequest, title: string) => new RegExp(`thread ([0-9A-Z]{26}) "${title}"`).exec(all(req))?.[1];

/** Desk: a small state machine over its own conversation. */
function desk(req: ChatRequest): FakeReply {
  const convo = all(req);
  if (!called(req, 'spawn_thread')) {
    return tools(
      call('update_plan', { items: [{ id: 'p1', title: 'Part A', status: 'in_progress' }, { id: 'p2', title: 'Part B', status: 'in_progress' }] }),
      call('spawn_thread', { title: 'A', brief: 'Write part A to a.md and publish it.' }),
      call('spawn_thread', { title: 'B', brief: 'Write part B to b.md.' }),
    );
  }
  const a = threadId(req, 'A');
  const b = threadId(req, 'B');
  const calls = [];
  if (/ — question(;[^\]\n]*)?\]\n> Which region\?/.test(convo) && !called(req, 'message_thread', (x) => x.kind === 'note')) {
    calls.push(call('message_thread', { thread_id: a, kind: 'note', text: 'Use EU.' }));
  }
  const bDone = (convo.match(/"B" \([0-9A-Z]{26}\) — completed\]/g) ?? []).length;
  if (bDone >= 1 && !called(req, 'message_thread', (x) => x.kind === 'revision')) {
    calls.push(call('message_thread', { thread_id: b, kind: 'revision', text: 'Add a conclusion.' }));
  }
  const aDone = /"A" \([0-9A-Z]{26}\) — completed\]/.test(convo);
  if (aDone && bDone >= 2 && !called(req, 'write_file')) {
    calls.push(call('write_file', { path: 'combined.md', content: '# Combined\nA + B' }));
  } else if (called(req, 'write_file') && !called(req, 'report')) {
    calls.push(
      call('library_publish', { path: 'combined.md', title: 'Combined', kind: 'report', description: 'A and B merged' }),
      call('memory_write', { kind: 'decision', content: 'Region is EU' }),
      call('update_plan', { items: [{ id: 'p1', title: 'Part A', status: 'done' }, { id: 'p2', title: 'Part B', status: 'done' }] }),
      call('report', { headline: 'A and B are done', progress: '2/2', results: ['combined.md'] }),
    );
  } else if (called(req, 'report')) {
    return text('All done: combined.md is in the library.');
  }
  return calls.length ? tools(...calls) : tools(call('wait_for_threads', {}));
}

const threadScripts: Record<string, FakeReply[]> = {
  A: [
    tools(call('message_desk', { kind: 'question', text: 'Which region?' }), call('wait_for_reply', {})),
    tools(call('write_file', { path: 'a.md', content: 'Part A (EU)' })),
    tools(call('library_publish', { path: 'a.md', title: 'Part A', kind: 'report', description: 'A' })),
    tools(call('complete', { summary: 'Wrote part A for EU', artifacts: ['a.md'] })),
  ],
  B: [
    tools(call('write_file', { path: 'b.md', content: 'Part B' })),
    tools(call('complete', { summary: 'Wrote part B' })),
    tools(call('write_file', { path: 'b.md', content: 'Part B\n\nConclusion.' })),
    tools(call('complete', { summary: 'Wrote part B with conclusion' })),
  ],
};

describe('end-to-end coordination', () => {
  it('plans, dispatches, answers, reviews, assembles and reports', async () => {
    h = await createHarness({
      script: (req) => {
        const system = String(req.messages[0]?.content ?? '');
        if (system.startsWith('You are Desk')) return desk(req);
        const title = /## Your assignment: (\w+)/.exec(system)![1]!;
        return threadScripts[title]!.shift() ?? text('(nothing left)');
      },
    });
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'Demo', goal: 'Produce parts A and B', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id } });
    rt.sendToDesk(projectId, 'Please produce parts A and B.');
    await rt.whenIdle();

    const deskAgent = getDeskAgent(h.store.db, projectId)!;
    const threads = listThreads(h.store.db, projectId);
    const a = threads.find((t) => t.title === 'A');
    const b = threads.find((t) => t.title === 'B');
    expect(a).toMatchObject({ title: 'A', status: 'done', result_artifacts: ['a.md'], review_round: 0 });
    expect(b).toMatchObject({ title: 'B', status: 'done', result_summary: 'Wrote part B with conclusion', review_round: 1 });
    expect(getAgent(h.store.db, deskAgent.id)?.status).toBe('idle');

    const aTranscript = h.store.list({ agentId: a!.id, types: ['message.agent'] }).map((e) => (e.type === 'message.agent' ? e.payload.text : ''));
    expect(aTranscript).toContain('Use EU.');
    expect(listArtifacts(h.store.db, projectId).map((x) => x.path).sort()).toEqual(['a.md', 'combined.md']);
    expect(activeMemory(h.store.db, projectId).map((m) => m.content)).toEqual(['Region is EU']);
    expect(getPlan(h.store.db, projectId)?.items.map((i) => i.status)).toEqual(['done', 'done']);
    expect(h.store.list({ projectId, types: ['report'] })).toHaveLength(1);
    const last = h.store.list({ agentId: deskAgent.id, types: ['assistant.message'] }).at(-1);
    expect(last?.type === 'assistant.message' && last.payload.content).toBe('All done: combined.md is in the library.');
  });
});

describe('threads that talk to each other', () => {
  let projectId = '';
  const systemOf = (req: ChatRequest) => String(req.messages[0]?.content ?? '');
  const isDesk = (req: ChatRequest) => systemOf(req).startsWith('You are Desk');
  const titleOf = (req: ChatRequest) => /## Your assignment: (\w+)/.exec(systemOf(req))?.[1];
  const statusOf = (title: string) => listThreads(h.store.db, projectId).find((t) => t.title === title)?.status;
  /** One step that only looks around, a little later: how a script waits for another agent to get somewhere. */
  const later = (): FakeReply => ({ kind: 'reply', toolCalls: [{ name: 'list_threads', args: {} }], delayMs: 10 });
  const completed = (req: ChatRequest, title: string) => new RegExp(`"${title}" \\([0-9A-Z]{26}\\) — completed\\]`).test(all(req));
  /** Desk spawns A and B and waits in one step, then waits until both completed. */
  const deskScript = (req: ChatRequest): FakeReply => {
    if (!called(req, 'spawn_thread')) {
      return tools(
        call('spawn_thread', { title: 'A', brief: 'Build the login form. B builds the login API.' }),
        call('spawn_thread', { title: 'B', brief: 'Build the login API. A builds the login form.' }),
        call('wait_for_threads', {}),
      );
    }
    return completed(req, 'A') && completed(req, 'B') ? text('Both parts are done.') : tools(call('wait_for_threads', {}));
  };
  /** The result of the agent's first call to tool `name`. */
  const resultOf = (agentId: string, name: string) =>
    h.store.list({ agentId, types: ['tool.result'] }).find((e): e is EventOf<'tool.result'> => e.type === 'tool.result' && e.payload.name === name)?.payload.content;
  /** The first message of `kind` stored on the agent's stream. */
  const firstOf = (agentId: string, kind: string) =>
    h.store.list({ agentId, types: ['message.agent'] }).find((e): e is EventOf<'message.agent'> => e.type === 'message.agent' && e.payload.kind === kind)!;
  /** Runs a project whose Desk spawns A and B; `threads` answers both threads' model calls. */
  async function run(threads: (req: ChatRequest) => FakeReply) {
    h = await createHarness({ script: (req) => (isDesk(req) ? deskScript(req) : threads(req)) });
    const rt = newRuntime(h);
    projectId = rt.createProject({ name: 'Login', goal: 'Ship login', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id } });
    rt.sendToDesk(projectId, 'Build the login form and the login API.');
    await rt.whenIdle();
    const list = listThreads(h.store.db, projectId);
    return { a: list.find((t) => t.title === 'A')!, b: list.find((t) => t.title === 'B')!, deskAgent: getDeskAgent(h.store.db, projectId)! };
  }

  it('lets A ask B once B is done: B answers in an answer run and stays done', async () => {
    const { a, b, deskAgent } = await run((req) => {
      if (titleOf(req) === 'B') {
        // B's answer run: its batch ends with the runtime's answer-mode line.
        return String(req.messages.at(-1)?.content).includes('[Desk runtime — answer mode]')
          ? text('Tokens are JWTs signed with RS256.')
          : tools(call('complete', { summary: 'Login API done' }));
      }
      if (all(req).includes('— answer to your question #')) return tools(call('complete', { summary: 'Form sends the JWT' }));
      if (called(req, 'message_thread')) return tools(call('wait_for_reply', {}));
      if (statusOf('B') !== 'done') return later();
      return tools(call('message_thread', { thread_id: 'B', kind: 'question', text: 'How are tokens signed?' }), call('wait_for_reply', {}));
    });
    expect(a.status).toBe('done');
    expect(b).toMatchObject({ status: 'done', result_summary: 'Login API done' });
    const q = firstOf(b.id, 'question');
    const answer = firstOf(a.id, 'answer');
    expect(resultOf(a.id, 'message_thread')).toBe(`Sent question #${q.id} to "B" (done: it will be woken to answer from its context; its result stays final).`);
    expect(answer.payload).toMatchObject({ from_agent_id: b.id, reply_to: q.id, text: 'Tokens are JWTs signed with RS256.' });
    expect(answer.payload.auto).toBeUndefined();
    const answerRun = h.store
      .list({ agentId: b.id, types: ['run.started'] })
      .find((e): e is EventOf<'run.started'> => e.type === 'run.started' && e.payload.answering !== undefined)!;
    expect(answerRun.payload.answering).toBe(q.id);
    expect(h.store.list({ agentId: b.id, after: answerRun.id, types: ['agent.status_changed'] })).toEqual([]);
    const fromB = h.store
      .list({ agentId: deskAgent.id, types: ['message.agent'] })
      .flatMap((e) => (e.type === 'message.agent' && e.payload.from_agent_id === b.id ? [e.payload.kind] : []));
    expect(fromB).toEqual(['completed']);
  });

  it("keeps Desk asleep while A asks running B and B answers, then shows the exchange under Desk's Thread traffic", async () => {
    let deskAtAsk = -1;
    let deskAtAnswer = -1;
    const deskRequests = () => h.fake.requests.filter(isDesk);
    const { a, b } = await run((req) => {
      const convo = all(req);
      if (titleOf(req) === 'A') {
        if (convo.includes('— answer to your question #')) return tools(call('complete', { summary: 'Login form sends a JWT' }));
        if (called(req, 'message_thread')) return tools(call('wait_for_reply', {}));
        if (statusOf('B') !== 'running') return later();
        deskAtAsk = deskRequests().length;
        return tools(call('message_thread', { thread_id: 'B', kind: 'question', text: 'Which token format does the API return?' }), call('wait_for_reply', {}));
      }
      if (called(req, 'message_thread')) return tools(call('complete', { summary: 'Login API done' }));
      if (/from thread "A" \([0-9A-Z]{26}\) — question;/.test(convo)) {
        deskAtAnswer = deskRequests().length;
        return tools(call('message_thread', { thread_id: 'A', text: 'A JWT, RS256.' }));
      }
      return later();
    });
    expect([a.status, b.status]).toEqual(['done', 'done']);
    const q = firstOf(b.id, 'question');
    const answer = firstOf(a.id, 'answer');
    expect(answer.payload).toMatchObject({ from_agent_id: b.id, reply_to: q.id, text: 'A JWT, RS256.' });
    expect(resultOf(a.id, 'message_thread')).toBe(
      `Sent question #${q.id} to "B" (running: it sees this at its next step). Call wait_for_reply to pause until it answers, or keep working.`,
    );
    expect(resultOf(b.id, 'message_thread')).toBe(`Sent #${answer.id} to "A" as the answer to its question #${q.id}.`);

    // Desk made no request from A's question to B's answer, and later ran only for completions.
    expect(deskAtAsk).toBe(1);
    expect(deskAtAnswer).toBe(deskAtAsk);
    const afterwards = deskRequests().slice(1);
    expect(afterwards.length).toBeGreaterThan(0);
    for (const r of afterwards) expect(String(r.messages.at(-1)?.content)).toMatch(/— completed\]/);

    // Desk's next system prompt lists the exchange.
    const at = (id: number) => h.store.list({ projectId }).find((e) => e.id === id)!.ts.slice(11, 16);
    const prompt = systemOf(afterwards[0]!);
    expect(prompt).toContain(`- #${q.id} ${at(q.id)} "A" → "B", question, answered by #${answer.id}: "Which token format does the API return?"`);
    expect(prompt).toContain(`- #${answer.id} ${at(answer.id)} "B" → "A", answer, answer to #${q.id}: "A JWT, RS256."`);
  });
});
