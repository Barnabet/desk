import { randomBytes } from 'node:crypto';
import { chmodSync, existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  CatalogService,
  createSwitchableAdapter,
  SkillRuntimes,
  EventStore,
  ModelRegistry,
  modelCredentialsFile,
  normalizeBaseURL,
  openDb,
  resolveModelEndpoint,
  Runtime,
  testModelEndpoint,
  type CatalogFetch,
  type Keychain,
  type ModelConfig,
} from '@desk/core';
import type { CatalogFile } from '@desk/protocol';
import { createApp } from './app';
import { loadDaemonFile, saveDaemonFile } from './config-file';
import { startNotifier, type Notification } from './notifier';
import { HttpError } from './http';
import { acquireLock } from './lock';
import { createLogger, type Logger } from './logger';
import { loadModels, saveModels } from './models-file';
import { daemonPaths, type DaemonInfo } from './paths';
import { startServer } from './server';

export const DAEMON_VERSION = '1.0.0';

/** uv for skill runtimes: DESK_UV, then the first uv on PATH; null when there is none. */
function findUv(): string | null {
  if (process.env.DESK_UV) return process.env.DESK_UV;
  for (const dir of (process.env.PATH ?? '').split(':')) {
    if (dir && existsSync(join(dir, 'uv'))) return join(dir, 'uv');
  }
  return null;
}
export const DEFAULT_PORT = 7433;

export type DaemonOptions = {
  dataDir: string;
  port?: number;
  version?: string;
  /** The bundle's build id (see scripts/bundle.mjs); null when running from source. */
  build?: string | null;
  /** Fixed model access (tests); otherwise resolved from env, ~/.config/cliproxyapi.env, then config.json + Keychain. */
  modelConfig?: ModelConfig;
  /** Environment and home used to resolve model access (default process.env / os.homedir()). */
  env?: NodeJS.ProcessEnv;
  home?: string;
  /** Where PUT /config/model-endpoint stores the key; null disables it (default null; main.ts passes the macOS Keychain). */
  keychain?: Keychain | null;
  /** Posts notifications (main.ts passes macNotify on macOS; tests leave it unset). */
  notify?: (n: Notification) => void;
  /** Drizzle migrations folder (the bundle ships its own copy). */
  migrationsDir?: string;
  /** Skill catalog: first-party skills folder, archive host and fetch (tests point these at fixtures). */
  catalog?: { builtinRoot?: string; archiveBase?: string; fetch?: CatalogFetch; file?: CatalogFile };
  /** Skill runtimes: the uv binary (default: DESK_UV, then uv on PATH), the Node used by skill scripts, npm registry. */
  runtimes?: { uv?: string | null; nodeExec?: string; registry?: string };
  sandboxAvailable?: boolean;
  stallIntervalMs?: number;
  now?: () => number;
  log?: Logger;
};

export type RunningDaemon = { port: number; token: string; runtime: Runtime; store: EventStore; stop(): Promise<void> };

export async function startDaemon(o: DaemonOptions): Promise<RunningDaemon> {
  const paths = daemonPaths(o.dataDir);
  const home = o.home ?? homedir();
  const log = o.log ?? createLogger(paths.logFile, false);
  mkdirSync(o.dataDir, { recursive: true });
  const releaseLock = acquireLock(paths.lock);
  try {
    const { db, close } = openDb(paths.db, o.migrationsDir ? { migrationsFolder: o.migrationsDir } : {});
    const store = new EventStore(db);
    const models = new ModelRegistry(loadModels(paths.models, (m) => log.error(m)));
    let file = loadDaemonFile(paths.config);
    const keychain = o.keychain ?? null;
    const resolveEndpoint = () =>
      o.modelConfig
        ? { config: o.modelConfig, source: 'env' as const }
        : resolveModelEndpoint({ env: o.env ?? process.env, home, baseUrl: file.base_url, keychain });
    let endpointState = resolveEndpoint();
    if (!endpointState.config) log.info('no model endpoint configured yet; agents stay paused until one is set');
    const adapter = createSwitchableAdapter(endpointState.config, models);
    let runtimeRef: Runtime | null = null;
    const builtinRoot = o.catalog?.builtinRoot ?? fileURLToPath(new URL('../../../catalog/skills', import.meta.url));
    const skillRuntimes = new SkillRuntimes({
      dataDir: o.dataDir,
      store,
      uv: o.runtimes?.uv !== undefined ? o.runtimes.uv : findUv(),
      nodeExec: o.runtimes?.nodeExec ?? process.execPath,
      exists: (ref) => (ref.scope === 'builtin' ? !!runtimeRef?.builtins?.entry(ref.name) : !!runtimeRef?.skills.get(ref.scope, ref.name, ref.projectId)),
      ...(o.runtimes?.registry ? { registry: o.runtimes.registry } : {}),
    });
    const runtime = new Runtime({
      skillEnv: skillRuntimes,
      builtins: { root: builtinRoot },
      store,
      adapter,
      models,
      dataDir: o.dataDir,
      // Off limits to agents: the token file, every project's history, and the model credentials file; and read-only,
      // the git and ssh config that deskd's own git runs with.
      secrets: [paths.daemonJson, ...['', '-wal', '-shm', '-journal'].map((x) => paths.db + x), modelCredentialsFile(home)],
      // Desk's built-in skills are read-only too (in development they live in the repo's catalog/skills).
      readOnly: [join(home, '.gitconfig'), join(process.env.XDG_CONFIG_HOME ?? join(home, '.config'), 'git'), join(home, '.ssh'), builtinRoot],
      home,
      ...(o.sandboxAvailable !== undefined ? { sandboxAvailable: o.sandboxAvailable } : {}),
      onError: (err, ctx) => log.error(`runtime error (${ctx})`, err),
    });
    runtimeRef = runtime;
    const interrupted = skillRuntimes.recoverInterrupted();
    if (interrupted) log.info(`${interrupted} skill environment setup(s) were interrupted; they will be set up again when needed`);
    const adopted = runtime.adoptBuiltins();
    if (adopted.length) log.info(`now built into Desk, removed catalog copies: ${adopted.join(', ')}`);
    const catalog = new CatalogService({
      runtime,
      runtimes: skillRuntimes,
      store,
      dataDir: o.dataDir,
      builtinRoot,
      ...(o.catalog?.archiveBase ? { archiveBase: o.catalog.archiveBase } : {}),
      ...(o.catalog?.fetch ? { fetch: o.catalog.fetch } : {}),
      ...(o.catalog?.file ? { catalog: o.catalog.file } : {}),
    });
    const token = randomBytes(32).toString('hex');
    const version = o.version ?? DAEMON_VERSION;
    const startedAt = Date.now();
    const endpointStatus = () => ({ configured: endpointState.config !== null, source: endpointState.source, base_url: endpointState.config?.baseURL ?? null });
    const app = createApp({
      runtime,
      store,
      catalog,
      skillRuntimes,
      models,
      token,
      version,
      build: o.build ?? null,
      saveModels: (m) => saveModels(paths.models, m),
      health: () => ({ proxy: runtime.proxyState, uptime_s: Math.floor((Date.now() - startedAt) / 1000) }),
      config: {
        get: () => ({ notifications: file.notifications }),
        patch: (p) => {
          file = { ...file, ...(p.notifications ? { notifications: p.notifications } : {}) };
          saveDaemonFile(paths.config, file);
          return { notifications: file.notifications };
        },
      },
      endpoint: {
        status: endpointStatus,
        save: ({ base_url, api_key }) => {
          if (!keychain) throw new HttpError(501, 'unsupported', 'Storing the key needs the macOS Keychain; use ~/.config/cliproxyapi.env instead');
          keychain.set(api_key);
          file = { ...file, base_url };
          saveDaemonFile(paths.config, file);
          endpointState = resolveEndpoint();
          adapter.replace(endpointState.config);
          log.info(`model endpoint updated (source: ${endpointState.source ?? 'none'})`);
          return endpointStatus();
        },
        test: async (req) => {
          const cfg = req ? { baseURL: normalizeBaseURL(req.base_url), apiKey: req.api_key } : endpointState.config;
          return cfg ? testModelEndpoint(cfg) : { ok: false, error: 'No model endpoint is configured' };
        },
      },
    });
    const server = await startServer({ app, store, token, port: o.port ?? DEFAULT_PORT });
    runtime.guardPort(server.port);

    const stopNotifier = o.notify
      ? startNotifier({
          store,
          enabled: () => file.notifications === 'auto',
          suppressed: () => server.notifyingClients() > 0,
          post: o.notify,
          onError: (err) => log.error('notifier failed', err),
        })
      : () => {};

    const info: DaemonInfo = { port: server.port, token, pid: process.pid, version, started_at: new Date().toISOString() };
    writeFileSync(paths.daemonJson, JSON.stringify(info, null, 2), { mode: 0o600 });
    chmodSync(paths.daemonJson, 0o600);

    const resumed = runtime.recover();
    log.info(`deskd ${version} listening on 127.0.0.1:${server.port} (data: ${o.dataDir}); resumed ${resumed.length} agent(s)`);

    const now = o.now ?? Date.now;
    const stallTimer = setInterval(() => {
      try {
        const stalled = runtime.checkStalls(now());
        if (stalled.length) log.info(`reported stalled threads: ${stalled.join(', ')}`);
      } catch (err) {
        log.error('stall check failed', err);
      }
    }, o.stallIntervalMs ?? 60_000);
    stallTimer.unref();

    let stopped = false;
    return {
      port: server.port,
      token,
      runtime,
      store,
      async stop() {
        if (stopped) return;
        stopped = true;
        stopNotifier();
        clearInterval(stallTimer);
        await server.close();
        await runtime.shutdown();
        close();
        rmSync(paths.daemonJson, { force: true });
        releaseLock();
        log.info('deskd stopped');
      },
    };
  } catch (err) {
    releaseLock();
    throw err;
  }
}
