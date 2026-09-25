import { describe, expect, it } from 'vitest';
import { foldMessages } from '@desk/client';
import { ev } from '@desk/client/testing';
import type { AgentMessageKind, EventOf, StoredEvent } from '@desk/protocol';
import { pairView, type PairView } from './pairs';

const TITLES: Record<string, string> = { d: 'Desk', a: 'Auth API', f: 'Frontend' };
const created = (id: number, agent: string) =>
  ev(id, 'agent.created', { role: agent === 'd' ? 'desk' : 'thread', model: 'm', title: TITLES[agent]!, brief: null, workspace_path: `/w/${agent}`, parent_id: agent === 'd' ? null : 'd' }, { agent });
type Extra = Partial<EventOf<'message.agent'>['payload']>;
const msg = (id: number, from: string, to: string, kind: AgentMessageKind, extra: Extra = {}) =>
  ev(id, 'message.agent', { from_agent_id: from, from_label: from === 'd' ? 'Desk' : `thread "${TITLES[from]}" (${from})`, kind, text: `${kind} ${id}`, ...extra }, { agent: to });
/** Each row as its id, the thread whose transcript shows it, and its answers the same way. */
const shape = (v: PairView) => v.rows.map((r) => ({ id: r.message.id, shownIn: r.shownIn, answers: r.answers.map((x) => [x.message.id, x.shownIn]) }));

/** Desk d and the threads a ("Auth API") and f ("Frontend"). */
const events: StoredEvent[] = [
  created(1, 'd'),
  created(2, 'a'),
  created(3, 'f'),
  msg(4, 'a', 'f', 'question', { tracked: true }),
  msg(5, 'f', 'a', 'note'),
  msg(6, 'f', 'a', 'answer', { reply_to: 4 }),
  msg(7, 'f', 'a', 'question', { tracked: true }),
  msg(8, 'a', 'f', 'answer', { reply_to: 7, auto: true, text: '(Auth API was stopped before answering.)' }),
  msg(9, 'd', 'a', 'start'),
  msg(10, 'd', 'a', 'question', { tracked: true }),
  msg(11, 'a', 'd', 'update'),
  msg(12, 'a', 'd', 'answer', { reply_to: 10 }),
  msg(13, 'd', 'f', 'note'),
];

describe('pairView', () => {
  it("lists two threads' messages oldest first, each answer under its question, each shown in its recipient's transcript", () => {
    const v = pairView(foldMessages(events), 'a', 'f');
    expect(v.title).toBe('Auth API ⇄ Frontend');
    expect(shape(v)).toEqual([
      { id: 4, shownIn: 'f', answers: [[6, 'a']] },
      { id: 5, shownIn: 'a', answers: [] },
      { id: 7, shownIn: 'a', answers: [[8, 'f']] },
    ]);
    expect([v.rows[0]!.message.state, v.rows[2]!.message.state]).toEqual(['answered', 'closed']);
    // Opened from the other side, the title follows.
    expect(pairView(foldMessages(events), 'f', 'a').title).toBe('Frontend ⇄ Auth API');
  });

  it("names Desk second, hides start, and shows a message to Desk in its sender's transcript", () => {
    const v = pairView(foldMessages(events), 'd', 'a');
    expect(v.title).toBe('Auth API ⇄ Desk');
    expect(shape(v)).toEqual([
      { id: 10, shownIn: 'a', answers: [[12, 'a']] },
      { id: 11, shownIn: 'a', answers: [] },
    ]);
  });
});
