import { spawn } from 'node:child_process';

export type ProcessResult = { exitCode: number | null; output: string; timedOut: boolean; aborted: boolean };

export type ProcessOptions = {
  command: string;
  args: string[];
  cwd: string;
  env?: NodeJS.ProcessEnv;
  timeoutMs?: number;
  signal?: AbortSignal;
  maxOutputChars?: number;
  /** Written to the process's stdin (closed afterwards); stdin is empty otherwise. */
  stdin?: string;
};

/** Runs a process in its own process group; stdout and stderr are combined. Rejects only on spawn failure. */
export function runProcess(opts: ProcessOptions): Promise<ProcessResult> {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(opts.command, opts.args, {
      cwd: opts.cwd,
      env: opts.env ?? process.env,
      detached: true,
      stdio: [opts.stdin === undefined ? 'ignore' : 'pipe', 'pipe', 'pipe'],
    });
    if (opts.stdin !== undefined && child.stdin) {
      child.stdin.on('error', () => {}); // the process may exit without reading its input
      child.stdin.end(opts.stdin);
    }
    const max = opts.maxOutputChars ?? 1_000_000;
    let output = '';
    let timedOut = false;
    let aborted = false;

    const onData = (buf: Buffer) => {
      if (output.length < max) output += buf.toString('utf8');
    };
    child.stdout!.on('data', onData);
    child.stderr!.on('data', onData);

    const killGroup = () => {
      const pid = child.pid;
      if (pid === undefined) return;
      try {
        process.kill(-pid, 'SIGTERM');
      } catch {}
      setTimeout(() => {
        try {
          process.kill(-pid, 'SIGKILL');
        } catch {}
      }, 5000).unref();
    };

    const timer = opts.timeoutMs
      ? setTimeout(() => {
          timedOut = true;
          killGroup();
        }, opts.timeoutMs)
      : undefined;
    const onAbort = () => {
      aborted = true;
      killGroup();
    };
    opts.signal?.addEventListener('abort', onAbort, { once: true });
    if (opts.signal?.aborted) onAbort();

    const cleanup = () => {
      if (timer) clearTimeout(timer);
      opts.signal?.removeEventListener('abort', onAbort);
    };
    child.on('error', (err) => {
      cleanup();
      reject(err);
    });
    child.on('close', (code) => {
      cleanup();
      resolvePromise({ exitCode: code, output: output.slice(0, max), timedOut, aborted });
    });
  });
}
