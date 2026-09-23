import type { z } from 'zod';
import type { AgentMessageKind, AgentStatus, ArtifactKind, MemoryKind, ToolResultStatus } from '@desk/protocol';
import type { EventStore } from '../events/store';
import type { JobManager } from './jobs';
import type { SandboxSpec } from './sandbox';

export type ToolContext = {
  projectId: string;
  agentId: string;
  runId: string;
  toolCallId: string;
  workspace: string;
  readRoots: string[];
  signal: AbortSignal;
  sandbox: SandboxSpec;
  jobs: JobManager;
  services: RuntimeServices;
  /** Set when the workspace is a git worktree. */
  git: { branch: string; base: string } | null;
};

/** Runtime capabilities available to tools (implemented by Runtime). */
export interface RuntimeServices {
  readonly store: EventStore;
  writeMemory(projectId: string, input: { kind: MemoryKind; content: string; supersedes?: string }, source: string): string;
  libraryDir(projectId: string): string;
  publishToLibrary(
    projectId: string,
    file: string,
    meta: { title: string; kind: ArtifactKind; description: string; name?: string },
    origin: string,
  ): Promise<{ id: string; path: string }>;
  sendAgentMessage(fromAgentId: string, toAgentId: string, kind: AgentMessageKind, text: string): void;
}

export type ToolYield = { status: AgentStatus; reason?: string };
export type ToolOutput = string | { content: string; yield?: ToolYield };
export type ToolResult = { status: ToolResultStatus; content: string; yield?: ToolYield };

export type PolicySubject = { branch?: string; command?: string; domain?: string };

/** Declares that a tool is checked against project policy rules before it runs. */
/** Facts about the calling agent that policy subjects may depend on. */
export type GateContext = { gitBranch?: string };

export type ToolGate<I = any> = {
  subject: (input: I, gctx: GateContext) => PolicySubject;
  /** Decision when no rule matches: `ask` for outward-facing tools, `auto` for sandboxed shell. */
  unmatched: 'ask' | 'auto';
};

export type Tool<I = any> = {
  name: string;
  description: string;
  input: z.ZodType<I>;
  gate?: ToolGate<I>;
  execute(input: I, ctx: ToolContext): Promise<ToolOutput>;
};

export function defineTool<S extends z.ZodType>(def: {
  name: string;
  description: string;
  input: S;
  gate?: ToolGate<z.output<S>>;
  execute(input: z.output<S>, ctx: ToolContext): Promise<ToolOutput>;
}): Tool<z.output<S>> {
  return def as Tool<z.output<S>>;
}

/** Thrown by tools when an action is not permitted; surfaces as tool.result status "denied". */
export class ToolDenied extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ToolDenied';
  }
}
