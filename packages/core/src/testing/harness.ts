import { mkdir, mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { ModelInfo } from '@desk/protocol';
import { startFakeModel, type FakeModelServer, type FakeReply, type Script } from '@desk/fake-model';
import { openDb } from '../db/open';
import { EventStore } from '../events/store';
import { newId } from '../ids';
import { createModelAdapter } from '../model/adapter';
import { ModelRegistry, SEED_MODELS } from '../model/registry';
import type { RetryOptions } from '../model/retry';
import type { ModelAdapter } from '../model/types';
import { Runtime, type RuntimeOptions } from '../runtime/runtime';

export const FAKE_MODEL: ModelInfo = {
  id: 'fake-model',
  family: 'claude',
  context_window: 100_000,
  max_output_tokens: 4096,
  supports_reasoning_effort: false,
  concurrency: 4,
};

export const noSleep: RetryOptions = { sleep: async () => {}, random: () => 0 };

export type Harness = {
  fake: FakeModelServer;
  dir: string;
  store: EventStore;
  models: ModelRegistry;
  adapter: ModelAdapter;
  cleanup(): Promise<void>;
};

export async function createHarness(opts: { script?: Script | FakeReply[]; concurrency?: number } = {}): Promise<Harness> {
  const fake = await startFakeModel(opts.script ?? []);
  const dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-test-')));
  const { db, close } = openDb(':memory:');
  const store = new EventStore(db);
  const models = new ModelRegistry([...SEED_MODELS, { ...FAKE_MODEL, concurrency: opts.concurrency ?? FAKE_MODEL.concurrency }]);
  const adapter = createModelAdapter({ baseURL: fake.url, apiKey: 'test' }, models);
  return {
    fake,
    dir,
    store,
    models,
    adapter,
    async cleanup() {
      await fake.close();
      close();
      await rm(dir, { recursive: true, force: true });
    },
  };
}

export async function seedThread(
  store: EventStore,
  dir: string,
  opts: { model?: string; brief?: string; projectId?: string } = {},
): Promise<{ projectId: string; agentId: string; workspace: string }> {
  const projectId = opts.projectId ?? newId();
  const agentId = newId();
  const workspace = join(dir, 'workspaces', agentId);
  await mkdir(workspace, { recursive: true });
  if (!opts.projectId) {
    store.append({ project_id: projectId, agent_id: null, type: 'project.created', payload: { name: 'Test project', goal: 'Test goal', instructions: '' } });
  }
  store.append({
    project_id: projectId,
    agent_id: agentId,
    type: 'agent.created',
    payload: { role: 'thread', model: opts.model ?? FAKE_MODEL.id, title: 'Test thread', brief: opts.brief ?? 'Do the test task', workspace_path: workspace, parent_id: null },
  });
  return { projectId, agentId, workspace };
}

/** A Runtime wired to the harness (fake model, temp data dir, no backoff sleeps, sandbox off). */
export function newRuntime(h: Harness, extra: Partial<RuntimeOptions> = {}): Runtime {
  return new Runtime({ store: h.store, adapter: h.adapter, models: h.models, dataDir: h.dir, retry: noSleep, sandboxAvailable: false, ...extra });
}
