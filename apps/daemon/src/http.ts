import { timingSafeEqual } from 'node:crypto';
import type { Context, MiddlewareHandler } from 'hono';
import type { ContentfulStatusCode } from 'hono/utils/http-status';
import { ZodError, type z } from 'zod';
import { DeskError, ValidationError } from '@desk/core';

const STATUS: Record<DeskError['code'], ContentfulStatusCode> = { not_found: 404, conflict: 409, invalid: 400 };

export class HttpError extends Error {
  constructor(
    readonly status: ContentfulStatusCode,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export function errorResponse(c: Context, err: unknown) {
  if (err instanceof HttpError) return c.json({ error: { code: err.code, message: err.message } }, err.status);
  if (err instanceof DeskError) return c.json({ error: { code: err.code, message: err.message } }, STATUS[err.code]);
  if (err instanceof ZodError) return c.json({ error: { code: 'invalid', message: 'Invalid request', details: err.issues } }, 400);
  console.error('[deskd] internal error:', err);
  return c.json({ error: { code: 'internal', message: err instanceof Error ? err.message : 'Internal error' } }, 500);
}

export async function body<S extends z.ZodType>(c: Context, schema: S): Promise<z.output<S>> {
  let raw: unknown;
  try {
    raw = await c.req.json();
  } catch {
    throw new ValidationError('Request body must be JSON');
  }
  return schema.parse(raw);
}

export function intQuery(c: Context, name: string): number | undefined {
  const v = c.req.query(name);
  if (v === undefined || v === '') return undefined;
  const n = Number(v);
  if (!Number.isInteger(n) || n < 0) throw new ValidationError(`Query parameter ${name} must be a non-negative integer`);
  return n;
}

export function tokenMatches(expected: string, provided: string | undefined | null): boolean {
  if (!provided) return false;
  const a = Buffer.from(expected);
  const b = Buffer.from(provided);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function bearerAuth(token: string): MiddlewareHandler {
  return async (c, next) => {
    const header = c.req.header('authorization') ?? '';
    const provided = header.startsWith('Bearer ') ? header.slice(7) : null;
    if (!tokenMatches(token, provided)) throw new HttpError(401, 'unauthorized', 'Missing or invalid bearer token');
    await next();
  };
}
