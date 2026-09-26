import { DestroyRef, inject, signal, type Signal } from '@angular/core';

/** Whether a CSS media query matches, following changes (false where matchMedia is missing, e.g. jsdom). Injection context. */
export function injectMediaQuery(query: string): Signal<boolean> {
  // First, so a call outside an injection context fails here too, and before a listener that could not be removed.
  const destroyRef = inject(DestroyRef);
  if (typeof window.matchMedia !== 'function') return signal(false).asReadonly();
  const mql = window.matchMedia(query);
  const matches = signal(mql.matches);
  const onChange = () => matches.set(mql.matches);
  mql.addEventListener('change', onChange);
  destroyRef.onDestroy(() => mql.removeEventListener('change', onChange));
  return matches.asReadonly();
}
