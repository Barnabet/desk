import { mkdir, realpath, rm } from 'node:fs/promises';
import { dirname, isAbsolute, resolve } from 'node:path';
import { runProcess } from '../tools/process';

/** Runs git and returns trimmed stdout+stderr; throws with git's output on failure. */
/**
 * deskd runs git outside the sandbox, in repos agents write to, so no repo content may make it run a command: hooks
 * (`core.hooksPath` may point into the working tree) and fsmonitor are off, and diffs skip external diff and textconv
 * drivers. The config that could name other commands stays out of agents' reach (`SandboxSpec.gitDirs`).
 */
export function safeGitArgs(args: string[]): string[] {
  const i = args.indexOf('diff');
  const sub = i >= 0 && args.slice(0, i).every((a, j) => a === '-c' || args[j - 1] === '-c') ? [...args.slice(0, i + 1), '--no-ext-diff', '--no-textconv', ...args.slice(i + 1)] : args;
  return ['-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', '-c', 'protocol.ext.allow=never', ...sub];
}

/** Daemon env for git/gh (needs HOME, SSH agent, credential helpers) minus model credentials; never prompts. */
export function gitEnv(): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, GIT_TERMINAL_PROMPT: '0' };
  for (const key of Object.keys(env)) if (/^(DESK_OPENAI|CLIPROXY)_/.test(key)) delete env[key];
  return env;
}

export async function git(cwd: string, args: string[], env: NodeJS.ProcessEnv = gitEnv()): Promise<string> {
  const r = await runProcess({ command: 'git', args: safeGitArgs(args), cwd, timeoutMs: 120_000, env });
  if (r.exitCode !== 0) throw new Error(`git ${args[0]} failed: ${r.output.trim()}`);
  return r.output.trim();
}

export function threadSlug(title: string): string {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40)
    .replace(/-+$/g, '');
  return slug || 'thread';
}

export function threadBranchName(title: string, threadId: string): string {
  return `desk/${threadSlug(title)}-${threadId.slice(-6).toLowerCase()}`;
}

export type WorkspaceGit = { branch: string; base: string; common_dir: string };

/** Creates a thread workspace: a git worktree on a new branch at the source's HEAD, or a plain directory. */
export async function createWorkspace(opts: { path: string; git?: { sourcePath: string; branch: string } }): Promise<{ git: WorkspaceGit | null }> {
  if (!opts.git) {
    await mkdir(opts.path, { recursive: true });
    return { git: null };
  }
  const { sourcePath, branch } = opts.git;
  let base: string;
  try {
    base = await git(sourcePath, ['rev-parse', 'HEAD']);
  } catch {
    throw new Error(`Source repository ${sourcePath} has no commits to branch from`);
  }
  await mkdir(dirname(opts.path), { recursive: true });
  await git(sourcePath, ['worktree', 'add', '-b', branch, opts.path, base]);
  const common = await git(opts.path, ['rev-parse', '--git-common-dir']);
  const commonDir = await realpath(isAbsolute(common) ? common : resolve(opts.path, common));
  return { git: { branch, base, common_dir: commonDir } };
}

/** Deletes a workspace. Worktrees are unregistered from their repo; their branch is kept. */
export async function removeWorkspace(opts: { path: string; gitSourcePath?: string | null }): Promise<void> {
  if (opts.gitSourcePath) {
    await git(opts.gitSourcePath, ['worktree', 'remove', '--force', opts.path]).catch(() => undefined);
    await git(opts.gitSourcePath, ['worktree', 'prune']).catch(() => undefined);
  }
  await rm(opts.path, { recursive: true, force: true });
}
