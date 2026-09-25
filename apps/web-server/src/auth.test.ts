import { describe, expect, it } from 'vitest';
import { CODE_TTL_MS, LoginCodes, Sessions } from './auth';

describe('Sessions', () => {
  it('creates 32-byte secrets, checks them and revokes them', () => {
    const sessions = new Sessions();
    const a = sessions.create();
    const b = sessions.create();
    expect(a).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(Buffer.from(a, 'base64url')).toHaveLength(32);
    expect(a).not.toBe(b);
    expect(sessions.valid(a)).toBe(true);
    expect(sessions.valid(`${a.slice(0, -1)}${a.endsWith('A') ? 'B' : 'A'}`)).toBe(false);
    expect(sessions.valid('')).toBe(false);
    expect(sessions.valid(undefined)).toBe(false);
    expect(sessions.valid(`${a}x`)).toBe(false);
    const revoked: string[] = [];
    sessions.onRevoke((s) => void revoked.push(s));
    sessions.revoke(a);
    sessions.revoke(a);
    expect(sessions.valid(a)).toBe(false);
    expect(sessions.valid(b)).toBe(true);
    expect(revoked).toEqual([a]);
    expect(sessions.size).toBe(1);
  });
});

describe('LoginCodes', () => {
  const setup = () => {
    let now = 1_000_000;
    const sessions = new Sessions();
    const redeemed: string[] = [];
    const codes = new LoginCodes(sessions, { now: () => now, onRedeemed: (c) => void redeemed.push(c) });
    return { sessions, codes, redeemed, tick: (ms: number) => void (now += ms) };
  };

  it('opens one session per code, once', () => {
    const { sessions, codes, redeemed } = setup();
    const code = codes.issue();
    expect(code).toMatch(/^[A-Za-z0-9_-]{43}$/);
    const r = codes.redeem(code);
    expect(r.ok).toBe(true);
    expect(r.ok && sessions.valid(r.secret)).toBe(true);
    expect(redeemed).toEqual([code]);
    expect(codes.redeem(codes.issue()).ok).toBe(true);
    expect(sessions.size).toBe(2);
  });

  it('refuses unknown codes and codes older than two minutes', () => {
    const { codes, tick } = setup();
    expect(CODE_TTL_MS).toBe(120_000);
    expect(codes.redeem('nope')).toEqual({ ok: false, reason: 'unknown' });
    expect(codes.redeem('A'.repeat(43))).toEqual({ ok: false, reason: 'unknown' });
    const code = codes.issue();
    tick(CODE_TTL_MS - 1);
    const fresh = codes.issue();
    tick(1);
    expect(codes.redeem(code)).toEqual({ ok: false, reason: 'expired' });
    expect(codes.redeem(code)).toEqual({ ok: false, reason: 'unknown' });
    expect(codes.redeem(fresh).ok).toBe(true);
  });

  it('revokes the session a spent code opened when that code comes back, even much later', () => {
    const { sessions, codes, tick } = setup();
    const code = codes.issue();
    const first = codes.redeem(code);
    if (!first.ok) throw new Error('expected a session');
    const other = codes.redeem(codes.issue());
    tick(CODE_TTL_MS * 10);
    codes.issue();
    expect(codes.redeem(code)).toEqual({ ok: false, reason: 'replayed' });
    expect(sessions.valid(first.secret)).toBe(false);
    expect(other.ok && sessions.valid(other.secret)).toBe(true);
    expect(codes.redeem(code)).toEqual({ ok: false, reason: 'replayed' });
  });

  it('remembers the newest 256 spent codes: an older one comes back as unknown and leaves its session alone', () => {
    const { sessions, codes } = setup();
    const spent = Array.from({ length: 257 }, () => {
      const code = codes.issue();
      const r = codes.redeem(code);
      if (!r.ok) throw new Error('expected a session');
      return { code, secret: r.secret };
    });
    codes.issue(); // spent codes are trimmed when a code is issued
    const [first, second] = [spent[0]!, spent[1]!];
    expect(codes.redeem(first.code)).toEqual({ ok: false, reason: 'unknown' });
    expect(sessions.valid(first.secret)).toBe(true);
    expect(codes.redeem(second.code)).toEqual({ ok: false, reason: 'replayed' });
    expect(sessions.valid(second.secret)).toBe(false);
  });
});
