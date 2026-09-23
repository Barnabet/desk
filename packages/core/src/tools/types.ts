import type { z } from 'zod';
import type { AgentStatus, ToolResultStatus } from '@desk/protocol';

export type ToolContext = {
  projectId: string;
  agentId: string;
  runId: string;
  toolCallId: string;
  workspace: string;
  readRoots: string[];
  signal: AbortSignal;
};

export type ToolYield = { status: AgentStatus; reason?: string };
export type ToolOutput = string | { content: string; yield?: ToolYield };
export type ToolResult = { status: ToolResultStatus; content: string; yield?: ToolYield };

export type Tool<I = any> = {
  name: string;
  description: string;
  input: z.ZodType<I>;
  execute(input: I, ctx: ToolContext): Promise<ToolOutput>;
};

export function defineTool<S extends z.ZodType>(def: {
  name: string;
  description: string;
  input: S;
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
