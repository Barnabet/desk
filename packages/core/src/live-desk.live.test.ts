import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  createModelAdapter,
  EventStore,
  getDeskAgent,
  listArtifacts,
  listThreads,
  loadModelConfig,
  ModelRegistry,
  openDb,
  renderTranscript,
  Runtime,
} from './index';

describe('live: Desk coordination on claude-opus-5-5', () => {
  it('dispatches threads, collects their artifacts and reports', async () => {
    const dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-live-desk-')));
    const { db, close } = openDb(':memory:');
    const store = new EventStore(db);
    const models = new ModelRegistry();
    const rt = new Runtime({ store, adapter: createModelAdapter(loadModelConfig(), models), models, dataDir: dir, retry: { maxAttempts: 3 } });
    try {
      const projectId = rt.createProject({
        name: 'Live Desk smoke',
        goal: 'Produce two small documents in the project library.',
        settings: { review_rounds: 1 },
      });
      rt.sendToDesk(
        projectId,
        'Please get two things done in parallel, one thread each: (1) haiku.md — a haiku about coordination; (2) sqlite.md — exactly three one-line facts about SQLite. Each thread must publish its file to the library. When both are done, send me a short report.',
      );
      await rt.whenIdle();

      const threads = listThreads(db, projectId);
      const artifacts = listArtifacts(db, projectId).map((a) => a.path);
      const desk = getDeskAgent(db, projectId)!;
      if (process.env.DESK_LIVE_VERBOSE || threads.some((t) => t.status !== 'done') || !artifacts.some((a) => a.includes('haiku'))) {
        console.log(renderTranscript(store.list({ agentId: desk.id })));
      }
      expect(threads.length).toBeGreaterThanOrEqual(2);
      expect(threads.every((t) => t.status === 'done')).toBe(true);
      expect(artifacts.some((a) => a.includes('haiku'))).toBe(true);
      expect(artifacts.some((a) => a.includes('sqlite'))).toBe(true);
      expect(store.list({ projectId, types: ['report'] }).length).toBeGreaterThanOrEqual(1);
      expect(['idle', 'waiting']).toContain(desk.status);
    } finally {
      close();
      await rm(dir, { recursive: true, force: true });
    }
  });
});
