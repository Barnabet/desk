import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { scrubbedEnv, type Keychain } from '@desk/core';
import { startDaemon, type RunningDaemon } from './daemon';
import { createLogger } from './logger';

const SERPER_KEY = 'serper-secret-key-0042';
const FIRECRAWL_KEY = 'fc-secret-key-0042';

/** A fake Serper and Firecrawl in one: accepts only the two keys above. */
let providers: Server;
let providerBase: string;
beforeAll(async () => {
  providers = createServer((req, res) => {
    req.resume();
    req.on('end', () => {
      const json = (status: number, body: unknown) => res.writeHead(status, { 'content-type': 'application/json' }).end(JSON.stringify(body));
      if (req.url?.startsWith('/serper/'))
        return req.headers['x-api-key'] === SERPER_KEY ? json(200, { organic: [{ title: 'Desk', link: 'https://desk.dev', snippet: 'ok' }] }) : json(403, { message: 'Unauthorized.' });
      if (req.url === '/fc/scrape')
        return req.headers.authorization === `Bearer ${FIRECRAWL_KEY}`
          ? json(200, { success: true, data: { markdown: '# Example', metadata: { url: 'https://example.com', statusCode: 200 } } })
          : json(401, { success: false, error: 'Unauthorized: Invalid token' });
      json(404, {});
    });
  });
  await new Promise<void>((r) => providers.listen(0, '127.0.0.1', r));
  providerBase = `http://127.0.0.1:${(providers.address() as AddressInfo).port}`;
});
afterAll(() => providers.close());

let dir: string;
let daemon: RunningDaemon | undefined;
afterEach(async () => {
  await daemon?.stop();
  daemon = undefined;
  await rm(dir, { recursive: true, force: true });
});

function memKeychain(): Keychain & { secret: string | null } {
  const k = { secret: null as string | null, get: () => k.secret, set: (s: string) => void (k.secret = s), delete: () => void (k.secret = null) };
  return k;
}

async function start(webKeychains: { serper: Keychain; firecrawl: Keychain } | null, env: NodeJS.ProcessEnv = {}) {
  dir = await mkdtemp(join(tmpdir(), 'desk-webkeys-'));
  daemon = await startDaemon({
    dataDir: dir,
    port: 0,
    env,
    home: dir,
    keychain: null,
    webKeychains,
    webEndpoints: { serper: `${providerBase}/serper`, firecrawl: `${providerBase}/fc` },
    sandboxAvailable: false,
    log: createLogger(join(dir, 'logs', 'deskd.log'), false),
  });
  return async (method: string, path: string, body?: unknown) => {
    const res = await fetch(`http://127.0.0.1:${daemon!.port}/v1${path}`, {
      method,
      headers: { authorization: `Bearer ${daemon!.token}`, ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const text = await res.text();
    return { status: res.status, text, body: text ? JSON.parse(text) : null };
  };
}

describe('web key routes', () => {
  it('stores, tests and removes keys without ever returning them', async () => {
    const kcs = { serper: memKeychain(), firecrawl: memKeychain() };
    const api = await start(kcs);
    expect((await api('GET', '/config/web-keys')).body).toEqual({ serper: { set: false, source: null }, firecrawl: { set: false, source: null } });

    expect((await api('POST', '/config/web-keys/serper/test', { key: SERPER_KEY })).body).toEqual({ ok: true });
    const bad = await api('POST', '/config/web-keys/serper/test', { key: 'wrong-serper-key-9' });
    expect(bad.body.ok).toBe(false);
    expect(bad.body.error).toMatch(/403/);
    expect(bad.text).not.toContain('wrong-serper-key-9');

    const saved = await api('PUT', '/config/web-keys/serper', { key: `  ${SERPER_KEY}\n` });
    expect(saved.body).toEqual({ set: true, source: 'keychain' });
    expect(kcs.serper.secret).toBe(SERPER_KEY);
    expect((await api('POST', '/config/web-keys/serper/test')).body).toEqual({ ok: true });

    await api('PUT', '/config/web-keys/firecrawl', { key: FIRECRAWL_KEY });
    expect((await api('POST', '/config/web-keys/firecrawl/test')).body).toEqual({ ok: true });
    const fcBad = await api('POST', '/config/web-keys/firecrawl/test', { key: 'fc-wrong-key-123' });
    expect(fcBad.body).toMatchObject({ ok: false });
    expect(fcBad.text).not.toContain('fc-wrong-key-123');

    // The running runtime reads the saved key at once.
    expect(daemon!.runtime.services.web.key('serper')).toBe(SERPER_KEY);

    expect((await api('DELETE', '/config/web-keys/serper')).body).toEqual({ set: false, source: null });
    expect(kcs.serper.secret).toBeNull();
    expect(daemon!.runtime.services.web.key('serper')).toBeNull();

    const everything = [
      saved.text,
      (await api('GET', '/config/web-keys')).text,
      await readFile(join(dir, 'logs', 'deskd.log'), 'utf8').catch(() => ''),
      await readFile(join(dir, 'config.json'), 'utf8').catch(() => ''),
      await readFile(join(dir, 'daemon.json'), 'utf8').catch(() => ''),
      JSON.stringify(daemon!.store.list()),
      JSON.stringify(scrubbedEnv(dir)),
    ].join('\n');
    expect(everything).not.toContain(SERPER_KEY);
    expect(everything).not.toContain(FIRECRAWL_KEY);
  });

  it('reports env keys, refuses unknown providers and unsafe keys, and answers 501 without a Keychain', async () => {
    const api = await start(null, { DESK_FIRECRAWL_API_KEY: FIRECRAWL_KEY });
    expect((await api('GET', '/config/web-keys')).body).toEqual({ serper: { set: false, source: null }, firecrawl: { set: true, source: 'env' } });
    expect((await api('POST', '/config/web-keys/firecrawl/test')).body).toEqual({ ok: true });
    expect((await api('PUT', '/config/web-keys/serper', { key: SERPER_KEY })).status).toBe(501);
    expect((await api('PUT', '/config/web-keys/serper', { key: 'has "quotes" in it' })).status).toBe(400);
    expect((await api('PUT', '/config/web-keys/brave', { key: SERPER_KEY })).status).toBe(404);
    expect((await api('POST', '/config/web-keys/serper/test')).body).toEqual({ ok: false, error: 'No Serper key is set' });
  });
});
