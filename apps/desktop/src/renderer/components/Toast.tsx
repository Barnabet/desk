import { call, DeskCallError } from '../bridge';
import { createStore, useStore } from '../store';

export type Toast = { id: number; tone: 'error' | 'info'; message: string; action?: { label: string; run(): void } };

export const toastStore = createStore<Toast[]>([]);
let nextId = 1;

export function dismissToast(id: number): void {
  toastStore.set((list) => list.filter((t) => t.id !== id));
}

export function toast(t: Omit<Toast, 'id'>, ms = 6000): void {
  const id = nextId++;
  toastStore.set((list) => [...list, { ...t, id }]);
  setTimeout(() => dismissToast(id), ms);
}

/** Server failures (5xx, internal) offer the logs; everything else is explained in place by its message. */
export function describeError(err: unknown): { message: string; revealLogs: boolean } {
  if (err instanceof DeskCallError) return { message: err.message, revealLogs: err.code === 'internal' || (err.status ?? 0) >= 500 };
  return { message: err instanceof Error ? err.message : String(err), revealLogs: false };
}

export function toastError(err: unknown): void {
  const d = describeError(err);
  toast({ tone: 'error', message: d.message, ...(d.revealLogs ? { action: { label: 'Reveal logs', run: () => void call('app.revealLogs', {}).catch(() => {}) } } : {}) });
}

export function Toaster() {
  const toasts = useStore(toastStore, (t) => t);
  return (
    <div className="toaster" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.tone}`}>
          <span>{t.message}</span>
          {t.action ? (
            <button type="button" className="btn btn-ghost btn-sm" onClick={t.action.run}>
              {t.action.label}
            </button>
          ) : null}
          <button type="button" className="toast-close" aria-label="Dismiss" onClick={() => dismissToast(t.id)}>
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
