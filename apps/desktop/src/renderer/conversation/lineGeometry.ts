import type { Lane, LaneMark, MessagesState, Station, ThreadView, TimelineState } from '@desk/client';
import type { AgentStatus } from '@desk/protocol';

export const LANE_COLOR: Record<AgentStatus, string> = {
  running: 'var(--run)',
  waiting: 'var(--wait)',
  queued: 'var(--wait)',
  idle: 'var(--muted-soft)',
  done: 'var(--muted)',
  cancelled: 'var(--muted)',
  failed: 'var(--accent)',
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
/** Messages between the same two agents closer than this (in px) share one link. */
const MERGE_PX = 10;
/** A link this fresh shows the message travelling. */
const LIVE_MS = 8_000;
/** Agent messages the diagram draws as links; the rest it already shows (start is the fork, completed a rejoin, stalled and approval marks) or never shows (reminder). */
const LINKED = new Set(['note', 'revision', 'update', 'question', 'blocker', 'answer']);
const IN_FLIGHT = new Set<AgentStatus>(['running', 'waiting', 'queued']);
const TERMINAL = new Set<AgentStatus>(['done', 'cancelled', 'failed']);

/**
 * A message between two agents (or a burst of them) drawn from the sender's line to the recipient's at the send time:
 * Desk's end on the trunk, a thread's on its lane.
 */
export type MessageLink = {
  /** The messages, oldest first; the first one's sender and recipient orient the link. */
  ids: number[];
  from: string;
  to: string;
  /** The strongest kind in it: a question, else an answer, else a note (notes, revisions, updates, blockers). */
  kind: 'question' | 'answer' | 'note';
  /** Messages went both ways. */
  both: boolean;
  x: number;
  y1: number;
  y2: number;
  count: number;
  firstTs: string;
  lastTs: string;
  /** The latest message's text. */
  text: string;
  /** The latest message was sent moments ago. */
  live: boolean;
};

export type LaneGeometry = {
  lane: Lane;
  thread: ThreadView | undefined;
  row: number;
  y: number;
  /** Where the lane runs straight after its fork curve. */
  start: number;
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
  /** A finished lane's answer run in progress: a short dotted stub straight on from its end, `x` its tip (the live dot). */
  stub: { d: string; x: number } | null;
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
  /** Messages between agents, in time order. */
  links: MessageLink[];
};

/**
 * Positions for the conversation's transit diagram: a time axis, Desk's trunk with stations, and thread lanes.
 * The viewport shows the last hour (or back to the oldest thread still in flight); older history scrolls to the left.
 * A new lane takes the first row a finished lane has left.
 */
export function lineGeometry(o: {
  timeline: TimelineState;
  threads: ThreadView[];
  now: number;
  width: number;
  showArchived?: boolean;
  /** Threads with an answer run in progress (the message fold's `answering`): a finished one gets a stub. */
  answering?: ReadonlySet<string>;
  /** The session's message fold: messages between agents become links. */
  messages?: MessagesState;
}): LineGeometry {
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
    // A finished lane answering a question gets a short stub past its end (design spec §8 item 10). A lane in flight already reaches now.
    const stubEnd = terminal && o.answering?.has(lane.threadId) ? Math.min(laneEnd + STUB * 2, nowX) : null;

    let row = rowEnds.findIndex((free) => free + ROW_GAP <= xf);
    if (row < 0) row = rowEnds.length;
    // The row stays taken past the stub.
    rowEnds[row] = terminal ? Math.max(lastRejoin !== undefined ? lastRejoin + CURVE * 2 : laneEnd, stubEnd ?? -Infinity) : Infinity;
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
      start: laneStart,
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
      stub: stubEnd === null ? null : { d: `M${laneEnd} ${y} H${stubEnd}`, x: stubEnd },
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

  const yOf = new Map<string, number>([[o.timeline.deskId, TRUNK_Y], ...lanes.map((l): [string, number] => [l.lane.threadId, l.y])]);
  /** Where each thread's lane begins after its fork: a message sent as it forks lands there, not in the curve. */
  const startOf = new Map(lanes.map((l): [string, number] => [l.lane.threadId, l.start]));
  const kindRank = { note: 0, answer: 1, question: 2 } as const;
  const links: MessageLink[] = [];
  const lastOfPair = new Map<string, MessageLink>();
  const sent = (o.messages?.messages ?? []).filter((m) => LINKED.has(m.kind) && yOf.has(m.from) && yOf.has(m.to) && m.from !== m.to);
  for (const m of [...sent].sort((p, q) => Date.parse(p.ts) - Date.parse(q.ts) || p.id - q.id)) {
    const mx = Math.min(nowX, Math.max(x0, x(m.ts), startOf.get(m.from) ?? x0, startOf.get(m.to) ?? x0));
    const kind = m.kind === 'question' || m.kind === 'answer' ? m.kind : 'note';
    const pairKey = [m.from, m.to].sort().join(' ');
    const prev = lastOfPair.get(pairKey);
    if (prev && mx - prev.x <= MERGE_PX) {
      prev.ids.push(m.id);
      prev.count++;
      prev.both ||= m.from !== prev.from;
      if (kindRank[kind] > kindRank[prev.kind]) prev.kind = kind;
      prev.lastTs = m.ts;
      prev.text = m.text;
      prev.live = o.now - Date.parse(m.ts) < LIVE_MS;
      continue;
    }
    const link: MessageLink = { ids: [m.id], from: m.from, to: m.to, kind, both: false, x: mx, y1: yOf.get(m.from)!, y2: yOf.get(m.to)!, count: 1, firstTs: m.ts, lastTs: m.ts, text: m.text, live: o.now - Date.parse(m.ts) < LIVE_MS };
    links.push(link);
    lastOfPair.set(pairKey, link);
  }

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
    links,
  };
}
