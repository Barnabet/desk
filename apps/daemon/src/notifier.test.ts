import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createHarness, newRuntime, type Harness } from '@desk/core/testing';
import { getDeskAgent } from '@desk/core';
import { startNotifier, type Notification } from './notifier';

let h: Harness;
afterEach(async () => h?.cleanup());

describe('notifier', () => {
  it('posts for approvals, questions, reports that need you, stalls and failures, only when enabled and not suppressed', async () => {
    h = await createHarness();
    const runtime = newRuntime(h);
    const p = runtime.createProject({ name: 'Onboarding revamp', goal: 'g' });
    const desk = getDeskAgent(h.store.db, p)!;
    const t = runtime.createThread(p, { title: 'Signup checklist', brief: 'b', workspacePath: join(h.dir, 't') });
    const posted: Notification[] = [];
    let enabled = true;
    let suppressed = false;
    const stop = startNotifier({ store: h.store, enabled: () => enabled, suppressed: () => suppressed, post: (n) => posted.push(n), onError: (e) => { throw e; } });
    const a = h.store.append.bind(h.store);
    const approval = (id: string, delegate: boolean) =>
      a({ project_id: p, agent_id: t, type: 'approval.requested', payload: { approval_id: id, run_id: 'r', tool_call_id: id, tool: 'bash', arguments: '{}', reason: 'r', delegate_to_desk: delegate } });

    approval('a1', false);
    approval('a2', true);
    a({ project_id: p, agent_id: desk.id, type: 'question.asked', payload: { question: 'Data source first?' } });
    a({ project_id: p, agent_id: desk.id, type: 'report', payload: { headline: 'Research is in', progress: '', needs_you: ['x', 'y'], results: [] } });
    a({ project_id: p, agent_id: desk.id, type: 'report', payload: { headline: 'Quiet', progress: '', needs_you: [], results: [] } });
    a({ project_id: p, agent_id: desk.id, type: 'message.agent', payload: { from_agent_id: t, from_label: 'l', kind: 'stalled', text: 'No activity' } });
    a({ project_id: p, agent_id: t, type: 'agent.status_changed', payload: { status: 'failed', reason: 'Model error' } });
    expect(posted).toEqual([
      { title: 'Desk · Onboarding revamp', body: 'Signup checklist wants to run bash' },
      { title: 'Desk · Onboarding revamp', body: 'Desk asks: Data source first?' },
      { title: 'Desk · Onboarding revamp', body: 'Research is in (2 need you)' },
      { title: 'Desk · Onboarding revamp', body: 'Signup checklist has stalled' },
      { title: 'Desk · Onboarding revamp', body: 'Signup checklist failed: Model error' },
    ]);

    suppressed = true;
    approval('a3', false);
    suppressed = false;
    enabled = false;
    approval('a4', false);
    expect(posted).toHaveLength(5);
    stop();
  });
});
