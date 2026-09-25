import { Hono } from 'hono';
import type { LoginCodes } from './auth';
import { DEV_RELOAD_JS } from './dev';
import { LOGIN_JS, loginFailedPage, loginPage } from './login';
import { mountRpc, type RpcDeps } from './rpc';
import { hostCheck, securityHeaders } from './security';
import { serveUi } from './static';

export type WebAppDeps = RpcDeps & {
  codes: LoginCodes;
  uiDir: string;
  dev: boolean;
  /** A spent login code came back, so the session it opened was revoked. */
  onReplay?(): void;
};

const page = (status: number, html: string) => new Response(html, { status, headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' } });
const script = (js: string) => new Response(js, { headers: { 'content-type': 'text/javascript; charset=utf-8', 'cache-control': 'no-store' } });

/** desk web's HTTP routes. Every response gets the security headers; every request must name Host 127.0.0.1:<port>. */
export function createWebApp(d: WebAppDeps): Hono {
  const app = new Hono();
  app.use('*', securityHeaders(d.port));
  app.use('*', hostCheck(d.port));
  app.get('/healthz', () => new Response(JSON.stringify({ ok: true }), { headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' } }));
  app.get('/login', (c) => {
    // Hono answers HEAD with the GET route: a HEAD (a link preview, a prefetch) must not spend the code.
    if (c.req.method === 'HEAD') return page(200, '');
    const r = d.codes.redeem(c.req.query('code') ?? '');
    if (r.ok) return page(200, loginPage(r.secret));
    if (r.reason === 'replayed') d.onReplay?.();
    return page(401, loginFailedPage());
  });
  app.get('/login.js', () => script(LOGIN_JS));
  mountRpc(app, d);
  if (d.dev) app.get('/__dev/reload.js', () => script(DEV_RELOAD_JS));
  app.get('*', serveUi(d.uiDir, { dev: d.dev }));
  return app;
}
