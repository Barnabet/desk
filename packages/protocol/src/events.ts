import { z } from 'zod';
import { AgentRole, AgentStatus, RunFinishReason, ToolCall, ToolResultStatus } from './domain';

const event = <T extends string, P extends z.ZodType>(type: T, payload: P) =>
  z.object({ type: z.literal(type), payload });

export const EventBody = z.discriminatedUnion('type', [
  event('project.created', z.object({ name: z.string().min(1), goal: z.string(), instructions: z.string() })),
  event(
    'agent.created',
    z.object({
      role: AgentRole,
      model: z.string().min(1),
      title: z.string().nullable(),
      brief: z.string().nullable(),
      workspace_path: z.string().nullable(),
      parent_id: z.string().nullable(),
    }),
  ),
  event('agent.status_changed', z.object({ status: AgentStatus, reason: z.string().optional() })),
  event('message.user', z.object({ text: z.string().min(1) })),
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
export const INBOX_EVENT_TYPES = ['message.user'] as const satisfies readonly EventType[];
