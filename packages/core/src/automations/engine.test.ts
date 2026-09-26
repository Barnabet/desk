import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { text } from '@desk/fake-model';
import { ValidationError } from '../errors';
import { automationHarness, askDef, waitDef, type FakeClock } from '../testing/automations';
import type { Harness } from '../testing/harness';
import type { Runtime } from '../runtime/runtime';
import { getRun, getStepRun, stepRuns } from './queries';
import { stepDir } from './folders';

let h: Harness;
let rt: Runtime;
let projectId: string;
let clock: FakeClock;
afterEach(async () => h?.cleanup());

async function setup(script = [text('Noted.'), text('Noted.'), text('Noted.')]) {
  ({ h, rt, projectId, clock } = await automationHarness({ script }));
}
const create = (def: object, name = 'a') => rt.automations.create(projectId, name, def, { origin: 'user', via: 'editor' }).automation.id;
const run = (id: string, inputs = {}) => rt.engine.startRun(id, { trigger: 'manual', test: false, inputs, by: 'user' });
const status = (runId: string, stepId: string) => getStepRun(h.store.db, runId, stepId)?.status;

describe('AutomationEngine', () => {
  it('waits, then finishes on a tick after the time', async () => {
    await setup();
    const r = await run(create(waitDef()));
    expect(getStepRun(h.store.db, r, 'w')).toMatchObject({ status: 'waiting', resume_at: '2026-09-28T06:01:00.000Z' });
    await rt.engine.tick();
    expect(status(r, 'w')).toBe('waiting');
    clock.advance(60_000);
    await rt.engine.tick();
    expect(status(r, 'w')).toBe('succeeded');
    expect(getRun(h.store.db, r)).toMatchObject({ status: 'succeeded' });
  });

  it('tells Desk, quoting the automation, and wakes it', async () => {
    await setup();
    const id = create({ title: 'Digest', inputs: [{ key: 'topic', label: 'Topic', type: 'text' }], steps: [{ id: 't', title: 'Tell', kind: 'tell_desk', text: 'New digest about {{inputs.topic}}' }] });
    const r = await run(id, { topic: 'AI' });
    await rt.whenIdle();
    expect(getRun(h.store.db, r)!.status).toBe('succeeded');
    const last = h.fake.requests[0]!.messages.at(-1)!;
    expect(last.content).toMatch(/^\[message #\d+ from automation "Digest" — automation\]\n> New digest about AI/);
    expect(last.content).toContain(`> (Run ${r} of automation "Digest".)`);
  });

  it('asks, then follows the approve or reject route', async () => {
    await setup();
    const id = create(askDef());
    const r1 = await run(id);
    expect(getStepRun(h.store.db, r1, 'ask')).toMatchObject({ status: 'waiting', question: { text: 'Publish?', files: [] } });
    await rt.engine.answer(r1, 'ask', { decision: 'approve', note: 'go' });
    await rt.whenIdle();
    expect(getStepRun(h.store.db, r1, 'ask')).toMatchObject({ status: 'succeeded', outputs: { note: 'go' }, answered_by: 'user', note: 'go' });
    expect([status(r1, 'yes'), status(r1, 'no')]).toEqual(['succeeded', 'skipped']);
    const r2 = await run(id);
    await rt.engine.answer(r2, 'ask', { decision: 'reject' });
    await rt.whenIdle();
    expect([status(r2, 'ask'), status(r2, 'yes'), status(r2, 'no')]).toEqual(['rejected', 'skipped', 'succeeded']);
    expect(getRun(h.store.db, r2)!.status).toBe('succeeded');
    await expect(rt.engine.answer(r2, 'ask', { decision: 'approve' })).rejects.toThrow(/not waiting/);
  });

  it('shows upstream files with a question and expires unanswered questions', async () => {
    await setup();
    const id = create({
      title: 'Show',
      steps: [
        { id: 'w', title: 'Wait', kind: 'wait', minutes: 1 },
        { id: 'ask', title: 'Look', kind: 'ask', question: 'Look at this', show: ['*.md'], expires_after_hours: 1 },
      ],
      edges: [{ from: 'w', to: 'ask' }],
    });
    const r = await run(id);
    writeFileSync(join(stepDir(h.dir, r, 'w'), 'digest.md'), '# hi');
    clock.advance(60_000);
    await rt.engine.tick();
    expect(getStepRun(h.store.db, r, 'ask')!.question!.files).toEqual([join(stepDir(h.dir, r, 'w'), 'digest.md')]);
    clock.advance(3_600_000);
    await rt.engine.tick();
    expect(getStepRun(h.store.db, r, 'ask')).toMatchObject({ status: 'rejected', note: 'expired' });
  });

  it('retries with backoff, continues on error through the error route, and stops the run otherwise', async () => {
    await setup();
    const attempts: number[] = [];
    // A test executor for a step kind that later tasks implement: fails twice, then succeeds.
    rt.engine.register('script', ({ run, step, attempt }) => {
      attempts.push(attempt);
      if (step.id === 'flaky') rt.engine.resolveStep(run.id, step.id, attempt < 3 ? { status: 'failed', error: `boom ${attempt}` } : { status: 'succeeded', route: null, outputs: { n: attempt }, summary: 'ok' });
      else rt.engine.resolveStep(run.id, step.id, { status: 'failed', error: 'always' });
    });
    const script = (id: string, extra: object = {}) => ({ id, title: id, kind: 'script', skill: 'x', script: 'x.py', ...extra });
    // Validation needs the skill: register a project skill named x with x.py.
    rt.saveSkill({ scope: 'project', projectId, name: 'x', description: 'test skill', instructions: 'Run x.py', files: [{ path: 'scripts/x.py', content: 'print(1)' }] }, { projectId });
    const flaky = create({ title: 'Flaky', steps: [script('flaky', { on_error: { retry: 2 } })] }, 'flaky');
    const r = await run(flaky);
    expect(getStepRun(h.store.db, r, 'flaky')).toMatchObject({ status: 'pending', attempt: 2, resume_at: '2026-09-28T06:00:30.000Z' });
    clock.advance(30_000);
    await rt.engine.tick();
    expect(getStepRun(h.store.db, r, 'flaky')).toMatchObject({ status: 'pending', attempt: 3 });
    clock.advance(120_000);
    await rt.engine.tick();
    expect(getStepRun(h.store.db, r, 'flaky')).toMatchObject({ status: 'succeeded', attempt: 3, outputs: { n: 3 } });
    expect(attempts).toEqual([1, 2, 3]);

    const cont = create(
      { title: 'Continue', steps: [script('bad', { on_error: 'continue' }), { id: 'catch', title: 'Catch', kind: 'tell_desk', text: 'failed' }, { id: 'next', title: 'Next', kind: 'tell_desk', text: 'ok' }], edges: [{ from: 'bad', to: 'catch', route: 'error' }, { from: 'bad', to: 'next' }] },
      'cont',
    );
    const r2 = await run(cont);
    await rt.whenIdle();
    expect([status(r2, 'bad'), status(r2, 'catch'), status(r2, 'next')]).toEqual(['failed', 'succeeded', 'skipped']);
    expect(getStepRun(h.store.db, r2, 'bad')!.route).toBe('error');
    expect(getRun(h.store.db, r2)!.status).toBe('succeeded');

    const stop = create({ title: 'Stop', steps: [script('bad'), { id: 'after', title: 'After', kind: 'wait', minutes: 5 }], edges: [{ from: 'bad', to: 'after' }] }, 'stop');
    const r3 = await run(stop);
    expect(getRun(h.store.db, r3)).toMatchObject({ status: 'failed', reason: 'bad failed: always' });
    expect(status(r3, 'after')).toBe('cancelled');
  });

  it('caps parallel scripts per run', async () => {
    await setup();
    rt.saveSkill({ scope: 'project', projectId, name: 'x', description: 'test skill', instructions: 'Run x.py', files: [{ path: 'scripts/x.py', content: 'print(1)' }] }, { projectId });
    const started: string[] = [];
    rt.engine.register('script', ({ step }) => void started.push(step.id));
    const id = create({ title: 'Cap', limits: { max_parallel_scripts: 1 }, steps: ['a', 'b'].map((s) => ({ id: s, title: s, kind: 'script', skill: 'x', script: 'x.py' })) });
    const r = await run(id);
    expect(started).toEqual(['a']);
    rt.engine.resolveStep(r, 'a', { status: 'succeeded', route: null, outputs: {}, summary: 'a' });
    expect(started).toEqual(['a', 'b']);
  });

  it('cancels a run and cancels at the deadline', async () => {
    await setup();
    const id = create(askDef());
    const r = await run(id);
    await rt.engine.cancelRun(r, 'user asked');
    expect(getRun(h.store.db, r)).toMatchObject({ status: 'cancelled', reason: 'user asked' });
    // Every unfinished step is cancelled, reached or not (as when a step fails with on_error stop).
    expect(Object.fromEntries(stepRuns(h.store.db, r).map((s) => [s.step_id, s.status]))).toEqual({ ask: 'cancelled', yes: 'cancelled', no: 'cancelled' });
    const r2 = await run(create(askDef({ limits: { run_deadline_hours: 1 } }), 'b'));
    clock.advance(3_600_000);
    await rt.engine.tick();
    expect(getRun(h.store.db, r2)).toMatchObject({ status: 'cancelled', reason: 'deadline' });
  });

  it('refuses bad inputs for manual runs, and records a failed run for schedules', async () => {
    await setup();
    const id = create(waitDef({ inputs: [{ key: 'doc', label: 'Doc', type: 'file', required: true }] }));
    await expect(run(id, {})).rejects.toThrow(ValidationError);
    expect(h.store.list({ projectId, types: ['automation.run_started'] })).toHaveLength(0);
    const r = await rt.engine.startRun(id, { trigger: 'schedule', test: false, inputs: { doc: '/nope/missing.pdf' }, by: 'schedule', triggerIndex: 0, dueAt: '2026-09-28T06:00:00.000Z' });
    expect(getRun(h.store.db, r)).toMatchObject({ status: 'failed', reason: expect.stringMatching(/does not exist/) });
  });

  it('fails a step whose kind has no executor', async () => {
    await setup();
    create(waitDef(), 'b');
    const id = create({ title: 'Sub', steps: [{ id: 's', title: 'S', kind: 'automation', automation: 'b' }] }, 'a');
    // Every kind has an executor by now: take the sub-automation one away.
    (rt.engine as unknown as { executors: Map<string, unknown> }).executors.delete('automation');
    const r = await run(id);
    expect(getStepRun(h.store.db, r, 's')).toMatchObject({ status: 'failed', error: 'No executor for automation steps' });
    expect(getRun(h.store.db, r)!.status).toBe('failed');
  });
});
