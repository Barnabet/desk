import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { startFakeModel } from '@desk/fake-model';
import { macKeychain, resolveModelEndpoint, testModelEndpoint, type Keychain } from './endpoint';

let home: string;
beforeEach(async () => {
  home = await mkdtemp(join(tmpdir(), 'desk-home-'));
});
afterEach(async () => rm(home, { recursive: true, force: true }));

const memKeychain = (secret: string | null = null): Keychain & { secret: string | null } => {
  const k = { secret, get: () => k.secret, set: (s: string) => void (k.secret = s) };
  return k;
};

describe('resolveModelEndpoint', () => {
  it('prefers env, then ~/.config/cliproxyapi.env, then config.json + Keychain', async () => {
    const keychain = memKeychain('kc-key');
    const base = { home, baseUrl: 'http://127.0.0.1:9000', keychain };
    expect(resolveModelEndpoint({ ...base, env: {} })).toEqual({ config: { baseURL: 'http://127.0.0.1:9000/v1', apiKey: 'kc-key' }, source: 'keychain' });
    await mkdir(join(home, '.config'));
    await writeFile(join(home, '.config', 'cliproxyapi.env'), 'CLIPROXY_BASE_URL=http://file:1\nCLIPROXY_API_KEY=file-key\n');
    expect(resolveModelEndpoint({ ...base, env: {} })).toMatchObject({ source: 'file', config: { apiKey: 'file-key' } });
    expect(resolveModelEndpoint({ ...base, env: { DESK_OPENAI_BASE_URL: 'http://env:1', DESK_OPENAI_API_KEY: 'env-key' } })).toMatchObject({ source: 'env', config: { apiKey: 'env-key' } });
  });

  it('is unconfigured without a base URL or a stored key', () => {
    expect(resolveModelEndpoint({ env: {}, home, baseUrl: null, keychain: memKeychain('k') })).toEqual({ config: null, source: null });
    expect(resolveModelEndpoint({ env: {}, home, baseUrl: 'http://x', keychain: memKeychain(null) })).toEqual({ config: null, source: null });
    expect(resolveModelEndpoint({ env: {}, home, baseUrl: 'http://x', keychain: null })).toEqual({ config: null, source: null });
  });
});

describe('macKeychain', () => {
  it('writes through `security -i` on stdin and reads with -w; the secret is never an argument', () => {
    const calls: Array<{ args: string[]; input?: string }> = [];
    const kc = macKeychain((args, input) => {
      calls.push({ args, ...(input !== undefined ? { input } : {}) });
      return args[0] === 'find-generic-password' ? 'stored-secret\n' : '';
    });
    kc.set('sk-secret');
    expect(kc.get()).toBe('stored-secret');
    expect(calls[0]!.args).toEqual(['-i']);
    expect(calls[0]!.input).toBe('add-generic-password -U -s "Desk model endpoint" -a "default" -w "sk-secret"\n');
    expect(calls[1]!.args).toEqual(['find-generic-password', '-s', 'Desk model endpoint', '-a', 'default', '-w']);
    for (const c of calls) expect(c.args.join(' ')).not.toContain('sk-secret');
  });

  it('returns null when the item is missing and refuses unsafe secrets', () => {
    const kc = macKeychain(() => {
      throw new Error('not found');
    });
    expect(kc.get()).toBeNull();
    expect(() => macKeychain(() => '').set('a"b')).toThrow(/unsafe/);
  });
});

describe('testModelEndpoint', () => {
  it('lists models on success and hides the key in errors', async () => {
    const fake = await startFakeModel();
    try {
      const ok = await testModelEndpoint({ baseURL: fake.url, apiKey: 'k' });
      expect(ok.ok).toBe(true);
      expect(Array.isArray(ok.models)).toBe(true);
    } finally {
      await fake.close();
    }
    const bad = await testModelEndpoint({ baseURL: 'http://127.0.0.1:9/v1', apiKey: 'sk-hidden-123' });
    expect(bad.ok).toBe(false);
    expect(JSON.stringify(bad)).not.toContain('sk-hidden-123');
  });
});
