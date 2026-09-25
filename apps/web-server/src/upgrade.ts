import type { IncomingMessage, Server } from 'node:http';
import type { Duplex } from 'node:stream';
import { hostAllowed, originAllowed } from './security';

export type UpgradeRoute = (req: IncomingMessage, socket: Duplex, head: Buffer) => void;

const REASON = { 403: 'Forbidden', 404: 'Not Found', 421: 'Misdirected Request' } as const;

/**
 * Writes the refusal and closes the socket, as ws's abortHandshake does. Node removes its own socket error handler
 * before emitting 'upgrade', so without ours a client's reset would crash desk web; and the socket is half-open,
 * so it is destroyed once the response is flushed rather than left waiting for the client.
 */
function refuse(socket: Duplex, status: keyof typeof REASON): void {
  socket.on('error', () => socket.destroy());
  socket.once('finish', () => socket.destroy());
  socket.end(`HTTP/1.1 ${status} ${REASON[status]}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
}

/** WebSocket upgrades: the same Host (421) and Origin (403) checks as /rpc, then the route for the path (else 404). */
export function attachUpgrades(server: Server, o: { port: () => number; routes: Record<string, UpgradeRoute> }): void {
  server.on('upgrade', (req: IncomingMessage, socket: Duplex, head: Buffer) => {
    if (!hostAllowed(req.headers.host, o.port())) return refuse(socket, 421);
    if (!originAllowed(req.headers.origin, o.port())) return refuse(socket, 403);
    let path: string;
    try {
      path = new URL(req.url ?? '/', 'http://127.0.0.1').pathname;
    } catch {
      return refuse(socket, 404);
    }
    const route = Object.hasOwn(o.routes, path) ? o.routes[path] : undefined;
    if (!route) return refuse(socket, 404);
    route(req, socket, head);
  });
}
