import { createEnvironmentInjector, DestroyRef, EnvironmentInjector } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';
import { createStore } from '@desk/ui-core';
import { fromStore } from './store-signal';

describe('fromStore', () => {
  it('follows a ui-core store until its DestroyRef is destroyed', () => {
    const store = createStore({ n: 1 });
    const scope = createEnvironmentInjector([], TestBed.inject(EnvironmentInjector));
    const value = fromStore(store, scope.get(DestroyRef));
    expect(value()).toEqual({ n: 1 });
    store.set((s) => ({ n: s.n + 1 }));
    expect(value()).toEqual({ n: 2 });
    scope.destroy();
    store.set({ n: 3 });
    expect(value()).toEqual({ n: 2 });
  });
});
