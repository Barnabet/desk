import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
  rmSync(`${dir}-src`, { recursive: true, force: true });
});

async function cli(...argv: string[]) {
  let out = '';
  let err = '';
  const code = await runCli(argv, { out: (s) => (out += s), err: (s) => (err += s), dataDir: dir });
  return { code, out, err };
}

describe('desk CLI', () => {
  it('imports, lists, shows, runs, follows, answers, switches and exports automations', async () => {
    const src = join(dir, '..', `${basename(dir)}-src`);
    mkdirSync(src, { recursive: true });
    await cli('project', 'new', 'Auto', '--goal', 'g');
    const file = join(src, 'ask.json');
    const definition = {
      title: 'Ask first',
      inputs: [{ key: 'topic', label: 'Topic', type: 'text', default: 'AI' }],
      triggers: [{ kind: 'schedule', cron: '0 8 * * 1', timezone: 'Europe/Paris', catch_up: 'once' }],
      steps: [{ id: 'ask', title: 'Go ahead?', kind: 'ask', question: 'Publish {{inputs.topic}}?' }],
    };
    writeFileSync(file, JSON.stringify({ format: 'desk-automation/1', name: 'ask', definition }));
    expect((await cli('automation', 'import', 'Auto', file)).out).toContain('Imported ask v1');
    expect((await cli('automations', 'Auto')).out).toContain('ask "Ask first" — off · v1 (not tested) · 0 8 * * 1 (Europe/Paris) · last: never');
    const shown = (await cli('automation', 'show', 'Auto', 'ask')).out;
    expect(shown).toContain('Steps:\n- ask [ask] Go ahead?');
    expect(shown).toContain('Inputs:\n- topic (text) = AI');
    expect((await cli('automation', 'show', 'Auto', 'nope')).err).toContain('No automation named "nope" in Auto');

    const following = cli('automation', 'run', 'Auto', 'ask', '-i', 'topic=robots', '--follow');
    let runId = '';
    await vi.waitFor(async () => {
      const m = /^(\S+)\s+waiting/m.exec((await cli('automation', 'runs', 'Auto', 'ask')).out);
      expect(m).not.toBeNull();
      runId = m![1]!;
    });
    expect((await cli('automation', 'answer', runId, 'ask', 'approve', 'looks', 'good')).out).toBe(`Approved ask in run ${runId}\n`);
    const followed = await following;
    expect(followed.code).toBe(0);
    expect(followed.out).toContain(`Started run ${runId} of ask`);
    expect(followed.out).toContain('? Go ahead? asks: Publish robots?');
    expect(followed.out).toContain(`desk automation answer ${runId} ask approve|reject`);
    expect(followed.out).toContain('• Go ahead? → succeeded');
    expect(followed.out).toMatch(/Run \S+ succeeded/);

    expect((await cli('automation', 'on', 'Auto', 'ask')).out).toBe('Turned on ask (v1 has no succeeded test run)\n');
    expect((await cli('automations', 'Auto')).out).toContain('ask "Ask first" — on');
    expect((await cli('automation', 'off', 'Auto', 'ask')).out).toBe('Turned off ask\n');
    const test = (await cli('automation', 'run', 'Auto', 'ask', '--test')).out;
    const testId = /Started test run (\S+)/.exec(test)![1]!;
    expect((await cli('automation', 'cancel', testId)).out).toBe(`Cancelled run ${testId}\n`);
    const exported = JSON.parse((await cli('automation', 'export', 'Auto', 'ask')).out);
    expect(exported).toMatchObject({ format: 'desk-automation/1', name: 'ask', definition: { title: 'Ask first' } });
    void readFileSync;
  });

  it('creates, lists, shows and configures projects', async () => {
    // A source cannot be the daemon's data dir (`dir` here), so the project's folder sits beside it.
    const src = join(dir, '..', `${basename(dir)}-src`);
    mkdirSync(src);
    const created = await cli('project', 'new', 'Launch', '--goal', 'Ship the launch', '--source', src);
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

  it('shows live threads only', async () => {
    const id = /Created project (\S+)/.exec((await cli('project', 'new', 'Tidy', '--goal', 'g')).out)![1]!;
    const live = daemon.runtime.createThread(id, { title: 'Live', brief: 'b', workspacePath: join(dir, 'live') });
    const old = daemon.runtime.createThread(id, { title: 'Old', brief: 'b', workspacePath: join(dir, 'old') });
    daemon.store.append({ project_id: id, agent_id: old, type: 'agent.status_changed', payload: { status: 'done' } });
    await daemon.runtime.archiveThread(old);
    const shown = (await cli('project', 'show', id)).out;
    expect(shown).toContain(`- ${live} "Live" [idle]`);
    expect(shown).not.toContain('"Old"');
  });

  it('asks a finished thread, which answers without reopening', async () => {
    const id = /Created project (\S+)/.exec((await cli('project', 'new', 'Pricing', '--goal', 'g')).out)![1]!;
    const thread = daemon.runtime.createThread(id, { title: 'Pricing page', brief: 'Price the plans', workspacePath: join(dir, 'pricing'), model: FAKE_MODEL.id });
    daemon.store.append({ project_id: id, agent_id: thread, type: 'agent.status_changed', payload: { status: 'done' } });
    // Only an answer run answers: its latest user turn ends with the runtime's answer-mode line.
    fake.setScript((req) =>
      String(req.messages.findLast((m) => m.role === 'user')?.content ?? '').includes('[Desk runtime — answer mode]') ? text('Per seat.') : text('(not an answer run)'),
    );
    expect(await cli('tell', thread, 'How', 'did', 'you', 'price', 'it?', '--ask')).toMatchObject({ code: 0, out: `Asked ${thread}\n` });
    await daemon.runtime.whenIdle();
    const [ask] = daemon.store.list({ agentId: thread, types: ['message.user'] });
    expect(ask).toMatchObject({ payload: { text: 'How did you price it?', question: true } });
    expect(daemon.store.list({ agentId: thread, types: ['run.started'] }).map((e) => e.payload)).toEqual([expect.objectContaining({ answering: ask!.id })]);
    expect(daemon.store.list({ agentId: thread, types: ['agent.status_changed'] }).at(-1)).toMatchObject({ payload: { status: 'done' } });
    const tail = (await cli('tail', thread)).out;
    expect(tail).toContain('you asked "Pricing page": How did you price it?');
    expect(tail).toContain('"Pricing page" › Per seat.');
  });

  it('says which threads answer an Ask and which read it as a message', async () => {
    const help = (await cli('tell', '--help')).out.replace(/\s+/g, ' ');
    expect(help).toContain('an idle, done or failed thread answers from its context and keeps its status');
    expect(help).toContain('any other thread (a stopped one too) reads it as a message and runs');
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

  it("lists, switches and duplicates Desk's built-in skills", async () => {
    const listed = (await cli('skills')).out;
    expect(listed).toContain('No skills\n\nBuilt into Desk:');
    expect(listed).toMatch(/ {2}pdf-toolkit — .+ \(environment set up on first use\)/);
    expect((await cli('skill', 'off', 'pdf-toolkit')).out).toContain('Turned off pdf-toolkit');
    expect((await cli('skills')).out).toMatch(/pdf-toolkit — .+ \(off, environment/);
    expect((await cli('skill', 'on', 'pdf-toolkit')).code).toBe(0);
    await cli('project', 'new', 'Ops', '--goal', 'g');
    expect((await cli('skill', 'duplicate', 'images', '-p', 'Ops')).out).toContain('Your copy is used instead of the built-in');
    const inOps = (await cli('skills', 'Ops')).out;
    expect(inOps).toContain('images (project, v1)');
    expect(inOps).toMatch(/images — .+ \(shadowed by your project skill/);
    expect((await cli('skill', 'off', 'nope')).code).toBe(1);
  });
});
