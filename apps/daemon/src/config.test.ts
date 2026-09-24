import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { startFakeModel, type FakeModelServer } from '@desk/fake-model';
import type { Keychain } from '@desk/core';
import { startDaemon, type RunningDaemon } from './daemon';
import { createLogger } from './logger';

let dir: string;
let daemon: RunningDaemon | undefined;
let fake: FakeModelServer | undefined;
afterEach(async () => {
  await daemon?.stop();
  daemon = undefined;
  await fake?.close();
  fake = undefined;
  await rm(dir, { recursive: true, force: true });
});

const SECRET = 'sk-desk-secret-0042';

async function start(keychain: Keychain | null) {
  dir = await mkdtemp(join(tmpdir(), 'desk-cfg-'));
  daemon = await startDaemon({ dataDir: dir, port: 0, env: {}, home: dir, keychain, sandboxAvailable: false, log: createLogger(join(dir, 'logs', 'deskd.log'), false) });
  const api = async (method: string, path: string, body?: unknown) => {
    const res = await fetch(`http://127.0.0.1:${daemon!.port}/v1${path}`, {
      method,
      headers: { authorization: `Bearer ${daemon!.token}`, ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const text = await res.text();
    return { status: res.status, text, body: text ? JSON.parse(text) : null };
  };
  return api;
}

function memKeychain(): Keychain & { secret: string | null } {
  const k = { secret: null as string | null, get: () => k.secret, set: (s: string) => void (k.secret = s) };
  return k;
}

describe('model endpoint setup', () => {
  it('starts unconfigured, stores the key in the Keychain, and never leaks it', async () => {
    const keychain = memKeychain();
    const api = await start(keychain);
    fake = await startFakeModel();
    expect((await api('GET', '/config/model-endpoint')).body).toEqual({ configured: false, source: null, base_url: null });

    const tested = await api('POST', '/config/model-endpoint/test', { base_url: fake.url, api_key: SECRET });
    expect(tested.body.ok).toBe(true);

    const saved = await api('PUT', '/config/model-endpoint', { base_url: fake.url, api_key: SECRET });
    expect(saved.body).toEqual({ configured: true, source: 'keychain', base_url: fake.url });
    expect(keychain.secret).toBe(SECRET);
    expect((await api('POST', '/config/model-endpoint/test')).body.ok).toBe(true);

    const everything = [
      saved.text,
      (await api('GET', '/config/model-endpoint')).text,
      await readFile(join(dir, 'daemon.json'), 'utf8'),
      await readFile(join(dir, 'config.json'), 'utf8'),
      await readFile(join(dir, 'logs', 'deskd.log'), 'utf8'),
      JSON.stringify(daemon!.store.list()),
    ].join('\n');
    expect(everything).not.toContain(SECRET);
  });

  it('answers 501 when no Keychain is available and 400 for unsafe keys', async () => {
    const api = await start(null);
    expect((await api('PUT', '/config/model-endpoint', { base_url: 'http://127.0.0.1:1/v1', api_key: 'sk-x' })).status).toBe(501);
    expect((await api('PUT', '/config/model-endpoint', { base_url: 'http://127.0.0.1:1/v1', api_key: 'a b' })).status).toBe(400);
  });
});

describe('daemon config and health', () => {
  it('patches notifications and reports proxy state and uptime', async () => {
    const api = await start(null);
    expect((await api('GET', '/config')).body).toEqual({ notifications: 'auto' });
    expect((await api('PATCH', '/config', { notifications: 'off' })).body).toEqual({ notifications: 'off' });
    expect(JSON.parse(await readFile(join(dir, 'config.json'), 'utf8'))).toMatchObject({ notifications: 'off' });
    const health = (await (await fetch(`http://127.0.0.1:${daemon!.port}/v1/health`)).json()) as { uptime_s: number };
    expect(health).toMatchObject({ protocol_version: 1, proxy: 'up' });
    expect(health.uptime_s).toBeGreaterThanOrEqual(0);
  });
});
