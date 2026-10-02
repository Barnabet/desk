import { cp, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import Database from 'better-sqlite3';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { EventStore } from '../events/store';
import { getAgent, getProject } from '../state/queries';
import { openDb } from './open';

type JournalEntry = { idx: number; when: number; tag: string };

let dir: string;
beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), 'desk-migration-'));
});
afterEach(async () => {
  await rm(dir, { recursive: true, force: true });
});

/** A copy of the migrations folder whose journal `edit` rewrites, standing for what an older deskd shipped. */
async function legacyMigrations(edit: (entries: JournalEntry[]) => JournalEntry[]): Promise<string> {
  const legacy = join(dir, 'migrations');
  await cp(fileURLToPath(new URL('../../drizzle', import.meta.url)), legacy, { recursive: true });
  const journalPath = join(legacy, 'meta', '_journal.json');
  const journal = JSON.parse(await readFile(journalPath, 'utf8'));
  journal.entries = edit(journal.entries);
  await writeFile(journalPath, JSON.stringify(journal));
  return legacy;
}

/** The pre-fix state, written raw so it works on any schema: settings hold the new model, the agents their creation model. */
function seedStaleDeskModel(file: string): void {
  const sqlite = new Database(file);
  const now = new Date(0).toISOString();
  sqlite.prepare('INSERT INTO projects (id, name, goal, instructions, settings, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)')
    .run('p', 'P', 'G', '', JSON.stringify({ desk_model: 'new-model' }), now, now);
  for (const role of ['desk', 'thread']) {
    sqlite.prepare('INSERT INTO agents (id, project_id, role, model, title, status, workspace_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)')
      .run(role, 'p', role, 'old-model', role, 'idle', dir, now, now);
  }
  sqlite.close();
}

it('repairs previously changed Desk models when an existing database is opened', async () => {
  const legacy = await legacyMigrations((entries) => entries.filter((e) => e.tag !== '0008_desk_model'));
  const file = join(dir, 'desk.db');
  let opened: ReturnType<typeof openDb> | undefined = openDb(file, { migrationsFolder: legacy });
  try {
    const store = new EventStore(opened.db);
    store.append({ project_id: 'p', agent_id: null, type: 'project.created', payload: { name: 'P', goal: 'G', instructions: '', settings: { desk_model: 'new-model' } } });
    for (const role of ['desk', 'thread'] as const) {
      store.append({ project_id: 'p', agent_id: role, type: 'agent.created', payload: { role, model: 'old-model', title: role, brief: null, workspace_path: dir, parent_id: null } });
    }
    opened.close();
    opened = undefined;
    opened = openDb(file);
    expect(getAgent(opened.db, 'desk')?.model).toBe('new-model');
    expect(getAgent(opened.db, 'thread')?.model).toBe('old-model');
    expect(getProject(opened.db, 'p')?.settings.desk_model).toBe('new-model');
    expect(new EventStore(opened.db).list()).toHaveLength(3);
  } finally {
    opened?.close();
  }
});

it('still applies 0007_reviews to a database that ran the desk-model fix under its first number', async () => {
  // Some Macs ran a build where the fix was 0007_desk_model, dated before 0007_reviews and with no reviews migration.
  const legacy = await legacyMigrations((entries) => [
    ...entries.filter((e) => e.idx < 7),
    { ...entries.find((e) => e.tag === '0008_desk_model')!, idx: 7, when: 1790812800000, tag: '0007_desk_model' },
  ]);
  await rename(join(legacy, '0008_desk_model.sql'), join(legacy, '0007_desk_model.sql'));
  const file = join(dir, 'desk.db');
  openDb(file, { migrationsFolder: legacy }).close();
  seedStaleDeskModel(file);

  const opened = openDb(file);
  try {
    expect(getAgent(opened.db, 'desk')).toMatchObject({ model: 'new-model', acceptance: 'none' });
    expect(getAgent(opened.db, 'thread')?.model).toBe('old-model');
    const sqlite = new Database(file, { readonly: true });
    const tables = sqlite.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").pluck().all();
    sqlite.close();
    expect(tables).toEqual(expect.arrayContaining(['reviews', 'submissions', 'findings']));
  } finally {
    opened.close();
  }
});
