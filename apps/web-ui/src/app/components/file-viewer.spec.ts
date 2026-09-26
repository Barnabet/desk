import { fireEvent, render, screen, waitFor } from '@testing-library/angular';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { FileViewer, rasterMime } from './file-viewer';
import { ToastService } from './toast';

const enc = (s: string) => new TextEncoder().encode(s);

/** jsdom has no object URLs: record the Blobs the viewer makes and the URLs it revokes. */
let created: Blob[] = [];
let revoked: string[] = [];
const original = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };

beforeEach(() => {
  created = [];
  revoked = [];
  URL.createObjectURL = vi.fn((b: Blob) => {
    created.push(b);
    return `blob:test/${created.length}`;
  });
  URL.revokeObjectURL = vi.fn((u: string) => {
    revoked.push(u);
  });
});

afterEach(() => {
  URL.createObjectURL = original.create;
  URL.revokeObjectURL = original.revoke;
});

describe('FileViewer', () => {
  it('renders Markdown, shows its source on Raw, and is rendered again for the next file', async () => {
    const bridge = new FakeDeskBridge();
    const view = await render(FileViewer, { inputs: { path: 'notes/plan.md', data: enc('# Plan\n\nShip it.') }, providers: bridge.providers });
    expect(screen.getByRole('heading', { name: 'Plan' })).toBeTruthy();
    expect(screen.getByText('notes/plan.md')).toBeTruthy();
    expect(screen.getByText('16 B')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Raw' }));
    expect(await screen.findByRole('button', { name: 'Rendered' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Plan' })).toBeNull();
    expect(document.querySelector('.file-viewer code')!.textContent).toBe('# Plan\n\nShip it.');
    await view.rerender({ inputs: { path: 'notes/next.md', data: enc('# Next') } });
    expect(await screen.findByRole('heading', { name: 'Next' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Raw' })).toBeTruthy();
  });

  it('shows an SVG as its source text, never as an image or a Blob', async () => {
    const svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><circle r="4"/></svg>';
    const bridge = new FakeDeskBridge();
    await render(FileViewer, { inputs: { path: 'art/logo.svg', data: enc(svg) }, providers: bridge.providers });
    expect(rasterMime('art/logo.svg')).toBeNull();
    expect(screen.queryByRole('img')).toBeNull();
    expect(document.querySelector('.file-viewer svg, .file-viewer script')).toBeNull();
    expect(document.querySelector('.file-viewer code')!.textContent).toBe(svg);
    expect(document.querySelector('.file-viewer .codeblock-lang')!.textContent).toBe('svg');
    expect(created).toEqual([]);
  });

  it('shows a raster image through a Blob of its own type, and revokes its URL when the file changes', async () => {
    const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0]);
    const bridge = new FakeDeskBridge();
    const view = await render(FileViewer, { inputs: { path: 'renders/page-1.png', data: png }, providers: bridge.providers });
    const img = await screen.findByRole('img', { name: 'page-1.png' });
    expect(img.getAttribute('src')).toBe('blob:test/1');
    expect(created.map((b) => b.type)).toEqual(['image/png']);
    expect(['a.jpg', 'a.JPEG', 'a.gif', 'a.webp', 'a.svg', 'a.html', 'a'].map(rasterMime)).toEqual(['image/jpeg', 'image/jpeg', 'image/gif', 'image/webp', null, null, null]);
    await view.rerender({ inputs: { path: 'renders/page-2.jpg', data: png } });
    await waitFor(() => expect(created.map((b) => b.type)).toEqual(['image/png', 'image/jpeg']));
    expect(revoked).toEqual(['blob:test/1']);
    expect((await screen.findByRole('img', { name: 'page-2.jpg' })).getAttribute('src')).toBe('blob:test/2');
    view.fixture.destroy();
    expect(revoked).toEqual(['blob:test/1', 'blob:test/2']);
  });

  it('never makes an image of a file whose extension names an Object.prototype member', async () => {
    expect(['a.__proto__', 'a.constructor', 'a.CONSTRUCTOR', 'a.toString', 'a.hasOwnProperty', 'a.valueOf'].map(rasterMime)).toEqual([null, null, null, null, null, null]);
    const bridge = new FakeDeskBridge();
    await render(FileViewer, { inputs: { path: 'out/x.constructor', data: new Uint8Array([0x89, 0x50, 0, 1]) }, providers: bridge.providers });
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.getByText("This file isn't text, so it can't be shown here. Save a copy to open it.")).toBeTruthy();
    expect(created).toEqual([]);
  });

  it('says a binary file cannot be shown, and saves a copy of it through the browser', async () => {
    const bridge = new FakeDeskBridge({ 'app.saveFile': () => true });
    const data = new Uint8Array([0, 1, 2, 3]);
    await render(FileViewer, { inputs: { path: 'out/archive.bin', data }, providers: bridge.providers });
    expect(screen.getByText("This file isn't text, so it can't be shown here. Save a copy to open it.")).toBeTruthy();
    expect(screen.getByText('4 B')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Raw' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Save a copy…' }));
    await waitFor(() => expect(bridge.calls).toEqual([{ channel: 'app.saveFile', input: { name: 'archive.bin', data } }]));
    expect(created).toEqual([]);
  });

  it('shows the error in a toast when saving a copy fails', async () => {
    const bridge = new FakeDeskBridge({
      'app.saveFile': () => {
        throw { code: 'internal', message: 'The download could not start' };
      },
    });
    await render(FileViewer, { inputs: { path: 'out/archive.bin', data: new Uint8Array([0, 1]) }, providers: bridge.providers });
    fireEvent.click(screen.getByRole('button', { name: 'Save a copy…' }));
    await waitFor(() => expect(TestBed.inject(ToastService).list().map((t) => [t.tone, t.message])).toEqual([['error', 'The download could not start']]));
  });

  it("saves a copy named 'file' when the path has no name", async () => {
    const bridge = new FakeDeskBridge({ 'app.saveFile': () => true });
    const data = new Uint8Array([0, 1]);
    await render(FileViewer, { inputs: { path: 'out/', data }, providers: bridge.providers });
    fireEvent.click(screen.getByRole('button', { name: 'Save a copy…' }));
    await waitFor(() => expect(bridge.calls).toEqual([{ channel: 'app.saveFile', input: { name: 'file', data } }]));
  });

  it("puts its host's actions in the bar, before Save a copy…", async () => {
    const bridge = new FakeDeskBridge();
    await render(`<div deskFileViewer path="notes.txt" [data]="data"><button type="button" aria-label="Close preview">✕</button></div>`, {
      imports: [FileViewer],
      componentProperties: { data: enc('hello') },
      providers: bridge.providers,
    });
    const bar = document.querySelector('.file-viewer-bar')!;
    expect([...bar.querySelectorAll('button')].map((b) => b.getAttribute('aria-label') ?? b.textContent)).toEqual(['Close preview', 'Save a copy…']);
    expect(document.querySelector('.file-viewer code')!.textContent).toBe('hello');
    expect(document.querySelector('.file-viewer .codeblock-lang')!.textContent).toBe('txt');
  });
});
