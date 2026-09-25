import { createInflate } from 'node:zlib';
import type { ImageMediaType } from '@desk/protocol';

/** What a file header says about an image models can take. */
export type ImageInfo = { media_type: ImageMediaType; width: number; height: number };

const PNG_SIG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

const ascii = (b: Uint8Array, at: number, len: number) => String.fromCharCode(...b.subarray(at, at + len));
const u16be = (b: Uint8Array, at: number) => (b[at]! << 8) | b[at + 1]!;
const u16le = (b: Uint8Array, at: number) => b[at]! | (b[at + 1]! << 8);
const u24le = (b: Uint8Array, at: number) => b[at]! | (b[at + 1]! << 8) | (b[at + 2]! << 16);
const u32be = (b: Uint8Array, at: number) => ((b[at]! << 24) >>> 0) + ((b[at + 1]! << 16) | (b[at + 2]! << 8) | b[at + 3]!);

/** The image format from the magic bytes alone (enough of a header to decide: 16 bytes). */
export function sniffImageType(b: Uint8Array): ImageMediaType | null {
  if (b.length >= 8 && PNG_SIG.every((v, i) => b[i] === v)) return 'image/png';
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return 'image/jpeg';
  if (b.length >= 6 && (ascii(b, 0, 6) === 'GIF87a' || ascii(b, 0, 6) === 'GIF89a')) return 'image/gif';
  if (b.length >= 12 && ascii(b, 0, 4) === 'RIFF' && ascii(b, 8, 4) === 'WEBP') return 'image/webp';
  return null;
}

function pngSize(b: Uint8Array): [number, number] | null {
  if (b.length < 24 || ascii(b, 12, 4) !== 'IHDR') return null;
  return [u32be(b, 16), u32be(b, 20)];
}

type JpegFrame = { marker: number; precision: number; width: number; height: number };

/** Walks JPEG segments to the first SOFn (baseline, progressive, lossless, arithmetic) frame header. */
function jpegFrame(b: Uint8Array): JpegFrame | null {
  let at = 2;
  while (at + 1 < b.length) {
    if (b[at] !== 0xff) return null;
    while (b[at] === 0xff && at < b.length) at++; // fill bytes
    const marker = b[at]!;
    at++;
    if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue; // no length
    if (marker === 0xd9 || marker === 0xda) return null; // end of image, or scan data before any frame header
    if (at + 2 > b.length) return null;
    const len = u16be(b, at);
    if (len < 2) return null;
    const isSof = marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc;
    if (isSof) return at + 7 <= b.length ? { marker, precision: b[at + 2]!, width: u16be(b, at + 5), height: u16be(b, at + 3) } : null;
    at += len;
  }
  return null;
}

function jpegSize(b: Uint8Array): [number, number] | null {
  const frame = jpegFrame(b);
  return frame ? [frame.width, frame.height] : null;
}

function gifSize(b: Uint8Array): [number, number] | null {
  return b.length >= 10 ? [u16le(b, 6), u16le(b, 8)] : null;
}

function webpSize(b: Uint8Array): [number, number] | null {
  if (b.length < 30) return null;
  switch (ascii(b, 12, 4)) {
    case 'VP8 ': // lossy: frame tag (3 bytes), start code 9d 01 2a, then 14-bit sizes
      if (b[23] !== 0x9d || b[24] !== 0x01 || b[25] !== 0x2a) return null;
      return [u16le(b, 26) & 0x3fff, u16le(b, 28) & 0x3fff];
    case 'VP8L': {
      if (b[20] !== 0x2f) return null;
      const b1 = b[21]!, b2 = b[22]!, b3 = b[23]!, b4 = b[24]!;
      return [1 + (b1 | ((b2 & 0x3f) << 8)), 1 + ((b2 >> 6) | (b3 << 2) | ((b4 & 0x0f) << 10))];
    }
    case 'VP8X': // extended: canvas size minus one, 24 bits each
      return [1 + u24le(b, 24), 1 + u24le(b, 27)];
    default:
      return null;
  }
}

/** Format and pixel size of a PNG, JPEG, GIF or WebP from its header; null for anything else or a corrupt header. */
export function readImageInfo(b: Uint8Array): ImageInfo | null {
  const media_type = sniffImageType(b);
  if (!media_type) return null;
  const size =
    media_type === 'image/png' ? pngSize(b) : media_type === 'image/jpeg' ? jpegSize(b) : media_type === 'image/gif' ? gifSize(b) : webpSize(b);
  if (!size || size[0] <= 0 || size[1] <= 0) return null;
  return { media_type, width: size[0], height: size[1] };
}

/**
 * Format and pixel size from the start of a base64 data URL, decoding only as much as the header needs
 * (a JPEG's frame header may follow large EXIF or ICC segments). Null when it is not a readable image.
 */
export function readDataUrlInfo(url: string): ImageInfo | null {
  const comma = url.indexOf(',');
  if (!url.startsWith('data:') || comma < 0 || !url.slice(0, comma).endsWith(';base64')) return null;
  const body = url.length - comma - 1;
  for (const chars of [4 * 1024, 256 * 1024, body]) {
    const n = Math.min(body, chars - (chars % 4));
    const info = readImageInfo(Buffer.from(url.slice(comma + 1, comma + 1 + n), 'base64'));
    if (info || n >= body) return info;
  }
  return null;
}

export const IMAGE_EXT: Record<ImageMediaType, string> = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp' };

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

/** CRC-32 as PNG chunks use it (zlib's polynomial). */
export function crc32(b: Uint8Array, from = 0, to = b.length): number {
  let c = 0xffffffff;
  for (let i = from; i < to; i++) c = CRC_TABLE[(c ^ b[i]!) & 0xff]! ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** Channels per PNG colour type (grey, -, RGB, palette, grey+alpha, -, RGBA). */
const PNG_CHANNELS: Record<number, number> = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 };
/** Adam7 passes: first column and row, then column and row steps. */
const ADAM7 = [
  [0, 0, 8, 8],
  [4, 0, 8, 8],
  [0, 4, 4, 8],
  [2, 0, 4, 4],
  [0, 2, 2, 4],
  [1, 0, 2, 2],
  [0, 1, 1, 2],
] as const;

/** Bytes of filtered scanlines a PNG's image data must inflate to (a filter byte per row). */
function pngRawBytes(width: number, height: number, bitsPerPixel: number, interlaced: boolean): number {
  const rows = (w: number, h: number) => (w > 0 && h > 0 ? h * (1 + Math.ceil((w * bitsPerPixel) / 8)) : 0);
  if (!interlaced) return rows(width, height);
  return ADAM7.reduce((sum, [x0, y0, dx, dy]) => sum + rows(Math.ceil((width - x0) / dx), Math.ceil((height - y0) / dy)), 0);
}

/**
 * Inflates zlib data without keeping it, stopping as soon as `enough` bytes came out (a stream that inflates far
 * past what its image needs costs no more than a real one): the bytes seen, or null when the stream is corrupt or
 * ends first.
 */
export function inflatedSize(parts: Uint8Array[], enough: number): Promise<number | null> {
  return new Promise((resolve) => {
    const inflate = createInflate();
    let size = 0;
    let done = false;
    const finish = (result: number | null) => {
      if (done) return;
      done = true;
      resolve(result);
      inflate.destroy();
    };
    inflate.on('data', (chunk: Buffer) => {
      size += chunk.length;
      if (size >= enough) finish(size);
    });
    inflate.on('end', () => finish(size));
    inflate.on('error', () => finish(null));
    for (const p of parts) {
      if (done) break;
      inflate.write(p);
    }
    if (!done) inflate.end();
  });
}

async function pngProblem(b: Uint8Array, info: ImageInfo): Promise<string | null> {
  const idat: Uint8Array[] = [];
  let header: { depth: number; colour: number; interlaced: boolean } | null = null;
  let at = 8;
  for (;;) {
    if (at === b.length) return 'it has no IEND chunk (the file is incomplete)';
    if (at + 12 > b.length) return 'the file is truncated (it ends inside a chunk)';
    const len = u32be(b, at);
    const type = ascii(b, at + 4, 4);
    if (at + 12 + len > b.length) return 'the file is truncated (it ends inside a chunk)';
    if (crc32(b, at + 4, at + 8 + len) !== u32be(b, at + 8 + len)) return `its ${type.replace(/[^\x20-\x7e]/g, '?')} chunk fails its CRC check (corrupt file)`;
    if (type === 'IHDR') header = { depth: b[at + 16]!, colour: b[at + 17]!, interlaced: b[at + 20] === 1 };
    else if (type === 'IDAT') idat.push(b.subarray(at + 8, at + 8 + len));
    at += 12 + len;
    if (type === 'IEND') break;
  }
  const channels = header ? PNG_CHANNELS[header.colour] : undefined;
  if (!header || !channels) return 'its header is invalid';
  if (!idat.length) return 'it has no image data';
  const expected = pngRawBytes(info.width, info.height, channels * header.depth, header.interlaced);
  const inflated = await inflatedSize(idat, expected);
  return inflated === null || inflated < expected ? 'its image data is corrupt or incomplete' : null;
}

function jpegProblem(b: Uint8Array): string | null {
  const frame = jpegFrame(b);
  if (!frame) return 'its header is invalid';
  if (frame.marker >= 0xc9) return 'it is an arithmetic-coded JPEG, which model providers cannot decode';
  if (frame.marker !== 0xc0 && frame.marker !== 0xc1 && frame.marker !== 0xc2) return 'it is a lossless or hierarchical JPEG, which model providers cannot decode';
  if (frame.precision !== 8) return `it is a ${frame.precision}-bit JPEG, which model providers cannot decode`;
  return null;
}

function webpProblem(b: Uint8Array): string | null {
  const end = 8 + (b[4]! | (b[5]! << 8) | (b[6]! << 16)) + b[7]! * 0x1000000;
  if (end > b.length) return 'the file is truncated';
  let at = 12;
  let image = false;
  while (at + 8 <= end) {
    const type = ascii(b, at, 4);
    const len = (b[at + 4]! | (b[at + 5]! << 8) | (b[at + 6]! << 16)) + b[at + 7]! * 0x1000000;
    if (at + 8 + len > end) return 'the file is truncated (it ends inside a chunk)';
    if (type === 'VP8 ' || type === 'VP8L' || type === 'ANMF') image = true;
    at += 8 + len + (len % 2);
  }
  return image ? null : 'it has no image data';
}

/**
 * Why model providers would refuse a whole image file, or null. Checked through the local proxy (2026-09-25):
 * claude-opus-5-5 and gpt-6-sol both refuse PNGs that are truncated, lack IEND, fail a chunk CRC or hold corrupt
 * image data, arithmetic-coded JPEGs and truncated WebPs, and both accept truncated JPEGs and GIFs.
 */
export async function imageProblem(b: Uint8Array, info: ImageInfo): Promise<string | null> {
  switch (info.media_type) {
    case 'image/png':
      return pngProblem(b, info);
    case 'image/jpeg':
      return jpegProblem(b);
    case 'image/webp':
      return webpProblem(b);
    case 'image/gif':
      return null;
  }
}

/** Long edge providers scale images down to before counting them. */
const PROVIDER_LONG_EDGE = 1568;

/** Estimated prompt tokens for an image: W×H/750 after the provider's downscale to a 1568 px long edge. */
export function imageTokens(width: number, height: number): number {
  const scale = Math.min(1, PROVIDER_LONG_EDGE / Math.max(width, height, 1));
  return Math.ceil((width * scale * height * scale) / 750);
}
