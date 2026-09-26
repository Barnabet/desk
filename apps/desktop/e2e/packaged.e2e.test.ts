import { spawn, type ChildProcess } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { _electron as electron, type ElectronApplication } from 'playwright';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { clientFromDataDir, readDaemonInfo } from '@desk/client/node';
import { startFakeModel, text, type FakeModelServer } from '@desk/fake-model';

/**
 * The packaged app (`pnpm --filter @desk/desktop package`) from a clean data dir. deskd runs exactly as its LaunchAgent
 * would run it (the app's own binary as Node, the bundled deskd.mjs), so the test never installs a real LaunchAgent.
 * Skipped when there is no packaged build.
 */
const release = fileURLToPath(new URL('../release', import.meta.url));
const appPath = join(release, process.arch === 'arm64' ? 'mac-arm64' : 'mac', 'Desk.app');
const binary = join(appPath, 'Contents', 'MacOS', 'Desk');
const bundle = join(appPath, 'Contents', 'Resources', 'deskd', 'deskd.mjs');
const packaged = process.platform === 'darwin' && existsSync(binary);

let dir: string;
let fake: FakeModelServer;
let deskd: ChildProcess;
let app: ElectronApplication;

async function waitFor<T>(fn: () => T | null | undefined | Promise<T | null | undefined>, ms = 20_000): Promise<T> {
  const deadline = Date.now() + ms;
  for (;;) {
    try {
      const v = await fn();
      if (v) return v;
    } catch {
      // Not yet.
    }
    if (Date.now() > deadline) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 200));
  }
}

beforeAll(async () => {
  if (!packaged) return;
  dir = mkdtempSync(join(tmpdir(), 'desk-packaged-'));
  fake = await startFakeModel(() => text('On it: two threads, one for the data source and one for invites.'));
  deskd = spawn(binary, [bundle, '--port', '0', '--data-dir', join(dir, 'data')], {
    env: { PATH: process.env.PATH ?? '', HOME: process.env.HOME ?? '', ELECTRON_RUN_AS_NODE: '1', DESK_BUNDLED: '1', DESK_OPENAI_BASE_URL: fake.url, DESK_OPENAI_API_KEY: 'test' },
    stdio: 'ignore',
  });
  await waitFor(async () => {
    const info = readDaemonInfo(join(dir, 'data'));
    return info && (await clientFromDataDir(join(dir, 'data')).health()).version;
  });
  app = await electron.launch({
    executablePath: binary,
    env: { ...process.env, DESK_DATA_DIR: join(dir, 'data'), DESK_USER_DATA: join(dir, 'user'), DESK_E2E: '1', DESK_OPENAI_BASE_URL: fake.url, DESK_OPENAI_API_KEY: 'test' },
  });
});

afterAll(async () => {
  if (!packaged) return;
  await app?.close();
  deskd?.kill('SIGTERM');
  await waitFor(() => !readDaemonInfo(join(dir, 'data')), 10_000).catch(() => {});
  await fake?.close();
  rmSync(dir, { recursive: true, force: true });
});

describe.skipIf(!packaged)('packaged app', () => {
  it('runs the bundled deskd under the app binary and onboards from a clean data dir', async () => {
    const client = clientFromDataDir(join(dir, 'data'));
    const health = await client.health();
    expect(health).toMatchObject({ version: '1.0.0' });
    // Desk's built-in skills load from Resources and match builtins.json.
    const builtins = await client.builtins.list();
    expect(builtins).toHaveLength(12);
    expect(builtins.filter((b) => b.broken).map((b) => b.name)).toEqual([]);

    const page = await app.firstWindow();
    await page.getByText('Welcome to Desk').waitFor();
    await page.getByText('deskd 1.0.0 is running.').waitFor();
    await page.getByRole('button', { name: 'Continue' }).click();
    await page.getByRole('button', { name: 'Test connection' }).click();
    await page.getByText(/^Connected\. \d+ models? available\.$/).waitFor();
    await page.getByRole('button', { name: 'Continue' }).click();
    await page.getByLabel('Name').fill('Launch');
    await page.getByLabel('Goal').fill('Relaunch onboarding next month');
    await page.getByRole('button', { name: 'Create project' }).click();
    await page.getByRole('heading', { name: 'Launch', exact: true }).waitFor();

    const [project] = await client.projects.list();
    await page.evaluate((h) => (location.hash = h), `#/p/${project!.id}/conversation`);
    await page.getByLabel('Message Desk').fill('Split the relaunch into threads');
    await page.getByLabel('Message Desk').press('Enter');
    await page.getByText('On it: two threads, one for the data source and one for invites.').waitFor();

    await page.evaluate(() => (location.hash = '#/system'));
    await page.getByText('Bundled deskd 1.0.0').waitFor();
    expect(await page.getByText('Running', { exact: true }).isVisible()).toBe(true);
  });
});
