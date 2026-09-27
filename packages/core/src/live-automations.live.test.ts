import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { createModelAdapter, EventStore, findAutomation, getDeskAgent, listRuns, loadModelConfig, ModelRegistry, openDb, renderTranscript, Runtime } from './index';

describe('live: Desk builds and tests an automation', () => {
  it('saves a two-step automation, tests it and proposes turning it on', async () => {
    const dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-live-auto-')));
    const { db, close } = openDb(':memory:');
    const store = new EventStore(db);
    const models = new ModelRegistry();
    const rt = new Runtime({ store, adapter: createModelAdapter(loadModelConfig(), models), models, dataDir: dir, retry: { maxAttempts: 3 } });
    try {
      const projectId = rt.createProject({ name: 'Live automations', goal: 'Automate small recurring writing tasks.' });
      rt.sendToDesk(
        projectId,
        'Build an automation named "haiku" with no schedule (Run now only) and two steps: an agent step that writes a haiku about {{inputs.topic}} (an input, default "autumn") and returns it as the output "haiku", then a Tell Desk step that sends you the haiku. No scripts are needed. Save it, test it with the topic "rain", read the report, and ask me to turn it on.',
      );
      await rt.whenIdle();
      await rt.engine.settled();
      await rt.whenIdle();
      const a = findAutomation(db, projectId, 'haiku');
      const desk = getDeskAgent(db, projectId)!;
      if (process.env.DESK_LIVE_VERBOSE || !a?.enable_request) console.log(renderTranscript(store.list({ agentId: desk.id })));
      expect(a).toBeTruthy();
      const runs = listRuns(db, a!.id);
      expect(runs.some((r) => r.test && r.status === 'succeeded')).toBe(true);
      expect(a!.enable_request).not.toBeNull();
      expect(a!.enabled).toBe(false);
    } finally {
      await rt.shutdown();
      close();
      await rm(dir, { recursive: true, force: true });
    }
  });
});
