import { spawn, type ChildProcess } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';
import { call, startFakeModel, text, tools, type ChatRequest, type FakeModelServer } from '@desk/fake-model';
import { SEED_MODELS } from '@desk/core';
import { FAKE_MODEL } from '@desk/core/testing';
import { daemonPaths } from './paths';

const ROOT = fileURLToPath(new URL('../../..', import.meta.url));
let dir: string;
let fake: FakeModelServer;
let child: ChildProcess | undefined;
afterEach(async () => {
  child?.kill('SIGKILL');
  await fake?.close();
  rmSync(dir, { recursive: true, force: true });
});

function startChild(): ChildProcess {
  rmSync(daemonPaths(dir).daemonJson, { force: true });
  return spawn(process.execPath, ['--import', join(ROOT, 'node_modules/tsx/dist/loader.mjs'), join(ROOT, 'apps/daemon/src/main.ts'), '--data-dir', dir, '--port', '0'], {
    cwd: ROOT,
    env: { ...process.env, DESK_OPENAI_BASE_URL: fake.url, DESK_OPENAI_API_KEY: 'k' },
    stdio: 'ignore',
  });
}
async function until<T>(fn: () => Promise<T | undefined> | T | undefined, ms = 20_000): Promise<T> {
  const start = Date.now();
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() - start > ms) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 100));
  }
}
const info = () => (existsSync(daemonPaths(dir).daemonJson) ? JSON.parse(readFileSync(daemonPaths(dir).daemonJson, 'utf8')) : undefined);
async function api(path: string, init: RequestInit = {}): Promise<any> {
  const d = info();
  const res = await fetch(`http://127.0.0.1:${d.port}/v1${path}`, { ...init, headers: { authorization: `Bearer ${d.token}`, 'content-type': 'application/json' } });
  return res.json();
}

describe('real daemon crash', () => {
  it('recovers a thread whose tool was running when deskd was SIGKILLed', async () => {
    dir = realpathSync(mkdtempSync(join(tmpdir(), 'deskd-crash-')));
    writeFileSync(daemonPaths(dir).models, JSON.stringify([...SEED_MODELS, FAKE_MODEL]));
    const hasInterrupted = (req: ChatRequest) => req.messages.some((m) => m.role === 'tool' && String(m.content).includes('restarted'));
    fake = await startFakeModel((req) => {
      if (req.model !== FAKE_MODEL.id) {
        return req.messages.some((m) => m.role === 'tool') ? text('dispatched') : tools(call('spawn_thread', { title: 'Sleeper', brief: 'sleep' }));
      }
      return hasInterrupted(req) ? tools(call('complete', { summary: 'recovered after crash' })) : tools(call('bash', { command: 'sleep 60' }));
    });

    child = startChild();
    await until(info);
    const created = await api('/projects', { method: 'POST', body: JSON.stringify({ name: 'Crash', settings: { thread_model: FAKE_MODEL.id } }) });
    const projectId = created.project.id;
    await api(`/projects/${projectId}/messages`, { method: 'POST', body: JSON.stringify({ text: 'go' }) });
    const threadId = await until(async () => {
      const page = await api(`/projects/${projectId}/events?types=tool.call`);
      return page.events.find((e: any) => e.payload.name === 'bash')?.agent_id as string | undefined;
    });

    child.kill('SIGKILL');
    await new Promise((r) => child!.once('exit', r));
    child = startChild();
    await until(info);

    const thread = await until(async () => {
      const t = await api(`/threads/${threadId}`);
      return t.status === 'done' ? t : undefined;
    });
    expect(thread.result_summary).toBe('recovered after crash');
    const results = await api(`/threads/${threadId}/transcript`);
    expect(results.events.filter((e: any) => e.type === 'tool.result').map((e: any) => e.payload.status)).toEqual(['interrupted', 'ok']);
  }, 60_000);
});
