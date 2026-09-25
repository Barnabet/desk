import { describe, expect, it } from 'vitest';
import { emptyChat, foldMessages, reduceChat } from '@desk/client';
import { ev } from '@desk/client/testing';
import type { AgentMessageKind, EventOf, StoredEvent } from '@desk/protocol';
import { keepStable, rowViews, ticks } from './rowViews';

/** `ev()` stamps event `id` this many seconds after 10:00:00Z on 2026-09-24. */
const ts = (id: number) => new Date(Date.UTC(2026, 8, 24, 10, 0, id)).toISOString();
const TITLES: Record<string, string> = { d: 'Desk', a: 'Auth API', f: 'Frontend' };
const created = (id: number, agent: string) =>
  ev(id, 'agent.created', { role: agent === 'd' ? 'desk' : 'thread', model: 'm', title: TITLES[agent]!, brief: null, workspace_path: `/w/${agent}`, parent_id: agent === 'd' ? null : 'd' }, { agent });
const msg = (id: number, from: string, to: string, kind: AgentMessageKind, text: string, extra: Partial<EventOf<'message.agent'>['payload']> = {}) =>
  ev(id, 'message.agent', { from_agent_id: from, from_label: from === 'd' ? 'Desk' : `thread "${TITLES[from]}" (${from})`, kind, text, ...extra }, { agent: to });

/** Desk d asks Auth API a, the user steers Frontend f, a asks f, and f asks Desk. */
const events: StoredEvent[] = [
  created(1, 'd'),
  created(2, 'a'),
  created(3, 'f'),
  msg(4, 'd', 'a', 'question', 'Which token format?', { tracked: true }),
  ev(5, 'message.user', { text: 'Use the v2 API.' }, { agent: 'f' }),
  msg(6, 'a', 'f', 'question', 'Is the page ready?', { tracked: true }),
  msg(7, 'f', 'd', 'question', 'Ship Friday?', { tracked: true }),
];
/** a answers Desk, and Desk answers f. */
const answers: StoredEvent[] = [msg(8, 'a', 'd', 'answer', 'JWT.', { reply_to: 4 }), msg(9, 'd', 'f', 'answer', 'Yes.', { reply_to: 7 })];
const chatOf = (list: StoredEvent[]) => list.reduce(reduceChat, emptyChat('d')).items;

describe('rowViews', () => {
  it('gives message, steer, question and digest rows what they show from the fold', () => {
    expect(Object.fromEntries(rowViews(chatOf(events), foldMessages(events)))).toEqual({
      'e:4': { toTitle: 'Auth API', question: { state: 'open', since: ts(4), toTitle: 'Auth API' } },
      'e:5': { toTitle: 'Frontend' },
      'e:6': { digest: { messages: 1, open: 1, pairs: [{ key: 'a f', label: 'Auth API ⇄ Frontend', count: 1, waiting: { who: 'Auth API', since: ts(6) }, to: 'f', at: 6 }] } },
      'e:7': { question: { state: 'open', since: ts(7), toTitle: 'Desk' } },
    });
  });

  it('quotes the question an answer answers, and settles the question row, in either direction', () => {
    const all = [...events, ...answers];
    const views = rowViews(chatOf(all), foldMessages(all));
    expect(views.get('e:8')).toEqual({ answers: { question: 4, asker: 'Desk', text: 'Which token format?' } });
    expect(views.get('e:9')).toEqual({ toTitle: 'Frontend', answers: { question: 7, asker: 'Frontend', text: 'Ship Friday?' } });
    expect(views.get('e:4')!.question).toEqual({ state: 'answered', answerId: 8, answeredAt: ts(8), since: ts(4), toTitle: 'Auth API' });
    expect(views.get('e:7')!.question).toMatchObject({ state: 'answered', answerId: 9 });
  });

  it('keeps the same object for a row whose view did not change, and ticks only open questions', () => {
    const items = chatOf(events);
    const first = rowViews(items, foldMessages(events));
    // The answers land elsewhere: these items do not change, but two of their views do.
    const next = keepStable(first, rowViews(items, foldMessages(answers, foldMessages(events))));
    expect(next.get('e:5')).toBe(first.get('e:5'));
    expect(next.get('e:6')).toBe(first.get('e:6'));
    expect(next.get('e:4')).not.toBe(first.get('e:4'));
    expect([ticks(first.get('e:4')), ticks(next.get('e:4')), ticks(first.get('e:6')), ticks(first.get('e:5')), ticks(undefined)]).toEqual([true, false, true, false, false]);
  });
});
