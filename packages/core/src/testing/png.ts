import { deflateSync } from 'node:zlib';
import { crc32 } from '../attachments/image';

/** A PNG chunk with its length and CRC. */
export function pngChunk(type: string, data: Buffer): Buffer {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}

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

/**
 * A real (decodable) 8-bit RGB PNG, for tests: `pixel(x, y)` gives each pixel's [r, g, b]. `interlaced` writes
 * Adam7 passes.
 */
export function encodePng(
  width: number,
  height: number,
  pixel: (x: number, y: number) => [number, number, number] = () => [255, 255, 255],
  opts: { interlaced?: boolean } = {},
): Buffer {
  const rows: Buffer[] = [];
  const addRows = (x0: number, y0: number, dx: number, dy: number) => {
    for (let y = y0; y < height; y += dy) {
      const cols = Math.ceil((width - x0) / dx);
      if (cols <= 0) return;
      const row = Buffer.alloc(1 + cols * 3); // filter byte 0: none
      for (let i = 0, x = x0; x < width; i++, x += dx) row.set(pixel(x, y), 1 + i * 3);
      rows.push(row);
    }
  };
  if (opts.interlaced) for (const [x0, y0, dx, dy] of ADAM7) addRows(x0, y0, dx, dy);
  else addRows(0, 0, 1, 1);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // colour type: RGB
  ihdr[12] = opts.interlaced ? 1 : 0;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', deflateSync(Buffer.concat(rows))),
    pngChunk('IEND', Buffer.alloc(0)),
  ]);
}

/** Header-only JPEG, GIF and WebP files for tests: enough bytes for header parsing, not decodable images. */
export const imageHeaders = {
  jpeg(width: number, height: number, sof = 0xc0): Buffer {
    const app0 = Buffer.from([0xff, 0xe0, 0x00, 0x10, 0x4a, 0x46, 0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00]);
    const dqt = Buffer.concat([Buffer.from([0xff, 0xdb, 0x00, 0x43, 0x00]), Buffer.alloc(64, 1)]);
    const sofSeg = Buffer.from([0xff, sof, 0x00, 0x11, 0x08, height >> 8, height & 0xff, width >> 8, width & 0xff, 0x03, 1, 0x22, 0, 2, 0x11, 1, 3, 0x11, 1]);
    return Buffer.concat([Buffer.from([0xff, 0xd8]), app0, dqt, Buffer.from([0xff, 0xff]), sofSeg, Buffer.from([0xff, 0xda, 0x00, 0x02, 0xff, 0xd9])]);
  },
  gif(width: number, height: number): Buffer {
    const b = Buffer.alloc(13);
    b.write('GIF89a', 0, 'ascii');
    b.writeUInt16LE(width, 6);
    b.writeUInt16LE(height, 8);
    return b;
  },
  webp(kind: 'VP8 ' | 'VP8L' | 'VP8X', width: number, height: number): Buffer {
    const b = Buffer.alloc(40);
    b.write('RIFF', 0, 'ascii');
    b.writeUInt32LE(32, 4);
    b.write('WEBP', 8, 'ascii');
    b.write(kind, 12, 'ascii');
    b.writeUInt32LE(20, 16);
    if (kind === 'VP8 ') {
      b.set([0x9d, 0x01, 0x2a], 23);
      b.writeUInt16LE(width, 26);
      b.writeUInt16LE(height, 28);
    } else if (kind === 'VP8L') {
      const w = width - 1;
      const h = height - 1;
      b[20] = 0x2f;
      b[21] = w & 0xff;
      b[22] = ((w >> 8) & 0x3f) | ((h & 0x03) << 6);
      b[23] = (h >> 2) & 0xff;
      b[24] = (h >> 10) & 0x0f;
    } else {
      b.writeUIntLE(width - 1, 24, 3);
      b.writeUIntLE(height - 1, 27, 3);
    }
    return b;
  },
};
