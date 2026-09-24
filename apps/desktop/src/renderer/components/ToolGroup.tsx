import type { ToolCallView } from '@desk/client';
import { summarizeToolArgs } from '@desk/protocol';
import { plural } from '../format';

/** "read_file ×2 · skill_run", in first-use order. */
export function toolNames(calls: ToolCallView[]): string {
  const counts = new Map<string, number>();
  for (const c of calls) counts.set(c.name, (counts.get(c.name) ?? 0) + 1);
  return [...counts].map(([n, k]) => (k > 1 ? `${n} ×${k}` : n)).join(' · ');
}

const LABEL: Record<ToolCallView['status'], string> = { running: 'running', ok: 'ok', error: 'error', denied: 'denied', interrupted: 'interrupted' };

export function ToolStatus({ status }: { status: ToolCallView['status'] }) {
  return (
    <span className={`tool-status tool-status-${status}`}>
      {status === 'running' ? <span className="live-dot" aria-hidden="true" /> : null}
      {LABEL[status]}
    </span>
  );
}

/** A collapsible group of tool calls (the Narrative depth). */
export function ToolGroup({ calls, title, defaultOpen = false }: { calls: ToolCallView[]; title?: string; defaultOpen?: boolean }) {
  const live = calls.some((c) => c.status === 'running');
  return (
    <details className="toolgroup" open={defaultOpen || undefined}>
      <summary>
        <span className="toolgroup-title">{title ?? `${live ? 'Using' : 'Used'} ${plural(calls.length, 'tool')}`}</span>
        <span className="toolgroup-names">{toolNames(calls)}</span>
      </summary>
      <ul>
        {calls.map((c) => (
          <li key={c.id}>
            <span className="mono grow">
              {c.name} <span className="muted">{summarizeToolArgs(c.arguments, 70)}</span>
            </span>
            <ToolStatus status={c.status} />
          </li>
        ))}
      </ul>
    </details>
  );
}
