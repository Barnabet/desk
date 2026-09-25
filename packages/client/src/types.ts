import type { AgentRow, ApprovalRow, ArtifactRow, MemoryRow, PlanRow, ProjectRow, ServiceRow, SkillDetail, SkillSummary, SourceRow, UsageRow } from '@desk/core';
import type { StoredEvent } from '@desk/protocol';

export type { AgentRow, ApprovalRow, ArtifactRow, MemoryRow, PlanRow, ProjectRow, ServiceRow, SkillDetail, SkillSummary, SourceRow, UsageRow };

/** Contents of `<dataDir>/daemon.json`. */
export type DaemonInfo = { port: number; token: string; pid: number; version: string; started_at?: string };

export type Credentials = { baseUrl: string; token: string };

/** GET /projects/:id */
export type ProjectOverview = {
  project: ProjectRow;
  desk: AgentRow | null;
  sources: SourceRow[];
  plan: PlanRow | null;
  /** Every thread of the project, archived ones included (`archived_at` set). */
  threads: AgentRow[];
  approvals: ApprovalRow[];
  /** Project services (older daemons omit it). */
  services?: ServiceRow[];
  /** Desk's current What's up (older daemons omit it). */
  whats_up?: WhatsUp | null;
  last_seq: number;
};

/** Desk's short account of the project for the user, and when it wrote it. */
export type WhatsUp = { text: string; ts: string };

/** Paged event lists (chat, transcript, events). */
export type EventPage = { events: StoredEvent[]; next_after: number };

/** One version of a skill; `origin` is `user`, `agent:<id>` or null for versions saved before the log existed. */
export type SkillHistoryEntry = { version: number; description: string; current: boolean; change_note: string; origin: string | null; ts: string | null };
export type SkillSaveResult = { version: number; dir: string; created: boolean; description: string };
export type ProjectUsage = { rows: UsageRow[]; totals: { prompt_tokens: number; completion_tokens: number } };
