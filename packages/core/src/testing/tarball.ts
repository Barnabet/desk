import { gzipSync } from 'node:zlib';

/** One entry of a test archive. `type` defaults to a regular file; `pax` adds an extended header before it. */
export type TarEntry = { name: string; content?: string | Buffer; type?: '0' | '1' | '2' | '5' | '3'; mode?: number; linkname?: string; pax?: Record<string, string>; gnuLongName?: boolean };

function header(name: string, size: number, type: string, mode: number, linkname = ''): Buffer {
  const h = Buffer.alloc(512);
  h.write(name.slice(0, 100), 0, 'utf8');
  h.write(`${mode.toString(8).padStart(7, '0')}\0`, 100);
  h.write('0000000\0', 108);
  h.write('0000000\0', 116);
  h.write(`${size.toString(8).padStart(11, '0')}\0`, 124);
  h.write('00000000000\0', 136);
  h.write('        ', 148);
  h.write(type, 156);
  h.write(linkname.slice(0, 100), 157);
  h.write('ustar\0', 257);
  h.write('00', 263);
  let sum = 0;
  for (const b of h) sum += b;
  h.write(`${sum.toString(8).padStart(6, '0')}\0 `, 148);
  return h;
}

const pad = (b: Buffer) => Buffer.concat([b, Buffer.alloc((512 - (b.length % 512)) % 512)]);

function paxBody(fields: Record<string, string>): Buffer {
  let out = '';
  for (const [k, v] of Object.entries(fields)) {
    const body = ` ${k}=${v}\n`;
    let len = body.length + 1;
    while (`${len}${body}`.length !== len) len = `${len}${body}`.length;
    out += `${len}${body}`;
  }
  return Buffer.from(out, 'utf8');
}

/** Builds a gzipped tar like GitHub's (one root directory, a global pax header), for tests and fixtures. */
export function makeTarball(root: string, entries: TarEntry[], opts: { end?: boolean } = {}): Buffer {
  const parts: Buffer[] = [];
  const g = paxBody({ comment: '0123456789012345678901234567890123456789' });
  parts.push(header('pax_global_header', g.length, 'g', 0o644), pad(g));
  parts.push(header(`${root}/`, 0, '5', 0o755));
  for (const e of entries) {
    const content = Buffer.isBuffer(e.content) ? e.content : Buffer.from(e.content ?? '', 'utf8');
    const type = e.type ?? '0';
    const full = e.name.startsWith('/') || e.name.startsWith('..') ? e.name : `${root}/${e.name}`;
    if (e.pax) {
      const body = paxBody(e.pax);
      parts.push(header('PaxHeader', body.length, 'x', 0o644), pad(body));
    }
    if (e.gnuLongName) {
      const body = Buffer.from(`${full}\0`, 'utf8');
      parts.push(header('././@LongLink', body.length, 'L', 0o644), pad(body));
    }
    const size = type === '0' ? content.length : 0;
    parts.push(header(e.gnuLongName ? full.slice(0, 99) : full, size, type, e.mode ?? (type === '5' ? 0o755 : 0o644), e.linkname ?? ''));
    if (size) parts.push(pad(content));
  }
  if (opts.end !== false) parts.push(Buffer.alloc(1024));
  return gzipSync(Buffer.concat(parts));
}

/** Yields a buffer in chunks, like a network body. */
export async function* chunked(buf: Buffer, size = 1000): AsyncIterable<Uint8Array> {
  for (let i = 0; i < buf.length; i += size) yield buf.subarray(i, i + size);
}
