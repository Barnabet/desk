import { describe, expect, it } from 'vitest';
import { AgentRole, AttentionKind, AutomationAnswerRequest, AutomationRunRequest, EventBody, ResolveApprovalRequest } from './index';

const def = { title: 'T', steps: [{ id: 'w', title: 'W', kind: 'wait', minutes: 1 }] };
const ev = (type: string, payload: unknown) => EventBody.safeParse({ type, payload });

describe('automation events', () => {
  it('adds the step role and the automation message kind', () => {
    expect(AgentRole.parse('step')).toBe('step');
    expect(ev('message.agent', { from_agent_id: 'a', from_label: 'Automation: T', kind: 'automation', text: 'hi' }).success).toBe(true);
  });

  it('parses the definition events', () => {
    expect(ev('automation.saved', { automation_id: 'a', name: 'digest', version: 1, definition: def, origin: 'user', change_note: '', via: 'editor' }).success).toBe(true);
    expect(ev('automation.saved', { automation_id: 'a', name: 'Digest', version: 1, definition: def, origin: 'user', change_note: '', via: 'editor' }).success).toBe(false);
    expect(ev('automation.saved', { automation_id: 'a', name: 'digest', version: 1, definition: def, origin: 'schedule', change_note: '', via: 'editor' }).success).toBe(false);
    expect(ev('automation.switched', { automation_id: 'a', enabled: true, by: 'user' }).success).toBe(true);
    expect(ev('automation.grants_set', { automation_id: 'a', grants: [{ tool: 'web_fetch', action: 'allow' }], reason: 'remembered' }).success).toBe(true);
    expect(ev('automation.enable_requested', { automation_id: 'a', note: 'ready', proposed_grants: [] }).success).toBe(true);
    expect(ev('automation.layout_saved', { automation_id: 'a', layout: { start: { x: 0, y: 0 }, w: { x: 10, y: 80 } } }).success).toBe(true);
    expect(ev('automation.deleted', { automation_id: 'a', origin: 'agent:d' }).success).toBe(true);
  });

  it('parses the run events', () => {
    const started = { run_id: 'r', automation_id: 'a', version: 2, trigger: 'schedule', test: false, inputs: { n: 1 }, by: 'schedule', trigger_index: 0, due_at: '2026-09-28T06:00:00.000Z', caught_up: 2, deadline_at: '2026-09-29T06:00:00.000Z' };
    expect(ev('automation.run_started', started).success).toBe(true);
    expect(ev('automation.run_started', { ...started, by: 'desk' }).success).toBe(false);
    expect(ev('automation.step_changed', { run_id: 'r', step_id: 'fetch', attempt: 1, status: 'waiting', gate: { tool: 'skill_run', subject: 'x/y.py', reason: 'Policy rule → ask' } }).success).toBe(true);
    expect(ev('automation.step_changed', { run_id: 'r', step_id: 'fetch', attempt: 0, status: 'running' }).success).toBe(false);
    expect(ev('automation.run_finished', { run_id: 'r', status: 'failed', summary: 'Fetch failed', reason: 'exit 1' }).success).toBe(true);
    expect(ev('automation.run_finished', { run_id: 'r', status: 'running', summary: '' }).success).toBe(false);
    expect(ev('automation.trigger_skipped', { automation_id: 'a', trigger_index: 0, due_at: '2026-09-28T06:00:00.000Z', reason: 'missed' }).success).toBe(true);
  });

  it('accepts automation origins, step agents and approval memory', () => {
    expect(ev('artifact.published', { artifact_id: 'x', path: 'automations/d/2026-09-28 0800/digest.md', title: 'd', kind: 'file', origin: 'automation:r1', description: '' }).success).toBe(true);
    expect(
      EventBody.safeParse({
        type: 'agent.created',
        payload: { role: 'step', model: 'm', title: 'Summarise', brief: 'b', workspace_path: '/w', parent_id: null, automation: { run_id: 'r', step_id: 'sum' } },
      }).success,
    ).toBe(true);
    expect(ResolveApprovalRequest.parse({ decision: 'approved', remember: true }).remember).toBe(true);
    expect(AttentionKind.parse('automation_ask')).toBe('automation_ask');
    expect(AutomationRunRequest.parse({})).toEqual({ inputs: {}, test: false });
    expect(AutomationAnswerRequest.safeParse({ decision: 'maybe' }).success).toBe(false);
  });
});
