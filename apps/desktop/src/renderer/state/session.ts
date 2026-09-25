import { useEffect, useMemo } from 'react';
import {
  applyChatDelta,
  applyTranscriptDelta,
  emptyChat,
  emptyMessages,
  emptyTimeline,
  emptyTranscript,
  foldMessages,
  projectFromOverview,
  reduceChat,
  reduceProject,
  reduceTimeline,
  reduceTranscript,
  type ChatState,
  type MessagesState,
  type ProjectState,
  type TimelineState,
  type TranscriptState,
} from '@desk/client';
import type { EphemeralEvent, StoredEvent } from '@desk/protocol';
import { createStore, type Store } from '@desk/ui-core';
import { call, DeskCallError, onPush } from '../bridge';
import { useStore } from '../store';

export type SessionState = {
  status: 'loading' | 'ready' | 'missing' | 'error';
  error: string | null;
  project: ProjectState | null;
  chat: ChatState;
  timeline: TimelineState;
  /**
   * The project's messages, folded over the full backfill like chat and timeline: the agent directory (archived threads
   * included), question states and answer runs. "Answering" and "waiting on" come from here, never from the overview,
   * so they survive reloads (design spec §1.4).
   */
  messages: MessagesState;
  /** The project's full event log, in id order. */
  events: StoredEvent[];
  /** Live streamed text per agent (ephemeral), until its assistant.message lands. */
  streams: Record<string, { runId: string; text: string }>;
};

const initial = (): SessionState => ({
  status: 'loading',
  error: null,
  project: null,
  chat: emptyChat(''),
  timeline: emptyTimeline(''),
  messages: emptyMessages(),
  events: [],
  streams: {},
});

let releaseDelayMs = 30_000;
/** How long a session outlives its last viewer (switching tabs keeps it warm). */
export const setReleaseDelay = (ms: number): void => {
  releaseDelayMs = ms;
};

class ProjectSession {
  readonly store: Store<SessionState> = createStore(initial());
  private refs = 0;
  private releaseTimer: ReturnType<typeof setTimeout> | undefined;
  private queue: StoredEvent[] = [];
  private scheduled = false;
  private started = false;
  /** Set once the backfill has arrived: until then events only queue, so the history renders once, not event by event. */
  private synced = false;
  private base: Pick<SessionState, 'project' | 'chat' | 'timeline' | 'messages'> | null = null;

  constructor(
    readonly projectId: string,
    private readonly onDispose: (s: ProjectSession) => void,
  ) {}

  acquire(): void {
    this.refs++;
    clearTimeout(this.releaseTimer);
    if (!this.started) void this.load();
  }

  release(): void {
    this.refs = Math.max(0, this.refs - 1);
    if (this.refs) return;
    clearTimeout(this.releaseTimer);
    this.releaseTimer = setTimeout(() => {
      void call('broker.unwatch', { projectId: this.projectId }).catch(() => {});
      this.onDispose(this);
    }, releaseDelayMs);
  }

  private async load(): Promise<void> {
    this.started = true;
    try {
      const overview = await call('projects.get', { id: this.projectId });
      const deskId = overview.desk?.id ?? '';
      this.base = { project: projectFromOverview(overview), chat: emptyChat(deskId), timeline: emptyTimeline(deskId), messages: emptyMessages() };
      await call('broker.watch', { projectId: this.projectId, afterSeq: 0 });
      // The backfill (pushed before watch resolves) and the overview become visible in a single update.
      const base = this.base;
      this.synced = true;
      this.store.set((s) => this.apply({ ...s, ...base, status: 'ready', error: null }));
    } catch (err) {
      this.started = false;
      this.synced = false;
      const missing = err instanceof DeskCallError && err.status === 404;
      this.store.set((s) => ({ ...s, status: missing ? 'missing' : 'error', error: err instanceof Error ? err.message : String(err) }));
    }
  }

  enqueue(e: StoredEvent): void {
    this.enqueueMany([e]);
  }

  enqueueMany(events: StoredEvent[]): void {
    for (const e of events) this.queue.push(e);
    if (this.scheduled) return;
    this.scheduled = true;
    queueMicrotask(() => {
      this.scheduled = false;
      this.flush();
    });
  }

  /** Applies queued events in one store update (a backfill arrives as one burst). */
  private flush(): void {
    if (!this.synced || this.store.get().status !== 'ready' || !this.queue.length) return;
    this.store.set((s) => this.apply(s));
  }

  /** Folds the queued events into a state. */
  private apply(s: SessionState): SessionState {
    const batch = this.queue;
    this.queue = [];
    let { project, chat, timeline, streams } = s;
    const fresh: StoredEvent[] = [];
    let last = s.events.at(-1)?.id ?? 0;
    for (const e of batch) {
      if (e.id <= last) continue;
      last = e.id;
      fresh.push(e);
      if (project) project = reduceProject(project, e);
      chat = reduceChat(chat, e);
      timeline = reduceTimeline(timeline, e);
      if ((e.type === 'assistant.message' || e.type === 'run.finished') && e.agent_id && streams[e.agent_id]?.runId === e.payload.run_id) {
        const { [e.agent_id]: _done, ...rest } = streams;
        streams = rest;
      }
    }
    return fresh.length ? { ...s, project, chat, timeline, streams, messages: foldMessages(fresh, s.messages), events: s.events.concat(fresh) } : s;
  }

  onDelta(e: EphemeralEvent): void {
    if (e.type !== 'assistant.delta' || this.store.get().status !== 'ready') return;
    this.store.set((s) => {
      const prev = s.streams[e.agent_id];
      const text = prev?.runId === e.payload.run_id ? prev.text + e.payload.text : e.payload.text;
      return { ...s, chat: applyChatDelta(s.chat, e), streams: { ...s.streams, [e.agent_id]: { runId: e.payload.run_id, text } } };
    });
  }
}

const sessions = new Map<string, ProjectSession>();
let stopRouting: (() => void) | null = null;

/** Routes pushed events to their project's session. Call once at startup. */
export function startSessionRouting(): () => void {
  if (stopRouting) return stopRouting;
  const offEvent = onPush<StoredEvent>('desk:event', (e) => sessions.get(e.project_id)?.enqueue(e));
  const offBatch = onPush<StoredEvent[]>('desk:events', (batch) => batch[0] && sessions.get(batch[0].project_id)?.enqueueMany(batch));
  const offDelta = onPush<EphemeralEvent>('desk:ephemeral', (e) => sessions.get(e.project_id)?.onDelta(e));
  stopRouting = () => {
    offEvent();
    offBatch();
    offDelta();
    stopRouting = null;
  };
  return stopRouting;
}

function sessionFor(projectId: string): ProjectSession {
  let s = sessions.get(projectId);
  if (!s) {
    s = new ProjectSession(projectId, (done) => {
      if (sessions.get(projectId) === done) sessions.delete(projectId);
    });
    sessions.set(projectId, s);
  }
  return s;
}

export function useSession(projectId: string): SessionState {
  const session = useMemo(() => sessionFor(projectId), [projectId]);
  useEffect(() => {
    session.acquire();
    return () => session.release();
  }, [session]);
  return useStore(session.store, (s) => s);
}

/** One agent's transcript, folded from the session log plus its live stream. */
export function useTranscript(s: SessionState, projectId: string, agentId: string): TranscriptState {
  const stream = s.streams[agentId];
  return useMemo(() => {
    let t = emptyTranscript(agentId);
    for (const e of s.events) if (e.agent_id === agentId) t = reduceTranscript(t, e);
    return stream ? applyTranscriptDelta(t, { type: 'assistant.delta', project_id: projectId, agent_id: agentId, payload: { run_id: stream.runId, text: stream.text } }) : t;
  }, [s.events, stream, agentId, projectId]);
}

export function resetSessions(): void {
  sessions.clear();
  stopRouting?.();
}
