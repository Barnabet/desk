import { mkdtempSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type FakeReply, type Script } from '@desk/fake-model';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '@desk/core/testing';
import { createApp } from './app';

let h: Harness;
afterEach(async () => h?.cleanup());

const TOKEN = 'test-token';
function routed(threadReplies: FakeReply[] = [], deskReplies: FakeReply[] = []): Script {
  return (req) => (req.model === FAKE_MODEL.id ? (threadReplies.shift() ?? text('(idle)')) : (deskReplies.shift() ?? text('Desk here.')));
}

async function setup(script: Script = routed()) {
  h = await createHarness({ script });
  const runtime = newRuntime(h);
  let saved: unknown = null;
  const app = createApp({ runtime, store: h.store, models: h.models, token: TOKEN, version: '1.0.0', saveModels: (m) => (saved = m) });
  const api = async (method: string, path: string, body?: unknown) => {
    const res = await app.request(`/v1${path}`, {
      method,
      headers: { authorization: `Bearer ${TOKEN}`, ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const type = res.headers.get('content-type') ?? '';
    // Non-JSON bodies are left unread so tests can consume raw bytes from `res`.
    return { status: res.status, body: (type.includes('json') ? await res.json() : null) as any, res };
  };
  return { app, runtime, api, saved: () => saved };
}

async function newProject(api: Awaited<ReturnType<typeof setup>>['api'], extra: object = {}) {
  const r = await api('POST', '/projects', { name: 'Demo', goal: 'Test goal', settings: { thread_model: FAKE_MODEL.id }, ...extra });
  expect(r.status).toBe(201);
  return r.body as { project: { id: string }; desk: { id: string } };
}

describe('auth', () => {
  it('serves health without auth and rejects missing or wrong tokens', async () => {
    const { app } = await setup();
    const health = await app.request('/v1/health');
    expect(await health.json()).toMatchObject({ version: '1.0.0', protocol_version: 1 });
    expect((await app.request('/v1/projects')).status).toBe(401);
    expect((await app.request('/v1/projects', { headers: { authorization: 'Bearer nope' } })).status).toBe(401);
  });
});

describe('projects', () => {
  it('creates, lists, shows, updates and archives projects', async () => {
    const { api } = await setup();
    const { project, desk } = await newProject(api);
    expect(desk).toMatchObject({ role: 'desk' });
    expect((await api('GET', '/projects')).body).toHaveLength(1);
    const overview = (await api('GET', `/projects/${project.id}`)).body;
    expect(overview).toMatchObject({ project: { name: 'Demo' }, desk: { id: desk.id }, sources: [], threads: [], approvals: [], plan: null });
    const patched = await api('PATCH', `/projects/${project.id}`, { goal: 'New goal', settings: { check_in: 'minimal' } });
    expect(patched.body).toMatchObject({ goal: 'New goal', settings: { check_in: 'minimal' } });
    expect((await api('POST', `/projects/${project.id}/archive`)).status).toBe(200);
    expect((await api('GET', '/projects')).body).toHaveLength(0);
    expect((await api('POST', `/projects/${project.id}/messages`, { text: 'hi' })).status).toBe(409);
  });

  it('patches one setting without resetting the others', async () => {
    const { api } = await setup();
    const { project } = await newProject(api, { settings: { thread_model: FAKE_MODEL.id, review_rounds: 5, policy: [] } });
    await api('PATCH', `/projects/${project.id}`, { settings: { check_in: 'minimal' } });
    const s = (await api('GET', `/projects/${project.id}`)).body.project.settings;
    expect(s).toMatchObject({ check_in: 'minimal', review_rounds: 5, thread_model: FAKE_MODEL.id, policy: [] });
  });

  it('adds and removes sources, validating paths', async () => {
    const { api } = await setup();
    const { project } = await newProject(api);
    const dir = mkdtempSync(join(h.dir, 'src-'));
    const added = await api('POST', `/projects/${project.id}/sources`, { path: dir, label: 'Docs' });
    expect(added.status).toBe(201);
    expect(added.body).toMatchObject({ label: 'Docs', kind: 'folder' });
    expect((await api('POST', `/projects/${project.id}/sources`, { path: join(h.dir, 'missing') })).status).toBe(400);
    expect((await api('DELETE', `/projects/${project.id}/sources/${added.body.id}`)).status).toBe(200);
    expect((await api('GET', `/projects/${project.id}`)).body.sources).toEqual([]);
  });

  it('messages Desk and exposes the chat', async () => {
    const { api, runtime } = await setup();
    const { project } = await newProject(api);
    expect((await api('POST', `/projects/${project.id}/messages`, { text: 'Hello Desk' })).status).toBe(202);
    await runtime.whenIdle();
    const chat = (await api('GET', `/projects/${project.id}/chat`)).body as { events: Array<{ type: string; payload: any }>; next_after: number };
    expect(chat.events.map((e) => e.type)).toEqual(expect.arrayContaining(['message.user', 'assistant.message']));
    expect(chat.events.find((e) => e.type === 'assistant.message')?.payload.content).toBe('Desk here.');
    const later = (await api('GET', `/projects/${project.id}/chat?after=${chat.next_after}`)).body;
    expect(later.events).toEqual([]);
  });

  it('validates bodies and maps unknown ids to 404', async () => {
    const { api } = await setup();
    expect((await api('POST', '/projects', { name: '' })).status).toBe(400);
    const bad = await api('POST', '/projects', { name: 'x', settings: { check_in: 'loud' } });
    expect(bad.body.error.code).toBe('invalid');
    expect((await api('GET', '/projects/nope')).status).toBe(404);
    expect((await api('POST', '/projects/nope/messages', { text: 'x' })).status).toBe(404);
  });
});

describe('threads and approvals', () => {
  it('lists threads, reads transcripts, messages, stops and archives them', async () => {
    const { api, runtime } = await setup(routed([tools(call('wait_for_reply', {}))], [tools(call('spawn_thread', { title: 'Scout', brief: 'Look around' })), text('dispatched')]));
    const { project } = await newProject(api);
    await api('POST', `/projects/${project.id}/messages`, { text: 'go' });
    await runtime.whenIdle();
    const threads = (await api('GET', `/projects/${project.id}/threads`)).body as Array<{ id: string; status: string; title: string }>;
    expect(threads).toHaveLength(1);
    const id = threads[0]!.id;
    expect((await api('GET', `/threads/${id}`)).body).toMatchObject({ title: 'Scout', status: 'waiting' });
    const transcript = (await api('GET', `/threads/${id}/transcript`)).body;
    expect(transcript.events.some((e: any) => e.type === 'message.agent')).toBe(true);
    expect((await api('POST', `/threads/${id}/messages`, { text: 'hurry' })).status).toBe(202);
    await runtime.whenIdle();
    expect((await api('POST', `/threads/${id}/archive`)).status).toBe(409);
    expect((await api('POST', `/threads/${id}/stop`)).status).toBe(200);
    await runtime.whenIdle();
    expect((await api('POST', `/threads/${id}/archive`)).status).toBe(200);
    expect((await api('GET', `/threads/${project.id}`)).status).toBe(404);
  });

  it('lists and resolves approvals', async () => {
    const { api, runtime } = await setup(routed([tools(call('bash', { command: 'sudo true' })), text('ok')], [tools(call('spawn_thread', { title: 'Risky', brief: 'b' })), text('noted')]));
    const { project } = await newProject(api);
    await api('POST', `/projects/${project.id}/messages`, { text: 'go' });
    await runtime.whenIdle();
    const pending = (await api('GET', `/projects/${project.id}/approvals?status=pending`)).body as Array<{ id: string; tool: string }>;
    expect(pending).toHaveLength(1);
    expect(pending[0]!.tool).toBe('bash');
    expect((await api('POST', `/approvals/${pending[0]!.id}/resolve`, { decision: 'denied', note: 'no sudo' })).status).toBe(200);
    expect((await api('POST', `/approvals/${pending[0]!.id}/resolve`, { decision: 'approved' })).status).toBe(409);
    await runtime.whenIdle();
    expect((await api('GET', `/projects/${project.id}/approvals?status=denied`)).body).toHaveLength(1);
  });
});

describe('memory, library, usage, events, models', () => {
  it('supports memory CRUD and search', async () => {
    const { api } = await setup();
    const { project } = await newProject(api);
    const created = await api('POST', `/projects/${project.id}/memory`, { kind: 'decision', content: 'Ship on Thursday' });
    expect(created.status).toBe(201);
    const updated = await api('PATCH', `/projects/${project.id}/memory/${created.body.id}`, { content: 'Ship on Friday' });
    expect(updated.body.id).not.toBe(created.body.id);
    const all = (await api('GET', `/projects/${project.id}/memory`)).body as Array<{ content: string; kind: string }>;
    expect(all.map((m) => [m.kind, m.content])).toEqual([['decision', 'Ship on Friday']]);
    expect((await api('GET', `/projects/${project.id}/memory?q=friday`)).body).toHaveLength(1);
    expect((await api('DELETE', `/projects/${project.id}/memory/${updated.body.id}`)).status).toBe(200);
    expect((await api('GET', `/projects/${project.id}/memory`)).body).toEqual([]);
  });

  it('uploads, lists and downloads library files byte-for-byte', async () => {
    const { api } = await setup();
    const { project } = await newProject(api);
    const bytes = Buffer.from([0, 1, 2, 250, 255]);
    const up = await api('POST', `/projects/${project.id}/library`, { name: 'blob.bin', content_base64: bytes.toString('base64'), title: 'Blob' });
    expect(up.status).toBe(201);
    expect((await api('GET', `/projects/${project.id}/library`)).body[0]).toMatchObject({ path: 'blob.bin', origin: 'user', title: 'Blob' });
    const dl = await api('GET', `/projects/${project.id}/library/file/blob.bin`);
    expect(dl.status).toBe(200);
    expect(Buffer.from(await dl.res.arrayBuffer())).toEqual(bytes);
    expect((await api('GET', `/projects/${project.id}/library/file/..%2F..%2Fdesk.db`)).status).toBe(403);
  });

  it('reports usage and pages events', async () => {
    const { api, runtime } = await setup();
    const { project } = await newProject(api);
    await api('POST', `/projects/${project.id}/messages`, { text: 'hi' });
    await runtime.whenIdle();
    const usage = (await api('GET', `/projects/${project.id}/usage`)).body;
    expect(usage.totals.prompt_tokens).toBeGreaterThan(0);
    const page = (await api('GET', `/projects/${project.id}/events?limit=2`)).body;
    expect(page.events).toHaveLength(2);
    const rest = (await api('GET', `/projects/${project.id}/events?after=${page.next_after}&types=usage`)).body;
    expect(rest.events.every((e: any) => e.type === 'usage')).toBe(true);
  });

  it('lists, replaces and persists models', async () => {
    const { api, saved } = await setup();
    const list = (await api('GET', '/models')).body as Array<{ id: string }>;
    expect(list.map((m) => m.id)).toContain('claude-opus-5-5');
    const next = [...list, { id: 'extra', family: 'gpt', context_window: 1000, max_output_tokens: 100, reasoning_efforts: [], default_reasoning_effort: null, concurrency: 1 }];
    expect((await api('PUT', '/models', next)).status).toBe(200);
    expect((saved() as Array<{ id: string }>).map((m) => m.id)).toContain('extra');
    expect((await api('PUT', '/models', [{ id: 'broken' }])).status).toBe(400);
  });
});
