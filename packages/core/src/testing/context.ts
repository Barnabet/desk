import { JobManager } from '../tools/jobs';
import { NO_SANDBOX } from '../tools/sandbox';
import type { ToolContext } from '../tools/types';

/** A ToolContext for unit tests: unsandboxed, workspace doubles as the only read root. */
export function testToolContext(workspace: string, overrides: Partial<ToolContext> = {}): ToolContext {
  return {
    projectId: 'p',
    agentId: 'a',
    runId: 'r',
    toolCallId: 't',
    workspace,
    readRoots: [workspace],
    signal: new AbortController().signal,
    sandbox: NO_SANDBOX,
    jobs: new JobManager(),
    ...overrides,
  };
}
