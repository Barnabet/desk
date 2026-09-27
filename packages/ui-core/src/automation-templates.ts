import type { AutomationDefinition, Step } from '@desk/protocol';
import { ancestors } from './automation-graph';

/** A path to offer. `open` paths end with `.`: the user names the output after it (scripts and sub-automations decide theirs at run time). */
export type TemplateSuggestion = { path: string; label: string; open?: boolean };

const RUN: TemplateSuggestion[] = [
  { path: 'run.date', label: "the run's date (YYYY-MM-DD)" },
  { path: 'run.dir', label: 'the run folder' },
  { path: 'run.id', label: 'the run id' },
  { path: 'run.trigger', label: 'what started the run' },
  { path: 'run.test', label: 'true in a test run' },
];

/** Outputs other steps can name, as the validator knows them (spec §3.3). */
function outputKeys(s: Step): Array<{ key: string; label: string }> {
  if (s.kind === 'agent') return s.output_keys.map((k) => ({ key: k.key, label: k.description }));
  if (s.kind === 'ask') return [{ key: 'note', label: 'your note' }];
  if (s.kind === 'automation') return [{ key: 'run_id', label: 'the child run id' }];
  return [];
}

const opensOutputs = (s: Step) => s.kind === 'script' || s.kind === 'automation';

/** What `{{…}}` may name in a step: inputs, results of steps upstream of it, the run, and the last good run's step folders. */
export function templateSuggestions(def: AutomationDefinition, stepId: string | null): TemplateSuggestion[] {
  const out: TemplateSuggestion[] = def.inputs.map((i) => ({ path: `inputs.${i.key}`, label: i.label }));
  const up = stepId ? ancestors(def, stepId) : new Set<string>();
  for (const s of def.steps) {
    if (!up.has(s.id)) continue;
    out.push({ path: `steps.${s.id}.summary`, label: `${s.title}: its summary` }, { path: `steps.${s.id}.dir`, label: `${s.title}: its folder` });
    if (s.routes.length || s.kind === 'ask' || s.on_error === 'continue') out.push({ path: `steps.${s.id}.route`, label: `${s.title}: the route it took` });
    for (const k of outputKeys(s)) out.push({ path: `steps.${s.id}.outputs.${k.key}`, label: `${s.title}: ${k.label}` });
    if (opensOutputs(s)) out.push({ path: `steps.${s.id}.outputs.`, label: `${s.title}: an output (type its name)`, open: true });
  }
  out.push(...RUN);
  for (const s of def.steps) out.push({ path: `previous.steps.${s.id}.dir`, label: `${s.title}: its folder in the last good run` });
  return out;
}

/** What a `when` on an edge leaving `from` may name: inputs, and the status, route and outputs of `from` and the steps upstream of it. */
export function conditionSuggestions(def: AutomationDefinition, from: string): TemplateSuggestion[] {
  const out: TemplateSuggestion[] = def.inputs.map((i) => ({ path: `inputs.${i.key}`, label: i.label }));
  const reach = new Set([from, ...ancestors(def, from)]);
  for (const s of def.steps) {
    if (!reach.has(s.id)) continue;
    out.push({ path: `steps.${s.id}.status`, label: `${s.title}: status` }, { path: `steps.${s.id}.route`, label: `${s.title}: route` });
    for (const k of outputKeys(s)) out.push({ path: `steps.${s.id}.outputs.${k.key}`, label: `${s.title}: ${k.label}` });
    if (opensOutputs(s)) out.push({ path: `steps.${s.id}.outputs.`, label: `${s.title}: an output (type its name)`, open: true });
  }
  return out;
}

/** The partial path typed after an unclosed `{{` before the caret, or null. */
export function activeToken(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, caret);
  const open = before.lastIndexOf('{{');
  if (open === -1 || before.indexOf('}}', open) !== -1) return null;
  const query = before.slice(open + 2).trimStart();
  return /^[a-z0-9_.-]*$/.test(query) ? { start: open, query } : null;
}

/** Suggestions matching a partial path, those starting with it first. */
export function filterSuggestions(list: TemplateSuggestion[], query: string, max = 8): TemplateSuggestion[] {
  const q = query.toLowerCase();
  return list
    .filter((s) => s.path.includes(q) || s.label.toLowerCase().includes(q))
    .sort((a, b) => Number(b.path.startsWith(q)) - Number(a.path.startsWith(q)))
    .slice(0, max);
}

/** Replaces the typed `{{partial` with `{{path}}` (reusing a `}}` right after the caret). An open path stays open for its key. */
export function insertSuggestion(text: string, caret: number, s: TemplateSuggestion): { text: string; caret: number } {
  const t = activeToken(text, caret);
  if (!t) return { text, caret };
  const after = text.slice(caret);
  const insert = s.open ? `{{${s.path}` : `{{${s.path}}}`;
  const rest = !s.open && after.startsWith('}}') ? after.slice(2) : after;
  return { text: text.slice(0, t.start) + insert + rest, caret: t.start + insert.length };
}

/** The `{{path}}`s a text uses, each once, in order. */
export function pathsIn(text: string): string[] {
  const out = new Set<string>();
  for (const m of text.matchAll(/\{\{\s*([^{}]+?)\s*\}\}/g)) out.add(m[1]!);
  return [...out];
}

/** Whether a path is one of the suggestions (an open one followed by a key), or a previous run's output of a known step. */
export function isKnownPath(path: string, suggestions: TemplateSuggestion[]): boolean {
  if (suggestions.some((s) => (s.open ? path.startsWith(s.path) && /^[a-z][a-z0-9_]*$/.test(path.slice(s.path.length)) : s.path === path))) return true;
  const prev = /^previous\.steps\.([a-z][a-z0-9_-]*)\.outputs\.[a-z][a-z0-9_]*$/.exec(path);
  return !!prev && suggestions.some((s) => s.path === `previous.steps.${prev[1]}.dir`);
}
