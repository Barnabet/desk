import { index, integer, primaryKey, sqliteTable, text } from 'drizzle-orm/sqlite-core';
// Type-only import: erased at runtime, so drizzle-kit can still load this file standalone.
import type { ProjectSettings } from '@desk/protocol';

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
});

export const agents = sqliteTable(
  'agents',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    role: text('role', { enum: ['desk', 'thread'] }).notNull(),
    status: text('status', { enum: ['idle', 'queued', 'running', 'waiting', 'done', 'failed', 'cancelled'] }).notNull(),
    model: text('model').notNull(),
    title: text('title'),
    brief: text('brief'),
    workspace_path: text('workspace_path'),
    parent_id: text('parent_id'),
    inbox_cursor: integer('inbox_cursor').notNull().default(0),
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
