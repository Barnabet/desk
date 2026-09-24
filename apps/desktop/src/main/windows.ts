import { readFile } from 'node:fs/promises';
import { extname, join, normalize, sep } from 'node:path';
import { BrowserWindow, protocol, session } from 'electron';

export const APP_SCHEME = 'desk-app';
export const APP_ORIGIN = `${APP_SCHEME}://ui`;

const PROD_CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "frame-src 'none'",
  "form-action 'none'",
].join('; ');

/** Vite's dev server needs inline scripts (React refresh) and its HMR socket. */
const devCsp = (origin: string) =>
  [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self' data:",
    `connect-src 'self' ${origin.replace(/^http/, 'ws')}`,
    "object-src 'none'",
  ].join('; ');

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.map': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
};

/** Must run before `app.ready`. */
export function registerAppScheme(): void {
  protocol.registerSchemesAsPrivileged([{ scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
}

/** Serves the built renderer from `dir` on desk-app://ui with the production CSP. */
export function serveRenderer(dir: string): void {
  const root = normalize(dir);
  protocol.handle(APP_SCHEME, async (req) => {
    const rel = decodeURIComponent(new URL(req.url).pathname).replace(/^\/+/, '') || 'index.html';
    const file = normalize(join(root, rel));
    if (!file.startsWith(root + sep)) return new Response('Not found', { status: 404 });
    try {
      const body = await readFile(file);
      return new Response(new Uint8Array(body), {
        headers: { 'content-type': MIME[extname(file)] ?? 'application/octet-stream', 'content-security-policy': PROD_CSP },
      });
    } catch {
      return new Response('Not found', { status: 404 });
    }
  });
}

export function rendererUrl(): string {
  return process.env.DESK_RENDERER_URL ?? `${APP_ORIGIN}/index.html`;
}

/** Whether a URL belongs to the app's own renderer (and may talk to main or be navigated to). */
export function isAppUrl(url: string): boolean {
  if (url.startsWith(`${APP_ORIGIN}/`)) return true;
  const dev = process.env.DESK_RENDERER_URL;
  return !!dev && url.startsWith(`${new URL(dev).origin}/`);
}

/** Denies every permission except writing to the clipboard; applies the dev CSP when on Vite. */
export function hardenSession(): void {
  const s = session.defaultSession;
  s.setPermissionRequestHandler((_wc, permission, cb) => cb(permission === 'clipboard-sanitized-write'));
  const dev = process.env.DESK_RENDERER_URL;
  if (dev) {
    const origin = new URL(dev).origin;
    s.webRequest.onHeadersReceived({ urls: [`${origin}/*`] }, (details, cb) =>
      cb({ responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': [devCsp(origin)] } }),
    );
  }
}

export function createMainWindow(o: { preload: string; route?: string }): BrowserWindow {
  const mac = process.platform === 'darwin';
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    show: false,
    title: 'Desk',
    backgroundColor: '#EFEAE0',
    ...(mac ? { titleBarStyle: 'hiddenInset' as const, trafficLightPosition: { x: 16, y: 16 } } : {}),
    webPreferences: { preload: o.preload, contextIsolation: true, sandbox: true, nodeIntegration: false, webSecurity: true, spellcheck: true },
  });
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', (e, url) => {
    if (!isAppUrl(url)) e.preventDefault();
  });
  win.once('ready-to-show', () => win.show());
  void win.loadURL(rendererUrl() + (o.route ?? ''));
  return win;
}
