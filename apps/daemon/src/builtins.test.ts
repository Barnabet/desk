import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createHarness, newRuntime, type Harness } from '@desk/core/testing';
import { createApp } from './app';

const SKILLS = join(import.meta.dirname, '..', '..', '..', 'catalog', 'skills');

let h: Harness;
afterEach(async () => h?.cleanup());

async function setup() {
  h = await createHarness();
  const runtime = newRuntime(h, { builtins: { root: SKILLS } });
  const app = createApp({ runtime, store: h.store, models: h.models, token: 't', version: '1.0.0' });
  const req = (method: string, path: string, body?: unknown) =>
    app.request(`/v1${path}`, {
      method,
      headers: { authorization: 'Bearer t', ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
  const api = async (method: string, path: string, body?: unknown) => {
    const res = await req(method, path, body);
    return { status: res.status, body: (await res.json()) as any };
  };
  return { api, req, runtime };
}

describe('built-in skills API', () => {
  it('lists built-ins apart from the user skills', async () => {
    const { api } = await setup();
    const list = await api('GET', '/builtin-skills');
    expect(list.status).toBe(200);
    expect(list.body).toHaveLength(12);
    expect(list.body.find((b: any) => b.name === 'pdf-toolkit')).toMatchObject({ enabled: true, broken: null, shadowed_by: null, runtime: { state: 'none', reason: null } });
    expect((await api('GET', '/skills')).body).toEqual([]);
  });

  it('switches, reads, duplicates and refuses unknown names', async () => {
    const { api, req, runtime } = await setup();
    expect((await api('PUT', '/builtin-skills/pdf-toolkit', { enabled: false })).body.enabled).toBe(false);
    expect(runtime.skills.resolve('pdf-toolkit')).toBeUndefined();
    const detail = await api('GET', '/builtin-skills/word-documents');
    expect(detail.body).toMatchObject({ name: 'word-documents', scope: 'builtin' });
    expect(await (await req('GET', '/builtin-skills/word-documents/files/SKILL.md')).text()).toMatch(/^---/);
    expect((await req('GET', '/builtin-skills/word-documents/files/..%2F..%2Fpackage.json')).status).toBe(400);
    const dup = await api('POST', '/builtin-skills/images/duplicate', {});
    expect(dup.status).toBe(201);
    expect((await api('GET', '/builtin-skills')).body.find((b: any) => b.name === 'images').shadowed_by).toBe('global');
    expect((await api('POST', '/builtin-skills/images/duplicate', {})).status).toBe(409);
    expect((await api('POST', '/builtin-skills/images/duplicate', { scope: 'project' })).status).toBe(400);
    expect((await api('PUT', '/builtin-skills/nope', { enabled: false })).status).toBe(404);
  });

  it('retries a built-in environment', async () => {
    const { api } = await setup();
    // No skill runtimes in this app: the retry says so.
    expect((await api('POST', '/builtin-skills/images/runtime/retry')).status).toBe(409);
  });
});
