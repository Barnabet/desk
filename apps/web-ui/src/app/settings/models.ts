import { DestroyRef, inject, signal, type Signal } from '@angular/core';
import type { ModelInfo } from '@desk/protocol';
import { DeskBridge } from '../core/desk-bridge';

/** The model registry, fetched once per component (React's `useModels`); null while loading or unavailable. Call it in an injection context. */
export function injectModels(): Signal<ModelInfo[] | null> {
  const bridge = inject(DeskBridge);
  const models = signal<ModelInfo[] | null>(null);
  let live = true;
  inject(DestroyRef).onDestroy(() => {
    live = false;
  });
  bridge
    .call('models.list', {})
    .then((m) => {
      if (live) models.set(m);
    })
    .catch(() => {
      if (live) models.set(null);
    });
  return models.asReadonly();
}
