import { randomBytes } from 'node:crypto';
import { chmodSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import {
  createSwitchableAdapter,
  EventStore,
  ModelRegistry,
  normalizeBaseURL,
  openDb,
  resolveModelEndpoint,
  Runtime,
  testModelEndpoint,
  type Keychain,
  type ModelConfig,
} from '@desk/core';
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
export const DEFAULT_PORT = 7433;

export type DaemonOptions = {
  dataDir: string;
  port?: number;
  version?: string;
  /** Fixed model access (tests); otherwise resolved from env, ~/.config/cliproxyapi.env, then config.json + Keychain. */
  modelConfig?: ModelConfig;
  /** Environment and home used to resolve model access (default process.env / os.homedir()). */
  env?: NodeJS.ProcessEnv;
  home?: string;
  /** Where PUT /config/model-endpoint stores the key; null disables it (default null; main.ts passes the macOS Keychain). */
  keychain?: Keychain | null;
  /** Posts notifications (main.ts passes macNotify on macOS; tests leave it unset). */
  notify?: (n: Notification) => void;
  sandboxAvailable?: boolean;
  stallIntervalMs?: number;
  now?: () => number;
  log?: Logger;
};

export type RunningDaemon = { port: number; token: string; runtime: Runtime; store: EventStore; stop(): Promise<void> };

export async function startDaemon(o: DaemonOptions): Promise<RunningDaemon> {
  const paths = daemonPaths(o.dataDir);
  const log = o.log ?? createLogger(paths.logFile, false);
  mkdirSync(o.dataDir, { recursive: true });
  const releaseLock = acquireLock(paths.lock);
  try {
    const { db, close } = openDb(paths.db);
    const store = new EventStore(db);
    const models = new ModelRegistry(loadModels(paths.models, (m) => log.error(m)));
    let file = loadDaemonFile(paths.config);
    const keychain = o.keychain ?? null;
    const resolveEndpoint = () =>
      o.modelConfig
        ? { config: o.modelConfig, source: 'env' as const }
        : resolveModelEndpoint({ env: o.env ?? process.env, home: o.home ?? homedir(), baseUrl: file.base_url, keychain });
    let endpointState = resolveEndpoint();
    if (!endpointState.config) log.info('no model endpoint configured yet; agents stay paused until one is set');
    const adapter = createSwitchableAdapter(endpointState.config, models);
    const runtime = new Runtime({
      store,
      adapter,
      models,
      dataDir: o.dataDir,
      ...(o.sandboxAvailable !== undefined ? { sandboxAvailable: o.sandboxAvailable } : {}),
      onError: (err, ctx) => log.error(`runtime error (${ctx})`, err),
    });
    const token = randomBytes(32).toString('hex');
    const version = o.version ?? DAEMON_VERSION;
    const startedAt = Date.now();
    const endpointStatus = () => ({ configured: endpointState.config !== null, source: endpointState.source, base_url: endpointState.config?.baseURL ?? null });
    const app = createApp({
      runtime,
      store,
      models,
      token,
      version,
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
