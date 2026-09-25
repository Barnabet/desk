import { WebSettingsStore } from './files';
import { startWebServer, type StartWebServerOptions, type WebServer } from './server';

export type WebCommandOptions = { dataDir: string; port?: number; open: boolean; dev: boolean };

export type WebCommandIO = {
  out(s: string): void;
  err(s: string): void;
  /** Calls back on each Enter in the terminal; returns an unsubscribe. Absent without a terminal. */
  onEnter?(cb: () => void): () => void;
  /** Settles when the person stops desk web (Ctrl-C). */
  stopped: Promise<void>;
};

/**
 * `desk web`: serves until stopped, printing a one-time login link now and on every Enter. With `open`, each time it
 * also opens a different fresh link in the browser, so using the printed link as well never replays the opened one.
 */
export async function runWebCommand(o: WebCommandOptions, io: WebCommandIO, start: (opts: StartWebServerOptions) => Promise<WebServer> = startWebServer): Promise<void> {
  const server = await start({ dataDir: o.dataDir, ...(o.port !== undefined ? { port: o.port } : {}), open: o.open, dev: o.dev, log: (message) => io.err(`${message}\n`) });
  // The origin, and everything the browser keeps for it (the session, onboarding, drafts), depends on the port.
  if (o.port !== undefined && o.port > 0) new WebSettingsStore(o.dataDir).update({ port: o.port });
  io.out(`Desk is at ${server.url}${o.dev ? ' (dev: the page reloads when the build changes)' : ''}\n`);
  io.out(`${o.open ? 'Opened Desk in your browser. If it did not open, sign in with' : 'Sign in with'} this one-time link (valid for 2 minutes):\n  ${server.loginLink()}\n`);
  io.out(io.onEnter ? 'Press Enter for a new link, Ctrl-C to stop.\n' : 'Press Ctrl-C to stop.\n');
  const off = io.onEnter?.(() => {
    io.out(`New one-time link (valid for 2 minutes):\n  ${server.loginLink()}\n`);
    if (o.open) void server.openLoginLink().catch((err: unknown) => io.err(`Could not open the browser: ${err instanceof Error ? err.message : String(err)}\n`));
  });
  try {
    await io.stopped;
  } finally {
    off?.();
    await server.close();
  }
  io.out('desk web stopped.\n');
}
