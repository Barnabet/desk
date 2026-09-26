import { z } from 'zod';
import { ReasoningEffort, SkillName } from './domain';

// Automations (spec 2026-09-26-automations-design §2): versioned graphs of steps, run by deskd on schedules or on demand.

/** An automation's name: fixed at creation, unique among the project's automations. */
export const AutomationName = z
  .string()
  .regex(/^[a-z0-9][a-z0-9-]{0,63}$/, 'Use lowercase letters, digits and dashes, starting with a letter or digit (at most 64 characters)');
export type AutomationName = z.infer<typeof AutomationName>;

export const StepId = z.string().regex(/^[a-z][a-z0-9_-]{0,39}$/, 'Use lowercase letters, digits, _ and -, starting with a letter (at most 40 characters)');
export type StepId = z.infer<typeof StepId>;

/** Input keys and output keys. */
export const InputKey = z.string().regex(/^[a-z][a-z0-9_]{0,39}$/, 'Use lowercase letters, digits and _, starting with a letter (at most 40 characters)');
export type InputKey = z.infer<typeof InputKey>;

export const RouteName = z.string().regex(/^[a-z][a-z0-9_-]{0,39}$/, 'Use lowercase letters, digits, _ and -, starting with a letter (at most 40 characters)');

/** Routes the runtime sets: `error` (a failure with on_error continue) and `rejected` (an Ask me step the user rejected). */
export const RESERVED_ROUTES = ['error', 'rejected'] as const;
const declaredRoute = RouteName.refine((r) => !(RESERVED_ROUTES as readonly string[]).includes(r), 'error and rejected are set by the runtime; do not declare them');

export const OUTPUTS_MAX_BYTES = 16_384;
export const OUTPUT_STRING_MAX = 4000;
export const OUTPUT_LIST_MAX = 200;
export const SUMMARY_MAX = 2000;
/** Schedules fire at most this often (checked on their next 10 times). */
export const MIN_SCHEDULE_GAP_MS = 5 * 60_000;
export const MAX_WAIT_MINUTES = 7 * 24 * 60;
export const MAX_SUB_DEPTH = 3;
/** Backoff before retry attempt 2, 3 and 4 of a step with `on_error: {retry}`. */
export const RETRY_BACKOFF_MS = [30_000, 120_000, 480_000] as const;
export const RUN_REPORT_MAX = 12_000;
export const ENGINE_TICK_MS = 30_000;

export const InputType = z.enum(['text', 'long_text', 'url', 'file', 'folder', 'number', 'boolean', 'choice']);
export type InputType = z.infer<typeof InputType>;
export const InputValue = z.union([z.string().max(10_000), z.number(), z.boolean()]);
export type InputValue = z.infer<typeof InputValue>;

export const InputSpec = z.object({
  key: InputKey,
  label: z.string().trim().min(1).max(80),
  type: InputType,
  required: z.boolean().default(false),
  default: InputValue.optional(),
  /** The choices of a `choice` input. */
  options: z.array(z.string().min(1).max(200)).min(1).max(50).optional(),
  description: z.string().max(500).optional(),
});
export type InputSpec = z.infer<typeof InputSpec>;

export const ScheduleTrigger = z.object({
  kind: z.literal('schedule'),
  /** Five fields: minute hour day-of-month month day-of-week. */
  cron: z.string().trim().min(1).max(100),
  /** IANA timezone, e.g. Europe/Paris. */
  timezone: z.string().min(1).max(64),
  /** After the Mac slept through due times: run once for the latest, or skip them. */
  catch_up: z.enum(['once', 'skip']).default('once'),
  inputs: z.record(InputKey, InputValue).optional(),
});
export type ScheduleTrigger = z.infer<typeof ScheduleTrigger>;
/** How an automation starts besides Run now (always available). Only schedules today. */
export const AutomationTrigger = ScheduleTrigger;
export type AutomationTrigger = z.infer<typeof AutomationTrigger>;

export const OnError = z.union([z.enum(['stop', 'continue']), z.object({ retry: z.number().int().min(1).max(3) })]);
export type OnError = z.infer<typeof OnError>;

const stepBase = {
  id: StepId,
  title: z.string().trim().min(1).max(80),
  /** all: wait until every incoming edge is decided, run if one fired. any: run on the first edge that fires. */
  join: z.enum(['all', 'any']).default('all'),
  on_error: OnError.default('stop'),
  /** Active minutes before the step fails (default: script 10, agent 60). */
  timeout_min: z.number().int().min(1).max(1440).optional(),
  /** Named outcomes the step may choose. */
  routes: z.array(declaredRoute).max(10).default([]),
  /** Globs relative to the step folder, copied to the library after success. */
  publish: z.array(z.string().min(1).max(200)).max(20).default([]),
};

export const ScriptStep = z.object({
  ...stepBase,
  kind: z.literal('script'),
  skill: SkillName,
  /** A file of the skill, e.g. scripts/fetch.py or fetch.py. */
  script: z.string().min(1).max(200),
  /** Arguments, each rendered on its own ({{…}} templates); never parsed by a shell. */
  args: z.array(z.string().max(4000)).max(50).default([]),
  stdin: z.string().max(20_000).optional(),
  /** May be re-run after a crash left its outcome unknown. */
  idempotent: z.boolean().default(false),
});

export const AgentStep = z.object({
  ...stepBase,
  kind: z.literal('agent'),
  brief: z.string().min(1).max(20_000),
  skills: z.array(SkillName).max(12).default([]),
  /** The outputs downstream templates and conditions may use. */
  output_keys: z.array(z.object({ key: InputKey, description: z.string().trim().min(1).max(300) })).max(20).default([]),
  model: z.string().min(1).optional(),
  reasoning_effort: ReasoningEffort.optional(),
  /** Work in a git worktree of this source (branch desk/auto-<name>-<id>). */
  git_source_id: z.string().min(1).optional(),
  max_steps: z.number().int().min(1).max(400).optional(),
});

export const AskStep = z.object({
  ...stepBase,
  kind: z.literal('ask'),
  question: z.string().min(1).max(4000),
  /** Globs of files in upstream step folders to show with the question. */
  show: z.array(z.string().min(1).max(200)).max(20).default([]),
  approve_label: z.string().trim().min(1).max(40).optional(),
  reject_label: z.string().trim().min(1).max(40).optional(),
  expires_after_hours: z.number().int().min(1).max(720).optional(),
});

export const SubAutomationStep = z.object({
  ...stepBase,
  kind: z.literal('automation'),
  automation: AutomationName,
  inputs: z.record(InputKey, z.string().max(10_000)).default({}),
});

export const WaitStep = z
  .object({
    ...stepBase,
    kind: z.literal('wait'),
    minutes: z.number().int().min(1).max(MAX_WAIT_MINUTES).optional(),
    /** HH:MM in the automation's timezone. */
    until: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, 'Use HH:MM (24 hours)').optional(),
  })
  .refine((w) => (w.minutes === undefined) !== (w.until === undefined), 'Set exactly one of minutes and until');

export const TellDeskStep = z.object({
  ...stepBase,
  kind: z.literal('tell_desk'),
  text: z.string().min(1).max(4000),
  /** Globs of files in upstream step folders whose paths go with the message. */
  attach: z.array(z.string().min(1).max(200)).max(20).default([]),
});

export const Step = z.discriminatedUnion('kind', [ScriptStep, AgentStep, AskStep, SubAutomationStep, WaitStep, TellDeskStep]);
export type Step = z.infer<typeof Step>;
export type StepKind = Step['kind'];
export type ScriptStep = z.infer<typeof ScriptStep>;
export type AgentStep = z.infer<typeof AgentStep>;
export type AskStep = z.infer<typeof AskStep>;
export type SubAutomationStep = z.infer<typeof SubAutomationStep>;
export type WaitStep = z.infer<typeof WaitStep>;
export type TellDeskStep = z.infer<typeof TellDeskStep>;

export const AutomationEdge = z.object({
  from: StepId,
  to: StepId,
  /** Fires only when the source took this route (error and rejected included); without it, when the source succeeded. */
  route: RouteName.optional(),
  /** A condition (spec §3.2), e.g. `steps.fetch.outputs.count > 0`. */
  when: z.string().trim().min(1).max(500).optional(),
});
export type AutomationEdge = z.infer<typeof AutomationEdge>;

export const AutomationLimits = z.object({
  run_deadline_hours: z.number().int().min(1).max(168).default(24),
  max_parallel_agents: z.number().int().min(1).max(4).default(2),
  max_parallel_scripts: z.number().int().min(1).max(8).default(4),
});
export type AutomationLimits = z.infer<typeof AutomationLimits>;

export const AfterRun = z.enum(['silent', 'notify', 'desk_review']);
export type AfterRun = z.infer<typeof AfterRun>;

export const AutomationDefinition = z.object({
  title: z.string().trim().min(1).max(120),
  description: z.string().max(2000).default(''),
  inputs: z.array(InputSpec).max(20).default([]),
  triggers: z.array(AutomationTrigger).max(10).default([]),
  steps: z.array(Step).max(50),
  edges: z.array(AutomationEdge).max(200).default([]),
  /** Its outputs and folder are this automation's result when it runs as a sub-automation. */
  output_step: StepId.optional(),
  after_run: AfterRun.default('notify'),
  // zod 4: .prefault runs the inner defaults (.default({}) would return {} as is).
  limits: AutomationLimits.prefault({}),
});
export type AutomationDefinition = z.infer<typeof AutomationDefinition>;

/** How a save happened (recorded on its version). */
export const SaveVia = z.enum(['editor', 'api', 'cli', 'import', 'restore', 'tool']);
export type SaveVia = z.infer<typeof SaveVia>;

/** A user-set rule for one automation's runs, checked before the project policy. */
export const Grant = z.object({
  tool: z.string().min(1).max(60),
  match: z
    .object({ branch: z.string().min(1).max(200).optional(), command: z.string().min(1).max(1000).optional(), domain: z.string().min(1).max(253).optional() })
    .optional(),
  action: z.enum(['allow', 'deny']),
});
export type Grant = z.infer<typeof Grant>;

/** Editor positions by step id, plus `start` for the Start pill. Not versioned. */
export const AutomationLayout = z.record(z.string().max(40), z.object({ x: z.number(), y: z.number() }));
export type AutomationLayout = z.infer<typeof AutomationLayout>;

const scalar = z.union([z.string().max(OUTPUT_STRING_MAX), z.number(), z.boolean()]);
export const OutputValue = z.union([scalar, z.null(), z.array(scalar).max(OUTPUT_LIST_MAX)]);
export type OutputValue = z.infer<typeof OutputValue>;

/** UTF-8 bytes of an outputs object's JSON. */
export function outputsSize(o: Record<string, unknown>): number {
  return new TextEncoder().encode(JSON.stringify(o)).length;
}

export const Outputs = z.record(InputKey, OutputValue).refine((o) => outputsSize(o) <= OUTPUTS_MAX_BYTES, `Outputs must stay under ${OUTPUTS_MAX_BYTES} bytes`);
export type Outputs = z.infer<typeof Outputs>;

/** What a script writes to $DESK_OUTPUT. */
export const StepOutputFile = z.object({ route: z.string().optional(), summary: z.string().max(SUMMARY_MAX).optional(), outputs: Outputs.optional() });
export type StepOutputFile = z.infer<typeof StepOutputFile>;

export const RunStatus = z.enum(['running', 'waiting', 'succeeded', 'failed', 'cancelled']);
export type RunStatus = z.infer<typeof RunStatus>;
export const StepStatus = z.enum(['pending', 'running', 'waiting', 'succeeded', 'failed', 'rejected', 'skipped', 'cancelled']);
export type StepStatus = z.infer<typeof StepStatus>;
export const RunTrigger = z.enum(['schedule', 'manual', 'desk', 'parent', 'test']);
export type RunTrigger = z.infer<typeof RunTrigger>;

/** A script step's call that policy says to ask about. */
export const StepGate = z.object({ tool: z.string(), subject: z.string(), reason: z.string() });
export type StepGate = z.infer<typeof StepGate>;
/** An Ask me step's rendered question and the files shown with it (absolute paths in the run folder). */
export const StepQuestion = z.object({ text: z.string(), files: z.array(z.string()), approve_label: z.string().optional(), reject_label: z.string().optional() });
export type StepQuestion = z.infer<typeof StepQuestion>;

/** A problem in a definition; `path` points into it, e.g. `steps[2].brief`. */
export const ValidationIssue = z.object({ path: z.string(), message: z.string() });
export type ValidationIssue = z.infer<typeof ValidationIssue>;

export const AutomationExport = z.object({ format: z.literal('desk-automation/1'), name: AutomationName, definition: AutomationDefinition });
export type AutomationExport = z.infer<typeof AutomationExport>;

// ── API views (daemon → clients) ─────────────────────────────────────

export type RunSummary = {
  id: string;
  status: RunStatus;
  trigger: RunTrigger;
  test: boolean;
  started_at: string;
  finished_at: string | null;
  summary: string | null;
  /** What a waiting run waits on, e.g. `Ask me: Publish this week's?`. */
  waiting_on: string | null;
};

export type AutomationSummary = {
  id: string;
  project_id: string;
  name: string;
  title: string;
  description: string;
  version: number;
  /** The latest version with a succeeded test run. */
  tested_version: number | null;
  enabled: boolean;
  grants_suspended: boolean;
  schedules: { cron: string; timezone: string }[];
  last_run: RunSummary | null;
  /** The next schedule time while on, else null. */
  next_due: string | null;
  enable_requested: boolean;
  updated_at: string;
};

export type AutomationDetail = AutomationSummary & {
  definition: AutomationDefinition;
  layout: AutomationLayout;
  grants: Grant[];
  proposed_grants: Grant[];
  /** The version current when grants were last set (the Grants tab diffs from it). */
  grants_set_version: number | null;
  enable_request: { note: string; proposed_grants: Grant[]; at: string } | null;
};

export type AutomationVersionInfo = { version: number; origin: string; change_note: string; via: string; created_at: string; tested: boolean };

export type RunInfo = {
  id: string;
  automation_id: string;
  project_id: string;
  version: number;
  trigger: RunTrigger;
  test: boolean;
  inputs: Record<string, InputValue>;
  by: string;
  parent_run_id: string | null;
  parent_step_id: string | null;
  due_at: string | null;
  caught_up: number;
  status: RunStatus;
  summary: string | null;
  reason: string | null;
  started_at: string;
  finished_at: string | null;
  deadline_at: string;
};

export type StepRunInfo = {
  step_id: string;
  attempt: number;
  status: StepStatus;
  route: string | null;
  outputs: Outputs;
  summary: string | null;
  error: string | null;
  agent_id: string | null;
  child_run_id: string | null;
  resume_at: string | null;
  gate: StepGate | null;
  question: StepQuestion | null;
  note: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type RunDetail = RunInfo & { automation_name: string; automation_title: string; definition: AutomationDefinition; steps: StepRunInfo[] };

export type RunListEntry =
  | { kind: 'run'; run: RunInfo }
  | { kind: 'skipped'; automation_id: string; trigger_index: number; due_at: string; reason: 'still_running' | 'missed'; ts: string };
