import { eq } from 'drizzle-orm';
import type { Db } from '../db/open';
import { agents, projects, usageTotals } from '../db/schema';

export type ProjectRow = typeof projects.$inferSelect;
export type AgentRow = typeof agents.$inferSelect;
export type UsageRow = typeof usageTotals.$inferSelect;

export const getProject = (db: Db, id: string): ProjectRow | undefined =>
  db.select().from(projects).where(eq(projects.id, id)).get();

export const getAgent = (db: Db, id: string): AgentRow | undefined => db.select().from(agents).where(eq(agents.id, id)).get();

export const listAgents = (db: Db, projectId: string): AgentRow[] =>
  db.select().from(agents).where(eq(agents.project_id, projectId)).all();

export const getUsageTotals = (db: Db, projectId: string): UsageRow[] =>
  db.select().from(usageTotals).where(eq(usageTotals.project_id, projectId)).all();
