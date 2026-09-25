import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { DaemonStatus } from '@desk/bff/contract';
import { UserFacingError, type Broker, type DaemonManager, type HandlerContext } from '@desk/bff/server';
import type { WebSettingsStore } from './files';

/** The DaemonManager calls desk web makes (repair is not offered on the web). */
export type WebDaemon = Pick<DaemonManager, 'status' | 'start' | 'restart' | 'stop'>;

export type WebContextDeps = {
  dataDir: string;
  version: string;
  platform: NodeJS.Platform;
  broker: Pick<Broker, 'getClient' | 'snapshot' | 'watch' | 'unwatch' | 'reconnect'>;
  daemon: WebDaemon;
  settings: WebSettingsStore;
  /** Opens a folder on this computer with the platform opener (app.revealLogs). */
  openPath(target: string): Promise<void>;
  /** The notification setting may have changed: desk web recomputes the stream hello. */
  onSettingsChange?(): void;
};

const notOffered = (what: string): never => {
  throw new UserFacingError('not_offered', `${what} happens in the browser, not in desk web.`);
};

/** desk web's HandlerContext: the browser does the host operations it can; the rest match the desktop app. */
export function webHandlerContext(d: WebContextDeps, senderId: number): HandlerContext {
  const thenReconnect = async (run: () => Promise<DaemonStatus>): Promise<DaemonStatus> => {
    const status = await run();
    await d.broker.reconnect();
    return status;
  };
  return {
    senderId,
    client: () => d.broker.getClient(),
    broker: d.broker,
    daemon: {
      status: () => d.daemon.status(),
      start: () => thenReconnect(() => d.daemon.start()),
      restart: () => thenReconnect(() => d.daemon.restart()),
      stop: () => thenReconnect(() => d.daemon.stop()),
      repair: async () => {
        throw new UserFacingError('not_offered', 'Repairing the LaunchAgent is only offered in the Desk app. Restart deskd instead.');
      },
    },
    app: {
      info: () => ({ version: d.version, platform: d.platform, packaged: false, dataDir: d.dataDir }),
      openExternal: async () => notOffered('Opening a link'),
      pickFolder: async () => notOffered('Choosing a folder'),
      revealLogs: async () => {
        const dir = join(d.dataDir, 'logs');
        mkdirSync(dir, { recursive: true });
        await d.openPath(dir);
      },
      saveFile: async () => notOffered('Saving a file'),
      openMain: () => notOffered('Opening the main window'),
      settings: () => ({ notifications: d.settings.get().notifications }),
      updateSettings: (patch) => {
        const next = d.settings.update(patch.notifications === undefined ? {} : { notifications: patch.notifications });
        d.onSettingsChange?.();
        return { notifications: next.notifications };
      },
    },
  };
}
