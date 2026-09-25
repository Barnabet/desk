import { describe, expect, it } from 'vitest';
import type { AgentRole, AgentStatus } from '@desk/protocol';
import { wakeDecision, type Item, type Wake, type WakeState } from './wake';

const show = (w: Wake) => (w.kind === 'none' ? 'none' : `${w.kind}:${w.trigger}`);
const item = (id: number, from: Item['from'], kind: Item['kind']): Item => ({ id, from, kind });
const state = (role: AgentRole, status: AgentStatus, pending: Item[], extra: Partial<WakeState> = {}): WakeState => ({
  agent: { role, status, archived: false },
  projectArchived: false,
  pendingApprovals: 0,
  pending,
  open: [],
  ...extra,
});
const decide = (role: AgentRole, status: AgentStatus, pending: Item[], extra: Partial<WakeState> = {}) => show(wakeDecision(state(role, status, pending, extra)));

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
  ['user Ask', { from: 'user', kind: 'user_question' }],
  ['Desk note', { from: 'desk', kind: 'note' }],
  ['Desk start', { from: 'desk', kind: 'start' }],
  ['Desk answer', { from: 'desk', kind: 'answer' }],
  ['Desk question', { from: 'desk', kind: 'question' }],
  ['Desk revision', { from: 'desk', kind: 'revision' }],
  ['thread question', { from: 'thread', kind: 'question' }],
  ['thread note', { from: 'thread', kind: 'note' }],
  ['thread answer', { from: 'thread', kind: 'answer' }],
];
const all = (cell: string) => COLUMNS.map(() => cell);
/**
 * Design spec §3.3, thread recipients, one cell per column above. `answer:*` is an answer run. The tool refusals of the
 * table (a note to a finished or stopped thread, anything to an archived one) are S3's; here those items are stored.
 */
const TABLE: Record<string, string[]> = {
  running: all('none'),
  queued: all('run:queued'),
  'waiting on a reply': ['run:user', 'run:user', 'run:agent', 'run:agent', 'run:agent', 'run:agent', 'run:agent', 'answer:agent', 'none', 'run:agent'],
  'waiting on an approval': all('none'),
  idle: ['run:user', 'answer:user', 'run:agent', 'run:lifecycle', 'run:agent', 'answer:agent', 'run:agent', 'answer:agent', 'none', 'run:agent'],
  done: ['run:user', 'answer:user', 'none', 'none', 'none', 'answer:agent', 'run:agent', 'answer:agent', 'none', 'none'],
  failed: ['run:user', 'answer:user', 'none', 'none', 'none', 'answer:agent', 'run:agent', 'answer:agent', 'none', 'none'],
  'cancelled, sent after the stop': ['run:user', 'run:user', 'none', 'none', 'none', 'none', 'none', 'none', 'none', 'none'],
  'cancelled, sent before the stop': all('none'),
  archived: all('none'),
  'in an archived project': all('none'),
};
const CASES = Object.entries(TABLE).flatMap(([row, cells]) => COLUMNS.map(([col, sent], i) => ({ row, col, sent, expected: cells[i]! })));

describe('wakeDecision: the delivery table for threads', () => {
  it.each(CASES)('$row × $col → $expected', ({ row, sent, expected }) => {
    const r = ROWS[row]!;
    const id = r.itemId ?? 100;
    const s: WakeState = {
      agent: { role: 'thread', status: r.status, archived: r.archived ?? false },
      projectArchived: false,
      pendingApprovals: 0,
      pending: [{ id, ...sent }],
      // A tracked question is open until it is answered, whether or not it was read.
      open: sent.kind === 'question' ? [{ id, seen: false }] : [],
      ...r.extra,
    };
    expect(show(wakeDecision(s))).toBe(expected);
  });
});

describe('wakeDecision: threads', () => {
  it('does nothing when nothing is pending or open', () => {
    for (const status of ['idle', 'waiting', 'done', 'failed'] as const) expect(decide('thread', status, [])).toBe('none');
  });

  it('runs an idle thread for a Desk note even with a sibling note pending too', () => {
    expect(decide('thread', 'idle', [item(1, 'thread', 'note'), item(2, 'desk', 'note')])).toBe('run:agent');
  });

  it('resumes a stopped thread for a user message sent after the stop, whatever else is pending', () => {
    expect(decide('thread', 'cancelled', [item(60, 'desk', 'note'), item(100, 'user', 'user')], { cancelledAt: STOPPED })).toBe('run:user');
  });
});

describe('wakeDecision: questions', () => {
  it('answers the oldest open question, read or not, in every status a thread answers from', () => {
    const open = [
      { id: 20, seen: true },
      { id: 40, seen: false },
    ];
    for (const status of ['waiting', 'idle', 'done', 'failed'] as const) {
      expect(wakeDecision(state('thread', status, [item(40, 'thread', 'question')], { open }))).toEqual({ kind: 'answer', question: 20, trigger: 'agent' });
    }
  });

  it("puts the user's Ask in line with the open questions, oldest first, and runs a waiting thread for it", () => {
    const ask = [item(30, 'user', 'user_question')];
    expect(wakeDecision(state('thread', 'done', ask, { open: [{ id: 20, seen: true }] }))).toEqual({ kind: 'answer', question: 20, trigger: 'agent' });
    expect(wakeDecision(state('thread', 'idle', ask, { open: [{ id: 40, seen: false }] }))).toEqual({ kind: 'answer', question: 30, trigger: 'user' });
    expect(wakeDecision(state('thread', 'waiting', ask, { open: [{ id: 40, seen: false }] }))).toEqual({ kind: 'run', trigger: 'user' });
  });

  it('runs a thread for what wakes it before it answers anything, and never answers while blocked or stopped', () => {
    const open = [{ id: 5, seen: true }];
    expect(decide('thread', 'waiting', [item(6, 'thread', 'answer')], { open })).toBe('run:agent');
    expect(decide('thread', 'idle', [item(6, 'thread', 'answer')], { open })).toBe('run:agent');
    expect(decide('thread', 'done', [item(6, 'desk', 'revision')], { open })).toBe('run:agent');
    expect(decide('thread', 'idle', [item(6, 'desk', 'note')], { open })).toBe('run:agent');
    expect(decide('thread', 'done', [], { open, pendingApprovals: 1 })).toBe('none');
    expect(decide('thread', 'cancelled', [], { open, cancelledAt: 1 })).toBe('none');
    expect(decide('thread', 'done', [], { open, agent: { role: 'thread', status: 'done', archived: true } })).toBe('none');
  });
});

describe('wakeDecision: Desk', () => {
  it('runs for anything pending, with the trigger of what is pending', () => {
    expect(decide('desk', 'idle', [])).toBe('none');
    expect(decide('desk', 'idle', [item(1, 'thread', 'completed')])).toBe('run:lifecycle');
    expect(decide('desk', 'idle', (['failed', 'cancelled', 'approval', 'stalled'] as const).map((k, i) => item(i + 1, 'thread', k)))).toBe('run:lifecycle');
    expect(decide('desk', 'idle', [item(1, 'desk', 'reminder')])).toBe('run:lifecycle');
    expect(decide('desk', 'idle', [item(1, 'thread', 'question')])).toBe('run:agent');
    expect(decide('desk', 'idle', [item(1, 'thread', 'answer')])).toBe('run:agent');
    expect(decide('desk', 'idle', [item(1, 'thread', 'completed'), item(2, 'thread', 'update')])).toBe('run:agent');
    expect(decide('desk', 'waiting', [item(1, 'thread', 'blocker')])).toBe('run:agent');
    expect(decide('desk', 'failed', [item(1, 'thread', 'completed')])).toBe('run:lifecycle');
    expect(decide('desk', 'idle', [item(1, 'thread', 'completed'), item(2, 'user', 'user')])).toBe('run:user');
  });

  it('never gets an answer run: a question it has read waits for its next run', () => {
    expect(decide('desk', 'idle', [], { open: [{ id: 5, seen: true }] })).toBe('none');
    expect(decide('desk', 'done', [], { open: [{ id: 5, seen: true }] })).toBe('none');
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
