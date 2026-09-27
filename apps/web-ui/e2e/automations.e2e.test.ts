import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { call, text, tools, type ChatRequest, type FakeReply } from '@desk/fake-model';
import { startWebE2E, type SignedIn, type WebE2E } from './harness';

let e2e: WebE2E;

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
  e2e = await startWebE2E({ script: (req) => (system(req).includes('You are one step of the automation') ? stepAgent(req) : text('Noted.')) });
});

afterAll(async () => {
  await e2e?.close();
});

/** A signed-in browser past onboarding, on `hash`. */
async function openAt(hash: string): Promise<SignedIn> {
  const signed = await e2e.signIn();
  await signed.page.evaluate(() => localStorage.setItem('desk.onboarded', '1'));
  await signed.page.evaluate((h) => (window.location.hash = h), hash);
  return signed;
}

describe('automations in the browser', () => {
  it('builds an automation in the editor, runs it with an input, answers its Ask me step and publishes its file', async () => {
    const client = e2e.client();
    const { project } = await client.projects.create({ name: 'Digest', goal: 'A weekly digest' });
    const { context, page, problems } = await openAt(`#/p/${project.id}/automations`);

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
    await e2e.shot(page, 'automations-design');

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
    await e2e.shot(page, 'automations-waiting');

    // The run view opens on the waiting step: answer it there.
    const answer = page.getByRole('region', { name: 'Your answer' });
    await answer.getByText('Publish the digest?').waitFor();
    await answer.getByRole('button', { name: 'Approve' }).click();
    await page.locator('.auto-run-head').getByText('succeeded').waitFor({ timeout: 30_000 });
    expect(await page.getByTestId('node-ask').getAttribute('class')).toMatch(/run-ok/);
    await e2e.shot(page, 'automations-succeeded');

    // The agent step's file, in its panel and in the Library.
    await page.getByTestId('node-agent').click();
    await page.getByRole('region', { name: 'Files' }).getByRole('button', { name: 'digest.md' }).waitFor({ timeout: 10_000 });
    await expect
      .poll(async () => (await client.library.list(project.id)).map((a) => a.path).find((p) => p.endsWith('/digest.md')) ?? null, { timeout: 15_000 })
      .toMatch(/^automations\/digest\/.+\/digest\.md$/);
    const [entry] = await client.automations.runs(automation!.id);
    expect(entry).toMatchObject({ kind: 'run', run: { number: 1, status: 'succeeded', inputs: { topic: 'robots' } } });

    expect(problems).toEqual([]);
    await context.close();
  }, 180_000);

  it('chooses a file input in the folder browser, and the run gets a copy of the file', async () => {
    const client = e2e.client();
    const { project } = await client.projects.create({ name: 'Briefs', goal: 'Check each brief' });
    const brief = join(e2e.home, 'Documents', 'brief.txt');
    writeFileSync(brief, 'The brief.\n');
    const { automation } = await client.automations.create(project.id, {
      name: 'check-brief',
      definition: {
        title: 'Check a brief',
        inputs: [{ key: 'brief', label: 'Brief', type: 'file', required: true }],
        steps: [{ id: 'ok', title: 'Looks right?', kind: 'ask', question: 'Is this the right brief?' }],
      },
    });
    const { context, page, problems } = await openAt(`#/p/${project.id}/automations/${automation.id}`);

    await page.getByRole('button', { name: 'Run now…' }).click();
    const runDialog = page.getByRole('dialog', { name: 'Run Check a brief' });
    await runDialog.getByRole('button', { name: 'Choose…' }).click();
    const files = page.getByRole('dialog', { name: 'Choose a file' });
    await files.getByRole('button', { name: 'Documents', exact: true }).click();
    await files.getByRole('button', { name: 'brief.txt', exact: true }).click();
    await expect.poll(() => files.getByLabel('Path', { exact: true }).inputValue()).toBe(brief);
    await e2e.shot(page, 'automations-file-input');
    await files.getByRole('button', { name: 'Choose this file' }).click();
    await files.waitFor({ state: 'detached' });
    await expect.poll(() => runDialog.getByLabel('Brief').inputValue()).toBe(brief);
    await runDialog.getByRole('button', { name: 'Run', exact: true }).click();

    await page.getByRole('heading', { name: 'Run #1' }).waitFor({ timeout: 10_000 });
    await page.locator('.auto-run-head').getByText('waiting on you · Looks right?').waitFor({ timeout: 30_000 });
    const [entry] = await client.automations.runs(automation.id);
    expect(entry?.kind).toBe('run');
    const given = entry?.kind === 'run' ? entry.run.inputs['brief'] : undefined;
    expect(typeof given).toBe('string');
    expect(readFileSync(String(given), 'utf8')).toBe('The brief.\n');

    expect(problems).toEqual([]);
    await context.close();
  }, 120_000);
});
