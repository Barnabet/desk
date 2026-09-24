import { afterEach, describe, expect, it } from 'vitest';
import { error, text, type Script } from '@desk/fake-model';
import type { ModelInfo } from '@desk/protocol';
import { buildToolContext } from './context';
import { effortFor } from '../model/registry';
import { getDeskAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';
import { spawnThreadTool, updateSettingsTool } from '../tools/desk';
import type { ToolOutput } from '../tools/types';

let h: Harness;
afterEach(async () => h?.cleanup());

const model = (over: Partial<ModelInfo> = {}): ModelInfo => ({ ...FAKE_MODEL, reasoning_efforts: ['low', 'medium', 'high'], default_reasoning_effort: null, ...over });

describe('effortFor', () => {
  it('sends the wanted level when the model takes it, else its default, else nothing', () => {
    expect(effortFor(model(), 'high')).toBe('high');
    expect(effortFor(model(), 'max')).toBeUndefined();
    expect(effortFor(model({ default_reasoning_effort: 'medium' }), 'max')).toBe('medium');
    expect(effortFor(model({ default_reasoning_effort: 'medium' }), null)).toBe('medium');
    expect(effortFor(model({ reasoning_efforts: [] }), 'high')).toBeUndefined();
    expect(effortFor(undefined, 'high')).toBeUndefined();
  });
});

describe('reasoning effort in runs', () => {
  /** The thinker model takes low/medium/high; a second model takes only max (for the fallback case). */
  async function setup(script: Script, settings: Record<string, unknown> = {}) {
    h = await createHarness({ script });
    h.models.upsert(model({ id: 'max-only', reasoning_efforts: ['max'], default_reasoning_effort: 'max' }));
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id, ...settings } });
    const desk = getDeskAgent(h.store.db, projectId)!;
    const ctx = buildToolContext(desk, 'run', 'tc', new AbortController().signal, { sandboxEnabled: false, jobs: rt.jobs, services: rt.services });
    return { rt, projectId, desk, ctx };
  }
  const textOf = (o: ToolOutput) => (typeof o === 'string' ? o : o.content);
  const effortsSent = () => h.fake.requests.map((r) => (r as { reasoning_effort?: string }).reasoning_effort ?? null);

  it("sends Desk's setting on Desk's calls, a thread's own level on the thread's, and records it on run.started", async () => {
    const { rt, projectId, ctx } = await setup(() => text('ok'), { desk_reasoning_effort: 'high', thread_reasoning_effort: 'low' });
    rt.sendToDesk(projectId, 'hello');
    await rt.whenIdle();
    expect(effortsSent()).toEqual(['high']);

    const hard = /thread ([0-9A-Z]{26})/.exec(textOf(await spawnThreadTool.execute({ title: 'Hard one', brief: 'Think', reasoning_effort: 'medium' }, ctx)))![1]!;
    const easy = /thread ([0-9A-Z]{26})/.exec(textOf(await spawnThreadTool.execute({ title: 'Easy one', brief: 'Look up' }, ctx)))![1]!;
    await rt.whenIdle();
    // Per agent, from run.started (Desk also runs again when the threads report back).
    const byAgent = new Map<string, Set<unknown>>();
    for (const e of h.store.list({ projectId, types: ['run.started'] })) {
      if (e.type !== 'run.started') continue;
      byAgent.set(e.agent_id!, (byAgent.get(e.agent_id!) ?? new Set()).add(e.payload.reasoning_effort));
    }
    expect(byAgent.get(ctx.agentId)).toEqual(new Set(['high']));
    expect(byAgent.get(hard)).toEqual(new Set(['medium']));
    expect(byAgent.get(easy)).toEqual(new Set(['low']));
    expect(new Set(effortsSent())).toEqual(new Set(['high', 'medium', 'low']));
  });

  it('sends nothing without a setting or model default, and follows a settings change on the next run', async () => {
    const { rt, projectId, ctx } = await setup(() => text('ok'));
    rt.sendToDesk(projectId, 'one');
    await rt.whenIdle();
    await updateSettingsTool.execute({ desk_reasoning_effort: 'low' }, ctx);
    rt.sendToDesk(projectId, 'two');
    await rt.whenIdle();
    expect(effortsSent()).toEqual([null, 'low']);
  });

  it("re-checks the level against the fallback model after a rate limit (its default replaces a level it doesn't take)", async () => {
    const { rt, projectId } = await setup((req) => (req.model === FAKE_MODEL.id ? error(429, 'rate_limit_error', 'slow down') : text('ok')), {
      desk_reasoning_effort: 'high',
      fallback_model: 'max-only',
    });
    rt.sendToDesk(projectId, 'go');
    await rt.whenIdle();
    const sent = h.fake.requests.map((r) => [r.model, (r as { reasoning_effort?: string }).reasoning_effort]);
    expect(sent.at(0)).toEqual([FAKE_MODEL.id, 'high']);
    expect(sent.at(-1)).toEqual(['max-only', 'max']);
  });

  it('refuses levels the model does not take, in spawn_thread and in settings', async () => {
    const { rt, projectId, ctx } = await setup(() => text('ok'));
    await expect(spawnThreadTool.execute({ title: 'T', brief: 'b', reasoning_effort: 'max' }, ctx)).rejects.toThrow(/fake-model does not take reasoning effort "max" \(it takes: low, medium, high\)/);
    expect(() => rt.updateSettings(projectId, { thread_reasoning_effort: 'xhigh' })).toThrow(/does not take reasoning effort "xhigh"/);
    expect(() => rt.updateSettings(projectId, { thread_model: 'max-only', thread_reasoning_effort: 'max' })).not.toThrow();
  });
});
