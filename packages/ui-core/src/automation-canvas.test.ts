import { describe, expect, it } from 'vitest';
import {
  bezierPath,
  dotPattern,
  dropTarget,
  edgeGeometry,
  fitViewport,
  graphBounds,
  HANDLE_HALF,
  nodeSize,
  sourceAnchor,
  STUB_H,
  targetAnchor,
  toGraphPoint,
  wheelFactor,
  zoomAt,
} from './automation-canvas';
import { NODE_H, NODE_W, START_H, START_W, toGraph, type GraphNode } from './automation-graph';
import { digestDef } from './testing/automations';

const step = (id: string, x: number, y: number): GraphNode => ({ id, type: 'step', position: { x, y }, data: { step: digestDef().steps[0]!, kind: 'script', detail: '', errors: [], run: null, output: false } });

describe('the canvas geometry', () => {
  it('sizes each kind of node: steps and Start fixed, stubs by their label', () => {
    expect(nodeSize(step('a', 0, 0))).toEqual({ width: NODE_W, height: NODE_H });
    expect(nodeSize({ id: '__start', type: 'start', position: { x: 0, y: 0 }, data: { label: 'Run now only', errors: [] } })).toEqual({ width: START_W, height: START_H });
    const stub = nodeSize({ id: 's', type: 'stub', position: { x: 0, y: 0 }, data: { label: 'unchanged · ends' } });
    expect(stub.height).toBe(STUB_H);
    expect(stub.width).toBeGreaterThan(80);
  });

  it('anchors edges on the handles: out of the bottom (or the side, to a stub), into the top (or a stub’s left)', () => {
    const n = step('a', 100, 50);
    expect(sourceAnchor(n)).toEqual({ x: 100 + NODE_W / 2, y: 50 + NODE_H + HANDLE_HALF, side: 'bottom' });
    expect(sourceAnchor(n, 'side')).toEqual({ x: 100 + NODE_W + HANDLE_HALF, y: 50 + NODE_H / 2, side: 'right' });
    expect(targetAnchor(n)).toEqual({ x: 100 + NODE_W / 2, y: 50 - HANDLE_HALF, side: 'top' });
    expect(targetAnchor({ id: 's', type: 'stub', position: { x: 300, y: 56 }, data: { label: 'x · ends' } })).toEqual({ x: 300 - HANDLE_HALF, y: 56 + STUB_H / 2, side: 'left' });
  });

  it('draws React Flow’s bezier: half the gap forward, a curvature-scaled loop backward, and the label at t = 0.5', () => {
    expect(bezierPath({ x: 0, y: 0, side: 'bottom' }, { x: 100, y: 100, side: 'top' })).toEqual({ path: 'M0,0 C0,50 100,50 100,100', labelX: 50, labelY: 50 });
    expect(bezierPath({ x: 0, y: 100, side: 'bottom' }, { x: 0, y: 0, side: 'top' }).path).toBe('M0,100 C0,162.5 0,-62.5 0,0');
    expect(bezierPath({ x: 0, y: 0, side: 'right' }, { x: 40, y: 0, side: 'left' }).path).toBe('M0,0 C20,0 20,0 40,0');
  });

  it('computes an edge between two laid-out nodes, and nothing for a missing end', () => {
    const g = toGraph(digestDef(), {}, { startLabel: 's', selection: { kind: 'none' }, editable: true });
    const byId = new Map(g.nodes.map((n) => [n.id, n]));
    const e = g.edges.find((x) => x.source === 'fetch' && x.target === 'sum')!;
    const from = sourceAnchor(byId.get('fetch')!);
    expect(edgeGeometry(e, byId)!.path.startsWith(`M${from.x},${from.y} C`)).toBe(true);
    expect(edgeGeometry({ ...e, target: 'nope' }, byId)).toBeNull();
  });

  it('bounds the nodes and fits them like React Flow (padding 0.2, zoom capped at 1)', () => {
    expect(graphBounds([])).toEqual({ x: 0, y: 0, width: 0, height: 0 });
    const b = graphBounds([step('a', 0, 0), step('b', 300, 200)]);
    expect(b).toEqual({ x: 0, y: 0, width: 300 + NODE_W, height: 200 + NODE_H });
    expect(fitViewport({ x: 0, y: 0, width: 200, height: 64 }, 800, 600)).toEqual({ x: 300, y: 268, zoom: 1 });
    const wide = fitViewport({ x: 0, y: 0, width: 4000, height: 100 }, 800, 600);
    expect(wide.zoom).toBe(0.3);
  });

  it('zooms around a point, within the limits, and maps screen points back to the graph', () => {
    const v = zoomAt({ x: 0, y: 0, zoom: 1 }, 2, { x: 100, y: 100 }, 0.3, 1.5);
    expect(v.zoom).toBe(1.5);
    expect(toGraphPoint(v, { x: 100, y: 100 })).toEqual({ x: 100, y: 100 });
    const out = zoomAt({ x: 10, y: 20, zoom: 1 }, 0.5, { x: 0, y: 0 });
    expect(out).toEqual({ x: 5, y: 10, zoom: 0.5 });
    expect(zoomAt(out, 0.01, { x: 0, y: 0 }).zoom).toBe(0.3);
  });

  it('steps a wheel like d3-zoom: pixels, lines and pinch', () => {
    expect(wheelFactor(100, 0, false)).toBeCloseTo(2 ** -0.2);
    expect(wheelFactor(-3, 1, false)).toBeCloseTo(2 ** 0.15);
    expect(wheelFactor(10, 0, true)).toBeCloseTo(2 ** -0.2);
  });

  it('drops a connection on the nearest step’s top handle within the radius', () => {
    const nodes = [step('a', 0, 0), step('b', 0, 200), { id: '__start', type: 'start', position: { x: 0, y: -100 }, data: { label: 's', errors: [] } } as GraphNode];
    const top = targetAnchor(nodes[1]!);
    expect(dropTarget(nodes, { x: top.x + 5, y: top.y - 5 }, 20)).toBe('b');
    expect(dropTarget(nodes, { x: top.x + 50, y: top.y }, 20)).toBeNull();
    expect(dropTarget(nodes, targetAnchor(nodes[2]!), 20)).toBeNull();
  });

  it('scales the dot pattern with the zoom and offsets it with the pan', () => {
    expect(dotPattern({ x: 40, y: -10, zoom: 2 })).toEqual({ x: 4, y: -10, cell: 36, r: 1 });
  });
});
