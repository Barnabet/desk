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

/** Reasoning effort levels, lowest first (what `reasoning_effort` may carry on a chat completion). */
/** A project service's name: lowercase, digits and dashes (`backend`, `web-2`). */
export const ServiceName = z.string().regex(/^[a-z0-9][a-z0-9-]{0,31}$/, 'Use lowercase letters, digits and dashes (max 32)');
export const ServiceStatus = z.enum(['running', 'exited', 'stopped']);
export type ServiceStatus = z.infer<typeof ServiceStatus>;
export const ServiceStopReason = z.enum(['requested', 'restart', 'thread_archived', 'project_archived', 'daemon_shutdown', 'daemon_restart']);
export type ServiceStopReason = z.infer<typeof ServiceStopReason>;
/** Who acted on a service: the user or an agent (`agent:<id>`); `system` for shutdown and recovery. */
export const ServiceActor = z.string().regex(/^(user|system|agent:.+)$/);

export const ReasoningEffort = z.enum(['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']);
export type ReasoningEffort = z.infer<typeof ReasoningEffort>;
export const REASONING_EFFORTS = ReasoningEffort.options;

export const ModelInfo = z
  .object({
    id: z.string().min(1),
    family: z.enum(['claude', 'gpt']),
    context_window: z.number().int().positive(),
    max_output_tokens: z.number().int().positive(),
    /** The levels the endpoint accepts for this model; empty when it takes no `reasoning_effort`. */
    reasoning_efforts: z.array(ReasoningEffort).default([]),
    /** Sent when neither the project nor the thread picks a level; null leaves it to the endpoint. */
    default_reasoning_effort: ReasoningEffort.nullable().default(null),
    concurrency: z.number().int().min(1),
  })
  .refine((m) => m.default_reasoning_effort === null || m.reasoning_efforts.includes(m.default_reasoning_effort), {
    message: 'The default reasoning effort must be one of the model’s levels',
    path: ['default_reasoning_effort'],
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
  /** From the runtime to Desk itself: a nudge (e.g. to update What's up); never shown in the chat. */
  'reminder',
]);
export type AgentMessageKind = z.infer<typeof AgentMessageKind>;

export const MemoryKind = z.enum(['fact', 'decision', 'preference', 'contact', 'note']);
export type MemoryKind = z.infer<typeof MemoryKind>;

export const SkillScope = z.enum(['project', 'global']);
export type SkillScope = z.infer<typeof SkillScope>;

/** Agent Skills naming: lowercase letters, digits and single hyphens, at most 64 characters. */
export const SkillName = z
  .string()
  .max(64)
  .regex(/^[a-z0-9]+(-[a-z0-9]+)*$/, 'Skill names use lowercase letters, digits and single hyphens (e.g. "weekly-report")');

/** Project id used for events about global skills changed outside any project. */
export const GLOBAL_PROJECT_ID = '_global';

export const ArtifactKind = z.enum(['file', 'report', 'code', 'data', 'other']);
export type ArtifactKind = z.infer<typeof ArtifactKind>;

export const PlanItemStatus = z.enum(['todo', 'in_progress', 'done', 'dropped']);
export type PlanItemStatus = z.infer<typeof PlanItemStatus>;

export const PlanItem = z.object({
  id: z.string(),
  title: z.string().min(1),
  status: PlanItemStatus,
  thread_ids: z.array(z.string()),
  notes: z.string(),
});
export type PlanItem = z.infer<typeof PlanItem>;
