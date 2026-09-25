/**
 * The /rpc codec, shared by desk web and the browser's DeskBridge: every Uint8Array in a value travels inside JSON as
 * { "$bytes": base64 }. desk web decodes input before validation and encodes results after the handler. It uses btoa
 * and atob only, so browser code can import it.
 */
export type EncodedBytes = { $bytes: string };

const CHUNK = 0x8000;

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i += CHUNK) binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  return btoa(binary);
}

/** Throws on anything that is not base64. */
export function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== 'object') return false;
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function isEncodedBytes(value: Record<string, unknown>): value is EncodedBytes {
  const keys = Object.keys(value);
  return keys.length === 1 && keys[0] === '$bytes' && typeof value.$bytes === 'string';
}

/** Replaces every Uint8Array (Buffers included), at any depth, with { $bytes: base64 }. Other values stay as they are. */
export function encodeBytes(value: unknown): unknown {
  if (value instanceof Uint8Array) return { $bytes: bytesToBase64(value) };
  if (Array.isArray(value)) return value.map(encodeBytes);
  if (isPlainObject(value)) return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, encodeBytes(v)]));
  return value;
}

/** Turns every { $bytes: base64 }, at any depth, back into a Uint8Array. Throws on invalid base64. */
export function decodeBytes(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(decodeBytes);
  if (!isPlainObject(value)) return value;
  if (isEncodedBytes(value)) return base64ToBytes(value.$bytes);
  return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, decodeBytes(v)]));
}
