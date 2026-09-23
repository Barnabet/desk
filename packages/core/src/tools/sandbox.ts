import { realpathSync } from 'node:fs';
import { runProcess } from './process';

/** How shell commands are confined. `writable` lists extra writable roots besides temp dirs. */
export type SandboxSpec = { enabled: boolean; writable: string[] };

export const NO_SANDBOX: SandboxSpec = { enabled: false, writable: [] };

const SANDBOX_EXEC = '/usr/bin/sandbox-exec';

const escapePath = (p: string) => p.replace(/\\/g, '\\\\').replace(/"/g, '\\"');

function realOrSelf(p: string): string {
  try {
    return realpathSync(p);
  } catch {
    return p;
  }
}

/** SBPL profile: reads and network allowed; writes only to temp dirs, devices and `writable`. */
export function buildSandboxProfile(writable: string[]): string {
  return [
    '(version 1)',
    '(allow default)',
    '(deny file-write*)',
    '(allow file-write*',
    '  (subpath "/private/tmp")',
    '  (subpath "/private/var/folders")',
    '  (literal "/dev/null") (literal "/dev/zero") (literal "/dev/dtracehelper")',
    '  (regex #"^/dev/tty") (regex #"^/dev/fd/")',
    ...writable.map((p) => `  (subpath "${escapePath(p)}")`),
    ')',
  ].join('\n');
}

let detected: Promise<boolean> | undefined;

/** True when `sandbox-exec` works on this machine. Cached for the process lifetime. */
export function detectSandbox(): Promise<boolean> {
  detected ??= runProcess({ command: SANDBOX_EXEC, args: ['-p', '(version 1)(allow default)', '/usr/bin/true'], cwd: '/', timeoutMs: 5000 }).then(
    (r) => r.exitCode === 0,
    () => false,
  );
  return detected;
}

export function shellInvocation(command: string, sandbox: SandboxSpec): { command: string; args: string[] } {
  const shell = ['-f', '-c', command];
  if (!sandbox.enabled) return { command: '/bin/zsh', args: shell };
  return { command: SANDBOX_EXEC, args: ['-p', buildSandboxProfile(sandbox.writable.map(realOrSelf)), '/bin/zsh', ...shell] };
}
