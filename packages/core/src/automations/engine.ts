import { lstatSync, mkdirSync, realpathSync, rmSync } from 'node:fs';
import { basename, join } from 'node:path';
import {
  MAX_SUB_DEPTH,
  RETRY_BACKOFF_MS,
  SUMMARY_MAX,
  type AutomationDefinition,
  type ArtifactKind,
  type AutomationEdge,
  type InputValue,
  type Outputs,
  type RunTrigger,
  type Step,
  type StepGate,
  type StepKind,
  type StepStatus,
} from '@desk/protocol';
import type { EventInput, Grant, PolicyRule, RunFinishReason, StoredEvent } from '@desk/protocol';
import { ConflictError, NotFoundError, ValidationError } from '../errors';
import { evaluatePolicy } from '../policy/evaluate';
import type { SkillSummary } from '../skills/store';
import type { EventStore } from '../events/store';
import type { CreateStepAgentInput } from '../runtime/runtime';
import type { AgentRow } from '../state/queries';
import { newId } from '../ids';
import { getProject, pendingApprovalsFor } from '../state/queries';
import { readAgentFile } from '../tools/agent-files';
import { scrubbedEnv, withSkillEnv } from '../tools/bash';
import type { SandboxGuard } from '../tools/sandbox';
import { skillRunTool } from '../tools/skills';
import { evalExpr, parseExpr, type Expr } from './expr';
import { ensureStepDir, logFile, prepareRunFolder, runDir, stepDir } from './folders';
import { activeGrantRules, addGrant, deriveGrant } from './grants';
import { isSettled, readiness, TERMINAL_STEP, type StepState } from './graph';
import { findAutomation, getAutomation, getRun, getStepRun, getVersion, listRunningRuns, stepAgentOf, stepAgentsOf, stepRuns, type AutomationRunRow, type StepRunRow } from './queries';
import { runReport } from './report';
import { exprScope, matchIn, matchUpstream, stepResultDir, templateScope, timezoneOf } from './scope';
import { localStamp, nextClock } from './schedule';
import { lastLine, parseStepOutput, runScript } from './script';
import { renderArgs, renderText } from './template';

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
  script: ScriptHost;
  /** Copies a file into the project library under `relDir` (Runtime.publishToLibraryAt). */
  publish(projectId: string, file: string, relDir: string, meta: { title: string; kind: ArtifactKind; description: string }, origin: string): Promise<{ id: string; path: string }>;
  agent: AgentHost;
};

export type AgentHost = {
  create(input: CreateStepAgentInput): Promise<string>;
  get(agentId: string): AgentRow | undefined;
  ending(agentId: string): { reason: RunFinishReason; detail: string | null } | null;
  stop(agentId: string, reason: string): void;
  archive(agentId: string): Promise<void>;
  activeMs(agentId: string): number;
  nudge(agentId: string, text: string): void;
  nudges(agentId: string): number;
  threadModel(projectId: string): string;
};

/** Time an agent spent in model runs: the sum of its run.started → run.finished spans, the open one up to `nowMs`. */
export function activeMs(events: StoredEvent[], nowMs: number): number {
  let total = 0;
  let open: number | null = null;
  for (const e of events) {
    if (e.type === 'run.started') open = Date.parse(e.ts);
    else if (e.type === 'run.finished' && open !== null) {
      total += Date.parse(e.ts) - open;
      open = null;
    }
  }
  return open !== null ? total + Math.max(0, nowMs - open) : total;
}

export const PUBLISH_MAX_FILES = 50;
const PARENT_ENDED = 'Its parent run ended';
const NUDGE = 'Call complete with your result, or fail_step with the reason. Nobody reads plain replies in an automation run.';

export type ScriptHost = {
  /** A usable skill of the project (project → global → built-in), or null (missing, turned off or broken). */
  resolve(projectId: string, name: string): SkillSummary | null;
  locate(skill: SkillSummary, script: string): string;
  /** Builds a built-in's environment on first use and waits for it. */
  prepare(projectId: string, skill: SkillSummary, signal: AbortSignal): Promise<void>;
  env(projectId: string, skill: SkillSummary): { bins: string[]; vars: Record<string, string>; blocked: string | null };
  sandboxEnabled(): Promise<boolean>;
  policy(projectId: string): PolicyRule[];
  /** Adds a grant the user remembered (reason `remembered`). */
  remember(automationId: string, grant: Grant): void;
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
  /** A caller-chosen id: a sub-automation step records it before the child can finish. */
  runId?: string;
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
  /** Running script processes, by `<run>/<step>`. */
  private readonly scripts = new Map<string, AbortController>();
  /** Child runs this engine cancelled because their parent run ended: the parent ignores their end. */
  private readonly parentEnded = new Set<string>();
  /** Executor work still going (child starts, agent creation, script processes). */
  private readonly background = new Set<Promise<unknown>>();
  private readonly publishing = new Map<string, Set<Promise<void>>>();

  constructor(private readonly host: EngineHost) {
    this.register('wait', (ctx) => this.startWait(ctx));
    this.register('tell_desk', (ctx) => this.tellDesk(ctx));
    this.register('ask', (ctx) => this.ask(ctx));
    this.register('script', (ctx) => this.scriptStep(ctx, false));
    this.onCancelStep((run, row) => this.scripts.get(`${run.id}/${row.step_id}`)?.abort());
    this.register('agent', (ctx) => this.agentStep(ctx));
    this.onCancelStep((_run, row) => {
      if (row.agent_id) this.host.agent.stop(row.agent_id, 'Its automation run ended');
    });
    this.onRunFinished((run) => {
      for (const a of stepAgentsOf(this.db, run.id)) {
        if (!a.archived_at) void this.host.agent.archive(a.id).catch((e) => this.host.onError(e, `archiving step agent ${a.id}`));
      }
    });
    this.onTick(() => this.agentTimeouts());
    this.register('automation', (ctx) => this.subAutomation(ctx));
    this.onStepSucceeded((ctx) => this.publishStep(ctx));
    this.onRunFinished((run) => this.track(this.afterRun(run)));
    this.onRunFinished((run) => this.childFinished(run));
    this.onCancelStep((_run, row) => {
      const child = row.child_run_id ? getRun(this.db, row.child_run_id) : undefined;
      if (child?.status !== 'running') return;
      this.parentEnded.add(child.id);
      void this.cancelRun(child.id, PARENT_ENDED).catch((e) => this.host.onError(e, `cancelling child run ${child.id}`));
    });
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

  /** The definition a run started with (its version), cached while the run is going. */
  definitionOf(run: AutomationRunRow): AutomationDefinition {
    const cached = this.defs.get(run.id);
    if (cached) return cached;
    const def = getVersion(this.db, run.automation_id, run.version)?.definition ?? getAutomation(this.db, run.automation_id)!.definition;
    if (run.status === 'running') this.defs.set(run.id, def);
    return def;
  }

  /** Background work that tests and shutdown wait for. */
  private track<T>(p: Promise<T>): void {
    const q = p.catch((e) => this.host.onError(e, 'automation background work')).finally(() => this.background.delete(q));
    this.background.add(q);
  }

  /** Resolves once no executor work is left (work it starts meanwhile included). */
  async settled(): Promise<void> {
    while (this.background.size) await Promise.allSettled([...this.background]);
  }

  /** Whether executor work is still going. */
  get busy(): boolean {
    return this.background.size > 0;
  }

  /** The run at the top of a sub-automation chain. */
  rootOf(run: AutomationRunRow): AutomationRunRow {
    let r = run;
    while (r.parent_run_id) {
      const up = getRun(this.db, r.parent_run_id);
      if (!up) break;
      r = up;
    }
    return r;
  }

  /** Copies the step's `publish` matches to the library (spec §3.3); a file that cannot be copied is skipped. */
  private publishStep({ run, def, step }: StepContext): void {
    if (!step.publish.length) return;
    const automation = getAutomation(this.db, run.automation_id)!;
    const stamp = localStamp(timezoneOf(def), new Date(run.started_at));
    const relDir = run.test ? `automations/${automation.name}/tests/${stamp}` : `automations/${automation.name}/${stamp}`;
    const dir = stepResultDir(this.db, this.host.dataDir, run.id, getStepRun(this.db, run.id, step.id), step.id);
    const job = (async () => {
      for (const file of matchIn(dir, step.publish).slice(0, PUBLISH_MAX_FILES)) {
        try {
          await this.host.publish(run.project_id, file, relDir, { title: basename(file), kind: 'file', description: `From "${step.title}" in automation "${def.title}" (run ${run.id})` }, `automation:${run.id}`);
        } catch (e) {
          this.host.onError(e, `publishing ${file} from run ${run.id}`);
        }
      }
    })();
    let set = this.publishing.get(run.id);
    if (!set) this.publishing.set(run.id, (set = new Set()));
    set.add(job);
    this.track(job);
  }

  /** After a top-level run: its report to Desk for `desk_review`, or when Desk started it (spec §4.7, §6.1). */
  private async afterRun(run: AutomationRunRow): Promise<void> {
    await Promise.allSettled([...(this.publishing.get(run.id) ?? [])]);
    this.publishing.delete(run.id);
    if (run.parent_run_id) return;
    const def = this.definitionOf(run);
    const byDesk = run.by.startsWith('agent:');
    if (def.after_run !== 'desk_review' && !byDesk) return;
    const why = byDesk ? '' : ' This automation asks you to review each run.';
    this.host.deliverToDesk(run.project_id, `automation "${def.title}"`, `Run ${run.id} ${run.status}.${why}\n\n${runReport(this.db, this.host.dataDir, run.id, this.host.now())}`);
  }

  /** A step of a run Desk started now waits on the user: Desk hears it once (the report comes at the end). */
  private noticeWaiting(run: AutomationRunRow, stepId: string): void {
    const root = this.rootOf(run);
    if (!root.by.startsWith('agent:')) return;
    const def = this.definitionOf(run);
    const step = def.steps.find((s) => s.id === stepId);
    const row = getStepRun(this.db, run.id, stepId);
    const ap = row?.agent_id ? pendingApprovalsFor(this.db, row.agent_id)[0] : undefined;
    const what = row?.question ? `Ask me: ${row.question.text}` : row?.gate ? `approve ${row.gate.subject}` : ap ? `approve ${ap.tool} (${ap.reason})` : 'an answer';
    const inSub = run.id !== root.id ? ` (in its sub-automation "${def.title}")` : '';
    this.host.deliverToDesk(
      root.project_id,
      `automation "${this.definitionOf(root).title}"`,
      `Run ${root.id} is waiting for the user at "${step?.title ?? stepId}"${inSub} (${truncate(what, 600)}). Only the user can answer, in the app. You'll get the report when the run finishes.`,
    );
  }

  /** How many runs this one is nested under (0 for a run nobody started as a step). */
  depthOf(run: AutomationRunRow): number {
    let depth = 0;
    for (let r = run; r.parent_run_id; depth++) {
      const up = getRun(this.db, r.parent_run_id);
      if (!up) break;
      r = up;
    }
    return depth;
  }

  /** Run another automation (spec §4.4): a child run, linked before it starts, which this step waits for. */
  private async subAutomation({ run, def, step, attempt }: StepContext): Promise<void> {
    if (step.kind !== 'automation') return;
    const target = findAutomation(this.db, run.project_id, step.automation);
    if (!target) return this.resolveStep(run.id, step.id, { status: 'failed', error: `Unknown automation: ${step.automation}` });
    if (this.depthOf(run) >= MAX_SUB_DEPTH) {
      return this.resolveStep(run.id, step.id, { status: 'failed', error: `Sub-automations nest at most ${MAX_SUB_DEPTH} deep`, retryable: false });
    }
    const scope = templateScope({ db: this.db, dataDir: this.host.dataDir, run, def });
    const inputs: Record<string, InputValue> = {};
    for (const [key, template] of Object.entries(step.inputs)) {
      const value = renderText(template, scope);
      if (value !== '') inputs[key] = value; // an empty value leaves the child's default
    }
    const childId = newId();
    this.stepChanged(run, { step_id: step.id, attempt, status: 'running', child_run_id: childId });
    await this.startRun(target.id, { runId: childId, trigger: 'parent', test: run.test, inputs, by: run.by, parent: { runId: run.id, stepId: step.id } });
  }

  /** A child run ended: its parent step follows it, unless the parent is the one that ended it. */
  private childFinished(child: AutomationRunRow): void {
    if (!child.parent_run_id || !child.parent_step_id) return;
    if (this.parentEnded.delete(child.id)) return;
    const row = getStepRun(this.db, child.parent_run_id, child.parent_step_id);
    if (row?.child_run_id !== child.id) return;
    const def = this.definitionOf(child);
    if (child.status === 'succeeded') {
      const out = def.output_step ? getStepRun(this.db, child.id, def.output_step) : undefined;
      const outputs: Outputs = out?.status === 'succeeded' ? { ...out.outputs, run_id: child.id } : { run_id: child.id, status: child.status };
      this.resolveStep(child.parent_run_id, child.parent_step_id, { status: 'succeeded', route: null, outputs, summary: child.summary ?? `${def.title} finished` });
    } else {
      this.resolveStep(child.parent_run_id, child.parent_step_id, { status: 'failed', error: `Sub-automation "${def.title}" ${child.status}: ${child.reason ?? child.summary ?? 'no reason'}` });
    }
  }

  private append(run: AutomationRunRow, e: Omit<EventInput, 'project_id' | 'agent_id'> & { agent_id?: string | null }): void {
    this.host.store.append({ project_id: run.project_id, agent_id: e.agent_id ?? null, type: e.type, payload: e.payload } as EventInput);
  }

  private stepChanged(run: AutomationRunRow, payload: Record<string, unknown> & { step_id: string; attempt: number; status: StepStatus }): void {
    this.append(run, { type: 'automation.step_changed', payload: { run_id: run.id, ...payload } } as never);
    if (payload.status === 'waiting' && (payload.question || payload.gate)) this.noticeWaiting(getRun(this.db, run.id) ?? run, payload.step_id);
  }

  /**
   * Starts a run of the automation's current version. Bad inputs throw ValidationError before anything is recorded,
   * except for a scheduled run, which is recorded as started and failed so the user sees why it did not happen.
   */
  async startRun(automationId: string, opts: StartRunOptions): Promise<string> {
    const a = getAutomation(this.db, automationId);
    if (!a || a.deleted_at) throw new NotFoundError(`Unknown automation: ${automationId}`);
    if (getProject(this.db, a.project_id)?.archived_at) throw new ConflictError(`Project ${a.project_id} is archived`);
    const runId = opts.runId ?? newId();
    let inputs: Record<string, InputValue>;
    let failure: string | null = null;
    try {
      inputs = await prepareRunFolder({ dataDir: this.host.dataDir, runId, specs: a.definition.inputs, inputs: opts.inputs, guard: this.host.guard });
    } catch (e) {
      if ((opts.trigger !== 'schedule' && opts.trigger !== 'parent') || !(e instanceof ValidationError)) throw e;
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
      if (pending instanceof Promise) this.track(pending.catch(fail));
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

  /** An agent step (spec §4.2): a fresh step agent per attempt, linked to the step before it can run. */
  private async agentStep({ run, def, step, attempt }: StepContext): Promise<void> {
    if (step.kind !== 'agent') return;
    for (const old of stepAgentsOf(this.db, run.id, step.id)) if (!old.archived_at) await this.host.agent.archive(old.id);
    const automation = getAutomation(this.db, run.automation_id)!;
    const brief = renderText(step.brief, templateScope({ db: this.db, dataDir: this.host.dataDir, run, def }));
    const agentId = newId();
    this.stepChanged(run, { step_id: step.id, attempt, status: 'running', agent_id: agentId });
    await this.host.agent.create({
      agentId,
      projectId: run.project_id,
      runId: run.id,
      stepId: step.id,
      title: step.title,
      brief,
      model: step.model ?? this.host.agent.threadModel(run.project_id),
      ...(step.reasoning_effort ? { reasoningEffort: step.reasoning_effort } : {}),
      skills: step.skills,
      ...(step.git_source_id ? { gitSourceId: step.git_source_id } : {}),
      automationName: automation.name,
    });
  }

  /**
   * After a step agent's run, when its step has not settled (complete and fail_step settle it at once): an approval
   * leaves it waiting; a failure, a stop or the step limit fails it; a plain reply gets one nudge, then fails it.
   */
  onAgentEnded(agent: AgentRow): void {
    const link = stepAgentOf(this.db, agent.id);
    if (!link) return;
    const { run, step: row } = link;
    if (run.status !== 'running' || row.agent_id !== agent.id || TERMINAL_STEP.has(row.status)) return;
    const fail = (error: string) => this.resolveStep(run.id, row.step_id, { status: 'failed', error });
    const ending = this.host.agent.ending(agent.id);
    switch (agent.status) {
      case 'failed':
        return fail(ending?.detail ?? 'The step agent failed');
      case 'cancelled':
        return fail('The step agent was stopped');
      case 'idle': {
        if (ending?.reason === 'max_steps') {
          const step = this.definitionOf(run).steps.find((s) => s.id === row.step_id);
          return fail(`Reached the step limit (${step?.kind === 'agent' ? (step.max_steps ?? 100) : 100}) without completing`);
        }
        if (this.host.agent.nudges(agent.id) === 0) return this.host.agent.nudge(agent.id, NUDGE);
        return fail('Ended its turn twice without calling complete or fail_step');
      }
      case 'waiting':
        return this.noticeWaiting(run, row.step_id);
      default:
        return; // queued or running: not ended
    }
  }

  /** Agent steps past their active-time limit are stopped and fail (spec §3.1: waits and questions do not count). */
  private agentTimeouts(): void {
    for (const run of listRunningRuns(this.db)) {
      const def = this.definitionOf(run);
      for (const row of stepRuns(this.db, run.id)) {
        const step = def.steps.find((s) => s.id === row.step_id);
        if (step?.kind !== 'agent' || !row.agent_id || TERMINAL_STEP.has(row.status)) continue;
        const limit = (step.timeout_min ?? 60) * 60_000;
        if (this.host.agent.activeMs(row.agent_id) <= limit) continue;
        this.host.agent.stop(row.agent_id, 'Timed out');
        this.resolveStep(run.id, row.step_id, { status: 'failed', error: `Timed out after ${step.timeout_min ?? 60} minutes of work` });
      }
    }
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

  /** The user's answer to a script step's gate: run it (optionally remembering a grant), or deny it. */
  protected async answerGate(run: AutomationRunRow, row: StepRunRow, a: { decision: 'approve' | 'reject'; note?: string; remember?: boolean }): Promise<void> {
    const gate = row.gate!;
    if (a.decision === 'reject') {
      this.resolveStep(run.id, row.step_id, { status: 'failed', error: `Denied by the user${a.note ? `: ${a.note}` : ''}`, retryable: false }, { answered_by: 'user', gate, ...(a.note ? { note: a.note } : {}) });
      return;
    }
    const automation = getAutomation(this.db, run.automation_id)!;
    if (a.remember) {
      if (automation.grants_suspended) throw new ConflictError('Grants are suspended until you keep them; approve without remembering, or review the change first');
      this.host.script.remember(automation.id, deriveGrant(gate.tool, { command: gate.subject }, automation.name));
    }
    this.stepChanged(run, { step_id: row.step_id, attempt: row.attempt, status: 'running', answered_by: 'user', gate, ...(a.note ? { note: a.note } : {}) });
    const def = this.definitionOf(run);
    await this.scriptStep({ run, def, step: def.steps.find((s) => s.id === row.step_id)!, attempt: row.attempt }, true);
  }

  /**
   * A script step (spec §4.1): resolve the skill and file, render argv, pass the policy (grants first; `ask` waits for the
   * user as a gate), then run it in the step folder and read DESK_OUTPUT.
   */
  private async scriptStep(ctx: StepContext, gateApproved: boolean): Promise<void> {
    const { run, def, step, attempt } = ctx;
    if (step.kind !== 'script') return;
    const fail = (error: string, retryable = true) => this.resolveStep(run.id, step.id, { status: 'failed', error, retryable });
    const skill = this.host.script.resolve(run.project_id, step.skill);
    if (!skill) return fail(`Skill ${step.skill} is not available in this project (missing, turned off or broken)`, false);
    let file: string;
    try {
      file = this.host.script.locate(skill, step.script);
    } catch (e) {
      return fail(e instanceof Error ? e.message : String(e), false);
    }
    const scope = templateScope({ db: this.db, dataDir: this.host.dataDir, run, def });
    const args = renderArgs(step.args, scope);
    const stdin = step.stdin !== undefined ? renderText(step.stdin, scope) : undefined;
    const sandboxEnabled = await this.host.script.sandboxEnabled();
    if (!gateApproved) {
      const automation = getAutomation(this.db, run.automation_id)!;
      const rules = [...activeGrantRules(automation), ...this.host.script.policy(run.project_id)];
      const d = evaluatePolicy(skillRunTool, { name: step.skill, script: step.script, args, timeout_s: 120 }, rules, { sandboxAvailable: sandboxEnabled });
      if (d.action === 'deny') return fail(`Denied by policy. ${d.reason}`, false);
      if (d.action === 'ask') {
        const subject = [`${step.skill}/${step.script}`, ...args].join(' ');
        this.stepChanged(run, { step_id: step.id, attempt, status: 'waiting', gate: { tool: 'skill_run', subject, reason: d.reason } });
        return;
      }
    }
    const key = `${run.id}/${step.id}`;
    const controller = new AbortController();
    this.scripts.set(key, controller);
    try {
      await this.host.script.prepare(run.project_id, skill, controller.signal);
      const env = this.host.script.env(run.project_id, skill);
      if (env.blocked) return fail(env.blocked);
      const dir = stepDir(this.host.dataDir, run.id, step.id);
      // The step folder is the script's: a retry may find a symlink planted by the last attempt. rm never follows one.
      rmSync(join(dir, '.desk'), { recursive: true, force: true });
      mkdirSync(join(dir, '.desk'));
      const output = join(dir, '.desk', 'output.json');
      const r = await runScript({
        file,
        args,
        ...(stdin !== undefined ? { stdin } : {}),
        stepDir: dir,
        env: {
          ...withSkillEnv(scrubbedEnv(dir), env),
          SKILL_DIR: skill.dir,
          SKILL_NAME: skill.name,
          DESK_RUN_DIR: runDir(this.host.dataDir, run.id),
          DESK_STEP_DIR: dir,
          DESK_INPUTS: join(runDir(this.host.dataDir, run.id), 'inputs.json'),
          DESK_OUTPUT: output,
          ...(run.test ? { DESK_TEST: '1' } : {}),
        },
        sandbox: { enabled: sandboxEnabled, writable: [dir], guard: this.host.guard },
        timeoutMs: (step.timeout_min ?? 10) * 60_000,
        signal: controller.signal,
        logFile: logFile(this.host.dataDir, run.id, step.id),
      });
      if (r.aborted) return; // cancelled (the cancel already ended the step) or the daemon is stopping
      if (r.timedOut) return fail(`Timed out after ${step.timeout_min ?? 10} minutes`);
      if (r.exitCode !== 0) return fail(`Exit code ${r.exitCode}: ${r.output.slice(-4000)}`);
      if (!lstatSync(output, { throwIfNoEntry: false })) {
        this.resolveStep(run.id, step.id, { status: 'succeeded', route: null, outputs: {}, summary: lastLine(r.output) ?? 'Done' });
        return;
      }
      // Read as a file the script controls: regular, no symlink, not swapped, never a secret.
      let raw: string;
      try {
        raw = (await readAgentFile(join(realpathSync(dir), '.desk', 'output.json'), this.host.guard)).toString('utf8');
      } catch (e) {
        return fail(`DESK_OUTPUT could not be read: ${e instanceof Error ? e.message : String(e)}`, false);
      }
      const parsed = parseStepOutput(raw, step.routes);
      if (!parsed.ok) return fail(parsed.error, false);
      this.resolveStep(run.id, step.id, { status: 'succeeded', route: parsed.route, outputs: parsed.outputs, summary: parsed.summary ?? lastLine(r.output) ?? 'Done' });
    } finally {
      this.scripts.delete(key);
    }
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
    const files = matchUpstream({ db: this.db, dataDir: this.host.dataDir, run, def, stepId: step.id, globs: step.attach });
    const text = [renderText(step.text, scope), '', `(Run ${run.id} of automation "${def.title}".${files.length ? ` Files: ${files.join(', ')}` : ''})`].join('\n');
    this.host.deliverToDesk(run.project_id, `automation "${def.title}"`, text);
    this.resolveStep(run.id, step.id, { status: 'succeeded', route: null, outputs: {}, summary: 'Told Desk' });
  }

  private ask({ run, def, step, attempt }: StepContext): void {
    if (step.kind !== 'ask') return;
    const scope = templateScope({ db: this.db, dataDir: this.host.dataDir, run, def });
    const files = matchUpstream({ db: this.db, dataDir: this.host.dataDir, run, def, stepId: step.id, globs: step.show });
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
