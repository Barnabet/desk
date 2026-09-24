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
  nowX: number;
  ticks: Array<{ x: number; t: number }>;
  stations: Array<Station & { x: number; showLabel: boolean }>;
  lanes: LaneGeometry[];
};

/** Positions for the conversation's transit diagram: a time axis, Desk's trunk with stations, one lane per thread. */
export function lineGeometry(o: { timeline: TimelineState; threads: ThreadView[]; now: number; width: number; showArchived?: boolean }): LineGeometry {
  const x0 = LABEL_W + 10;
  const x1 = Math.max(x0 + 240, o.width - LEGEND_W - 30);
  const start = o.timeline.start ? Date.parse(o.timeline.start) : o.now - 30 * 60_000;
  const end = Math.max(o.now, start + 10 * 60_000);
  const x = (t: string | number) => x0 + (((typeof t === 'number' ? t : Date.parse(t)) - start) / (end - start)) * (x1 - x0);
  const step = (TICK_MINUTES.find((m) => (end - start) / 60_000 / m <= 8) ?? 10080) * 60_000;
  const ticks: Array<{ x: number; t: number }> = [];
  for (let t = Math.ceil(start / step) * step; t <= end; t += step) ticks.push({ x: x(t), t });
  const nowX = x(o.now);

  const byId = new Map(o.threads.map((t) => [t.id, t]));
  const visible = o.timeline.lanes.filter((l) => o.showArchived || !l.archived);
  const lanes = visible.map((lane, i): LaneGeometry => {
    const y = TRUNK_Y + FIRST_LANE + i * LANE_GAP;
    const xf = x(lane.forkedAt);
    const laneStart = xf + CURVE * 2;
    const terminal = lane.status === 'done' || lane.status === 'cancelled' || lane.status === 'failed';
    const segments = lane.segments
      .map((seg, idx) => {
        const last = idx === lane.segments.length - 1;
        const a = Math.max(laneStart, x(seg.from));
        const b = Math.max(a, last ? (terminal ? a : nowX) : x(seg.to ?? o.now));
        return { a, b, status: seg.status };
      })
      .filter((s) => s.b - s.a > 0.5)
      .map((s) => ({ d: `M${s.a} ${y} H${s.b}`, color: LANE_COLOR[s.status], dashed: s.status === 'idle' || s.status === 'queued' }));
    const marks = lane.marks.map((m) => ({ ...m, x: Math.max(laneStart, x(m.ts)) }));
    const rejoins = marks
      .filter((m) => m.kind === 'rejoin')
      .map((m) => `M${m.x} ${y} C${m.x + CURVE} ${y} ${m.x + CURVE} ${TRUNK_Y} ${m.x + CURVE * 2} ${TRUNK_Y}`);
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
    const sx = x(s.ts);
    const showLabel = sx - lastLabel >= 120;
    if (showLabel) lastLabel = sx;
    return { ...s, x: sx, showLabel };
  });

  const height = Math.max(120, TRUNK_Y + FIRST_LANE + Math.max(0, lanes.length - 1) * LANE_GAP + 34);
  return { width: o.width, height, x0, x1, trunkY: TRUNK_Y, nowX, ticks, stations, lanes };
}
