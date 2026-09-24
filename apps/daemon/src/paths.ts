import { homedir } from 'node:os';
import { join } from 'node:path';

export function defaultDataDir(env: NodeJS.ProcessEnv = process.env): string {
  return env.DESK_DATA_DIR ?? join(homedir(), 'Library', 'Application Support', 'Desk');
}

export function daemonPaths(dataDir: string) {
  return {
    dataDir,
    db: join(dataDir, 'desk.db'),
    daemonJson: join(dataDir, 'daemon.json'),
    lock: join(dataDir, 'daemon.lock'),
    models: join(dataDir, 'models.json'),
    config: join(dataDir, 'config.json'),
    logs: join(dataDir, 'logs'),
    logFile: join(dataDir, 'logs', 'deskd.log'),
  };
}

/** Contents of daemon.json, read by clients to find and authenticate to the daemon. */
export type DaemonInfo = { port: number; token: string; pid: number; version: string; started_at: string };
