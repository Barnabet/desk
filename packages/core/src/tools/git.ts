import { z } from 'zod';
import { git } from '../workspaces/workspaces';
import { runProcess } from './process';
import { defineTool, type ToolContext } from './types';

/** Daemon env for git/gh (needs HOME, SSH agent, credential helpers) minus model credentials; never prompts. */
function gitEnv(): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, GIT_TERMINAL_PROMPT: '0' };
  for (const key of Object.keys(env)) if (/^(DESK_OPENAI|CLIPROXY)_/.test(key)) delete env[key];
  return env;
}

function requireGit(ctx: ToolContext): { branch: string; base: string } {
  if (!ctx.git) throw new Error('Your workspace is not a git worktree; git tools are unavailable');
  return ctx.git;
}

export const gitStatusTool = defineTool({
  name: 'git_status',
  description: 'Show the git status of your worktree (branch, staged/unstaged/untracked files).',
  input: z.object({}),
  async execute(_i, ctx) {
    requireGit(ctx);
    return (await git(ctx.workspace, ['status', '--short', '--branch'], gitEnv())) || '(clean)';
  },
});

export const gitDiffTool = defineTool({
  name: 'git_diff',
  description: 'Show changes in your worktree (committed and uncommitted) relative to the branch base, or to `ref` if given.',
  input: z.object({ ref: z.string().optional() }),
  async execute({ ref }, ctx) {
    const g = requireGit(ctx);
    await git(ctx.workspace, ['add', '--intent-to-add', '--all'], gitEnv());
    return (await git(ctx.workspace, ['diff', ref ?? g.base], gitEnv())) || '(no changes)';
  },
});

export const gitCommitTool = defineTool({
  name: 'git_commit',
  description: 'Stage and commit changes in your worktree. Commits all changes unless `paths` are given.',
  input: z.object({ message: z.string().min(1), paths: z.array(z.string()).optional() }),
  async execute({ message, paths }, ctx) {
    requireGit(ctx);
    const env = gitEnv();
    await git(ctx.workspace, paths?.length ? ['add', '--', ...paths] : ['add', '--all'], env);
    const staged = await runProcess({ command: 'git', args: ['diff', '--cached', '--quiet'], cwd: ctx.workspace, env });
    if (staged.exitCode === 0) throw new Error('Nothing to commit');
    const email = await runProcess({ command: 'git', args: ['config', 'user.email'], cwd: ctx.workspace, env });
    const identity = email.exitCode === 0 && email.output.trim() ? [] : ['-c', 'user.name=Desk', '-c', 'user.email=desk@localhost'];
    await git(ctx.workspace, [...identity, 'commit', '-q', '-m', message], env);
    const sha = await git(ctx.workspace, ['rev-parse', '--short', 'HEAD'], env);
    return `Committed ${sha}: ${message.split('\n')[0]}`;
  },
});

export const gitPushTool = defineTool({
  name: 'git_push',
  description: "Push your commits to the remote on your thread's branch.",
  input: z.object({}),
  gate: { subject: (_i, g) => (g.gitBranch ? { branch: g.gitBranch } : {}), unmatched: 'ask' },
  async execute(_i, ctx) {
    const g = requireGit(ctx);
    const out = await git(ctx.workspace, ['push', '-u', 'origin', `HEAD:refs/heads/${g.branch}`], gitEnv());
    return `Pushed to origin/${g.branch}\n${out}`;
  },
});

export const openPrTool = defineTool({
  name: 'open_pr',
  description: 'Open a pull request for your pushed branch with the GitHub CLI. Push first.',
  input: z.object({ title: z.string().min(1), body: z.string(), base: z.string().optional() }),
  gate: { subject: () => ({}), unmatched: 'ask' },
  async execute({ title, body, base }, ctx) {
    const g = requireGit(ctx);
    const r = await runProcess({
      command: 'gh',
      args: ['pr', 'create', '--head', g.branch, '--title', title, '--body', body, ...(base ? ['--base', base] : [])],
      cwd: ctx.workspace,
      env: gitEnv(),
      timeoutMs: 120_000,
    });
    if (r.exitCode !== 0) throw new Error(`gh pr create failed: ${r.output.trim()}`);
    return r.output.trim();
  },
});

export const gitTools = [gitStatusTool, gitDiffTool, gitCommitTool, gitPushTool, openPrTool];
