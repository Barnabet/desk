import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { lstat, mkdir, open } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import type { CheckStatus, ReceiptOutcome } from '@desk/protocol';
import { stripAnsi } from '../services/manager';
import { scrubbedEnv } from '../tools/bash';
import { runProcess, type ProcessResult } from '../tools/process';
import { isWithin, shellInvocation, type SandboxSpec } from '../tools/sandbox';

/** Running checks a project may have at once (spec 2026-10-03 §2). */
export const MAX_RUNNING_CHECKS = 3;
export const CHECK_TIMEOUT_S = 1800;
export const MAX_CHECK_TIMEOUT_S = 3600;
/** Lines of the failing step's log in the notice to Desk. */
export const CHECK_TAIL_LINES = 20;
/** How an expected file names the check's output folder. */
export const OUT_PREFIX = '$DESK_CHECK_OUT/';

/** A check's folders under `<data>/checks/<id>/`: `work/` (scratch or snapshot), `out/` (always writable) and `logs/` (deskd's). */
export function checkDirs(dataDir: string, id: string): { root: string; work: string; out: string; logs: string } {
  const root = join(dataDir, 'checks', id);
  return { root, work: join(root, 'work'), out: join(root, 'out'), logs: join(root, 'logs') };
}

export const stepLog = (logs: string, step: number) => join(logs, `step-${step}.log`);

export type StepRun = { step: number; command: string; exitCode: number | null; outcome: ReceiptOutcome; durationMs: number };
export type CheckResult = { status: Exclude<CheckStatus, 'running' | 'interrupted'>; failedStep: number | null; reason: string; steps: StepRun[] };
/** What one step leaves for its receipt: its log's size and hash stand for its output. */
export type StepEnd = {
  step: number;
  command: string;
  startedAt: number;
  state: { head: string | null; dirty: boolean | null };
  exitCode: number | null;
  timedOut: boolean;
  aborted: boolean;
  bytes: number;
  sha256: string;
};

async function hashFile(path: string): Promise<{ bytes: number; sha256: string }> {
  const h = createHash('sha256');
  let bytes = 0;
  for await (const chunk of createReadStream(path)) {
    h.update(chunk as Buffer);
    bytes += (chunk as Buffer).length;
  }
  return { bytes, sha256: h.digest('hex') };
}

/** The last `lines` lines of a log (its last 256 KB at most), without terminal escapes. */
export async function tailText(path: string, lines: number): Promise<{ text: string; truncated: boolean }> {
  const f = await open(path, 'r').catch(() => null);
  if (!f) return { text: '', truncated: false };
  try {
    const { size } = await f.stat();
    const len = Math.min(size, 256 * 1024);
    const buf = Buffer.alloc(len);
    await f.read(buf, 0, len, size - len);
    const all = stripAnsi(buf.toString('utf8')).replace(/\r(?!\n)/g, '\n').split('\n');
    if (all.at(-1) === '') all.pop();
    if (len < size) all.shift(); // a partial first line
    return { text: all.slice(-lines).join('\n'), truncated: all.length > lines || len < size };
  } finally {
    await f.close();
  }
}

/**
 * Where an expected file is: relative to the working folder, or under `$DESK_CHECK_OUT/`. Refuses a path that leaves
 * its folder.
 */
export function expectedPath(entry: string, cwd: string, out: string): string {
  const [root, rel] = entry.startsWith(OUT_PREFIX) ? [out, entry.slice(OUT_PREFIX.length)] : [cwd, entry];
  const p = resolve(root, rel);
  if (!rel || !isWithin(p, root) || p === root) throw new Error(`Expected file ${entry} is outside the check's folders`);
  return p;
}

/** Why an expected file does not count, or null: it must be a regular file (not a link), non-empty, modified since `since`. */
async function missing(entry: string, cwd: string, out: string, since: number): Promise<string | null> {
  const st = await lstat(expectedPath(entry, cwd, out)).catch(() => null);
  if (!st) return `missing ${entry}`;
  if (!st.isFile()) return `${entry} is not a regular file`;
  if (st.size === 0) return `${entry} is empty`;
  // Some filesystems keep whole seconds.
  if (st.mtimeMs < Math.floor(since / 1000) * 1000) return `${entry} was not written by this check`;
  return null;
}

/**
 * Runs a check's steps in order with zsh, sandboxed, each into its own log, stopping at the first failure; then checks
 * the expected files. `timeoutMs` covers every step. Nothing is retried.
 */
export async function runCheckSteps(o: {
  steps: string[];
  cwd: string;
  out: string;
  logs: string;
  sandbox: SandboxSpec;
  timeoutMs: number;
  expect: string[];
  signal: AbortSignal;
  /** The folder's git state, read before each step for its receipt. */
  state: () => Promise<{ head: string | null; dirty: boolean | null }>;
  onStep: (end: StepEnd) => void;
}): Promise<CheckResult> {
  await mkdir(o.logs, { recursive: true });
  const startedAt = Date.now();
  const deadline = startedAt + o.timeoutMs;
  const env = { ...scrubbedEnv(o.cwd), DESK_CHECK_OUT: o.out };
  const steps: StepRun[] = [];
  for (const [i, command] of o.steps.entries()) {
    const step = i + 1;
    const left = deadline - Date.now();
    if (left <= 0) return { status: 'timed_out', failedStep: step, reason: `timed out before step ${step}`, steps };
    if (o.signal.aborted) return { status: 'cancelled', failedStep: step, reason: 'cancelled', steps };
    const state = await o.state();
    const log = stepLog(o.logs, step);
    const f = await open(log, 'w');
    const t0 = Date.now();
    let r: ProcessResult;
    try {
      r = await runProcess({ ...shellInvocation(command, o.sandbox), cwd: o.cwd, env, timeoutMs: left, signal: o.signal, outputFd: f.fd });
    } catch (err) {
      await f.write(`[failed to start: ${(err as Error).message}]\n`);
      r = { exitCode: null, output: '', timedOut: false, aborted: false };
    } finally {
      await f.close();
    }
    const fp = await hashFile(log);
    o.onStep({ step, command, startedAt: t0, state, exitCode: r.exitCode, timedOut: r.timedOut, aborted: r.aborted, ...fp });
    const outcome: ReceiptOutcome = r.timedOut ? 'timeout' : r.aborted ? 'aborted' : 'exit';
    steps.push({ step, command, exitCode: r.exitCode, outcome, durationMs: Date.now() - t0 });
    if (r.aborted) return { status: 'cancelled', failedStep: step, reason: 'cancelled', steps };
    if (r.timedOut) return { status: 'timed_out', failedStep: step, reason: `timed out after ${Math.round(o.timeoutMs / 1000)} s in step ${step}`, steps };
    if (r.exitCode !== 0) return { status: 'failed', failedStep: step, reason: r.exitCode === null ? `step ${step} did not start or was killed` : `step ${step} exited ${r.exitCode}`, steps };
  }
  const why = [];
  for (const entry of o.expect) {
    const m = await missing(entry, o.cwd, o.out, startedAt);
    if (m) why.push(m);
  }
  if (why.length) return { status: 'failed', failedStep: null, reason: why.join('; '), steps };
  return { status: 'passed', failedStep: null, reason: '', steps };
}
