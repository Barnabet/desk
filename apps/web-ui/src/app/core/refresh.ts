/** A refresh that never overlaps itself: `run()` to ask for one, `stop()` once its owner is gone. */
export type Refresh = { run(): Promise<void>; stop(): void };

/**
 * Runs `load` one at a time. An ask while a load runs starts nothing: it marks the refresh dirty and gets the running
 * call's promise, which loads once more when the current load ends, however many asks came in meanwhile. So a list slower
 * than its poll never piles up overlapping calls, a slow earlier answer never lands after a newer one, and the last ask
 * always gets an answer that started after it (the loads are never starved). After `stop()` nothing more loads.
 * `load` handles its own errors; one that escapes rejects that run's promise and the next ask loads again.
 */
export function singleFlight(load: () => Promise<void>): Refresh {
  let running: Promise<void> | null = null;
  let dirty = false;
  let stopped = false;
  const run = (): Promise<void> => {
    if (stopped) return Promise.resolve();
    if (running) {
      dirty = true;
      return running;
    }
    let settled = false;
    const flight = (async () => {
      try {
        do {
          dirty = false;
          await load();
        } while (dirty && !stopped);
      } finally {
        settled = true;
        running = null;
      }
    })();
    // A `load` that throws before returning a promise settles the flight before this line: it must not stay `running`.
    if (!settled) running = flight;
    return flight;
  };
  return {
    run,
    stop: () => {
      stopped = true;
    },
  };
}
