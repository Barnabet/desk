import {
  AutomationDefinition,
  type AutomationEdge,
  type InputSpec,
  type InputValue,
  type ReasoningEffort,
  type Step,
  type ValidationIssue,
} from '@desk/protocol';
import { ExprError, exprPaths, parseExpr } from './expr';
import { ancestors, topoOrder } from './graph';
import { checkSchedule, isTimezone } from './schedule';
import { TemplateError, templatePaths } from './template';

// Definition validation (spec 2026-09-26-automations-design §6.3), shared by automation_save, the API and the editor.

/** What validation needs from the project (Runtime.validateContext builds it). */
export type ValidateContext = {
  projectId: string;
  /** The automation being validated ('' for a draft without a name). */
  name: string;
  /** A skill as agents resolve it (project → global → built-in); `hasScript` accepts `fetch.py` for `scripts/fetch.py`. */
  skill(name: string): { ok: true; hasScript(path: string): boolean; builtinOff: boolean } | { ok: false; reason: string };
  /** Why a model and effort cannot be used, as in spawn_thread, or null. `''` means the project's thread model. */
  model(model: string, effort?: ReasoningEffort): string | null;
  /** Whether the id names a git source of the project. */
  gitSource(id: string): boolean;
  /** The current definition of another automation of the project, by name. */
  automation(name: string): AutomationDefinition | null;
  now?: Date;
};

export type ValidationResult = { definition: AutomationDefinition | null; errors: ValidationIssue[]; warnings: ValidationIssue[] };

const KIND_LABEL: Record<Step['kind'], string> = {
  script: 'script',
  agent: 'agent',
  ask: 'Ask me',
  automation: 'sub-automation',
  wait: 'wait',
  tell_desk: 'Tell Desk',
};

/** A zod issue path as the editor addresses fields: `steps[2].brief`, `edges[0].route`; the root is ''. */
export function issuePath(path: readonly PropertyKey[]): string {
  let out = '';
  for (const seg of path) {
    if (typeof seg === 'number') out += `[${seg}]`;
    else out += out ? `.${String(seg)}` : String(seg);
  }
  return out;
}

/** The fields of a step that hold `{{…}}` templates, with their paths. */
function templateFields(s: Step, i: number): Array<[path: string, text: string]> {
  const at = `steps[${i}]`;
  switch (s.kind) {
    case 'agent':
      return [[`${at}.brief`, s.brief]];
    case 'script':
      return [...s.args.map((a, j): [string, string] => [`${at}.args[${j}]`, a]), ...(s.stdin !== undefined ? [[`${at}.stdin`, s.stdin] as [string, string]] : [])];
    case 'ask':
      return [[`${at}.question`, s.question]];
    case 'tell_desk':
      return [[`${at}.text`, s.text]];
    case 'automation':
      return Object.entries(s.inputs).map(([k, v]): [string, string] => [`${at}.inputs.${k}`, v]);
    case 'wait':
      return [];
  }
}

/** Why an input value does not fit its input (type, choice options), or null. */
function valueProblem(inp: InputSpec, value: InputValue): string | null {
  const want = inp.type === 'number' ? 'number' : inp.type === 'boolean' ? 'boolean' : 'string';
  if (typeof value !== want) return `'${inp.key}' is a ${inp.type} input: use a ${want}`;
  if (inp.type === 'choice' && inp.options && !inp.options.includes(String(value))) return `'${String(value)}' is not an option of '${inp.key}' (${inp.options.join(', ')})`;
  return null;
}

/** Why an edge may not use `route` from `src`, or null. */
function routeProblem(src: Step, route: string): string | null {
  if (src.routes.includes(route)) return null;
  if (route === 'error') return src.on_error === 'continue' ? null : `Route 'error' needs on_error: continue on '${src.id}'`;
  if (route === 'rejected') return src.kind === 'ask' ? null : `Only Ask me steps take the route 'rejected' ('${src.id}' is a ${KIND_LABEL[src.kind]} step)`;
  return src.routes.length ? `'${src.id}' has no route '${route}' (its routes: ${src.routes.join(', ')})` : `'${src.id}' has no route '${route}' (it declares none)`;
}

/** Why `steps.<id>.outputs.<key>` of `s` cannot be read, or null: agents declare theirs, Ask me has `note`, waits and Tell Desk have none. */
function outputProblem(p: string, s: Step, key: string): string | null {
  switch (s.kind) {
    case 'agent': {
      if (s.output_keys.some((k) => k.key === key)) return null;
      const declared = s.output_keys.map((k) => k.key);
      return `${p}: '${s.id}' does not declare the output '${key}' (${declared.length ? `declared: ${declared.join(', ')}` : 'it declares none'})`;
    }
    case 'ask':
      return key === 'note' ? null : `${p}: Ask me steps have only the output 'note'`;
    case 'wait':
    case 'tell_desk':
      return `${p}: '${s.id}' is a ${KIND_LABEL[s.kind]} step and has no outputs`;
    case 'script':
    case 'automation':
      return null; // known only at run time
  }
}

/**
 * The first loop of sub-automation calls reachable from `name` (whose definition is `def`), as names, e.g.
 * `['a', 'b', 'a']`; `['a', 'a']` when it runs itself. Unknown automations are skipped.
 */
export function subAutomationCycle(name: string, def: AutomationDefinition, lookup: (name: string) => AutomationDefinition | null): string[] | null {
  const done = new Set<string>();
  const stack: string[] = [];
  const visit = (n: string, d: AutomationDefinition): string[] | null => {
    stack.push(n);
    for (const s of d.steps) {
      if (s.kind !== 'automation') continue;
      const at = stack.indexOf(s.automation);
      if (at >= 0) return [...stack.slice(at), s.automation];
      if (done.has(s.automation)) continue;
      const child = lookup(s.automation);
      if (!child) continue;
      const found = visit(s.automation, child);
      if (found) return found;
    }
    stack.pop();
    done.add(n);
    return null;
  };
  return visit(name, def);
}

/** Parses `raw` and runs every check of spec §6.3. `definition` is null only when the shape itself is wrong. */
export function validateDefinition(raw: unknown, ctx: ValidateContext): ValidationResult {
  const parsed = AutomationDefinition.safeParse(raw);
  if (!parsed.success) {
    return { definition: null, errors: parsed.error.issues.map((i) => ({ path: issuePath(i.path), message: i.message })), warnings: [] };
  }
  const def = parsed.data;
  const errors: ValidationIssue[] = [];
  const warnings: ValidationIssue[] = [];
  const error = (path: string, message: string): void => {
    if (!errors.some((e) => e.path === path && e.message === message)) errors.push({ path, message });
  };
  const warn = (path: string, message: string): void => {
    warnings.push({ path, message });
  };

  // ── ids and keys ──
  if (!def.steps.length) error('steps', 'Add at least one step');
  const steps = new Map<string, Step>();
  def.steps.forEach((s, i) => {
    if (steps.has(s.id)) error(`steps[${i}].id`, `Another step already has the id '${s.id}'`);
    else steps.set(s.id, s);
  });
  const inputs = new Map<string, InputSpec>();
  def.inputs.forEach((inp, i) => {
    if (inputs.has(inp.key)) error(`inputs[${i}].key`, `Another input already has the key '${inp.key}'`);
    else inputs.set(inp.key, inp);
  });

  // ── inputs ──
  def.inputs.forEach((inp, i) => {
    if (inp.type === 'choice' && !inp.options?.length) error(`inputs[${i}].options`, `'${inp.key}' is a choice input: give it options`);
    if (inp.default !== undefined) {
      const problem = valueProblem(inp, inp.default);
      if (problem) error(`inputs[${i}].default`, problem);
    }
  });

  // ── edges and the graph ──
  const graphEdges: AutomationEdge[] = [];
  const seen = new Map<string, number>();
  def.edges.forEach((e, i) => {
    let ok = true;
    if (!steps.has(e.from)) {
      error(`edges[${i}].from`, `No step has the id '${e.from}'`);
      ok = false;
    }
    if (!steps.has(e.to)) {
      error(`edges[${i}].to`, `No step has the id '${e.to}'`);
      ok = false;
    }
    if (ok && e.from === e.to) {
      error(`edges[${i}]`, `An edge cannot lead from '${e.from}' to itself`);
      ok = false;
    }
    const key = JSON.stringify([e.from, e.to, e.route ?? null, e.when ?? null]);
    const first = seen.get(key);
    if (first !== undefined) error(`edges[${i}]`, `Same edge as edges[${first}]`);
    else {
      seen.set(key, i);
      if (ok) graphEdges.push(e);
    }
  });
  // The graph functions see each id once and only well-formed edges.
  const graph: AutomationDefinition = { ...def, steps: [...steps.values()], edges: graphEdges };
  let acyclic = true;
  try {
    topoOrder(graph);
  } catch (e) {
    acyclic = false;
    error('edges', e instanceof Error ? e.message : String(e));
  }
  const upstream = new Map<string, Set<string>>();
  const upstreamOf = (id: string): Set<string> => {
    let u = upstream.get(id);
    if (!u) {
      // In a cyclic graph "upstream" means nothing: any other step passes, so the cycle stays the error shown.
      u = acyclic ? ancestors(graph, id) : new Set([...steps.keys()].filter((s) => s !== id));
      upstream.set(id, u);
    }
    return u;
  };

  /** Why a template or condition path cannot be read from where it is used, or null. `self` is the step it belongs to (null for an edge). */
  const refProblem = (p: string, reach: ReadonlySet<string>, self: string | null, where: string): string | null => {
    const seg = p.split('.');
    if (seg[0] === 'inputs') {
      const key = seg[1] ?? '';
      return inputs.has(key) ? null : `${p}: no input has the key '${key}'`;
    }
    if (seg[0] === 'previous') {
      const id = seg[2] ?? '';
      return steps.has(id) ? null : `${p}: no step has the id '${id}'`;
    }
    if (seg[0] === 'steps') {
      const id = seg[1] ?? '';
      const s = steps.get(id);
      if (!s) return `${p}: no step has the id '${id}'`;
      if (id === self) return `${p}: a step cannot read its own results`;
      if (!reach.has(id)) return `${p}: '${id}' is not upstream of ${where}`;
      return seg[2] === 'outputs' ? outputProblem(p, s, seg[3] ?? '') : null;
    }
    return null; // run.*: always available
  };

  def.edges.forEach((e, i) => {
    const src = steps.get(e.from);
    if (src && e.route !== undefined) {
      const problem = routeProblem(src, e.route);
      if (problem) error(`edges[${i}].route`, problem);
    }
    if (e.when === undefined) return;
    let paths: string[];
    try {
      paths = exprPaths(parseExpr(e.when));
    } catch (err) {
      error(`edges[${i}].when`, err instanceof ExprError ? `${err.message} (at position ${err.position})` : String(err));
      return;
    }
    if (!src) return; // reported above
    const reach = new Set([e.from, ...upstreamOf(e.from)]);
    for (const p of paths) {
      const problem = refProblem(p, reach, null, `'${e.from}'`);
      if (problem) error(`edges[${i}].when`, problem);
    }
  });

  // ── steps ──
  def.steps.forEach((s, i) => {
    const at = `steps[${i}]`;
    for (const [path, text] of templateFields(s, i)) {
      let paths: string[];
      try {
        paths = templatePaths(text);
      } catch (err) {
        error(path, err instanceof TemplateError ? err.message : String(err));
        continue;
      }
      const reach = upstreamOf(s.id);
      for (const p of paths) {
        const problem = refProblem(p, reach, s.id, `'${s.id}'`);
        if (problem) error(path, problem);
      }
    }
    const routes = new Set<string>();
    s.routes.forEach((r, j) => {
      if (routes.has(r)) error(`${at}.routes[${j}]`, `The route '${r}' is listed twice`);
      routes.add(r);
    });

    if (s.kind === 'script') {
      const skill = ctx.skill(s.skill);
      if (!skill.ok) error(`${at}.skill`, skill.reason);
      else {
        if (!skill.hasScript(s.script)) error(`${at}.script`, `The skill '${s.skill}' has no script '${s.script}'`);
        if (skill.builtinOff) warn(`${at}.skill`, `'${s.skill}' is a built-in skill that is turned off: this step fails until it is turned on`);
      }
    }

    if (s.kind === 'agent') {
      s.skills.forEach((name, j) => {
        const skill = ctx.skill(name);
        if (!skill.ok) error(`${at}.skills[${j}]`, skill.reason);
        else if (skill.builtinOff) warn(`${at}.skills[${j}]`, `'${name}' is a built-in skill that is turned off: the agent cannot use it until it is turned on`);
      });
      if (s.model !== undefined || s.reasoning_effort !== undefined) {
        const problem = ctx.model(s.model ?? '', s.reasoning_effort);
        if (problem) error(s.model !== undefined ? `${at}.model` : `${at}.reasoning_effort`, problem);
      }
      if (s.git_source_id !== undefined && !ctx.gitSource(s.git_source_id)) error(`${at}.git_source_id`, `'${s.git_source_id}' is not a git source of this project`);
      const keys = new Set<string>();
      s.output_keys.forEach((k, j) => {
        if (keys.has(k.key)) error(`${at}.output_keys[${j}].key`, `The output key '${k.key}' is listed twice`);
        keys.add(k.key);
      });
    }

    // Running itself is reported as a cycle below.
    if (s.kind === 'automation' && s.automation !== ctx.name) {
      const child = ctx.automation(s.automation);
      if (!child) error(`${at}.automation`, `No automation named '${s.automation}' in this project`);
      else {
        const declared = new Set(child.inputs.map((inp) => inp.key));
        for (const key of Object.keys(s.inputs)) if (!declared.has(key)) error(`${at}.inputs.${key}`, `'${s.automation}' has no input '${key}'`);
        for (const inp of child.inputs) {
          if (inp.required && inp.default === undefined && !(inp.key in s.inputs)) error(`${at}.inputs`, `'${s.automation}' needs the input '${inp.key}'`);
        }
      }
    }
  });

  const cycle = subAutomationCycle(ctx.name, def, (n) => (n === ctx.name ? def : ctx.automation(n)));
  if (cycle) {
    const i = def.steps.findIndex((s) => s.kind === 'automation' && s.automation === cycle[1]);
    const j = i >= 0 ? i : def.steps.findIndex((s) => s.kind === 'automation');
    error(`steps[${j}].automation`, `Automations would run each other in a loop: ${cycle.join(' → ')}`);
  }

  if (def.output_step !== undefined && !steps.has(def.output_step)) error('output_step', `No step has the id '${def.output_step}'`);

  // ── schedules ──
  def.triggers.forEach((t, i) => {
    const at = `triggers[${i}]`;
    if (!isTimezone(t.timezone)) error(`${at}.timezone`, `Unknown timezone '${t.timezone}': use an IANA name such as Europe/Paris`);
    else {
      const problem = checkSchedule(t.cron, t.timezone, ctx.now);
      if (problem) error(`${at}.cron`, problem);
    }
    const given = t.inputs ?? {};
    for (const [key, value] of Object.entries(given)) {
      const inp = inputs.get(key);
      if (!inp) error(`${at}.inputs.${key}`, `No input has the key '${key}'`);
      else {
        const problem = valueProblem(inp, value);
        if (problem) error(`${at}.inputs.${key}`, problem);
      }
    }
    // A scheduled run has nobody to ask for a value.
    for (const inp of inputs.values()) {
      if (inp.required && inp.default === undefined && !(inp.key in given)) error(`${at}.inputs`, `'${inp.key}' is required and has no default: give it a value for this schedule`);
    }
  });

  // ── warnings ──
  def.steps.forEach((s, i) => {
    s.routes.forEach((r, j) => {
      if (!def.edges.some((e) => e.from === s.id && e.route === r)) warn(`steps[${i}].routes[${j}]`, `No edge takes the route '${r}': when '${s.id}' chooses it, that branch ends`);
    });
  });
  if (steps.size > 1) {
    def.steps.forEach((s, i) => {
      if (!def.edges.some((e) => e.from === s.id || e.to === s.id)) warn(`steps[${i}]`, `'${s.id}' has no edges: it runs on its own when the run starts`);
    });
  }

  return { definition: def, errors, warnings };
}
