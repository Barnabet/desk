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

const IMAGE: Record<string, string> = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp', svg: 'image/svg+xml' };

export const extOf = (path: string) => (/\.([^./]+)$/.exec(path)?.[1] ?? '').toLowerCase();
export const imageMime = (path: string): string | null => IMAGE[extOf(path)] ?? null;
export const isMarkdown = (path: string) => /^(md|markdown)$/.test(extOf(path));
export const MAX_UPLOAD = 25 * 1024 * 1024;
