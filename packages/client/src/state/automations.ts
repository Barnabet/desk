// packages/client/src/state/automations.ts
import type { AutomationSummary, EventOf, RunDetail, RunStatus, StepRunInfo, StoredEvent } from '@desk/protocol';

/** A project's automations as the app shows them: summaries (by name) and the runs it has loaded. */
export type AutomationsState = { projectId: string; list: AutomationSummary[]; runs: Record<string, RunDetail> };

const TERMINAL_STEP = new Set(['succeeded', 'failed', 'rejected', 'skipped', 'cancelled']);

export const emptyAutomations = (projectId: string): AutomationsState => ({ projectId, list: [], runs: {} });

export const withAutomationSummaries = (s: AutomationsState, list: AutomationSummary[]): AutomationsState => ({ ...s, list: [...list].sort((a, b) => a.name.localeCompare(b.name)) });

export const withRunDetail = (s: AutomationsState, detail: RunDetail): AutomationsState => ({ ...s, runs: { ...s.runs, [detail.id]: detail } });

/** Whether a step waits on the user (not on a timer). */
const waitsOnUser = (st: StepRunInfo) => st.status === 'waiting' && (st.question !== null || st.gate !== null || st.agent_id !== null || st.child_run_id !== null);

/** A loaded run's status from its steps, as the daemon derives it (spec §2.2), minus agent approvals it cannot see. */
export function derivedRunStatus(run: RunDetail): RunStatus {
  if (run.status === 'succeeded' || run.status === 'failed' || run.status === 'cancelled') return run.status;
  if (run.steps.some((st) => st.status === 'running')) return 'running';
  return run.steps.some(waitsOnUser) ? 'waiting' : 'running';
}

/** What a waiting run waits on, as the daemon words it in summaries. */
function waitingOn(run: RunDetail): string | null {
  for (const st of run.steps) {
    if (!waitsOnUser(st)) continue;
    const title = run.definition.steps.find((x) => x.id === st.step_id)?.title ?? st.step_id;
    if (st.question) return `Ask me: ${title}`;
    if (st.gate) return `Approval: ${st.gate.subject}`;
  }
  return null;
}

const EMPTY_STEP = { route: null, outputs: {}, summary: null, error: null, agent_id: null, child_run_id: null, resume_at: null, gate: null, question: null, note: null, started_at: null, finished_at: null };

/** Folds a step change like the daemon's projection: a new attempt starts from a blank step. */
function applyStep(run: RunDetail, e: EventOf<'automation.step_changed'>): RunDetail {
  const p = e.payload;
  const i = run.steps.findIndex((st) => st.step_id === p.step_id);
  const existing = i >= 0 ? run.steps[i]! : undefined;
  const fresh = !existing || existing.attempt !== p.attempt;
  const base: StepRunInfo = fresh ? { step_id: p.step_id, attempt: p.attempt, status: p.status, ...EMPTY_STEP } : existing;
  const next: StepRunInfo = {
    ...base,
    attempt: p.attempt,
    status: p.status,
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
    ...(p.status === 'running' && (fresh || !base.started_at) ? { started_at: e.ts } : {}),
    ...(TERMINAL_STEP.has(p.status) ? { finished_at: e.ts } : {}),
  };
  const steps = i >= 0 ? run.steps.map((st, j) => (j === i ? next : st)) : [...run.steps, next];
  const updated = { ...run, steps };
  return { ...updated, status: derivedRunStatus(updated) };
}

function withSummary(s: AutomationsState, id: string, fn: (a: AutomationSummary) => AutomationSummary): AutomationsState {
  const i = s.list.findIndex((a) => a.id === id);
  if (i < 0) return s;
  return { ...s, list: s.list.map((a, j) => (j === i ? fn(a) : a)) };
}

/** Updates the summary whose last run is `runId`. */
function withLastRun(s: AutomationsState, runId: string, fn: (r: NonNullable<AutomationSummary['last_run']>) => NonNullable<AutomationSummary['last_run']>): AutomationsState {
  const i = s.list.findIndex((a) => a.last_run?.id === runId);
  if (i < 0) return s;
  return { ...s, list: s.list.map((a, j) => (j === i ? { ...a, last_run: fn(a.last_run!) } : a)) };
}

/** Folds one event into the automations state (pure; replaying an event changes nothing). */
export function reduceAutomations(s: AutomationsState, e: StoredEvent): AutomationsState {
  if (e.project_id !== s.projectId) return s;
  switch (e.type) {
    case 'automation.saved': {
      const p = e.payload;
      const schedules = p.definition.triggers.map((t) => ({ cron: t.cron, timezone: t.timezone }));
      const fields = { title: p.definition.title, description: p.definition.description, version: p.version, schedules, updated_at: e.ts };
      if (s.list.some((a) => a.id === p.automation_id)) return withSummary(s, p.automation_id, (a) => ({ ...a, ...fields }));
      const added: AutomationSummary = { id: p.automation_id, project_id: e.project_id, name: p.name, tested_version: null, enabled: false, grants_suspended: false, last_run: null, next_due: null, enable_requested: false, ...fields };
      return withAutomationSummaries(s, [...s.list, added]);
    }
    case 'automation.deleted':
      return { ...s, list: s.list.filter((a) => a.id !== e.payload.automation_id) };
    case 'automation.switched':
      return withSummary(s, e.payload.automation_id, (a) => ({ ...a, enabled: e.payload.enabled, ...(e.payload.enabled ? { enable_requested: false } : {}) }));
    case 'automation.grants_set':
      return e.payload.reason === 'remembered' ? s : withSummary(s, e.payload.automation_id, (a) => ({ ...a, grants_suspended: false }));
    case 'automation.enable_requested':
      return withSummary(s, e.payload.automation_id, (a) => ({ ...a, enable_requested: true }));
    case 'automation.run_started': {
      const p = e.payload;
      return withSummary(s, p.automation_id, (a) => ({ ...a, last_run: { id: p.run_id, status: 'running', trigger: p.trigger, test: p.test, started_at: e.ts, finished_at: null, summary: null, waiting_on: null } }));
    }
    case 'automation.step_changed': {
      const run = s.runs[e.payload.run_id];
      if (!run) return s;
      const next = applyStep(run, e);
      const out = withRunDetail(s, next);
      return withLastRun(out, next.id, (r) => ({ ...r, status: next.status, waiting_on: next.status === 'waiting' ? waitingOn(next) : null }));
    }
    case 'automation.run_finished': {
      const p = e.payload;
      const run = s.runs[p.run_id];
      const out = run ? withRunDetail(s, { ...run, status: p.status, summary: p.summary, reason: p.reason ?? null, finished_at: e.ts }) : s;
      return withLastRun(out, p.run_id, (r) => ({ ...r, status: p.status, summary: p.summary, finished_at: e.ts, waiting_on: null }));
    }
    default:
      return s;
  }
}

export const affectsAutomations = (e: StoredEvent): boolean => e.type.startsWith('automation.');

export const affectsRun = (e: StoredEvent, run: RunDetail): boolean =>
  ((e.type === 'approval.requested' || e.type === 'approval.resolved') && run.steps.some((st) => st.agent_id !== null && st.agent_id === e.agent_id)) ||
  (e.type === 'automation.run_finished' && run.steps.some((st) => st.child_run_id === e.payload.run_id));
