import { describe, expect, it } from 'vitest';
import type { AttentionItem } from '@desk/protocol';
import { automationTarget, stripWho } from './strips';

const item = (over: Partial<AttentionItem>): AttentionItem => ({
  id: 'x',
  kind: 'automation_failed',
  project_id: 'p',
  project_name: 'Tax 2026',
  agent_id: null,
  title: 'Weekly digest failed',
  detail: '',
  created_at: '2026-09-28T06:00:00.000Z',
  ref: { automation_id: 'a1', run_id: 'r14' },
  ...over,
});
const none = () => null;

describe('automation strips', () => {
  it('names the automation, read from the daemon’s titles', () => {
    expect(stripWho(item({}), none)).toEqual({ label: 'Automation', name: 'Weekly digest', tag: 'failed' });
    expect(stripWho(item({ kind: 'automation_ask', title: 'Weekly digest: Publish "Robots"?', ref: { automation_id: 'a1', run_id: 'r14', step_id: 'ok' } }), none)).toEqual({ label: 'Automation', name: 'Weekly digest', tag: 'question' });
    expect(stripWho(item({ kind: 'automation_ask', title: 'Weekly digest · Fetch pages wants to run digest/fetch.py robots', ref: { automation_id: 'a1', run_id: 'r14', step_id: 'fetch' } }), none)).toEqual({ label: 'Automation', name: 'Weekly digest', tag: 'script' });
    expect(stripWho(item({ kind: 'automation_enable_request', title: 'Desk proposes turning on Weekly digest', ref: { automation_id: 'a1' } }), none)).toEqual({ label: 'Automation', name: 'Weekly digest', tag: 'turn on?' });
    expect(stripWho(item({ kind: 'automation_grants_suspended', title: 'Weekly digest changed: its grants are suspended', ref: { automation_id: 'a1' } }), none)).toEqual({ label: 'Automation', name: 'Weekly digest', tag: 'grants' });
    expect(stripWho(item({ kind: 'approval', agent_id: 'ag1', title: 'Weekly digest · Summarise wants to run bash', ref: { approval_id: 'ap9', automation_id: 'a1', run_id: 'r14', step_id: 'sum' } }), none)).toEqual({ label: 'Automation', name: 'Weekly digest', tag: 'bash' });
    expect(stripWho(item({ kind: 'approval', agent_id: 't', title: 'Signup wants to run bash', ref: { approval_id: 'a1', thread_id: 't' } }), () => 'Signup')).toEqual({ label: 'Thread', name: 'Signup', tag: 'bash' });
  });

  it('opens an item’s run, its grants or the automation', () => {
    expect(automationTarget(item({}))).toEqual({ name: 'project', id: 'p', tab: 'automations', automationId: 'a1', view: 'runs', runId: 'r14' });
    expect(automationTarget(item({ kind: 'approval', ref: { approval_id: 'ap9', automation_id: 'a1', run_id: 'r14', step_id: 'sum' } }))).toMatchObject({ view: 'runs', runId: 'r14' });
    expect(automationTarget(item({ kind: 'automation_grants_suspended', ref: { automation_id: 'a1' } }))).toEqual({ name: 'project', id: 'p', tab: 'automations', automationId: 'a1', view: 'grants' });
    expect(automationTarget(item({ kind: 'automation_enable_request', ref: { automation_id: 'a1' } }))).toEqual({ name: 'project', id: 'p', tab: 'automations', automationId: 'a1', view: 'design' });
    expect(automationTarget(item({ kind: 'approval', ref: { approval_id: 'a1', thread_id: 't' } }))).toBeNull();
  });
});
