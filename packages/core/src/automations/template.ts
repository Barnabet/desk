// {{path}} templates (spec 2026-09-26-automations-design §3.3): agent briefs, script args and stdin, questions, Tell Desk text and
// sub-automation inputs. Values are substituted once and never scanned again, and script arguments stay one argv element each.

const KEY = '[a-z][a-z0-9_]{0,39}';
const ID = '[a-z][a-z0-9_-]{0,39}';

/** Every path a template may name. */
export const PATH_PATTERN = new RegExp(
  `^(?:inputs\\.${KEY}|steps\\.${ID}\\.(?:outputs\\.${KEY}|summary|route|dir)|run\\.(?:id|dir|date|trigger|test)|previous\\.steps\\.${ID}\\.(?:dir|outputs\\.${KEY}))$`,
);

/** A template that does not parse. `position` is the index of the `{{` at fault. */
export class TemplateError extends Error {
  constructor(
    message: string,
    readonly position: number,
  ) {
    super(message);
    this.name = 'TemplateError';
  }
}

/** What templates read. `previous` is the last succeeded non-test run of the same automation, or null. */
export type TemplateScope = {
  inputs: Record<string, unknown>;
  steps: Record<string, { outputs: Record<string, unknown>; summary: string | null; route: string | null; dir: string }>;
  run: { id: string; dir: string; date: string; trigger: string; test: boolean };
  previous: { steps: Record<string, { outputs: Record<string, unknown>; dir: string }> } | null;
};

type Segment = { text: string } | { path: string };

function clip(s: string): string {
  return s.length > 60 ? `${s.slice(0, 60)}…` : s;
}

/** Splits a template into literal text and paths. A lone `}}` is text. */
function segments(src: string): Segment[] {
  const out: Segment[] = [];
  let i = 0;
  while (i < src.length) {
    const open = src.indexOf('{{', i);
    if (open === -1) {
      out.push({ text: src.slice(i) });
      break;
    }
    if (open > i) out.push({ text: src.slice(i, open) });
    const close = src.indexOf('}}', open + 2);
    if (close === -1) throw new TemplateError(`Unclosed {{ at position ${open}`, open);
    const path = src.slice(open + 2, close).trim();
    if (path === '') throw new TemplateError(`Empty {{ }} at position ${open}`, open);
    if (!PATH_PATTERN.test(path)) {
      throw new TemplateError(
        `'{{${clip(path)}}}' at position ${open} is not a valid template path: use inputs.<key>, steps.<id>.outputs.<key>|summary|route|dir, run.id|dir|date|trigger|test or previous.steps.<id>.dir|outputs.<key>`,
        open,
      );
    }
    out.push({ path });
    i = close + 2;
  }
  return out;
}

/** The paths a template names, each once, in first-seen order. Throws `TemplateError` on an unclosed `{{` or a malformed path. */
export function templatePaths(src: string): string[] {
  const seen = new Set<string>();
  for (const s of segments(src)) if ('path' in s) seen.add(s.path);
  return [...seen];
}

/** An own property's value, or undefined. Never reads inherited names such as `constructor`. */
function own(obj: unknown, key: string): unknown {
  return typeof obj === 'object' && obj !== null && Object.hasOwn(obj, key) ? (obj as Record<string, unknown>)[key] : undefined;
}

/** `outputs.<key>`, or a field (`summary`, `route`, `dir`) of a step. */
function stepValue(step: unknown, rest: string[]): unknown {
  return rest[0] === 'outputs' ? own(own(step, 'outputs'), rest[1]!) : own(step, rest[0]!);
}

/** A checked path's value; undefined when anything along it is missing. */
function resolve(path: string, scope: TemplateScope): unknown {
  const p = path.split('.');
  switch (p[0]) {
    case 'inputs':
      return own(scope.inputs, p[1]!);
    case 'run':
      return own(scope.run, p[1]!);
    case 'steps':
      return stepValue(own(scope.steps, p[1]!), p.slice(2));
    case 'previous':
      return scope.previous ? stepValue(own(scope.previous.steps, p[2]!), p.slice(3)) : undefined;
  }
  return undefined;
}

/** Missing and null render empty; numbers and booleans through String(); a list one item per line. */
function toText(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (Array.isArray(v)) return v.map((item) => toText(item)).join('\n');
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return JSON.stringify(v) ?? '';
}

function renderSegments(segs: Segment[], scope: TemplateScope): string {
  return segs.map((s) => ('path' in s ? toText(resolve(s.path, scope)) : s.text)).join('');
}

/** Renders a text template. Missing values and a null `previous` render as ''. */
export function renderText(src: string, scope: TemplateScope): string {
  return renderSegments(segments(src), scope);
}

/**
 * Renders script arguments, each element on its own; nothing is ever parsed by a shell. An element that is exactly one
 * `{{path}}` and resolves to a list spreads into one element per item (none for an empty list); a missing value gives ''.
 */
export function renderArgs(args: string[], scope: TemplateScope): string[] {
  const out: string[] = [];
  for (const arg of args) {
    const segs = segments(arg);
    const only = segs.length === 1 ? segs[0]! : null;
    if (only && 'path' in only) {
      const value = resolve(only.path, scope);
      if (Array.isArray(value)) out.push(...value.map((item) => toText(item)));
      else out.push(toText(value));
    } else out.push(renderSegments(segs, scope));
  }
  return out;
}
