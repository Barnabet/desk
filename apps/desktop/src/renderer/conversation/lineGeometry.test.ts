import { describe, expect, it } from 'vitest';
import { emptyTimeline, reduceTimeline, type ThreadView } from '@desk/client';
import { ev } from '@desk/client/testing';
import { lineGeometry } from './lineGeometry';

const at = (min: number) => new Date(Date.UTC(2026, 8, 24, 10, min)).toISOString();
const thread = (id: string, status: ThreadView['status'], activity: string | null = null) => ({ id, status, activity }) as ThreadView;

function timeline() {
  const d = { agent: 'd' };
  const created = (id: number, t: string, title: string, min: number) =>
    ev(id, 'agent.created', { role: 'thread', model: 'm', title, brief: 'b', workspace_path: '/w', parent_id: 'd' }, { agent: t, ts: at(min) });
  return [
    ev(1, 'message.user', { text: 'Relaunch onboarding' }, { ...d, ts: at(0) }),
    created(2, 'a', 'Research', 1),
    created(3, 'b', 'Emails', 1),
    ev(4, 'agent.status_changed', { status: 'running' }, { agent: 'a', ts: at(2) }),
    ev(5, 'agent.status_changed', { status: 'running' }, { agent: 'b', ts: at(2) }),
    ev(6, 'agent.model_switched', { from: 'opus', to: 'fable', reason: 'rate', scope: 'run' }, { agent: 'b', ts: at(10) }),
    ev(7, 'agent.result', { summary: 'Done', artifacts: [] }, { agent: 'a', ts: at(30) }),
    ev(8, 'agent.status_changed', { status: 'done' }, { agent: 'a', ts: at(30) }),
    ev(9, 'approval.requested', { approval_id: 'x', run_id: 'r', tool_call_id: 'c', tool: 'bash', arguments: '{}', reason: 'r', delegate_to_desk: false }, { agent: 'b', ts: at(40) }),
    ev(10, 'report', { headline: 'Research is in', progress: '', needs_you: [], results: [] }, { ...d, ts: at(31) }),
  ].reduce(reduceTimeline, emptyTimeline('d'));
}

describe('lineGeometry', () => {
  it('lays out time, stations and lanes', () => {
    const now = Date.parse(at(60));
    const g = lineGeometry({ timeline: timeline(), threads: [thread('a', 'done'), thread('b', 'running', 'bash · ls')], now, width: 1440 });
    expect(g.nowX).toBeCloseTo(g.x1);
    expect(g.ticks.length).toBeGreaterThan(2);
    expect(g.ticks.length).toBeLessThanOrEqual(9);
    for (let i = 1; i < g.ticks.length; i++) expect(g.ticks[i]!.x).toBeGreaterThan(g.ticks[i - 1]!.x);
    expect(g.stations.map((s) => s.kind)).toEqual(['brief', 'dispatch', 'result_in', 'report']);
    expect(g.stations[0]!.showLabel).toBe(true);
    const [a, b] = g.lanes;
    expect(b!.y).toBeGreaterThan(a!.y);
    expect(a!.rejoins).toHaveLength(1);
    expect(a!.trainX).toBeNull();
    expect(b!.trainX).toBe(g.nowX);
    expect(b!.marks.map((m) => m.kind)).toEqual(['detour', 'signal']);
    expect(b!.signal?.label).toBe('bash');
    expect(a!.signal).toBeNull();
    expect(g.height).toBeGreaterThan(b!.y);
    expect(g.trunkStart).toBeGreaterThan(g.x0);
  });

  it('keeps forks and rejoins that happen just now inside the diagram', () => {
    const now = Date.parse(at(31)) + 500;
    const g = lineGeometry({ timeline: timeline(), threads: [thread('a', 'done'), thread('b', 'running')], now: Date.parse(at(60)), width: 1440 });
    const late = lineGeometry({ timeline: timeline(), threads: [thread('a', 'done'), thread('b', 'running')], now, width: 1440 });
    for (const geo of [g, late]) {
      for (const l of geo.lanes) {
        const xs = [...l.fork.matchAll(/-?\d+(?:\.\d+)?/g)].map((m) => Number(m[0])).filter((_, i) => i % 2 === 0);
        expect(Math.max(...xs)).toBeLessThanOrEqual(geo.nowX + 0.01);
        for (const r of l.rejoins) {
          const rx = [...r.matchAll(/-?\d+(?:\.\d+)?/g)].map((m) => Number(m[0])).filter((_, i) => i % 2 === 0);
          expect(Math.max(...rx)).toBeLessThanOrEqual(geo.nowX + 0.01);
        }
      }
      for (const st of geo.stations) expect(st.x).toBeLessThanOrEqual(geo.nowX);
      const done = geo.lanes[0]!;
      expect(done.segments.length).toBeGreaterThan(0);
    }
  });

  it('draws a still-empty project around now', () => {
    const now = Date.parse(at(5));
    const g = lineGeometry({ timeline: emptyTimeline('d'), threads: [], now, width: 1200 });
    expect(g.lanes).toEqual([]);
    expect(g.nowX).toBeGreaterThan(g.x0);
  });
});
