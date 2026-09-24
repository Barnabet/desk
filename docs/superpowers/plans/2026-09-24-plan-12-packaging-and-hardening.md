# Plan 12 · Packaging and hardening: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Desk as an installable macOS app (`.dmg` and `.zip`) with the bundled deskd. Prove that the installed app works from a clean data dir, compare every screen against canvas row C, and document the app.

**Architecture:**
- `scripts/package.mjs` builds the renderer, main and preload bundles and the deskd bundle.
- It stages a dependency-free app outside the workspace and lets electron-builder lay out `Desk.app` from the local Electron distribution, with no downloads.
- It copies deskd (with its migrations and the `darwin-*` better-sqlite3 prebuilds) into `Contents/Resources/deskd`, then signs ad hoc and writes the `.dmg` with `hdiutil` and the `.zip` with `ditto`.
- The app offers to move itself to /Applications, because the LaunchAgent runs deskd from the bundle.
- A packaged e2e test runs deskd exactly as the LaunchAgent would, but without installing a LaunchAgent, then onboards through the packaged app.

**Tech Stack:** electron-builder 26 (the `dir` target only), macOS `codesign`, `hdiutil`, `ditto`, `sips`, `iconutil`, and Playwright for Electron.

**Spec:** `docs/superpowers/specs/2026-09-24-desk-desktop-app-design.md` (§8 daemon lifecycle, §10 testing, §11 Plan 12 row, §12 out of scope).

## Global Constraints

- `pnpm test` and `pnpm typecheck` must pass before every commit, and `pnpm test:e2e` must pass at the end.
- **No real LaunchAgent in tests.** No test may write `~/Library/LaunchAgents` or register a launchd job.
- **Unsigned is expected.** Code signing and notarisation need the user's Apple identity (spec §12). The build is ad-hoc signed so that Apple silicon runs it.
- **The Windows target is configured but untested** (spec §12).
- **Secrets.** The packaged app and its deskd keep every secret rule: the key stays in the Keychain or env and never reaches `daemon.json`, logs or the renderer.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File map

| File | Responsibility |
|---|---|
| `apps/desktop/scripts/package.mjs` | build, stage, electron-builder `dir`, deskd copy, ad-hoc sign, `.dmg` and `.zip` |
| `apps/desktop/build/icon.svg` → `icon.png`, `icon.icns` | the app icon (orbit ring, accent sun, blue satellite) |
| `apps/desktop/scripts/icon.mjs`, `render-icon.cjs` | render the SVG with Electron, then build the iconset with `sips` and `iconutil` |
| `apps/desktop/src/main/index.ts` | `offerMoveToApplications()` before start |
| `apps/desktop/e2e/packaged.e2e.test.ts` | the packaged app from a clean data dir |
| `apps/desktop/src/renderer/threads/route.ts`, `RouteView.tsx`, `threads.css` | visual QA fixes: route fills the width, "N tools" stops, clearer approval label |
| `README.md`, `docs/desktop.md`, `CLAUDE.md` | docs |

---

### Task 1: Package Desk.app with the bundled deskd, the icon, Move to Applications, and the packaged e2e

**Files:**
- Create: `apps/desktop/e2e/packaged.e2e.test.ts`, `apps/desktop/scripts/package.mjs`, `apps/desktop/scripts/icon.mjs`, `apps/desktop/scripts/render-icon.cjs`, `apps/desktop/build/icon.svg`
- Modify: `apps/desktop/package.json`, `package.json`, `.gitignore`, `apps/desktop/src/main/index.ts`, `apps/desktop/vite.config.ts`

**Interfaces:**

- Consumes:
  - `buildMainAndPreload()` and `buildRenderer()` from `scripts/build.mjs`
  - `apps/daemon/scripts/bundle.mjs`, which writes `apps/daemon/dist/{deskd.mjs, drizzle/, node_modules/better-sqlite3}`
  - `DaemonManager`'s packaged paths: `process.resourcesPath/deskd/deskd.mjs` and `process.execPath` with `ELECTRON_RUN_AS_NODE=1`
- Produces:
  - `pnpm package:desktop` (root) and `pnpm --filter @desk/desktop package`, which write `apps/desktop/release/{mac-arm64/Desk.app, Desk-<v>-<arch>.dmg, Desk-<v>-<arch>.zip}`
  - `pnpm --filter @desk/desktop icon`

- [ ] **Step 1: Write the failing tests**

`apps/desktop/e2e/packaged.e2e.test.ts`:

```ts
import { spawn, type ChildProcess } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { _electron as electron, type ElectronApplication } from 'playwright';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { clientFromDataDir, readDaemonInfo } from '@desk/client/node';
import { startFakeModel, text, type FakeModelServer } from '@desk/fake-model';

/**
 * The packaged app (`pnpm --filter @desk/desktop package`) from a clean data dir. deskd runs exactly as its LaunchAgent
 * would run it (the app's own binary as Node, the bundled deskd.mjs), so the test never installs a real LaunchAgent.
 * Skipped when there is no packaged build.
 */
const release = fileURLToPath(new URL('../release', import.meta.url));
const appPath = join(release, process.arch === 'arm64' ? 'mac-arm64' : 'mac', 'Desk.app');
const binary = join(appPath, 'Contents', 'MacOS', 'Desk');
const bundle = join(appPath, 'Contents', 'Resources', 'deskd', 'deskd.mjs');
const packaged = process.platform === 'darwin' && existsSync(binary);

let dir: string;
let fake: FakeModelServer;
let deskd: ChildProcess;
let app: ElectronApplication;

async function waitFor<T>(fn: () => T | null | undefined | Promise<T | null | undefined>, ms = 20_000): Promise<T> {
  const deadline = Date.now() + ms;
  for (;;) {
    try {
      const v = await fn();
      if (v) return v;
    } catch {
      // Not yet.
    }
    if (Date.now() > deadline) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 200));
  }
}

beforeAll(async () => {
  if (!packaged) return;
  dir = mkdtempSync(join(tmpdir(), 'desk-packaged-'));
  fake = await startFakeModel(() => text('On it: two threads, one for the data source and one for invites.'));
  deskd = spawn(binary, [bundle, '--port', '0', '--data-dir', join(dir, 'data')], {
    env: { PATH: process.env.PATH ?? '', HOME: process.env.HOME ?? '', ELECTRON_RUN_AS_NODE: '1', DESK_BUNDLED: '1', DESK_OPENAI_BASE_URL: fake.url, DESK_OPENAI_API_KEY: 'test' },
    stdio: 'ignore',
  });
  await waitFor(async () => {
    const info = readDaemonInfo(join(dir, 'data'));
    return info && (await clientFromDataDir(join(dir, 'data')).health()).version;
  });
  app = await electron.launch({
    executablePath: binary,
    env: { ...process.env, DESK_DATA_DIR: join(dir, 'data'), DESK_USER_DATA: join(dir, 'user'), DESK_E2E: '1', DESK_OPENAI_BASE_URL: fake.url, DESK_OPENAI_API_KEY: 'test' },
  });
});

afterAll(async () => {
  if (!packaged) return;
  await app?.close();
  deskd?.kill('SIGTERM');
  await waitFor(() => !readDaemonInfo(join(dir, 'data')), 10_000).catch(() => {});
  await fake?.close();
  rmSync(dir, { recursive: true, force: true });
});

describe.skipIf(!packaged)('packaged app', () => {
  it('runs the bundled deskd under the app binary and onboards from a clean data dir', async () => {
    const client = clientFromDataDir(join(dir, 'data'));
    const health = await client.health();
    expect(health).toMatchObject({ version: '1.0.0' });

    const page = await app.firstWindow();
    await page.getByText('Welcome to Desk').waitFor();
    await page.getByText('deskd 1.0.0 is running.').waitFor();
    await page.getByRole('button', { name: 'Continue' }).click();
    await page.getByRole('button', { name: 'Test connection' }).click();
    await page.getByText(/^Connected\. \d+ models? available\.$/).waitFor();
    await page.getByRole('button', { name: 'Continue' }).click();
    await page.getByLabel('Name').fill('Launch');
    await page.getByLabel('Goal').fill('Relaunch onboarding next month');
    await page.getByRole('button', { name: 'Create project' }).click();
    await page.getByRole('heading', { name: 'Launch', exact: true }).waitFor();

    const [project] = await client.projects.list();
    await page.evaluate((h) => (location.hash = h), `#/p/${project!.id}/conversation`);
    await page.getByLabel('Message Desk').fill('Split the relaunch into threads');
    await page.getByLabel('Message Desk').press('Enter');
    await page.getByText('On it: two threads, one for the data source and one for invites.').waitFor();

    await page.evaluate(() => (location.hash = '#/system'));
    await page.getByText('Bundled deskd 1.0.0').waitFor();
    expect(await page.getByText('Running', { exact: true }).isVisible()).toBe(true);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @desk/desktop package && pnpm vitest run --config vitest.e2e.config.ts packaged`
Expected: FAIL, because the modules don't exist yet.

- [ ] **Step 3: Implement**

`apps/desktop/scripts/package.mjs`:

```js
// Packages Desk for macOS: builds the app and the bundled deskd, lays out Desk.app with electron-builder,
// signs it ad hoc (no Apple identity; Apple silicon refuses unsigned code), then writes release/Desk-<v>-<arch>.dmg and .zip.
// The Windows target is configured (`--win`) but untested: deskd itself is macOS-only for now.
import { execFileSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import builder from 'electron-builder';
import { buildMainAndPreload, buildRenderer } from './build.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
const repo = join(root, '..', '..');
const release = join(root, 'release');
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const require = createRequire(import.meta.url);
const electronPkg = JSON.parse(readFileSync(require.resolve('electron/package.json'), 'utf8'));
const win = process.argv.includes('--win');
const arch = process.arch === 'arm64' ? 'arm64' : 'x64';

const run = (file, args, opts = {}) => execFileSync(file, args, { stdio: 'inherit', ...opts });

rmSync(release, { recursive: true, force: true });
await buildMainAndPreload();
await buildRenderer();
run(process.execPath, [join(repo, 'apps', 'daemon', 'scripts', 'bundle.mjs')]);

// The staged app has no dependencies: main, preload and renderer are bundled. It is staged outside the
// workspace so electron-builder does not fall back to collecting apps/desktop's node_modules.
const stage = mkdtempSync(join(tmpdir(), 'desk-stage-'));
for (const f of ['main.cjs', 'preload.cjs', 'renderer']) cpSync(join(root, 'dist', f), join(stage, 'dist', f), { recursive: true });
// An empty npm lockfile tells electron-builder there is nothing to collect.
writeFileSync(join(stage, 'package-lock.json'), JSON.stringify({ name: 'desk', version: pkg.version, lockfileVersion: 3, requires: true, packages: { '': { name: 'desk', version: pkg.version } } }));
writeFileSync(
  join(stage, 'package.json'),
  JSON.stringify({ name: 'desk', productName: 'Desk', version: pkg.version, description: 'Desk: a coordinator and parallel threads for your projects.', author: 'Desk', main: 'dist/main.cjs' }, null, 2),
);

await builder.build({
  projectDir: stage,
  publish: 'never',
  ...(win ? { win: [] } : { mac: ['dir'] }),
  config: {
    appId: 'app.desk.desktop',
    productName: 'Desk',
    copyright: 'Desk',
    directories: { app: stage, output: release, buildResources: join(root, 'build') },
    electronVersion: electronPkg.version,
    electronDist: join(dirname(require.resolve('electron/package.json')), 'dist'),
    npmRebuild: false,
    nodeGypRebuild: false,
    buildDependenciesFromSource: false,
    asar: true,
    files: ['**/*'],
    mac: {
      category: 'public.app-category.productivity',
      icon: join(root, 'build', 'icon.icns'),
      identity: null,
      target: [{ target: 'dir', arch: [arch] }],
      extendInfo: { NSHighResolutionCapable: true },
    },
    win: { icon: join(root, 'build', 'icon.png'), target: [{ target: 'nsis', arch: ['x64'] }] },
    nsis: { oneClick: false, allowToChangeInstallationDirectory: true },
  },
});
rmSync(stage, { recursive: true, force: true });
if (win) process.exit(0);

const appDir = join(release, arch === 'arm64' ? 'mac-arm64' : 'mac', 'Desk.app');
if (!existsSync(appDir)) throw new Error(`Desk.app not found at ${appDir}`);
// The bundled deskd goes in Resources/deskd with its node_modules (electron-builder's extraResources drops those),
// keeping only this platform's better-sqlite3 prebuilds. Electron's default app is not needed.
const resources = join(appDir, 'Contents', 'Resources');
const deskd = join(resources, 'deskd');
cpSync(join(repo, 'apps', 'daemon', 'dist'), deskd, { recursive: true });
const prebuilds = join(deskd, 'node_modules', 'better-sqlite3', 'prebuilds');
for (const f of readdirSync(prebuilds)) if (!f.startsWith('darwin-')) rmSync(join(prebuilds, f));
rmSync(join(resources, 'default_app.asar'), { force: true });
run('codesign', ['--force', '--deep', '--sign', '-', appDir]);
run('codesign', ['--verify', '--deep', '--strict', appDir]);

const name = `Desk-${pkg.version}-${arch}`;
run('ditto', ['-c', '-k', '--sequesterRsrc', '--keepParent', appDir, join(release, `${name}.zip`)]);
const dmgRoot = join(release, 'dmg');
mkdirSync(dmgRoot);
cpSync(appDir, join(dmgRoot, 'Desk.app'), { recursive: true, verbatimSymlinks: true });
symlinkSync('/Applications', join(dmgRoot, 'Applications'));
run('hdiutil', ['create', '-volname', 'Desk', '-srcfolder', dmgRoot, '-fs', 'HFS+', '-format', 'UDZO', '-ov', join(release, `${name}.dmg`)], { stdio: 'ignore' });
rmSync(dmgRoot, { recursive: true, force: true });
console.log(`packaged ${join(release, `${name}.dmg`)} and ${name}.zip`);
```

`apps/desktop/scripts/icon.mjs`:

```js
// Regenerates build/icon.png and build/icon.icns from build/icon.svg (macOS: sips and iconutil).
import { execFileSync } from 'node:child_process';
import { mkdirSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const buildDir = join(root, 'build');
const png = join(buildDir, 'icon.png');
const electron = createRequire(import.meta.url)('electron');
execFileSync(electron, [join(root, 'scripts', 'render-icon.cjs'), join(buildDir, 'icon.svg'), png], { stdio: 'inherit' });

const set = join(buildDir, 'icon.iconset');
rmSync(set, { recursive: true, force: true });
mkdirSync(set);
for (const size of [16, 32, 128, 256, 512]) {
  execFileSync('sips', ['-z', String(size), String(size), png, '--out', join(set, `icon_${size}x${size}.png`)], { stdio: 'ignore' });
  execFileSync('sips', ['-z', String(size * 2), String(size * 2), png, '--out', join(set, `icon_${size}x${size}@2x.png`)], { stdio: 'ignore' });
}
execFileSync('iconutil', ['-c', 'icns', set, '-o', join(buildDir, 'icon.icns')]);
rmSync(set, { recursive: true, force: true });
console.log(`wrote ${png} and icon.icns`);
```

`apps/desktop/scripts/render-icon.cjs`:

```js
// Run with Electron: renders build/icon.svg to a 1024 px PNG (argv: svg path, png path).
const { app, BrowserWindow } = require('electron');
const { readFileSync, writeFileSync } = require('node:fs');

const [svgPath, pngPath] = process.argv.slice(-2);
app.disableHardwareAcceleration();
app.whenReady().then(async () => {
  const win = new BrowserWindow({ width: 1024, height: 1024, show: false, transparent: true, frame: false, useContentSize: true, webPreferences: { offscreen: true } });
  win.webContents.setZoomFactor(1);
  const svg = readFileSync(svgPath, 'utf8');
  const html = `<html><body style="margin:0;background:transparent">${svg}</body></html>`;
  await win.loadURL(`data:text/html;base64,${Buffer.from(html).toString('base64')}`);
  await new Promise((r) => setTimeout(r, 300));
  const image = await win.webContents.capturePage({ x: 0, y: 0, width: 1024, height: 1024 });
  writeFileSync(pngPath, image.resize({ width: 1024, height: 1024 }).toPNG());
  app.quit();
});
```

`apps/desktop/build/icon.svg`:

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#f5f1e8"/>
      <stop offset="1" stop-color="#e6dfd1"/>
    </linearGradient>
    <filter id="s" x="-10%" y="-10%" width="120%" height="125%">
      <feDropShadow dx="0" dy="12" stdDeviation="14" flood-color="#1c1b18" flood-opacity="0.28"/>
    </filter>
  </defs>
  <rect x="100" y="100" width="824" height="824" rx="185" fill="url(#g)" filter="url(#s)"/>
  <g fill="none" stroke-linecap="round">
    <ellipse cx="512" cy="512" rx="300" ry="300" stroke="#c9bfad" stroke-width="7" stroke-dasharray="2 22"/>
    <circle cx="512" cy="512" r="205" stroke="#1c1b18" stroke-width="26"/>
  </g>
  <circle cx="512" cy="512" r="74" fill="#c4441c"/>
  <circle cx="657" cy="367" r="34" fill="#2f5bd3" stroke="#f3efe6" stroke-width="14"/>
  <circle cx="300" cy="724" r="20" fill="#a15c00" stroke="#efeae0" stroke-width="10"/>
</svg>
```

Other changes:
- `apps/desktop/package.json` gains `"package": "node scripts/package.mjs"`, `"icon": "node scripts/icon.mjs"` and the dev dependency `electron-builder`.
- The root `package.json` gains `"package:desktop"`.
- `.gitignore` gains `apps/desktop/release/`.
- `build/icon.png` and `build/icon.icns` are generated by `pnpm --filter @desk/desktop icon` and committed.
- `vite.config.ts` sets `chunkSizeWarningLimit: 2000`, because the renderer is one local bundle.
- `src/main/index.ts` adds `offerMoveToApplications`. For a packaged darwin build outside /Applications (and not in e2e), it asks "Move Desk to your Applications folder?". On Move it calls `app.moveToApplicationsFolder()`, which relaunches. It runs before `start()`.

Things learned while packaging:
- electron-builder falls back to collecting `apps/desktop/node_modules` when the app dir has none. Staging in `os.tmpdir()` with an empty npm lockfile keeps `app.asar` down to `dist/` and `package.json`.
- `extraResources` drops `node_modules`, so deskd is copied after layout.
- `identity: null` skips signing, so an explicit `codesign --force --deep --sign -` follows.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(desktop): package Desk.app — dmg and zip with the bundled deskd, ad-hoc signed, app icon, Move to Applications, packaged e2e from a clean data dir

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

