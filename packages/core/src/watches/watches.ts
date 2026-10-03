import { and, eq } from 'drizzle-orm';
import { quoteLines, sanitizeLabel, snippet, type StoredEvent } from '@desk/protocol';
import type { Db } from '../db/open';
import { watches } from '../db/schema';
import { ConflictError, NotFoundError, ValidationError } from '../errors';
import type { EventStore } from '../events/store';
import { newId } from '../ids';
import { getAgent, type AgentRow } from '../state/queries';

export type WatchRow = typeof watches.$inferSelect;

/** Open watches a project may have (spec 2026-10-03 §3). */
export const MAX_OPEN_WATCHES = 10;
const FINISHED = new Set(['done', 'failed', 'cancelled']);

export const openWatches = (db: Db, projectId: string): WatchRow[] =>
  db.select().from(watches).where(and(eq(watches.project_id, projectId), eq(watches.state, 'open'))).all();

const nameOf = (a: AgentRow | undefined, id: string) => (!a ? id : a.role === 'desk' ? 'Desk' : `"${sanitizeLabel(a.title ?? 'untitled')}" (${a.id})`);

/**
 * Desk's one-shot watches on threads (spec 2026-10-03 §3): the next message a watched thread sends another thread
 * (not Desk, whose messages already wake it) wakes Desk with a reminder. A watch ends when it fires, when Desk drops
 * it, or silently when its thread finishes or is archived.
 */
export class ThreadWatches {
  /** Thread ids with an open watch, so most events cost one lookup. */
  private watched = new Set<string>();
  private unsubscribe: (() => void) | undefined;

  constructor(
    private readonly d: {
      store: EventStore;
      /** A reminder to the project's Desk, which this wakes. */
      noteToDesk: (projectId: string, text: string) => void;
      onError: (err: unknown, context: string) => void;
    },
  ) {
    this.reload();
    this.unsubscribe = d.store.subscribe((item) => {
      if (item.kind !== 'event') return;
      const ev = item.event;
      if (!this.relevant(ev)) return;
      // After the append that called us has reached every listener: our own appends come after it.
      queueMicrotask(() => {
        try {
          this.onEvent(ev);
        } catch (err) {
          d.onError(err, `watch on event ${ev.id}`);
        }
      });
    });
  }

  close(): void {
    this.unsubscribe?.();
    this.unsubscribe = undefined;
  }

  private reload(): void {
    this.watched = new Set(this.d.store.db.select({ t: watches.thread_id }).from(watches).where(eq(watches.state, 'open')).all().map((r) => r.t));
  }

  private relevant(ev: StoredEvent): boolean {
    if (ev.type === 'message.agent') return this.watched.has(ev.payload.from_agent_id);
    if (ev.type === 'agent.status_changed' || ev.type === 'agent.archived') return !!ev.agent_id && this.watched.has(ev.agent_id);
    return false;
  }

  private onEvent(ev: StoredEvent): void {
    const db = this.d.store.db;
    if (ev.type === 'message.agent') {
      const from = ev.payload.from_agent_id;
      const to = ev.agent_id;
      if (!to || to === from) return;
      for (const w of this.openOn(from)) {
        if (to === w.desk_id) continue;
        if (w.match && !ev.payload.text.toLowerCase().includes(w.match.toLowerCase())) continue;
        this.end(w, 'fired', ev.id);
        const thread = getAgent(db, from);
        const recipient = getAgent(db, to);
        this.d.noteToDesk(
          w.project_id,
          [
            `Watch on ${nameOf(thread, from)}${w.match ? ` (matching ${snippet(w.match, 80)})` : ''}: it sent a ${ev.payload.kind} to ${nameOf(recipient, to)} (message #${ev.id}):`,
            quoteLines(ev.payload.text.length > 1500 ? `${ev.payload.text.slice(0, 1500)}…` : ev.payload.text),
            'The watch has ended; call watch_thread again to keep watching.',
          ].join('\n'),
        );
      }
      return;
    }
    if (ev.type === 'agent.archived' || (ev.type === 'agent.status_changed' && FINISHED.has(ev.payload.status))) {
      for (const w of this.openOn(ev.agent_id!)) this.end(w, 'thread_finished');
    }
  }

  private openOn(threadId: string): WatchRow[] {
    return this.d.store.db.select().from(watches).where(and(eq(watches.thread_id, threadId), eq(watches.state, 'open'))).all();
  }

  private end(w: WatchRow, reason: 'fired' | 'cancelled' | 'thread_finished', messageId?: number): void {
    this.d.store.append({ project_id: w.project_id, agent_id: w.desk_id, type: 'watch.ended', payload: { watch_id: w.id, reason, ...(messageId !== undefined ? { message_id: messageId } : {}) } });
    if (!this.openOn(w.thread_id).length) this.watched.delete(w.thread_id);
  }

  /** Watches a live thread for Desk; a watch already open on it is replaced. */
  set(deskId: string, thread: AgentRow, match?: string): WatchRow {
    const db = this.d.store.db;
    const desk = getAgent(db, deskId);
    if (!desk || desk.role !== 'desk') throw new ValidationError('Only Desk watches threads.');
    const name = nameOf(thread, thread.id);
    if (thread.project_id !== desk.project_id || thread.role !== 'thread') throw new NotFoundError(`Unknown thread: ${thread.id}`);
    if (thread.archived_at || FINISHED.has(thread.status)) throw new ConflictError(`${name} has finished (${thread.status}); there is nothing to watch.`);
    const previous = this.openOn(thread.id);
    if (!previous.length && openWatches(db, desk.project_id).length >= MAX_OPEN_WATCHES) {
      throw new ConflictError(`You already watch ${MAX_OPEN_WATCHES} threads; drop one with unwatch_thread first.`);
    }
    for (const w of previous) this.end(w, 'cancelled');
    const id = `wch_${newId()}`;
    const m = match?.trim();
    this.d.store.append({ project_id: desk.project_id, agent_id: deskId, type: 'watch.set', payload: { watch_id: id, thread_id: thread.id, ...(m ? { match: m } : {}) } });
    this.watched.add(thread.id);
    return db.select().from(watches).where(eq(watches.id, id)).get()!;
  }

  /** Drops Desk's open watch on a thread; false when there was none. */
  unset(deskId: string, threadId: string): boolean {
    const open = this.openOn(threadId).filter((w) => w.desk_id === deskId);
    for (const w of open) this.end(w, 'cancelled');
    return open.length > 0;
  }
}
