import { afterEach, describe, expect, it, vi } from 'vitest';
import { DaemonNotRunning, DeskClient } from '@desk/client';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { CatalogService, SkillRuntimes, treeDigest } from '@desk/core';
import { automationHarness, askDef, createHarness, encodePng, newRuntime, type Harness } from '@desk/core/testing';
import { createApp, startServer, type RunningServer } from '@desk/daemon';
import { channels, initialGlobalState, type Channel, type ChannelOutput } from '../contract';
import { dispatch, handlers, MAX_ATTACHMENT_BYTES, type HandlerContext } from './handlers';

/** True only when A and B are the same type. */
type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;

let h: Harness;
let server: RunningServer | undefined;
afterEach(async () => {
  await server?.close();
  server = undefined;
  await h?.cleanup();
});

/** A catalog with one builtin skill written into the harness directory. */
function builtinCatalog(runtime: ReturnType<typeof newRuntime>) {
  const md = Buffer.from('---\nname: pre-mortem\ndescription: Imagine the plan failed.\n---\n\nList the risks.\n');
  mkdirSync(join(h.dir, 'builtin', 'pre-mortem'), { recursive: true });
  writeFileSync(join(h.dir, 'builtin', 'pre-mortem', 'SKILL.md'), md);
  const skillRuntimes = new SkillRuntimes({ dataDir: h.dir, store: h.store, uv: null, nodeExec: process.execPath, exists: () => true });
  const entry = {
    id: 'pre-mortem', title: 'Pre-mortem', category: 'planning' as const, summary: 'Imagine the plan failed.', license: 'MIT', homepage: 'https://example.com',
    source: { type: 'builtin' as const, path: 'pre-mortem' }, digest: treeDigest([{ path: 'SKILL.md', content: md }]), files: 1, bytes: md.length, scripts: 0, runtime: {}, caveats: [],
  };
  const catalog = new CatalogService({ runtime, store: h.store, dataDir: h.dir, builtinRoot: join(h.dir, 'builtin'), runtimes: skillRuntimes, catalog: { version: 1, updated: '2026-09-24', entries: [entry] } });
  return { catalog, skillRuntimes };
}

const BUILTINS = join(import.meta.dirname, '..', '..', '..', '..', 'catalog', 'skills');

function makeCtx(client: DeskClient, overrides: Partial<HandlerContext> = {}) {
  const opened: string[] = [];
  const watched: Array<[number, string, number]> = [];
  const ctx: HandlerContext = {
    senderId: 7,
    client: () => client,
    broker: { snapshot: () => initialGlobalState(), watch: async (s, p, a) => void watched.push([s, p, a]), unwatch: () => {} },
    daemon: { status: vi.fn(), start: vi.fn(), restart: vi.fn(), stop: vi.fn(), repair: vi.fn() },
    app: {
      info: () => ({ version: '1.0.0', platform: 'darwin', packaged: false, dataDir: '/d' }),
      openExternal: async (url) => void opened.push(url),
      pickFolder: async () => '/picked',
      pickFile: async () => '/picked/file.txt',
      revealLogs: async () => {},
      revealRun: async (runId, stepId) => void opened.push(`reveal:${runId}:${stepId ?? ''}`),
      saveFile: async () => true,
      openMain: (route) => void opened.push(`main:${route ?? ''}`),
      settings: () => ({ notifications: true, appearance: 'system' }),
      updateSettings: (p) => ({ notifications: p.notifications ?? true, appearance: p.appearance ?? 'system' }),
    },
    ...overrides,
  };
  return { ctx, opened, watched };
}

async function setup(overrides: Partial<HandlerContext> = {}, withCatalog = false) {
  h = await createHarness();
  const runtime = newRuntime(h, { builtins: { root: BUILTINS } });
  const extra = withCatalog ? builtinCatalog(runtime) : {};
  server = await startServer({ app: createApp({ runtime, store: h.store, models: h.models, token: 't', version: '1.0.0', ...extra }), store: h.store, token: 't', port: 0 });
  const client = new DeskClient({ baseUrl: `http://127.0.0.1:${server.port}`, token: 't' });
  return { ...makeCtx(client, overrides), runtime };
}

describe('IPC dispatch', () => {
  it('has a handler for every channel', () => {
    expect(Object.keys(handlers).sort()).toEqual(Object.keys(channels).sort());
  });

  it('returns what the contract declares, for every operation', () => {
    // An operation whose handler returns another type makes its property `false`, and the typecheck names it.
    const agree: { [C in Channel]: Equal<Awaited<ReturnType<(typeof handlers)[C]>>, ChannelOutput<C>> } = Object.fromEntries(Object.keys(channels).map((c) => [c, true])) as {
      [C in Channel]: true;
    };
    expect(Object.values(agree).every(Boolean)).toBe(true);
  });

  it('runs validated operations against deskd', async () => {
    const { ctx } = await setup();
    const created = await dispatch('projects.create', { name: 'Launch', goal: 'g' }, ctx);
    expect(created).toMatchObject({ ok: true, value: { project: { name: 'Launch' } } });
    const list = await dispatch('projects.list', {}, ctx);
    expect(list.ok && (list.value as Array<{ name: string }>).map((p) => p.name)).toEqual(['Launch']);
    expect(await dispatch('broker.watch', { projectId: 'p1', afterSeq: 3 }, ctx)).toEqual({ ok: true, value: { ok: true } });
  });

  it('browses, reviews and installs catalog skills, and reports runtimes', async () => {
    const { ctx, runtime } = await setup({}, true);
    const projectId = runtime.createProject({ name: 'Launch', goal: 'g' });
    expect(await dispatch('catalog.list', {}, ctx)).toMatchObject({ ok: true, value: [{ id: 'pre-mortem', installs: [] }] });
    expect(await dispatch('catalog.prepare', { id: 'pre-mortem' }, ctx)).toMatchObject({ ok: true, value: { source_url: null, files: [{ path: 'SKILL.md' }] } });
    const file = await dispatch('catalog.file', { id: 'pre-mortem', path: 'SKILL.md' }, ctx);
    expect(file.ok && new TextDecoder().decode(file.value as Uint8Array)).toContain('List the risks.');
    expect(await dispatch('catalog.install', { id: 'pre-mortem', projectId }, ctx)).toMatchObject({ ok: true, value: { skill: { scope: 'project', project_id: projectId }, state: 'installed' } });
    expect(await dispatch('catalog.install', { id: 'pre-mortem' }, ctx)).toMatchObject({ ok: true, value: { skill: { scope: 'global' } } });
    expect(await dispatch('system.runtimes', {}, ctx)).toEqual({ ok: true, value: { bytes: 0, envs: [] } });
    expect(await dispatch('system.runtimesCleanup', {}, ctx)).toEqual({ ok: true, value: { removed: 0, bytes: 0 } });
    expect(await dispatch('system.workspaces', {}, ctx)).toEqual({ ok: true, value: { retention_hours: 24, reclaimable_bytes: 0, workspaces: [] } });
    expect(await dispatch('system.workspacesCleanup', {}, ctx)).toEqual({ ok: true, value: { removed: 0, bytes: 0, errors: [] } });
    expect(await dispatch('skills.runtimeRetry', { name: 'pre-mortem' }, ctx)).toMatchObject({ ok: true, value: { state: 'none' } });
    expect(await dispatch('catalog.prepare', { id: '../etc' }, ctx)).toMatchObject({ ok: false });
  });

  it("lists, switches, reads and duplicates Desk's built-in skills", async () => {
    const { ctx, runtime } = await setup();
    const projectId = runtime.createProject({ name: 'Launch', goal: 'g' });
    const list = await dispatch('builtins.list', { projectId }, ctx);
    expect(list.ok && (list.value as unknown[]).length).toBe(13);
    expect(await dispatch('builtins.setEnabled', { name: 'pdf-toolkit', enabled: false }, ctx)).toMatchObject({ ok: true, value: { name: 'pdf-toolkit', enabled: false } });
    expect(await dispatch('builtins.get', { name: 'word-documents' }, ctx)).toMatchObject({ ok: true, value: { scope: 'builtin' } });
    const file = await dispatch('builtins.file', { name: 'word-documents', path: 'SKILL.md' }, ctx);
    expect(file.ok && new TextDecoder().decode(file.value as Uint8Array)).toMatch(/^---/);
    expect(await dispatch('builtins.duplicate', { name: 'images', projectId }, ctx)).toMatchObject({ ok: true, value: { version: 1 } });
    expect(await dispatch('builtins.duplicate', { name: 'images' }, ctx)).toMatchObject({ ok: true });
    expect(runtime.skills.resolve('images', projectId)?.scope).toBe('project');
    expect(await dispatch('builtins.retry', { name: 'images' }, ctx)).toMatchObject({ ok: false, error: { status: 409 } });
    expect(await dispatch('builtins.setEnabled', { name: '../x', enabled: false }, ctx)).toMatchObject({ ok: false });
  });

  it('rejects unknown channels and invalid payloads', async () => {
    const { ctx } = await setup();
    expect(await dispatch('nope', {}, ctx)).toMatchObject({ ok: false, error: { code: 'unknown_channel' } });
    expect(await dispatch('projects.get', { id: 5 }, ctx)).toMatchObject({ ok: false, error: { code: 'invalid_request' } });
    expect(await dispatch('__proto__', {}, ctx)).toMatchObject({ ok: false, error: { code: 'unknown_channel' } });
    expect(await dispatch('app.updateSettings', { appearance: 'sepia' }, ctx)).toMatchObject({ ok: false, error: { code: 'invalid_request' } });
    expect(await dispatch('app.updateSettings', { appearance: 'dark' }, ctx)).toMatchObject({ ok: true, value: { appearance: 'dark' } });
  });

  it('maps daemon and runtime errors without leaking internals', async () => {
    const { ctx } = await setup();
    expect(await dispatch('projects.get', { id: 'missing' }, ctx)).toEqual({ ok: false, error: { code: 'not_found', message: 'Unknown project: missing', status: 404 } });
    const offline = await dispatch('projects.list', {}, { ...ctx, client: () => { throw new DaemonNotRunning('/secret/dir'); } });
    expect(offline).toEqual({ ok: false, error: { code: 'daemon_not_running', message: 'Desk is not running.' } });
    const log = vi.fn();
    const boom = await dispatch('app.revealLogs', {}, { ...ctx, app: { ...ctx.app, revealLogs: async () => { throw new Error('token=abc stack'); } } }, log);
    expect(boom).toEqual({ ok: false, error: { code: 'internal', message: 'Something went wrong. See the logs for details.' } });
    expect(log).toHaveBeenCalledOnce();
  });

  it('opens only web and mail links', async () => {
    const { ctx, opened } = await setup();
    expect(await dispatch('app.openExternal', { url: 'https://example.com/a?b=1' }, ctx)).toMatchObject({ ok: true });
    expect(await dispatch('app.openExternal', { url: 'mailto:a@b.c' }, ctx)).toMatchObject({ ok: true });
    for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'not a url']) {
      expect(await dispatch('app.openExternal', { url }, ctx)).toMatchObject({ ok: false, error: { code: 'invalid_url' } });
    }
    expect(opened).toEqual(['https://example.com/a?b=1', 'mailto:a@b.c']);
  });

  it('opens the main window only at an in-app hash route', async () => {
    const { ctx, opened } = await setup();
    expect(await dispatch('app.openMain', {}, ctx)).toMatchObject({ ok: true });
    expect(await dispatch('app.openMain', { route: '#/attention?item=approval%3Aa1' }, ctx)).toMatchObject({ ok: true });
    for (const route of ['https://evil.example', 'javascript:alert(1)', '#/a b']) {
      expect(await dispatch('app.openMain', { route }, ctx)).toMatchObject({ ok: false, error: { code: 'invalid_request' } });
    }
    expect(opened).toEqual(['main:', 'main:#/attention?item=approval%3Aa1']);
  });

  it('returns raw bytes and passes the sender to the broker', async () => {
    const { ctx, watched } = await setup();
    const { value } = (await dispatch('projects.create', { name: 'P' }, ctx)) as { value: { project: { id: string } } };
    const up = await dispatch('library.upload', { projectId: value.project.id, file: { name: 'a.txt', content_base64: Buffer.from('hi').toString('base64') } }, ctx);
    const path = (up as { value: { path: string } }).value.path;
    const file = await dispatch('library.file', { projectId: value.project.id, path }, ctx);
    expect(file.ok && new TextDecoder().decode(file.value as Uint8Array)).toBe('hi');
    await dispatch('broker.watch', { projectId: value.project.id, afterSeq: 0 }, ctx);
    expect(watched).toEqual([[7, value.project.id, 0]]);
  });

  it('returns attachments as image data URLs, validating the id and capping the size', async () => {
    const { ctx, runtime } = await setup();
    const png = encodePng(2, 2);
    const sha = await runtime.attachments.put(png, 'image/png');
    expect(await dispatch('attachments.get', { sha256: sha }, ctx)).toEqual({ ok: true, value: `data:image/png;base64,${png.toString('base64')}` });
    expect(await dispatch('attachments.get', { sha256: '../x' }, ctx)).toMatchObject({ ok: false, error: { code: 'invalid_request' } });
    expect(await dispatch('attachments.get', { sha256: 'f'.repeat(64) }, ctx)).toMatchObject({ ok: false, error: { code: 'not_found', status: 404 } });
    const big = await runtime.attachments.put(Buffer.concat([png, Buffer.alloc(MAX_ATTACHMENT_BYTES)]), 'image/png');
    expect(await dispatch('attachments.get', { sha256: big }, ctx)).toMatchObject({ ok: false, error: { code: 'attachment_too_large' } });
  });

  it('passes attachment bytes through without decoding them in main (the sandboxed renderer decodes images)', async () => {
    const { ctx, runtime } = await setup();
    // Not a decodable PNG past its signature: main never looks inside, it only checks the type and the size.
    const opaque = Buffer.concat([encodePng(1, 1).subarray(0, 8), Buffer.from('arbitrary bytes from the web')]);
    const sha = await runtime.attachments.put(opaque, 'image/png');
    expect(await dispatch('attachments.get', { sha256: sha }, ctx)).toEqual({ ok: true, value: `data:image/png;base64,${opaque.toString('base64')}` });
    expect(Object.keys(ctx.app)).not.toContain('thumbnail');
  });

  it("carries the user's Ask to a thread, and validates it", async () => {
    const { ctx, runtime } = await setup();
    const projectId = runtime.createProject({ name: 'Launch', goal: 'g' });
    const thread = runtime.createThread(projectId, { title: 'Pricing', brief: 'b', workspacePath: join(h.dir, 'pricing') });
    // A running thread reads an Ask at its next step, like any message: nothing starts a run here.
    h.store.append({ project_id: projectId, agent_id: thread, type: 'agent.status_changed', payload: { status: 'running' } });
    expect(await dispatch('threads.send', { id: thread, text: 'How did you price it?', question: true }, ctx)).toMatchObject({ ok: true });
    expect(await dispatch('threads.send', { id: thread, text: 'Carry on.' }, ctx)).toMatchObject({ ok: true });
    expect(h.store.list({ agentId: thread, types: ['message.user'] }).map((e) => e.payload)).toEqual([{ text: 'How did you price it?', question: true }, { text: 'Carry on.' }]);
    expect(await dispatch('threads.send', { id: thread, text: 'x', question: 'yes' }, ctx)).toMatchObject({ ok: false, error: { code: 'invalid_request' } });
  });

  it('drives automations: create, validate, save (409 on a stale base), lay out, run, answer, list runs, files, grants, export, delete', async () => {
    const r = await automationHarness();
    h = r.h;
    server = await startServer({ app: createApp({ runtime: r.rt, store: h.store, models: h.models, token: 't', version: '1.0.0' }), store: h.store, token: 't', port: 0 });
    const { ctx } = makeCtx(new DeskClient({ baseUrl: `http://127.0.0.1:${server.port}`, token: 't' }));
    const projectId = r.projectId;

    const created = await dispatch('automations.create', { projectId, req: { name: 'ask', definition: askDef(), via: 'editor' } }, ctx);
    expect(created).toMatchObject({ ok: true, value: { automation: { name: 'ask', version: 1 } } });
    const id = (created as { value: { automation: { id: string } } }).value.automation.id;
    expect(await dispatch('automations.list', { projectId }, ctx)).toMatchObject({ ok: true, value: [{ id, name: 'ask' }] });
    expect(await dispatch('automations.validate', { projectId, req: { definition: { title: 'x', steps: [] } } }, ctx)).toMatchObject({ ok: true, value: { errors: [expect.objectContaining({ path: 'steps' })] } });
    expect(await dispatch('automations.save', { id, req: { definition: askDef({ title: 'Ask 2' }), base_version: 1, via: 'editor' } }, ctx)).toMatchObject({ ok: true, value: { automation: { version: 2 } } });
    expect(await dispatch('automations.save', { id, req: { definition: askDef(), base_version: 1 } }, ctx)).toMatchObject({ ok: false, error: { status: 409 } });
    expect(await dispatch('automations.layout', { id, layout: { __start: { x: 0, y: 0 }, ask: { x: 10, y: 90 } } }, ctx)).toMatchObject({ ok: true });
    expect(await dispatch('automations.get', { id }, ctx)).toMatchObject({ ok: true, value: { layout: { ask: { x: 10, y: 90 } } } });
    expect(await dispatch('automations.versions', { id }, ctx)).toMatchObject({ ok: true, value: [{ version: 2 }, { version: 1 }] });
    expect(await dispatch('automations.version', { id, version: 1 }, ctx)).toMatchObject({ ok: true, value: { definition: { title: 'Ask then tell' } } });

    const started = await dispatch('automations.run', { id, req: { inputs: {} } }, ctx);
    const runId = (started as { value: { run_id: string } }).value.run_id;
    expect(await dispatch('automations.getRun', { runId }, ctx)).toMatchObject({ ok: true, value: { status: 'waiting', number: 1, at_step: 'Go ahead?' } });
    expect(await dispatch('automations.answer', { runId, stepId: 'ask', req: { decision: 'approve', note: 'go' } }, ctx)).toMatchObject({ ok: true });
    expect(await dispatch('automations.getRun', { runId }, ctx)).toMatchObject({ ok: true, value: { status: 'succeeded' } });
    expect(await dispatch('automations.runs', { id, limit: 5 }, ctx)).toMatchObject({ ok: true, value: [{ kind: 'run', run: { id: runId, number: 1 } }] });
    const files = await dispatch('automations.files', { runId }, ctx);
    expect(files.ok && (files.value as Array<{ name: string }>).map((f) => f.name)).toContain('inputs.json');
    const inputs = await dispatch('automations.file', { runId, path: 'inputs.json' }, ctx);
    expect(inputs.ok && new TextDecoder().decode(inputs.value as Uint8Array)).toContain('{');
    expect(await dispatch('automations.stopStep', { runId, stepId: 'ask' }, ctx)).toMatchObject({ ok: false, error: { status: 409 } });
    expect(await dispatch('automations.answer', { runId, stepId: 'Not An Id', req: { decision: 'approve' } }, ctx)).toMatchObject({ ok: false, error: { code: 'invalid_request' } });

    const grants = [{ tool: 'web_fetch', match: { domain: 'example.com' }, action: 'allow' as const }];
    expect(await dispatch('automations.setGrants', { id, grants }, ctx)).toMatchObject({ ok: true, value: { grants } });
    expect(await dispatch('automations.keepGrants', { id }, ctx)).toMatchObject({ ok: true });
    expect(await dispatch('automations.setEnabled', { id, enabled: true }, ctx)).toMatchObject({ ok: true, value: { enabled: true } });
    expect(await dispatch('automations.restore', { id, version: 1 }, ctx)).toMatchObject({ ok: true, value: { version: 3 } });
    const exp = await dispatch('automations.export', { id }, ctx);
    expect(exp).toMatchObject({ ok: true, value: { format: 'desk-automation/1', name: 'ask' } });
    expect(await dispatch('automations.import', { projectId, exp: { ...(exp as { value: object }).value, name: 'ask-copy' } }, ctx)).toMatchObject({ ok: true, value: { automation: { name: 'ask-copy' } } });
    expect(await dispatch('automations.remove', { id }, ctx)).toMatchObject({ ok: true });
    expect(await dispatch('automations.get', { id }, ctx)).toMatchObject({ ok: false, error: { status: 404 } });
  });

  it('picks files for run inputs, reveals only run folders, and accepts remember on approvals', async () => {
    const { ctx, opened } = await setup();
    const runId = '01HZX3K9Q4T6V8W2Y5B7C0D1EF';
    expect(await dispatch('app.pickFile', { purpose: 'automation-input' }, ctx)).toEqual({ ok: true, value: '/picked/file.txt' });
    expect(await dispatch('app.pickFolder', { purpose: 'automation-input' }, ctx)).toEqual({ ok: true, value: '/picked' });
    expect(await dispatch('app.revealPath', { runId, stepId: 'fetch' }, ctx)).toMatchObject({ ok: true });
    expect(await dispatch('app.revealPath', { runId }, ctx)).toMatchObject({ ok: true });
    expect(opened).toEqual([`reveal:${runId}:fetch`, `reveal:${runId}:`]);
    for (const bad of [{ runId: '../etc' }, { runId, stepId: '../../x' }, { runId: runId.toLowerCase() }]) {
      expect(await dispatch('app.revealPath', bad, ctx)).toMatchObject({ ok: false, error: { code: 'invalid_request' } });
    }
    expect(channels['approvals.resolve'].safeParse({ id: 'a', decision: 'approved', remember: true }).success).toBe(true);
  });
});
