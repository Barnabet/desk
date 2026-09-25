import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools } from '@desk/fake-model';
import { getAgent, getDeskAgent, getProject } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';
import { deskToolsFor, threadToolsFor } from '../runtime/toolsets';
import { deskSystemPrompt, threadSystemPrompt } from './prompts';

let h: Harness;
afterEach(async () => h?.cleanup());

const SEE_FILES = 'To see a document, page, slide, sheet, video frame or image, render it with its file skill and look at it with view_image.';

async function setup() {
  h = await createHarness({ script: (req) => (req.model === FAKE_MODEL.id ? tools(call('wait_for_reply', {})) : text('noted')) });
  const rt = newRuntime(h);
  const projectId = rt.createProject({
    name: 'Checkout',
    goal: 'Reduce checkout p75 latency',
    instructions: 'Never touch the billing service without asking.',
    settings: { thread_model: FAKE_MODEL.id, check_in: 'minimal' },
  });
  const repo = join(h.dir, 'api');
  mkdirSync(repo);
  execFileSync('git', ['init', '-q', '-b', 'main'], { cwd: repo });
  writeFileSync(join(repo, 'x'), 'x');
  execFileSync('git', ['-c', 'user.email=a@b', '-c', 'user.name=a', 'commit', '-q', '--allow-empty', '-m', 'i'], { cwd: repo });
  const sourceId = await rt.addSource(projectId, repo, 'API');
  rt.writeMemory(projectId, { kind: 'decision', content: 'Release moved to Friday' });
  return { rt, projectId, sourceId, desk: getDeskAgent(h.store.db, projectId)! };
}

describe('Desk prompt', () => {
  it('describes the role, settings and live project state', async () => {
    const { rt, projectId, sourceId, desk } = await setup();
    const threadId = await rt.spawnThread(desk.id, { title: 'Profile endpoints', brief: 'Profile /checkout', gitSourceId: sourceId });
    await rt.whenIdle();
    rt.addLibraryFile(projectId, { name: 'baseline.md', content: 'p75 = 900ms', title: 'Baseline' });
    const p = deskSystemPrompt({ db: h.store.db, agent: desk, project: getProject(h.store.db, projectId)!, libraryDir: rt.libraryDir(projectId) });
    expect(p).toContain('You are Desk');
    expect(p).toContain('Reduce checkout p75 latency');
    expect(p).toContain('Never touch the billing service');
    expect(p).toMatch(/check_in: minimal/);
    expect(p).toContain(`${sourceId} API (git)`);
    expect(p).toContain(`${threadId} "Profile endpoints" [waiting]`);
    expect(p).toContain('Release moved to Friday');
    expect(p).toContain('baseline.md — Baseline');
    expect(p).toContain('(no plan yet)');
    expect(p).toMatch(/never merge/i);
    expect(p).toContain(SEE_FILES);
  });
});

describe('thread prompt', () => {
  it('includes brief, workspace, branch, revision round and memory', async () => {
    const { rt, projectId, sourceId, desk } = await setup();
    const threadId = await rt.spawnThread(desk.id, { title: 'Profile endpoints', brief: 'Profile /checkout', gitSourceId: sourceId });
    await rt.whenIdle();
    h.store.append({ project_id: projectId, agent_id: threadId, type: 'agent.revision', payload: { round: 1, feedback: 'x' } });
    const t = getAgent(h.store.db, threadId)!;
    const p = threadSystemPrompt({ db: h.store.db, agent: t, project: getProject(h.store.db, projectId)!, libraryDir: rt.libraryDir(projectId) });
    expect(p).toContain('Profile /checkout');
    expect(p).toContain(t.workspace_path!);
    expect(p).toContain(t.git_branch!);
    expect(p).toMatch(/revision round 1/i);
    expect(p).toContain('Release moved to Friday');
    expect(p).toContain('API (git)');
    expect(p).toContain(SEE_FILES);
  });

  it('marks the thread’s own library artifacts', async () => {
    const { rt, projectId, desk } = await setup();
    const threadId = await rt.spawnThread(desk.id, { title: 'Writer', brief: 'b' });
    await rt.whenIdle();
    const file = join(h.dir, 'mine.md');
    writeFileSync(file, 'x');
    await rt.publishToLibrary(projectId, file, { title: 'Mine', kind: 'report', description: '' }, `agent:${threadId}`);
    const p = threadSystemPrompt({ db: h.store.db, agent: getAgent(h.store.db, threadId)!, project: getProject(h.store.db, projectId)!, libraryDir: rt.libraryDir(projectId) });
    expect(p).toContain('mine.md — Mine [report]');
    expect(p).toContain('(published by you)');
  });
});

describe('tool sets', () => {
  it('gives git tools only to worktree threads and no read-write shell to Desk', async () => {
    const { rt, desk, sourceId } = await setup();
    const gitThread = getAgent(h.store.db, await rt.spawnThread(desk.id, { title: 'G', brief: 'b', gitSourceId: sourceId }))!;
    const plain = getAgent(h.store.db, await rt.spawnThread(desk.id, { title: 'S', brief: 'b' }))!;
    await rt.whenIdle();
    const names = (ts: { name: string }[]) => ts.map((t) => t.name);
    expect(names(threadToolsFor(gitThread))).toEqual(expect.arrayContaining(['git_commit', 'git_push', 'bash', 'complete', 'message_desk']));
    expect(names(threadToolsFor(plain))).not.toContain('git_push');
    const deskNames = names(deskToolsFor(desk));
    expect(deskNames).toEqual(expect.arrayContaining(['spawn_thread', 'bash_readonly', 'read_file', 'view_image', 'write_file', 'library_publish', 'memory_write']));
    expect(names(threadToolsFor(plain))).toContain('view_image');
    expect(deskNames).not.toContain('bash');
    expect(deskNames).not.toContain('complete');
  });

  it('lets threads read project sources and the library', async () => {
    const { rt, projectId, desk } = await setup();
    rt.addLibraryFile(projectId, { name: 'notes.md', content: 'library note' });
    h.fake.setScript((req) =>
      req.model === FAKE_MODEL.id
        ? req.messages.some((m) => m.role === 'tool')
          ? tools(call('complete', { summary: 'read it' }))
          : tools(call('read_file', { path: join(rt.libraryDir(projectId), 'notes.md') }))
        : text('noted'),
    );
    const id = await rt.spawnThread(desk.id, { title: 'Reader', brief: 'Read the library note' });
    await rt.whenIdle();
    const res = h.store.list({ agentId: id, types: ['tool.result'] })[0];
    expect(res?.type === 'tool.result' && res.payload).toMatchObject({ status: 'ok', content: expect.stringContaining('library note') });
  });
});
