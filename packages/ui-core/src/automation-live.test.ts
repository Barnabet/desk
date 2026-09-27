import { describe, expect, it } from 'vitest';
import { ev } from '@desk/client/testing';
import { automationDetail, automationSummary, runDetail } from './testing/automations';
import { automationDetailRefetch, automationListRefetch, automationRunsRefetch, eventsAfter, reduceAutomationList, reduceRunDetail, runDetailRefetch } from './automation-live';

describe('live rules', () => {
  it('finds the events after an id in an id-ordered log', () => {
    const log = [1, 3, 4].map((id) => ev(id, 'automation.layout_saved', { automation_id: 'a1', layout: {} }));
    expect(eventsAfter(log, 0).map((e) => e.id)).toEqual([1, 3, 4]);
    expect(eventsAfter(log, 3).map((e) => e.id)).toEqual([4]);
    expect(eventsAfter(log, 9)).toEqual([]);
  });

  it('folds list events, and reloads the list for anything but a layout', () => {
    const switched = ev(1, 'automation.switched', { automation_id: 'a1', enabled: false, by: 'user' });
    expect(reduceAutomationList([automationSummary()], switched)[0]!.enabled).toBe(false);
    expect(automationListRefetch(switched)).toBe(true);
    expect(automationListRefetch(ev(2, 'automation.layout_saved', { automation_id: 'a1', layout: {} }))).toBe(false);
    expect(automationListRefetch(ev(3, 'plan.updated', { items: [] }))).toBe(false);
  });

  it('reloads a detail for its own changes and its last run’s end', () => {
    const refetch = automationDetailRefetch('a1');
    const d = automationDetail({ last_run: { id: 'r9', number: 9, status: 'running', trigger: 'manual', test: false, started_at: 't', finished_at: null, summary: null, waiting_on: null } });
    expect(refetch(ev(1, 'automation.switched', { automation_id: 'a1', enabled: true, by: 'user' }), d)).toBe(true);
    expect(refetch(ev(2, 'automation.switched', { automation_id: 'a2', enabled: true, by: 'user' }), d)).toBe(false);
    expect(refetch(ev(3, 'automation.layout_saved', { automation_id: 'a1', layout: {} }), d)).toBe(false);
    expect(refetch(ev(4, 'automation.run_finished', { run_id: 'r9', status: 'succeeded', summary: 'ok' }), d)).toBe(true);
  });

  it('reloads runs when one starts or is skipped, and when a listed run changes', () => {
    const refetch = automationRunsRefetch('a1');
    const list = [{ kind: 'run' as const, run: runDetail() }];
    expect(refetch(ev(1, 'automation.trigger_skipped', { automation_id: 'a1', trigger_index: 0, due_at: '2026-09-28T06:00:00.000Z', reason: 'missed' }), list)).toBe(true);
    expect(refetch(ev(2, 'automation.step_changed', { run_id: 'r14', step_id: 'ok', attempt: 1, status: 'running' }), list)).toBe(true);
    expect(refetch(ev(3, 'automation.step_changed', { run_id: 'other', step_id: 'ok', attempt: 1, status: 'running' }), list)).toBe(false);
  });

  it('folds step changes into a run, and reloads it for its agents’ approvals and its end', () => {
    const run = runDetail();
    const next = reduceRunDetail(run, ev(1, 'automation.step_changed', { run_id: 'r14', step_id: 'sum', attempt: 1, status: 'succeeded', summary: 'done' }));
    expect(next.steps.find((s) => s.step_id === 'sum')).toMatchObject({ status: 'succeeded', summary: 'done' });
    const refetch = runDetailRefetch('r14');
    expect(refetch(ev(2, 'approval.requested', { approval_id: 'ap', run_id: 'x', tool_call_id: 't', tool: 'bash', arguments: '{}', reason: 'r', delegate_to_desk: false }, { agent: 'ag1' }), run)).toBe(true);
    expect(refetch(ev(3, 'automation.run_finished', { run_id: 'r14', status: 'succeeded', summary: 'ok' }), run)).toBe(true);
    expect(refetch(ev(4, 'automation.run_finished', { run_id: 'r1', status: 'succeeded', summary: 'ok' }), run)).toBe(false);
  });
});
