import { z } from 'zod';
import { AgentMessageKind, AgentRole, ArtifactKind, AgentStatus, GitInfo, MemoryKind, PlanItem, RunFinishReason, SourceKind, ToolCall, ToolResultStatus } from './domain';
import { ProjectSettingsPatch } from './settings';

const event = <T extends string, P extends z.ZodType>(type: T, payload: P) =>
  z.object({ type: z.literal(type), payload });

export const EventBody = z.discriminatedUnion('type', [
  event(
    'project.created',
    z.object({ name: z.string().min(1), goal: z.string(), instructions: z.string(), settings: ProjectSettingsPatch.optional() }),
  ),
  event('project.archived', z.object({})),
  event(
    'project.updated',
    z.object({
      name: z.string().min(1).optional(),
      goal: z.string().optional(),
      instructions: z.string().optional(),
      settings: ProjectSettingsPatch.optional(),
    }),
  ),
  event(
    'agent.created',
    z.object({
      role: AgentRole,
      model: z.string().min(1),
      title: z.string().nullable(),
      brief: z.string().nullable(),
      workspace_path: z.string().nullable(),
      parent_id: z.string().nullable(),
      git: GitInfo.nullable().optional(),
    }),
  ),
  event('source.added', z.object({ source_id: z.string(), path: z.string(), kind: SourceKind, label: z.string() })),
  event('source.removed', z.object({ source_id: z.string() })),
  event('agent.status_changed', z.object({ status: AgentStatus, reason: z.string().optional() })),
  event('agent.result', z.object({ summary: z.string().min(1), artifacts: z.array(z.string()) })),
  event(
    'agent.model_switched',
    z.object({ from: z.string(), to: z.string(), reason: z.string(), scope: z.literal('run') }),
  ),
  event('agent.revision', z.object({ round: z.number().int().min(1), feedback: z.string() })),
  event('agent.archived', z.object({})),
  event('plan.updated', z.object({ items: z.array(PlanItem) })),
  event(
    'report',
    z.object({ headline: z.string().min(1), progress: z.string(), needs_you: z.array(z.string()), results: z.array(z.string()) }),
  ),
  event('question.asked', z.object({ question: z.string().min(1), options: z.array(z.string()).optional() })),
  event('message.user', z.object({ text: z.string().min(1) })),
  event(
    'message.agent',
    z.object({ from_agent_id: z.string(), from_label: z.string(), kind: AgentMessageKind, text: z.string().min(1) }),
  ),
  event('inbox.drained', z.object({ run_id: z.string(), up_to: z.number().int() })),
  event('run.started', z.object({ run_id: z.string(), model: z.string() })),
  event('run.finished', z.object({ run_id: z.string(), reason: RunFinishReason, detail: z.string().optional() })),
  event(
    'assistant.message',
    z.object({ run_id: z.string(), content: z.string().nullable(), tool_calls: z.array(ToolCall) }),
  ),
  event(
    'tool.call',
    z.object({ run_id: z.string(), tool_call_id: z.string(), name: z.string(), arguments: z.string() }),
  ),
  event(
    'tool.result',
    z.object({
      run_id: z.string(),
      tool_call_id: z.string(),
      name: z.string(),
      status: ToolResultStatus,
      content: z.string(),
    }),
  ),
  event(
    'approval.requested',
    z.object({
      approval_id: z.string(),
      run_id: z.string(),
      tool_call_id: z.string(),
      tool: z.string(),
      arguments: z.string(),
      reason: z.string(),
      delegate_to_desk: z.boolean(),
    }),
  ),
  event(
    'approval.resolved',
    z.object({
      approval_id: z.string(),
      decision: z.enum(['approved', 'denied']),
      resolved_by: z.enum(['user', 'desk', 'system']),
      note: z.string().optional(),
    }),
  ),
  event(
    'memory.written',
    z.object({
      memory_id: z.string(),
      kind: MemoryKind,
      content: z.string().min(1),
      source: z.string().regex(/^(user|agent:.+)$/),
      supersedes: z.string().optional(),
    }),
  ),
  event('memory.deleted', z.object({ memory_id: z.string() })),
  event(
    'artifact.published',
    z.object({
      artifact_id: z.string(),
      path: z.string().min(1),
      title: z.string(),
      kind: ArtifactKind,
      origin: z.string().regex(/^(user|agent:.+)$/),
      description: z.string(),
    }),
  ),
  event(
    'system.notice',
    z.object({ level: z.enum(['info', 'warning', 'error']), code: z.string(), message: z.string() }),
  ),
  event(
    'usage',
    z.object({
      run_id: z.string(),
      model: z.string(),
      prompt_tokens: z.number().int().nonnegative(),
      completion_tokens: z.number().int().nonnegative(),
      cached_tokens: z.number().int().nonnegative().optional(),
      estimated: z.boolean(),
    }),
  ),
]);
export type EventBody = z.infer<typeof EventBody>;
export type EventType = EventBody['type'];

export type EventInput = EventBody & { project_id: string; agent_id: string | null };
export type StoredEvent = EventInput & { id: number; ts: string };
export type EventOf<T extends EventType> = Extract<StoredEvent, { type: T }>;

export const EphemeralEvent = z.discriminatedUnion('type', [
  z.object({
    type: z.literal('assistant.delta'),
    project_id: z.string(),
    agent_id: z.string(),
    payload: z.object({ run_id: z.string(), text: z.string() }),
  }),
]);
export type EphemeralEvent = z.infer<typeof EphemeralEvent>;

/** Event types that land in an agent's inbox and trigger/steer runs. */
export const INBOX_EVENT_TYPES = ['message.user', 'message.agent'] as const satisfies readonly EventType[];
