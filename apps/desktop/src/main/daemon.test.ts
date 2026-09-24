import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { compareVersions, DaemonManager, type DaemonManagerOptions } from './daemon';
import { launchdPlist, plistPath } from './launchd';

let dir: string;
beforeEach(() => void (dir = mkdtempSync(join(tmpdir(), 'desk-dm-'))));
afterEach(() => rmSync(dir, { recursive: true, force: true }));

function manager(o: Partial<DaemonManagerOptions> = {}) {
  const calls: string[][] = [];
  const dataDir = join(dir, 'data');
  mkdirSync(dataDir, { recursive: true });
  const up = (pid = 42) => writeFileSync(join(dataDir, 'daemon.json'), JSON.stringify({ port: 1234, token: 't', pid, version: '1.0.0' }));
  const down = () => rmSync(join(dataDir, 'daemon.json'), { force: true });
  let version = '1.0.0';
  const m = new DaemonManager({
    dataDir,
    mode: 'dev',
    platform: 'darwin',
    home: join(dir, 'home'),
    uid: 501,
    bundledVersion: '1.0.0',
    execPath: '/Applications/Desk.app/Contents/MacOS/Desk',
    bundlePath: '/Applications/Desk.app/Contents/Resources/deskd/deskd.mjs',
    repoRoot: '/repo',
    nodePath: 'node',
    exec: async (file, args) => {
      calls.push([file, ...args]);
      if (args[0] === 'bootstrap' || args[0] === 'kickstart') {
        up(args[0] === 'kickstart' ? 43 : 42);
        version = '1.0.0';
      }
      if (args[0] === 'bootout') down();
      return { code: 0, stdout: '', stderr: '' };
    },
    spawnDetached: (file, args) => {
      calls.push(['spawn', file, ...args]);
      up();
    },
    kill: (pid) => {
      calls.push(['kill', String(pid)]);
      down();
    },
    fetchHealth: async () => ({ version, protocol_version: 1, proxy: 'up', uptime_s: 5 }),
    sleep: async () => {},
    startTimeoutMs: 200,
    ...o,
  });
  return { m, calls, up, down, dataDir, setVersion: (v: string) => void (version = v) };
}

describe('launchd plist', () => {
  it('runs the bundle with Electron as Node and escapes paths', () => {
    const xml = launchdPlist({ programArguments: ['/A & B/Desk', 'x.mjs'], env: { ELECTRON_RUN_AS_NODE: '1' }, workingDirectory: '/w', logFile: '/l/deskd.log' });
    expect(xml).toContain('<string>dev.desk.deskd</string>');
    expect(xml).toContain('<string>/A &amp; B/Desk</string>');
    expect(xml).toContain('<key>ELECTRON_RUN_AS_NODE</key>');
    expect(xml).toContain('<key>KeepAlive</key>');
  });
});

describe('DaemonManager', () => {
  it('reports status from daemon.json and health', async () => {
    const { m, up } = manager();
    expect(await m.status()).toMatchObject({ running: false, agent: 'unsupported', mode: 'dev' });
    up();
    expect(await m.status()).toMatchObject({ running: true, version: '1.0.0', pid: 42, proxy: 'up' });
  });

  it('starts the repo daemon through tsx in dev', async () => {
    const { m, calls } = manager();
    expect((await m.start()).running).toBe(true);
    expect(calls[0]).toEqual(['spawn', 'node', '--import', '/repo/node_modules/tsx/dist/loader.mjs', '/repo/apps/daemon/src/main.ts', '--data-dir', join(dir, 'data')]);
  });

  it('installs and bootstraps the LaunchAgent when packaged on macOS', async () => {
    const { m, calls } = manager({ mode: 'packaged' });
    expect((await m.start()).agent).toBe('installed');
    const file = plistPath(join(dir, 'home'));
    expect(readFileSync(file, 'utf8')).toContain('<string>/Applications/Desk.app/Contents/Resources/deskd/deskd.mjs</string>');
    expect(calls).toEqual([['launchctl', 'bootstrap', 'gui/501', file]]);
  });

  it('refreshes an older daemon under an installed agent, and leaves others alone', async () => {
    const { m, calls, up, setVersion } = manager({ mode: 'packaged' });
    up();
    setVersion('0.9.0');
    expect(await m.ensureCurrent()).toBe(false); // no agent installed: a daemon started by hand is left alone
    await m.installAgent(); // bootstrap brings up the bundled 1.0.0
    setVersion('0.9.0');
    calls.length = 0;
    expect(await m.ensureCurrent()).toBe(true);
    expect(calls.map((c) => c[1])).toEqual(['bootout', 'bootstrap']);
    calls.length = 0;
    expect(await m.ensureCurrent()).toBe(false);
    expect(calls).toEqual([]);
    expect(await manager().m.ensureCurrent()).toBe(false);
  });

  it('restarts with kickstart and stops with bootout', async () => {
    const { m, calls } = manager({ mode: 'packaged' });
    await m.start();
    calls.length = 0;
    expect((await m.restart()).pid).toBe(43);
    expect(calls[0]).toEqual(['launchctl', 'kickstart', '-k', 'gui/501/dev.desk.deskd']);
    expect((await m.stop()).running).toBe(false);
    expect(calls.at(-1)).toEqual(['launchctl', 'bootout', 'gui/501/dev.desk.deskd']);
  });

  it('repairs by reinstalling the LaunchAgent, or restarts in dev', async () => {
    const { m, calls, up } = manager({ mode: 'packaged' });
    await m.start();
    up(7); // the agent's daemon has since been replaced by another process; repair must bring up a fresh one
    calls.length = 0;
    const s = await m.repair();
    expect(s).toMatchObject({ running: true, agent: 'installed' });
    expect(calls.map((c) => c[1])).toEqual(['bootout', 'bootstrap']);
    const dev = manager();
    dev.up();
    expect((await dev.m.repair()).running).toBe(true);
    expect(dev.calls.map((c) => c[0])).toEqual(['kill', 'spawn']);
  });

  it('stops a dev daemon by pid and times out when a start never comes up', async () => {
    const { m, calls, up } = manager();
    up();
    expect((await m.stop()).running).toBe(false);
    expect(calls).toEqual([['kill', '42']]);
    const stuck = manager({ spawnDetached: () => {} }).m;
    await expect(stuck.start()).rejects.toMatchObject({ code: 'daemon_start_timeout' });
  });

  it('refuses to start automatically where there is no LaunchAgent', async () => {
    const { m } = manager({ mode: 'packaged', platform: 'win32' });
    await expect(m.start()).rejects.toMatchObject({ code: 'unsupported' });
    expect(existsSync(plistPath(join(dir, 'home')))).toBe(false);
  });

  it('compares versions numerically', () => {
    expect(compareVersions('0.9.0', '1.0.0')).toBeLessThan(0);
    expect(compareVersions('1.10.0', '1.9.3')).toBeGreaterThan(0);
    expect(compareVersions('1.0', '1.0.0')).toBe(0);
  });
});
