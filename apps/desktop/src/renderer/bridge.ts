import type { PushChannel } from '../shared/channels';
import type { Channel, ChannelInput, IpcError, IpcResult } from '../shared/ipc';
import type { ChannelOutput } from '../main/handlers';

export type DeskBridge = {
  invoke(channel: string, input: unknown): Promise<IpcResult<unknown>>;
  on(channel: PushChannel, cb: (payload: unknown) => void): () => void;
  platform: string;
};

declare global {
  interface Window {
    desk: DeskBridge;
  }
}

/** A failed call: `code` is the daemon's error code (or an app code such as daemon_not_running). */
export class DeskCallError extends Error {
  readonly code: string;
  readonly status: number | undefined;

  constructor(e: IpcError) {
    super(e.message);
    this.name = 'DeskCallError';
    this.code = e.code;
    this.status = e.status;
  }
}

export async function call<C extends Channel>(channel: C, input: ChannelInput<C>): Promise<ChannelOutput<C>> {
  const r = await window.desk.invoke(channel, input);
  if (!r.ok) throw new DeskCallError(r.error);
  return r.value as ChannelOutput<C>;
}

export function onPush<T = unknown>(channel: PushChannel, cb: (payload: T) => void): () => void {
  return window.desk.on(channel, cb as (payload: unknown) => void);
}
