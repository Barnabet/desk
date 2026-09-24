import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { _electron as electron, type ElectronApplication, type Locator, type Page } from 'playwright';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { clientFromDataDir } from '@desk/client/node';
import { startDaemon, type RunningDaemon } from '@desk/daemon';
import { call, startFakeModel, text, tools, type FakeModelServer } from '@desk/fake-model';

const appDir = fileURLToPath(new URL('..', import.meta.url));
let dir: string;
let fake: FakeModelServer;
let daemon: RunningDaemon;
let app: ElectronApplication;

/** Real options Desk offered: long enough that one row of buttons is far wider than the chat. */
const OPTIONS = [
  'Too fast: moves and hits fly by, captions and lines are hard to read, things need more room to breathe (slower, even if fights get longer)',
  'Too long: 20–30 s fights drag and should be shorter and punchier overall',
  'Uneven: some parts rush (hits, combos) while others drag (intro, taunts, text beats)',
  'Something else (I’ll explain)',
];

beforeAll(async () => {
  dir = mkdtempSync(join(tmpdir(), 'desk-question-'));
  fake = await startFakeModel((req) =>
    req.messages.some((m) => m.role === 'tool')
      ? text('Asked.')
      : tools(call('ask_user', { question: 'When you say it was "going too far", what did you mean?', options: OPTIONS })),
  );
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

async function shot(page: Page, name: string) {
  const out = process.env.DESK_E2E_SHOTS;
  if (out) await page.screenshot({ path: join(out, `${name}.png`) });
}

/** Every option button lies inside `box` horizontally, and `scroller` cannot scroll sideways. */
async function expectFits(box: Locator, scroller: Locator) {
  const outer = (await box.boundingBox())!;
  for (const opt of OPTIONS) {
    const b = (await box.getByRole('button', { name: opt, exact: true }).boundingBox())!;
    expect(b.x).toBeGreaterThanOrEqual(outer.x - 0.5);
    expect(b.x + b.width).toBeLessThanOrEqual(outer.x + outer.width + 0.5);
  }
  const overflow = await scroller.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

describe('question options', () => {
  it('wrap inside the question card and the attention inspector instead of scrolling the view sideways', async () => {
    const client = clientFromDataDir(join(dir, 'data'));
    const { project } = await client.projects.create({ name: 'anyfight', goal: 'Make fights feel good' });
    const page = await app.firstWindow();
    await page.evaluate(() => localStorage.setItem('desk.onboarded', '1'));
    await page.evaluate((h) => (window.location.hash = h), `#/p/${project.id}/conversation`);
    await client.projects.send(project.id, 'The pacing must be reworked.');

    const card = page.getByRole('region', { name: /going too far/ });
    await card.getByRole('button', { name: OPTIONS[0], exact: true }).waitFor({ timeout: 20_000 });
    await shot(page, 'question-card');
    await expectFits(card, page.locator('.chat-list'));

    await page.evaluate(() => (window.location.hash = '#/attention'));
    const inspector = page.getByRole('article', { name: 'Selected: question from desk' });
    await inspector.getByRole('button', { name: OPTIONS[0], exact: true }).waitFor({ timeout: 15_000 });
    await shot(page, 'question-inspector');
    await expectFits(inspector, inspector);
  });
});
