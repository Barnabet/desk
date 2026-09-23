import { and, asc, desc, eq, inArray, isNull } from 'drizzle-orm';
import type { EventType, StoredEvent } from '@desk/protocol';
import type { Db } from '../db/open';
import { agents, approvals, events, projects, sources, usageTotals } from '../db/schema';

export type ProjectRow = typeof projects.$inferSelect;
export type AgentRow = typeof agents.$inferSelect;
export type UsageRow = typeof usageTotals.$inferSelect;
export type ApprovalRow = typeof approvals.$inferSelect;
export type SourceRow = typeof sources.$inferSelect;
export type ApprovalStatus = ApprovalRow['status'];

export const getProject = (db: Db, id: string): ProjectRow | undefined =>
  db.select().from(projects).where(eq(projects.id, id)).get();

export const getAgent = (db: Db, id: string): AgentRow | undefined => db.select().from(agents).where(eq(agents.id, id)).get();

export const listAgents = (db: Db, projectId: string): AgentRow[] =>
  db.select().from(agents).where(eq(agents.project_id, projectId)).all();

export const getUsageTotals = (db: Db, projectId: string): UsageRow[] =>
  db.select().from(usageTotals).where(eq(usageTotals.project_id, projectId)).all();

export const getApproval = (db: Db, id: string): ApprovalRow | undefined => db.select().from(approvals).where(eq(approvals.id, id)).get();

export const listApprovals = (db: Db, projectId: string, status?: ApprovalStatus): ApprovalRow[] =>
  db
    .select()
    .from(approvals)
    .where(and(eq(approvals.project_id, projectId), status ? eq(approvals.status, status) : undefined))
    .orderBy(asc(approvals.created_at), asc(approvals.id))
    .all();

export const pendingApprovalsFor = (db: Db, agentId: string): ApprovalRow[] =>
  db.select().from(approvals).where(and(eq(approvals.agent_id, agentId), eq(approvals.status, 'pending'))).all();

export const getDeskAgent = (db: Db, projectId: string): AgentRow | undefined =>
  db.select().from(agents).where(and(eq(agents.project_id, projectId), eq(agents.role, 'desk'))).get();

export const listThreads = (db: Db, projectId: string, status?: AgentRow['status']): AgentRow[] =>
  db
    .select()
    .from(agents)
    .where(and(eq(agents.project_id, projectId), eq(agents.role, 'thread'), status ? eq(agents.status, status) : undefined))
    .orderBy(asc(agents.created_at), asc(agents.id))
    .all();

export const listSources = (db: Db, projectId: string): SourceRow[] =>
  db.select().from(sources).where(eq(sources.project_id, projectId)).orderBy(asc(sources.created_at), asc(sources.id)).all();

export const getSource = (db: Db, id: string): SourceRow | undefined => db.select().from(sources).where(eq(sources.id, id)).get();

/** Threads that are running or waiting, across all projects. */
export const listActiveThreads = (db: Db): AgentRow[] =>
  db.select().from(agents).where(and(eq(agents.role, 'thread'), inArray(agents.status, ['running', 'waiting']))).all();

/** The agent's most recent event (optionally of one type). */
export function lastEvent(db: Db, agentId: string, type?: EventType): StoredEvent | undefined {
  const row = db
    .select()
    .from(events)
    .where(and(eq(events.agent_id, agentId), type ? eq(events.type, type) : undefined))
    .orderBy(desc(events.id))
    .limit(1)
    .get();
  return row ? ({ ...row } as StoredEvent) : undefined;
}

export const listProjects = (db: Db, opts: { includeArchived?: boolean } = {}): ProjectRow[] =>
  db
    .select()
    .from(projects)
    .where(opts.includeArchived ? undefined : isNull(projects.archived_at))
    .orderBy(asc(projects.created_at), asc(projects.id))
    .all();

/** Agents of non-archived projects, optionally filtered by status. */
export const listLiveAgents = (db: Db, statuses?: AgentRow['status'][]): AgentRow[] =>
  db
    .select({ agent: agents })
    .from(agents)
    .innerJoin(projects, eq(projects.id, agents.project_id))
    .where(and(isNull(projects.archived_at), statuses ? inArray(agents.status, statuses) : undefined))
    .orderBy(asc(agents.created_at), asc(agents.id))
    .all()
    .map((r) => r.agent);
