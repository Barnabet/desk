import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { _electron as electron, type ElectronApplication, type Page } from 'playwright';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { clientFromDataDir } from '@desk/client/node';
import { startDaemon, type RunningDaemon } from '@desk/daemon';
import { startFakeModel, text, type FakeModelServer } from '@desk/fake-model';

const appDir = fileURLToPath(new URL('..', import.meta.url));
let dir: string;
let fake: FakeModelServer;
let daemon: RunningDaemon;
let app: ElectronApplication;

beforeAll(async () => {
  dir = mkdtempSync(join(tmpdir(), 'desk-builtins-e2e-'));
  fake = await startFakeModel(() => text('Noted.'));
  // Desk's real built-in skills, verified from the repo's catalog/skills; no environment is built here.
  daemon = await startDaemon({ dataDir: join(dir, 'data'), port: 0, modelConfig: { baseURL: fake.url, apiKey: 'test' } });
  const { default: electronPath } = await import('electron');
  app = await electron.launch({
    executablePath: electronPath as unknown as string,
    args: [appDir],
    env: { ...process.env, DESK_DATA_DIR: join(dir, 'data'), DESK_USER_DATA: join(dir, 'user'), DESK_E2E: '1' },
  });
});

afterAll(async () => {
  await app?.close();
  await daemon?.stop();
  await fake?.close();
  rmSync(dir, { recursive: true, force: true });
});

/** Set DESK_E2E_SHOTS=<dir> to keep screenshots of each screen for visual review. */
async function shot(page: Page, name: string) {
  const out = process.env.DESK_E2E_SHOTS;
  if (out) await page.screenshot({ path: join(out, `${name}.png`) });
}

describe('built-in skills, end to end', () => {
  it('lists the built-ins, turns one off, and duplicates one into your skills', async () => {
    const client = clientFromDataDir(join(dir, 'data'));
    const page = await app.firstWindow();
    await page.evaluate(() => localStorage.setItem('desk.onboarded', '1'));
    await page.evaluate(() => (window.location.hash = '#/skills'));
    await page.getByRole('group', { name: 'View' }).getByRole('button', { name: 'List' }).click();

    const group = page.getByRole('region', { name: 'Built into Desk' });
    await group.waitFor();
    expect(await group.getByRole('switch').count()).toBe(12);
    await group.getByText('Set up on first use').first().waitFor();
    await shot(page, 'b1-builtins');

    const pdf = (await client.builtins.list()).find((b) => b.name === 'pdf-toolkit')!;
    await group.getByRole('switch', { name: pdf.title }).click();
    await expect.poll(async () => (await client.builtins.list()).find((b) => b.name === 'pdf-toolkit')?.enabled).toBe(false);
    await group.getByRole('switch', { name: pdf.title, checked: false }).waitFor();

    const images = (await client.builtins.list()).find((b) => b.name === 'images')!;
    await group.getByRole('button', { name: `Open ${images.title}` }).click();
    const panel = page.getByRole('article', { name: 'Built-in skill images' });
    await panel.getByRole('tab', { name: 'Instructions' }).click();
    await panel.getByRole('button', { name: 'Duplicate to my skills' }).click();
    await page.getByRole('dialog', { name: 'Duplicate images' }).getByRole('button', { name: 'Duplicate' }).click();

    const copy = page.getByRole('article', { name: 'Skill images' });
    await copy.getByText(/Customised from the built-in skill/).waitFor();
    expect((await client.skills.list({})).map((s) => s.name)).toContain('images');
    await group.getByRole('button', { name: `Open ${images.title}` }).getByText('Shadowed by your global skill').waitFor();
    await shot(page, 'b2-duplicate');
  });
});
