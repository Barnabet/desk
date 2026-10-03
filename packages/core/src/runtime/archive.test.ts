import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { afterEach, expect, it } from 'vitest';
import { text } from '@desk/fake-model';
import { newId } from '../ids';
import { getAgent, getDeskAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';
import { createWorkspace, threadBranchName } from '../workspaces/workspaces';

const git = (cwd: string, ...args: string[]) => execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();

let h: Harness;
afterEach(async () => h?.cleanup());

/** A finished thread in its managed workspace: a scratch folder, or a worktree of a fresh repository. */
async function setup(kind: 'scratch' | 'worktree') {
  h = await createHarness({ script: () => text('ok') });
  const rt = newRuntime(h);
  const project = rt.createProject({ name: 'P', goal: 'G' });
  const desk = getDeskAgent(h.store.db, project)!;
  const id = newId();
  const workspace = join(h.dir, 'workspaces', id);
  let repo: string | null = null;
  let branch: string | null = null;
  if (kind === 'worktree') {
    repo = join(h.files, 'repo');
    await mkdir(repo, { recursive: true });
    git(repo, 'init', '-q', '-b', 'main');
    git(repo, 'config', 'user.email', 'dev@example.com');
    git(repo, 'config', 'user.name', 'Dev');
    await writeFile(join(repo, 'README.md'), 'hello\n');
    git(repo, 'add', '.');
    git(repo, 'commit', '-q', '-m', 'init');
    branch = threadBranchName('Finished', id);
    await createWorkspace({ path: workspace, git: { sourcePath: repo, branch } });
  } else {
    await mkdir(workspace, { recursive: true });
  }
  h.store.append([
    { project_id: project, agent_id: id, type: 'agent.created', payload: { role: 'thread', model: FAKE_MODEL.id, title: 'Finished', brief: 'G', workspace_path: workspace, parent_id: desk.id } },
    { project_id: project, agent_id: id, type: 'agent.status_changed', payload: { status: 'done' } },
  ]);
  return { rt, id, workspace, repo, branch };
}

it("removes a scratch workspace's files when the user archives the thread", async () => {
  const { rt, id, workspace } = await setup('scratch');
  await writeFile(join(workspace, 'notes.md'), 'draft');
  await rt.archiveThread(id);
  expect(existsSync(workspace)).toBe(false);
  expect(getAgent(h.store.db, id)?.archived_at).toBeTruthy();
});

it('removes a worktree with untracked files and uncommitted edits, keeping its branch', async () => {
  const { rt, id, workspace, repo, branch } = await setup('worktree');
  await writeFile(join(workspace, 'README.md'), 'edited\n');
  await writeFile(join(workspace, 'output.txt'), 'scratch');
  await rt.archiveThread(id);
  expect(existsSync(workspace)).toBe(false);
  expect(git(repo!, 'worktree', 'list')).not.toContain(workspace);
  expect(git(repo!, 'branch', '--list', branch!)).toContain(branch!);
  expect(getAgent(h.store.db, id)?.archived_at).toBeTruthy();
});

it('refuses to archive a detached worktree, whose commits no branch keeps', async () => {
  const { rt, id, workspace } = await setup('worktree');
  git(workspace, 'checkout', '-q', '--detach');
  await expect(rt.archiveThread(id)).rejects.toThrow(/Detached worktree/);
  expect(existsSync(workspace)).toBe(true);
  expect(getAgent(h.store.db, id)?.archived_at).toBeNull();
});
