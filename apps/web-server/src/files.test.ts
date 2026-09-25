import { existsSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { processAlive, readWebInfo, removeWebInfo, WebSettingsStore, webPaths, writeLoginFile, writeWebInfo } from './files';
import { createLog } from './log';

let dir: string;
beforeEach(() => void (dir = mkdtempSync(join(tmpdir(), 'desk-web-files-'))));
afterEach(() => rmSync(dir, { recursive: true, force: true }));

const mode = (file: string) => statSync(file).mode & 0o777;
const posix = process.platform !== 'win32';

describe('web.json', () => {
  it('holds the pid and port, owner-only, and is removed only by the process it names', () => {
    expect(readWebInfo(dir)).toBeNull();
    writeWebInfo(dir, { pid: 4242, port: 7434 });
    expect(readWebInfo(dir)).toEqual({ pid: 4242, port: 7434 });
    expect(JSON.parse(readFileSync(webPaths(dir).info, 'utf8'))).toEqual({ pid: 4242, port: 7434 });
    if (posix) expect(mode(webPaths(dir).info)).toBe(0o600);
    removeWebInfo(dir, 1);
    expect(readWebInfo(dir)).not.toBeNull();
    removeWebInfo(dir, 4242);
    expect(existsSync(webPaths(dir).info)).toBe(false);
  });

  it('reads a broken web.json as absent', () => {
    writeFileSync(webPaths(dir).info, '{"pid":"x","port":7434}');
    expect(readWebInfo(dir)).toBeNull();
    writeFileSync(webPaths(dir).info, 'not json');
    expect(readWebInfo(dir)).toBeNull();
  });

  it('tells a live pid from a dead one', () => {
    expect(processAlive(process.pid)).toBe(true);
    expect(processAlive(2 ** 22 + 12345)).toBe(false);
  });
});

describe('web-settings.json', () => {
  it('defaults to notifications on and no saved port, keeps changes, and survives a bad file', () => {
    expect(new WebSettingsStore(dir).get()).toEqual({ notifications: true });
    new WebSettingsStore(dir).update({ port: 7500 });
    new WebSettingsStore(dir).update({ notifications: false, port: undefined });
    expect(new WebSettingsStore(dir).get()).toEqual({ port: 7500, notifications: false });
    if (posix) expect(mode(webPaths(dir).settings)).toBe(0o600);
    writeFileSync(webPaths(dir).settings, '{"port": 70000}');
    expect(new WebSettingsStore(dir).get()).toEqual({ notifications: true });
    expect(() => new WebSettingsStore(dir).update({ port: 0 })).toThrow();
  });
});

describe('login redirect file', () => {
  it('is an owner-only page in the data dir that sends the browser to the link', () => {
    const link = 'http://127.0.0.1:7434/login?code=abc_DEF-123';
    const file = writeLoginFile(dir, link);
    expect(basename(file)).toMatch(/^web-login-[0-9a-f]{16}\.html$/);
    expect(join(dir, basename(file))).toBe(file);
    if (posix) expect(mode(file)).toBe(0o600);
    const html = readFileSync(file, 'utf8');
    expect(html).toContain(`<meta http-equiv="refresh" content="0;url=${link}">`);
    expect(html).toContain(`<a href="${link}">Open Desk</a>`);
    expect(writeLoginFile(dir, link)).not.toBe(file);
  });
});

describe('createLog', () => {
  it('appends timestamped lines with error details and never throws', () => {
    const file = join(dir, 'logs', 'web.log');
    const log = createLog(file);
    log('started');
    log('push failed', new Error('boom'));
    const text = readFileSync(file, 'utf8');
    expect(text).toMatch(/^\d{4}-\d\d-\d\dT\S+ started\n/);
    expect(text).toContain('push failed Error: boom');
    writeFileSync(join(dir, 'plain'), 'x');
    expect(() => createLog(join(dir, 'plain', 'web.log'))('lost')).not.toThrow();
  });
});
