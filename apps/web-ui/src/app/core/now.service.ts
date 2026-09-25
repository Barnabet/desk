import { DestroyRef, Injectable, inject, signal, type Signal } from '@angular/core';

const STEP_MS = 15_000;

/** The current time, shared and refreshed every 15 s (elapsed times, the "now" line): renderer/state/now.ts. */
@Injectable({ providedIn: 'root' })
export class NowService {
  private readonly value = signal(Date.now());
  readonly now: Signal<number> = this.value.asReadonly();

  constructor() {
    const timer = setInterval(() => this.value.set(Date.now()), STEP_MS);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }
}
