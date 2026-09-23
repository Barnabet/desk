import type { AgentRow } from '../state/queries';
import type { JobManager } from '../tools/jobs';
import type { SandboxSpec } from '../tools/sandbox';
import type { ToolContext } from '../tools/types';

export type ToolEnvironment = { sandboxEnabled: boolean; jobs: JobManager; readRoots?: (agent: AgentRow) => string[] };

/** Builds the execution context for one tool call of an agent. */
export function buildToolContext(agent: AgentRow, runId: string, toolCallId: string, signal: AbortSignal, env: ToolEnvironment): ToolContext {
  const workspace = agent.workspace_path;
  if (!workspace) throw new Error(`Agent ${agent.id} has no workspace`);
  const sandbox: SandboxSpec = { enabled: env.sandboxEnabled, writable: [workspace] };
  return {
    projectId: agent.project_id,
    agentId: agent.id,
    runId,
    toolCallId,
    workspace,
    readRoots: env.readRoots?.(agent) ?? [workspace],
    signal,
    sandbox,
    jobs: env.jobs,
  };
}
