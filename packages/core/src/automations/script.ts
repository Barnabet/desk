import { mkdir, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import { StepOutputFile, SUMMARY_MAX, type Outputs } from '@desk/protocol';
import { runProcess, type ProcessResult } from '../tools/process';
import { commandInvocation, type SandboxSpec } from '../tools/sandbox';
import { commandParts } from '../tools/skills';

/** A script's DESK_OUTPUT: valid JSON, a declared route (or none), outputs within their limits. */
export function parseStepOutput(raw: string, routes: string[]): { ok: true; route: string | null; outputs: Outputs; summary: string | null } | { ok: false; error: string } {
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch (e) {
    return { ok: false, error: `DESK_OUTPUT is not valid JSON: ${e instanceof Error ? e.message : String(e)}` };
  }
  const p = StepOutputFile.safeParse(json);
  if (!p.success) return { ok: false, error: `DESK_OUTPUT: ${p.error.issues.map((i) => `${i.path.join('.') || 'value'}: ${i.message}`).join('; ')}` };
  const route = p.data.route ?? null;
  if (route !== null && !routes.includes(route)) {
    return { ok: false, error: `DESK_OUTPUT: route "${route}" is not one of this step's routes (${routes.join(', ') || 'none declared'})` };
  }
  return { ok: true, route, outputs: p.data.outputs ?? {}, summary: p.data.summary ?? null };
}

/** The last non-blank line of a script's output (its summary when it wrote no DESK_OUTPUT), capped. */
export function lastLine(output: string): string | null {
  const line = output
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .at(-1);
  return line ? line.slice(0, SUMMARY_MAX) : null;
}

/** Runs a skill file with argv (no shell), sandboxed when enabled; writes the whole output to `logFile`. */
export async function runScript(o: {
  file: string;
  args: string[];
  stdin?: string;
  stepDir: string;
  env: NodeJS.ProcessEnv;
  sandbox: SandboxSpec;
  timeoutMs: number;
  signal: AbortSignal;
  logFile: string;
}): Promise<ProcessResult> {
  const { command, args } = commandParts(o.file);
  const r = await runProcess({
    ...commandInvocation(command, [...args, ...o.args], o.sandbox),
    cwd: o.stepDir,
    env: o.env,
    timeoutMs: o.timeoutMs,
    signal: o.signal,
    ...(o.stdin !== undefined ? { stdin: o.stdin } : {}),
  });
  // `<run>/logs/` is deskd's own (never writable by a step), so a plain write is safe here.
  await mkdir(dirname(o.logFile), { recursive: true });
  await writeFile(o.logFile, r.output);
  return r;
}
