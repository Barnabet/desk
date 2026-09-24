import type { EphemeralEvent, StoredEvent, StreamServerMessage } from '@desk/protocol';
import type { Credentials } from './types';

export type StreamStatus = 'connecting' | 'live' | 'reconnecting' | 'closed';

export type StreamOptions = {
  credentials: () => Credentials;
  /** Called after the server closes with 4401 (bad token); resolve true once credentials were refreshed. */
  refresh?: () => Promise<boolean>;
  projectId: string;
  afterSeq: number;
  hello?: { client: string; notifications: boolean };
  onEvent(e: StoredEvent): void;
  onEphemeral?(e: EphemeralEvent): void;
  onReady?(seq: number): void;
  onStatus?(s: StreamStatus): void;
  onError?(message: string): void;
  WebSocketImpl?: typeof WebSocket;
  backoff?: { minMs: number; maxMs: number };
  random?: () => number;
};

/**
 * The deskd event stream with resume: every (re)connection subscribes after the last applied event id,
 * so events are never missed or applied twice. Reconnects with jittered exponential backoff.
 */
export class DeskStream {
  private ws: WebSocket | null = null;
  private cursor: number;
  private closed = false;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(private readonly o: StreamOptions) {
    this.cursor = o.afterSeq;
  }

  get lastSeq(): number {
    return this.cursor;
  }

  start(): void {
    this.closed = false;
    this.connect();
  }

  close(): void {
    this.closed = true;
    clearTimeout(this.timer);
    const ws = this.ws;
    if (ws) ws.close();
    else this.o.onStatus?.('closed');
  }

  private connect(): void {
    const { baseUrl, token } = this.o.credentials();
    this.o.onStatus?.(this.attempt ? 'reconnecting' : 'connecting');
    const WS = this.o.WebSocketImpl ?? WebSocket;
    const ws = new WS(`${baseUrl.replace(/^http/, 'ws')}/v1/stream?token=${encodeURIComponent(token)}`);
    this.ws = ws;
    ws.onopen = () => {
      if (this.o.hello) ws.send(JSON.stringify({ hello: this.o.hello }));
      ws.send(JSON.stringify({ subscribe: { project_id: this.o.projectId, after_seq: this.cursor } }));
    };
    ws.onmessage = (e: MessageEvent) => {
      let m: StreamServerMessage;
      try {
        m = JSON.parse(String(e.data)) as StreamServerMessage;
      } catch {
        return;
      }
      if (m.kind === 'event') {
        if (m.event.id <= this.cursor) return;
        this.cursor = m.event.id;
        this.o.onEvent(m.event);
      } else if (m.kind === 'ephemeral') {
        this.o.onEphemeral?.(m.event);
      } else if (m.kind === 'ready') {
        this.attempt = 0;
        this.o.onStatus?.('live');
        this.o.onReady?.(m.seq);
      } else {
        this.o.onError?.(m.message);
      }
    };
    ws.onerror = () => {};
    ws.onclose = (e: { code: number }) => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (this.closed) {
        this.o.onStatus?.('closed');
        return;
      }
      if (e.code === 4401 && this.o.refresh) {
        void this.o.refresh().then(
          () => this.schedule(),
          () => this.schedule(),
        );
      } else this.schedule();
    };
  }

  private schedule(): void {
    if (this.closed) return;
    const { minMs, maxMs } = this.o.backoff ?? { minMs: 500, maxMs: 10_000 };
    const base = Math.min(maxMs, minMs * 2 ** this.attempt);
    const delay = base * (0.5 + (this.o.random ?? Math.random)() * 0.5);
    this.attempt++;
    this.o.onStatus?.('reconnecting');
    this.timer = setTimeout(() => this.connect(), delay);
  }
}
