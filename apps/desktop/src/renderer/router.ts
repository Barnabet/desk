import { useMemo, useSyncExternalStore } from 'react';
import { href, parseRoute, type Route } from '@desk/ui-core';

export function navigate(to: Route | string): void {
  const target = typeof to === 'string' ? to : href(to);
  window.location.hash = target.replace(/^#/, '');
}

/** Changes the route without adding a history entry (selection within a screen). */
export function replaceRoute(to: Route | string): void {
  const target = typeof to === 'string' ? to : href(to);
  if (window.location.hash === target) return;
  history.replaceState(null, '', target);
  window.dispatchEvent(new HashChangeEvent('hashchange'));
}

const subscribeHash = (fn: () => void) => {
  window.addEventListener('hashchange', fn);
  return () => window.removeEventListener('hashchange', fn);
};

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribeHash, () => window.location.hash);
  return useMemo(() => parseRoute(hash), [hash]);
}
