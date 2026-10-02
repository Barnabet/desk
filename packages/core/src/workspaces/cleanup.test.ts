import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, mkdtemp, readFile, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { cleanWorkspaceDependencies, workspaceCleanupCandidates } from './cleanup';

let root: string;
beforeEach(async () => { root = await realpath(await mkdtemp(join(tmpdir(), 'desk-cleanup-'))); });
afterEach(async () => rm(root, { recursive: true, force: true }));
const git = (...args: string[]) => execFileSync('git', args, { cwd: root, encoding: 'utf8' });
async function file(path: string, content = 'data') {
  const full = join(root, path);
  await mkdir(join(full, '..'), { recursive: true });
  await writeFile(full, content);
}
async function node() {
  await file('package.json', '{}');
  await file('package-lock.json', '{}');
  await file('node_modules/package/index.js');
}

it('removes reinstallable dependencies and caches, preserving source, outputs and ordinary ignored files', async () => {
  git('init', '-q');
  await node();
  await file('requirements.txt', 'pytest==8.0.0');
  await file('.venv/pyvenv.cfg', 'home = /usr/bin');
  await file('.venv/lib/pkg.py');
  await file('.pytest_cache/result');
  await file('dist/report.pdf');
  await file('unpublished.docx');
  await file('src/main.py');
  await file('.gitignore', 'node_modules/\n.venv/\n.pytest_cache/\ndist/\n');
  git('add', 'package.json', 'package-lock.json', 'requirements.txt', '.gitignore', 'src');
  const report = await workspaceCleanupCandidates(root);
  expect(report.map((r) => r.path).sort()).toEqual(['.pytest_cache', '.venv', 'node_modules'].map((p) => join(root, p)).sort());
  const result = await cleanWorkspaceDependencies(root);
  expect(result.removed).toBe(3);
  expect(result.bytes).toBeGreaterThan(0);
  for (const p of ['src/main.py', 'dist/report.pdf', 'unpublished.docx', 'package-lock.json']) expect(existsSync(join(root, p))).toBe(true);
  expect(await cleanWorkspaceDependencies(root)).toEqual({ removed: 0, bytes: 0 });
});

it('keeps tracked dependencies, unignored caches, and directories without reinstall instructions', async () => {
  git('init', '-q');
  await node();
  await file('.gitignore', 'node_modules/\n');
  git('add', '-f', 'node_modules/package/index.js');
  await file('.pytest_cache/source.py');
  await file('venv/source.py');
  expect(await cleanWorkspaceDependencies(root)).toEqual({ removed: 0, bytes: 0 });
  await rm(join(root, '.git'), { recursive: true });
  await rm(join(root, 'package-lock.json'));
  expect((await workspaceCleanupCandidates(root)).map((r) => r.path)).not.toContain(join(root, 'node_modules'));
});

it('does not follow symlinks or remove a reported skill draft', async () => {
  const ws = join(root, 'workspace');
  const outside = join(root, 'outside');
  await mkdir(ws);
  await mkdir(outside);
  await writeFile(join(outside, 'keep'), 'keep');
  await symlink(outside, join(ws, 'node_modules'));
  await writeFile(join(ws, 'package.json'), '{}');
  await writeFile(join(ws, 'package-lock.json'), '{}');
  await mkdir(join(ws, '.ruff_cache'));
  await writeFile(join(ws, '.ruff_cache', 'SKILL.md'), 'draft');
  expect(await cleanWorkspaceDependencies(ws, [join(ws, '.ruff_cache')])).toEqual({ removed: 0, bytes: 0 });
  expect(await readFile(join(outside, 'keep'), 'utf8')).toBe('keep');
  await symlink(ws, join(root, 'linked'));
  await expect(cleanWorkspaceDependencies(join(root, 'linked'))).rejects.toThrow(/symlink/);
});

it('leaves nested repositories and cancels deletion if the task becomes active', async () => {
  await node();
  await file('nested/.git/config');
  await file('nested/.pytest_cache/state');
  expect((await workspaceCleanupCandidates(root)).map((r) => r.path)).toEqual([join(root, 'node_modules')]);
  expect(await cleanWorkspaceDependencies(root, [], () => false)).toEqual({ removed: 0, bytes: 0 });
  expect(existsSync(join(root, 'node_modules'))).toBe(true);
});
