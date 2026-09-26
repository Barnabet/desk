import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ToolImage } from '@desk/protocol';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { clearAttachmentCache, ImageThumbs } from './image-thumbs';

const image = (n: number): ToolImage => ({ sha256: String(n).repeat(64), media_type: 'image/png', width: 800, height: 600, bytes: 1000, name: `renders/page-${n}.png` });

/** A controllable IntersectionObserver: `show(el)` reports that element as visible. */
function fakeObserver() {
  const observers: Array<{ cb: IntersectionObserverCallback; els: Element[] }> = [];
  class FakeIO {
    private readonly entry: { cb: IntersectionObserverCallback; els: Element[] };
    constructor(cb: IntersectionObserverCallback) {
      this.entry = { cb, els: [] };
      observers.push(this.entry);
    }
    observe(el: Element) {
      this.entry.els.push(el);
    }
    disconnect() {
      this.entry.els = [];
    }
  }
  vi.stubGlobal('IntersectionObserver', FakeIO);
  return {
    show(el: Element) {
      for (const o of observers) {
        if (o.els.includes(el)) o.cb([{ isIntersecting: true, target: el } as unknown as IntersectionObserverEntry], {} as IntersectionObserver);
      }
    },
  };
}

beforeEach(() => clearAttachmentCache());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('ImageThumbs', () => {
  it('loads a thumbnail only once it comes near the viewport', async () => {
    const io = fakeObserver();
    const bridge = new FakeDeskBridge({ 'attachments.get': () => 'data:image/png;base64,dGh1bWI=' });
    await render(ImageThumbs, { inputs: { images: [image(1), image(2)] }, providers: bridge.providers });
    const first = screen.getByRole('button', { name: 'Open page-1.png' });
    expect(bridge.calls).toEqual([]);
    io.show(first);
    await waitFor(() => expect(within(first).getByRole('img').getAttribute('src')).toBe('data:image/png;base64,dGh1bWI='));
    expect(bridge.calls.map((c) => c.input)).toEqual([{ sha256: image(1).sha256 }]);
    expect(within(screen.getByRole('button', { name: 'Open page-2.png' })).queryByRole('img')).toBeNull();
  });

  it('scales thumbnails down here in the browser (the server never decodes an image), and falls back to the full image', async () => {
    const bridge = new FakeDeskBridge({ 'attachments.get': () => 'data:image/png;base64,ZnVsbA==' });
    const resized: Array<[string, ImageBitmapOptions | undefined]> = [];
    const close = vi.fn();
    vi.stubGlobal('createImageBitmap', async (blob: Blob, opts?: ImageBitmapOptions) => {
      resized.push([`${blob.type}:${await blob.text()}`, opts]);
      return { width: opts?.resizeWidth, height: opts?.resizeHeight, close } as unknown as ImageBitmap;
    });
    const drawn: unknown[] = [];
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ({ drawImage: (b: unknown) => drawn.push(b) }) as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockImplementation(function (this: HTMLCanvasElement) {
      return `data:image/png;base64,small-${this.width}x${this.height}`;
    });
    const view = await render(ImageThumbs, { inputs: { images: [image(1)] }, providers: bridge.providers });
    await waitFor(() => expect(screen.getByRole('img').getAttribute('src')).toBe('data:image/png;base64,small-152x114'));
    // 800×600 scaled to 152 px on the long side, from the full image the bridge returned.
    expect(resized).toEqual([['image/png:full', { resizeWidth: 152, resizeHeight: 114, resizeQuality: 'high' }]]);
    expect(drawn).toHaveLength(1);
    expect(close).toHaveBeenCalled();
    expect(bridge.calls.map((c) => c.input)).toEqual([{ sha256: image(1).sha256 }]);
    // An image the browser cannot decode shows as is (the large view shows the same bytes).
    vi.stubGlobal('createImageBitmap', async () => {
      throw new Error('cannot decode');
    });
    await view.rerender({ inputs: { images: [image(2)] } });
    await waitFor(() => expect(screen.getByRole('img').getAttribute('src')).toBe('data:image/png;base64,ZnVsbA=='));
  });

  it('keeps a bounded amount of image data: the least recently used is fetched again', async () => {
    const big = (n: number) => `data:image/png;base64,${String(n).repeat(10 * 1024 * 1024)}`;
    const bridge = new FakeDeskBridge({ 'attachments.get': ({ sha256 }: { sha256: string }) => big(Number(sha256[0])) });
    const loads = () => bridge.calls.map((c) => (c.input as { sha256: string }).sha256[0]);
    const view = await render(ImageThumbs, { inputs: { images: [image(1)] }, providers: bridge.providers });
    await waitFor(() => expect(screen.getByRole('img').getAttribute('src')?.length).toBe(big(1).length));
    for (const n of [2, 3, 3, 1]) {
      // An empty list first destroys the Thumb, as React's unmount does, so the next one goes through the cache again.
      await view.rerender({ inputs: { images: [] } });
      await view.rerender({ inputs: { images: [image(n)] } });
      await waitFor(() => expect(screen.getByRole('img').getAttribute('src')?.length).toBe(big(n).length));
    }
    // 24 MB of data URLs at most: the third 10 MB image pushed out the first; the third is still cached.
    expect(loads()).toEqual(['1', '2', '3', '1']);
  });

  it('opens an image larger in a sheet, with its path, size and weight', async () => {
    const bridge = new FakeDeskBridge({ 'attachments.get': () => 'data:image/png;base64,ZnVsbA==' });
    await render(ImageThumbs, { inputs: { images: [{ ...image(1), bytes: 52_000 }] }, providers: bridge.providers });
    fireEvent.click(screen.getByRole('button', { name: 'Open page-1.png' }));
    const dialog = await screen.findByRole('dialog', { name: 'page-1.png' });
    await waitFor(() => expect(within(dialog).getByRole('img', { name: 'page-1.png' }).getAttribute('src')).toBe('data:image/png;base64,ZnVsbA=='));
    expect(dialog.querySelector('.image-caption')!.textContent).toBe('renders/page-1.png · 800×600 · 51 KB');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(bridge.calls.map((c) => c.input)).toEqual([{ sha256: image(1).sha256 }, { sha256: image(1).sha256 }]);
  });
});
