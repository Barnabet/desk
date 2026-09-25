import { DestroyRef, inject, signal, type Signal } from '@angular/core';

/** Whether a CSS media query matches, following changes (false where matchMedia is missing, e.g. jsdom). Injection context. */
export function injectMediaQuery(query: string): Signal<boolean> {
  if (typeof window.matchMedia !== 'function') return signal(false).asReadonly();
  const mql = window.matchMedia(query);
  const matches = signal(mql.matches);
  const onChange = () => matches.set(mql.matches);
  mql.addEventListener('change', onChange);
  inject(DestroyRef).onDestroy(() => mql.removeEventListener('change', onChange));
  return matches.asReadonly();
}
