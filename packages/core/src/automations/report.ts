import { closeSync, fstatSync, openSync, readSync } from 'node:fs';
import { RUN_REPORT_MAX } from '@desk/protocol';
import type { Db } from '../db/open';
import { NotFoundError } from '../errors';
import { listArtifacts } from '../library/library';
import { pendingApprovalsFor } from '../state/queries';
import { logFile } from './folders';
import { topoOrder } from './graph';
import { getAutomation, getRun, getVersion, stepRuns } from './queries';
import { stepResultDir } from './scope';
import { effectiveRunStatus, effectiveStepStatus } from './views';

const TRIM_FLOOR = 300;
const TRIMMED = ' …[trimmed]';

/** "850 ms", "42 s", "3 min 5 s", "2 h 10 min". */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

/** The last `bytes` of a file (deskd's own logs), from a line start when cut; '' when it is missing. */
export function tailOf(file: string, bytes: number): string {
  let fd: number;
  try {
    fd = openSync(file, 'r');
  } catch {
    return '';
  }
  try {
    const size = fstatSync(fd).size;
    const len = Math.min(size, bytes);
    const buf = Buffer.alloc(len);
    readSync(fd, buf, 0, len, size - len);
    const text = buf.toString('utf8');
    return size > len ? `…${text.slice(text.indexOf('\n') + 1)}` : text;
  } finally {
    closeSync(fd);
  }
}

/** A line of the report; `trim` parts give way when the report is too long (`from: 'start'` keeps the end). */
type Part = { head: string; body: string; trim: boolean; from?: 'start' };

function cut(p: Part): void {
  const n = Math.max(TRIM_FLOOR, Math.floor(p.body.length * 0.6));
  p.body = p.from === 'start' ? `${TRIMMED.trim()} ${p.body.slice(p.body.length - n)}` : `${p.body.slice(0, n)}${TRIMMED}`;
}

function render(parts: Part[]): string {
  return parts.map((p) => p.head + p.body).join('\n');
}

/**
 * What Desk reads about a run (spec §6.2): a header, each step in graph order, then the published library paths.
 * Capped at RUN_REPORT_MAX: the longest summaries, outputs, errors and logs are trimmed first.
 */
export function runReport(db: Db, dataDir: string, runId: string, now = new Date()): string {
  const run = getRun(db, runId);
  if (!run) throw new NotFoundError(`Unknown run: ${runId}`);
  const automation = getAutomation(db, run.automation_id)!;
  const def = getVersion(db, run.automation_id, run.version)?.definition ?? automation.definition;
  const parts: Part[] = [];
  const line = (text: string) => parts.push({ head: text, body: '', trim: false });
  const long = (head: string, body: string, from?: 'start') => parts.push({ head, body, trim: true, ...(from ? { from } : {}) });

  const took = formatDuration((run.finished_at ? Date.parse(run.finished_at) : now.getTime()) - Date.parse(run.started_at));
  line(`Automation "${def.title}" (${automation.name}), v${run.version} · run ${run.id}${run.test ? ' · test run' : ''}`);
  line(`Trigger: ${run.trigger} by ${run.by} · Status: ${effectiveRunStatus(db, run)} · Took ${took}`);
  if (run.reason) long('Reason: ', run.reason);
  if (Object.keys(run.inputs).length) long('Inputs: ', JSON.stringify(run.inputs));
  if (run.summary) long('Summary: ', run.summary);
  line('');
  line('Steps:');

  const rows = new Map(stepRuns(db, run.id).map((r) => [r.step_id, r]));
  topoOrder(def).forEach((id, i) => {
    const step = def.steps.find((s) => s.id === id)!;
    const row = rows.get(id);
    const status = row ? effectiveStepStatus(db, row) : 'pending';
    line(`${i + 1}. ${step.title} [${step.kind}] — ${status}${row && row.attempt > 1 ? ` (attempt ${row.attempt})` : ''}${row?.route ? ` · route ${row.route}` : ''}`);
    if (!row) return;
    if (row.summary) long('   Summary: ', row.summary);
    if (Object.keys(row.outputs).length) long('   Outputs: ', JSON.stringify(row.outputs));
    if (row.error) long('   Error: ', row.error);
    if (step.kind === 'script' && row.status === 'failed') {
      line(`   Log: ${logFile(dataDir, run.id, id)}`);
      const tail = tailOf(logFile(dataDir, run.id, id), 3000);
      if (tail) long('   Log tail:\n', tail, 'start');
    }
    if (row.status !== 'pending') line(`   Folder: ${stepResultDir(db, dataDir, run.id, row, id)}`);
    if (row.agent_id) line(`   Agent: ${row.agent_id}`);
    if (row.child_run_id) line(`   Child run: ${row.child_run_id}`);
    if (status === 'waiting') {
      const ap = row.agent_id ? pendingApprovalsFor(db, row.agent_id)[0] : undefined;
      const on = row.question ? `Ask me: ${row.question.text}` : row.gate ? `Approval: ${row.gate.subject}` : ap ? `Approval: ${ap.tool} (${ap.reason})` : row.child_run_id ? 'its sub-automation run' : null;
      if (on) long('   Waiting on: ', on);
    }
  });

  const published = listArtifacts(db, run.project_id).filter((a) => a.origin === `automation:${run.id}`);
  if (published.length) {
    line('');
    line('Published to the library:');
    for (const a of published) line(`- ${a.path}`);
  }

  let out = render(parts);
  while (out.length > RUN_REPORT_MAX) {
    const longest = parts.filter((p) => p.trim && p.body.length > TRIM_FLOOR).sort((a, b) => b.body.length - a.body.length)[0];
    if (!longest) break;
    cut(longest);
    out = render(parts);
  }
  return out.length > RUN_REPORT_MAX ? `${out.slice(0, RUN_REPORT_MAX - 22)}\n…[report truncated]` : out;
}
