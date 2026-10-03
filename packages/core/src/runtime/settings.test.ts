import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools } from '@desk/fake-model';
import { getAgent, getDeskAgent, getProject } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, noSleep, type Harness } from '../testing/harness';
import { Runtime } from './runtime';

let h: Harness;
afterEach(async () => h?.cleanup());

describe('project settings', () => {
  it('uses the updated Desk model on the next run and leaves existing threads alone', async () => {
    h = await createHarness({ script: () => text('ok') });
    const rt = newRuntime(h);
    const id = rt.createProject({ name: 'P', goal: 'G' });
    const desk = getDeskAgent(h.store.db, id)!;
    const threadId = rt.createThread(id, { title: 'T', brief: 'G', workspacePath: h.files });
    const originalThreadModel = getAgent(h.store.db, threadId)!.model;
    rt.sendToDesk(id, 'Before');
    await rt.whenIdle();

    rt.updateSettings(id, { desk_model: FAKE_MODEL.id, desk_reasoning_effort: 'low', thread_model: FAKE_MODEL.id });
    expect(getDeskAgent(h.store.db, id)?.model).toBe(FAKE_MODEL.id);
    expect(getAgent(h.store.db, threadId)?.model).toBe(originalThreadModel);
    rt.sendToDesk(id, 'After');
    await rt.whenIdle();
    expect(h.fake.requests.map((r) => r.model)).toEqual([desk.model, FAKE_MODEL.id]);
    expect(h.store.list({ agentId: desk.id, types: ['run.started'] }).at(-1)?.payload).toMatchObject({ model: FAKE_MODEL.id, reasoning_effort: 'low' });

    expect(() => rt.updateSettings(id, { desk_model: 'missing-model' })).toThrow();
    expect(getProject(h.store.db, id)?.settings.desk_model).toBe(FAKE_MODEL.id);
    expect(getDeskAgent(h.store.db, id)?.model).toBe(FAKE_MODEL.id);
  });

  it('applies a model change made through update_settings on the next model call of the same run', async () => {
    h = await createHarness({ script: (req) => req.messages.at(-1)?.role === 'tool'
      ? text('Changed.')
      : tools(call('update_settings', { desk_model: FAKE_MODEL.id })) });
    const rt = newRuntime(h);
    const id = rt.createProject({ name: 'P', goal: 'G' });
    const originalModel = getDeskAgent(h.store.db, id)!.model;
    rt.sendToDesk(id, 'Change your model');
    await rt.whenIdle();
    expect(h.fake.requests.map((r) => r.model)).toEqual([originalModel, FAKE_MODEL.id]);
    expect(h.store.list({ projectId: id, types: ['usage'] }).map((e) => e.type === 'usage' && e.payload.model)).toEqual([originalModel, FAKE_MODEL.id]);
  });

  it('stores resolved settings and applies updates', async () => {
    h = await createHarness();
    const rt = new Runtime({ store: h.store, adapter: h.adapter, models: h.models, dataDir: h.dir, retry: noSleep });
    const id = rt.createProject({ name: 'P', goal: 'G', settings: { check_in: 'minimal' } });
    expect(getProject(h.store.db, id)?.settings).toMatchObject({ check_in: 'minimal', max_concurrent_threads: 4 });
    rt.updateProject(id, { goal: 'G2', settings: { max_concurrent_threads: 2 } });
    const p = getProject(h.store.db, id)!;
    expect(p.goal).toBe('G2');
    expect(p.settings).toMatchObject({ check_in: 'minimal', max_concurrent_threads: 2 });
    expect(() => rt.updateProject(id, { settings: { review_rounds: -1 } })).toThrow();
    expect(getProject(h.store.db, id)?.settings.review_rounds).toBe(2);
  });
});
