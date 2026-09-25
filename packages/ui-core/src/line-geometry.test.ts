import { describe, expect, it } from 'vitest';
import { emptyTimeline, reduceTimeline, type ThreadView } from '@desk/client';
import type { StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { LANE_COLOR, lineGeometry } from './line-geometry';

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

  it("draws a short stub past a finished lane's end while it answers, and none for a lane still in flight", () => {
    const now = Date.parse(at(60));
    const threads = [thread('a', 'done'), thread('b', 'running')];
    expect(lineGeometry({ timeline: timeline(), threads, now, width: 1440 }).lanes.map((l) => l.stub)).toEqual([null, null]);
    const g = lineGeometry({ timeline: timeline(), threads, now, width: 1440, answering: new Set(['a', 'b']) });
    const [a, b] = g.lanes;
    // Lane a ends at its rejoin; the stub runs 32 px straight on from there.
    const rx = Number(/^M(-?[\d.]+) /.exec(a!.rejoins[0]!)![1]);
    expect(a!.stub).toEqual({ d: `M${rx} ${a!.y} H${rx + 32}`, x: rx + 32 });
    expect(a!.stub!.x).toBeLessThanOrEqual(g.nowX);
    expect(b!.stub).toBeNull();
  });
});

/** Threads created (and running) at the given minutes; `doneAt` marks a thread done with a result then. */
function lanes(specs: Array<{ id: string; at: number; doneAt?: number; status?: 'cancelled' | 'failed' }>, briefAt = 0) {
  const events: StoredEvent[] = [ev(1, 'message.user', { text: 'Go' }, { agent: 'd', ts: at(briefAt) })];
  let n = 2;
  for (const s of specs) {
    events.push(ev(n++, 'agent.created', { role: 'thread', model: 'm', title: `T ${s.id}`, brief: 'b', workspace_path: '/w', parent_id: 'd' }, { agent: s.id, ts: at(s.at) }));
    events.push(ev(n++, 'agent.status_changed', { status: 'running' }, { agent: s.id, ts: at(s.at) }));
  }
  const ends = specs.filter((s) => s.doneAt !== undefined).sort((a, b) => a.doneAt! - b.doneAt!);
  for (const s of ends) {
    if (!s.status) events.push(ev(n++, 'agent.result', { summary: 'ok', artifacts: [] }, { agent: s.id, ts: at(s.doneAt!) }));
    events.push(ev(n++, 'agent.status_changed', { status: s.status ?? 'done' }, { agent: s.id, ts: at(s.doneAt!) }));
  }
  events.sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts) || a.id - b.id);
  return events.map((e, i) => ({ ...e, id: i + 1 })).reduce(reduceTimeline, emptyTimeline('d'));
}
const views = (specs: Array<{ id: string; doneAt?: number; status?: 'cancelled' | 'failed' }>) =>
  specs.map((s) => thread(s.id, s.doneAt === undefined ? 'running' : (s.status ?? 'done')));

describe('lineGeometry: finished lanes', () => {
  it('draws a done or stopped lane in grey end to end, a failed one in red, and a live one by its history', () => {
    const specs = [{ id: 'a', at: 1, doneAt: 30 }, { id: 'b', at: 2, doneAt: 20, status: 'cancelled' as const }, { id: 'c', at: 3, doneAt: 25, status: 'failed' as const }, { id: 'd', at: 4 }];
    const g = lineGeometry({ timeline: lanes(specs), threads: views(specs), now: Date.parse(at(40)), width: 1440 });
    const [a, b, c, d] = g.lanes;
    for (const l of [a!, b!]) {
      expect(l.segments.length).toBeGreaterThan(0);
      expect(l.segments.every((s) => s.color === LANE_COLOR.done && !s.dashed)).toBe(true);
      expect(l.forkColor).toBe(LANE_COLOR.done);
      expect(l.rejoinColor).toBe(LANE_COLOR.done);
    }
    expect(c!.segments.every((s) => s.color === LANE_COLOR.failed)).toBe(true);
    expect(d!.segments.at(-1)!.color).toBe(LANE_COLOR.running);
    // A lane that stopped without a result runs to where it stopped (it has no rejoin).
    const end = (l: typeof b) => Math.max(...l!.segments.map((s) => Number(/H(-?[\d.]+)/.exec(s.d)![1])));
    expect(b!.rejoins).toEqual([]);
    expect(end(b)).toBeGreaterThan(Number(/^M(-?[\d.]+)/.exec(b!.fork)![1]) + 100);
  });
});

describe('lineGeometry: time window', () => {
  const now = Date.parse(at(180));
  it('shows the last hour and scrolls the older history', () => {
    const specs = [{ id: 'a', at: 5, doneAt: 40 }];
    const g = lineGeometry({ timeline: lanes(specs), threads: views(specs), now, width: 1440 });
    expect(g.windowMs).toBe(60 * 60_000);
    expect(g.contentWidth).toBeGreaterThan(g.viewportWidth * 2.5);
    expect(g.nowX).toBeCloseTo(g.x1);
    // The hour before now fills the viewport, less its paddings.
    const hourPx = (60 * 60_000) * ((g.nowX - g.x0) / (now - g.startMs));
    expect(g.contentWidth - hourPx).toBeGreaterThan(0);
    expect(hourPx).toBeGreaterThan(g.viewportWidth - 80);
    expect(hourPx).toBeLessThanOrEqual(g.viewportWidth);
    for (let i = 1; i < g.ticks.length; i++) expect(g.ticks[i]!.x).toBeGreaterThan(g.ticks[i - 1]!.x);
    const visibleTicks = g.ticks.filter((t) => t.x >= g.contentWidth - g.viewportWidth);
    expect(visibleTicks.length).toBeGreaterThan(2);
    expect(visibleTicks.length).toBeLessThanOrEqual(9);
  });

  it('reaches back to the fork of the oldest thread still in flight', () => {
    const specs = [{ id: 'a', at: 10, doneAt: 50 }, { id: 'b', at: 60 }];
    const g = lineGeometry({ timeline: lanes(specs), threads: views(specs), now, width: 1440 });
    expect(g.windowMs).toBeGreaterThanOrEqual(120 * 60_000);
    expect(g.windowMs).toBeLessThan(130 * 60_000);
    const forkX = Number(/^M(-?[\d.]+)/.exec(g.lanes[1]!.fork)![1]);
    expect(forkX).toBeGreaterThanOrEqual(g.contentWidth - g.viewportWidth);
  });

  it('fits a history shorter than an hour without scrolling', () => {
    const specs = [{ id: 'a', at: 2, doneAt: 10 }];
    const g = lineGeometry({ timeline: lanes(specs), threads: views(specs), now: Date.parse(at(20)), width: 1440 });
    expect(g.contentWidth).toBe(g.viewportWidth);
    expect(g.windowMs).toBe(Date.parse(at(20)) - g.startMs);
  });
});

describe('lineGeometry: rows', () => {
  it('puts a new lane in the first row a finished lane has left, and never shares a row between live lanes', () => {
    const specs = [{ id: 'a', at: 1, doneAt: 10 }, { id: 'b', at: 1 }, { id: 'c', at: 30 }, { id: 'd', at: 31 }, { id: 'e', at: 32, doneAt: 33 }];
    const g = lineGeometry({ timeline: lanes(specs), threads: views(specs), now: Date.parse(at(40)), width: 1440 });
    const [a, b, c, d, e] = g.lanes;
    expect(c!.y).toBe(a!.y);
    expect(b!.y).toBeGreaterThan(a!.y);
    expect(d!.y).toBeGreaterThan(b!.y);
    expect(e!.y).toBeGreaterThan(d!.y);
    expect(new Set(g.lanes.map((l) => l.y)).size).toBe(4);
    expect(g.height).toBeLessThan(g.lanes.length * 36 + 140);
    // The label column names each row's latest lane; the earlier lane in a shared row is titled on the lane itself.
    expect(g.rows.map((r) => r.lane.threadId)).toEqual(['c', 'b', 'd', 'e']);
    expect(a!.inlineLabel).not.toBeNull();
    for (const l of [b, c, d, e]) expect(l!.inlineLabel).toBeNull();
  });

  it('keeps a row taken until the finished lane has rejoined', () => {
    const specs = [{ id: 'a', at: 1, doneAt: 20 }, { id: 'b', at: 20 }];
    const g = lineGeometry({ timeline: lanes(specs), threads: views(specs), now: Date.parse(at(40)), width: 1440 });
    expect(g.lanes[1]!.y).toBeGreaterThan(g.lanes[0]!.y);
  });
});
