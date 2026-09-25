import { readFile, stat } from 'node:fs/promises';
import { extname, join, normalize, resolve, sep } from 'node:path';
import type { Context } from 'hono';

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.webmanifest': 'application/manifest+json',
};

/** The external script `desk web --dev` adds to index.html. */
export const DEV_RELOAD_TAG = '<script src="/__dev/reload.js"></script>';

/** Inline <script> bodies, scripts without `src`, and on*= attributes: code the CSP blocks, so index.html must have none. */
export function findInlineCode(html: string): string[] {
  const problems: string[] = [];
  for (const m of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)) {
    if ((m[2] ?? '').trim()) problems.push(`inline script: ${m[0].slice(0, 80)}`);
    else if (!/\bsrc\s*=/i.test(m[1] ?? '')) problems.push(`script without src: ${m[0].slice(0, 80)}`);
  }
  for (const m of html.matchAll(/<[a-z][a-z0-9-]*\b[^>]*?\s(on[a-z]+)\s*=/gi)) problems.push(`event handler attribute: ${m[1]}`);
  return problems;
}

const notFound = () => new Response('Not found', { status: 404, headers: { 'content-type': 'text/plain; charset=utf-8' } });

const notBuilt = (uiDir: string) =>
  new Response(`The Desk web app is not built yet (${uiDir} has no index.html).\nBuild it with: pnpm --filter @desk/web-ui build\n`, {
    status: 503,
    headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' },
  });

/** Serves the Angular build. It holds no secrets, so it needs no session; `/` is index.html and is never cached. */
export function serveUi(uiDir: string, o: { dev: boolean }): (c: Context) => Promise<Response> {
  const root = resolve(uiDir);
  return async (c) => {
    let rel: string;
    try {
      rel = decodeURIComponent(new URL(c.req.url).pathname).replace(/^\/+/, '');
    } catch {
      return notFound();
    }
    const isIndex = rel === '' || rel === 'index.html';
    const file = normalize(join(root, isIndex ? 'index.html' : rel));
    if (!file.startsWith(root + sep)) return notFound();
    let body: Buffer;
    try {
      if (!(await stat(file)).isFile()) return notFound();
      body = await readFile(file);
    } catch {
      return isIndex ? notBuilt(uiDir) : notFound();
    }
    if (isIndex) {
      const html = body.toString('utf8');
      const page = o.dev ? (html.includes('</body>') ? html.replace('</body>', `${DEV_RELOAD_TAG}</body>`) : html + DEV_RELOAD_TAG) : html;
      return new Response(page, { headers: { 'content-type': MIME['.html']!, 'cache-control': 'no-store' } });
    }
    return new Response(new Uint8Array(body), { headers: { 'content-type': MIME[extname(file).toLowerCase()] ?? 'application/octet-stream' } });
  };
}
