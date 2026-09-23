import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, error, text, tools, type FakeReply } from '@desk/fake-model';
import { getAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';

let h: Harness;
afterEach(async () => h?.cleanup());

async function setup(fallback: string | null, fallbackReplies: FakeReply[] = []) {
  h = await createHarness({
    script: (req) => (req.model === FAKE_MODEL.id ? error(429, 'rate_limit_error', 'This request would exceed your account rate limit') : (fallbackReplies.shift() ?? text('(none)'))),
  });
  const rt = newRuntime(h, { retry: { sleep: async () => {}, random: () => 0, maxAttempts: 3 } });
  const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { fallback_model: fallback } });
  const t = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w'), model: FAKE_MODEL.id, parentId: null });
  return { rt, t };
}

describe('fallback model', () => {
  it('switches the rest of the run to the fallback after the retry budget', async () => {
    const { rt, t } = await setup('gpt-6-sol', [tools(call('list_dir', {})), text('finished on fallback')]);
    rt.sendMessage(t, 'go');
    await rt.whenIdle();
    expect(h.fake.requests.map((r) => r.model)).toEqual([FAKE_MODEL.id, FAKE_MODEL.id, FAKE_MODEL.id, 'gpt-6-sol', 'gpt-6-sol']);
    const switched = h.store.list({ agentId: t, types: ['agent.model_switched'] });
    expect(switched).toHaveLength(1);
    expect(switched[0]?.type === 'agent.model_switched' && switched[0].payload).toMatchObject({ from: FAKE_MODEL.id, to: 'gpt-6-sol', scope: 'run' });
    expect(getAgent(h.store.db, t)).toMatchObject({ status: 'idle', model: FAKE_MODEL.id });
    const usage = h.store.list({ agentId: t, types: ['usage'] }).map((e) => (e.type === 'usage' ? e.payload.model : ''));
    expect(usage).toEqual(['gpt-6-sol', 'gpt-6-sol']);
  });

  it('fails with the rate-limit reason when no fallback is configured', async () => {
    const { rt, t } = await setup(null);
    rt.sendMessage(t, 'go');
    await rt.whenIdle();
    expect(getAgent(h.store.db, t)?.status).toBe('failed');
    const fin = h.store.list({ agentId: t, types: ['run.finished'] }).at(-1);
    expect(fin?.type === 'run.finished' && fin.payload.detail).toMatch(/rate limit/i);
  });

  it('does not switch to the same model', async () => {
    const { rt, t } = await setup(FAKE_MODEL.id);
    rt.sendMessage(t, 'go');
    await rt.whenIdle();
    expect(h.store.list({ agentId: t, types: ['agent.model_switched'] })).toHaveLength(0);
    expect(getAgent(h.store.db, t)?.status).toBe('failed');
  });
});
