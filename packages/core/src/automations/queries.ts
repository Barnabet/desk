import { and, asc, desc, eq, inArray, isNull, lt } from 'drizzle-orm';
import type { EventOf } from '@desk/protocol';
import type { Db } from '../db/open';
import type { AgentRow } from '../state/queries';
import { agents, automationRuns, automations, automationStepRuns, automationVersions, events, projects } from '../db/schema';

export type AutomationRow = typeof automations.$inferSelect;
export type AutomationVersionRow = typeof automationVersions.$inferSelect;
export type AutomationRunRow = typeof automationRuns.$inferSelect;
export type StepRunRow = typeof automationStepRuns.$inferSelect;

/** Deleted automations too (their runs still point at them). */
export const getAutomation = (db: Db, id: string): AutomationRow | undefined => db.select().from(automations).where(eq(automations.id, id)).get();

export const findAutomation = (db: Db, projectId: string, name: string): AutomationRow | undefined =>
  db
    .select()
    .from(automations)
    .where(and(eq(automations.project_id, projectId), eq(automations.name, name), isNull(automations.deleted_at)))
    .get();

export const listAutomations = (db: Db, projectId: string): AutomationRow[] =>
  db
    .select()
    .from(automations)
    .where(and(eq(automations.project_id, projectId), isNull(automations.deleted_at)))
    .orderBy(asc(automations.name))
    .all();

/** Switched-on automations of projects that are not archived: what the schedule tick looks at. */
export const listEnabledAutomations = (db: Db): AutomationRow[] =>
  db
    .select({ a: automations })
    .from(automations)
    .innerJoin(projects, eq(projects.id, automations.project_id))
    .where(and(eq(automations.enabled, true), isNull(automations.deleted_at), isNull(projects.archived_at)))
    .all()
    .map((r) => r.a);

export const listVersions = (db: Db, automationId: string): AutomationVersionRow[] =>
  db.select().from(automationVersions).where(eq(automationVersions.automation_id, automationId)).orderBy(desc(automationVersions.version)).all();

export const getVersion = (db: Db, automationId: string, version: number): AutomationVersionRow | undefined =>
  db
    .select()
    .from(automationVersions)
    .where(and(eq(automationVersions.automation_id, automationId), eq(automationVersions.version, version)))
    .get();

export const getRun = (db: Db, runId: string): AutomationRunRow | undefined => db.select().from(automationRuns).where(eq(automationRuns.id, runId)).get();

/** Newest first; `before` pages by `started_at`. */
export const listRuns = (db: Db, automationId: string, opts: { before?: string; limit?: number } = {}): AutomationRunRow[] =>
  db
    .select()
    .from(automationRuns)
    .where(and(eq(automationRuns.automation_id, automationId), opts.before ? lt(automationRuns.started_at, opts.before) : undefined))
    .orderBy(desc(automationRuns.started_at), desc(automationRuns.id))
    .limit(opts.limit ?? 50)
    .all();

export const listRunningRuns = (db: Db): AutomationRunRow[] => db.select().from(automationRuns).where(eq(automationRuns.status, 'running')).all();

/** The automation's run still going (running or waiting): the overlap check. Test runs count only with `includeTests`. */
export const runningRunOf = (db: Db, automationId: string, opts: { includeTests?: boolean } = {}): AutomationRunRow | undefined =>
  db
    .select()
    .from(automationRuns)
    .where(and(eq(automationRuns.automation_id, automationId), eq(automationRuns.status, 'running'), opts.includeTests ? undefined : eq(automationRuns.test, false)))
    .get();

export const childRuns = (db: Db, parentRunId: string): AutomationRunRow[] => db.select().from(automationRuns).where(eq(automationRuns.parent_run_id, parentRunId)).all();

export const stepRuns = (db: Db, runId: string): StepRunRow[] => db.select().from(automationStepRuns).where(eq(automationStepRuns.run_id, runId)).all();

export const getStepRun = (db: Db, runId: string, stepId: string): StepRunRow | undefined =>
  db
    .select()
    .from(automationStepRuns)
    .where(and(eq(automationStepRuns.run_id, runId), eq(automationStepRuns.step_id, stepId)))
    .get();

/** The last succeeded non-test run: what `{{previous.…}}` reads. */
export const lastSucceededRun = (db: Db, automationId: string): AutomationRunRow | undefined =>
  db
    .select()
    .from(automationRuns)
    .where(and(eq(automationRuns.automation_id, automationId), eq(automationRuns.status, 'succeeded'), eq(automationRuns.test, false)))
    .orderBy(desc(automationRuns.started_at), desc(automationRuns.id))
    .limit(1)
    .get();

/** The highest version with a succeeded test run, or null. */
export function testedVersion(db: Db, automationId: string): number | null {
  const row = db
    .select({ v: automationRuns.version })
    .from(automationRuns)
    .where(and(eq(automationRuns.automation_id, automationId), eq(automationRuns.status, 'succeeded'), eq(automationRuns.test, true)))
    .orderBy(desc(automationRuns.version))
    .limit(1)
    .get();
  return row?.v ?? null;
}

/** A step agent's run and step row. */
export function stepAgentOf(db: Db, agentId: string): { run: AutomationRunRow; step: StepRunRow } | undefined {
  const a = db.select().from(agents).where(eq(agents.id, agentId)).get();
  if (!a?.automation_run_id || !a.automation_step_id) return undefined;
  const run = getRun(db, a.automation_run_id);
  const step = getStepRun(db, a.automation_run_id, a.automation_step_id);
  return run && step ? { run, step } : undefined;
}

/** Schedule times that started nothing, from the event log (newest first). */
export function listTriggerSkips(db: Db, projectId: string, automationId: string): Array<{ trigger_index: number; due_at: string; reason: 'still_running' | 'missed'; ts: string }> {
  return db
    .select()
    .from(events)
    .where(and(eq(events.project_id, projectId), inArray(events.type, ['automation.trigger_skipped'])))
    .orderBy(desc(events.id))
    .all()
    .map((row) => ({ ...row }) as unknown as EventOf<'automation.trigger_skipped'>)
    .filter((e) => e.payload.automation_id === automationId)
    .map((e) => ({ trigger_index: e.payload.trigger_index, due_at: e.payload.due_at, reason: e.payload.reason, ts: e.ts }));
}

/** The step agents of a run (optionally of one step), oldest first. */
export const stepAgentsOf = (db: Db, runId: string, stepId?: string): AgentRow[] =>
  db
    .select()
    .from(agents)
    .where(and(eq(agents.automation_run_id, runId), stepId ? eq(agents.automation_step_id, stepId) : undefined))
    .orderBy(asc(agents.created_at), asc(agents.id))
    .all();

/** Every automation id that has runs, deleted automations included (retention at start). */
export const automationIdsWithRuns = (db: Db): string[] =>
  db
    .selectDistinct({ id: automationRuns.automation_id })
    .from(automationRuns)
    .all()
    .map((r) => r.id);

/**
 * Runs whose folders retention keeps, per automation of the project: the last succeeded non-test run (what
 * `previous.*` reads) and the latest top-level run (a failed one carries an attention item).
 */
export function protectedRuns(db: Db, projectId: string): Set<string> {
  const out = new Set<string>();
  const ids = db.selectDistinct({ id: automationRuns.automation_id }).from(automationRuns).where(eq(automationRuns.project_id, projectId)).all();
  for (const { id } of ids) {
    const prev = lastSucceededRun(db, id);
    if (prev) out.add(prev.id);
    const latest = db
      .select({ id: automationRuns.id })
      .from(automationRuns)
      .where(and(eq(automationRuns.automation_id, id), isNull(automationRuns.parent_run_id)))
      .orderBy(desc(automationRuns.started_at), desc(automationRuns.id))
      .limit(1)
      .get();
    if (latest) out.add(latest.id);
  }
  return out;
}

export const runIdsOfProject = (db: Db, projectId: string): string[] =>
  db
    .select({ id: automationRuns.id })
    .from(automationRuns)
    .where(eq(automationRuns.project_id, projectId))
    .all()
    .map((r) => r.id);
