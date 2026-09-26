import { describe, expect, it } from 'vitest';
import { extOf, imageMime, pastedName } from './files';

describe('files', () => {
  it('types images by their extension, whatever its case', () => {
    expect(['a.png', 'a.JPG', 'a.jpeg', 'a.gif', 'a.webp', 'a.svg'].map(imageMime)).toEqual(['image/png', 'image/jpeg', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml']);
    expect(['a.html', 'a', 'dir/'].map(imageMime)).toEqual([null, null, null]);
    expect(extOf('dir/a.TAR.GZ')).toBe('gz');
  });

  it('never answers with an Object.prototype member for an extension named after one', () => {
    expect(['a.__proto__', 'a.constructor', 'a.CONSTRUCTOR', 'a.toString', 'a.hasOwnProperty', 'a.valueOf'].map(imageMime)).toEqual([null, null, null, null, null, null]);
  });

  it("names a pasted screenshot after the moment it was pasted, and keeps a copied file's own name", () => {
    const at = new Date(2026, 8, 26, 1, 12, 7);
    expect(pastedName({ name: 'image.png', type: 'image/png' }, at, 0)).toBe('pasted-2026-09-26-011207.png');
    expect(pastedName({ name: 'image.jpeg', type: 'image/jpeg' }, at, 1)).toBe('pasted-2026-09-26-011207-2.jpeg');
    expect(pastedName({ name: '', type: 'image/webp' }, at, 0)).toBe('pasted-2026-09-26-011207.webp');
    expect(pastedName({ name: 'spec.pdf', type: 'application/pdf' }, at, 3)).toBe('spec.pdf');
  });
});
