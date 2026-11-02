import type { ModelConfig } from './config';
import { apiError } from './errors';

/** Server-sent events of a response body, as parsed JSON `data` lines. */
export async function* events(body: ReadableStream<Uint8Array>): AsyncGenerator<Record<string, any>> {
  const decoder = new TextDecoder();
  let buffer = '';
  for await (const chunk of body as unknown as AsyncIterable<Uint8Array>) {
    buffer += decoder.decode(chunk, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const data = frame
        .split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).trimStart())
        .join('\n');
      if (data && data !== '[DONE]') yield JSON.parse(data) as Record<string, any>;
    }
  }
}

/**
 * POSTs `body` as JSON to `<base><path>` and returns the streamed response body. An error answer throws its ModelError
 * (status, the endpoint's error type and message); the request gives up after 10 minutes.
 */
export async function postStream(config: ModelConfig, path: string, body: unknown, signal: AbortSignal | undefined, headers: Record<string, string> = {}): Promise<ReadableStream<Uint8Array>> {
  const res = await fetch(`${config.baseURL}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${config.apiKey}`, ...headers },
    body: JSON.stringify(body),
    signal: AbortSignal.any([AbortSignal.timeout(10 * 60_000), ...(signal ? [signal] : [])]),
  });
  if (res.ok && res.body) return res.body;
  const text = await res.text().catch(() => '');
  let type: unknown;
  let message = text || `${res.status} ${res.statusText}`;
  try {
    const parsed = JSON.parse(text) as { error?: { type?: unknown; code?: unknown; message?: unknown } };
    type = parsed.error?.type ?? parsed.error?.code;
    if (typeof parsed.error?.message === 'string') message = parsed.error.message;
  } catch {}
  throw apiError(res.status, `${res.status} ${message}`, type);
}
