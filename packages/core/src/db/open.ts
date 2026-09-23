import { fileURLToPath } from 'node:url';
import Database from 'better-sqlite3';
import { drizzle, type BetterSQLite3Database } from 'drizzle-orm/better-sqlite3';
import { migrate } from 'drizzle-orm/better-sqlite3/migrator';
import * as schema from './schema';

export type Db = BetterSQLite3Database<typeof schema>;
export type Tx = Parameters<Parameters<Db['transaction']>[0]>[0];

const MIGRATIONS = fileURLToPath(new URL('../../drizzle', import.meta.url));

export function openDb(file = ':memory:'): { db: Db; close(): void } {
  const sqlite = new Database(file);
  sqlite.pragma('journal_mode = WAL');
  sqlite.pragma('busy_timeout = 5000');
  const db = drizzle(sqlite, { schema });
  migrate(db, { migrationsFolder: MIGRATIONS });
  // FTS5 index over active memory; drizzle has no virtual-table support, so it is managed here and in projections.
  sqlite.exec(
    `CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(content, memory_id UNINDEXED, project_id UNINDEXED, tokenize = 'porter unicode61')`,
  );
  return { db, close: () => sqlite.close() };
}
