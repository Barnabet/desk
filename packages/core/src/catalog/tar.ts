import { once } from 'node:events';
import { createGunzip } from 'node:zlib';
import { ValidationError } from '../errors';

export type ExtractCaps = {
  /** Compressed bytes read from the input. */
  maxDownload: number;
  /** Total bytes of the extracted files. */
  maxBytes: number;
  maxFiles: number;
  maxFileBytes: number;
};

/** The skill store's limits, plus a download cap for the whole repository archive. */
export const DEFAULT_CAPS: ExtractCaps = { maxDownload: 50 * 1024 * 1024, maxBytes: 10 * 1024 * 1024, maxFiles: 200, maxFileBytes: 2 * 1024 * 1024 };

/** Decompressed bytes allowed per compressed byte of the cap, against decompression bombs. */
const INFLATE_FACTOR = 8;

export type ExtractedFile = { path: string; content: Buffer; mode: number };

const BLOCK = 512;

/**
 * Streams a `.tar.gz` repository archive (one root directory, as GitHub serves them) and returns the regular files
 * under `prefix` (a path relative to that root; '' for everything), with paths relative to the prefix.
 *
 * The whole archive is refused if any entry has an absolute path or a `..` segment. Under the prefix, links, devices
 * and FIFOs are refused, as is anything over the caps. Entries outside the prefix are skipped without being kept, except
 * regular files at the archive root whose names match `rootFiles` (the repository's LICENSE, for skills in subfolders).
 */
export async function extractSubtree(
  input: AsyncIterable<Uint8Array>,
  prefix: string,
  opts: { caps?: Partial<ExtractCaps>; rootFiles?: RegExp } = {},
): Promise<{ files: ExtractedFile[]; rootFiles: ExtractedFile[] }> {
  const c = { ...DEFAULT_CAPS, ...opts.caps };
  const cleanPrefix = prefix.replace(/^\/+|\/+$/g, '');
  const gunzip = createGunzip();
  const feed = (async () => {
    let read = 0;
    try {
      for await (const chunk of input) {
        read += chunk.length;
        if (read > c.maxDownload) throw new ValidationError(`The download is larger than ${mb(c.maxDownload)}`);
        if (!gunzip.write(chunk)) await once(gunzip, 'drain');
      }
      gunzip.end();
    } catch (err) {
      gunzip.destroy(err as Error);
    }
  })();

  const parser = new TarReader(cleanPrefix, c, c.maxDownload * INFLATE_FACTOR, opts.rootFiles ?? null);
  try {
    for await (const chunk of gunzip) parser.push(chunk as Buffer);
  } catch (err) {
    if (err instanceof ValidationError) throw err;
    throw new ValidationError(`The archive could not be read: ${(err as Error).message}`);
  } finally {
    await feed;
  }
  parser.finish();
  return { files: parser.files, rootFiles: parser.rootFiles };
}

type Header = { name: string; size: number; type: string; mode: number; linkname: string };

class TarReader {
  readonly files: ExtractedFile[] = [];
  readonly rootFiles: ExtractedFile[] = [];
  private buf: Buffer = Buffer.alloc(0);
  private inflated = 0;
  private total = 0;
  private ended = false;
  /** Current entry data: bytes still to consume, padding after it, and where they go. */
  private data: { remaining: number; padding: number; sink: Buffer[] | null; header: Header; kind: 'file' | 'root' | 'pax' | 'longname' } | null = null;
  private pax: Record<string, string> = {};
  private longName: string | null = null;

  constructor(
    private readonly prefix: string,
    private readonly caps: ExtractCaps,
    private readonly maxInflated: number,
    private readonly rootPattern: RegExp | null,
  ) {}

  push(chunk: Buffer): void {
    this.inflated += chunk.length;
    if (this.inflated > this.maxInflated) throw new ValidationError('The archive expands to more than is allowed');
    if (this.ended) return;
    this.buf = this.buf.length ? Buffer.concat([this.buf, chunk]) : chunk;
    for (;;) {
      if (this.data) {
        if (!this.consumeData()) return;
        continue;
      }
      if (this.buf.length < BLOCK) return;
      const block = this.buf.subarray(0, BLOCK);
      this.buf = this.buf.subarray(BLOCK);
      if (block.every((b) => b === 0)) {
        this.ended = true;
        return;
      }
      this.startEntry(parseHeader(block));
    }
  }

  finish(): void {
    if (!this.ended || this.data) throw new ValidationError('The archive is truncated');
  }

  /** Consumes entry data from the buffer; false when more input is needed. */
  private consumeData(): boolean {
    const d = this.data!;
    if (d.remaining > 0) {
      const take = Math.min(d.remaining, this.buf.length);
      if (take === 0) return false;
      if (d.sink) d.sink.push(Buffer.from(this.buf.subarray(0, take)));
      this.buf = this.buf.subarray(take);
      d.remaining -= take;
      if (d.remaining > 0) return false;
    }
    if (this.buf.length < d.padding) return false;
    this.buf = this.buf.subarray(d.padding);
    this.data = null;
    this.completeEntry(d);
    return true;
  }

  private startEntry(h: Header): void {
    const padding = (BLOCK - (h.size % BLOCK)) % BLOCK;
    if (h.type === 'x' || h.type === 'L') {
      if (h.size > 1024 * 1024) throw new ValidationError('The archive has an oversized extended header');
      this.data = { remaining: h.size, padding, sink: [], header: h, kind: h.type === 'x' ? 'pax' : 'longname' };
      return;
    }
    if (h.type === 'g' || h.type === 'K') {
      // Global pax header (GitHub puts the commit there) and GNU long link names: skipped.
      this.data = { remaining: h.size, padding, sink: null, header: h, kind: 'longname' };
      return;
    }
    const name = this.pax.path ?? this.longName ?? h.name;
    const size = this.pax.size !== undefined ? Number(this.pax.size) : h.size;
    this.pax = {};
    this.longName = null;
    const header = { ...h, name, size };
    const realPadding = (BLOCK - (size % BLOCK)) % BLOCK;

    const segments = name.split('/').filter((s) => s !== '' && s !== '.');
    if (name.startsWith('/') || segments.includes('..')) throw new ValidationError(`The archive contains an unsafe path: ${name}`);
    const rel = this.relative(segments);
    const isFile = h.type === '0' || h.type === '\0' || h.type === '7';

    if (rel !== null && h.type !== '5' && !isFile) {
      throw new ValidationError(`${rel || name} is a ${h.type === '2' ? 'symbolic link' : h.type === '1' ? 'hard link' : 'special file'}, which skills cannot contain`);
    }
    let sink: Buffer[] | null = null;
    if (rel !== null && isFile) {
      if (size > this.caps.maxFileBytes) throw new ValidationError(`${rel} is larger than ${mb(this.caps.maxFileBytes)}`);
      if (this.files.length + 1 > this.caps.maxFiles) throw new ValidationError(`A skill can hold at most ${this.caps.maxFiles} files`);
      this.total += size;
      if (this.total > this.caps.maxBytes) throw new ValidationError(`The skill is larger than ${mb(this.caps.maxBytes)}`);
      sink = [];
    } else if (rel === null && isFile && this.rootPattern && segments.length === 2 && this.rootPattern.test(segments[1]!) && size <= 256 * 1024) {
      this.data = { remaining: size, padding: realPadding, sink: [], header: { ...header, name: segments[1]! }, kind: 'root' };
      return;
    }
    this.data = { remaining: size, padding: realPadding, sink, header: { ...header, name: rel ?? name }, kind: 'file' };
  }

  private completeEntry(d: NonNullable<TarReader['data']>): void {
    if (d.kind === 'pax' && d.sink) {
      this.pax = parsePax(Buffer.concat(d.sink).toString('utf8'));
      return;
    }
    if (d.kind === 'longname') {
      if (d.sink) this.longName = Buffer.concat(d.sink).toString('utf8').replace(/\0.*$/s, '');
      return;
    }
    if (d.sink) (d.kind === 'root' ? this.rootFiles : this.files).push({ path: d.header.name, content: Buffer.concat(d.sink), mode: d.header.mode });
  }

  /** The path relative to the prefix (after the archive's root directory), or null when outside it. */
  private relative(segments: string[]): string | null {
    const inner = segments.slice(1).join('/');
    if (!this.prefix) return inner;
    if (inner === this.prefix) return '';
    return inner.startsWith(`${this.prefix}/`) ? inner.slice(this.prefix.length + 1) : null;
  }
}

function parseHeader(block: Buffer): Header {
  const stored = octal(block.subarray(148, 156));
  let sum = 0;
  for (let i = 0; i < BLOCK; i++) sum += i >= 148 && i < 156 ? 32 : block[i]!;
  if (stored !== sum) throw new ValidationError('The archive is corrupt (bad header checksum)');
  const name = cstr(block.subarray(0, 100));
  const magic = cstr(block.subarray(257, 263));
  const prefix = magic.startsWith('ustar') ? cstr(block.subarray(345, 500)) : '';
  return {
    name: prefix ? `${prefix}/${name}` : name,
    mode: octal(block.subarray(100, 108)),
    size: size(block.subarray(124, 136)),
    type: String.fromCharCode(block[156]!),
    linkname: cstr(block.subarray(157, 257)),
  };
}

function size(field: Buffer): number {
  if (field[0]! & 0x80) {
    // GNU base-256: big-endian, first byte's high bit is the marker.
    let n = field[0]! & 0x7f;
    for (let i = 1; i < field.length; i++) n = n * 256 + field[i]!;
    return n;
  }
  return octal(field);
}

function octal(field: Buffer): number {
  const s = cstr(field).trim();
  return s ? Number.parseInt(s, 8) : 0;
}

function cstr(field: Buffer): string {
  const end = field.indexOf(0);
  return field.subarray(0, end === -1 ? field.length : end).toString('utf8');
}

/** pax records: "<len> <key>=<value>\n". */
function parsePax(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  let i = 0;
  while (i < text.length) {
    const space = text.indexOf(' ', i);
    if (space === -1) break;
    const len = Number.parseInt(text.slice(i, space), 10);
    if (!Number.isFinite(len) || len <= 0) break;
    const record = text.slice(space + 1, i + len - 1);
    const eq = record.indexOf('=');
    if (eq > 0) out[record.slice(0, eq)] = record.slice(eq + 1);
    i += len;
  }
  return out;
}

const mb = (n: number) => `${Math.round(n / 1024 / 1024)} MB`;
