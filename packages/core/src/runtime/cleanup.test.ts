import { existsSync } from 'node:fs';
import { mkdir, symlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { afterEach, expect, it } from 'vitest';
import { text } from '@desk/fake-model';
import { newId } from '../ids';
import { getAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';

let h: Harness;
afterEach(async () => h?.cleanup());

async function setup(ageHours = 25) {
  const created = new Date('2026-09-20T00:00:00Z');
  h = await createHarness({ now: () => created, script: () => text('ok') });
  const rt = newRuntime(h, { now: () => new Date(created.getTime() + ageHours * 3_600_000) });
  const project = rt.createProject({ name: 'P', goal: 'G' });
  const id = newId();
  const workspace = join(h.dir, 'workspaces', id);
  await mkdir(join(workspace, 'node_modules'), { recursive: true });
  await writeFile(join(workspace, 'package.json'), '{}');
  await writeFile(join(workspace, 'package-lock.json'), '{}');
  await writeFile(join(workspace, 'node_modules', 'dep.js'), 'dependency');
  await writeFile(join(workspace, 'unpublished.txt'), 'valuable work');
  h.store.append([
    { project_id: project, agent_id: id, type: 'agent.created', payload: { role: 'thread', model: FAKE_MODEL.id, title: 'Finished', brief: 'G', workspace_path: workspace, parent_id: null } },
    { project_id: project, agent_id: id, type: 'agent.status_changed', payload: { status: 'done' } },
  ]);
  return { rt, project, id, workspace };
}

it('cleans old completed tasks, including after restart, retaining their workspace, history and source', async () => {
  const { rt, id, workspace } = await setup();
  expect((await rt.workspaceStorage()).reclaimable_bytes).toBe(10);
  const recovered = newRuntime(h, { now: () => new Date('2026-09-22T00:00:00Z') });
  expect(await recovered.cleanupWorkspaces()).toMatchObject({ removed: 1, bytes: 10, errors: [] });
  expect(existsSync(join(workspace, 'node_modules'))).toBe(false);
  expect(existsSync(join(workspace, 'unpublished.txt'))).toBe(true);
  expect(getAgent(h.store.db, id)).toMatchObject({ status: 'done', archived_at: null, workspace_path: workspace });
  expect(await recovered.cleanupWorkspaces()).toMatchObject({ removed: 0, errors: [] });
});

it('honors the automatic grace period and allows manual cleanup sooner', async () => {
  const { rt, workspace } = await setup(2);
  expect(await rt.cleanupWorkspaces()).toMatchObject({ removed: 0 });
  expect(existsSync(join(workspace, 'node_modules'))).toBe(true);
  expect(await rt.cleanupWorkspaces(true)).toMatchObject({ removed: 1 });
});

it('keeps a completed workspace that a running service uses', async () => {
  const { rt, project, id, workspace } = await setup();
  h.store.append({ project_id: project, agent_id: null, type: 'service.started', payload: { service_id: 'svc', name: 'preview', command: 'preview', cwd: '.', workspace_agent_id: id, source_id: null, pid: 123, by: 'user' } });
  expect(await rt.cleanupWorkspaces()).toMatchObject({ removed: 0 });
  expect((await rt.workspaceStorage()).workspaces[0]?.reason).toMatch(/running service/);
  expect(existsSync(join(workspace, 'node_modules'))).toBe(true);
});

it.each(['running', 'waiting', 'failed', 'cancelled'] as const)('does not clean %s tasks', async (status) => {
  const { rt, project, id, workspace } = await setup();
  h.store.append({ project_id: project, agent_id: id, type: 'agent.status_changed', payload: { status } });
  expect(await rt.cleanupWorkspaces(true)).toMatchObject({ removed: 0 });
  expect(existsSync(join(workspace, 'node_modules'))).toBe(true);
});

it('preserves task drafts even inside a dependency directory', async () => {
  const { rt, project, id, workspace } = await setup();
  h.store.append({ project_id: project, agent_id: id, type: 'agent.result', payload: { summary: 'draft ready', artifacts: [], skill_drafts: ['node_modules'] } });
  expect(await rt.cleanupWorkspaces()).toMatchObject({ removed: 0 });
  expect(existsSync(join(workspace, 'node_modules'))).toBe(true);
});

it('serializes cleanup and defers a new task message until maintenance finishes', async () => {
  const { rt, id, workspace } = await setup();
  const first = rt.cleanupWorkspaces();
  expect(rt.cleanupWorkspaces()).toBe(first);
  rt.sendMessage(id, 'Continue working');
  expect(h.fake.requests).toHaveLength(0);
  expect(await first).toMatchObject({ removed: 0 });
  await rt.whenIdle();
  expect(h.fake.requests).toHaveLength(1);
  expect(existsSync(join(workspace, 'node_modules'))).toBe(true);
});

it('does not clean external workspaces or symlinks masquerading as managed workspaces', async () => {
  const { rt, project, workspace } = await setup();
  const external = rt.createThread(project, { title: 'External', brief: 'G', workspacePath: h.files });
  h.store.append({ project_id: project, agent_id: external, type: 'agent.status_changed', payload: { status: 'done' } });
  const linked = newId();
  await symlink(workspace, join(h.dir, 'workspaces', linked));
  h.store.append([
    { project_id: project, agent_id: linked, type: 'agent.created', payload: { role: 'thread', model: FAKE_MODEL.id, title: 'Link', brief: '', workspace_path: join(h.dir, 'workspaces', linked), parent_id: null } },
    { project_id: project, agent_id: linked, type: 'agent.status_changed', payload: { status: 'done' } },
  ]);
  const report = await rt.workspaceStorage();
  expect(report.workspaces.filter((w) => [external, linked].includes(w.agent_id)).every((w) => w.reason === 'This is not a managed thread workspace')).toBe(true);
});

it('refuses archiving unpublished scratch files and keeps the task unarchived', async () => {
  const { rt, id, workspace } = await setup();
  await expect(rt.archiveThread(id)).rejects.toThrow(/Publish or move/);
  expect(getAgent(h.store.db, id)?.archived_at).toBeNull();
  expect(existsSync(join(workspace, 'unpublished.txt'))).toBe(true);
});
