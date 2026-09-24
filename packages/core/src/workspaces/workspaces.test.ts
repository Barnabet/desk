import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { DEFAULT_POLICY } from '@desk/protocol';
import { evaluatePolicy } from '../policy/evaluate';
import { testToolContext } from '../testing/context';
import { bashReadonlyTool } from '../tools/bash';
import { gitCommitTool, gitDiffTool, gitPushTool, gitStatusTool, openPrTool } from '../tools/git';
import { detectSandbox } from '../tools/sandbox';
import type { ToolContext } from '../tools/types';
import { createWorkspace, removeWorkspace, threadBranchName } from './workspaces';

const git = (cwd: string, ...args: string[]) => execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();

let base: string;
let repo: string;
let remote: string;
beforeEach(async () => {
  base = realpathSync(await mkdtemp(join(tmpdir(), 'desk-ws-')));
  repo = join(base, 'repo');
  remote = join(base, 'remote.git');
  mkdirSync(repo);
  git(base, 'init', '-q', '--bare', remote);
  git(repo, 'init', '-q', '-b', 'main');
  git(repo, 'config', 'user.email', 'dev@example.com');
  git(repo, 'config', 'user.name', 'Dev');
  writeFileSync(join(repo, 'README.md'), 'hello\n');
  git(repo, 'add', '.');
  git(repo, 'commit', '-q', '-m', 'init');
  git(repo, 'remote', 'add', 'origin', remote);
});
afterEach(async () => rm(base, { recursive: true, force: true }));

async function worktree(title = 'Fix login bug', id = '01K5ABCDEFGHJKMNPQRSTVWXYZ') {
  const path = join(base, 'workspaces', id);
  const branch = threadBranchName(title, id);
  const ws = await createWorkspace({ path, git: { sourcePath: repo, branch } });
  const ctx: ToolContext = testToolContext(path, { git: { branch, base: ws.git!.base } });
  return { path, branch, ws, ctx };
}

describe('workspaces', () => {
  it('names branches desk/<slug>-<id suffix>', () => {
    expect(threadBranchName('Fix Login bug!! (urgent)', '01K5ABCDEFGHJKMNPQRSTVWXYZ')).toBe('desk/fix-login-bug-urgent-tvwxyz');
    expect(threadBranchName('???', '01K5ABCDEFGHJKMNPQRSTVWXYZ')).toBe('desk/thread-tvwxyz');
  });

  it('creates a worktree on a new branch at source HEAD, even with a dirty source', async () => {
    writeFileSync(join(repo, 'README.md'), 'uncommitted change\n');
    const { path, branch, ws } = await worktree();
    expect(ws.git).toMatchObject({ branch, base: git(repo, 'rev-parse', 'HEAD') });
    expect(ws.git!.common_dir).toBe(realpathSync(join(repo, '.git')));
    expect(git(path, 'rev-parse', '--abbrev-ref', 'HEAD')).toBe(branch);
    expect(readFileSync(join(path, 'README.md'), 'utf8')).toBe('hello\n');
  });

  it('creates a scratch dir without git', async () => {
    const path = join(base, 'workspaces', 'scratch');
    expect((await createWorkspace({ path })).git).toBeNull();
    expect(existsSync(path)).toBe(true);
  });

  it('removes the worktree but keeps the branch', async () => {
    const { path, branch } = await worktree();
    await removeWorkspace({ path, gitSourcePath: repo });
    expect(existsSync(path)).toBe(false);
    expect(git(repo, 'branch', '--list', branch)).toContain(branch);
  });
});

describe('git tools', () => {
  it('status, commit and diff inside the worktree only', async () => {
    const { path, ctx } = await worktree();
    writeFileSync(join(path, 'login.ts'), 'export const ok = true;\n');
    expect(String(await gitStatusTool.execute({}, ctx))).toContain('login.ts');
    const committed = String(await gitCommitTool.execute({ message: 'Fix login' }, ctx));
    expect(committed).toMatch(/Committed [0-9a-f]{7,}/);
    expect(git(path, 'log', '-1', '--format=%s')).toBe('Fix login');
    expect(git(repo, 'log', '-1', '--format=%s')).toBe('init');
    expect(String(await gitDiffTool.execute({}, ctx))).toContain('+export const ok = true;');
    await expect(gitCommitTool.execute({ message: 'nothing' }, ctx)).rejects.toThrow(/Nothing to commit/);
  });

  it('pushes the thread branch to the remote', async () => {
    const { path, branch, ctx } = await worktree();
    writeFileSync(join(path, 'a.txt'), 'a');
    await gitCommitTool.execute({ message: 'a' }, ctx);
    expect(String(await gitPushTool.execute({}, ctx))).toContain(branch);
    expect(git(remote, 'branch', '--list', branch)).toContain(branch);
  });

  it('gates push by the thread branch and opens PRs without asking', async () => {
    const on = { sandboxAvailable: true };
    expect(evaluatePolicy(gitPushTool, {}, DEFAULT_POLICY, { ...on, gitBranch: 'desk/x-123456' }).action).toBe('allow');
    expect(evaluatePolicy(gitPushTool, {}, DEFAULT_POLICY, { ...on, gitBranch: 'main' }).action).toBe('deny');
    expect(evaluatePolicy(openPrTool, { title: 't', body: 'b' }, DEFAULT_POLICY, on).action).toBe('allow');
  });

  it('refuses git tools outside a worktree', async () => {
    const ctx = testToolContext(base);
    await expect(gitStatusTool.execute({}, ctx)).rejects.toThrow(/not a git worktree/);
  });
});

describe('bash_readonly', () => {
  it('cannot write outside temp even in its cwd', async (t) => {
    if (!(await detectSandbox())) t.skip();
    // Temp dirs are always writable, so use a directory outside $TMPDIR, like a real project source.
    const source = realpathSync(await mkdtemp(join(process.cwd(), '.sandbox-test-')));
    try {
      writeFileSync(join(source, 'README.md'), 'hello\n');
      const ctx = testToolContext(source, { sandbox: { enabled: true, writable: [source] } });
      const out = String(await bashReadonlyTool.execute({ command: 'echo x > new.txt; cat README.md', timeout_s: 10 }, ctx));
      expect(out).toContain('hello');
      expect(existsSync(join(source, 'new.txt'))).toBe(false);
    } finally {
      await rm(source, { recursive: true, force: true });
    }
  });

  it('is shell-gated', () => {
    expect(evaluatePolicy(bashReadonlyTool, { command: 'sudo ls' }, DEFAULT_POLICY, { sandboxAvailable: true }).action).toBe('ask');
    expect(evaluatePolicy(bashReadonlyTool, { command: 'ls' }, DEFAULT_POLICY, { sandboxAvailable: false }).action).toBe('ask');
    expect(evaluatePolicy(bashReadonlyTool, { command: 'ls' }, DEFAULT_POLICY, { sandboxAvailable: true }).action).toBe('auto');
  });
});
