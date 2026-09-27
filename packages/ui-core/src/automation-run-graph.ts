import { summarizeToolArgs, type AutomationDefinition, type RunDetail, type StepRunInfo, type StoredEvent } from '@desk/protocol';
import type { GraphRun, NodeRunState } from './automation-graph';
import { clock, duration } from './format';

const firstLine = (s: string) => s.split('\n')[0]!.trim();
const clipTo = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const span = (a: string | null, b: string | null, now: number) => (a ? duration((b ? Date.parse(b) : now) - Date.parse(a)) : '');

/** A step's look in a run (spec §8.3): its tone, a short badge and a one-line detail. `activity` is a running agent's tool call. */
export function stepLook(row: StepRunInfo | undefined, now: number, activity?: string | null): NodeRunState {
  if (!row || row.attempt === 0 || row.status === 'pending') return { tone: 'pending', badge: 'pending', detail: null };
  switch (row.status) {
    case 'running':
      return { tone: 'run', badge: `● ${span(row.started_at, null, now)}`.trim(), detail: activity ?? (row.attempt > 1 ? `attempt ${row.attempt}` : 'running') };
    case 'waiting':
      return {
        tone: 'wait',
        badge: 'waiting',
        detail: row.question ? 'waiting on you' : row.gate ? `approve ${row.gate.tool}` : row.resume_at ? `until ${clock(row.resume_at)}` : row.child_run_id ? 'running the other automation' : row.agent_id ? 'waiting on an approval' : 'waiting',
      };
    case 'succeeded':
      return { tone: 'ok', badge: `✓ ${span(row.started_at, row.finished_at, now)}`.trim(), detail: row.route ? `route ${row.route}` : row.summary ? clipTo(firstLine(row.summary), 60) : null };
    case 'rejected':
      return { tone: 'ok', badge: '✓ rejected', detail: 'route rejected' };
    case 'failed':
      return { tone: 'fail', badge: '✕ failed', detail: row.error ? clipTo(firstLine(row.error), 60) : null };
    case 'skipped':
      return { tone: 'skipped', badge: 'skipped', detail: null };
    default:
      return { tone: 'idle', badge: 'cancelled', detail: null };
  }
}

const REACHED = new Set(['running', 'waiting', 'succeeded', 'failed', 'rejected']);

/**
 * The edges that fired, by index: the source resolved the way the edge waits for (spec §3.2) and its target was reached.
 * `when` cannot be evaluated here; with `join: any` a target reached through another edge can light one whose `when` was false.
 */
export function firedEdges(def: AutomationDefinition, steps: StepRunInfo[]): Set<number> {
  const byId = new Map(steps.map((s) => [s.step_id, s]));
  const out = new Set<number>();
  def.edges.forEach((e, i) => {
    const from = byId.get(e.from);
    const to = byId.get(e.to);
    if (!from || !to || !(REACHED.has(to.status) || to.started_at)) return;
    const ok = e.route ? from.route === e.route : from.status === 'succeeded';
    if (ok) out.add(i);
  });
  return out;
}

/** Every step's look and the fired edges, for the run canvas. `activity` maps step ids to their agent's tool call. */
export function runGraph(run: RunDetail, now: number, activity: Record<string, string | null>): GraphRun {
  const byId = new Map(run.steps.map((s) => [s.step_id, s]));
  return {
    steps: Object.fromEntries(run.definition.steps.map((s) => [s.id, stepLook(byId.get(s.id), now, activity[s.id] ?? null)])),
    fired: firedEdges(run.definition, run.steps),
  };
}

/** A running agent's current tool call ("read_file · acme.md"), or null between calls. */
export function agentActivity(events: readonly StoredEvent[], agentId: string): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i]!;
    if (e.agent_id !== agentId) continue;
    if (e.type === 'tool.result') return null;
    if (e.type === 'tool.call') return `${e.payload.name} · ${summarizeToolArgs(e.payload.arguments)}`;
  }
  return null;
}

/** The step a run view opens on: one waiting on the user, else one running, else the one that failed. */
export function focusStep(run: RunDetail): string | null {
  const find = (pred: (s: StepRunInfo) => boolean) => run.steps.find(pred)?.step_id ?? null;
  return find((s) => s.status === 'waiting' && (s.question !== null || s.gate !== null)) ?? find((s) => s.status === 'running' || s.status === 'waiting') ?? find((s) => s.status === 'failed');
}

/** Ask Desk to fix (spec §8.3): "Please fix <automation>: run #<n> failed at <step>. <error first line>". */
export function askDeskText(run: Pick<RunDetail, 'automation_name' | 'number'>, stepTitle: string, error: string): string {
  return `Please fix ${run.automation_name}: run #${run.number} failed at ${stepTitle}. ${firstLine(error)}`;
}

/** Stop step (spec §8.3) is offered for an unfinished agent step of a run still going. */
export const canStopStep = (run: Pick<RunDetail, 'status'>, row: StepRunInfo): boolean => (run.status === 'running' || run.status === 'waiting') && row.agent_id !== null && (row.status === 'running' || row.status === 'waiting');

/** A path inside the run folder (an Ask me step's shown files are absolute) as the run files API names it, or null. */
export function relRunPath(path: string, runId: string): string | null {
  const marker = `/automation-runs/${runId}/`;
  const i = path.indexOf(marker);
  return i < 0 ? null : path.slice(i + marker.length);
}
