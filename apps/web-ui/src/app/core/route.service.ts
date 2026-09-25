import { DestroyRef, Injectable, computed, inject, signal, type Signal } from '@angular/core';
import { href, parseRoute, type Route } from '@desk/ui-core';

/** The desktop's hash routes (`#/map`, `#/p/<id>/conversation`, …) as a signal. There is no Angular Router (spec §5). */
@Injectable({ providedIn: 'root' })
export class RouteService {
  private readonly hash = signal(window.location.hash);
  readonly route: Signal<Route> = computed(() => parseRoute(this.hash()));

  constructor() {
    const onHash = () => this.hash.set(window.location.hash);
    window.addEventListener('hashchange', onHash);
    inject(DestroyRef).onDestroy(() => window.removeEventListener('hashchange', onHash));
  }

  /** Goes to a route, adding a history entry. */
  navigate(to: Route | string): void {
    const target = typeof to === 'string' ? to : href(to);
    window.location.hash = target.replace(/^#/, '');
    this.hash.set(window.location.hash);
  }

  /** Changes the route without adding a history entry (selection within a screen). */
  replace(to: Route | string): void {
    const target = typeof to === 'string' ? to : href(to);
    if (window.location.hash === target) return;
    history.replaceState(null, '', target);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  }
}
