import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { serve } from '@hono/node-server';
import type { Hono } from 'hono';
import type { EventStore } from '@desk/core';
import { attachStream } from './stream';

export type RunningServer = { port: number; server: Server; close(): Promise<void> };

function listen(app: Hono, port: number): Promise<Server> {
  return new Promise((resolve, reject) => {
    const server = serve({ fetch: app.fetch, port, hostname: '127.0.0.1' }, () => resolve(server as Server)) as Server;
    server.once('error', reject);
  });
}

/** Serves the app and the event stream on 127.0.0.1. Falls back to an ephemeral port if `port` is taken. */
export async function startServer(opts: { app: Hono; store: EventStore; token: string; port: number }): Promise<RunningServer> {
  let server: Server;
  try {
    server = await listen(opts.app, opts.port);
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== 'EADDRINUSE' || opts.port === 0) throw err;
    server = await listen(opts.app, 0);
  }
  const closeStream = attachStream(server, opts.store, opts.token);
  return {
    port: (server.address() as AddressInfo).port,
    server,
    close: () =>
      new Promise<void>((resolve) => {
        closeStream();
        server.closeAllConnections?.();
        server.close(() => resolve());
      }),
  };
}
