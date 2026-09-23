import { describe, expect, it, vi } from 'vitest';
import { ModelError } from './errors';
import { abortableSleep, withRetry } from './retry';

const noSleep = { sleep: async () => {}, random: () => 1 };

describe('withRetry', () => {
  it('retries transient errors then succeeds', async () => {
    const fn = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new ModelError('transient', 'blip'))
      .mockRejectedValueOnce(new ModelError('rate_limited', 'slow'))
      .mockResolvedValue('ok');
    await expect(withRetry(fn, noSleep)).resolves.toBe('ok');
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it('gives up after maxAttempts', async () => {
    const fn = vi.fn<() => Promise<string>>().mockRejectedValue(new ModelError('rate_limited', 'slow'));
    await expect(withRetry(fn, { ...noSleep, maxAttempts: 3 })).rejects.toMatchObject({ kind: 'rate_limited' });
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it('does not retry fatal errors', async () => {
    const fn = vi.fn<() => Promise<string>>().mockRejectedValue(new ModelError('fatal', 'nope'));
    await expect(withRetry(fn, noSleep)).rejects.toMatchObject({ kind: 'fatal' });
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('uses full-jitter exponential backoff capped at capMs', async () => {
    const delays: number[] = [];
    const fn = vi.fn<() => Promise<string>>().mockRejectedValue(new ModelError('transient', 'x'));
    await withRetry(fn, {
      maxAttempts: 6,
      baseMs: 2000,
      capMs: 10_000,
      random: () => 0.5,
      sleep: async () => {},
      onRetry: (_e, _a, d) => delays.push(d),
    }).catch(() => {});
    expect(delays).toEqual([1000, 2000, 4000, 5000, 5000]);
  });
});

describe('abortableSleep', () => {
  it('rejects with an aborted ModelError when the signal fires', async () => {
    const c = new AbortController();
    const p = abortableSleep(10_000, c.signal);
    c.abort();
    await expect(p).rejects.toMatchObject({ kind: 'aborted' });
  });
});
