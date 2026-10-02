import { lstat, readdir, realpath, rm } from 'node:fs/promises';
import { dirname, join, relative, resolve } from 'node:path';
import { isWithin } from '../tools/sandbox';
import { git } from './workspaces';

export const WORKSPACE_RETENTION_MS = 24 * 60 * 60_000;
export type CleanupCandidate = { path: string; bytes: number; dev: number; ino: number };

const nodeLocks = ['package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml', 'yarn.lock', 'bun.lock', 'bun.lockb'];
const pythonSpecs = ['requirements.txt', 'requirements-dev.txt', 'uv.lock', 'poetry.lock', 'Pipfile.lock'];
const caches = new Set(['.pytest_cache', '.mypy_cache', '.ruff_cache', '__pycache__']);

async function regularFile(path: string): Promise<boolean> {
  return (await lstat(path).catch(() => null))?.isFile() ?? false;
}

/** Only conventional dependencies with reinstall instructions, and named Python tool caches. Never build outputs. */
async function reproducible(path: string, root: string): Promise<boolean> {
  const name = path.slice(dirname(path).length + 1);
  if (caches.has(name)) return true;
  if (name !== 'node_modules' && name !== '.venv' && name !== 'venv') return false;
  if (name !== 'node_modules' && !(await regularFile(join(path, 'pyvenv.cfg')))) return false;
  for (let dir = dirname(path); isWithin(dir, root); dir = dirname(dir)) {
    if (name === 'node_modules') {
      if (await regularFile(join(dir, 'package.json'))) {
        for (const lock of nodeLocks) if (await regularFile(join(dir, lock))) return true;
      }
    } else {
      for (const spec of pythonSpecs) if (await regularFile(join(dir, spec))) return true;
    }
    if (dir === root) break;
  }
  return false;
}

/** Measures entries without following links, including links inside dependency directories. */
async function treeBytes(path: string): Promise<number> {
  const st = await lstat(path);
  if (!st.isDirectory()) return st.size;
  let total = 0;
  for (const e of await readdir(path)) {
    if (e === '.git') throw new Error('Dependency directory contains a Git repository; it was kept');
    total += await treeBytes(join(path, e));
  }
  return total;
}

async function safeCandidate(root: string, path: string, inGit: boolean, protectedPaths: string[]): Promise<boolean> {
  if (protectedPaths.some((p) => isWithin(p, path) || isWithin(path, p))) return false;
  if ((await realpath(path)) !== path || !(await reproducible(path, root))) return false;
  if (inGit) {
    const rel = relative(root, path);
    // Check each candidate separately: truncating a huge repository-wide index listing must never hide tracked files.
    if (await git(root, ['ls-files', '--cached', '-z', '--', rel])) return false;
    try {
      if (!(await git(root, ['check-ignore', '--', `${rel}/`]))) return false;
    } catch {
      return false;
    }
  }
  return true;
}

/** Inspects only this workspace. Symlinks, repositories inside it, and arbitrary ignored output are preserved. */
export async function workspaceCleanupCandidates(path: string, protectedPaths: string[] = []): Promise<CleanupCandidate[]> {
  const root = resolve(path);
  if ((await realpath(root)) !== root) throw new Error('Workspace is a symlink; cleanup was skipped');
  const inGit = !!(await lstat(join(root, '.git')).catch(() => null));
  if (inGit && resolve(await git(root, ['rev-parse', '--show-toplevel'])) !== root) throw new Error('Workspace repository does not match its folder');
  const out: CleanupCandidate[] = [];
  const visit = async (dir: string) => {
    for (const e of await readdir(dir, { withFileTypes: true })) {
      if (!e.isDirectory() || e.name === '.git' || e.name === '.desk') continue;
      const full = join(dir, e.name);
      if (protectedPaths.some((p) => isWithin(full, p))) continue;
      // Do not delete or descend into nested repositories, even if ignored by the outer one.
      if (await lstat(join(full, '.git')).catch(() => null)) continue;
      const known = caches.has(e.name) || ['node_modules', '.venv', 'venv'].includes(e.name);
      if (known) {
        if (await safeCandidate(root, full, inGit, protectedPaths)) {
          const st = await lstat(full);
          out.push({ path: full, bytes: await treeBytes(full), dev: st.dev, ino: st.ino });
        }
        continue;
      }
      await visit(full);
    }
  };
  await visit(root);
  return out;
}

/** Revalidates immediately before removal. The caller holds the agent's maintenance lock for the whole operation. */
export async function cleanWorkspaceDependencies(path: string, protectedPaths: string[] = [], stillIdle: () => boolean = () => true): Promise<{ removed: number; bytes: number }> {
  const root = resolve(path);
  const inGit = !!(await lstat(join(root, '.git')).catch(() => null));
  const candidates = await workspaceCleanupCandidates(root, protectedPaths);
  let removed = 0;
  let bytes = 0;
  for (const c of candidates) {
    if (!stillIdle()) break;
    const st = await lstat(c.path).catch(() => null);
    if (!st?.isDirectory() || st.dev !== c.dev || st.ino !== c.ino || !(await safeCandidate(root, c.path, inGit, protectedPaths)) || !stillIdle()) continue;
    await rm(c.path, { recursive: true });
    removed++;
    bytes += c.bytes;
  }
  return { removed, bytes };
}
