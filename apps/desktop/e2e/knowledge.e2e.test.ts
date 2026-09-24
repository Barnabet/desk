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
  dir = mkdtempSync(join(tmpdir(), 'desk-knowledge-'));
  fake = await startFakeModel(() => text('Noted.'));
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

const b64 = (s: string) => Buffer.from(s).toString('base64');
/** Set DESK_E2E_SHOTS=<dir> to keep screenshots of each screen for visual review. */
async function shot(page: Page, name: string) {
  const out = process.env.DESK_E2E_SHOTS;
  if (out) await page.screenshot({ path: join(out, `${name}.png`) });
}

async function go(page: Page, hash: string) {
  await page.evaluate((h) => (window.location.hash = h), hash);
}

describe('knowledge and system screens, end to end', () => {
  it('refines a skill and restores v1, uploads to the library, corrects memory, changes settings, and searches', async () => {
    const client = clientFromDataDir(join(dir, 'data'));
    const { project } = await client.projects.create({ name: 'Tax paperwork', goal: 'File on time' });
    await client.skills.save({}, 'receipt-sorting', { description: 'Sort receipts into tax categories', instructions: 'Sort by category.', change_note: 'First version' });
    await client.memory.add(project.id, { kind: 'fact', content: 'The accountant is Dana' });

    const page = await app.firstWindow();
    await page.evaluate(() => localStorage.setItem('desk.onboarded', '1'));

    // Skills: refine by hand, then restore v1.
    await go(page, '#/skills');
    await page.getByRole('button', { name: /^receipt-sorting, global, version 1/ }).click();
    const panel = page.getByRole('article', { name: 'Skill receipt-sorting' });
    await panel.getByText('First version').waitFor();
    await panel.getByRole('button', { name: 'Edit' }).click();
    const editor = page.getByRole('dialog', { name: 'Edit receipt-sorting' });
    await editor.getByLabel('Instructions (SKILL.md)').fill('Sort by category, then flag unclear ones.');
    await editor.getByLabel('Change note (optional)').fill('Flag unclear receipts');
    await editor.getByRole('button', { name: 'Save new version' }).click();
    await panel.getByText('Flag unclear receipts').waitFor();
    await panel.getByRole('tab', { name: /^History/ }).click();
    await expect.poll(() => panel.getByLabel('Changes from v1 to v2').textContent()).toContain('+ Sort by category, then flag unclear ones.');
    await shot(page, 'k1-skills');
    await panel.getByRole('button', { name: 'Restore' }).click();
    await page.getByRole('dialog', { name: 'Restore v1?' }).getByRole('button', { name: 'Restore' }).click();
    await expect.poll(async () => (await client.skills.get({}, 'receipt-sorting')).version).toBe(3);
    expect((await client.skills.get({}, 'receipt-sorting')).instructions.trim()).toBe('Sort by category.');

    // Library: upload through the picker and preview it.
    await go(page, `#/p/${project.id}/library`);
    await page.getByTestId('library-input').setInputFiles({ name: 'checklist.md', mimeType: 'text/markdown', buffer: Buffer.from('# Documents to gather\n\n- W-2\n- 1099') });
    const preview = page.getByRole('complementary', { name: 'Preview' });
    await preview.getByRole('heading', { name: 'Documents to gather' }).waitFor({ timeout: 15_000 });
    await page.getByRole('button', { name: /checklist\.md/ }).first().waitFor();
    await shot(page, 'k2-library');

    // Memory: correct an entry; the old version stays in the chain.
    await go(page, `#/p/${project.id}/memory`);
    const entry = page.getByRole('listitem', { name: /The accountant is Dana/ });
    await entry.getByRole('button', { name: 'Correct' }).click();
    await entry.getByLabel('Correct this entry').fill('The accountant is Dana Reyes');
    await entry.getByRole('button', { name: 'Save correction' }).click();
    const corrected = page.getByRole('listitem', { name: /The accountant is Dana Reyes/ });
    await corrected.getByRole('button', { name: 'Corrected 1 time' }).click();
    await corrected.getByRole('list', { name: 'Earlier versions' }).getByText('The accountant is Dana').waitFor();
    expect((await client.memory.list(project.id)).map((m) => m.content)).toEqual(['The accountant is Dana Reyes']);
    await shot(page, 'k3-memory');

    // Settings: review rounds and a policy rule.
    await go(page, `#/p/${project.id}/settings`);
    const style = page.getByRole('region', { name: 'How Desk works' });
    await style.getByLabel('Review rounds').fill('3');
    await style.getByRole('button', { name: 'Save' }).click();
    await expect.poll(async () => (await client.projects.get(project.id)).project.settings.review_rounds).toBe(3);
    const policy = page.getByRole('region', { name: 'Policy' });
    await policy.getByRole('button', { name: 'Add rule' }).click();
    await policy.getByLabel('Rule 10 tool').fill('web_fetch');
    await policy.getByLabel('Rule 10 action').selectOption('deny');
    await policy.getByRole('button', { name: 'Save policy' }).click();
    await expect.poll(async () => (await client.projects.get(project.id)).project.settings.policy.at(-1)).toEqual({ tool: 'web_fetch', action: 'deny' });
    await shot(page, 'k4-settings');

    // System shows the running daemon and the registry.
    await go(page, '#/system');
    await page.getByRole('region', { name: 'deskd' }).getByText('Running').waitFor();
    await page.getByRole('region', { name: 'Model registry' }).getByLabel('Model 1 id').waitFor();
    await shot(page, 'k5-system');

    // ⌘K finds the project.
    await page.keyboard.press('Meta+k');
    await page.getByRole('combobox', { name: /Search projects/ }).fill('tax');
    await shot(page, 'k6-palette');
    await page.keyboard.press('Enter');
    await expect.poll(() => page.evaluate(() => window.location.hash)).toBe(`#/p/${project.id}/conversation`);
  });
});
