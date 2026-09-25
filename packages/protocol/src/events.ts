import { z } from 'zod';
import {
  AgentMessageKind,
  AgentRole,
  ArtifactKind,
  AgentStatus,
  GitInfo,
  MemoryKind,
  PlanItem,
  ReasoningEffort,
  RunFinishReason,
  ServiceActor,
  ServiceName,
  ServiceStopReason,
  SkillName,
  SkillScope,
  SourceKind,
  ToolCall,
  ToolImage,
  ToolResultStatus,
} from './domain';
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
      /** A level Desk chose for this thread; otherwise the project's thread setting applies. */
      reasoning_effort: ReasoningEffort.optional(),
      title: z.string().nullable(),
      brief: z.string().nullable(),
      workspace_path: z.string().nullable(),
      parent_id: z.string().nullable(),
      git: GitInfo.nullable().optional(),
      /** Skills active from the start (their instructions are in the agent's system prompt). */
      skills: z.array(SkillName).optional(),
    }),
  ),
  event('source.added', z.object({ source_id: z.string(), path: z.string(), kind: SourceKind, label: z.string(), agent_write: z.boolean().optional() })),
  /** Whether agents may write to (and run services in) the source folder. */
  event('source.updated', z.object({ source_id: z.string(), agent_write: z.boolean() })),
  event('source.removed', z.object({ source_id: z.string() })),
  event('agent.status_changed', z.object({ status: AgentStatus, reason: z.string().optional() })),
  event(
    'agent.result',
    z.object({
      summary: z.string().min(1),
      artifacts: z.array(z.string()),
      /** Skill drafts (workspace directories with a SKILL.md) for Desk to review and install. */
      skill_drafts: z.array(z.string()).optional(),
    }),
  ),
  event(
    'agent.model_switched',
    z.object({ from: z.string(), to: z.string(), reason: z.string(), scope: z.literal('run') }),
  ),
  event('agent.revision', z.object({ round: z.number().int().min(1), feedback: z.string() })),
  event('agent.archived', z.object({})),
  event('agent.skills_changed', z.object({ skills: z.array(SkillName) })),
  event(
    'skill.saved',
    z.object({
      scope: SkillScope,
      name: SkillName,
      version: z.number().int().min(1),
      description: z.string(),
      origin: z.string(),
      change_note: z.string(),
    }),
  ),
  event('skill.deleted', z.object({ scope: SkillScope, name: SkillName, origin: z.string() })),
  /** A catalog skill's Desk-managed runtime (Python/Node environment) changed state. */
  event(
    'skill.runtime_changed',
    z.object({ scope: SkillScope, name: SkillName, state: z.enum(['preparing', 'ready', 'failed', 'removed']), reason: z.string().nullable() }),
  ),
  event('plan.updated', z.object({ items: z.array(PlanItem) })),
  /** Desk's short account of the project for the user: what is happening now, what is next, what waits on them. */
  event('whats_up.updated', z.object({ text: z.string().min(1) })),
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
  event('run.started', z.object({ run_id: z.string(), model: z.string(), reasoning_effort: ReasoningEffort.optional() })),
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
      /** Images shown to the model after the result (view_image). */
      images: z.array(ToolImage).optional(),
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
  event('attention.dismissed', z.object({ item_id: z.string().min(1) })),
  /** A project service (long-lived process in a thread's workspace) started; a new run of an existing name keeps its id. */
  event(
    'service.started',
    z.object({
      service_id: z.string(),
      name: ServiceName,
      command: z.string().min(1),
      /** Relative to the workspace ('.' = its root). */
      cwd: z.string(),
      workspace_agent_id: z.string(),
      /** Runs in this project source folder instead of `workspace_agent_id`'s workspace (then the agent that started it). */
      source_id: z.string().nullable().optional(),
      pid: z.number().int().nullable(),
      by: ServiceActor,
    }),
  ),
  /** The first loopback URL printed by the current run. */
  event('service.url', z.object({ service_id: z.string(), url: z.string().url() })),
  /** The process ended on its own. */
  event('service.exited', z.object({ service_id: z.string(), code: z.number().int().nullable(), signal: z.string().nullable() })),
  event('service.stopped', z.object({ service_id: z.string(), by: ServiceActor, reason: ServiceStopReason })),
  event(
    'context.compacted',
    z.object({
      run_id: z.string(),
      /** Structured summary replacing every conversation message produced by events with id <= up_to. */
      checkpoint: z.string(),
      up_to: z.number().int().nonnegative(),
      trigger: z.enum(['threshold', 'overflow']),
    }),
  ),
  /**
   * The model endpoint refused a request because of images in it (an image it cannot decode): these image
   * occurrences (the tool result that showed each) are sent as text from now on, so one bad image never wedges the agent.
   */
  event(
    'images.withheld',
    z.object({
      run_id: z.string(),
      images: z.array(z.object({ tool_call_id: z.string(), sha256: ToolImage.shape.sha256, name: z.string() })).min(1),
      /** The endpoint's answer, e.g. "400 Could not process image". */
      reason: z.string(),
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
  /** Progress while a skill runtime is being set up (not stored). */
  z.object({
    type: z.literal('skill.runtime_progress'),
    project_id: z.string(),
    agent_id: z.null(),
    payload: z.object({ scope: SkillScope, name: SkillName, step: z.string(), done: z.number().int().optional(), total: z.number().int().optional() }),
  }),
]);
export type EphemeralEvent = z.infer<typeof EphemeralEvent>;

/** Event types that land in an agent's inbox and trigger/steer runs. */
export const INBOX_EVENT_TYPES = ['message.user', 'message.agent'] as const satisfies readonly EventType[];
