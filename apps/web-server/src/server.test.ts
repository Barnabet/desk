import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { createServer as createNetServer, type AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { DeskClient, type StreamOptions } from '@desk/client';
import { createHarness, newRuntime, type Harness } from '@desk/core/testing';
import { createApp, startServer, type RunningServer } from '@desk/daemon';
import type { StoredEvent } from '@desk/protocol';
import { readWebInfo, writeWebInfo } from './files';
import { startWebServer, type StartWebServerOptions, type WebServer } from './server';
import { connected, openPush, rawRequest, redeem, refusedUpgrade, rpc, sleep, until } from './testing';

let h: Harness | undefined;
let deskd: RunningServer | undefined;
let web: WebServer | undefined;
const dirs: string[] = [];
afterEach(async () => {
  await web?.close();
  web = undefined;
  await deskd?.close();
  deskd = undefined;
  await h?.cleanup();
  h = undefined;
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});

const tempDir = (prefix: string) => {
  const d = realpathSync(mkdtempSync(join(tmpdir(), prefix)));
  dirs.push(d);
  return d;
};

/** A port on 127.0.0.1 that was free a moment ago. */
const freePort = () =>
  new Promise<number>((resolve) => {
    const s = createNetServer();
    s.listen(0, '127.0.0.1', () => {
      const { port } = s.address() as AddressInfo;
      s.close(() => resolve(port));
    });
  });

/** Open fs.watch handles in this process (desk web --dev watches the build folder). */
const watchers = () => process.getActiveResourcesInfo().filter((r) => r === 'FSEventWrap').length;

/** An in-process deskd with one project, and desk web on a free port in front of it; the broker's stream is captured. */
async function setup(o: Partial<StartWebServerOptions> = {}) {
  h = await createHarness();
  const runtime = newRuntime(h);
  const projectId = runtime.createProject({ name: 'Launch', goal: 'g' });
  deskd = await startServer({ app: createApp({ runtime, store: h.store, models: h.models, token: 't', version: '1.0.0' }), store: h.store, token: 't', port: 0 });
  const creds = { baseUrl: `http://127.0.0.1:${deskd.port}`, token: 't' };
  const dataDir = tempDir('desk-web-');
  const uiDir = join(dataDir, 'ui');
  mkdirSync(uiDir);
  writeFileSync(join(uiDir, 'index.html'), '<!doctype html><html><head><title>Desk</title></head><body><desk-root></desk-root></body></html>');
  const streams: StreamOptions[] = [];
  const lines: string[] = [];
  web = await startWebServer({
    dataDir,
    port: 0,
    uiDir,
    client: () => new DeskClient(creds),
    streamFactory: (s) => {
      streams.push(s);
      return { start: () => {}, close: () => {} };
    },
    log: (m) => void lines.push(m),
    opener: async () => {},
    ...o,
  });
  await until(() => streams.length > 0);
  const hello = () => streams.at(-1)?.hello?.notifications;
  return { store: h.store, projectId, dataDir, uiDir, streams, lines, hello, stream: () => streams.at(-1)! };
}

describe('startWebServer', () => {
  it('answers only Host 127.0.0.1:<port>, and same-origin /rpc and /push, over the network', async () => {
    await setup();
    const port = web!.port;
    expect((await fetch(`http://127.0.0.1:${port}/`)).status).toBe(200);
    for (const host of [`localhost:${port}`, `[::1]:${port}`, `127.0.0.1:${port + 1}`]) {
      expect((await rawRequest(port, { path: '/', headers: { host } })).status, host).toBe(421);
      expect(await refusedUpgrade(port, { host }), host).toBe(421);
    }
    expect((await rawRequest(port, { method: 'POST', path: '/rpc/health', headers: { 'content-type': 'application/json' }, body: '{}' })).status).toBe(403);
    expect(await refusedUpgrade(port, { origin: null })).toBe(403);
    expect(await refusedUpgrade(port, { origin: 'http://evil.example' })).toBe(403);
  });

  it('signs in with a one-time link and runs operations against deskd, with no cookie anywhere', async () => {
    await setup();
    const port = web!.port;
    expect(web!.url).toBe(`http://127.0.0.1:${port}`);
    const link = web!.loginLink();
    expect(link).toMatch(new RegExp(`^http://127\\.0\\.0\\.1:${port}/login\\?code=[A-Za-z0-9_-]{43}$`));
    const login = await fetch(link);
    expect(login.status).toBe(200);
    expect(login.headers.get('cache-control')).toBe('no-store');
    const secret = /<meta name="desk-session" content="([^"]+)">/.exec(await login.text())![1]!;
    const created = await rpc(port, secret, 'projects.create', { name: 'Second', goal: 'g' });
    expect(created).toMatchObject({ status: 200, body: { ok: true, value: { project: { name: 'Second' } } } });
    const list = await rpc(port, secret, 'projects.list', {});
    expect((list.body.value as Array<{ name: string }>).map((p) => p.name).sort()).toEqual(['Launch', 'Second']);
    expect((await rpc(port, 'A'.repeat(43), 'projects.list', {})).status).toBe(401);
    const others = [await fetch(`http://127.0.0.1:${port}/`), await fetch(`http://127.0.0.1:${port}/healthz`), await fetch(`http://127.0.0.1:${port}/login.js`)];
    for (const headers of [login.headers, created.headers, list.headers, ...others.map((r) => r.headers)]) expect(headers.get('set-cookie')).toBeNull();
  });

  it('watches a project over /push: backfill, then the ack, then live events; a new socket watches again as a new sender', async () => {
    const { store, projectId, stream } = await setup();
    const secret = await redeem(web!.loginLink());
    const a = await connected(web!.port, secret);
    a.send({ op: 'broker.watch', id: 1, input: { projectId, afterSeq: 0 } });
    const ack = await a.next((f) => f.ack === 1);
    expect(ack.result).toEqual({ ok: true, value: { ok: true } });
    const backfill = a.frames
      .slice(0, a.frames.indexOf(ack))
      .filter((f) => f.channel === 'desk:events')
      .flatMap((f) => f.payload as StoredEvent[]);
    expect(backfill.map((e) => e.type)).toEqual(['project.created', 'agent.created']);
    const [live] = store.append({ project_id: projectId, agent_id: null, type: 'message.user', payload: { text: 'hi' } });
    stream().onEvent(live as StoredEvent);
    expect((await a.next((f) => f.channel === 'desk:event')).payload.id).toBe(live!.id);

    a.ws.close();
    await a.closed;
    const [missed] = store.append({ project_id: projectId, agent_id: null, type: 'message.user', payload: { text: 'while away' } });
    stream().onEvent(missed as StoredEvent);
    const b = await connected(web!.port, secret);
    b.send({ op: 'broker.watch', id: 1, input: { projectId, afterSeq: live!.id } });
    const ackB = await b.next((f) => f.ack === 1);
    const again = b.frames
      .slice(0, b.frames.indexOf(ackB))
      .filter((f) => f.channel === 'desk:events')
      .flatMap((f) => (f.payload as StoredEvent[]).map((e) => e.id));
    expect(again).toEqual([missed!.id]);
  });

  it('claims notifications in the stream hello only while the setting is on and a signed-in socket reports permission', async () => {
    const { streams, hello } = await setup();
    expect(streams[0]!.hello).toEqual({ client: 'web', notifications: false });
    const secret = await redeem(web!.loginLink());
    const a = await connected(web!.port, secret);
    a.send({ notifyPermission: 'default' });
    a.send({ notifyPermission: 'granted' });
    await until(() => hello() === true);
    const claimed = streams.length;
    const b = await connected(web!.port, secret);
    b.send({ notifyPermission: 'granted' });
    await sleep(100); // b's permission lands before a leaves, so the count goes 1, 2, 1 and never crosses zero
    a.ws.close();
    await a.closed;
    await sleep(50);
    expect(streams.length).toBe(claimed);
    expect((await rpc(web!.port, secret, 'app.updateSettings', { notifications: false })).body).toEqual({ ok: true, value: { notifications: false } });
    await until(() => hello() === false);
    await rpc(web!.port, secret, 'app.updateSettings', { notifications: true });
    await until(() => hello() === true);
    b.ws.close();
    await b.closed;
    await until(() => hello() === false);
  });

  it('sends new attention on desk:notify, in the desktop wording, to the sockets that may show it', async () => {
    const { store, projectId, stream, hello } = await setup();
    const secret = await redeem(web!.loginLink());
    const granted = await connected(web!.port, secret);
    granted.send({ notifyPermission: 'granted' });
    const denied = await connected(web!.port, secret);
    denied.send({ notifyPermission: 'denied' });
    await until(() => hello() === true);
    const [q] = store.append({ project_id: projectId, agent_id: null, type: 'question.asked', payload: { question: 'Which first?' } });
    stream().onEvent(q as StoredEvent);
    const note = await granted.next((f) => f.channel === 'desk:notify');
    expect(note.payload).toEqual([{ tag: `question:${q!.id}`, title: 'Launch: Desk has a question', body: 'Which first?', route: `#/attention?item=question%3A${q!.id}` }]);
    await sleep(50);
    expect(denied.frames.some((f) => f.channel === 'desk:notify')).toBe(false);
  });

  it('opens the login link through a 0600 redirect file that is deleted once the link is used', async () => {
    const opened: Array<{ file: string; mode: number; html: string }> = [];
    const { dataDir } = await setup({ open: true, opener: async (file) => void opened.push({ file, mode: statSync(file).mode & 0o777, html: readFileSync(file, 'utf8') }) });
    expect(opened).toHaveLength(1);
    const { file, mode, html } = opened[0]!;
    expect(dirname(file)).toBe(dataDir);
    expect(basename(file)).toMatch(/^web-login-[0-9a-f]{16}\.html$/);
    if (process.platform !== 'win32') expect(mode).toBe(0o600);
    const link = /content="0;url=([^"]+)"/.exec(html)![1]!;
    expect(link).toMatch(new RegExp(`^http://127\\.0\\.0\\.1:${web!.port}/login\\?code=`));
    expect(existsSync(file)).toBe(true);
    await redeem(link);
    expect(existsSync(file)).toBe(false);
  });

  it('warns in the terminal when a spent login link comes back', async () => {
    const { lines } = await setup();
    const link = web!.loginLink();
    await redeem(link);
    expect((await fetch(link)).status).toBe(401);
    expect(lines).toEqual([expect.stringContaining('a login link was used twice')]);
  });

  it('runs once per data dir, removes web.json when it stops, and replaces a stale one', async () => {
    const { dataDir, uiDir } = await setup();
    expect(readWebInfo(dataDir)).toEqual({ pid: process.pid, port: web!.port });
    await expect(startWebServer({ dataDir, port: 0, uiDir, log: () => {}, client: () => null })).rejects.toMatchObject({
      code: 'already_running',
      message: expect.stringContaining(`http://127.0.0.1:${web!.port}`),
    });
    await web!.close();
    web = undefined;
    expect(readWebInfo(dataDir)).toBeNull();
    writeWebInfo(dataDir, { pid: 2 ** 22 + 12345, port: 1 });
    web = await startWebServer({ dataDir, port: 0, uiDir, log: () => {}, client: () => null });
    expect(readWebInfo(dataDir)).toEqual({ pid: process.pid, port: web.port });
  });

  it('closes promptly while a signed-in /push socket and a --dev reload socket are open', async () => {
    await setup({ dev: true });
    const push = await connected(web!.port, await redeem(web!.loginLink()));
    const reload = openPush(web!.port, { path: '/__dev/reload' });
    await reload.opened;
    const closing = web!.close();
    web = undefined;
    await Promise.race([
      closing,
      sleep(2000).then(() => {
        throw new Error('close() was still waiting for the open sockets after 2 s');
      }),
    ]);
    expect(await push.closed).toBe(1006);
    expect(await reload.closed).toBe(1006);
  });

  it('closes what it opened when a step after listen fails (web.json is a folder)', async () => {
    const dataDir = tempDir('desk-web-fail-');
    mkdirSync(join(dataDir, 'web.json'));
    const port = await freePort();
    const before = watchers();
    await expect(startWebServer({ dataDir, port, uiDir: join(dataDir, 'ui', 'browser'), dev: true, log: () => {}, client: () => null })).rejects.toMatchObject({ code: 'EISDIR' });
    await expect(fetch(`http://127.0.0.1:${port}/healthz`)).rejects.toThrow();
    await until(() => watchers() === before);
  });

  it('says to choose another port with --port when the port is taken, and never moves on its own', async () => {
    const dataDir = tempDir('desk-web-port-');
    const blocker = createNetServer();
    await new Promise<void>((resolve) => blocker.listen(0, '127.0.0.1', () => resolve()));
    const port = (blocker.address() as AddressInfo).port;
    try {
      await expect(startWebServer({ dataDir, port, uiDir: dataDir, log: () => {}, client: () => null })).rejects.toMatchObject({
        code: 'port_in_use',
        message: expect.stringContaining('desk web --port'),
      });
      expect(readWebInfo(dataDir)).toBeNull();
    } finally {
      await new Promise<void>((resolve) => blocker.close(() => resolve()));
    }
  });
});
