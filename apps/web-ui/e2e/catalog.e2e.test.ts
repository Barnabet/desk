import { createHash } from 'node:crypto';
import { chmodSync, cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Page } from 'playwright';
import type { CatalogEntry, CatalogFile } from '@desk/protocol';
import { startWebE2E, type SignedIn, type WebE2E } from './harness';

let dir: string | undefined;
let e2e: WebE2E;
let catalog: CatalogFile;

/** A uv stand-in that creates the venv layout instantly, so runtime setup finishes offline (as in apps/desktop/e2e/catalog.e2e.test.ts). */
function uvStub(at: string): string {
  const uv = join(at, 'uv');
  writeFileSync(
    uv,
    ['#!/bin/sh', 'if [ "$1" = venv ]; then eval last=\\${$#}; mkdir -p "$last/bin"; printf \'#!/bin/sh\\necho py\\n\' > "$last/bin/python"; chmod +x "$last/bin/python"; fi', 'sleep 1', 'exit 0', ''].join('\n'),
  );
  chmodSync(uv, 0o755);
  return uv;
}

const sha256 = (b: Buffer | string) => createHash('sha256').update(b).digest('hex');

/**
 * Writes a first-party skill to `root/<id>` and returns what its catalog entry pins: the file count, the size, and the
 * digest deskd checks before a review or an install (core's `treeDigest`: sha256 over the sorted
 * `path\0size\0sha256(content)\n` records).
 */
function builtinSkill(root: string, id: string, files: Record<string, string>): Pick<CatalogEntry, 'files' | 'bytes' | 'digest'> {
  const records: string[] = [];
  let bytes = 0;
  for (const [path, text] of Object.entries(files)) {
    const content = Buffer.from(text);
    const target = join(root, id, path);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, content);
    if (path.startsWith('scripts/')) chmodSync(target, 0o755);
    records.push(`${path}\0${content.length}\0${sha256(content)}\n`);
    bytes += content.length;
  }
  records.sort();
  return { files: records.length, bytes, digest: `sha256:${sha256(records.join(''))}` };
}

const MIT = 'MIT License\n\nCopyright (c) 2026 Desk tests\n\nPermission is hereby granted, free of charge, to any person obtaining a copy of this software.\n';

/**
 * The shipped catalog (packages/core/src/catalog/catalog.json), except that two entries come from local stand-ins, so
 * installing them downloads nothing (as the Electron catalog e2e does): Excel automation with a Python script and its
 * runtime, and Summarize meeting with instructions only. `root` is deskd's built-in root too, so Desk's own skills are
 * copied beside the stand-ins.
 */
function offlineCatalog(root: string): CatalogFile {
  cpSync(fileURLToPath(new URL('../../../catalog/skills', import.meta.url)), root, { recursive: true });
  const file = JSON.parse(readFileSync(fileURLToPath(new URL('../../../packages/core/src/catalog/catalog.json', import.meta.url)), 'utf8')) as CatalogFile;
  const excel = builtinSkill(root, 'excel-automation', {
    'SKILL.md': '---\nname: excel-automation\ndescription: Automate Excel workbooks.\n---\n\nRun scripts/excel_fill.py to fill a template.\n',
    'scripts/excel_fill.py': '# Fill an Excel template from a CSV file.\nprint("filled")\n',
    LICENSE: MIT,
  });
  const meeting = builtinSkill(root, 'summarize-meeting', {
    'SKILL.md': '---\nname: summarize-meeting\ndescription: Turn a meeting transcript into decisions and next steps.\n---\n\nList the decisions first, then who does what by when.\n',
  });
  const standIns: Record<string, Partial<CatalogEntry>> = {
    'excel-automation': { source: { type: 'builtin', path: 'excel-automation' }, ...excel, scripts: 1, runtime: { python: { version: '3.12', packages: [] } } },
    'summarize-meeting': { source: { type: 'builtin', path: 'summarize-meeting' }, ...meeting, scripts: 0, runtime: {} },
  };
  return { ...file, entries: file.entries.map((e) => ({ ...e, ...standIns[e.id] })) };
}

const titleOf = (id: string) => catalog.entries.find((e) => e.id === id)!.title;

beforeAll(async () => {
  dir = mkdtempSync(join(tmpdir(), 'desk-web-catalog-'));
  const builtinRoot = join(dir, 'skills');
  catalog = offlineCatalog(builtinRoot);
  e2e = await startWebE2E({ daemon: { catalog: { builtinRoot, file: catalog }, runtimes: { uv: uvStub(dir) } } });
});

afterAll(async () => {
  await e2e?.close();
  if (dir) rmSync(dir, { recursive: true, force: true });
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

describe('skills and the catalog in the browser', () => {
  it('browses the catalog, reviews an entry, installs it with its runtime, finds it on the map, and installs another into a project', async () => {
    const client = e2e.client();
    const { project } = await client.projects.create({ name: 'Thesis', goal: 'Write the thesis' });
    const { context, page, problems } = await openAt('#/skills');
    const excel = titleOf('excel-automation');
    const meeting = titleOf('summarize-meeting');

    await page.getByRole('group', { name: 'View' }).getByRole('button', { name: 'Catalog' }).click();
    await expect.poll(() => hashOf(page)).toBe('#/skills/catalog');
    await page.getByRole('heading', { name: 'Skill catalog' }).waitFor();
    for (const bay of ['Research', 'Documents & data', 'Writing & diagrams', 'Planning', 'Code']) await page.getByRole('region', { name: bay }).waitFor();
    expect(await page.getByRole('region', { name: 'Files & media' }).count()).toBe(0);
    expect(await page.getByRole('listitem').filter({ has: page.locator('.catalog-card-title') }).count()).toBe(18);
    const card = page.getByRole('listitem', { name: excel });
    await expect.poll(() => card.textContent()).toContain('Python 3.12 · set up by Desk');
    await e2e.shot(page, 'catalog');

    await card.getByRole('button', { name: `Install ${excel}` }).click();
    await expect.poll(() => hashOf(page)).toBe('#/skills/catalog/excel-automation');
    const sheet = page.getByRole('dialog', { name: `Install ${excel}` });
    await sheet.getByText('Written by Desk and shipped with the app').waitFor();
    await sheet.getByRole('button', { name: 'Read it' }).click();
    await sheet.getByText(/Permission is hereby granted/).first().waitFor();
    await sheet.getByRole('button', { name: /scripts\/excel_fill\.py/ }).click();
    await sheet.getByText('Fill an Excel template from a CSV file.', { exact: false }).first().waitFor();
    await e2e.shot(page, 'catalog-review');
    await sheet.getByRole('button', { name: 'Install', exact: true }).click();
    await sheet.getByText('excel-automation is installed for every project.').waitFor();
    await sheet.getByRole('status').filter({ hasText: 'Ready · Python 3.12' }).waitFor({ timeout: 20_000 });
    await e2e.shot(page, 'catalog-installed');
    await sheet.getByRole('button', { name: 'Open skill' }).click();

    await expect.poll(() => hashOf(page)).toBe('#/skills/global%3Aexcel-automation');
    const panel = page.getByRole('article', { name: 'Skill excel-automation' });
    await panel.getByText('From catalog').waitFor();
    await panel.getByText(/Ready · Python 3\.12/).first().waitFor();
    await page.getByRole('group', { name: 'Skill map' }).getByRole('button', { name: /^excel-automation, global, version 1, from the catalog/ }).waitFor();
    await panel.getByRole('tab', { name: /^History/ }).click();
    await panel.getByText('Installed from the catalog (Desk)').first().waitFor();
    await e2e.shot(page, 'catalog-panel');

    // A deep link opens the review; this one goes into a project.
    await go(page, '#/skills/catalog/summarize-meeting');
    const notes = page.getByRole('dialog', { name: `Install ${meeting}` });
    await notes.getByText('Written by Desk and shipped with the app').waitFor();
    await notes.getByLabel('Install for').selectOption({ label: 'Thesis only' });
    await notes.getByRole('button', { name: 'Install', exact: true }).click();
    await notes.getByText('summarize-meeting is installed in Thesis.').waitFor();
    await go(page, '#/skills/catalog');
    await notes.waitFor({ state: 'detached' });
    await expect.poll(() => page.getByRole('listitem', { name: meeting }).textContent()).toContain('In Thesis');

    const items = await client.catalog.list();
    expect(items.find((i) => i.id === 'excel-automation')?.installs).toMatchObject([{ scope: 'global', state: 'installed', runtime: 'ready' }]);
    expect(items.find((i) => i.id === 'summarize-meeting')?.installs).toMatchObject([{ scope: 'project', project_id: project.id, state: 'installed', runtime: 'none' }]);
    expect((await client.catalog.runtimes()).envs).toMatchObject([{ scope: 'global', name: 'excel-automation', orphan: false }]);
    expect(problems).toEqual([]);
    await context.close();
  });

  it('refines a skill by hand, restores v1, and imports a folder from ~/.claude/skills through the folder browser', async () => {
    const client = e2e.client();
    await client.skills.save({}, 'receipt-sorting', { description: 'Sort receipts into tax categories', instructions: 'Sort by category.', change_note: 'First version' });
    const folder = join(e2e.home, '.claude', 'skills', 'expense-report');
    mkdirSync(folder, { recursive: true });
    writeFileSync(join(folder, 'SKILL.md'), '---\nname: expense-report\ndescription: Turn receipts into an expense report\n---\n\nList each receipt with its date and total.\n');
    const { context, page, problems } = await openAt('#/skills');

    // Refine by hand, then restore v1 (the skills steps of apps/desktop/e2e/knowledge.e2e.test.ts).
    await page.getByRole('button', { name: /^receipt-sorting, global, version 1/ }).click();
    await expect.poll(() => hashOf(page)).toBe('#/skills/global%3Areceipt-sorting');
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
    await e2e.shot(page, 'skills-history');
    await panel.getByRole('button', { name: 'Restore' }).click();
    await page.getByRole('dialog', { name: 'Restore v1?' }).getByRole('button', { name: 'Restore' }).click();
    await expect.poll(async () => (await client.skills.get({}, 'receipt-sorting')).version).toBe(3);
    expect((await client.skills.get({}, 'receipt-sorting')).instructions.trim()).toBe('Sort by category.');

    // Import: app.pickFolder is the web folder browser, with hidden folders shown for ~/.claude/skills.
    await page.getByRole('button', { name: 'Import from ~/.claude/skills' }).click();
    const browser = page.getByRole('dialog', { name: 'Choose a skill folder' });
    await browser.getByRole('button', { name: '.claude', exact: true }).click();
    await browser.getByRole('button', { name: 'skills', exact: true }).click();
    await browser.getByRole('button', { name: 'expense-report', exact: true }).click();
    await expect.poll(() => browser.getByLabel('Folder', { exact: true }).inputValue()).toBe(folder);
    await e2e.shot(page, 'skills-import-browser');
    await browser.getByRole('button', { name: 'Choose this folder' }).click();
    await browser.waitFor({ state: 'detached' });
    const importing = page.getByRole('dialog', { name: 'Import a skill' });
    await importing.getByText(folder, { exact: true }).waitFor();
    await importing.getByRole('button', { name: 'Import', exact: true }).click();
    await expect.poll(() => hashOf(page)).toBe('#/skills/global%3Aexpense-report');
    await page.getByRole('article', { name: 'Skill expense-report' }).waitFor();
    expect((await client.skills.get({}, 'expense-report')).description).toBe('Turn receipts into an expense report');
    expect(problems).toEqual([]);
    await context.close();
  });

  it("lists Desk's built-in skills, turns one off, and duplicates one into your skills (apps/desktop/e2e/builtins.e2e.test.ts)", async () => {
    const client = e2e.client();
    const { context, page, problems } = await openAt('#/skills');
    await page.getByRole('group', { name: 'View' }).getByRole('button', { name: 'List' }).click();

    const group = page.getByRole('region', { name: 'Built into Desk' });
    await group.waitFor();
    expect(await group.getByRole('switch').count()).toBe((await client.builtins.list()).filter((b) => !b.broken).length);
    await group.getByText('Set up on first use').first().waitFor();
    await e2e.shot(page, 'builtins');

    const pdf = (await client.builtins.list()).find((b) => b.name === 'pdf-toolkit')!;
    await group.getByRole('switch', { name: pdf.title }).click();
    await expect.poll(async () => (await client.builtins.list()).find((b) => b.name === 'pdf-toolkit')?.enabled).toBe(false);
    await group.getByRole('switch', { name: pdf.title, checked: false }).waitFor();

    const images = (await client.builtins.list()).find((b) => b.name === 'images')!;
    await group.getByRole('button', { name: `Open ${images.title}` }).click();
    await expect.poll(() => hashOf(page)).toBe('#/skills/builtin%3Aimages');
    const panel = page.getByRole('article', { name: 'Built-in skill images' });
    await panel.getByRole('tab', { name: 'Instructions' }).click();
    await panel.getByRole('button', { name: 'Duplicate to my skills' }).click();
    await page.getByRole('dialog', { name: 'Duplicate images' }).getByRole('button', { name: 'Duplicate' }).click();

    await expect.poll(() => hashOf(page)).toBe('#/skills/global%3Aimages');
    const copy = page.getByRole('article', { name: 'Skill images' });
    await copy.getByText(/Customised from the built-in skill/).waitFor();
    expect((await client.skills.list({})).map((s) => s.name)).toContain('images');
    await group.getByRole('button', { name: `Open ${images.title}` }).getByText('Shadowed by your global skill').waitFor();
    await e2e.shot(page, 'builtins-duplicate');
    expect(problems).toEqual([]);
    await context.close();
  });
});
