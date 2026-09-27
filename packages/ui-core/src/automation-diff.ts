import type { AutomationDefinition, AutomationEdge, InputSpec, ScheduleTrigger, StepKind } from '@desk/protocol';
import { describeSchedule, systemTimezone } from './automation-schedules';

export type FieldChange = { field: string; before: string; after: string };
export type StepChange = { id: string; title: string; kind: StepKind; change: 'added' | 'removed' | 'changed'; fields: FieldChange[] };
export type InputChange = { key: string; label: string; change: 'added' | 'removed' | 'changed'; fields: FieldChange[] };
/** What changed between two versions of a definition (spec §8.4), as text to show. */
export type DefinitionDiff = {
  settings: FieldChange[];
  steps: StepChange[];
  edges: { added: string[]; removed: string[] };
  inputs: InputChange[];
  schedules: { before: string[]; after: string[] } | null;
  empty: boolean;
};

/** JSON with object keys sorted, so key order never counts as a change. */
function stable(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(stable).join(',')}]`;
  if (v && typeof v === 'object') {
    return `{${Object.keys(v)
      .sort()
      .filter((k) => (v as Record<string, unknown>)[k] !== undefined)
      .map((k) => `${JSON.stringify(k)}:${stable((v as Record<string, unknown>)[k])}`)
      .join(',')}}`;
  }
  return JSON.stringify(v ?? null);
}

const same = (a: unknown, b: unknown) => stable(a) === stable(b);

/** A value as a line of text: "—" for nothing, lists of strings joined. */
function show(v: unknown): string {
  if (v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length)) return '—';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (Array.isArray(v) && v.every((x) => typeof x === 'string')) return v.join(', ');
  return stable(v);
}

function fieldChanges(a: object, b: object, skip: string[]): FieldChange[] {
  const ra = a as Record<string, unknown>;
  const rb = b as Record<string, unknown>;
  const keys = [...new Set([...Object.keys(ra), ...Object.keys(rb)])].filter((k) => !skip.includes(k));
  return keys.filter((k) => !same(ra[k], rb[k])).map((k) => ({ field: k, before: show(ra[k]), after: show(rb[k]) }));
}

const edgeKey = (e: AutomationEdge) => `${e.from}>${e.to}|${e.route ?? ''}|${e.when ?? ''}`;

function edgeText(def: AutomationDefinition, e: AutomationEdge): string {
  const title = (id: string) => def.steps.find((s) => s.id === id)?.title ?? id;
  return `${title(e.from)} → ${title(e.to)}${e.route ? ` on ${e.route}` : ''}${e.when ? ` if ${e.when}` : ''}`;
}

function scheduleText(t: ScheduleTrigger, local: string): string {
  const inputs = t.inputs && Object.keys(t.inputs).length ? ` with ${Object.entries(t.inputs).map(([k, v]) => `${k} = ${String(v)}`).join(', ')}` : '';
  return `${describeSchedule(t.cron, t.timezone, local)}${t.catch_up === 'skip' ? ', skipping missed times' : ''}${inputs}`;
}

export function diffDefinitions(before: AutomationDefinition, after: AutomationDefinition, local = systemTimezone()): DefinitionDiff {
  const settings = [
    ...fieldChanges(
      { title: before.title, description: before.description, after_run: before.after_run, output_step: before.output_step },
      { title: after.title, description: after.description, after_run: after.after_run, output_step: after.output_step },
      [],
    ),
    ...fieldChanges(before.limits, after.limits, []).map((c) => ({ ...c, field: `limits.${c.field}` })),
  ];

  const steps: StepChange[] = [];
  const ids = [...new Set([...before.steps.map((s) => s.id), ...after.steps.map((s) => s.id)])];
  for (const id of ids) {
    const a = before.steps.find((s) => s.id === id);
    const b = after.steps.find((s) => s.id === id);
    if (a && !b) steps.push({ id, title: a.title, kind: a.kind, change: 'removed', fields: [] });
    else if (!a && b) steps.push({ id, title: b.title, kind: b.kind, change: 'added', fields: [] });
    else if (a && b) {
      const fields = fieldChanges(a, b, ['id']);
      if (fields.length) steps.push({ id, title: b.title, kind: b.kind, change: 'changed', fields });
    }
  }

  const beforeEdges = new Set(before.edges.map(edgeKey));
  const afterEdges = new Set(after.edges.map(edgeKey));
  const edges = {
    added: after.edges.filter((e) => !beforeEdges.has(edgeKey(e))).map((e) => edgeText(after, e)),
    removed: before.edges.filter((e) => !afterEdges.has(edgeKey(e))).map((e) => edgeText(before, e)),
  };

  const inputs: InputChange[] = [];
  const keys = [...new Set([...before.inputs.map((i) => i.key), ...after.inputs.map((i) => i.key)])];
  for (const key of keys) {
    const a: InputSpec | undefined = before.inputs.find((i) => i.key === key);
    const b: InputSpec | undefined = after.inputs.find((i) => i.key === key);
    if (a && !b) inputs.push({ key, label: a.label, change: 'removed', fields: [] });
    else if (!a && b) inputs.push({ key, label: b.label, change: 'added', fields: [] });
    else if (a && b) {
      const fields = fieldChanges(a, b, ['key']);
      if (fields.length) inputs.push({ key, label: b.label, change: 'changed', fields });
    }
  }

  const sb = before.triggers.map((t) => scheduleText(t, local));
  const sa = after.triggers.map((t) => scheduleText(t, local));
  const schedules = same(sb, sa) ? null : { before: sb, after: sa };

  return { settings, steps, edges, inputs, schedules, empty: !settings.length && !steps.length && !edges.added.length && !edges.removed.length && !inputs.length && !schedules };
}
