import { DestroyRef, computed, effect, inject, signal, untracked, type Signal } from '@angular/core';
import type { CatalogItem } from '@desk/protocol';
import { describeError } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { singleFlight } from '../../core/refresh';
import { GlobalStore } from '../../core/global.store';

/** The catalog's pure helpers live in `@desk/ui-core`, shared by both UIs; its bays keep their name here. */
export { CATALOG_BAYS as BAYS, actionFor, catalogIndex, fromCatalog, installRef, runtimePackages, runtimeWords, sourceLabel } from '@desk/ui-core';

/** What `injectCatalog` gives a screen (the React `useCatalog` result). */
export type CatalogState = {
  status: Signal<'loading' | 'ready' | 'error'>;
  error: Signal<string | null>;
  items: Signal<CatalogItem[]>;
  refresh(): Promise<void>;
};

/**
 * The catalog with where each entry is installed (renderer/skills/catalog/data.ts `useCatalog`). Lists again when a skill
 * runtime changes state (the broker bumps `runtimes.seq`), on focus, and every 3 s while a runtime is being set up.
 * Call it in a field initializer.
 */
export function injectCatalog(): CatalogState {
  const bridge = inject(DeskBridge);
  const global = inject(GlobalStore);
  const items = signal<CatalogItem[] | null>(null);
  const error = signal<string | null>(null);
  // One list at a time (a runtime change, focus, the 3 s poll): an ask while one runs lists once more when it ends, so
  // a list slower than the poll neither piles up calls nor lets an older catalog land last.
  const flight = singleFlight(async () => {
    try {
      items.set(await bridge.call('catalog.list', {}));
      error.set(null);
    } catch (err) {
      error.set(describeError(err).message);
    }
  });
  const refresh = flight.run;

  // Its own computed, so a desk:global push that leaves the sequence alone lists nothing.
  const seq = computed(() => global.state().runtimes.seq);
  effect(() => {
    seq();
    untracked(() => void refresh());
  });

  const onFocus = () => void refresh();
  window.addEventListener('focus', onFocus);
  inject(DestroyRef).onDestroy(() => {
    window.removeEventListener('focus', onFocus);
    flight.stop();
  });

  const preparing = computed(() => (items() ?? []).some((i) => i.installs.some((x) => x.runtime === 'preparing')));
  effect((onCleanup) => {
    if (!preparing()) return;
    const timer = setInterval(() => void refresh(), 3000);
    onCleanup(() => clearInterval(timer));
  });

  const status = computed<'loading' | 'ready' | 'error'>(() => (items() ? 'ready' : error() ? 'error' : 'loading'));
  return { status, error: error.asReadonly(), items: computed(() => items() ?? []), refresh };
}
