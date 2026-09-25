import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { DaemonNotRunning } from '@desk/client';
import { initialGlobalState, type DaemonStatus } from '@desk/bff/contract';
import { dispatch } from '@desk/bff/server';
import { WebSettingsStore, webPaths } from './files';
import { webHandlerContext, type WebDaemon } from './web-context';

let dataDir: string;
beforeEach(() => void (dataDir = mkdtempSync(join(tmpdir(), 'desk-web-ctx-'))));
afterEach(() => rmSync(dataDir, { recursive: true, force: true }));

const status = (running: boolean): DaemonStatus => ({
  running,
  version: running ? '1.0.0' : null,
  pid: running ? 42 : null,
  uptime_s: null,
  proxy: null,
  mode: 'web',
  bundledVersion: '1.0.0',
  build: null,
  bundledBuild: null,
  agent: 'unsupported',
});

function setup() {
  const opened: string[] = [];
  const events: string[] = [];
  const broker = {
    getClient: () => {
      throw new DaemonNotRunning(dataDir);
    },
    snapshot: () => initialGlobalState(),
    watch: async () => {},
    unwatch: () => {},
    reconnect: async () => void events.push('reconnect'),
  };
  const daemon: WebDaemon = {
    status: async () => (events.push('status'), status(false)),
    start: async () => (events.push('start'), status(true)),
    restart: async () => (events.push('restart'), status(true)),
    stop: async () => (events.push('stop'), status(false)),
  };
  const settings = new WebSettingsStore(dataDir);
  const ctx = webHandlerContext(
    { dataDir, version: '1.0.0', platform: 'darwin', broker, daemon, settings, openPath: async (p) => void opened.push(p), onSettingsChange: () => void events.push('settings') },
    0,
  );
  return { ctx, opened, events, settings };
}

describe('the web HandlerContext', () => {
  it('reports the web host: not packaged, this data dir', async () => {
    const { ctx } = setup();
    expect(await dispatch('app.info', {}, ctx)).toEqual({ ok: true, value: { version: '1.0.0', platform: 'darwin', packaged: false, dataDir } });
  });

  it('refuses the operations the browser does itself, and LaunchAgent repair', async () => {
    const { ctx } = setup();
    const calls: Array<[string, unknown]> = [
      ['app.openExternal', { url: 'https://example.com' }],
      ['app.pickFolder', { purpose: 'source' }],
      ['app.saveFile', { name: 'a.txt', data: new Uint8Array([1]) }],
      ['app.openMain', {}],
      ['daemon.repair', {}],
    ];
    for (const [op, input] of calls) {
      expect(await dispatch(op, input, ctx), op).toMatchObject({ ok: false, error: { code: 'not_offered' } });
    }
  });

  it('opens the logs folder with the platform opener', async () => {
    const { ctx, opened } = setup();
    expect(await dispatch('app.revealLogs', {}, ctx)).toEqual({ ok: true, value: { ok: true } });
    expect(opened).toEqual([join(dataDir, 'logs')]);
    expect(existsSync(join(dataDir, 'logs'))).toBe(true);
  });

  it('keeps the notification setting in web-settings.json and reports the change', async () => {
    const { ctx, events, settings } = setup();
    expect(await dispatch('app.settings', {}, ctx)).toEqual({ ok: true, value: { notifications: true } });
    expect(await dispatch('app.updateSettings', { notifications: false }, ctx)).toEqual({ ok: true, value: { notifications: false } });
    expect(events).toEqual(['settings']);
    expect(JSON.parse(readFileSync(webPaths(dataDir).settings, 'utf8'))).toEqual({ notifications: false });
    settings.update({ port: 7500 });
    expect(await dispatch('app.settings', {}, ctx)).toEqual({ ok: true, value: { notifications: false } });
  });

  it('starts, restarts and stops deskd, reconnecting the broker after each', async () => {
    const { ctx, events } = setup();
    expect(await dispatch('daemon.status', {}, ctx)).toMatchObject({ ok: true, value: { running: false, mode: 'web' } });
    expect(await dispatch('daemon.start', {}, ctx)).toMatchObject({ ok: true, value: { running: true } });
    expect(await dispatch('daemon.restart', {}, ctx)).toMatchObject({ ok: true, value: { running: true } });
    expect(await dispatch('daemon.stop', {}, ctx)).toMatchObject({ ok: true, value: { running: false } });
    expect(events).toEqual(['status', 'start', 'reconnect', 'restart', 'reconnect', 'stop', 'reconnect']);
  });

  it("reaches deskd through the broker's client", async () => {
    const { ctx } = setup();
    expect(await dispatch('health', {}, ctx)).toEqual({ ok: false, error: { code: 'daemon_not_running', message: 'Desk is not running.' } });
  });
});
