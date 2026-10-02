import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync } from 'node:fs';
import { createServer, type AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { DeviceStore, readWebInfo } from '@desk/web-server';
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
      // --no-open and a settled `stopped`: were the check to regress, the run would end at once and open no browser.
      const io = { out: () => {}, err: (s: string) => void (err += s), dataDir: dir, stopped: Promise.resolve() };
      expect(await runCli(['web', '--port', bad, '--no-open'], io), bad).toBe(1);
      expect(err).toContain('--port must be a whole number from 1 to 65535');
    }
    expect(existsSync(join(dir, 'web.json'))).toBe(false);
  });

  it('pairs a phone at the remembered remote address, lists it and unpairs it', async () => {
    let out = '';
    let err = '';
    const io = { out: (s: string) => void (out += s), err: (s: string) => void (err += s), dataDir: dir, stopped: Promise.resolve() };
    expect(await runCli(['web', 'pair'], io)).toBe(1);
    expect(err).toContain("Set the phones' address first");
    expect(await runCli(['web', '--no-open', '--remote-url', 'http://mac.ts.net'], io)).toBe(1);
    expect(err).toContain('--remote-url must be an https:// address');

    const port = await freePort();
    expect(await runCli(['web', '--no-open', '--port', String(port), '--remote-url', 'https://mac.tail1234.ts.net'], io)).toBe(0);
    expect(out).toContain('Paired phones reach it at https://mac.tail1234.ts.net');
    expect(JSON.parse(readFileSync(join(dir, 'web-settings.json'), 'utf8'))).toMatchObject({ remoteUrl: 'https://mac.tail1234.ts.net' });

    out = '';
    expect(await runCli(['web', 'pair', '--name', 'iPhone'], io)).toBe(0);
    expect(out).toMatch(/https:\/\/mac\.tail1234\.ts\.net\/pair\?code=[A-Za-z0-9_-]{43}/);
    expect(out).toMatch(/type this code: [A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}\n/);
    expect(out).toContain('desk web is not running yet');
    expect(out).toContain('▄');

    out = '';
    expect(await runCli(['web', 'phones'], io)).toBe(0);
    expect(out).toContain('No paired phones');
    const { device } = new DeviceStore(dir).add('iPhone');
    out = '';
    expect(await runCli(['web', 'phones'], io)).toBe(0);
    expect(out).toContain(`${device.id}  iPhone  paired`);
    expect(await runCli(['web', 'unpair', device.id], io)).toBe(0);
    expect(out).toContain(`Signed out iPhone (${device.id}).`);
    expect(await runCli(['web', 'unpair', device.id], io)).toBe(1);
    expect(new DeviceStore(dir).list()).toEqual([]);

    expect(await runCli(['web', '--no-open', '--port', String(port), '--remote-url', 'off'], io)).toBe(0);
    expect(JSON.parse(readFileSync(join(dir, 'web-settings.json'), 'utf8'))).not.toHaveProperty('remoteUrl');
  });

  it('prints no login link as a login item, and desk web login signs a browser in', async () => {
    const port = await freePort();
    let out = '';
    const keys: { stop?: () => void } = {};
    const run = runCli(['web', '--service', '--port', String(port)], {
      out: (s) => void (out += s),
      err: () => {},
      dataDir: dir,
      stopped: new Promise<void>((resolve) => (keys.stop = () => resolve())),
    });
    await until(() => out.includes('desk web login'));
    expect(out).not.toMatch(LINK);

    let printed = '';
    expect(await runCli(['web', 'login', '--no-open'], { out: (s) => void (printed += s), err: () => {}, dataDir: dir })).toBe(0);
    // --no-open is login's own flag: it prints the link and never tries the browser.
    expect(printed).not.toContain('Could not open');
    expect(printed).not.toContain('Opened Desk');
    const link = /http:\/\/127\.0\.0\.1:\d+\/login\?code=[A-Za-z0-9_-]{43}/.exec(printed)?.[0];
    expect(link).toBeDefined();
    const page = await (await fetch(link!)).text();
    expect(page).toMatch(/<meta name="desk-session" content="[A-Za-z0-9_-]{43}">/);
    expect((await fetch(link!)).status).toBe(401);

    keys.stop?.();
    expect(await run).toBe(0);
    let err = '';
    expect(await runCli(['web', 'login', '--no-open'], { out: () => {}, err: (s) => void (err += s), dataDir: dir })).toBe(1);
    expect(err).toContain('desk web is not running');
  });
});
