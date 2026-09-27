import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { applyNodeChanges, Background, Controls, ReactFlow, ReactFlowProvider, type Connection, type Edge, type NodeChange } from '@xyflow/react';
import type { AutomationDefinition, AutomationLayout } from '@desk/protocol';
import { canConnect, edgeIndex, toGraph, type GraphIssues, type GraphRun, type GraphSelection } from '@desk/ui-core';
import type { FlowEdge, FlowNode } from './flow';
import { edgeTypes, nodeTypes } from './nodes';

export type CanvasProps = {
  def: AutomationDefinition;
  layout: AutomationLayout;
  startLabel: string;
  selection: GraphSelection;
  onSelect(sel: GraphSelection): void;
  issues?: GraphIssues;
  run?: GraphRun;
  editable: boolean;
  /** Positions of nodes the user finished dragging. */
  onMove?(positions: AutomationLayout): void;
  onConnect?(from: string, to: string): void;
  /** Delete or Backspace on the selection: steps and edges (by index) of the same definition. */
  onDelete?(sel: { steps: string[]; edges: number[] }): void;
};

/** Keeps React Flow's own node fields (measured size, an ongoing drag) when nodes are derived again from the draft. */
function mergeNodes(prev: FlowNode[], next: FlowNode[]): FlowNode[] {
  const byId = new Map(prev.map((n) => [n.id, n]));
  return next.map((n) => {
    const p = byId.get(n.id);
    if (!p) return n;
    return { ...n, ...(p.measured ? { measured: p.measured } : {}), ...(p.dragging ? { position: p.position, dragging: true } : {}) } as FlowNode;
  });
}

function Canvas(o: CanvasProps) {
  const props = useRef(o);
  props.current = o;
  const graph = useMemo(
    () => toGraph(o.def, o.layout, { startLabel: o.startLabel, selection: o.selection, editable: o.editable, ...(o.issues ? { issues: o.issues } : {}), ...(o.run ? { run: o.run } : {}) }),
    [o.def, o.layout, o.startLabel, o.selection, o.editable, o.issues, o.run],
  );
  const flowNodes = graph.nodes as FlowNode[];
  const flowEdges = graph.edges as FlowEdge[];
  const [nodes, setNodes] = useState<FlowNode[]>(flowNodes);
  useEffect(() => setNodes((prev) => mergeNodes(prev, flowNodes)), [flowNodes]);

  const onNodesChange = useCallback((changes: NodeChange<FlowNode>[]) => setNodes((ns) => applyNodeChanges(changes, ns)), []);
  const onNodeDragStop = useCallback((_e: unknown, _node: FlowNode, dragged: FlowNode[]) => {
    const moved = dragged.filter((n) => n.type !== 'stub');
    if (moved.length) props.current.onMove?.(Object.fromEntries(moved.map((n) => [n.id, { x: Math.round(n.position.x), y: Math.round(n.position.y) }])));
  }, []);
  const onNodeClick = useCallback((_e: unknown, n: FlowNode) => {
    if (n.type === 'start') props.current.onSelect({ kind: 'start' });
    else if (n.type === 'step') props.current.onSelect({ kind: 'step', id: n.id });
  }, []);
  const onEdgeClick = useCallback((_e: unknown, e: FlowEdge) => {
    const index = edgeIndex(e.id);
    if (index !== null) props.current.onSelect({ kind: 'edge', index });
  }, []);
  const onPaneClick = useCallback(() => props.current.onSelect({ kind: 'none' }), []);
  const onConnect = useCallback((c: Connection) => {
    if (c.source && c.target) props.current.onConnect?.(c.source, c.target);
  }, []);
  const isValidConnection = useCallback((c: Edge | Connection) => !!c.source && !!c.target && canConnect(props.current.def, c.source, c.target) === null, []);
  const onDelete = useCallback(({ nodes: gone, edges: cut }: { nodes: FlowNode[]; edges: FlowEdge[] }) => {
    const steps = gone.filter((n) => n.type === 'step').map((n) => n.id);
    const edges = cut.map((e) => edgeIndex(e.id)).filter((i): i is number => i !== null);
    if (steps.length || edges.length) props.current.onDelete?.({ steps, edges });
  }, []);

  return (
    <ReactFlow<FlowNode, FlowEdge>
      nodes={nodes}
      edges={flowEdges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      colorMode="system"
      onNodesChange={onNodesChange}
      onNodeDragStop={onNodeDragStop}
      onNodeClick={onNodeClick}
      onEdgeClick={onEdgeClick}
      onPaneClick={onPaneClick}
      onConnect={onConnect}
      isValidConnection={isValidConnection}
      onDelete={onDelete}
      nodesDraggable={o.editable}
      nodesConnectable={o.editable}
      elementsSelectable
      deleteKeyCode={o.editable ? ['Backspace', 'Delete'] : null}
      fitView
      fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
      minZoom={0.3}
      maxZoom={1.5}
    >
      <Background gap={18} size={1} color="var(--rule)" />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

/** The automation graph, top to bottom (spec §8.2): editable in Design, read-only and lit in a run. */
export function GraphCanvas(o: CanvasProps) {
  return (
    <div className="auto-canvas">
      <ReactFlowProvider>
        <Canvas {...o} />
      </ReactFlowProvider>
    </div>
  );
}
