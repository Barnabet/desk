import { mkdirSync, mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium, type Browser, type BrowserContext, type Page } from 'playwright';
import type { DeskClient } from '@desk/client';
import { clientFromDataDir } from '@desk/client/node';
import { startDaemon, type RunningDaemon } from '@desk/daemon';
import { startFakeModel, text, type FakeModelServer, type Script } from '@desk/fake-model';
import { startWebServer, type WebServer } from '@desk/web-server';

/** A browser context signed in through a one-time login link, on the app's first screen. */
export type SignedIn = {
  context: BrowserContext;
  page: Page;
  /** Content-Security-Policy violations and uncaught page errors seen so far (a passing run has none). */
  problems: string[];
};

export type WebE2E = {
  /** The temporary home folder desk web and deskd see: code/app, Documents and a hidden .config. */
  home: string;
  /** deskd's data dir (outside home): daemon.json, web.json, the database. */
  dataDir: string;
  fake: FakeModelServer;
  daemon: RunningDaemon;
  web: WebServer;
  /** What desk web printed for the person at the terminal (a login link used twice, …). */
  warnings: string[];
  /** A DeskClient on the same deskd, for checks behind the UI's back. */
  client(): DeskClient;
  /** Opens a new browser context on `link` (a fresh login link by default) and waits for the app to replace it. */
  signIn(link?: string): Promise<SignedIn>;
  /** Saves a full-page screenshot to $DESK_E2E_SHOTS/web-<name>.png when that variable is set. */
  shot(page: Page, name: string): Promise<void>;
  close(): Promise<void>;
};

/**
 * A real deskd (in process, on the fake model), desk web in front of it on a free port serving the built Angular app
 * (apps/web-ui/dist/browser: `pnpm test:web-e2e` builds it first), and headless Chromium.
 */
export async function startWebE2E(o: { script?: Script } = {}): Promise<WebE2E> {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'desk-web-e2e-')));
  const home = join(root, 'home');
  const dataDir = join(root, 'data');
  for (const dir of [join(home, 'code', 'app'), join(home, 'Documents'), join(home, '.config')]) mkdirSync(dir, { recursive: true });
  const closers: Array<() => Promise<void> | void> = [() => rmSync(root, { recursive: true, force: true })];
  // Every closer runs even when one throws (deskd, the fake model and the temp root still go); the first error is rethrown.
  const close = async () => {
    const errors: unknown[] = [];
    for (const c of closers.splice(0).reverse()) {
      try {
        await c();
      } catch (err) {
        errors.push(err);
      }
    }
    if (errors.length) throw errors[0];
  };
  try {
    const fake = await startFakeModel(o.script ?? (() => text('Noted.')));
    closers.push(() => fake.close());
    const daemon = await startDaemon({ dataDir, port: 0, home, modelConfig: { baseURL: fake.url, apiKey: 'test' } });
    closers.push(() => daemon.stop());
    const warnings: string[] = [];
    const web = await startWebServer({ dataDir, port: 0, open: false, home, log: (m) => void warnings.push(m) });
    closers.push(() => web.close());
    const browser: Browser = await chromium.launch();
    closers.push(() => browser.close());
    const shots = process.env.DESK_E2E_SHOTS;

    return {
      home,
      dataDir,
      fake,
      daemon,
      web,
      warnings,
      client: () => clientFromDataDir(dataDir),
      async signIn(link = web.loginLink()) {
        const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
        const page = await context.newPage();
        const problems: string[] = [];
        page.on('console', (m) => {
          if (m.type() === 'error' && /Content Security Policy/i.test(m.text())) problems.push(m.text());
        });
        page.on('pageerror', (err) => void problems.push(`uncaught: ${err.message}`));
        await page.goto(link);
        // /login stores the session secret and replaces itself with the app at /, so the code leaves the history.
        await page.waitForURL((url) => url.pathname === '/' && url.search === '');
        return { context, page, problems };
      },
      async shot(page, name) {
        if (!shots) return;
        mkdirSync(shots, { recursive: true });
        await page.screenshot({ path: join(shots, `web-${name}.png`), fullPage: true });
      },
      close,
    };
  } catch (err) {
    await close();
    throw err;
  }
}
