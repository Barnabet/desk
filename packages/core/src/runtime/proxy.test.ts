import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { startFakeModel, text, type FakeModelServer } from '@desk/fake-model';
import { getAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';

let h: Harness;
let revived: FakeModelServer | undefined;
afterEach(async () => {
  await revived?.close();
  revived = undefined;
  await h?.cleanup();
});

const notices = (projectId: string) =>
  h.store.list({ projectId, types: ['system.notice'] }).map((e) => (e.type === 'system.notice' ? e.payload.code : ''));
const until = async (fn: () => boolean, ms = 5000) => {
  const start = Date.now();
  while (!fn()) {
    if (Date.now() - start > ms) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 20));
  }
};

async function setup() {
  h = await createHarness();
  const rt = newRuntime(h, { proxyProbeIntervalMs: 30 });
  const projectId = rt.createProject({ name: 'P', goal: 'G' });
  const t = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w'), model: FAKE_MODEL.id, parentId: null });
  const port = Number(new URL(h.fake.url).port);
  await h.fake.close();
  return { rt, projectId, t, port };
}

describe('proxy outage', () => {
  it('pauses agents while the proxy is down and resumes when it is back', async () => {
    const { rt, projectId, t, port } = await setup();
    rt.sendMessage(t, 'go');
    await until(() => notices(projectId).includes('proxy_down'));
    await new Promise((r) => setTimeout(r, 150));
    expect(getAgent(h.store.db, t)?.status).toBe('running');
    expect(notices(projectId)).toEqual(['proxy_down']);

    revived = await startFakeModel([text('back online')], { port });
    await rt.whenIdle();
    expect(getAgent(h.store.db, t)?.status).toBe('idle');
    expect(notices(projectId)).toEqual(['proxy_down', 'proxy_up']);
    const last = h.store.list({ agentId: t, types: ['assistant.message'] }).at(-1);
    expect(last?.type === 'assistant.message' && last.payload.content).toBe('back online');
  });

  it('can be stopped during the pause', async () => {
    const { rt, projectId, t } = await setup();
    rt.sendMessage(t, 'go');
    await until(() => notices(projectId).includes('proxy_down'));
    rt.stop(t);
    await rt.whenIdle();
    expect(getAgent(h.store.db, t)?.status).toBe('cancelled');
  });
});
