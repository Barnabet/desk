import { existsSync } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { afterEach, expect, it } from 'vitest';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '@desk/core/testing';
import { createApp } from '../app';

let h: Harness;
afterEach(async () => h?.cleanup());

it('authenticates storage routes, previews without deleting, and cleans only eligible dependency folders', async () => {
  h = await createHarness({ now: () => new Date('2026-09-20T00:00:00Z') });
  const runtime = newRuntime(h, { now: () => new Date('2026-09-22T00:00:00Z') });
  const project = runtime.createProject({ name: 'P', goal: 'G' });
  const workspace = join(h.dir, 'workspaces', 'finished');
  await mkdir(join(workspace, 'node_modules'), { recursive: true });
  for (const [name, content] of [['package.json', '{}'], ['package-lock.json', '{}'], ['node_modules/dep', 'dependency'], ['report.pdf', 'unpublished']]) await writeFile(join(workspace, name!), content!);
  h.store.append([
    { project_id: project, agent_id: 'finished', type: 'agent.created', payload: { role: 'thread', model: FAKE_MODEL.id, title: 'Finished', brief: null, workspace_path: workspace, parent_id: null } },
    { project_id: project, agent_id: 'finished', type: 'agent.status_changed', payload: { status: 'done' } },
  ]);
  const app = createApp({ runtime, store: h.store, models: h.models, token: 't', version: '1.0.0' });
  expect((await app.request('/v1/system/workspaces')).status).toBe(401);
  expect((await app.request('/v1/system/workspaces/cleanup', { method: 'POST' })).status).toBe(401);
  const headers = { authorization: 'Bearer t' };
  const preview = await app.request('/v1/system/workspaces', { headers });
  expect(await preview.json()).toMatchObject({ reclaimable_bytes: 10, retention_hours: 24 });
  expect(existsSync(join(workspace, 'node_modules'))).toBe(true);
  const clean = await app.request('/v1/system/workspaces/cleanup', { method: 'POST', headers });
  expect(await clean.json()).toEqual({ removed: 1, bytes: 10, errors: [] });
  expect(existsSync(join(workspace, 'report.pdf'))).toBe(true);
  expect(existsSync(join(workspace, 'node_modules'))).toBe(false);
});
