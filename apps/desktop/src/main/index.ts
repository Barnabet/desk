import { join } from 'node:path';
import { app, BrowserWindow } from 'electron';

app.whenReady().then(() => {
  const win = new BrowserWindow({ width: 800, height: 600, webPreferences: { preload: join(__dirname, 'preload.cjs'), sandbox: true, contextIsolation: true } });
  void win.loadURL(process.env.DESK_RENDERER_URL ?? `file://${join(__dirname, 'renderer', 'index.html')}`);
});
