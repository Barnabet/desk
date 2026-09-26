import { DestroyRef, Injectable, InjectionToken, computed, effect, inject, untracked, type Signal } from '@angular/core';
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
import type { GlobalState } from '@desk/bff/contract';
import type { EphemeralEvent, StoredEvent } from '@desk/protocol';
import { createStore, type Store } from '@desk/ui-core';
import { DeskBridge, DeskCallError } from './desk-bridge';
import { fromStore } from './store-signal';

export type SessionState = {
  status: 'loading' | 'ready' | 'missing' | 'error';
  error: string | null;
  project: ProjectState | null;
  chat: ChatState;
  timeline: TimelineState;
  /**
   * The project's messages, folded over the full backfill like chat and timeline: the agent directory (archived threads
   * included), question states and answer runs. "Answering" and "waiting on" come from here, never from the overview,
   * so they survive reloads.
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

/** How long a session outlives its last viewer (switching tabs keeps it warm). Specs provide 0. */
export const SESSION_RELEASE_DELAY = new InjectionToken<number>('SESSION_RELEASE_DELAY', { providedIn: 'root', factory: () => 30_000 });

type Stream = { runId: string; text: string };

/** One agent's transcript, folded from the session log plus its live stream (the desktop's useTranscript). */
export function transcriptOf(events: readonly StoredEvent[], stream: Stream | undefined, projectId: string, agentId: string): TranscriptState {
  let t = emptyTranscript(agentId);
  for (const e of events) if (e.agent_id === agentId) t = reduceTranscript(t, e);
  return stream ? applyTranscriptDelta(t, { type: 'assistant.delta', project_id: projectId, agent_id: agentId, payload: { run_id: stream.runId, text: stream.text } }) : t;
}

class ProjectSession {
  readonly store: Store<SessionState> = createStore(initial());
  readonly state: Signal<SessionState> = fromStore(this.store);
  private readonly transcripts = new Map<string, Signal<TranscriptState>>();
  private refs = 0;
  private releaseTimer: ReturnType<typeof setTimeout> | undefined;
  private queue: StoredEvent[] = [];
  private scheduled = false;
  private started = false;
  /** Set once the backfill has arrived: until then events only queue, so the history renders once, not event by event. */
  private synced = false;
  /** The highest event id received: where a watch after a reconnect resumes. */
  private cursor = 0;
  private base: Pick<SessionState, 'project' | 'chat' | 'timeline' | 'messages'> | null = null;
  /** Set while a watch after a reconnect has failed (deskd was offline): `retryWatch` tries again. */
  private watchFailed = false;
  /** Counts rewatches, so only the latest one's outcome sets or clears `watchFailed`. */
  private watches = 0;

  constructor(
    readonly projectId: string,
    private readonly bridge: DeskBridge,
    private readonly releaseDelayMs: number,
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
      void this.bridge.call('broker.unwatch', { projectId: this.projectId }).catch(() => {});
      this.onDispose(this);
    }, this.releaseDelayMs);
  }

  dispose(): void {
    clearTimeout(this.releaseTimer);
  }

  /**
   * After /push reconnects (a new broker sender), watch again from the last event this session received. The broker cannot
   * register a watch while deskd is offline, so a failed one is remembered for `retryWatch`.
   */
  rewatch(): void {
    if (!this.synced) return;
    const n = ++this.watches;
    this.bridge.call('broker.watch', { projectId: this.projectId, afterSeq: this.cursor }).then(
      () => {
        if (n === this.watches) this.watchFailed = false;
      },
      () => {
        if (n === this.watches) this.watchFailed = true;
      },
    );
  }

  /** Watches again if the last rewatch failed (called when deskd is live again). */
  retryWatch(): void {
    if (this.watchFailed) this.rewatch();
  }

  transcript(agentId: string): Signal<TranscriptState> {
    let t = this.transcripts.get(agentId);
    if (!t) {
      const events = computed(() => this.state().events);
      const stream = computed(() => this.state().streams[agentId]);
      t = computed(() => transcriptOf(events(), stream(), this.projectId, agentId));
      this.transcripts.set(agentId, t);
    }
    return t;
  }

  private async load(): Promise<void> {
    this.started = true;
    try {
      const overview = await this.bridge.call('projects.get', { id: this.projectId });
      const deskId = overview.desk?.id ?? '';
      this.base = { project: projectFromOverview(overview), chat: emptyChat(deskId), timeline: emptyTimeline(deskId), messages: emptyMessages() };
      await this.bridge.call('broker.watch', { projectId: this.projectId, afterSeq: 0 });
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
    for (const e of events) {
      this.queue.push(e);
      if (e.id > this.cursor) this.cursor = e.id;
    }
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

/**
 * Every open project's session (renderer/state/session.ts). Constructing it starts routing pushed events to their project's
 * session; a session loads on its first acquire and unwatches SESSION_RELEASE_DELAY after its last release.
 */
@Injectable({ providedIn: 'root' })
export class SessionService {
  private readonly bridge = inject(DeskBridge);
  private readonly releaseDelayMs = inject(SESSION_RELEASE_DELAY);
  private readonly sessions = new Map<string, ProjectSession>();
  private connection: GlobalState['connection']['status'] | null = null;

  constructor() {
    const offs = [
      this.bridge.onPush<StoredEvent>('desk:event', (e) => this.sessions.get(e.project_id)?.enqueue(e)),
      this.bridge.onPush<StoredEvent[]>('desk:events', (batch) => {
        const first = batch[0];
        if (first) this.sessions.get(first.project_id)?.enqueueMany(batch);
      }),
      this.bridge.onPush<EphemeralEvent>('desk:ephemeral', (e) => this.sessions.get(e.project_id)?.onDelta(e)),
      this.bridge.onReconnect(() => {
        for (const s of this.sessions.values()) s.rewatch();
      }),
      // A rewatch fails while deskd is offline; once desk:global reports deskd live again, those sessions watch again.
      this.bridge.onPush<GlobalState>('desk:global', (g) => {
        const was = this.connection;
        this.connection = g.connection.status;
        if (this.connection === 'live' && was !== 'live') for (const s of this.sessions.values()) s.retryWatch();
      }),
    ];
    inject(DestroyRef).onDestroy(() => {
      for (const off of offs) off();
      for (const s of this.sessions.values()) s.dispose();
      this.sessions.clear();
    });
  }

  acquire(projectId: string): void {
    this.sessionFor(projectId).acquire();
  }

  release(projectId: string): void {
    this.sessions.get(projectId)?.release();
  }

  state(projectId: string): Signal<SessionState> {
    return this.sessionFor(projectId).state;
  }

  /** One agent's transcript: recomputed only when the session's event log or that agent's stream changes. */
  transcript(projectId: string, agentId: string): Signal<TranscriptState> {
    return this.sessionFor(projectId).transcript(agentId);
  }

  private sessionFor(projectId: string): ProjectSession {
    let s = this.sessions.get(projectId);
    if (!s) {
      s = new ProjectSession(projectId, this.bridge, this.releaseDelayMs, (done) => {
        if (this.sessions.get(projectId) === done) this.sessions.delete(projectId);
      });
      this.sessions.set(projectId, s);
    }
    return s;
  }
}

/**
 * The desktop's useSession: keeps the project's session acquired while the calling component lives, following `projectId`
 * (an input signal), and returns its state. Call it in a field initializer.
 */
export function injectSession(projectId: () => string): Signal<SessionState> {
  const sessions = inject(SessionService);
  effect((onCleanup) => {
    const id = projectId();
    untracked(() => sessions.acquire(id));
    onCleanup(() => sessions.release(id));
  });
  return computed(() => sessions.state(projectId())());
}
