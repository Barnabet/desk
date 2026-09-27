import { afterEach, describe, expect, it } from 'vitest';
import { text } from '@desk/fake-model';
import { automationHarness, FakeClock } from '../testing/automations';
import type { Harness } from '../testing/harness';
import type { Runtime } from '../runtime/runtime';
import { getAutomation, listRuns, listTriggerSkips } from './queries';

let h: Harness;
let rt: Runtime;
let projectId: string;
let clock: FakeClock;
afterEach(async () => h?.cleanup());

/** Every day at 08:00 in Paris: 06:00Z while summer time lasts. */
const daily = (catch_up: 'once' | 'skip' = 'once', cron = '0 8 * * *') => ({ kind: 'schedule', cron, timezone: 'Europe/Paris', catch_up });
const quick = (extra: object = {}) => ({ title: 'Quick', steps: [{ id: 'note', title: 'Note', kind: 'tell_desk', text: 'ran' }], triggers: [daily()], ...extra });
/** Waits on an Ask me; a 48-hour deadline outlives the day the tests move through. */
const slow = (extra: object = {}) => ({ title: 'Slow', steps: [{ id: 'ok', title: 'OK?', kind: 'ask', question: 'Go?' }], triggers: [daily()], limits: { run_deadline_hours: 48 }, ...extra });

async function setup(at = '2026-09-28T05:00:00.000Z') {
  ({ h, rt, projectId, clock } = await automationHarness({ clock: new FakeClock(Date.parse(at)), script: () => text('ok') }));
}
function onNow(name: string, def: object): string {
  const id = rt.automations.create(projectId, name, def, { origin: 'user', via: 'editor' }).automation.id;
  rt.automations.setEnabled(id, true, 'user');
  return id;
}
const runs = (id: string) => listRuns(h.store.db, id).reverse(); // oldest first

describe('schedules', () => {
  it('never fires when turned on, fires at the due time, and only once', async () => {
    await setup();
    const id = onNow('quick', quick());
    expect(getAutomation(h.store.db, id)!.last_due).toEqual({ '0': '2026-09-28T05:00:00.000Z' });
    await rt.engine.tick();
    expect(runs(id)).toEqual([]);
    clock.set('2026-09-28T06:00:10.000Z');
    await rt.engine.tick();
    await rt.engine.tick();
    expect(runs(id)).toHaveLength(1);
    expect(runs(id)[0]).toMatchObject({ trigger: 'schedule', by: 'schedule', test: false, due_at: '2026-09-28T06:00:00.000Z', status: 'succeeded' });
    expect(getAutomation(h.store.db, id)!.last_due).toEqual({ '0': '2026-09-28T06:00:00.000Z' });
  });

  it('skips a due time while a non-test run is still going, not for a test run', async () => {
    await setup();
    const id = onNow('slow', slow());
    const test = await rt.engine.startRun(id, { trigger: 'test', test: true, inputs: {}, by: 'user' });
    clock.set('2026-09-28T06:00:10.000Z');
    await rt.engine.tick();
    expect(runs(id).map((r) => r.trigger)).toEqual(['test', 'schedule']); // the waiting test run does not block it
    clock.set('2026-09-29T06:00:10.000Z');
    await rt.engine.tick();
    expect(runs(id)).toHaveLength(2);
    expect(listTriggerSkips(h.store.db, projectId, id)).toEqual([expect.objectContaining({ trigger_index: 0, due_at: '2026-09-29T06:00:00.000Z', reason: 'still_running' })]);
    expect(getAutomation(h.store.db, id)!.last_due).toEqual({ '0': '2026-09-29T06:00:00.000Z' });
    void test;
  });

  it('catches up once after downtime, or records the missed time', async () => {
    await setup();
    const once = onNow('once', quick());
    const skip = onNow('skip', quick({ triggers: [daily('skip')] }));
    clock.set('2026-10-01T07:00:00.000Z'); // four due times passed: Sep 28, 29, 30 and Oct 1 at 06:00Z
    await rt.engine.tick();
    expect(runs(once)).toEqual([expect.objectContaining({ due_at: '2026-10-01T06:00:00.000Z', caught_up: 3 })]);
    expect(runs(skip)).toEqual([]);
    expect(listTriggerSkips(h.store.db, projectId, skip)).toEqual([expect.objectContaining({ due_at: '2026-10-01T06:00:00.000Z', reason: 'missed' })]);
    clock.set('2026-10-02T06:00:20.000Z'); // on time: runs
    await rt.engine.tick();
    expect(runs(skip)).toEqual([expect.objectContaining({ due_at: '2026-10-02T06:00:00.000Z' })]);
  });

  it('restarts the cursor when a changed schedule is saved while on, and stops when turned off', async () => {
    await setup();
    const id = onNow('quick', quick());
    clock.set('2026-09-28T05:30:00.000Z');
    rt.automations.save(id, quick({ triggers: [daily('once', '30 8 * * *')] }), { origin: 'user', via: 'editor' });
    expect(getAutomation(h.store.db, id)!.last_due).toEqual({ '0': '2026-09-28T05:30:00.000Z' });
    clock.set('2026-09-28T06:00:10.000Z');
    await rt.engine.tick();
    expect(runs(id)).toEqual([]); // the old 08:00 is gone
    clock.set('2026-09-28T06:30:10.000Z');
    await rt.engine.tick();
    expect(runs(id)).toHaveLength(1);
    rt.automations.setEnabled(id, false, 'user');
    clock.set('2026-09-29T06:30:10.000Z');
    await rt.engine.tick();
    expect(runs(id)).toHaveLength(1);
  });

  it('runs one tick at a time', async () => {
    await setup();
    const id = onNow('quick', quick());
    clock.set('2026-09-28T06:00:10.000Z');
    await Promise.all([rt.engine.tick(), rt.engine.tick(), rt.engine.tick()]);
    expect(runs(id)).toHaveLength(1);
  });

  it('passes the schedule inputs', async () => {
    await setup();
    const id = onNow('quick', quick({ inputs: [{ key: 'topic', label: 'Topic', type: 'text', required: true }], triggers: [{ ...daily(), inputs: { topic: 'AI' } }] }));
    clock.set('2026-09-28T06:00:10.000Z');
    await rt.engine.tick();
    expect(runs(id)[0]!.inputs).toEqual({ topic: 'AI' });
  });
});
