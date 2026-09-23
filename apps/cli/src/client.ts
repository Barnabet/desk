import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { StreamServerMessage } from '@desk/protocol';

export type DaemonInfo = { port: number; token: string; pid: number; version: string };

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export class DaemonNotRunning extends Error {
  constructor(dataDir: string) {
    super(`deskd is not running (no daemon.json in ${dataDir}). Start it with: desk up`);
  }
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

/** Minimal typed client over the deskd HTTP API and event stream. Responses are plain JSON rows. */
export class DeskClient {
  constructor(
    readonly baseUrl: string,
    private readonly token: string,
  ) {}

  static fromDataDir(dataDir: string): DeskClient {
    const info = readDaemonInfo(dataDir);
    if (!info) throw new DaemonNotRunning(dataDir);
    return new DeskClient(`http://127.0.0.1:${info.port}`, info.token);
  }

  async request<T = any>(method: string, path: string, body?: unknown): Promise<T> {
    let res: Response;
    try {
      res = await fetch(`${this.baseUrl}/v1${path}`, {
        method,
        headers: { authorization: `Bearer ${this.token}`, ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
    } catch {
      throw new Error(`Cannot reach deskd at ${this.baseUrl}. Is it running? (desk status)`);
    }
    const data = (res.headers.get('content-type') ?? '').includes('json') ? await res.json() : await res.text();
    if (!res.ok) {
      const e = (data as { error?: { code?: string; message?: string } })?.error;
      throw new ApiError(res.status, e?.code ?? 'error', e?.message ?? `HTTP ${res.status}`);
    }
    return data as T;
  }

  get = <T = any>(path: string) => this.request<T>('GET', path);
  post = <T = any>(path: string, body: unknown = {}) => this.request<T>('POST', path, body);
  patch = <T = any>(path: string, body: unknown) => this.request<T>('PATCH', path, body);
  del = <T = any>(path: string) => this.request<T>('DELETE', path);

  /** Subscribes to the event stream; resolves once replay is done. Returns a closer. */
  stream(projectId: string, afterSeq: number, onMessage: (m: StreamServerMessage) => void): Promise<() => void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(`${this.baseUrl.replace(/^http/, 'ws')}/v1/stream?token=${this.token}`);
      let ready = false;
      ws.onopen = () => ws.send(JSON.stringify({ subscribe: { project_id: projectId, after_seq: afterSeq } }));
      ws.onmessage = (e) => {
        const m = JSON.parse(String(e.data)) as StreamServerMessage;
        onMessage(m);
        if (m.kind === 'ready' && !ready) {
          ready = true;
          resolve(() => ws.close());
        }
      };
      ws.onerror = () => {
        if (!ready) reject(new Error('Event stream connection failed'));
      };
    });
  }
}
