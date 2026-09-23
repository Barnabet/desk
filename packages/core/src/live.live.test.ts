import { mkdtemp, readFile, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { createModelAdapter, EventStore, getAgent, loadModelConfig, ModelRegistry, openDb, Runtime } from './index';

const MODELS = ['claude-opus-5-5', 'gpt-6-astra', 'gpt-6-sol', 'claude-fable-5-1'];

describe.each(MODELS)('live: %s', (model) => {
  it('completes a small file task end to end', async (ctx) => {
    const dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-live-')));
    const { db, close } = openDb(':memory:');
    const store = new EventStore(db);
    const models = new ModelRegistry();
    const runtime = new Runtime({ store, adapter: createModelAdapter(loadModelConfig(), models), models, dataDir: dir, retry: { maxAttempts: 3 } });
    try {
      const projectId = runtime.createProject({ name: 'Live smoke', goal: 'Verify the Desk runtime end to end' });
      const workspace = join(dir, 'ws');
      const expected = `hello from ${model}`;
      const agentId = runtime.createThread(projectId, {
        title: 'Hello file',
        brief: `Create a file named hello.txt in your workspace containing exactly this text and nothing else: ${expected}\nThen read it back with read_file to verify, then call complete.`,
        workspacePath: workspace,
        model,
      });
      runtime.sendMessage(agentId, 'Start now.');
      await runtime.whenIdle();

      const finished = store.list({ agentId, types: ['run.finished'] }).at(-1);
      if (finished?.type === 'run.finished' && finished.payload.reason === 'error' && /rate limit|429/i.test(finished.payload.detail ?? '')) {
        ctx.skip();
      }

      expect(getAgent(db, agentId)?.status).toBe('done');
      expect((await readFile(join(workspace, 'hello.txt'), 'utf8')).trim()).toBe(expected);
      const toolNames = store.list({ agentId, types: ['tool.call'] }).map((e) => (e.type === 'tool.call' ? e.payload.name : ''));
      expect(toolNames).toContain('read_file');
      expect(toolNames.at(-1)).toBe('complete');
      const usage = store.list({ agentId, types: ['usage'] });
      expect(usage.length).toBeGreaterThan(1);
      expect(usage.every((u) => u.type === 'usage' && !u.payload.estimated && u.payload.prompt_tokens > 0)).toBe(true);
    } finally {
      close();
      await rm(dir, { recursive: true, force: true });
    }
  });
});
