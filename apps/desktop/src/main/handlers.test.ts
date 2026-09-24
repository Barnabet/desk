import { afterEach, describe, expect, it, vi } from 'vitest';
import { DaemonNotRunning, DeskClient } from '@desk/client';
import { createHarness, newRuntime, type Harness } from '@desk/core/testing';
import { createApp, startServer, type RunningServer } from '@desk/daemon';
import { channels } from '../shared/ipc';
import { initialGlobalState } from '../shared/state';
import { dispatch, handlers, type HandlerContext } from './handlers';

let h: Harness;
let server: RunningServer | undefined;
afterEach(async () => {
  await server?.close();
  server = undefined;
  await h?.cleanup();
});

async function setup(overrides: Partial<HandlerContext> = {}) {
  h = await createHarness();
  const runtime = newRuntime(h);
  server = await startServer({ app: createApp({ runtime, store: h.store, models: h.models, token: 't', version: '1.0.0' }), store: h.store, token: 't', port: 0 });
  const client = new DeskClient({ baseUrl: `http://127.0.0.1:${server.port}`, token: 't' });
  const opened: string[] = [];
  const watched: Array<[number, string, number]> = [];
  const ctx: HandlerContext = {
    senderId: 7,
    client: () => client,
    broker: { snapshot: () => initialGlobalState(), watch: async (s, p, a) => void watched.push([s, p, a]), unwatch: () => {} },
    daemon: { status: vi.fn(), start: vi.fn(), restart: vi.fn(), stop: vi.fn() },
    app: {
      info: () => ({ version: '1.0.0', platform: 'darwin', packaged: false, dataDir: '/d' }),
      openExternal: async (url) => void opened.push(url),
      pickFolder: async () => '/picked',
      revealLogs: async () => {},
      saveFile: async () => true,
      openMain: (route) => void opened.push(`main:${route ?? ''}`),
      settings: () => ({ notifications: true }),
      updateSettings: (p) => ({ notifications: p.notifications ?? true }),
    },
    ...overrides,
  };
  return { ctx, runtime, opened, watched };
}

describe('IPC dispatch', () => {
  it('has a handler for every channel', () => {
    expect(Object.keys(handlers).sort()).toEqual(Object.keys(channels).sort());
  });

  it('runs validated operations against deskd', async () => {
    const { ctx } = await setup();
    const created = await dispatch('projects.create', { name: 'Launch', goal: 'g' }, ctx);
    expect(created).toMatchObject({ ok: true, value: { project: { name: 'Launch' } } });
    const list = await dispatch('projects.list', {}, ctx);
    expect(list.ok && (list.value as Array<{ name: string }>).map((p) => p.name)).toEqual(['Launch']);
    expect(await dispatch('broker.watch', { projectId: 'p1', afterSeq: 3 }, ctx)).toEqual({ ok: true, value: { ok: true } });
  });

  it('rejects unknown channels and invalid payloads', async () => {
    const { ctx } = await setup();
    expect(await dispatch('nope', {}, ctx)).toMatchObject({ ok: false, error: { code: 'unknown_channel' } });
    expect(await dispatch('projects.get', { id: 5 }, ctx)).toMatchObject({ ok: false, error: { code: 'invalid_request' } });
    expect(await dispatch('__proto__', {}, ctx)).toMatchObject({ ok: false, error: { code: 'unknown_channel' } });
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
});
