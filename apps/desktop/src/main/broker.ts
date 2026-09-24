import { affectsAttention, affectsOverview, DaemonNotRunning, DeskStream, ProtocolMismatch, reduceSystem, systemFromHealth, type Credentials, type DeskClient, type StreamOptions } from '@desk/client';
import { checkDaemon } from '@desk/client/node';
import type { AttentionItem, EphemeralEvent, StoredEvent } from '@desk/protocol';
import type { PushChannel } from '../shared/channels';
import { initialGlobalState, runtimeKey, type GlobalState } from '../shared/state';

type StreamLike = { start(): void; close(): void };

export type BrokerDeps = {
  /** A client for the running daemon, or null when daemon.json is missing. */
  connect(): DeskClient | null;
  /** Fresh credentials from daemon.json (read on every stream reconnect), or null when the daemon is gone. */
  credentials(): Credentials | null;
  send(senderId: number, channel: PushChannel, payload: unknown): void;
  broadcast(channel: PushChannel, payload: unknown): void;
  hello(): { client: string; notifications: boolean };
  onChange?(state: GlobalState): void;
  onAttentionAdded?(items: AttentionItem[]): void;
  streamFactory?(o: StreamOptions): StreamLike;
  debounceMs?: number;
  pollMs?: number;
  log?(message: string, err?: unknown): void;
};

type Watch = { cursor: number; pending: StoredEvent[] | null };

/**
 * The app's single deskd connection: global state for the map, tray and notifications, and per-window
 * forwarding of watched projects' events (backfilled from /events so none are missed).
 */
export class Broker {
  private state: GlobalState = initialGlobalState();
  private client: DeskClient | null = null;
  private stream: StreamLike | null = null;
  private lastSeq = 0;
  private seen = new Set<string>();
  private watchers = new Map<number, Map<string, Watch>>();
  private refreshTimer: ReturnType<typeof setTimeout> | undefined;
  private pollTimer: ReturnType<typeof setTimeout> | undefined;
  private stopped = false;
  private generation = 0;

  constructor(private readonly d: BrokerDeps) {}

  snapshot(): GlobalState {
    return this.state;
  }

  getClient(): DeskClient {
    if (!this.client) throw new DaemonNotRunning('the data directory');
    return this.client;
  }

  async start(): Promise<void> {
    this.stopped = false;
    await this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.generation++;
    clearTimeout(this.refreshTimer);
    clearTimeout(this.pollTimer);
    this.stream?.close();
    this.stream = null;
  }

  /** Connects again from scratch (after starting, restarting or stopping the daemon). */
  async reconnect(): Promise<void> {
    this.stream?.close();
    this.stream = null;
    this.client = null;
    await this.connect();
  }

  /** Re-sends the hello (the notification setting changed) by reopening the stream where it left off. */
  updateHello(): void {
    if (this.client && this.stream) this.openStream(this.lastSeq);
  }

  private set(patch: Partial<GlobalState>): void {
    this.state = { ...this.state, ...patch };
    this.d.broadcast('desk:global', this.state);
    this.d.onChange?.(this.state);
  }

  private async connect(): Promise<void> {
    const gen = ++this.generation;
    clearTimeout(this.pollTimer);
    const client = this.d.connect();
    if (!client) return this.goOffline();
    this.client = client;
    this.set({ connection: { status: 'connecting' } });
    try {
      const health = await checkDaemon(client);
      const [overview, attention] = await Promise.all([client.overview(), client.attention.list()]);
      if (gen !== this.generation) return;
      this.seen = new Set(attention.items.map((i) => i.id));
      this.lastSeq = attention.seq;
      this.set({ health, overview, attention: attention.items, system: { ...systemFromHealth(health), notices: this.state.system.notices, lastSeq: attention.seq } });
      this.openStream(attention.seq);
    } catch (err) {
      if (gen !== this.generation) return;
      if (err instanceof ProtocolMismatch) {
        this.client = null;
        this.set({ connection: { status: 'mismatch', detail: err.message } });
        return;
      }
      this.d.log?.('connect failed', err);
      this.goOffline();
    }
  }

  private goOffline(): void {
    this.stream?.close();
    this.stream = null;
    this.client = null;
    if (this.state.connection.status !== 'offline') this.set({ connection: { status: 'offline' } });
    this.schedulePoll();
  }

  private schedulePoll(): void {
    clearTimeout(this.pollTimer);
    if (this.stopped) return;
    this.pollTimer = setTimeout(() => {
      if (this.d.credentials()) void this.connect();
      else this.schedulePoll();
    }, this.d.pollMs ?? 2000);
  }

  private openStream(afterSeq: number): void {
    this.stream?.close();
    let readyOnce = false;
    const factory = this.d.streamFactory ?? ((o: StreamOptions) => new DeskStream(o));
    const stream: StreamLike = factory({
      credentials: () => this.d.credentials() ?? this.client?.credentials() ?? { baseUrl: 'http://127.0.0.1:0', token: '' },
      refresh: async () => (this.client ? this.client.refresh() : false),
      projectId: '*',
      afterSeq,
      hello: this.d.hello(),
      onEvent: (e) => this.onEvent(e),
      onEphemeral: (e) => this.onEphemeral(e),
      onReady: () => {
        this.set({ connection: { status: 'live' } });
        if (readyOnce) this.scheduleRefresh(0);
        readyOnce = true;
      },
      onStatus: (s) => {
        if (s !== 'reconnecting' || this.stream !== stream) return;
        if (!this.d.credentials()) return this.goOffline();
        if (this.state.connection.status !== 'reconnecting') this.set({ connection: { status: 'reconnecting' } });
      },
      onError: (m) => this.d.log?.(`stream error: ${m}`),
    });
    this.stream = stream;
    stream.start();
  }

  private onEvent(e: StoredEvent): void {
    this.lastSeq = Math.max(this.lastSeq, e.id);
    if (e.type === 'system.notice') this.set({ system: reduceSystem(this.state.system, e) });
    if (e.type === 'skill.runtime_changed') {
      const { [runtimeKey(e.payload.scope, e.project_id, e.payload.name)]: _done, ...progress } = this.state.runtimes.progress;
      this.set({ runtimes: { progress, seq: this.state.runtimes.seq + 1 } });
    }
    if (affectsAttention(e) || affectsOverview(e)) this.scheduleRefresh();
    for (const [sender, watches] of this.watchers) {
      const w = watches.get(e.project_id);
      if (!w) continue;
      if (w.pending) w.pending.push(e);
      else this.deliver(sender, w, e);
    }
  }

  private onEphemeral(e: EphemeralEvent): void {
    if (e.type === 'skill.runtime_progress') {
      const { scope, name, step, done, total } = e.payload;
      const progress = { ...this.state.runtimes.progress, [runtimeKey(scope, e.project_id, name)]: { step, ...(done !== undefined ? { done } : {}), ...(total !== undefined ? { total } : {}) } };
      this.set({ runtimes: { ...this.state.runtimes, progress } });
      return;
    }
    for (const [sender, watches] of this.watchers) if (watches.has(e.project_id)) this.d.send(sender, 'desk:ephemeral', e);
  }

  private deliver(sender: number, w: Watch, e: StoredEvent): void {
    if (e.id <= w.cursor) return;
    w.cursor = e.id;
    this.d.send(sender, 'desk:event', e);
  }

  private scheduleRefresh(delay = this.d.debounceMs ?? 250): void {
    clearTimeout(this.refreshTimer);
    this.refreshTimer = setTimeout(() => void this.refresh(), delay);
  }

  /** Refetches overview, attention and health; reports attention items not seen before. */
  async refresh(): Promise<void> {
    const client = this.client;
    if (!client) return;
    try {
      const [overview, attention, health] = await Promise.all([client.overview(), client.attention.list(), client.health()]);
      if (client !== this.client) return;
      const added = attention.items.filter((i) => !this.seen.has(i.id));
      this.seen = new Set(attention.items.map((i) => i.id));
      this.set({ overview, attention: attention.items, health, system: health.proxy ? { ...this.state.system, proxy: health.proxy } : this.state.system });
      if (added.length) this.d.onAttentionAdded?.(added);
    } catch (err) {
      this.d.log?.('refresh failed', err);
    }
  }

  /** Starts forwarding a project's events after `afterSeq` to a window: backfill first, then live, never twice. */
  async watch(senderId: number, projectId: string, afterSeq: number): Promise<void> {
    const client = this.getClient();
    let watches = this.watchers.get(senderId);
    if (!watches) this.watchers.set(senderId, (watches = new Map()));
    const w: Watch = { cursor: afterSeq, pending: [] };
    watches.set(projectId, w);
    try {
      let after = afterSeq;
      while (after < this.lastSeq) {
        const page = await client.projects.events(projectId, { after, limit: 1000 });
        if (watches.get(projectId) !== w) return;
        for (const e of page.events) this.deliver(senderId, w, e);
        if (!page.events.length) break;
        after = page.next_after;
      }
    } finally {
      const pending = w.pending ?? [];
      w.pending = null;
      if (watches.get(projectId) === w) for (const e of pending) this.deliver(senderId, w, e);
    }
  }

  unwatch(senderId: number, projectId: string): void {
    this.watchers.get(senderId)?.delete(projectId);
  }

  dropSender(senderId: number): void {
    this.watchers.delete(senderId);
  }
}
