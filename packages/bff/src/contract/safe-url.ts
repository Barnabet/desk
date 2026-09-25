/** A link Desk will not open. `message` is written for people; `code` is the IPC error code. */
export class UnsafeUrlError extends Error {
  readonly code = 'invalid_url';

  constructor(message: string) {
    super(message);
    this.name = 'UnsafeUrlError';
  }
}

/**
 * The normalised form of a link from agent text, when it is a web or mail link (`http:`, `https:`, `mailto:`). Anything
 * else throws `UnsafeUrlError`. Electron main runs it before `shell.openExternal`; the web UI before `window.open`.
 */
export function safeExternalUrl(raw: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new UnsafeUrlError('That is not a valid link.');
  }
  if (!['http:', 'https:', 'mailto:'].includes(url.protocol)) throw new UnsafeUrlError('Only web and mail links can be opened.');
  return url.toString();
}
