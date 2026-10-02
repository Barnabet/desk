import { Hono } from 'hono';
import { compress } from 'hono/compress';
import type { LoginCodes } from './auth';
import { DEV_RELOAD_JS } from './dev';
import { isShortCode, PENDING_TTL_MS, redeemPendingCode } from './devices';
import { sweepLoginFiles } from './files';
import { LOGIN_JS, loginFailedPage, loginPage, PAIR_JS, pairFailedPage, pairPage } from './login';
import { mountRpc, rpcJson, type RpcDeps } from './rpc';
import { hostCheck, originCheck, securityHeaders } from './security';
import { serveUi } from './static';

export type WebAppDeps = RpcDeps & {
  codes: LoginCodes;
  /** The data dir, where `desk web login` and `desk web pair` leave their one-time codes (web-code-*.json). */
  dataDir?: string;
  uiDir: string;
  dev: boolean;
  /** A spent login code came back, so the session it opened was revoked. */
  onReplay?(): void;
  /** A phone was paired. */
  onPaired?(name: string): void;
};

/** Wrong short pairing codes allowed per window, across all callers: a short code cannot be guessed in so few tries. */
export const PAIR_GUESSES = 10;
const PAIR_WINDOW_MS = 10 * 60_000;

const page = (status: number, html: string) => new Response(html, { status, headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' } });
const script = (js: string) => new Response(js, { headers: { 'content-type': 'text/javascript; charset=utf-8', 'cache-control': 'no-store' } });

/** desk web's HTTP routes. Every response gets the security headers; every request must name Host 127.0.0.1:<port>. */
export function createWebApp(d: WebAppDeps): Hono {
  const app = new Hono();
  app.use('*', securityHeaders(d.port, d.remote));
  app.use('*', hostCheck(d.port, d.remote));
  app.get('/healthz', () => new Response(JSON.stringify({ ok: true }), { headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' } }));
  app.get('/login', (c) => {
    // Hono answers HEAD with the GET route: a HEAD (a link preview, a prefetch) must not spend the code.
    if (c.req.method === 'HEAD') return page(200, '');
    const code = c.req.query('code') ?? '';
    const r = d.codes.redeem(code);
    if (r.ok) return page(200, loginPage(r.secret));
    if (r.reason === 'replayed') d.onReplay?.();
    // A code `desk web login` wrote, for a desk web running with no terminal (under launchd).
    if (r.reason === 'unknown' && d.dataDir) {
      // The redirect file `desk web login` opened is left behind (the CLI has exited): drop old ones.
      sweepLoginFiles(d.dataDir, PENDING_TTL_MS);
      if (redeemPendingCode(d.dataDir, 'browser', code)) return page(200, loginPage(d.sessions.create()));
    }
    return page(401, loginFailedPage());
  });
  app.get('/login.js', () => script(LOGIN_JS));
  app.get('/pair', (c) => {
    if (c.req.method === 'HEAD') return page(200, '');
    const pending = d.dataDir && d.devices ? redeemPendingCode(d.dataDir, 'phone', c.req.query('code') ?? '') : null;
    if (!pending || !d.devices) return page(401, pairFailedPage());
    const { device, secret } = d.devices.add(pending.name || 'Phone');
    d.onPaired?.(device.name);
    return page(200, pairPage(secret));
  });
  app.get('/pair.js', () => script(PAIR_JS));
  // The short code typed into the app's signed-out page: an iPhone Home Screen app keeps its own storage, so the QR link
  // (opened in Safari) cannot pair it. Wrong codes are counted across all callers, PAIR_GUESSES per 10 minutes.
  let guesses: number[] = [];
  app.post('/pair', originCheck(d.port, d.remote), async (c) => {
    const now = Date.now();
    guesses = guesses.filter((t) => now - t < PAIR_WINDOW_MS);
    if (guesses.length >= PAIR_GUESSES) return rpcJson(429, { ok: false, error: { code: 'too_many_tries', message: 'Too many wrong codes. Wait 10 minutes, then run desk web pair for a new one.' } });
    let code = '';
    try {
      const body = (await c.req.json()) as { code?: unknown };
      code = typeof body.code === 'string' ? body.code.slice(0, 32) : '';
    } catch {
      code = '';
    }
    const pending = d.dataDir && d.devices && isShortCode(code) ? redeemPendingCode(d.dataDir, 'phone', code, now) : null;
    if (!pending || !d.devices) {
      guesses.push(now);
      return rpcJson(401, { ok: false, error: { code: 'bad_code', message: 'That code is wrong or has expired. Run desk web pair on your Mac for a new one.' } });
    }
    const { device, secret } = d.devices.add(pending.name || 'Phone');
    d.onPaired?.(device.name);
    return rpcJson(200, { ok: true, value: { secret } });
  });
  // The app's files and /rpc answers are gzipped: a phone on Tailscale downloads a quarter as much. Pages and answers
  // holding a secret (/login, /pair) are not, so their size says nothing about it.
  const squeeze = compress({ encoding: 'gzip' });
  app.use('/rpc/*', squeeze);
  mountRpc(app, d);
  if (d.dev) app.get('/__dev/reload.js', () => script(DEV_RELOAD_JS));
  app.get('*', squeeze, serveUi(d.uiDir, { dev: d.dev }));
  return app;
}
