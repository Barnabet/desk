import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { text } from '@desk/fake-model';
import type { EventInput } from '@desk/protocol';
import { getAgent, listApprovals } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';

let h: Harness;
afterEach(async () => h?.cleanup());

/** Writes the events a daemon killed mid-tool-execution would leave behind. */
function simulateCrash(projectId: string, agentId: string, opts: { withApproval?: boolean } = {}) {
  const base = { project_id: projectId, agent_id: agentId };
  const events: EventInput[] = [
    { ...base, type: 'message.user', payload: { text: 'go' } },
    { ...base, type: 'run.started', payload: { run_id: 'r1', model: FAKE_MODEL.id } },
    { ...base, type: 'agent.status_changed', payload: { status: 'running' } },
    { ...base, type: 'assistant.message', payload: { run_id: 'r1', content: null, tool_calls: [{ id: 'c1', name: 'list_dir', arguments: '{}' }, { id: 'c2', name: 'bash', arguments: '{"command":"make deploy"}' }] } },
    { ...base, type: 'tool.call', payload: { run_id: 'r1', tool_call_id: 'c1', name: 'list_dir', arguments: '{}' } },
    { ...base, type: 'tool.call', payload: { run_id: 'r1', tool_call_id: 'c2', name: 'bash', arguments: '{"command":"make deploy"}' } },
    { ...base, type: 'tool.result', payload: { run_id: 'r1', tool_call_id: 'c1', name: 'list_dir', status: 'ok', content: 'a.txt' } },
  ];
  if (opts.withApproval) {
    events.push({
      ...base,
      type: 'approval.requested',
      payload: { approval_id: 'ap1', run_id: 'r1', tool_call_id: 'c2', tool: 'bash', arguments: '{"command":"make deploy"}', reason: 'risky', delegate_to_desk: false },
    });
  }
  h.store.append(events);
  const drained = h.store.list({ agentId, types: ['message.user'] }).at(-1)!;
  h.store.append({ ...base, type: 'inbox.drained', payload: { run_id: 'r1', up_to: drained.id } });
}

describe('crash recovery', () => {
  it('marks unfinished tool calls interrupted and resumes the run', async () => {
    h = await createHarness({ script: [text('I will verify the deploy state before retrying.')] });
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const t = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w'), model: FAKE_MODEL.id, parentId: null });
    simulateCrash(projectId, t);

    const next = newRuntime(h);
    expect(next.recover()).toEqual([t]);
    await next.whenIdle();

    const results = h.store.list({ agentId: t, types: ['tool.result'] }).map((e) => (e.type === 'tool.result' ? [e.payload.tool_call_id, e.payload.status] : []));
    expect(results).toEqual([
      ['c1', 'ok'],
      ['c2', 'interrupted'],
    ]);
    const fin = h.store.list({ agentId: t, types: ['run.finished'] })[0];
    expect(fin?.type === 'run.finished' && fin.payload).toMatchObject({ run_id: 'r1', reason: 'error', detail: 'daemon_restart' });
    const sent = h.fake.requests[0]!.messages;
    expect(sent.at(-1)).toMatchObject({ role: 'tool', tool_call_id: 'c2', content: expect.stringContaining('verify before retrying') });
    expect(getAgent(h.store.db, t)?.status).toBe('idle');
    expect(h.store.list({ projectId, types: ['system.notice'] })).toHaveLength(1);
  });

  it('marks an approved call cut off by the crash interrupted, then resumes the agent with a pending note', async () => {
    h = await createHarness({ script: [text('I will check whether the deploy ran before retrying.')] });
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const t = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w'), model: FAKE_MODEL.id });
    simulateCrash(projectId, t, { withApproval: true });
    // The run yielded on the approval; the user approved it, and the daemon died while the approved call ran.
    const base = { project_id: projectId, agent_id: t };
    h.store.append([
      { ...base, type: 'run.finished', payload: { run_id: 'r1', reason: 'yielded', detail: 'Awaiting approval: bash' } },
      { ...base, type: 'agent.status_changed', payload: { status: 'waiting', reason: 'Awaiting approval: bash' } },
      { ...base, type: 'approval.resolved', payload: { approval_id: 'ap1', decision: 'approved', resolved_by: 'user' } },
    ]);
    const deskId = getAgent(h.store.db, t)!.parent_id!;
    h.store.append({ ...base, type: 'message.agent', payload: { from_agent_id: deskId, from_label: 'Desk', kind: 'note', text: 'Deploy to staging only.' } });

    const next = newRuntime(h);
    expect(next.recover()).toContain(t);
    await next.whenIdle();

    const results = h.store.list({ agentId: t, types: ['tool.result'] }).map((e) => (e.type === 'tool.result' ? [e.payload.tool_call_id, e.payload.status] : []));
    expect(results).toEqual([
      ['c1', 'ok'],
      ['c2', 'interrupted'],
    ]);
    const sent = h.fake.requests.find((r) => r.messages.some((m) => m.role === 'tool' && m.tool_call_id === 'c2'))!.messages;
    expect(sent.find((m) => m.role === 'tool' && m.tool_call_id === 'c2')).toMatchObject({ content: expect.stringContaining('verify before retrying') });
    expect(String(sent.at(-1)?.content)).toContain('Deploy to staging only.');
    expect(getAgent(h.store.db, t)?.status).toBe('idle');
    expect(h.store.list({ projectId, types: ['system.notice'] })).toHaveLength(1);
  });

  it('keeps approval-held calls pending and leaves the agent waiting', async () => {
    h = await createHarness();
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const t = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w'), model: FAKE_MODEL.id, parentId: null });
    simulateCrash(projectId, t, { withApproval: true });
    const next = newRuntime(h);
    expect(next.recover()).toEqual([]);
    expect(getAgent(h.store.db, t)?.status).toBe('waiting');
    expect(listApprovals(h.store.db, projectId, 'pending')).toHaveLength(1);
    expect(h.store.list({ agentId: t, types: ['tool.result'] })).toHaveLength(1);
  });
});
