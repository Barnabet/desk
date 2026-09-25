import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { DeskClient } from '@desk/client';
import { initialGlobalState } from '@desk/bff/contract';
import { createWebApp } from './app';
import { LoginCodes, Sessions } from './auth';
import { decodeBytes } from './codec';
import { WebSettingsStore } from './files';
import { listDirs } from './list-dirs';
import { MAX_RPC_BODY } from './rpc';
import { webHandlerContext, type WebDaemon } from './web-context';

const PORT = 7434;
const BYTES = new Uint8Array([0, 1, 2, 250, 255]);
const dirs: string[] = [];
afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});

/** A DeskClient whose fetch answers like deskd: file routes return bytes, POST /projects creates, anything else is a 404. */
function fakeDeskd() {
  const seen: string[] = [];
  const fetchLike = async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const url = new URL(String(input));
    const method = init?.method ?? 'GET';
    seen.push(`${method} ${url.pathname}`);
    if (method === 'GET' && (url.pathname.includes('/files/') || url.pathname.includes('/library/file/'))) {
      return new Response(BYTES, { headers: { 'content-type': 'application/octet-stream' } });
    }
    if (method === 'POST' && url.pathname === '/v1/projects') {
      const { name } = JSON.parse(String(init?.body)) as { name: string };
      return Response.json({ project: { id: 'P1', name } });
    }
    return Response.json({ error: { code: 'not_found', message: 'No such thing' } }, { status: 404 });
  };
  return { client: new DeskClient({ baseUrl: 'http://deskd.test', token: 't', fetch: fetchLike as typeof fetch }), seen };
}

const unusedDaemon = async (): Promise<never> => {
  throw new Error('not used');
};

function setup(o: { maxBodyBytes?: number } = {}) {
  const home = realpathSync(mkdtempSync(join(tmpdir(), 'desk-rpc-')));
  dirs.push(home);
  const dataDir = join(home, 'Library', 'Desk');
  const uiDir = join(home, 'ui');
  mkdirSync(join(home, 'code'));
  mkdirSync(dataDir, { recursive: true });
  mkdirSync(uiDir);
  writeFileSync(join(uiDir, 'index.html'), '<!doctype html><title>Desk</title>');
  const { client, seen } = fakeDeskd();
  const sessions = new Sessions();
  const secret = sessions.create();
  const broker = { getClient: () => client, snapshot: () => initialGlobalState(), watch: async () => {}, unwatch: () => {}, reconnect: async () => {} };
  const daemon: WebDaemon = { status: unusedDaemon, start: unusedDaemon, restart: unusedDaemon, stop: unusedDaemon };
  const app = createWebApp({
    port: () => PORT,
    sessions,
    codes: new LoginCodes(sessions),
    uiDir,
    dev: false,
    ctx: () => webHandlerContext({ dataDir, version: '1.0.0', platform: 'darwin', broker, daemon, settings: new WebSettingsStore(dataDir), openPath: async () => {} }, 0),
    web: { 'fs.listDirs': (input) => listDirs(input, { home, dataDir, sources: async () => [] }) },
    ...(o.maxBodyBytes ? { maxBodyBytes: o.maxBodyBytes } : {}),
  });
  /** POST /rpc/<op> as the web UI sends it; `null` removes a header. */
  const call = async (op: string, body: unknown, headers: Record<string, string | null> = {}) => {
    const merged: Record<string, string | null> = {
      host: `127.0.0.1:${PORT}`,
      origin: `http://127.0.0.1:${PORT}`,
      'content-type': 'application/json',
      'x-desk-session': secret,
      ...headers,
    };
    const res = await app.request(`/rpc/${op}`, {
      method: 'POST',
      headers: Object.fromEntries(Object.entries(merged).filter((e): e is [string, string] => e[1] !== null)),
      body: typeof body === 'string' ? body : JSON.stringify(body),
    });
    const text = await res.text();
    let raw: unknown = null;
    try {
      raw = JSON.parse(text);
    } catch {
      raw = null;
    }
    return { status: res.status, headers: res.headers, raw, body: decodeBytes(raw) as any };
  };
  return { call, seen, sessions, secret, home, dataDir };
}

describe('POST /rpc/:op', () => {
  it('runs an operation and answers with its IpcResult, never cached', async () => {
    const { call, seen } = setup();
    const r = await call('projects.create', { name: 'Launch', goal: 'g' });
    expect(r.status).toBe(200);
    expect(r.body).toEqual({ ok: true, value: { project: { id: 'P1', name: 'Launch' } } });
    expect(r.headers.get('content-type')).toBe('application/json; charset=utf-8');
    expect(r.headers.get('cache-control')).toBe('no-store');
    expect(r.headers.get('set-cookie')).toBeNull();
    expect(seen).toEqual(['POST /v1/projects']);
  });

  it('returns the five byte operations as $bytes that decode to the same bytes', async () => {
    const { call, seen } = setup();
    const ops: Array<[string, object]> = [
      ['threads.file', { id: 't1', path: 'a.bin' }],
      ['library.file', { projectId: 'p1', path: 'a.bin' }],
      ['skills.file', { name: 's', path: 'a.bin' }],
      ['skills.versionFile', { name: 's', version: 2, path: 'a.bin' }],
      ['catalog.file', { id: 'c', path: 'a.bin' }],
    ];
    for (const [op, input] of ops) {
      const r = await call(op, input);
      expect(r.status, op).toBe(200);
      expect(r.raw, op).toEqual({ ok: true, value: { $bytes: 'AAEC+v8=' } });
      expect(r.body.value, op).toEqual(BYTES);
    }
    expect(seen).toEqual([
      'GET /v1/threads/t1/files/raw/a.bin',
      'GET /v1/projects/p1/library/file/a.bin',
      'GET /v1/skills/s/files/a.bin',
      'GET /v1/skills/s/versions/2/files/a.bin',
      'GET /v1/catalog/c/files/a.bin',
    ]);
  });

  it('decodes $bytes input before validation', async () => {
    const { call } = setup();
    expect((await call('app.saveFile', { name: 'a.bin', data: { $bytes: 'AAEC' } })).body).toMatchObject({ ok: false, error: { code: 'not_offered' } });
    const plain = await call('app.saveFile', { name: 'a.bin', data: 'AAEC' });
    expect(plain.status).toBe(400);
    expect(plain.body).toMatchObject({ ok: false, error: { code: 'invalid_request' } });
    const bad = await call('app.saveFile', { name: 'a.bin', data: { $bytes: '%%%' } });
    expect(bad.status).toBe(400);
    expect(bad.body).toMatchObject({ ok: false, error: { code: 'invalid_request' } });
  });

  it('refuses bad input with 400 and unknown operations with 404, in channels and webChannels', async () => {
    const { call } = setup();
    for (const [op, input] of [['projects.get', {}], ['fs.listDirs', { hidden: 'yes' }]] as const) {
      const r = await call(op, input);
      expect(r.status, op).toBe(400);
      expect(r.body, op).toMatchObject({ ok: false, error: { code: 'invalid_request' } });
    }
    for (const op of ['nope.op', '__proto__', 'constructor', 'toString']) {
      const r = await call(op, {});
      expect(r.status, op).toBe(404);
      expect(r.body, op).toMatchObject({ ok: false, error: { code: 'unknown_channel' } });
    }
    const notJson = await call('health', '{"oops"');
    expect(notJson.status).toBe(400);
    expect(notJson.body).toMatchObject({ ok: false, error: { code: 'invalid_request' } });
  });

  it('runs broker.watch and broker.unwatch only on /push, and broker.snapshot here', async () => {
    const { call } = setup();
    for (const op of ['broker.watch', 'broker.unwatch']) {
      const r = await call(op, { projectId: 'p1', afterSeq: 0 });
      expect(r.status, op).toBe(400);
      expect(r.body, op).toMatchObject({ ok: false, error: { code: 'push_only' } });
    }
    expect((await call('broker.snapshot', {})).body).toEqual({ ok: true, value: initialGlobalState() });
  });

  it("passes deskd's errors through as the desktop app does", async () => {
    const { call } = setup();
    const r = await call('projects.get', { id: 'P9' });
    expect(r.status).toBe(200);
    expect(r.body).toEqual({ ok: false, error: { code: 'not_found', message: 'No such thing', status: 404 } });
  });

  it('lists folders with fs.listDirs', async () => {
    const { call, home, dataDir } = setup();
    expect((await call('fs.listDirs', {})).body).toEqual({
      ok: true,
      value: {
        path: home,
        parent: null,
        dirs: [
          { name: 'code', path: join(home, 'code') },
          { name: 'Library', path: join(home, 'Library') },
          { name: 'ui', path: join(home, 'ui') },
        ],
      },
    });
    expect((await call('fs.listDirs', { path: dataDir })).body).toMatchObject({ ok: false, error: { code: 'not_allowed' } });
  });

  it('needs the session secret: 401 without it, with a wrong one, or once it is revoked', async () => {
    const { call, sessions, secret } = setup();
    for (const session of [null, 'wrong', 'A'.repeat(43)]) {
      const r = await call('health', {}, { 'x-desk-session': session });
      expect(r.status, String(session)).toBe(401);
      expect(r.body).toMatchObject({ ok: false, error: { code: 'unauthorized' } });
    }
    sessions.revoke(secret);
    expect((await call('health', {})).status).toBe(401);
  });

  it('needs Origin http://127.0.0.1:<port> (403), Host 127.0.0.1:<port> (421) and JSON (415)', async () => {
    const { call } = setup();
    for (const origin of [null, 'null', `http://localhost:${PORT}`, `http://127.0.0.1:${PORT + 1}`, 'https://evil.example']) {
      expect((await call('health', {}, { origin })).status, String(origin)).toBe(403);
    }
    for (const host of [`localhost:${PORT}`, `[::1]:${PORT}`, `127.0.0.1:${PORT + 1}`]) {
      expect((await call('health', {}, { host })).status, host).toBe(421);
    }
    for (const type of ['text/plain', 'application/x-www-form-urlencoded', 'multipart/form-data']) {
      const r = await call('health', {}, { 'content-type': type });
      expect(r.status, type).toBe(415);
      expect(r.body).toMatchObject({ ok: false, error: { code: 'unsupported_media_type' } });
    }
    expect((await call('health', {}, { 'content-type': 'application/json; charset=utf-8' })).status).toBe(200);
  });

  it('takes a 25 MB library upload (about 34 MB of JSON) and refuses bodies over 40 MB', async () => {
    const { call, seen } = setup();
    expect(MAX_RPC_BODY).toBe(40 * 1024 * 1024);
    const upload = await call('library.upload', { projectId: 'p1', file: { name: 'big.bin', content_base64: 'A'.repeat(Math.ceil((25 * 1024 * 1024) / 3) * 4) } });
    expect(upload.status).toBe(200);
    expect(upload.body).toMatchObject({ ok: false, error: { code: 'not_found' } });
    expect(seen).toEqual(['POST /v1/projects/p1/library']);
    const huge = await call('projects.send', { id: 'p1', text: 'x'.repeat(MAX_RPC_BODY) });
    expect(huge.status).toBe(413);
    expect(huge.body).toMatchObject({ ok: false, error: { code: 'too_large' } });
    expect(seen).toHaveLength(1);
  });
});
