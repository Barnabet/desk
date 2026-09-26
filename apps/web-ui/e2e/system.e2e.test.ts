import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { Page } from 'playwright';
import { startWebE2E, type SignedIn, type WebE2E } from './harness';

let e2e: WebE2E;

beforeAll(async () => {
  // The full Chromium: the headless shell reports every notification permission as denied, even a granted one.
  e2e = await startWebE2E({ chromium: 'full' });
});

afterAll(async () => {
  await e2e?.close();
});

async function go(page: Page, hash: string) {
  await page.evaluate((h) => (window.location.hash = h), hash);
}

/** A signed-in browser past onboarding, on `hash`. */
async function openAt(hash: string): Promise<SignedIn> {
  const signed = await e2e.signIn();
  await signed.page.evaluate(() => localStorage.setItem('desk.onboarded', '1'));
  await go(signed.page, hash);
  return signed;
}

const hashOf = (page: Page) => page.evaluate(() => window.location.hash);
/** desk web's own settings file, written on each change. */
const webSettings = () => JSON.parse(readFileSync(join(e2e.dataDir, 'web-settings.json'), 'utf8')) as { notifications: boolean };

describe('the System screen in the browser', () => {
  it('edits the model registry: a duplicate cannot be saved, a new model reaches deskd and survives a reload, a draft can be discarded', async () => {
    const { context, page, problems } = await openAt('#/system');
    await page.getByRole('heading', { name: 'System', level: 1 }).waitFor();
    const reg = page.getByRole('region', { name: 'Model registry' });
    const field = (label: string) => reg.getByLabel(label, { exact: true });
    await field('Model 1 id').waitFor();
    expect(await reg.getByLabel(/^Model \d+ id$/).count()).toBe(4);
    expect(await field('Model 1 id').inputValue()).toBe('claude-opus-5-5');

    await reg.getByRole('button', { name: 'Add model' }).click();
    await field('Model 5 id').fill('claude-opus-5-5');
    await expect.poll(() => reg.getByRole('alert').textContent()).toContain('claude-opus-5-5 is listed twice.');
    expect(await reg.getByRole('button', { name: 'Save registry' }).isDisabled()).toBe(true);

    await field('Model 5 id').fill('claude-haiku-5');
    await field('Model 5 context window').fill('100000');
    await field('Model 5 max output tokens').fill('8000');
    // A number box holds whole numbers: it writes back the number it keeps, as the desktop's controlled box does.
    await field('Model 5 concurrency').fill('2.9');
    await expect.poll(() => field('Model 5 concurrency').inputValue()).toBe('2');
    const levels = reg.getByRole('group', { name: 'Model 5 reasoning levels' });
    await levels.getByRole('button', { name: 'low', exact: true }).click();
    await levels.getByRole('button', { name: 'high', exact: true }).click();
    await field('Model 5 default reasoning effort').selectOption('high');
    await field('Model 5 sees images').uncheck();
    await reg.getByRole('button', { name: 'Remove model 4', exact: true }).click();
    expect(await reg.getByRole('alert').count()).toBe(0);
    await e2e.shot(page, 'system-registry-draft');
    await reg.getByRole('button', { name: 'Save registry' }).click();
    await page.getByText('Model registry saved.').waitFor();

    const saved = await e2e.client().models.list();
    expect(saved.map((m) => m.id)).toEqual(['claude-opus-5-5', 'claude-fable-5-1', 'gpt-6-astra', 'claude-haiku-5']);
    expect(saved.at(-1)).toMatchObject({ family: 'claude', context_window: 100000, max_output_tokens: 8000, concurrency: 2, reasoning_efforts: ['low', 'high'], default_reasoning_effort: 'high', vision: false });
    expect(readFileSync(join(e2e.dataDir, 'models.json'), 'utf8')).toContain('claude-haiku-5');

    await page.reload();
    await field('Model 4 id').waitFor();
    expect(await field('Model 4 id').inputValue()).toBe('claude-haiku-5');
    expect(await field('Model 4 default reasoning effort').inputValue()).toBe('high');
    expect(await field('Model 4 sees images').isChecked()).toBe(false);
    expect(await reg.getByRole('group', { name: 'Model 4 reasoning levels' }).locator('button[aria-pressed="true"]').allTextContents()).toEqual(['low', 'high']);

    await field('Model 1 concurrency').fill('9');
    await reg.getByRole('button', { name: 'Discard changes' }).click();
    await expect.poll(() => field('Model 1 concurrency').inputValue()).toBe('4');
    expect(await reg.getByRole('button', { name: 'Discard changes' }).count()).toBe(0);
    expect(await reg.getByRole('button', { name: 'Save registry' }).isDisabled()).toBe(true);
    await e2e.shot(page, 'system');
    expect(problems).toEqual([]);
    await context.close();
  });

  it('shows deskd without LaunchAgent repair, keeps the notification switches, and jumps with ⌘K and ⌘P', async () => {
    const client = e2e.client();
    const { project } = await client.projects.create({ name: 'Tax paperwork', goal: 'File on time' });
    const { context, page, problems } = await openAt('#/system');
    await page.getByRole('heading', { name: 'System', level: 1 }).waitFor();

    // deskd runs in this test process: look only. Restart and Stop would signal it; Reveal logs would open Finder.
    const d = page.getByRole('region', { name: 'deskd' });
    await d.getByText('Running', { exact: true }).waitFor();
    await expect.poll(() => d.textContent()).toContain('Development (runs from this repository)');
    expect(await d.textContent()).toContain('No. desk web starts it from this repository.');
    expect(await d.getByRole('button', { name: /LaunchAgent/ }).count()).toBe(0);
    expect(await d.getByRole('button', { name: 'Restart' }).count()).toBe(1);
    expect(await d.getByRole('button', { name: 'Stop' }).count()).toBe(1);

    const data = page.getByRole('region', { name: 'Data' });
    await data.getByText(e2e.dataDir, { exact: true }).waitFor();
    await data.getByText('None yet', { exact: true }).waitFor();
    const usage = page.getByRole('region', { name: 'Usage' });
    await usage.getByRole('button', { name: 'All time' }).click();
    await expect.poll(() => usage.getByRole('button', { name: 'All time' }).getAttribute('aria-pressed')).toBe('true');

    // A browser page follows the system's color scheme: the choice shows System, and none of it can be pressed.
    const appearance = page.getByRole('region', { name: 'Appearance' }).getByRole('group', { name: 'Appearance' });
    expect(await appearance.getByRole('button', { name: 'System' }).getAttribute('aria-pressed')).toBe('true');
    for (const name of ['System', 'Light', 'Dark']) expect(await appearance.getByRole('button', { name }).isDisabled()).toBe(true);

    // Before this browser allows notifications, the hint says so, and the switch saves without waiting on the prompt
    // it opens (a headless browser shows none, so nobody answers it).
    const n = page.getByRole('region', { name: 'Notifications' });
    const fromApp = n.getByLabel(/From the app/);
    const fromDeskd = n.getByLabel(/From deskd/);
    await expect.poll(() => fromApp.isChecked()).toBe(true);
    await n.getByText('This browser has not allowed notifications from Desk yet.').waitFor();
    expect(await n.getByRole('button', { name: 'Allow notifications' }).count()).toBe(1);
    await fromApp.uncheck();
    await expect.poll(() => webSettings().notifications).toBe(false);
    await expect.poll(() => n.getByText(/notifications from Desk/).count()).toBe(0);
    // Each switch is disabled while its write is pending: check() waits until it takes clicks again.
    await fromApp.check();
    await expect.poll(() => webSettings().notifications).toBe(true);
    await expect.poll(() => fromApp.isEnabled()).toBe(true);
    expect(await fromApp.isChecked()).toBe(true);

    await context.grantPermissions(['notifications'], { origin: new URL(e2e.web.url).origin });
    await page.reload();
    await expect.poll(() => fromApp.isChecked()).toBe(true);
    expect(await n.getByText(/notifications from Desk/).count()).toBe(0);
    await fromApp.uncheck();
    await expect.poll(() => webSettings().notifications).toBe(false);
    await fromApp.check();
    await expect.poll(() => webSettings().notifications).toBe(true);
    await expect.poll(() => fromDeskd.isChecked()).toBe(true);
    await fromDeskd.uncheck();
    await expect.poll(async () => (await client.config.get()).notifications).toBe('off');
    await fromDeskd.check();
    await expect.poll(async () => (await client.config.get()).notifications).toBe('auto');
    await expect.poll(() => fromDeskd.isEnabled()).toBe(true);
    await e2e.shot(page, 'system-notifications');

    // ⌘K works only in the shell: during onboarding the key is the browser's, and no palette opens once the shell is back.
    const palette = page.getByRole('dialog', { name: 'Search Desk' });
    await go(page, '#/onboarding');
    await page.getByText('Welcome to Desk').waitFor();
    await page.keyboard.press('ControlOrMeta+k');
    await go(page, '#/system');
    await page.getByRole('heading', { name: 'System', level: 1 }).waitFor();
    expect(await palette.count()).toBe(0);

    await page.keyboard.press('ControlOrMeta+k');
    await palette.getByRole('combobox').fill('tax');
    await palette.getByRole('option', { name: /Tax paperwork/ }).waitFor();
    await e2e.shot(page, 'palette');
    await page.keyboard.press('Enter');
    await expect.poll(() => hashOf(page)).toBe(`#/p/${project.id}/conversation`);
    await palette.waitFor({ state: 'detached' });

    await page.keyboard.press('ControlOrMeta+p');
    const switcher = page.getByRole('dialog', { name: 'Switch project' });
    await switcher.waitFor();
    await page.keyboard.press('Escape');
    await switcher.waitFor({ state: 'detached' });
    expect(problems).toEqual([]);
    await context.close();
  });
});
