import { describe, expect, it } from 'vitest';
import { ev } from '../testing';
import { emptyTimeline, reduceTimeline } from './timeline';

describe('reduceTimeline', () => {
  it('draws Desk stations and thread lanes with forks, rejoins, a revision loop, a detour and a signal', () => {
    const d = { agent: 'd' };
    const thread = (id: number, t: string, title: string) => ev(id, 'agent.created', { role: 'thread', model: 'm', title, brief: 'b', workspace_path: '/w', parent_id: 'd' }, { agent: t });
    const events = [
      ev(1, 'message.user', { text: 'Relaunch onboarding next month' }, d),
      thread(2, 'a', 'Research'),
      thread(3, 'b', 'Emails'),
      thread(4, 'c', 'Checklist'),
      ev(5, 'agent.status_changed', { status: 'running' }, { agent: 'a' }),
      ev(6, 'agent.status_changed', { status: 'running' }, { agent: 'b' }),
      ev(7, 'agent.model_switched', { from: 'opus', to: 'fable', reason: 'rate', scope: 'run' }, { agent: 'b' }),
      ev(8, 'agent.result', { summary: 'Summary of three flows', artifacts: [] }, { agent: 'a' }),
      ev(9, 'agent.status_changed', { status: 'done' }, { agent: 'a' }),
      ev(10, 'agent.result', { summary: 'Five drafts', artifacts: [] }, { agent: 'b' }),
      ev(11, 'agent.status_changed', { status: 'done' }, { agent: 'b' }),
      ev(12, 'agent.revision', { round: 1, feedback: 'Less salesy' }, { agent: 'b' }),
      ev(13, 'agent.status_changed', { status: 'running' }, { agent: 'b' }),
      ev(14, 'approval.requested', { approval_id: 'x', run_id: 'r', tool_call_id: 'c', tool: 'bash', arguments: '{}', reason: 'r', delegate_to_desk: false }, { agent: 'c' }),
      ev(15, 'report', { headline: 'Research is in', progress: '', needs_you: [], results: [] }, d),
      ev(16, 'question.asked', { question: 'Data source first?' }, d),
    ];
    const s = events.reduce(reduceTimeline, emptyTimeline('d'));
    expect(s.start).toBe(events[0]!.ts);
    expect(s.stations.map((x) => [x.kind, x.label])).toEqual([
      ['brief', 'Relaunch onboarding next month'],
      ['dispatch', '3 threads'],
      ['result_in', 'Research'],
      ['result_in', 'Emails'],
      ['sent_back', 'Emails · round 1'],
      ['report', 'Research is in'],
      ['question', 'Data source first?'],
    ]);
    expect(s.stations[1]!.threadIds).toEqual(['a', 'b', 'c']);
    const emails = s.lanes.find((l) => l.threadId === 'b')!;
    expect(emails.marks.map((m) => m.kind)).toEqual(['detour', 'rejoin', 'sent_back']);
    expect(emails.segments.map((x) => x.status)).toEqual(['idle', 'running', 'done', 'running']);
    expect(emails.segments.at(-1)!.to).toBeNull();
    expect(s.lanes.find((l) => l.threadId === 'c')!.marks).toEqual([expect.objectContaining({ kind: 'signal', label: 'bash' })]);

    const s2 = reduceTimeline(s, ev(17, 'approval.resolved', { approval_id: 'x', decision: 'approved', resolved_by: 'user' }, { agent: 'c' }));
    expect(s2.lanes.find((l) => l.threadId === 'c')!.marks.map((m) => m.kind)).toEqual(['signal', 'signal_cleared']);
    const s3 = reduceTimeline(s2, ev(18, 'message.agent', { from_agent_id: 'c', from_label: 'l', kind: 'stalled', text: 'No activity' }, d));
    expect(s3.lanes.find((l) => l.threadId === 'c')!.marks.at(-1)!.kind).toBe('stalled');
    expect(reduceTimeline(s3, ev(18, 'agent.archived', {}, { agent: 'a' }))).toBe(s3);
  });

  it("marks a tracked question on its asker's lane, labelled with its recipient", () => {
    const ts = (id: number) => new Date(Date.UTC(2026, 8, 24, 10, 0, id)).toISOString();
    const thread = (id: number, t: string, title: string) => ev(id, 'agent.created', { role: 'thread', model: 'm', title, brief: 'b', workspace_path: '/w', parent_id: 'd' }, { agent: t });
    const ask = (id: number, from: string, to: string, tracked = true) =>
      ev(id, 'message.agent', { from_agent_id: from, from_label: from === 'd' ? 'Desk' : `thread "${from}" (${from})`, kind: 'question', text: 'q', ...(tracked ? { tracked: true as const } : {}) }, { agent: to });
    const s = [
      thread(1, 'a', 'Auth API'),
      thread(2, 'f', 'Frontend'),
      ask(3, 'a', 'f'),
      ask(4, 'f', 'd'),
      // Desk has no lane, and an untracked question has no state to show.
      ask(5, 'd', 'a'),
      ask(6, 'a', 'd', false),
      ev(7, 'message.agent', { from_agent_id: 'a', from_label: 'thread "a" (a)', kind: 'note', text: 'n' }, { agent: 'f' }),
    ].reduce(reduceTimeline, emptyTimeline('d'));
    expect(s.lanes.find((l) => l.threadId === 'a')!.marks).toEqual([{ eventId: 3, ts: ts(3), kind: 'question', label: 'Frontend' }]);
    expect(s.lanes.find((l) => l.threadId === 'f')!.marks).toEqual([{ eventId: 4, ts: ts(4), kind: 'question', label: 'Desk' }]);
  });
});
