import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, hang, startFakeModel, text, tools, type FakeModelServer } from '@desk/fake-model';
import { getAgent, SEED_MODELS } from '@desk/core';
import { FAKE_MODEL } from '@desk/core/testing';
import { startDaemon, type RunningDaemon } from './daemon';
import { daemonPaths } from './paths';

let dir: string;
let fake: FakeModelServer | undefined;
const daemons: RunningDaemon[] = [];
afterEach(async () => {
  for (const d of daemons.splice(0)) await d.stop().catch(() => {});
  await fake?.close();
  fake = undefined;
  rmSync(dir, { recursive: true, force: true });
});

async function boot(script: Parameters<typeof startFakeModel>[0] = [], extra: Partial<Parameters<typeof startDaemon>[0]> = {}) {
  dir ??= realpathSync(mkdtempSync(join(tmpdir(), 'deskd-')));
  fake ??= await startFakeModel(script);
  const f = fake;
  writeFileSync(daemonPaths(dir).models, JSON.stringify([...SEED_MODELS, FAKE_MODEL]));
  const d = await startDaemon({ dataDir: dir, port: 0, version: '1.0.0-test', modelConfig: { baseURL: f.url, apiKey: 'secret-key-xyz' }, sandboxAvailable: false, ...extra });
  daemons.push(d);
  return d;
}
const json = async (res: Promise<Response>): Promise<any> => (await res).json();
const api = (d: RunningDaemon, path: string, init: RequestInit = {}) =>
  fetch(`http://127.0.0.1:${d.port}/v1${path}`, { ...init, headers: { authorization: `Bearer ${d.token}`, 'content-type': 'application/json', ...init.headers } });

describe('startDaemon', () => {
  it("keeps agents off deskd's token file, its database, the model credentials file and its port", async () => {
    dir = realpathSync(mkdtempSync(join(tmpdir(), 'deskd-')));
    const home = realpathSync(mkdtempSync(join(tmpdir(), 'deskd-home-')));
    try {
      const d = await boot([], { home });
      const p = daemonPaths(dir);
      expect(d.runtime.guard).toEqual({
        dataDir: dir,
        secrets: [p.daemonJson, p.db, `${p.db}-wal`, `${p.db}-shm`, `${p.db}-journal`, join(home, '.config', 'cliproxyapi.env')],
        readOnly: [join(home, '.gitconfig'), join(process.env.XDG_CONFIG_HOME ?? join(home, '.config'), 'git'), join(home, '.ssh')],
        ports: [d.port],
      });
    } finally {
      rmSync(home, { recursive: true, force: true });
    }
  });

  it('serves the API and writes a private daemon.json without secrets', async () => {
    dir = realpathSync(mkdtempSync(join(tmpdir(), 'deskd-')));
    const d = await boot();
    const info = JSON.parse(readFileSync(daemonPaths(dir).daemonJson, 'utf8'));
    expect(info).toMatchObject({ port: d.port, token: d.token, pid: process.pid, version: '1.0.0-test' });
    expect(statSync(daemonPaths(dir).daemonJson).mode & 0o777).toBe(0o600);
    expect(readFileSync(daemonPaths(dir).daemonJson, 'utf8')).not.toContain('secret-key-xyz');
    expect(d.token).toMatch(/^[0-9a-f]{64}$/);
    expect(await (await fetch(`http://127.0.0.1:${d.port}/v1/health`)).json()).toMatchObject({ version: '1.0.0-test', protocol_version: 1 });
    expect((await api(d, '/projects')).status).toBe(200);
    // The catalog and skill runtimes are wired in: the shipped catalog lists, and runtimes report.
    expect((await json(api(d, '/catalog'))).length).toBe(20);
    expect(await json(api(d, '/system/runtimes'))).toEqual({ bytes: 0, envs: [] });
  });

  it('refuses a second instance and takes over a stale lock', async () => {
    dir = realpathSync(mkdtempSync(join(tmpdir(), 'deskd-')));
    const d = await boot();
    await expect(startDaemon({ dataDir: dir, port: 0, modelConfig: { baseURL: fake!.url, apiKey: 'k' } })).rejects.toThrow(/already running/);
    await d.stop();
    daemons.splice(0);
    writeFileSync(daemonPaths(dir).lock, '999999');
    await boot();
    expect(readFileSync(daemonPaths(dir).lock, 'utf8')).toBe(String(process.pid));
  });

  it('stop() removes daemon.json and the lock', async () => {
    dir = realpathSync(mkdtempSync(join(tmpdir(), 'deskd-')));
    const d = await boot();
    await d.stop();
    daemons.splice(0);
    expect(existsSync(daemonPaths(dir).daemonJson)).toBe(false);
    expect(existsSync(daemonPaths(dir).lock)).toBe(false);
  });

  it('resumes work interrupted by a previous stop', async () => {
    dir = realpathSync(mkdtempSync(join(tmpdir(), 'deskd-')));
    const d1 = await boot([hang()]);
    const created = await json(api(d1, '/projects', { method: 'POST', body: JSON.stringify({ name: 'P', settings: { desk_model: FAKE_MODEL.id } }) }));
    await api(d1, `/projects/${created.project.id}/messages`, { method: 'POST', body: JSON.stringify({ text: 'hello' }) });
    await new Promise((r) => setTimeout(r, 100));
    await d1.stop();
    daemons.splice(0);
    fake!.setScript([text('back online')]);
    const d2 = await boot();
    await d2.runtime.whenIdle();
    const chat = await json(api(d2, `/projects/${created.project.id}/chat`));
    expect(chat.events.some((e: any) => e.type === 'assistant.message' && e.payload.content === 'back online')).toBe(true);
  });

  it('runs stall checks on an interval', async () => {
    dir = realpathSync(mkdtempSync(join(tmpdir(), 'deskd-')));
    const future = Date.now() + 60 * 60_000;
    let now = Date.now();
    const d = await boot(
      (req) => (req.model === FAKE_MODEL.id ? tools(call('wait_for_reply', {})) : text('noted')),
      { stallIntervalMs: 20, now: () => now },
    );
    const created = await json(api(d, '/projects', { method: 'POST', body: JSON.stringify({ name: 'P', settings: { thread_model: FAKE_MODEL.id } }) }));
    const threadId = await d.runtime.spawnThread(created.desk.id, { title: 'T', brief: 'b' });
    await d.runtime.whenIdle();
    expect(getAgent(d.store.db, threadId)?.status).toBe('waiting');
    now = future;
    await new Promise((r) => setTimeout(r, 100));
    await d.runtime.whenIdle();
    const stalled = d.store.list({ projectId: created.project.id, types: ['message.agent'] }).filter((e) => e.type === 'message.agent' && e.payload.kind === 'stalled');
    expect(stalled).toHaveLength(1);
  });

  it('persists model registry changes to models.json', async () => {
    dir = realpathSync(mkdtempSync(join(tmpdir(), 'deskd-')));
    const d = await boot();
    const list = await json(api(d, '/models'));
    await api(d, '/models', { method: 'PUT', body: JSON.stringify([...list, { ...FAKE_MODEL, id: 'another' }]) });
    expect(JSON.parse(readFileSync(daemonPaths(dir).models, 'utf8')).map((m: { id: string }) => m.id)).toContain('another');
  });
});
