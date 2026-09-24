import { useSyncExternalStore } from 'react';

const STEP_MS = 15_000;
let now = Date.now();
const subs = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | undefined;

function subscribe(fn: () => void): () => void {
  subs.add(fn);
  timer ??= setInterval(() => {
    now = Date.now();
    for (const s of subs) s();
  }, STEP_MS);
  return () => {
    subs.delete(fn);
    if (!subs.size && timer) {
      clearInterval(timer);
      timer = undefined;
    }
  };
}

function snapshot(): number {
  if (Date.now() - now > STEP_MS) now = Date.now();
  return now;
}

/** The current time, shared and refreshed every 15 s (elapsed times, the "now" line). */
export function useNow(): number {
  return useSyncExternalStore(subscribe, snapshot);
}
