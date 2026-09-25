import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { runWebCommand, type WebCommandIO } from './command';
import { WebSettingsStore, webPaths } from './files';
import { WebServerError, type StartWebServerOptions, type WebServer } from './server';
import { until } from './testing';

let dir: string;
beforeEach(() => void (dir = mkdtempSync(join(tmpdir(), 'desk-web-cmd-'))));
afterEach(() => rmSync(dir, { recursive: true, force: true }));

/** A server double: numbered links, and a log of what happened. */
function fake() {
  let n = 0;
  const events: string[] = [];
  const started: StartWebServerOptions[] = [];
  const server: WebServer = {
    port: 7500,
    url: 'http://127.0.0.1:7500',
    loginLink: () => `http://127.0.0.1:7500/login?code=c${++n}`,
    openLoginLink: async () => {
      const link = `http://127.0.0.1:7500/login?code=c${++n}`;
      events.push(`opened ${link}`);
      return link;
    },
    close: async () => void events.push('closed'),
  };
  const start = async (o: StartWebServerOptions) => {
    started.push(o);
    events.push('started');
    return server;
  };
  return { events, started, start };
}

/** A terminal double: collects output, and lets the test press Enter and Ctrl-C. */
function terminal() {
  let out = '';
  let err = '';
  const keys: { enter?: () => void; stop?: () => void } = {};
  const io: WebCommandIO = {
    out: (s) => void (out += s),
    err: (s) => void (err += s),
    onEnter: (cb) => {
      keys.enter = cb;
      return () => void delete keys.enter;
    },
    stopped: new Promise<void>((resolve) => (keys.stop = () => resolve())),
  };
  return { io, keys, out: () => out, err: () => err };
}

describe('runWebCommand', () => {
  it('prints the address and a one-time link, a new link on each Enter, and closes the server when stopped', async () => {
    const { events, start } = fake();
    const t = terminal();
    const run = runWebCommand({ dataDir: dir, open: false, dev: false }, t.io, start);
    await until(() => t.keys.enter !== undefined);
    expect(t.out()).toBe(
      'Desk is at http://127.0.0.1:7500\n' +
        'Sign in with this one-time link (valid for 2 minutes):\n  http://127.0.0.1:7500/login?code=c1\n' +
        'Press Enter for a new link, Ctrl-C to stop.\n',
    );
    t.keys.enter?.();
    expect(t.out()).toContain('New one-time link (valid for 2 minutes):\n  http://127.0.0.1:7500/login?code=c2\n');
    t.keys.stop?.();
    await run;
    expect(events).toEqual(['started', 'closed']);
    expect(t.keys.enter).toBeUndefined();
    expect(t.out().endsWith('desk web stopped.\n')).toBe(true);
  });

  it('opens the browser at start and on each Enter, with a link other than the printed one', async () => {
    const { events, started, start } = fake();
    const t = terminal();
    const run = runWebCommand({ dataDir: dir, open: true, dev: true }, t.io, start);
    await until(() => t.keys.enter !== undefined);
    expect(started[0]).toMatchObject({ dataDir: dir, open: true, dev: true });
    expect(t.out()).toContain('Desk is at http://127.0.0.1:7500 (dev: the page reloads when the build changes)\n');
    expect(t.out()).toContain('Opened Desk in your browser. If it did not open, sign in with this one-time link (valid for 2 minutes):\n  http://127.0.0.1:7500/login?code=c1\n');
    t.keys.enter?.();
    await until(() => events.some((e) => e.startsWith('opened')));
    expect(t.out()).toContain('login?code=c2\n');
    expect(events).toEqual(['started', 'opened http://127.0.0.1:7500/login?code=c3']);
    t.keys.stop?.();
    await run;
  });

  it('remembers a chosen port once the server is up', async () => {
    const { start } = fake();
    const t = terminal();
    const run = runWebCommand({ dataDir: dir, port: 7500, open: false, dev: false }, t.io, start);
    await until(() => t.keys.enter !== undefined);
    expect(JSON.parse(readFileSync(webPaths(dir).settings, 'utf8'))).toMatchObject({ port: 7500 });
    t.keys.stop?.();
    await run;
  });

  it('keeps the chosen port when the running server later changes a setting through its own store', async () => {
    const { start } = fake();
    // Like startWebServer: the server loads its store before the command saves the port.
    let serverStore!: WebSettingsStore;
    const startWithStore = async (o: StartWebServerOptions) => {
      serverStore = new WebSettingsStore(o.dataDir);
      return start(o);
    };
    const t = terminal();
    const run = runWebCommand({ dataDir: dir, port: 7500, open: false, dev: false }, t.io, startWithStore);
    await until(() => t.keys.enter !== undefined);
    serverStore.update({ notifications: false });
    expect(JSON.parse(readFileSync(webPaths(dir).settings, 'utf8'))).toEqual({ port: 7500, notifications: false });
    expect(new WebSettingsStore(dir).get()).toEqual({ port: 7500, notifications: false });
    t.keys.stop?.();
    await run;
  });

  it('passes a refusal to start on, without saving the port or printing a link', async () => {
    const t = terminal();
    const refuse = async (): Promise<WebServer> => {
      throw new WebServerError('port_in_use', 'Port 7500 on 127.0.0.1 is in use. Choose another with: desk web --port <n>');
    };
    await expect(runWebCommand({ dataDir: dir, port: 7500, open: false, dev: false }, t.io, refuse)).rejects.toMatchObject({ code: 'port_in_use' });
    expect(existsSync(webPaths(dir).settings)).toBe(false);
    expect(t.out()).toBe('');
  });

  it('does not offer Enter without a terminal', async () => {
    const { start } = fake();
    let out = '';
    let stop!: () => void;
    const io: WebCommandIO = { out: (s) => void (out += s), err: () => {}, stopped: new Promise<void>((resolve) => (stop = () => resolve())) };
    const run = runWebCommand({ dataDir: dir, open: false, dev: false }, io, start);
    await until(() => out.includes('Ctrl-C'));
    expect(out).toContain('Press Ctrl-C to stop.\n');
    expect(out).not.toContain('Press Enter');
    stop();
    await run;
  });
});
