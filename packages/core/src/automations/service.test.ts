import { afterEach, describe, expect, it } from 'vitest';
import { ConflictError, NotFoundError, ValidationError } from '../errors';
import { automationHarness, waitDef, type FakeClock } from '../testing/automations';
import type { Harness } from '../testing/harness';
import type { Runtime } from '../runtime/runtime';
import { getAutomation } from './queries';
import { automationDetail, automationSummary, runDetail, versionInfos } from './views';
import { toolByName } from '../runtime/toolsets';

let h: Harness;
let rt: Runtime;
let projectId: string;
let clock: FakeClock;
afterEach(async () => h?.cleanup());

async function setup() {
  ({ h, rt, projectId, clock } = await automationHarness());
}

const scheduled = (cron = '0 8 * * 1') => waitDef({ triggers: [{ kind: 'schedule', cron, timezone: 'Europe/Paris' }] });

describe('Automations service', () => {
  it('creates, saves new versions and refuses a stale base version', async () => {
    await setup();
    const { automation, warnings } = rt.automations.create(projectId, 'digest', waitDef(), { origin: 'user', via: 'editor' });
    expect(warnings).toEqual([]);
    expect(automation).toMatchObject({ name: 'digest', version: 1, title: 'Wait a bit', enabled: false });
    const v2 = rt.automations.save(automation.id, waitDef({ title: 'Wait longer' }), { origin: 'user', via: 'editor', baseVersion: 1, changeNote: 'rename' });
    expect(v2.automation).toMatchObject({ version: 2, title: 'Wait longer' });
    const stale = (() => {
      try {
        rt.automations.save(automation.id, waitDef(), { origin: 'user', via: 'editor', baseVersion: 1 });
      } catch (e) {
        return e;
      }
    })();
    expect(stale).toBeInstanceOf(ConflictError);
    expect((stale as ConflictError).details).toEqual({ current_version: 2 });
    // Desk saves without a base version.
    expect(rt.automations.save(automation.id, waitDef(), { origin: 'agent:x', via: 'tool' }).automation.version).toBe(3);
    expect(versionInfos(h.store.db, automation.id).map((v) => [v.version, v.origin, v.via])).toEqual([
      [3, 'agent:x', 'tool'],
      [2, 'user', 'editor'],
      [1, 'user', 'editor'],
    ]);
  });

  it('refuses invalid definitions with their issues, and taken names', async () => {
    await setup();
    const err = (() => {
      try {
        rt.automations.create(projectId, 'bad', { title: 'x', steps: [] }, { origin: 'user', via: 'editor' });
      } catch (e) {
        return e;
      }
    })();
    expect(err).toBeInstanceOf(ValidationError);
    expect((err as ValidationError).details).toMatchObject({ errors: [{ path: 'steps', message: expect.any(String) }] });
    expect(() => rt.automations.create(projectId, 'Bad Name', waitDef(), { origin: 'user', via: 'editor' })).toThrow(ValidationError);
    rt.automations.create(projectId, 'digest', waitDef(), { origin: 'user', via: 'editor' });
    expect(() => rt.automations.create(projectId, 'digest', waitDef(), { origin: 'user', via: 'editor' })).toThrow(ConflictError);
  });

  it('validates with next schedule times', async () => {
    await setup();
    const r = rt.automations.validate(projectId, scheduled('0 8 * * *'));
    expect(r.errors).toEqual([]);
    // The clock is at 08:00 in Paris, and next times are strictly later.
    expect(r.next_times['0']).toEqual(['2026-09-29T06:00:00.000Z', '2026-09-30T06:00:00.000Z', '2026-10-01T06:00:00.000Z']);
    expect(rt.automations.validate(projectId, { title: 'x' }).errors[0]!.path).toBe('steps');
  });

  it('switches, sets and keeps grants, and records an enable request', async () => {
    await setup();
    const { rt: r } = { rt };
    const { automation } = r.automations.create(projectId, 'digest', scheduled(), { origin: 'user', via: 'editor' });
    r.automations.setEnabled(automation.id, true, 'user');
    r.automations.setEnabled(automation.id, true, 'user'); // no-op
    expect(h.store.list({ projectId, types: ['automation.switched'] })).toHaveLength(1);
    r.automations.setGrants(automation.id, [{ tool: 'web_fetch', match: { domain: 'example.com' }, action: 'allow' }], 'edited');
    r.automations.save(automation.id, scheduled(), { origin: 'agent:d', via: 'tool' });
    expect(getAutomation(h.store.db, automation.id)!.grants_suspended).toBe(true);
    r.automations.keepGrants(automation.id);
    expect(getAutomation(h.store.db, automation.id)!.grants_suspended).toBe(false);
    r.automations.requestEnable(automation.id, 'desk-agent', 'Tested, ready');
    expect(getAutomation(h.store.db, automation.id)!.enable_request).toMatchObject({ note: 'Tested, ready', proposed_grants: [] });
  });

  it('restores, exports, imports and deletes', async () => {
    await setup();
    const { automation } = rt.automations.create(projectId, 'digest', waitDef(), { origin: 'user', via: 'editor' });
    rt.automations.save(automation.id, waitDef({ title: 'Changed' }), { origin: 'user', via: 'editor' });
    rt.automations.restore(automation.id, 1, 'user');
    expect(getAutomation(h.store.db, automation.id)).toMatchObject({ version: 3, title: 'Wait a bit' });
    const exp = rt.automations.exportOf(automation.id);
    expect(exp).toMatchObject({ format: 'desk-automation/1', name: 'digest' });
    expect(() => rt.automations.importInto(projectId, exp, 'user')).toThrow(ConflictError);
    const imported = rt.automations.importInto(projectId, { ...exp, name: 'digest-copy' }, 'user');
    expect(imported.automation.version).toBe(1);
    await rt.automations.delete(automation.id, 'user');
    expect(() => rt.automations.require(automation.id)).toThrow(NotFoundError);
    expect(() => rt.automations.version(imported.automation.id, 9)).toThrow(NotFoundError);
  });

  it('builds summaries and details', async () => {
    await setup();
    const { automation } = rt.automations.create(projectId, 'digest', scheduled('0 8 * * 1'), { origin: 'user', via: 'editor' });
    let s = automationSummary(h.store.db, getAutomation(h.store.db, automation.id)!, clock.now());
    expect(s).toMatchObject({ enabled: false, next_due: null, tested_version: null, last_run: null, schedules: [{ cron: '0 8 * * 1', timezone: 'Europe/Paris' }] });
    rt.automations.setEnabled(automation.id, true, 'user');
    s = automationSummary(h.store.db, getAutomation(h.store.db, automation.id)!, clock.now());
    // Turned on at 2026-09-28T06:00Z (Monday 08:00 Paris): the next Monday.
    expect(s.next_due).toBe('2026-10-05T06:00:00.000Z');
    const d = automationDetail(h.store.db, getAutomation(h.store.db, automation.id)!, clock.now(), toolByName);
    expect(d).toMatchObject({ grants: [], proposed_grants: [], layout: {}, enable_request: null });
  });

  it('derives waiting from a question in a run detail, and lists every step', async () => {
    await setup();
    const { automation } = rt.automations.create(
      projectId,
      'ask',
      { title: 'Ask', steps: [{ id: 'a', title: 'Ask', kind: 'ask', question: 'OK?' }, { id: 'w', title: 'Wait', kind: 'wait', minutes: 1 }], edges: [{ from: 'a', to: 'w' }] },
      { origin: 'user', via: 'editor' },
    );
    h.store.append({ project_id: projectId, agent_id: null, type: 'automation.run_started', payload: { run_id: 'r1', automation_id: automation.id, version: 1, trigger: 'manual', test: false, inputs: {}, by: 'user', deadline_at: '2026-09-29T06:00:00.000Z' } });
    h.store.append({ project_id: projectId, agent_id: null, type: 'automation.step_changed', payload: { run_id: 'r1', step_id: 'a', attempt: 1, status: 'waiting', question: { text: 'OK?', files: [] } } });
    const detail = runDetail(h.store.db, 'r1');
    expect(detail.status).toBe('waiting');
    expect(detail.steps.map((s) => [s.step_id, s.status])).toEqual([
      ['a', 'waiting'],
      ['w', 'pending'],
    ]);
    expect(automationSummary(h.store.db, getAutomation(h.store.db, automation.id)!, clock.now()).last_run).toMatchObject({ status: 'waiting', waiting_on: 'Ask me: Ask' });
  });
});
