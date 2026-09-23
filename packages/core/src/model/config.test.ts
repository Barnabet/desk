import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { loadModelConfig, normalizeBaseURL, parseEnvFile } from './config';

let home: string;
beforeEach(async () => {
  home = await mkdtemp(join(tmpdir(), 'desk-home-'));
});
afterEach(async () => rm(home, { recursive: true, force: true }));

describe('parseEnvFile', () => {
  it('handles export prefixes, quotes and comments', () => {
    expect(parseEnvFile('# c\nexport A=1\nB="two"\nC=\'three\'\n\nD=x=y\n')).toEqual({ A: '1', B: 'two', C: 'three', D: 'x=y' });
  });
});

describe('normalizeBaseURL', () => {
  it('appends /v1 once and strips trailing slashes', () => {
    expect(normalizeBaseURL('http://127.0.0.1:8317')).toBe('http://127.0.0.1:8317/v1');
    expect(normalizeBaseURL('http://127.0.0.1:8317/')).toBe('http://127.0.0.1:8317/v1');
    expect(normalizeBaseURL('http://127.0.0.1:8317/v1/')).toBe('http://127.0.0.1:8317/v1');
  });
});

describe('loadModelConfig', () => {
  it('prefers DESK_OPENAI_* env vars', () => {
    const cfg = loadModelConfig({ DESK_OPENAI_BASE_URL: 'http://h:1', DESK_OPENAI_API_KEY: 'k' }, home);
    expect(cfg).toEqual({ baseURL: 'http://h:1/v1', apiKey: 'k' });
  });

  it('falls back to ~/.config/cliproxyapi.env', async () => {
    await mkdir(join(home, '.config'), { recursive: true });
    await writeFile(join(home, '.config', 'cliproxyapi.env'), 'export CLIPROXY_API_KEY=secret\nexport CLIPROXY_BASE_URL=http://127.0.0.1:8317\n');
    expect(loadModelConfig({}, home)).toEqual({ baseURL: 'http://127.0.0.1:8317/v1', apiKey: 'secret' });
  });

  it('throws a helpful error when nothing is configured', () => {
    expect(() => loadModelConfig({}, home)).toThrow(/DESK_OPENAI_BASE_URL/);
  });
});
