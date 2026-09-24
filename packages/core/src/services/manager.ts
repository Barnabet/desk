import { spawn, execFileSync, type ChildProcess } from 'node:child_process';
import { appendFileSync, existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { shellInvocation, type SandboxSpec } from '../tools/sandbox';

/** Logs are cut to their last KEEP bytes once they pass MAX. */
export const SERVICE_LOG_MAX_BYTES = 2_000_000;
const SERVICE_LOG_KEEP_BYTES = 1_000_000;
const KILL_GRACE_MS = 5000;

const ANSI = /\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g;
export const stripAnsi = (s: string) => s.replace(ANSI, '');

const LOOPBACK_URL = /\bhttps?:\/\/(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d{1,5})?(?:\/[^\s'"<>`)\]]*)?/i;

/** The first loopback URL in a chunk of (ANSI-stripped) output, with 0.0.0.0 shown as localhost; null if none. */
export function detectLoopbackUrl(text: string): string | null {
  const m = LOOPBACK_URL.exec(stripAnsi(text));
  if (!m) return null;
  const url = m[0].replace(/[.,;:]+$/, '').replace('0.0.0.0', 'localhost');
  try {
    const u = new URL(url);
    return u.pathname === '/' && !u.search ? u.origin : u.toString();
  } catch {
    return null;
  }
}

/** The last `lines` lines of a log file, ANSI stripped. */
export function tailLog(file: string, lines: number): { text: string; truncated: boolean } {
  if (!existsSync(file)) return { text: '', truncated: false };
  const all = stripAnsi(readFileSync(file, 'utf8')).replace(/\r(?!\n)/g, '\n').split('\n');
  if (all.at(-1) === '') all.pop();
  const truncated = all.length > lines;
  return { text: all.slice(-lines).join('\n'), truncated };
}

type Run = {
  child: ChildProcess;
  logFile: string;
  logBytes: number;
  urlFound: boolean;
  urlCarry: string;
  stopping: boolean;
  closed: Promise<void>;
};

export type ServiceProcessHooks = {
  /** The first loopback URL of a run. */
  onUrl(serviceId: string, url: string): void;
  /** The run ended on its own (not after `stop`). */
  onExit(serviceId: string, code: number | null, signal: string | null): void;
};

/** Long-lived service processes: one run per service id, output appended to a capped log file. */
export class ServiceProcesses {
  private readonly runs = new Map<string, Run>();

  constructor(private readonly hooks: ServiceProcessHooks) {}

  isRunning(serviceId: string): boolean {
    return this.runs.has(serviceId);
  }

  /** Starts a run (the caller stops any previous one first). Returns the pid, or null if the spawn failed at once. */
  start(serviceId: string, o: { command: string; cwd: string; env: NodeJS.ProcessEnv; sandbox: SandboxSpec; logFile: string; header: string }): number | null {
    if (this.runs.has(serviceId)) throw new Error(`Service ${serviceId} is already running`);
    mkdirSync(dirname(o.logFile), { recursive: true });
    appendFileSync(o.logFile, `${o.header}\n`);
    const inv = shellInvocation(o.command, o.sandbox);
    const child = spawn(inv.command, inv.args, { cwd: o.cwd, env: o.env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let closedResolve!: () => void;
    const run: Run = {
      child,
      logFile: o.logFile,
      logBytes: existsSync(o.logFile) ? statSync(o.logFile).size : 0,
      urlFound: false,
      urlCarry: '',
      stopping: false,
      closed: new Promise((r) => (closedResolve = r)),
    };
    this.runs.set(serviceId, run);
    const onData = (buf: Buffer) => this.output(serviceId, run, buf.toString('utf8'));
    child.stdout?.on('data', onData);
    child.stderr?.on('data', onData);
    child.on('error', (err) => this.write(run, `\n[failed to start: ${err.message}]\n`));
    child.on('close', (code, signal) => {
      if (this.runs.get(serviceId) === run) this.runs.delete(serviceId);
      this.write(run, `── ${run.stopping ? 'stopped' : `exited (${signal ?? code})`} ${new Date().toISOString()} ──\n`);
      closedResolve();
      if (!run.stopping) this.hooks.onExit(serviceId, code, signal);
    });
    return child.pid ?? null;
  }

  /** Stops a run: SIGTERM to its process group, SIGKILL after a grace period. Resolves once it has closed. */
  stop(serviceId: string, graceMs = KILL_GRACE_MS): Promise<void> {
    const run = this.runs.get(serviceId);
    if (!run) return Promise.resolve();
    run.stopping = true;
    this.runs.delete(serviceId);
    killGroup(run.child.pid, 'SIGTERM');
    const timer = setTimeout(() => killGroup(run.child.pid, 'SIGKILL'), graceMs);
    timer.unref();
    return run.closed.finally(() => clearTimeout(timer));
  }

  /** Stops every run; resolves when all have closed (SIGKILL after `graceMs`). */
  async stopAll(graceMs = 3000): Promise<string[]> {
    const ids = [...this.runs.keys()];
    await Promise.all(ids.map((id) => this.stop(id, graceMs)));
    return ids;
  }

  private output(serviceId: string, run: Run, text: string): void {
    this.write(run, text);
    if (run.urlFound) return;
    const window = run.urlCarry + text;
    const url = detectLoopbackUrl(window);
    if (url) {
      run.urlFound = true;
      this.hooks.onUrl(serviceId, url);
    } else {
      run.urlCarry = window.slice(-200);
    }
  }

  private write(run: Run, text: string): void {
    appendFileSync(run.logFile, text);
    run.logBytes += Buffer.byteLength(text);
    if (run.logBytes > SERVICE_LOG_MAX_BYTES) {
      const buf = readFileSync(run.logFile);
      const kept = buf.subarray(buf.length - SERVICE_LOG_KEEP_BYTES);
      writeFileSync(run.logFile, Buffer.concat([Buffer.from('[earlier output removed]\n'), kept]));
      run.logBytes = SERVICE_LOG_KEEP_BYTES + 24;
    }
  }
}

function killGroup(pid: number | undefined, signal: NodeJS.Signals): void {
  if (pid === undefined) return;
  try {
    process.kill(-pid, signal);
  } catch {}
}

/**
 * After an unclean daemon exit: kills a service's process group if its recorded pid is still alive and still runs the
 * recorded command (so a reused pid is never touched). Best effort; returns whether a kill was sent.
 */
export function reapOrphan(pid: number | null, command: string): boolean {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
  } catch {
    return false;
  }
  let cmdline = '';
  try {
    cmdline = execFileSync('/bin/ps', ['-o', 'command=', '-p', String(pid)], { encoding: 'utf8', timeout: 2000 });
  } catch {
    return false;
  }
  if (!sameCommand(cmdline, command)) return false;
  killGroup(pid, 'SIGKILL');
  return true;
}

/**
 * Whether a `ps` command line runs `command`. The shell may have exec'd it (quotes gone) and ps escapes non-ASCII, so
 * compare the command's first distinctive words rather than the exact string.
 */
export function sameCommand(cmdline: string, command: string): boolean {
  const words = [...new Set(command.match(/[A-Za-z0-9._/:=@+-]{3,}/g) ?? [])].slice(0, 6);
  return words.length > 0 && words.every((w) => cmdline.includes(w));
}
