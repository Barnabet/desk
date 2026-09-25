import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { text, type ChatRequest } from '@desk/fake-model';
import { messageById, type AgentStatus, type EventOf } from '@desk/protocol';
import { getAgent, getDeskAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';
import type { SendInput } from '../tools/types';
import type { Runtime } from './runtime';

let h: Harness;
let rt: Runtime;
afterEach(async () => h?.cleanup());

const systemOf = (req: ChatRequest) => String(req.messages[0]?.content ?? '');
/** The model requests of the thread titled `title`, found by its system prompt. */
const threadRequests = (title: string) => h.fake.requests.filter((r) => systemOf(r).includes(`## Your assignment: ${title}\n`));
/** A stored message by id. */
const stored = (id: number) =>
  h.store.list({ types: ['message.agent'] }).find((e): e is EventOf<'message.agent'> => e.type === 'message.agent' && e.id === id)!;

/**
 * A project where every agent answers "noted". Threads are put in a status directly: a `running` thread has no job, so
 * what it is sent waits for a next step that never comes, and a `cancelled` one is a thread the user stopped.
 */
async function setup() {
  h = await createHarness({ script: () => text('noted') });
  rt = newRuntime(h);
  const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id } });
  const desk = getDeskAgent(h.store.db, projectId)!;
  const setStatus = (agentId: string, status: AgentStatus) =>
    h.store.append({ project_id: projectId, agent_id: agentId, type: 'agent.status_changed', payload: { status } });
  let n = 0;
  const thread = (title: string, status?: AgentStatus) => {
    const id = rt.createThread(projectId, { title, brief: `Do the ${title} part`, workspacePath: join(h.dir, `ws-${++n}`) });
    if (status) setStatus(id, status);
    return id;
  };
  /** Marks the agent's stream as read up to `upTo`, as a run's drain does. */
  const drained = (agentId: string, upTo: number) =>
    h.store.append({ project_id: projectId, agent_id: agentId, type: 'inbox.drained', payload: { run_id: 'test-drain', up_to: upTo } });
  return { projectId, desk, thread, setStatus, drained };
}

describe('send refusals (design spec §2.4)', () => {
  it('refuses with the exact texts, in order, and stores nothing', async () => {
    const { projectId, desk, thread } = await setup();
    const a = thread('Auth API', 'running');
    const f = thread('Frontend', 'running');
    const send = (from: string, to: string, kind: SendInput['kind'] = 'note', body = 'Hello.') => () => rt.send({ from, to, kind, text: body });

    expect(send(a, a)).toThrow('That is you.');
    expect(send(desk.id, desk.id, 'question')).toThrow('That is you.');
    expect(send(a, 'Nobody')).toThrow('Unknown thread: Nobody');
    const elsewhere = getDeskAgent(h.store.db, rt.createProject({ name: 'Q', goal: 'G' }))!.id;
    expect(send(a, elsewhere, 'update')).toThrow(`Unknown thread: ${elsewhere}`);

    const old = thread('Old', 'done');
    h.store.append({ project_id: projectId, agent_id: old, type: 'agent.archived', payload: {} });
    expect(send(a, old, 'question')).toThrow('"Old" is archived.');
    expect(send(desk.id, 'Old', 'revision')).toThrow('"Old" is archived.');

    const scout = thread('Scout', 'cancelled');
    for (const kind of ['note', 'question'] as const) expect(send(a, scout, kind)).toThrow('"Scout" was stopped; it cannot receive messages.');
    for (const kind of ['note', 'question', 'revision'] as const) expect(send(desk.id, scout, kind)).toThrow('"Scout" was stopped; it cannot receive messages.');

    const sibling = (name: string) =>
      `"${name}" has finished; its result is final. Send kind "question" to ask about its work, or tell Desk (message_desk) if its work needs to change.`;
    const fromDesk = (name: string) =>
      `"${name}" has finished; its result is final. Send kind "question" to ask about its work, "revision" if it fell short of its brief, or spawn a new thread whose brief points at its result or branch.`;
    const pricing = thread('Pricing', 'done');
    const deploy = thread('Deploy', 'failed');
    expect(send(a, pricing)).toThrow(sibling('Pricing'));
    expect(send(a, deploy)).toThrow(sibling('Deploy'));
    expect(send(desk.id, pricing)).toThrow(fromDesk('Pricing'));
    expect(send(desk.id, deploy)).toThrow(fromDesk('Deploy'));

    const long = 'Messages are limited to 4000 characters; publish long content with library_publish and send its path.';
    expect(send(a, f, 'note', 'x'.repeat(4001))).toThrow(long);
    expect(send(a, desk.id, 'update', 'x'.repeat(4001))).toThrow(long);
    expect(send(desk.id, f, 'question', 'x'.repeat(4001))).toThrow(long);

    expect(h.store.list({ projectId, types: ['message.agent', 'agent.revision'] })).toEqual([]);
    expect(rt.send({ from: a, to: f, kind: 'note', text: 'x'.repeat(4000) }).kind).toBe('note');
  });

  it('refuses a thread that is being archived, and every agent of an archived project', async () => {
    const { projectId, thread } = await setup();
    const a = thread('Auth API', 'running');
    const f = thread('Frontend', 'running');
    const done = thread('Pricing', 'done');
    const archiving = rt.archiveThread(done);
    expect(() => rt.send({ from: a, to: done, kind: 'question', text: 'Per seat?' })).toThrow('"Pricing" is archived.');
    await archiving;
    expect(() => rt.send({ from: a, to: 'Pricing', kind: 'question', text: 'Per seat?' })).toThrow('"Pricing" is archived.');
    rt.archiveProject(projectId);
    expect(() => rt.send({ from: a, to: f, kind: 'note', text: 'Hi.' })).toThrow('This project is archived.');
    await rt.whenIdle();
  });

  it('lets an asker have one open question to a thread, and does not cap questions to Desk', async () => {
    const { desk, thread } = await setup();
    const a = thread('Auth API', 'running');
    const f = thread('Frontend', 'running');
    const q = rt.send({ from: a, to: f, kind: 'question', text: 'Which token format?' });
    expect(() => rt.send({ from: a, to: 'Frontend', kind: 'question', text: 'And the expiry?' })).toThrow(
      `You already asked "Frontend" #${q.id} and it has not answered. Wait for the answer (wait_for_reply) or send a note.`,
    );
    expect(rt.send({ from: a, to: f, kind: 'note', text: 'No rush.' }).kind).toBe('note');
    const dq = rt.send({ from: desk.id, to: f, kind: 'question', text: 'Status?' });
    expect(() => rt.send({ from: desk.id, to: f, kind: 'question', text: 'Status now?' })).toThrow(`You already asked "Frontend" #${dq.id}`);
    expect(rt.send({ from: a, to: desk.id, kind: 'question', text: 'EU or US?' }).kind).toBe('question');
    expect(rt.send({ from: a, to: desk.id, kind: 'question', text: 'And the currency?' }).kind).toBe('question');
    rt.answer(q.id, f, 'JWT.');
    expect(rt.send({ from: a, to: f, kind: 'question', text: 'And the expiry?' }).kind).toBe('question');
    await rt.whenIdle();
  });

  it("caps a thread's questions and notes to other threads at 20 an hour, not Desk's nor messages to Desk", async () => {
    const { desk, thread } = await setup();
    const a = thread('Auth API', 'running');
    const f = thread('Frontend', 'running');
    const c = thread('Checkout', 'running');
    for (let i = 0; i < 19; i++) rt.send({ from: a, to: i % 2 ? f : c, kind: 'note', text: `Note ${i}` });
    rt.send({ from: a, to: f, kind: 'question', text: 'Which token format?' });
    const capped = 'You have sent 20 questions or notes to other threads this hour. Stop and ask Desk (message_desk) to coordinate, or keep working with what you have.';
    expect(() => rt.send({ from: a, to: c, kind: 'note', text: 'One more.' })).toThrow(capped);
    expect(() => rt.send({ from: a, to: c, kind: 'question', text: 'One more?' })).toThrow(capped);
    expect(rt.send({ from: a, to: desk.id, kind: 'update', text: 'Halfway.' }).kind).toBe('update');
    expect(rt.send({ from: desk.id, to: c, kind: 'note', text: 'Carry on.' }).kind).toBe('note');
    await rt.whenIdle();
  });
});

describe('what send stores', () => {
  it('tracks a question, records the tool call on every message, and finds a thread by exact title', async () => {
    const { projectId, desk, thread } = await setup();
    const a = thread('Auth API', 'running');
    const gone = thread('Frontend', 'done');
    h.store.append({ project_id: projectId, agent_id: gone, type: 'agent.archived', payload: {} });
    const f = thread('Frontend', 'running');
    const q = rt.send({ from: a, to: 'Frontend', kind: 'question', text: 'Which token format?', toolCallId: 'call_q' });
    const n = rt.send({ from: a, to: f, kind: 'note', text: 'FYI.', toolCallId: 'call_n' });
    const u = rt.send({ from: a, to: desk.id, kind: 'blocker', text: 'No API key.', toolCallId: 'call_b' });
    expect([q, n, u].map((r) => [r.kind, r.replyTo])).toEqual([
      ['question', undefined],
      ['note', undefined],
      ['blocker', undefined],
    ]);
    expect(stored(q.id)).toMatchObject({ agent_id: f, payload: { from_agent_id: a, kind: 'question', text: 'Which token format?', tracked: true, tool_call_id: 'call_q' } });
    expect(stored(n.id).payload).toMatchObject({ kind: 'note', tool_call_id: 'call_n' });
    expect(stored(n.id).payload.tracked).toBeUndefined();
    expect(stored(u.id)).toMatchObject({ agent_id: desk.id, payload: { kind: 'blocker', tool_call_id: 'call_b' } });
    expect(messageById(rt.messages(projectId), q.id)).toMatchObject({ state: 'open', tracked: true });
    thread('Twin', 'running');
    thread('Twin', 'running');
    expect(() => rt.send({ from: a, to: 'Twin', kind: 'note', text: 'Hi.' })).toThrow('Several threads are titled "Twin"; use its id.');
    expect(() => rt.send({ from: a, to: 'frontend', kind: 'note', text: 'Hi.' })).toThrow('Unknown thread: frontend');
    await rt.whenIdle();
  });

  it("keeps Desk's revision: the review-round limit, then agent.revision, then the message", async () => {
    const { projectId, desk, thread, setStatus } = await setup();
    rt.updateSettings(projectId, { review_rounds: 1 });
    const t = thread('Draft', 'done');
    const r = rt.send({ from: desk.id, to: 'Draft', kind: 'revision', text: 'Add sources.', toolCallId: 'call_r' });
    expect(r).toEqual({ id: expect.any(Number), kind: 'revision', note: 'Sent revision 1 to "Draft"; it has been reopened.' });
    expect(h.store.list({ agentId: t, types: ['agent.revision'] }).map((e) => e.payload)).toEqual([{ round: 1, feedback: 'Add sources.' }]);
    expect(stored(r.id).payload).toMatchObject({ kind: 'revision', tool_call_id: 'call_r' });
    await rt.whenIdle();
    expect(getAgent(h.store.db, t)?.review_round).toBe(1);
    const a = thread('Auth API', 'running');
    expect(() => rt.send({ from: a, to: t, kind: 'revision', text: 'Mine too.' })).toThrow('Only Desk sends revisions.');
    setStatus(t, 'done');
    expect(() => rt.send({ from: desk.id, to: t, kind: 'revision', text: 'Again.' })).toThrow(
      'Review round limit (1) reached for "Draft": accept the work with noted caveats or escalate to the user.',
    );
  });
});

describe('send results (design spec §2.5)', () => {
  it('say what happens next to the message', async () => {
    const { projectId, desk, thread } = await setup();
    const a = thread('Auth API', 'running');
    /** The tool result of a send, with its id written N. */
    const say = (from: string, to: string, kind: SendInput['kind']) => {
      const r = rt.send({ from, to, kind, text: 'Hello.' });
      return r.note.replace(`#${r.id} `, '#N ');
    };
    const running = thread('Frontend', 'running');
    expect(say(a, running, 'question')).toBe(
      'Sent question #N to "Frontend" (running: it sees this at its next step). Call wait_for_reply to pause until it answers, or keep working.',
    );
    expect(say(a, running, 'note')).toBe('Sent note #N to "Frontend" (running: it sees this at its next step).');
    for (const status of ['done', 'idle', 'failed'] as const) {
      const t = thread(`Was ${status}`, status === 'idle' ? undefined : status);
      expect(say(a, t, 'question')).toBe(`Sent question #N to "Was ${status}" (${status}: it will be woken to answer from its context; its result stays final).`);
    }
    const waiter = thread('Waiter', 'waiting');
    expect(say(a, waiter, 'question')).toBe('Sent question #N to "Waiter" (waiting on its own reply: it will be woken just to answer you, and keeps waiting).');
    expect(say(a, waiter, 'note')).toBe(
      'Sent note #N to "Waiter" (waiting on its own reply: it reads this when it next runs; notes from other threads do not wake it).',
    );
    const blocked = thread('Blocked', 'waiting');
    h.store.append({
      project_id: projectId,
      agent_id: blocked,
      type: 'approval.requested',
      payload: { approval_id: 'ap1', run_id: 'r', tool_call_id: 'c1', tool: 'bash', arguments: '{"command":"sudo ls"}', reason: 'needs approval', delegate_to_desk: false },
    });
    expect(say(a, blocked, 'note')).toBe('Sent note #N to "Blocked" (waiting on an approval: it sees this once the approval is decided).');
    expect(say(a, thread('Idle'), 'note')).toBe('Sent note #N to "Idle" (idle: it reads this when Desk or the user resumes it).');
    expect(say(a, desk.id, 'question')).toBe(
      'Sent question #N to Desk (idle: it is woken to read this). Call wait_for_reply to pause until it answers, or keep working.',
    );
    await rt.whenIdle();
    rt.stop(desk.id);
    for (const kind of ['question', 'update', 'blocker'] as const) {
      expect(say(a, desk.id, kind)).toBe(`Sent ${kind} #N to Desk (stopped by the user: it reads this when the user resumes it).`);
    }
    await rt.whenIdle();
  });
});
