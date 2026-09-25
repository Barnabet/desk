// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ToolImage } from '@desk/protocol';
import { installBridge } from '../test/bridge';
import { clearAttachmentCache, ImageThumbs } from './ImageThumbs';

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
  cleanup();
  vi.unstubAllGlobals();
});

describe('ImageThumbs', () => {
  it('loads a thumbnail only once it comes near the viewport', async () => {
    const io = fakeObserver();
    const bridge = installBridge({ 'attachments.get': () => 'data:image/png;base64,dGh1bWI=' });
    render(<ImageThumbs images={[image(1), image(2)]} />);
    const first = screen.getByRole('button', { name: 'Open page-1.png' });
    expect(bridge.calls).toEqual([]);
    act(() => io.show(first));
    await waitFor(() => expect(within(first).getByRole('img').getAttribute('src')).toBe('data:image/png;base64,dGh1bWI='));
    expect(bridge.calls.map((c) => c.input)).toEqual([{ sha256: image(1).sha256 }]);
    expect(within(screen.getByRole('button', { name: 'Open page-2.png' })).queryByRole('img')).toBeNull();
  });

  it('scales thumbnails down here in the renderer (main never decodes an image), and falls back to the full image', async () => {
    const bridge = installBridge({ 'attachments.get': () => 'data:image/png;base64,ZnVsbA==' });
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
    try {
      render(<ImageThumbs images={[image(1)]} />);
      await waitFor(() => expect(screen.getByRole('img').getAttribute('src')).toBe('data:image/png;base64,small-152x114'));
      // 800×600 scaled to 152 px on the long side, from the full image the bridge returned.
      expect(resized).toEqual([['image/png:full', { resizeWidth: 152, resizeHeight: 114, resizeQuality: 'high' }]]);
      expect(drawn).toHaveLength(1);
      expect(close).toHaveBeenCalled();
      expect(bridge.calls.map((c) => c.input)).toEqual([{ sha256: image(1).sha256 }]);
      cleanup();
      // An image the browser cannot decode shows as is (the dialog shows the same bytes).
      vi.stubGlobal('createImageBitmap', async () => {
        throw new Error('cannot decode');
      });
      render(<ImageThumbs images={[image(2)]} />);
      await waitFor(() => expect(screen.getByRole('img').getAttribute('src')).toBe('data:image/png;base64,ZnVsbA=='));
    } finally {
      vi.restoreAllMocks();
    }
  });

  it('keeps a bounded amount of image data: the least recently used is fetched again', async () => {
    const big = (n: number) => `data:image/png;base64,${String(n).repeat(10 * 1024 * 1024)}`;
    const bridge = installBridge({ 'attachments.get': ({ sha256 }: { sha256: string }) => big(Number(sha256[0])) });
    const loads = () => bridge.calls.map((c) => (c.input as { sha256: string }).sha256[0]);
    for (const n of [1, 2, 3]) {
      const { unmount } = render(<ImageThumbs images={[image(n)]} />);
      await waitFor(() => expect(screen.getByRole('img').getAttribute('src')?.length).toBe(big(n).length));
      unmount();
    }
    // 24 MB of data URLs at most: the third 10 MB image pushed out the first; the third is still cached.
    for (const n of [3, 1]) {
      const { unmount } = render(<ImageThumbs images={[image(n)]} />);
      await waitFor(() => expect(screen.getByRole('img').getAttribute('src')?.length).toBe(big(n).length));
      unmount();
    }
    expect(loads()).toEqual(['1', '2', '3', '1']);
  });
});
