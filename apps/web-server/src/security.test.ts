import { Hono } from 'hono';
import { describe, expect, it } from 'vitest';
import { contentSecurityPolicy, hostAllowed, hostCheck, originAllowed, originCheck, securityHeaders } from './security';

const PORT = 7434;
const HOST = { host: `127.0.0.1:${PORT}` };

function app() {
  const a = new Hono();
  a.use('*', securityHeaders(() => PORT));
  a.use('*', hostCheck(() => PORT));
  a.get('/', (c) => c.text('home'));
  a.post('/rpc/x', originCheck(() => PORT), (c) => c.json({ ok: true }));
  return a;
}

describe('Host, Origin and headers', () => {
  it('answers only Host 127.0.0.1:<port>, refusing localhost too (421)', async () => {
    expect((await app().request('/', { headers: HOST })).status).toBe(200);
    for (const host of [`localhost:${PORT}`, `[::1]:${PORT}`, `127.0.0.1:${PORT + 1}`, '127.0.0.1', `desk.example:${PORT}`]) {
      expect((await app().request('/', { headers: { host } })).status, host).toBe(421);
    }
    expect((await app().request('/')).status).toBe(421);
    expect(hostAllowed(`127.0.0.1:${PORT}`, PORT)).toBe(true);
    expect(hostAllowed(undefined, PORT)).toBe(false);
  });

  it('takes guarded requests only from Origin http://127.0.0.1:<port> (403)', async () => {
    const post = (origin?: string) => app().request('/rpc/x', { method: 'POST', headers: { ...HOST, ...(origin ? { origin } : {}) } });
    expect((await post(`http://127.0.0.1:${PORT}`)).status).toBe(200);
    for (const origin of [undefined, 'null', `http://localhost:${PORT}`, `http://127.0.0.1:${PORT + 1}`, `https://127.0.0.1:${PORT}`, 'https://evil.example']) {
      const res = await post(origin);
      expect(res.status, String(origin)).toBe(403);
      expect(await res.json()).toEqual({ ok: false, error: { code: 'forbidden', message: 'Cross-origin requests are refused.' } });
    }
    expect(originAllowed(`http://127.0.0.1:${PORT}`, PORT)).toBe(true);
    expect(originAllowed(null, PORT)).toBe(false);
  });

  it('sends the CSP and the other security headers on every response, refusals included', async () => {
    expect(contentSecurityPolicy(PORT)).toBe(
      "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' ws://127.0.0.1:7434; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    );
    const a = app();
    const responses = [
      await a.request('/', { headers: HOST }),
      await a.request('/', { headers: { host: `localhost:${PORT}` } }),
      await a.request('/missing', { headers: HOST }),
      await a.request('/rpc/x', { method: 'POST', headers: HOST }),
    ];
    expect(responses.map((r) => r.status)).toEqual([200, 421, 404, 403]);
    for (const r of responses) {
      expect(r.headers.get('content-security-policy')).toBe(contentSecurityPolicy(PORT));
      expect(r.headers.get('x-content-type-options')).toBe('nosniff');
      expect(r.headers.get('referrer-policy')).toBe('no-referrer');
      expect(r.headers.get('cross-origin-opener-policy')).toBe('same-origin');
      expect(r.headers.get('cross-origin-resource-policy')).toBe('same-origin');
      expect(r.headers.get('set-cookie')).toBeNull();
    }
  });
});
