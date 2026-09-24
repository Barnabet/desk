import type { ArtifactKind, StoredEvent } from '@desk/protocol';

export type LibraryItem = { id: string; path: string; title: string; kind: ArtifactKind; origin: string; description: string; ts: string; eventId: number };

/** Library items from the project log, newest first; a republished path replaces its older entry. */
export function libraryFromEvents(events: StoredEvent[]): LibraryItem[] {
  const byPath = new Map<string, LibraryItem>();
  for (const e of events) {
    if (e.type !== 'artifact.published') continue;
    const p = e.payload;
    byPath.set(p.path, { id: p.artifact_id, path: p.path, title: p.title, kind: p.kind, origin: p.origin, description: p.description, ts: e.ts, eventId: e.id });
  }
  return [...byPath.values()].sort((a, b) => b.eventId - a.eventId);
}

/** `user` or `agent:<id>` → who published it. */
export const originAgent = (origin: string): string | null => (origin.startsWith('agent:') ? origin.slice(6) : null);

export function filterLibrary(items: LibraryItem[], kind: ArtifactKind | 'all', query: string): LibraryItem[] {
  const q = query.trim().toLowerCase();
  return items.filter((i) => (kind === 'all' || i.kind === kind) && (!q || i.title.toLowerCase().includes(q) || i.path.toLowerCase().includes(q) || i.description.toLowerCase().includes(q)));
}
