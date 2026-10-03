import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { call, text, tools, type ChatRequest, type FakeReply } from '@desk/fake-model';
import type { EventOf } from '@desk/protocol';
import { evaluatePolicy } from '../policy/evaluate';
import { getCheck, listReceipts } from '../receipts/receipts';
import { builderReport, latestSubmission, threadReview } from '../reviews/reviews';
import type { Runtime } from '../runtime/runtime';
import { getAgent, getDeskAgent, type AgentRow } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, seedThread, type Harness } from '../testing/harness';
import { testToolContext } from '../testing/context';
import { listReceiptsTool, runCheckTool } from '../tools/checks';
import { listThreadsTool } from '../tools/desk';
import { openWatches } from '../watches/watches';
import { checkDirs, stepLog } from './runner';

let h: Harness;
let rt: Runtime;
let open = false;
afterEach(async () => {
  if (!open) return;
  open = false;
  await rt.shutdown();
  await h.cleanup();
});

const git = (cwd: string, ...args: string[]) => execFileSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@t', ...args], { cwd, encoding: 'utf8' }).trim();
const systemOf = (req: ChatRequest) => String(req.messages[0]?.content ?? '');
const callsOf = (req: ChatRequest, name: string) =>
  req.messages
    .filter((m) => m.role === 'assistant')
    .flatMap((m) => m.tool_calls ?? [])
    .filter((c) => c.function?.name === name).length;

/** Desk's messages of one kind, oldest first. */
const deskMessages = (desk: AgentRow, kind: string) =>
  h.store.list({ agentId: desk.id, types: ['message.agent'] }).flatMap((e) => (e.type === 'message.agent' && e.payload.kind === kind ? [e as EventOf<'message.agent'>] : []));

async function project(script: (req: ChatRequest) => FakeReply = () => text('ok')) {
  h = await createHarness({ script });
  rt = newRuntime(h);
  open = true;
  // Tests run without macOS's sandbox, where every shell command asks first unless a rule allows it.
  const policy = [{ tool: 'bash', action: 'allow' as const }];
  const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id, policy } });
  return { projectId, desk: getDeskAgent(h.store.db, projectId)! };
}

async function repo(): Promise<string> {
  const r = join(h.files, 'repo');
  mkdirSync(r);
  git(r, 'init', '-q');
  writeFileSync(join(r, 'README.md'), 'v0\n');
  git(r, 'add', '-A');
  git(r, 'commit', '-qm', 'init');
  return r;
}

const ended = async (id: string) => {
  await vi.waitFor(() => expect(getCheck(h.store.db, id)?.status).not.toBe('running'), { timeout: 10_000 });
  await rt.whenIdle();
  return getCheck(h.store.db, id)!;
};

describe('check jobs', () => {
  it('runs steps in a scratch folder, checks expected files, records receipts and wakes Desk', async () => {
    const { projectId, desk } = await project();
    const c = await rt.checks.start(desk.id, { title: 'Build the report', steps: ['echo hi > $DESK_CHECK_OUT/report.txt', 'echo built'], where: 'scratch', expect: ['$DESK_CHECK_OUT/report.txt'] });
    expect(c).toMatchObject({ status: 'running', head: null });
    const done = await ended(c.id);
    expect(done).toMatchObject({ status: 'passed', failed_step: null, reason: '' });
    const dirs = checkDirs(rt.projectDir(projectId), c.id);
    expect(readFileSync(join(dirs.out, 'report.txt'), 'utf8')).toBe('hi\n');
    expect(readFileSync(stepLog(dirs.logs, 2), 'utf8')).toBe('built\n');
    expect(existsSync(dirs.work)).toBe(false);
    const receipts = listReceipts(h.store.db, projectId, { checkId: c.id });
    expect(receipts.map((r) => [r.step, r.tool, r.exit_code, r.agent_id])).toEqual([
      [2, 'run_check', 0, desk.id],
      [1, 'run_check', 0, desk.id],
    ]);
    const [notice] = deskMessages(desk, 'check');
    expect(notice!.payload.from_label).toBe('Check');
    expect(notice!.payload.text).toMatch(/^Check passed: Build the report \(chk_[\w-]+\) · 2 steps · /);
    expect(notice!.payload.text).toContain('- step 2: exit 0 · ');
    // A check's end is a lifecycle wake: Desk ran on it.
    expect(h.fake.requests.some((r) => JSON.stringify(r.messages).includes('Check passed: Build the report'))).toBe(true);
  });

  it('stops at the failing step and quotes its last lines; check_log reads more', async () => {
    const { desk } = await project();
    const c = await rt.checks.start(desk.id, { title: 'Tests', steps: ['echo one', 'echo boom >&2; exit 2', 'echo never'], where: 'scratch', expect: [] });
    const done = await ended(c.id);
    expect(done).toMatchObject({ status: 'failed', failed_step: 2, reason: 'step 2 exited 2' });
    const text = deskMessages(desk, 'check')[0]!.payload.text;
    expect(text).toContain('Reason: step 2 exited 2');
    expect(text).toContain("Last lines of step 2's log:\n> boom");
    expect(text).toContain('Not run: step 3');
    expect(await rt.checks.log(desk.id, c.id)).toContain('> boom');
    expect(await rt.checks.log(desk.id, c.id, 3)).toContain('(no output, or the step did not run)');
  });

  it('fails when an expected file is missing or stale, and on its timeout', async () => {
    const { desk } = await project();
    const missing = await rt.checks.start(desk.id, { title: 'Report', steps: ['true'], where: 'scratch', expect: ['out.html'] });
    expect(await ended(missing.id)).toMatchObject({ status: 'failed', failed_step: null, reason: 'missing out.html' });
    await expect(rt.checks.start(desk.id, { title: 'Escape', steps: ['true'], where: 'scratch', expect: ['../x'] }).then((c) => ended(c.id))).resolves.toMatchObject({
      status: 'failed',
      reason: expect.stringContaining('outside the check'),
    });
    const slow = await rt.checks.start(desk.id, { title: 'Slow', steps: ['sleep 5'], where: 'scratch', expect: [], timeoutS: 1 });
    expect(await ended(slow.id)).toMatchObject({ status: 'timed_out', failed_step: 1 });
  });

  it('checks a clean snapshot of a thread’s commit without touching its workspace, and removes it after', async () => {
    const { projectId, desk } = await project();
    const sourceId = await rt.addSource(projectId, await repo(), 'repo');
    const thread = await rt.spawnThread(desk.id, { title: 'Builder', brief: 'b', gitSourceId: sourceId });
    await rt.whenIdle();
    const t = getAgent(h.store.db, thread)!;
    writeFileSync(join(t.workspace_path!, 'README.md'), 'v1\n');
    git(t.workspace_path!, 'commit', '-qam', 'v1');
    const commit = git(t.workspace_path!, 'rev-parse', 'HEAD');
    writeFileSync(join(t.workspace_path!, 'README.md'), 'uncommitted\n');
    const c = await rt.checks.start(desk.id, { title: 'Snapshot', steps: ['grep -q v1 README.md', 'git status --porcelain > $DESK_CHECK_OUT/status.txt; echo changed > README.md'], where: { thread_id: thread, mode: 'snapshot' }, expect: [] });
    expect(c.head).toBe(commit);
    expect(await ended(c.id)).toMatchObject({ status: 'passed' });
    expect(readFileSync(join(checkDirs(rt.projectDir(projectId), c.id).out, 'status.txt'), 'utf8')).toBe('');
    expect(readFileSync(join(t.workspace_path!, 'README.md'), 'utf8')).toBe('uncommitted\n');
    expect(git(getAgent(h.store.db, thread)!.workspace_path!, 'worktree', 'list').split('\n')).toHaveLength(2);
    expect(listReceipts(h.store.db, projectId, { checkId: c.id }).map((r) => [r.step, r.head, r.dirty])).toEqual([
      [2, commit, false],
      [1, commit, false],
    ]);
    // The workspace as it is: a thread's uncommitted state, read in place.
    const ws = await rt.checks.start(desk.id, { title: 'Workspace', steps: ['grep -q uncommitted README.md'], where: { thread_id: thread, mode: 'workspace' }, expect: [] });
    expect(ws.cwd).toBe(t.workspace_path);
    expect(await ended(ws.id)).toMatchObject({ status: 'passed' });
  });

  it('changes a source or a finished thread’s workspace only with write, and only where agents may write', async () => {
    const { projectId, desk } = await project();
    const source = await repo();
    const sourceId = await rt.addSource(projectId, source, 'repo');
    const thread = await rt.spawnThread(desk.id, { title: 'Builder', brief: 'b', gitSourceId: sourceId });
    await rt.whenIdle();
    const ws = getAgent(h.store.db, thread)!.workspace_path!;
    // A one-off cleanup Desk used to run as a "service": unlocking a thread's worktree so close_thread can remove it.
    git(source, 'worktree', 'lock', ws);
    const unlock = await rt.checks.start(desk.id, { title: 'Unlock', steps: ['git worktree unlock "$PWD"'], where: { thread_id: thread, mode: 'workspace', write: true }, expect: [] });
    expect(await ended(unlock.id)).toMatchObject({ status: 'passed' });
    expect(git(source, 'worktree', 'list', '--porcelain')).not.toContain('locked');

    const clean = await rt.checks.start(desk.id, { title: 'Clean', steps: ['echo stale > stale.txt', 'rm stale.txt README.md'], where: { source_id: sourceId, write: true }, expect: [] });
    expect(clean.cwd).toBe(source);
    expect(await ended(clean.id)).toMatchObject({ status: 'passed' });
    expect(existsSync(join(source, 'README.md'))).toBe(false);
    // The source is never removed with the check's working copy.
    expect(existsSync(source)).toBe(true);

    rt.setSourceWrite(projectId, sourceId, false);
    await expect(rt.checks.start(desk.id, { title: 'No', steps: ['true'], where: { source_id: sourceId, write: true }, expect: [] })).rejects.toThrow(/may not write to repo/);
    // Read-only checks still run there.
    expect(await ended((await rt.checks.start(desk.id, { title: 'Read', steps: ['git status'], where: { source_id: sourceId }, expect: [] })).id)).toMatchObject({ status: 'passed' });
  });

  it('allows three running checks per project, cancels, and is Desk’s only', async () => {
    const { projectId, desk } = await project();
    const ids: string[] = [];
    for (let i = 0; i < 3; i++) ids.push((await rt.checks.start(desk.id, { title: `Sleep ${i}`, steps: ['sleep 30'], where: 'scratch', expect: [] })).id);
    await expect(rt.checks.start(desk.id, { title: 'One more', steps: ['true'], where: 'scratch', expect: [] })).rejects.toThrow(/3 checks are already running/);
    const { agentId } = await seedThread(h.store, h.dir, { projectId });
    await expect(rt.checks.start(agentId, { title: 'Mine', steps: ['true'], where: 'scratch', expect: [] })).rejects.toThrow('Only Desk runs checks.');
    for (const id of ids) rt.checks.cancel(desk.id, id);
    for (const id of ids) expect(await ended(id)).toMatchObject({ status: 'cancelled', reason: 'cancelled by Desk' });
    expect(() => rt.checks.cancel(desk.id, ids[0]!)).toThrow(/already ended/);
  });

  it('records a check cut off by a crash as interrupted, and by a shutdown too', async () => {
    const { projectId, desk } = await project();
    h.store.append({ project_id: projectId, agent_id: desk.id, type: 'check.started', payload: { check_id: 'chk_old', title: 'Old', steps: ['true'], where: 'scratch', expect: [], timeout_s: 60, cwd: h.dir, head: null } });
    rt.recover();
    expect(getCheck(h.store.db, 'chk_old')).toMatchObject({ status: 'interrupted', reason: 'Desk stopped unexpectedly while it ran' });
    expect(deskMessages(desk, 'check')[0]!.payload.text).toMatch(/^Check interrupted: Old/);
    await rt.whenIdle();
    const c = await rt.checks.start(desk.id, { title: 'Long', steps: ['sleep 30'], where: 'scratch', expect: [] });
    await rt.shutdown();
    expect(getCheck(h.store.db, c.id)).toMatchObject({ status: 'interrupted', reason: 'Desk shut down while it ran' });
  });

  it('is gated like bash, step by step', () => {
    const rules = [{ tool: 'bash', match: { command: '^git push' }, action: 'ask' as const }];
    const input = { title: 't', steps: ['echo ok', 'git push origin main'], where: 'scratch' as const, expect: [], timeout_s: 60 };
    expect(evaluatePolicy(runCheckTool, input, rules, { sandboxAvailable: true }).action).toBe('ask');
    expect(evaluatePolicy(runCheckTool, { ...input, steps: ['echo ok'] }, rules, { sandboxAvailable: true }).action).toBe('auto');
    expect(evaluatePolicy(runCheckTool, { ...input, steps: ['echo ok'] }, [], { sandboxAvailable: false }).action).toBe('ask');
  });
});

describe('receipts on submissions', () => {
  it('shows a reviewer and Desk the commands run on the submitted commit with no uncommitted changes', async () => {
    const builder = (req: ChatRequest): FakeReply => {
      const [writes, bashes, commits, completes] = ['write_file', 'bash', 'git_commit', 'complete'].map((n) => callsOf(req, n));
      if (writes === 0) return tools(call('write_file', { path: 'README.md', content: 'v1\n' }));
      if (bashes === 0) return tools(call('bash', { command: 'echo dirty run' }));
      if (commits === 0) return tools(call('git_commit', { message: 'v1' }));
      if (bashes === 1) return tools(call('bash', { command: 'echo 12 passed' }));
      if (completes === 0) return tools(call('complete', { summary: 'Done', evidence: 'Ran the tests' }));
      return text('done');
    };
    const { projectId, desk } = await project((req) => (systemOf(req).includes('## Your assignment: Builder\n') ? builder(req) : text('ok')));
    const sourceId = await rt.addSource(projectId, await repo(), 'repo');
    const thread = await rt.spawnThread(desk.id, { title: 'Builder', brief: 'b', gitSourceId: sourceId });
    await rt.whenIdle();
    const t = getAgent(h.store.db, thread)!;
    const sub = latestSubmission(h.store.db, thread)!;
    const all = listReceipts(h.store.db, projectId, { agentIds: [thread] });
    expect(all.map((r) => [r.command, r.dirty])).toEqual([
      ['echo 12 passed', false],
      ['echo dirty run', true],
    ]);
    const review = threadReview(h.store.db, t);
    expect(review.receipts[sub.id]!.map((r) => r.command)).toEqual(['echo 12 passed']);
    const report = builderReport(h.store.db, t, sub);
    expect(report).toContain(`Commands Desk recorded on commit ${sub.commit!.slice(0, 10)} with no uncommitted changes (newest first):`);
    expect(report).toMatch(/- exit 0 · \d+ ms .*bash: echo 12 passed/);
    expect(report).not.toContain('dirty run');

    const ctx = testToolContext(desk.workspace_path!, { projectId, agentId: desk.id, services: rt.services });
    const listed = await listReceiptsTool.execute({ thread_id: thread, failed_only: false, limit: 30 }, ctx);
    expect(listed).toContain(`"Builder" (${thread}) @${sub.commit!.slice(0, 10)} · bash: echo 12 passed`);
    expect(listed).toContain('+changes · bash: echo dirty run');
    expect(await listReceiptsTool.execute({ commit: sub.commit!.slice(0, 8), failed_only: false, limit: 30 }, ctx)).not.toContain('dirty run');
    expect(await listReceiptsTool.execute({ failed_only: true, limit: 30 }, ctx)).toBe('No commands match.');
  });
});

describe('watching a thread', () => {
  it('wakes Desk once on the next matching message to another thread, ignoring messages to Desk', async () => {
    const { projectId, desk } = await project();
    const a = (await seedThread(h.store, h.dir, { projectId })).agentId;
    const b = (await seedThread(h.store, h.dir, { projectId })).agentId;
    const thread = getAgent(h.store.db, a)!;
    rt.watches.set(desk.id, thread, 'READY');
    const ctx = testToolContext(desk.workspace_path!, { projectId, agentId: desk.id, services: rt.services });
    expect(await listThreadsTool.execute({}, ctx)).toContain('watched (matching "READY")');

    rt.deliver(a, b, 'note', 'Still working.');
    rt.deliver(a, desk.id, 'update', 'The API is ready.');
    await rt.whenIdle();
    expect(deskMessages(desk, 'reminder')).toHaveLength(0);
    const sent = rt.deliver(a, b, 'note', 'The API is ready: use /v2.');
    await vi.waitFor(() => expect(deskMessages(desk, 'reminder')).toHaveLength(1));
    const note = deskMessages(desk, 'reminder')[0]!.payload.text;
    expect(note).toContain(`it sent a note to "Test thread" (${b}) (message #${sent}):\n> The API is ready: use /v2.`);
    expect(note).toContain('The watch has ended');
    rt.deliver(a, b, 'note', 'Ready again.');
    await rt.whenIdle();
    expect(deskMessages(desk, 'reminder')).toHaveLength(1);
    expect(openWatches(h.store.db, projectId)).toHaveLength(0);
  });

  it('ends silently when the thread finishes, refuses finished threads, caps open watches, and unwatches', async () => {
    const { projectId, desk } = await project();
    const threads: string[] = [];
    for (let i = 0; i < 11; i++) threads.push((await seedThread(h.store, h.dir, { projectId })).agentId);
    const row = (id: string) => getAgent(h.store.db, id)!;
    for (const id of threads.slice(0, 10)) rt.watches.set(desk.id, row(id));
    expect(() => rt.watches.set(desk.id, row(threads[10]!))).toThrow(/already watch 10 threads/);
    rt.watches.set(desk.id, row(threads[0]!), 'again'); // replacing one is fine
    expect(rt.watches.unset(desk.id, threads[1]!)).toBe(true);
    expect(rt.watches.unset(desk.id, threads[1]!)).toBe(false);
    h.store.append({ project_id: projectId, agent_id: threads[2]!, type: 'agent.status_changed', payload: { status: 'done' } });
    await vi.waitFor(() => expect(openWatches(h.store.db, projectId)).toHaveLength(8));
    expect(deskMessages(desk, 'reminder')).toHaveLength(0);
    expect(() => rt.watches.set(desk.id, row(threads[2]!))).toThrow(/has finished \(done\)/);
  });
});

describe('shared scratch', () => {
  it("lets a thread read Desk's workspace with its file tools, never write it", async () => {
    let deskWs = '';
    const thread = (req: ChatRequest): FakeReply => {
      if (callsOf(req, 'read_file') === 0) return tools(call('read_file', { path: join(deskWs, 'shared', 'facts.csv') }));
      if (callsOf(req, 'write_file') === 0) return tools(call('write_file', { path: join(deskWs, 'shared', 'facts.csv'), content: 'changed' }));
      return text('done');
    };
    const { desk } = await project((req) => (systemOf(req).includes('## Your assignment: Reader\n') ? thread(req) : text('ok')));
    deskWs = desk.workspace_path!;
    mkdirSync(join(deskWs, 'shared'));
    writeFileSync(join(deskWs, 'shared', 'facts.csv'), 'id,value\n1,2\n');
    const id = await rt.spawnThread(desk.id, { title: 'Reader', brief: 'Read the facts' });
    await rt.whenIdle();
    const results = h.store.list({ agentId: id, types: ['tool.result'] }).flatMap((e) => (e.type === 'tool.result' ? [e.payload] : []));
    expect(results.find((r) => r.name === 'read_file')).toMatchObject({ status: 'ok' });
    expect(results.find((r) => r.name === 'read_file')!.content).toContain('1,2');
    expect(results.find((r) => r.name === 'write_file')!.status).not.toBe('ok');
    expect(readFileSync(join(deskWs, 'shared', 'facts.csv'), 'utf8')).toBe('id,value\n1,2\n');
    expect(h.fake.requests.find((r) => systemOf(r).includes('## Your assignment: Reader\n'))!.messages[0]!.content).toContain(`${deskWs} — Desk's workspace, which you can read but not write`);
  });
});
