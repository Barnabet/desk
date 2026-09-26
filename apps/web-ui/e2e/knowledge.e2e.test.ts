import { join } from 'node:path';
import type { Page } from 'playwright';
import { startWebE2E, type SignedIn, type WebE2E } from './harness';

let e2e: WebE2E;

beforeAll(async () => {
  e2e = await startWebE2E();
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

/** A 1×1 PNG. */
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64');

describe('knowledge and settings in the browser', () => {
  it('uploads to the library through the picker and by dropping, and previews Markdown, a raster image and text', async () => {
    const client = e2e.client();
    const { project } = await client.projects.create({ name: 'Tax paperwork', goal: 'File on time' });
    const { context, page, problems } = await openAt(`#/p/${project.id}/conversation`);
    await page.getByRole('navigation', { name: 'Project' }).getByRole('link', { name: 'Library' }).click();
    await page.getByRole('heading', { name: 'Library', level: 1 }).waitFor();
    await page.getByText('Nothing here yet').waitFor();

    // The picker: a single upload opens in the preview, rendered.
    await page.getByTestId('library-input').setInputFiles({ name: 'checklist.md', mimeType: 'text/markdown', buffer: Buffer.from('# Documents to gather\n\n- W-2\n- 1099\n') });
    await expect.poll(() => hashOf(page)).toBe(`#/p/${project.id}/library?file=checklist.md`);
    const preview = page.getByRole('complementary', { name: 'Preview' });
    await preview.getByRole('heading', { name: 'Documents to gather' }).waitFor({ timeout: 15_000 });
    await preview.getByText(/published by you at/).waitFor();
    await page.getByRole('button', { name: /checklist\.md/ }).first().waitFor();
    await e2e.shot(page, 'knowledge-1-library');

    // A raster image: a blob: URL of its own type, which the CSP allows.
    await page.getByTestId('library-input').setInputFiles({ name: 'dot.png', mimeType: 'image/png', buffer: PNG });
    await expect.poll(() => hashOf(page)).toBe(`#/p/${project.id}/library?file=dot.png`);
    const image = preview.locator('img.file-image');
    await image.waitFor({ timeout: 15_000 });
    expect(await image.getAttribute('src')).toMatch(/^blob:/);
    await expect.poll(() => image.evaluate((i) => (i as HTMLImageElement).naturalWidth)).toBe(1);

    // A file dropped anywhere on the page.
    const library = page.locator('.library');
    const dropped = await page.evaluateHandle(() => {
      const data = new DataTransfer();
      data.items.add(new File(['category,total\nrent,1200\n'], 'totals.csv', { type: 'text/csv' }));
      return data;
    });
    await library.dispatchEvent('dragover', { dataTransfer: dropped });
    await page.getByText('Drop to upload').waitFor();
    expect(await library.getAttribute('class')).toContain('dragging');
    await library.dispatchEvent('drop', { dataTransfer: dropped });
    await expect.poll(() => hashOf(page)).toBe(`#/p/${project.id}/library?file=totals.csv`);
    await preview.locator('.codeblock code', { hasText: 'rent,1200' }).waitFor({ timeout: 15_000 });
    expect(await library.getAttribute('class')).not.toContain('dragging');
    await e2e.shot(page, 'knowledge-2-drop');

    // Close the preview; the kind switch and the filter narrow the grid.
    await preview.getByRole('button', { name: 'Close preview' }).click();
    await expect.poll(() => hashOf(page)).toBe(`#/p/${project.id}/library`);
    // The hash moves at once; the preview goes when the route change has rendered.
    await page.getByRole('complementary', { name: 'Preview' }).waitFor({ state: 'detached' });
    expect((await client.library.list(project.id)).map((a) => a.path).sort()).toEqual(['checklist.md', 'dot.png', 'totals.csv']);
    const kinds = page.getByRole('group', { name: 'Kind' });
    await kinds.getByRole('button', { name: 'Report' }).click();
    await page.getByText('Nothing matches.').waitFor();
    await kinds.getByRole('button', { name: 'All' }).click();
    await page.getByLabel('Filter the library').fill('totals');
    await expect.poll(() => page.locator('.library-card').count()).toBe(1);

    expect(problems).toEqual([]);
    await context.close();
  }, 120_000);

  it('remembers, corrects with the history kept, searches, and forgets', async () => {
    const client = e2e.client();
    const { project } = await client.projects.create({ name: 'Household', goal: 'Keep the paperwork in order' });
    await client.memory.add(project.id, { kind: 'fact', content: 'The accountant is Dana' });
    const { context, page, problems } = await openAt(`#/p/${project.id}/library`);
    await page.getByRole('navigation', { name: 'Project' }).getByRole('link', { name: 'Memory' }).click();
    await page.getByRole('heading', { name: 'Memory', level: 1 }).waitFor();

    // Add a preference.
    await page.getByLabel('Remember something').fill('Prefer email over calls');
    await page.getByLabel('Kind').selectOption('preference');
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    const preference = page.getByRole('region', { name: /Preferences/ }).getByRole('listitem', { name: /Prefer email over calls/ });
    await preference.waitFor({ timeout: 15_000 });

    // Correct the fact; the old version stays in the chain.
    const entry = page.getByRole('listitem', { name: /The accountant is Dana/ });
    await entry.getByRole('button', { name: 'Correct' }).click();
    await entry.getByLabel('Correct this entry').fill('The accountant is Dana Reyes');
    await entry.getByRole('button', { name: 'Save correction' }).click();
    const corrected = page.getByRole('listitem', { name: /The accountant is Dana Reyes/ });
    await corrected.getByRole('button', { name: 'Corrected 1 time' }).click();
    await corrected.getByRole('list', { name: 'Earlier versions' }).getByText('The accountant is Dana').waitFor();
    expect((await client.memory.list(project.id)).map((m) => m.content).sort()).toEqual(['Prefer email over calls', 'The accountant is Dana Reyes']);
    await e2e.shot(page, 'knowledge-3-memory');

    // Search: deskd answers, and the route follows the box.
    await page.getByLabel('Search memory').fill('accountant');
    await expect.poll(() => hashOf(page)).toBe(`#/p/${project.id}/memory?q=accountant`);
    const results = page.getByRole('list', { name: 'Search results' });
    await results.getByRole('listitem', { name: /Dana Reyes/ }).waitFor({ timeout: 15_000 });
    expect(await results.getByRole('listitem', { name: /Prefer email/ }).count()).toBe(0);
    await page.getByLabel('Search memory').fill('');
    await expect.poll(() => hashOf(page)).toBe(`#/p/${project.id}/memory`);
    await results.waitFor({ state: 'detached' });

    // Delete the preference after the confirm.
    await preference.getByRole('button', { name: 'Delete' }).click();
    await page.getByRole('dialog', { name: 'Delete this memory?' }).getByRole('button', { name: 'Delete' }).click();
    await preference.waitFor({ state: 'detached', timeout: 15_000 });
    expect((await client.memory.list(project.id)).map((m) => m.content)).toEqual(['The accountant is Dana Reyes']);

    expect(problems).toEqual([]);
    await context.close();
  }, 120_000);

  it('adds a source through the folder browser (deskd refuses the home folder), takes write access back, saves how Desk works and the policy, and archives', async () => {
    const client = e2e.client();
    const { project } = await client.projects.create({ name: 'Garden', goal: 'Plan the spring beds' });
    const { context, page, problems } = await openAt(`#/p/${project.id}/memory`);
    await page.getByRole('navigation', { name: 'Project' }).getByRole('link', { name: 'Settings' }).click();
    await page.getByRole('heading', { name: 'Settings', level: 1 }).waitFor();
    const sources = page.getByRole('region', { name: 'Sources' });
    await sources.getByText('No sources yet.').waitFor();

    // The browser starts at home and offers it; deskd refuses a source that is the home folder (spec §4.7).
    await sources.getByRole('button', { name: 'Add folder…' }).click();
    const sheet = page.getByRole('dialog', { name: 'Choose a folder' });
    await sheet.getByRole('button', { name: 'code', exact: true }).waitFor();
    expect(await sheet.getByRole('button', { name: '.config' }).count()).toBe(0);
    await expect.poll(() => sheet.getByLabel('Folder', { exact: true }).inputValue()).toBe(e2e.home);
    await sheet.getByRole('button', { name: 'Choose this folder' }).click();
    await sheet.waitFor({ state: 'detached' });
    await page.getByText(/A project source cannot be your home folder/).waitFor({ timeout: 15_000 });
    expect((await client.projects.get(project.id)).sources).toEqual([]);
    await sources.getByText('No sources yet.').waitFor();

    // A folder inside home: browse to it and choose it; it is added with write access on.
    await sources.getByRole('button', { name: 'Add folder…' }).click();
    await sheet.getByRole('button', { name: 'code', exact: true }).click();
    await sheet.getByRole('button', { name: 'app', exact: true }).click();
    const folder = join(e2e.home, 'code', 'app');
    await expect.poll(() => sheet.getByLabel('Folder', { exact: true }).inputValue()).toBe(folder);
    await e2e.shot(page, 'knowledge-4-folder-browser');
    await sheet.getByRole('button', { name: 'Choose this folder' }).click();
    await sheet.waitFor({ state: 'detached' });
    await sources.getByText(folder, { exact: true }).waitFor({ timeout: 15_000 });
    expect((await client.projects.get(project.id)).sources.map((s) => [s.path, s.label, s.agent_write])).toEqual([[folder, 'app', true]]);
    const write = sources.getByLabel('Agents can write here');
    expect(await write.isChecked()).toBe(true);
    await e2e.shot(page, 'knowledge-5-sources');

    // Take write access back: deskd records it, and the box follows.
    await write.click();
    await expect.poll(async () => (await client.projects.get(project.id)).sources.map((s) => s.agent_write)).toEqual([false]);
    await expect.poll(() => write.isChecked()).toBe(false);

    // How Desk works: review rounds and the threads' reasoning effort.
    const style = page.getByRole('region', { name: 'How Desk works' });
    await style.getByLabel('Review rounds').fill('3');
    await style.getByLabel("Threads' reasoning effort").selectOption('high');
    await style.getByRole('button', { name: 'Save' }).click();
    await expect.poll(async () => (await client.projects.get(project.id)).project.settings.review_rounds).toBe(3);
    expect((await client.projects.get(project.id)).project.settings.thread_reasoning_effort).toBe('high');

    // The policy: one more rule, at the end.
    const policy = page.getByRole('region', { name: 'Policy' });
    await policy.getByRole('button', { name: 'Add rule' }).click();
    await policy.getByLabel('Rule 10 tool').fill('web_fetch');
    await policy.getByLabel('Rule 10 action').selectOption('deny');
    await policy.getByRole('button', { name: 'Save policy' }).click();
    await expect.poll(async () => (await client.projects.get(project.id)).project.settings.policy.at(-1)).toEqual({ tool: 'web_fetch', action: 'deny' });
    await policy.scrollIntoViewIfNeeded();
    await e2e.shot(page, 'knowledge-6-settings');

    // Remove the source, then archive: the browser lands on the map, and the project leaves the list.
    await sources.getByRole('button', { name: 'Remove app' }).click();
    await sources.getByText('No sources yet.').waitFor({ timeout: 15_000 });
    expect((await client.projects.get(project.id)).sources).toEqual([]);
    await page.getByRole('button', { name: 'Archive project…' }).click();
    await page.getByRole('dialog', { name: 'Archive Garden?' }).getByRole('button', { name: 'Archive', exact: true }).click();
    await expect.poll(() => hashOf(page)).toBe('#/map');
    await expect.poll(async () => (await client.projects.list()).some((p) => p.id === project.id)).toBe(false);

    expect(problems).toEqual([]);
    await context.close();
  }, 120_000);
});
