import { useLayoutEffect, useState, type RefObject } from 'react';

/** The element's client width, kept current with a ResizeObserver (jsdom reports 0, so `fallback` stands in). */
export function useWidth(ref: RefObject<HTMLElement | null>, fallback = 1200): number {
  const [w, setW] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setW(el.clientWidth || fallback);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, fallback]);
  return w;
}
