import { z } from 'zod';
import {
  Acceptance,
  AgentMessageKind,
  AgentRole,
  ArtifactKind,
  AgentStatus,
  CheckStatus,
  CheckWhere,
  GitInfo,
  MemoryKind,
  PlanItem,
  ReasoningEffort,
  ReceiptOutcome,
  RequirementCheck,
  ReviewVerdict,
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
import {
  AutomationDefinition,
  AutomationLayout,
  AutomationName,
  Grant,
  InputValue,
  Outputs,
  RunTrigger,
  SaveVia,
  StepGate,
  StepQuestion,
  StepStatus,
} from './automations';
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
      /** A step agent's run and step (role `step`). */
      automation: z.object({ run_id: z.string(), step_id: z.string() }).optional(),
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
  /** A thread's `complete`, pinned: the commit and the library artifacts it submits (reviews and acceptance spec §1.1). */
  event(
    'submission.created',
    z.object({
      submission_id: z.string(),
      seq: z.number().int().min(1),
      /** HEAD of its worktree; null for a thread without git. */
      commit: z.string().nullable(),
      base: z.string().nullable(),
      artifacts: z.array(z.object({ path: z.string(), sha256: z.string() })),
      claims: z.array(z.string()),
      limitations: z.array(z.string()),
      evidence: z.string(),
    }),
  ),
  /** On the reviewer's stream: what it reviews and against which criteria. `revealed`: it already has the builder's report (a re-review). */
  event(
    'review.requested',
    z.object({
      review_id: z.string(),
      submission_id: z.string(),
      builder_id: z.string(),
      criteria: z.array(z.string().min(1)).min(1),
      focus: z.string().optional(),
      requested_by: z.enum(['desk', 'user']),
      revealed: z.boolean(),
    }),
  ),
  event(
    'review.assessed',
    z.object({
      review_id: z.string(),
      submission_id: z.string(),
      builder_id: z.string(),
      phase: z.enum(['initial', 'final']),
      verdict: ReviewVerdict,
      requirements: z.array(RequirementCheck),
      not_checked: z.array(z.string()),
    }),
  ),
  event(
    'finding.raised',
    z.object({
      finding_id: z.string(),
      review_id: z.string(),
      submission_id: z.string(),
      title: z.string().min(1),
      detail: z.string(),
      blocking: z.boolean(),
      reproducer: z.string().min(1),
    }),
  ),
  event(
    'finding.resolved',
    z.object({
      finding_id: z.string(),
      outcome: z.enum(['fixed', 'waived', 'withdrawn']),
      /** `fixed`: the submission that fixes it. */
      submission_id: z.string().optional(),
      reason: z.string().optional(),
      by: z.enum(['desk', 'user', 'reviewer']),
    }),
  ),
  /** On the builder's stream: Desk's or the user's decision about one submission. */
  event(
    'acceptance.recorded',
    z.object({
      submission_id: z.string(),
      decision: Acceptance.extract(['accepted', 'accepted_with_limitations', 'changes_requested']),
      limitations: z.array(z.string()),
      by: z.enum(['desk', 'user']),
      note: z.string().optional(),
    }),
  ),
  /**
   * A shell command the platform ran for an agent and saw end (receipts spec 2026-10-03 §1): where it ran (the git
   * commit and whether tracked files had uncommitted changes, null outside git), how it ended, and a fingerprint of
   * its output. On the agent's stream; a check's steps carry `check_id` and `step`.
   */
  event(
    'receipt.recorded',
    z.object({
      receipt_id: z.string(),
      tool: z.string(),
      tool_call_id: z.string().optional(),
      check_id: z.string().optional(),
      step: z.number().int().min(0).optional(),
      command: z.string(),
      cwd: z.string(),
      head: z.string().nullable(),
      dirty: z.boolean().nullable(),
      exit_code: z.number().int().nullable(),
      outcome: ReceiptOutcome,
      duration_ms: z.number().int().min(0),
      output_bytes: z.number().int().min(0),
      output_sha256: z.string(),
      started_at: z.string(),
    }),
  ),
  /** On Desk's stream: a check job started (spec 2026-10-03 §2). `head`: the snapshot's commit. */
  event(
    'check.started',
    z.object({
      check_id: z.string(),
      title: z.string(),
      steps: z.array(z.string().min(1)).min(1),
      where: CheckWhere,
      expect: z.array(z.string()),
      timeout_s: z.number().int().min(1),
      cwd: z.string(),
      head: z.string().nullable(),
    }),
  ),
  event(
    'check.finished',
    z.object({
      check_id: z.string(),
      status: CheckStatus.exclude(['running']),
      /** The step that failed (0-based); null when every step passed or none ran. */
      failed_step: z.number().int().min(0).nullable(),
      reason: z.string(),
      duration_ms: z.number().int().min(0),
    }),
  ),
  /** On Desk's stream: wake Desk once on the thread's next message to another thread (spec 2026-10-03 §3). */
  event('watch.set', z.object({ watch_id: z.string(), thread_id: z.string(), match: z.string().optional() })),
  event('watch.ended', z.object({ watch_id: z.string(), reason: z.enum(['fired', 'cancelled', 'thread_finished']), message_id: z.number().int().optional() })),
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
  /** The user turned one of Desk's built-in skills off or on (global; project_id is GLOBAL_PROJECT_ID). */
  event('skill.builtin_toggled', z.object({ name: SkillName, enabled: z.boolean() })),
  event('plan.updated', z.object({ items: z.array(PlanItem) })),
  /** Desk's short account of the project for the user: what is happening now, what is next, what waits on them. */
  event('whats_up.updated', z.object({ text: z.string().min(1) })),
  event(
    'report',
    z.object({ headline: z.string().min(1), progress: z.string(), needs_you: z.array(z.string()), results: z.array(z.string()) }),
  ),
  event('question.asked', z.object({ question: z.string().min(1), options: z.array(z.string()).optional() })),
  event(
    'message.user',
    z.object({
      text: z.string().min(1),
      /** The user's Ask to a thread: an idle, done or failed thread answers it without reopening (design spec §4.8). */
      question: z.literal(true).optional(),
    }),
  ),
  event(
    'message.agent',
    z.object({
      from_agent_id: z.string(),
      from_label: z.string(),
      kind: AgentMessageKind,
      text: z.string().min(1),
      /** The question this message answers (kind `answer`). */
      reply_to: z.number().int().optional(),
      /** An answer the runtime wrote for the question's recipient (a closure), not the recipient itself. */
      auto: z.literal(true).optional(),
      /** A question sent through the send path: only these have a state (open, answered, closed, withdrawn). */
      tracked: z.literal(true).optional(),
      /** The tool call that sent it; absent on runtime notices and closures. */
      tool_call_id: z.string().optional(),
    }),
  ),
  event('inbox.drained', z.object({ run_id: z.string(), up_to: z.number().int() })),
  event(
    'run.started',
    z.object({
      run_id: z.string(),
      model: z.string(),
      reasoning_effort: ReasoningEffort.optional(),
      /** Set on an answer run: the question it answers (a `message.agent` question, or the user's Ask). */
      answering: z.number().int().optional(),
    }),
  ),
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
      origin: z.string().regex(/^(user|agent:.+|automation:.+)$/),
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
  /** A service that is not running was taken off the project's list (its log goes too). */
  event('service.removed', z.object({ service_id: z.string(), by: ServiceActor })),
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
  // ── automations (spec 2026-09-26-automations-design §2.3) ──
  event(
    'automation.saved',
    z.object({
      automation_id: z.string(),
      name: AutomationName,
      version: z.number().int().min(1),
      definition: AutomationDefinition,
      origin: z.string().regex(/^(user|agent:.+)$/),
      change_note: z.string().max(2000),
      via: SaveVia,
    }),
  ),
  event('automation.layout_saved', z.object({ automation_id: z.string(), layout: AutomationLayout })),
  event('automation.deleted', z.object({ automation_id: z.string(), origin: z.string().regex(/^(user|agent:.+)$/) })),
  /** User only; `system` when the project is archived. */
  event('automation.switched', z.object({ automation_id: z.string(), enabled: z.boolean(), by: z.enum(['user', 'system']) })),
  /** User only. `remembered` keeps a suspension as it is; the other reasons end it. */
  event(
    'automation.grants_set',
    z.object({
      automation_id: z.string(),
      grants: z.array(Grant).max(100),
      reason: z.enum(['edited', 'remembered', 'kept', 'enabled']),
      /** The run and step whose approval a `remembered` grant came from. */
      source: z.object({ run_id: z.string(), step_id: z.string() }).optional(),
    }),
  ),
  event('automation.enable_requested', z.object({ automation_id: z.string(), note: z.string().max(2000), proposed_grants: z.array(Grant).max(100) })),
  event(
    'automation.run_started',
    z.object({
      run_id: z.string(),
      automation_id: z.string(),
      version: z.number().int().min(1),
      trigger: RunTrigger,
      test: z.boolean(),
      inputs: z.record(z.string(), InputValue),
      by: z.string().regex(/^(user|schedule|agent:.+)$/),
      parent: z.object({ run_id: z.string(), step_id: z.string() }).optional(),
      trigger_index: z.number().int().min(0).optional(),
      due_at: z.string().datetime().optional(),
      caught_up: z.number().int().min(0).optional(),
      deadline_at: z.string().datetime(),
    }),
  ),
  event(
    'automation.step_changed',
    z.object({
      run_id: z.string(),
      step_id: z.string(),
      attempt: z.number().int().min(1),
      status: StepStatus,
      route: z.string().nullable().optional(),
      outputs: Outputs.optional(),
      summary: z.string().max(4000).optional(),
      error: z.string().max(8000).optional(),
      agent_id: z.string().optional(),
      child_run_id: z.string().optional(),
      resume_at: z.string().datetime().nullable().optional(),
      gate: StepGate.nullable().optional(),
      question: StepQuestion.nullable().optional(),
      answered_by: z.literal('user').optional(),
      note: z.string().max(2000).optional(),
    }),
  ),
  event(
    'automation.run_finished',
    z.object({ run_id: z.string(), status: z.enum(['succeeded', 'failed', 'cancelled']), summary: z.string().max(4000), reason: z.string().max(4000).optional() }),
  ),
  event(
    'automation.trigger_skipped',
    z.object({ automation_id: z.string(), trigger_index: z.number().int().min(0), due_at: z.string().datetime(), reason: z.enum(['still_running', 'missed']) }),
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
