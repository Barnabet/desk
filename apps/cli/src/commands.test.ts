import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { call, startFakeModel, text, tools, type FakeModelServer } from '@desk/fake-model';
import { SEED_MODELS } from '@desk/core';
import { FAKE_MODEL, seedThread } from '@desk/core/testing';
import { daemonPaths, startDaemon, type RunningDaemon } from '@desk/daemon';
import { runCli } from './commands';
import { plistFor } from './launchd';

let dir: string;
let fake: FakeModelServer;
let daemon: RunningDaemon;
beforeEach(async () => {
  dir = realpathSync(mkdtempSync(join(tmpdir(), 'desk-cli-')));
  fake = await startFakeModel((req) =>
    req.model === FAKE_MODEL.id
      ? tools(call('bash', { command: 'sudo true' }))
      : req.messages.some((m) => m.role === 'tool')
        ? text('Dispatched a scout.')
        : tools(call('spawn_thread', { title: 'Scout', brief: 'look' })),
  );
  writeFileSync(daemonPaths(dir).models, JSON.stringify([...SEED_MODELS, FAKE_MODEL]));
  daemon = await startDaemon({ dataDir: dir, port: 0, modelConfig: { baseURL: fake.url, apiKey: 'k' }, sandboxAvailable: false });
});
afterEach(async () => {
  await daemon.stop();
  await fake.close();
  rmSync(dir, { recursive: true, force: true });
});

async function cli(...argv: string[]) {
  let out = '';
  let err = '';
  const code = await runCli(argv, { out: (s) => (out += s), err: (s) => (err += s), dataDir: dir });
  return { code, out, err };
}

describe('desk CLI', () => {
  it('creates, lists, shows and configures projects', async () => {
    const created = await cli('project', 'new', 'Launch', '--goal', 'Ship the launch', '--source', dir);
    expect(created.code).toBe(0);
    const id = /Created project (\S+)/.exec(created.out)![1]!;
    expect((await cli('project', 'list')).out).toContain(`${id}  Launch — Ship the launch`);
    expect((await cli('project', 'set', 'Launch', 'check_in=minimal', `thread_model=${FAKE_MODEL.id}`, 'review_rounds=1')).code).toBe(0);
    const shown = (await cli('project', 'show', id)).out;
    expect(shown).toContain('Goal: Ship the launch');
    expect(shown).toContain('check_in=minimal');
    expect((await cli('project', 'set', 'Launch', 'thread_reasoning_effort=high')).code).toBe(0);
    expect((await cli('project', 'show', id)).out).toContain('thread_reasoning_effort=high');
    expect((await cli('project', 'set', 'Launch', 'thread_reasoning_effort=max')).code).toBe(1);
    expect((await cli('project', 'set', 'Launch', 'thread_reasoning_effort=default')).code).toBe(0);
    expect((await cli('project', 'show', id)).out).toContain('thread_reasoning_effort=default');
    expect(shown).toMatch(/Sources:\n- \S+ desk-cli-/);
    expect((await cli('project', 'show', 'nope')).code).toBe(1);
  });

  it('talks to Desk, lists threads, and resolves approvals', async () => {
    await cli('project', 'new', 'Ops', '--goal', 'g');
    await cli('project', 'set', 'Ops', `thread_model=${FAKE_MODEL.id}`);
    const said = await cli('say', 'Ops', 'Please', 'scout');
    expect(said.code).toBe(0);
    expect(said.out).toContain('desk › Dispatched a scout.');
    await daemon.runtime.whenIdle();
    expect((await cli('threads', 'Ops')).out).toMatch(/"Scout" \[waiting\]/);
    const approvals = (await cli('approvals', 'Ops')).out;
    const approvalId = /^(\S+)\s+bash/m.exec(approvals)![1]!;
    expect(approvals).toContain('sudo true');
    expect((await cli('deny', approvalId, 'no', 'sudo')).out).toContain(`Denied ${approvalId}`);
    expect((await cli('approve', approvalId)).code).toBe(1);
  });

  it('manages memory and shows usage', async () => {
    await cli('project', 'new', 'Mem', '--goal', 'g');
    expect((await cli('remember', 'Mem', 'decision', 'Release', 'on', 'Friday')).out).toMatch(/Saved memory \S+/);
    expect((await cli('memory', 'Mem')).out).toContain('[decision] Release on Friday');
    expect((await cli('memory', 'Mem', 'friday')).out).toContain('Release on Friday');
    await cli('say', 'Mem', 'hi', '--no-wait');
    await daemon.runtime.whenIdle();
    expect((await cli('usage', 'Mem')).out).toMatch(/claude-opus-5-5\s+\d+\s+\d+/);
  });

  it('lists services and stops, starts and tails one', async () => {
    const id = /Created project (\S+)/.exec((await cli('project', 'new', 'Web', '--goal', 'g')).out)![1]!;
    expect((await cli('services', 'Web')).out).toContain('No services');
    const { agentId } = await seedThread(daemon.store, dir, { projectId: id });
    await daemon.runtime.startService(id, { name: 'web', command: `node -e "console.log('up'); setInterval(() => {}, 1000)"`, threadId: agentId, by: 'user' });
    expect((await cli('services', 'Web')).out).toMatch(/^web {2}running/);
    expect((await cli('service', 'stop', 'Web', 'web')).out).toContain('web  stopped (requested)');
    expect((await cli('service', 'start', 'Web', 'web')).out).toContain('web  running');
    for (let i = 0; i < 100 && !(await cli('service', 'logs', 'Web', 'web')).out.includes('up'); i++) await new Promise((r) => setTimeout(r, 25));
    expect((await cli('service', 'logs', 'Web', 'web')).out).toContain('up');
    expect((await cli('service', 'stop', 'Web', 'nope')).code).toBe(1);
  });

  it('reports status', async () => {
    const s = await cli('status');
    expect(s.code).toBe(0);
    expect(s.out).toContain(`deskd 1.0.0 on 127.0.0.1:${daemon.port}`);
  });
});

describe('launchd plist', () => {
  it('runs deskd with node and the tsx loader, kept alive, logging to the data dir', () => {
    const plist = plistFor({ nodePath: '/usr/local/bin/node', loader: '/repo/node_modules/tsx/dist/loader.mjs', entry: '/repo/apps/daemon/src/main.ts', dataDir: '/data', cwd: '/repo' });
    expect(plist).toContain('<string>dev.desk.deskd</string>');
    expect(plist).toContain('<string>/usr/local/bin/node</string>');
    expect(plist).toContain('<string>--import</string>');
    expect(plist).toContain('<string>/repo/apps/daemon/src/main.ts</string>');
    expect(plist).toContain('<key>KeepAlive</key>');
    expect(plist).toContain('/data/logs/deskd.launchd.log');
  });

  it('imports, lists, shows and removes skills', async () => {
    const src = join(dir, 'my-skill');
    mkdirSync(join(src, 'scripts'), { recursive: true });
    writeFileSync(join(src, 'SKILL.md'), '---\nname: my-skill\ndescription: Does my thing\n---\nRun scripts/go.sh\n');
    writeFileSync(join(src, 'scripts', 'go.sh'), 'echo go');
    expect((await cli('skill', 'import', src)).out).toContain('Imported skill v1');
    expect((await cli('skills')).out).toContain('my-skill (global, v1) — Does my thing');
    const shown = (await cli('skill', 'show', 'my-skill')).out;
    expect(shown).toContain('Files: SKILL.md, scripts/go.sh');
    expect(shown).toContain('Run scripts/go.sh');
    await cli('project', 'new', 'Ops', '--goal', 'g');
    expect((await cli('skills', 'Ops')).out).toContain('my-skill (global, v1)');
    expect((await cli('skill', 'rm', 'my-skill')).code).toBe(0);
    expect((await cli('skills')).out).toContain('No skills');
    expect((await cli('skill', 'restore', 'my-skill', '1')).out).toContain('Restored my-skill v1 as v2');
    expect((await cli('skill', 'history', 'my-skill')).out).toContain('v2 (current) — Does my thing');
  });
});
