import { execFileSync } from 'node:child_process';
import { mkdir, mkdtemp, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { AgentRow } from '../state/queries';
import { ToolDenied } from '../tools/types';
import { createWorkspace } from './workspaces';
import { listWorkspace, resolveWorkspaceFile, threadDiff } from './inspect';

let dir: string;
beforeEach(async () => {
  dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-inspect-')));
});
afterEach(async () => rm(dir, { recursive: true, force: true }));

const g = (cwd: string, ...args: string[]) => execFileSync('git', ['-c', 'user.email=t@t', '-c', 'user.name=t', ...args], { cwd, stdio: 'pipe' }).toString();

function row(over: Partial<AgentRow>): AgentRow {
  return {
    id: 't1', project_id: 'p', role: 'thread', status: 'running', model: 'm', title: 'T', brief: 'b', workspace_path: null, parent_id: null,
    inbox_cursor: 0, review_round: 0, result_summary: null, result_artifacts: null, active_skills: [], git_source_id: null, git_branch: null,
    git_base: null, git_common_dir: null, archived_at: null, created_at: '', updated_at: '', ...over,
  };
}

async function gitThread() {
  const repo = join(dir, 'repo');
  await mkdir(repo);
  g(repo, 'init', '-q', '-b', 'main');
  await writeFile(join(repo, 'a.txt'), 'one\ntwo\n');
  await writeFile(join(repo, 'gone.txt'), 'bye\n');
  g(repo, 'add', '.');
  g(repo, 'commit', '-qm', 'init');
  const ws = join(dir, 'ws');
  const { git } = await createWorkspace({ path: ws, git: { sourcePath: repo, branch: 'desk/test' } });
  return row({ workspace_path: ws, git_branch: git!.branch, git_base: git!.base, git_common_dir: git!.common_dir });
}

describe('threadDiff', () => {
  it('reports added, modified and deleted files with line counts and a patch', async () => {
    const t = await gitThread();
    await writeFile(join(t.workspace_path!, 'a.txt'), 'one\nTWO\nthree\n');
    await writeFile(join(t.workspace_path!, 'new.md'), '# hi\n');
    await rm(join(t.workspace_path!, 'gone.txt'));
    const d = await threadDiff(t);
    expect(d).toMatchObject({ base: t.git_base, branch: 'desk/test' });
    expect(d.files).toEqual([
      { path: 'a.txt', status: 'modified', additions: 2, deletions: 1 },
      { path: 'gone.txt', status: 'deleted', additions: 0, deletions: 1 },
      { path: 'new.md', status: 'added', additions: 1, deletions: 0 },
    ]);
    expect(d.patch).toContain('+TWO');
  });

  it('refuses non-git and archived threads', async () => {
    await expect(threadDiff(row({ workspace_path: dir }))).rejects.toThrow(/does not work in git/);
    const t = await gitThread();
    await expect(threadDiff({ ...t, archived_at: 'x' })).rejects.toThrow(/archived/);
  });
});

describe('workspace files', () => {
  it('lists directories first, hides .git, and resolves files inside the workspace only', async () => {
    const ws = join(dir, 'plain');
    await mkdir(join(ws, 'emails'), { recursive: true });
    await writeFile(join(ws, 'emails', '01.md'), 'hello');
    await writeFile(join(ws, 'notes.txt'), 'n');
    await mkdir(join(ws, '.git'));
    await symlink('/etc', join(ws, 'escape'));
    const t = row({ workspace_path: ws });
    expect(await listWorkspace(t)).toEqual([
      { name: 'emails', path: 'emails', type: 'dir', size: 0 },
      { name: 'escape', path: 'escape', type: 'dir', size: 0 },
      { name: 'notes.txt', path: 'notes.txt', type: 'file', size: 1 },
    ]);
    expect(await listWorkspace(t, 'emails')).toEqual([{ name: '01.md', path: 'emails/01.md', type: 'file', size: 5 }]);
    expect(await resolveWorkspaceFile(t, 'emails/01.md')).toBe(join(ws, 'emails', '01.md'));
    await expect(resolveWorkspaceFile(t, '../repo')).rejects.toBeInstanceOf(ToolDenied);
    await expect(resolveWorkspaceFile(t, 'escape/hosts')).rejects.toBeInstanceOf(ToolDenied);
    await expect(listWorkspace(t, 'escape')).rejects.toBeInstanceOf(ToolDenied);
    await expect(resolveWorkspaceFile(t, 'missing.txt')).rejects.toThrow(/No such file/);
    await expect(listWorkspace({ ...t, archived_at: 'x' })).rejects.toThrow(/archived/);
  });
});
