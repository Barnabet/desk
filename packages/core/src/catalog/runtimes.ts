import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { chmodSync, existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { GLOBAL_PROJECT_ID, type CatalogEntry, type NodeLockEntry, type RuntimeState, type RuntimesReport, type SkillScope } from '@desk/protocol';
import type { EventStore } from '../events/store';
import type { CatalogRuntimes, SkillRef } from './service';
import { extractSubtree } from './tar';

export type ExecResult = { code: number; stdout: string; stderr: string };
export type Exec = (command: string, args: string[], opts: { env: Record<string, string>; cwd?: string; timeoutMs?: number }) => Promise<ExecResult>;

/** A skill's runtime as tools see it: PATH entries and variables when ready, or why not. */
export type SkillEnv = { state: RuntimeState; reason: string | null; bins: string[]; vars: Record<string, string> };

/** What the agent runtime needs: the environment of a skill, and removal when the skill is deleted. */
export interface SkillEnvProvider {
  env(ref: SkillRef): SkillEnv;
  remove(ref: SkillRef): void;
}

export type SkillRuntimesOptions = {
  dataDir: string;
  store: EventStore;
  /** The uv binary (bundled in the app; DESK_UV or PATH in dev); null when unavailable. */
  uv: string | null;
  /** How to run Node: the daemon's own executable (the app binary with ELECTRON_RUN_AS_NODE=1 when packaged). */
  nodeExec: string;
  /** Whether a skill still exists (orphaned environments are reported and cleaned up). */
  exists: (ref: SkillRef) => boolean;
  registry?: string;
  fetch?: typeof fetch;
  exec?: Exec;
  platform?: NodeJS.Platform;
  arch?: string;
};

type EnvFile = { bins: string[]; vars: Record<string, string>; digest: string };

const NPM_CAPS = { maxDownload: 80 * 1024 * 1024, maxBytes: 120 * 1024 * 1024, maxFiles: 5000, maxFileBytes: 60 * 1024 * 1024 };

/** Node resolve hook: bare imports that fail next to the script are retried from the skill's environment. */
const HOOKS = `import { pathToFileURL } from 'node:url';
const dir = process.env.DESK_NODE_MODULES;
const base = dir ? pathToFileURL(dir.replace(/\\/node_modules\\/?$/, '') + '/').href : null;
export async function resolve(specifier, context, next) {
  try {
    return await next(specifier, context);
  } catch (err) {
    if (!base || err?.code !== 'ERR_MODULE_NOT_FOUND' || /^(\\.|\\/|node:|file:|data:)/.test(specifier)) throw err;
    return next(specifier, { ...context, parentURL: base });
  }
}
`;
const REGISTER = `import { register } from 'node:module';\nregister('./resolve-hooks.mjs', import.meta.url);\n`;

const defaultExec: Exec = (command, args, opts) =>
  new Promise((resolve) => {
    const child = spawn(command, args, { env: opts.env, cwd: opts.cwd ?? '/', stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (d) => (stdout += String(d)));
    child.stderr.on('data', (d) => (stderr += String(d)));
    const timer = opts.timeoutMs ? setTimeout(() => child.kill('SIGKILL'), opts.timeoutMs) : null;
    child.on('error', (err) => resolve({ code: 127, stdout, stderr: `${stderr}${err.message}` }));
    child.on('close', (code) => {
      if (timer) clearTimeout(timer);
      resolve({ code: code ?? 1, stdout, stderr });
    });
  });

const shq = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;

/**
 * Desk-managed runtimes for catalog skills: one environment per installed skill under `<data>/runtimes`, with Python
 * from uv (prebuilt wheels only, exact pins) and Node packages from a lockfile checked against its integrity hashes
 * (install scripts never run). Nothing is installed system-wide; agents only read and execute these directories.
 */
export class SkillRuntimes implements CatalogRuntimes, SkillEnvProvider {
  private readonly root: string;
  private readonly exec: Exec;
  private readonly fetch: typeof fetch;
  private queue: Promise<void> = Promise.resolve();
  private readonly pending = new Map<string, Promise<void>>();

  constructor(private readonly o: SkillRuntimesOptions) {
    this.root = join(o.dataDir, 'runtimes');
    this.exec = o.exec ?? defaultExec;
    this.fetch = o.fetch ?? fetch;
  }

  /** The environment directory of a skill. */
  dir(ref: SkillRef): string {
    return join(this.root, ref.scope, ref.scope === 'project' ? ref.projectId! : '_global', ref.name);
  }

  state(ref: SkillRef): { state: RuntimeState; reason: string | null } {
    const last = this.lastEvent(ref);
    if (!last || last.state === 'removed') return { state: 'none', reason: null };
    if (last.state === 'ready' && !existsSync(join(this.dir(ref), 'desk-env.json'))) {
      return { state: 'failed', reason: 'The environment is missing from disk. Retry to rebuild it.' };
    }
    return { state: last.state, reason: last.reason };
  }

  env(ref: SkillRef): SkillEnv {
    const s = this.state(ref);
    if (s.state !== 'ready') return { ...s, bins: [], vars: {} };
    const file = JSON.parse(readFileSync(join(this.dir(ref), 'desk-env.json'), 'utf8')) as EnvFile;
    return { ...s, bins: file.bins, vars: file.vars };
  }

  /** Builds (or rebuilds) a skill's environment in the background; entries without runtime needs get none. */
  setup(ref: SkillRef, entry: CatalogEntry, updated: string): void {
    const rt = entry.runtime;
    if (!rt.python && !rt.node && !rt.extras?.length) {
      if (this.lastEvent(ref) && this.lastEvent(ref)!.state !== 'removed') this.remove(ref);
      return;
    }
    this.record(ref, 'preparing', null);
    const key = this.key(ref);
    const job = (this.queue = this.queue
      .catch(() => {})
      .then(() => this.build(ref, entry, updated))
      .then(
        () => this.record(ref, 'ready', null),
        (err: unknown) => this.record(ref, 'failed', this.reason(err)),
      ));
    this.pending.set(key, job);
    void job.finally(() => this.pending.get(key) === job && this.pending.delete(key));
  }

  /** Resolves when the current setup of a skill (if any) has finished (tests, CLI). */
  async settled(ref: SkillRef): Promise<void> {
    await this.pending.get(this.key(ref));
  }

  remove(ref: SkillRef): void {
    const dir = this.dir(ref);
    const had = existsSync(dir);
    rmSync(dir, { recursive: true, force: true });
    const last = this.lastEvent(ref);
    if (had || (last && last.state !== 'removed')) this.record(ref, 'removed', null);
  }

  report(): RuntimesReport {
    const envs: RuntimesReport['envs'] = [];
    for (const ref of this.listEnvs()) {
      envs.push({ scope: ref.scope, project_id: ref.projectId ?? null, name: ref.name, bytes: du(this.dir(ref)), orphan: !this.o.exists(ref) });
    }
    return { bytes: existsSync(this.root) ? du(this.root) : 0, envs };
  }

  /** Removes environments whose skill no longer exists. */
  cleanup(): { removed: number; bytes: number } {
    let removed = 0;
    let bytes = 0;
    for (const ref of this.listEnvs()) {
      if (this.o.exists(ref)) continue;
      bytes += du(this.dir(ref));
      this.remove(ref);
      removed++;
    }
    return { removed, bytes };
  }

  // ── building ─────────────────────────────────────────────────────────

  private async build(ref: SkillRef, entry: CatalogEntry, updated: string): Promise<void> {
    const dir = this.dir(ref);
    rmSync(dir, { recursive: true, force: true });
    mkdirSync(join(dir, 'bin'), { recursive: true });
    const env: EnvFile = { bins: [join(dir, 'bin')], vars: { PYTHONDONTWRITEBYTECODE: '1' }, digest: entry.digest };
    const rt = entry.runtime;

    if (rt.python) {
      if (!this.o.uv) throw new Error('uv is not available, so Desk cannot set up Python. Reinstall Desk, or set DESK_UV in development.');
      const uvEnv = this.uvEnv();
      this.progress(ref, `Setting up Python ${rt.python.version}`);
      const py = join(dir, 'py');
      await this.run(this.o.uv, ['venv', '--no-config', '--python', rt.python.version, py], uvEnv, 15 * 60_000);
      if (rt.python.packages.length) {
        this.progress(ref, `Installing ${rt.python.packages.length} Python package${rt.python.packages.length === 1 ? '' : 's'}`);
        await this.run(
          this.o.uv,
          ['pip', 'install', '--no-config', '--python', join(py, 'bin', 'python'), '--only-binary', ':all:', '--exclude-newer', `${updated}T23:59:59Z`, ...rt.python.packages],
          uvEnv,
          15 * 60_000,
        );
      }
      env.bins.push(join(py, 'bin'));
      env.vars.VIRTUAL_ENV = py;
    }

    if (rt.node) {
      await this.installNode(ref, dir, rt.node.lock);
      env.vars.DESK_NODE_MODULES = join(dir, 'node', 'node_modules');
      env.vars.NODE_PATH = join(dir, 'node', 'node_modules');
    }
    // Scripts may call `node` even without packages; the shim runs the daemon's own Node.
    this.writeNodeShim(dir);

    if (rt.extras?.includes('playwright-chromium')) {
      if (!rt.python) throw new Error('playwright-chromium needs the Python runtime with playwright');
      this.progress(ref, 'Downloading Chromium for Playwright');
      const browsers = join(dir, 'browsers');
      await this.run(join(dir, 'py', 'bin', 'python'), ['-m', 'playwright', 'install', 'chromium'], { ...this.baseEnv(), PLAYWRIGHT_BROWSERS_PATH: browsers }, 20 * 60_000);
      env.vars.PLAYWRIGHT_BROWSERS_PATH = browsers;
    }
    writeFileSync(join(dir, 'desk-env.json'), JSON.stringify(env, null, 2));
  }

  private async installNode(ref: SkillRef, dir: string, lock: NodeLockEntry[]): Promise<void> {
    const registry = (this.o.registry ?? 'https://registry.npmjs.org').replace(/\/+$/, '');
    const platform = this.o.platform ?? process.platform;
    const arch = this.o.arch ?? process.arch;
    const wanted = lock.filter((l) => (!l.os || l.os.includes(platform)) && (!l.cpu || l.cpu.includes(arch)));
    let done = 0;
    for (const l of wanted) {
      this.progress(ref, `Installing ${l.name}`, done, wanted.length);
      const base = l.name.startsWith('@') ? l.name.split('/')[1]! : l.name;
      const res = await this.fetch(`${registry}/${l.name}/-/${base}-${l.version}.tgz`);
      if (!res.ok) throw new Error(`Downloading ${l.name}@${l.version} failed (${res.status})`);
      const buf = Buffer.from(await res.arrayBuffer());
      const [alg, expected] = [l.integrity.slice(0, l.integrity.indexOf('-')), l.integrity.slice(l.integrity.indexOf('-') + 1)];
      if (createHash(alg).update(buf).digest('base64') !== expected) throw new Error(`${l.name}@${l.version} does not match its integrity hash`);
      const { files } = await extractSubtree(
        (async function* () {
          yield buf;
        })(),
        '',
        { caps: NPM_CAPS },
      );
      for (const f of files) {
        const target = join(dir, 'node', l.path, f.path);
        mkdirSync(dirname(target), { recursive: true });
        writeFileSync(target, f.content);
        chmodSync(target, f.mode & 0o111 ? 0o755 : 0o644);
      }
      done++;
    }
    // Command-line entry points of top-level packages become wrappers in bin/.
    for (const l of wanted) {
      if (l.path !== `node_modules/${l.name}`) continue;
      const pkgFile = join(dir, 'node', l.path, 'package.json');
      if (!existsSync(pkgFile)) continue;
      const pkg = JSON.parse(readFileSync(pkgFile, 'utf8')) as { name?: string; bin?: string | Record<string, string> };
      const bins = typeof pkg.bin === 'string' ? { [base(pkg.name ?? l.name)]: pkg.bin } : (pkg.bin ?? {});
      for (const [name, rel] of Object.entries(bins)) {
        if (!/^[\w.-]+$/.test(name)) continue;
        const script = join(dir, 'node', l.path, rel);
        writeExecutable(join(dir, 'bin', name), `#!/bin/sh\nexec ${shq(join(dir, 'bin', 'node'))} ${shq(script)} "$@"\n`);
      }
    }
  }

  private writeNodeShim(dir: string): void {
    const lib = join(this.root, 'lib');
    mkdirSync(lib, { recursive: true });
    writeFileSync(join(lib, 'resolve-hooks.mjs'), HOOKS);
    writeFileSync(join(lib, 'resolve-register.mjs'), REGISTER);
    const modules = join(dir, 'node', 'node_modules');
    writeExecutable(
      join(dir, 'bin', 'node'),
      [
        '#!/bin/sh',
        'export ELECTRON_RUN_AS_NODE=1',
        `export DESK_NODE_MODULES=${shq(modules)}`,
        `export NODE_PATH=${shq(modules)}`,
        `exec ${shq(this.o.nodeExec)} --import ${shq(join(lib, 'resolve-register.mjs'))} "$@"`,
        '',
      ].join('\n'),
    );
  }

  private uvEnv(): Record<string, string> {
    return {
      ...this.baseEnv(),
      UV_CACHE_DIR: join(this.root, 'uv', 'cache'),
      UV_PYTHON_INSTALL_DIR: join(this.root, 'uv', 'python'),
      UV_PYTHON_PREFERENCE: 'only-managed',
      UV_NO_CONFIG: '1',
    };
  }

  /** A minimal environment for installers: no inherited secrets. */
  private baseEnv(): Record<string, string> {
    return { PATH: '/usr/bin:/bin:/usr/sbin:/sbin', HOME: process.env.HOME ?? '/tmp', LANG: 'en_US.UTF-8', TMPDIR: process.env.TMPDIR ?? '/tmp' };
  }

  private async run(command: string, args: string[], env: Record<string, string>, timeoutMs: number): Promise<void> {
    const r = await this.exec(command, args, { env, timeoutMs });
    if (r.code !== 0) throw new Error(`${command.split('/').pop()} ${args[0]} failed: ${(r.stderr || r.stdout).trim().split('\n').slice(-6).join('\n')}`);
  }

  // ── state ────────────────────────────────────────────────────────────

  private key(ref: SkillRef): string {
    return `${ref.scope}/${ref.projectId ?? ''}/${ref.name}`;
  }

  private lastEvent(ref: SkillRef): { state: 'preparing' | 'ready' | 'failed' | 'removed'; reason: string | null } | null {
    const events = this.o.store.list({ projectId: ref.scope === 'project' ? ref.projectId! : GLOBAL_PROJECT_ID, types: ['skill.runtime_changed'] });
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i]!;
      if (e.type === 'skill.runtime_changed' && e.payload.scope === ref.scope && e.payload.name === ref.name) return e.payload;
    }
    return null;
  }

  private record(ref: SkillRef, state: 'preparing' | 'ready' | 'failed' | 'removed', reason: string | null): void {
    this.o.store.append({
      project_id: ref.scope === 'project' ? ref.projectId! : GLOBAL_PROJECT_ID,
      agent_id: null,
      type: 'skill.runtime_changed',
      payload: { scope: ref.scope, name: ref.name, state, reason },
    });
  }

  private progress(ref: SkillRef, step: string, done?: number, total?: number): void {
    this.o.store.publishEphemeral({
      type: 'skill.runtime_progress',
      project_id: ref.scope === 'project' ? ref.projectId! : GLOBAL_PROJECT_ID,
      agent_id: null,
      payload: { scope: ref.scope, name: ref.name, step, ...(done !== undefined ? { done } : {}), ...(total !== undefined ? { total } : {}) },
    });
  }

  private reason(err: unknown): string {
    const msg = err instanceof Error ? err.message : String(err);
    return msg.split(this.o.dataDir).join('<data>').slice(0, 800);
  }

  private listEnvs(): SkillRef[] {
    const out: SkillRef[] = [];
    for (const scope of ['global', 'project'] as SkillScope[]) {
      const scopeDir = join(this.root, scope);
      if (!existsSync(scopeDir)) continue;
      for (const owner of readdirSync(scopeDir)) {
        for (const name of readdirSync(join(scopeDir, owner))) {
          out.push({ scope, name, ...(scope === 'project' ? { projectId: owner } : {}) });
        }
      }
    }
    return out;
  }
}

const base = (name: string) => name.split('/').pop()!;

function writeExecutable(path: string, text: string): void {
  writeFileSync(path, text);
  chmodSync(path, 0o755);
}

/** Bytes on disk under a directory (links not followed). */
function du(path: string): number {
  const st = lstatSync(path);
  if (!st.isDirectory()) return st.isFile() ? statSync(path).size : 0;
  let n = 0;
  for (const name of readdirSync(path)) n += du(join(path, name));
  return n;
}
