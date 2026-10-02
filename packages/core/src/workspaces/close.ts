import { lstat, readdir, realpath, rm } from 'node:fs/promises';
import { isAbsolute, join, relative, resolve } from 'node:path';
import { ConflictError } from '../errors';
import { isWithin } from '../tools/sandbox';
import { git } from './workspaces';

export type WorkspaceClosure = {
  bytes: number;
  files: number;
  entries: string[];
  repositories: Array<{ path: string; common: string; branch: string; head: string }>;
};

/** Closing is explicit disposal, unlike cache cleanup. Git history must survive outside the workspace. */
export async function inspectWorkspaceClosure(path: string): Promise<WorkspaceClosure> {
  const root = resolve(path);
  const result: WorkspaceClosure = { bytes: 0, files: 0, entries: [], repositories: [] };
  const st = await lstat(root).catch((e: NodeJS.ErrnoException) => { if (e.code === 'ENOENT') return null; throw e; });
  if (!st) return result;
  if (!st.isDirectory() || await realpath(root) !== root) throw new ConflictError('Workspace is not a real directory; nothing was deleted');
  result.entries = (await readdir(root)).slice(0, 100);
  const visit = async (dir: string): Promise<void> => {
    if (await lstat(join(dir, 'HEAD')).catch(() => null)
      && await lstat(join(dir, 'objects')).catch(() => null)
      && await lstat(join(dir, 'refs')).catch(() => null)) throw new ConflictError(`Possible bare Git repository in ${dir}; preserve its history outside the workspace before closing`);
    const marker = await lstat(join(dir, '.git')).catch((e: NodeJS.ErrnoException) => { if (e.code === 'ENOENT') return null; throw e; });
    if (marker) {
      if (!marker.isFile()) throw new ConflictError(`Repository ${dir} stores its own Git history here. Move it to a permanent source before closing`);
      if (resolve(await git(dir, ['rev-parse', '--show-toplevel'])) !== dir) throw new ConflictError(`Repository root does not match ${dir}`);
      const raw = await git(dir, ['rev-parse', '--git-common-dir']);
      const common = await realpath(isAbsolute(raw) ? raw : resolve(dir, raw));
      if (isWithin(common, root)) throw new ConflictError(`Git history for ${dir} is inside the workspace; preserve it before closing`);
      let branch: string;
      try { branch = await git(dir, ['symbolic-ref', '--quiet', 'HEAD']); }
      catch { throw new ConflictError(`Detached worktree ${dir}: create a retained branch before closing`); }
      const head = await git(dir, ['rev-parse', 'HEAD']);
      if (await git(dir, ['status', '--porcelain=v1', '--untracked-files=no'])) throw new ConflictError(`Uncommitted tracked changes in ${dir}; commit or preserve them before closing`);
      const gitDir = await git(dir, ['rev-parse', '--absolute-git-dir']);
      if (await lstat(join(gitDir, 'locked')).catch(() => null)) throw new ConflictError(`Worktree ${dir} is locked; it was kept`);
      result.repositories.push({ path: dir, common, branch, head });
    }
    for (const name of await readdir(dir)) {
      if (name === '.git') continue;
      const full = join(dir, name);
      const entry = await lstat(full);
      if (entry.isDirectory()) await visit(full);
      else { result.files++; result.bytes += entry.size; }
    }
  };
  await visit(root);
  return result;
}

/** Caller has explicitly declared remaining untracked/ignored/scratch files disposable and holds the task lock. */
export async function closeWorkspace(path: string, stillSafe: () => void): Promise<WorkspaceClosure> {
  const report = await inspectWorkspaceClosure(path);
  stillSafe();
  // Validate all repositories before removing any. Nested worktrees go first; their branches remain in common Git dirs.
  for (const repo of [...report.repositories].sort((a, b) => b.path.length - a.path.length)) {
    stillSafe();
    if (await realpath(repo.path) !== repo.path
      || await git(repo.path, ['symbolic-ref', '--quiet', 'HEAD']) !== repo.branch
      || await git(repo.path, ['rev-parse', 'HEAD']) !== repo.head
      || await git(repo.path, ['status', '--porcelain=v1', '--untracked-files=no'])) throw new ConflictError(`Worktree changed during closing: ${relative(path, repo.path) || '.'}`);
    // --force discards only explicitly reviewed untracked/ignored files; tracked edits were refused above.
    await git(repo.common, ['worktree', 'remove', '--force', repo.path]);
  }
  stillSafe();
  if (await lstat(path).catch((e: NodeJS.ErrnoException) => { if (e.code === 'ENOENT') return null; throw e; })) {
    if (await realpath(path) !== resolve(path)) throw new ConflictError('Workspace path changed during closing');
    await rm(path, { recursive: true });
  }
  return report;
}
