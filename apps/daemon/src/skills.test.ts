import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '@desk/core/testing';
import { createApp } from './app';

let h: Harness;
afterEach(async () => h?.cleanup());

const TOKEN = 't';
async function setup() {
  h = await createHarness();
  const runtime = newRuntime(h);
  const app = createApp({ runtime, store: h.store, models: h.models, token: TOKEN, version: '1.0.0', saveModels: () => {} });
  const api = async (method: string, path: string, body?: unknown) => {
    const res = await app.request(`/v1${path}`, {
      method,
      headers: { authorization: `Bearer ${TOKEN}`, ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const type = res.headers.get('content-type') ?? '';
    return { status: res.status, body: (type.includes('json') ? await res.json() : null) as any, res };
  };
  const project = (await api('POST', '/projects', { name: 'P', goal: 'g', settings: { thread_model: FAKE_MODEL.id } })).body.project.id as string;
  return { api, project, runtime };
}

const b64 = (s: string) => Buffer.from(s).toString('base64');

describe('skills API', () => {
  it('creates, reads, downloads, updates, lists, deletes and restores global skills', async () => {
    const { api } = await setup();
    const created = await api('PUT', '/skills/greet', {
      description: 'Greets',
      instructions: 'Run scripts/hi.sh',
      files: [{ path: 'scripts/hi.sh', content_base64: b64('echo hi') }],
      change_note: 'v1',
    });
    expect(created.status).toBe(201);
    expect(created.body).toMatchObject({ version: 1, created: true });
    const shown = await api('GET', '/skills/greet');
    expect(shown.body).toMatchObject({ name: 'greet', scope: 'global', description: 'Greets', instructions: 'Run scripts/hi.sh' });
    expect(shown.body.files.map((f: any) => f.path)).toEqual(['SKILL.md', 'scripts/hi.sh']);
    const file = await api('GET', '/skills/greet/files/scripts/hi.sh');
    expect(await file.res.text()).toBe('echo hi');
    expect((await api('GET', '/skills/greet/files/..%2F..%2Fx')).status).toBe(400);
    expect((await api('PUT', '/skills/greet', { instructions: 'Say hi' })).body).toMatchObject({ version: 2, created: false });
    expect((await api('GET', '/skills')).body.map((s: any) => [s.name, s.version])).toEqual([['greet', 2]]);
    expect((await api('GET', '/skills/greet/history')).body.map((x: any) => x.version)).toEqual([1, 2]);
    expect((await api('DELETE', '/skills/greet')).status).toBe(200);
    expect((await api('GET', '/skills/greet')).status).toBe(404);
    expect((await api('POST', '/skills/greet/restore', { version: 2 })).body.version).toBe(3);
    expect((await api('GET', '/skills/greet')).body.instructions).toBe('Say hi');
    expect((await api('PUT', '/skills/Bad_Name', { description: 'd', instructions: 'x' })).status).toBe(400);
  });

  it('project skills shadow global ones and can be imported', async () => {
    const { api, project, runtime } = await setup();
    await api('PUT', '/skills/style', { description: 'Global style', instructions: 'G' });
    const src = join(h.dir, 'import-me');
    mkdirSync(src);
    writeFileSync(join(src, 'SKILL.md'), '---\nname: style\ndescription: Project style\n---\nP\n');
    const imported = await api('POST', `/projects/${project}/skills/import`, { path: src });
    expect(imported.status).toBe(201);
    expect((await api('GET', `/projects/${project}/skills`)).body.map((s: any) => [s.name, s.scope])).toEqual([['style', 'project']]);
    expect((await api('GET', `/projects/${project}/skills/style`)).body.description).toBe('Project style');
    expect((await api('GET', '/skills/style')).body.description).toBe('Global style');
    expect(h.store.list({ types: ['skill.saved'] }).map((e) => e.project_id)).toEqual(['_global', project]);
    expect((await api('POST', '/projects/nope/skills/import', { path: src })).status).toBe(404);
    expect(runtime.listSkills().map((s) => s.name)).toEqual(['style']);
  });
});
