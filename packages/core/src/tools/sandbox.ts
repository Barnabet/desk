import { readdirSync, readFileSync, realpathSync } from 'node:fs';
import { basename, dirname, join, sep } from 'node:path';
import { runProcess } from './process';

/**
 * What agents may never touch, whatever their roots; the sandbox profile and the file tools both apply it. Agents may not
 * write in Desk's data dir outside their own writable roots in it (thread workspaces), read or replace the files holding
 * secrets (deskd's token file, its database, model credentials, and the files `secretPatterns` cover, such as desk web's
 * one-time login files), write `readOnly` paths (the git and ssh config deskd's git runs with), or connect to deskd's port
 * or to a port that one of `portFiles` names (desk web's `web.json`, read each time a profile is built, since desk web
 * starts, stops and changes port on its own). Paths are real paths.
 */
export type SandboxGuard = { dataDir: string; secrets: string[]; secretPatterns: SecretPattern[]; readOnly: string[]; ports: number[]; portFiles: string[] };

/** Secret files whose names are not known in advance: every file directly in `dir` named `<prefix>…<suffix>`. */
export type SecretPattern = { dir: string; prefix: string; suffix: string };

/**
 * How shell commands are confined. `writable` lists extra writable roots besides temp dirs; `guard` what stays off
 * limits; `gitDirs` the git dirs of repos deskd runs git in (a thread's common dir and its worktree's `.git` link,
 * writable git sources' `.git`), whose control files stay read-only.
 */
export type SandboxSpec = { enabled: boolean; writable: string[]; guard?: SandboxGuard; gitDirs?: string[] };

export const NO_SANDBOX: SandboxSpec = { enabled: false, writable: [] };

const SANDBOX_EXEC = '/usr/bin/sandbox-exec';

const escapePath = (p: string) => p.replace(/\\/g, '\\\\').replace(/"/g, '\\"');

/** The real path of `p`, or of its parent joined with its name when `p` does not exist yet (e.g. `desk.db-wal`). */
export function realOrSelf(p: string): string {
  try {
    return realpathSync(p);
  } catch {
    try {
      return join(realpathSync(dirname(p)), basename(p));
    } catch {
      return p;
    }
  }
}

const escapeRegex = (p: string) => p.replace(/[.[\]()*+?{}|^$\\]/g, '\\$&');

/** True when `p` is `root` or lies inside it. */
export function isWithin(p: string, root: string): boolean {
  return p === root || p.startsWith(root.endsWith(sep) ? root : root + sep);
}

/** A guard for `dataDir`, with the secrets' real paths. deskd adds its port once it listens. */
export function sandboxGuard(o: { dataDir: string; secrets: string[]; secretPatterns?: SecretPattern[]; readOnly?: string[]; ports?: number[]; portFiles?: string[] }): SandboxGuard {
  const real = (paths: string[] = []) => [...new Set(paths.map(realOrSelf))];
  return {
    dataDir: realOrSelf(o.dataDir),
    secrets: real(o.secrets),
    secretPatterns: (o.secretPatterns ?? []).map((p) => ({ ...p, dir: realOrSelf(p.dir) })),
    readOnly: real(o.readOnly),
    ports: [...(o.ports ?? [])],
    portFiles: real(o.portFiles),
  };
}

/** Whether `real` (a real path) is a file one of `patterns` covers. */
export function matchesSecretPattern(real: string, patterns: readonly SecretPattern[]): boolean {
  const name = basename(real);
  return patterns.some((p) => dirname(real) === p.dir && name.length >= p.prefix.length + p.suffix.length && name.startsWith(p.prefix) && name.endsWith(p.suffix));
}

/** The guard's secret files as they are now: the named ones, and the existing files its patterns cover. */
export function secretFiles(g: SandboxGuard): string[] {
  const covered = g.secretPatterns.flatMap((p) => {
    try {
      return readdirSync(p.dir)
        .map((name) => join(p.dir, name))
        .filter((f) => matchesSecretPattern(f, [p]));
    } catch {
      return [];
    }
  });
  return [...g.secrets, ...covered];
}

/** The ports agents may not connect to now: the guard's own, and the `port` each port file names (a missing or unreadable file names none). */
export function guardPorts(g: SandboxGuard): number[] {
  const named = g.portFiles.flatMap((file) => {
    try {
      const port: unknown = (JSON.parse(readFileSync(file, 'utf8')) as { port?: unknown } | null)?.port;
      return typeof port === 'number' && Number.isInteger(port) && port > 0 && port < 65_536 ? [port] : [];
    } catch {
      return [];
    }
  });
  return [...new Set([...g.ports, ...named])];
}

function ancestors(p: string): string[] {
  const out: string[] = [];
  for (let d = dirname(p); d !== dirname(d); d = dirname(d)) out.push(d);
  return out;
}

/** Guard rules come after the writable roots: in SBPL the last matching rule wins. */
function guardRules(writable: string[], g: SandboxGuard, gitDirs: string[]): string[] {
  const subpath = (p: string) => `  (subpath "${escapePath(p)}")`;
  const literal = (p: string) => `  (literal "${escapePath(p)}")`;
  // Desk's data dir stays read-only even when a writable root contains it; roots inside it (the workspace) stay writable.
  const inData = writable.filter((w) => w !== g.dataDir && isWithin(w, g.dataDir));
  const rules = ['(deny file-write*', subpath(g.dataDir), ')'];
  if (inData.length) rules.push('(allow file-write*', ...inData.map(subpath), ')');
  const patterns = g.secretPatterns.map((p) => `  (regex #"^${escapeRegex(p.dir)}/${escapeRegex(p.prefix)}[^/]*${escapeRegex(p.suffix)}$")`);
  if (g.secrets.length || patterns.length) {
    // No moving or replacing a secret or any folder above it either: a moved folder would leave these path rules behind.
    const nodes = [...new Set([...g.secrets.flatMap((s) => [s, ...ancestors(s)]), ...g.secretPatterns.flatMap((p) => [p.dir, ...ancestors(p.dir)])])];
    rules.push('(deny file-write*', ...nodes.map(literal), ...patterns, ')', '(deny file-read*', ...g.secrets.map(literal), ...patterns, ')');
  }
  if (g.readOnly.length) rules.push('(deny file-write*', ...g.readOnly.map(subpath), ')');
  // deskd runs git outside the sandbox in these repos, so nothing that makes git run commands (hooks, and the config
  // naming fsmonitor, credential helpers, filters and drivers) may change, nor the links that say which git dir is used.
  for (const d of gitDirs) {
    const rest = `^${escapeRegex(d)}/(worktrees/[^/]+/(commondir|gitdir|config\\.worktree)|modules/.+/(hooks(/.*)?|config))$`;
    rules.push('(deny file-write*', literal(d), subpath(join(d, 'hooks')), literal(join(d, 'config')), `  (regex #"${rest}")`, ')');
  }
  // The Keychain CLI reads the items it created, like a stored model key, without asking.
  rules.push('(deny process-exec (literal "/usr/bin/security"))');
  // `localhost` covers 127.0.0.1 and ::1 but not the rest of 127/8 or the LAN address: this holds because deskd (and
  // desk web) listen only on 127.0.0.1.
  rules.push(...guardPorts(g).map((p) => `(deny network-outbound (remote ip "localhost:${p}"))`));
  return rules;
}

/** SBPL profile: reads and network allowed; writes only to temp dirs, devices and `writable`; then the guard's exceptions. */
export function buildSandboxProfile(writable: string[], guard?: SandboxGuard, gitDirs: string[] = []): string {
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
    ...(guard ? guardRules(writable, guard, gitDirs) : []),
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

const profileOf = (sandbox: SandboxSpec) => buildSandboxProfile(sandbox.writable.map(realOrSelf), sandbox.guard, (sandbox.gitDirs ?? []).map(realOrSelf));

export function shellInvocation(command: string, sandbox: SandboxSpec): { command: string; args: string[] } {
  const shell = ['-f', '-c', command];
  if (!sandbox.enabled) return { command: '/bin/zsh', args: shell };
  return { command: SANDBOX_EXEC, args: ['-p', profileOf(sandbox), '/bin/zsh', ...shell] };
}

/** Runs a program directly (no shell) under the sandbox when it is enabled, e.g. `rg` for the grep tool. */
export function commandInvocation(command: string, args: string[], sandbox: SandboxSpec): { command: string; args: string[] } {
  if (!sandbox.enabled) return { command, args };
  return { command: SANDBOX_EXEC, args: ['-p', profileOf(sandbox), command, ...args] };
}
