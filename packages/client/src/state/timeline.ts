import { clip, type AgentStatus, type StoredEvent } from '@desk/protocol';

export type Station = {
  eventId: number;
  ts: string;
  kind: 'brief' | 'dispatch' | 'result_in' | 'sent_back' | 'report' | 'question';
  label: string;
  threadIds: string[];
};
/** A mark on a thread's lane. `question`: a tracked question the thread asked, labelled with its recipient (its state is in the message fold, by `eventId`). */
export type LaneMark = { eventId: number; ts: string; kind: 'rejoin' | 'sent_back' | 'detour' | 'signal' | 'signal_cleared' | 'stalled' | 'question'; label: string };
export type Lane = {
  threadId: string;
  title: string;
  forkedAt: string;
  status: AgentStatus;
  archived: boolean;
  segments: Array<{ from: string; to: string | null; status: AgentStatus }>;
  marks: LaneMark[];
};

/** Time-positioned data for the conversation's line diagram: Desk's trunk stations and one lane per thread. */
export type TimelineState = { deskId: string; start: string | null; stations: Station[]; lanes: Lane[]; approvals: Record<string, string>; lastSeq: number };

export const emptyTimeline = (deskId: string): TimelineState => ({ deskId, start: null, stations: [], lanes: [], approvals: {}, lastSeq: 0 });

/** Dispatches closer together than this merge into one station. */
const DISPATCH_WINDOW_MS = 2 * 60_000;

function withLane(s: TimelineState, threadId: string | null, fn: (l: Lane) => Lane): TimelineState {
  const i = s.lanes.findIndex((l) => l.threadId === threadId);
  if (i < 0) return s;
  const lanes = s.lanes.slice();
  lanes[i] = fn(lanes[i]!);
  return { ...s, lanes };
}

const mark = (e: StoredEvent, kind: LaneMark['kind'], label: string): LaneMark => ({ eventId: e.id, ts: e.ts, kind, label });

export function reduceTimeline(prev: TimelineState, e: StoredEvent): TimelineState {
  if (e.id <= prev.lastSeq) return prev;
  let s: TimelineState = { ...prev, lastSeq: e.id, start: prev.start ?? e.ts };
  const station = (kind: Station['kind'], label: string, threadIds: string[] = []) => ({ ...s, stations: [...s.stations, { eventId: e.id, ts: e.ts, kind, label, threadIds }] });
  const titleOf = (id: string | null) => s.lanes.find((l) => l.threadId === id)?.title ?? 'Thread';
  switch (e.type) {
    case 'message.user':
      return e.agent_id === s.deskId ? station('brief', clip(e.payload.text, 48)) : s;
    case 'agent.created': {
      if (e.payload.role !== 'thread' || e.payload.parent_id !== s.deskId || !e.agent_id) return s;
      s = { ...s, lanes: [...s.lanes, { threadId: e.agent_id, title: e.payload.title ?? 'Thread', forkedAt: e.ts, status: 'idle', archived: false, segments: [{ from: e.ts, to: null, status: 'idle' }], marks: [] }] };
      const last = s.stations.at(-1);
      if (last?.kind === 'dispatch' && Date.parse(e.ts) - Date.parse(last.ts) <= DISPATCH_WINDOW_MS) {
        const threadIds = [...last.threadIds, e.agent_id];
        return { ...s, stations: [...s.stations.slice(0, -1), { ...last, threadIds, label: `${threadIds.length} threads` }] };
      }
      return station('dispatch', '1 thread', [e.agent_id]);
    }
    case 'agent.status_changed':
      return withLane(s, e.agent_id, (l) => {
        if (l.status === e.payload.status) return l;
        const segments = l.segments.map((seg, i) => (i === l.segments.length - 1 ? { ...seg, to: e.ts } : seg));
        return { ...l, status: e.payload.status, segments: [...segments, { from: e.ts, to: null, status: e.payload.status }] };
      });
    case 'agent.result': {
      const title = titleOf(e.agent_id);
      s = withLane(s, e.agent_id, (l) => ({ ...l, marks: [...l.marks, mark(e, 'rejoin', clip(e.payload.summary, 60))] }));
      return s.lanes.some((l) => l.threadId === e.agent_id) ? station('result_in', title, [e.agent_id!]) : s;
    }
    case 'agent.revision': {
      const title = titleOf(e.agent_id);
      s = withLane(s, e.agent_id, (l) => ({ ...l, marks: [...l.marks, mark(e, 'sent_back', `round ${e.payload.round}`)] }));
      return s.lanes.some((l) => l.threadId === e.agent_id) ? station('sent_back', `${title} · round ${e.payload.round}`, [e.agent_id!]) : s;
    }
    case 'agent.model_switched':
      return withLane(s, e.agent_id, (l) => ({ ...l, marks: [...l.marks, mark(e, 'detour', e.payload.to)] }));
    case 'approval.requested':
      if (e.payload.delegate_to_desk || !s.lanes.some((l) => l.threadId === e.agent_id)) return s;
      s = { ...s, approvals: { ...s.approvals, [e.payload.approval_id]: e.agent_id! } };
      return withLane(s, e.agent_id, (l) => ({ ...l, marks: [...l.marks, mark(e, 'signal', e.payload.tool)] }));
    case 'approval.resolved': {
      const lane = s.approvals[e.payload.approval_id];
      return lane ? withLane(s, lane, (l) => ({ ...l, marks: [...l.marks, mark(e, 'signal_cleared', e.payload.decision)] })) : s;
    }
    case 'message.agent': {
      const p = e.payload;
      if (p.kind === 'stalled') return withLane(s, p.from_agent_id, (l) => ({ ...l, marks: [...l.marks, mark(e, 'stalled', 'stalled')] }));
      // A tracked question, on its asker's lane at its send time (design spec §8 item 10). Desk has no lane.
      if (p.kind === 'question' && p.tracked) {
        const to = e.agent_id === s.deskId ? 'Desk' : titleOf(e.agent_id);
        return withLane(s, p.from_agent_id, (l) => ({ ...l, marks: [...l.marks, mark(e, 'question', to)] }));
      }
      return s;
    }
    case 'agent.archived':
      return withLane(s, e.agent_id, (l) => ({ ...l, archived: true }));
    case 'report':
      return e.agent_id === s.deskId ? station('report', clip(e.payload.headline, 60)) : s;
    case 'question.asked':
      return e.agent_id === s.deskId ? station('question', clip(e.payload.question, 60)) : s;
    default:
      return s;
  }
}
