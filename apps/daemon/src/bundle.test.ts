import { execFileSync, spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const appDir = fileURLToPath(new URL('..', import.meta.url));

describe('bundled daemon', () => {
  it('builds a self-contained deskd.mjs that starts and serves health', async () => {
    execFileSync(process.execPath, ['scripts/bundle.mjs'], { cwd: appDir, stdio: 'pipe' });
    const dist = join(appDir, 'dist');
    expect(existsSync(join(dist, 'deskd.mjs'))).toBe(true);
    expect(existsSync(join(dist, 'drizzle'))).toBe(true);
    expect(existsSync(join(dist, 'node_modules', 'better-sqlite3', 'prebuilds'))).toBe(true);

    const dataDir = await mkdtemp(join(tmpdir(), 'desk-bundle-'));
    const child = spawn(process.execPath, [join(dist, 'deskd.mjs'), '--data-dir', dataDir, '--port', '0'], {
      cwd: tmpdir(),
      env: { PATH: process.env.PATH ?? '', HOME: dataDir, DESK_OPENAI_BASE_URL: 'http://127.0.0.1:9/v1', DESK_OPENAI_API_KEY: 'x' },
      stdio: 'ignore',
    });
    try {
      const infoFile = join(dataDir, 'daemon.json');
      for (let i = 0; i < 100 && !existsSync(infoFile); i++) await new Promise((r) => setTimeout(r, 100));
      const info = JSON.parse(readFileSync(infoFile, 'utf8')) as { port: number };
      const health = await (await fetch(`http://127.0.0.1:${info.port}/v1/health`)).json();
      expect(health).toMatchObject({ protocol_version: 1 });
    } finally {
      child.kill('SIGTERM');
      await new Promise((r) => child.once('exit', r));
      await rm(dataDir, { recursive: true, force: true });
    }
  }, 60_000);
});
