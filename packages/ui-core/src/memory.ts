import type { MemoryKind, StoredEvent } from '@desk/protocol';

export type MemoryEntry = { id: string; kind: MemoryKind; content: string; source: string; ts: string; supersedes: string | null; supersededBy: string | null; deleted: boolean };

export const MEMORY_KINDS: Array<{ kind: MemoryKind; title: string }> = [
  { kind: 'decision', title: 'Decisions' },
  { kind: 'fact', title: 'Facts' },
  { kind: 'preference', title: 'Preferences' },
  { kind: 'contact', title: 'Contacts' },
  { kind: 'note', title: 'Notes' },
];

/** Every memory entry ever written in the project, with supersession links, by id. */
export function memoryFromEvents(events: StoredEvent[]): Map<string, MemoryEntry> {
  const all = new Map<string, MemoryEntry>();
  for (const e of events) {
    if (e.type === 'memory.written') {
      const p = e.payload;
      all.set(p.memory_id, { id: p.memory_id, kind: p.kind, content: p.content, source: p.source, ts: e.ts, supersedes: p.supersedes ?? null, supersededBy: null, deleted: false });
      const old = p.supersedes ? all.get(p.supersedes) : undefined;
      if (old) all.set(old.id, { ...old, supersededBy: p.memory_id });
    } else if (e.type === 'memory.deleted') {
      const m = all.get(e.payload.memory_id);
      if (m) all.set(m.id, { ...m, deleted: true });
    }
  }
  return all;
}

/** Entries in use (not superseded, not deleted), newest first. */
export const activeEntries = (all: Map<string, MemoryEntry>): MemoryEntry[] =>
  [...all.values()].filter((m) => !m.supersededBy && !m.deleted).sort((a, b) => b.ts.localeCompare(a.ts) || b.id.localeCompare(a.id));

/** The versions an entry replaced, newest first. */
export function chainOf(all: Map<string, MemoryEntry>, id: string): MemoryEntry[] {
  const out: MemoryEntry[] = [];
  const seen = new Set<string>([id]);
  let next = all.get(id)?.supersedes;
  while (next && !seen.has(next)) {
    seen.add(next);
    const m = all.get(next);
    if (!m) break;
    out.push(m);
    next = m.supersedes;
  }
  return out;
}
