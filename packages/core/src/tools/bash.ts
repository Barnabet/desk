import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { z } from 'zod';
import { runProcess } from './process';
import { shellInvocation } from './sandbox';
import { defineTool } from './types';

export const SAFE_ENV_KEYS = ['PATH', 'HOME', 'LANG', 'TERM', 'TMPDIR', 'USER', 'SHELL'] as const;

export function scrubbedEnv(workspace: string, source: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {};
  for (const key of SAFE_ENV_KEYS) {
    const value = source[key];
    if (value !== undefined) env[key] = value;
  }
  env.DESK_WORKSPACE = workspace;
  // Keep package-manager caches inside the sandbox's writable temp area.
  const cache = join(source.TMPDIR ?? tmpdir(), 'desk-cache');
  env.XDG_CACHE_HOME = cache;
  env.npm_config_cache = join(cache, 'npm');
  env.npm_config_store_dir = join(cache, 'pnpm-store');
  env.PIP_CACHE_DIR = join(cache, 'pip');
  env.YARN_CACHE_FOLDER = join(cache, 'yarn');
  return env;
}

/** Puts skill runtimes first on PATH and adds their variables. */
export function withSkillEnv(env: NodeJS.ProcessEnv, e: { bins: string[]; vars: Record<string, string> }): NodeJS.ProcessEnv {
  if (!e.bins.length && !Object.keys(e.vars).length) return env;
  return { ...env, ...e.vars, PATH: [...e.bins, env.PATH ?? '/usr/bin:/bin'].join(':') };
}

export const bashTool = defineTool({
  name: 'bash',
  description:
    'Run a shell command with zsh in your workspace directory. stdout and stderr are combined. Default timeout 120s (max 600s). Avoid interactive commands.',
  input: z.object({
    command: z.string().min(1),
    timeout_s: z.number().int().min(1).max(600).default(120),
  }),
  gate: { subject: (i) => ({ command: i.command }), unmatched: 'auto' },
  async execute({ command, timeout_s }, ctx) {
    const r = await runProcess({
      ...shellInvocation(command, ctx.sandbox),
      cwd: ctx.workspace,
      env: withSkillEnv(scrubbedEnv(ctx.workspace), ctx.services.skillEnv(ctx.agentId)),
      timeoutMs: timeout_s * 1000,
      signal: ctx.signal,
    });
    const status = r.aborted ? 'aborted' : r.timedOut ? `timed out after ${timeout_s}s` : `exit code ${r.exitCode}`;
    return `[${status}]\n${r.output}`;
  },
});

export const bashReadonlyTool = defineTool({
  name: 'bash_readonly',
  description:
    'Run a read-only shell command (zsh) for inspection: listing, searching, git log/diff, running read-only scripts. Writes are blocked except to temp dirs. Default timeout 120s.',
  input: z.object({
    command: z.string().min(1),
    timeout_s: z.number().int().min(1).max(600).default(120),
  }),
  gate: { subject: (i) => ({ command: i.command }), unmatched: 'auto' },
  async execute({ command, timeout_s }, ctx) {
    const r = await runProcess({
      ...shellInvocation(command, { ...ctx.sandbox, writable: [] }),
      cwd: ctx.workspace,
      env: withSkillEnv(scrubbedEnv(ctx.workspace), ctx.services.skillEnv(ctx.agentId)),
      timeoutMs: timeout_s * 1000,
      signal: ctx.signal,
    });
    const status = r.aborted ? 'aborted' : r.timedOut ? `timed out after ${timeout_s}s` : `exit code ${r.exitCode}`;
    return `[${status}]\n${r.output}`;
  },
});
