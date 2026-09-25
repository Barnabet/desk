import { DestroyRef, afterRenderEffect, inject, signal, type Signal } from '@angular/core';

/**
 * The element's client width, kept current with a ResizeObserver (jsdom reports 0, so `fallback` stands in).
 * `target` is usually a viewChild; call it in an injection context.
 */
export function injectWidth(target: () => HTMLElement | null | undefined, fallback = 1200): Signal<number> {
  const width = signal(fallback);
  let observed: HTMLElement | null = null;
  let observer: ResizeObserver | null = null;
  afterRenderEffect(() => {
    const el = target() ?? null;
    if (el === observed) return;
    observer?.disconnect();
    observer = null;
    observed = el;
    if (!el) return;
    const measure = () => width.set(el.clientWidth || fallback);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    observer = new ResizeObserver(measure);
    observer.observe(el);
  });
  inject(DestroyRef).onDestroy(() => observer?.disconnect());
  return width.asReadonly();
}
