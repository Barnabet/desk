import { createHash, randomBytes } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import type { ImageMediaType, ToolImage } from '@desk/protocol';
import { IMAGE_EXT } from './image';

const SHA256 = /^[0-9a-f]{64}$/;
const TYPES = Object.entries(IMAGE_EXT) as Array<[ImageMediaType, string]>;
/** Data URLs kept in memory (characters), so every model call does not re-read and re-encode the same images. */
const CACHE_CHARS = 64 * 1024 * 1024;

/**
 * Content-addressed store of images shown to models: `<dir>/<sha256>.<ext>`. Files never change once written,
 * so a transcript keeps showing what the agent saw even after the workspace file changes.
 */
export class AttachmentStore {
  private readonly cache = new Map<string, string>();
  private cached = 0;

  constructor(readonly dir: string) {}

  /** Stores the bytes once (atomically) and returns their sha256. */
  async put(data: Uint8Array, mediaType: ImageMediaType): Promise<string> {
    const sha256 = createHash('sha256').update(data).digest('hex');
    const file = this.pathOf(sha256, mediaType);
    if (existsSync(file)) return sha256;
    await mkdir(this.dir, { recursive: true });
    const tmp = join(this.dir, `.tmp-${randomBytes(6).toString('hex')}`);
    try {
      await writeFile(tmp, data);
      await rename(tmp, file);
    } finally {
      await rm(tmp, { force: true });
    }
    return sha256;
  }

  pathOf(sha256: string, mediaType: ImageMediaType): string {
    return join(this.dir, `${sha256}.${IMAGE_EXT[mediaType]}`);
  }

  /** The stored file for a digest, or null (also for anything that is not a sha256). */
  find(sha256: string): { path: string; media_type: ImageMediaType } | null {
    if (!SHA256.test(sha256)) return null;
    for (const [media_type] of TYPES) {
      const path = this.pathOf(sha256, media_type);
      if (existsSync(path)) return { path, media_type };
    }
    return null;
  }

  async read(sha256: string): Promise<{ data: Buffer; media_type: ImageMediaType } | null> {
    const found = this.find(sha256);
    if (!found) return null;
    const data = await readFile(found.path).catch(() => null);
    return data ? { data, media_type: found.media_type } : null;
  }

  /**
   * The images as base64 data URLs for chat messages, by sha256; missing attachments are left out. Files are read
   * asynchronously and the encoded URLs cached, so building a conversation never blocks on disk.
   */
  async dataUrls(images: ReadonlyArray<Pick<ToolImage, 'sha256' | 'media_type'>>): Promise<Map<string, string>> {
    const out = new Map<string, string>();
    await Promise.all(
      images.map(async (image) => {
        if (out.has(image.sha256)) return;
        const url = await this.dataUrl(image);
        if (url) out.set(image.sha256, url);
      }),
    );
    return out;
  }

  private async dataUrl(image: Pick<ToolImage, 'sha256' | 'media_type'>): Promise<string | null> {
    const hit = this.cache.get(image.sha256);
    if (hit !== undefined) {
      this.cache.delete(image.sha256);
      this.cache.set(image.sha256, hit); // most recently used last
      return hit;
    }
    if (!SHA256.test(image.sha256)) return null;
    const data = await readFile(this.pathOf(image.sha256, image.media_type)).catch(() => null);
    if (!data) return null;
    const url = `data:${image.media_type};base64,${data.toString('base64')}`;
    if (!this.cache.has(image.sha256)) {
      this.cache.set(image.sha256, url);
      this.cached += url.length;
    }
    for (const [key, value] of this.cache) {
      if (this.cached <= CACHE_CHARS) break;
      this.cache.delete(key);
      this.cached -= value.length;
    }
    return url;
  }
}
