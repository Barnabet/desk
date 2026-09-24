import { describe, expect, it } from 'vitest';
import type { AttentionItem, ProjectSummary } from '@desk/protocol';
import { miniLine } from './miniLine';

const now = Date.parse('2026-09-24T11:27:00.000Z');
const at = (min: number) => new Date(now - min * 60_000).toISOString();
const thread = (id: string, status: string, created: number, updated: number, title = id) => ({ id, title, status, reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: at(created), updated_at: at(updated) });
const project = (id: string, threads: ReturnType<typeof thread>[]) =>
  ({ project: { id, name: id, goal: '', updated_at: at(0) }, desk_status: 'idle', threads, latest_report: null, plan_progress: { done: 0, total: 0 }, attention_count: 0 }) as unknown as ProjectSummary;

describe('miniLine', () => {
  it('forks the busiest threads, newest nearest the trunk, with signals and trains', () => {
    const overview = [
      project('a', [thread('funnel', 'running', 20, 0), thread('emails', 'running', 60, 0), thread('old', 'done', 60 * 30, 60 * 30)]),
      project('b', [thread('checklist', 'waiting', 90, 10), thread('backup', 'running', 120, 22), thread('extra', 'done', 100, 50)]),
    ];
    const attention = [
      { id: 'approval:x', kind: 'approval', project_id: 'b', project_name: 'b', agent_id: 'checklist', title: '', detail: '', created_at: at(10), ref: { thread_id: 'checklist' } },
      { id: 'stalled:backup:1', kind: 'stalled', project_id: 'b', project_name: 'b', agent_id: 'backup', title: '', detail: '', created_at: at(22), ref: { thread_id: 'backup' } },
    ] as AttentionItem[];
    const m = miniLine({ overview, attention, now });
    expect(m.lanes.map((l) => l.id)).toEqual(['funnel', 'emails', 'checklist', 'backup']);
    expect(m.lanes.map((l) => l.y)).toEqual([...m.lanes.map((l) => l.y)].sort((a, b) => a - b));
    expect(m.lanes[0]!.end).toEqual({ x: m.nowX, kind: 'train' });
    expect(m.lanes[2]!.end.kind).toBe('signal');
    expect(m.lanes[3]).toMatchObject({ title: 'backup · stalled', dashed: true });
    expect(m.lanes[3]!.end.kind).toBe('train');
    for (const l of m.lanes) expect(l.y).toBeLessThan(m.height);
  });

  it('draws just the trunk on a quiet day', () => {
    const m = miniLine({ overview: [project('a', [thread('old', 'done', 60 * 30, 60 * 30)])], attention: [], now });
    expect(m.lanes).toEqual([]);
    expect(m.trunk).toBe(`M8 18 H ${m.nowX}`);
  });
});
