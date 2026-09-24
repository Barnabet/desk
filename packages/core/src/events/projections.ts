import { eq, sql } from 'drizzle-orm';
import { resolveSettings, type StoredEvent } from '@desk/protocol';
import type { Tx } from '../db/open';
import { agents, approvals, artifacts, attentionDismissals, memory, plans, projects, sources, usageTotals } from '../db/schema';

function requireAgentId(ev: StoredEvent): string {
  if (!ev.agent_id) throw new Error(`${ev.type} requires agent_id`);
  return ev.agent_id;
}

/** Applies one event to the projection tables. Runs inside the append transaction. */
export function applyProjections(tx: Tx, ev: StoredEvent): void {
  switch (ev.type) {
    case 'project.created':
      tx.insert(projects)
        .values({
          id: ev.project_id,
          name: ev.payload.name,
          goal: ev.payload.goal,
          instructions: ev.payload.instructions,
          settings: resolveSettings(ev.payload.settings),
          created_at: ev.ts,
          updated_at: ev.ts,
        })
        .run();
      return;
    case 'project.archived':
      tx.update(projects).set({ archived_at: ev.ts, updated_at: ev.ts }).where(eq(projects.id, ev.project_id)).run();
      return;
    case 'project.updated': {
      const current = tx.select().from(projects).where(eq(projects.id, ev.project_id)).get();
      if (!current) throw new Error(`Unknown project: ${ev.project_id}`);
      const { settings, ...fields } = ev.payload;
      tx.update(projects)
        .set({ ...fields, settings: resolveSettings({ ...current.settings, ...settings }), updated_at: ev.ts })
        .where(eq(projects.id, ev.project_id))
        .run();
      return;
    }
    case 'agent.created':
      tx.insert(agents)
        .values({
          id: requireAgentId(ev),
          project_id: ev.project_id,
          role: ev.payload.role,
          status: 'idle',
          model: ev.payload.model,
          title: ev.payload.title,
          brief: ev.payload.brief,
          workspace_path: ev.payload.workspace_path,
          parent_id: ev.payload.parent_id,
          active_skills: ev.payload.skills ?? [],
          inbox_cursor: 0,
          git_source_id: ev.payload.git?.source_id ?? null,
          git_branch: ev.payload.git?.branch ?? null,
          git_base: ev.payload.git?.base ?? null,
          git_common_dir: ev.payload.git?.common_dir ?? null,
          created_at: ev.ts,
          updated_at: ev.ts,
        })
        .run();
      return;
    case 'source.added':
      tx.insert(sources)
        .values({ id: ev.payload.source_id, project_id: ev.project_id, path: ev.payload.path, kind: ev.payload.kind, label: ev.payload.label, created_at: ev.ts })
        .run();
      return;
    case 'source.removed':
      tx.delete(sources).where(eq(sources.id, ev.payload.source_id)).run();
      return;
    case 'memory.written': {
      const p = ev.payload;
      tx.insert(memory)
        .values({ id: p.memory_id, project_id: ev.project_id, kind: p.kind, content: p.content, source: p.source, supersedes: p.supersedes ?? null, created_at: ev.ts })
        .run();
      tx.run(sql`INSERT INTO memory_fts (content, memory_id, project_id) VALUES (${p.content}, ${p.memory_id}, ${ev.project_id})`);
      if (p.supersedes) {
        tx.update(memory).set({ superseded_by: p.memory_id }).where(eq(memory.id, p.supersedes)).run();
        tx.run(sql`DELETE FROM memory_fts WHERE memory_id = ${p.supersedes}`);
      }
      return;
    }
    case 'memory.deleted':
      tx.delete(memory).where(eq(memory.id, ev.payload.memory_id)).run();
      tx.run(sql`DELETE FROM memory_fts WHERE memory_id = ${ev.payload.memory_id}`);
      return;
    case 'artifact.published': {
      const p = ev.payload;
      tx.insert(artifacts)
        .values({ id: p.artifact_id, project_id: ev.project_id, path: p.path, title: p.title, kind: p.kind, origin: p.origin, description: p.description, created_at: ev.ts })
        .run();
      return;
    }
    case 'agent.result':
      tx.update(agents)
        .set({ result_summary: ev.payload.summary, result_artifacts: ev.payload.artifacts, updated_at: ev.ts })
        .where(eq(agents.id, requireAgentId(ev)))
        .run();
      return;
    case 'agent.revision':
      tx.update(agents).set({ review_round: ev.payload.round, updated_at: ev.ts }).where(eq(agents.id, requireAgentId(ev))).run();
      return;
    case 'agent.skills_changed':
      tx.update(agents).set({ active_skills: ev.payload.skills, updated_at: ev.ts }).where(eq(agents.id, requireAgentId(ev))).run();
      return;
    case 'agent.archived':
      tx.update(agents).set({ archived_at: ev.ts, updated_at: ev.ts }).where(eq(agents.id, requireAgentId(ev))).run();
      return;
    case 'plan.updated':
      tx.insert(plans)
        .values({ project_id: ev.project_id, items: ev.payload.items, updated_at: ev.ts })
        .onConflictDoUpdate({ target: plans.project_id, set: { items: ev.payload.items, updated_at: ev.ts } })
        .run();
      return;
    case 'agent.status_changed':
      tx.update(agents).set({ status: ev.payload.status, updated_at: ev.ts }).where(eq(agents.id, requireAgentId(ev))).run();
      return;
    case 'inbox.drained':
      tx.update(agents).set({ inbox_cursor: ev.payload.up_to, updated_at: ev.ts }).where(eq(agents.id, requireAgentId(ev))).run();
      return;
    case 'approval.requested':
      tx.insert(approvals)
        .values({
          id: ev.payload.approval_id,
          project_id: ev.project_id,
          agent_id: requireAgentId(ev),
          run_id: ev.payload.run_id,
          tool_call_id: ev.payload.tool_call_id,
          tool: ev.payload.tool,
          arguments: ev.payload.arguments,
          reason: ev.payload.reason,
          delegate_to_desk: ev.payload.delegate_to_desk,
          status: 'pending',
          created_at: ev.ts,
        })
        .run();
      return;
    case 'approval.resolved':
      tx.update(approvals)
        .set({ status: ev.payload.decision, resolved_by: ev.payload.resolved_by, note: ev.payload.note ?? null, resolved_at: ev.ts })
        .where(eq(approvals.id, ev.payload.approval_id))
        .run();
      return;
    case 'usage':
      tx.insert(usageTotals)
        .values({
          project_id: ev.project_id,
          agent_id: requireAgentId(ev),
          model: ev.payload.model,
          day: ev.ts.slice(0, 10),
          prompt_tokens: ev.payload.prompt_tokens,
          completion_tokens: ev.payload.completion_tokens,
        })
        .onConflictDoUpdate({
          target: [usageTotals.project_id, usageTotals.agent_id, usageTotals.model, usageTotals.day],
          set: {
            prompt_tokens: sql`${usageTotals.prompt_tokens} + ${ev.payload.prompt_tokens}`,
            completion_tokens: sql`${usageTotals.completion_tokens} + ${ev.payload.completion_tokens}`,
          },
        })
        .run();
      return;
    case 'attention.dismissed':
      tx.insert(attentionDismissals).values({ item_id: ev.payload.item_id, project_id: ev.project_id, dismissed_at: ev.ts }).onConflictDoNothing().run();
      return;
    default:
      return;
  }
}
