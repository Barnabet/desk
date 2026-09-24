import { clip, summarizeToolArgs, type PlanItem, type StoredEvent } from '@desk/protocol';
import type { AgentRow, ApprovalRow, ProjectOverview, ProjectRow, SourceRow } from '../types';

/** A thread (or Desk) with live details that are not columns: current tool activity, status reason, fallback model. */
export type ThreadView = AgentRow & {
  activity: string | null;
  reason: string | null;
  model_override: string | null;
  /** The reasoning level the latest run sent (its own, or the project's); null when none was sent. */
  effort: string | null;
};

export type ProjectState = {
  project: ProjectRow;
  desk: ThreadView | null;
  sources: SourceRow[];
  plan: PlanItem[];
  threads: ThreadView[];
  /** Pending approvals only. */
  approvals: ApprovalRow[];
  lastSeq: number;
};

const view = (a: AgentRow): ThreadView => ({ ...a, activity: null, reason: null, model_override: null, effort: a.reasoning_effort ?? null });

export function projectFromOverview(o: ProjectOverview): ProjectState {
  return {
    project: o.project,
    desk: o.desk ? view(o.desk) : null,
    sources: o.sources,
    plan: o.plan?.items ?? [],
    threads: o.threads.map(view),
    approvals: o.approvals,
    lastSeq: o.last_seq,
  };
}

const TERMINAL = new Set(['done', 'failed', 'cancelled']);

function updateAgent(s: ProjectState, id: string | null, fn: (a: ThreadView) => ThreadView): ProjectState {
  if (!id) return s;
  if (s.desk?.id === id) return { ...s, desk: fn(s.desk) };
  const i = s.threads.findIndex((t) => t.id === id);
  if (i < 0) return s;
  const threads = s.threads.slice();
  threads[i] = fn(threads[i]!);
  return { ...s, threads };
}

/** Folds one event into the project state. Events at or below `lastSeq` are ignored (replay safety). */
export function reduceProject(prev: ProjectState, e: StoredEvent): ProjectState {
  if (e.id <= prev.lastSeq || e.project_id !== prev.project.id) return prev;
  const s: ProjectState = { ...prev, lastSeq: e.id };
  const touch = (fn: (a: ThreadView) => ThreadView) => updateAgent(s, e.agent_id, (a) => ({ ...fn(a), updated_at: e.ts }));
  switch (e.type) {
    case 'project.updated': {
      const { settings, ...fields } = e.payload;
      return { ...s, project: { ...s.project, ...fields, settings: { ...s.project.settings, ...(settings ?? {}) }, updated_at: e.ts } as ProjectRow };
    }
    case 'project.archived':
      return { ...s, project: { ...s.project, archived_at: e.ts } };
    case 'source.added':
      return { ...s, sources: [...s.sources, { id: e.payload.source_id, project_id: e.project_id, path: e.payload.path, kind: e.payload.kind, label: e.payload.label, created_at: e.ts }] };
    case 'source.removed':
      return { ...s, sources: s.sources.filter((x) => x.id !== e.payload.source_id) };
    case 'agent.created': {
      if (!e.agent_id) return s;
      const row: ThreadView = view({
        id: e.agent_id,
        project_id: e.project_id,
        role: e.payload.role,
        status: 'idle',
        model: e.payload.model,
        reasoning_effort: e.payload.reasoning_effort ?? null,
        title: e.payload.title,
        brief: e.payload.brief,
        workspace_path: e.payload.workspace_path,
        parent_id: e.payload.parent_id,
        inbox_cursor: 0,
        review_round: 0,
        result_summary: null,
        result_artifacts: null,
        active_skills: e.payload.skills ?? [],
        git_source_id: e.payload.git?.source_id ?? null,
        git_branch: e.payload.git?.branch ?? null,
        git_base: e.payload.git?.base ?? null,
        git_common_dir: e.payload.git?.common_dir ?? null,
        archived_at: null,
        created_at: e.ts,
        updated_at: e.ts,
      });
      return e.payload.role === 'desk' ? { ...s, desk: row } : { ...s, threads: [...s.threads, row] };
    }
    case 'agent.status_changed':
      return touch((a) => ({
        ...a,
        status: e.payload.status,
        reason: e.payload.reason ?? null,
        activity: TERMINAL.has(e.payload.status) ? null : a.activity,
      }));
    case 'agent.result':
      return touch((a) => ({ ...a, result_summary: e.payload.summary, result_artifacts: e.payload.artifacts }));
    case 'agent.revision':
      return touch((a) => ({ ...a, review_round: e.payload.round }));
    case 'agent.model_switched':
      return touch((a) => ({ ...a, model_override: e.payload.to }));
    case 'run.started':
      return touch((a) => ({ ...a, model_override: null, effort: e.payload.reasoning_effort ?? null }));
    case 'agent.skills_changed':
      return touch((a) => ({ ...a, active_skills: e.payload.skills }));
    case 'agent.archived':
      return touch((a) => ({ ...a, archived_at: e.ts }));
    case 'tool.call':
      return touch((a) => ({ ...a, activity: `${e.payload.name} · ${summarizeToolArgs(e.payload.arguments)}` }));
    case 'tool.result':
      return touch((a) => ({ ...a, activity: null }));
    case 'plan.updated':
      return { ...s, plan: e.payload.items };
    case 'approval.requested':
      return {
        ...s,
        approvals: [
          ...s.approvals,
          {
            id: e.payload.approval_id,
            project_id: e.project_id,
            agent_id: e.agent_id ?? '',
            run_id: e.payload.run_id,
            tool_call_id: e.payload.tool_call_id,
            tool: e.payload.tool,
            arguments: e.payload.arguments,
            reason: e.payload.reason,
            delegate_to_desk: e.payload.delegate_to_desk,
            status: 'pending',
            resolved_by: null,
            note: null,
            created_at: e.ts,
            resolved_at: null,
          },
        ],
      };
    case 'approval.resolved':
      return { ...s, approvals: s.approvals.filter((a) => a.id !== e.payload.approval_id) };
    default:
      return s;
  }
}

/** Threads to show (not archived), and a one-line label for the roster. */
export const liveThreads = (s: ProjectState) => s.threads.filter((t) => !t.archived_at);
export const threadLabel = (t: ThreadView) => clip(t.title ?? t.id, 60);
