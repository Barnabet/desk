import type { IncomingMessage, Server } from 'node:http';
import type { Duplex } from 'node:stream';
import { WebSocketServer, type WebSocket } from 'ws';
import { StreamClientMessage, type StreamServerMessage } from '@desk/protocol';
import type { EventStore, StreamItem } from '@desk/core';
import { tokenMatches } from './http';

const REPLAY_PAGE = 1000;
const HEARTBEAT_MS = 30_000;

type Subscription = { projectId: string; cursor: number };

function send(ws: WebSocket, msg: StreamServerMessage): void {
  if (ws.readyState === ws.OPEN) ws.send(JSON.stringify(msg));
}

function serve(ws: WebSocket, store: EventStore, notifying: Set<WebSocket>): void {
  let sub: Subscription | null = null;
  const matches = (projectId: string) => sub !== null && (sub.projectId === '*' || sub.projectId === projectId);

  const unsubscribe = store.subscribe((item: StreamItem) => {
    if (!sub || !matches(item.event.project_id)) return;
    if (item.kind === 'event') {
      if (item.event.id <= sub.cursor) return;
      sub.cursor = item.event.id;
    }
    send(ws, item);
  });

  ws.on('message', (data) => {
    let parsed: StreamClientMessage;
    try {
      parsed = StreamClientMessage.parse(JSON.parse(String(data)));
    } catch {
      send(ws, { kind: 'error', message: 'Expected {"subscribe":{"project_id":string,"after_seq":number}} or {"hello":{"client":string,"notifications":boolean}}' });
      return;
    }
    if ('hello' in parsed) {
      if (parsed.hello.notifications) notifying.add(ws);
      else notifying.delete(ws);
      return;
    }
    const { project_id, after_seq } = parsed.subscribe;
    sub = { projectId: project_id, cursor: after_seq };
    // Synchronous replay: no live event can interleave before `ready`, so there are no gaps or duplicates.
    for (;;) {
      const page = store.list({ after: sub.cursor, limit: REPLAY_PAGE, ...(project_id === '*' ? {} : { projectId: project_id }) });
      for (const event of page) {
        send(ws, { kind: 'event', event });
        sub.cursor = event.id;
      }
      if (page.length < REPLAY_PAGE) break;
    }
    send(ws, { kind: 'ready', seq: sub.cursor });
  });

  const heartbeat = setInterval(() => ws.ping(), HEARTBEAT_MS);
  heartbeat.unref();
  ws.on('close', () => {
    notifying.delete(ws);
    clearInterval(heartbeat);
    unsubscribe();
  });
}

/** Serves `/v1/stream` WebSocket upgrades on `server`. Also counts clients that announced they show notifications. */
export function attachStream(server: Server, store: EventStore, token: string): { close(): void; notifyingClients(): number } {
  const wss = new WebSocketServer({ noServer: true });
  const notifying = new Set<WebSocket>();
  server.on('upgrade', (req: IncomingMessage, socket: Duplex, head: Buffer) => {
    const url = new URL(req.url ?? '/', 'http://localhost');
    if (url.pathname !== '/v1/stream') {
      socket.destroy();
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => {
      if (!tokenMatches(token, url.searchParams.get('token'))) {
        ws.close(4401, 'unauthorized');
        return;
      }
      serve(ws, store, notifying);
    });
  });
  return {
    close: () => {
      for (const client of wss.clients) client.terminate();
      wss.close();
    },
    notifyingClients: () => notifying.size,
  };
}
