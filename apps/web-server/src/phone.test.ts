import { mkdtempSync, rmSync } from 'node:fs';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { channels, initialGlobalState } from '@desk/bff/contract';
import { createWebApp } from './app';
import { LoginCodes, Sessions } from './auth';
import { DeviceStore, writePairingCodes, writePendingCode } from './devices';
import { PAIR_GUESSES } from './app';
import { PHONE_OPS, phoneRefusal } from './phone-policy';
import { PushHub } from './push';
import { contentSecurityPolicy, hostAllowed, originAllowed, parseRemoteUrl } from './security';
import { findInlineCode } from './static';
import { openPush, refusedUpgrade, unusedContext } from './testing';
import { attachUpgrades } from './upgrade';

const PORT = 7434;
const REMOTE = parseRemoteUrl('https://mac.tail1234.ts.net')!;
const PHONE = { host: REMOTE.host, origin: REMOTE.origin, 'content-type': 'application/json' };
const dirs: string[] = [];
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
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});

function setup() {
  const dataDir = mkdtempSync(join(tmpdir(), 'desk-phone-'));
  dirs.push(dataDir);
  const uiDir = join(dataDir, 'ui');
  const sessions = new Sessions();
  const devices = new DeviceStore(dataDir);
  const paired: string[] = [];
  const app = createWebApp({
    port: () => PORT,
    remote: () => REMOTE,
    sessions,
    devices,
    codes: new LoginCodes(sessions),
    dataDir,
    uiDir,
    dev: false,
    ctx: () => ({
      ...unusedContext(),
      client: () => ({ projects: { list: async () => [{ id: 'P1' }] } }) as never,
      app: { ...unusedContext().app, info: () => ({ version: '1.0.0', platform: 'darwin', packaged: false, dataDir: '/d' }) },
    }),
    web: { 'fs.listDirs': async () => ({ path: '/h', parent: null, dirs: [] }) },
    onPaired: (name) => void paired.push(name),
  });
  const secretOf = async (res: Response) => /<meta name="desk-session" content="([^"]+)">/.exec(await res.text())?.[1];
  const pair = async (name = 'iPhone') => {
    const res = await app.request(`/pair?code=${writePendingCode(dataDir, 'phone', { name })}`, { headers: { host: REMOTE.host } });
    expect(res.status).toBe(200);
    return (await secretOf(res))!;
  };
  const rpc = (op: string, secret: string, body: unknown = {}, headers: Record<string, string> = PHONE) =>
    app.request(`/rpc/${op}`, { method: 'POST', headers: { ...headers, 'x-desk-session': secret }, body: JSON.stringify(body) });
  return { app, dataDir, sessions, devices, paired, pair, rpc, secretOf };
}

describe('the remote address', () => {
  it('accepts only an https:// origin with no path, query, fragment or credentials', () => {
    expect(parseRemoteUrl('https://mac.tail1234.ts.net/')).toEqual({ host: 'mac.tail1234.ts.net', origin: 'https://mac.tail1234.ts.net' });
    expect(parseRemoteUrl('https://mac.tail1234.ts.net:8443')).toEqual({ host: 'mac.tail1234.ts.net:8443', origin: 'https://mac.tail1234.ts.net:8443' });
    for (const bad of ['http://mac.tail1234.ts.net', 'https://mac.ts.net/desk', 'https://u:p@mac.ts.net', 'https://mac.ts.net/?a=1', 'mac.ts.net', ''])
      expect(parseRemoteUrl(bad), bad).toBeNull();
  });

  it('is answered besides 127.0.0.1, and the CSP lets the page open its secure socket', () => {
    expect(hostAllowed(REMOTE.host, PORT, REMOTE)).toBe(true);
    expect(hostAllowed(REMOTE.host, PORT, null)).toBe(false);
    expect(hostAllowed('evil.example', PORT, REMOTE)).toBe(false);
    expect(originAllowed(REMOTE.origin, PORT, REMOTE)).toBe(true);
    expect(originAllowed(`http://${REMOTE.host}`, PORT, REMOTE)).toBe(false);
    expect(contentSecurityPolicy(PORT, REMOTE)).toContain(`connect-src 'self' ws://127.0.0.1:${PORT} wss://${REMOTE.host}`);
  });
});

describe('pairing a phone', () => {
  it('opens a no-store page that stores the secret and skips onboarding, once', async () => {
    const { app, dataDir, devices, paired } = setup();
    const code = writePendingCode(dataDir, 'phone', { name: 'iPhone' });
    const res = await app.request(`/pair?code=${code}`, { headers: { host: REMOTE.host } });
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(res.headers.get('content-security-policy')).toBe(contentSecurityPolicy(PORT, REMOTE));
    const html = await res.text();
    expect(html).toContain('<script src="/pair.js"></script>');
    expect(findInlineCode(html)).toEqual([]);
    expect(devices.list().map((d) => d.name)).toEqual(['iPhone']);
    expect(paired).toEqual(['iPhone']);
    const js = await (await app.request('/pair.js', { headers: { host: REMOTE.host } })).text();
    expect(js).toContain("localStorage.setItem('desk.onboarded', '1')");

    const again = await app.request(`/pair?code=${code}`, { headers: { host: REMOTE.host } });
    expect(again.status).toBe(401);
    expect(await again.text()).toContain('desk web pair');
    expect(devices.list()).toHaveLength(1);
  });

  it('never spends a code on HEAD, and a browser code does not pair a phone', async () => {
    const { app, dataDir, devices } = setup();
    const code = writePendingCode(dataDir, 'phone');
    expect((await app.request(`/pair?code=${code}`, { method: 'HEAD', headers: { host: REMOTE.host } })).status).toBe(200);
    expect((await app.request(`/pair?code=${writePendingCode(dataDir, 'browser')}`, { headers: { host: REMOTE.host } })).status).toBe(401);
    expect((await app.request(`/pair?code=${code}`, { headers: { host: REMOTE.host } })).status).toBe(200);
    expect(devices.list().map((d) => d.name)).toEqual(['Phone']);
  });

  it('signs a browser on this Mac in with a code desk web login wrote', async () => {
    const { app, dataDir, sessions, secretOf } = setup();
    const res = await app.request(`/login?code=${writePendingCode(dataDir, 'browser')}`, { headers: { host: `127.0.0.1:${PORT}` } });
    expect(res.status).toBe(200);
    expect(sessions.valid(await secretOf(res))).toBe(true);
    expect((await app.request(`/login?code=${writePendingCode(dataDir, 'phone')}`, { headers: { host: `127.0.0.1:${PORT}` } })).status).toBe(401);
  });
});

describe('pairing a Home Screen app with the short code', () => {
  const post = (app: ReturnType<typeof setup>['app'], code: unknown, origin = REMOTE.origin) =>
    app.request('/pair', { method: 'POST', headers: { host: REMOTE.host, origin, 'content-type': 'application/json' }, body: JSON.stringify({ code }) });

  it('pairs once with the typed code and answers the secret as JSON', async () => {
    const { app, dataDir, devices, paired } = setup();
    const { short } = writePairingCodes(dataDir, { name: 'iPhone' });
    const res = await post(app, short.toLowerCase());
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('no-store');
    const body = (await res.json()) as { ok: true; value: { secret: string } };
    expect(devices.who(body.value.secret)?.name).toBe('iPhone');
    expect(paired).toEqual(['iPhone']);
    expect((await post(app, short)).status).toBe(401);
  });

  it('refuses another origin, a QR code, and too many wrong codes, even a right one after them', async () => {
    const { app, dataDir } = setup();
    const { code, short } = writePairingCodes(dataDir);
    expect((await post(app, short, 'https://evil.example')).status).toBe(403);
    expect((await post(app, code)).status).toBe(401);
    for (let i = 1; i < PAIR_GUESSES; i++) expect((await post(app, 'ZZZZ-ZZZZ')).status).toBe(401);
    expect((await post(app, short)).status).toBe(429);
  });
});

describe('what a paired phone may do', () => {
  it('reads and talks, but is refused what stays on the Mac', async () => {
    const { pair, rpc } = setup();
    const secret = await pair();
    const listed = await rpc('projects.list', secret);
    expect(listed.status).toBe(200);
    expect(await listed.json()).toEqual({ ok: true, value: [{ id: 'P1' }] });
    for (const op of ['config.patch', 'config.saveEndpoint', 'projects.update', 'projects.addSource', 'automations.setGrants', 'daemon.stop', 'skills.import', 'fs.listDirs', 'app.updateSettings', 'threads.requestReview', 'threads.accept', 'threads.waiveFinding']) {
      const res = await rpc(op, secret);
      expect(res.status, op).toBe(403);
      expect(((await res.json()) as { error: { code: string } }).error.code, op).toBe('not_on_phone');
    }
    const remember = await rpc('approvals.resolve', secret, { id: 'a1', decision: 'approved', remember: true });
    expect(remember.status).toBe(403);
  });

  it('is signed out once unpaired, and a browser session is not limited', async () => {
    const { pair, rpc, devices, sessions } = setup();
    const secret = await pair();
    devices.revoke(devices.list()[0]!.id);
    expect((await rpc('projects.list', secret)).status).toBe(401);
    const browser = sessions.create();
    const res = await rpc('fs.listDirs', browser, {}, { host: `127.0.0.1:${PORT}`, origin: `http://127.0.0.1:${PORT}`, 'content-type': 'application/json' });
    expect(res.status).toBe(200);
  });

  it('refuses a cross-origin call on the remote host', async () => {
    const { pair, rpc } = setup();
    const secret = await pair();
    expect((await rpc('projects.list', secret, {}, { ...PHONE, origin: 'https://evil.example' })).status).toBe(403);
  });

  it('lists only operations that exist, and refuses every operation it does not list', () => {
    for (const op of PHONE_OPS) expect(Object.hasOwn(channels, op), op).toBe(true);
    for (const op of Object.keys(channels)) expect(phoneRefusal(op, {}) === null).toBe(PHONE_OPS.has(op));
    expect(phoneRefusal('approvals.resolve', { id: 'a', decision: 'approved' })).toBeNull();
  });
});

describe('/push for a paired phone', () => {
  async function pushSetup() {
    const { devices, dataDir } = setup();
    const sessions = new Sessions();
    const h = new PushHub({
      sessions,
      devices,
      recheckMs: 60_000,
      broker: { snapshot: () => initialGlobalState(), dropSender: () => {} },
      ctx: (senderId) => ({ ...unusedContext(senderId), broker: { snapshot: () => initialGlobalState(), watch: async () => {}, unwatch: () => {} } }),
    });
    hub = h;
    const s = createServer((_req, res) => res.writeHead(404).end());
    server = s;
    let port = 0;
    attachUpgrades(s, { port: () => port, remote: () => REMOTE, routes: { '/push': h.handleUpgrade } });
    await new Promise<void>((resolve) => s.listen(0, '127.0.0.1', () => resolve()));
    port = (s.address() as AddressInfo).port;
    return { hub: h, devices, dataDir, port };
  }

  it('signs in on the remote host, and closes with 4401 once the phone is unpaired', async () => {
    const { hub: h, devices, port } = await pushSetup();
    const { device, secret } = devices.add('iPhone');
    const c = openPush(port, { host: REMOTE.host, origin: REMOTE.origin });
    await c.opened;
    c.send({ session: secret });
    await c.next((f) => f.channel === 'desk:global');
    c.send({ op: 'broker.watch', id: 1, input: { projectId: 'P1', afterSeq: 0 } });
    expect((await c.next((f) => f.ack === 1)).result.ok).toBe(true);
    devices.revoke(device.id);
    h.recheckPhones();
    expect(await c.closed).toBe(4401);
  });

  it('cuts long tool texts in what it pushes to a phone, and not to a browser', async () => {
    const { hub: h, devices, port } = await pushSetup();
    const { secret } = devices.add('iPhone');
    const c = openPush(port, { host: REMOTE.host, origin: REMOTE.origin });
    await c.opened;
    c.send({ session: secret });
    await c.next((f) => f.channel === 'desk:global');
    const content = 'x'.repeat(10_000);
    const event = { id: 1, ts: 't', project_id: 'P1', agent_id: 'A1', type: 'tool.result', payload: { run_id: 'r', tool_call_id: 't', name: 'bash', status: 'ok', content } };
    h.broadcast('desk:events', [event]);
    const f = await c.next((x) => x.channel === 'desk:events');
    expect(f.payload[0].payload.content.length).toBeLessThan(3000);
    expect(event.payload.content.length).toBe(10_000);
  });

  it("leaves the threads' transcripts out of a phone's backfill, keeping Desk's own and every live event", async () => {
    const { devices } = setup();
    const sessions = new Sessions();
    const e = (id: number, agent: string, type: string, payload: object = {}) => ({ id, ts: 't', project_id: 'P1', agent_id: agent, type, payload });
    const backfill = [
      e(1, 'D', 'agent.created', { role: 'desk' }),
      e(2, 'D', 'tool.call', { run_id: 'r', tool_call_id: 'c', name: 'spawn', arguments: '{}' }),
      e(3, 'T', 'agent.created', { role: 'thread' }),
      e(4, 'T', 'tool.call', { run_id: 'r', tool_call_id: 'c', name: 'bash', arguments: '{}' }),
      e(5, 'T', 'tool.result', { run_id: 'r', tool_call_id: 'c', name: 'bash', status: 'ok', content: 'ok' }),
      e(6, 'T', 'agent.status_changed', { status: 'done' }),
    ];
    let hubRef: PushHub | null = null;
    const h = new PushHub({
      sessions,
      devices,
      recheckMs: 60_000,
      broker: { snapshot: () => initialGlobalState(), dropSender: () => {} },
      ctx: (senderId) => ({
        ...unusedContext(senderId),
        client: () => ({ projects: { get: async () => ({ desk: { id: 'D' } }) } }) as never,
        broker: {
          snapshot: () => initialGlobalState(),
          watch: async () => void hubRef!.send(senderId, 'desk:events', backfill),
          unwatch: () => {},
        },
      }),
    });
    hubRef = h;
    hub = h;
    const s = createServer((_req, res) => res.writeHead(404).end());
    server = s;
    let port = 0;
    attachUpgrades(s, { port: () => port, remote: () => REMOTE, routes: { '/push': h.handleUpgrade } });
    await new Promise<void>((resolve) => s.listen(0, '127.0.0.1', () => resolve()));
    port = (s.address() as AddressInfo).port;

    const phone = openPush(port, { host: REMOTE.host, origin: REMOTE.origin });
    const browser = openPush(port, { host: `127.0.0.1:${port}`, origin: `http://127.0.0.1:${port}` });
    await Promise.all([phone.opened, browser.opened]);
    phone.send({ session: devices.add('iPhone').secret });
    browser.send({ session: sessions.create() });
    await Promise.all([phone.next((f) => f.channel === 'desk:global'), browser.next((f) => f.channel === 'desk:global')]);
    for (const c of [phone, browser]) c.send({ op: 'broker.watch', id: 1, input: { projectId: 'P1', afterSeq: 0 } });
    await Promise.all([phone.next((f) => f.ack === 1), browser.next((f) => f.ack === 1)]);
    const ids = (c: typeof phone) => c.frames.filter((f) => f.channel === 'desk:events').flatMap((f) => (f.payload as Array<{ id: number }>).map((x) => x.id));
    expect(ids(phone)).toEqual([1, 2, 3, 6]);
    expect(ids(browser)).toEqual([1, 2, 3, 4, 5, 6]);
    // Live events are never left out.
    h.broadcast('desk:event', e(7, 'T', 'tool.call', { run_id: 'r', tool_call_id: 'd', name: 'bash', arguments: '{}' }));
    expect((await phone.next((f) => f.channel === 'desk:event')).payload.id).toBe(7);
  });

  it('refuses an upgrade from another origin on the remote host', async () => {
    const { port } = await pushSetup();
    expect(await refusedUpgrade(port, { host: REMOTE.host, origin: 'https://evil.example' })).toBe(403);
    expect(await refusedUpgrade(port, { host: 'evil.example', origin: REMOTE.origin })).toBe(421);
  });
});
