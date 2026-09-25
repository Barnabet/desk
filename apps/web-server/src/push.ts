import type { IncomingMessage } from 'node:http';
import type { Duplex } from 'node:stream';
import { WebSocketServer, type WebSocket } from 'ws';
import type { GlobalState, IpcResult } from '@desk/bff/contract';
import { dispatch, type HandlerContext } from '@desk/bff/server';
import type { Sessions } from './auth';
import {
  AUTH_TIMEOUT_MS,
  CLOSE_BAD_FRAME,
  CLOSE_UNAUTHORIZED,
  PUSH_OPS,
  PushClientFrame,
  SessionFrame,
  type NotifyPermission,
  type PushServerFrame,
  type WebNotice,
  type WebPushChannel,
} from './frames';

export type PushHubDeps = {
  sessions: Sessions;
  broker: { snapshot(): GlobalState; dropSender(senderId: number): void };
  /** The HandlerContext a socket's broker.watch and unwatch run with (its sender id is the socket's). */
  ctx(senderId: number): HandlerContext;
  /** The number of sockets reporting Notification.permission === 'granted' changed. */
  onGrantedChange?(granted: number): void;
  authTimeoutMs?: number;
  log?(message: string, err?: unknown): void;
};

type Client = { ws: WebSocket; senderId: number; secret: string; permission: NotifyPermission };

const pushOps = new Set<string>(PUSH_OPS);

function parse(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}

/**
 * /push: each signed-in socket is one broker sender. The broker's broadcasts reach every signed-in socket, its sends
 * reach one; broker.watch and unwatch arrive here, so their sender is always the socket they came on.
 */
export class PushHub {
  private readonly wss = new WebSocketServer({ noServer: true, maxPayload: 64 * 1024 });
  private readonly clients = new Map<number, Client>();
  private nextSender = 1;
  private readonly offRevoke: () => void;

  constructor(private readonly d: PushHubDeps) {
    this.offRevoke = d.sessions.onRevoke((secret) => {
      for (const c of this.clients.values()) if (c.secret === secret) c.ws.close(CLOSE_UNAUTHORIZED, 'signed out');
    });
  }

  readonly handleUpgrade = (req: IncomingMessage, socket: Duplex, head: Buffer): void => {
    this.wss.handleUpgrade(req, socket, head, (ws) => this.accept(ws));
  };

  private accept(ws: WebSocket): void {
    // A bad frame (over maxPayload, unmasked, invalid UTF-8) emits 'error' before ws closes the socket itself
    // (1009, 1002, 1007), signed in or not: unhandled, it would crash desk web. 'close' then drops the sender.
    ws.on('error', (err) => this.d.log?.(`push socket error: ${err.message}`));
    let client: Client | null = null;
    const timer = setTimeout(() => ws.close(CLOSE_UNAUTHORIZED, 'no session'), this.d.authTimeoutMs ?? AUTH_TIMEOUT_MS);
    ws.on('message', (data) => {
      // Frames that arrive after a refusal (the socket is closing) are ignored.
      if (ws.readyState !== ws.OPEN) return;
      const frame = parse(String(data));
      if (client) return void this.onFrame(client, frame);
      clearTimeout(timer);
      const first = SessionFrame.safeParse(frame);
      if (!first.success || !this.d.sessions.valid(first.data.session)) return ws.close(CLOSE_UNAUTHORIZED, 'unauthorized');
      client = { ws, senderId: this.nextSender++, secret: first.data.session, permission: 'default' };
      this.clients.set(client.senderId, client);
      this.write(client, { channel: 'desk:global', payload: this.d.broker.snapshot() });
    });
    ws.on('close', () => {
      clearTimeout(timer);
      if (!client) return;
      const before = this.granted();
      this.clients.delete(client.senderId);
      this.d.broker.dropSender(client.senderId);
      this.grantedMaybeChanged(before);
    });
  }

  private async onFrame(c: Client, raw: unknown): Promise<void> {
    const parsed = PushClientFrame.safeParse(raw);
    if (!parsed.success) return c.ws.close(CLOSE_BAD_FRAME, 'bad frame');
    const f = parsed.data;
    if ('notifyPermission' in f) {
      const before = this.granted();
      c.permission = f.notifyPermission;
      return this.grantedMaybeChanged(before);
    }
    const result: IpcResult<unknown> = pushOps.has(f.op)
      ? await dispatch(f.op, f.input, this.d.ctx(c.senderId), (err) => this.d.log?.(`push ${f.op} failed`, err))
      : { ok: false, error: { code: 'unknown_channel', message: 'Only broker.watch and broker.unwatch run on /push.' } };
    this.write(c, { ack: f.id, result });
  }

  private grantedMaybeChanged(before: number): void {
    const now = this.granted();
    if (now !== before) this.d.onGrantedChange?.(now);
  }

  /** Signed-in sockets whose page reported Notification.permission === 'granted'. */
  granted(): number {
    let n = 0;
    for (const c of this.clients.values()) if (c.permission === 'granted') n++;
    return n;
  }

  send(senderId: number, channel: WebPushChannel, payload: unknown): void {
    const c = this.clients.get(senderId);
    if (c) this.write(c, { channel, payload });
  }

  broadcast(channel: WebPushChannel, payload: unknown): void {
    for (const c of this.clients.values()) this.write(c, { channel, payload });
  }

  /** desk:notify, to the sockets that may show notifications. */
  notify(notices: WebNotice[]): void {
    if (!notices.length) return;
    for (const c of this.clients.values()) if (c.permission === 'granted') this.write(c, { channel: 'desk:notify', payload: notices });
  }

  private write(c: Client, frame: PushServerFrame): void {
    if (c.ws.readyState === c.ws.OPEN) c.ws.send(JSON.stringify(frame));
  }

  close(): void {
    this.offRevoke();
    for (const ws of this.wss.clients) ws.terminate();
    this.wss.close();
  }
}
