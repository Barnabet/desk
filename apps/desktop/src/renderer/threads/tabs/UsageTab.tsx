import type { StoredEvent } from '@desk/protocol';
import { EmptyState } from '../../components/EmptyState';

export type ModelUsage = { model: string; prompt: number; completion: number; cached: number; estimated: boolean; runs: number };

/** Sums `usage` events by model, in first-use order. */
export function usageByModel(events: StoredEvent[], agentId: string): ModelUsage[] {
  const by = new Map<string, ModelUsage>();
  for (const e of events) {
    if (e.type !== 'usage' || e.agent_id !== agentId) continue;
    const u = by.get(e.payload.model) ?? { model: e.payload.model, prompt: 0, completion: 0, cached: 0, estimated: false, runs: 0 };
    u.prompt += e.payload.prompt_tokens;
    u.completion += e.payload.completion_tokens;
    u.cached += e.payload.cached_tokens ?? 0;
    u.estimated ||= e.payload.estimated;
    u.runs += 1;
    by.set(u.model, u);
  }
  return [...by.values()];
}

/** A token count: `@desk/ui-core`'s `tokens`, re-exported where the thread's tabs and System import it from. */
export { tokens } from '@desk/ui-core';

export function UsageTab({ usage }: { usage: ModelUsage[] }) {
  if (!usage.length) return <EmptyState title="No usage yet">Token counts appear after the thread's first model call.</EmptyState>;
  const total = usage.reduce((a, u) => ({ prompt: a.prompt + u.prompt, completion: a.completion + u.completion }), { prompt: 0, completion: 0 });
  return (
    <div className="tab-body">
      <table className="usage-table">
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col">Calls</th>
            <th scope="col">Prompt</th>
            <th scope="col">Cached</th>
            <th scope="col">Completion</th>
          </tr>
        </thead>
        <tbody>
          {usage.map((u) => (
            <tr key={u.model}>
              <td className="mono">{u.model}</td>
              <td>{u.runs}</td>
              <td>{u.prompt.toLocaleString()}</td>
              <td>{u.cached.toLocaleString()}</td>
              <td>{u.completion.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">Total</th>
            <td />
            <td>{total.prompt.toLocaleString()}</td>
            <td />
            <td>{total.completion.toLocaleString()}</td>
          </tr>
        </tfoot>
      </table>
      {usage.some((u) => u.estimated) ? <p className="muted small">Some counts are estimates; the model endpoint didn't report usage for every call.</p> : null}
    </div>
  );
}
