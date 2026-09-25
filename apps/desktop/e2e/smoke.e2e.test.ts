import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { _electron as electron, type ElectronApplication } from 'playwright';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { clientFromDataDir } from '@desk/client/node';
import { startDaemon, type RunningDaemon } from '@desk/daemon';
import { call, startFakeModel, text, tools, type FakeModelServer } from '@desk/fake-model';

const appDir = fileURLToPath(new URL('..', import.meta.url));
let dir: string;
let fake: FakeModelServer;
let daemon: RunningDaemon;
let app: ElectronApplication;

beforeAll(async () => {
  dir = mkdtempSync(join(tmpdir(), 'desk-e2e-'));
  fake = await startFakeModel((req) =>
    req.messages.some((m) => m.role === 'tool') ? text('Asked.') : tools(call('ask_user', { question: 'Data source or teammate invite first?', options: ['Data source', 'Invite'] })),
  );
  daemon = await startDaemon({ dataDir: join(dir, 'data'), port: 0, modelConfig: { baseURL: fake.url, apiKey: 'test' } });
  const { default: electronPath } = await import('electron');
  app = await electron.launch({
    executablePath: electronPath as unknown as string,
    args: [appDir],
    // Playwright emulates a light prefers-color-scheme by default; let nativeTheme drive it, as in the real app.
    colorScheme: null,
    env: { ...process.env, DESK_DATA_DIR: join(dir, 'data'), DESK_USER_DATA: join(dir, 'user'), DESK_E2E: '1' },
  });
});

afterAll(async () => {
  await app?.close();
  await daemon?.stop();
  await fake?.close();
  rmSync(dir, { recursive: true, force: true });
});

const trayTitle = () => app.evaluate(() => (globalThis as unknown as { __deskTest: { trayTitle(): string } }).__deskTest.trayTitle());

describe('desktop shell', () => {
  it('onboards, connects to deskd, lands on the map, and keeps the tray count live', async () => {
    const page = await app.firstWindow();
    await page.getByText('Welcome to Desk').waitFor();
    await page.getByText('deskd 1.0.0 is running.').waitFor();
    await page.getByRole('button', { name: 'Continue' }).click();
    await page.getByText(/from the DESK_OPENAI_\* environment variables/).waitFor();
    await page.getByRole('button', { name: 'Test connection' }).click();
    await page.getByText(/^Connected\. \d+ models? available\.$/).waitFor();
    await page.getByRole('button', { name: 'Continue' }).click();
    await page.getByLabel('Name').fill('Launch');
    await page.getByLabel('Goal').fill('Relaunch onboarding next month');
    await page.getByRole('button', { name: 'Create project' }).click();
    await page.getByRole('heading', { name: 'Projects', exact: true }).waitFor();
    await page.getByRole('heading', { name: 'Launch', exact: true }).waitFor();
    expect(await page.getByRole('link', { name: 'All clear' }).isVisible()).toBe(true);
    expect(await trayTitle()).toBe('');

    const client = clientFromDataDir(join(dir, 'data'));
    const [project] = await client.projects.list();
    await client.projects.send(project!.id, 'Kick things off');
    await page.getByRole('link', { name: '1 need you', exact: true }).waitFor({ timeout: 20_000 });
    await expect.poll(trayTitle, { timeout: 10_000 }).toBe('1');

    const csp = await page.evaluate(async () => (await fetch(location.href)).headers.get('content-security-policy'));
    expect(csp).toContain("default-src 'self'");
    expect(await page.evaluate(() => typeof (window as unknown as { require?: unknown }).require)).toBe('undefined');
    expect(await page.evaluate(() => Object.keys((window as unknown as { desk: object }).desk).sort())).toEqual(['invoke', 'on', 'platform']);
  });

  it('switches every window between light and dark from System → Appearance', async () => {
    const page = await app.firstWindow();
    const look = () => page.evaluate(() => ({ dark: matchMedia('(prefers-color-scheme: dark)').matches, ground: getComputedStyle(document.body).backgroundColor }));
    const fill = () => app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map((w) => w.getBackgroundColor()));
    await page.evaluate(() => (window.location.hash = '#/system'));
    const appearance = page.getByRole('group', { name: 'Appearance' });
    await appearance.getByRole('button', { name: 'Dark' }).click();
    await expect.poll(look).toEqual({ dark: true, ground: 'rgb(23, 22, 19)' });
    await expect.poll(fill).toContain('#171613');
    await appearance.getByRole('button', { name: 'Light' }).click();
    await expect.poll(look).toEqual({ dark: false, ground: 'rgb(239, 234, 224)' });
    await expect.poll(fill).toContain('#EFEAE0');
    expect(await app.evaluate(({ nativeTheme }) => nativeTheme.themeSource)).toBe('light');
    await appearance.getByRole('button', { name: 'System' }).click();
    await expect.poll(() => app.evaluate(({ nativeTheme }) => nativeTheme.themeSource)).toBe('system');
  });

  it('attaches a pasted screenshot, and files dropped on the chat, to the Library', async () => {
    const page = await app.firstWindow();
    const client = clientFromDataDir(join(dir, 'data'));
    const [project] = await client.projects.list();
    await page.evaluate((h) => (window.location.hash = h), `#/p/${project!.id}/conversation`);
    const box = page.getByLabel('Message Desk');
    await box.fill('');
    await box.evaluate((el) => {
      const dt = new DataTransfer();
      dt.items.add(new File([new Uint8Array([137, 80, 78, 71])], 'image.png', { type: 'image/png' }));
      el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    });
    await expect.poll(() => box.inputValue()).toMatch(/^Attached: (\S+\/)?pasted-[\d-]+\.png\n$/);
    await box.fill('');
    await page.locator('.chat-list').evaluate((el) => {
      const dt = new DataTransfer();
      dt.items.add(new File(['# Spec'], 'spec.md', { type: 'text/markdown' }));
      el.dispatchEvent(new DragEvent('dragenter', { dataTransfer: dt, bubbles: true, cancelable: true }));
      el.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
    });
    await expect.poll(() => box.inputValue()).toMatch(/^Attached: (\S+\/)?spec\.md\n$/);
    const names = (await client.library.list(project!.id)).map((a) => a.path);
    expect(names.some((n) => /pasted-[\d-]+\.png$/.test(n))).toBe(true);
    expect(names.some((n) => n.endsWith('spec.md'))).toBe(true);
    await box.fill('');
  });
});
