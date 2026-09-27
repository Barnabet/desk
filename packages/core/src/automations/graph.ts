// Pure graph semantics of automations (spec 2026-09-26-automations-design §3.2): order, ancestors, edge firing and readiness.
import type { AutomationEdge, StepStatus } from '@desk/protocol';

/** A step's state as the graph sees it. */
export type StepState = { status: StepStatus; route: string | null };

/** The part of a definition the graph needs. An `AutomationDefinition` fits. */
export type GraphDef = { steps: ReadonlyArray<{ id: string; join?: 'all' | 'any' }>; edges: ReadonlyArray<AutomationEdge> };

export const TERMINAL_STEP: ReadonlySet<StepStatus> = new Set<StepStatus>(['succeeded', 'failed', 'rejected', 'skipped', 'cancelled']);
const UNDECIDED: ReadonlySet<StepStatus> = new Set<StepStatus>(['pending', 'running', 'waiting']);
/** Statuses whose route a routed edge follows: `failed` carries `error` (on_error continue), `rejected` carries `rejected`. */
const ROUTED: ReadonlySet<StepStatus> = new Set<StepStatus>(['succeeded', 'failed', 'rejected']);
const PENDING: StepState = { status: 'pending', route: null };

/** A step's own entry in `states`, or pending when it has none. */
function stateOf(states: Record<string, StepState>, id: string): StepState {
  return (Object.hasOwn(states, id) ? states[id] : undefined) ?? PENDING;
}

export function incoming(def: GraphDef, stepId: string): AutomationEdge[] {
  return def.edges.filter((e) => e.to === stepId);
}

/** Steps with no incoming edge, in definition order: they start when the run starts. */
export function startSteps(def: GraphDef): string[] {
  const targets = new Set(def.edges.map((e) => e.to));
  return def.steps.filter((s) => !targets.has(s.id)).map((s) => s.id);
}

/** Every step with a path to `stepId`, not counting itself. */
export function ancestors(def: GraphDef, stepId: string): Set<string> {
  const found = new Set<string>();
  const queue = [stepId];
  while (queue.length > 0) {
    const current = queue.pop()!;
    for (const e of def.edges) {
      if (e.to === current && !found.has(e.from)) {
        found.add(e.from);
        queue.push(e.from);
      }
    }
  }
  found.delete(stepId);
  return found;
}

/**
 * Step ids in topological order (Kahn), ties broken by definition order. Edges naming unknown steps are ignored
 * (validation reports them). Throws `Error('cycle: a → b → a')` naming one cycle.
 */
export function topoOrder(def: GraphDef): string[] {
  const ids = [...new Set(def.steps.map((s) => s.id))];
  const rank = new Map(ids.map((id, i) => [id, i]));
  const indegree = new Map(ids.map((id) => [id, 0]));
  const next = new Map(ids.map((id): [string, string[]] => [id, []]));
  const edges = def.edges.filter((e) => rank.has(e.from) && rank.has(e.to));
  for (const e of edges) {
    next.get(e.from)!.push(e.to);
    indegree.set(e.to, indegree.get(e.to)! + 1);
  }
  const ready = ids.filter((id) => indegree.get(id) === 0);
  const order: string[] = [];
  while (ready.length > 0) {
    ready.sort((a, b) => rank.get(a)! - rank.get(b)!);
    const id = ready.shift()!;
    order.push(id);
    for (const to of next.get(id)!) {
      const left = indegree.get(to)! - 1;
      indegree.set(to, left);
      if (left === 0) ready.push(to);
    }
  }
  if (order.length === ids.length) return order;

  // Every step left has a predecessor that is also left: walk predecessors until one repeats.
  const done = new Set(order);
  const left = new Set(ids.filter((id) => !done.has(id)));
  const trail: string[] = [];
  const seenAt = new Map<string, number>();
  let current = ids.find((id) => left.has(id))!;
  while (!seenAt.has(current)) {
    seenAt.set(current, trail.length);
    trail.push(current);
    current = edges.find((e) => e.to === current && left.has(e.from))!.from;
  }
  const loop = trail.slice(seenAt.get(current)).reverse();
  const first = loop.reduce((best, id, i) => (rank.get(id)! < rank.get(loop[best]!)! ? i : best), 0);
  const cycle = [...loop.slice(first), ...loop.slice(0, first)];
  throw new Error(`cycle: ${[...cycle, cycle[0]].join(' → ')}`);
}

/**
 * Whether an edge fires from a settled source. Without a route: the source succeeded (with any route or none). With a route:
 * the source took it and succeeded, failed or was rejected. `when` decides the edge's condition (true when it has none),
 * and is asked only when the rest holds.
 */
export function edgeFires(edge: AutomationEdge, source: StepState, when: (edge: AutomationEdge) => boolean): boolean {
  if (edge.route === undefined) return source.status === 'succeeded' && when(edge);
  return source.route === edge.route && ROUTED.has(source.status) && when(edge);
}

/**
 * One layer of decisions over the pending steps: which to start and which to skip (spec §3.2 joins). A step missing from
 * `states` counts as pending. Skips spread: the caller records the skips and calls again until both lists are empty.
 */
export function readiness(def: GraphDef, states: Record<string, StepState>, when: (edge: AutomationEdge) => boolean): { start: string[]; skip: string[] } {
  const start: string[] = [];
  const skip: string[] = [];
  for (const step of def.steps) {
    if (stateOf(states, step.id).status !== 'pending') continue;
    const edges = incoming(def, step.id);
    if (edges.length === 0) {
      start.push(step.id);
      continue;
    }
    let undecided = 0;
    let fired = 0;
    for (const e of edges) {
      const source = stateOf(states, e.from);
      if (UNDECIDED.has(source.status)) undecided += 1;
      else if (edgeFires(e, source, when)) fired += 1;
    }
    if (step.join === 'any') {
      if (fired > 0) start.push(step.id);
      else if (undecided === 0) skip.push(step.id);
    } else if (undecided === 0) {
      (fired > 0 ? start : skip).push(step.id);
    }
  }
  return { start, skip };
}

/** True when no step is pending, running or waiting. With `def`, a step missing from `states` counts as pending. */
export function isSettled(states: Record<string, StepState>, def?: GraphDef): boolean {
  if (def && def.steps.some((s) => !Object.hasOwn(states, s.id))) return false;
  return Object.values(states).every((s) => !UNDECIDED.has(s.status));
}
