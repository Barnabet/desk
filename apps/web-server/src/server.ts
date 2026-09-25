import { execFile, spawn } from 'node:child_process';
import { closeSync, mkdirSync, openSync, readFileSync, rmSync } from 'node:fs';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { serve } from '@hono/node-server';
import type { Hono } from 'hono';
import type { DeskClient } from '@desk/client';
import { clientFromDataDir } from '@desk/client/node';
import { Broker, DaemonManager, type BrokerDeps } from '@desk/bff/server';
import { createWebApp } from './app';
import { CODE_TTL_MS, LoginCodes, Sessions } from './auth';
import { DevReload } from './dev';
import { processAlive, readWebInfo, removeWebInfo, WebSettingsStore, writeLoginFile, writeWebInfo, type WebInfo } from './files';
import { listDirs } from './list-dirs';
import { createLog } from './log';
import { webNotices } from './notices';
import { openPath } from './opener';
import { PushHub } from './push';
import { attachUpgrades } from './upgrade';
import { webHandlerContext, type WebDaemon } from './web-context';

/** deskd listens on 7433; common dev servers use neither. */
export const DEFAULT_WEB_PORT = 7434;
export const WEB_VERSION = (JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')) as { version: string }).version;
const REPO_ROOT = fileURLToPath(new URL('../../..', import.meta.url));
const DEFAULT_UI_DIR = fileURLToPath(new URL('../../web-ui/dist/browser', import.meta.url));

/** desk web cannot start: another instance runs for this data dir, or the port is taken. `message` is for the terminal. */
export class WebServerError extends Error {
  constructor(
    readonly code: 'already_running' | 'port_in_use',
    message: string,
  ) {
    super(message);
    this.name = 'WebServerError';
  }
}

export type StartWebServerOptions = {
  dataDir: string;
  /** The port on 127.0.0.1: web-settings.json's by default, else 7434. 0 picks a free one (tests). */
  port?: number;
  /** Open a fresh login link in the browser once listening. */
  open?: boolean;
  /** Serve /__dev/reload.js and reload the page when the build changes. */
  dev?: boolean;
  /** The built Angular app: apps/web-ui/dist/browser by default. */
  uiDir?: string;
  /** How to reach deskd: daemon.json in dataDir by default; null while deskd is down. */
  client?: () => DeskClient | null;
  /** Warnings for the person at the terminal (stderr by default). */
  log?: (message: string) => void;
  /** Opens a file with the platform opener (the login redirect file, the logs folder). */
  opener?: (target: string) => Promise<void>;
  daemon?: WebDaemon;
  home?: string;
  streamFactory?: BrokerDeps['streamFactory'];
  authTimeoutMs?: number;
};

export type WebServer = {
  port: number;
  /** http://127.0.0.1:<port> */
  url: string;
  /** A fresh one-time login link (valid for 2 minutes). */
  loginLink(): string;
  /** Opens a fresh login link in the browser through a 0600 redirect file; returns that link. */
  openLoginLink(): Promise<string>;
  close(): Promise<void>;
};

/** The web.json of a desk web that is still running and answering, if any. */
async function liveInstance(dataDir: string): Promise<WebInfo | null> {
  const info = readWebInfo(dataDir);
  if (!info || !processAlive(info.pid)) return null;
  try {
    const res = await fetch(`http://127.0.0.1:${info.port}/healthz`, { signal: AbortSignal.timeout(1500) });
    return res.ok ? info : null;
  } catch {
    return null;
  }
}

function listen(app: Hono, port: number): Promise<Server> {
  return new Promise((resolve, reject) => {
    const server = serve({ fetch: app.fetch, port, hostname: '127.0.0.1' }, () => resolve(server as Server)) as Server;
    server.once('error', reject);
  });
}

/** DaemonManager in web mode: the desktop app's LaunchAgent through launchctl when installed, else the repo's deskd. */
function webDaemonManager(dataDir: string, home: string): DaemonManager {
  return new DaemonManager({
    dataDir,
    mode: 'web',
    platform: process.platform,
    home,
    uid: process.getuid?.() ?? 0,
    bundledVersion: WEB_VERSION,
    bundledBuild: null,
    execPath: process.execPath,
    bundlePath: '',
    repoRoot: REPO_ROOT,
    nodePath: process.env.DESK_NODE ?? process.execPath,
    exec: (file, args) =>
      new Promise((done) =>
        execFile(file, args, (err, stdout, stderr) => done({ code: err ? (typeof err.code === 'number' ? err.code : 1) : 0, stdout: String(stdout), stderr: String(stderr) })),
      ),
    spawnDetached: (file, args, s) => {
      const fd = openSync(s.logFile, 'a');
      spawn(file, args, { cwd: s.cwd, env: { ...process.env, ...s.env }, detached: true, stdio: ['ignore', fd, fd] }).unref();
      closeSync(fd);
    },
    kill: (pid) => process.kill(pid, 'SIGTERM'),
  });
}

/** Starts desk web on 127.0.0.1 (spec §2): the Angular app, /login, /rpc, /push, /healthz. */
export async function startWebServer(o: StartWebServerOptions): Promise<WebServer> {
  mkdirSync(o.dataDir, { recursive: true });
  const running = await liveInstance(o.dataDir);
  if (running) {
    throw new WebServerError('already_running', `desk web is already running at http://127.0.0.1:${running.port} (pid ${running.pid}). Press Enter in its terminal for a new login link.`);
  }

  const say = o.log ?? ((message: string) => void process.stderr.write(`${message}\n`));
  const fileLog = createLog(join(o.dataDir, 'logs', 'web.log'));
  const home = o.home ?? homedir();
  const settings = new WebSettingsStore(o.dataDir);
  const port = o.port ?? settings.get().port ?? DEFAULT_WEB_PORT;
  const uiDir = o.uiDir ?? DEFAULT_UI_DIR;
  const opener = o.opener ?? ((target: string) => openPath(target));
  const connect =
    o.client ??
    (() => {
      try {
        return clientFromDataDir(o.dataDir);
      } catch {
        return null;
      }
    });
  let boundPort = port;

  const loginFiles = new Map<string, string>();
  const dropLoginFile = (code: string) => {
    const file = loginFiles.get(code);
    if (!file) return;
    rmSync(file, { force: true });
    loginFiles.delete(code);
  };
  const sessions = new Sessions();
  const codes = new LoginCodes(sessions, { onRedeemed: dropLoginFile });

  // The hub and the broker need each other: the broker pushes through the hub, the hub drops the broker's senders.
  let hub!: PushHub;
  const claim = () => settings.get().notifications && hub.granted() > 0;
  const broker = new Broker({
    connect,
    credentials: () => connect()?.credentials() ?? null,
    send: (senderId, channel, payload) => hub.send(senderId, channel, payload),
    broadcast: (channel, payload) => hub.broadcast(channel, payload),
    hello: () => ({ client: 'web', notifications: claim() }),
    onAttentionAdded: (items) => {
      if (settings.get().notifications) hub.notify(webNotices(items));
    },
    ...(o.streamFactory ? { streamFactory: o.streamFactory } : {}),
    log: fileLog,
  });
  // deskd posts its own notifications unless a client claims them: claim only while a browser can show them.
  let claimed = false;
  const refreshClaim = () => {
    const next = claim();
    if (next === claimed) return;
    claimed = next;
    broker.updateHello();
  };
  const daemon = o.daemon ?? webDaemonManager(o.dataDir, home);
  const context = (senderId: number) =>
    webHandlerContext({ dataDir: o.dataDir, version: WEB_VERSION, platform: process.platform, broker, daemon, settings, openPath: opener, onSettingsChange: refreshClaim }, senderId);
  hub = new PushHub({ sessions, broker, ctx: context, onGrantedChange: refreshClaim, ...(o.authTimeoutMs ? { authTimeoutMs: o.authTimeoutMs } : {}), log: fileLog });

  const sources = async () => {
    const client = broker.getClient();
    const projects = await client.projects.list();
    const overviews = await Promise.all(projects.map((p) => client.projects.get(p.id)));
    return overviews.flatMap((ov) => ov.sources.map((s) => s.path));
  };
  const app = createWebApp({
    port: () => boundPort,
    sessions,
    codes,
    uiDir,
    dev: o.dev ?? false,
    ctx: () => context(0),
    web: { 'fs.listDirs': (input) => listDirs(input, { home, dataDir: o.dataDir, sources }) },
    log: fileLog,
    onReplay: () =>
      say('Warning: a login link was used twice, so the session it opened was signed out. If that was not you, someone else on this computer saw the link.'),
  });

  let server: Server;
  try {
    server = await listen(app, port);
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'EADDRINUSE') {
      throw new WebServerError('port_in_use', `Port ${port} on 127.0.0.1 is in use. Choose another with: desk web --port <n>`);
    }
    throw err;
  }
  boundPort = (server.address() as AddressInfo).port;
  const dev = o.dev ? new DevReload(uiDir) : null;
  dev?.start();
  attachUpgrades(server, { port: () => boundPort, routes: { '/push': hub.handleUpgrade, ...(dev ? { '/__dev/reload': dev.handleUpgrade } : {}) } });
  writeWebInfo(o.dataDir, { pid: process.pid, port: boundPort });
  void broker.start();

  const url = `http://127.0.0.1:${boundPort}`;
  const issueLink = () => {
    const code = codes.issue();
    return { code, link: `${url}/login?code=${code}` };
  };
  const timers = new Set<ReturnType<typeof setTimeout>>();
  const openLoginLink = async (): Promise<string> => {
    const { code, link } = issueLink();
    loginFiles.set(code, writeLoginFile(o.dataDir, link));
    const timer = setTimeout(() => {
      timers.delete(timer);
      dropLoginFile(code);
    }, CODE_TTL_MS);
    timer.unref();
    timers.add(timer);
    await opener(loginFiles.get(code)!);
    return link;
  };
  if (o.open) await openLoginLink().catch((err: unknown) => say(`Could not open the browser: ${err instanceof Error ? err.message : String(err)}`));

  let closed = false;
  return {
    port: boundPort,
    url,
    loginLink: () => issueLink().link,
    openLoginLink,
    close: async () => {
      if (closed) return;
      closed = true;
      broker.stop();
      hub.close();
      dev?.close();
      for (const timer of timers) clearTimeout(timer);
      for (const code of [...loginFiles.keys()]) dropLoginFile(code);
      removeWebInfo(o.dataDir, process.pid);
      await new Promise<void>((resolve) => {
        server.closeAllConnections();
        server.close(() => resolve());
      });
    },
  };
}
