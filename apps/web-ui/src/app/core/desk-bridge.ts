import { DestroyRef, Injectable, inject, signal, type Signal } from '@angular/core';
import { safeExternalUrl, UnsafeUrlError, type Channel, type ChannelInput, type ChannelOutput, type IpcError, type IpcResult } from '@desk/bff/contract';
import {
  CLOSE_UNAUTHORIZED,
  PUSH_OPS,
  SESSION_HEADER,
  SESSION_STORAGE_KEY,
  decodeBytes,
  encodeBytes,
  type NotifyPermission,
  type PushServerFrame,
  type WebChannel,
  type WebChannelInput,
  type WebChannelOutput,
  type WebPushChannel,
} from '@desk/web-server/contract';

/** A failed call: `code` is deskd's error code, or the host's (`unauthorized`, `invalid_url`, `web_unreachable`, …). */
export class DeskCallError extends Error {
  readonly code: string;
  readonly status: number | undefined;

  constructor(error: IpcError);
  constructor(code: string, message: string, status?: number);
  constructor(a: IpcError | string, message?: string, status?: number) {
    const e: IpcError = typeof a === 'string' ? { code: a, message: message ?? a, ...(status === undefined ? {} : { status }) } : a;
    super(e.message);
    this.name = 'DeskCallError';
    this.code = e.code;
    this.status = e.status;
  }
}

/** Why `app.pickFolder` asks for a folder. */
export type FolderPurpose = ChannelInput<'app.pickFolder'>['purpose'];
/** An open `app.pickFolder` request, shown by the folder browser. */
export type FolderRequest = { purpose: FolderPurpose };
/** The /push socket: `idle` (signed out, or nothing listens yet), `connecting` (first try), `live`, `reconnecting` (lost it; retrying). */
export type PushStatus = 'idle' | 'connecting' | 'live' | 'reconnecting';

const SIGNED_OUT: IpcError = { code: 'unauthorized', message: 'This browser is signed out of Desk. Open Desk from your terminal with desk web.', status: 401 };
const UNREACHABLE: IpcError = { code: 'web_unreachable', message: 'desk web is not answering. If you stopped it, run desk web again.' };
/** Delays before each /push reconnect; the last one repeats. */
const BACKOFF_MS = [500, 1000, 2000, 5000, 10_000];
/** WebSocket.OPEN */
const OPEN = 1;

type Pending = { frame: { op: string; id: number; input: unknown }; resolve(value: unknown): void; reject(error: unknown): void };

function storedSecret(): string | null {
  try {
    return localStorage.getItem(SESSION_STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

/** What this browser lets the page do with notifications (`denied` where it has none); WebNotifications reads it too. */
export function notificationPermission(): NotifyPermission {
  return typeof Notification === 'undefined' ? 'denied' : Notification.permission;
}

/**
 * The web UI's only way to desk web (spec §5). `call` posts one operation to /rpc with the session secret; `onPush` listens on
 * the /push socket. The host operations a browser does itself never reach the server (§3): links open here, files download
 * here, and picking a folder opens the folder browser. broker.watch and broker.unwatch travel on the socket, which is their
 * broker sender. A 401, or the socket closed with 4401, drops the secret and signs the page out, unless another tab has stored
 * a newer secret, which the page then uses; a call refused for a secret the page no longer holds is tried once more.
 */
@Injectable({ providedIn: 'root' })
export class DeskBridge {
  private secret = storedSecret();
  private readonly signedOutState = signal(this.secret === null);
  /** True while this browser holds no valid session secret; the app then shows only "Open Desk from your terminal". */
  readonly signedOut: Signal<boolean> = this.signedOutState.asReadonly();
  private readonly pushState = signal<PushStatus>('idle');
  readonly pushStatus: Signal<PushStatus> = this.pushState.asReadonly();
  private readonly folderState = signal<FolderRequest | null>(null);
  /** The open `app.pickFolder` request, for the folder browser; `answerFolder` settles it. */
  readonly folderRequest: Signal<FolderRequest | null> = this.folderState.asReadonly();
  private settleFolder: ((path: string | null) => void) | null = null;

  private readonly listeners = new Map<WebPushChannel, Set<(payload: unknown) => void>>();
  private readonly reconnectListeners = new Set<() => void>();
  private readonly pending = new Map<number, Pending>();
  private nextId = 0;
  private socket: WebSocket | null = null;
  private live = false;
  private wasLive = false;
  private attempt = 0;
  private retry: ReturnType<typeof setTimeout> | undefined;
  private permission = notificationPermission();
  private destroyed = false;

  constructor() {
    const onStorage = (e: StorageEvent) => {
      if (e.key === SESSION_STORAGE_KEY && e.newValue) this.signIn(e.newValue);
    };
    window.addEventListener('storage', onStorage);
    inject(DestroyRef).onDestroy(() => {
      this.destroyed = true;
      window.removeEventListener('storage', onStorage);
      clearTimeout(this.retry);
      this.closeSocket();
    });
  }

  call<C extends Channel>(op: C, input: ChannelInput<C>): Promise<ChannelOutput<C>>;
  call<C extends WebChannel>(op: C, input: WebChannelInput<C>): Promise<WebChannelOutput<C>>;
  call(op: string, input: unknown): Promise<unknown> {
    // Host operations run synchronously: window.open must happen inside the click that asked for it.
    try {
      if (op === 'app.openExternal') return Promise.resolve(this.openExternal((input as ChannelInput<'app.openExternal'>).url));
      if (op === 'app.saveFile') return Promise.resolve(this.saveFile(input as ChannelInput<'app.saveFile'>));
      if (op === 'app.pickFolder') return this.pickFolder((input as ChannelInput<'app.pickFolder'>).purpose);
    } catch (err) {
      return Promise.reject(err);
    }
    return (PUSH_OPS as readonly string[]).includes(op) ? this.request(op, input) : this.rpc(op, input);
  }

  onPush<T = unknown>(channel: WebPushChannel, cb: (payload: T) => void): () => void {
    const set = this.listeners.get(channel) ?? new Set<(payload: unknown) => void>();
    this.listeners.set(channel, set);
    const listener = cb as (payload: unknown) => void;
    set.add(listener);
    this.ensureSocket();
    return () => {
      set.delete(listener);
    };
  }

  /** Called each time /push signs in again after a drop. A new socket is a new broker sender, so watches must be renewed. */
  onReconnect(cb: () => void): () => void {
    this.reconnectListeners.add(cb);
    return () => {
      this.reconnectListeners.delete(cb);
    };
  }

  /** Settles the open `app.pickFolder` request with the chosen folder, or null when the user cancelled. */
  answerFolder(path: string | null): void {
    const settle = this.settleFolder;
    this.settleFolder = null;
    this.folderState.set(null);
    settle?.(path);
  }

  /** Tells desk web whether this page may show notifications (sent on every sign-in, and now if the socket is live). */
  setNotifyPermission(permission: NotifyPermission): void {
    this.permission = permission;
    if (this.live && this.socket) this.send(this.socket, { notifyPermission: permission });
  }

  /** Forgets the session secret, unless another tab has stored a newer one, which this page then uses. */
  signOut(): void {
    const stored = storedSecret();
    if (stored && stored !== this.secret) {
      this.signIn(stored);
      return;
    }
    try {
      if (stored) localStorage.removeItem(SESSION_STORAGE_KEY);
    } catch {
      // Storage may be unavailable; the page is signed out either way.
    }
    this.secret = null;
    this.signedOutState.set(true);
    this.pushState.set('idle');
    clearTimeout(this.retry);
    this.retry = undefined;
    this.closeSocket();
    for (const p of this.pending.values()) p.reject(new DeskCallError(SIGNED_OUT));
    this.pending.clear();
  }

  private signIn(secret: string): void {
    if (secret === this.secret && !this.signedOutState()) return;
    this.secret = secret;
    this.signedOutState.set(false);
    clearTimeout(this.retry);
    this.retry = undefined;
    this.closeSocket();
    if (this.listeners.size || this.pending.size) this.open();
  }

  private openExternal(raw: string): { ok: true } {
    let url: string;
    try {
      url = safeExternalUrl(raw);
    } catch (err) {
      throw err instanceof UnsafeUrlError ? new DeskCallError(err.code, err.message) : err;
    }
    window.open(url, '_blank', 'noopener,noreferrer');
    return { ok: true };
  }

  private saveFile({ name, data }: ChannelInput<'app.saveFile'>): boolean {
    // application/octet-stream: the browser downloads it and never renders it with this origin (spec §4.11).
    const url = URL.createObjectURL(new Blob([new Uint8Array(data)], { type: 'application/octet-stream' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.rel = 'noopener';
    a.style.display = 'none';
    document.body.append(a);
    try {
      a.click();
    } finally {
      a.remove();
      URL.revokeObjectURL(url);
    }
    return true;
  }

  private pickFolder(purpose: FolderPurpose): Promise<string | null> {
    this.answerFolder(null);
    return new Promise((resolve) => {
      this.settleFolder = resolve;
      this.folderState.set({ purpose });
    });
  }

  private async rpc(op: string, input: unknown, retried = false): Promise<unknown> {
    const secret = this.secret;
    if (!secret) {
      this.signOut();
      throw new DeskCallError(SIGNED_OUT);
    }
    let res: Response;
    try {
      res = await fetch(`/rpc/${op}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', [SESSION_HEADER]: secret },
        body: JSON.stringify(encodeBytes(input ?? {})),
      });
    } catch {
      throw new DeskCallError(UNREACHABLE);
    }
    if (res.status === 401) {
      // Only the secret that was sent is refused. signOut() drops it, or adopts a newer one another tab stored; when the page
      // holds another secret by now (adopted during the call, or just now), the call is tried once more with it.
      if (this.secret === secret) this.signOut();
      if (this.secret && this.secret !== secret && !retried) return this.rpc(op, input, true);
      throw new DeskCallError(SIGNED_OUT);
    }
    let result: IpcResult<unknown>;
    try {
      result = decodeBytes(await res.json()) as IpcResult<unknown>;
    } catch {
      throw new DeskCallError('bad_response', `desk web answered ${op} with HTTP ${res.status}.`, res.status);
    }
    if (result.ok) return result.value;
    throw new DeskCallError(result.error.status === undefined && res.status >= 400 ? { ...result.error, status: res.status } : result.error);
  }

  private request(op: string, input: unknown): Promise<unknown> {
    if (!this.secret) {
      this.signOut();
      return Promise.reject(new DeskCallError(SIGNED_OUT));
    }
    const frame = { op, id: this.nextId++, input };
    return new Promise((resolve, reject) => {
      this.pending.set(frame.id, { frame, resolve, reject });
      if (this.live && this.socket) this.send(this.socket, frame);
      else this.ensureSocket();
    });
  }

  private ensureSocket(): void {
    if (!this.socket && this.retry === undefined && this.secret && !this.destroyed) this.open();
  }

  private open(): void {
    this.retry = undefined;
    const secret = this.secret;
    if (!secret || this.destroyed) return;
    this.pushState.set(this.wasLive || this.attempt > 0 ? 'reconnecting' : 'connecting');
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/push`);
    this.socket = ws;
    ws.onopen = () => ws.send(JSON.stringify({ session: secret }));
    ws.onmessage = (e: MessageEvent) => {
      if (this.socket === ws) this.onFrame(ws, e.data);
    };
    ws.onclose = (e: CloseEvent) => {
      if (this.socket === ws) this.onClose(e.code);
    };
  }

  private onFrame(ws: WebSocket, data: unknown): void {
    let frame: PushServerFrame;
    try {
      frame = JSON.parse(String(data)) as PushServerFrame;
    } catch {
      return;
    }
    if ('ack' in frame) {
      const p = this.pending.get(frame.ack);
      if (!p) return;
      this.pending.delete(frame.ack);
      if (frame.result.ok) p.resolve(frame.result.value);
      else p.reject(new DeskCallError(frame.result.error));
      return;
    }
    // The first frame after { session } is desk:global: the socket is signed in.
    if (!this.live) this.becomeLive(ws);
    for (const listener of [...(this.listeners.get(frame.channel) ?? [])]) listener(frame.payload);
  }

  private becomeLive(ws: WebSocket): void {
    this.live = true;
    this.attempt = 0;
    this.pushState.set('live');
    this.send(ws, { notifyPermission: this.permission });
    for (const p of this.pending.values()) this.send(ws, p.frame);
    const again = this.wasLive;
    this.wasLive = true;
    if (again) for (const cb of [...this.reconnectListeners]) cb();
  }

  private onClose(code: number): void {
    this.socket = null;
    this.live = false;
    if (code === CLOSE_UNAUTHORIZED) {
      this.signOut();
      return;
    }
    if (this.destroyed || !this.secret) return;
    this.pushState.set('reconnecting');
    const delay = BACKOFF_MS[Math.min(this.attempt, BACKOFF_MS.length - 1)] ?? 10_000;
    this.attempt++;
    this.retry = setTimeout(() => this.open(), delay);
  }

  private closeSocket(): void {
    const ws = this.socket;
    this.socket = null;
    this.live = false;
    if (ws && ws.readyState <= OPEN) ws.close(1000);
  }

  private send(ws: WebSocket, frame: unknown): void {
    if (ws.readyState === OPEN) ws.send(JSON.stringify(frame));
  }
}

/** The DeskBridge surface components use; `FakeDeskBridge` implements it for specs. */
export type DeskBridgeApi = { [K in keyof DeskBridge]: DeskBridge[K] };
