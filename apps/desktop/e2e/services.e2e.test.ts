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
const PORT = 41000 + Math.floor(Math.random() * 2000);
let dir: string;
let fake: FakeModelServer;
let daemon: RunningDaemon;
let app: ElectronApplication;

const content = (m: Record<string, unknown> | undefined) => (typeof m?.content === 'string' ? m.content : JSON.stringify(m?.content ?? ''));
const system = (req: ChatRequest) => content(req.messages[0]);

/** Desk dispatches one thread; the thread starts a real HTTP server as a service, then completes. */
function desk(req: ChatRequest): FakeReply {
  const last = req.messages.at(-1);
  if (last?.role === 'tool') return text('Dispatched.');
  if (content(last).includes('Run the app')) return tools(call('spawn_thread', { title: 'Web app', brief: 'Start the web server for the user.' }));
  return text('Noted.');
}

function thread(req: ChatRequest): FakeReply {
  const calls = req.messages.filter((m) => m.role === 'assistant').length;
  if (calls === 0) {
    const server = `require('http').createServer((q, r) => r.end('hello from web')).listen(${PORT}, '127.0.0.1', () => console.log('ready on http://127.0.0.1:${PORT}'))`;
    return tools(call('service_start', { name: 'web', command: `node -e "${server}"` }));
  }
  if (calls === 1) return tools(call('complete', { summary: `Web server running at http://127.0.0.1:${PORT}` }));
  return text('Done.');
}

beforeAll(async () => {
  dir = mkdtempSync(join(tmpdir(), 'desk-services-'));
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

async function shot(page: Page, name: string) {
  const out = process.env.DESK_E2E_SHOTS;
  if (out) await page.screenshot({ path: join(out, `${name}.png`) });
}

const reachable = () =>
  fetch(`http://127.0.0.1:${PORT}`)
    .then((r) => r.text())
    .catch(() => null);

describe('services, end to end', () => {
  it('a thread starts a server that outlives it; the Services card shows it, its logs, and stops it', async () => {
    const client = clientFromDataDir(join(dir, 'data'));
    const { project } = await client.projects.create({ name: 'Web', goal: 'Try the app' });
    const page = await app.firstWindow();
    await page.evaluate(() => localStorage.setItem('desk.onboarded', '1'));
    await page.evaluate((h) => (window.location.hash = h), `#/p/${project.id}/conversation`);

    const composer = page.getByLabel('Message Desk');
    await composer.fill('Run the app so I can try it.');
    await composer.press('Enter');

    const card = page.getByRole('region', { name: 'Services' });
    const row = card.getByRole('listitem', { name: /^web, running/ });
    await row.waitFor({ timeout: 30_000 });
    await expect.poll(() => row.textContent(), { timeout: 15_000 }).toContain(`:${PORT}`);
    await row.getByText('from Web app').waitFor();
    await row.getByRole('button', { name: 'Open' }).waitFor();
    expect(await reachable()).toBe('hello from web');

    // The thread has finished; the service is still up.
    await expect.poll(async () => (await client.threads.list(project.id))[0]?.status, { timeout: 15_000 }).toBe('done');
    expect(await reachable()).toBe('hello from web');
    await shot(page, 's1-services');

    await row.getByRole('button', { name: 'Logs' }).click();
    const log = page.getByLabel('web log');
    await expect.poll(() => log.textContent(), { timeout: 10_000 }).toContain(`ready on http://127.0.0.1:${PORT}`);
    await shot(page, 's2-logs');
    await page.getByRole('dialog', { name: 'web · logs' }).getByRole('button', { name: 'Close' }).click();

    await row.getByRole('button', { name: 'Stop web' }).click();
    await card.getByRole('listitem', { name: /^web, stopped/ }).waitFor({ timeout: 15_000 });
    await expect.poll(reachable, { timeout: 10_000 }).toBeNull();
    await card.getByRole('button', { name: 'Start web' }).waitFor();
    await shot(page, 's3-stopped');
  });
});
