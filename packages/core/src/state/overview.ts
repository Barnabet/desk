import { summarizeToolArgs, type OverviewThread, type ProjectSummary } from '@desk/protocol';
import type { Db } from '../db/open';
import { getPlan } from '../coordination/plan';
import { listAttention } from './attention';
import { getDeskAgent, lastEvent, lastProjectEvent, listProjects, listThreads, type AgentRow } from './queries';

function activity(db: Db, agentId: string): string | null {
  const call = lastEvent(db, agentId, 'tool.call');
  if (call?.type !== 'tool.call') return null;
  const result = lastEvent(db, agentId, 'tool.result');
  if (result && result.id > call.id) return null;
  return `${call.payload.name} · ${summarizeToolArgs(call.payload.arguments)}`;
}

function reason(db: Db, t: AgentRow): string | null {
  if (!['waiting', 'queued', 'failed', 'cancelled'].includes(t.status)) return null;
  const ev = lastEvent(db, t.id, 'agent.status_changed');
  return ev?.type === 'agent.status_changed' ? (ev.payload.reason ?? null) : null;
}

/** One summary per open project: enough to draw the map, the home screen and the tray without per-project calls. */
export function listOverview(db: Db): ProjectSummary[] {
  const attention = listAttention(db);
  return listProjects(db).map((p) => {
    const threads: OverviewThread[] = listThreads(db, p.id)
      .filter((t) => !t.archived_at)
      .map((t) => ({
        id: t.id,
        title: t.title,
        status: t.status,
        reason: reason(db, t),
        activity: activity(db, t.id),
        model: t.model,
        git_branch: t.git_branch,
        skills: t.active_skills,
        review_round: t.review_round,
        created_at: t.created_at,
        updated_at: t.updated_at,
      }));
    const items = (getPlan(db, p.id)?.items ?? []).filter((i) => i.status !== 'dropped');
    const report = lastProjectEvent(db, p.id, 'report');
    return {
      project: { id: p.id, name: p.name, goal: p.goal, updated_at: p.updated_at },
      desk_status: getDeskAgent(db, p.id)?.status ?? 'idle',
      threads,
      latest_report: report ? { headline: report.payload.headline, ts: report.ts } : null,
      plan_progress: { done: items.filter((i) => i.status === 'done').length, total: items.length },
      attention_count: attention.filter((a) => a.project_id === p.id).length,
    };
  });
}
