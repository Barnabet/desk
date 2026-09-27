import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { _electron as electron, type ElectronApplication, type Page } from 'playwright';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { clientFromDataDir } from '@desk/client/node';
import { startDaemon, type RunningDaemon } from '@desk/daemon';
import { call, startFakeModel, text, tools, type ChatRequest, type FakeModelServer, type FakeReply } from '@desk/fake-model';

const appDir = fileURLToPath(new URL('..', import.meta.url));
let dir: string;
let fake: FakeModelServer;
let daemon: RunningDaemon;
let app: ElectronApplication;

const content = (m: Record<string, unknown> | undefined) => (typeof m?.content === 'string' ? m.content : JSON.stringify(m?.content ?? ''));
const system = (req: ChatRequest) => content(req.messages[0]);

/** The step agent writes the digest into its step folder, then completes. Anyone else (Desk) only acknowledges. */
function stepAgent(req: ChatRequest): FakeReply {
  const calls = req.messages.filter((m) => m.role === 'assistant').length;
  if (calls === 0) return tools(call('write_file', { path: 'digest.md', content: '# Robots\n\nRobots had a big week.\n' }));
  if (calls === 1) return tools(call('complete', { summary: 'Wrote digest.md about robots.' }));
  return text('Done.');
}

beforeAll(async () => {
  dir = mkdtempSync(join(tmpdir(), 'desk-automations-'));
  fake = await startFakeModel((req) => (system(req).includes('You are one step of the automation') ? stepAgent(req) : text('Noted.')));
  daemon = await startDaemon({ dataDir: join(dir, 'data'), port: 0, modelConfig: { baseURL: fake.url, apiKey: 'test' } });
  const { default: electronPath } = await import('electron');
  app = await electron.launch({
    executablePath: electronPath as unknown as string,
    args: [appDir],
    colorScheme: process.env.DESK_E2E_SCHEME === 'dark' ? 'dark' : 'light',
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

describe('automations, end to end', () => {
  it('builds an automation in the editor, runs it with an input, answers its Ask me step and publishes its file', async () => {
    const client = clientFromDataDir(join(dir, 'data'));
    const { project } = await client.projects.create({ name: 'Digest', goal: 'A weekly digest' });
    const page = await app.firstWindow();
    await page.evaluate(() => localStorage.setItem('desk.onboarded', '1'));
    await page.evaluate((h) => (window.location.hash = h), `#/p/${project.id}/automations`);

    // A Blank automation, named once.
    await page.getByRole('button', { name: 'Blank automation' }).click();
    const naming = page.getByRole('dialog', { name: 'New automation' });
    await naming.getByLabel('Name').fill('digest');
    await naming.getByRole('button', { name: 'Create' }).click();

    // Start: one text input.
    await page.getByTestId('node-start').click();
    await page.getByRole('button', { name: 'Add input' }).click();
    const input = page.getByRole('group', { name: 'Input 1' });
    await input.getByLabel('Label', { exact: true }).fill('Topic');
    await input.getByLabel('Key', { exact: true }).fill('topic');
    await input.getByLabel('Key', { exact: true }).press('Tab');

    // An agent step that publishes digest.md, then (added while it is selected, so connected under it) an Ask me step.
    const strip = page.getByRole('toolbar', { name: 'Add a step' });
    await strip.getByRole('button', { name: 'Agent' }).click();
    await page.getByLabel('Brief', { exact: true }).fill('Write digest.md about {{inputs.topic}}.');
    await page.getByRole('button', { name: 'Add file' }).click();
    await page.getByLabel('Publish to the Library 1', { exact: true }).fill('digest.md');
    await strip.getByRole('button', { name: 'Ask me' }).click();
    await page.getByLabel('Question', { exact: true }).fill('Publish the digest?');
    await page.getByText('Ready to save').waitFor({ timeout: 10_000 });
    await shot(page, 'a1-design');

    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await page.waitForFunction(() => /^#\/p\/[^/]+\/automations\/[^/?]+$/.test(window.location.hash), undefined, { timeout: 10_000 });
    const [automation] = await client.automations.list(project.id);
    expect(automation).toMatchObject({ name: 'digest', version: 1 });
    const saved = await client.automations.get(automation!.id);
    expect(saved.definition.inputs).toMatchObject([{ key: 'topic', label: 'Topic' }]);
    expect(saved.definition.edges).toEqual([{ from: 'agent', to: 'ask' }]);

    // Run now with an input: the run view opens and the graph lights up.
    await page.getByRole('button', { name: 'Run now…' }).click();
    const runDialog = page.getByRole('dialog', { name: /^Run / });
    await runDialog.getByLabel('Topic').fill('robots');
    await runDialog.getByRole('button', { name: 'Run', exact: true }).click();
    await page.getByRole('heading', { name: 'Run #1' }).waitFor({ timeout: 10_000 });
    await expect.poll(() => page.getByTestId('node-agent').getAttribute('class'), { timeout: 30_000 }).toMatch(/run-(run|ok)/);
    await expect.poll(() => page.getByTestId('node-ask').getAttribute('class'), { timeout: 30_000 }).toMatch(/run-wait/);
    await page.locator('.auto-run-head').getByText('waiting on you · Ask me').waitFor({ timeout: 10_000 });
    await shot(page, 'a2-waiting');

    // The run view opens on the waiting step: answer it there.
    const answer = page.getByRole('region', { name: 'Your answer' });
    await answer.getByText('Publish the digest?').waitFor();
    await answer.getByRole('button', { name: 'Approve' }).click();
    await page.locator('.auto-run-head').getByText('succeeded').waitFor({ timeout: 30_000 });
    expect(await page.getByTestId('node-ask').getAttribute('class')).toMatch(/run-ok/);
    await shot(page, 'a3-succeeded');

    // The agent step's file, in its panel and in the Library.
    await page.getByTestId('node-agent').click();
    await page.getByRole('region', { name: 'Files' }).getByRole('button', { name: 'digest.md' }).waitFor({ timeout: 10_000 });
    await expect
      .poll(async () => (await client.library.list(project.id)).map((a) => a.path).find((p) => p.endsWith('/digest.md')) ?? null, { timeout: 15_000 })
      .toMatch(/^automations\/digest\/.+\/digest\.md$/);
    const [entry] = await client.automations.runs(automation!.id);
    expect(entry).toMatchObject({ kind: 'run', run: { number: 1, status: 'succeeded', inputs: { topic: 'robots' } } });
  });
});
