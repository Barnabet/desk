import { EventEmitter } from 'node:events';
import { and, asc, eq, gt, inArray, type SQL } from 'drizzle-orm';
import { EventBody, type EphemeralEvent, type EventInput, type EventType, type StoredEvent } from '@desk/protocol';
import type { Db } from '../db/open';
import { events } from '../db/schema';
import { applyProjections } from './projections';

export type StreamItem = { kind: 'event'; event: StoredEvent } | { kind: 'ephemeral'; event: EphemeralEvent };
export type ListQuery = { projectId?: string; agentId?: string; after?: number; types?: readonly EventType[]; limit?: number };

export class EventStore {
  private readonly emitter = new EventEmitter();

  constructor(
    readonly db: Db,
    private readonly now: () => Date = () => new Date(),
  ) {
    this.emitter.setMaxListeners(0);
  }

  /** Validates, persists and projects events atomically, then notifies subscribers. */
  append(input: EventInput | EventInput[]): StoredEvent[] {
    const inputs = Array.isArray(input) ? input : [input];
    const stored = this.db.transaction((tx) =>
      inputs.map((e) => {
        EventBody.parse({ type: e.type, payload: e.payload });
        const ts = this.now().toISOString();
        const row = tx
          .insert(events)
          .values({ project_id: e.project_id, agent_id: e.agent_id, type: e.type, payload: e.payload, ts })
          .returning({ id: events.id })
          .get();
        const ev = { ...e, id: row.id, ts } as StoredEvent;
        applyProjections(tx, ev);
        return ev;
      }),
    );
    for (const event of stored) this.emitter.emit('item', { kind: 'event', event } satisfies StreamItem);
    return stored;
  }

  /** Broadcasts a non-persisted event (e.g. streamed text deltas). */
  publishEphemeral(event: EphemeralEvent): void {
    this.emitter.emit('item', { kind: 'ephemeral', event } satisfies StreamItem);
  }

  list(q: ListQuery = {}): StoredEvent[] {
    const conds: SQL[] = [];
    if (q.projectId) conds.push(eq(events.project_id, q.projectId));
    if (q.agentId) conds.push(eq(events.agent_id, q.agentId));
    if (q.after !== undefined) conds.push(gt(events.id, q.after));
    if (q.types) conds.push(inArray(events.type, [...q.types]));
    let query = this.db.select().from(events).where(and(...conds)).orderBy(asc(events.id)).$dynamic();
    if (q.limit) query = query.limit(q.limit);
    return query.all().map(
      (row) => ({ id: row.id, project_id: row.project_id, agent_id: row.agent_id, type: row.type, payload: row.payload, ts: row.ts }) as StoredEvent,
    );
  }

  /** Listeners run synchronously after commit and must not throw. */
  subscribe(listener: (item: StreamItem) => void): () => void {
    this.emitter.on('item', listener);
    return () => this.emitter.off('item', listener);
  }
}
