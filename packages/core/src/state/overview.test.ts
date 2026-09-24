import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createHarness, newRuntime, type Harness } from '../testing';
import { getDeskAgent } from './queries';
import { listOverview } from './overview';

let h: Harness;
afterEach(async () => h?.cleanup());

describe('listOverview', () => {
  it('summarises projects with threads, activity, report, plan and attention', async () => {
    h = await createHarness();
    const runtime = newRuntime(h);
    const p = runtime.createProject({ name: 'Onboarding revamp', goal: 'Relaunch' });
    const desk = getDeskAgent(h.store.db, p)!;
    const t = runtime.createThread(p, { title: 'Funnel analysis', brief: 'b', workspacePath: join(h.dir, 'f') });
    const append = h.store.append.bind(h.store);
    append({ project_id: p, agent_id: t, type: 'agent.status_changed', payload: { status: 'running' } });
    append({ project_id: p, agent_id: t, type: 'tool.call', payload: { run_id: 'r', tool_call_id: 'c1', name: 'bash', arguments: JSON.stringify({ command: 'python3 funnel.py --by-week' }) } });
    append({ project_id: p, agent_id: desk.id, type: 'report', payload: { headline: 'Research is in', progress: '', needs_you: ['Approve bun'], results: [] } });
    append({
      project_id: p,
      agent_id: desk.id,
      type: 'plan.updated',
      payload: { items: [
        { id: '1', title: 'A', status: 'done', thread_ids: [], notes: '' },
        { id: '2', title: 'B', status: 'in_progress', thread_ids: [t], notes: '' },
        { id: '3', title: 'C', status: 'dropped', thread_ids: [], notes: '' },
      ] },
    });

    const [s] = listOverview(h.store.db);
    expect(s).toMatchObject({
      project: { id: p, name: 'Onboarding revamp', goal: 'Relaunch' },
      desk_status: 'idle',
      latest_report: { headline: 'Research is in' },
      plan_progress: { done: 1, total: 2 },
      attention_count: 1,
    });
    expect(s!.threads).toEqual([expect.objectContaining({ id: t, title: 'Funnel analysis', status: 'running', activity: 'bash · python3 funnel.py --by-week', skills: [], review_round: 0 })]);

    append({ project_id: p, agent_id: t, type: 'tool.result', payload: { run_id: 'r', tool_call_id: 'c1', name: 'bash', status: 'ok', content: 'done' } });
    expect(listOverview(h.store.db)[0]!.threads[0]!.activity).toBeNull();
  });

  it('shows the waiting reason and skips archived threads and projects', async () => {
    h = await createHarness();
    const runtime = newRuntime(h);
    const p = runtime.createProject({ name: 'P', goal: 'g' });
    const t = runtime.createThread(p, { title: 'W', brief: 'b', workspacePath: join(h.dir, 'w') });
    h.store.append({ project_id: p, agent_id: t, type: 'agent.status_changed', payload: { status: 'waiting', reason: 'Waiting for approval' } });
    expect(listOverview(h.store.db)[0]!.threads[0]).toMatchObject({ status: 'waiting', reason: 'Waiting for approval' });
    h.store.append({ project_id: p, agent_id: t, type: 'agent.archived', payload: {} });
    expect(listOverview(h.store.db)[0]!.threads).toEqual([]);
    runtime.archiveProject(p);
    expect(listOverview(h.store.db)).toEqual([]);
  });
});
