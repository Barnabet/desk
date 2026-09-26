const MAX_TEXT = 1024 * 1024;

/** Decodes bytes as UTF-8 text, or returns null for binary (or very large) content. */
export function asText(data: Uint8Array): string | null {
  if (data.length > MAX_TEXT) return null;
  if (data.subarray(0, 8000).includes(0)) return null;
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(data);
  } catch {
    return null;
  }
}

/** Base64 of a Blob, for upload bodies. */
export async function fileToBase64(file: Blob): Promise<string> {
  const buf = new Uint8Array(await file.arrayBuffer());
  let s = '';
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return btoa(s);
}

export const textToBase64 = (text: string) => fileToBase64(new Blob([text]));

/** A Map, not an object literal: a file named `x.constructor` or `x.__proto__` must not find Object.prototype's members. */
const IMAGE = new Map([
  ['png', 'image/png'],
  ['jpg', 'image/jpeg'],
  ['jpeg', 'image/jpeg'],
  ['gif', 'image/gif'],
  ['webp', 'image/webp'],
  ['svg', 'image/svg+xml'],
]);

export const extOf = (path: string) => (/\.([^./]+)$/.exec(path)?.[1] ?? '').toLowerCase();
export const imageMime = (path: string): string | null => IMAGE.get(extOf(path)) ?? null;
export const isMarkdown = (path: string) => /^(md|markdown)$/.test(extOf(path));
export const MAX_UPLOAD = 25 * 1024 * 1024;

const pad2 = (n: number) => String(n).padStart(2, '0');

/**
 * A pasted screenshot arrives as "image.png" every time: it gets a name of its own, "pasted-2026-09-26-011207.png"
 * ("-2" and on for more in the same paste). Files copied in Finder keep theirs.
 */
export function pastedName(file: { name: string; type: string }, at: Date, index: number): string {
  if (!/^image\.\w+$/.test(file.name) && file.name) return file.name;
  const ext = /\.(\w+)$/.exec(file.name)?.[1] ?? file.type.split('/')[1] ?? 'png';
  const stamp = `${at.getFullYear()}-${pad2(at.getMonth() + 1)}-${pad2(at.getDate())}-${pad2(at.getHours())}${pad2(at.getMinutes())}${pad2(at.getSeconds())}`;
  return `pasted-${stamp}${index ? `-${index + 1}` : ''}.${ext}`;
}
