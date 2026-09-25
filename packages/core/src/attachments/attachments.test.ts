import { mkdtemp, readdir, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { deflateSync } from 'node:zlib';
import { encodePng, imageHeaders as fixtures, pngChunk } from '../testing/png';
import { imageProblem, imageTokens, inflatedSize, readDataUrlInfo, readImageInfo, sniffImageType, type ImageInfo } from './image';
import { AttachmentStore } from './store';

describe('image headers', () => {
  it('reads format and size of PNG, JPEG (any SOFn), GIF and every WebP flavour', () => {
    expect(readImageInfo(encodePng(3, 2))).toEqual({ media_type: 'image/png', width: 3, height: 2 });
    expect(readImageInfo(fixtures.jpeg(1240, 1754))).toEqual({ media_type: 'image/jpeg', width: 1240, height: 1754 });
    expect(readImageInfo(fixtures.jpeg(640, 480, 0xc2))).toEqual({ media_type: 'image/jpeg', width: 640, height: 480 });
    expect(readImageInfo(fixtures.gif(320, 200))).toEqual({ media_type: 'image/gif', width: 320, height: 200 });
    expect(readImageInfo(fixtures.webp('VP8 ', 800, 600))).toEqual({ media_type: 'image/webp', width: 800, height: 600 });
    expect(readImageInfo(fixtures.webp('VP8L', 1000, 16383))).toEqual({ media_type: 'image/webp', width: 1000, height: 16383 });
    expect(readImageInfo(fixtures.webp('VP8X', 9000, 3))).toEqual({ media_type: 'image/webp', width: 9000, height: 3 });
  });

  it('refuses other formats and corrupt headers', () => {
    expect(readImageInfo(Buffer.from('%PDF-1.7\n'))).toBeNull();
    expect(readImageInfo(Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"/>'))).toBeNull();
    expect(sniffImageType(Buffer.from('II*\0'))).toBeNull();
    expect(readImageInfo(encodePng(3, 2).subarray(0, 20))).toBeNull();
    expect(readImageInfo(Buffer.from([0xff, 0xd8, 0xff, 0xd9]))).toBeNull();
    expect(readImageInfo(fixtures.jpeg(0, 10))).toBeNull();
  });

  it('estimates tokens as W×H/750 after the provider downscale', () => {
    expect(imageTokens(750, 100)).toBe(100);
    expect(imageTokens(1568, 1568)).toBe(imageTokens(8000, 8000));
  });
});

describe('whole-file checks (what model endpoints refuse)', () => {
  const info = (b: Buffer): ImageInfo => readImageInfo(b)!;
  const check = (b: Buffer) => imageProblem(b, info(b));
  const png = encodePng(40, 30, (x, y) => [x * 6, y * 8, (x * y) % 256]);
  /** Offset of the first IDAT chunk (after the signature and the 25-byte IHDR chunk). */
  const IDAT_AT = 33;

  it('accepts complete PNGs, interlaced ones included, and GIFs even when truncated (endpoints take those)', async () => {
    expect(await check(png)).toBeNull();
    expect(await check(encodePng(13, 7, () => [1, 2, 3], { interlaced: true }))).toBeNull();
    expect(await check(encodePng(1, 1))).toBeNull();
    expect(await check(fixtures.gif(320, 200))).toBeNull();
    expect(await check(fixtures.jpeg(640, 480, 0xc2))).toBeNull();
    expect(await check(fixtures.webp('VP8L', 64, 64))).toBeNull();
  });

  it('refuses truncated, incomplete or corrupt PNGs', async () => {
    expect(await check(png.subarray(0, IDAT_AT))).toBe('it has no IEND chunk (the file is incomplete)');
    expect(await check(png.subarray(0, png.length - 12))).toBe('it has no IEND chunk (the file is incomplete)');
    expect(await check(png.subarray(0, png.length - 20))).toBe('the file is truncated (it ends inside a chunk)');
    const badCrc = Buffer.from(png);
    badCrc[IDAT_AT + 10] = badCrc[IDAT_AT + 10]! ^ 0xff;
    expect(await check(badCrc)).toBe('its IDAT chunk fails its CRC check (corrupt file)');
    // Valid chunks, but the image data inflates to fewer rows than the header promises, or is not zlib at all.
    const head = png.subarray(0, IDAT_AT);
    const iend = pngChunk('IEND', Buffer.alloc(0));
    const short = Buffer.concat([head, pngChunk('IDAT', deflateSync(Buffer.alloc(10 * (1 + 40 * 3)))), iend]);
    expect(await check(short)).toBe('its image data is corrupt or incomplete');
    expect(await check(Buffer.concat([head, pngChunk('IDAT', Buffer.from('not zlib data')), iend]))).toBe('its image data is corrupt or incomplete');
    expect(await check(Buffer.concat([head, iend]))).toBe('it has no image data');
    const interlaced = encodePng(13, 7, () => [1, 2, 3], { interlaced: true });
    const flat = encodePng(13, 7, () => [1, 2, 3]);
    // Adam7 needs more bytes than the same pixels written row by row (a filter byte per row of each pass): 287, not 280.
    expect(await check(Buffer.concat([interlaced.subarray(0, IDAT_AT), flat.subarray(IDAT_AT)]))).toBe('its image data is corrupt or incomplete');
  });

  it('stops inflating PNG image data once it has what the header needs (a zlib bomb costs no more than a real image)', async () => {
    const bomb = deflateSync(Buffer.alloc(32 * 1024 * 1024));
    const seen = await inflatedSize([bomb], 1000);
    expect(seen).toBeGreaterThanOrEqual(1000);
    expect(seen).toBeLessThan(1024 * 1024);
    // A stream that ends before giving what is needed is still refused.
    expect(await inflatedSize([bomb.subarray(0, 100)], 16 * 1024 * 1024)).toBeNull();
    // A PNG whose data inflates far past its 40×30 pixels is accepted without inflating all of it.
    const head = png.subarray(0, IDAT_AT);
    expect(await check(Buffer.concat([head, pngChunk('IDAT', bomb), pngChunk('IEND', Buffer.alloc(0))]))).toBeNull();
  });

  it('refuses JPEGs endpoints cannot decode, and truncated WebPs', async () => {
    expect(await check(fixtures.jpeg(640, 480, 0xc9))).toBe('it is an arithmetic-coded JPEG, which model providers cannot decode');
    expect(await check(fixtures.jpeg(640, 480, 0xc3))).toBe('it is a lossless or hierarchical JPEG, which model providers cannot decode');
    const twelveBit = fixtures.jpeg(640, 480);
    twelveBit[twelveBit.indexOf(Buffer.from([0xff, 0xc0])) + 4] = 12;
    expect(await check(twelveBit)).toBe('it is a 12-bit JPEG, which model providers cannot decode');
    const webp = fixtures.webp('VP8L', 64, 64);
    expect(await check(webp.subarray(0, 30))).toBe('the file is truncated');
    expect(await check(fixtures.webp('VP8X', 64, 64))).toBe('it has no image data');
  });

  it('reads the size from a data URL without decoding all of it, past large JPEG metadata', () => {
    const app1 = Buffer.concat([Buffer.from([0xff, 0xe1, 0xff, 0xff]), Buffer.alloc(0xfffd)]);
    const jpeg = fixtures.jpeg(1240, 1754);
    const big = Buffer.concat([jpeg.subarray(0, 2), app1, app1, app1, app1, app1, jpeg.subarray(2)]);
    expect(readDataUrlInfo(`data:image/jpeg;base64,${big.toString('base64')}`)).toEqual({ media_type: 'image/jpeg', width: 1240, height: 1754 });
    expect(readDataUrlInfo(`data:image/png;base64,${png.toString('base64')}`)).toEqual({ media_type: 'image/png', width: 40, height: 30 });
    expect(readDataUrlInfo('data:image/png,raw')).toBeNull();
    expect(readDataUrlInfo('https://example.com/a.png')).toBeNull();
  });
});

describe('AttachmentStore', () => {
  let dir: string;
  afterEach(async () => dir && rm(dir, { recursive: true, force: true }));

  it('stores content once under its sha256 and serves data URLs; missing ones are null', async () => {
    dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-att-')));
    const store = new AttachmentStore(join(dir, 'attachments'));
    const png = encodePng(2, 2);
    const sha = await store.put(png, 'image/png');
    expect(sha).toMatch(/^[0-9a-f]{64}$/);
    expect(await store.put(png, 'image/png')).toBe(sha);
    expect(await readdir(join(dir, 'attachments'))).toEqual([`${sha}.png`]);
    expect(store.find(sha)).toEqual({ path: join(dir, 'attachments', `${sha}.png`), media_type: 'image/png' });
    expect((await store.read(sha))?.data.equals(png)).toBe(true);
    const missing = { sha256: 'f'.repeat(64), media_type: 'image/png' as const };
    expect(await store.dataUrls([{ sha256: sha, media_type: 'image/png' }, missing])).toEqual(new Map([[sha, `data:image/png;base64,${png.toString('base64')}`]]));
    // Cached: served even after the file is gone (it never changes while it exists).
    await rm(join(dir, 'attachments', `${sha}.png`));
    expect((await store.dataUrls([{ sha256: sha, media_type: 'image/png' }])).get(sha)).toBe(`data:image/png;base64,${png.toString('base64')}`);
    expect(store.find('../../etc/passwd')).toBeNull();
    expect(await store.read('0'.repeat(64))).toBeNull();
  });
});
