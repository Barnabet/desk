import fg from 'fast-glob';
import { realpathSync } from 'node:fs';
import type { AutomationDefinition } from '@desk/protocol';
import type { Db } from '../db/open';
import { isWithin } from '../tools/sandbox';
import type { ExprScope } from './expr';
import { runDir, stepDir } from './folders';
import { ancestors } from './graph';
import { lastSucceededRun, stepRuns, type AutomationRunRow } from './queries';
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
    steps[s.id] = { outputs: r?.outputs ?? {}, summary: r?.summary ?? null, route: r?.route ?? null, dir: stepDir(o.dataDir, o.run.id, s.id) };
  }
  const prev = lastSucceededRun(o.db, o.run.automation_id);
  const previous =
    prev && prev.id !== o.run.id
      ? {
          steps: Object.fromEntries(stepRuns(o.db, prev.id).map((r) => [r.step_id, { outputs: r.outputs, dir: stepDir(o.dataDir, prev.id, r.step_id) }])),
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
export function matchUpstream(o: { dataDir: string; run: AutomationRunRow; def: AutomationDefinition; stepId: string; globs: string[] }): string[] {
  const safe = o.globs.filter((g) => !g.startsWith('/') && !g.split(/[\\/]/).includes('..'));
  if (!safe.length) return [];
  const out: string[] = [];
  for (const id of o.def.steps.map((s) => s.id).filter((id) => ancestors(o.def, o.stepId).has(id))) {
    const dir = stepDir(o.dataDir, o.run.id, id);
    let real: string;
    try {
      real = realpathSync(dir);
    } catch {
      continue;
    }
    for (const f of fg.sync(safe, { cwd: real, onlyFiles: true, followSymbolicLinks: false, absolute: true, dot: false, ignore: ['.desk/**'] })) {
      if (isWithin(f, real)) out.push(f);
    }
  }
  return [...new Set(out)];
}
