import { describe, expect, it } from 'vitest';
import { extOf, imageMime } from './files';

describe('files', () => {
  it('types images by their extension, whatever its case', () => {
    expect(['a.png', 'a.JPG', 'a.jpeg', 'a.gif', 'a.webp', 'a.svg'].map(imageMime)).toEqual(['image/png', 'image/jpeg', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml']);
    expect(['a.html', 'a', 'dir/'].map(imageMime)).toEqual([null, null, null]);
    expect(extOf('dir/a.TAR.GZ')).toBe('gz');
  });

  it('never answers with an Object.prototype member for an extension named after one', () => {
    expect(['a.__proto__', 'a.constructor', 'a.CONSTRUCTOR', 'a.toString', 'a.hasOwnProperty', 'a.valueOf'].map(imageMime)).toEqual([null, null, null, null, null, null]);
  });
});
