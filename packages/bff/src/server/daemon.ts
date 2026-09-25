import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { readDaemonInfo } from '@desk/client/node';
import type { HealthResponse } from '@desk/protocol';
import type { DaemonMode, DaemonStatus } from '../contract';
import { UserFacingError } from './errors';
import { LAUNCHD_LABEL, launchdPlist, plistPath } from './launchd';

export type ExecResult = { code: number; stdout: string; stderr: string };

export type DaemonManagerOptions = {
  dataDir: string;
  mode: DaemonMode;
  platform: NodeJS.Platform;
  home: string;
  uid: number;
  bundledVersion: string;
  bundledBuild: string | null;
  /** Packaged: the app's own binary (run with ELECTRON_RUN_AS_NODE) and the bundled deskd.mjs. */
  execPath: string;
  bundlePath: string;
  /** Dev: the repo root and a Node binary; the TypeScript daemon runs through tsx. */
  repoRoot: string;
  nodePath: string;
  exec(file: string, args: string[]): Promise<ExecResult>;
  spawnDetached(file: string, args: string[], opts: { cwd: string; env: Record<string, string>; logFile: string }): void;
  kill(pid: number): void;
  fetchHealth?(port: number): Promise<HealthResponse>;
  sleep?(ms: number): Promise<void>;
  startTimeoutMs?: number;
};

export function compareVersions(a: string, b: string): number {
  const pa = a.split('.').map((n) => Number.parseInt(n, 10) || 0);
  const pb = b.split('.').map((n) => Number.parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d) return d;
  }
  return 0;
}

/**
 * Whether a running daemon is older than the bundled one: a lower version, or the same version from another build
 * (an app update that kept the version number). A daemon run from source (build null) or a newer one is left alone.
 */
export function isOutdated(health: Pick<HealthResponse, 'version' | 'build'>, bundled: { version: string; build: string | null }): boolean {
  const cmp = compareVersions(health.version, bundled.version);
  if (cmp !== 0) return cmp < 0;
  return !!bundled.build && health.build !== null && health.build !== bundled.build;
}

async function defaultFetchHealth(port: number): Promise<HealthResponse> {
  const res = await fetch(`http://127.0.0.1:${port}/v1/health`, { signal: AbortSignal.timeout(1500) });
  if (!res.ok) throw new Error(`health ${res.status}`);
  return (await res.json()) as HealthResponse;
}

/** Finds, starts, restarts and stops deskd. Quitting the app never stops it. */
export class DaemonManager {
  constructor(private readonly o: DaemonManagerOptions) {}

  private get launchd(): boolean {
    return this.o.mode === 'packaged' && this.o.platform === 'darwin';
  }

  private logsDir(): string {
    return join(this.o.dataDir, 'logs');
  }

  private sleep(ms: number): Promise<void> {
    return this.o.sleep ? this.o.sleep(ms) : new Promise((r) => setTimeout(r, ms));
  }

  private async probe(): Promise<{ health: HealthResponse; pid: number } | null> {
    const info = readDaemonInfo(this.o.dataDir);
    if (!info) return null;
    try {
      return { health: await (this.o.fetchHealth ?? defaultFetchHealth)(info.port), pid: info.pid };
    } catch {
      return null;
    }
  }

  async status(): Promise<DaemonStatus> {
    const p = await this.probe();
    return {
      running: !!p,
      version: p?.health.version ?? null,
      pid: p?.pid ?? null,
      uptime_s: p?.health.uptime_s ?? null,
      proxy: p?.health.proxy ?? null,
      mode: this.o.mode,
      bundledVersion: this.o.bundledVersion,
      build: p ? p.health.build : null,
      bundledBuild: this.o.bundledBuild,
      agent: this.launchd ? (existsSync(plistPath(this.o.home)) ? 'installed' : 'missing') : 'unsupported',
    };
  }

  /** The LaunchAgent that runs the bundled daemon with the app's own binary. */
  plist(): string {
    return launchdPlist({
      programArguments: [this.o.execPath, this.o.bundlePath, '--data-dir', this.o.dataDir],
      env: { ELECTRON_RUN_AS_NODE: '1', DESK_BUNDLED: '1' },
      workingDirectory: dirname(this.o.bundlePath),
      logFile: join(this.logsDir(), 'deskd.launchd.log'),
    });
  }

  async start(): Promise<DaemonStatus> {
    const current = await this.status();
    if (current.running) return current;
    mkdirSync(this.logsDir(), { recursive: true });
    if (this.launchd) await this.installAgent();
    else if (this.o.mode === 'dev') this.spawnDev();
    else throw new UserFacingError('unsupported', 'Starting Desk automatically is not supported on this platform yet. Start deskd yourself, then retry.');
    return this.waitHealthy(null);
  }

  async restart(): Promise<DaemonStatus> {
    if (this.launchd) {
      if (!existsSync(plistPath(this.o.home))) return this.start();
      const before = await this.status();
      await this.launchctl(['kickstart', '-k', `${this.domain()}/${LAUNCHD_LABEL}`]);
      return this.waitHealthy(before.pid);
    }
    await this.stop();
    return this.start();
  }

  async stop(): Promise<DaemonStatus> {
    if (this.launchd && existsSync(plistPath(this.o.home))) {
      await this.launchctl(['bootout', `${this.domain()}/${LAUNCHD_LABEL}`], true);
    } else {
      const info = readDaemonInfo(this.o.dataDir);
      if (info) {
        try {
          this.o.kill(info.pid);
        } catch {
          // Already gone.
        }
      }
    }
    for (let i = 0; i < 40 && readDaemonInfo(this.o.dataDir); i++) await this.sleep(250);
    return this.status();
  }

  /** Rewrites and reloads the LaunchAgent (System → Repair). Where there is no LaunchAgent, a restart. */
  async repair(): Promise<DaemonStatus> {
    if (!this.launchd) return this.restart();
    const before = await this.status();
    await this.installAgent();
    return this.waitHealthy(before.running ? before.pid : null);
  }

  /** Packaged builds: when the installed agent runs an older daemon (or another build) than the bundled one, reinstall it. */
  async ensureCurrent(): Promise<boolean> {
    if (!this.launchd || !existsSync(plistPath(this.o.home))) return false;
    const p = await this.probe();
    const bundled = { version: this.o.bundledVersion, build: this.o.bundledBuild };
    if (!p || !isOutdated(p.health, bundled)) return false;
    await this.installAgent();
    await this.waitHealthy(null, (h) => !isOutdated(h, bundled));
    return true;
  }

  async installAgent(): Promise<void> {
    const file = plistPath(this.o.home);
    mkdirSync(dirname(file), { recursive: true });
    mkdirSync(this.logsDir(), { recursive: true });
    if (existsSync(file)) await this.launchctl(['bootout', `${this.domain()}/${LAUNCHD_LABEL}`], true);
    writeFileSync(file, this.plist());
    // launchd can refuse a bootstrap right after a bootout while the old job is still tearing down.
    for (let attempt = 1; ; attempt++) {
      try {
        await this.launchctl(['bootstrap', this.domain(), file]);
        return;
      } catch (err) {
        if (attempt >= 3) throw err;
        await this.sleep(500);
      }
    }
  }

  private spawnDev(): void {
    const loader = join(this.o.repoRoot, 'node_modules', 'tsx', 'dist', 'loader.mjs');
    const entry = join(this.o.repoRoot, 'apps', 'daemon', 'src', 'main.ts');
    this.o.spawnDetached(this.o.nodePath, ['--import', loader, entry, '--data-dir', this.o.dataDir], {
      cwd: this.o.repoRoot,
      env: {},
      logFile: join(this.logsDir(), 'deskd.out.log'),
    });
  }

  private domain(): string {
    return `gui/${this.o.uid}`;
  }

  private async launchctl(args: string[], ignoreFailure = false): Promise<void> {
    const r = await this.o.exec('launchctl', args);
    if (r.code !== 0 && !ignoreFailure) throw new UserFacingError('launchd_failed', `launchctl ${args[0]} failed: ${r.stderr.trim() || `exit ${r.code}`}`);
  }

  /** Waits for a healthy daemon (a new pid when restarting, the bundled build when refreshing). */
  private async waitHealthy(previousPid: number | null, current: (h: HealthResponse) => boolean = () => true): Promise<DaemonStatus> {
    const deadline = Date.now() + (this.o.startTimeoutMs ?? 10_000);
    for (;;) {
      const p = await this.probe();
      if (p && p.pid !== previousPid && current(p.health)) return this.status();
      if (Date.now() > deadline) throw new UserFacingError('daemon_start_timeout', 'Desk did not start within 10 seconds. Check the logs, then retry.');
      await this.sleep(250);
    }
  }
}
