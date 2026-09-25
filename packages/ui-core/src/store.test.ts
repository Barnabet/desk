import { describe, expect, it } from 'vitest';
import { createStore } from './store';

describe('createStore', () => {
  it('holds a value, sets it directly or from the previous one, and tells its subscribers until they leave', () => {
    const store = createStore({ n: 1 });
    const seen: number[] = [];
    const off = store.subscribe(() => seen.push(store.get().n));
    store.set({ n: 2 });
    store.set((prev) => ({ n: prev.n + 10 }));
    off();
    store.set({ n: 99 });
    expect(store.get()).toEqual({ n: 99 });
    expect(seen).toEqual([2, 12]);
  });
});
