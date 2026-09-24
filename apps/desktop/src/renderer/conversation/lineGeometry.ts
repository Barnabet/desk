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
/** The visible window: the last hour, or back to the fork of the oldest thread still in flight. */
const WINDOW_MS = 60 * 60_000;
const PAD_L = 16;
const PAD_R = 50;
/** Space kept between a finished lane's rejoin and the next lane forking into its row. */
const ROW_GAP = 12;
const IN_FLIGHT = new Set<AgentStatus>(['running', 'waiting', 'queued']);
const TERMINAL = new Set<AgentStatus>(['done', 'cancelled', 'failed']);

export type LaneGeometry = {
  lane: Lane;
  thread: ThreadView | undefined;
  row: number;
  y: number;
  color: string;
  forkColor: string;
  rejoinColor: string;
  fork: string;
  segments: Array<{ d: string; color: string; dashed: boolean }>;
  rejoins: string[];
  marks: Array<LaneMark & { x: number }>;
  signal: (LaneMark & { x: number }) | null;
  trainX: number | null;
  /** Where the lane's own title is drawn: set for a lane that shares its row with a later one (the label column names a row's latest lane). */
  inlineLabel: { x: number; width: number } | null;
};

export type LineGeometry = {
  width: number;
  height: number;
  /** The scrolling part of the diagram: its left edge, its visible width, and the width of everything it holds. Every x below is in its coordinates. */
  viewportLeft: number;
  viewportWidth: number;
  contentWidth: number;
  /** The time span the viewport shows, and the first instant drawn. */
  windowMs: number;
  startMs: number;
  x0: number;
  x1: number;
  trunkY: number;
  /** Where Desk's line begins: the first event. */
  trunkStart: number;
  nowX: number;
  ticks: Array<{ x: number; t: number }>;
  stations: Array<Station & { x: number; showLabel: boolean }>;
  lanes: LaneGeometry[];
  /** One entry per row, top to bottom: the row's latest lane, which the label column names. */
  rows: LaneGeometry[];
};

/**
 * Positions for the conversation's transit diagram: a time axis, Desk's trunk with stations, and thread lanes.
 * The viewport shows the last hour (or back to the oldest thread still in flight); older history scrolls to the left.
 * A new lane takes the first row a finished lane has left.
 */
export function lineGeometry(o: { timeline: TimelineState; threads: ThreadView[]; now: number; width: number; showArchived?: boolean }): LineGeometry {
  const viewportLeft = LABEL_W;
  const viewportWidth = Math.max(260, o.width - LABEL_W - LEGEND_W - 20);
  // The axis always ends at now and spans at least three minutes, with a little air before the brief.
  const first = o.timeline.start ? Date.parse(o.timeline.start) : o.now - 30 * 60_000;
  const end = o.now;
  const startMs = Math.min(first - (end - first) * 0.04, end - MIN_SPAN);
  const history = end - startMs;
  const byId = new Map(o.threads.map((t) => [t.id, t]));
  const visible = o.timeline.lanes.filter((l) => o.showArchived || !l.archived);
  const inFlight = visible.filter((l) => IN_FLIGHT.has(l.status)).map((l) => Date.parse(l.forkedAt));
  const wanted = inFlight.length ? Math.max(WINDOW_MS, (end - Math.min(...inFlight)) * 1.04) : WINDOW_MS;
  const windowMs = Math.min(history, wanted);
  const perMs = (viewportWidth - PAD_L - PAD_R) / windowMs;
  const contentWidth = Math.max(viewportWidth, Math.round(PAD_L + history * perMs + PAD_R));
  const x0 = PAD_L;
  const x = (t: string | number) => x0 + ((typeof t === 'number' ? t : Date.parse(t)) - startMs) * perMs;
  const step = (TICK_MINUTES.find((m) => windowMs / 60_000 / m <= 8) ?? 10080) * 60_000;
  const ticks: Array<{ x: number; t: number }> = [];
  const nowX = x(end);
  // Ticks that would sit under the "now" pill are dropped.
  for (let t = Math.ceil(startMs / step) * step; t <= end; t += step) if (nowX - x(t) > 56) ticks.push({ x: x(t), t });
  const trunkStart = o.timeline.start ? x(o.timeline.start) : x0;
  const latest = Math.max(x0, nowX - CURVE * 2);

  /** Per row: the x where it is free again (Infinity while its lane is live), and its latest lane. */
  const rowEnds: number[] = [];
  const rowLane: number[] = [];
  const lanes = visible.map((lane, i): LaneGeometry => {
    // Leave room for the fork, a short stub of lane and the rejoin before now.
    const xf = Math.max(x0, Math.min(x(lane.forkedAt), nowX - CURVE * 4 - STUB));
    const laneStart = xf + CURVE * 2;
    const terminal = TERMINAL.has(lane.status);
    const marks = lane.marks.map((m) => ({ ...m, x: Math.min(nowX, Math.max(laneStart, x(m.ts))) }));
    const rejoinXs = marks.filter((m) => m.kind === 'rejoin').map((m) => Math.min(Math.max(m.x, laneStart + STUB), latest));
    const lastRejoin = rejoinXs.at(-1);
    // A finished lane ends at its last rejoin, or where it stopped when it has no result.
    const stoppedAt = Math.min(Math.max(laneStart + STUB, x(lane.segments.at(-1)?.from ?? lane.forkedAt)), nowX);
    const laneEnd = terminal ? (lastRejoin ?? stoppedAt) : nowX;

    let row = rowEnds.findIndex((free) => free + ROW_GAP <= xf);
    if (row < 0) row = rowEnds.length;
    rowEnds[row] = terminal ? (lastRejoin !== undefined ? lastRejoin + CURVE * 2 : laneEnd) : Infinity;
    rowLane[row] = i;
    const y = TRUNK_Y + FIRST_LANE + row * LANE_GAP;

    // A finished lane is drawn in its final colour end to end; a live one shows its history.
    const finalColor = terminal ? LANE_COLOR[lane.status] : null;
    const segments = lane.segments
      .map((seg, idx) => {
        const last = idx === lane.segments.length - 1;
        const a = idx === 0 ? laneStart : Math.min(Math.max(laneStart, x(seg.from)), laneEnd);
        const b = Math.min(Math.max(a, last ? laneEnd : x(seg.to ?? o.now)), laneEnd);
        return { a, b, status: seg.status };
      })
      .filter((s) => s.b - s.a > 0.5)
      .map((s) => ({
        d: `M${s.a} ${y} H${s.b}`,
        color: finalColor ?? LANE_COLOR[s.status],
        dashed: !finalColor && (s.status === 'idle' || s.status === 'queued'),
      }));
    const rejoins = rejoinXs.map((rx) => `M${rx} ${y} C${rx + CURVE} ${y} ${rx + CURVE} ${TRUNK_Y} ${rx + CURVE * 2} ${TRUNK_Y}`);
    let signal: (LaneMark & { x: number }) | null = null;
    for (const m of marks) {
      if (m.kind === 'signal') signal = m;
      if (m.kind === 'signal_cleared') signal = null;
    }
    return {
      lane,
      thread: byId.get(lane.threadId),
      row,
      y,
      color: LANE_COLOR[lane.status],
      forkColor: finalColor ?? segments[0]?.color ?? LANE_COLOR[lane.status],
      rejoinColor: finalColor ?? LANE_COLOR.done,
      fork: `M${xf} ${TRUNK_Y} C${xf + CURVE} ${TRUNK_Y} ${xf + CURVE} ${y} ${laneStart} ${y}`,
      segments,
      rejoins,
      marks,
      signal,
      trainX: byId.get(lane.threadId)?.status === 'running' ? nowX : null,
      inlineLabel: laneEnd - laneStart >= 56 ? { x: laneStart + 4, width: laneEnd - laneStart - 8 } : null,
    };
  });
  const rows = rowLane.map((i) => lanes[i]!);
  // Only a lane that is not its row's latest carries its own title; the label column names the rest.
  const latestInRow = new Set(rowLane);
  lanes.forEach((l, i) => {
    if (latestInRow.has(i)) l.inlineLabel = null;
  });

  let lastLabel = -Infinity;
  const stations = o.timeline.stations.map((s) => {
    const sx = Math.min(nowX, x(s.ts));
    const showLabel = sx - lastLabel >= 120;
    if (showLabel) lastLabel = sx;
    return { ...s, x: sx, showLabel };
  });

  const height = Math.max(120, TRUNK_Y + FIRST_LANE + Math.max(0, rows.length - 1) * LANE_GAP + 34);
  const firstFork = Math.min(...lanes.map((l) => Number(/^M(-?[\d.]+)/.exec(l.fork)?.[1] ?? Infinity)));
  return {
    width: o.width,
    height,
    viewportLeft,
    viewportWidth,
    contentWidth,
    windowMs,
    startMs,
    x0,
    x1: nowX,
    trunkY: TRUNK_Y,
    trunkStart: Math.min(trunkStart, firstFork),
    nowX,
    ticks,
    stations,
    lanes,
    rows,
  };
}
