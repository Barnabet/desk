import { eq } from 'drizzle-orm';
import { WAKES_PAUSED, type AttentionItem, type EventOf } from '@desk/protocol';
import { getRun, getVersion, latestFinishedRun, listAutomations, listRunningRuns, stepAgentOf, stepRuns, type AutomationRunRow } from '../automations/queries';
import type { Db } from '../db/open';
import { attentionDismissals } from '../db/schema';
import {
  hasProjectEventAfter,
  lastEvent,
  lastProjectEvent,
  lastProjectNotice,
  lastStallFor,
  listAgents,
  listApprovals,
  listProjects,
  listThreads,
  type AgentRow,
} from './queries';

/** The definition a run started with. */
const defOf = (db: Db, run: AutomationRunRow) => getVersion(db, run.automation_id, run.version)?.definition;

const label = (agent: AgentRow | undefined) => (agent?.role === 'desk' ? 'Desk' : (agent?.title ?? 'A thread'));

/**
 * The project's `wakes_paused` notice while the pause it announced holds (design spec §5.4): no `message.user` in the
 * project since, and its `paused:<notice id>` item not dismissed. The runtime re-derives its paused projects from it
 * after a restart.
 */
export function pausedNotice(db: Db, projectId: string): EventOf<'system.notice'> | undefined {
  const notice = lastProjectNotice(db, projectId, WAKES_PAUSED);
  if (!notice || hasProjectEventAfter(db, projectId, 'message.user', notice.id)) return undefined;
  const dismissed = db.select({ id: attentionDismissals.item_id }).from(attentionDismissals).where(eq(attentionDismissals.item_id, `paused:${notice.id}`)).get();
  return dismissed ? undefined : notice;
}

/**
 * Everything that currently needs the user, derived from state (see the design spec §4.1).
 * Approvals and questions leave the list when answered; the other kinds can also be dismissed.
 */
export function listAttention(db: Db, opts: { projectId?: string } = {}): AttentionItem[] {
  const dismissed = new Set(db.select({ id: attentionDismissals.item_id }).from(attentionDismissals).all().map((r) => r.id));
  const items: AttentionItem[] = [];
  for (const p of listProjects(db)) {
    if (opts.projectId && p.id !== opts.projectId) continue;
    const base = { project_id: p.id, project_name: p.name };
    const agents = new Map(listAgents(db, p.id).map((a) => [a.id, a]));

    for (const a of listApprovals(db, p.id, 'pending')) {
      if (a.delegate_to_desk) continue;
      const agent = agents.get(a.agent_id);
      const link = agent?.role === 'step' ? stepAgentOf(db, agent.id) : undefined;
      const def = link ? defOf(db, link.run) : undefined;
      items.push({
        ...base,
        id: `approval:${a.id}`,
        kind: 'approval',
        agent_id: a.agent_id,
        title: link ? `${def?.title ?? 'An automation'} · ${agent?.title ?? link.step.step_id} wants to run ${a.tool}` : `${label(agent)} wants to run ${a.tool}`,
        detail: a.reason,
        created_at: a.created_at,
        ref: {
          approval_id: a.id,
          ...(agent?.role === 'thread' ? { thread_id: agent.id } : {}),
          ...(link ? { automation_id: link.run.automation_id, run_id: link.run.id, step_id: link.step.step_id } : {}),
        },
      });
    }

    // Desk's question stays open until the user writes to Desk; a message to a thread does not answer it.
    const q = lastProjectEvent(db, p.id, 'question.asked');
    const reply = q?.agent_id ? lastEvent(db, q.agent_id, 'message.user') : undefined;
    if (q && !(reply && reply.id > q.id)) {
      items.push({
        ...base,
        id: `question:${q.id}`,
        kind: 'question',
        agent_id: q.agent_id,
        title: q.payload.question,
        detail: '',
        created_at: q.ts,
        ref: { event_id: q.id, ...(q.payload.options ? { options: q.payload.options } : {}) },
      });
    }

    const report = lastProjectEvent(db, p.id, 'report');
    report?.payload.needs_you.forEach((text, i) => {
      const id = `report:${report.id}:${i}`;
      if (dismissed.has(id)) return;
      items.push({ ...base, id, kind: 'needs_you', agent_id: report.agent_id, title: text, detail: report.payload.headline, created_at: report.ts, ref: { event_id: report.id } });
    });

    const paused = pausedNotice(db, p.id);
    if (paused) {
      items.push({
        ...base,
        id: `paused:${paused.id}`,
        kind: 'paused',
        agent_id: null,
        title: `Agents in ${p.name} are paused: too many automatic wakes this hour`,
        detail: 'Their messages are kept. Resume, or write to any agent.',
        created_at: paused.ts,
        ref: { event_id: paused.id },
      });
    }

    for (const t of listThreads(db, p.id)) {
      if (t.archived_at) continue;
      if (t.status === 'failed') {
        const id = `failed:${t.id}`;
        if (dismissed.has(id)) continue;
        const status = lastEvent(db, t.id, 'agent.status_changed');
        const reason = status?.type === 'agent.status_changed' ? (status.payload.reason ?? '') : '';
        items.push({ ...base, id, kind: 'failed', agent_id: t.id, title: `${t.title ?? 'Thread'} failed`, detail: reason, created_at: t.updated_at, ref: { thread_id: t.id } });
        continue;
      }
      if (t.status !== 'running' && t.status !== 'waiting') continue;
      const stall = lastStallFor(db, p.id, t.id);
      if (!stall) continue;
      const last = lastEvent(db, t.id);
      if (last && last.id > stall.id) continue;
      const id = `stalled:${t.id}:${stall.id}`;
      if (dismissed.has(id)) continue;
      items.push({ ...base, id, kind: 'stalled', agent_id: t.id, title: `${t.title ?? 'Thread'} has stalled`, detail: stall.payload.text, created_at: stall.ts, ref: { thread_id: t.id, event_id: stall.id } });
    }
    // Automations (spec §4.7, §5.4): questions and script gates only the user answers, the latest failure, Desk's
    // turn-on request, and grants an agent's version suspended.
    for (const run of listRunningRuns(db)) {
      if (run.project_id !== p.id) continue;
      const def = defOf(db, run);
      for (const row of stepRuns(db, run.id)) {
        if (row.status !== 'waiting' || (!row.question && !row.gate)) continue;
        const step = def?.steps.find((s) => s.id === row.step_id);
        items.push({
          ...base,
          id: `automation_ask:${run.id}:${row.step_id}`,
          kind: 'automation_ask',
          agent_id: null,
          title: row.question ? `${def?.title ?? 'An automation'}: ${row.question.text}` : `${def?.title ?? 'An automation'} · ${step?.title ?? row.step_id} wants to run ${row.gate!.subject}`,
          detail: row.question ? row.question.files.join('\n') : row.gate!.reason,
          created_at: row.started_at ?? run.started_at,
          ref: { automation_id: run.automation_id, run_id: run.id, step_id: row.step_id },
        });
      }
    }
    for (const a of listAutomations(db, p.id)) {
      const last = latestFinishedRun(db, a.id);
      if (last?.status === 'failed' && !dismissed.has(`automation_failed:${last.id}`)) {
        items.push({
          ...base,
          id: `automation_failed:${last.id}`,
          kind: 'automation_failed',
          agent_id: null,
          title: `${a.title} failed`,
          detail: last.reason ?? last.summary ?? '',
          created_at: last.finished_at ?? last.started_at,
          ref: { automation_id: a.id, run_id: last.id },
        });
      }
      if (a.enable_request && !a.enabled) {
        const id = `automation_enable:${a.id}:${a.enable_request.at}`;
        const when = a.definition.triggers.length ? a.definition.triggers.map((t) => `${t.cron} (${t.timezone})`).join('; ') : 'Run now only';
        if (!dismissed.has(id)) {
          items.push({
            ...base,
            id,
            kind: 'automation_enable_request',
            agent_id: null,
            title: `Desk proposes turning on ${a.title}`,
            detail: `${when}; ${a.enable_request.proposed_grants.length} proposed grant(s). ${a.enable_request.note}`,
            created_at: a.enable_request.at,
            ref: { automation_id: a.id },
          });
        }
      }
      if (a.grants_suspended) {
        const id = `automation_grants:${a.id}:${a.version}`;
        if (!dismissed.has(id)) {
          items.push({
            ...base,
            id,
            kind: 'automation_grants_suspended',
            agent_id: null,
            title: `${a.title} changed: its grants are suspended`,
            detail: `Desk saved v${a.version}. Review the change and keep the grants, or its runs will ask again.`,
            created_at: a.updated_at,
            ref: { automation_id: a.id },
          });
        }
      }
    }
  }
  return items.sort((a, b) => a.created_at.localeCompare(b.created_at));
}
