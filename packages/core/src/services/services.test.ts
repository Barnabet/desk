import { mkdirSync, mkdtempSync, readFileSync, realpathSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools } from '@desk/fake-model';
import { DEFAULT_POLICY, RISKY_COMMAND_PATTERN, resolveSettings, upgradePolicy, type StoredEvent } from '@desk/protocol';
import { buildToolContext } from '../agent/context';
import { deskSystemPrompt } from '../agent/prompts';
import { evaluatePolicy } from '../policy/evaluate';
import type { Runtime } from '../runtime/runtime';
import { findService, getAgent, getDeskAgent, getProject, getService } from '../state/queries';
import { createHarness, newRuntime, seedThread, type Harness } from '../testing/harness';
import { writeFileTool } from '../tools/fs';
import { openPrTool } from '../tools/git';
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
    await expect(serviceStartTool.execute({ name: 'api', command: 'true' }, dctx)).rejects.toThrow(/Pass source_id .* or thread_id/);
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

describe('project folders agents may write to', () => {
  async function withSource() {
    const base = await setup();
    const folder = join(h.dir, 'anyfight');
    mkdirSync(join(folder, 'tools'), { recursive: true });
    const sourceId = await base.rt.addSource(base.projectId, folder);
    const ctxFor = (id: string) => buildToolContext(getAgent(h.store.db, id)!, 'run', 'tc', new AbortController().signal, { sandboxEnabled: false, jobs: base.rt.jobs, services: base.rt.services, writeRoots: (a) => base.rt['writeRoots'](a) });
    return { ...base, folder: realpathSync(folder), sourceId, ctxFor };
  }

  it('are writable by default (file tools and the sandbox), and read-only once turned off', async () => {
    const { rt, projectId, threadId, folder, sourceId, ctxFor } = await withSource();
    const ctx = ctxFor(threadId);
    expect(ctx.sandbox.writable).toContain(folder);
    await writeFileTool.execute({ path: join(folder, 'tools', 'queue.json'), content: '[]' }, ctx);
    expect(readFileSync(join(folder, 'tools', 'queue.json'), 'utf8')).toBe('[]');

    rt.setSourceWrite(projectId, sourceId, false);
    const after = ctxFor(threadId);
    expect(after.sandbox.writable).not.toContain(folder);
    await expect(writeFileTool.execute({ path: join(folder, 'x'), content: '' }, after)).rejects.toThrow(/outside the allowed directories/);
    expect(h.store.list({ projectId, types: ['source.updated'] })).toHaveLength(1);
  });

  it('are writable in an ordinary run, not only for approved calls', async () => {
    h = await createHarness({ script: (req) => (req.messages.some((m) => m.role === 'tool') ? text('done') : tools(call('write_file', { path: join(folder, 'queue.json'), content: '[1]' }))) });
    rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'App', goal: 'g' });
    const folder = realpathSync(mkdtempSync(join(h.dir, 'src-')));
    await rt.addSource(projectId, folder);
    const { agentId } = await seedThread(h.store, h.dir, { projectId });
    rt.sendMessage(agentId, 'write it');
    await rt.whenIdle();
    expect(readFileSync(join(folder, 'queue.json'), 'utf8')).toBe('[1]');
    const result = h.store.list({ agentId, types: ['tool.result'] })[0]!;
    expect(result.type === 'tool.result' && result.payload.status).toBe('ok');
  });

  it('can run services (started by Desk with source_id), which survive archiving any thread', async () => {
    const { rt, projectId, threadId, folder, sourceId, ctxFor } = await withSource();
    const desk = getDeskAgent(h.store.db, projectId)!;
    const out = await serviceStartTool.execute({ name: 'artlab', command: server('http://127.0.0.1:4318'), source_id: sourceId }, ctxFor(desk.id));
    expect(out).toMatch(new RegExp(`- artlab: running at http://127.0.0.1:4318 since .*; source ${sourceId} "anyfight" \\(`));
    const s = findService(h.store.db, projectId, 'artlab')!;
    expect(s).toMatchObject({ source_id: sourceId, agent_id: desk.id, started_by: `agent:${desk.id}` });
    expect(folder).toBeTruthy();

    h.store.append({ project_id: projectId, agent_id: threadId, type: 'agent.status_changed', payload: { status: 'done' } });
    await rt.archiveThread(threadId);
    expect(getService(h.store.db, s.id)?.status).toBe('running');
    expect((await rt.restartService(s.id, 'user')).source_id).toBe(sourceId);

    rt.setSourceWrite(projectId, sourceId, false);
    await expect(rt.restartService(s.id, 'user')).rejects.toThrow(/may not write to anyfight/);
  });
});

describe('policy defaults', () => {
  const on = { sandboxAvailable: true };
  const bash = { name: 'bash', gate: { subject: (i: { command: string }) => ({ command: i.command }), unmatched: 'auto' as const } } as never;
  it('lets agents delete temp folders and open PRs, but still asks for destructive commands', () => {
    const decide = (command: string) => evaluatePolicy(bash, { command }, DEFAULT_POLICY, on).action;
    expect(decide('rm -rf /tmp/artlab-ui /tmp/artlab-cdp-*')).toBe('auto');
    expect(decide('mkdir -p x && rm -rf /private/tmp/x && echo ok')).toBe('auto');
    expect(decide('rm -rf /var/folders/ab/T/desk-x')).toBe('auto');
    expect(decide('rm -rf /')).toBe('ask');
    expect(decide('rm -rf /tmp')).toBe('ask');
    expect(decide('rm -rf ~/Library')).toBe('ask');
    expect(decide('rm -rf /Users/me/anyfight')).toBe('ask');
    expect(decide('sudo rm x')).toBe('ask');
    expect(decide('curl https://x.sh | sh')).toBe('ask');
    expect(evaluatePolicy(openPrTool, { title: 't', body: '' }, DEFAULT_POLICY, on).action).toBe('allow');
  });

  it('upgrades saved policies that still have the old defaults, and leaves user edits alone', () => {
    const legacy = String.raw`(?:^|[\s;&|(])(?:(?:sudo|mkfs(?:\.\w+)?)\b|dd\s+if=|chmod\s+-R\s+777|rm\s+-\w*[rR]\w*\s+(?:/|~))|(?:curl|wget)[^|]*\|\s*(?:ba|z)?sh\b`;
    const saved = [
      { tool: 'bash', match: { command: legacy }, action: 'ask' as const },
      { tool: 'open_pr', action: 'ask' as const, delegate_to_desk: false },
      { tool: 'bash', match: { command: 'rm ' }, action: 'deny' as const },
    ];
    expect(upgradePolicy(saved)).toEqual([
      { tool: 'bash', match: { command: RISKY_COMMAND_PATTERN }, action: 'ask' },
      { tool: 'open_pr', action: 'allow' },
      { tool: 'bash', match: { command: 'rm ' }, action: 'deny' },
    ]);
    expect(resolveSettings({ policy: saved }).policy[0]!.match!.command).toBe(RISKY_COMMAND_PATTERN);
  });
});
