import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { z } from 'zod';
import { call, text, tools } from '@desk/fake-model';
import { getAgent, listApprovals } from '../state/queries';
import { createHarness, FAKE_MODEL, noSleep, type Harness } from '../testing/harness';
import { bashTool } from '../tools/bash';
import { fileTools } from '../tools/fs';
import { completeTool } from '../tools/thread';
import { defineTool, type Tool } from '../tools/types';
import { Runtime } from './runtime';

let h: Harness;
afterEach(async () => h?.cleanup());

// A gated tool that the default policy classifies as "ask" (open_pr).
const openPr = defineTool({
  name: 'open_pr',
  description: 'Open a PR',
  input: z.object({ title: z.string() }),
  gate: { subject: () => ({}), unmatched: 'ask' },
  async execute({ title }, ctx) {
    const { writeFile } = await import('node:fs/promises');
    await writeFile(join(ctx.workspace, 'PR_OPENED'), title);
    return `Opened PR "${title}"`;
  },
});
// A gated tool the default policy denies for non-desk branches (git_push).
const gitPush = defineTool({
  name: 'git_push',
  description: 'Push',
  input: z.object({ branch: z.string() }),
  gate: { subject: (i) => ({ branch: i.branch }), unmatched: 'ask' },
  async execute() {
    return 'pushed';
  },
});
const testTools: Tool[] = [...fileTools, bashTool, completeTool, openPr, gitPush];

async function setup(script: NonNullable<Parameters<typeof createHarness>[0]>['script'], sandboxAvailable = true) {
  h = await createHarness({ script });
  const rt = new Runtime({ store: h.store, adapter: h.adapter, models: h.models, dataDir: h.dir, retry: noSleep, toolsFor: () => testTools, sandboxAvailable });
  const projectId = rt.createProject({ name: 'P', goal: 'G' });
  const workspace = join(h.dir, 'ws');
  const agentId = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: workspace, model: FAKE_MODEL.id, parentId: null });
  return { rt, projectId, agentId, workspace };
}
const results = (agentId: string) =>
  h.store.list({ agentId, types: ['tool.result'] }).map((e) => (e.type === 'tool.result' ? { name: e.payload.name, status: e.payload.status, content: e.payload.content } : null));

describe('approvals', () => {
  it('requests approval, still runs parallel safe calls, and waits', async () => {
    const { rt, projectId, agentId, workspace } = await setup([
      tools(call('open_pr', { title: 'Fix' }, 'c1'), call('write_file', { path: 'notes.md', content: 'n' }, 'c2')),
    ]);
    rt.sendMessage(agentId, 'go');
    await rt.whenIdle();
    expect(getAgent(h.store.db, agentId)?.status).toBe('waiting');
    expect(existsSync(join(workspace, 'notes.md'))).toBe(true);
    expect(existsSync(join(workspace, 'PR_OPENED'))).toBe(false);
    const pending = listApprovals(h.store.db, projectId, 'pending');
    expect(pending).toHaveLength(1);
    expect(pending[0]).toMatchObject({ tool: 'open_pr', agent_id: agentId, delegate_to_desk: false });
    expect(h.fake.requests).toHaveLength(1);
  });

  it('executes the call on approval and continues the run', async () => {
    const { rt, projectId, agentId, workspace } = await setup([tools(call('open_pr', { title: 'Fix' }, 'c1')), text('PR is open')]);
    rt.sendMessage(agentId, 'go');
    await rt.whenIdle();
    const [ap] = listApprovals(h.store.db, projectId, 'pending');
    await rt.resolveApproval(ap!.id, 'approved');
    await rt.whenIdle();
    expect(existsSync(join(workspace, 'PR_OPENED'))).toBe(true);
    expect(results(agentId)).toEqual([{ name: 'open_pr', status: 'ok', content: 'Opened PR "Fix"' }]);
    expect(h.fake.requests[1]!.messages.at(-1)).toMatchObject({ role: 'tool', tool_call_id: 'c1', content: 'Opened PR "Fix"' });
    expect(getAgent(h.store.db, agentId)?.status).toBe('idle');
    expect(listApprovals(h.store.db, projectId, 'approved')).toHaveLength(1);
  });

  it('returns a denial with the note to the model', async () => {
    const { rt, projectId, agentId, workspace } = await setup([tools(call('open_pr', { title: 'Fix' }, 'c1')), text('ok, no PR')]);
    rt.sendMessage(agentId, 'go');
    await rt.whenIdle();
    const [ap] = listApprovals(h.store.db, projectId, 'pending');
    await rt.resolveApproval(ap!.id, 'denied', { note: 'not yet' });
    await rt.whenIdle();
    expect(existsSync(join(workspace, 'PR_OPENED'))).toBe(false);
    expect(results(agentId)[0]).toMatchObject({ status: 'denied', content: expect.stringContaining('not yet') });
    expect(h.fake.requests).toHaveLength(2);
  });

  it('queues messages while an approval is pending', async () => {
    const { rt, projectId, agentId } = await setup([tools(call('open_pr', { title: 'Fix' }, 'c1')), text('done')]);
    rt.sendMessage(agentId, 'go');
    await rt.whenIdle();
    rt.sendMessage(agentId, 'also mention the changelog');
    await rt.whenIdle();
    expect(h.fake.requests).toHaveLength(1);
    const [ap] = listApprovals(h.store.db, projectId, 'pending');
    await rt.resolveApproval(ap!.id, 'approved');
    await rt.whenIdle();
    const last = h.fake.requests[1]!.messages;
    expect(last.at(-2)).toMatchObject({ role: 'tool', tool_call_id: 'c1' });
    expect(last.at(-1)).toEqual({ role: 'user', content: 'also mention the changelog' });
  });

  it('denies by rule without raising an approval', async () => {
    const { rt, projectId, agentId } = await setup([tools(call('git_push', { branch: 'main' }, 'c1')), text('cannot push to main')]);
    rt.sendMessage(agentId, 'go');
    await rt.whenIdle();
    expect(listApprovals(h.store.db, projectId)).toHaveLength(0);
    expect(results(agentId)[0]).toMatchObject({ status: 'denied' });
    expect(h.fake.requests).toHaveLength(2);
  });

  it('stopping a waiting agent denies its approvals', async () => {
    const { rt, projectId, agentId } = await setup([tools(call('open_pr', { title: 'Fix' }, 'c1'))]);
    rt.sendMessage(agentId, 'go');
    await rt.whenIdle();
    rt.stop(agentId);
    expect(getAgent(h.store.db, agentId)?.status).toBe('cancelled');
    expect(listApprovals(h.store.db, projectId, 'denied')[0]).toMatchObject({ resolved_by: 'system' });
    expect(results(agentId)[0]).toMatchObject({ status: 'denied' });
  });

  it('rejects resolving twice', async () => {
    const { rt, projectId, agentId } = await setup([tools(call('open_pr', { title: 'Fix' }, 'c1')), text('ok')]);
    rt.sendMessage(agentId, 'go');
    await rt.whenIdle();
    const [ap] = listApprovals(h.store.db, projectId, 'pending');
    await rt.resolveApproval(ap!.id, 'denied');
    await rt.whenIdle();
    await expect(rt.resolveApproval(ap!.id, 'approved')).rejects.toThrow(/already resolved/);
  });

  it('requires approval for shell when the sandbox is unavailable', async () => {
    const { rt, projectId, agentId } = await setup([tools(call('bash', { command: 'ls' }, 'c1'))], false);
    rt.sendMessage(agentId, 'go');
    await rt.whenIdle();
    expect(listApprovals(h.store.db, projectId, 'pending')[0]).toMatchObject({ tool: 'bash', reason: expect.stringContaining('sandbox') });
  });
});
