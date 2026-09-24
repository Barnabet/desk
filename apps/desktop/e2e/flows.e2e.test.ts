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

/** Desk: dispatch a thread and ask a question; send the first result back for revision; report on the second. */
function desk(req: ChatRequest): FakeReply {
  const last = req.messages.at(-1);
  if (last?.role === 'tool') return text('On it.');
  const said = content(last);
  if (said.includes('Relaunch onboarding')) {
    return tools(
      call('spawn_thread', { title: 'Signup checklist', brief: 'Draft the signup checklist.' }),
      call('ask_user', { question: 'Data source or teammate invite first?', options: ['Data source', 'Teammate invite'] }),
    );
  }
  if (said.includes('— completed]')) {
    const spawned = req.messages.map(content).join('\n').match(/Spawned thread (\S+) /)?.[1] ?? '';
    const results = req.messages.filter((m) => m.role === 'user' && content(m).includes('— completed]')).length;
    return results === 1
      ? tools(call('message_thread', { thread_id: spawned, kind: 'revision', text: 'Item 3 reads as salesy; tighten it.' }))
      : tools(call('report', { headline: 'The signup checklist is in', progress: 'Two rounds, now tight.', needs_you: ['Review the checklist copy'], results: [] }));
  }
  return text('Noted.');
}

/** The thread: run a command that needs approval, then complete; complete again after a revision; acknowledge steering. */
function thread(req: ChatRequest): FakeReply {
  const last = req.messages.at(-1);
  if (last?.role === 'tool') {
    const assistant = [...req.messages].reverse().find((m) => m.role === 'assistant' && Array.isArray(m.tool_calls));
    const name = (assistant?.tool_calls as Array<{ function: { name: string } }> | undefined)?.[0]?.function.name;
    return name === 'bash' ? tools(call('complete', { summary: 'Checklist drafted: five steps, one first win.' })) : text('Done.');
  }
  const said = content(last);
  if (said.includes('Keep it short')) return text('Will do.');
  if (said.includes('— revision]')) return tools(call('complete', { summary: 'Checklist tightened.' }));
  return tools(call('bash', { command: 'echo sudo make me a checklist' }));
}

beforeAll(async () => {
  dir = mkdtempSync(join(tmpdir(), 'desk-flows-'));
  fake = await startFakeModel((req) => (system(req).startsWith('You are Desk,') ? desk(req) : thread(req)));
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

const hooks = <T>(fn: string) => app.evaluate((_e, f) => (globalThis as unknown as { __deskTest: Record<string, () => unknown> }).__deskTest[f]!(), fn) as Promise<T>;

/** Set DESK_E2E_SHOTS=<dir> to keep screenshots of each step for visual review. */
async function shot(page: Page, name: string) {
  const out = process.env.DESK_E2E_SHOTS;
  if (out) await page.screenshot({ path: join(out, `${name}.png`) });
}

async function go(page: Page, hash: string) {
  await page.evaluate((h) => (window.location.hash = h), hash);
}

describe('core screens, end to end', () => {
  it('briefs Desk, forks a thread, answers, approves, loops a revision, steers, and updates the tray', async () => {
    const client = clientFromDataDir(join(dir, 'data'));
    const { project } = await client.projects.create({ name: 'Onboarding revamp', goal: 'Relaunch onboarding next month' });
    const page = await app.firstWindow();
    await page.evaluate(() => localStorage.setItem('desk.onboarded', '1'));
    await go(page, `#/p/${project.id}/conversation`);

    // Brief Desk from the composer.
    const composer = page.getByLabel('Message Desk');
    await composer.fill('Relaunch onboarding: start with a signup checklist.');
    await composer.press('Enter');
    await page.getByText('Relaunch onboarding: start with a signup checklist.').first().waitFor();

    // The thread forks on the line diagram, and Desk's question arrives.
    const diagram = page.getByRole('region', { name: /Line diagram/ });
    await diagram.getByRole('link', { name: /Signup checklist/ }).first().waitFor({ timeout: 20_000 });
    await page.getByRole('button', { name: 'Data source', exact: true }).click();
    await page.getByText('Answered').waitFor({ timeout: 15_000 });
    await shot(page, '1-conversation-forked');

    // Approve the thread's command from Attention.
    await page.getByRole('link', { name: /need you$/ }).click();
    const inspector = page.getByRole('article', { name: /clearance request/ });
    await expect.poll(() => inspector.getByLabel('Command').textContent(), { timeout: 15_000 }).toBe('$ echo sudo make me a checklist');
    await inspector.getByLabel('Note to the thread (optional)').fill('Fine, it only echoes.');
    await shot(page, '2-attention');
    await inspector.getByRole('button', { name: /^Approve once/ }).click();

    // The thread reports, Desk sends it back, it reports again, and Desk reports to you.
    await go(page, `#/p/${project.id}/conversation`);
    await page.getByRole('heading', { name: 'The signup checklist is in' }).waitFor({ timeout: 30_000 });
    expect(await diagram.getByText(/^sent back/).count()).toBeGreaterThan(0);
    await page.getByRole('link', { name: 'Review the checklist copy' }).waitFor();
    await shot(page, '3-conversation-report');

    // The thread's route shows the revision; steer it.
    const [t] = await client.threads.list(project.id);
    await go(page, `#/p/${project.id}/threads/${t!.id}`);
    await page.getByRole('button', { name: /Desk sent it back/ }).waitFor();
    const transcript = page.getByRole('complementary', { name: 'Transcript' });
    await transcript.getByLabel('Steer this thread').fill('Keep it short');
    await transcript.getByRole('button', { name: 'Steer' }).click();
    await transcript.getByText(/^You steered/).first().waitFor({ timeout: 15_000 });
    await transcript.getByText('Will do.').waitFor({ timeout: 15_000 });
    await shot(page, '4-thread');
    await go(page, `#/p/${project.id}/threads`);
    await page.getByRole('heading', { name: 'Threads' }).waitFor();
    await shot(page, '5-roster');
    await go(page, '#/map');
    await page.getByRole('heading', { name: 'Projects', exact: true }).waitFor();
    await shot(page, '6-map');

    // The tray counts the one hand-off left, and the popover shows it.
    await expect.poll(() => hooks<string>('trayTitle'), { timeout: 10_000 }).toBe('1');
    await hooks('togglePopover');
    const popover = await expect
      .poll(() => app.windows().find((w) => w.url().endsWith('#/tray')), { timeout: 10_000 })
      .toBeTruthy()
      .then(() => app.windows().find((w) => w.url().endsWith('#/tray'))!);
    await popover.getByText('1 need you').waitFor();
    await popover.getByText('Review the checklist copy').waitFor();
    await shot(popover, '7-popover');
    await popover.getByRole('button', { name: /^Open Desk/ }).click();
  });
});
