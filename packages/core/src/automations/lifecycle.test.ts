import { existsSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { text } from '@desk/fake-model';
import { ConflictError } from '../errors';
import { newId } from '../ids';
import { automationHarness, type FakeClock } from '../testing/automations';
import type { Harness } from '../testing/harness';
import type { Runtime } from '../runtime/runtime';
import { getAgent } from '../state/queries';
import { INTERRUPTED_SCRIPT } from './engine';
import { runDir } from './folders';
import { getAutomation, getRun, getStepRun, listRuns } from './queries';

let h: Harness;
let rt: Runtime;
let projectId: string;
let clock: FakeClock;
afterEach(async () => h?.cleanup());

/** `x.py` succeeds at once; `hang.py` never answers (a test executor); `sleepy.sh` is a real script that sleeps. */
async function setup() {
  ({ h, rt, projectId, clock } = await automationHarness({ script: () => text('ok') }));
  rt.saveSkill(
    {
      scope: 'project',
      projectId,
      name: 'x',
      description: 'test skill',
      instructions: 'Run',
      files: [
        { path: 'scripts/x.py', content: 'print(1)' },
        { path: 'scripts/hang.py', content: 'print(1)' },
        { path: 'scripts/sleepy.sh', content: '#!/bin/bash\nsleep 30\n' },
      ],
    },
    { projectId },
  );
}
/** Test script executor: `x.py` succeeds, `hang.py` stays running. */
function stubScripts() {
  rt.engine.register('script', ({ run, step }) => {
    if (step.kind === 'script' && step.script === 'x.py') rt.engine.resolveStep(run.id, step.id, { status: 'succeeded', route: null, outputs: {}, summary: 'ok' });
  });
}
const create = (name: string, def: object) => rt.automations.create(projectId, name, def, { origin: 'user', via: 'editor' }).automation.id;
const start = (id: string, test = false) => rt.engine.startRun(id, { trigger: test ? 'test' : 'manual', test, inputs: {}, by: 'user' });
const script = (id: string, file: string, extra: object = {}) => ({ id, title: id, kind: 'script', skill: 'x', script: file, ...extra });

/** Appends a run and step states as a crash would have left them (no engine involved). */
function crashedRun(
  automationId: string,
  steps: Array<Record<string, unknown> & { step_id: string; status: string }>,
  deadline = '2026-09-29T06:00:00.000Z',
  o: { runId?: string; parent?: { run_id: string; step_id: string } } = {},
): string {
  const runId = o.runId ?? newId();
  const parent = o.parent ? { trigger: 'parent', parent: o.parent } : { trigger: 'manual' };
  h.store.append({ project_id: projectId, agent_id: null, type: 'automation.run_started', payload: { run_id: runId, automation_id: automationId, version: 1, ...parent, test: false, inputs: {}, by: 'user', deadline_at: deadline } as never });
  for (const s of steps) h.store.append({ project_id: projectId, agent_id: null, type: 'automation.step_changed', payload: { run_id: runId, attempt: 1, ...s } as never });
  return runId;
}

describe('recovery', () => {
  it('fails a crash-interrupted script, retrying it only when idempotent', async () => {
    await setup();
    stubScripts();
    const plain = crashedRun(create('plain', { title: 'Plain', steps: [script('s', 'x.py', { on_error: { retry: 2 } })] }), [{ step_id: 's', status: 'running' }]);
    const idem = crashedRun(create('idem', { title: 'Idem', steps: [script('s', 'x.py', { on_error: { retry: 2 }, idempotent: true })] }), [{ step_id: 's', status: 'running' }]);
    rt.engine.recover(new Set());
    expect(getStepRun(h.store.db, plain, 's')).toMatchObject({ status: 'failed', error: INTERRUPTED_SCRIPT });
    expect(getRun(h.store.db, plain)!.status).toBe('failed');
    expect(getStepRun(h.store.db, idem, 's')).toMatchObject({ status: 'pending', attempt: 2 });
    clock.advance(30_000);
    await rt.engine.tick();
    expect(getRun(h.store.db, idem)!.status).toBe('succeeded');
  });

  it('re-attaches agent and sub-automation steps, relaunches cut-off built-ins, and moves every run on', async () => {
    await setup();
    stubScripts();
    create('child', { title: 'Child', steps: [script('s', 'x.py')] });
    const id = create('mixed', {
      title: 'Mixed',
      steps: [
        { id: 'agent', title: 'Agent', kind: 'agent', brief: 'Do it.' },
        { id: 'sub', title: 'Sub', kind: 'automation', automation: 'child' },
        { id: 'ask', title: 'Ask', kind: 'ask', question: 'Go?' },
        script('after', 'x.py'),
      ],
      edges: [{ from: 'agent', to: 'after' }],
    });
    // The agent was never created, the child never started, and the Ask me was cut off before `waiting`.
    const r = crashedRun(id, [
      { step_id: 'agent', status: 'running', agent_id: 'nobody' },
      { step_id: 'sub', status: 'running', child_run_id: 'never-started' },
      { step_id: 'ask', status: 'running' },
    ]);
    rt.engine.recover(new Set());
    expect(getStepRun(h.store.db, r, 'agent')).toMatchObject({ status: 'failed', error: 'interrupted: the daemon stopped before the step agent started' });
    expect(getRun(h.store.db, r)!.status).toBe('failed'); // on_error stop

    const r2 = newId();
    const child = crashedRun(rt.automations.requireByName(projectId, 'child').id, [{ step_id: 's', status: 'succeeded', summary: 'done' }], undefined, { parent: { run_id: r2, step_id: 'sub' } });
    crashedRun(
      create('subonly', { title: 'Sub only', steps: [{ id: 'sub', title: 'Sub', kind: 'automation', automation: 'child' }, { id: 'ask', title: 'Ask', kind: 'ask', question: 'Go?' }] }),
      [
        { step_id: 'sub', status: 'running', child_run_id: child },
        { step_id: 'ask', status: 'running' },
      ],
      undefined,
      { runId: r2 },
    );
    rt.engine.recover(new Set());
    expect(getRun(h.store.db, child)!.status).toBe('succeeded'); // settled but unfinished: finished now
    expect(getStepRun(h.store.db, r2, 'sub')!.status).toBe('succeeded'); // followed its child
    expect(getStepRun(h.store.db, r2, 'ask')).toMatchObject({ status: 'waiting', question: { text: 'Go?' } });
  });

  it('cancels a run whose deadline passed while the daemon was down', async () => {
    await setup();
    const r = crashedRun(create('slow', { title: 'Slow', steps: [{ id: 'ask', title: 'Ask', kind: 'ask', question: 'Go?' }] }), [{ step_id: 'ask', status: 'waiting', question: { text: 'Go?', files: [] } }], '2026-09-28T05:00:00.000Z');
    rt.engine.recover(new Set());
    expect(getRun(h.store.db, r)).toMatchObject({ status: 'cancelled', reason: 'deadline' });
  });

  it('settles a step agent whose run ended just before the crash', async () => {
    await setup();
    h.fake.setScript(() => text('I did it.'));
    const ended = vi.spyOn(rt.engine, 'onAgentEnded').mockImplementation(() => {}); // as if afterRun never ran
    const r = await start(create('a', { title: 'A', steps: [{ id: 'agent', title: 'Agent', kind: 'agent', brief: 'Do it.' }] }));
    await rt.whenIdle();
    const agentId = getStepRun(h.store.db, r, 'agent')!.agent_id!;
    expect(getStepRun(h.store.db, r, 'agent')!.status).toBe('running');
    expect(getAgent(h.store.db, agentId)!.status).toBe('idle');
    ended.mockRestore();
    rt.engine.recover(new Set());
    await rt.whenIdle();
    // Recovery settles it as afterRun would have: the one nudge, then the failure.
    expect(getStepRun(h.store.db, r, 'agent')).toMatchObject({ status: 'failed', error: 'Ended its turn twice without calling complete or fail_step' });
  });

  it('leaves a resumed step agent to its run', async () => {
    await setup();
    h.fake.setScript(() => ({ kind: 'hang' }));
    const r = await start(create('a', { title: 'A', steps: [{ id: 'agent', title: 'Agent', kind: 'agent', brief: 'Do it.' }] }));
    await new Promise((res) => setTimeout(res, 50));
    const agentId = getStepRun(h.store.db, r, 'agent')!.agent_id!;
    rt.engine.recover(new Set([agentId]));
    expect(getStepRun(h.store.db, r, 'agent')!.status).toBe('running');
  });
});

describe('shutdown, archiving and deletion', () => {
  it('kills running scripts on shutdown, leaving them for recovery, and refuses new runs', async () => {
    await setup();
    // No sandbox in the harness: without a rule every script would wait on a gate instead of running.
    rt.updateSettings(projectId, { policy: [{ tool: 'skill_run', match: { command: '^x/' }, action: 'allow' }] });
    const id = create('sleepy', { title: 'Sleepy', steps: [script('s', 'sleepy.sh')] });
    const r = await start(id);
    await new Promise((res) => setTimeout(res, 200)); // the process starts after the runtime is prepared
    const t0 = Date.now();
    await rt.engine.shutdown();
    expect(Date.now() - t0).toBeLessThan(10_000);
    expect(getStepRun(h.store.db, r, 's')!.status).toBe('running');
    await expect(start(id)).rejects.toThrow(ConflictError);
  });

  it('turns automations off and cancels their runs when the project is archived', async () => {
    await setup();
    h.fake.setScript(() => ({ kind: 'hang' }));
    const id = create('both', {
      title: 'Both',
      steps: [
        { id: 'ask', title: 'Ask', kind: 'ask', question: 'Go?' },
        { id: 'agent', title: 'Agent', kind: 'agent', brief: 'Do it.' },
      ],
    });
    rt.automations.setEnabled(id, true, 'user');
    const r = await start(id);
    await new Promise((res) => setTimeout(res, 50));
    rt.archiveProject(projectId);
    await rt.whenIdle();
    expect(getAutomation(h.store.db, id)!.enabled).toBe(false);
    expect(h.store.list({ projectId, types: ['automation.switched'] }).at(-1)!.payload).toMatchObject({ enabled: false, by: 'system' });
    expect(getRun(h.store.db, r)).toMatchObject({ status: 'cancelled', reason: 'Project archived' });
    const agentId = getStepRun(h.store.db, r, 'agent')!.agent_id!;
    expect(getAgent(h.store.db, agentId)!.status).toBe('cancelled');
  });

  it('cancels runs, test runs included, when an automation is deleted', async () => {
    await setup();
    const id = create('ask', { title: 'Ask', steps: [{ id: 'ask', title: 'Ask', kind: 'ask', question: 'Go?' }] });
    const r = await start(id);
    const t = await start(id, true);
    await rt.automations.delete(id, 'user');
    expect([getRun(h.store.db, r)!.reason, getRun(h.store.db, t)!.reason]).toEqual(['Automation deleted', 'Automation deleted']);
  });
});

describe('retention', () => {
  it('keeps the newest 20 or 30 days, whichever keeps more, and the protected runs', async () => {
    await setup();
    stubScripts();
    const id = create('often', { title: 'Often', steps: [script('s', 'x.py')] });
    const old: string[] = [];
    for (let i = 0; i < 22; i++) old.push(await start(id));
    await rt.engine.settled();
    expect(old.every((r) => existsSync(runDir(h.dir, r)))).toBe(true); // all younger than 30 days
    clock.advance(31 * 24 * 3_600_000);
    const fresh = await start(id);
    await rt.engine.settled();
    const gone = old.filter((r) => !existsSync(runDir(h.dir, r)));
    expect(gone).toEqual(old.slice(0, 3)); // newest first: fresh + 19 old kept; the 3 oldest removed
    expect(existsSync(runDir(h.dir, fresh))).toBe(true);
    expect(listRuns(h.store.db, id, { limit: 100 })).toHaveLength(23); // rows stay; only folders go
  });
});
