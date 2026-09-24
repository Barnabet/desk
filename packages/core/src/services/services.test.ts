import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it } from 'vitest';
import { DEFAULT_POLICY, type StoredEvent } from '@desk/protocol';
import { buildToolContext } from '../agent/context';
import { deskSystemPrompt } from '../agent/prompts';
import { evaluatePolicy } from '../policy/evaluate';
import type { Runtime } from '../runtime/runtime';
import { findService, getAgent, getDeskAgent, getProject, getService } from '../state/queries';
import { createHarness, newRuntime, seedThread, type Harness } from '../testing/harness';
import { serviceListTool, serviceStartTool, serviceStopTool } from '../tools/services';
import { detectLoopbackUrl, sameCommand, stripAnsi } from './manager';

let h: Harness;
let rt: Runtime | undefined;
afterEach(async () => {
  await rt?.shutdown();
  rt = undefined;
  await h?.cleanup();
});

/** A node one-liner that prints a URL and keeps running (like a dev server). */
const server = (url: string) => `node -e "console.log('  ➜  Local:   ${url}'); setInterval(() => {}, 1000)"`;

async function until<T>(fn: () => T | undefined | false, ms = 5000): Promise<T> {
  const deadline = Date.now() + ms;
  for (;;) {
    const v = fn();
    if (v) return v;
    if (Date.now() > deadline) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 25));
  }
}

const alive = (pid: number | null) => {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
};

async function setup() {
  h = await createHarness();
  rt = newRuntime(h);
  const projectId = rt.createProject({ name: 'App', goal: 'Ship it' });
  const thread = await seedThread(h.store, h.dir, { projectId });
  return { rt, projectId, threadId: thread.agentId, workspace: thread.workspace };
}

const serviceEvents = (projectId: string) =>
  h.store.list({ projectId, types: ['service.started', 'service.url', 'service.exited', 'service.stopped'] }).map((e: StoredEvent) => e.type);

describe('loopback URL detection', () => {
  it('finds dev-server URLs, rewrites 0.0.0.0, and ignores other hosts', () => {
    expect(detectLoopbackUrl('  \x1b[32m➜\x1b[39m  Local:   \x1b[36mhttp://localhost:\x1b[1m5173\x1b[22m/\x1b[39m')).toBe('http://localhost:5173');
    expect(detectLoopbackUrl('Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)')).toBe('http://localhost:8000');
    expect(detectLoopbackUrl('Listening at http://127.0.0.1:3000/app.')).toBe('http://127.0.0.1:3000/app');
    expect(detectLoopbackUrl('see https://example.com:443/ for docs')).toBeNull();
    expect(stripAnsi('\x1b[1mbold\x1b[0m')).toBe('bold');
  });

  it('recognises a service command in a ps line after the shell parsed it', () => {
    const cmd = `node -e "console.log('  ➜  Local:   http://localhost:7002'); setInterval(() => {}, 1000)"`;
    expect(sameCommand("node -e console.log('  M-bM^^M^\\  Local:   http://localhost:7002'); setInterval(() => {}, 1000)", cmd)).toBe(true);
    expect(sameCommand('node server.js --port 7002', cmd)).toBe(false);
    expect(sameCommand('anything', '!!')).toBe(false);
  });
});

describe('project services', () => {
  it('starts in the thread workspace, records the URL and the log, and stops without also reporting an exit', async () => {
    const { rt, projectId, threadId } = await setup();
    const started = await rt.startService(projectId, { name: 'frontend', command: server('http://0.0.0.0:5173/'), threadId, by: 'user' });
    expect(started).toMatchObject({ name: 'frontend', status: 'running', agent_id: threadId, cwd: '.', started_by: 'user' });
    const withUrl = await until(() => (getService(h.store.db, started.id)?.url ? getService(h.store.db, started.id) : undefined));
    expect(withUrl!.url).toBe('http://localhost:5173');
    expect(readFileSync(rt.serviceLogFile(projectId, started.id), 'utf8')).toContain('Local:');
    expect(rt.serviceLogs(started.id, 5).text).toContain('Local:   http://0.0.0.0:5173/');

    const stopped = await rt.stopService(started.id, 'user');
    expect(stopped).toMatchObject({ status: 'stopped', stop_reason: 'requested' });
    expect(alive(started.pid)).toBe(false);
    await new Promise((r) => setTimeout(r, 100));
    expect(serviceEvents(projectId)).toEqual(['service.started', 'service.url', 'service.stopped']);
  });

  it('records an exit with its code, and restarting a name keeps one row', async () => {
    const { rt, projectId, threadId } = await setup();
    const s = await rt.startService(projectId, { name: 'worker', command: 'echo boom; exit 3', threadId, by: 'user' });
    await until(() => getService(h.store.db, s.id)?.status === 'exited');
    expect(getService(h.store.db, s.id)).toMatchObject({ exit_code: 3, exit_signal: null });
    expect(rt.serviceLogs(s.id, 10).text).toContain('boom');

    const first = await rt.startService(projectId, { name: 'worker', command: server('http://localhost:9001'), threadId, by: 'agent:x' });
    const second = await rt.restartService(first.id, 'user');
    expect(second.id).toBe(s.id);
    expect(alive(first.pid)).toBe(false);
    expect(alive(second.pid)).toBe(true);
    expect(serviceEvents(projectId).slice(-3)).toEqual(['service.started', 'service.stopped', 'service.started']);
    expect(h.store.list({ projectId, types: ['service.stopped'] }).at(-1)!.payload).toMatchObject({ reason: 'restart', by: 'user' });
  });

  it('refuses bad names, a cwd outside the workspace, and threads of other projects or without a workspace', async () => {
    const { rt, projectId, threadId } = await setup();
    await expect(rt.startService(projectId, { name: 'Front End', command: 'true', threadId, by: 'user' })).rejects.toThrow(/Invalid service name/);
    await expect(rt.startService(projectId, { name: 'x', command: 'true', cwd: '../..', threadId, by: 'user' })).rejects.toThrow(/inside the thread's workspace/);
    const desk = getDeskAgent(h.store.db, projectId)!;
    await expect(rt.startService(projectId, { name: 'x', command: 'true', threadId: desk.id, by: 'user' })).rejects.toThrow(/Unknown thread/);
  });

  it('keeps running when its thread finishes, and stops when the thread is archived', async () => {
    const { rt, projectId, threadId } = await setup();
    const s = await rt.startService(projectId, { name: 'api', command: server('http://localhost:8000'), threadId, by: 'user' });
    h.store.append({ project_id: projectId, agent_id: threadId, type: 'agent.status_changed', payload: { status: 'done' } });
    expect(getService(h.store.db, s.id)?.status).toBe('running');
    await rt.archiveThread(threadId);
    expect(getService(h.store.db, s.id)).toMatchObject({ status: 'stopped', stop_reason: 'thread_archived' });
    expect(alive(s.pid)).toBe(false);
    await expect(rt.restartService(s.id, 'user')).rejects.toThrow(/archived/);
  });

  it('stops everything on shutdown, and after a crash reaps the orphan and marks it stopped', async () => {
    const { rt: first, projectId, threadId } = await setup();
    const a = await first.startService(projectId, { name: 'a', command: server('http://localhost:7001'), threadId, by: 'user' });
    await first.shutdown();
    expect(getService(h.store.db, a.id)).toMatchObject({ status: 'stopped', stop_reason: 'daemon_shutdown' });
    expect(alive(a.pid)).toBe(false);

    // A daemon that died without shutting down: its service is still running and still marked so.
    const crashed = newRuntime(h);
    const b = await crashed.startService(projectId, { name: 'b', command: server('http://localhost:7002'), threadId, by: 'user' });
    rt = newRuntime(h);
    rt.recover();
    expect(getService(h.store.db, b.id)).toMatchObject({ status: 'stopped', stop_reason: 'daemon_restart' });
    await until(() => !alive(b.pid));
    await crashed.serviceProcs.stopAll(0);
  });

  it('archiving the project stops its services', async () => {
    const { rt, projectId, threadId } = await setup();
    const s = await rt.startService(projectId, { name: 'api', command: server('http://localhost:8001'), threadId, by: 'user' });
    rt.archiveProject(projectId);
    expect(getService(h.store.db, s.id)).toMatchObject({ status: 'stopped', stop_reason: 'project_archived' });
    await until(() => !alive(s.pid));
  });
});

describe('service tools', () => {
  it('threads run in their own workspace; Desk must name the thread; Desk sees services in its prompt', async () => {
    const { rt, projectId, threadId } = await setup();
    const thread = getAgent(h.store.db, threadId)!;
    const ctxFor = (agent: typeof thread) => buildToolContext(agent, 'run', 'tc', new AbortController().signal, { sandboxEnabled: false, jobs: rt.jobs, services: rt.services });

    const tctx = ctxFor(thread);
    await expect(serviceStartTool.execute({ name: 'web', command: 'true', thread_id: 'other' }, tctx)).rejects.toThrow(/own workspace/);
    const out = await serviceStartTool.execute({ name: 'web', command: server('http://localhost:5174') }, tctx);
    expect(out).toMatch(/- web: running at http:\/\/localhost:5174 since .*; thread .* "Test thread"/);
    expect(out).toContain('Last log lines:');

    const desk = getDeskAgent(h.store.db, projectId)!;
    const dctx = ctxFor(desk);
    await expect(serviceStartTool.execute({ name: 'api', command: 'true' }, dctx)).rejects.toThrow(/Pass thread_id/);
    expect(await serviceListTool.execute({}, dctx)).toContain('- web: running');
    expect(await serviceStopTool.execute({ name: 'web' }, dctx)).toMatch(/- web: stopped \(requested\)/);
    await expect(serviceStopTool.execute({ name: 'nope' }, dctx)).rejects.toThrow(/No service named "nope". Services: web/);
    expect(findService(h.store.db, projectId, 'web')?.started_by).toBe(`agent:${threadId}`);

    const prompt = deskSystemPrompt({ db: h.store.db, agent: desk, project: getProject(h.store.db, projectId)!, libraryDir: h.dir, skills: rt.skills } as never);
    expect(prompt).toContain('## Services\n- web: stopped (requested)');
  });

  it('is policy-gated like bash_background, including saved policies that predate it', () => {
    const on = { sandboxAvailable: true };
    expect(evaluatePolicy(serviceStartTool, { name: 'x', command: 'npm run dev' }, DEFAULT_POLICY, on).action).toBe('auto');
    expect(evaluatePolicy(serviceStartTool, { name: 'x', command: 'sudo npm run dev' }, DEFAULT_POLICY, on).action).toBe('ask');
    expect(evaluatePolicy(serviceStartTool, { name: 'x', command: 'npm run dev' }, [], { sandboxAvailable: false }).action).toBe('ask');
    expect(evaluatePolicy(serviceStartTool, { name: 'x', command: 'npm start' }, [{ tool: 'bash_background', action: 'deny' }], on).action).toBe('deny');
  });
});
