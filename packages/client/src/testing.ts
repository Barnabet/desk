import type { EventOf, EventType } from '@desk/protocol';

/** Builds a stored event for reducer tests. Timestamps advance one second per id from 10:00:00Z. */
export function ev<T extends EventType>(
  id: number,
  type: T,
  payload: EventOf<T>['payload'],
  opts: { agent?: string | null; project?: string; ts?: string } = {},
): EventOf<T> {
  const ts = opts.ts ?? new Date(Date.UTC(2026, 8, 24, 10, 0, id)).toISOString();
  return { id, project_id: opts.project ?? 'p', agent_id: opts.agent ?? null, ts, type, payload } as unknown as EventOf<T>;
}
