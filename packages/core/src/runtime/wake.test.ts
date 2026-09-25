import { describe, expect, it } from 'vitest';
import type { AgentRole, AgentStatus } from '@desk/protocol';
import { wakeDecision, type Item, type Wake, type WakeState } from './wake';

const show = (w: Wake) => (w.kind === 'none' ? 'none' : `${w.kind}:${w.trigger}`);
const item = (id: number, from: Item['from'], kind: Item['kind']): Item => ({ id, from, kind });
const decide = (role: AgentRole, status: AgentStatus, pending: Item[], extra: Partial<WakeState> = {}) =>
  show(wakeDecision({ agent: { role, status, archived: false }, projectArchived: false, pendingApprovals: 0, pending, ...extra }));

/** A stop at event 50: items with id 100 arrived after it, items with id 10 before it. */
const STOPPED = 50;

type Row = { status: AgentStatus; archived?: boolean; extra?: Partial<WakeState>; itemId?: number };
const ROWS: Record<string, Row> = {
  running: { status: 'running' },
  queued: { status: 'queued' },
  'waiting on a reply': { status: 'waiting' },
  'waiting on an approval': { status: 'waiting', extra: { pendingApprovals: 1 } },
  idle: { status: 'idle' },
  done: { status: 'done' },
  failed: { status: 'failed' },
  'cancelled, sent after the stop': { status: 'cancelled', extra: { cancelledAt: STOPPED } },
  'cancelled, sent before the stop': { status: 'cancelled', extra: { cancelledAt: STOPPED }, itemId: 10 },
  archived: { status: 'done', archived: true },
  'in an archived project': { status: 'idle', extra: { projectArchived: true } },
};
const COLUMNS: Array<[string, Omit<Item, 'id'>]> = [
  ['user message', { from: 'user', kind: 'user' }],
  ['Desk note', { from: 'desk', kind: 'note' }],
  ['Desk revision', { from: 'desk', kind: 'revision' }],
  ['thread note', { from: 'thread', kind: 'note' }],
];
/** Design spec §3.3, thread recipients: user message, Desk note, Desk revision, thread note. */
const TABLE: Record<string, [string, string, string, string]> = {
  running: ['none', 'none', 'none', 'none'],
  queued: ['run:queued', 'run:queued', 'run:queued', 'run:queued'],
  'waiting on a reply': ['run:user', 'run:agent', 'run:agent', 'none'],
  'waiting on an approval': ['none', 'none', 'none', 'none'],
  idle: ['run:user', 'run:agent', 'run:agent', 'none'],
  done: ['run:user', 'none', 'run:agent', 'none'],
  failed: ['run:user', 'none', 'run:agent', 'none'],
  'cancelled, sent after the stop': ['run:user', 'none', 'none', 'none'],
  'cancelled, sent before the stop': ['none', 'none', 'none', 'none'],
  archived: ['none', 'none', 'none', 'none'],
  'in an archived project': ['none', 'none', 'none', 'none'],
};
const CASES = Object.entries(TABLE).flatMap(([row, cells]) => COLUMNS.map(([col, sent], i) => ({ row, col, sent, expected: cells[i]! })));

describe('wakeDecision: the delivery table for threads', () => {
  it.each(CASES)('$row × $col → $expected', ({ row, sent, expected }) => {
    const r = ROWS[row]!;
    const state: WakeState = {
      agent: { role: 'thread', status: r.status, archived: r.archived ?? false },
      projectArchived: false,
      pendingApprovals: 0,
      pending: [{ id: r.itemId ?? 100, ...sent }],
      ...r.extra,
    };
    expect(show(wakeDecision(state))).toBe(expected);
  });
});

describe('wakeDecision: threads', () => {
  it('does nothing when nothing is pending', () => {
    for (const status of ['idle', 'waiting', 'done', 'failed'] as const) expect(decide('thread', status, [])).toBe('none');
  });

  it('runs an idle thread for a Desk note even with a sibling note pending too', () => {
    expect(decide('thread', 'idle', [item(1, 'thread', 'note'), item(2, 'desk', 'note')])).toBe('run:agent');
  });

  it('resumes a stopped thread for a user message sent after the stop, whatever else is pending', () => {
    expect(decide('thread', 'cancelled', [item(60, 'desk', 'note'), item(100, 'user', 'user')], { cancelledAt: STOPPED })).toBe('run:user');
  });
});

describe('wakeDecision: Desk', () => {
  it('runs for anything pending, with the trigger of what is pending', () => {
    expect(decide('desk', 'idle', [])).toBe('none');
    expect(decide('desk', 'idle', [item(1, 'thread', 'completed')])).toBe('run:lifecycle');
    expect(decide('desk', 'idle', (['failed', 'cancelled', 'approval', 'stalled'] as const).map((k, i) => item(i + 1, 'thread', k)))).toBe('run:lifecycle');
    expect(decide('desk', 'idle', [item(1, 'desk', 'reminder')])).toBe('run:lifecycle');
    expect(decide('desk', 'idle', [item(1, 'thread', 'question')])).toBe('run:agent');
    expect(decide('desk', 'idle', [item(1, 'thread', 'completed'), item(2, 'thread', 'update')])).toBe('run:agent');
    expect(decide('desk', 'waiting', [item(1, 'thread', 'blocker')])).toBe('run:agent');
    expect(decide('desk', 'failed', [item(1, 'thread', 'completed')])).toBe('run:lifecycle');
    expect(decide('desk', 'idle', [item(1, 'thread', 'completed'), item(2, 'user', 'user')])).toBe('run:user');
  });

  it('runs a stopped Desk only for a user message sent after the stop', () => {
    const stopped = { cancelledAt: STOPPED };
    expect(decide('desk', 'cancelled', [item(60, 'thread', 'completed')], stopped)).toBe('none');
    expect(decide('desk', 'cancelled', [item(10, 'user', 'user')], stopped)).toBe('none');
    expect(decide('desk', 'cancelled', [item(60, 'thread', 'question'), item(100, 'user', 'user')], stopped)).toBe('run:user');
  });

  it('never runs a running, blocked or archived Desk, and keeps a queued one queued', () => {
    expect(decide('desk', 'running', [item(1, 'user', 'user')])).toBe('none');
    expect(decide('desk', 'idle', [item(1, 'user', 'user')], { pendingApprovals: 1 })).toBe('none');
    expect(decide('desk', 'idle', [item(1, 'user', 'user')], { projectArchived: true })).toBe('none');
    expect(decide('desk', 'queued', [])).toBe('run:queued');
  });
});
