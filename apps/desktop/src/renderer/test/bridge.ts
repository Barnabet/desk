import type { Channel, IpcError, PushChannel } from '@desk/bff/contract';

type Handler = (input: any) => unknown;

/** Installs a fake `window.desk` for component tests. Handlers may throw an IpcError-shaped object to fail. */
export function installBridge(handlers: Partial<Record<Channel, Handler>> = {}) {
  const listeners = new Map<string, Set<(p: unknown) => void>>();
  const calls: Array<{ channel: string; input: unknown }> = [];
  window.desk = {
    platform: 'darwin',
    invoke: async (channel: string, input: unknown) => {
      calls.push({ channel, input });
      const h = handlers[channel as Channel];
      if (!h) return { ok: false, error: { code: 'unknown_channel', message: channel } };
      try {
        return { ok: true, value: await h(input) };
      } catch (e) {
        return { ok: false, error: e as IpcError };
      }
    },
    on: (channel: PushChannel, cb: (p: unknown) => void) => {
      const set = listeners.get(channel) ?? new Set();
      listeners.set(channel, set);
      set.add(cb);
      return () => {
        set.delete(cb);
      };
    },
  };
  return { calls, emit: (channel: PushChannel, payload: unknown) => listeners.get(channel)?.forEach((l) => l(payload)) };
}
