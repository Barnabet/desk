import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createWebApp } from './app';
import { CODE_TTL_MS, LoginCodes, Sessions } from './auth';
import { contentSecurityPolicy } from './security';
import { findInlineCode } from './static';
import { unusedContext } from './testing';

const PORT = 7434;
const HOST = { host: `127.0.0.1:${PORT}` };
const RPC = { ...HOST, origin: `http://127.0.0.1:${PORT}`, 'content-type': 'application/json' };
const dirs: string[] = [];
afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});

function setup(o: { dev?: boolean } = {}) {
  const uiDir = mkdtempSync(join(tmpdir(), 'desk-web-app-'));
  dirs.push(uiDir);
  writeFileSync(join(uiDir, 'index.html'), '<!doctype html><html><head><title>Desk</title></head><body><desk-root></desk-root><script src="main-AB12.js" type="module"></script></body></html>');
  let now = 1_000_000;
  const sessions = new Sessions();
  const codes = new LoginCodes(sessions, { now: () => now });
  const replays: number[] = [];
  const app = createWebApp({
    port: () => PORT,
    sessions,
    codes,
    uiDir,
    dev: o.dev ?? false,
    ctx: () => ({ ...unusedContext(), app: { ...unusedContext().app, info: () => ({ version: '1.0.0', platform: 'darwin', packaged: false, dataDir: '/d' }) } }),
    web: { 'fs.listDirs': async () => ({ path: '/h', parent: null, dirs: [] }) },
    onReplay: () => void replays.push(1),
  });
  const secretOf = async (res: Response) => /<meta name="desk-session" content="([^"]+)">/.exec(await res.text())?.[1];
  return { app, sessions, codes, replays, secretOf, tick: (ms: number) => void (now += ms) };
}

describe('the desk web app', () => {
  it('answers /healthz without a session, and only on Host 127.0.0.1:<port>', async () => {
    const { app } = setup();
    const res = await app.request('/healthz', { headers: HOST });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
    for (const path of ['/healthz', '/', '/login.js']) expect((await app.request(path, { headers: { host: `localhost:${PORT}` } })).status, path).toBe(421);
  });

  it('signs in with a one-time code: a no-store page with the secret in a meta tag and one external script', async () => {
    const { app, codes, sessions, secretOf } = setup();
    const res = await app.request(`/login?code=${codes.issue()}`, { headers: HOST });
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(res.headers.get('content-type')).toBe('text/html; charset=utf-8');
    const html = await res.clone().text();
    expect(html).toContain('<script src="/login.js"></script>');
    expect(findInlineCode(html)).toEqual([]);
    const secret = await secretOf(res);
    expect(sessions.valid(secret)).toBe(true);
    const js = await app.request('/login.js', { headers: HOST });
    expect(js.headers.get('content-type')).toBe('text/javascript; charset=utf-8');
    expect(await js.text()).toContain('localStorage.setItem("desk.session"');
    const call = await app.request('/rpc/app.info', { method: 'POST', headers: { ...RPC, 'x-desk-session': secret! }, body: '{}' });
    expect(await call.json()).toEqual({ ok: true, value: { version: '1.0.0', platform: 'darwin', packaged: false, dataDir: '/d' } });
  });

  it('refuses a spent code and signs out the session it opened; refuses expired and unknown codes', async () => {
    const { app, codes, sessions, replays, secretOf, tick } = setup();
    const code = codes.issue();
    const secret = await secretOf(await app.request(`/login?code=${code}`, { headers: HOST }));
    const again = await app.request(`/login?code=${code}`, { headers: HOST });
    expect(again.status).toBe(401);
    expect(await again.text()).toContain('This login link has expired or was already used.');
    expect(sessions.valid(secret)).toBe(false);
    expect(replays).toEqual([1]);
    const late = codes.issue();
    tick(CODE_TTL_MS);
    expect((await app.request(`/login?code=${late}`, { headers: HOST })).status).toBe(401);
    expect((await app.request('/login?code=nope', { headers: HOST })).status).toBe(401);
    expect((await app.request('/login', { headers: HOST })).status).toBe(401);
    expect(replays).toEqual([1]);
  });

  it('puts the security headers on every response and never sets a cookie', async () => {
    const { app, codes } = setup();
    const responses = [
      await app.request('/', { headers: HOST }),
      await app.request('/healthz', { headers: HOST }),
      await app.request(`/login?code=${codes.issue()}`, { headers: HOST }),
      await app.request('/login.js', { headers: HOST }),
      await app.request('/missing.js', { headers: HOST }),
      await app.request('/healthz', { headers: { host: `localhost:${PORT}` } }),
      await app.request('/rpc/health', { method: 'POST', headers: RPC, body: '{}' }),
      await app.request('/rpc/health', { method: 'POST', headers: HOST, body: '{}' }),
    ];
    expect(responses.map((r) => r.status)).toEqual([200, 200, 200, 200, 404, 421, 401, 403]);
    for (const r of responses) {
      expect(r.headers.get('content-security-policy')).toBe(contentSecurityPolicy(PORT));
      expect(r.headers.get('x-content-type-options')).toBe('nosniff');
      expect(r.headers.get('referrer-policy')).toBe('no-referrer');
      expect(r.headers.get('cross-origin-opener-policy')).toBe('same-origin');
      expect(r.headers.get('cross-origin-resource-policy')).toBe('same-origin');
      expect(r.headers.get('set-cookie')).toBeNull();
    }
  });

  it('serves the reload script and adds it to index.html only under --dev', async () => {
    const plain = setup();
    expect((await plain.app.request('/__dev/reload.js', { headers: HOST })).status).toBe(404);
    const dev = setup({ dev: true });
    const js = await dev.app.request('/__dev/reload.js', { headers: HOST });
    expect(js.status).toBe(200);
    expect(await js.text()).toContain('/__dev/reload');
    expect(await (await dev.app.request('/', { headers: HOST })).text()).toContain('<script src="/__dev/reload.js"></script></body>');
  });
});
