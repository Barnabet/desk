// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installBridge } from '../test/bridge';
import { FileViewer } from './FileViewer';

/** jsdom has no object URLs: record the Blobs the viewer makes. */
let created: Blob[] = [];
const original = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };

beforeEach(() => {
  created = [];
  URL.createObjectURL = vi.fn((b: Blob) => {
    created.push(b);
    return `blob:test/${created.length}`;
  });
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  cleanup();
  URL.createObjectURL = original.create;
  URL.revokeObjectURL = original.revoke;
});

describe('FileViewer', () => {
  it('shows an image through a Blob of its type, and never one for an extension named after an Object.prototype member', () => {
    installBridge();
    const view = render(<FileViewer path="renders/page-1.png" data={new Uint8Array([0x89, 0x50, 0, 1])} />);
    expect(screen.getByRole('img', { name: 'page-1.png' }).getAttribute('src')).toBe('blob:test/1');
    view.rerender(<FileViewer path="out/x.constructor" data={new Uint8Array([0x89, 0x50, 0, 1])} />);
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.getByText("This file isn't text, so it can't be shown here. Save a copy to open it.")).toBeTruthy();
    expect(created.map((b) => b.type)).toEqual(['image/png']);
  });

  it("saves a copy under the file's name, or 'file' when the path has none", async () => {
    const bridge = installBridge({ 'app.saveFile': () => true });
    const data = new Uint8Array([0, 1]);
    const view = render(<FileViewer path="out/archive.bin" data={data} />);
    fireEvent.click(screen.getByRole('button', { name: 'Save a copy…' }));
    view.rerender(<FileViewer path="out/" data={data} />);
    fireEvent.click(screen.getByRole('button', { name: 'Save a copy…' }));
    await waitFor(() => expect(bridge.calls.map((c) => (c.input as { name: string }).name)).toEqual(['archive.bin', 'file']));
  });
});
