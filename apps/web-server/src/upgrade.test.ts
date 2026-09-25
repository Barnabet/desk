import { createServer, type Server } from 'node:http';
import { connect, type AddressInfo, type Socket } from 'node:net';
import type { Duplex } from 'node:stream';
import { describe, expect, it } from 'vitest';
import { WebSocketServer } from 'ws';
import { openPush, refusedUpgrade, sleep, until } from './testing';
import { attachUpgrades } from './upgrade';

/** A server whose only route, /ok, accepts the upgrade and closes the socket with 1000. */
async function serve(): Promise<{ server: Server; port: number; close(): Promise<void> }> {
  const wss = new WebSocketServer({ noServer: true });
  const server = createServer((_req, res) => res.writeHead(404).end());
  let port = 0;
  attachUpgrades(server, { port: () => port, routes: { '/ok': (req, socket, head) => wss.handleUpgrade(req, socket, head, (ws) => ws.close(1000, 'bye')) } });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()));
  port = (server.address() as AddressInfo).port;
  return {
    server,
    port,
    close: async () => {
      wss.close();
      server.closeAllConnections();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    },
  };
}

type UpgradeHeaders = { host?: string; origin?: string; path?: string };

/** A raw WebSocket upgrade request (Host and Origin are the server's own unless given). */
const upgradeRequest = (port: number, o: UpgradeHeaders) =>
  [
    `GET ${o.path ?? '/ok'} HTTP/1.1`,
    `Host: ${o.host ?? `127.0.0.1:${port}`}`,
    `Origin: ${o.origin ?? `http://127.0.0.1:${port}`}`,
    'Connection: Upgrade',
    'Upgrade: websocket',
    'Sec-WebSocket-Version: 13',
    'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==',
    '',
    '',
  ].join('\r\n');

/** An upgrade over a raw socket that stays half-open when the server ends it; resolves with the response's status line. */
function halfOpenUpgrade(port: number, o: UpgradeHeaders = {}): Promise<{ socket: Socket; status: string }> {
  return new Promise((resolve, reject) => {
    const socket = connect({ host: '127.0.0.1', port, allowHalfOpen: true });
    socket.once('error', reject);
    socket.once('data', (data) => resolve({ socket, status: String(data).split('\r\n')[0] ?? '' }));
    socket.write(upgradeRequest(port, o));
  });
}

/** Sends an upgrade and resets the socket on the next tick, before the refusal is read: the server's write then fails. */
function resetBeforeRead(port: number, o: UpgradeHeaders): void {
  const socket = connect({ host: '127.0.0.1', port, allowHalfOpen: true });
  socket.on('error', () => {});
  socket.on('connect', () => {
    socket.write(upgradeRequest(port, o));
    setImmediate(() => socket.resetAndDestroy());
  });
}

describe('attachUpgrades', () => {
  it('checks Host and Origin before any WebSocket route, and refuses unknown paths', async () => {
    const { port, close } = await serve();
    try {
      const ok = openPush(port, { path: '/ok' });
      expect(await ok.closed).toBe(1000);
      expect(await refusedUpgrade(port, { path: '/ok', host: `localhost:${port}` })).toBe(421);
      expect(await refusedUpgrade(port, { path: '/ok', host: `[::1]:${port}` })).toBe(421);
      expect(await refusedUpgrade(port, { path: '/ok', origin: null })).toBe(403);
      expect(await refusedUpgrade(port, { path: '/ok', origin: `http://localhost:${port}` })).toBe(403);
      expect(await refusedUpgrade(port, { path: '/nope' })).toBe(404);
    } finally {
      await close();
    }
  });

  it('survives a refused client that resets its half-open socket', async () => {
    const { port, close } = await serve();
    try {
      const refusals = [
        { o: { host: `localhost:${port}` }, status: 'HTTP/1.1 421 Misdirected Request' },
        { o: { origin: 'https://evil.example' }, status: 'HTTP/1.1 403 Forbidden' },
        { o: { path: '/nope' }, status: 'HTTP/1.1 404 Not Found' },
      ];
      for (const { o, status } of refusals) {
        const c = await halfOpenUpgrade(port, o);
        expect(c.status).toBe(status);
        c.socket.resetAndDestroy();
      }
      expect(await openPush(port, { path: '/ok' }).closed).toBe(1000);
      // A reset before the refusal is read reaches a socket still writing it: only the socket's 'error' listener
      // keeps that EPIPE or ECONNRESET from crashing the server.
      for (const { o } of refusals) resetBeforeRead(port, o);
      await sleep(100);
      expect(await openPush(port, { path: '/ok' }).closed).toBe(1000);
    } finally {
      await close();
    }
  });

  it('drops a refused socket even while its client holds it half-open', async () => {
    const { server, port, close } = await serve();
    const serverSide: Duplex[] = [];
    server.on('upgrade', (_req, socket: Duplex) => serverSide.push(socket));
    let held: Socket | undefined;
    try {
      const c = await halfOpenUpgrade(port, { host: `localhost:${port}` });
      held = c.socket;
      expect(c.status).toBe('HTTP/1.1 421 Misdirected Request');
      await until(() => serverSide[0]?.destroyed === true);
    } finally {
      held?.destroy();
      for (const s of serverSide) s.destroy();
      await close();
    }
  });
});
