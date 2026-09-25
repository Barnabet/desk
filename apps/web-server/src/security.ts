import type { MiddlewareHandler } from 'hono';

/** desk web answers only on 127.0.0.1:<port>: `localhost` may resolve to ::1, where another process can hold the port. */
export const hostAllowed = (host: string | null | undefined, port: number): boolean => host === `127.0.0.1:${port}`;
export const originAllowed = (origin: string | null | undefined, port: number): boolean => origin === `http://127.0.0.1:${port}`;

/** Electron's production CSP, plus frame-ancestors and the /push socket's origin. */
export function contentSecurityPolicy(port: number): string {
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self' ws://127.0.0.1:${port}`,
    "object-src 'none'",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join('; ');
}

export function securityHeaderValues(port: number): Record<string, string> {
  return {
    'content-security-policy': contentSecurityPolicy(port),
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
    'cross-origin-opener-policy': 'same-origin',
    'cross-origin-resource-policy': 'same-origin',
  };
}

/** Adds the security headers to every response, refusals included. Register it first. */
export function securityHeaders(port: () => number): MiddlewareHandler {
  return async (c, next) => {
    await next();
    for (const [name, value] of Object.entries(securityHeaderValues(port()))) c.res.headers.set(name, value);
  };
}

/** 421 unless Host is exactly 127.0.0.1:<port>: blocks DNS rebinding. */
export function hostCheck(port: () => number): MiddlewareHandler {
  return async (c, next) => {
    if (!hostAllowed(c.req.header('host'), port())) return c.text('Misdirected request: open Desk at the http://127.0.0.1 address desk web printed.', 421);
    await next();
  };
}

/** 403 unless Origin is exactly http://127.0.0.1:<port>. */
export function originCheck(port: () => number): MiddlewareHandler {
  return async (c, next) => {
    if (!originAllowed(c.req.header('origin'), port())) return c.json({ ok: false, error: { code: 'forbidden', message: 'Cross-origin requests are refused.' } }, 403);
    await next();
  };
}
