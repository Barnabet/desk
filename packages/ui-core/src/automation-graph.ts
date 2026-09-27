import { Graph, layout as dagreLayout } from '@dagrejs/dagre';
import type { AutomationDefinition, AutomationEdge, AutomationLayout, Step } from '@desk/protocol';

/** The Start pill's node id and layout key. Step ids start with a letter, so none can collide. */
export const START_ID = '__start';
export const NODE_W = 200;
export const NODE_H = 64;
export const START_W = 240;
export const START_H = 44;

type GraphDef = Pick<AutomationDefinition, 'steps' | 'edges'>;

/** What the inspector shows: the automation (none), the Start pill, a step, or an edge (by index). */
export type GraphSelection = { kind: 'none' } | { kind: 'start' } | { kind: 'step'; id: string } | { kind: 'edge'; index: number };

const EDGE = 'edge-';
export const edgeId = (i: number): string => `${EDGE}${i}`;
export function edgeIndex(id: string): number | null {
  if (!id.startsWith(EDGE)) return null;
  const n = Number(id.slice(EDGE.length));
  return Number.isInteger(n) && n >= 0 ? n : null;
}

/** Steps with no incoming edge: they start when the run starts. */
export function startSteps(def: GraphDef): string[] {
  const targets = new Set(def.edges.map((e) => e.to));
  return def.steps.filter((s) => !targets.has(s.id)).map((s) => s.id);
}

/** Every step upstream of `id`. */
export function ancestors(def: GraphDef, id: string): Set<string> {
  const out = new Set<string>();
  const stack = [id];
  while (stack.length) {
    const cur = stack.pop()!;
    for (const e of def.edges) {
      if (e.to !== cur || e.from === id || out.has(e.from)) continue;
      out.add(e.from);
      stack.push(e.from);
    }
  }
  return out;
}

export const incomingCount = (def: GraphDef, id: string): number => def.edges.filter((e) => e.to === id).length;

const KIND: Record<Step['kind'], string> = { script: 'script', agent: 'agent', ask: 'ask me', wait: 'wait', automation: 'run automation', tell_desk: 'tell desk' };

/** A node's kind line: "agent", or "agent · join all" when two or more edges come in. */
export function kindLabel(step: Step, incoming: number): string {
  return incoming >= 2 ? `${KIND[step.kind]} · join ${step.join}` : KIND[step.kind];
}

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** A node's one-line detail (mockup 2). */
export function stepDetail(step: Step): string {
  const publish = step.publish.length ? ' · publish' : '';
  switch (step.kind) {
    case 'script':
      return (step.skill && step.script ? `${step.skill}: ${step.script.replace(/^scripts\//, '')}` : 'pick a skill and script') + publish;
    case 'agent':
      return (step.skills.length ? `skills: ${step.skills.join(', ')}` : 'no skills') + publish;
    case 'ask':
      return step.show.length ? `shows ${step.show.join(', ')}` : step.question ? clip(step.question, 40) : 'write the question';
    case 'wait':
      return step.until ? `until ${step.until}` : step.minutes ? `${step.minutes} min` : 'set how long';
    case 'automation':
      return step.automation ? `runs ${step.automation}` : 'pick an automation';
    case 'tell_desk':
      return step.text ? `"${clip(step.text, 34)}"` : 'write the message';
  }
}

/** An edge's label: its route and a short form of its condition. */
export function edgeLabel(e: AutomationEdge): string | null {
  const when = e.when ? clip(e.when, 28) : null;
  if (e.route && when) return `${e.route} · ${when}`;
  return e.route ?? (when ? `if ${when}` : null);
}

/** Routes a step declares that no edge takes: each ends its branch, drawn as a dashed "<route> · ends" stub. */
export function danglingRoutes(def: GraphDef, step: Step): string[] {
  const used = new Set(def.edges.filter((e) => e.from === step.id && e.route).map((e) => e.route));
  return step.routes.filter((r) => !used.has(r));
}

/** Top-to-bottom positions for the Start pill and every step (dagre). */
export function autoLayout(def: GraphDef): AutomationLayout {
  const g = new Graph();
  g.setGraph({ rankdir: 'TB', nodesep: 48, ranksep: 56, marginx: 24, marginy: 24 });
  g.setDefaultEdgeLabel(() => ({}));
  g.setNode(START_ID, { width: START_W, height: START_H });
  const ids = new Set(def.steps.map((s) => s.id));
  for (const id of ids) g.setNode(id, { width: NODE_W, height: NODE_H });
  for (const id of startSteps(def)) g.setEdge(START_ID, id);
  for (const e of def.edges) if (ids.has(e.from) && ids.has(e.to) && e.from !== e.to) g.setEdge(e.from, e.to);
  dagreLayout(g);
  const out: AutomationLayout = {};
  for (const id of [START_ID, ...ids]) {
    const n = g.node(id) as { x: number; y: number; width: number; height: number };
    out[id] = { x: Math.round(n.x - n.width / 2), y: Math.round(n.y - n.height / 2) };
  }
  return out;
}

/** The saved positions, with dagre's for any node that has none (all of them for a definition saved without a layout). */
export function ensureLayout(def: GraphDef, layout: AutomationLayout): AutomationLayout {
  const ids = [START_ID, ...def.steps.map((s) => s.id)];
  const missing = ids.filter((id) => !layout[id]);
  if (!missing.length) return layout;
  const auto = autoLayout(def);
  if (missing.length === ids.length) return auto;
  const out = { ...layout };
  for (const id of missing) out[id] = auto[id]!;
  return out;
}

/** A step's look in a run (automation-run-graph.ts computes it). */
export type NodeTone = 'ok' | 'run' | 'wait' | 'pending' | 'skipped' | 'fail' | 'idle';
export type NodeRunState = { tone: NodeTone; badge: string; detail: string | null };

export type StepNodeData = { step: Step; kind: string; detail: string; errors: string[]; run: NodeRunState | null; output: boolean };
export type StartNodeData = { label: string; errors: string[] };
export type StubNodeData = { label: string };

type NodeOf<K extends string, D> = {
  id: string;
  type: K;
  position: { x: number; y: number };
  data: D;
  selected?: boolean;
  deletable?: boolean;
  connectable?: boolean;
  selectable?: boolean;
  draggable?: boolean;
};
export type StepGraphNode = NodeOf<'step', StepNodeData>;
export type StartGraphNode = NodeOf<'start', StartNodeData>;
export type StubGraphNode = NodeOf<'stub', StubNodeData>;
/** A canvas node, shaped like React Flow's `Node` (the desktop passes it as is). */
export type GraphNode = StepGraphNode | StartGraphNode | StubGraphNode;

export type RouteEdgeData = { label: string | null; look: 'plain' | 'fired' | 'idle' | 'stub'; error: boolean };
/** A canvas edge, shaped like React Flow's `Edge`. */
export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
  type: 'route';
  selected?: boolean;
  deletable?: boolean;
  selectable?: boolean;
  data: RouteEdgeData;
};

/** Validation problems by where they show (automation-issues.ts maps paths to these). */
export type GraphIssues = { steps: Record<string, string[]>; edges: Record<number, string[]>; start: string[] };
/** A run's step looks and the edges that fired (by index). */
export type GraphRun = { steps: Record<string, NodeRunState>; fired: ReadonlySet<number> };

/** The canvas for a definition: the Start pill, steps, route stubs, and edges (Start → start steps, then the definition's). */
export function toGraph(
  def: AutomationDefinition,
  layout: AutomationLayout,
  o: { startLabel: string; selection: GraphSelection; editable: boolean; issues?: GraphIssues; run?: GraphRun },
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const pos = ensureLayout(def, layout);
  const sel = o.selection;
  const nodes: GraphNode[] = [
    { id: START_ID, type: 'start', position: pos[START_ID]!, data: { label: o.startLabel, errors: o.issues?.start ?? [] }, selected: sel.kind === 'start', deletable: false, connectable: false },
  ];
  const edges: GraphEdge[] = [];
  for (const step of def.steps) {
    const p = pos[step.id]!;
    nodes.push({
      id: step.id,
      type: 'step',
      position: p,
      data: { step, kind: kindLabel(step, incomingCount(def, step.id)), detail: stepDetail(step), errors: o.issues?.steps[step.id] ?? [], run: o.run?.steps[step.id] ?? null, output: def.output_step === step.id },
      selected: sel.kind === 'step' && sel.id === step.id,
      deletable: o.editable,
    });
    danglingRoutes(def, step).forEach((route, i) => {
      const id = `__stub:${step.id}:${route}`;
      nodes.push({ id, type: 'stub', position: { x: p.x + NODE_W + 36, y: p.y + 6 + i * 26 }, data: { label: `${route} · ends` }, selectable: false, draggable: false, deletable: false, connectable: false });
      edges.push({ id: `${step.id}->${id}`, source: step.id, sourceHandle: 'side', target: id, type: 'route', selectable: false, deletable: false, data: { label: null, look: 'stub', error: false } });
    });
  }
  for (const id of startSteps(def)) {
    edges.push({ id: `${START_ID}->${id}`, source: START_ID, target: id, type: 'route', selectable: false, deletable: false, data: { label: null, look: o.run ? 'fired' : 'plain', error: false } });
  }
  def.edges.forEach((e, i) => {
    edges.push({
      id: edgeId(i),
      source: e.from,
      target: e.to,
      type: 'route',
      selected: sel.kind === 'edge' && sel.index === i,
      deletable: o.editable,
      data: { label: edgeLabel(e), look: o.run ? (o.run.fired.has(i) ? 'fired' : 'idle') : 'plain', error: (o.issues?.edges[i]?.length ?? 0) > 0 },
    });
  });
  return { nodes, edges };
}
