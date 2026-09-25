import { Injectable, signal, type Signal } from '@angular/core';
import type { ProjectSummary } from '@desk/protocol';

const KEY = 'desk.seen';

function load(): Record<string, string> {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? '{}') as unknown;
    return v && typeof v === 'object' ? (v as Record<string, string>) : {};
  } catch {
    return {};
  }
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

/** When the user last saw each project (its conversation was open). Local to this browser. */
@Injectable({ providedIn: 'root' })
export class Unread {
  private readonly value = signal<Record<string, string>>(load());
  /** The whole last-seen map (a new object only when something is marked seen). */
  readonly seen: Signal<Record<string, string>> = this.value.asReadonly();

  markSeen(projectId: string, at: string = new Date().toISOString()): void {
    this.value.update((all) => ({ ...all, [projectId]: at }));
    try {
      localStorage.setItem(KEY, JSON.stringify(this.value()));
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
