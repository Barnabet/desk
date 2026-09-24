import type { ProjectSummary } from '@desk/protocol';
import { createStore, useStore } from '../store';

const KEY = 'desk.seen';

function load(): Record<string, string> {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? '{}') as unknown;
    return v && typeof v === 'object' ? (v as Record<string, string>) : {};
  } catch {
    return {};
  }
}

const seenStore = createStore<Record<string, string>>(load());

/** Records that the user has seen a project (its conversation is open). Local to this viewer. */
export function markSeen(projectId: string, at: string = new Date().toISOString()): void {
  seenStore.set((all) => ({ ...all, [projectId]: at }));
  try {
    localStorage.setItem(KEY, JSON.stringify(seenStore.get()));
  } catch {
    // A convenience only.
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

export function useUnread(p: ProjectSummary): boolean {
  return useStore(seenStore, (s) => unreadIn(p, s));
}

export function resetSeen(): void {
  seenStore.set({});
}

/** The whole last-seen map (a stable reference until something is marked seen). */
export function useSeen(): Record<string, string> {
  return useStore(seenStore, (s) => s);
}
