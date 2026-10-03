import { mkdir, rm } from 'node:fs/promises';
import { quoteLines, sanitizeLabel, type CheckWhere } from '@desk/protocol';
import { ConflictError, NotFoundError, ValidationError } from '../errors';
import type { EventStore } from '../events/store';
import { newId } from '../ids';
import { formatCheckLine, formatDuration, getCheck, gitState, receiptOf, runningChecks, type CheckRow, type ReceiptInput } from '../receipts/receipts';
import { latestSubmission } from '../reviews/reviews';
import { getAgent, getSource } from '../state/queries';
import type { SandboxGuard } from '../tools/sandbox';
import { discardStepWorktree, git } from '../workspaces/workspaces';
import { CHECK_TAIL_LINES, CHECK_TIMEOUT_S, checkDirs, MAX_RUNNING_CHECKS, runCheckSteps, stepLog, tailText, type CheckResult } from './runner';

export type RunCheckInput = { title: string; steps: string[]; where: CheckWhere; expect: string[]; timeoutS?: number };

export type CheckDeps = {
  store: EventStore;
  /** `<data>/projects/<id>`: checks live in its `checks/` folder, which Desk reads. */
  projectDir: (projectId: string) => string;
  sandboxEnabled: () => Promise<boolean>;
  guard: SandboxGuard;
  recordReceipt: (agentId: string, receipt: ReceiptInput) => void;
  /** Delivers a check's end to Desk (a `check` message) and wakes it. */
  notifyDesk: (deskId: string, text: string) => void;
  onError: (err: unknown, context: string) => void;
};

/** Where a check runs, resolved: its folder, what its steps may write, and how to read its git state. */
type Place = { cwd: string; writable: string[]; head: string | null; state: () => Promise<{ head: string | null; dirty: boolean | null }>; snapshotOf?: string };

const NO_GIT = async () => ({ head: null, dirty: null });
const SHUTDOWN = 'Desk shut down while it ran';

/**
 * Desk's check jobs (spec 2026-10-03 §2): model-less background runs of a few commands, sandboxed like bash, that
 * wake Desk with the outcome. A check still running when deskd stops is recorded `interrupted` and never re-run.
 */
export class CheckJobs {
  private readonly running = new Map<string, { controller: AbortController; done: Promise<void> }>();
  private shuttingDown = false;

  constructor(private readonly d: CheckDeps) {}

  /** Starts a check for Desk and returns its row; the steps run in the background. */
  async start(deskId: string, input: RunCheckInput): Promise<CheckRow> {
    const db = this.d.store.db;
    const desk = getAgent(db, deskId);
    if (!desk || desk.role !== 'desk') throw new ValidationError('Only Desk runs checks.');
    if (this.shuttingDown) throw new ConflictError('Desk is shutting down.');
    const busy = runningChecks(db, desk.project_id);
    if (busy.length >= MAX_RUNNING_CHECKS) {
      throw new ConflictError(`${busy.length} checks are already running (at most ${MAX_RUNNING_CHECKS}); wait for one or cancel it with cancel_check:\n${busy.map(formatCheckLine).join('\n')}`);
    }
    const steps = input.steps.map((s) => s.trim()).filter(Boolean);
    if (!steps.length) throw new ValidationError('Give at least one command to run.');
    const id = `chk_${newId()}`;
    const dirs = checkDirs(this.d.projectDir(desk.project_id), id);
    await mkdir(dirs.out, { recursive: true });
    await mkdir(dirs.logs, { recursive: true });
    let place: Place;
    try {
      place = await this.place(desk.project_id, input.where, dirs.work);
    } catch (err) {
      await rm(dirs.root, { recursive: true, force: true });
      throw err;
    }
    const timeoutS = input.timeoutS ?? CHECK_TIMEOUT_S;
    this.d.store.append({
      project_id: desk.project_id,
      agent_id: deskId,
      type: 'check.started',
      payload: { check_id: id, title: input.title, steps, where: input.where, expect: input.expect, timeout_s: timeoutS, cwd: place.cwd, head: place.head },
    });
    const controller = new AbortController();
    const done = this.run(deskId, id, { steps, place, dirs, timeoutS, expect: input.expect, controller })
      .catch((err) => this.d.onError(err, `check ${id}`))
      .finally(() => this.running.delete(id));
    this.running.set(id, { controller, done });
    return getCheck(db, id)!;
  }

  /** Resolves `where`: scratch, a clean snapshot at a thread's submitted commit, a thread's workspace or a source (both read-only). */
  private async place(projectId: string, where: CheckWhere, work: string): Promise<Place> {
    const db = this.d.store.db;
    if (where === 'scratch') {
      await mkdir(work, { recursive: true });
      return { cwd: work, writable: [work], head: null, state: NO_GIT };
    }
    if ('source_id' in where) {
      const s = getSource(db, where.source_id);
      if (!s || s.project_id !== projectId) throw new NotFoundError(`Unknown source: ${where.source_id}`);
      const state = s.kind === 'git' ? () => gitState(s.path) : NO_GIT;
      return { cwd: s.path, writable: [], head: (await state()).head, state };
    }
    const t = getAgent(db, where.thread_id);
    if (!t || t.project_id !== projectId || t.role !== 'thread') throw new NotFoundError(`Unknown thread: ${where.thread_id}`);
    const name = `"${sanitizeLabel(t.title ?? 'untitled')}"`;
    if (where.mode === 'workspace') {
      if (t.archived_at || !t.workspace_path) throw new ConflictError(`${name} has no workspace to check.`);
      const ws = t.workspace_path;
      const state = t.git_branch ? () => gitState(ws) : NO_GIT;
      return { cwd: ws, writable: [], head: (await state()).head, state };
    }
    const source = t.git_source_id ? getSource(db, t.git_source_id) : undefined;
    if (!source || !t.git_branch) throw new ValidationError(`${name} does not work in a git repository: check its workspace instead (mode "workspace").`);
    const commit = latestSubmission(db, t.id)?.commit ?? (await git(source.path, ['rev-parse', '--verify', `${t.git_branch}^{commit}`]).catch(() => null));
    if (!commit) throw new ConflictError(`${name} has no commit to check.`);
    await git(source.path, ['worktree', 'add', '--detach', work, commit]);
    return { cwd: work, writable: [work], head: commit, state: () => gitState(work), snapshotOf: source.path };
  }

  private async run(
    deskId: string,
    id: string,
    o: { steps: string[]; place: Place; dirs: ReturnType<typeof checkDirs>; timeoutS: number; expect: string[]; controller: AbortController },
  ): Promise<void> {
    const started = Date.now();
    let result: CheckResult | undefined;
    let error: string | undefined;
    try {
      result = await runCheckSteps({
        steps: o.steps,
        cwd: o.place.cwd,
        out: o.dirs.out,
        logs: o.dirs.logs,
        sandbox: { enabled: await this.d.sandboxEnabled(), writable: [...o.place.writable, o.dirs.out], guard: this.d.guard },
        timeoutMs: o.timeoutS * 1000,
        expect: o.expect,
        signal: o.controller.signal,
        state: o.place.state,
        onStep: (e) =>
          this.d.recordReceipt(
            deskId,
            receiptOf({
              tool: 'run_check',
              command: e.command,
              cwd: o.place.cwd,
              state: e.state,
              startedAt: e.startedAt,
              exitCode: e.exitCode,
              timedOut: e.timedOut,
              aborted: e.aborted,
              output: { bytes: e.bytes, sha256: e.sha256 },
              checkId: id,
              step: e.step,
            }),
          ),
      });
    } catch (err) {
      error = (err as Error).message;
    } finally {
      // The working copy goes; out/ and logs/ stay for Desk to read.
      if (o.place.snapshotOf) await discardStepWorktree({ path: o.dirs.work, gitSourcePath: o.place.snapshotOf }).catch((err) => this.d.onError(err, `removing ${id}'s snapshot`));
      else if (o.place.writable.includes(o.dirs.work)) await rm(o.dirs.work, { recursive: true, force: true }).catch(() => undefined);
    }
    const reason = o.controller.signal.reason;
    const interrupted = reason === SHUTDOWN;
    const status: Exclude<CheckRow['status'], 'running'> = error ? 'failed' : interrupted ? 'interrupted' : result!.status;
    const why = error ? `could not run: ${error}` : interrupted ? SHUTDOWN : result!.status === 'cancelled' && typeof reason === 'string' ? reason : result!.reason;
    const end = { status, failed_step: result?.failedStep ?? null, reason: why, duration_ms: Date.now() - started };
    const tail = end.failed_step !== null && status !== 'passed' ? await tailText(stepLog(o.dirs.logs, end.failed_step), CHECK_TAIL_LINES) : null;
    this.finish(deskId, id, end, result, o.dirs, tail);
  }

  /** Records a check's end and tells Desk: status, each step, and the failing step's last lines. */
  private finish(
    deskId: string,
    id: string,
    end: { status: Exclude<CheckRow['status'], 'running'>; failed_step: number | null; reason: string; duration_ms: number },
    result: CheckResult | undefined,
    dirs: ReturnType<typeof checkDirs>,
    tail: { text: string; truncated: boolean } | null,
  ): void {
    const c = getCheck(this.d.store.db, id);
    if (!c || c.status !== 'running') return;
    this.d.store.append({ project_id: c.project_id, agent_id: deskId, type: 'check.finished', payload: { check_id: id, ...end } });
    this.d.notifyDesk(deskId, checkNotice(getCheck(this.d.store.db, id)!, result, dirs.out, tail));
  }

  /** Stops a running check; it ends `cancelled`. */
  cancel(deskId: string, checkId: string, reason = 'cancelled by Desk'): CheckRow {
    const c = this.require(deskId, checkId);
    if (c.status !== 'running') throw new ConflictError(`${c.id} already ended (${c.status.replace('_', ' ')}).`);
    this.running.get(c.id)?.controller.abort(reason);
    return c;
  }

  /** The last `lines` lines of a step's log (the failing step, else the last one run). */
  async log(deskId: string, checkId: string, step?: number, lines = 100): Promise<string> {
    const c = this.require(deskId, checkId);
    const dirs = checkDirs(this.d.projectDir(c.project_id), c.id);
    const n = step ?? c.failed_step ?? c.steps.length;
    if (n < 1 || n > c.steps.length) throw new ValidationError(`${c.id} has steps 1 to ${c.steps.length}.`);
    const tail = await tailText(stepLog(dirs.logs, n), lines);
    const label = `Step ${n} of ${c.id} (${c.status.replace('_', ' ')}): ${c.steps[n - 1]!.replace(/\s+/g, ' ').slice(0, 160)}`;
    if (!tail.text) return `${label}\n(no output${c.status === 'running' ? ' yet' : ', or the step did not run'})`;
    return `${label}\n${tail.truncated ? `(last ${lines} lines)\n` : ''}${quoteLines(tail.text)}`;
  }

  private require(deskId: string, checkId: string): CheckRow {
    const c = getCheck(this.d.store.db, checkId);
    const desk = getAgent(this.d.store.db, deskId);
    if (!c || !desk || c.project_id !== desk.project_id) throw new NotFoundError(`Unknown check: ${checkId}`);
    return c;
  }

  /** Cancels a project's running checks (archive). */
  cancelProject(projectId: string): void {
    for (const c of runningChecks(this.d.store.db, projectId)) this.running.get(c.id)?.controller.abort('Project archived');
  }

  /** Stops every running check; each is recorded `interrupted`, its notice read by Desk after the restart. */
  async shutdown(): Promise<void> {
    this.shuttingDown = true;
    const all = [...this.running.values()];
    for (const r of all) r.controller.abort(SHUTDOWN);
    await Promise.all(all.map((r) => r.done));
  }

  /** After a crash: checks still marked running are recorded `interrupted` (never re-run), and Desk is told. */
  recover(): void {
    for (const c of runningChecks(this.d.store.db)) {
      if (this.running.has(c.id)) continue;
      const dirs = checkDirs(this.d.projectDir(c.project_id), c.id);
      this.finish(c.agent_id, c.id, { status: 'interrupted', failed_step: null, reason: 'Desk stopped unexpectedly while it ran', duration_ms: Math.max(0, Date.now() - Date.parse(c.started_at)) }, undefined, dirs, null);
      const t = typeof c.where === 'object' && 'thread_id' in c.where && c.where.mode === 'snapshot' ? getAgent(this.d.store.db, c.where.thread_id) : undefined;
      const source = t?.git_source_id ? getSource(this.d.store.db, t.git_source_id) : undefined;
      const removed = source ? discardStepWorktree({ path: dirs.work, gitSourcePath: source.path }) : rm(dirs.work, { recursive: true, force: true });
      void removed.catch((err) => this.d.onError(err, `removing ${c.id}'s working copy`));
    }
  }
}

/** What Desk reads when a check ends: status, each step run, the failing step's last lines (quoted) and the output folder. */
export function checkNotice(c: CheckRow, result: CheckResult | undefined, out: string, tail: { text: string; truncated: boolean } | null): string {
  const n = c.steps.length;
  const lines = [`Check ${c.status.replace('_', ' ')}: ${sanitizeLabel(c.title)} (${c.id}) · ${n} step${n === 1 ? '' : 's'}${c.duration_ms !== null ? ` · ${formatDuration(c.duration_ms)}` : ''}`];
  if (c.reason && c.status !== 'passed') lines.push(`Reason: ${c.reason}`);
  for (const s of result?.steps ?? []) {
    const how = s.outcome === 'timeout' ? 'timed out' : s.outcome === 'aborted' ? 'stopped' : `exit ${s.exitCode}`;
    lines.push(`- step ${s.step}: ${how} · ${formatDuration(s.durationMs)} · ${s.command.replace(/\s+/g, ' ').slice(0, 120)}`);
  }
  const ran = result?.steps.length ?? 0;
  if (result && ran < n) lines.push(`Not run: step${n - ran === 1 ? '' : 's'} ${Array.from({ length: n - ran }, (_, i) => ran + i + 1).join(', ')}`);
  if (tail?.text && c.failed_step !== null) lines.push(`Last lines of step ${c.failed_step}'s log${tail.truncated ? ' (check_log reads more)' : ''}:`, quoteLines(tail.text));
  lines.push(`Output folder: ${out}`);
  return lines.join('\n');
}
