import { execFile, spawn } from 'node:child_process';
import { mkdirSync, openSync } from 'node:fs';
import { writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { basename, join, resolve } from 'node:path';
import { app, BrowserWindow, dialog, ipcMain, Menu, Notification, shell, webContents } from 'electron';
import { clientFromDataDir, defaultDataDir, readDaemonInfo } from '@desk/client/node';
import type { AttentionItem } from '@desk/protocol';
import { INVOKE_CHANNEL } from '../shared/channels';
import { Broker } from './broker';
import { DaemonManager } from './daemon';
import { UserFacingError } from './errors';
import { dispatch, type HandlerContext } from './handlers';
import { createLog } from './log';
import { buildAppMenu } from './menu';
import { attentionRoute, notificationFor } from './notify';
import { AppSettingsStore } from './settings';
import { TrayPopover } from './popover';
import { DeskTray } from './tray';
import { createMainWindow, hardenSession, isAppUrl, registerAppScheme, serveRenderer } from './windows';

const e2e = process.env.DESK_E2E === '1';
if (process.env.DESK_USER_DATA) app.setPath('userData', process.env.DESK_USER_DATA);
registerAppScheme();

const testHooks = { trayTitle: () => '', notifications: [] as Array<{ title: string; body: string }>, togglePopover: () => {} };
if (e2e) (globalThis as { __deskTest?: typeof testHooks }).__deskTest = testHooks;

async function start(): Promise<void> {
  const dataDir = defaultDataDir();
  const log = createLog(join(app.getPath('logs'), 'desktop.log'));
  const settings = new AppSettingsStore(join(app.getPath('userData'), 'settings.json'));
  const preload = join(__dirname, 'preload.cjs');

  const daemon = new DaemonManager({
    dataDir,
    mode: app.isPackaged ? 'packaged' : 'dev',
    platform: process.platform,
    home: homedir(),
    uid: process.getuid?.() ?? 0,
    bundledVersion: app.getVersion(),
    execPath: process.execPath,
    bundlePath: join(process.resourcesPath, 'deskd', 'deskd.mjs'),
    repoRoot: resolve(__dirname, '..', '..', '..'),
    nodePath: process.env.DESK_NODE ?? 'node',
    exec: (file, args) =>
      new Promise((done) =>
        execFile(file, args, (err, stdout, stderr) => done({ code: err ? (typeof err.code === 'number' ? err.code : 1) : 0, stdout: String(stdout), stderr: String(stderr) })),
      ),
    spawnDetached: (file, args, o) => {
      const fd = openSync(o.logFile, 'a');
      const env: NodeJS.ProcessEnv = { ...process.env, ...o.env };
      delete env.ELECTRON_RUN_AS_NODE;
      spawn(file, args, { cwd: o.cwd, env, detached: true, stdio: ['ignore', fd, fd] }).unref();
    },
    kill: (pid) => process.kill(pid, 'SIGTERM'),
  });

  let tray: DeskTray | null = null;
  const popover = new TrayPopover({ preload, hideOnBlur: !e2e });
  const mainWindow = () => BrowserWindow.getAllWindows().find((w) => !popover.is(w) && !w.isDestroyed());
  const openRoute = (route?: string) => {
    popover.hide();
    const win = mainWindow();
    if (!win) {
      createMainWindow({ preload, ...(route ? { route } : {}) });
      return;
    }
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
    if (route) win.webContents.send('desk:navigate', route);
  };

  const notify = (items: AttentionItem[]) => {
    const focused = BrowserWindow.getFocusedWindow();
    if (!settings.get().notifications || (focused && !popover.is(focused))) return;
    for (const item of items.slice(0, 3)) {
      const n = notificationFor(item);
      if (e2e) {
        testHooks.notifications.push(n);
        continue;
      }
      if (!Notification.isSupported()) continue;
      const note = new Notification({ title: n.title, body: n.body });
      note.on('click', () => openRoute(attentionRoute(item)));
      note.show();
    }
  };

  const broker = new Broker({
    connect: () => (readDaemonInfo(dataDir) ? clientFromDataDir(dataDir) : null),
    credentials: () => {
      const info = readDaemonInfo(dataDir);
      return info ? { baseUrl: `http://127.0.0.1:${info.port}`, token: info.token } : null;
    },
    send: (id, channel, payload) => {
      const wc = webContents.fromId(id);
      if (wc && !wc.isDestroyed()) wc.send(channel, payload);
    },
    broadcast: (channel, payload) => {
      for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed()) w.webContents.send(channel, payload);
    },
    hello: () => ({ client: 'desktop', notifications: settings.get().notifications }),
    onChange: (state) => {
      tray?.update(state);
      if (process.platform === 'darwin') app.dock?.setBadge(state.attention.length ? String(state.attention.length) : '');
    },
    onAttentionAdded: notify,
    log,
  });

  const ctx = (senderId: number): HandlerContext => ({
    senderId,
    client: () => broker.getClient(),
    broker,
    daemon: {
      status: () => daemon.status(),
      start: async () => {
        const s = await daemon.start();
        await broker.reconnect();
        return s;
      },
      restart: async () => {
        const s = await daemon.restart();
        await broker.reconnect();
        return s;
      },
      stop: async () => {
        const s = await daemon.stop();
        await broker.reconnect();
        return s;
      },
      repair: async () => {
        const s = await daemon.repair();
        await broker.reconnect();
        return s;
      },
    },
    app: {
      info: () => ({ version: app.getVersion(), platform: process.platform, packaged: app.isPackaged, dataDir }),
      openExternal: (url) => shell.openExternal(url),
      pickFolder: async (purpose) => {
        const opts: Electron.OpenDialogOptions = {
          properties: ['openDirectory', 'createDirectory'],
          ...(purpose === 'skill-import' ? { title: 'Import a skill folder', defaultPath: join(homedir(), '.claude', 'skills') } : { title: 'Add a source folder' }),
        };
        const win = BrowserWindow.getFocusedWindow();
        const r = win ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts);
        return r.canceled ? null : (r.filePaths[0] ?? null);
      },
      revealLogs: async () => {
        const dir = join(dataDir, 'logs');
        mkdirSync(dir, { recursive: true });
        const err = await shell.openPath(dir);
        if (err) throw new UserFacingError('reveal_failed', err);
      },
      openMain: (route) => openRoute(route),
      saveFile: async (name, data) => {
        const r = await dialog.showSaveDialog({ defaultPath: join(app.getPath('downloads'), basename(name)) });
        if (r.canceled || !r.filePath) return false;
        await writeFile(r.filePath, data);
        return true;
      },
      settings: () => settings.get(),
      updateSettings: (patch) => {
        const before = settings.get();
        const next = settings.update(patch);
        if (before.notifications !== next.notifications) broker.updateHello();
        return next;
      },
    },
  });

  ipcMain.handle(INVOKE_CHANNEL, (event, channel: unknown, input: unknown) => {
    if (!isAppUrl(event.senderFrame?.url ?? '')) return { ok: false, error: { code: 'forbidden', message: 'Untrusted sender.' } };
    return dispatch(channel, input, ctx(event.sender.id), (err) => log(`ipc ${String(channel).slice(0, 64)} failed`, err));
  });
  app.on('web-contents-created', (_e, wc) => {
    const id = wc.id;
    wc.on('destroyed', () => broker.dropSender(id));
  });

  hardenSession();
  serveRenderer(join(__dirname, 'renderer'));
  Menu.setApplicationMenu(buildAppMenu({ navigate: openRoute, dev: !app.isPackaged }));
  tray = new DeskTray({ open: openRoute, quit: () => app.quit(), toggle: (bounds) => popover.toggle(bounds) });
  testHooks.trayTitle = () => tray?.title ?? '';
  testHooks.togglePopover = () => popover.toggle();
  tray.update(broker.snapshot());

  if (app.isPackaged) await daemon.ensureCurrent().catch((err) => log('daemon refresh failed', err));
  void broker.start();
  createMainWindow({ preload });

  app.on('activate', () => openRoute());
  app.on('second-instance', () => openRoute());
  app.on('before-quit', () => broker.stop());
}

if (!e2e && !app.requestSingleInstanceLock()) {
  app.quit();
} else {
  // Closing the last window keeps Desk in the menu bar; Quit is in the tray menu and ⌘Q.
  app.on('window-all-closed', () => {});
  app.whenReady().then(start, (err) => console.error(err));
}
