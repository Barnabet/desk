import { randomBytes, timingSafeEqual } from 'node:crypto';

/** How long a login link works. */
export const CODE_TTL_MS = 2 * 60_000;
/** How many spent codes are remembered (the newest), so a replay is caught. */
const SPENT_KEPT = 256;
const TOKEN = /^[A-Za-z0-9_-]{43}$/;

/** 32 random bytes as base64url (43 characters): login codes and session secrets. */
export const newToken = (): string => randomBytes(32).toString('base64url');

function sameToken(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

/** Session secrets, in memory only: created by /login, compared in constant time, gone when desk web stops. */
export class Sessions {
  private secrets: string[] = [];
  private readonly listeners = new Set<(secret: string) => void>();

  create(): string {
    const secret = newToken();
    this.secrets.push(secret);
    return secret;
  }

  valid(secret: string | null | undefined): boolean {
    if (!secret || !TOKEN.test(secret)) return false;
    let found = false;
    for (const s of this.secrets) found = sameToken(s, secret) || found;
    return found;
  }

  revoke(secret: string): void {
    if (!this.secrets.includes(secret)) return;
    this.secrets = this.secrets.filter((s) => s !== secret);
    for (const cb of this.listeners) cb(secret);
  }

  /** Called with each revoked secret, so /push can close the sockets that use it. */
  onRevoke(cb: (secret: string) => void): () => void {
    this.listeners.add(cb);
    return () => void this.listeners.delete(cb);
  }

  get size(): number {
    return this.secrets.length;
  }
}

export type Redeemed = { ok: true; secret: string } | { ok: false; reason: 'unknown' | 'expired' | 'replayed' };

export type LoginCodesOptions = {
  ttlMs?: number;
  now?: () => number;
  /** A code opened a session (desk web then deletes the redirect file that carried it). */
  onRedeemed?(code: string): void;
};

type Code = { code: string; expiresAt: number; secret: string | null };

/**
 * One-time login codes: 32 random bytes, single use, valid for 2 minutes. A spent code presented again revokes the
 * session it opened, whoever presents it: someone else saw the link.
 */
export class LoginCodes {
  private codes: Code[] = [];

  constructor(
    private readonly sessions: Sessions,
    private readonly o: LoginCodesOptions = {},
  ) {}

  private now(): number {
    return (this.o.now ?? Date.now)();
  }

  issue(): string {
    const now = this.now();
    // Unused codes are forgotten once expired; spent ones are kept (the newest SPENT_KEPT) to catch replays.
    this.codes = this.codes.filter((c) => c.secret !== null || c.expiresAt > now);
    const spent = this.codes.filter((c) => c.secret !== null);
    if (spent.length > SPENT_KEPT) {
      const forget = new Set(spent.slice(0, spent.length - SPENT_KEPT));
      this.codes = this.codes.filter((c) => !forget.has(c));
    }
    const code = newToken();
    this.codes.push({ code, expiresAt: now + (this.o.ttlMs ?? CODE_TTL_MS), secret: null });
    return code;
  }

  redeem(code: string): Redeemed {
    if (!TOKEN.test(code)) return { ok: false, reason: 'unknown' };
    let hit: Code | undefined;
    for (const c of this.codes) if (sameToken(c.code, code)) hit = c;
    if (!hit) return { ok: false, reason: 'unknown' };
    if (hit.secret !== null) {
      this.sessions.revoke(hit.secret);
      return { ok: false, reason: 'replayed' };
    }
    if (hit.expiresAt <= this.now()) {
      const expired = hit;
      this.codes = this.codes.filter((c) => c !== expired);
      return { ok: false, reason: 'expired' };
    }
    hit.secret = this.sessions.create();
    this.o.onRedeemed?.(code);
    return { ok: true, secret: hit.secret };
  }
}
