import { ChangeDetectionStrategy, Component, DestroyRef, Injectable, inject, signal, ViewEncapsulation, type Signal } from '@angular/core';
import { DeskBridge, DeskCallError } from '../core/desk-bridge';

export type Toast = { id: number; tone: 'error' | 'info'; message: string; action?: { label: string; run(): void } };

/** Server failures (5xx, internal) offer the logs; everything else is explained in place by its message. */
export function describeError(err: unknown): { message: string; revealLogs: boolean } {
  if (err instanceof DeskCallError) return { message: err.message, revealLogs: err.code === 'internal' || (err.status ?? 0) >= 500 };
  return { message: err instanceof Error ? err.message : String(err), revealLogs: false };
}

/** The toast list (the desktop's toastStore, toast, dismissToast and toastError). */
@Injectable({ providedIn: 'root' })
export class ToastService {
  private readonly bridge = inject(DeskBridge);
  private readonly value = signal<Toast[]>([]);
  readonly list: Signal<Toast[]> = this.value.asReadonly();
  private nextId = 1;
  private readonly timers = new Set<ReturnType<typeof setTimeout>>();

  constructor() {
    inject(DestroyRef).onDestroy(() => {
      for (const t of this.timers) clearTimeout(t);
      this.timers.clear();
    });
  }

  toast(t: Omit<Toast, 'id'>, ms = 6000): number {
    const id = this.nextId++;
    this.value.update((list) => [...list, { ...t, id }]);
    const timer = setTimeout(() => {
      this.timers.delete(timer);
      this.dismiss(id);
    }, ms);
    this.timers.add(timer);
    return id;
  }

  dismiss(id: number): void {
    this.value.update((list) => list.filter((t) => t.id !== id));
  }

  /** The desktop's toastError. */
  error(err: unknown): void {
    const d = describeError(err);
    this.toast({
      tone: 'error',
      message: d.message,
      ...(d.revealLogs ? { action: { label: 'Reveal logs', run: () => void this.bridge.call('app.revealLogs', {}).catch(() => {}) } } : {}),
    });
  }
}

@Component({
  selector: 'div[deskToaster]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'toaster', role: 'status', 'aria-live': 'polite' },
  template: `
    @for (t of toasts.list(); track t.id) {
      <div class="toast" [class]="'toast-' + t.tone">
        <span>{{ t.message }}</span>
        @if (t.action; as action) {
          <button type="button" class="btn btn-ghost btn-sm" (click)="action.run()">{{ action.label }}</button>
        }
        <button type="button" class="toast-close" aria-label="Dismiss" (click)="toasts.dismiss(t.id)">×</button>
      </div>
    }
  `,
})
export class Toaster {
  protected readonly toasts = inject(ToastService);
}
