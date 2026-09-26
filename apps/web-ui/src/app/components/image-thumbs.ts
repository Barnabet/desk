import { ChangeDetectionStrategy, Component, DestroyRef, ElementRef, ViewEncapsulation, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import { imageLabel, type ToolImage } from '@desk/protocol';
import { DeskBridge, type DeskBridgeApi } from '../core/desk-bridge';
import { Button } from './button';
import { Sheet } from './sheet';

type Size = 'thumb' | 'full';
type Loaded = { url: string | null; failed: boolean };

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
 * Scales an image down to a thumbnail, here in the browser: images an agent looked at may come from the web, so desk web
 * and deskd never decode them. The full image when the browser cannot scale it.
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

function loadAttachment(bridge: Pick<DeskBridgeApi, 'call'>, image: ToolImage, size: Size): Promise<string> {
  const { sha256 } = image;
  const key = `${size}:${sha256}`;
  const hit = cache.get(key);
  if (hit) {
    cache.delete(key);
    cache.set(key, hit);
    return hit.url;
  }
  const full = bridge.call('attachments.get', { sha256 });
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

/** For specs: forget loaded images. */
export function clearAttachmentCache(): void {
  cache.clear();
  cached = 0;
}

const baseName = (name: string) => name.split('/').pop() || name;

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** A small preview, loaded only once it scrolls near the viewport (always, without IntersectionObserver). */
@Component({
  selector: 'button[deskThumb]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { type: 'button', class: 'image-thumb', '[attr.aria-label]': "'Open ' + name()", '[attr.title]': 'label()', '(click)': 'open.emit()' },
  template: `
    @if (loaded().url; as url) {
      <img [src]="url" [alt]="name()" />
    } @else {
      <span class="image-thumb-empty small muted">{{ loaded().failed ? 'Unavailable' : '…' }}</span>
    }
  `,
})
export class Thumb {
  readonly image = input.required<ToolImage>();
  readonly open = output<void>();
  protected readonly name = computed(() => baseName(this.image().name));
  protected readonly label = computed(() => imageLabel(this.image()));
  protected readonly loaded = signal<Loaded>({ url: null, failed: false });
  private readonly near = signal(typeof IntersectionObserver === 'undefined');
  /** An attachment never changes: its digest names it, so only a new digest loads again. */
  private readonly sha = computed(() => this.image().sha256);

  constructor() {
    const bridge = inject(DeskBridge);
    if (!this.near()) {
      const host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
      const io = new IntersectionObserver(
        (entries) => {
          if (entries.some((e) => e.isIntersecting)) {
            this.near.set(true);
            io.disconnect();
          }
        },
        { rootMargin: '200px' },
      );
      io.observe(host);
      inject(DestroyRef).onDestroy(() => io.disconnect());
    }
    effect((onCleanup) => {
      if (!this.near()) return;
      this.sha();
      const image = untracked(this.image);
      let live = true;
      this.loaded.set({ url: null, failed: false });
      loadAttachment(bridge, image, 'thumb').then(
        (url) => {
          if (live) this.loaded.set({ url, failed: false });
        },
        () => {
          if (live) this.loaded.set({ url: null, failed: true });
        },
      );
      onCleanup(() => {
        live = false;
      });
    });
  }
}

/** Thumbnails of the images a tool result showed the agent (view_image); a click opens one larger. */
@Component({
  selector: 'div[deskImageThumbs]',
  imports: [Thumb, Sheet, Button],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  // Clicks on the thumbnails stay here instead of selecting the transcript stop. The large view lives in document.body.
  host: { '[class.image-thumbs]': 'images().length > 0', '[hidden]': '!images().length', '(click)': '$event.stopPropagation()' },
  template: `
    @for (image of images(); track $index + ':' + image.sha256) {
      <button deskThumb [image]="image" (open)="open.set(image)"></button>
    }
    @if (open(); as image) {
      <div deskSheet [title]="baseName(image.name)" [width]="960" (close)="open.set(null)">
        <p class="small muted image-caption"><span class="mono">{{ image.name }}</span> · {{ image.width }}×{{ image.height }} · {{ humanSize(image.bytes) }}</p>
        @if (full().url; as url) {
          <img class="file-image image-large" [src]="url" [alt]="baseName(image.name)" />
        } @else {
          <p class="small muted">{{ full().failed ? 'This image is no longer available.' : 'Loading…' }}</p>
        }
        <div class="sheet-footer"><button deskButton (click)="open.set(null)">Close</button></div>
      </div>
    }
  `,
})
export class ImageThumbs {
  readonly images = input.required<ToolImage[]>();
  protected readonly open = signal<ToolImage | null>(null);
  protected readonly full = signal<Loaded>({ url: null, failed: false });
  protected readonly baseName = baseName;
  protected readonly humanSize = humanSize;

  constructor() {
    const bridge = inject(DeskBridge);
    effect((onCleanup) => {
      const image = this.open();
      if (!image) return;
      let live = true;
      this.full.set({ url: null, failed: false });
      loadAttachment(bridge, image, 'full').then(
        (url) => {
          if (live) this.full.set({ url, failed: false });
        },
        () => {
          if (live) this.full.set({ url: null, failed: true });
        },
      );
      onCleanup(() => {
        live = false;
      });
    });
  }
}
