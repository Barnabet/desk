import { and, desc, eq, isNull, sql } from 'drizzle-orm';
import { sanitizeLabel } from '@desk/protocol';
import type { Db } from '../db/open';
import { memory } from '../db/schema';
import { listThreads } from '../state/queries';

export type MemoryRow = typeof memory.$inferSelect;

export const DEFAULT_DIGEST_CHARS = 16_000;

export const getMemory = (db: Db, id: string): MemoryRow | undefined => db.select().from(memory).where(eq(memory.id, id)).get();

/** Active (non-superseded) entries, newest first. */
export const activeMemory = (db: Db, projectId: string): MemoryRow[] =>
  db
    .select()
    .from(memory)
    .where(and(eq(memory.project_id, projectId), isNull(memory.superseded_by)))
    .orderBy(desc(memory.created_at), desc(memory.id))
    .all();

/** Full-text search over active entries. Free-form input is reduced to OR-ed quoted terms (FTS syntax-safe). */
export function searchMemory(db: Db, projectId: string, query: string, limit = 20): MemoryRow[] {
  const terms = query.match(/[\p{L}\p{N}_]+/gu);
  if (!terms?.length) return [];
  const match = terms.map((t) => `"${t}"`).join(' OR ');
  return db.all<MemoryRow>(sql`
    SELECT m.* FROM memory_fts f JOIN memory m ON m.id = f.memory_id
    WHERE memory_fts MATCH ${match} AND f.project_id = ${projectId} AND m.superseded_by IS NULL
    ORDER BY rank LIMIT ${limit}`);
}

/** Titles of the project's threads by id, archived ones included: who wrote an `agent:<id>` memory entry. */
export function threadTitles(db: Db, projectId: string): Map<string, string | null> {
  return new Map(listThreads(db, projectId).map((t) => [t.id, t.title]));
}

/**
 * One memory entry on one line, whitespace collapsed. An entry a thread wrote (source `agent:<thread id>`, `threads`
 * mapping thread ids to titles) ends with ` (by thread "<title>")`: it is that thread's claim, not the user's
 * preference (design spec §5.1).
 */
export const formatMemoryLine = (m: MemoryRow, threads: ReadonlyMap<string, string | null> = new Map()): string => {
  const author = m.source.startsWith('agent:') ? m.source.slice('agent:'.length) : undefined;
  const by = author !== undefined && threads.has(author) ? ` (by thread "${sanitizeLabel(threads.get(author) ?? 'untitled')}")` : '';
  return `- [${m.kind}] (${m.id}) ${m.content.replace(/[\s\u0085]+/g, ' ').trim()}${by}`;
};

/** Compact memory for system prompts: all preferences and decisions, then the newest other entries. */
export function memoryDigest(db: Db, projectId: string, maxChars = DEFAULT_DIGEST_CHARS): string {
  const active = activeMemory(db, projectId);
  if (!active.length) return '';
  const pinned = active.filter((m) => m.kind === 'preference' || m.kind === 'decision');
  const rest = active.filter((m) => m.kind !== 'preference' && m.kind !== 'decision');
  const titles = threadTitles(db, projectId);
  const lines: string[] = [];
  let used = 0;
  let omitted = 0;
  for (const m of [...pinned, ...rest]) {
    const line = formatMemoryLine(m, titles);
    if (used + line.length + 1 > maxChars - 80) {
      omitted++;
      continue;
    }
    lines.push(line);
    used += line.length + 1;
  }
  if (omitted) lines.push(`(${omitted} more entries — use memory_search)`);
  return lines.join('\n');
}
