import { z } from 'zod';

export const AgentRole = z.enum(['desk', 'thread']);
export type AgentRole = z.infer<typeof AgentRole>;

export const AgentStatus = z.enum(['idle', 'queued', 'running', 'waiting', 'done', 'failed', 'cancelled']);
export type AgentStatus = z.infer<typeof AgentStatus>;

export const RunFinishReason = z.enum(['yielded', 'no_tool_calls', 'max_steps', 'stopped', 'error']);
export type RunFinishReason = z.infer<typeof RunFinishReason>;

export const ToolResultStatus = z.enum(['ok', 'error', 'denied', 'interrupted']);
export type ToolResultStatus = z.infer<typeof ToolResultStatus>;

export const ToolCall = z.object({ id: z.string(), name: z.string(), arguments: z.string() });
export type ToolCall = z.infer<typeof ToolCall>;

export const ModelInfo = z.object({
  id: z.string().min(1),
  family: z.enum(['claude', 'gpt']),
  context_window: z.number().int().positive(),
  max_output_tokens: z.number().int().positive(),
  supports_reasoning_effort: z.boolean(),
  concurrency: z.number().int().min(1),
});
export type ModelInfo = z.infer<typeof ModelInfo>;

export const SourceKind = z.enum(['folder', 'git']);
export type SourceKind = z.infer<typeof SourceKind>;

/** Git worktree details for a thread working on a repository source. */
export const GitInfo = z.object({ source_id: z.string(), branch: z.string(), base: z.string(), common_dir: z.string() });
export type GitInfo = z.infer<typeof GitInfo>;

export const AgentMessageKind = z.enum([
  'note',
  'revision',
  'update',
  'question',
  'blocker',
  'completed',
  'failed',
  'cancelled',
  'approval',
  'stalled',
]);
export type AgentMessageKind = z.infer<typeof AgentMessageKind>;

export const MemoryKind = z.enum(['fact', 'decision', 'preference', 'contact', 'note']);
export type MemoryKind = z.infer<typeof MemoryKind>;

export const ArtifactKind = z.enum(['file', 'report', 'code', 'data', 'other']);
export type ArtifactKind = z.infer<typeof ArtifactKind>;
