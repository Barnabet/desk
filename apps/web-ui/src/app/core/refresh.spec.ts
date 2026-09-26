import { describe, expect, it } from 'vitest';
import { singleFlight } from './refresh';

/** A load held until released, counting its calls. */
function held() {
  const releases: Array<() => void> = [];
  let calls = 0;
  const load = () => {
    calls++;
    return new Promise<void>((resolve) => releases.push(resolve));
  };
  return { load, calls: () => calls, release: () => releases.shift()?.() };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('singleFlight', () => {
  it('starts nothing while a load runs, then loads once more for every ask that came in meanwhile', async () => {
    const h = held();
    const r = singleFlight(h.load);
    const first = r.run();
    let asked = false;
    const second = r.run().then(() => (asked = true));
    void r.run();
    void r.run();
    expect(h.calls()).toBe(1);
    h.release();
    await tick();
    // The asks got one more load, and the asker waits for it: an answer that started after it.
    expect(h.calls()).toBe(2);
    expect(asked).toBe(false);
    h.release();
    await Promise.all([first, second]);
    expect(asked).toBe(true);
    expect(h.calls()).toBe(2);
  });

  it('loads at once when nothing runs, and each time it is asked after one ends', async () => {
    let calls = 0;
    const r = singleFlight(async () => void calls++);
    await r.run();
    await r.run();
    expect(calls).toBe(2);
  });

  it('loads nothing more once stopped, not even the pending rerun', async () => {
    const h = held();
    const r = singleFlight(h.load);
    const first = r.run();
    void r.run();
    r.stop();
    h.release();
    await first;
    await r.run();
    expect(h.calls()).toBe(1);
  });
});
