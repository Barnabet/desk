import { JobManager } from '../tools/jobs';
import { NO_SANDBOX } from '../tools/sandbox';
import type { RuntimeServices, ToolContext } from '../tools/types';

/** Throws on any access: for unit tests of tools that must not touch runtime services. */
export const NO_SERVICES = new Proxy({} as RuntimeServices, {
  get(_t, prop) {
    throw new Error(`Runtime services are not available in this test context (accessed ${String(prop)})`);
  },
});

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
    services: NO_SERVICES,
    git: null,
    ...overrides,
  };
}
