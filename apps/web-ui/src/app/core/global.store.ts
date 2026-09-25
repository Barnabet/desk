import { Injectable, inject, signal, type Signal } from '@angular/core';
import { initialGlobalState, type GlobalState } from '@desk/bff/contract';
import { DeskBridge } from './desk-bridge';

/** Connection, health, overview, attention, system and runtimes: the broker's `desk:global` state (renderer/state/global.ts). */
@Injectable({ providedIn: 'root' })
export class GlobalStore {
  private readonly bridge = inject(DeskBridge);
  private readonly value = signal<GlobalState>(initialGlobalState());
  readonly state: Signal<GlobalState> = this.value.asReadonly();

  set(next: GlobalState | ((prev: GlobalState) => GlobalState)): void {
    this.value.update((prev) => (typeof next === 'function' ? next(prev) : next));
  }

  /** Seeds from the broker's snapshot, then follows its pushes; returns the function that stops following them. */
  start(): () => void {
    let pushed = false;
    const off = this.bridge.onPush<GlobalState>('desk:global', (s) => {
      pushed = true;
      this.value.set(s);
    });
    this.bridge
      .call('broker.snapshot', {})
      .then((s) => {
        if (!pushed) this.value.set(s);
      })
      .catch(() => {});
    return off;
  }
}
