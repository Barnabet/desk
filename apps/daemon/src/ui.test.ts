import { execFileSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createHarness, newRuntime, type Harness } from '@desk/core/testing';
import { getDeskAgent } from '@desk/core';
import { createApp } from './app';

let h: Harness;
afterEach(async () => h?.cleanup());
const TOKEN = 't';

async function setup() {
  h = await createHarness();
  const runtime = newRuntime(h);
  const app = createApp({ runtime, store: h.store, models: h.models, token: TOKEN, version: '1.0.0' });
  const api = async (method: string, path: string, body?: unknown) => {
    const res = await app.request(`/v1${path}`, {
      method,
      headers: { authorization: `Bearer ${TOKEN}`, ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const json = (res.headers.get('content-type') ?? '').includes('json');
    return { status: res.status, body: (json ? await res.json() : await res.text()) as any };
  };
  const projectId = runtime.createProject({ name: 'Onboarding revamp', goal: 'g' });
  return { runtime, api, projectId, desk: getDeskAgent(h.store.db, projectId)! };
}

describe('attention routes', () => {
  it('lists, filters and dismisses', async () => {
    const { api, projectId, desk } = await setup();
    const [r] = h.store.append({ project_id: projectId, agent_id: desk.id, type: 'report', payload: { headline: 'H', progress: '', needs_you: ['Upload the 1099'], results: [] } });
    const list = await api('GET', '/attention');
    expect(list.status).toBe(200);
    expect(list.body.items).toEqual([expect.objectContaining({ id: `report:${r!.id}:0`, kind: 'needs_you' })]);
    expect(list.body.seq).toBeGreaterThanOrEqual(r!.id);
    expect((await api('GET', '/attention?project_id=nope')).body.items).toEqual([]);
    expect((await api('POST', `/attention/${encodeURIComponent(`report:${r!.id}:0`)}/dismiss`)).status).toBe(200);
    expect((await api('GET', '/attention')).body.items).toEqual([]);
    expect((await api('POST', '/attention/approval%3Ax/dismiss')).status).toBe(409);
    expect((await api('POST', '/attention/report%3A1%3A9/dismiss')).status).toBe(404);
  });
});

describe('overview', () => {
  it('returns one summary per project', async () => {
    const { api, projectId } = await setup();
    const r = await api('GET', '/overview');
    expect(r.body).toEqual([expect.objectContaining({ project: expect.objectContaining({ id: projectId }), threads: [], attention_count: 0 })]);
  });
});

describe('thread inspection', () => {
  it('serves the diff of a git thread and browses its workspace', async () => {
    const { runtime, api, projectId } = await setup();
    const repo = join(h.files, 'repo');
    await mkdir(repo);
    const g = (...a: string[]) => execFileSync('git', ['-c', 'user.email=t@t', '-c', 'user.name=t', ...a], { cwd: repo, stdio: 'pipe' });
    g('init', '-q', '-b', 'main');
    await writeFile(join(repo, 'a.txt'), 'one\n');
    g('add', '.');
    g('commit', '-qm', 'init');
    const gitSourceId = await runtime.addSource(projectId, repo);
    const threadId = await runtime.services.spawnThread(getDeskAgent(h.store.db, projectId)!.id, { title: 'Checklist', brief: 'b', gitSourceId });
    const t = (await api('GET', `/threads/${threadId}`)).body;
    await writeFile(join(t.workspace_path, 'a.txt'), 'two\n');
    await mkdir(join(t.workspace_path, 'docs'));
    await writeFile(join(t.workspace_path, 'docs', 'x.md'), '# x');

    const diff = await api('GET', `/threads/${threadId}/diff`);
    expect(diff.status).toBe(200);
    expect(diff.body.files).toContainEqual({ path: 'a.txt', status: 'modified', additions: 1, deletions: 1 });
    const files = await api('GET', `/threads/${threadId}/files`);
    expect(files.body.map((f: any) => f.name)).toEqual(['docs', 'a.txt']);
    expect((await api('GET', `/threads/${threadId}/files?path=docs`)).body).toEqual([{ name: 'x.md', path: 'docs/x.md', type: 'file', size: 3 }]);
    const raw = await api('GET', `/threads/${threadId}/files/raw/docs/x.md`);
    expect(raw).toMatchObject({ status: 200, body: '# x' });
    expect((await api('GET', `/threads/${threadId}/files/raw/..%2F..%2Fetc%2Fpasswd`)).status).toBe(403);
    expect((await api('GET', `/threads/${threadId}/files/raw/nope.txt`)).status).toBe(404);
    await runtime.shutdown();
  });

  it('409s for a thread without git', async () => {
    const { runtime, api, projectId } = await setup();
    const t = runtime.createThread(projectId, { title: 'Plain', brief: 'b', workspacePath: join(h.dir, 'plain') });
    expect((await api('GET', `/threads/${t}/diff`)).status).toBe(409);
  });
});

describe('skill versions', () => {
  it('reads past versions and their files, globally and through a project', async () => {
    const { api, projectId } = await setup();
    const b64 = (s: string) => Buffer.from(s).toString('base64');
    await api('PUT', '/skills/email-sequence', { description: 'Emails', instructions: 'v1 steps', files: [{ path: 'notes.md', content_base64: b64('one') }] });
    await api('PUT', '/skills/email-sequence', { instructions: 'v2 steps', files: [{ path: 'notes.md', content_base64: b64('two') }], change_note: 'Refined' });
    const v1 = await api('GET', '/skills/email-sequence/versions/1');
    expect(v1.body).toMatchObject({ version: 1, instructions: 'v1 steps' });
    expect((await api('GET', '/skills/email-sequence/versions/1/files/notes.md')).body).toBe('one');
    expect((await api('GET', `/projects/${projectId}/skills/email-sequence/versions/2`)).body).toMatchObject({ version: 2, instructions: 'v2 steps' });
    expect((await api('GET', '/skills/email-sequence/versions/9')).status).toBe(404);
    expect((await api('GET', '/skills/email-sequence/versions/x')).status).toBe(400);
  });
});

describe('usage', () => {
  it('aggregates per project and model, optionally since a day', async () => {
    const { api, projectId, desk } = await setup();
    h.store.append({ project_id: projectId, agent_id: desk.id, type: 'usage', payload: { run_id: 'r', model: 'm1', prompt_tokens: 10, completion_tokens: 5, estimated: false } });
    h.store.append({ project_id: projectId, agent_id: desk.id, type: 'usage', payload: { run_id: 'r2', model: 'm1', prompt_tokens: 1, completion_tokens: 1, estimated: false } });
    const r = await api('GET', '/usage');
    expect(r.body).toEqual({ rows: [{ project_id: projectId, model: 'm1', prompt_tokens: 11, completion_tokens: 6 }], totals: { prompt_tokens: 11, completion_tokens: 6 } });
    expect((await api('GET', '/usage?since=2999-01-01')).body.rows).toEqual([]);
    expect((await api('GET', '/usage?since=yesterday')).status).toBe(400);
  });
});
