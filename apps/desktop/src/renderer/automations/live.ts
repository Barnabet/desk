import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { StoredEvent } from '@desk/protocol';
import { eventsAfter } from '@desk/ui-core';
import { DeskCallError } from '../bridge';

export type LiveStatus = 'loading' | 'ready' | 'missing' | 'error';

export type Live<T> = {
  status: LiveStatus;
  value: T | null;
  error: string | null;
  /** Loads again, keeping the current value on screen meanwhile. */
  reload(): void;
  /** Adopts a value a write returned, as of the latest event. */
  replace(value: T): void;
};

export type LiveOptions<T> = {
  /** A new key loads from scratch. */
  key: string;
  /** The project session's log, in id order. */
  events: readonly StoredEvent[];
  /** Whether the session has its backfill: loading waits for it, so the snapshot's cursor means something. */
  ready: boolean;
  load(): Promise<T>;
  /** Folds one later event into the snapshot. Pass a module-level function: it is a memo dependency. */
  reduce?: (value: T, e: StoredEvent) => T;
  /** Whether an event means the snapshot should be loaded again. */
  refetch(e: StoredEvent, value: T): boolean;
  debounceMs?: number;
};

type Snap<T> = { key: string; value: T; cursor: number };

/**
 * A daemon snapshot kept current from the project's event log: events after the snapshot fold in at once through
 * `reduce`, and events `refetch` picks reload it (debounced), so fields no reducer can derive catch up.
 */
export function useLive<T>(o: LiveOptions<T>): Live<T> {
  const [snap, setSnap] = useState<Snap<T> | null>(null);
  const [status, setStatus] = useState<LiveStatus>('loading');
  const [error, setError] = useState<string | null>(null);
  const opts = useRef(o);
  opts.current = o;
  const gen = useRef(0);
  const seen = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const fetchNow = useCallback((fresh: boolean) => {
    const g = ++gen.current;
    const key = opts.current.key;
    const cursor = opts.current.events.at(-1)?.id ?? 0;
    if (fresh) {
      setSnap(null);
      setStatus('loading');
    }
    opts.current.load().then(
      (value) => {
        if (g !== gen.current) return;
        seen.current = Math.max(seen.current, cursor);
        setSnap({ key, value, cursor });
        setStatus('ready');
        setError(null);
      },
      (err: unknown) => {
        if (g !== gen.current) return;
        if (err instanceof DeskCallError && err.status === 404) {
          setStatus('missing');
          return;
        }
        setError(err instanceof Error ? err.message : String(err));
        setStatus((s) => (s === 'ready' ? s : 'error'));
      },
    );
  }, []);

  useEffect(() => {
    seen.current = 0;
    if (o.ready) fetchNow(true);
    return () => {
      gen.current++;
      clearTimeout(timer.current);
    };
  }, [o.key, o.ready, fetchNow]);

  useEffect(() => {
    if (!snap || snap.key !== o.key) return;
    const fresh = eventsAfter(o.events, seen.current);
    if (!fresh.length) return;
    seen.current = fresh.at(-1)!.id;
    if (!fresh.some((e) => opts.current.refetch(e, snap.value))) return;
    clearTimeout(timer.current);
    timer.current = setTimeout(() => fetchNow(false), opts.current.debounceMs ?? 300);
  }, [o.events, o.key, snap, fetchNow]);

  const reduce = o.reduce;
  const value = useMemo(() => {
    if (!snap || snap.key !== o.key) return null;
    return reduce ? eventsAfter(o.events, snap.cursor).reduce(reduce, snap.value) : snap.value;
  }, [snap, o.key, o.events, reduce]);

  const reload = useCallback(() => fetchNow(false), [fetchNow]);
  const replace = useCallback((v: T) => {
    gen.current++;
    const cursor = opts.current.events.at(-1)?.id ?? 0;
    seen.current = Math.max(seen.current, cursor);
    setSnap({ key: opts.current.key, value: v, cursor });
    setStatus('ready');
  }, []);
  return { status: snap && snap.key !== o.key ? 'loading' : status, value, error, reload, replace };
}
