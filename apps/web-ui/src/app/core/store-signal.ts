import { signal, type DestroyRef, type Signal } from '@angular/core';
import type { Store } from '@desk/ui-core';

/** A `@desk/ui-core` store as a read-only signal. Pass a DestroyRef to stop following it. */
export function fromStore<T>(store: Store<T>, destroyRef?: DestroyRef): Signal<T> {
  const value = signal(store.get());
  const off = store.subscribe(() => value.set(store.get()));
  destroyRef?.onDestroy(off);
  return value.asReadonly();
}
