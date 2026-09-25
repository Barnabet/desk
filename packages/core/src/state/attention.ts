import type { AttentionItem } from '@desk/protocol';
import type { Db } from '../db/open';
import { attentionDismissals } from '../db/schema';
import {
  lastEvent,
  lastProjectEvent,
  lastStallFor,
  listAgents,
  listApprovals,
  listProjects,
  listThreads,
  type AgentRow,
} from './queries';

const label = (agent: AgentRow | undefined) => (agent?.role === 'desk' ? 'Desk' : (agent?.title ?? 'A thread'));

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
      items.push({
        ...base,
        id: `approval:${a.id}`,
        kind: 'approval',
        agent_id: a.agent_id,
        title: `${label(agent)} wants to run ${a.tool}`,
        detail: a.reason,
        created_at: a.created_at,
        ref: { approval_id: a.id, ...(agent?.role === 'thread' ? { thread_id: agent.id } : {}) },
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
  }
  return items.sort((a, b) => a.created_at.localeCompare(b.created_at));
}
