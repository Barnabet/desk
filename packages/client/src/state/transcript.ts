import type { AgentMessageKind, AgentStatus, EphemeralEvent, StoredEvent } from '@desk/protocol';
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
  | { kind: 'steer'; id: string; ts: string; text: string }
  | { kind: 'incoming'; id: string; ts: string; fromAgentId: string; fromLabel: string; messageKind: AgentMessageKind; text: string }
  | { kind: 'approval'; id: string; ts: string; approvalId: string; tool: string; arguments: string; reason: string; state: 'pending' | 'approved' | 'denied'; resolvedBy: string | null; note: string | null };

/** A thread's transcript as a narrative. Every-step detail lives in the tool groups' calls. */
export type TranscriptState = { agentId: string; entries: TranscriptEntry[]; lastSeq: number };

export const emptyTranscript = (agentId: string): TranscriptState => ({ agentId, entries: [], lastSeq: 0 });

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
    case 'assistant.message':
      return { ...s, entries: finalizeRun(s.entries, e) };
    case 'run.finished':
      return e.payload.reason === 'error' ? { ...s, entries: interruptRun(s.entries, e.payload.run_id) } : s;
    case 'tool.call':
      return { ...s, entries: pushToolCall(s.entries, { id: e.payload.tool_call_id, name: e.payload.name, arguments: e.payload.arguments, status: 'running', content: null }, e.ts, e.id) };
    case 'tool.result':
      return { ...s, entries: resolveToolCall(s.entries, e.payload.tool_call_id, e.payload.status, e.payload.content) };
    case 'agent.model_switched':
      return push({ kind: 'detour', id, ts: e.ts, from: e.payload.from, to: e.payload.to, reason: e.payload.reason });
    case 'context.compacted':
      return push({ kind: 'compacted', id, ts: e.ts });
    case 'agent.result':
      return push({ kind: 'result', id, ts: e.ts, summary: e.payload.summary, artifacts: e.payload.artifacts });
    case 'agent.revision':
      return push({ kind: 'revision', id, ts: e.ts, round: e.payload.round, feedback: e.payload.feedback });
    case 'message.user':
      return push({ kind: 'steer', id, ts: e.ts, text: e.payload.text });
    case 'message.agent':
      // Revisions arrive as both agent.revision and a message; the revision entry already shows the feedback.
      return e.payload.kind === 'revision'
        ? s
        : push({ kind: 'incoming', id, ts: e.ts, fromAgentId: e.payload.from_agent_id, fromLabel: e.payload.from_label, messageKind: e.payload.kind, text: e.payload.text });
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
