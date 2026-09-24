import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';
import { INVOKE_CHANNEL, PUSH_CHANNELS } from '../shared/channels';

const push = new Set<string>(PUSH_CHANNELS);

/** The only surface the renderer gets: validated calls into main and a few push channels. */
const bridge = {
  invoke: (channel: string, input: unknown) => ipcRenderer.invoke(INVOKE_CHANNEL, channel, input),
  on: (channel: string, cb: (payload: unknown) => void) => {
    if (!push.has(channel)) throw new Error(`Unknown channel: ${channel}`);
    const listener = (_e: IpcRendererEvent, payload: unknown) => cb(payload);
    ipcRenderer.on(channel, listener);
    return () => {
      ipcRenderer.removeListener(channel, listener);
    };
  },
  platform: process.platform,
};

contextBridge.exposeInMainWorld('desk', bridge);
