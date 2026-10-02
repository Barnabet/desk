import type { IncomingMessage } from 'node:http';
import type { Duplex } from 'node:stream';
import { WebSocketServer, type WebSocket } from 'ws';
import type { GlobalState, IpcResult } from '@desk/bff/contract';
import type { StoredEvent } from '@desk/protocol';
import { dispatch, type HandlerContext } from '@desk/bff/server';
import type { Sessions } from './auth';
import { callerOf, type Caller } from './caller';
import type { DeviceStore } from './devices';
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
import { phoneRefusal } from './phone-policy';
import { slimPushForPhone, trimBackfillForPhone } from './phone-slim';

export type PushHubDeps = {
  sessions: Sessions;
  /** Paired phones: their secrets sign in too, and are checked again every `recheckMs` (a phone unpaired from the CLI). */
  devices?: DeviceStore;
  recheckMs?: number;
  broker: { snapshot(): GlobalState; dropSender(senderId: number): void };
  /** The HandlerContext a socket's broker.watch and unwatch run with (its sender id is the socket's). */
  ctx(senderId: number): HandlerContext;
  /** The number of sockets reporting Notification.permission === 'granted' changed. */
  onGrantedChange?(granted: number): void;
  authTimeoutMs?: number;
  log?(message: string, err?: unknown): void;
};

type Client = {
  ws: WebSocket;
  senderId: number;
  secret: string;
  caller: Caller;
  permission: NotifyPermission;
  /** Operations not answered yet. */
  busy: Set<string>;
  /** A phone's watched projects and their Desk agent: its backfill leaves out every other agent's transcript. */
  desks: Map<string, string>;
};

/**
 * The most JSON one `desk:events` frame carries (one event more when a single event is larger). A project's history is
 * pushed in pages of thousands of events, several megabytes each: a phone's socket that drops on a frame that large
 * never finishes opening the project. In frames this size, what arrived stays, and the page resumes after it.
 */
export const EVENTS_FRAME_BYTES = 256 * 1024;

/** `desk:events` frames for a batch, as JSON texts of at most EVENTS_FRAME_BYTES each, in order. */
export function eventFrames(batch: unknown[], limit = EVENTS_FRAME_BYTES): string[] {
  const frames: string[] = [];
  let parts: string[] = [];
  let size = 0;
  const flush = () => {
    if (parts.length) frames.push(`{"channel":"desk:events","payload":[${parts.join(',')}]}`);
    parts = [];
    size = 0;
  };
  for (const e of batch) {
    const text = JSON.stringify(e);
    if (parts.length && size + text.length > limit) flush();
    parts.push(text);
    size += text.length + 1;
  }
  flush();
  return frames;
}

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
  // Compressed: a project's history is sent whole when a page opens it, and JSON shrinks several times over (phones on
  // Tailscale feel that most).
  private readonly wss = new WebSocketServer({ noServer: true, maxPayload: 64 * 1024, perMessageDeflate: { threshold: 1024 } });
  private readonly clients = new Map<number, Client>();
  private nextSender = 1;
  private readonly offRevoke: () => void;
  private readonly recheck: ReturnType<typeof setInterval> | null;

  constructor(private readonly d: PushHubDeps) {
    this.offRevoke = d.sessions.onRevoke((secret) => {
      for (const c of this.clients.values()) if (c.secret === secret) c.ws.close(CLOSE_UNAUTHORIZED, 'signed out');
    });
    this.recheck = d.devices ? setInterval(() => this.recheckPhones(), d.recheckMs ?? 15_000) : null;
    this.recheck?.unref();
  }

  /** Closes the sockets of phones that were unpaired or went idle since they signed in. */
  recheckPhones(): void {
    for (const c of this.clients.values()) if (c.caller.kind === 'phone' && !this.d.devices?.who(c.secret)) c.ws.close(CLOSE_UNAUTHORIZED, 'signed out');
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
      const caller = first.success ? callerOf(first.data.session, this.d.sessions, this.d.devices) : null;
      if (!first.success || !caller) return ws.close(CLOSE_UNAUTHORIZED, 'unauthorized');
      client = { ws, senderId: this.nextSender++, secret: first.data.session, caller, permission: 'default', busy: new Set(), desks: new Map() };
      this.clients.set(client.senderId, client);
      this.write(client, { channel: 'desk:global', payload: this.d.broker.snapshot() });
    });
    ws.on('close', (code) => {
      clearTimeout(timer);
      if (!client) return;
      // A socket that drops while a project's history is on its way: the page asks again, from what it received.
      if (client.busy.size) this.d.log?.(`push socket of ${client.caller.kind === 'phone' ? 'a phone' : 'this computer'} closed (${code}) during ${[...client.busy].join(', ')}`);
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
    const refused = c.caller.kind === 'phone' ? phoneRefusal(f.op, f.input) : null;
    const running = `${f.op} #${f.id}`;
    c.busy.add(running);
    if (!refused && c.caller.kind === 'phone' && f.op === 'broker.watch') await this.learnDesk(c, f.input);
    const result: IpcResult<unknown> = refused
      ? { ok: false, error: { code: 'not_on_phone', message: refused } }
      : pushOps.has(f.op)
      ? await dispatch(f.op, f.input, this.d.ctx(c.senderId), (err) => this.d.log?.(`push ${f.op} failed`, err))
      : { ok: false, error: { code: 'unknown_channel', message: 'Only broker.watch and broker.unwatch run on /push.' } };
    c.busy.delete(running);
    this.write(c, { ack: f.id, result });
  }

  /** Notes a phone's watched project's Desk agent before its backfill goes out; when unknown, the backfill stays whole. */
  private async learnDesk(c: Client, input: unknown): Promise<void> {
    const projectId = (input as { projectId?: unknown } | null)?.projectId;
    if (typeof projectId !== 'string') return;
    const r = await dispatch('projects.get', { id: projectId }, this.d.ctx(c.senderId), () => {});
    const deskId = r.ok ? (r.value as { desk?: { id: string } | null }).desk?.id : undefined;
    if (deskId) c.desks.set(projectId, deskId);
    else c.desks.delete(projectId);
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
    if (!c) return;
    if (c.caller.kind !== 'phone') return this.write(c, { channel, payload });
    // desk:events is a watch's backfill (live events come one by one as desk:event, whole).
    const first = channel === 'desk:events' && Array.isArray(payload) ? (payload[0] as { project_id?: string } | undefined) : undefined;
    const page = first?.project_id ? trimBackfillForPhone(payload as StoredEvent[], c.desks.get(first.project_id)) : payload;
    if (Array.isArray(page) && !page.length) return;
    this.write(c, { channel, payload: slimPushForPhone(channel, page) });
  }

  broadcast(channel: WebPushChannel, payload: unknown): void {
    for (const c of this.clients.values()) this.write(c, { channel, payload: c.caller.kind === 'phone' ? slimPushForPhone(channel, payload) : payload });
  }

  /** desk:notify, to the sockets that may show notifications. */
  notify(notices: WebNotice[]): void {
    if (!notices.length) return;
    for (const c of this.clients.values()) if (c.permission === 'granted') this.write(c, { channel: 'desk:notify', payload: notices });
  }

  private write(c: Client, frame: PushServerFrame): void {
    if (c.ws.readyState !== c.ws.OPEN) return;
    if ('channel' in frame && frame.channel === 'desk:events' && Array.isArray(frame.payload)) for (const text of eventFrames(frame.payload)) c.ws.send(text);
    else c.ws.send(JSON.stringify(frame));
  }

  close(): void {
    this.offRevoke();
    if (this.recheck) clearInterval(this.recheck);
    for (const ws of this.wss.clients) ws.terminate();
    this.wss.close();
  }
}
