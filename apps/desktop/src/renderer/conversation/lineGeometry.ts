import type { Lane, LaneMark, Station, ThreadView, TimelineState } from '@desk/client';
import type { AgentStatus } from '@desk/protocol';

export const LANE_COLOR: Record<AgentStatus, string> = {
  running: '#2F5BD3',
  waiting: '#A15C00',
  queued: '#A15C00',
  idle: '#B9B3A7',
  done: '#8A857B',
  cancelled: '#8A857B',
  failed: '#C4441C',
};

const TICK_MINUTES = [1, 2, 5, 10, 15, 30, 60, 120, 240, 480, 720, 1440, 2880, 10080];
export const LABEL_W = 200;
const LEGEND_W = 120;
const TRUNK_Y = 62;
const FIRST_LANE = 44;
const LANE_GAP = 36;
const CURVE = 30;
const STUB = 16;
const MIN_SPAN = 3 * 60_000;

export type LaneGeometry = {
  lane: Lane;
  thread: ThreadView | undefined;
  y: number;
  color: string;
  fork: string;
  segments: Array<{ d: string; color: string; dashed: boolean }>;
  rejoins: string[];
  marks: Array<LaneMark & { x: number }>;
  signal: (LaneMark & { x: number }) | null;
  trainX: number | null;
};

export type LineGeometry = {
  width: number;
  height: number;
  x0: number;
  x1: number;
  trunkY: number;
  /** Where Desk's line begins: the first event. */
  trunkStart: number;
  nowX: number;
  ticks: Array<{ x: number; t: number }>;
  stations: Array<Station & { x: number; showLabel: boolean }>;
  lanes: LaneGeometry[];
};

/** Positions for the conversation's transit diagram: a time axis, Desk's trunk with stations, one lane per thread. */
export function lineGeometry(o: { timeline: TimelineState; threads: ThreadView[]; now: number; width: number; showArchived?: boolean }): LineGeometry {
  const x0 = LABEL_W + 10;
  const x1 = Math.max(x0 + 240, o.width - LEGEND_W - 30);
  // The axis always ends at now and spans at least three minutes, with a little air before the brief.
  const first = o.timeline.start ? Date.parse(o.timeline.start) : o.now - 30 * 60_000;
  const end = o.now;
  const start = Math.min(first - (end - first) * 0.04, end - MIN_SPAN);
  const x = (t: string | number) => x0 + (((typeof t === 'number' ? t : Date.parse(t)) - start) / (end - start)) * (x1 - x0);
  const step = (TICK_MINUTES.find((m) => (end - start) / 60_000 / m <= 8) ?? 10080) * 60_000;
  const ticks: Array<{ x: number; t: number }> = [];
  // Ticks that would sit under the "now" pill are dropped.
  for (let t = Math.ceil(start / step) * step; t <= end; t += step) if (x1 - x(t) > 56) ticks.push({ x: x(t), t });
  const nowX = x(o.now);
  const trunkStart = o.timeline.start ? x(o.timeline.start) : x0;
  const latest = Math.max(x0, nowX - CURVE * 2);

  const byId = new Map(o.threads.map((t) => [t.id, t]));
  const visible = o.timeline.lanes.filter((l) => o.showArchived || !l.archived);
  const lanes = visible.map((lane, i): LaneGeometry => {
    const y = TRUNK_Y + FIRST_LANE + i * LANE_GAP;
    // Leave room for the fork, a short stub of lane and the rejoin before now.
    const xf = Math.max(x0, Math.min(x(lane.forkedAt), nowX - CURVE * 4 - STUB));
    const laneStart = xf + CURVE * 2;
    const terminal = lane.status === 'done' || lane.status === 'cancelled' || lane.status === 'failed';
    const marks = lane.marks.map((m) => ({ ...m, x: Math.min(nowX, Math.max(laneStart, x(m.ts))) }));
    const rejoinXs = marks.filter((m) => m.kind === 'rejoin').map((m) => Math.min(Math.max(m.x, laneStart + STUB), latest));
    const lastRejoin = rejoinXs.at(-1);
    const segments = lane.segments
      .map((seg, idx) => {
        const last = idx === lane.segments.length - 1;
        const laneEnd = terminal ? (lastRejoin ?? laneStart) : nowX;
        const a = idx === 0 ? laneStart : Math.min(Math.max(laneStart, x(seg.from)), laneEnd);
        const b = Math.min(Math.max(a, last ? laneEnd : x(seg.to ?? o.now)), laneEnd);
        return { a, b, status: seg.status };
      })
      .filter((s) => s.b - s.a > 0.5)
      .map((s) => ({ d: `M${s.a} ${y} H${s.b}`, color: LANE_COLOR[s.status], dashed: s.status === 'idle' || s.status === 'queued' }));
    const rejoins = rejoinXs.map((rx) => `M${rx} ${y} C${rx + CURVE} ${y} ${rx + CURVE} ${TRUNK_Y} ${rx + CURVE * 2} ${TRUNK_Y}`);
    let signal: (LaneMark & { x: number }) | null = null;
    for (const m of marks) {
      if (m.kind === 'signal') signal = m;
      if (m.kind === 'signal_cleared') signal = null;
    }
    return {
      lane,
      thread: byId.get(lane.threadId),
      y,
      color: LANE_COLOR[lane.status],
      fork: `M${xf} ${TRUNK_Y} C${xf + CURVE} ${TRUNK_Y} ${xf + CURVE} ${y} ${laneStart} ${y}`,
      segments,
      rejoins,
      marks,
      signal,
      trainX: byId.get(lane.threadId)?.status === 'running' ? nowX : null,
    };
  });

  let lastLabel = -Infinity;
  const stations = o.timeline.stations.map((s) => {
    const sx = Math.min(nowX, x(s.ts));
    const showLabel = sx - lastLabel >= 120;
    if (showLabel) lastLabel = sx;
    return { ...s, x: sx, showLabel };
  });

  const height = Math.max(120, TRUNK_Y + FIRST_LANE + Math.max(0, lanes.length - 1) * LANE_GAP + 34);
  const firstFork = Math.min(...lanes.map((l) => Number(/^M(-?[\d.]+)/.exec(l.fork)?.[1] ?? Infinity)));
  return { width: o.width, height, x0, x1, trunkY: TRUNK_Y, trunkStart: Math.min(trunkStart, firstFork), nowX, ticks, stations, lanes };
}
