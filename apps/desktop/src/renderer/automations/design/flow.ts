import type { Edge, Node } from '@xyflow/react';
import type { RouteEdgeData, StartNodeData, StepNodeData, StubNodeData } from '@desk/ui-core';

// React Flow's view of the ui-core graph: `toGraph`'s nodes and edges already have these shapes.
export type StepFlowNode = Node<StepNodeData, 'step'>;
export type StartFlowNode = Node<StartNodeData, 'start'>;
export type StubFlowNode = Node<StubNodeData, 'stub'>;
export type FlowNode = StepFlowNode | StartFlowNode | StubFlowNode;
export type FlowEdge = Edge<RouteEdgeData, 'route'>;
