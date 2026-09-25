import type { AgentRow } from '../state/queries';
import type { JobManager } from '../tools/jobs';
import type { SandboxGuard, SandboxSpec } from '../tools/sandbox';
import type { RuntimeServices, ToolContext } from '../tools/types';

export type ToolEnvironment = {
  sandboxEnabled: boolean;
  jobs: JobManager;
  services: RuntimeServices;
  readRoots?: (agent: AgentRow) => string[];
  /** Extra folders the agent may write to: project sources that allow it. */
  writeRoots?: (agent: AgentRow) => string[];
  /** What stays off limits to its commands and file tools. */
  guard?: SandboxGuard;
  /** Git dirs of the repos deskd runs git in for this agent (see `SandboxSpec.gitDirs`). */
  gitDirs?: (agent: AgentRow) => string[];
};

/** Builds the execution context for one tool call of an agent. */
export function buildToolContext(agent: AgentRow, runId: string, toolCallId: string, signal: AbortSignal, env: ToolEnvironment): ToolContext {
  const workspace = agent.workspace_path;
  if (!workspace) throw new Error(`Agent ${agent.id} has no workspace`);
  // Worktree threads may also write their repo's shared git dir (objects, refs) so plain `git` works in the shell.
  const extra = env.writeRoots?.(agent) ?? [];
  const writable = [workspace, ...(agent.git_common_dir ? [agent.git_common_dir] : []), ...extra];
  const gitDirs = env.gitDirs?.(agent) ?? [];
  const sandbox: SandboxSpec = { enabled: env.sandboxEnabled, writable, ...(env.guard ? { guard: env.guard } : {}), ...(gitDirs.length ? { gitDirs } : {}) };
  return {
    projectId: agent.project_id,
    agentId: agent.id,
    runId,
    toolCallId,
    workspace,
    readRoots: env.readRoots?.(agent) ?? [workspace],
    writeRoots: [workspace, ...extra],
    signal,
    sandbox,
    jobs: env.jobs,
    services: env.services,
    git: agent.git_branch && agent.git_base ? { branch: agent.git_branch, base: agent.git_base } : null,
  };
}
