import { index, integer, primaryKey, sqliteTable, text, uniqueIndex } from 'drizzle-orm/sqlite-core';
// Type-only import: erased at runtime, so drizzle-kit can still load this file standalone.
import type { PlanItem, ProjectSettings, ReasoningEffort } from '@desk/protocol';

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
  (t) => [index('events_project_idx').on(t.project_id, t.id), index('events_agent_idx').on(t.agent_id, t.id)],
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
    role: text('role', { enum: ['desk', 'thread'] }).notNull(),
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
