// packages/client/src/state/automations.test.ts
import { describe, expect, it } from 'vitest';
import type { AutomationSummary, RunDetail, StoredEvent } from '@desk/protocol';
import { affectsAutomations, affectsRun, derivedRunStatus, emptyAutomations, reduceAutomations, withAutomationSummaries, withRunDetail } from './automations';

let seq = 0;
const ev = (type: string, payload: object, extra: Partial<StoredEvent> = {}): StoredEvent =>
  ({ id: ++seq, project_id: 'p', agent_id: null, type, payload, ts: `2026-09-28T06:00:${String(seq).padStart(2, '0')}.000Z`, ...extra }) as unknown as StoredEvent;

const def = (title: string, triggers: object[] = []) => ({
  title,
  description: '',
  inputs: [],
  triggers,
  steps: [{ id: 'ask', title: 'OK?', kind: 'ask', question: 'Go?' }],
  edges: [],
  after_run: 'notify',
  limits: { run_deadline_hours: 24, max_parallel_agents: 2, max_parallel_scripts: 4 },
});
const summary = (over: Partial<AutomationSummary> = {}): AutomationSummary => ({
  id: 'a1',
  project_id: 'p',
  name: 'digest',
  title: 'Digest',
  description: '',
  version: 1,
  tested_version: null,
  enabled: false,
  grants_suspended: false,
  schedules: [],
  last_run: null,
  next_due: null,
  enable_requested: false,
  updated_at: '2026-09-28T06:00:00.000Z',
  ...over,
});
const detail = (over: Partial<RunDetail> = {}): RunDetail =>
  ({
    id: 'r1',
    automation_id: 'a1',
    project_id: 'p',
    version: 1,
    trigger: 'manual',
    test: false,
    inputs: {},
    by: 'user',
    parent_run_id: null,
    parent_step_id: null,
    due_at: null,
    caught_up: 0,
    status: 'running',
    summary: null,
    reason: null,
    started_at: '2026-09-28T06:00:00.000Z',
    finished_at: null,
    deadline_at: '2026-09-29T06:00:00.000Z',
    automation_name: 'digest',
    automation_title: 'Digest',
    definition: def('Digest'),
    steps: [{ step_id: 'ask', attempt: 0, status: 'pending', route: null, outputs: {}, summary: null, error: null, agent_id: null, child_run_id: null, resume_at: null, gate: null, question: null, note: null, started_at: null, finished_at: null }],
    ...over,
  }) as RunDetail;

describe('automations state', () => {
  it('keeps summaries current: saved, switched, grants, turn-on requests, deleted', () => {
    let s = withAutomationSummaries(emptyAutomations('p'), [summary()]);
    s = reduceAutomations(s, ev('automation.saved', { automation_id: 'a1', name: 'digest', version: 2, definition: def('Morning digest', [{ kind: 'schedule', cron: '0 8 * * *', timezone: 'Europe/Paris', catch_up: 'once' }]), origin: 'user', change_note: '', via: 'editor' }));
    expect(s.list[0]).toMatchObject({ title: 'Morning digest', version: 2, schedules: [{ cron: '0 8 * * *', timezone: 'Europe/Paris' }] });
    s = reduceAutomations(s, ev('automation.saved', { automation_id: 'a0', name: 'alpha', version: 1, definition: def('Alpha'), origin: 'agent:d', change_note: '', via: 'tool' }));
    expect(s.list.map((a) => a.name)).toEqual(['alpha', 'digest']);
    s = reduceAutomations(s, ev('automation.enable_requested', { automation_id: 'a1', note: 'ready', proposed_grants: [] }));
    expect(s.list[1]!.enable_requested).toBe(true);
    s = reduceAutomations(s, ev('automation.switched', { automation_id: 'a1', enabled: true, by: 'user' }));
    expect(s.list[1]).toMatchObject({ enabled: true, enable_requested: false });
    s = { ...s, list: s.list.map((a) => ({ ...a, grants_suspended: true })) };
    s = reduceAutomations(s, ev('automation.grants_set', { automation_id: 'a1', grants: [], reason: 'remembered' }));
    expect(s.list[1]!.grants_suspended).toBe(true);
    s = reduceAutomations(s, ev('automation.grants_set', { automation_id: 'a1', grants: [], reason: 'kept' }));
    expect(s.list[1]!.grants_suspended).toBe(false);
    s = reduceAutomations(s, ev('automation.deleted', { automation_id: 'a0', origin: 'user' }));
    expect(s.list.map((a) => a.name)).toEqual(['digest']);
    expect(reduceAutomations(s, ev('automation.deleted', { automation_id: 'a1', origin: 'user' }, { project_id: 'other' }))).toBe(s);
  });

  it('follows runs: last run, live steps, derived status, the end', () => {
    let s = withAutomationSummaries(emptyAutomations('p'), [summary()]);
    s = reduceAutomations(s, ev('automation.run_started', { run_id: 'r1', automation_id: 'a1', version: 1, trigger: 'manual', test: false, inputs: {}, by: 'user', deadline_at: '2026-09-29T06:00:00.000Z' }));
    expect(s.list[0]!.last_run).toMatchObject({ id: 'r1', number: 1, status: 'running', trigger: 'manual', test: false, finished_at: null });
    s = withRunDetail(s, detail());
    s = reduceAutomations(s, ev('automation.step_changed', { run_id: 'r1', step_id: 'ask', attempt: 1, status: 'running' }));
    expect(s.runs.r1!.steps[0]).toMatchObject({ status: 'running', attempt: 1, started_at: expect.any(String) });
    const waiting = ev('automation.step_changed', { run_id: 'r1', step_id: 'ask', attempt: 1, status: 'waiting', question: { text: 'Go?', files: [] } });
    s = reduceAutomations(s, waiting);
    expect(s.runs.r1!.status).toBe('waiting');
    expect(s.list[0]!.last_run).toMatchObject({ status: 'waiting', waiting_on: 'Ask me: OK?' });
    expect(reduceAutomations(s, waiting)).toEqual(s); // replay changes nothing
    s = reduceAutomations(s, ev('automation.step_changed', { run_id: 'r1', step_id: 'ask', attempt: 1, status: 'succeeded', outputs: { note: null }, summary: 'Approved' }));
    s = reduceAutomations(s, ev('automation.run_finished', { run_id: 'r1', status: 'succeeded', summary: 'Approved' }));
    expect(s.runs.r1).toMatchObject({ status: 'succeeded', summary: 'Approved', finished_at: expect.any(String) });
    expect(s.list[0]!.last_run).toMatchObject({ status: 'succeeded', summary: 'Approved', waiting_on: null });
  });

  it('resets a step on a new attempt, and derives statuses', () => {
    let s = withRunDetail(emptyAutomations('p'), detail());
    s = reduceAutomations(s, ev('automation.step_changed', { run_id: 'r1', step_id: 'ask', attempt: 1, status: 'failed', error: 'boom' }));
    s = reduceAutomations(s, ev('automation.step_changed', { run_id: 'r1', step_id: 'ask', attempt: 2, status: 'pending', resume_at: '2026-09-28T06:01:00.000Z' }));
    expect(s.runs.r1!.steps[0]).toMatchObject({ attempt: 2, status: 'pending', error: null, resume_at: '2026-09-28T06:01:00.000Z', finished_at: null });
    const base = detail();
    const step = base.steps[0]!;
    expect(derivedRunStatus({ ...base, status: 'failed' })).toBe('failed');
    expect(derivedRunStatus({ ...base, steps: [{ ...step, status: 'waiting', resume_at: 'x' }] })).toBe('running'); // a Wait is not the user
    expect(derivedRunStatus({ ...base, steps: [{ ...step, status: 'waiting', gate: { tool: 'skill_run', subject: 's', reason: 'r' } }] })).toBe('waiting');
  });

  it('says when to refetch', () => {
    expect(affectsAutomations(ev('automation.trigger_skipped', { automation_id: 'a1', trigger_index: 0, due_at: 'x', reason: 'missed' }))).toBe(true);
    expect(affectsAutomations(ev('message.user', { text: 'x' }))).toBe(false);
    const run = detail({ steps: [{ ...detail().steps[0]!, agent_id: 'ag1', status: 'running' }] });
    expect(affectsRun(ev('approval.requested', { approval_id: 'x' }, { agent_id: 'ag1' }), run)).toBe(true);
    expect(affectsRun(ev('approval.resolved', { approval_id: 'x' }, { agent_id: 'other' }), run)).toBe(false);
  });
});
