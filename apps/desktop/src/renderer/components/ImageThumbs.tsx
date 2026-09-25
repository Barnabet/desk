import { useEffect, useRef, useState, type RefObject } from 'react';
import { imageLabel, type ToolImage } from '@desk/protocol';
import { call } from '../bridge';
import { Button } from './Button';
import { Sheet } from './Sheet';

type Size = 'thumb' | 'full';

/**
 * Data URLs by size and sha256, least recently used first. Attachments never change, so entries never go stale;
 * the characters kept are bounded (thumbnails are small, full images up to a few MB each).
 */
const cache = new Map<string, { url: Promise<string>; chars: number }>();
const CACHE_CHARS = 24 * 1024 * 1024;
let cached = 0;

/** Long side of thumbnails in pixels (twice the 76 px shown, for Retina screens). */
export const THUMBNAIL_PX = 152;

/**
 * Scales an image down to a thumbnail, here in the sandboxed renderer: images an agent looked at may come from the
 * web, so they are never decoded in the main process. The full image when the browser cannot scale it.
 */
async function shrink(url: string, image: ToolImage): Promise<string> {
  const scale = Math.min(1, THUMBNAIL_PX / Math.max(image.width, image.height, 1));
  if (scale >= 1 || typeof createImageBitmap !== 'function') return url;
  const width = Math.max(1, Math.round(image.width * scale));
  const height = Math.max(1, Math.round(image.height * scale));
  try {
    const binary = atob(url.slice(url.indexOf(',') + 1));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const bitmap = await createImageBitmap(new Blob([bytes], { type: image.media_type }), { resizeWidth: width, resizeHeight: height, resizeQuality: 'high' });
    try {
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext('2d');
      if (!ctx) return url;
      ctx.drawImage(bitmap, 0, 0);
      return canvas.toDataURL('image/png');
    } finally {
      bitmap.close();
    }
  } catch {
    return url;
  }
}

function loadAttachment(image: ToolImage, size: Size): Promise<string> {
  const { sha256 } = image;
  const key = `${size}:${sha256}`;
  const hit = cache.get(key);
  if (hit) {
    cache.delete(key);
    cache.set(key, hit);
    return hit.url;
  }
  const full = call('attachments.get', { sha256 });
  const entry = { url: size === 'thumb' ? full.then((url) => shrink(url, image)) : full, chars: 0 };
  cache.set(key, entry);
  entry.url.then(
    (url) => {
      if (cache.get(key) !== entry) return;
      entry.chars = url.length;
      cached += url.length;
      for (const [k, e] of cache) {
        if (cached <= CACHE_CHARS) break;
        if (k === key) continue;
        cache.delete(k);
        cached -= e.chars;
      }
    },
    () => {
      if (cache.get(key) === entry) cache.delete(key); // a failure is retried next time
    },
  );
  return entry.url;
}

/** For tests: forget loaded images. */
export function clearAttachmentCache(): void {
  cache.clear();
  cached = 0;
}

/** Whether the element has come near the viewport (always true without IntersectionObserver). */
function useNearViewport(ref: RefObject<Element | null>): boolean {
  const [near, setNear] = useState(() => typeof IntersectionObserver === 'undefined');
  useEffect(() => {
    const el = ref.current;
    if (near || !el) return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        setNear(true);
        io.disconnect();
      }
    }, { rootMargin: '200px' });
    io.observe(el);
    return () => io.disconnect();
  }, [near, ref]);
  return near;
}

function useAttachment(image: ToolImage, size: Size, enabled = true): { url: string | null; failed: boolean } {
  const [state, setState] = useState<{ url: string | null; failed: boolean }>({ url: null, failed: false });
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    setState({ url: null, failed: false });
    loadAttachment(image, size).then(
      (url) => live && setState({ url, failed: false }),
      () => live && setState({ url: null, failed: true }),
    );
    return () => {
      live = false;
    };
    // An attachment never changes: its digest names it.
  }, [image.sha256, size, enabled]);
  return state;
}

const baseName = (name: string) => name.split('/').pop() || name;

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** A small preview, loaded only once it scrolls near the viewport. */
function Thumb({ image, onOpen }: { image: ToolImage; onOpen(): void }) {
  const ref = useRef<HTMLButtonElement>(null);
  const { url, failed } = useAttachment(image, 'thumb', useNearViewport(ref));
  return (
    <button ref={ref} type="button" className="image-thumb" onClick={onOpen} aria-label={`Open ${baseName(image.name)}`} title={imageLabel(image)}>
      {url ? <img src={url} alt={baseName(image.name)} /> : <span className="image-thumb-empty small muted">{failed ? 'Unavailable' : '…'}</span>}
    </button>
  );
}

function ImageDialog({ image, onClose }: { image: ToolImage; onClose(): void }) {
  const { url, failed } = useAttachment(image, 'full');
  return (
    <Sheet title={baseName(image.name)} onClose={onClose} width={960} footer={<Button onClick={onClose}>Close</Button>}>
      <p className="small muted image-caption">
        <span className="mono">{image.name}</span> · {image.width}×{image.height} · {humanSize(image.bytes)}
      </p>
      {url ? (
        <img className="file-image image-large" src={url} alt={baseName(image.name)} />
      ) : (
        <p className="small muted">{failed ? 'This image is no longer available.' : 'Loading…'}</p>
      )}
    </Sheet>
  );
}

/** Thumbnails of the images a tool result showed the agent (view_image); a click opens one larger. */
export function ImageThumbs({ images }: { images: ToolImage[] }) {
  const [open, setOpen] = useState<ToolImage | null>(null);
  if (!images.length) return null;
  return (
    // Clicks (including inside the dialog's portal) stay here instead of selecting the transcript stop.
    <div className="image-thumbs" onClick={(e) => e.stopPropagation()}>
      {images.map((image, i) => (
        <Thumb key={`${i}:${image.sha256}`} image={image} onOpen={() => setOpen(image)} />
      ))}
      {open ? <ImageDialog image={open} onClose={() => setOpen(null)} /> : null}
    </div>
  );
}
