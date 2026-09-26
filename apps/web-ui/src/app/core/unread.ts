import { DestroyRef, Injectable, inject, signal, type Signal } from '@angular/core';
import type { ProjectSummary } from '@desk/protocol';

const KEY = 'desk.seen';

function parse(raw: string | null): Record<string, string> {
  try {
    const v = JSON.parse(raw ?? '{}') as unknown;
    return v && typeof v === 'object' ? (v as Record<string, string>) : {};
  } catch {
    return {};
  }
}

function load(): Record<string, string> {
  try {
    return parse(localStorage.getItem(KEY));
  } catch {
    return {};
  }
}

/** `base` with each of `more`'s marks that is later than its own; `base` itself when none is. */
function later(base: Record<string, string>, more: Record<string, string>): Record<string, string> {
  let out = base;
  for (const [id, at] of Object.entries(more)) {
    if (typeof at !== 'string' || (Object.hasOwn(out, id) && out[id]! >= at)) continue;
    if (out === base) out = { ...base };
    out[id] = at;
  }
  return out;
}

/** The latest thing that happened in a project (a report or any thread change). */
export function lastActivity(p: ProjectSummary): string {
  return [p.latest_report?.ts ?? '', ...p.threads.map((t) => t.updated_at)].reduce((a, b) => (b > a ? b : a), '');
}

/** Something happened since the last visit; never-visited projects count once they have activity. */
export function unreadIn(p: ProjectSummary, seen: Record<string, string>): boolean {
  const last = lastActivity(p);
  if (!last) return false;
  const at = seen[p.project.id];
  return !at || last > at;
}

/**
 * When the user last saw each project (its conversation was open). Local to this browser and shared by its tabs: marking
 * keeps what other tabs stored (the later mark for each project), and a `storage` event brings their marks in.
 */
@Injectable({ providedIn: 'root' })
export class Unread {
  private readonly value = signal<Record<string, string>>(load());
  /** The whole last-seen map (a new object only when a mark changes). */
  readonly seen: Signal<Record<string, string>> = this.value.asReadonly();

  constructor() {
    const onStorage = (e: StorageEvent) => {
      if (e.key === KEY) this.value.update((all) => later(all, parse(e.newValue)));
    };
    window.addEventListener('storage', onStorage);
    inject(DestroyRef).onDestroy(() => window.removeEventListener('storage', onStorage));
  }

  markSeen(projectId: string, at: string = new Date().toISOString()): void {
    const next = later(later(this.value(), load()), { [projectId]: at });
    this.value.set(next);
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      // A convenience only.
    }
  }

  /** useUnread: reactive when read in a template or a computed. */
  isUnread(p: ProjectSummary): boolean {
    return unreadIn(p, this.value());
  }

  reset(): void {
    this.value.set({});
  }
}
