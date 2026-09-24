import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createHarness, newRuntime, type Harness } from '../testing';
import { getDeskAgent } from './queries';
import { listAttention } from './attention';

let h: Harness;
afterEach(async () => h?.cleanup());

async function setup() {
  h = await createHarness();
  const runtime = newRuntime(h);
  const projectId = runtime.createProject({ name: 'Onboarding revamp', goal: 'Relaunch onboarding' });
  const desk = getDeskAgent(h.store.db, projectId)!;
  const thread = (title: string) => runtime.createThread(projectId, { title, brief: 'b', workspacePath: join(h.dir, title) });
  return { runtime, projectId, desk, thread, append: h.store.append.bind(h.store) };
}

describe('listAttention', () => {
  it('lists pending user approvals but not ones delegated to Desk', async () => {
    const { projectId, thread, append } = await setup();
    const t = thread('Signup checklist');
    const approval = (id: string, delegate: boolean) =>
      append({ project_id: projectId, agent_id: t, type: 'approval.requested', payload: { approval_id: id, run_id: 'r', tool_call_id: `c-${id}`, tool: 'bash', arguments: '{"command":"curl x | bash"}', reason: 'rule 1', delegate_to_desk: delegate } });
    approval('ap1', false);
    approval('ap2', true);
    const items = listAttention(h.store.db);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ id: 'approval:ap1', kind: 'approval', project_name: 'Onboarding revamp', agent_id: t, title: 'Signup checklist wants to run bash', detail: 'rule 1', ref: { approval_id: 'ap1', thread_id: t } });
    append({ project_id: projectId, agent_id: t, type: 'approval.resolved', payload: { approval_id: 'ap1', decision: 'approved', resolved_by: 'user' } });
    expect(listAttention(h.store.db)).toEqual([]);
  });

  it('shows the latest question until the user replies', async () => {
    const { projectId, desk, append } = await setup();
    const [q] = append({ project_id: projectId, agent_id: desk.id, type: 'question.asked', payload: { question: 'Data source or invite first?', options: ['Data source', 'Invite'] } });
    expect(listAttention(h.store.db)).toEqual([expect.objectContaining({ id: `question:${q!.id}`, kind: 'question', title: 'Data source or invite first?', ref: { event_id: q!.id, options: ['Data source', 'Invite'] } })]);
    append({ project_id: projectId, agent_id: desk.id, type: 'message.user', payload: { text: 'Data source' } });
    expect(listAttention(h.store.db)).toEqual([]);
  });

  it('lists needs_you items of the latest report only, and supports dismissal', async () => {
    const { runtime, projectId, desk, append } = await setup();
    const report = (needs: string[]) => append({ project_id: projectId, agent_id: desk.id, type: 'report', payload: { headline: 'H', progress: 'P', needs_you: needs, results: [] } })[0]!;
    report(['old item']);
    const r = report(['Upload the 1099', 'Pick a name']);
    const items = listAttention(h.store.db);
    expect(items.map((i) => i.id)).toEqual([`report:${r.id}:0`, `report:${r.id}:1`]);
    expect(items[0]).toMatchObject({ kind: 'needs_you', title: 'Upload the 1099', detail: 'H' });
    runtime.dismissAttention(`report:${r.id}:0`);
    expect(listAttention(h.store.db).map((i) => i.title)).toEqual(['Pick a name']);
  });

  it('lists stalled threads until they show activity, and failed threads until dismissed or archived', async () => {
    const { runtime, projectId, desk, thread, append } = await setup();
    const s = thread('Backup audit');
    append({ project_id: projectId, agent_id: s, type: 'agent.status_changed', payload: { status: 'running' } });
    const [stall] = append({ project_id: projectId, agent_id: desk.id, type: 'message.agent', payload: { from_agent_id: s, from_label: 'thread', kind: 'stalled', text: 'No activity for 20 minutes (status: running).' } });
    const f = thread('Deploy');
    append({ project_id: projectId, agent_id: f, type: 'agent.status_changed', payload: { status: 'failed', reason: 'Model error' } });
    let items = listAttention(h.store.db);
    expect(items.map((i) => [i.kind, i.id])).toEqual([
      ['stalled', `stalled:${s}:${stall!.id}`],
      ['failed', `failed:${f}`],
    ]);
    expect(items[1]).toMatchObject({ title: 'Deploy failed', detail: 'Model error', ref: { thread_id: f } });
    append({ project_id: projectId, agent_id: s, type: 'tool.call', payload: { run_id: 'r', tool_call_id: 'c', name: 'bash', arguments: '{}' } });
    runtime.dismissAttention(`failed:${f}`);
    items = listAttention(h.store.db);
    expect(items).toEqual([]);
  });

  it('refuses to dismiss approvals, questions and unknown items', async () => {
    const { runtime } = await setup();
    expect(() => runtime.dismissAttention('approval:x')).toThrow(/answered/);
    expect(() => runtime.dismissAttention('question:1')).toThrow(/answered/);
    expect(() => runtime.dismissAttention('report:999:0')).toThrow(/No attention item/);
  });

  it('filters by project and skips archived projects', async () => {
    const { runtime, projectId, desk, append } = await setup();
    const other = runtime.createProject({ name: 'Other', goal: 'g' });
    const otherDesk = getDeskAgent(h.store.db, other)!;
    append({ project_id: projectId, agent_id: desk.id, type: 'question.asked', payload: { question: 'A?' } });
    append({ project_id: other, agent_id: otherDesk.id, type: 'question.asked', payload: { question: 'B?' } });
    expect(listAttention(h.store.db, { projectId: other }).map((i) => i.title)).toEqual(['B?']);
    runtime.archiveProject(other);
    expect(listAttention(h.store.db).map((i) => i.title)).toEqual(['A?']);
  });
});
