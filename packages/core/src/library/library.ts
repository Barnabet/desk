import { existsSync } from 'node:fs';
import { asc, eq } from 'drizzle-orm';
import { basename, extname, join } from 'node:path';
import type { Db } from '../db/open';
import { artifacts } from '../db/schema';

export type ArtifactRow = typeof artifacts.$inferSelect;

export const listArtifacts = (db: Db, projectId: string): ArtifactRow[] =>
  db.select().from(artifacts).where(eq(artifacts.project_id, projectId)).orderBy(asc(artifacts.created_at), asc(artifacts.id)).all();

/** A safe single-segment file name. */
export function sanitizeLibraryName(name: string): string {
  const cleaned = basename(name).replace(/[^\w.\- ]+/g, '_').trim();
  return cleaned && cleaned !== '.' && cleaned !== '..' ? cleaned : 'file';
}

/** `name`, or `name-2`, `name-3`… (before the extension) if taken. */
export function uniqueLibraryName(dir: string, name: string): string {
  const safe = sanitizeLibraryName(name);
  if (!existsSync(join(dir, safe))) return safe;
  const ext = extname(safe);
  const stem = safe.slice(0, safe.length - ext.length);
  for (let i = 2; ; i++) {
    const candidate = `${stem}-${i}${ext}`;
    if (!existsSync(join(dir, candidate))) return candidate;
  }
}

/** One line per library item (system prompts, library_list). Titles and descriptions are agents' words: kept on one line. */
export const formatArtifactLine = (a: ArtifactRow): string => {
  const flat = (s: string) => s.replace(/[\s\u0085]+/g, ' ').trim();
  return `- ${a.path} — ${flat(a.title)} [${a.kind}] (${a.origin})${a.description ? `: ${flat(a.description)}` : ''}`;
};
