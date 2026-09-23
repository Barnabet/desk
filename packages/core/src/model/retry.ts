import { classifyModelError, ModelError } from './errors';

export type RetryOptions = {
  maxAttempts?: number;
  baseMs?: number;
  capMs?: number;
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
  random?: () => number;
  signal?: AbortSignal;
  onRetry?: (err: ModelError, attempt: number, delayMs: number) => void;
};

export function abortableSleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new ModelError('aborted', 'Aborted during backoff'));
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new ModelError('aborted', 'Aborted during backoff'));
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/** Retries rate_limited and transient errors with full-jitter exponential backoff. Always throws ModelError. */
export async function withRetry<T>(fn: () => Promise<T>, opts: RetryOptions = {}): Promise<T> {
  const { maxAttempts = 6, baseMs = 2000, capMs = 60_000, sleep = abortableSleep, random = Math.random } = opts;
  for (let attempt = 1; ; attempt++) {
    try {
      return await fn();
    } catch (e) {
      const err = classifyModelError(e);
      const retryable = err.kind === 'rate_limited' || err.kind === 'transient';
      if (!retryable || attempt >= maxAttempts) throw err;
      const delay = Math.floor(random() * Math.min(capMs, baseMs * 2 ** (attempt - 1)));
      opts.onRetry?.(err, attempt, delay);
      await sleep(delay, opts.signal);
    }
  }
}
