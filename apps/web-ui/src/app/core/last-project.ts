import { Injectable, signal, type Signal } from '@angular/core';

const KEY = 'desk.lastProject';

function read(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

/** The project the user was last in, so the title bar keeps it while they visit Map, Skills or System. */
@Injectable({ providedIn: 'root' })
export class LastProject {
  private readonly value = signal<string | null>(read());
  readonly id: Signal<string | null> = this.value.asReadonly();

  remember(id: string): void {
    if (this.value() === id) return;
    this.value.set(id);
    try {
      localStorage.setItem(KEY, id);
    } catch {
      // A convenience only.
    }
  }

  reset(): void {
    this.value.set(null);
    try {
      localStorage.removeItem(KEY);
    } catch {
      // A convenience only.
    }
  }
}
