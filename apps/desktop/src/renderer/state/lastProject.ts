import { createStore } from '@desk/ui-core';
import { useStore } from '../store';

const KEY = 'desk.lastProject';

function read(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

/** The project the user was last in, so the title bar keeps it while they visit Map, Skills or System. */
const store = createStore<string | null>(read());

export function rememberProject(id: string): void {
  if (store.get() === id) return;
  store.set(id);
  try {
    localStorage.setItem(KEY, id);
  } catch {
    // A convenience only.
  }
}

export const useLastProject = (): string | null => useStore(store, (s) => s);

/** Tests: forget the remembered project. */
export function resetLastProject(): void {
  store.set(null);
  try {
    localStorage.removeItem(KEY);
  } catch {}
}
