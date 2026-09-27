import { NODE_H, NODE_W, START_H, START_W, type GraphEdge, type GraphNode } from './automation-graph';

/** The canvas's pan and zoom: a graph point p shows at (p.x × zoom + x, p.y × zoom + y). */
export type Viewport = { x: number; y: number; zoom: number };
export type Point = { x: number; y: number };
export type Rect = { x: number; y: number; width: number; height: number };
export type Side = 'top' | 'bottom' | 'left' | 'right';
/** Where an edge leaves or meets a node: the handle's outer edge, and the side it sits on. */
export type Anchor = Point & { side: Side };
export type EdgeGeometry = { path: string; labelX: number; labelY: number };

export const MIN_ZOOM = 0.3;
export const MAX_ZOOM = 1.5;
/** A route stub's height (automations.css: 10px mono, 2px padding, 1px border); its width follows its text. */
export const STUB_H = 18;
/** Half a handle's box (9px plus a 1.5px border each side): React Flow's edges end on the handle's outer edge. */
export const HANDLE_HALF = 6;
/** How near a target handle a dropped connection must land, in screen pixels (React Flow's connectionRadius). */
export const CONNECT_RADIUS = 20;
/** The width of one 10px mono character, for a stub's box in the canvas bounds. */
const STUB_CHAR_W = 6.2;

const between = (v: number, min: number, max: number): number => Math.min(max, Math.max(min, v));

/** A node's box. Steps and Start have fixed sizes (automations.css); a stub is as wide as its label. */
export function nodeSize(n: GraphNode): { width: number; height: number } {
  switch (n.type) {
    case 'step':
      return { width: NODE_W, height: NODE_H };
    case 'start':
      return { width: START_W, height: START_H };
    case 'stub':
      return { width: Math.ceil(n.data.label.length * STUB_CHAR_W) + 18, height: STUB_H };
  }
}

/** Where an edge leaves a node: its bottom handle, or its side handle (the edge to a route stub). */
export function sourceAnchor(n: GraphNode, handle?: string): Anchor {
  const { width, height } = nodeSize(n);
  if (handle === 'side') return { x: n.position.x + width + HANDLE_HALF, y: n.position.y + height / 2, side: 'right' };
  return { x: n.position.x + width / 2, y: n.position.y + height + HANDLE_HALF, side: 'bottom' };
}

/** Where an edge meets a node: its top handle, or a stub's left one. */
export function targetAnchor(n: GraphNode): Anchor {
  const { width, height } = nodeSize(n);
  if (n.type === 'stub') return { x: n.position.x - HANDLE_HALF, y: n.position.y + height / 2, side: 'left' };
  return { x: n.position.x + width / 2, y: n.position.y - HANDLE_HALF, side: 'top' };
}

const controlOffset = (distance: number, curvature: number): number => (distance >= 0 ? 0.5 * distance : curvature * 25 * Math.sqrt(-distance));

function control(a: Anchor, b: Point, curvature: number): Point {
  switch (a.side) {
    case 'left':
      return { x: a.x - controlOffset(a.x - b.x, curvature), y: a.y };
    case 'right':
      return { x: a.x + controlOffset(b.x - a.x, curvature), y: a.y };
    case 'top':
      return { x: a.x, y: a.y - controlOffset(a.y - b.y, curvature) };
    case 'bottom':
      return { x: a.x, y: a.y + controlOffset(b.y - a.y, curvature) };
  }
}

/** React Flow's bezier edge (`getBezierPath`, curvature 0.25): the SVG path, and its point at t = 0.5 for the label. */
export function bezierPath(s: Anchor, t: Anchor, curvature = 0.25): EdgeGeometry {
  const sc = control(s, t, curvature);
  const tc = control(t, s, curvature);
  return {
    path: `M${s.x},${s.y} C${sc.x},${sc.y} ${tc.x},${tc.y} ${t.x},${t.y}`,
    labelX: s.x * 0.125 + sc.x * 0.375 + tc.x * 0.375 + t.x * 0.125,
    labelY: s.y * 0.125 + sc.y * 0.375 + tc.y * 0.375 + t.y * 0.125,
  };
}

/** An edge between its nodes' handles, or null when a node is missing. */
export function edgeGeometry(e: GraphEdge, byId: ReadonlyMap<string, GraphNode>): EdgeGeometry | null {
  const s = byId.get(e.source);
  const t = byId.get(e.target);
  return s && t ? bezierPath(sourceAnchor(s, e.sourceHandle), targetAnchor(t)) : null;
}

/** The box around every node (all zeros for none). */
export function graphBounds(nodes: readonly GraphNode[]): Rect {
  if (!nodes.length) return { x: 0, y: 0, width: 0, height: 0 };
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const n of nodes) {
    const { width, height } = nodeSize(n);
    x0 = Math.min(x0, n.position.x);
    y0 = Math.min(y0, n.position.y);
    x1 = Math.max(x1, n.position.x + width);
    y1 = Math.max(y1, n.position.y + height);
  }
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

/** React Flow's fitView (`getViewportForBounds` with a numeric padding): the bounds centred, zoomed to fit within the limits. */
export function fitViewport(bounds: Rect, width: number, height: number, o: { padding?: number; minZoom?: number; maxZoom?: number } = {}): Viewport {
  const padding = o.padding ?? 0.2;
  const px = Math.floor((width - width / (1 + padding)) * 0.5);
  const py = Math.floor((height - height / (1 + padding)) * 0.5);
  const fit = Math.min((width - 2 * px) / Math.max(bounds.width, 1), (height - 2 * py) / Math.max(bounds.height, 1));
  const zoom = between(fit, o.minZoom ?? MIN_ZOOM, o.maxZoom ?? 1);
  return { x: width / 2 - (bounds.x + bounds.width / 2) * zoom, y: height / 2 - (bounds.y + bounds.height / 2) * zoom, zoom };
}

/** A new zoom (clamped) that keeps the graph point under `at` (screen, relative to the canvas) where it is. */
export function zoomAt(v: Viewport, zoom: number, at: Point, min = MIN_ZOOM, max = MAX_ZOOM): Viewport {
  const z = between(zoom, min, max);
  const k = z / v.zoom;
  return { zoom: z, x: at.x - (at.x - v.x) * k, y: at.y - (at.y - v.y) * k };
}

/** d3-zoom's wheel step, which React Flow uses: the zoom factor for one wheel event (ctrl is a trackpad pinch). */
export function wheelFactor(deltaY: number, deltaMode: number, ctrlKey: boolean): number {
  return 2 ** (-deltaY * (deltaMode === 1 ? 0.05 : deltaMode ? 1 : 0.002) * (ctrlKey ? 10 : 1));
}

/** A screen point (relative to the canvas) in graph coordinates. */
export function toGraphPoint(v: Viewport, p: Point): Point {
  return { x: (p.x - v.x) / v.zoom, y: (p.y - v.y) / v.zoom };
}

/** The step whose top handle is nearest `p` (graph coordinates) within `radius`, or null: where a dragged connection lands. */
export function dropTarget(nodes: readonly GraphNode[], p: Point, radius: number): string | null {
  let best: { id: string; d: number } | null = null;
  for (const n of nodes) {
    if (n.type !== 'step') continue;
    const a = targetAnchor(n);
    const d = Math.hypot(a.x - p.x, a.y - p.y);
    if (d <= radius && (!best || d < best.d)) best = { id: n.id, d };
  }
  return best?.id ?? null;
}

/** React Flow's dot background (gap 18, size 1) on screen: the pattern's offset and cell, and the dot's radius. */
export function dotPattern(v: Viewport, gap = 18, size = 1): { x: number; y: number; cell: number; r: number } {
  const cell = gap * v.zoom;
  return { x: v.x % cell, y: v.y % cell, cell, r: (size * v.zoom) / 2 };
}
