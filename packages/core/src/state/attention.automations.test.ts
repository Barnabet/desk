// packages/core/src/state/attention.automations.test.ts
import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type ChatRequest } from '@desk/fake-model';
import { ConflictError } from '../errors';
import { automationHarness, askDef } from '../testing/automations';
import type { Harness } from '../testing/harness';
import type { Runtime } from '../runtime/runtime';
import { listAttention } from './attention';

let h: Harness;
let rt: Runtime;
let projectId: string;
afterEach(async () => h?.cleanup());

const system = (req: ChatRequest) => String(req.messages[0]?.content ?? '');
const items = (kind?: string) => listAttention(h.store.db, { projectId }).filter((i) => !kind || i.kind === kind);
const create = (name: string, def: object) => rt.automations.create(projectId, name, def, { origin: 'user', via: 'editor' }).automation.id;
const start = (id: string, test = false) => rt.engine.startRun(id, { trigger: test ? 'test' : 'manual', test, inputs: {}, by: 'user' });

describe('automation attention', () => {
  it("lists questions until answered, and never lets them be dismissed", async () => {
    ({ h, rt, projectId } = await automationHarness({ script: () => text('ok') }));
    const id = create('ask', askDef({ title: 'Publish digest' }));
    const r = await start(id);
    const [item] = items('automation_ask');
    expect(item).toMatchObject({ id: `automation_ask:${r}:ask`, title: 'Publish digest: Publish?', ref: { automation_id: id, run_id: r, step_id: 'ask' } });
    expect(() => rt.dismissAttention(item!.id)).toThrow(ConflictError);
    await rt.engine.answer(r, 'ask', { decision: 'approve' });
    expect(items('automation_ask')).toEqual([]);
  });

  it('names the automation on a step agent approval', async () => {
    ({ h, rt, projectId } = await automationHarness({ script: (req) => (system(req).includes('You are one step') ? tools(call('bash', { command: 'echo hi' })) : text('ok')) }));
    const id = create('echo', { title: 'Echo', steps: [{ id: 'say', title: 'Say hi', kind: 'agent', brief: 'Say hi.' }] });
    const r = await start(id);
    await rt.whenIdle();
    const [item] = items('approval');
    expect(item).toMatchObject({ title: 'Echo · Say hi wants to run bash', ref: { automation_id: id, run_id: r, step_id: 'say' } });
    expect(item!.ref.approval_id).toBeTruthy();
  });

  it('shows the latest failure until a later run does not fail, or until dismissed; never for tests', async () => {
    ({ h, rt, projectId } = await automationHarness({ script: () => text('ok') }));
    rt.saveSkill({ scope: 'project', projectId, name: 'x', description: 'test skill', instructions: 'Run', files: [{ path: 'scripts/x.py', content: 'print(1)' }] }, { projectId });
    let fail = true;
    rt.engine.register('script', ({ run, step }) => rt.engine.resolveStep(run.id, step.id, fail ? { status: 'failed', error: 'boom' } : { status: 'succeeded', route: null, outputs: {}, summary: 'ok' }));
    const id = create('fetch', { title: 'Fetch', steps: [{ id: 's', title: 'S', kind: 'script', skill: 'x', script: 'x.py' }] });
    await start(id, true);
    expect(items('automation_failed')).toEqual([]);
    const r = await start(id);
    expect(items('automation_failed')).toEqual([expect.objectContaining({ id: `automation_failed:${r}`, title: 'Fetch failed', detail: 's failed: boom', ref: { automation_id: id, run_id: r } })]);
    rt.dismissAttention(`automation_failed:${r}`);
    expect(items('automation_failed')).toEqual([]);
    const r2 = await start(id);
    expect(items('automation_failed').map((i) => i.id)).toEqual([`automation_failed:${r2}`]);
    fail = false;
    await start(id);
    expect(items('automation_failed')).toEqual([]);
  });

  it("shows Desk's turn-on request and suspended grants", async () => {
    ({ h, rt, projectId } = await automationHarness({ script: () => text('ok') }));
    const daily = { kind: 'schedule', cron: '0 8 * * *', timezone: 'Europe/Paris', catch_up: 'once' };
    const id = create('digest', askDef({ title: 'Digest', triggers: [daily] }));
    rt.automations.requestEnable(id, 'desk-agent', 'Tested it');
    const [req] = items('automation_enable_request');
    expect(req).toMatchObject({ title: 'Desk proposes turning on Digest', detail: '0 8 * * * (Europe/Paris); 0 proposed grant(s). Tested it', ref: { automation_id: id } });
    expect(req!.id).toMatch(new RegExp(`^automation_enable:${id}:`));
    rt.automations.setEnabled(id, true, 'user');
    expect(items('automation_enable_request')).toEqual([]);

    rt.automations.setGrants(id, [{ tool: 'web_fetch', match: { domain: 'example.com' }, action: 'allow' }], 'edited');
    rt.automations.save(id, askDef({ title: 'Digest 2', triggers: [daily] }), { origin: 'agent:desk', via: 'tool' });
    expect(items('automation_grants_suspended')).toEqual([
      expect.objectContaining({ id: `automation_grants:${id}:2`, title: 'Digest 2 changed: its grants are suspended', detail: 'Desk saved v2. Review the change and keep the grants, or its runs will ask again.' }),
    ]);
    rt.automations.keepGrants(id);
    expect(items('automation_grants_suspended')).toEqual([]);
  });
});
