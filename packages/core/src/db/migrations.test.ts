import { cp, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, it } from 'vitest';
import { EventStore } from '../events/store';
import { getAgent, getProject } from '../state/queries';
import { openDb } from './open';

it('repairs previously changed Desk models when an existing database is opened', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'desk-migration-'));
  let opened: ReturnType<typeof openDb> | undefined;
  try {
    const legacy = join(dir, 'migrations');
    await cp(fileURLToPath(new URL('../../drizzle', import.meta.url)), legacy, { recursive: true });
    const journalPath = join(legacy, 'meta', '_journal.json');
    const journal = JSON.parse(await readFile(journalPath, 'utf8'));
    journal.entries = journal.entries.filter((e: { idx: number }) => e.idx < 7);
    await writeFile(journalPath, JSON.stringify(journal));
    const file = join(dir, 'desk.db');
    opened = openDb(file, { migrationsFolder: legacy });
    const store = new EventStore(opened.db);
    store.append({ project_id: 'p', agent_id: null, type: 'project.created', payload: { name: 'P', goal: 'G', instructions: '', settings: { desk_model: 'new-model' } } });
    // The pre-fix state: settings contain the new model, but the agent still has its creation model.
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
    await rm(dir, { recursive: true, force: true });
  }
});
