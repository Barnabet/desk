export type Store<T> = { get(): T; set(next: T | ((prev: T) => T)): void; subscribe(fn: () => void): () => void };

/**
 * A minimal external store. React reads it with `useStore`, Angular with `fromStore`; selectors must return stable values
 * (e.g. fields, not new objects).
 */
export function createStore<T>(initial: T): Store<T> {
  let value = initial;
  const subs = new Set<() => void>();
  return {
    get: () => value,
    set: (next) => {
      value = typeof next === 'function' ? (next as (prev: T) => T)(value) : next;
      for (const s of subs) s();
    },
    subscribe: (fn) => {
      subs.add(fn);
      return () => {
        subs.delete(fn);
      };
    },
  };
}
