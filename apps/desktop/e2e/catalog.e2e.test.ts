import { chmodSync, cpSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { _electron as electron, type ElectronApplication, type Page } from 'playwright';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { clientFromDataDir } from '@desk/client/node';
import { loadCatalog, treeDigest } from '@desk/core';
import { startDaemon, type RunningDaemon } from '@desk/daemon';
import { startFakeModel, text, type FakeModelServer } from '@desk/fake-model';

const appDir = fileURLToPath(new URL('..', import.meta.url));
let dir: string;
let fake: FakeModelServer;
let daemon: RunningDaemon;
let app: ElectronApplication;

/** A uv stand-in that creates the venv layout instantly, so runtime setup finishes offline. */
function uvStub(at: string): string {
  const uv = join(at, 'uv');
  writeFileSync(
    uv,
    ['#!/bin/sh', 'if [ "$1" = venv ]; then eval last=\\${$#}; mkdir -p "$last/bin"; printf \'#!/bin/sh\\necho py\\n\' > "$last/bin/python"; chmod +x "$last/bin/python"; fi', 'sleep 1', 'exit 0', ''].join('\n'),
  );
  chmodSync(uv, 0o755);
  return uv;
}

/**
 * The real catalog, except that Excel automation comes from a local stand-in (a builtin-source entry beside copies
 * of Desk's built-in skills), so installing it downloads nothing.
 */
function offlineCatalog(root: string) {
  cpSync(fileURLToPath(new URL('../../../catalog/skills', import.meta.url)), root, { recursive: true });
  const md = Buffer.from('---\nname: excel-automation\ndescription: Automate Excel workbooks.\n---\n\nRun scripts/excel_fill.py to fill a template.\n');
  const script = Buffer.from('# Fill an Excel template from a CSV file.\nprint("filled")\n');
  mkdirSync(join(root, 'excel-automation', 'scripts'), { recursive: true });
  writeFileSync(join(root, 'excel-automation', 'SKILL.md'), md);
  writeFileSync(join(root, 'excel-automation', 'scripts', 'excel_fill.py'), script);
  const file = loadCatalog();
  return {
    ...file,
    entries: file.entries.map((e) =>
      e.id !== 'excel-automation'
        ? e
        : {
            ...e,
            source: { type: 'builtin' as const, path: 'excel-automation' },
            digest: treeDigest([
              { path: 'SKILL.md', content: md },
              { path: 'scripts/excel_fill.py', content: script },
            ]),
            files: 2,
            bytes: md.length + script.length,
            scripts: 1,
            runtime: { python: { version: '3.12', packages: [] } },
          },
    ),
  };
}

beforeAll(async () => {
  dir = mkdtempSync(join(tmpdir(), 'desk-catalog-e2e-'));
  fake = await startFakeModel(() => text('Noted.'));
  const builtinRoot = join(dir, 'skills');
  const file = offlineCatalog(builtinRoot);
  daemon = await startDaemon({ dataDir: join(dir, 'data'), port: 0, modelConfig: { baseURL: fake.url, apiKey: 'test' }, runtimes: { uv: uvStub(dir) }, catalog: { builtinRoot, file } });
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

describe('skill catalog, end to end', () => {
  it('browses the catalog, reviews a skill, installs it with its runtime, and finds it on the map', async () => {
    const client = clientFromDataDir(join(dir, 'data'));
    await client.projects.create({ name: 'Thesis', goal: 'Write the thesis' });
    const page = await app.firstWindow();
    await page.evaluate(() => localStorage.setItem('desk.onboarded', '1'));

    await page.evaluate(() => (window.location.hash = '#/skills'));
    await page.getByRole('group', { name: 'View' }).getByRole('button', { name: 'Catalog' }).click();
    await page.getByRole('heading', { name: 'Skill catalog' }).waitFor();
    for (const bay of ['Research', 'Documents & data', 'Writing & diagrams', 'Planning', 'Code']) await page.getByRole('region', { name: bay }).waitFor();
    expect(await page.getByRole('region', { name: 'Files & media' }).count()).toBe(0);
    expect(await page.getByRole('listitem').filter({ has: page.locator('.catalog-card-title') }).count()).toBe(18);
    await shot(page, 'c1-catalog');

    const title = loadCatalog().entries.find((e) => e.id === 'excel-automation')!.title;
    await page.getByRole('button', { name: `Install ${title}` }).click();
    const sheet = page.getByRole('dialog', { name: `Install ${title}` });
    await sheet.getByRole('button', { name: /scripts\/excel_fill\.py/ }).click();
    await sheet.getByText('Fill an Excel template from a CSV file.', { exact: false }).first().waitFor();
    await shot(page, 'c2-review');
    await sheet.getByRole('button', { name: 'Install', exact: true }).click();
    await sheet.getByText('excel-automation is installed for every project.').waitFor();
    await sheet.getByRole('status').filter({ hasText: 'Ready · Python 3.12' }).waitFor({ timeout: 20_000 });
    await shot(page, 'c3-installed');
    await sheet.getByRole('button', { name: 'Open skill' }).click();

    const panel = page.getByRole('article', { name: 'Skill excel-automation' });
    await panel.getByText('From catalog').waitFor();
    await panel.getByText(/Ready · Python 3\.12/).waitFor();
    await page.getByRole('button', { name: /^excel-automation, global, version 1, from the catalog/ }).waitFor();
    await panel.getByRole('tab', { name: /^History/ }).click();
    await panel.getByText('Installed from the catalog (Desk)').waitFor();
    await shot(page, 'c4-panel');

    await page.evaluate(() => (window.location.hash = '#/system'));
    await page.getByRole('region', { name: 'Data' }).getByText(/for 1 skill/).waitFor();
    expect((await client.catalog.list()).find((i) => i.id === 'excel-automation')?.installs[0]).toMatchObject({ state: 'installed', runtime: 'ready' });
  });
});
