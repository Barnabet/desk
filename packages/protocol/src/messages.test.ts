import { beforeEach, describe, expect, it } from 'vitest';
import type { AgentMessageKind, AgentRole, AgentStatus } from './domain';
import type { EventBody, EventOf, StoredEvent } from './events';
import {
  answeringOf,
  emptyMessages,
  foldMessages,
  messageById,
  openFrom,
  openTo,
  pair,
  reduceMessages,
  sentSince,
  traffic,
  type MessagesState,
} from './messages';

let seq = 0;
beforeEach(() => {
  seq = 0;
});

/** Minute `m` of the test hour, written like stored timestamps. */
const at = (m: number) => `2026-09-25T10:${String(m).padStart(2, '0')}:00.000Z`;
const ev = (agent_id: string | null, body: EventBody, minute = 0): StoredEvent => ({ ...body, id: ++seq, project_id: 'p', agent_id, ts: at(minute) }) as StoredEvent;
const created = (id: string, role: AgentRole, title: string | null) =>
  ev(id, { type: 'agent.created', payload: { role, model: 'm', title, brief: null, workspace_path: null, parent_id: null } });
/** Desk D and the threads A ("Auth API") and F ("Frontend"). */
const team = () => [created('D', 'desk', null), created('A', 'thread', 'Auth API'), created('F', 'thread', 'Frontend')];
const status = (id: string, s: AgentStatus, minute = 0) => ev(id, { type: 'agent.status_changed', payload: { status: s } }, minute);
type Extra = Partial<EventOf<'message.agent'>['payload']>;
const msg = (from: string, to: string, kind: AgentMessageKind, extra: Extra = {}, minute = 0) =>
  ev(to, { type: 'message.agent', payload: { from_agent_id: from, from_label: from, kind, text: `${kind} from ${from}`, ...extra } }, minute);
/** A tracked question, as the send path stores it. */
const ask = (from: string, to: string, minute = 0) => msg(from, to, 'question', { tracked: true }, minute);
/** An answer to `q`, from its recipient to its asker. */
const reply = (q: StoredEvent, extra: Extra = {}, minute = 0) =>
  msg(q.agent_id!, q.type === 'message.agent' ? q.payload.from_agent_id : '?', 'answer', { reply_to: q.id, ...extra }, minute);
const view = (s: MessagesState, e: StoredEvent) => messageById(s, e.id);
const ids = (views: Array<{ id: number }>) => views.map((m) => m.id);

describe('foldMessages', () => {
  it('pairs each answer with its question', () => {
    const t = team();
    const q = ask('A', 'F', 1);
    const a = reply(q, { tool_call_id: 'call_7' }, 3);
    const s = foldMessages([...t, q, a]);
    expect(view(s, q)).toEqual({ id: q.id, ts: at(1), from: 'A', to: 'F', kind: 'question', text: 'question from A', tracked: true, state: 'answered', answerId: a.id, stateAt: at(3) });
    expect(view(s, a)).toEqual({ id: a.id, ts: at(3), from: 'F', to: 'A', kind: 'answer', text: 'answer from F', replyTo: q.id, toolCallId: 'call_7' });
    expect(openFrom(s, 'A')).toEqual([]);
    expect(openTo(s, 'F')).toEqual([]);
  });

  it('keeps the first answer and the first state', () => {
    const t = team();
    const q1 = ask('A', 'F');
    const closure = reply(q1, { auto: true, text: '(Frontend was stopped before answering.)' });
    const late = reply(q1);
    const q2 = ask('D', 'F');
    const a2 = reply(q2);
    const extra = reply(q2, { auto: true });
    const s = foldMessages([...t, q1, closure, late, q2, a2, extra]);
    expect(view(s, q1)).toMatchObject({ state: 'closed', answerId: closure.id });
    expect(view(s, q2)).toMatchObject({ state: 'answered', answerId: a2.id });
    expect(view(s, closure)).toMatchObject({ kind: 'answer', auto: true, replyTo: q1.id });
    expect(view(s, late)).toMatchObject({ kind: 'answer', replyTo: q1.id });
  });

  it("withdraws the questions of an asker that completes, for good, and still records a late answer", () => {
    const t = team();
    const q = ask('A', 'F', 1);
    const done = status('A', 'done', 2);
    const reopened = status('A', 'running', 3);
    const late = reply(q, {}, 4);
    const s = foldMessages([...t, q, done, reopened, late]);
    expect(view(s, q)).toMatchObject({ state: 'withdrawn', stateAt: at(2), answerId: late.id });
    expect(openFrom(s, 'A')).toEqual([]);
    expect(s.agents.A).toMatchObject({ status: 'running' });
  });

  it('keeps the questions of a failed or stopped asker open, Desk included', () => {
    const t = team();
    const fromA = ask('A', 'F');
    const fromDesk = [ask('D', 'A'), ask('D', 'F')];
    const s = foldMessages([...t, fromA, ...fromDesk, status('A', 'failed'), status('A', 'cancelled'), status('D', 'failed'), status('D', 'cancelled')]);
    expect(ids(openFrom(s, 'A'))).toEqual([fromA.id]);
    expect(ids(openFrom(s, 'D'))).toEqual(ids(fromDesk));
    expect(ids(openTo(s, 'F'))).toEqual([fromA.id, fromDesk[1]!.id]);
  });

  it('withdraws an archived asker’s questions and keeps its title, and withdraws every open question when the project is archived', () => {
    const t = team();
    const fromF = ask('F', 'A', 1);
    const archiveF = ev('F', { type: 'agent.archived', payload: {} }, 2);
    const fromDesk = ask('D', 'A', 3);
    const fromA = ask('A', 'D', 4);
    const answered = ask('D', 'F', 5);
    const a = reply(answered, {}, 6);
    const s1 = foldMessages([...t, fromF, archiveF, fromDesk, fromA, answered, a]);
    expect(view(s1, fromF)).toMatchObject({ state: 'withdrawn', stateAt: at(2) });
    expect(s1.agents.F).toEqual({ id: 'F', role: 'thread', title: 'Frontend', status: 'idle', archived: true });
    expect(ids(openTo(s1, 'A'))).toEqual([fromDesk.id]);

    const s2 = foldMessages([ev(null, { type: 'project.archived', payload: {} }, 9)], s1);
    expect([fromDesk, fromA, answered].map((e) => view(s2, e)?.state)).toEqual(['withdrawn', 'withdrawn', 'answered']);
    expect(view(s2, fromA)?.stateAt).toBe(at(9));
  });

  it('gives untracked questions no state', () => {
    const t = team();
    const legacy = msg('A', 'D', 'question');
    const a = reply(legacy);
    const s = foldMessages([...t, legacy, a]);
    expect(view(s, legacy)).not.toHaveProperty('state');
    expect(view(s, legacy)).not.toHaveProperty('tracked');
    expect(view(s, legacy)).toMatchObject({ kind: 'question', answerId: a.id });
    expect(openFrom(s, 'A')).toEqual([]);
    expect(openTo(s, 'D')).toEqual([]);
  });

  it('tracks answer runs from their start to the end of the same run', () => {
    const t = team();
    const q = ask('A', 'F', 1);
    const userAsk = ev('F', { type: 'message.user', payload: { text: 'What did you change?' } }, 2);
    const start = ev('F', { type: 'run.started', payload: { run_id: 'r1', model: 'm', answering: q.id } }, 3);
    const s1 = foldMessages([...t, q, userAsk, start]);
    expect(answeringOf(s1, 'F')).toEqual({ runId: 'r1', question: q.id, asker: 'A', since: at(3) });
    expect(answeringOf(s1, 'A')).toBeUndefined();

    const otherRun = ev('F', { type: 'run.finished', payload: { run_id: 'r0', reason: 'error' } });
    const finished = ev('F', { type: 'run.finished', payload: { run_id: 'r1', reason: 'no_tool_calls' } });
    expect(answeringOf(foldMessages([otherRun], s1), 'F')).toMatchObject({ runId: 'r1' });
    const s2 = foldMessages([otherRun, finished], s1);
    expect(answeringOf(s2, 'F')).toBeUndefined();

    const s3 = foldMessages([ev('F', { type: 'run.started', payload: { run_id: 'r2', model: 'm', answering: userAsk.id } }, 5)], s2);
    expect(answeringOf(s3, 'F')).toEqual({ runId: 'r2', question: userAsk.id, asker: 'user', since: at(5) });
    // A full run is not an answer run.
    expect(answeringOf(foldMessages([ev('A', { type: 'run.started', payload: { run_id: 'r3', model: 'm' } })], s3), 'A')).toBeUndefined();
  });

  it('folds in two halves the same as at once, never changes its input, and skips what it already folded', () => {
    const t = team();
    const q = ask('A', 'F', 1);
    const events: StoredEvent[] = [
      ...t,
      q,
      ev('F', { type: 'run.started', payload: { run_id: 'r1', model: 'm', answering: q.id } }, 2),
      reply(q, {}, 3),
      ev('F', { type: 'run.finished', payload: { run_id: 'r1', reason: 'no_tool_calls' } }, 3),
      ev('F', { type: 'message.user', payload: { text: 'Use the v2 API.' } }, 4),
      ev('D', { type: 'plan.updated', payload: { items: [] } }, 4),
      status('A', 'done', 5),
      ask('D', 'F', 6),
    ];
    const all = foldMessages(events);
    const half = foldMessages(events.slice(0, 6));
    const before = structuredClone(half);
    expect(foldMessages(events.slice(6), half)).toEqual(all);
    expect(half).toEqual(before);
    expect(events.reduce(reduceMessages, emptyMessages())).toEqual(all);
    expect(foldMessages(events, all)).toBe(all);
    expect(reduceMessages(all, ev('D', { type: 'plan.updated', payload: { items: [] } }))).toBe(all);
  });
});

describe('message selectors', () => {
  it('traffic holds the latest messages between threads and from the user to threads', () => {
    const t = team();
    const toDesk = msg('A', 'D', 'update');
    const fromDesk = msg('D', 'F', 'note');
    const between = [ask('A', 'F'), msg('F', 'A', 'note')];
    const userToF = ev('F', { type: 'message.user', payload: { text: 'Use the v2 API.' } });
    const userToDesk = ev('D', { type: 'message.user', payload: { text: 'Status?' } });
    const reminder = msg('D', 'D', 'reminder');
    const s = foldMessages([...t, toDesk, fromDesk, ...between, userToF, userToDesk, reminder]);
    expect(ids(traffic(s, 12))).toEqual([...ids(between), userToF.id]);
    expect(ids(traffic(s, 2))).toEqual([between[1]!.id, userToF.id]);
    expect(traffic(s, 0)).toEqual([]);
    expect(messageById(s, userToF.id)).toEqual({ id: userToF.id, ts: at(0), from: 'user', to: 'F', kind: 'user', text: 'Use the v2 API.' });
    expect(messageById(s, userToDesk.id)).toBeUndefined();
    expect(messageById(s, reminder.id)).toBeUndefined();
  });

  it('pair, openTo and sentSince', () => {
    const t = team();
    const q = ask('A', 'F', 1);
    const note = msg('A', 'F', 'note', {}, 30);
    const a = reply(q, {}, 31);
    const toDesk = ask('A', 'D', 32);
    const fromDesk = msg('D', 'A', 'note', {}, 33);
    const s = foldMessages([...t, q, note, a, toDesk, fromDesk]);
    expect(ids(pair(s, 'F', 'A'))).toEqual([q.id, note.id, a.id]);
    expect(ids(openTo(s, 'D'))).toEqual([toDesk.id]);
    expect(ids(sentSince(s, 'A', at(0)))).toEqual([q.id, note.id]);
    expect(ids(sentSince(s, 'A', at(10)))).toEqual([note.id]);
    // Answers are exempt from the sender cap.
    expect(sentSince(s, 'F', at(0))).toEqual([]);
  });
});

describe("the user's Ask", () => {
  it('folds as user_question, and its answer run answers the user', () => {
    const t = team();
    const userAsk = ev('F', { type: 'message.user', payload: { text: 'What did you change?', question: true } }, 2);
    const start = ev('F', { type: 'run.started', payload: { run_id: 'r1', model: 'm', answering: userAsk.id } }, 3);
    const s = foldMessages([...t, userAsk, start]);
    expect(messageById(s, userAsk.id)).toEqual({ id: userAsk.id, ts: at(2), from: 'user', to: 'F', kind: 'user_question', text: 'What did you change?' });
    expect(answeringOf(s, 'F')).toEqual({ runId: 'r1', question: userAsk.id, asker: 'user', since: at(3) });
    expect(ids(traffic(s, 12))).toEqual([userAsk.id]);
  });
});
