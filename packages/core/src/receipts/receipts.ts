import { createHash } from 'node:crypto';
import { and, desc, eq, inArray, like } from 'drizzle-orm';
import type { EventOf, ReceiptOutcome } from '@desk/protocol';
import type { Db } from '../db/open';
import { checks, receipts } from '../db/schema';
import { git } from '../workspaces/workspaces';

export type ReceiptRow = typeof receipts.$inferSelect;
export type CheckRow = typeof checks.$inferSelect;

/** What a tool reports about one command it ran; Runtime.recordReceipt turns it into a `receipt.recorded` event. */
export type ReceiptInput = Omit<EventOf<'receipt.recorded'>['payload'], 'receipt_id'>;

/**
 * The commit a folder is at and whether its tracked files have uncommitted changes, read by deskd (not the agent)
 * with git's safe arguments. Nulls outside a git work tree, or when git cannot tell.
 */
export async function gitState(cwd: string): Promise<{ head: string | null; dirty: boolean | null }> {
  try {
    const head = await git(cwd, ['rev-parse', '--verify', 'HEAD^{commit}']);
    const status = await git(cwd, ['status', '--porcelain', '--untracked-files=no']);
    return { head, dirty: status.length > 0 };
  } catch {
    return { head: null, dirty: null };
  }
}

/** The size and SHA-256 of a command's output as UTF-8. */
export function fingerprint(output: string): { bytes: number; sha256: string } {
  const bytes = Buffer.from(output, 'utf8');
  return { bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') };
}

/** A receipt for one finished command: its outcome, duration and a fingerprint of its combined output. */
export function receiptOf(o: {
  tool: string;
  command: string;
  cwd: string;
  state: { head: string | null; dirty: boolean | null };
  startedAt: number;
  exitCode: number | null;
  timedOut?: boolean;
  aborted?: boolean;
  killed?: boolean;
  /** The combined output, or its size and hash when the caller only kept a tail of it (a background job). */
  output: string | { bytes: number; sha256: string };
  toolCallId?: string;
  checkId?: string;
  step?: number;
}): ReceiptInput {
  const outcome: ReceiptOutcome = o.timedOut ? 'timeout' : o.aborted ? 'aborted' : o.killed ? 'killed' : 'exit';
  const fp = typeof o.output === 'string' ? fingerprint(o.output) : o.output;
  return {
    tool: o.tool,
    ...(o.toolCallId ? { tool_call_id: o.toolCallId } : {}),
    ...(o.checkId ? { check_id: o.checkId } : {}),
    ...(o.step !== undefined ? { step: o.step } : {}),
    command: o.command,
    cwd: o.cwd,
    head: o.state.head,
    dirty: o.state.dirty,
    exit_code: o.exitCode,
    outcome,
    duration_ms: Math.max(0, Date.now() - o.startedAt),
    output_bytes: fp.bytes,
    output_sha256: fp.sha256,
    started_at: new Date(o.startedAt).toISOString(),
  };
}

/** Receipts of a project, newest first, filtered by agent, commit prefix, command text or failure. */
export function listReceipts(
  db: Db,
  projectId: string,
  f: { agentIds?: string[]; commit?: string; command?: string; failedOnly?: boolean; checkId?: string; limit?: number } = {},
): ReceiptRow[] {
  const conds = [eq(receipts.project_id, projectId)];
  if (f.agentIds) conds.push(inArray(receipts.agent_id, f.agentIds));
  if (f.commit) conds.push(like(receipts.head, `${f.commit.replace(/[%_]/g, '')}%`));
  if (f.command) conds.push(like(receipts.command, `%${f.command.replace(/[%_]/g, '')}%`));
  if (f.checkId) conds.push(eq(receipts.check_id, f.checkId));
  const rows = db
    .select()
    .from(receipts)
    .where(and(...conds))
    .orderBy(desc(receipts.finished_at), desc(receipts.id))
    .limit(f.failedOnly ? 2000 : (f.limit ?? 50))
    .all();
  return f.failedOnly ? rows.filter(failed).slice(0, f.limit ?? 50) : rows;
}

/** Commands a thread ran on `commit` with no uncommitted changes to tracked files: what the platform saw for a submission. */
export function receiptsOnCommit(db: Db, threadId: string, commit: string, limit = 30): ReceiptRow[] {
  return db
    .select()
    .from(receipts)
    .where(and(eq(receipts.agent_id, threadId), eq(receipts.head, commit), eq(receipts.dirty, false)))
    .orderBy(desc(receipts.finished_at), desc(receipts.id))
    .limit(limit)
    .all();
}

export const failed = (r: ReceiptRow): boolean => r.outcome !== 'exit' || r.exit_code !== 0;

const clip = (s: string, n: number) => {
  const flat = s.replace(/\s+/g, ' ').trim();
  return flat.length > n ? `${flat.slice(0, n - 1)}…` : flat;
};

/** `1.2 s`, `340 ms`, `2 min 5 s`. */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
  const m = Math.floor(s / 60);
  return `${m} min ${Math.round(s - m * 60)} s`;
}

/** How a command ended: `exit 0`, `timed out`, `stopped`, `killed`. */
export const endedAs = (r: { outcome: string; exit_code: number | null }): string =>
  r.outcome === 'timeout' ? 'timed out' : r.outcome === 'aborted' ? 'stopped' : r.outcome === 'killed' ? 'killed' : `exit ${r.exit_code}`;

/** One receipt on one line, for Desk and reviewers: the command is the agent's text, shown as a snippet. */
export function formatReceiptLine(r: ReceiptRow, who?: string): string {
  const at = r.head ? ` @${r.head.slice(0, 10)}${r.dirty ? '+changes' : ''}` : '';
  return `- ${endedAs(r)} · ${formatDuration(r.duration_ms)}${who ? ` · ${who}` : ''}${at} · ${r.tool}: ${clip(r.command, 160)}`;
}

export const getCheck = (db: Db, id: string): CheckRow | undefined => db.select().from(checks).where(eq(checks.id, id)).get();

/** A project's checks, newest first. */
export const listChecks = (db: Db, projectId: string, limit = 20): CheckRow[] =>
  db.select().from(checks).where(eq(checks.project_id, projectId)).orderBy(desc(checks.started_at), desc(checks.id)).limit(limit).all();

export const runningChecks = (db: Db, projectId?: string): CheckRow[] =>
  db
    .select()
    .from(checks)
    .where(projectId ? and(eq(checks.project_id, projectId), eq(checks.status, 'running')) : eq(checks.status, 'running'))
    .all();

/** One check on one line: `chk_… passed · Run the tests · 3 steps · 42 s`. */
export function formatCheckLine(c: CheckRow): string {
  const steps = `${c.steps.length} step${c.steps.length === 1 ? '' : 's'}`;
  const how = c.status === 'running' ? 'running' : `${c.status.replace('_', ' ')}${c.reason && c.status !== 'passed' ? ` (${c.reason})` : ''}`;
  return `- ${c.id} ${how} · ${clip(c.title, 80)} · ${steps}${c.duration_ms !== null ? ` · ${formatDuration(c.duration_ms)}` : ''}`;
}
