import { existsSync } from 'node:fs';
import { readdir, stat } from 'node:fs/promises';
import { join, relative } from 'node:path';
import type { ThreadDiff, WorkspaceEntry } from '@desk/protocol';
import { ConflictError, NotFoundError } from '../errors';
import type { AgentRow } from '../state/queries';
import { resolveInside } from '../tools/paths';
import { git } from './workspaces';

function requireWorkspace(t: AgentRow): string {
  if (!t.workspace_path) throw new ConflictError(`Thread ${t.id} has no workspace`);
  if (t.archived_at || !existsSync(t.workspace_path)) {
    throw new ConflictError(`Thread ${t.id} is archived; its workspace was removed${t.git_branch ? ` (branch ${t.git_branch} is kept)` : ''}`);
  }
  return t.workspace_path;
}

const STATUS: Record<string, ThreadDiff['files'][number]['status']> = { A: 'added', M: 'modified', D: 'deleted' };

/** The thread's changes against its base commit, including uncommitted and untracked files. */
export async function threadDiff(t: AgentRow): Promise<ThreadDiff> {
  if (!t.git_base || !t.git_branch) throw new ConflictError(`Thread ${t.id} does not work in git`);
  const ws = requireWorkspace(t);
  await git(ws, ['add', '--intent-to-add', '--all']);
  const names = await git(ws, ['diff', '--no-renames', '--name-status', t.git_base]);
  const counts = await git(ws, ['diff', '--no-renames', '--numstat', t.git_base]);
  const patch = await git(ws, ['diff', '--no-renames', t.git_base]);
  const numbers = new Map<string, [number | null, number | null]>();
  for (const line of counts.split('\n').filter(Boolean)) {
    const [add, del, ...path] = line.split('\t');
    numbers.set(path.join('\t'), [add === '-' ? null : Number(add), del === '-' ? null : Number(del)]);
  }
  const files = names
    .split('\n')
    .filter(Boolean)
    .map((line) => {
      const [code, ...path] = line.split('\t');
      const p = path.join('\t');
      const [additions, deletions] = numbers.get(p) ?? [null, null];
      return { path: p, status: STATUS[code?.[0] ?? 'M'] ?? 'modified', additions, deletions };
    })
    .sort((a, b) => a.path.localeCompare(b.path));
  return { base: t.git_base, branch: t.git_branch, files, patch };
}

/** Lists one directory under `root` (directories first, `.git` hidden). Symlinks show as entries but cannot be followed out. */
export async function listFolder(root: string, rel = ''): Promise<WorkspaceEntry[]> {
  const top = await resolveInside('.', [root], root);
  const dir = await resolveInside(rel || '.', [root], root);
  const entries = await readdir(dir, { withFileTypes: true }).catch(() => {
    throw new NotFoundError(`No such directory: ${rel}`);
  });
  const out: WorkspaceEntry[] = [];
  for (const e of entries) {
    if (e.name === '.git') continue;
    const full = join(dir, e.name);
    const s = await stat(full).catch(() => null);
    const isDir = s ? s.isDirectory() : e.isDirectory();
    out.push({ name: e.name, path: relative(top, full).split('\\').join('/'), type: isDir ? 'dir' : 'file', size: isDir || !s ? 0 : s.size });
  }
  return out.sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'dir' ? -1 : 1));
}

/** Resolves a file under `root` for reading; refuses anything that leads outside it. */
export async function resolveFolderFile(root: string, rel: string): Promise<string> {
  const file = await resolveInside(rel, [root], root);
  const s = await stat(file).catch(() => null);
  if (!s?.isFile()) throw new NotFoundError(`No such file: ${rel}`);
  return file;
}

/** Lists one directory of the workspace (directories first, `.git` hidden). Symlinks show as entries but cannot be followed out. */
export async function listWorkspace(t: AgentRow, rel = ''): Promise<WorkspaceEntry[]> {
  return listFolder(requireWorkspace(t), rel);
}

/** Resolves a workspace file for reading; refuses anything that leads outside the workspace. */
export async function resolveWorkspaceFile(t: AgentRow, rel: string): Promise<string> {
  return resolveFolderFile(requireWorkspace(t), rel);
}
