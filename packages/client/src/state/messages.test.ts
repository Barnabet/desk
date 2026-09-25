import { describe, expect, it } from 'vitest';
import type { AgentMessageKind, AttentionItem, EventOf, StoredEvent } from '@desk/protocol';
import { ev } from '../testing';
import { agentTitle, answeringOf, digestView, foldMessages, questionView, trafficSince, waitingOn } from './messages';

/** `ev()` stamps event `id` this many seconds after 10:00:00Z on 2026-09-24. */
const ts = (id: number) => new Date(Date.UTC(2026, 8, 24, 10, 0, id)).toISOString();
const created = (id: number, agent: string, role: 'desk' | 'thread', title: string) =>
  ev(id, 'agent.created', { role, model: 'm', title, brief: role === 'thread' ? 'b' : null, workspace_path: `/w/${agent}`, parent_id: role === 'thread' ? 'd' : null }, { agent });
/** Desk d and the threads a ("Auth API"), b ("Billing"), c ("Checkout") and f ("Frontend"). */
const team = (): StoredEvent[] => [created(1, 'd', 'desk', 'Desk'), created(2, 'a', 'thread', 'Auth API'), created(3, 'b', 'thread', 'Billing'), created(4, 'c', 'thread', 'Checkout'), created(5, 'f', 'thread', 'Frontend')];
type Extra = Partial<EventOf<'message.agent'>['payload']>;
const msg = (id: number, from: string, to: string, kind: AgentMessageKind, extra: Extra = {}) =>
  ev(id, 'message.agent', { from_agent_id: from, from_label: from === 'd' ? 'Desk' : `thread "${from}" (${from})`, kind, text: `${kind} ${id}`, ...extra }, { agent: to });
/** A tracked question, as the send path stores it. */
const ask = (id: number, from: string, to: string) => msg(id, from, to, 'question', { tracked: true });
/** An answer to question `q`. */
const reply = (id: number, q: number, from: string, to: string, extra: Extra = {}) => msg(id, from, to, 'answer', { reply_to: q, ...extra });
const item = (id: string, agentId: string | null, kind: AttentionItem['kind'] = 'approval'): AttentionItem => ({
  id,
  kind,
  project_id: 'p',
  project_name: 'P',
  agent_id: agentId,
  title: id,
  detail: '',
  created_at: '',
  ref: {},
});

describe('the client message fold', () => {
  it('names agents, archived threads included', () => {
    const s = foldMessages([...team(), ev(6, 'agent.archived', {}, { agent: 'c' })]);
    expect([agentTitle(s, 'd'), agentTitle(s, 'a'), agentTitle(s, 'c'), agentTitle(s, 'x')]).toEqual(['Desk', 'Auth API', 'Checkout', 'a thread']);
  });

  it("sets answering at an answer run's start and clears it at that run's end", () => {
    const s1 = foldMessages([...team(), ask(6, 'a', 'f'), ev(7, 'run.started', { run_id: 'r1', model: 'm', answering: 6 }, { agent: 'f' })]);
    expect(answeringOf(s1, 'f')).toEqual({ runId: 'r1', question: 6, asker: 'a', since: ts(7) });
    const s2 = foldMessages([ev(8, 'run.finished', { run_id: 'r1', reason: 'no_tool_calls' }, { agent: 'f' })], s1);
    expect(answeringOf(s2, 'f')).toBeUndefined();
  });
});

describe('waitingOn', () => {
  it("lists each recipient of the agent's open questions once, oldest first, with its attention item", () => {
    const s = foldMessages([...team(), ask(6, 'a', 'f'), ask(7, 'a', 'd'), ask(8, 'a', 'd'), ask(9, 'a', 'b'), reply(10, 9, 'b', 'a')]);
    const deskAsks = item('question:40', 'd', 'question');
    expect(waitingOn(s, 'a', [deskAsks])).toEqual([
      { agentId: 'f', question: 6, since: ts(6) },
      { agentId: 'd', question: 7, since: ts(7), attention: deskAsks },
    ]);
    expect(waitingOn(s, 'f', [])).toEqual([]);
  });

  it('follows one hop to an agent the target asked that needs the user', () => {
    const approval = item('approval:x', 'c');
    const s = foldMessages([...team(), ask(6, 'a', 'f'), ask(7, 'f', 'b'), ask(8, 'f', 'c')]);
    expect(waitingOn(s, 'a', [approval])).toEqual([{ agentId: 'f', question: 6, since: ts(6), via: { agentId: 'c', question: 8, since: ts(8), attention: approval } }]);
  });

  it('never hops back to the asking agent', () => {
    const stalled = item('stalled:a:1', 'a', 'stalled');
    const s = foldMessages([...team(), ask(6, 'a', 'f'), ask(7, 'f', 'a')]);
    expect(waitingOn(s, 'a', [stalled])).toEqual([{ agentId: 'f', question: 6, since: ts(6) }]);
    // f waits on a, and a itself needs the user: that is the target's own item, not a hop.
    expect(waitingOn(s, 'f', [stalled])).toEqual([{ agentId: 'a', question: 7, since: ts(7), attention: stalled }]);
  });
});

describe('trafficSince, questionView and digestView', () => {
  it('lists the messages between threads and from the user to threads after an event', () => {
    const s = foldMessages([
      ...team(),
      ask(6, 'a', 'f'),
      msg(7, 'd', 'f', 'note'),
      msg(8, 'f', 'd', 'update'),
      ev(9, 'message.user', { text: 'Use v2.' }, { agent: 'b' }),
      reply(10, 6, 'f', 'a'),
      ev(11, 'message.user', { text: 'Status?' }, { agent: 'd' }),
    ]);
    expect(trafficSince(s, 0).map((m) => m.id)).toEqual([6, 9, 10]);
    expect(trafficSince(s, 6).map((m) => m.id)).toEqual([9, 10]);
    expect(trafficSince(s, 10)).toEqual([]);
  });

  it("gives a tracked question its state line, and nothing else one", () => {
    const s = foldMessages([
      ...team(),
      ask(6, 'd', 'f'),
      ask(7, 'd', 'a'),
      reply(8, 6, 'f', 'd'),
      ask(9, 'd', 'b'),
      reply(10, 9, 'b', 'd', { auto: true, text: '(Billing was stopped before answering.)' }),
      msg(11, 'a', 'd', 'question'),
      ask(12, 'c', 'f'),
      ev(13, 'agent.status_changed', { status: 'done' }, { agent: 'c' }),
    ]);
    expect(questionView(s, 6)).toEqual({ state: 'answered', answerId: 8, answeredAt: ts(8), since: ts(6), toTitle: 'Frontend' });
    expect(questionView(s, 7)).toEqual({ state: 'open', since: ts(7), toTitle: 'Auth API' });
    expect(questionView(s, 9)).toEqual({ state: 'closed', answerId: 10, answeredAt: ts(10), since: ts(9), toTitle: 'Billing' });
    expect(questionView(s, 12)).toEqual({ state: 'withdrawn', since: ts(12), toTitle: 'Frontend' });
    // An untracked question has no state, and an answer is not a question.
    expect(questionView(s, 11)).toBeUndefined();
    expect(questionView(s, 8)).toBeUndefined();
  });

  it("counts a digest's messages and open questions pair by pair, from the fold at render time", () => {
    const s = foldMessages([...team(), ask(6, 'a', 'f'), msg(7, 'f', 'a', 'note'), ask(8, 'b', 'c'), reply(9, 8, 'c', 'b'), ask(10, 'f', 'a')]);
    const ids = [6, 7, 8, 9, 10];
    expect(digestView(s, ids)).toEqual({
      messages: 5,
      open: 2,
      pairs: [
        { a: 'a', b: 'f', count: 3, latest: 10, latestTo: 'a', waiting: { agentId: 'a', since: ts(6) } },
        { a: 'b', b: 'c', count: 2, latest: 9, latestTo: 'b' },
      ],
    });
    // An answer that lands on another stream lowers the open count and moves the wait.
    const later = foldMessages([reply(11, 6, 'f', 'a')], s);
    expect(digestView(later, ids)).toMatchObject({ messages: 5, open: 1, pairs: [{ waiting: { agentId: 'f', since: ts(10) } }, {}] });
  });
});
