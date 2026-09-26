import { afterEach, describe, expect, it } from 'vitest';
import type { AutomationDefinition, EventInput } from '@desk/protocol';
import { AutomationDefinition as Def } from '@desk/protocol';
import { createHarness, type Harness } from '../testing/harness';
import { getAgent } from '../state/queries';
import {
  findAutomation,
  getAutomation,
  getRun,
  getStepRun,
  lastSucceededRun,
  listAutomations,
  listTriggerSkips,
  listVersions,
  runningRunOf,
  stepAgentOf,
  testedVersion,
} from './queries';

let h: Harness;
afterEach(async () => h?.cleanup());

const P = 'proj1';
const def = (cron = '0 8 * * 1'): AutomationDefinition =>
  Def.parse({ title: 'Digest', triggers: [{ kind: 'schedule', cron, timezone: 'Europe/Paris' }], steps: [{ id: 'w', title: 'Wait', kind: 'wait', minutes: 1 }] });
const e = (type: string, payload: unknown, agent_id: string | null = null) => ({ project_id: P, agent_id, type, payload }) as EventInput;
const saved = (version: number, origin = 'user', d = def()) => e('automation.saved', { automation_id: 'a1', name: 'digest', version, definition: d, origin, change_note: `v${version}`, via: 'editor' });

async function setup() {
  h = await createHarness();
  h.store.append(e('project.created', { name: 'P', goal: 'G', instructions: '' }));
}

describe('automation projections', () => {
  it('creates, versions and deletes', async () => {
    await setup();
    h.store.append(saved(1));
    h.store.append(saved(2, 'user', def('0 9 * * 1')));
    const a = findAutomation(h.store.db, P, 'digest')!;
    expect(a).toMatchObject({ version: 2, title: 'Digest', enabled: false, grants: [], grants_suspended: false, layout: {} });
    expect(a.definition.triggers[0]!.cron).toBe('0 9 * * 1');
    expect(listVersions(h.store.db, 'a1').map((v) => [v.version, v.change_note])).toEqual([
      [2, 'v2'],
      [1, 'v1'],
    ]);
    h.store.append(e('automation.layout_saved', { automation_id: 'a1', layout: { w: { x: 5, y: 6 } } }));
    expect(getAutomation(h.store.db, 'a1')!.layout).toEqual({ w: { x: 5, y: 6 } });
    h.store.append(e('automation.deleted', { automation_id: 'a1', origin: 'user' }));
    expect(findAutomation(h.store.db, P, 'digest')).toBeUndefined();
    expect(listAutomations(h.store.db, P)).toEqual([]);
    expect(getAutomation(h.store.db, 'a1')!.deleted_at).not.toBeNull();
  });

  it('suspends grants on an agent save and ends the suspension on grants_set', async () => {
    await setup();
    h.store.append(saved(1, 'agent:desk1'));
    expect(getAutomation(h.store.db, 'a1')!.grants_suspended).toBe(false); // no grants yet
    h.store.append(e('automation.grants_set', { automation_id: 'a1', grants: [{ tool: 'web_fetch', action: 'allow' }], reason: 'enabled' }));
    expect(getAutomation(h.store.db, 'a1')).toMatchObject({ grants_set_version: 1, grants_suspended: false });
    h.store.append(saved(2, 'agent:desk1'));
    expect(getAutomation(h.store.db, 'a1')!.grants_suspended).toBe(true);
    h.store.append(saved(3, 'user'));
    expect(getAutomation(h.store.db, 'a1')!.grants_suspended).toBe(true); // a user save does not end it
    h.store.append(e('automation.grants_set', { automation_id: 'a1', grants: [{ tool: 'web_fetch', action: 'allow' }, { tool: 'open_pr', action: 'allow' }], reason: 'remembered' }));
    expect(getAutomation(h.store.db, 'a1')!.grants_suspended).toBe(true); // remembered keeps it
    h.store.append(e('automation.grants_set', { automation_id: 'a1', grants: [{ tool: 'web_fetch', action: 'allow' }], reason: 'kept' }));
    expect(getAutomation(h.store.db, 'a1')).toMatchObject({ grants_suspended: false, grants_set_version: 3 });
  });

  it('keeps the schedule cursors: on, changed schedule, runs and skips', async () => {
    await setup();
    h.store.append(saved(1));
    const [on] = h.store.append(e('automation.switched', { automation_id: 'a1', enabled: true, by: 'user' }));
    expect(getAutomation(h.store.db, 'a1')!.last_due).toEqual({ '0': on!.ts });
    h.store.append(e('automation.run_started', { run_id: 'r1', automation_id: 'a1', version: 1, trigger: 'schedule', test: false, inputs: {}, by: 'schedule', trigger_index: 0, due_at: '2026-09-28T06:00:00.000Z', deadline_at: '2026-09-29T06:00:00.000Z' }));
    expect(getAutomation(h.store.db, 'a1')!.last_due).toEqual({ '0': '2026-09-28T06:00:00.000Z' });
    h.store.append(e('automation.trigger_skipped', { automation_id: 'a1', trigger_index: 0, due_at: '2026-10-05T06:00:00.000Z', reason: 'still_running' }));
    expect(getAutomation(h.store.db, 'a1')!.last_due).toEqual({ '0': '2026-10-05T06:00:00.000Z' });
    expect(listTriggerSkips(h.store.db, P, 'a1')).toEqual([expect.objectContaining({ trigger_index: 0, reason: 'still_running' })]);
    // Saving the same schedule keeps the cursor; a changed one restarts it from the save.
    h.store.append(saved(2));
    expect(getAutomation(h.store.db, 'a1')!.last_due).toEqual({ '0': '2026-10-05T06:00:00.000Z' });
    const [changed] = h.store.append(saved(3, 'user', def('30 7 * * *')));
    expect(getAutomation(h.store.db, 'a1')!.last_due).toEqual({ '0': changed!.ts });
    h.store.append(e('automation.enable_requested', { automation_id: 'a1', note: 'ready', proposed_grants: [] }));
    h.store.append(e('automation.switched', { automation_id: 'a1', enabled: false, by: 'user' }));
    expect(getAutomation(h.store.db, 'a1')!.enable_request).toMatchObject({ note: 'ready' });
    h.store.append(e('automation.switched', { automation_id: 'a1', enabled: true, by: 'user' }));
    expect(getAutomation(h.store.db, 'a1')!.enable_request).toBeNull();
  });

  it('projects runs and step runs, resetting results on a new attempt', async () => {
    await setup();
    h.store.append(saved(1));
    h.store.append(e('automation.run_started', { run_id: 'r1', automation_id: 'a1', version: 1, trigger: 'test', test: true, inputs: { n: 2 }, by: 'agent:desk1', deadline_at: '2026-09-29T06:00:00.000Z' }));
    expect(getRun(h.store.db, 'r1')).toMatchObject({ status: 'running', test: true, inputs: { n: 2 }, caught_up: 0 });
    expect(runningRunOf(h.store.db, 'a1')).toBeUndefined(); // tests excluded by default
    expect(runningRunOf(h.store.db, 'a1', { includeTests: true })!.id).toBe('r1');
    h.store.append(e('automation.step_changed', { run_id: 'r1', step_id: 'w', attempt: 1, status: 'running' }));
    expect(getStepRun(h.store.db, 'r1', 'w')!.started_at).not.toBeNull();
    h.store.append(e('automation.step_changed', { run_id: 'r1', step_id: 'w', attempt: 1, status: 'failed', error: 'boom' }));
    expect(getStepRun(h.store.db, 'r1', 'w')).toMatchObject({ status: 'failed', error: 'boom' });
    expect(getStepRun(h.store.db, 'r1', 'w')!.finished_at).not.toBeNull();
    h.store.append(e('automation.step_changed', { run_id: 'r1', step_id: 'w', attempt: 2, status: 'pending', resume_at: '2026-09-28T06:00:30.000Z' }));
    expect(getStepRun(h.store.db, 'r1', 'w')).toMatchObject({ attempt: 2, status: 'pending', error: null, finished_at: null, resume_at: '2026-09-28T06:00:30.000Z' });
    h.store.append(e('automation.step_changed', { run_id: 'r1', step_id: 'w', attempt: 2, status: 'succeeded', route: 'done', outputs: { n: 1 }, summary: 'ok', resume_at: null }));
    expect(getStepRun(h.store.db, 'r1', 'w')).toMatchObject({ status: 'succeeded', route: 'done', outputs: { n: 1 }, summary: 'ok', resume_at: null });
    h.store.append(e('automation.run_finished', { run_id: 'r1', status: 'succeeded', summary: 'ok' }));
    expect(getRun(h.store.db, 'r1')).toMatchObject({ status: 'succeeded', summary: 'ok' });
    expect(testedVersion(h.store.db, 'a1')).toBe(1);
    expect(lastSucceededRun(h.store.db, 'a1')).toBeUndefined(); // test runs are not "previous"
  });

  it('links step agents to their run and step', async () => {
    await setup();
    h.store.append(saved(1));
    h.store.append(e('automation.run_started', { run_id: 'r1', automation_id: 'a1', version: 1, trigger: 'manual', test: false, inputs: {}, by: 'user', deadline_at: '2026-09-29T06:00:00.000Z' }));
    h.store.append(e('automation.step_changed', { run_id: 'r1', step_id: 'w', attempt: 1, status: 'running', agent_id: 's1' }));
    h.store.append(e('agent.created', { role: 'step', model: 'fake-model', title: 'Wait', brief: 'b', workspace_path: '/tmp/x', parent_id: null, automation: { run_id: 'r1', step_id: 'w' } }, 's1'));
    expect(getAgent(h.store.db, 's1')).toMatchObject({ role: 'step', automation_run_id: 'r1', automation_step_id: 'w' });
    expect(stepAgentOf(h.store.db, 's1')).toMatchObject({ run: { id: 'r1' }, step: { step_id: 'w' } });
  });
});
