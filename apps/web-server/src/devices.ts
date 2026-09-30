import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { chmodSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import { newToken } from './auth';

/** A phone not used for this long is signed out. */
export const DEVICE_IDLE_MS = 30 * 24 * 60 * 60_000;
/** How long a pairing link (`desk web pair`) or a login code written by `desk web login` works. */
export const PENDING_TTL_MS = 10 * 60_000;
/** last_seen_at is written at most this often per phone. */
const TOUCH_EVERY_MS = 60 * 60_000;
const TOKEN = /^[A-Za-z0-9_-]{43}$/;

export const devicePaths = (dataDir: string) => ({ devices: join(dataDir, 'web-devices.json') });

/** Only a hash of each secret is kept, so reading the file signs nobody in. */
const hashOf = (secret: string): string => createHash('sha256').update(secret).digest('hex');

function sameHash(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

/** Writes a 0600 file through a temporary name, so a reader never sees half of it. */
function writePrivateAtomic(file: string, text: string): void {
  const tmp = `${file}.${randomBytes(4).toString('hex')}.tmp`;
  writeFileSync(tmp, text, { mode: 0o600, flag: 'wx' });
  chmodSync(tmp, 0o600);
  renameSync(tmp, file);
}

const Device = z.object({
  id: z.string().regex(/^[a-f0-9]{8}$/),
  name: z.string().min(1).max(80),
  hash: z.string().regex(/^[a-f0-9]{64}$/),
  created_at: z.string(),
  last_seen_at: z.string(),
});
type Device = z.infer<typeof Device>;
const DevicesFile = z.object({ devices: z.array(Device) });

/** A paired phone as the CLI lists it (never its hash). */
export type PairedDevice = Omit<Device, 'hash'>;

const shown = ({ hash: _hash, ...rest }: Device): PairedDevice => rest;

/**
 * Paired phones, in `<data>/web-devices.json` (0600, a SHA-256 of each secret only). Read on every check, so a phone
 * that `desk web unpair` removed while desk web runs is refused on its next request. Phones idle for 30 days go.
 */
export class DeviceStore {
  private readonly file: string;

  constructor(
    private readonly dataDir: string,
    private readonly o: { now?: () => number; idleMs?: number } = {},
  ) {
    this.file = devicePaths(dataDir).devices;
  }

  private now(): number {
    return (this.o.now ?? Date.now)();
  }

  private read(): Device[] {
    let text: string;
    try {
      text = readFileSync(this.file, 'utf8');
    } catch {
      return [];
    }
    const parsed = DevicesFile.safeParse(JSON.parse(text) as unknown);
    return parsed.success ? parsed.data.devices : [];
  }

  private write(devices: Device[]): void {
    mkdirSync(this.dataDir, { recursive: true });
    writePrivateAtomic(this.file, `${JSON.stringify({ devices }, null, 2)}\n`);
  }

  private live(devices: Device[]): Device[] {
    const cutoff = this.now() - (this.o.idleMs ?? DEVICE_IDLE_MS);
    return devices.filter((d) => Date.parse(d.last_seen_at) > cutoff);
  }

  /** Pairs a phone: returns its secret, which is shown once (to the phone) and never stored. */
  add(name: string): { device: PairedDevice; secret: string } {
    const secret = newToken();
    const at = new Date(this.now()).toISOString();
    const device: Device = { id: randomBytes(4).toString('hex'), name: name.trim().slice(0, 80) || 'Phone', hash: hashOf(secret), created_at: at, last_seen_at: at };
    this.write([...this.live(this.read()), device]);
    return { device: shown(device), secret };
  }

  /** The paired phone this secret belongs to, or null. Compared in constant time; notes when the phone was last seen. */
  who(secret: string | null | undefined): PairedDevice | null {
    if (!secret || !TOKEN.test(secret)) return null;
    const hash = hashOf(secret);
    const all = this.read();
    let hit: Device | undefined;
    for (const d of this.live(all)) if (sameHash(d.hash, hash)) hit = d;
    if (!hit) return null;
    const now = this.now();
    if (now - Date.parse(hit.last_seen_at) > TOUCH_EVERY_MS) {
      const seen = new Date(now).toISOString();
      const id = hit.id;
      this.write(this.live(all).map((d) => (d.id === id ? { ...d, last_seen_at: seen } : d)));
      hit = { ...hit, last_seen_at: seen };
    }
    return shown(hit);
  }

  list(): PairedDevice[] {
    return this.live(this.read()).map(shown);
  }

  /** Signs a phone out by its id; returns it, or null when no phone has that id. */
  revoke(id: string): PairedDevice | null {
    const all = this.read();
    const hit = all.find((d) => d.id === id);
    if (!hit) return null;
    this.write(all.filter((d) => d !== hit));
    return shown(hit);
  }
}

/** What a pending code opens: a paired phone (`desk web pair`), or a session in a browser on this Mac (`desk web login`). */
export type PendingKind = 'phone' | 'browser';

const Pending = z.object({ kind: z.enum(['phone', 'browser']), hash: z.string().regex(/^[a-f0-9]{64}$/), name: z.string().max(80), expires_at: z.number() });
const PENDING_PREFIX = 'web-code-';
const PENDING_SUFFIX = '.json';

/**
 * Writes `<data>/web-code-<random>.json` (0600) holding a hash of a new one-time code, and returns the code. The CLI
 * issues codes this way, so it needs no connection to desk web, which may run under launchd with no terminal. Agents
 * cannot read these files (deskd's sandbox guard), and could not use what is in them.
 */
export function writePendingCode(dataDir: string, kind: PendingKind, o: { name?: string; now?: number } = {}): string {
  const code = newToken();
  mkdirSync(dataDir, { recursive: true });
  const file = join(dataDir, `${PENDING_PREFIX}${randomBytes(8).toString('hex')}${PENDING_SUFFIX}`);
  const body = { kind, hash: hashOf(code), name: (o.name ?? '').trim().slice(0, 80), expires_at: (o.now ?? Date.now()) + PENDING_TTL_MS };
  writeFileSync(file, `${JSON.stringify(body)}\n`, { mode: 0o600, flag: 'wx' });
  chmodSync(file, 0o600);
  return code;
}

/**
 * Spends a code `writePendingCode` wrote for this kind: deletes its file and returns the name it was issued with, or
 * null. Expired and unreadable files are deleted on the way.
 */
export function redeemPendingCode(dataDir: string, kind: PendingKind, code: string, now = Date.now()): { name: string } | null {
  if (!TOKEN.test(code)) return null;
  const hash = hashOf(code);
  let names: string[];
  try {
    names = readdirSync(dataDir).filter((n) => n.startsWith(PENDING_PREFIX) && n.endsWith(PENDING_SUFFIX));
  } catch {
    return null;
  }
  let found: { name: string } | null = null;
  for (const n of names) {
    const file = join(dataDir, n);
    let p: z.infer<typeof Pending> | null = null;
    try {
      const parsed = Pending.safeParse(JSON.parse(readFileSync(file, 'utf8')) as unknown);
      p = parsed.success ? parsed.data : null;
    } catch {
      p = null;
    }
    if (!p || p.expires_at <= now) {
      rmSync(file, { force: true });
      continue;
    }
    if (found || p.kind !== kind || !sameHash(p.hash, hash)) continue;
    // Deleted before the session exists: a second request racing this one finds nothing.
    rmSync(file, { force: true });
    found = { name: p.name };
  }
  return found;
}
