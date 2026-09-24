import { and, asc, desc, eq, gt, gte, inArray, isNull, max, sql } from 'drizzle-orm';
import { resolveSettings, type EventOf, type EventType, type StoredEvent } from '@desk/protocol';
import type { Db } from '../db/open';
import { agents, approvals, events, projects, services, sources, usageTotals } from '../db/schema';

export type ProjectRow = typeof projects.$inferSelect;
export type AgentRow = typeof agents.$inferSelect;
export type UsageRow = typeof usageTotals.$inferSelect;
export type ApprovalRow = typeof approvals.$inferSelect;
export type SourceRow = typeof sources.$inferSelect;
export type ApprovalStatus = ApprovalRow['status'];
export type ServiceRow = typeof services.$inferSelect;

/** Stored settings with defaults filled in, so projects saved before a setting existed read it as its default. */
const withSettings = (row: ProjectRow): ProjectRow => ({ ...row, settings: resolveSettings(row.settings) });

export const getProject = (db: Db, id: string): ProjectRow | undefined => {
  const row = db.select().from(projects).where(eq(projects.id, id)).get();
  return row ? withSettings(row) : undefined;
};

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

export const listServices = (db: Db, projectId: string): ServiceRow[] =>
  db.select().from(services).where(eq(services.project_id, projectId)).orderBy(asc(services.name)).all();

export const getService = (db: Db, id: string): ServiceRow | undefined => db.select().from(services).where(eq(services.id, id)).get();

export const findService = (db: Db, projectId: string, name: string): ServiceRow | undefined =>
  db.select().from(services).where(and(eq(services.project_id, projectId), eq(services.name, name))).get();

/** Services still marked running (across projects): for shutdown and crash recovery. */
export const listRunningServices = (db: Db): ServiceRow[] => db.select().from(services).where(eq(services.status, 'running')).all();

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
    .all()
    .map(withSettings);

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

/** Highest event id of a project (0 if none): a stream cursor meaning "from now on". */
export const lastProjectSeq = (db: Db, projectId: string): number =>
  db.select({ seq: max(events.id) }).from(events).where(eq(events.project_id, projectId)).get()?.seq ?? 0;

/** The project's most recent event of `type`. */
export function lastProjectEvent<T extends EventType>(db: Db, projectId: string, type: T): EventOf<T> | undefined {
  const row = db
    .select()
    .from(events)
    .where(and(eq(events.project_id, projectId), eq(events.type, type)))
    .orderBy(desc(events.id))
    .limit(1)
    .get();
  return row ? ({ ...row } as unknown as EventOf<T>) : undefined;
}

/** Whether the project has an event of `type` after event id `afterId`. */
export function hasProjectEventAfter(db: Db, projectId: string, type: EventType, afterId: number): boolean {
  return (
    db
      .select({ id: events.id })
      .from(events)
      .where(and(eq(events.project_id, projectId), eq(events.type, type), gt(events.id, afterId)))
      .limit(1)
      .get() !== undefined
  );
}

/** The latest `stalled` notice sent on behalf of `threadId`. */
export function lastStallFor(db: Db, projectId: string, threadId: string): EventOf<'message.agent'> | undefined {
  const row = db
    .select()
    .from(events)
    .where(
      and(
        eq(events.project_id, projectId),
        eq(events.type, 'message.agent'),
        sql`json_extract(${events.payload}, '$.kind') = 'stalled'`,
        sql`json_extract(${events.payload}, '$.from_agent_id') = ${threadId}`,
      ),
    )
    .orderBy(desc(events.id))
    .limit(1)
    .get();
  return row ? ({ ...row } as unknown as EventOf<'message.agent'>) : undefined;
}

/** Token usage per project and model, optionally from `sinceDay` (YYYY-MM-DD) on. */
export function getUsageByProject(db: Db, sinceDay?: string): Array<{ project_id: string; model: string; prompt_tokens: number; completion_tokens: number }> {
  return db
    .select({
      project_id: usageTotals.project_id,
      model: usageTotals.model,
      prompt_tokens: sql<number>`sum(${usageTotals.prompt_tokens})`,
      completion_tokens: sql<number>`sum(${usageTotals.completion_tokens})`,
    })
    .from(usageTotals)
    .where(sinceDay ? gte(usageTotals.day, sinceDay) : undefined)
    .groupBy(usageTotals.project_id, usageTotals.model)
    .orderBy(asc(usageTotals.project_id), asc(usageTotals.model))
    .all()
    .map((r) => ({ ...r, prompt_tokens: Number(r.prompt_tokens), completion_tokens: Number(r.completion_tokens) }));
}

/** Highest event id overall (0 if none). */
export const lastSeq = (db: Db): number => db.select({ seq: max(events.id) }).from(events).get()?.seq ?? 0;
