import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { text } from '@desk/fake-model';
import { foldMessages, messageById } from '@desk/protocol';
import { NotFoundError, ValidationError } from '../errors';
import { getDeskAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';

let h: Harness;
afterEach(async () => h?.cleanup());

/**
 * A project whose threads the user stopped: messages to them are stored and never run them (now, and once questions
 * start answer runs), and a stopped asker keeps its questions open (design spec §1.3).
 */
async function setup() {
  h = await createHarness({ script: () => text('noted') });
  const rt = newRuntime(h);
  const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { thread_model: FAKE_MODEL.id } });
  let n = 0;
  const stopped = (title: string) => {
    const id = rt.createThread(projectId, { title, brief: 'b', workspacePath: join(h.dir, `ws-${++n}`) });
    h.store.append({ project_id: projectId, agent_id: id, type: 'agent.status_changed', payload: { status: 'cancelled' } });
    return id;
  };
  return { rt, projectId, stopped };
}

describe('Runtime.messages', () => {
  it('folds each project incrementally, the same as one fold of all its events', async () => {
    const { rt, projectId, stopped } = await setup();
    const a = stopped('Auth API');
    const f = stopped('Frontend');
    const q = rt.deliver(a, f, 'question', 'Which token format?', { tracked: true });
    expect(messageById(rt.messages(projectId), q)).toMatchObject({ from: a, to: f, kind: 'question', state: 'open' });
    const answer = rt.deliver(f, a, 'answer', 'JWT.', { replyTo: q });
    const s = rt.messages(projectId);
    expect(messageById(s, q)).toMatchObject({ state: 'answered', answerId: answer });
    expect(s).toEqual(foldMessages(h.store.list({ projectId })));
    expect(rt.messages(projectId)).toBe(s);

    const other = rt.createProject({ name: 'Q', goal: 'G' });
    expect(rt.messages(other).messages).toEqual([]);
    expect(Object.keys(rt.messages(other).agents)).toEqual([getDeskAgent(h.store.db, other)!.id]);
  });
});

describe('deliver and answer', () => {
  it('stores what the send path passes, labels a thread with its sanitised title, and returns the id', async () => {
    const { rt, stopped } = await setup();
    const from = stopped('Auth]  API\n v2');
    const to = stopped('Frontend');
    const id = rt.deliver(from, to, 'question', 'Which token format?', { tracked: true, toolCallId: 'call_1' });
    const closure = rt.deliver(to, from, 'answer', '(Frontend was stopped before answering.)', { replyTo: id, auto: true });
    expect(h.store.list({ types: ['message.agent'] }).map((e) => [e.id, e.agent_id, e.payload])).toEqual([
      [id, to, { from_agent_id: from, from_label: `thread "Auth API v2" (${from})`, kind: 'question', text: 'Which token format?', tracked: true, tool_call_id: 'call_1' }],
      [closure, from, { from_agent_id: to, from_label: `thread "Frontend" (${to})`, kind: 'answer', text: '(Frontend was stopped before answering.)', reply_to: id, auto: true }],
    ]);
  });

  it('answers a question once, and closes it only while it is open', async () => {
    const { rt, projectId, stopped } = await setup();
    const a = stopped('Auth API');
    const f = stopped('Frontend');
    const c = stopped('Checkout');
    const ask = (from: string, to: string) => rt.deliver(from, to, 'question', 'Which token format?', { tracked: true });
    const answersTo = (q: number) =>
      h.store.list({ projectId, types: ['message.agent'] }).flatMap((e) => (e.type === 'message.agent' && e.payload.reply_to === q ? [e] : []));
    const stateOf = (q: number) => messageById(rt.messages(projectId), q);

    // The first answer is written; later answers and closures are dropped.
    const q1 = ask(a, f);
    const first = rt.answer(q1, f, 'JWT, RS256.', { toolCallId: 'call_9' });
    expect(first).toEqual(expect.any(Number));
    expect(rt.answer(q1, f, 'Actually, opaque tokens.')).toBeNull();
    expect(rt.answer(q1, f, '(Frontend was stopped before answering.)', { auto: true })).toBeNull();
    expect(answersTo(q1).map((e) => [e.id, e.agent_id, e.payload.kind, e.payload.text, e.payload.auto, e.payload.tool_call_id])).toEqual([
      [first, a, 'answer', 'JWT, RS256.', undefined, 'call_9'],
    ]);
    expect(stateOf(q1)).toMatchObject({ state: 'answered', answerId: first });

    // A closure closes an open question.
    const q2 = ask(c, f);
    const closure = rt.answer(q2, f, '(Frontend was stopped before answering.)', { auto: true });
    expect(answersTo(q2).map((e) => e.payload.auto)).toEqual([true]);
    expect(stateOf(q2)).toMatchObject({ state: 'closed', answerId: closure });

    // Once its asker completed, a question is withdrawn: no closure is written, and a real answer still lands once.
    const q3 = ask(a, f);
    h.store.append({ project_id: projectId, agent_id: a, type: 'agent.status_changed', payload: { status: 'done' } });
    expect(rt.answer(q3, f, '(Frontend was stopped before answering.)', { auto: true })).toBeNull();
    expect(answersTo(q3)).toEqual([]);
    const late = rt.answer(q3, f, 'JWT.');
    expect(stateOf(q3)).toMatchObject({ state: 'withdrawn', answerId: late });

    // Only the question's recipient answers it, and only a question can be answered.
    expect(() => rt.answer(q2, a, 'Me too.')).toThrow(ValidationError);
    const note = rt.deliver(a, f, 'note', 'FYI');
    expect(() => rt.answer(note, f, 'Thanks.')).toThrow(NotFoundError);
    await rt.whenIdle();
  });
});
