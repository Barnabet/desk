import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { DEVICE_IDLE_MS, DeviceStore, devicePaths, PENDING_TTL_MS, redeemPendingCode, writePendingCode } from './devices';

let dir: string;
beforeEach(() => void (dir = mkdtempSync(join(tmpdir(), 'desk-devices-'))));
afterEach(() => rmSync(dir, { recursive: true, force: true }));

const HOUR = 60 * 60_000;

describe('DeviceStore', () => {
  it('pairs a phone, knows its secret, and keeps only a hash of it in a 0600 file', () => {
    const store = new DeviceStore(dir);
    const { device, secret } = store.add('  Louis’s iPhone  ');
    expect(device).toMatchObject({ name: 'Louis’s iPhone' });
    expect(device.id).toMatch(/^[a-f0-9]{8}$/);
    expect(store.who(secret)).toEqual(device);
    expect(store.who(`${secret.slice(0, -1)}${secret.endsWith('A') ? 'B' : 'A'}`)).toBeNull();
    expect(store.who('')).toBeNull();
    expect(store.who(null)).toBeNull();
    const file = devicePaths(dir).devices;
    expect(readFileSync(file, 'utf8')).not.toContain(secret);
    expect(statSync(file).mode & 0o777).toBe(0o600);
    expect(readdirSync(dir)).toEqual(['web-devices.json']);
    expect(store.list()).toEqual([device]);
  });

  it('signs a phone out by id, seen by another store on the same file (the CLI while desk web runs)', () => {
    const server = new DeviceStore(dir);
    const { device, secret } = server.add('Phone');
    const other = server.add('Tablet');
    expect(new DeviceStore(dir).revoke(device.id)).toEqual(device);
    expect(server.who(secret)).toBeNull();
    expect(server.who(other.secret)?.name).toBe('Tablet');
    expect(new DeviceStore(dir).revoke(device.id)).toBeNull();
  });

  it('signs out a phone idle for 30 days, and notes use at most hourly', () => {
    let now = Date.parse('2026-09-30T10:00:00Z');
    const store = new DeviceStore(dir, { now: () => now });
    const { secret } = store.add('Phone');
    now += HOUR / 2;
    expect(store.who(secret)?.last_seen_at).toBe('2026-09-30T10:00:00.000Z');
    now += HOUR;
    expect(store.who(secret)?.last_seen_at).toBe('2026-09-30T11:30:00.000Z');
    now += DEVICE_IDLE_MS - 1000;
    expect(store.who(secret)).not.toBeNull();
    now += DEVICE_IDLE_MS + 1000;
    expect(store.who(secret)).toBeNull();
    expect(store.list()).toEqual([]);
  });
});

describe('pending codes', () => {
  it('works once, for its own kind, within 10 minutes, from a 0600 file holding only a hash', () => {
    const now = 1_000_000;
    const code = writePendingCode(dir, 'phone', { name: 'iPhone', now });
    const [file] = readdirSync(dir);
    expect(file).toMatch(/^web-code-[a-f0-9]{16}\.json$/);
    expect(readFileSync(join(dir, file!), 'utf8')).not.toContain(code);
    expect(statSync(join(dir, file!)).mode & 0o777).toBe(0o600);
    expect(redeemPendingCode(dir, 'browser', code, now)).toBeNull();
    expect(redeemPendingCode(dir, 'phone', code, now + 1000)).toEqual({ name: 'iPhone' });
    expect(redeemPendingCode(dir, 'phone', code, now + 2000)).toBeNull();
    expect(readdirSync(dir)).toEqual([]);
  });

  it('refuses and removes an expired code, and ignores malformed ones', () => {
    const now = 1_000_000;
    const code = writePendingCode(dir, 'browser', { now });
    expect(redeemPendingCode(dir, 'browser', 'not a code', now)).toBeNull();
    expect(redeemPendingCode(dir, 'browser', code, now + PENDING_TTL_MS)).toBeNull();
    expect(readdirSync(dir)).toEqual([]);
  });
});
