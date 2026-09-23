import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { hang, text } from '@desk/fake-model';
import { getAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, noSleep, type Harness } from '../testing/harness';
import { Runtime } from './runtime';

let h: Harness;
afterEach(async () => h?.cleanup());

const makeRuntime = (extra: { maxConcurrentThreads?: number } = {}) =>
  new Runtime({ store: h.store, adapter: h.adapter, models: h.models, dataDir: h.dir, retry: noSleep, ...extra });

const statuses = (agentId: string) =>
  h.store.list({ agentId, types: ['agent.status_changed'] }).map((e) => (e.type === 'agent.status_changed' ? e.payload.status : ''));

describe('Runtime', () => {
  it('runs a thread when messaged', async () => {
    h = await createHarness({ script: [text('hi back')] });
    const rt = makeRuntime();
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const agentId = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w1'), model: FAKE_MODEL.id, parentId: null });
    rt.sendMessage(agentId, 'hello');
    await rt.whenIdle();
    expect(statuses(agentId)).toEqual(['queued', 'running', 'idle']);
    expect(getAgent(h.store.db, agentId)?.status).toBe('idle');
  });

  it('re-runs when a message arrives after the last step', async () => {
    h = await createHarness({ script: [text('first', { delayMs: 100 }), text('second')] });
    const rt = makeRuntime();
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const agentId = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w1'), model: FAKE_MODEL.id, parentId: null });
    rt.sendMessage(agentId, 'one');
    await new Promise((r) => setTimeout(r, 30));
    rt.sendMessage(agentId, 'two');
    await rt.whenIdle();
    expect(h.fake.requests).toHaveLength(2);
    expect(h.fake.requests[1]!.messages.at(-1)).toEqual({ role: 'user', content: 'two' });
    expect(h.store.list({ agentId, types: ['run.started'] })).toHaveLength(2);
  });

  it('enforces model concurrency across threads', async () => {
    h = await createHarness({ script: () => text('ok', { delayMs: 50 }), concurrency: 1 });
    const rt = makeRuntime();
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const ids = [1, 2, 3].map((n) => rt.createThread(projectId, { title: `T${n}`, brief: 'B', workspacePath: join(h.dir, `w${n}`), model: FAKE_MODEL.id, parentId: null }));
    ids.forEach((id) => rt.sendMessage(id, 'go'));
    await rt.whenIdle();
    expect(h.fake.requests).toHaveLength(3);
    expect(h.fake.maxInFlight).toBe(1);
  });

  it('enforces the per-project thread cap', async () => {
    h = await createHarness({ script: () => text('ok', { delayMs: 50 }) });
    const rt = makeRuntime({ maxConcurrentThreads: 1 });
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const ids = [1, 2].map((n) => rt.createThread(projectId, { title: `T${n}`, brief: 'B', workspacePath: join(h.dir, `w${n}`), model: FAKE_MODEL.id, parentId: null }));
    ids.forEach((id) => rt.sendMessage(id, 'go'));
    await rt.whenIdle();
    expect(h.fake.maxInFlight).toBe(1);
  });

  it('stops running and queued threads', async () => {
    h = await createHarness({ script: () => hang(), concurrency: 1 });
    const rt = makeRuntime();
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const a = rt.createThread(projectId, { title: 'A', brief: 'B', workspacePath: join(h.dir, 'wa'), model: FAKE_MODEL.id, parentId: null });
    const b = rt.createThread(projectId, { title: 'B', brief: 'B', workspacePath: join(h.dir, 'wb'), model: FAKE_MODEL.id, parentId: null });
    rt.sendMessage(a, 'go');
    rt.sendMessage(b, 'go');
    await new Promise((r) => setTimeout(r, 50));
    rt.stop(b);
    rt.stop(a);
    await rt.whenIdle();
    expect(getAgent(h.store.db, a)?.status).toBe('cancelled');
    expect(getAgent(h.store.db, b)?.status).toBe('cancelled');
    expect(h.store.list({ agentId: b, types: ['run.started'] })).toHaveLength(0);
  });

  it('rejects unknown models', async () => {
    h = await createHarness();
    const rt = makeRuntime();
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    expect(() => rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w'), model: 'nope' })).toThrow(/Unknown model/);
  });
});
