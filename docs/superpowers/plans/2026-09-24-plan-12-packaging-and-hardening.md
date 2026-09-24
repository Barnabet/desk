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

### Task 2: Visual QA against canvas row C, and the docs

**Files:**
- Create: `apps/desktop/src/renderer/threads/route.test.ts`, `docs/desktop.md`
- Modify: `apps/desktop/src/renderer/threads/route.ts`, `apps/desktop/src/renderer/threads/RouteView.tsx`, `apps/desktop/src/renderer/threads/threads.css`, `README.md`, `CLAUDE.md`

**Interfaces:**

- Consumes: the canvas row C boards (OrbitHome, OrbitMain, OrbitThread, OrbitAttention, OrbitMenuBar, SkillsMap), rendered at 1440×900, and the `DESK_E2E_SHOTS` screenshots from `pnpm test:e2e`.
- Produces: no new interfaces.

- [ ] **Step 1: Write the failing tests**

`apps/desktop/src/renderer/threads/route.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { emptyTranscript, reduceTranscript } from '@desk/client';
import { ev } from '@desk/client/testing';
import { narrate, routeLayout, stopsOf, stopText } from './route';

const at = (min: number) => new Date(Date.UTC(2026, 8, 24, 10, min)).toISOString();
const a = { agent: 't' };

function transcript() {
  return [
    ev(1, 'agent.created', { role: 'thread', model: 'opus', title: 'Emails', brief: 'Draft five emails', workspace_path: '/w', parent_id: 'd' }, { ...a, ts: at(18) }),
    ev(2, 'agent.status_changed', { status: 'running' }, { ...a, ts: at(18) }),
    ev(3, 'run.started', { run_id: 'r1', model: 'opus' }, { ...a, ts: at(19) }),
    ev(4, 'assistant.message', { run_id: 'r1', content: 'Reading the brief.', tool_calls: [] }, { ...a, ts: at(19) }),
    ev(5, 'tool.call', { run_id: 'r1', tool_call_id: 'c1', name: 'read_file', arguments: '{"path":"a.md"}' }, { ...a, ts: at(20) }),
    ev(6, 'tool.result', { run_id: 'r1', tool_call_id: 'c1', name: 'read_file', status: 'ok', content: 'x' }, { ...a, ts: at(20) }),
    ev(7, 'tool.call', { run_id: 'r1', tool_call_id: 'c2', name: 'read_file', arguments: '{"path":"b.md"}' }, { ...a, ts: at(21) }),
    ev(8, 'tool.result', { run_id: 'r1', tool_call_id: 'c2', name: 'read_file', status: 'ok', content: 'y' }, { ...a, ts: at(21) }),
    ev(9, 'agent.model_switched', { from: 'opus', to: 'fable', reason: 'rate', scope: 'run' }, { ...a, ts: at(31) }),
    ev(10, 'context.compacted', { run_id: 'r1', checkpoint: 'earlier', up_to: 8, trigger: 'threshold' }, { ...a, ts: at(40) }),
    ev(11, 'agent.result', { summary: 'Five drafts published', artifacts: ['emails/01.md'] }, { ...a, ts: at(52) }),
    ev(12, 'agent.revision', { round: 1, feedback: 'Emails 3 and 4 read as salesy.' }, { ...a, ts: at(58) }),
    ev(13, 'message.user', { text: 'Keep email 5 under 120 words.' }, { ...a, ts: at(62) }),
    ev(14, 'tool.call', { run_id: 'r2', tool_call_id: 'c3', name: 'write_file', arguments: '{"path":"emails/04.md"}' }, { ...a, ts: at(63) }),
  ].reduce(reduceTranscript, emptyTranscript('t')).entries;
}

describe('narrate', () => {
  it('numbers stops, merges work, and keeps the compaction divider', () => {
    const rows = narrate(transcript());
    expect(rows.map((r) => (r.kind === 'stop' ? `${r.stop.n}:${r.stop.kind}` : 'compacted'))).toEqual([
      '1:brief',
      '2:work',
      '3:detour',
      'compacted',
      '4:result',
      '5:revision',
      '6:steer',
      '7:work',
    ]);
    const stops = stopsOf(rows);
    expect(stops[1]!.tools.map((c) => c.name)).toEqual(['read_file', 'read_file']);
    expect(stopText(stops[1]!, 2).sub).toMatch(/^read_file ×2 · /);
    expect(stops[6]!.live).toBe(true);
    expect(stopText(stops[6]!, 2).title).toBe('Using 1 tool');
    expect(stopText(stops[4]!, 2)).toMatchObject({ title: 'Desk sent it back', quote: 'Emails 3 and 4 read as salesy.' });
    expect(stopText(stops[2]!, 2).sub).toMatch(/^continued on fable/);
  });
});

describe('routeLayout', () => {
  it('spreads each row across the full width, with the turns inside it', () => {
    const stops = stopsOf(narrate(transcript()));
    const l = routeLayout(stops, 980, false);
    const xs = l.points.filter((p) => p.row === 0).map((p) => p.x);
    expect(Math.min(...xs)).toBe(120);
    expect(Math.max(...xs)).toBe(860);
    for (const piece of l.pieces) {
      for (const n of piece.d.match(/-?\d+(\.\d+)?/g)!.filter((_, i) => i % 2 === 0).map(Number)) {
        expect(n).toBeGreaterThanOrEqual(0);
        expect(n).toBeLessThanOrEqual(980);
      }
    }
  });

  it('snakes stops across rows and marks the live stretch after the last revision', () => {
    const stops = stopsOf(narrate(transcript()));
    const l = routeLayout(stops, 980, true);
    expect(l.points).toHaveLength(7);
    const rows = [...new Set(l.points.map((p) => p.row))];
    expect(rows.length).toBeGreaterThan(1);
    const row0 = l.points.filter((p) => p.row === 0 && p.stop.kind !== 'detour');
    const row1 = l.points.filter((p) => p.row === 1);
    expect(row0[1]!.x).toBeGreaterThan(row0[0]!.x);
    if (row1.length > 1) expect(row1[1]!.x).toBeLessThan(row1[0]!.x);
    expect(l.points.find((p) => p.stop.kind === 'detour')!.y).toBeLessThan(row0[0]!.y);
    expect(l.pieces).toHaveLength(7);
    expect(l.pieces.at(-1)!.live).toBe(true);
    expect(l.pieces[0]!.live).toBe(false);
    expect(l.now).not.toBeNull();
    expect(l.tailPath).not.toBeNull();
    for (const p of l.points) expect(p.x).toBeGreaterThan(0), expect(p.x).toBeLessThan(980);
    expect(l.height).toBeGreaterThan(Math.max(...l.points.map((p) => p.y)));
  });

  it('has no now marker for a finished thread', () => {
    const l = routeLayout(stopsOf(narrate(transcript())), 1400, false);
    expect(l.now).toBeNull();
    expect(l.tail).toEqual([]);
    expect(l.pieces.every((p) => !p.live)).toBe(true);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop/src/renderer/threads/route.test.ts`
Expected: FAIL, because the modules don't exist yet.

- [ ] **Step 3: Implement**

`docs/desktop.md`:

```
# Desk desktop app

The desktop app (`apps/desktop`) is an Electron + React client for `deskd`. It uses design direction C, "Orbit map, refined". It is a client only: every piece of state lives in the daemon, and closing or quitting the app never stops the work.

- **Spec:** [`superpowers/specs/2026-09-24-desk-desktop-app-design.md`](superpowers/specs/2026-09-24-desk-desktop-app-design.md)
- **Plans:** 7 to 12 in [`superpowers/plans/`](superpowers/plans/)
- **API it consumes:** [`api.md`](api.md)

## Install

1. Build the release with `pnpm package:desktop`. It writes the following to `apps/desktop/release/`:
   - `Desk-<version>-<arch>.dmg`
   - `Desk-<version>-<arch>.zip`
2. Open the `.dmg` and drag Desk to Applications.
3. The build is ad-hoc signed and not notarised, so the first launch needs **right-click → Open**. If you launch it from anywhere else, Desk offers to move itself to Applications. It should live there because its background service runs from the app bundle.
4. Onboarding:
   1. **deskd.** Desk finds a running daemon. If none is running, it installs the bundled one as a LaunchAgent (`dev.desk.deskd`) and starts it.
   2. **Model endpoint.** Desk detects `DESK_OPENAI_*`, `~/.config/cliproxyapi.env` or a stored endpoint. If none is found, you enter a base URL and key. The key goes to the macOS Keychain, then you test the connection.
   3. **First project.**
   4. **The Map.**

The packaged daemon is `Desk.app/Contents/Resources/deskd/deskd.mjs`, run by the app's own binary with `ELECTRON_RUN_AS_NODE=1`. It ships with its migrations and the N-API `better-sqlite3` prebuild, so it needs no separate Node install.
- When a newer app finds an older daemon, it rewrites the LaunchAgent and restarts the daemon.
- **System → Repair LaunchAgent** does the same on demand.

The CLI's `desk up --install` uses the same LaunchAgent label, so there is only ever one deskd.

## Screens

| Place | What it does |
|---|---|
| **Map** (home) | Each project is a territory, sized by activity. Desk nodes have their threads in orbit, needs-you pins sit on the rim and deskd is the sun. The inspector shows the report headline, the plan, the threads and what needs you. There is a List view and a New project sheet with "More options". |
| **Conversation** | The line diagram: the trunk is Desk, threads fork and rejoin, stations link to chat items, and there is a live "now" marker. The chat shows Desk's replies (streamed), the thread feed, report and question cards, and notices. The composer can attach files to the Library or turn a message into a skill. A plan route panel sits alongside. |
| **Threads** | A roster of cards. Each thread has a serpentine route with numbered stops, synced to a Narrative or Every-step transcript. You can steer, stop or archive a thread, or turn it into a skill. Tabs: Result, Diff, Files, Skill drafts, Usage. |
| **Attention** | A flight-strip rack in four bays: clearance, queries, handoffs and holding. The inspector shows the exact arguments, who is asking and where, the policy reason and the thread's last words. Keys: J/K, ⌘⏎ approve, ⌘⌫ deny, E open. |
| **Skills** | A map and a list covering global, project and shadowed skills, with live usage. The detail view has instructions, files, history (compare, restore, change notes), an editor with uploads, import, delete, and Ask Desk. |
| **Library** | A grid with a safe preview (Markdown, text, code and images from the daemon). It shows each file's origin, supports drag-and-drop upload, and has Save a copy. |
| **Memory** | Entries grouped by kind, with search. You can add, correct (with the supersession chain shown) and delete. |
| **Settings** | About, sources, working style (check-ins, autonomy, review rounds, models, slots), the ordered policy editor with reset, and archive. |
| **System** | deskd start/restart/stop/repair and the proxy state; the model endpoint; notifications; the data directory and logs; the model registry editor; usage by model and project; and system notices. |
| **Menu bar** | A count badge and a popover with a mini line diagram of today. The strips let you approve or deny in place. The popover also shows what's running and has Open Desk. |

Global shortcuts: ⌘K (palette: places, projects, threads, skills, library titles, and memory search across projects), ⌘P (project switcher), and ⌘1 to ⌘4 (Map, Attention, Skills, System).

## Architecture

```
renderer (React, sandboxed, no Node)          main process (Node)                        deskd
  window.desk.invoke(channel, payload) ──IPC──▶ zod-validate ─▶ handler ─▶ DeskClient ──HTTP──▶ /v1/*
  window.desk.on('desk:…')            ◀─push── Broker (one WebSocket, all projects) ◀──WS─── /v1/stream
```

- **`src/main`**
  - `index.ts`: app lifecycle and the single-instance lock.
  - `daemon.ts`: `DaemonManager` (start, restart, stop, repair; launchd when packaged, `tsx` in dev).
  - `broker.ts`: the only stream subscriber. It fans out per-window watches, resumes from the last sequence and backs off with jitter.
  - `handlers.ts`: one handler per IPC channel.
  - `tray.ts`, `popover.ts`, `notify.ts`, `menu.ts`, and `windows.ts` (the `desk-app://` scheme, CSP, navigation lock).
- **`src/shared/ipc.ts`**: every channel's zod input schema. `handlers.test.ts` checks that every channel has a handler.
- **`src/preload`**: exposes only `invoke`, `on` and `platform`.
- **`src/renderer`**
  - `state/`:
    - `session.ts`: each project's event log, folded into chat, timeline, transcripts and streams.
    - `global.ts`: connection, health, overview, attention and system.
  - A hash router, and one folder per place.

### Security rules the app keeps

- The daemon token never leaves the main process. The renderer cannot reach deskd directly, and every IPC payload is validated in main.
- Agent content is untrusted. `SafeMarkdown` renders no raw HTML, and remote images become links. Links open in the system browser after a confirmation, and only `http`, `https` and `mailto` are allowed. Images come only from daemon bytes, through blob URLs.
- `app.openMain` accepts only `#/…` routes. The main window cannot navigate away from `desk-app://`.
- Nothing resembling a key is displayed. The endpoint panel shows the source and base URL only, and a new key goes to the Keychain through stdin.

## Development

```sh
pnpm desktop            # Vite HMR + Electron; finds or starts the repo daemon (tsx)
pnpm test               # includes the desktop unit and component tests (jsdom)
pnpm test:e2e           # builds the app, then Playwright-for-Electron against a real deskd + fake model
pnpm package:desktop    # release/Desk-<v>-<arch>.dmg and .zip (then `pnpm test:e2e` also runs the packaged test)
pnpm --filter @desk/desktop icon   # regenerate build/icon.png and icon.icns from build/icon.svg
```

The end-to-end suite (`apps/desktop/e2e`):

| File | Covers |
|---|---|
| `smoke.e2e.test.ts` | Onboarding, connecting, the map, and a live tray count |
| `flows.e2e.test.ts` | Brief → threads fork → question → approval in Attention → revision loop → report → steering → tray popover |
| `knowledge.e2e.test.ts` | Refine and restore a skill, library upload, memory correction, settings and policy, System, ⌘K |
| `packaged.e2e.test.ts` | The packaged `Desk.app` from a clean data dir. deskd runs as the LaunchAgent would, but no real LaunchAgent is installed. Skipped if there is no build. |

Set `DESK_E2E_SHOTS=<dir>` to save screenshots of each screen for visual review.

Environment switches:
- `DESK_DATA_DIR`: the daemon data directory.
- `DESK_USER_DATA`: Electron's `userData` directory.
- `DESK_E2E=1`: test hooks, no single-instance lock, a popover that stays open.
- `DESK_NODE`: the Node binary used to start the dev daemon.

## Packaging notes

`scripts/package.mjs` does the following:
1. Builds main, preload and renderer (all bundled, so there are no runtime `node_modules`) and bundles deskd.
2. Stages the app outside the workspace with an empty npm lockfile, so electron-builder collects no dependencies.
3. Lays out `Desk.app` from the local Electron distribution, which avoids any download.
4. Copies deskd with only the `darwin-*` sqlite prebuilds.
5. Signs the bundle ad hoc, then writes the `.dmg` with `hdiutil` and the `.zip` with `ditto`.

Signing with a Developer ID and notarising need your Apple identity. Set `mac.identity` and add a notarise step when you have one.

`--win` produces an NSIS installer layout. It is untested, because deskd itself is macOS-only for now (it depends on `sandbox-exec` and launchd).

## Troubleshooting

- **"Desk is not running" overlay:** use Start, or System → Restart. Logs are in `<data dir>/logs/` (System → Reveal logs) and the app's own `desktop.log` is in Electron's logs folder.
- **Agents paused:** the model proxy is down. Work resumes by itself, and System → Model endpoint → Test connection checks it.
- **"This deskd is vX; the app bundles vY" in System:** the running deskd is older than the app bundle. The app refreshes it at launch, and System → Repair LaunchAgent does the same on demand.
```

Comparison, by eye, at 1440×900:

| Board | App screen | Result |
|---|---|---|
| OrbitHome | Map | Matches: territories, orbits, pins, the deskd sun, inspector, legend and New project |
| OrbitMain | Conversation | Matches: line diagram, legend, report and question cards, plan panel, composer |
| OrbitThread | Threads → route | **Fixed:** the serpentine used a fixed 190 px step and left the remainder empty on the right, and its U-turn ran into the left edge. Columns now spread across the width (`step = (width − 2·MARGIN)/(perRow − 1)`), with MARGIN 120 and a turn bulge of 72. Tool stops read "N tools" in mono (radius 38) as in the board. Approval stops read "You approved" / "You denied". |
| OrbitAttention | Attention | Matches: bays, strips, inspector, keys and legend |
| OrbitMenuBar | Tray popover | Matches (reviewed in Plan 10) |
| SkillsMap | Skills | Matches (reviewed in Plan 11) |

Docs:
- `README.md` describes the three parts (daemon, CLI, app) and installing from the `.dmg`, and adds `package:desktop`. The "no UI" limitation becomes the unsigned-build note.
- `CLAUDE.md` names the app and `package:desktop`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(desktop): visual QA against canvas C — route fills the width with N-tools stops; docs for the app (README, docs/desktop.md, CLAUDE.md)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---


## Self-review

Coverage against spec §10, §11 (Plan 12's row) and §12:

| Spec item | Where |
|---|---|
| electron-builder `.dmg` and `.zip` with the bundled deskd | Task 1 (`package.mjs`). electron-builder lays out the app; `hdiutil` and `ditto` write the images. |
| The installed app works from a clean data dir | Task 1 (`packaged.e2e.test.ts`): bundled deskd under the app binary, then onboarding, a project, a Desk reply, and System showing the bundled daemon |
| §8 lifecycle: LaunchAgent install, refresh on version drift, Repair, Stop | Plans 9 and 11 (`DaemonManager`). Task 1 keeps the bundle path stable by offering to move to /Applications. |
| Visual QA at 1440×900 against canvas row C | Task 2 (comparison table and route fixes) |
| The full e2e suite (§10's flow list) | `smoke`, `flows` (Plan 10), `knowledge` (Plan 11) and `packaged` (Task 1). `pnpm test:e2e` runs all four. |
| Docs: README and `docs/desktop.md` | Task 2 |
| Unsigned build; Windows configured but untested | Task 1 (`identity: null` plus ad-hoc signing; `--win` gives an NSIS config), documented in Task 2 |
| `pnpm test:live` desktop smoke | Not added. The packaged e2e covers the same wiring against the fake model; a live smoke would only re-test the proxy, which `pnpm test:live` already covers for deskd. |

Both tasks were written from the code that shipped.
