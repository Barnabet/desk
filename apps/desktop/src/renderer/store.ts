import { useSyncExternalStore } from 'react';
import type { Store } from '@desk/ui-core';

export function useStore<T, S>(store: Store<T>, select: (value: T) => S): S {
  return useSyncExternalStore(store.subscribe, () => select(store.get()));
}
