import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { text } from '@desk/fake-model';
import { askDef, automationHarness, createHarness, FAKE_MODEL, newRuntime, type Harness } from '@desk/core/testing';
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

  it('posts for automation questions, gates, failures, notify successes and turn-on requests', async () => {
    const r = await automationHarness({ script: () => text('ok') });
    h = r.h;
    const { rt, projectId } = r;
    const posted: Notification[] = [];
    const stop = startNotifier({ store: h.store, enabled: () => true, suppressed: () => false, post: (n) => posted.push(n), onError: (e) => { throw e; } });
    rt.saveSkill({ scope: 'project', projectId, name: 'x', description: 'test skill', instructions: 'Run', files: [{ path: 'scripts/x.py', content: 'print(1)' }] }, { projectId });
    let fail = false;
    rt.engine.register('script', ({ run, step }) => rt.engine.resolveStep(run.id, step.id, fail ? { status: 'failed', error: 'boom' } : { status: 'succeeded', route: null, outputs: {}, summary: 'All fetched' }));
    const create = (name: string, def: object) => rt.automations.create(projectId, name, def, { origin: 'user', via: 'editor' }).automation.id;
    const start = (id: string, test = false) => rt.engine.startRun(id, { trigger: test ? 'test' : 'manual', test, inputs: {}, by: 'user' });
    const title = 'Desk · P';

    const ask = create('ask', askDef({ title: 'Publish digest' }));
    await start(ask);
    const fetch = create('fetch', { title: 'Fetch', steps: [{ id: 's', title: 'S', kind: 'script', skill: 'x', script: 'x.py' }] });
    await start(fetch); // after_run notify (default): posts the summary
    create('quiet', { title: 'Quiet', after_run: 'silent', steps: [{ id: 's', title: 'S', kind: 'script', skill: 'x', script: 'x.py' }] });
    await start(rt.automations.requireByName(projectId, 'quiet').id);
    fail = true;
    await start(fetch);
    await start(fetch, true); // test runs never notify
    rt.automations.requestEnable(fetch, 'desk-agent', 'Ready');
    expect(posted).toEqual([
      { title, body: 'Publish digest: Publish?' },
      { title, body: 'Fetch: All fetched' },
      { title, body: 'Fetch failed: s failed: boom' },
      { title, body: 'Desk proposes turning on Fetch' },
    ]);
    stop();
  });

  it('posts once when a project pauses its automatic wakes, and not for other notices', async () => {
    h = await createHarness({ script: () => text('Done for now.') });
    const runtime = newRuntime(h, { wakeBudget: 1 });
    const p = runtime.createProject({ name: 'Onboarding revamp', goal: 'g', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id } });
    const desk = getDeskAgent(h.store.db, p)!;
    const t = runtime.createThread(p, { title: 'Signup checklist', brief: 'b', workspacePath: join(h.dir, 't') });
    const posted: Notification[] = [];
    const stop = startNotifier({ store: h.store, enabled: () => true, suppressed: () => false, post: (n) => posted.push(n), onError: (e) => { throw e; } });
    h.store.append({ project_id: p, agent_id: null, type: 'system.notice', payload: { level: 'error', code: 'proxy_down', message: 'The model proxy is unreachable.' } });

    // Desk's note runs the thread: the one agent-triggered wake of the hour. The thread's update to Desk then pauses
    // the project, and the later wakes are held without a second notice.
    runtime.deliver(desk.id, t, 'note', 'Check the signup copy.');
    await runtime.whenIdle();
    runtime.deliver(desk.id, t, 'note', 'And the footer.');
    runtime.deliver(t, desk.id, 'update', 'Halfway.');
    await runtime.whenIdle();
    expect(h.store.list({ projectId: p, types: ['run.started'] })).toHaveLength(1);
    expect(posted).toEqual([{ title: 'Desk · Onboarding revamp', body: 'Agents are paused: too many automatic wakes this hour' }]);
    stop();
  });
});
