import type { MiddlewareHandler } from 'hono';

/**
 * The private HTTPS address a paired phone uses (`desk web --remote-url`), such as the one Tailscale Serve gives this
 * Mac. desk web still listens on 127.0.0.1 only, and Tailscale Serve forwards to it.
 */
export type RemoteOrigin = { host: string; origin: string };

/** An https:// origin with no path, query, fragment or credentials, else null. */
export function parseRemoteUrl(text: string): RemoteOrigin | null {
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || (url.pathname !== '/' && url.pathname !== '')) return null;
  return { host: url.host, origin: url.origin };
}

/**
 * desk web answers only on 127.0.0.1:<port>, and on the remote address when one is set: `localhost` may resolve to
 * ::1, where another process can hold the port. A session secret is still needed on either.
 */
export const hostAllowed = (host: string | null | undefined, port: number, remote?: RemoteOrigin | null): boolean =>
  host === `127.0.0.1:${port}` || (!!remote && host === remote.host);
export const originAllowed = (origin: string | null | undefined, port: number, remote?: RemoteOrigin | null): boolean =>
  origin === `http://127.0.0.1:${port}` || (!!remote && origin === remote.origin);

/** Electron's production CSP, plus frame-ancestors and the /push socket's origin (and the remote one's). */
export function contentSecurityPolicy(port: number, remote?: RemoteOrigin | null): string {
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self' ws://127.0.0.1:${port}${remote ? ` wss://${remote.host}` : ''}`,
    "object-src 'none'",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join('; ');
}

export function securityHeaderValues(port: number, remote?: RemoteOrigin | null): Record<string, string> {
  return {
    'content-security-policy': contentSecurityPolicy(port, remote),
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
    'cross-origin-opener-policy': 'same-origin',
    'cross-origin-resource-policy': 'same-origin',
  };
}

/** Adds the security headers to every response, refusals included. Register it first. */
export function securityHeaders(port: () => number, remote: () => RemoteOrigin | null = () => null): MiddlewareHandler {
  return async (c, next) => {
    await next();
    for (const [name, value] of Object.entries(securityHeaderValues(port(), remote()))) c.res.headers.set(name, value);
  };
}

/** 421 unless Host is exactly 127.0.0.1:<port> or the remote address's host: blocks DNS rebinding. */
export function hostCheck(port: () => number, remote: () => RemoteOrigin | null = () => null): MiddlewareHandler {
  return async (c, next) => {
    if (!hostAllowed(c.req.header('host'), port(), remote())) return c.text('Misdirected request: open Desk at the http://127.0.0.1 address desk web printed.', 421);
    await next();
  };
}

/** 403 unless Origin is exactly http://127.0.0.1:<port> or the remote address. */
export function originCheck(port: () => number, remote: () => RemoteOrigin | null = () => null): MiddlewareHandler {
  return async (c, next) => {
    if (!originAllowed(c.req.header('origin'), port(), remote())) return c.json({ ok: false, error: { code: 'forbidden', message: 'Cross-origin requests are refused.' } }, 403);
    await next();
  };
}
