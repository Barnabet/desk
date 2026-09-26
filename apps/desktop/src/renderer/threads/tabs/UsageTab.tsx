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

/** A token count in at most ~3 significant digits: 236, 7.7k, 402M, 3.2B. */
export function tokens(n: number): string {
  if (n < 1000) return String(n);
  const units: Array<[number, string]> = [
    [1e3, 'k'],
    [1e6, 'M'],
    [1e9, 'B'],
  ];
  for (const [i, [size, unit]] of units.entries()) {
    const v = n / size;
    const text = v < 9.95 ? v.toFixed(1) : String(Math.round(v));
    // 999_700 rounds to "1000k"; the next unit says it better.
    if (Number(text) < 1000 || i === units.length - 1) return `${text}${unit}`;
  }
  return String(n);
}

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
