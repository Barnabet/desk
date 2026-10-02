import { execFileSync } from 'node:child_process';
import { existsSync, realpathSync } from 'node:fs';
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { closeWorkspace, inspectWorkspaceClosure } from './close';

let base: string;
let root: string;
let repo: string;
const git = (cwd: string, ...args: string[]) => execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();
beforeEach(async () => {
  base = realpathSync(await mkdtemp(join(tmpdir(), 'desk-close-')));
  root = join(base, 'workspace');
  repo = join(base, 'repo');
  await mkdir(repo);
  git(repo, 'init', '-q', '-b', 'main');
  git(repo, 'config', 'user.email', 'test@example.com');
  git(repo, 'config', 'user.name', 'Test');
  await writeFile(join(repo, 'code.txt'), 'committed code');
  git(repo, 'add', '.'); git(repo, 'commit', '-qm', 'base');
});
afterEach(async () => rm(base, { recursive: true, force: true }));

it('previews without deleting, then removes an entire disposable scratch workspace without following symlinks', async () => {
  await mkdir(join(root, 'outputs'), { recursive: true });
  await writeFile(join(root, 'outputs', 'scratch.bin'), 'temporary');
  await symlink(repo, join(root, 'external'));
  const preview = await inspectWorkspaceClosure(root);
  expect(preview.entries).toEqual(['external', 'outputs']);
  expect(existsSync(join(root, 'outputs', 'scratch.bin'))).toBe(true);
  await closeWorkspace(root, () => {});
  expect(existsSync(root)).toBe(false);
  expect(await readFile(join(repo, 'code.txt'), 'utf8')).toBe('committed code');
});

it.each([false, true])('removes a worktree and all disposable outputs while retaining its commits (nested: %s)', async nested => {
  const ws = nested ? join(root, 'checkout') : root;
  await mkdir(root, { recursive: true });
  git(repo, 'worktree', 'add', '-qb', 'done', ws);
  await writeFile(join(ws, 'code.txt'), 'new committed work');
  git(ws, 'commit', '-qam', 'work');
  const head = git(ws, 'rev-parse', 'HEAD');
  await writeFile(join(ws, 'untracked-output.json'), '{}');
  const report = await closeWorkspace(root, () => {});
  expect(report.repositories).toHaveLength(1);
  expect(existsSync(root)).toBe(false);
  expect(git(repo, 'rev-parse', 'done')).toBe(head);
  expect(git(repo, 'worktree', 'list', '--porcelain')).not.toContain(ws);
});

it('refuses dirty tracked files and removes nothing', async () => {
  git(repo, 'worktree', 'add', '-qb', 'done', root);
  await writeFile(join(root, 'code.txt'), 'uncommitted');
  await expect(closeWorkspace(root, () => {})).rejects.toThrow('Uncommitted tracked changes');
  expect(await readFile(join(root, 'code.txt'), 'utf8')).toBe('uncommitted');
});

it.each(['detached', 'locked', 'local', 'bare'] as const)('preserves %s Git history/worktrees', async kind => {
  if (kind === 'local') git(base, 'clone', '-q', repo, root);
  else if (kind === 'bare') git(base, 'clone', '-q', '--bare', repo, root);
  else {
    git(repo, 'worktree', 'add', '-qb', 'done', root);
    if (kind === 'detached') git(root, 'checkout', '-q', '--detach');
    else git(repo, 'worktree', 'lock', root);
  }
  await expect(closeWorkspace(root, () => {})).rejects.toThrow();
  expect(existsSync(root)).toBe(true);
});

it('validates all nested repositories before removing the first', async () => {
  await mkdir(root);
  git(repo, 'worktree', 'add', '-qb', 'a', join(root, 'a'));
  git(repo, 'worktree', 'add', '-qb', 'b', join(root, 'b'));
  await writeFile(join(root, 'b', 'code.txt'), 'dirty');
  await expect(closeWorkspace(root, () => {})).rejects.toThrow();
  expect(existsSync(join(root, 'a'))).toBe(true);
});

it('refuses root symlinks and a task that becomes busy', async () => {
  await symlink(repo, root);
  await expect(closeWorkspace(root, () => {})).rejects.toThrow('real directory');
  await rm(root);
  await mkdir(root);
  await writeFile(join(root, 'output'), 'keep');
  await expect(closeWorkspace(root, () => { throw new Error('Busy'); })).rejects.toThrow('Busy');
  expect(existsSync(join(root, 'output'))).toBe(true);
});
