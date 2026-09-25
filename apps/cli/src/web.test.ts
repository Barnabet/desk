import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync } from 'node:fs';
import { createServer, type AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { readWebInfo } from '@desk/web-server';
import { runCli } from './commands';

let dir: string;
beforeEach(() => void (dir = realpathSync(mkdtempSync(join(tmpdir(), 'desk-cli-web-')))));
afterEach(() => rmSync(dir, { recursive: true, force: true }));

const freePort = () =>
  new Promise<number>((resolve) => {
    const s = createServer();
    s.listen(0, '127.0.0.1', () => {
      const { port } = s.address() as AddressInfo;
      s.close(() => resolve(port));
    });
  });

const until = async (pred: () => boolean, ms = 5000) => {
  const end = Date.now() + ms;
  while (!pred()) {
    if (Date.now() > end) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 10));
  }
};

const LINK = /\/login\?code=[A-Za-z0-9_-]{43}/g;

describe('desk web', () => {
  it('serves until stopped, prints a login link now and on Enter, refuses a second instance, and remembers the port', async () => {
    const port = await freePort();
    let out = '';
    let err = '';
    const keys: { enter?: () => void; stop?: () => void } = {};
    const run = runCli(['web', '--port', String(port), '--no-open'], {
      out: (s) => void (out += s),
      err: (s) => void (err += s),
      dataDir: dir,
      stopped: new Promise<void>((resolve) => (keys.stop = () => resolve())),
      onEnter: (cb) => {
        keys.enter = cb;
        return () => void delete keys.enter;
      },
    });
    await until(() => out.includes('Ctrl-C to stop'));
    expect(out).toContain(`Desk is at http://127.0.0.1:${port}\n`);
    expect(out.match(LINK)).toHaveLength(1);
    expect(readWebInfo(dir)).toEqual({ pid: process.pid, port });
    expect(await (await fetch(`http://127.0.0.1:${port}/healthz`)).json()).toEqual({ ok: true });
    keys.enter?.();
    expect(out.match(LINK)).toHaveLength(2);

    const second = await runCli(['web', '--no-open'], { out: () => {}, err: (s) => void (err += s), dataDir: dir, stopped: new Promise<void>(() => {}) });
    expect(second).toBe(1);
    expect(err).toContain(`error: desk web is already running at http://127.0.0.1:${port}`);

    keys.stop?.();
    expect(await run).toBe(0);
    expect(out).toContain('desk web stopped.');
    expect(existsSync(join(dir, 'web.json'))).toBe(false);
    expect(JSON.parse(readFileSync(join(dir, 'web-settings.json'), 'utf8'))).toMatchObject({ port });
  });

  it('refuses a port that is not a whole number from 1 to 65535', async () => {
    for (const bad of ['abc', '0', '70000', '80.5']) {
      let err = '';
      expect(await runCli(['web', '--port', bad], { out: () => {}, err: (s) => void (err += s), dataDir: dir }), bad).toBe(1);
      expect(err).toContain('--port must be a whole number from 1 to 65535');
    }
    expect(existsSync(join(dir, 'web.json'))).toBe(false);
  });
});
