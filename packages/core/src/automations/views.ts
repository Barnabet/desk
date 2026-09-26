import type {
  AutomationDetail,
  AutomationSummary,
  AutomationVersionInfo,
  RunDetail,
  RunInfo,
  RunStatus,
  RunSummary,
  StepRunInfo,
  StepStatus,
} from '@desk/protocol';
import type { Db } from '../db/open';
import { NotFoundError } from '../errors';
import { pendingApprovalsFor } from '../state/queries';
import type { Tool } from '../tools/types';
import { proposedGrants } from './grants';
import {
  getAutomation,
  getRun,
  getVersion,
  listRuns,
  listVersions,
  stepRuns,
  testedVersion,
  type AutomationRow,
  type AutomationRunRow,
  type StepRunRow,
} from './queries';
import { nextDue } from './schedule';

/** A step as the user sees it: an agent step whose agent waits on an approval shows `waiting`. */
/**
 * A step as the user sees it: an agent step whose agent waits on an approval, or a sub-automation step whose child
 * run waits on the user, shows `waiting`.
 */
export function effectiveStepStatus(db: Db, row: StepRunRow): StepStatus {
  if (row.status !== 'running') return row.status;
  if (row.agent_id && pendingApprovalsFor(db, row.agent_id).length > 0) return 'waiting';
  const child = row.child_run_id ? getRun(db, row.child_run_id) : undefined;
  return child && effectiveRunStatus(db, child) === 'waiting' ? 'waiting' : 'running';
}

/** Whether the step waits on the user (not on a timer): a question, a script gate, an agent's approval, or a child's wait. */
function waitsOnUser(db: Db, row: StepRunRow): boolean {
  return effectiveStepStatus(db, row) === 'waiting' && (row.question !== null || row.gate !== null || row.agent_id !== null || row.child_run_id !== null);
}

/** A running run is `waiting` while a step waits on the user and none is effectively running (spec §2.2). */
export function effectiveRunStatus(db: Db, run: AutomationRunRow): RunStatus {
  if (run.status !== 'running') return run.status;
  const rows = stepRuns(db, run.id);
  const running = rows.some((r) => effectiveStepStatus(db, r) === 'running');
  return !running && rows.some((r) => waitsOnUser(db, r)) ? 'waiting' : 'running';
}

/** What a waiting run waits on, for lists: `Ask me: <title>`, `Approval: <subject or tool>`. */
export function waitingOn(db: Db, run: AutomationRunRow): string | null {
  if (effectiveRunStatus(db, run) !== 'waiting') return null;
  const def = getVersion(db, run.automation_id, run.version)?.definition;
  for (const row of stepRuns(db, run.id)) {
    if (!waitsOnUser(db, row)) continue;
    const title = def?.steps.find((s) => s.id === row.step_id)?.title ?? row.step_id;
    if (row.question) return `Ask me: ${title}`;
    if (row.gate) return `Approval: ${row.gate.subject}`;
    const child = row.child_run_id ? getRun(db, row.child_run_id) : undefined;
    const inner = child ? waitingOn(db, child) : null;
    if (inner) return `${title} › ${inner}`;
    const ap = row.agent_id ? pendingApprovalsFor(db, row.agent_id)[0] : undefined;
    if (ap) return `Approval: ${title} wants to run ${ap.tool}`;
  }
  return null;
}

function runSummary(db: Db, run: AutomationRunRow): RunSummary {
  return {
    id: run.id,
    status: effectiveRunStatus(db, run),
    trigger: run.trigger,
    test: run.test,
    started_at: run.started_at,
    finished_at: run.finished_at,
    summary: run.summary,
    waiting_on: waitingOn(db, run),
  };
}

/** The next schedule time of a switched-on automation (a time already due shows as now), or null. */
export function nextDueOf(row: AutomationRow, now: Date): string | null {
  if (!row.enabled) return null;
  let best: number | null = null;
  row.definition.triggers.forEach((t, i) => {
    const last = row.last_due[String(i)];
    let next = nextDue(t.cron, t.timezone, last ? new Date(last) : now);
    if (next && next.getTime() < now.getTime()) next = now;
    if (next && (best === null || next.getTime() < best)) best = next.getTime();
  });
  return best === null ? null : new Date(best).toISOString();
}

export function automationSummary(db: Db, row: AutomationRow, now: Date): AutomationSummary {
  const last = listRuns(db, row.id, { limit: 1 })[0];
  return {
    id: row.id,
    project_id: row.project_id,
    name: row.name,
    title: row.title,
    description: row.description,
    version: row.version,
    tested_version: testedVersion(db, row.id),
    enabled: row.enabled,
    grants_suspended: row.grants_suspended,
    schedules: row.definition.triggers.map((t) => ({ cron: t.cron, timezone: t.timezone })),
    last_run: last ? runSummary(db, last) : null,
    next_due: nextDueOf(row, now),
    enable_requested: row.enable_request !== null,
    updated_at: row.updated_at,
  };
}

export function automationDetail(db: Db, row: AutomationRow, now: Date, tools: (name: string) => Tool | undefined): AutomationDetail {
  return {
    ...automationSummary(db, row, now),
    definition: row.definition,
    layout: row.layout,
    grants: row.grants,
    proposed_grants: proposedGrants(db, row.id, tools),
    grants_set_version: row.grants_set_version,
    enable_request: row.enable_request ?? null,
  };
}

export function versionInfos(db: Db, automationId: string): AutomationVersionInfo[] {
  const tested = new Set(
    listRuns(db, automationId, { limit: 10_000 })
      .filter((r) => r.test && r.status === 'succeeded')
      .map((r) => r.version),
  );
  return listVersions(db, automationId).map((v) => ({ version: v.version, origin: v.origin, change_note: v.change_note, via: v.via, created_at: v.created_at, tested: tested.has(v.version) }));
}

export function runInfo(row: AutomationRunRow): RunInfo {
  return {
    id: row.id,
    automation_id: row.automation_id,
    project_id: row.project_id,
    version: row.version,
    trigger: row.trigger,
    test: row.test,
    inputs: row.inputs,
    by: row.by,
    parent_run_id: row.parent_run_id,
    parent_step_id: row.parent_step_id,
    due_at: row.due_at,
    caught_up: row.caught_up,
    status: row.status,
    summary: row.summary,
    reason: row.reason,
    started_at: row.started_at,
    finished_at: row.finished_at,
    deadline_at: row.deadline_at,
  };
}

export function stepRunInfo(row: StepRunRow): StepRunInfo {
  return {
    step_id: row.step_id,
    attempt: row.attempt,
    status: row.status,
    route: row.route,
    outputs: row.outputs,
    summary: row.summary,
    error: row.error,
    agent_id: row.agent_id,
    child_run_id: row.child_run_id,
    resume_at: row.resume_at,
    gate: row.gate ?? null,
    question: row.question ?? null,
    note: row.note,
    started_at: row.started_at,
    finished_at: row.finished_at,
  };
}

/** A run with every step of its version (steps not reached yet show `pending`) and derived statuses. */
export function runDetail(db: Db, runId: string): RunDetail {
  const run = getRun(db, runId);
  if (!run) throw new NotFoundError(`Unknown run: ${runId}`);
  const automation = getAutomation(db, run.automation_id)!;
  const definition = getVersion(db, run.automation_id, run.version)?.definition ?? automation.definition;
  const rows = new Map(stepRuns(db, run.id).map((r) => [r.step_id, r]));
  const steps = definition.steps.map((s): StepRunInfo => {
    const row = rows.get(s.id);
    if (!row)
      return { step_id: s.id, attempt: 0, status: 'pending', route: null, outputs: {}, summary: null, error: null, agent_id: null, child_run_id: null, resume_at: null, gate: null, question: null, note: null, started_at: null, finished_at: null };
    return { ...stepRunInfo(row), status: effectiveStepStatus(db, row) };
  });
  return { ...runInfo(run), status: effectiveRunStatus(db, run), automation_name: automation.name, automation_title: automation.title, definition, steps };
}
