import { agentTitle, emptyMessages, messageById, type MessagesState, type ToolCallView, type TranscriptEntry } from '@desk/client';
import { clip, type RunFinishReason } from '@desk/protocol';
import { toolNames } from '../components/ToolGroup';
import { clock } from '../format';

export type StopKind = 'brief' | 'work' | 'detour' | 'result' | 'revision' | 'steer' | 'approval' | 'incoming' | 'answer';

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
 * Groups transcript entries into numbered stops. Runs of assistant text and tool calls form one "work" stop. An answer
 * run is one "answer" stop, from its start until the thread's status next changes (a full run always starts with a
 * status change; an answer run never makes one), and a message that arrives meanwhile is its own stop without
 * splitting it. Every other entry is its own stop. Status changes are not stops; compaction becomes a divider.
 */
export function narrate(entries: TranscriptEntry[]): NarrativeRow[] {
  const rows: NarrativeRow[] = [];
  let work: Stop | null = null;
  let answer: Stop | null = null;
  let n = 0;
  for (const e of entries) {
    if (e.kind === 'status') {
      answer = null;
      continue;
    }
    if (e.kind === 'compacted') {
      work = null;
      answer = null;
      rows.push({ kind: 'compacted', id: e.id, ts: e.ts });
      continue;
    }
    if (e.kind === 'answer') {
      work = null;
      answer = { n: ++n, kind: 'answer', from: e.ts, to: e.ts, entries: [e], tools: [], live: !e.ended };
      rows.push({ kind: 'stop', stop: answer });
      continue;
    }
    const own = OWN[e.kind];
    if (own) {
      work = null;
      rows.push({ kind: 'stop', stop: { n: ++n, kind: own, from: e.ts, to: e.ts, entries: [e], tools: [], live: e.kind === 'approval' && e.state === 'pending' } });
      continue;
    }
    if (answer) {
      answer.entries.push(e);
      answer.to = e.ts;
      if (e.kind === 'tools') answer.tools.push(...e.calls);
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

/** A stop's words on the route and at the top of its transcript entry. `muted`: an answer that did not happen (S6 styles it). */
export type StopText = { title: string; sub: string; quote?: string; muted?: true };

const NO_MESSAGES = emptyMessages();

/** A message sender's name: the fold's directory (archived threads keep their titles), else what its label says. */
function senderName(m: MessagesState, id: string, label: string): string {
  if (m.agents[id]) return agentTitle(m, id);
  return label === 'Desk' ? 'Desk' : (/^thread "(.*)" \(/.exec(label)?.[1] ?? label);
}

/** Why an answer run for the user's Ask ended without an answer. */
function whyNot(end: { reason: RunFinishReason; detail?: string }): string {
  if (end.reason === 'stopped') return 'stopped';
  if (end.reason === 'max_steps') return 'step limit';
  // A graceful shutdown and a crash both cut the run off (design spec §4.6).
  if (end.detail === 'daemon_shutdown' || end.detail === 'daemon_restart') return 'Desk was restarting';
  if (end.reason === 'error') return end.detail ?? 'error';
  return 'no answer';
}

/** An answer run's stop: in progress, answered, or not (design spec §8 item 6). */
function answerText(e: Extract<TranscriptEntry, { kind: 'answer' }>, from: string, m: MessagesState): StopText {
  const q = messageById(m, e.question);
  const user = q?.from === 'user';
  const asker = !q ? 'a question' : user ? 'you' : agentTitle(m, q.from);
  const asked = q ? { quote: clip(q.text, 70) } : {};
  const sub = clock(from);
  if (!e.ended) return { title: `Answering ${asker}`, sub, ...asked };
  if (user) {
    // Only the run's final reply is an answer: text written next to a tool call is stored with the call.
    if (e.ended.reason === 'no_tool_calls' && e.text?.trim()) return { title: 'Answered you', sub, quote: clip(e.text, 70) };
    return { title: "Couldn't answer you", sub: `${whyNot(e.ended)} · ${sub}`, ...asked, muted: true };
  }
  // An agent's question: its first answer in the fold, which lands on the asker's stream.
  const a = q?.answerId === undefined ? undefined : messageById(m, q.answerId);
  if (a && !a.auto) return { title: `Answered ${asker}`, sub, quote: clip(a.text, 70) };
  return { title: `Couldn't answer ${asker}`, sub, ...(a ? { quote: clip(a.text, 70) } : {}), muted: true };
}

/**
 * The stop's title and subtitle, as shown under it on the route and at the top of its transcript entry. `messages`,
 * the session's message fold, names senders and askers and tells how an answer run ended.
 */
export function stopText(s: Stop, reviewRounds: number, messages: MessagesState = NO_MESSAGES): StopText {
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
      return { title: `${e.question ? 'You asked' : 'You steered'} · ${clock(s.from)}`, sub: '', quote: clip(e.text, 70) };
    case 'approval':
      return { title: e.state === 'pending' ? 'Waiting for your approval' : e.state === 'approved' ? 'You approved' : e.state === 'denied' ? 'You denied' : `Approval ${e.state}`, sub: `${e.tool} · ${clock(s.from)}` };
    case 'incoming': {
      const who = senderName(messages, e.fromAgentId, e.fromLabel);
      const title = e.messageKind === 'question' ? `${who} asked` : e.messageKind === 'answer' ? (e.auto ? `${who} could not answer` : `Answer from ${who}`) : `${who}: ${e.messageKind}`;
      return { title, sub: clock(s.from), quote: clip(e.text, 70), ...(e.auto ? { muted: true as const } : {}) };
    }
    case 'answer':
      return answerText(e, s.from, messages);
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

const RADIUS: Record<StopKind, number> = { brief: 28, work: 38, detour: 30, result: 28, revision: 28, steer: 24, approval: 24, incoming: 24, answer: 28 };

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
