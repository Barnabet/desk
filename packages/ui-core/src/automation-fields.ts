import type { SkillSummary } from '@desk/client';
import { InputKey, RESERVED_ROUTES, RouteName, type AutomationDefinition, type BuiltinSkillInfo } from '@desk/protocol';

/** A skill a step can name, and where the one agents would use lives. */
export type SkillChoice = { name: string; source: 'project' | 'global' | 'builtin' };

/** The skills a step can name, each once: a project skill over a global one over a built-in (as agents resolve them). Broken, disabled or malformed ones are left out. */
export function skillChoices(project: SkillSummary[], global: SkillSummary[], builtins: BuiltinSkillInfo[]): SkillChoice[] {
  const out = new Map<string, SkillChoice>();
  for (const b of builtins) if (b.enabled && !b.broken) out.set(b.name, { name: b.name, source: 'builtin' });
  for (const g of global) if (!g.error) out.set(g.name, { name: g.name, source: 'global' });
  for (const p of project) if (!p.error) out.set(p.name, { name: p.name, source: 'project' });
  return [...out.values()].sort((a, b) => a.name.localeCompare(b.name));
}

const SCRIPT = /\.(py|js|mjs|cjs|ts|sh)$/;

/** A skill's scripts, by path: files with a script extension, not helpers (`_common.py`), caches or hidden files. */
export function scriptFiles(files: Array<{ path: string }>): string[] {
  return files
    .map((f) => f.path)
    .filter((p) => SCRIPT.test(p) && !p.split('/').some((part) => part.startsWith('.') || part.startsWith('_')))
    .sort();
}

/** Why a step cannot declare a route of this name, or null. */
export function routeProblem(name: string): string | null {
  if ((RESERVED_ROUTES as readonly string[]).includes(name)) return `${name} is set by Desk itself (a failure with "continue", or a rejected question). Edges can wait for it without declaring it.`;
  const r = RouteName.safeParse(name);
  return r.success ? null : (r.error.issues[0]?.message ?? 'Not a valid route name');
}

/** Why an output key is not valid, or null. */
export function keyProblem(key: string): string | null {
  const r = InputKey.safeParse(key);
  return r.success ? null : (r.error.issues[0]?.message ?? 'Not a valid key');
}

export type RouteOption = { value: string; label: string };

/** What an edge leaving `from` can wait for: success (no route), each declared route, `error` (on error continue), `rejected` (Ask me). */
export function edgeRouteOptions(def: AutomationDefinition, from: string, current?: string): RouteOption[] {
  const s = def.steps.find((x) => x.id === from);
  const out: RouteOption[] = [{ value: '', label: 'When it succeeds' }];
  for (const r of s?.routes ?? []) out.push({ value: r, label: `On route ${r}` });
  if (s?.on_error === 'continue') out.push({ value: 'error', label: 'When it fails (route error)' });
  if (s?.kind === 'ask') out.push({ value: 'rejected', label: 'When you reject it' });
  if (current && !out.some((o) => o.value === current)) out.push({ value: current, label: `On route ${current} (not declared)` });
  return out;
}
