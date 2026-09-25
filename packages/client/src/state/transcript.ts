import type { AgentMessageKind, AgentStatus, EphemeralEvent, RunFinishReason, StoredEvent } from '@desk/protocol';
import { appendDelta, finalizeRun, interruptRun, pushToolCall, resolveToolCall, type ToolCallView } from './chat';

export type TranscriptEntry =
  | { kind: 'brief'; id: string; ts: string; text: string }
  | { kind: 'status'; id: string; ts: string; status: AgentStatus; reason: string | null }
  | { kind: 'assistant'; id: string; ts: string; runId: string; text: string; streaming: boolean; interrupted?: boolean }
  | { kind: 'tools'; id: string; ts: string; calls: ToolCallView[] }
  | { kind: 'detour'; id: string; ts: string; from: string; to: string; reason: string }
  | { kind: 'compacted'; id: string; ts: string }
  | { kind: 'result'; id: string; ts: string; summary: string; artifacts: string[] }
  | { kind: 'revision'; id: string; ts: string; round: number; feedback: string }
  | { kind: 'steer'; id: string; ts: string; text: string; question?: true }
  | { kind: 'incoming'; id: string; ts: string; fromAgentId: string; fromLabel: string; messageKind: AgentMessageKind; text: string; replyTo?: number; auto?: true }
  /**
   * An answer run, from its run.started{answering} to that run's run.finished (`ended`). `text` is the run's latest
   * reply without tool calls, which is its answer when the run ended on it (design spec §8 item 6).
   */
  | { kind: 'answer'; id: string; ts: string; runId: string; question: number; text?: string; ended?: { reason: RunFinishReason; detail?: string } }
  | { kind: 'approval'; id: string; ts: string; approvalId: string; tool: string; arguments: string; reason: string; state: 'pending' | 'approved' | 'denied'; resolvedBy: string | null; note: string | null };

/** A thread's transcript as a narrative. Every-step detail lives in the tool groups' calls. `answerRun`: the answer run in progress. */
export type TranscriptState = { agentId: string; entries: TranscriptEntry[]; lastSeq: number; answerRun: string | null };

export const emptyTranscript = (agentId: string): TranscriptState => ({ agentId, entries: [], lastSeq: 0, answerRun: null });

type AnswerEntry = Extract<TranscriptEntry, { kind: 'answer' }>;

/** Updates the answer entry of run `runId`, when there is one. */
function withAnswer(entries: TranscriptEntry[], runId: string, fn: (a: AnswerEntry) => AnswerEntry): TranscriptEntry[] {
  const i = entries.findLastIndex((x) => x.kind === 'answer' && x.runId === runId);
  const a = entries[i];
  if (a?.kind !== 'answer') return entries;
  const next = entries.slice();
  next[i] = fn(a);
  return next;
}

export function reduceTranscript(prev: TranscriptState, e: StoredEvent): TranscriptState {
  if (e.id <= prev.lastSeq) return prev;
  const s: TranscriptState = { ...prev, lastSeq: e.id };
  if (e.agent_id !== s.agentId) return s;
  const id = `e:${e.id}`;
  const push = (entry: TranscriptEntry) => ({ ...s, entries: [...s.entries, entry] });
  switch (e.type) {
    case 'agent.created':
      return e.payload.brief ? push({ kind: 'brief', id, ts: e.ts, text: e.payload.brief }) : s;
    case 'agent.status_changed':
      return push({ kind: 'status', id, ts: e.ts, status: e.payload.status, reason: e.payload.reason ?? null });
    case 'run.started':
      return e.payload.answering === undefined ? s : { ...push({ kind: 'answer', id, ts: e.ts, runId: e.payload.run_id, question: e.payload.answering }), answerRun: e.payload.run_id };
    case 'assistant.message': {
      const entries = finalizeRun(s.entries, e);
      // An answer run's reply without tool calls is its answer; text written next to a tool call is stored with the call.
      if (e.payload.run_id !== s.answerRun || e.payload.tool_calls.length) return { ...s, entries };
      return { ...s, entries: withAnswer(entries, e.payload.run_id, (a) => ({ ...a, text: e.payload.content ?? '' })) };
    }
    case 'run.finished': {
      const entries = e.payload.reason === 'error' ? interruptRun(s.entries, e.payload.run_id) : s.entries;
      if (e.payload.run_id !== s.answerRun) return { ...s, entries };
      const { reason, detail } = e.payload;
      return { ...s, answerRun: null, entries: withAnswer(entries, e.payload.run_id, (a) => ({ ...a, ended: { reason, ...(detail !== undefined ? { detail } : {}) } })) };
    }
    case 'tool.call':
      return { ...s, entries: pushToolCall(s.entries, { id: e.payload.tool_call_id, name: e.payload.name, arguments: e.payload.arguments, status: 'running', content: null }, e.ts, e.id) };
    case 'tool.result':
      return { ...s, entries: resolveToolCall(s.entries, e.payload.tool_call_id, e.payload.status, e.payload.content, e.payload.images) };
    case 'agent.model_switched':
      return push({ kind: 'detour', id, ts: e.ts, from: e.payload.from, to: e.payload.to, reason: e.payload.reason });
    case 'context.compacted':
      return push({ kind: 'compacted', id, ts: e.ts });
    case 'agent.result':
      return push({ kind: 'result', id, ts: e.ts, summary: e.payload.summary, artifacts: e.payload.artifacts });
    case 'agent.revision':
      return push({ kind: 'revision', id, ts: e.ts, round: e.payload.round, feedback: e.payload.feedback });
    case 'message.user':
      return push({ kind: 'steer', id, ts: e.ts, text: e.payload.text, ...(e.payload.question ? { question: true as const } : {}) });
    case 'message.agent': {
      const p = e.payload;
      // Revisions arrive as both agent.revision and a message; the revision entry already shows the feedback.
      // A thread's `start` says what its brief already says.
      if (p.kind === 'revision' || p.kind === 'start') return s;
      return push({
        kind: 'incoming',
        id,
        ts: e.ts,
        fromAgentId: p.from_agent_id,
        fromLabel: p.from_label,
        messageKind: p.kind,
        text: p.text,
        ...(p.reply_to !== undefined ? { replyTo: p.reply_to } : {}),
        ...(p.auto ? { auto: true as const } : {}),
      });
    }
    case 'approval.requested':
      return push({ kind: 'approval', id, ts: e.ts, approvalId: e.payload.approval_id, tool: e.payload.tool, arguments: e.payload.arguments, reason: e.payload.reason, state: 'pending', resolvedBy: null, note: null });
    case 'approval.resolved':
      return {
        ...s,
        entries: s.entries.map((x) =>
          x.kind === 'approval' && x.approvalId === e.payload.approval_id ? { ...x, state: e.payload.decision, resolvedBy: e.payload.resolved_by, note: e.payload.note ?? null } : x,
        ),
      };
    default:
      return s;
  }
}

export function applyTranscriptDelta(s: TranscriptState, e: EphemeralEvent): TranscriptState {
  if (e.agent_id !== s.agentId) return s;
  return { ...s, entries: appendDelta(s.entries, e.payload.run_id, e.payload.text) };
}
