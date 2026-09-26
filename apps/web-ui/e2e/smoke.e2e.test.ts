import { join } from 'node:path';
import { startWebE2E, type WebE2E } from './harness';

let e2e: WebE2E;

beforeAll(async () => {
  e2e = await startWebE2E();
});

afterAll(async () => {
  await e2e?.close();
});

describe('desk web', () => {
  it('signs in with a one-time link, connects to deskd, and onboards with a folder from the folder browser', async () => {
    const { context, page, problems } = await e2e.signIn();
    await page.getByText('Welcome to Desk').waitFor();
    await page.waitForURL(/#\/onboarding$/);
    expect(page.url()).not.toContain('code=');
    expect(await page.evaluate(() => localStorage.getItem('desk.session'))).toMatch(/^[A-Za-z0-9_-]{43}$/);

    await page.getByText('deskd 1.0.0 is running.').waitFor();
    await e2e.shot(page, 'onboarding-daemon');
    await page.getByRole('button', { name: 'Continue' }).click();
    await page.getByText(/from the DESK_OPENAI_\* environment variables/).waitFor();
    await page.getByRole('button', { name: 'Test connection' }).click();
    await page.getByText(/^Connected\. \d+ models? available\.$/).waitFor();
    await page.getByRole('button', { name: 'Continue' }).click();

    await page.getByLabel('Name').fill('Launch');
    await page.getByLabel('Goal').fill('Relaunch onboarding next month');
    await page.getByRole('button', { name: 'Add folder…' }).click();
    const sheet = page.getByRole('dialog', { name: 'Choose a folder' });
    await sheet.getByRole('button', { name: 'code', exact: true }).waitFor();
    expect(await sheet.getByRole('button', { name: '.config' }).count()).toBe(0);
    await sheet.getByRole('button', { name: 'code', exact: true }).click();
    await sheet.getByRole('button', { name: 'app', exact: true }).click();
    const folder = join(e2e.home, 'code', 'app');
    await expect.poll(() => sheet.getByLabel('Folder', { exact: true }).inputValue()).toBe(folder);
    await e2e.shot(page, 'folder-browser');
    await sheet.getByRole('button', { name: 'Choose this folder' }).click();
    await sheet.waitFor({ state: 'detached' });
    await page.getByText(folder, { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Create project' }).click();

    await page.waitForURL(/#\/map$/);
    await page.getByRole('heading', { name: 'Projects', exact: true }).waitFor();
    // The empty state goes once the pushed global state brings the new project to the map.
    await page.getByText('No projects yet').waitFor({ state: 'detached' });
    await e2e.shot(page, 'map');
    const [project] = await e2e.client().projects.list();
    expect(project).toMatchObject({ name: 'Launch', goal: 'Relaunch onboarding next month' });
    expect((await e2e.client().projects.get(project!.id)).sources.map((s) => s.path)).toEqual([folder]);

    const served = await page.evaluate(async () => {
      const r = await fetch(location.href);
      return { csp: r.headers.get('content-security-policy'), cache: r.headers.get('cache-control') };
    });
    expect(served.csp).toContain("script-src 'self'");
    expect(served.csp).toContain("frame-ancestors 'none'");
    expect(served.cache).toBe('no-store');
    expect(await context.cookies()).toEqual([]);
    expect(await page.evaluate(() => JSON.stringify({ ...localStorage }))).not.toContain(e2e.daemon.token);
    expect(await page.content()).not.toContain(e2e.daemon.token);
    expect(problems).toEqual([]);
    await context.close();
  });

  it('signs the browser out when its login link is used a second time', async () => {
    const link = e2e.web.loginLink();
    const { context, page } = await e2e.signIn(link);
    await page.getByText('Welcome to Desk').waitFor();
    const replay = await context.newPage();
    expect((await replay.goto(link))?.status()).toBe(401);
    await replay.close();
    await page.reload();
    await page.getByText(/Open Desk from your terminal/).waitFor();
    expect(await page.evaluate(() => localStorage.getItem('desk.session'))).toBeNull();
    expect(e2e.warnings).toContainEqual(expect.stringContaining('a login link was used twice'));
    await context.close();
  });
});
