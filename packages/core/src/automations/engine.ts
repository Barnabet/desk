import {
  RETRY_BACKOFF_MS,
  SUMMARY_MAX,
  type AutomationDefinition,
  type AutomationEdge,
  type InputValue,
  type Outputs,
  type RunTrigger,
  type Step,
  type StepGate,
  type StepKind,
  type StepStatus,
} from '@desk/protocol';
import type { EventInput } from '@desk/protocol';
import { ConflictError, NotFoundError, ValidationError } from '../errors';
import type { EventStore } from '../events/store';
import { newId } from '../ids';
import { getProject } from '../state/queries';
import type { SandboxGuard } from '../tools/sandbox';
import { evalExpr, parseExpr, type Expr } from './expr';
import { ensureStepDir, prepareRunFolder } from './folders';
import { isSettled, readiness, TERMINAL_STEP, type StepState } from './graph';
import { getAutomation, getRun, getStepRun, getVersion, listRunningRuns, stepRuns, type AutomationRunRow, type StepRunRow } from './queries';
import { exprScope, matchUpstream, templateScope, timezoneOf } from './scope';
import { nextClock } from './schedule';
import { renderText } from './template';

export type StepResult =
  | { status: 'succeeded'; route: string | null; outputs: Outputs; summary: string }
  /** `retryable: false` skips `on_error: retry` (a crash-interrupted script that is not idempotent). */
  | { status: 'failed'; error: string; retryable?: boolean }
  | { status: 'rejected'; note?: string };

export type StepContext = { run: AutomationRunRow; def: AutomationDefinition; step: Step; attempt: number };
export type StepExecutor = (ctx: StepContext) => void | Promise<void>;

export type EngineHost = {
  store: EventStore;
  dataDir: string;
  guard: SandboxGuard;
  now(): Date;
  onError(err: unknown, context: string): void;
  /** Stores an `automation` message on Desk's stream (label `automation "<title>"`) and wakes Desk. */
  deliverToDesk(projectId: string, label: string, text: string): void;
};

export type StartRunOptions = {
  trigger: RunTrigger;
  test: boolean;
  inputs: Record<string, InputValue>;
  /** `user`, `schedule` or `agent:<id>`. */
  by: string;
  parent?: { runId: string; stepId: string };
  triggerIndex?: number;
  dueAt?: string;
  caughtUp?: number;
};

const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const agentOf = (by: string): string | null => (by.startsWith('agent:') ? by.slice('agent:'.length) : null);
const ACTIVE: ReadonlySet<StepStatus> = new Set<StepStatus>(['running', 'waiting']);

/**
 * Advances automation runs through their graphs (spec §3). Every change is an event; `advance` is synchronous and
 * re-entrant safe (a result delivered while advancing schedules one more pass). Step kinds run through executors that
 * the Runtime registers; wait, tell_desk and ask are built in.
 */
export class AutomationEngine {
  private readonly executors = new Map<StepKind, StepExecutor>();
  private readonly advancing = new Set<string>();
  private readonly again = new Set<string>();
  private readonly defs = new Map<string, AutomationDefinition>();
  private readonly exprs = new Map<string, Expr>();
  private readonly succeededHooks: Array<(ctx: StepContext) => void | Promise<void>> = [];
  private readonly finishedHooks: Array<(run: AutomationRunRow) => void> = [];
  private readonly tickHooks: Array<(now: Date) => void | Promise<void>> = [];
  private readonly cancelHooks: Array<(run: AutomationRunRow, row: StepRunRow) => void> = [];

  constructor(private readonly host: EngineHost) {
    this.register('wait', (ctx) => this.startWait(ctx));
    this.register('tell_desk', (ctx) => this.tellDesk(ctx));
    this.register('ask', (ctx) => this.ask(ctx));
  }

  private get db() {
    return this.host.store.db;
  }

  register(kind: StepKind, executor: StepExecutor): void {
    this.executors.set(kind, executor);
  }
  onStepSucceeded(fn: (ctx: StepContext) => void | Promise<void>): void {
    this.succeededHooks.push(fn);
  }
  onRunFinished(fn: (run: AutomationRunRow) => void): void {
    this.finishedHooks.push(fn);
  }
  onTick(fn: (now: Date) => void | Promise<void>): void {
    this.tickHooks.push(fn);
  }
  onCancelStep(fn: (run: AutomationRunRow, row: StepRunRow) => void): void {
    this.cancelHooks.push(fn);
  }

  /** The definition a run started with (its version), cached. */
  definitionOf(run: AutomationRunRow): AutomationDefinition {
    let def = this.defs.get(run.id);
    if (!def) {
      def = getVersion(this.db, run.automation_id, run.version)?.definition ?? getAutomation(this.db, run.automation_id)!.definition;
      this.defs.set(run.id, def);
    }
    return def;
  }

  private append(run: AutomationRunRow, e: Omit<EventInput, 'project_id' | 'agent_id'> & { agent_id?: string | null }): void {
    this.host.store.append({ project_id: run.project_id, agent_id: e.agent_id ?? null, type: e.type, payload: e.payload } as EventInput);
  }

  private stepChanged(run: AutomationRunRow, payload: Record<string, unknown> & { step_id: string; attempt: number; status: StepStatus }): void {
    this.append(run, { type: 'automation.step_changed', payload: { run_id: run.id, ...payload } } as never);
  }

  /**
   * Starts a run of the automation's current version. Bad inputs throw ValidationError before anything is recorded,
   * except for a scheduled run, which is recorded as started and failed so the user sees why it did not happen.
   */
  async startRun(automationId: string, opts: StartRunOptions): Promise<string> {
    const a = getAutomation(this.db, automationId);
    if (!a || a.deleted_at) throw new NotFoundError(`Unknown automation: ${automationId}`);
    if (getProject(this.db, a.project_id)?.archived_at) throw new ConflictError(`Project ${a.project_id} is archived`);
    const runId = newId();
    let inputs: Record<string, InputValue>;
    let failure: string | null = null;
    try {
      inputs = await prepareRunFolder({ dataDir: this.host.dataDir, runId, specs: a.definition.inputs, inputs: opts.inputs, guard: this.host.guard });
    } catch (e) {
      if (opts.trigger !== 'schedule' || !(e instanceof ValidationError)) throw e;
      inputs = opts.inputs;
      failure = e.message;
    }
    const now = this.host.now();
    const deadline = new Date(now.getTime() + a.definition.limits.run_deadline_hours * 3_600_000).toISOString();
    this.host.store.append({
      project_id: a.project_id,
      agent_id: agentOf(opts.by),
      type: 'automation.run_started',
      payload: {
        run_id: runId,
        automation_id: a.id,
        version: a.version,
        trigger: opts.trigger,
        test: opts.test,
        inputs,
        by: opts.by,
        ...(opts.parent ? { parent: { run_id: opts.parent.runId, step_id: opts.parent.stepId } } : {}),
        ...(opts.triggerIndex !== undefined ? { trigger_index: opts.triggerIndex } : {}),
        ...(opts.dueAt ? { due_at: opts.dueAt } : {}),
        ...(opts.caughtUp ? { caught_up: opts.caughtUp } : {}),
        deadline_at: deadline,
      },
    });
    const run = getRun(this.db, runId)!;
    if (failure) this.finish(run, 'failed', `Could not start: ${failure}`, failure);
    else this.advance(runId);
    return runId;
  }

  /** Re-entrant: a call made while the run is advancing runs one more pass afterwards. */
  private advance(runId: string): void {
    if (this.advancing.has(runId)) {
      this.again.add(runId);
      return;
    }
    this.advancing.add(runId);
    try {
      do {
        this.again.delete(runId);
        this.pass(runId);
      } while (this.again.has(runId));
    } finally {
      this.advancing.delete(runId);
    }
  }

  private states(run: AutomationRunRow): { rows: Map<string, StepRunRow>; states: Record<string, StepState> } {
    const rows = new Map(stepRuns(this.db, run.id).map((r) => [r.step_id, r]));
    const states: Record<string, StepState> = {};
    for (const [id, r] of rows) states[id] = { status: r.status, route: r.route };
    return { rows, states };
  }

  private when(run: AutomationRunRow, def: AutomationDefinition): (edge: AutomationEdge) => boolean {
    const scope = exprScope(this.db, run, def);
    return (edge) => {
      if (!edge.when) return true;
      let e = this.exprs.get(edge.when);
      if (!e) this.exprs.set(edge.when, (e = parseExpr(edge.when)));
      return evalExpr(e, scope);
    };
  }

  private pass(runId: string): void {
    let run = getRun(this.db, runId);
    if (!run || run.status !== 'running') return;
    const def = this.definitionOf(run);
    for (;;) {
      const { rows, states } = this.states(run);
      const { start, skip } = readiness(def, states, this.when(run, def));
      if (skip.length) {
        for (const id of skip) this.stepChanged(run, { step_id: id, attempt: rows.get(id)?.attempt ?? 1, status: 'skipped' });
        continue;
      }
      const now = this.host.now().getTime();
      const kindOf = (id: string) => def.steps.find((s) => s.id === id)!.kind;
      const active = [...rows.values()].filter((r) => ACTIVE.has(r.status));
      let agents = active.filter((r) => kindOf(r.step_id) === 'agent').length;
      let scripts = active.filter((r) => kindOf(r.step_id) === 'script' && r.status === 'running').length;
      for (const id of start) {
        const row = rows.get(id);
        if (row?.resume_at && Date.parse(row.resume_at) > now) continue; // retry backoff
        const kind = kindOf(id);
        if (kind === 'agent' && agents >= def.limits.max_parallel_agents) continue;
        if (kind === 'script' && scripts >= def.limits.max_parallel_scripts) continue;
        if (kind === 'agent') agents++;
        if (kind === 'script') scripts++;
        this.launch(run, def, def.steps.find((s) => s.id === id)!, row?.attempt ?? 1);
        run = getRun(this.db, runId)!;
        if (run.status !== 'running') return;
      }
      break;
    }
    const { states } = this.states(run);
    for (const s of def.steps) states[s.id] ??= { status: 'pending', route: null };
    if (isSettled(states)) this.finish(run, 'succeeded', this.summaryOf(run, def));
  }

  private launch(run: AutomationRunRow, def: AutomationDefinition, step: Step, attempt: number): void {
    const executor = this.executors.get(step.kind);
    this.stepChanged(run, { step_id: step.id, attempt, status: 'running' });
    if (!executor) {
      this.resolveStep(run.id, step.id, { status: 'failed', error: `No executor for ${step.kind} steps`, retryable: false });
      return;
    }
    const ctx: StepContext = { run, def, step, attempt };
    const fail = (e: unknown) => this.resolveStep(run.id, step.id, { status: 'failed', error: e instanceof Error ? e.message : String(e) });
    // Synchronous up to the executor, so built-in kinds (wait, ask, tell_desk) settle within this pass.
    try {
      ensureStepDir(this.host.dataDir, run.id, step.id);
      const pending = executor(ctx);
      if (pending instanceof Promise) pending.catch(fail);
    } catch (e) {
      fail(e);
    }
  }

  /**
   * Records a step's result and moves the run on. A failure retries (`on_error: {retry}`, with backoff), takes the
   * `error` route (`continue`), or fails the run (`stop`). Results for steps that already ended are ignored.
   */
  resolveStep(runId: string, stepId: string, result: StepResult, extra: { answered_by?: 'user'; note?: string; gate?: StepGate | null } = {}): void {
    const run = getRun(this.db, runId);
    if (!run || run.status !== 'running') return;
    const row = getStepRun(this.db, runId, stepId);
    if (!row || TERMINAL_STEP.has(row.status)) return;
    const def = this.definitionOf(run);
    const step = def.steps.find((s) => s.id === stepId)!;
    const base = { step_id: stepId, attempt: row.attempt, ...extra };
    if (result.status === 'succeeded') {
      this.stepChanged(run, { ...base, status: 'succeeded', route: result.route, outputs: result.outputs, summary: truncate(result.summary, SUMMARY_MAX), resume_at: null });
      const ctx: StepContext = { run, def, step, attempt: row.attempt };
      for (const hook of this.succeededHooks) {
        try {
          void Promise.resolve(hook(ctx)).catch((e) => this.host.onError(e, `automation step ${runId}/${stepId}`));
        } catch (e) {
          this.host.onError(e, `automation step ${runId}/${stepId}`);
        }
      }
    } else if (result.status === 'rejected') {
      this.stepChanged(run, { ...base, status: 'rejected', route: 'rejected', ...(result.note !== undefined ? { note: result.note, outputs: { note: result.note } } : {}) });
    } else {
      const error = truncate(result.error, 8000);
      const oe = step.on_error;
      if (typeof oe === 'object' && result.retryable !== false && row.attempt <= oe.retry) {
        this.stepChanged(run, { ...base, status: 'failed', error });
        const wait = RETRY_BACKOFF_MS[row.attempt - 1] ?? RETRY_BACKOFF_MS[RETRY_BACKOFF_MS.length - 1]!;
        this.stepChanged(run, { step_id: stepId, attempt: row.attempt + 1, status: 'pending', resume_at: new Date(this.host.now().getTime() + wait).toISOString() });
      } else if (oe === 'continue') {
        this.stepChanged(run, { ...base, status: 'failed', route: 'error', error });
      } else {
        this.stepChanged(run, { ...base, status: 'failed', error });
        this.cancelActive(getRun(this.db, runId)!, `${step.title} failed`);
        this.finish(getRun(this.db, runId)!, 'failed', `${step.title} failed: ${error}`, `${step.id} failed: ${error}`);
        return;
      }
    }
    this.advance(runId);
  }

  /** Cancels every step that has not ended (their agents, processes and child runs through the cancel hooks). */
  private cancelActive(run: AutomationRunRow, why: string): void {
    const def = this.definitionOf(run);
    const rows = new Map(stepRuns(this.db, run.id).map((r) => [r.step_id, r]));
    for (const s of def.steps) {
      const row = rows.get(s.id);
      if (row && TERMINAL_STEP.has(row.status)) continue;
      if (row) for (const hook of this.cancelHooks) hook(run, row);
      this.stepChanged(run, { step_id: s.id, attempt: row?.attempt ?? 1, status: 'cancelled', error: why });
    }
  }

  private finish(run: AutomationRunRow, status: 'succeeded' | 'failed' | 'cancelled', summary: string, reason?: string): void {
    this.append(run, { type: 'automation.run_finished', payload: { run_id: run.id, status, summary: truncate(summary, 4000), ...(reason ? { reason } : {}) } } as never);
    this.defs.delete(run.id);
    const done = getRun(this.db, run.id)!;
    for (const hook of this.finishedHooks) {
      try {
        hook(done);
      } catch (e) {
        this.host.onError(e, `automation run ${run.id}`);
      }
    }
  }

  /** The run's summary: its output step's, else the last succeeded step's, else a plain line. */
  private summaryOf(run: AutomationRunRow, def: AutomationDefinition): string {
    const rows = stepRuns(this.db, run.id);
    const out = def.output_step ? rows.find((r) => r.step_id === def.output_step && r.status === 'succeeded') : undefined;
    const last = rows.filter((r) => r.status === 'succeeded' && r.summary).sort((a, b) => (b.finished_at ?? '').localeCompare(a.finished_at ?? ''))[0];
    return out?.summary ?? last?.summary ?? `${def.title} finished`;
  }

  async cancelRun(runId: string, reason: string): Promise<void> {
    const run = getRun(this.db, runId);
    if (!run) throw new NotFoundError(`Unknown run: ${runId}`);
    if (run.status !== 'running') throw new ConflictError(`Run ${runId} already ended (${run.status})`);
    this.cancelActive(run, `Run cancelled: ${reason}`);
    this.finish(getRun(this.db, runId)!, 'cancelled', `Cancelled: ${reason}`, reason);
  }

  /** The user's answer to an Ask me step (script gates: Task 13). */
  async answer(runId: string, stepId: string, a: { decision: 'approve' | 'reject'; note?: string; remember?: boolean }): Promise<void> {
    const run = getRun(this.db, runId);
    const row = run ? getStepRun(this.db, runId, stepId) : undefined;
    if (!run || !row) throw new NotFoundError(`Unknown step: ${runId}/${stepId}`);
    if (run.status !== 'running' || row.status !== 'waiting' || (!row.question && !row.gate)) throw new ConflictError('This step is not waiting for an answer');
    if (row.question) {
      if (a.remember) throw new ValidationError('Only approvals can be remembered');
      if (a.decision === 'approve') {
        this.resolveStep(runId, stepId, { status: 'succeeded', route: null, outputs: { note: a.note ?? null }, summary: a.note ? `Approved: ${a.note}` : 'Approved' }, { answered_by: 'user', ...(a.note ? { note: a.note } : {}) });
      } else {
        this.resolveStep(runId, stepId, { status: 'rejected', ...(a.note ? { note: a.note } : {}) }, { answered_by: 'user' });
      }
      return;
    }
    await this.answerGate(run, row, a);
  }

  /** Script gates are answered in Task 13. */
  protected async answerGate(_run: AutomationRunRow, _row: StepRunRow, _a: { decision: 'approve' | 'reject'; note?: string; remember?: boolean }): Promise<void> {
    throw new ConflictError('This step is not waiting for an answer');
  }

  /** Deadlines, waits, question expiries and retry backoffs; then the hooks (schedules, timeouts). */
  async tick(): Promise<void> {
    const now = this.host.now();
    for (const run of listRunningRuns(this.db)) {
      try {
        if (Date.parse(run.deadline_at) <= now.getTime()) {
          await this.cancelRun(run.id, 'deadline');
          continue;
        }
        const def = this.definitionOf(run);
        let retryDue = false;
        for (const row of stepRuns(this.db, run.id)) {
          if (!row.resume_at || Date.parse(row.resume_at) > now.getTime()) continue;
          const kind = def.steps.find((s) => s.id === row.step_id)?.kind;
          if (row.status === 'waiting' && kind === 'wait') this.resolveStep(run.id, row.step_id, { status: 'succeeded', route: null, outputs: {}, summary: 'Waited' });
          else if (row.status === 'waiting' && row.question) this.resolveStep(run.id, row.step_id, { status: 'rejected', note: 'expired' });
          else if (row.status === 'pending') retryDue = true;
        }
        if (retryDue) this.advance(run.id);
      } catch (e) {
        this.host.onError(e, `automation run ${run.id}`);
      }
    }
    for (const hook of this.tickHooks) {
      try {
        await hook(now);
      } catch (e) {
        this.host.onError(e, 'automation tick');
      }
    }
  }

  // ── built-in step kinds ──────────────────────────────────────────

  private startWait({ run, def, step, attempt }: StepContext): void {
    if (step.kind !== 'wait') return;
    const now = this.host.now();
    const at = step.minutes !== undefined ? new Date(now.getTime() + step.minutes * 60_000) : nextClock(step.until!, timezoneOf(def), now);
    this.stepChanged(run, { step_id: step.id, attempt, status: 'waiting', resume_at: at.toISOString() });
  }

  private tellDesk({ run, def, step }: StepContext): void {
    if (step.kind !== 'tell_desk') return;
    const scope = templateScope({ db: this.db, dataDir: this.host.dataDir, run, def });
    const files = matchUpstream({ dataDir: this.host.dataDir, run, def, stepId: step.id, globs: step.attach });
    const text = [renderText(step.text, scope), '', `(Run ${run.id} of automation "${def.title}".${files.length ? ` Files: ${files.join(', ')}` : ''})`].join('\n');
    this.host.deliverToDesk(run.project_id, `automation "${def.title}"`, text);
    this.resolveStep(run.id, step.id, { status: 'succeeded', route: null, outputs: {}, summary: 'Told Desk' });
  }

  private ask({ run, def, step, attempt }: StepContext): void {
    if (step.kind !== 'ask') return;
    const scope = templateScope({ db: this.db, dataDir: this.host.dataDir, run, def });
    const files = matchUpstream({ dataDir: this.host.dataDir, run, def, stepId: step.id, globs: step.show });
    const expires = step.expires_after_hours ? new Date(this.host.now().getTime() + step.expires_after_hours * 3_600_000).toISOString() : null;
    this.stepChanged(run, {
      step_id: step.id,
      attempt,
      status: 'waiting',
      question: { text: renderText(step.question, scope), files, ...(step.approve_label ? { approve_label: step.approve_label } : {}), ...(step.reject_label ? { reject_label: step.reject_label } : {}) },
      resume_at: expires,
    });
  }
}
