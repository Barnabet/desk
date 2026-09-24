import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { PROTOCOL_VERSION, type HealthResponse } from '@desk/protocol';
import { DeskClient } from './client';
import { DaemonNotRunning, ProtocolMismatch } from './errors';
import type { Credentials, DaemonInfo } from './types';

/** Where deskd keeps its data (and daemon.json) on this platform. */
export function defaultDataDir(env: NodeJS.ProcessEnv = process.env, platform: NodeJS.Platform = process.platform, home: string = homedir()): string {
  if (env.DESK_DATA_DIR) return env.DESK_DATA_DIR;
  if (platform === 'darwin') return join(home, 'Library', 'Application Support', 'Desk');
  if (platform === 'win32') return join(env.APPDATA ?? join(home, 'AppData', 'Roaming'), 'Desk');
  return join(env.XDG_DATA_HOME ?? join(home, '.local', 'share'), 'desk');
}

export function readDaemonInfo(dataDir: string): DaemonInfo | null {
  const file = join(dataDir, 'daemon.json');
  if (!existsSync(file)) return null;
  try {
    return JSON.parse(readFileSync(file, 'utf8')) as DaemonInfo;
  } catch {
    return null;
  }
}

const credsOf = (info: DaemonInfo): Credentials => ({ baseUrl: `http://127.0.0.1:${info.port}`, token: info.token });

/** A client for the daemon of `dataDir`; after a 401 it re-reads daemon.json (the token changes on every daemon start). */
export function clientFromDataDir(dataDir: string, opts: { fetch?: typeof fetch } = {}): DeskClient {
  const info = readDaemonInfo(dataDir);
  if (!info) throw new DaemonNotRunning(dataDir);
  return new DeskClient({
    ...credsOf(info),
    refresh: async () => {
      const next = readDaemonInfo(dataDir);
      return next ? credsOf(next) : null;
    },
    ...(opts.fetch ? { fetch: opts.fetch } : {}),
  });
}

/** Checks the daemon answers and speaks our protocol. */
export async function checkDaemon(client: DeskClient): Promise<HealthResponse> {
  const health = await client.health();
  if (health.protocol_version !== PROTOCOL_VERSION) throw new ProtocolMismatch(health.protocol_version, PROTOCOL_VERSION);
  return health;
}

export type { DaemonInfo };
