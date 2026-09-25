import { mkdirSync, mkdtempSync, rmSync, writeFileSync, type FSWatcher } from 'node:fs';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { DEV_RELOAD_JS, DevReload } from './dev';
import { openPush } from './testing';
import { attachUpgrades } from './upgrade';

const cleanups: Array<() => void | Promise<void>> = [];
afterEach(async () => {
  for (const c of cleanups.splice(0).reverse()) await c();
});

/** A DevReload on a temp build folder, reached through attachUpgrades like desk web's /__dev/reload. */
async function devServer(): Promise<{ dev: DevReload; uiDir: string; port: number }> {
  const root = mkdtempSync(join(tmpdir(), 'desk-web-dev-'));
  cleanups.push(() => rmSync(root, { recursive: true, force: true }));
  const uiDir = join(root, 'dist', 'browser');
  const dev = new DevReload(uiDir, 20);
  dev.start();
  const server = createServer((_req, res) => res.writeHead(404).end());
  let port = 0;
  attachUpgrades(server, { port: () => port, routes: { '/__dev/reload': dev.handleUpgrade } });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()));
  port = (server.address() as AddressInfo).port;
  cleanups.push(
    () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections();
        server.close(() => resolve());
      }),
  );
  // Runs before the server closes: server.close waits for the upgraded socket, which only dev.close ends.
  cleanups.push(() => dev.close());
  return { dev, uiDir, port };
}

describe('desk web --dev', () => {
  it('tells /__dev/reload sockets to reload once the build output changes', async () => {
    const { uiDir, port } = await devServer();
    const socket = openPush(port, { path: '/__dev/reload' });
    await socket.opened;
    mkdirSync(uiDir, { recursive: true });
    writeFileSync(join(uiDir, 'main-XYZ.js'), 'console.log(2);');
    expect(await socket.next((f) => f.text === 'reload', 5000)).toEqual({ text: 'reload' });
  });

  it('survives a bad frame and a failing watcher', async () => {
    const { dev, port } = await devServer();
    const bad = openPush(port, { path: '/__dev/reload' });
    await bad.opened;
    bad.ws.on('error', () => {});
    bad.ws.send('x'.repeat(2000));
    expect(await bad.closed).toBe(1009);
    (dev as unknown as { watcher: FSWatcher }).watcher.emit('error', new Error('EPERM'));
    const socket = openPush(port, { path: '/__dev/reload' });
    await socket.opened;
    dev.reload();
    expect(await socket.next((f) => f.text === 'reload')).toEqual({ text: 'reload' });
  });

  it('reloads through an external script on a same-origin socket', () => {
    expect(DEV_RELOAD_JS).toContain("new WebSocket('ws://' + location.host + '/__dev/reload')");
    expect(DEV_RELOAD_JS).toContain('location.reload()');
  });
});
