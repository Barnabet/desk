import type { ToolCallView, TranscriptEntry } from '@desk/client';
import { clip } from '@desk/protocol';
import { toolNames } from '../components/ToolGroup';
import { clock } from '../format';

export type StopKind = 'brief' | 'work' | 'detour' | 'result' | 'revision' | 'steer' | 'approval' | 'incoming';

/** One numbered stop on a thread's route; the same number marks its transcript entries. */
export type Stop = {
  n: number;
  kind: StopKind;
  from: string;
  to: string;
  entries: TranscriptEntry[];
  tools: ToolCallView[];
  live: boolean;
};

export type NarrativeRow = { kind: 'stop'; stop: Stop } | { kind: 'compacted'; id: string; ts: string };

const OWN: Partial<Record<TranscriptEntry['kind'], StopKind>> = {
  brief: 'brief',
  detour: 'detour',
  result: 'result',
  revision: 'revision',
  steer: 'steer',
  approval: 'approval',
  incoming: 'incoming',
};

/**
 * Groups transcript entries into numbered stops. Runs of assistant text and tool calls form one "work" stop;
 * every other entry is its own stop. Status changes are not stops; compaction becomes a divider.
 */
export function narrate(entries: TranscriptEntry[]): NarrativeRow[] {
  const rows: NarrativeRow[] = [];
  let work: Stop | null = null;
  let n = 0;
  for (const e of entries) {
    if (e.kind === 'status') continue;
    if (e.kind === 'compacted') {
      work = null;
      rows.push({ kind: 'compacted', id: e.id, ts: e.ts });
      continue;
    }
    const own = OWN[e.kind];
    if (own) {
      work = null;
      rows.push({ kind: 'stop', stop: { n: ++n, kind: own, from: e.ts, to: e.ts, entries: [e], tools: [], live: e.kind === 'approval' && e.state === 'pending' } });
      continue;
    }
    if (!work) {
      work = { n: ++n, kind: 'work', from: e.ts, to: e.ts, entries: [], tools: [], live: false };
      rows.push({ kind: 'stop', stop: work });
    }
    work.entries.push(e);
    work.to = e.ts;
    if (e.kind === 'tools') work.tools.push(...e.calls);
    work.live = work.entries.some((x) => (x.kind === 'assistant' && x.streaming) || (x.kind === 'tools' && x.calls.some((c) => c.status === 'running')));
  }
  return rows;
}

export const stopsOf = (rows: NarrativeRow[]): Stop[] => rows.flatMap((r) => (r.kind === 'stop' ? [r.stop] : []));

/** The stop's title and subtitle, as shown under it on the route and at the top of its transcript entry. */
export function stopText(s: Stop, reviewRounds: number): { title: string; sub: string; quote?: string } {
  const e = s.entries[0];
  switch (e?.kind) {
    case 'brief':
      return { title: 'Brief from Desk', sub: clock(s.from) };
    case 'detour':
      return { title: 'Rate limited · a short detour', sub: `continued on ${e.to} · ${clock(s.from)}` };
    case 'result':
      return { title: 'Reported to Desk', sub: `${clip(e.summary, 60)} · ${clock(s.from)}` };
    case 'revision':
      return { title: 'Desk sent it back', sub: `round ${e.round} of ${reviewRounds} · ${clock(s.from)}`, quote: clip(e.feedback, 70) };
    case 'steer':
      return { title: `You steered · ${clock(s.from)}`, sub: '', quote: clip(e.text, 70) };
    case 'approval':
      return { title: e.state === 'pending' ? 'Waiting for your approval' : e.state === 'approved' ? 'You approved' : e.state === 'denied' ? 'You denied' : `Approval ${e.state}`, sub: `${e.tool} · ${clock(s.from)}` };
    case 'incoming':
      return { title: `${e.fromLabel} · ${e.messageKind}`, sub: clock(s.from), quote: clip(e.text, 70) };
    default: {
      const span = clock(s.from) === clock(s.to) ? clock(s.from) : `${clock(s.from)}–${clock(s.to)}`;
      if (!s.tools.length) return { title: s.live ? 'Writing' : 'Wrote', sub: span };
      const verb = s.live ? 'Using' : 'Used';
      return { title: `${verb} ${s.tools.length} tool${s.tools.length === 1 ? '' : 's'}`, sub: `${clip(toolNames(s.tools), 60)} · ${span}` };
    }
  }
}

export type RoutePoint = { stop: Stop; x: number; y: number; r: number; row: number };
export type RouteLayout = {
  width: number;
  height: number;
  points: RoutePoint[];
  /** Path pieces between consecutive points; `live` pieces follow the last revision of a running thread. */
  pieces: Array<{ d: string; live: boolean }>;
  now: { x: number; y: number } | null;
  tail: Array<{ x: number; y: number }>;
  tailPath: string | null;
};

const SPACING = 190;
const ROW_GAP = 200;
const MARGIN = 120;
const TOP = 150;

const RADIUS: Record<StopKind, number> = { brief: 28, work: 38, detour: 30, result: 28, revision: 28, steer: 24, approval: 24, incoming: 24 };

/** Lays the stops out as a serpentine: left to right, a U-turn, then right to left, and so on. */
export function routeLayout(stops: Stop[], width: number, running: boolean): RouteLayout {
  const perRow = Math.max(2, Math.floor((width - MARGIN * 2) / SPACING) + 1);
  // Spread the columns to fill the width rather than leaving the remainder empty on the right.
  const step = (width - MARGIN * 2) / (perRow - 1);
  const total = stops.length + (running ? 1 : 0);
  const pos = (i: number) => {
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    const x = row % 2 === 0 ? MARGIN + col * step : width - MARGIN - col * step;
    return { row, x, y: TOP + row * ROW_GAP };
  };
  const points = stops.map((stop, i) => {
    const p = pos(i);
    return { stop, x: p.x, y: stop.kind === 'detour' ? p.y - 88 : p.y, r: RADIUS[stop.kind], row: p.row };
  });
  const nowP = running ? pos(stops.length) : null;
  const now = nowP ? { x: nowP.x, y: nowP.y } : null;

  const link = (a: { x: number; y: number; row: number }, b: { x: number; y: number; row: number }) => {
    if (a.row === b.row) {
      const mx = (a.x + b.x) / 2;
      return `M${a.x} ${a.y} C${mx} ${a.y} ${mx} ${b.y} ${b.x} ${b.y}`;
    }
    const dir = a.row % 2 === 0 ? 1 : -1;
    const bulge = Math.max(a.x, b.x) * (dir > 0 ? 1 : 0) + Math.min(a.x, b.x) * (dir < 0 ? 1 : 0) + dir * 72;
    return `M${a.x} ${a.y} C${bulge} ${a.y} ${bulge} ${b.y} ${b.x} ${b.y}`;
  };
  const lastRevision = stops.map((s) => s.kind).lastIndexOf('revision');
  const chain: Array<{ x: number; y: number; row: number }> = [...points, ...(nowP ? [{ ...nowP }] : [])];
  const pieces = chain.slice(1).map((b, i) => ({ d: link(chain[i]!, b), live: running && lastRevision >= 0 && i + 1 > lastRevision }));

  const tail: Array<{ x: number; y: number }> = [];
  let tailPath: string | null = null;
  if (running) {
    const t = pos(total);
    tail.push({ x: t.x, y: t.y });
    tailPath = link({ ...nowP!, row: nowP!.row }, t);
  }
  const rows = Math.floor(Math.max(0, total + (running ? 1 : 0) - 1) / perRow) + 1;
  return { width, height: TOP + (rows - 1) * ROW_GAP + 110, points, pieces, now, tail, tailPath };
}
