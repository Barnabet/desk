import { request } from 'node:http';
import { WebSocket } from 'ws';
import { initialGlobalState } from '@desk/bff/contract';
import type { HandlerContext } from '@desk/bff/server';
import { decodeBytes, encodeBytes } from './codec';

/** Helpers for @desk/web-server's tests. Not exported by the package. */

export const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export async function until(pred: () => boolean, ms = 3000): Promise<void> {
  const end = Date.now() + ms;
  while (!pred()) {
    if (Date.now() > end) throw new Error('timed out');
    await sleep(5);
  }
}

/** A HandlerContext whose every host part throws: for tests that never reach it. */
export function unusedContext(senderId = 0): HandlerContext {
  const unused = (): never => {
    throw new Error('not used in this test');
  };
  const unusedAsync = async (): Promise<never> => unused();
  return {
    senderId,
    client: unused,
    broker: { snapshot: () => initialGlobalState(), watch: unusedAsync, unwatch: unused },
    daemon: { status: unusedAsync, start: unusedAsync, restart: unusedAsync, stop: unusedAsync, repair: unusedAsync },
    app: {
      info: unused,
      openExternal: unusedAsync,
      pickFolder: unusedAsync,
      revealLogs: unusedAsync,
      saveFile: unusedAsync,
      openMain: unused,
      settings: unused,
      updateSettings: unused,
    },
  };
}

export type RawResponse = { status: number; headers: Record<string, string | string[] | undefined>; body: string };

/** An HTTP request with exactly these headers (Host included), which fetch cannot send. */
export function rawRequest(port: number, o: { method?: string; path?: string; headers?: Record<string, string>; body?: string } = {}): Promise<RawResponse> {
  return new Promise((resolve, reject) => {
    const req = request({ host: '127.0.0.1', port, method: o.method ?? 'GET', path: o.path ?? '/', headers: o.headers ?? {} }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk: string) => (body += chunk));
      res.on('end', () => resolve({ status: res.statusCode ?? 0, headers: res.headers, body }));
    });
    req.on('error', reject);
    req.end(o.body);
  });
}

/** A frame a test socket received: parsed JSON, or `text` when it was not JSON. */
export type Frame = { channel?: string; payload?: any; ack?: number; result?: any; text?: string };

export type PushClient = {
  ws: WebSocket;
  frames: Frame[];
  opened: Promise<void>;
  closed: Promise<number>;
  send(frame: unknown): void;
  /** The first frame (already received or still to come) that matches. */
  next(pred: (f: Frame) => boolean, ms?: number): Promise<Frame>;
};

/** `origin: null` sends no Origin header; `host` replaces the Host header. */
export type PushOptions = { origin?: string | null; host?: string; path?: string };

function socket(port: number, o: PushOptions): WebSocket {
  const origin = o.origin === undefined ? `http://127.0.0.1:${port}` : o.origin;
  return new WebSocket(`ws://127.0.0.1:${port}${o.path ?? '/push'}`, { ...(origin ? { origin } : {}), ...(o.host ? { headers: { host: o.host } } : {}) });
}

export function openPush(port: number, o: PushOptions = {}): PushClient {
  const ws = socket(port, o);
  const frames: Frame[] = [];
  const waiters = new Set<() => void>();
  ws.on('message', (data) => {
    const text = String(data);
    try {
      frames.push(JSON.parse(text) as Frame);
    } catch {
      frames.push({ text });
    }
    for (const w of [...waiters]) w();
  });
  const opened = new Promise<void>((resolve, reject) => {
    ws.once('open', () => resolve());
    ws.once('error', reject);
  });
  opened.catch(() => {});
  const closed = new Promise<number>((resolve) => ws.once('close', (code) => resolve(code)));
  return {
    ws,
    frames,
    opened,
    closed,
    send: (frame) => ws.send(JSON.stringify(frame)),
    next: (pred, ms = 3000) =>
      new Promise<Frame>((resolve, reject) => {
        const timer = setTimeout(() => {
          waiters.delete(check);
          reject(new Error(`no matching frame within ${ms} ms; got ${JSON.stringify(frames).slice(0, 800)}`));
        }, ms);
        function check(): void {
          const hit = frames.find(pred);
          if (!hit) return;
          waiters.delete(check);
          clearTimeout(timer);
          resolve(hit);
        }
        waiters.add(check);
        check();
      }),
  };
}

/** The HTTP status of an upgrade the server refuses; rejects if it is accepted. */
export function refusedUpgrade(port: number, o: PushOptions = {}): Promise<number> {
  return new Promise((resolve, reject) => {
    const ws = socket(port, o);
    ws.on('error', () => {});
    ws.on('unexpected-response', (_req, res) => {
      resolve(res.statusCode ?? 0);
      ws.terminate();
    });
    ws.on('open', () => {
      ws.close();
      reject(new Error('the upgrade was accepted'));
    });
  });
}

/** A /push socket signed in with `secret` (its first frame, the global snapshot, has arrived). */
export async function connected(port: number, secret: string): Promise<PushClient> {
  const c = openPush(port);
  await c.opened;
  c.send({ session: secret });
  await c.next((f) => f.channel === 'desk:global');
  return c;
}

/** Follows a login link and returns the session secret from its page. */
export async function redeem(link: string): Promise<string> {
  const res = await fetch(link);
  const html = await res.text();
  const secret = /<meta name="desk-session" content="([^"]+)">/.exec(html)?.[1];
  if (!secret) throw new Error(`no session secret (HTTP ${res.status})`);
  return secret;
}

/** POST /rpc/<op> as the web UI sends it. */
export async function rpc(port: number, secret: string, op: string, input: unknown = {}): Promise<{ status: number; body: any; headers: Headers }> {
  const res = await fetch(`http://127.0.0.1:${port}/rpc/${op}`, {
    method: 'POST',
    headers: { origin: `http://127.0.0.1:${port}`, 'content-type': 'application/json', 'x-desk-session': secret },
    body: JSON.stringify(encodeBytes(input)),
  });
  return { status: res.status, body: decodeBytes(await res.json()), headers: res.headers };
}
