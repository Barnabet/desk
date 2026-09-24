import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createHarness, newRuntime, type Harness } from '@desk/core/testing';
import { createApp, startServer, type RunningServer } from '@desk/daemon';
import { ApiError, DaemonUnavailable, DeskClient } from './index';
import { checkDaemon, clientFromDataDir, defaultDataDir, readDaemonInfo } from './node';

let h: Harness;
let server: RunningServer | undefined;
afterEach(async () => {
  await server?.close();
  server = undefined;
  await h?.cleanup();
});

const TOKEN = 'client-token';
async function setup() {
  h = await createHarness();
  const runtime = newRuntime(h);
  server = await startServer({ app: createApp({ runtime, store: h.store, models: h.models, token: TOKEN, version: '1.0.0' }), store: h.store, token: TOKEN, port: 0 });
  const baseUrl = `http://127.0.0.1:${server.port}`;
  return { runtime, baseUrl, client: new DeskClient({ baseUrl, token: TOKEN }) };
}

describe('DeskClient', () => {
  it('covers projects, threads, attention, skills and raw files', async () => {
    const { client, runtime } = await setup();
    expect((await client.health()).protocol_version).toBe(1);
    const created = await client.projects.create({ name: 'Onboarding', goal: 'g' });
    const id = created.project.id;
    expect((await client.projects.list()).map((p) => p.name)).toEqual(['Onboarding']);
    expect((await client.projects.get(id)).desk?.role).toBe('desk');
    expect((await client.projects.update(id, { goal: 'new' })).goal).toBe('new');
    expect(await client.overview()).toHaveLength(1);
    expect((await client.attention.list()).items).toEqual([]);
    expect(await client.projects.plan(id)).toBeNull();

    const t = runtime.createThread(id, { title: 'T', brief: 'b', workspacePath: join(h.dir, 'ws') });
    writeFileSync(join(h.dir, 'ws', 'out.txt'), 'bytes!');
    expect((await client.threads.list(id)).map((x) => x.id)).toEqual([t]);
    expect(await client.threads.files(t)).toEqual([{ name: 'out.txt', path: 'out.txt', type: 'file', size: 6 }]);
    expect(new TextDecoder().decode(await client.threads.file(t, 'out.txt'))).toBe('bytes!');

    await client.skills.save({}, 'weekly-report', { description: 'Weekly', instructions: 'v1' });
    await client.skills.save({ projectId: id }, 'weekly-report', { description: 'Project flavour', instructions: 'p1' });
    expect((await client.skills.get({ projectId: id }, 'weekly-report')).scope).toBe('project');
    expect((await client.skills.version({}, 'weekly-report', 1)).instructions).toBe('v1');
    expect((await client.skills.history({}, 'weekly-report')).map((v) => v.version)).toEqual([1]);

    const mem = await client.memory.add(id, { kind: 'fact', content: 'Launch in October' });
    expect((await client.memory.list(id, 'October')).map((m) => m.id)).toEqual([mem.id]);
    const up = await client.library.upload(id, { name: 'brief.md', content_base64: Buffer.from('# Brief').toString('base64') });
    expect(new TextDecoder().decode(await client.library.file(id, up.path))).toBe('# Brief');
    expect((await client.config.endpoint().catch((e: ApiError) => e.status))).toBe(501);
  });

  it('maps errors, retries once after refreshing credentials on 401, and reports an unreachable daemon', async () => {
    const { baseUrl } = await setup();
    let refreshed = 0;
    const client = new DeskClient({ baseUrl, token: 'stale', refresh: async () => (refreshed++, { baseUrl, token: TOKEN }) });
    expect(await client.projects.list()).toEqual([]);
    expect(refreshed).toBe(1);
    const err = await client.projects.get('nope').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect([err.status, err.code]).toEqual([404, 'not_found']);
    const bad = await new DeskClient({ baseUrl, token: 'wrong' }).projects.list().catch((e) => e);
    expect([bad.status, bad.code]).toEqual([401, 'unauthorized']);
    await expect(new DeskClient({ baseUrl: 'http://127.0.0.1:9', token: 'x' }).projects.list()).rejects.toBeInstanceOf(DaemonUnavailable);
  });
  it('follows the daemon to a new port after refreshing credentials', async () => {
    const { baseUrl } = await setup();
    const client = new DeskClient({ baseUrl: 'http://127.0.0.1:9', token: TOKEN, refresh: async () => ({ baseUrl, token: TOKEN }) });
    expect(await client.projects.list()).toEqual([]);
    expect(client.baseUrl).toBe(baseUrl);
  });
});

describe('node discovery', () => {
  it('finds the data dir per platform and reads daemon.json', async () => {
    expect(defaultDataDir({ DESK_DATA_DIR: '/x' }, 'darwin', '/h')).toBe('/x');
    expect(defaultDataDir({}, 'darwin', '/h')).toBe('/h/Library/Application Support/Desk');
    expect(defaultDataDir({ APPDATA: 'C:\\Users\\u\\AppData\\Roaming' }, 'win32', 'C:\\Users\\u')).toBe(join('C:\\Users\\u\\AppData\\Roaming', 'Desk'));
    expect(defaultDataDir({}, 'linux', '/h')).toBe('/h/.local/share/desk');

    const { baseUrl } = await setup();
    const dir = mkdtempSync(join(tmpdir(), 'desk-client-'));
    try {
      expect(readDaemonInfo(dir)).toBeNull();
      expect(() => clientFromDataDir(dir)).toThrow(/not running/);
      const port = Number(new URL(baseUrl).port);
      writeFileSync(join(dir, 'daemon.json'), JSON.stringify({ port, token: 'old', pid: 1, version: '1.0.0' }));
      const client = clientFromDataDir(dir);
      writeFileSync(join(dir, 'daemon.json'), JSON.stringify({ port, token: TOKEN, pid: 1, version: '1.0.0' }));
      expect(await client.projects.list()).toEqual([]);
      expect((await checkDaemon(client)).protocol_version).toBe(1);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
