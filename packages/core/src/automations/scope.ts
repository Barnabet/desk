import fg from 'fast-glob';
import { realpathSync } from 'node:fs';
import type { AutomationDefinition } from '@desk/protocol';
import type { Db } from '../db/open';
import { isWithin } from '../tools/sandbox';
import type { ExprScope } from './expr';
import { runDir, stepDir } from './folders';
import { ancestors } from './graph';
import { childRuns, getRun, getVersion, lastSucceededRun, stepRuns, type AutomationRunRow, type StepRunRow } from './queries';
import { localDate, systemTimezone } from './schedule';
import type { TemplateScope } from './template';

/** The automation's clock: its first schedule's timezone, else the Mac's. */
export function timezoneOf(def: AutomationDefinition): string {
  return def.triggers[0]?.timezone ?? systemTimezone();
}

/** What `{{…}}` reads in a run: inputs, each step's outputs, summary, route and folder, the run, and the previous run. */
export function templateScope(o: { db: Db; dataDir: string; run: AutomationRunRow; def: AutomationDefinition }): TemplateScope {
  const rows = new Map(stepRuns(o.db, o.run.id).map((r) => [r.step_id, r]));
  const steps: TemplateScope['steps'] = {};
  for (const s of o.def.steps) {
    const r = rows.get(s.id);
    steps[s.id] = { outputs: r?.outputs ?? {}, summary: r?.summary ?? null, route: r?.route ?? null, dir: stepResultDir(o.db, o.dataDir, o.run.id, r, s.id) };
  }
  const prev = lastSucceededRun(o.db, o.run.automation_id);
  const previous =
    prev && prev.id !== o.run.id
      ? {
          steps: Object.fromEntries(stepRuns(o.db, prev.id).map((r) => [r.step_id, { outputs: r.outputs, dir: stepResultDir(o.db, o.dataDir, prev.id, r, r.step_id) }])),
        }
      : null;
  return {
    inputs: o.run.inputs,
    steps,
    run: { id: o.run.id, dir: runDir(o.dataDir, o.run.id), date: localDate(timezoneOf(o.def), new Date(o.run.started_at)), trigger: o.run.trigger, test: o.run.test },
    previous,
  };
}

/** What edge conditions read: inputs, and each step's status, route and outputs. */
export function exprScope(db: Db, run: AutomationRunRow, def: AutomationDefinition): ExprScope {
  const rows = new Map(stepRuns(db, run.id).map((r) => [r.step_id, r]));
  const steps: ExprScope['steps'] = {};
  for (const s of def.steps) {
    const r = rows.get(s.id);
    steps[s.id] = { status: r?.status ?? 'pending', route: r?.route ?? null, outputs: r?.outputs ?? {} };
  }
  return { inputs: run.inputs, steps };
}

/** Files matching `globs` in the folders of `stepId`'s ancestors (regular files, no symlinks, never outside a folder). */
/**
 * A step's result folder: its own step folder, or for a sub-automation step, the child run's output step folder
 * (followed down when that step is itself a sub-automation), or the child run folder when there is no output step.
 */
export function stepResultDir(db: Db, dataDir: string, runId: string, row: Pick<StepRunRow, 'step_id' | 'child_run_id'> | undefined, stepId: string): string {
  if (!row?.child_run_id) return stepDir(dataDir, runId, stepId);
  const child = getRun(db, row.child_run_id);
  const def = child ? getVersion(db, child.automation_id, child.version)?.definition : undefined;
  if (!child || !def?.output_step) return runDir(dataDir, row.child_run_id);
  const out = stepRuns(db, child.id).find((r) => r.step_id === def.output_step);
  return stepResultDir(db, dataDir, child.id, out, def.output_step);
}

/** The folders of every run started under `runId` (sub-automations, nested). */
export function descendantRunDirs(db: Db, dataDir: string, runId: string): string[] {
  const out: string[] = [];
  const queue = [runId];
  while (queue.length) {
    for (const c of childRuns(db, queue.shift()!)) {
      out.push(runDir(dataDir, c.id));
      queue.push(c.id);
    }
  }
  return out;
}

/** Regular files matching `globs` under `dir`: relative globs only, symlinked folders not followed, never `.desk/`. */
export function matchIn(dir: string, globs: string[]): string[] {
  const safe = globs.filter((g) => !g.startsWith('/') && !g.split(/[\\/]/).includes('..'));
  if (!safe.length) return [];
  let real: string;
  try {
    real = realpathSync(dir);
  } catch {
    return [];
  }
  return fg
    .sync(safe, { cwd: real, onlyFiles: true, followSymbolicLinks: false, absolute: true, dot: false, ignore: ['.desk/**', 'steps/*/.desk/**', 'logs/**'] })
    .filter((f) => isWithin(f, real))
    .sort();
}

export function matchUpstream(o: { db: Db; dataDir: string; run: AutomationRunRow; def: AutomationDefinition; stepId: string; globs: string[] }): string[] {
  const rows = new Map(stepRuns(o.db, o.run.id).map((r) => [r.step_id, r]));
  const up = ancestors(o.def, o.stepId);
  const out: string[] = [];
  for (const id of o.def.steps.map((s) => s.id).filter((id) => up.has(id))) out.push(...matchIn(stepResultDir(o.db, o.dataDir, o.run.id, rows.get(id), id), o.globs));
  return [...new Set(out)];
}
