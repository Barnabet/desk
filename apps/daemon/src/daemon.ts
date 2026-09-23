import { randomBytes } from 'node:crypto';
import { chmodSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { createModelAdapter, EventStore, loadModelConfig, ModelRegistry, openDb, Runtime, type ModelConfig } from '@desk/core';
import { createApp } from './app';
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
  /** Model access; defaults to DESK_OPENAI_* env or ~/.config/cliproxyapi.env. */
  modelConfig?: ModelConfig;
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
    const adapter = createModelAdapter(o.modelConfig ?? loadModelConfig(), models);
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
    const app = createApp({ runtime, store, models, token, version, saveModels: (m) => saveModels(paths.models, m) });
    const server = await startServer({ app, store, token, port: o.port ?? DEFAULT_PORT });

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
