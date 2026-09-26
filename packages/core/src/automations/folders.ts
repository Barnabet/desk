import { lstat, mkdir, readdir, writeFile } from 'node:fs/promises';
import { existsSync, mkdirSync, rmSync } from 'node:fs';
import { basename, isAbsolute, join } from 'node:path';
import type { InputSpec, InputValue } from '@desk/protocol';
import type { Db } from '../db/open';
import { ValidationError } from '../errors';
import { openAgentFile } from '../tools/agent-files';
import { isWithin, realOrSelf, type SandboxGuard } from '../tools/sandbox';
import { childRuns, listRuns, type AutomationRunRow } from './queries';

/** File and folder inputs of one run, copied, at most. */
export const INPUTS_MAX_BYTES = 200 * 1024 * 1024;

export const runDir = (dataDir: string, runId: string): string => join(dataDir, 'automation-runs', runId);
export const stepDir = (dataDir: string, runId: string, stepId: string): string => join(runDir(dataDir, runId), 'steps', stepId);
export const inputsDir = (dataDir: string, runId: string): string => join(runDir(dataDir, runId), 'inputs');
/** A step's full output. Only deskd writes `<run>/logs/`; step folders are the scripts' and agents' own. */
export const logFile = (dataDir: string, runId: string, stepId: string): string => join(runDir(dataDir, runId), 'logs', `${stepId}.txt`);

const STRING_TYPES = new Set(['text', 'long_text', 'url', 'file', 'folder', 'choice']);

/** A run's input values: defaults applied, CLI strings coerced, every value checked against its spec. */
export function resolveInputs(specs: InputSpec[], given: Record<string, InputValue>): Record<string, InputValue> {
  const byKey = new Map(specs.map((s) => [s.key, s]));
  for (const k of Object.keys(given)) if (!byKey.has(k)) throw new ValidationError(`Unknown input: ${k}`);
  const out: Record<string, InputValue> = {};
  for (const s of specs) {
    let v: InputValue | undefined = Object.hasOwn(given, s.key) ? given[s.key] : s.default;
    if (v === undefined || v === '') {
      if (s.required) throw new ValidationError(`Input "${s.label}" is required`);
      continue;
    }
    if (s.type === 'number') {
      const n = typeof v === 'string' ? Number(v) : v;
      if (typeof n !== 'number' || !Number.isFinite(n)) throw new ValidationError(`Input "${s.label}" must be a number`);
      v = n;
    } else if (s.type === 'boolean') {
      const b = v === 'true' ? true : v === 'false' ? false : v;
      if (typeof b !== 'boolean') throw new ValidationError(`Input "${s.label}" must be true or false`);
      v = b;
    } else if (STRING_TYPES.has(s.type)) {
      if (typeof v !== 'string') throw new ValidationError(`Input "${s.label}" must be text`);
      if (s.type === 'url' && !/^https?:\/\/[^\s]+$/i.test(v)) throw new ValidationError(`Input "${s.label}" must be an http(s) URL`);
      if (s.type === 'choice' && !(s.options ?? []).includes(v)) throw new ValidationError(`Input "${s.label}" must be one of: ${(s.options ?? []).join(', ')}`);
    }
    out[s.key] = v;
  }
  return out;
}

type Budget = { left: number };

/** Copies one agent-reachable file: regular, no final symlink, not a secret, not in Desk's data folder. */
async function copyInputFile(src: string, dest: string, guard: SandboxGuard, budget: Budget, label: string): Promise<void> {
  const real = realOrSelf(src);
  if (isWithin(real, guard.dataDir)) throw new ValidationError(`Input "${label}" is inside Desk's data folder`);
  let handle;
  try {
    handle = await openAgentFile(src, guard);
  } catch (e) {
    throw new ValidationError(`Input "${label}" cannot be read: ${e instanceof Error ? e.message : String(e)}`);
  }
  try {
    budget.left -= handle.st.size;
    if (budget.left < 0) throw new ValidationError(`File inputs are too large (at most 200 MB per run)`);
    await mkdir(join(dest, '..'), { recursive: true });
    await writeFile(dest, await handle.fh.readFile());
  } finally {
    await handle.fh.close();
  }
}

/** Copies a folder's regular files recursively; symlinks and other special files are skipped. */
async function copyInputFolder(src: string, dest: string, guard: SandboxGuard, budget: Budget, label: string): Promise<void> {
  await mkdir(dest, { recursive: true });
  for (const entry of await readdir(src, { withFileTypes: true })) {
    const from = join(src, entry.name);
    const to = join(dest, entry.name);
    if (entry.isSymbolicLink()) continue;
    if (entry.isDirectory()) await copyInputFolder(from, to, guard, budget, label);
    else if (entry.isFile()) await copyInputFile(from, to, guard, budget, label);
  }
}

/**
 * Creates the run folder (inputs/, steps/), copies file and folder inputs into inputs/<key>/ and writes inputs.json.
 * Returns the resolved values, with file and folder inputs as the paths of their copies. Throws ValidationError
 * before copying anything when a value does not fit its spec.
 */
export async function prepareRunFolder(o: {
  dataDir: string;
  runId: string;
  specs: InputSpec[];
  inputs: Record<string, InputValue>;
  guard: SandboxGuard;
  maxBytes?: number;
}): Promise<Record<string, InputValue>> {
  const values = resolveInputs(o.specs, o.inputs);
  const dir = runDir(o.dataDir, o.runId);
  await mkdir(join(dir, 'steps'), { recursive: true });
  await mkdir(inputsDir(o.dataDir, o.runId), { recursive: true });
  const budget = { left: o.maxBytes ?? INPUTS_MAX_BYTES };
  for (const s of o.specs) {
    const v = values[s.key];
    if ((s.type !== 'file' && s.type !== 'folder') || typeof v !== 'string') continue;
    if (!isAbsolute(v)) throw new ValidationError(`Input "${s.label}" must be an absolute path`);
    const st = await lstat(v).catch(() => null);
    if (!st) throw new ValidationError(`Input "${s.label}" does not exist: ${v}`);
    const dest = join(inputsDir(o.dataDir, o.runId), s.key);
    if (s.type === 'folder') {
      if (!st.isDirectory()) throw new ValidationError(`Input "${s.label}" must be a folder`);
      if (isWithin(realOrSelf(v), o.guard.dataDir)) throw new ValidationError(`Input "${s.label}" is inside Desk's data folder`);
      await copyInputFolder(v, dest, o.guard, budget, s.label);
      values[s.key] = dest;
    } else {
      const target = join(dest, basename(v));
      await copyInputFile(v, target, o.guard, budget, s.label);
      values[s.key] = target;
    }
  }
  await writeFile(join(dir, 'inputs.json'), JSON.stringify(values, null, 2));
  return values;
}

/**
 * The step's working folder (with `.desk/` for DESK_OUTPUT) and the run's `logs/`, created synchronously so a step
 * starts in one pass.
 */
export function ensureStepDir(dataDir: string, runId: string, stepId: string): string {
  const dir = stepDir(dataDir, runId, stepId);
  mkdirSync(join(dir, '.desk'), { recursive: true });
  mkdirSync(join(runDir(dataDir, runId), 'logs'), { recursive: true });
  return dir;
}

/** A run's sub-automation runs, nested. */
function descendants(db: Db, runId: string): AutomationRunRow[] {
  const out: AutomationRunRow[] = [];
  const queue = [runId];
  while (queue.length) {
    for (const c of childRuns(db, queue.shift()!)) {
      out.push(c);
      queue.push(c.id);
    }
  }
  return out;
}

/**
 * Deletes the folders of an automation's top-level runs beyond the `keep` newest and older than `days`, with their
 * sub-automation runs' folders. Never a running family, a protected run, or a protected descendant.
 */
export function pruneRuns(o: { db: Db; dataDir: string; automationId: string; keep?: number; days?: number; now: Date; protect: Set<string> }): string[] {
  const keep = o.keep ?? 20;
  const cutoff = o.now.getTime() - (o.days ?? 30) * 24 * 60 * 60_000;
  const roots = listRuns(o.db, o.automationId, { limit: 100_000 }).filter((r) => !r.parent_run_id);
  const removed: string[] = [];
  roots.forEach((r, i) => {
    if (i < keep || r.status === 'running' || o.protect.has(r.id)) return;
    if (Date.parse(r.started_at) >= cutoff) return;
    const family = [r, ...descendants(o.db, r.id)];
    if (family.some((d) => d.status === 'running')) return;
    for (const d of family) {
      if (d.id !== r.id && o.protect.has(d.id)) continue;
      const dir = runDir(o.dataDir, d.id);
      if (!existsSync(dir)) continue;
      rmSync(dir, { recursive: true, force: true });
      removed.push(d.id);
    }
  });
  return removed;
}
