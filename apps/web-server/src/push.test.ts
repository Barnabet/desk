import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { afterEach, describe, expect, it } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { HandlerContext } from '@desk/bff/server';
import { Sessions } from './auth';
import type { WebNotice } from './frames';
import { PushHub } from './push';
import { connected, openPush, refusedUpgrade, sleep, unusedContext, until } from './testing';
import { attachUpgrades } from './upgrade';

let server: Server | undefined;
let hub: PushHub | undefined;
afterEach(async () => {
  hub?.close();
  hub = undefined;
  const s = server;
  server = undefined;
  if (s) {
    s.closeAllConnections();
    await new Promise<void>((resolve) => s.close(() => resolve()));
  }
});

async function setup(watch: HandlerContext['broker']['watch'] = async () => {}) {
  const sessions = new Sessions();
  const secret = sessions.create();
  const dropped: number[] = [];
  const granted: number[] = [];
  const unwatched: Array<[number, string]> = [];
  const logs: string[] = [];
  const h = new PushHub({
    sessions,
    broker: { snapshot: () => initialGlobalState(), dropSender: (id) => void dropped.push(id) },
    ctx: (senderId) => ({ ...unusedContext(senderId), broker: { snapshot: () => initialGlobalState(), watch, unwatch: (s, p) => void unwatched.push([s, p]) } }),
    onGrantedChange: (n) => void granted.push(n),
    authTimeoutMs: 100,
    log: (message) => void logs.push(message),
  });
  hub = h;
  const s = createServer((_req, res) => res.writeHead(404).end());
  server = s;
  let port = 0;
  attachUpgrades(s, { port: () => port, routes: { '/push': h.handleUpgrade } });
  await new Promise<void>((resolve) => s.listen(0, '127.0.0.1', () => resolve()));
  port = (s.address() as AddressInfo).port;
  return { hub: h, sessions, secret, port, dropped, granted, unwatched, logs };
}

const NOTICE: WebNotice = { tag: 'question:1', title: 'Launch: Desk has a question', body: 'Which first?', route: '#/attention?item=question%3A1' };

describe('/push', () => {
  it('signs a socket in with the session in its first frame, then sends the global snapshot', async () => {
    const { port, secret } = await setup();
    const c = openPush(port);
    await c.opened;
    c.send({ session: secret });
    expect(await c.next(() => true)).toEqual({ channel: 'desk:global', payload: initialGlobalState() });
  });

  it('closes with 4401 on a wrong, malformed or late session frame, and when the session is revoked', async () => {
    const { port, secret, sessions } = await setup();
    const wrong = openPush(port);
    await wrong.opened;
    wrong.send({ session: 'A'.repeat(43) });
    expect(await wrong.closed).toBe(4401);
    const garbled = openPush(port);
    await garbled.opened;
    garbled.ws.send('hello');
    expect(await garbled.closed).toBe(4401);
    const silent = openPush(port);
    await silent.opened;
    expect(await silent.closed).toBe(4401);
    expect(silent.frames).toEqual([]);
    const live = await connected(port, secret);
    sessions.revoke(secret);
    expect(await live.closed).toBe(4401);
  });

  it('survives a bad frame before and after sign-in: ws closes the socket with 1009 and drops its sender', async () => {
    const { port, secret, dropped, logs } = await setup();
    const early = openPush(port);
    await early.opened;
    early.ws.on('error', () => {});
    early.ws.send('x'.repeat(70 * 1024));
    expect(await early.closed).toBe(1009);
    const a = await connected(port, secret); // sender 1
    a.ws.on('error', () => {});
    a.ws.send('x'.repeat(70 * 1024));
    expect(await a.closed).toBe(1009);
    await until(() => dropped.length === 1);
    expect(dropped).toEqual([1]);
    expect(logs).toHaveLength(2);
    for (const line of logs) expect(line).toMatch(/^push socket error: /);
    const b = await connected(port, secret);
    expect(b.frames[0]).toEqual({ channel: 'desk:global', payload: initialGlobalState() });
  });

  it('refuses upgrades without Host 127.0.0.1:<port> (421) or Origin http://127.0.0.1:<port> (403)', async () => {
    const { port } = await setup();
    expect(await refusedUpgrade(port, { host: `localhost:${port}` })).toBe(421);
    expect(await refusedUpgrade(port, { host: `[::1]:${port}` })).toBe(421);
    expect(await refusedUpgrade(port, { origin: null })).toBe(403);
    expect(await refusedUpgrade(port, { origin: `http://localhost:${port}` })).toBe(403);
    expect(await refusedUpgrade(port, { path: '/other' })).toBe(404);
  });

  it("broadcasts to signed-in sockets only, and sends a sender's pushes to its socket alone", async () => {
    const { hub: h, port, secret } = await setup();
    const a = await connected(port, secret); // sender 1
    const b = await connected(port, secret); // sender 2
    const lurker = openPush(port);
    await lurker.opened;
    h.broadcast('desk:global', { n: 1 });
    await a.next((f) => f.payload?.n === 1);
    await b.next((f) => f.payload?.n === 1);
    h.send(1, 'desk:event', { id: 5 });
    expect((await a.next((f) => f.channel === 'desk:event')).payload).toEqual({ id: 5 });
    h.send(99, 'desk:event', { id: 6 });
    await sleep(50);
    expect(b.frames.some((f) => f.channel === 'desk:event')).toBe(false);
    expect(lurker.frames).toEqual([]);
  });

  it('answers broker.watch after the frames the watch sent, and runs broker.unwatch', async () => {
    const box: { hub?: PushHub } = {};
    const { hub: h, port, secret, unwatched } = await setup(async (senderId, _projectId, afterSeq) => {
      box.hub!.send(senderId, 'desk:events', [{ id: afterSeq + 1 }]);
      await sleep(20);
      box.hub!.send(senderId, 'desk:events', [{ id: afterSeq + 2 }]);
    });
    box.hub = h;
    const a = await connected(port, secret);
    a.send({ op: 'broker.watch', id: 1, input: { projectId: 'p1', afterSeq: 3 } });
    const ack = await a.next((f) => f.ack === 1);
    expect(ack).toEqual({ ack: 1, result: { ok: true, value: { ok: true } } });
    const before = a.frames.slice(0, a.frames.indexOf(ack)).filter((f) => f.channel === 'desk:events');
    expect(before.map((f) => f.payload)).toEqual([[{ id: 4 }], [{ id: 5 }]]);
    a.send({ op: 'broker.unwatch', id: 2, input: { projectId: 'p1' } });
    expect(await a.next((f) => f.ack === 2)).toEqual({ ack: 2, result: { ok: true, value: { ok: true } } });
    expect(unwatched).toEqual([[1, 'p1']]);
  });

  it('refuses other operations and bad input, and closes a socket that sends a malformed frame', async () => {
    const { port, secret } = await setup();
    const a = await connected(port, secret);
    a.send({ op: 'projects.list', id: 3, input: {} });
    expect((await a.next((f) => f.ack === 3)).result).toMatchObject({ ok: false, error: { code: 'unknown_channel' } });
    a.send({ op: 'broker.watch', id: 4, input: { projectId: '' } });
    expect((await a.next((f) => f.ack === 4)).result).toMatchObject({ ok: false, error: { code: 'invalid_request' } });
    a.ws.send('not json');
    expect(await a.closed).toBe(4400);
  });

  it("drops a closed socket's sender, counts the sockets that may show notifications, and notifies only those", async () => {
    const { hub: h, port, secret, dropped, granted } = await setup();
    const a = await connected(port, secret); // sender 1
    const b = await connected(port, secret); // sender 2
    a.send({ notifyPermission: 'granted' });
    await until(() => granted.length === 1);
    b.send({ notifyPermission: 'default' });
    b.send({ notifyPermission: 'granted' });
    b.send({ notifyPermission: 'denied' });
    await until(() => granted.length === 3);
    expect(h.granted()).toBe(1);
    h.notify([NOTICE]);
    expect((await a.next((f) => f.channel === 'desk:notify')).payload).toEqual([NOTICE]);
    await sleep(50);
    expect(b.frames.some((f) => f.channel === 'desk:notify')).toBe(false);
    a.ws.close();
    await a.closed;
    await until(() => dropped.length === 1);
    expect(dropped).toEqual([1]);
    expect(granted).toEqual([1, 2, 1, 0]);
    expect(h.granted()).toBe(0);
  });
});
