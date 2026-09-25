import { mkdirSync, watch, type FSWatcher } from 'node:fs';
import type { IncomingMessage } from 'node:http';
import { dirname } from 'node:path';
import type { Duplex } from 'node:stream';
import { WebSocketServer } from 'ws';

/** Served at /__dev/reload.js under `desk web --dev`: an external script, as the CSP requires. */
export const DEV_RELOAD_JS = `(() => {
  const ws = new WebSocket('ws://' + location.host + '/__dev/reload');
  ws.onmessage = () => location.reload();
})();
`;

/** Watches the Angular output and tells /__dev/reload sockets to reload once a rebuild settles. */
export class DevReload {
  private readonly wss = new WebSocketServer({ noServer: true, maxPayload: 1024 });
  private watcher: FSWatcher | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(
    private readonly uiDir: string,
    private readonly debounceMs = 150,
  ) {}

  start(): void {
    // The build folder's parent: `ng build --watch` may replace the folder itself.
    const dir = dirname(this.uiDir);
    mkdirSync(dir, { recursive: true });
    this.watcher = watch(dir, { recursive: true }, () => {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.reload(), this.debounceMs);
    });
    // Unhandled, an error (EPERM on Windows once the folder is deleted) would crash desk web; reloads just stop.
    this.watcher.on('error', () => {});
  }

  reload(): void {
    for (const ws of this.wss.clients) if (ws.readyState === ws.OPEN) ws.send('reload');
  }

  readonly handleUpgrade = (req: IncomingMessage, socket: Duplex, head: Buffer): void => {
    // A bad frame (over maxPayload, unmasked, invalid UTF-8) emits 'error' before ws closes the socket itself:
    // unhandled, it would crash desk web.
    this.wss.handleUpgrade(req, socket, head, (ws) => {
      ws.on('error', () => {});
    });
  };

  close(): void {
    clearTimeout(this.timer);
    this.watcher?.close();
    for (const ws of this.wss.clients) ws.terminate();
    this.wss.close();
  }
}
