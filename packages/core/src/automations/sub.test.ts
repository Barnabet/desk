import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type ChatRequest } from '@desk/fake-model';
import type { AgentRow } from '../state/queries';
import { automationHarness, type FakeClock } from '../testing/automations';
import type { Harness } from '../testing/harness';
import type { Runtime } from '../runtime/runtime';
import { runDir, stepDir } from './folders';
import { childRuns, getRun, getStepRun } from './queries';
import { runDetail, waitingOn } from './views';

let h: Harness;
let rt: Runtime;
let projectId: string;
let desk: AgentRow;
let clock: FakeClock;
afterEach(async () => h?.cleanup());

const system = (req: ChatRequest) => String(req.messages[0]?.content ?? '');

async function setup(script?: (req: ChatRequest) => ReturnType<typeof text>) {
  ({ h, rt, projectId, desk, clock } = await automationHarness({ script: script ?? (() => text('ok')) }));
  rt.saveSkill({ scope: 'project', projectId, name: 'x', description: 'test skill', instructions: 'Run x.py', files: [{ path: 'scripts/x.py', content: 'print(1)' }] }, { projectId });
  // Script steps in these tests: `make` writes out.txt and answers from its args; `boom` fails.
  rt.engine.register('script', ({ run, step }) => {
    if (step.kind !== 'script') return;
    if (step.script === 'boom.py') return rt.engine.resolveStep(run.id, step.id, { status: 'failed', error: 'boom' });
    writeFileSync(join(stepDir(h.dir, run.id, step.id), 'out.txt'), 'hello from the child');
    rt.engine.resolveStep(run.id, step.id, { status: 'succeeded', route: null, outputs: { headline: `${String(run.inputs.topic)}!` }, summary: `made ${String(run.inputs.topic)}` });
  });
}
const create = (name: string, def: object) => rt.automations.create(projectId, name, def, { origin: 'user', via: 'editor' }).automation.id;
/** Starts a run and waits for the executor work it set off (child runs start asynchronously). */
const start = async (id: string, inputs: object = {}, test = false) => {
  const runId = await rt.engine.startRun(id, { trigger: test ? 'test' : 'manual', test, inputs: inputs as never, by: 'user' });
  await rt.engine.settled();
  return runId;
};
const deskTexts = () => h.store.list({ agentId: desk.id, types: ['message.agent'] }).map((e) => (e.type === 'message.agent' ? e.payload.text : ''));

const childDef = (extra: object = {}) => ({
  title: 'Child',
  inputs: [
    { key: 'topic', label: 'Topic', type: 'text', required: true },
    { key: 'tone', label: 'Tone', type: 'text', default: 'calm' },
  ],
  steps: [{ id: 'make', title: 'Make', kind: 'script', skill: 'x', script: 'x.py' }],
  output_step: 'make',
  ...extra,
});
const parentDef = (sub: object = {}, extra: object = {}) => ({
  title: 'Parent',
  inputs: [{ key: 'topic', label: 'Topic', type: 'text' }],
  steps: [
    { id: 'sub', title: 'Run child', kind: 'automation', automation: 'child', inputs: { topic: '{{inputs.topic}} news', tone: '' }, ...sub },
    { id: 'tell', title: 'Tell', kind: 'tell_desk', text: '{{steps.sub.outputs.headline}} | {{steps.sub.dir}} | {{steps.sub.outputs.run_id}}', attach: ['*.txt'] },
  ],
  edges: [{ from: 'sub', to: 'tell' }],
  ...extra,
});

describe('sub-automation steps', () => {
  it("runs the child with rendered inputs and takes its output step's outputs and folder", async () => {
    await setup();
    create('child', childDef());
    const r = await start(create('parent', parentDef()), { topic: 'AI' });
    await rt.whenIdle();
    const [child] = childRuns(h.store.db, r);
    expect(child).toMatchObject({ trigger: 'parent', test: false, by: 'user', parent_run_id: r, parent_step_id: 'sub', status: 'succeeded', inputs: { topic: 'AI news', tone: 'calm' } });
    expect(getStepRun(h.store.db, r, 'sub')).toMatchObject({ status: 'succeeded', child_run_id: child!.id, outputs: { headline: 'AI news!', run_id: child!.id }, summary: 'made AI news' });
    const out = stepDir(h.dir, child!.id, 'make');
    const [told] = deskTexts();
    expect(told).toContain(`AI news! | ${out} | ${child!.id}`);
    expect(told).toContain(join(out, 'out.txt')); // Tell Desk attachments look in the child's output folder
    expect(getRun(h.store.db, r)!.status).toBe('succeeded');
  });

  it('without an output step: {run_id, status} and the child run folder, even when the child ends at once', async () => {
    await setup();
    create('child', { title: 'Child', steps: [{ id: 'note', title: 'Note', kind: 'tell_desk', text: 'child ran' }] });
    const r = await start(create('parent', parentDef({ inputs: {} })));
    const [child] = childRuns(h.store.db, r);
    expect(getStepRun(h.store.db, r, 'sub')).toMatchObject({ status: 'succeeded', outputs: { run_id: child!.id, status: 'succeeded' } });
    expect(deskTexts().some((t) => t.includes(` | ${runDir(h.dir, child!.id)} | `))).toBe(true);
  });

  it("fails with the child, and the parent step's on_error applies", async () => {
    await setup();
    rt.saveSkill({ scope: 'project', projectId, name: 'x', description: 'test skill', instructions: 'Run', files: [{ path: 'scripts/x.py', content: 'print(1)' }, { path: 'scripts/boom.py', content: 'raise SystemExit(1)' }] }, { projectId });
    create('child', childDef({ steps: [{ id: 'make', title: 'Make', kind: 'script', skill: 'x', script: 'boom.py' }] }));
    const stop = await start(create('parent', parentDef()), { topic: 'AI' });
    expect(getStepRun(h.store.db, stop, 'sub')!.error).toBe('Sub-automation "Child" failed: make failed: boom');
    expect(getRun(h.store.db, stop)).toMatchObject({ status: 'failed', reason: 'sub failed: Sub-automation "Child" failed: make failed: boom' });

    const cont = await start(
      create('parent2', parentDef({ on_error: 'continue' }, { edges: [{ from: 'sub', to: 'tell', route: 'error' }], steps: [parentDef({ on_error: 'continue' }).steps[0], { id: 'tell', title: 'Tell', kind: 'tell_desk', text: 'child failed' }] })),
      { topic: 'AI' },
    );
    expect(getStepRun(h.store.db, cont, 'sub')).toMatchObject({ status: 'failed', route: 'error' });
    expect(getRun(h.store.db, cont)!.status).toBe('succeeded');
  });

  it('a bad input fails the child as a recorded run, then the step', async () => {
    await setup();
    create('child', childDef());
    const r = await start(create('parent', parentDef({ inputs: { topic: '' } }))); // topic renders empty and is dropped: required
    const [child] = childRuns(h.store.db, r);
    expect(child).toMatchObject({ status: 'failed' });
    expect(child!.reason).toMatch(/Topic/);
    expect(getStepRun(h.store.db, r, 'sub')!.error).toMatch(/^Sub-automation "Child" failed: .*Topic/);
  });

  it('cancelling the parent cancels the child; cancelling the child fails the parent step', async () => {
    await setup();
    create('child', { title: 'Child', steps: [{ id: 'ask', title: 'OK?', kind: 'ask', question: 'Go?' }] });
    const parent = create('parent', parentDef({ inputs: {} }));
    const r = await start(parent, {}, true);
    const [child] = childRuns(h.store.db, r);
    expect(child!.test).toBe(true);
    expect(runDetail(h.store.db, r).status).toBe('waiting');
    expect(waitingOn(h.store.db, getRun(h.store.db, r)!)).toBe('Run child › Ask me: OK?');
    await rt.engine.cancelRun(r, 'enough');
    expect(getRun(h.store.db, child!.id)).toMatchObject({ status: 'cancelled', reason: 'Its parent run ended' });
    expect(getRun(h.store.db, r)).toMatchObject({ status: 'cancelled', reason: 'enough' });
    expect(getStepRun(h.store.db, r, 'sub')!.status).toBe('cancelled');

    const r2 = await start(parent);
    const [child2] = childRuns(h.store.db, r2);
    await rt.engine.cancelRun(child2!.id, 'not now');
    expect(getStepRun(h.store.db, r2, 'sub')!.error).toBe('Sub-automation "Child" cancelled: not now');
    expect(getRun(h.store.db, r2)!.status).toBe('failed');
  });

  it('nests at most three deep', async () => {
    await setup();
    create('a5', { title: 'A5', steps: [{ id: 'note', title: 'Note', kind: 'tell_desk', text: 'deep' }] });
    for (const i of [4, 3, 2, 1]) create(`a${i}`, { title: `A${i}`, steps: [{ id: 'sub', title: 'Sub', kind: 'automation', automation: `a${i + 1}` }] });
    const r1 = await start(rt.automations.requireByName(projectId, 'a1').id);
    const r2 = childRuns(h.store.db, r1)[0]!;
    const r3 = childRuns(h.store.db, r2.id)[0]!;
    const r4 = childRuns(h.store.db, r3.id)[0]!;
    expect(rt.engine.depthOf(r4)).toBe(3);
    expect(childRuns(h.store.db, r4.id)).toEqual([]);
    expect(getStepRun(h.store.db, r4.id, 'sub')!.error).toBe('Sub-automations nest at most 3 deep');
    expect(getRun(h.store.db, r1)!.status).toBe('failed');
  });

  it('fails on an automation deleted after the parent was saved', async () => {
    await setup();
    const child = create('child', childDef());
    const parent = create('parent', parentDef());
    await rt.automations.delete(child, 'user');
    const r = await start(parent, { topic: 'AI' });
    expect(getStepRun(h.store.db, r, 'sub')!.error).toBe('Unknown automation: child');
  });

  it("lets a later agent step read the child's files", async () => {
    let read = '';
    await setup((req) => {
      if (!system(req).includes('You are one step of the automation "Parent"')) return text('ok');
      const results = req.messages.filter((m) => m.role === 'tool');
      if (results.length) {
        read = String(results[0]!.content);
        return tools(call('complete', { summary: 'read it' }));
      }
      const path = /Read (\S+out\.txt)/.exec(system(req))![1]!;
      return tools(call('read_file', { path }));
    });
    create('child', childDef());
    const r = await start(
      create('parent', parentDef({}, { steps: [parentDef().steps[0], { id: 'use', title: 'Use', kind: 'agent', brief: 'Read {{steps.sub.dir}}/out.txt' }], edges: [{ from: 'sub', to: 'use' }] })),
      { topic: 'AI' },
    );
    await rt.whenIdle();
    expect(getStepRun(h.store.db, r, 'use')!.status).toBe('succeeded');
    expect(read).toContain('hello from the child');
    expect(readFileSync(join(stepDir(h.dir, childRuns(h.store.db, r)[0]!.id, 'make'), 'out.txt'), 'utf8')).toBe('hello from the child');
    void clock;
  });
});
