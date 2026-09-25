import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import { Readable } from 'node:stream';
import { Hono } from 'hono';
import { NotFoundError, ValidationError } from '@desk/core';
import type { AppDeps } from '../app';

const SHA256 = /^[0-9a-f]{64}$/;

/** Images agents looked at (view_image), by content digest. Immutable, so clients may cache them for good. */
export function attachmentRoutes({ runtime }: AppDeps): Hono {
  const r = new Hono();

  r.get('/attachments/:sha256', async (c) => {
    const sha256 = c.req.param('sha256');
    if (!SHA256.test(sha256)) throw new ValidationError('An attachment id is a sha256: 64 lowercase hex characters');
    const found = runtime.attachments.find(sha256);
    const st = found ? await stat(found.path).catch(() => null) : null;
    if (!found || !st?.isFile()) throw new NotFoundError(`No attachment ${sha256}`);
    const body = Readable.toWeb(createReadStream(found.path)) as unknown as ReadableStream<Uint8Array>;
    return c.body(body, 200, {
      'content-type': found.media_type,
      'content-length': String(st.size),
      'cache-control': 'private, max-age=31536000, immutable',
      'x-content-type-options': 'nosniff',
    });
  });

  return r;
}
