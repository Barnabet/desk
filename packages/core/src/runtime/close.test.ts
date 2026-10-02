import { existsSync } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { afterEach, expect, it } from 'vitest';
import { call, text, tools } from '@desk/fake-model';
import { newId } from '../ids';
import { getAgent, getDeskAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';
import { closeThreadTool } from '../tools/desk';
import { deskToolsFor, threadToolsFor, stepToolsFor } from './toolsets';

let h: Harness;
afterEach(async () => h?.cleanup());
async function setup() {
  h = await createHarness({ script: () => text('ok') });
  const rt = newRuntime(h);
  const project = rt.createProject({ name: 'P', goal: 'G' });
  const desk = getDeskAgent(h.store.db, project)!;
  const id = newId();
  const workspace = join(h.dir, 'workspaces', id);
  await mkdir(workspace, { recursive: true });
  await writeFile(join(workspace, 'scratch'), 'disposable');
  h.store.append([
    { project_id: project, agent_id: id, type: 'agent.created', payload: { role: 'thread', model: FAKE_MODEL.id, title: 'Finished', brief: 'G', workspace_path: workspace, parent_id: desk.id } },
    { project_id: project, agent_id: id, type: 'agent.result', payload: { summary: 'Finished', artifacts: [] } },
    { project_id: project, agent_id: id, type: 'agent.status_changed', payload: { status: 'done' } },
  ]);
  return { rt, project, desk, id, workspace };
}

it('previews, closes and keeps task history; retries do not repeat deletion', async () => {
  const { rt, desk, id, workspace } = await setup();
  expect(JSON.parse(await rt.closeThread(desk.id, id, false))).toMatchObject({ action: 'preview', bytes: 10 });
  expect(existsSync(workspace)).toBe(true);
  expect(getAgent(h.store.db, id)?.archived_at).toBeNull();
  expect(await rt.closeThread(desk.id, id, true)).toContain('10 bytes');
  expect(existsSync(workspace)).toBe(false);
  expect(getAgent(h.store.db, id)?.archived_at).toBeTruthy();
  expect(getAgent(h.store.db, id)?.result_summary).toBe('Finished');
  expect(await rt.closeThread(desk.id, id, true)).toContain('already closed');
});

it('keeps published files and other tasks, and blocks messages during closing', async () => {
  const { rt, project, desk, id, workspace } = await setup();
  const published = join(rt.services.libraryDir(project), 'deliverable.txt');
  await mkdir(rt.services.libraryDir(project), { recursive: true });
  await writeFile(published, 'keep');
  const other = rt.createThread(project, { title: 'Still working', brief: 'G', workspacePath: join(h.dir, 'another-workspace') });
  h.store.append({ project_id: project, agent_id: other, type: 'agent.status_changed', payload: { status: 'running' } });
  const closing = rt.closeThread(desk.id, id, true);
  expect(() => rt.sendMessage(id, 'Reopen')).toThrow('archived');
  await closing;
  expect(existsSync(workspace)).toBe(false);
  expect(existsSync(published)).toBe(true);
  expect(getAgent(h.store.db, other)?.archived_at).toBeNull();
});

it.each(['running', 'queued', 'waiting', 'failed', 'cancelled'] as const)('refuses %s tasks', async status => {
  const { rt, project, desk, id, workspace } = await setup();
  h.store.append({ project_id: project, agent_id: id, type: 'agent.status_changed', payload: { status } });
  await expect(rt.closeThread(desk.id, id, true)).rejects.toThrow('fully completed');
  expect(existsSync(workspace)).toBe(true);
});

it('refuses a pending user message, and a running service', async () => {
  const { rt, project, desk, id, workspace } = await setup();
  h.store.append({ project_id: project, agent_id: null, type: 'service.started', payload: { service_id: 'svc', name: 'preview', command: 'preview', cwd: '.', workspace_agent_id: id, source_id: null, pid: 123, by: 'user' } });
  await expect(rt.closeThread(desk.id, id, true)).rejects.toThrow('running service');
  h.store.append({ project_id: project, agent_id: id, type: 'message.user', payload: { text: 'Wait, I need a revision' } });
  await expect(rt.closeThread(desk.id, id, true)).rejects.toThrow('pending messages');
  expect(existsSync(workspace)).toBe(true);
});

it('rejects cross-project callers and external workspaces', async () => {
  const { rt, project, desk, id } = await setup();
  const other = rt.createProject({ name: 'Other', goal: 'G' });
  await expect(rt.closeThread(getDeskAgent(h.store.db, other)!.id, id, true)).rejects.toThrow('own project');
  await expect(rt.closeThread(id, id, true)).rejects.toThrow('own project');
  const external = rt.createThread(project, { title: 'External', brief: 'G', workspacePath: h.files });
  h.store.append({ project_id: project, agent_id: external, type: 'agent.status_changed', payload: { status: 'done' } });
  await expect(rt.closeThread(desk.id, external, true)).rejects.toThrow('managed');
});

it('exposes close only to Desk, defaults to preview, and executes through its normal tool loop', async () => {
  const { rt, project, desk, id, workspace } = await setup();
  expect(closeThreadTool.input.parse({ thread_id: id, reason: 'Accepted' }).discard_workspace).toBe(false);
  expect(deskToolsFor(desk)).toContain(closeThreadTool);
  expect(threadToolsFor(getAgent(h.store.db, id)!)).not.toContain(closeThreadTool);
  expect(stepToolsFor(getAgent(h.store.db, id)!)).not.toContain(closeThreadTool);
  h.fake.setScript([
    tools(call('close_thread', { thread_id: id, reason: 'Accepted; deliverables already saved' })),
    tools(call('close_thread', { thread_id: id, reason: 'Only disposable scratch remains', discard_workspace: true })),
    text('Closed.'),
  ]);
  rt.sendToDesk(project, 'Close the finished task');
  await rt.whenIdle();
  expect(existsSync(workspace)).toBe(false);
  expect(h.store.list({ agentId: desk.id, types: ['tool.result'] }).map(e => e.type === 'tool.result' && e.payload.status)).toEqual(['ok', 'ok']);
});
