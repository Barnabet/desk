import { BaseEdge, EdgeLabelRenderer, getBezierPath, Handle, Position, type EdgeProps, type EdgeTypes, type NodeProps, type NodeTypes } from '@xyflow/react';
import type { Step } from '@desk/protocol';
import type { FlowEdge, StartFlowNode, StepFlowNode, StubFlowNode } from './flow';

/** Each kind's colour stripe (automations.css maps these to tokens). */
const KIND_CLASS: Record<Step['kind'], string> = { script: 'k-script', agent: 'k-agent', ask: 'k-ask', wait: 'k-wait', automation: 'k-automation', tell_desk: 'k-tell' };

/** A step (mockup 2): kind, title and a one-line detail; red when validation found a problem; lit with its state in a run. */
export function StepNode({ data, selected }: NodeProps<StepFlowNode>) {
  const { step, run } = data;
  const cls = ['auto-node', KIND_CLASS[step.kind], selected ? 'selected' : '', data.errors.length ? 'invalid' : '', run ? `run-${run.tone}` : ''].filter(Boolean).join(' ');
  return (
    <div className={cls} data-testid={`node-${step.id}`} aria-label={`${data.kind} step: ${step.title}`} title={data.errors.length ? data.errors.join('\n') : undefined}>
      <Handle type="target" position={Position.Top} />
      <div className="auto-node-kind">
        <span>{data.kind}</span>
        {run ? <b>{run.badge}</b> : data.output ? <b>result</b> : null}
      </div>
      <div className="auto-node-title">{step.title}</div>
      <div className="auto-node-detail">{run?.detail ?? data.detail}</div>
      <Handle type="source" position={Position.Bottom} />
      <Handle type="source" id="side" position={Position.Right} isConnectable={false} className="auto-handle-side" />
    </div>
  );
}

/** The Start pill: schedules and Run now. Clicking it edits schedules and inputs. */
export function StartNode({ data, selected }: NodeProps<StartFlowNode>) {
  return (
    <div className={`auto-start${selected ? ' selected' : ''}${data.errors.length ? ' invalid' : ''}`} data-testid="node-start" aria-label={`Start: ${data.label}`} title={data.errors.length ? data.errors.join('\n') : undefined}>
      <span aria-hidden="true">▶</span>
      <span className="auto-start-label">{data.label}</span>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

/** A declared route no edge takes: "<route> · ends". */
export function StubNode({ data }: NodeProps<StubFlowNode>) {
  return (
    <div className="auto-stub">
      <Handle type="target" position={Position.Left} isConnectable={false} />
      {data.label}
    </div>
  );
}

/** An edge with its route and condition as a label. Clicks go through the label to the edge. */
export function RouteEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected, markerEnd }: EdgeProps<FlowEdge>) {
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition });
  const look = data?.look ?? 'plain';
  const state = `${selected ? ' selected' : ''}${data?.error ? ' invalid' : ''}`;
  return (
    <>
      <BaseEdge id={id} path={path} {...(markerEnd ? { markerEnd } : {})} className={`auto-edge look-${look}${state}`} />
      {data?.label ? (
        <EdgeLabelRenderer>
          <div className={`auto-edge-label${state}`} style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}>
            {data.label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

export const nodeTypes: NodeTypes = { step: StepNode, start: StartNode, stub: StubNode } as NodeTypes;
export const edgeTypes: EdgeTypes = { route: RouteEdge } as EdgeTypes;
