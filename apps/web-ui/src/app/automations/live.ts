import { computed, DestroyRef, effect, inject, signal, untracked, type Signal } from '@angular/core';
import type { StoredEvent } from '@desk/protocol';
import { eventsAfter } from '@desk/ui-core';
import { DeskCallError } from '../core/desk-bridge';

export type LiveStatus = 'loading' | 'ready' | 'missing' | 'error';

export type Live<T> = {
  status: Signal<LiveStatus>;
  value: Signal<T | null>;
  error: Signal<string | null>;
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
  /** Folds one later event into the snapshot. */
  reduce?: (value: T, e: StoredEvent) => T;
  /** Whether an event means the snapshot should be loaded again. */
  refetch(e: StoredEvent, value: T): boolean;
  debounceMs?: number;
};

type Snap<T> = { key: string; value: T; cursor: number };

/**
 * A daemon snapshot kept current from the project's event log (the desktop's `useLive`): events after the snapshot fold in
 * at once through `reduce`, and events `refetch` picks reload it (debounced), so fields no reducer can derive catch up.
 * `options` is read in reactive contexts, so the signals it reads (the key, the session) drive it.
 */
export function injectLive<T>(options: () => LiveOptions<T>): Live<T> {
  const opts = computed(options);
  const key = computed(() => opts().key);
  const ready = computed(() => opts().ready);
  const events = computed(() => opts().events);
  const snap = signal<Snap<T> | null>(null);
  const status = signal<LiveStatus>('loading');
  const error = signal<string | null>(null);
  let gen = 0;
  let seen = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const fetchNow = (fresh: boolean): void => {
    const g = ++gen;
    const o = untracked(opts);
    const cursor = o.events.at(-1)?.id ?? 0;
    if (fresh) {
      snap.set(null);
      status.set('loading');
    }
    o.load().then(
      (value) => {
        if (g !== gen) return;
        seen = Math.max(seen, cursor);
        snap.set({ key: o.key, value, cursor });
        status.set('ready');
        error.set(null);
      },
      (err: unknown) => {
        if (g !== gen) return;
        if (err instanceof DeskCallError && err.status === 404) {
          status.set('missing');
          return;
        }
        error.set(err instanceof Error ? err.message : String(err));
        status.update((s) => (s === 'ready' ? s : 'error'));
      },
    );
  };

  // A new key, or the session's backfill arriving, loads from scratch.
  effect((onCleanup) => {
    key();
    const r = ready();
    untracked(() => {
      seen = 0;
      if (r) fetchNow(true);
    });
    onCleanup(() => {
      gen++;
      clearTimeout(timer);
    });
  });

  // Events after the ones already seen: a burst that `refetch` picks reloads once.
  effect(() => {
    const s = snap();
    const k = key();
    const evs = events();
    if (!s || s.key !== k) return;
    untracked(() => {
      const fresh = eventsAfter(evs, seen);
      if (!fresh.length) return;
      seen = fresh.at(-1)!.id;
      const o = opts();
      if (!fresh.some((e) => o.refetch(e, s.value))) return;
      clearTimeout(timer);
      timer = setTimeout(() => fetchNow(false), o.debounceMs ?? 300);
    });
  });

  inject(DestroyRef).onDestroy(() => {
    gen++;
    clearTimeout(timer);
  });

  const value = computed(() => {
    const s = snap();
    if (!s || s.key !== key()) return null;
    const reduce = untracked(opts).reduce;
    return reduce ? eventsAfter(events(), s.cursor).reduce(reduce, s.value) : s.value;
  });

  return {
    status: computed(() => {
      const s = snap();
      return s && s.key !== key() ? 'loading' : status();
    }),
    value,
    error: error.asReadonly(),
    reload: () => fetchNow(false),
    replace: (v: T) => {
      gen++;
      const o = untracked(opts);
      const cursor = o.events.at(-1)?.id ?? 0;
      seen = Math.max(seen, cursor);
      snap.set({ key: o.key, value: v, cursor });
      status.set('ready');
    },
  };
}
