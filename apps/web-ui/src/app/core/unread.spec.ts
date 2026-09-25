import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';
import type { ProjectSummary } from '@desk/protocol';
import { lastActivity, Unread, unreadIn } from './unread';

const summary = (updated: string[], report?: string): ProjectSummary => ({
  project: { id: 'p', name: 'P', goal: '', updated_at: '2026-09-24T09:00:00.000Z' },
  desk_status: 'idle',
  threads: updated.map((u, i) => ({ id: `t${i}`, title: null, status: 'running', reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: u, updated_at: u })),
  latest_report: report ? { headline: 'h', ts: report } : null,
  plan_progress: { done: 0, total: 0 },
  attention_count: 0,
});

beforeEach(() => localStorage.clear());

describe('unread', () => {
  it('compares the last activity with the last visit', () => {
    const p = summary(['2026-09-24T10:00:00.000Z'], '2026-09-24T10:30:00.000Z');
    expect(lastActivity(p)).toBe('2026-09-24T10:30:00.000Z');
    expect(unreadIn(p, {})).toBe(true);
    expect(unreadIn(p, { p: '2026-09-24T11:00:00.000Z' })).toBe(false);
    expect(unreadIn(p, { p: '2026-09-24T10:15:00.000Z' })).toBe(true);
    expect(unreadIn(summary([]), {})).toBe(false);
  });

  it('persists visits', () => {
    const unread = TestBed.inject(Unread);
    unread.markSeen('p', '2026-09-24T11:00:00.000Z');
    expect(JSON.parse(localStorage.getItem('desk.seen')!)).toEqual({ p: '2026-09-24T11:00:00.000Z' });
    expect(unread.isUnread(summary(['2026-09-24T10:00:00.000Z']))).toBe(false);
  });
});
