import { index, integer, primaryKey, sqliteTable, text, uniqueIndex } from 'drizzle-orm/sqlite-core';
// Type-only import: erased at runtime, so drizzle-kit can still load this file standalone.
import type { AutomationDefinition, AutomationLayout, CheckWhere, Grant, InputValue, Outputs, PlanItem, ProjectSettings, ReasoningEffort, RequirementCheck, StepGate, StepQuestion } from '@desk/protocol';

export const events = sqliteTable(
  'events',
  {
    id: integer('id').primaryKey({ autoIncrement: true }),
    project_id: text('project_id').notNull(),
    agent_id: text('agent_id'),
    type: text('type').notNull(),
    payload: text('payload', { mode: 'json' }).notNull(),
    ts: text('ts').notNull(),
  },
  (t) => [
    index('events_project_idx').on(t.project_id, t.id),
    index('events_agent_idx').on(t.agent_id, t.id),
    // The message fold reads a project's events of a few types after an id (design spec §1.4).
    index('events_project_type_idx').on(t.project_id, t.type, t.id),
  ],
);

export const projects = sqliteTable('projects', {
  id: text('id').primaryKey(),
  name: text('name').notNull(),
  goal: text('goal').notNull(),
  instructions: text('instructions').notNull(),
  settings: text('settings', { mode: 'json' }).$type<ProjectSettings>().notNull(),
  created_at: text('created_at').notNull(),
  updated_at: text('updated_at').notNull(),
  archived_at: text('archived_at'),
});

export const agents = sqliteTable(
  'agents',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    role: text('role', { enum: ['desk', 'thread', 'step'] }).notNull(),
    status: text('status', { enum: ['idle', 'queued', 'running', 'waiting', 'done', 'failed', 'cancelled'] }).notNull(),
    model: text('model').notNull(),
    /** A reasoning level chosen for this agent (threads, at spawn); null follows the project setting. */
    reasoning_effort: text('reasoning_effort').$type<ReasoningEffort>(),
    title: text('title'),
    brief: text('brief'),
    workspace_path: text('workspace_path'),
    parent_id: text('parent_id'),
    inbox_cursor: integer('inbox_cursor').notNull().default(0),
    review_round: integer('review_round').notNull().default(0),
    result_summary: text('result_summary'),
    result_artifacts: text('result_artifacts', { mode: 'json' }).$type<string[]>(),
    active_skills: text('active_skills', { mode: 'json' }).$type<string[]>().notNull().default([]),
    git_source_id: text('git_source_id'),
    git_branch: text('git_branch'),
    git_base: text('git_base'),
    git_common_dir: text('git_common_dir'),
    /** Where its latest submission stands (reviews and acceptance spec §0). */
    acceptance: text('acceptance', { enum: ['none', 'in_review', 'reviewed', 'changes_requested', 'accepted', 'accepted_with_limitations'] })
      .notNull()
      .default('none'),
    accepted_submission_id: text('accepted_submission_id'),
    /** A reviewer thread: the submission it reviews (its latest review's). */
    reviews_submission_id: text('reviews_submission_id'),
    /** A step agent's automation run and step (role `step`). */
    automation_run_id: text('automation_run_id'),
    automation_step_id: text('automation_step_id'),
    archived_at: text('archived_at'),
    created_at: text('created_at').notNull(),
    updated_at: text('updated_at').notNull(),
  },
  (t) => [index('agents_project_idx').on(t.project_id)],
);

export const usageTotals = sqliteTable(
  'usage_totals',
  {
    project_id: text('project_id').notNull(),
    agent_id: text('agent_id').notNull(),
    model: text('model').notNull(),
    day: text('day').notNull(),
    prompt_tokens: integer('prompt_tokens').notNull(),
    completion_tokens: integer('completion_tokens').notNull(),
  },
  (t) => [primaryKey({ columns: [t.project_id, t.agent_id, t.model, t.day] })],
);

export const approvals = sqliteTable(
  'approvals',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    agent_id: text('agent_id').notNull(),
    run_id: text('run_id').notNull(),
    tool_call_id: text('tool_call_id').notNull(),
    tool: text('tool').notNull(),
    arguments: text('arguments').notNull(),
    reason: text('reason').notNull(),
    delegate_to_desk: integer('delegate_to_desk', { mode: 'boolean' }).notNull(),
    status: text('status', { enum: ['pending', 'approved', 'denied'] }).notNull(),
    resolved_by: text('resolved_by', { enum: ['user', 'desk', 'system'] }),
    note: text('note'),
    created_at: text('created_at').notNull(),
    resolved_at: text('resolved_at'),
  },
  (t) => [index('approvals_project_idx').on(t.project_id, t.status), index('approvals_agent_idx').on(t.agent_id, t.status)],
);

export const sources = sqliteTable(
  'sources',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    path: text('path').notNull(),
    kind: text('kind', { enum: ['folder', 'git'] }).notNull(),
    label: text('label').notNull(),
    /** Desk and its threads may write here (sandboxed) and run services here. */
    agent_write: integer('agent_write', { mode: 'boolean' }).notNull().default(true),
    created_at: text('created_at').notNull(),
  },
  (t) => [index('sources_project_idx').on(t.project_id)],
);

export const memory = sqliteTable(
  'memory',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    kind: text('kind', { enum: ['fact', 'decision', 'preference', 'contact', 'note'] }).notNull(),
    content: text('content').notNull(),
    source: text('source').notNull(),
    supersedes: text('supersedes'),
    superseded_by: text('superseded_by'),
    created_at: text('created_at').notNull(),
  },
  (t) => [index('memory_project_idx').on(t.project_id, t.superseded_by)],
);

export const artifacts = sqliteTable(
  'artifacts',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    path: text('path').notNull(),
    title: text('title').notNull(),
    kind: text('kind', { enum: ['file', 'report', 'code', 'data', 'other'] }).notNull(),
    origin: text('origin').notNull(),
    description: text('description').notNull(),
    created_at: text('created_at').notNull(),
  },
  (t) => [index('artifacts_project_idx').on(t.project_id)],
);

export const plans = sqliteTable('plans', {
  project_id: text('project_id').primaryKey(),
  items: text('items', { mode: 'json' }).$type<PlanItem[]>().notNull(),
  updated_at: text('updated_at').notNull(),
});

/** Project services: long-lived processes in thread workspaces (one row per name, updated per run). */
export const services = sqliteTable(
  'services',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    name: text('name').notNull(),
    command: text('command').notNull(),
    cwd: text('cwd').notNull(),
    /** The thread whose workspace the service runs in. */
    agent_id: text('agent_id').notNull(),
    status: text('status', { enum: ['running', 'exited', 'stopped'] }).notNull(),
    /** Set when the service runs in a project source folder instead of a thread workspace (`agent_id` then = who started it). */
    source_id: text('source_id'),
    pid: integer('pid'),
    exit_code: integer('exit_code'),
    exit_signal: text('exit_signal'),
    stop_reason: text('stop_reason', { enum: ['requested', 'restart', 'thread_archived', 'project_archived', 'daemon_shutdown', 'daemon_restart'] }),
    url: text('url'),
    started_by: text('started_by').notNull(),
    started_at: text('started_at').notNull(),
    ended_at: text('ended_at'),
  },
  (t) => [uniqueIndex('services_project_name_idx').on(t.project_id, t.name)],
);

/** Attention items the user dismissed (report needs_you, stalled and failed threads). */
export const attentionDismissals = sqliteTable('attention_dismissals', {
  item_id: text('item_id').primaryKey(),
  project_id: text('project_id').notNull(),
  dismissed_at: text('dismissed_at').notNull(),
});

/** The user's switch for each of Desk's built-in skills; no row means on. Projected from `skill.builtin_toggled`. */
export const builtinSkillSettings = sqliteTable('builtin_skill_settings', {
  name: text('name').primaryKey(),
  enabled: integer('enabled', { mode: 'boolean' }).notNull(),
  updated_at: text('updated_at').notNull(),
});

/** Automations (spec 2026-09-26-automations-design §2): the current definition, the user's switch and grants. */
export const automations = sqliteTable(
  'automations',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    name: text('name').notNull(),
    title: text('title').notNull(),
    description: text('description').notNull(),
    version: integer('version').notNull(),
    definition: text('definition', { mode: 'json' }).$type<AutomationDefinition>().notNull(),
    layout: text('layout', { mode: 'json' }).$type<AutomationLayout>().notNull().default({}),
    enabled: integer('enabled', { mode: 'boolean' }).notNull().default(false),
    grants: text('grants', { mode: 'json' }).$type<Grant[]>().notNull().default([]),
    grants_suspended: integer('grants_suspended', { mode: 'boolean' }).notNull().default(false),
    /** The version current when grants were last set. */
    grants_set_version: integer('grants_set_version'),
    enable_request: text('enable_request', { mode: 'json' }).$type<{ note: string; proposed_grants: Grant[]; at: string } | null>(),
    /** Per schedule (trigger index): the due time it last fired or skipped, or when it was turned on. */
    last_due: text('last_due', { mode: 'json' }).$type<Record<string, string>>().notNull().default({}),
    deleted_at: text('deleted_at'),
    created_at: text('created_at').notNull(),
    updated_at: text('updated_at').notNull(),
  },
  (t) => [index('automations_project_idx').on(t.project_id, t.name)],
);

export const automationVersions = sqliteTable(
  'automation_versions',
  {
    automation_id: text('automation_id').notNull(),
    version: integer('version').notNull(),
    definition: text('definition', { mode: 'json' }).$type<AutomationDefinition>().notNull(),
    origin: text('origin').notNull(),
    change_note: text('change_note').notNull(),
    via: text('via').notNull(),
    created_at: text('created_at').notNull(),
  },
  (t) => [primaryKey({ columns: [t.automation_id, t.version] })],
);

export const automationRuns = sqliteTable(
  'automation_runs',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    automation_id: text('automation_id').notNull(),
    version: integer('version').notNull(),
    trigger: text('trigger', { enum: ['schedule', 'manual', 'desk', 'parent', 'test'] }).notNull(),
    test: integer('test', { mode: 'boolean' }).notNull(),
    inputs: text('inputs', { mode: 'json' }).$type<Record<string, InputValue>>().notNull(),
    by: text('by').notNull(),
    parent_run_id: text('parent_run_id'),
    parent_step_id: text('parent_step_id'),
    trigger_index: integer('trigger_index'),
    due_at: text('due_at'),
    caught_up: integer('caught_up').notNull().default(0),
    /** Stored statuses; `waiting` is derived on read (automations/views.ts). */
    status: text('status', { enum: ['running', 'succeeded', 'failed', 'cancelled'] }).notNull(),
    summary: text('summary'),
    reason: text('reason'),
    started_at: text('started_at').notNull(),
    finished_at: text('finished_at'),
    deadline_at: text('deadline_at').notNull(),
  },
  (t) => [
    index('automation_runs_automation_idx').on(t.automation_id, t.started_at),
    index('automation_runs_status_idx').on(t.status),
    index('automation_runs_parent_idx').on(t.parent_run_id),
  ],
);

export const automationStepRuns = sqliteTable(
  'automation_step_runs',
  {
    run_id: text('run_id').notNull(),
    step_id: text('step_id').notNull(),
    attempt: integer('attempt').notNull(),
    status: text('status', { enum: ['pending', 'running', 'waiting', 'succeeded', 'failed', 'rejected', 'skipped', 'cancelled'] }).notNull(),
    route: text('route'),
    outputs: text('outputs', { mode: 'json' }).$type<Outputs>().notNull().default({}),
    summary: text('summary'),
    error: text('error'),
    agent_id: text('agent_id'),
    child_run_id: text('child_run_id'),
    resume_at: text('resume_at'),
    gate: text('gate', { mode: 'json' }).$type<StepGate | null>(),
    question: text('question', { mode: 'json' }).$type<StepQuestion | null>(),
    note: text('note'),
    answered_by: text('answered_by'),
    started_at: text('started_at'),
    finished_at: text('finished_at'),
    updated_at: text('updated_at').notNull(),
  },
  (t) => [primaryKey({ columns: [t.run_id, t.step_id] }), index('automation_step_runs_status_idx').on(t.status)],
);

/** What a thread's `complete` submitted (reviews and acceptance spec §1.2). */
export const submissions = sqliteTable(
  'submissions',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    thread_id: text('thread_id').notNull(),
    seq: integer('seq').notNull(),
    commit: text('commit'),
    base: text('base'),
    artifacts: text('artifacts', { mode: 'json' }).$type<Array<{ path: string; sha256: string }>>().notNull(),
    claims: text('claims', { mode: 'json' }).$type<string[]>().notNull(),
    limitations: text('limitations', { mode: 'json' }).$type<string[]>().notNull(),
    evidence: text('evidence').notNull(),
    superseded_by: text('superseded_by'),
    created_at: text('created_at').notNull(),
  },
  (t) => [index('submissions_thread_idx').on(t.thread_id, t.seq)],
);

export const reviews = sqliteTable(
  'reviews',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    submission_id: text('submission_id').notNull(),
    builder_id: text('builder_id').notNull(),
    reviewer_id: text('reviewer_id').notNull(),
    criteria: text('criteria', { mode: 'json' }).$type<string[]>().notNull(),
    focus: text('focus'),
    requested_by: text('requested_by', { enum: ['desk', 'user'] }).notNull(),
    /** The reviewer has the builder's report (after its initial assessment, or from the start on a re-review). */
    revealed: integer('revealed', { mode: 'boolean' }).notNull(),
    /** null until an assessment; `initial`, then `final`. */
    phase: text('phase', { enum: ['initial', 'final'] }),
    verdict: text('verdict', { enum: ['no_material_issues', 'issues_found', 'could_not_review'] }),
    requirements: text('requirements', { mode: 'json' }).$type<RequirementCheck[]>().notNull(),
    not_checked: text('not_checked', { mode: 'json' }).$type<string[]>().notNull(),
    created_at: text('created_at').notNull(),
    updated_at: text('updated_at').notNull(),
  },
  (t) => [index('reviews_submission_idx').on(t.submission_id), index('reviews_reviewer_idx').on(t.reviewer_id)],
);

export const findings = sqliteTable(
  'findings',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    review_id: text('review_id').notNull(),
    submission_id: text('submission_id').notNull(),
    builder_id: text('builder_id').notNull(),
    title: text('title').notNull(),
    detail: text('detail').notNull(),
    blocking: integer('blocking', { mode: 'boolean' }).notNull(),
    reproducer: text('reproducer').notNull(),
    state: text('state', { enum: ['open', 'fixed', 'waived', 'withdrawn'] }).notNull(),
    /** `fixed`: the submission that fixed it. */
    fixed_in: text('fixed_in'),
    reason: text('reason'),
    resolved_by: text('resolved_by', { enum: ['desk', 'user', 'reviewer'] }),
    created_at: text('created_at').notNull(),
    resolved_at: text('resolved_at'),
  },
  (t) => [index('findings_builder_idx').on(t.builder_id, t.state)],
);

/** Shell commands the platform ran and saw end (spec 2026-10-03 §1). */
export const receipts = sqliteTable(
  'receipts',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    agent_id: text('agent_id').notNull(),
    tool: text('tool').notNull(),
    tool_call_id: text('tool_call_id'),
    check_id: text('check_id'),
    step: integer('step'),
    command: text('command').notNull(),
    cwd: text('cwd').notNull(),
    head: text('head'),
    dirty: integer('dirty', { mode: 'boolean' }),
    exit_code: integer('exit_code'),
    outcome: text('outcome', { enum: ['exit', 'timeout', 'aborted', 'killed'] }).notNull(),
    duration_ms: integer('duration_ms').notNull(),
    output_bytes: integer('output_bytes').notNull(),
    output_sha256: text('output_sha256').notNull(),
    started_at: text('started_at').notNull(),
    finished_at: text('finished_at').notNull(),
  },
  (t) => [index('receipts_agent_idx').on(t.agent_id, t.finished_at), index('receipts_head_idx').on(t.project_id, t.head), index('receipts_check_idx').on(t.check_id)],
);

/** Desk's check jobs (spec 2026-10-03 §2). */
export const checks = sqliteTable(
  'checks',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    agent_id: text('agent_id').notNull(),
    title: text('title').notNull(),
    steps: text('steps', { mode: 'json' }).$type<string[]>().notNull(),
    where: text('where', { mode: 'json' }).$type<CheckWhere>().notNull(),
    expect: text('expect', { mode: 'json' }).$type<string[]>().notNull(),
    timeout_s: integer('timeout_s').notNull(),
    cwd: text('cwd').notNull(),
    head: text('head'),
    status: text('status', { enum: ['running', 'passed', 'failed', 'timed_out', 'cancelled', 'interrupted'] }).notNull(),
    failed_step: integer('failed_step'),
    reason: text('reason'),
    duration_ms: integer('duration_ms'),
    started_at: text('started_at').notNull(),
    finished_at: text('finished_at'),
  },
  (t) => [index('checks_project_idx').on(t.project_id, t.status)],
);

/** Desk's one-shot watches on a thread's messages to other threads (spec 2026-10-03 §3). */
export const watches = sqliteTable(
  'watches',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    desk_id: text('desk_id').notNull(),
    thread_id: text('thread_id').notNull(),
    match: text('match'),
    state: text('state', { enum: ['open', 'fired', 'cancelled', 'thread_finished'] }).notNull(),
    message_id: integer('message_id'),
    created_at: text('created_at').notNull(),
    ended_at: text('ended_at'),
  },
  (t) => [index('watches_open_idx').on(t.project_id, t.state)],
);
