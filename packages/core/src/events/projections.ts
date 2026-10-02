import { and, eq, sql } from 'drizzle-orm';
import { resolveSettings, type AutomationDefinition, type StoredEvent } from '@desk/protocol';
import type { Tx } from '../db/open';
import {
  agents,
  approvals,
  artifacts,
  attentionDismissals,
  automationRuns,
  automations,
  automationStepRuns,
  automationVersions,
  builtinSkillSettings,
  memory,
  plans,
  projects,
  services,
  sources,
  usageTotals,
} from '../db/schema';

function requireAgentId(ev: StoredEvent): string {
  if (!ev.agent_id) throw new Error(`${ev.type} requires agent_id`);
  return ev.agent_id;
}

const TERMINAL_STEP = new Set(['succeeded', 'failed', 'rejected', 'skipped', 'cancelled']);

/** Each schedule's cursor after a save while on: kept when its cron and timezone are unchanged, else the save time. */
function carryLastDue(prev: AutomationDefinition['triggers'], next: AutomationDefinition['triggers'], lastDue: Record<string, string>, ts: string): Record<string, string> {
  const out: Record<string, string> = {};
  next.forEach((t, i) => {
    const p = prev[i];
    const same = p !== undefined && p.cron === t.cron && p.timezone === t.timezone;
    out[String(i)] = same && lastDue[String(i)] ? lastDue[String(i)]! : ts;
  });
  return out;
}

function setLastDue(tx: Tx, automationId: string, index: number, dueAt: string): void {
  const a = tx.select().from(automations).where(eq(automations.id, automationId)).get();
  if (a) tx.update(automations).set({ last_due: { ...a.last_due, [String(index)]: dueAt } }).where(eq(automations.id, automationId)).run();
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
      if (settings?.desk_model !== undefined) {
        tx.update(agents)
          .set({ model: settings.desk_model, updated_at: ev.ts })
          .where(and(eq(agents.project_id, ev.project_id), eq(agents.role, 'desk')))
          .run();
      }
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
          reasoning_effort: ev.payload.reasoning_effort ?? null,
          title: ev.payload.title,
          brief: ev.payload.brief,
          workspace_path: ev.payload.workspace_path,
          parent_id: ev.payload.parent_id,
          active_skills: ev.payload.skills ?? [],
          automation_run_id: ev.payload.automation?.run_id ?? null,
          automation_step_id: ev.payload.automation?.step_id ?? null,
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
        .values({ id: ev.payload.source_id, project_id: ev.project_id, path: ev.payload.path, kind: ev.payload.kind, label: ev.payload.label, agent_write: ev.payload.agent_write ?? true, created_at: ev.ts })
        .run();
      return;
    case 'source.updated':
      tx.update(sources).set({ agent_write: ev.payload.agent_write }).where(eq(sources.id, ev.payload.source_id)).run();
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
    case 'service.started': {
      const p = ev.payload;
      const run = { command: p.command, cwd: p.cwd, agent_id: p.workspace_agent_id, source_id: p.source_id ?? null, status: 'running' as const, pid: p.pid, exit_code: null, exit_signal: null, stop_reason: null, url: null, started_by: p.by, started_at: ev.ts, ended_at: null };
      tx.insert(services)
        .values({ id: p.service_id, project_id: ev.project_id, name: p.name, ...run })
        .onConflictDoUpdate({ target: services.id, set: run })
        .run();
      return;
    }
    case 'service.url':
      tx.update(services).set({ url: ev.payload.url }).where(eq(services.id, ev.payload.service_id)).run();
      return;
    case 'service.exited':
      tx.update(services)
        .set({ status: 'exited', exit_code: ev.payload.code, exit_signal: ev.payload.signal, ended_at: ev.ts })
        .where(eq(services.id, ev.payload.service_id))
        .run();
      return;
    case 'service.stopped':
      tx.update(services).set({ status: 'stopped', stop_reason: ev.payload.reason, ended_at: ev.ts }).where(eq(services.id, ev.payload.service_id)).run();
      return;
    case 'attention.dismissed':
      tx.insert(attentionDismissals).values({ item_id: ev.payload.item_id, project_id: ev.project_id, dismissed_at: ev.ts }).onConflictDoNothing().run();
      return;
    case 'skill.builtin_toggled':
      tx.insert(builtinSkillSettings)
        .values({ name: ev.payload.name, enabled: ev.payload.enabled, updated_at: ev.ts })
        .onConflictDoUpdate({ target: builtinSkillSettings.name, set: { enabled: ev.payload.enabled, updated_at: ev.ts } })
        .run();
      return;
    case 'automation.saved': {
      const p = ev.payload;
      const current = tx.select().from(automations).where(eq(automations.id, p.automation_id)).get();
      if (!current) {
        tx.insert(automations)
          .values({
            id: p.automation_id,
            project_id: ev.project_id,
            name: p.name,
            title: p.definition.title,
            description: p.definition.description,
            version: p.version,
            definition: p.definition,
            created_at: ev.ts,
            updated_at: ev.ts,
          })
          .run();
      } else {
        tx.update(automations)
          .set({
            title: p.definition.title,
            description: p.definition.description,
            version: p.version,
            definition: p.definition,
            last_due: current.enabled ? carryLastDue(current.definition.triggers, p.definition.triggers, current.last_due, ev.ts) : current.last_due,
            // An agent's version suspends grants until the user keeps them (spec §5.3).
            grants_suspended: current.grants_suspended || (p.origin.startsWith('agent:') && current.grants.length > 0),
            updated_at: ev.ts,
          })
          .where(eq(automations.id, p.automation_id))
          .run();
      }
      tx.insert(automationVersions)
        .values({ automation_id: p.automation_id, version: p.version, definition: p.definition, origin: p.origin, change_note: p.change_note, via: p.via, created_at: ev.ts })
        .run();
      return;
    }
    case 'automation.layout_saved':
      tx.update(automations).set({ layout: ev.payload.layout }).where(eq(automations.id, ev.payload.automation_id)).run();
      return;
    case 'automation.deleted':
      tx.update(automations).set({ deleted_at: ev.ts, enabled: false, updated_at: ev.ts }).where(eq(automations.id, ev.payload.automation_id)).run();
      return;
    case 'automation.switched': {
      const a = tx.select().from(automations).where(eq(automations.id, ev.payload.automation_id)).get();
      if (!a) return;
      tx.update(automations)
        .set(
          ev.payload.enabled
            ? { enabled: true, enable_request: null, last_due: Object.fromEntries(a.definition.triggers.map((_, i) => [String(i), ev.ts])), updated_at: ev.ts }
            : { enabled: false, updated_at: ev.ts },
        )
        .where(eq(automations.id, a.id))
        .run();
      return;
    }
    case 'automation.grants_set': {
      const a = tx.select().from(automations).where(eq(automations.id, ev.payload.automation_id)).get();
      if (!a) return;
      tx.update(automations)
        .set({
          grants: ev.payload.grants,
          grants_set_version: a.version,
          grants_suspended: ev.payload.reason === 'remembered' ? a.grants_suspended : false,
          updated_at: ev.ts,
        })
        .where(eq(automations.id, a.id))
        .run();
      return;
    }
    case 'automation.enable_requested':
      tx.update(automations)
        .set({ enable_request: { note: ev.payload.note, proposed_grants: ev.payload.proposed_grants, at: ev.ts } })
        .where(eq(automations.id, ev.payload.automation_id))
        .run();
      return;
    case 'automation.run_started': {
      const p = ev.payload;
      tx.insert(automationRuns)
        .values({
          id: p.run_id,
          project_id: ev.project_id,
          automation_id: p.automation_id,
          version: p.version,
          trigger: p.trigger,
          test: p.test,
          inputs: p.inputs,
          by: p.by,
          parent_run_id: p.parent?.run_id ?? null,
          parent_step_id: p.parent?.step_id ?? null,
          trigger_index: p.trigger_index ?? null,
          due_at: p.due_at ?? null,
          caught_up: p.caught_up ?? 0,
          status: 'running',
          started_at: ev.ts,
          deadline_at: p.deadline_at,
        })
        .run();
      if (p.trigger_index !== undefined && p.due_at) setLastDue(tx, p.automation_id, p.trigger_index, p.due_at);
      return;
    }
    case 'automation.trigger_skipped':
      setLastDue(tx, ev.payload.automation_id, ev.payload.trigger_index, ev.payload.due_at);
      return;
    case 'automation.step_changed': {
      const p = ev.payload;
      const where = and(eq(automationStepRuns.run_id, p.run_id), eq(automationStepRuns.step_id, p.step_id));
      const existing = tx.select().from(automationStepRuns).where(where).get();
      const newAttempt = !existing || existing.attempt !== p.attempt;
      const reset = newAttempt
        ? { route: null, outputs: {}, summary: null, error: null, agent_id: null, child_run_id: null, resume_at: null, gate: null, question: null, note: null, answered_by: null, started_at: null, finished_at: null }
        : {};
      const fields = {
        ...reset,
        attempt: p.attempt,
        status: p.status,
        updated_at: ev.ts,
        ...(p.route !== undefined ? { route: p.route } : {}),
        ...(p.outputs !== undefined ? { outputs: p.outputs } : {}),
        ...(p.summary !== undefined ? { summary: p.summary } : {}),
        ...(p.error !== undefined ? { error: p.error } : {}),
        ...(p.agent_id !== undefined ? { agent_id: p.agent_id } : {}),
        ...(p.child_run_id !== undefined ? { child_run_id: p.child_run_id } : {}),
        ...(p.resume_at !== undefined ? { resume_at: p.resume_at } : {}),
        ...(p.gate !== undefined ? { gate: p.gate } : {}),
        ...(p.question !== undefined ? { question: p.question } : {}),
        ...(p.note !== undefined ? { note: p.note } : {}),
        ...(p.answered_by !== undefined ? { answered_by: p.answered_by } : {}),
        ...(p.status === 'running' && (newAttempt || !existing?.started_at) ? { started_at: ev.ts } : {}),
        ...(TERMINAL_STEP.has(p.status) ? { finished_at: ev.ts } : {}),
      };
      if (existing) tx.update(automationStepRuns).set(fields).where(where).run();
      else tx.insert(automationStepRuns).values({ run_id: p.run_id, step_id: p.step_id, ...fields }).run();
      return;
    }
    case 'automation.run_finished':
      tx.update(automationRuns)
        .set({ status: ev.payload.status, summary: ev.payload.summary, reason: ev.payload.reason ?? null, finished_at: ev.ts })
        .where(eq(automationRuns.id, ev.payload.run_id))
        .run();
      return;
    default:
      return;
  }
}
