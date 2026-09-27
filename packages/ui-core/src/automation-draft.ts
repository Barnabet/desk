import { InputKey, StepId, type AutomationDefinition, type AutomationEdge, type AutomationLayout, type InputSpec, type ScheduleTrigger, type Step, type StepKind } from '@desk/protocol';
import { ancestors, NODE_W, START_ID, START_W } from './automation-graph';

/** What the Design view edits: the definition and the node positions. */
export type AutomationDoc = { def: AutomationDefinition; layout: AutomationLayout };

const GAP_Y = 110;
const GAP_X = NODE_W + 40;

const dropUndefined = <T extends object>(o: T): T => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined)) as T;
const firstMessage = (r: { success: false; error: { issues: Array<{ message: string }> } }) => r.error.issues[0]?.message ?? 'Not valid';

/** A new automation's definition: the title from its name, no steps yet (saving needs one). */
export function blankDefinition(name: string): AutomationDefinition {
  const words = name.replace(/-+/g, ' ').trim();
  return {
    title: words ? `${words[0]!.toUpperCase()}${words.slice(1)}` : name,
    description: '',
    inputs: [],
    triggers: [],
    steps: [],
    edges: [],
    after_run: 'notify',
    limits: { run_deadline_hours: 24, max_parallel_agents: 2, max_parallel_scripts: 4 },
  };
}

const common = () => ({ join: 'all' as const, on_error: 'stop' as const, routes: [] as string[], publish: [] as string[] });

/** A new step of a kind, with every default filled so the draft compares equal to what the daemon returns. */
export function freshStep(kind: StepKind, id: string): Step {
  switch (kind) {
    case 'script':
      return { ...common(), id, kind, title: 'New script step', skill: '', script: '', args: [], idempotent: false };
    case 'agent':
      return { ...common(), id, kind, title: 'New agent step', brief: '', skills: [], output_keys: [] };
    case 'ask':
      return { ...common(), id, kind, title: 'Ask me', question: '', show: [] };
    case 'wait':
      return { ...common(), id, kind, title: 'Wait', minutes: 60 };
    case 'automation':
      return { ...common(), id, kind, title: 'Run another automation', automation: '', inputs: {} };
    case 'tell_desk':
      return { ...common(), id, kind, title: 'Tell Desk', text: '', attach: [] };
  }
}

/** `agent`, then `agent_2`, `agent_3`… */
export function uniqueStepId(def: AutomationDefinition, kind: StepKind): string {
  const ids = new Set(def.steps.map((s) => s.id));
  if (!ids.has(kind)) return kind;
  for (let n = 2; ; n++) if (!ids.has(`${kind}_${n}`)) return `${kind}_${n}`;
}

/** Adds a step. After a step, it is connected below it (to the right of that step's other children); otherwise it goes under everything. */
export function addStep(doc: AutomationDoc, kind: StepKind, after: string | null): { doc: AutomationDoc; id: string } {
  const id = uniqueStepId(doc.def, kind);
  const parent = after && doc.def.steps.some((s) => s.id === after) ? after : null;
  const anchor = parent ? doc.layout[parent] : undefined;
  const lowest = Object.values(doc.layout).reduce((m, p) => Math.max(m, p.y), 0);
  const siblings = parent ? doc.def.edges.filter((e) => e.from === parent).length : 0;
  const position = anchor ? { x: anchor.x + siblings * GAP_X, y: anchor.y + GAP_Y } : { x: (doc.layout[START_ID]?.x ?? 0) + (START_W - NODE_W) / 2, y: lowest + GAP_Y };
  return {
    id,
    doc: {
      def: { ...doc.def, steps: [...doc.def.steps, freshStep(kind, id)], edges: parent ? [...doc.def.edges, { from: parent, to: id }] : doc.def.edges },
      layout: { ...doc.layout, [id]: position },
    },
  };
}

/** Removes steps (with their edges and positions, and the output step if it was one) and edges by index, both from the same definition. */
export function removeSelection(doc: AutomationDoc, stepIds: string[], edgeIndices: number[]): AutomationDoc {
  const gone = new Set(stepIds);
  const cut = new Set(edgeIndices);
  const { output_step, ...rest } = doc.def;
  const def: AutomationDefinition = {
    ...rest,
    steps: doc.def.steps.filter((s) => !gone.has(s.id)),
    edges: doc.def.edges.filter((e, i) => !cut.has(i) && !gone.has(e.from) && !gone.has(e.to)),
    ...(output_step && !gone.has(output_step) ? { output_step } : {}),
  };
  return { def, layout: Object.fromEntries(Object.entries(doc.layout).filter(([k]) => !gone.has(k))) };
}

/** Why `from` → `to` cannot be added, or null. */
export function canConnect(def: AutomationDefinition, from: string, to: string): string | null {
  if (from === to) return 'A step cannot come after itself.';
  if (!def.steps.some((s) => s.id === from) || !def.steps.some((s) => s.id === to)) return 'Connect two steps.';
  if (def.edges.some((e) => e.from === from && e.to === to)) return 'These steps are already connected.';
  if (ancestors(def, from).has(to)) return 'That would make a loop: an automation runs top to bottom.';
  return null;
}

export function connect(doc: AutomationDoc, from: string, to: string): { doc: AutomationDoc } | { error: string } {
  const error = canConnect(doc.def, from, to);
  return error ? { error } : { doc: { ...doc, def: { ...doc.def, edges: [...doc.def.edges, { from, to }] } } };
}

/** Merges fields into one step (never its id or kind). A field set to undefined is removed. */
export function patchStep(doc: AutomationDoc, id: string, patch: Partial<Step>): AutomationDoc {
  return { ...doc, def: { ...doc.def, steps: doc.def.steps.map((s) => (s.id === id ? (dropUndefined({ ...s, ...patch, id: s.id, kind: s.kind }) as Step) : s)) } };
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Every templated text of a step, mapped. */
function mapText(step: Step, fn: (text: string) => string): Step {
  switch (step.kind) {
    case 'script':
      return { ...step, args: step.args.map(fn), ...(step.stdin !== undefined ? { stdin: fn(step.stdin) } : {}) };
    case 'agent':
      return { ...step, brief: fn(step.brief) };
    case 'ask':
      return { ...step, question: fn(step.question) };
    case 'automation':
      return { ...step, inputs: Object.fromEntries(Object.entries(step.inputs).map(([k, v]) => [k, fn(v)])) };
    case 'tell_desk':
      return { ...step, text: fn(step.text) };
    default:
      return step;
  }
}

/** Renames a step and every reference to it: edges, positions, the output step, `steps.<id>.` and `previous.steps.<id>.` in templates and conditions. */
export function renameStep(doc: AutomationDoc, from: string, to: string): { doc: AutomationDoc } | { error: string } {
  if (from === to) return { doc };
  const parsed = StepId.safeParse(to);
  if (!parsed.success) return { error: firstMessage(parsed) };
  if (doc.def.steps.some((s) => s.id === to)) return { error: `Another step is already called ${to}.` };
  const refs = new RegExp(`(^|[^a-z0-9_.-])((?:previous\\.)?steps\\.)${escape(from)}(?=\\.)`, 'g');
  const fix = (text: string) => text.replace(refs, `$1$2${to}`);
  const rename = (id: string) => (id === from ? to : id);
  const def: AutomationDefinition = {
    ...doc.def,
    steps: doc.def.steps.map((s) => mapText(s.id === from ? { ...s, id: to } : s, fix)),
    edges: doc.def.edges.map((e) => ({ ...e, from: rename(e.from), to: rename(e.to), ...(e.when ? { when: fix(e.when) } : {}) })),
    ...(doc.def.output_step ? { output_step: rename(doc.def.output_step) } : {}),
  };
  return { doc: { def, layout: Object.fromEntries(Object.entries(doc.layout).map(([k, v]) => [rename(k), v])) } };
}

/** Sets or clears an edge's route and condition. */
export function patchEdge(doc: AutomationDoc, index: number, patch: { route?: string | undefined; when?: string | undefined }): AutomationDoc {
  return { ...doc, def: { ...doc.def, edges: doc.def.edges.map((e, i) => (i === index ? (dropUndefined({ ...e, ...patch }) as AutomationEdge) : e)) } };
}

export const setInputs = (doc: AutomationDoc, inputs: InputSpec[]): AutomationDoc => ({ ...doc, def: { ...doc.def, inputs } });

/** Renames an input and `inputs.<key>` in templates, conditions and schedule inputs. */
export function renameInput(doc: AutomationDoc, from: string, to: string): { doc: AutomationDoc } | { error: string } {
  if (from === to) return { doc };
  const parsed = InputKey.safeParse(to);
  if (!parsed.success) return { error: firstMessage(parsed) };
  if (doc.def.inputs.some((i) => i.key === to)) return { error: `Another input is already called ${to}.` };
  const refs = new RegExp(`(^|[^a-z0-9_.-])inputs\\.${escape(from)}(?![a-z0-9_])`, 'g');
  const fix = (text: string) => text.replace(refs, `$1inputs.${to}`);
  const def: AutomationDefinition = {
    ...doc.def,
    inputs: doc.def.inputs.map((i) => (i.key === from ? { ...i, key: to } : i)),
    steps: doc.def.steps.map((s) => mapText(s, fix)),
    edges: doc.def.edges.map((e) => (e.when ? { ...e, when: fix(e.when) } : e)),
    triggers: doc.def.triggers.map((t) => (t.inputs ? { ...t, inputs: Object.fromEntries(Object.entries(t.inputs).map(([k, v]) => [k === from ? to : k, v])) } : t)),
  };
  return { doc: { ...doc, def } };
}

export const setTriggers = (doc: AutomationDoc, triggers: ScheduleTrigger[]): AutomationDoc => ({ ...doc, def: { ...doc.def, triggers } });

/** Automation-level settings. `output_step: undefined` clears it. */
export type AutomationMeta = Pick<AutomationDefinition, 'title' | 'description' | 'after_run' | 'limits'> & { output_step?: string | undefined };
export const setMeta = (doc: AutomationDoc, patch: Partial<AutomationMeta>): AutomationDoc => ({ ...doc, def: dropUndefined({ ...doc.def, ...patch }) as AutomationDefinition });

export const moveNodes = (doc: AutomationDoc, positions: AutomationLayout): AutomationDoc => ({ ...doc, layout: { ...doc.layout, ...positions } });
