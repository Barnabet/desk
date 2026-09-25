import { signal, type Provider } from '@angular/core';
import type { Channel, ChannelInput, ChannelOutput, GlobalState, IpcError } from '@desk/bff/contract';
import type { NotifyPermission, WebChannel, WebChannelInput, WebChannelOutput, WebPushChannel } from '@desk/web-server/contract';
import { DeskBridge, DeskCallError, type DeskBridgeApi, type FolderPurpose, type FolderRequest, type PushStatus } from '../core/desk-bridge';
import { GlobalStore } from '../core/global.store';

/** A scripted answer: return (or resolve) the value, or throw (or reject with) an IpcError-shaped object to fail. */
export type FakeHandler = (input: any) => unknown;
export type FakeHandlers = Partial<Record<Channel | WebChannel, FakeHandler>>;

/**
 * DeskBridge for specs, like the desktop's `installBridge`: records every call as `{ channel, input }`, answers from its
 * handlers (an operation without one fails with `unknown_channel`; `app.pickFolder` without one opens a folder request, as the
 * real bridge does), and pushes with `emit`. Provide it with `providers: bridge.providers`.
 */
export class FakeDeskBridge implements DeskBridgeApi {
  readonly calls: Array<{ channel: string; input: unknown }> = [];
  /** What `setNotifyPermission` was told, in order. */
  readonly notifyPermissions: NotifyPermission[] = [];
  private readonly handlers: FakeHandlers;
  private readonly listeners = new Map<string, Set<(payload: unknown) => void>>();
  private readonly reconnectListeners = new Set<() => void>();
  private readonly signedOutState = signal(false);
  readonly signedOut = this.signedOutState.asReadonly();
  private readonly pushState = signal<PushStatus>('live');
  readonly pushStatus = this.pushState.asReadonly();
  private readonly folderState = signal<FolderRequest | null>(null);
  readonly folderRequest = this.folderState.asReadonly();
  private settleFolder: ((path: string | null) => void) | null = null;

  constructor(handlers: FakeHandlers = {}) {
    this.handlers = { ...handlers };
  }

  /** Makes this fake the app's DeskBridge. */
  get providers(): Provider[] {
    return [{ provide: DeskBridge, useValue: this }];
  }

  /** Adds or replaces the answer for one operation. */
  handle(channel: Channel | WebChannel, handler: FakeHandler): this {
    this.handlers[channel] = handler;
    return this;
  }

  call<C extends Channel>(op: C, input: ChannelInput<C>): Promise<ChannelOutput<C>>;
  call<C extends WebChannel>(op: C, input: WebChannelInput<C>): Promise<WebChannelOutput<C>>;
  call(op: string, input: unknown): Promise<unknown> {
    this.calls.push({ channel: op, input });
    const handler = this.handlers[op as Channel | WebChannel];
    if (!handler) {
      if (op === 'app.pickFolder') return this.pickFolder((input as { purpose: FolderPurpose }).purpose);
      return Promise.reject(new DeskCallError({ code: 'unknown_channel', message: op }));
    }
    return (async () => {
      try {
        return await handler(input);
      } catch (err) {
        throw err instanceof DeskCallError ? err : new DeskCallError(err as IpcError);
      }
    })();
  }

  onPush<T = unknown>(channel: WebPushChannel, cb: (payload: T) => void): () => void {
    const set = this.listeners.get(channel) ?? new Set<(payload: unknown) => void>();
    this.listeners.set(channel, set);
    const listener = cb as (payload: unknown) => void;
    set.add(listener);
    return () => {
      set.delete(listener);
    };
  }

  onReconnect(cb: () => void): () => void {
    this.reconnectListeners.add(cb);
    return () => {
      this.reconnectListeners.delete(cb);
    };
  }

  /** Pushes a payload to every listener on `channel`, as desk web would. */
  emit(channel: WebPushChannel, payload: unknown): void {
    for (const listener of [...(this.listeners.get(channel) ?? [])]) listener(payload);
  }

  /** Fires the reconnect listeners, as a /push socket that signed in again would. */
  reconnect(): void {
    for (const cb of [...this.reconnectListeners]) cb();
  }

  answerFolder(path: string | null): void {
    const settle = this.settleFolder;
    this.settleFolder = null;
    this.folderState.set(null);
    settle?.(path);
  }

  setNotifyPermission(permission: NotifyPermission): void {
    this.notifyPermissions.push(permission);
  }

  signOut(): void {
    this.signedOutState.set(true);
  }

  setPushStatus(status: PushStatus): void {
    this.pushState.set(status);
  }

  private pickFolder(purpose: FolderPurpose): Promise<string | null> {
    this.answerFolder(null);
    return new Promise((resolve) => {
      this.settleFolder = resolve;
      this.folderState.set({ purpose });
    });
  }
}

/** Makes the GlobalStore start from `state`, for specs of components that read global state. */
export function provideGlobal(state: GlobalState): Provider {
  return {
    provide: GlobalStore,
    useFactory: () => {
      const store = new GlobalStore();
      store.set(state);
      return store;
    },
  };
}
