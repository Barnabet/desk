# Plan 9 · Desktop shell — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `apps/desktop`, an Electron + React + Vite app that finds or starts deskd, connects through a main-process broker, and keeps the tray count live. It has a secure preload bridge, the design-C shell (title bar, tokens, core components, SafeMarkdown) and onboarding. Plans 10 and 11 fill in the screens.

**Architecture:** The main process is the only deskd client (spec approach A).
- The `Broker` owns one `DeskClient` and one `DeskStream('*')`. It keeps the global state (overview, attention, system), refetches it (debounced) after relevant events, and forwards project events to the windows that watch them. It backfills from `/events` so a window never misses an event.
- The renderer calls main through one validated invoke channel (`desk:invoke` plus an operation name with a zod schema). Results come back as `{ ok, value } | { ok: false, error: { code, message, status? } }`. The token never leaves main.
- `DaemonManager` finds, starts, restarts and stops deskd. In dev it spawns the repo daemon through tsx; in a packaged macOS build it installs the `dev.desk.deskd` LaunchAgent, which runs the bundled `deskd.mjs` with `ELECTRON_RUN_AS_NODE`.

**Tech Stack:**
- Electron 44, React 19, Vite 8 with `@vitejs/plugin-react`, and esbuild for main and preload.
- zod 4, react-markdown 10 with remark-gfm.
- Fonts from `@fontsource-variable` (Geist, Geist Mono, Newsreader; all OFL).
- Vitest 5 with jsdom and Testing Library; Playwright for the Electron end-to-end test.

**Spec:** `docs/superpowers/specs/2026-09-24-desk-desktop-app-design.md` §1–3, §5, §7 (Shell, Onboarding, Tray, Resilience, Safety), §8, §9, §10.

## Global Constraints

- `pnpm test` and `pnpm typecheck` must pass before every commit. The Electron end-to-end suite runs separately with `pnpm test:e2e`.
- Renderer security:
  - `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`, and a strict CSP (`default-src 'self'`, with no remote scripts, fonts or images).
  - The preload exposes only `window.desk.{invoke,on,platform}`.
- The bearer token, raw daemon errors and stack traces never cross IPC. Every invoke payload is zod-validated in main, and the sender frame must be the app's own origin.
- Agent text is untrusted:
  - SafeMarkdown renders no raw HTML and turns remote images into links.
  - Links open in the system browser only after a confirmation, and only for `http:`, `https:` and `mailto:`.
- No optimistic agent state. Writes show a pending control until they complete, and the event stream confirms the change.
- Design tokens (spec §5):
  - ground `#EFEAE0`, title bar `#E6E0D4`, rules `#D6CFC1`/`#E3DFD6`
  - ink `#1C1B18`, text `#3D3A34`, minimum text `#4A4740`
  - running `#2F5BD3`/`#1F45A8`/`#E3E8F5`, waiting `#A15C00`/`#7A4500`/`#F3E6CF`, done/muted `#8A857B`/`#B7C4E6`
  - accent `#C4441C`, tint = accent + `14`
  - cards: white, 1px `#D6CFC1`, radius 16, shadow `0 16px 36px rgba(28,27,24,.14)`
  - fonts: Newsreader, Geist, Geist Mono
- The app's code stays portable. Anything macOS-only must have a fallback: the LaunchAgent throws `unsupported` elsewhere, the tray title is macOS-only and the dock badge is optional.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Work on the `desktop-app` branch.

## File map

| File | Responsibility |
|---|---|
| `packages/protocol/src/settings.ts` | `ProjectSettingsPatch` without defaults (bug fix) |
| `apps/desktop/package.json`, `tsconfig.json`, `vite.config.ts`, `scripts/{build,dev}.mjs` | package, types, bundling |
| `apps/desktop/src/shared/channels.ts` | invoke and push channel names (no deps; used by preload) |
| `apps/desktop/src/shared/ipc.ts` | per-operation zod schemas, `IpcResult`, `AppSettings` |
| `apps/desktop/src/shared/state.ts` | `GlobalState` and its initial value |
| `apps/desktop/src/shared/attention.ts` | strip codes per attention kind |
| `apps/desktop/src/main/errors.ts` | `UserFacingError` |
| `apps/desktop/src/main/handlers.ts` | operation → `DeskClient`/app call; `dispatch`, `toIpcError`, `ChannelOutput` |
| `apps/desktop/src/main/broker.ts` | the `Broker` |
| `apps/desktop/src/main/launchd.ts`, `daemon.ts` | LaunchAgent plist; `DaemonManager` |
| `apps/desktop/src/main/settings.ts` | app settings in `userData/settings.json` |
| `apps/desktop/src/main/notify.ts` | notification text and route for an attention item |
| `apps/desktop/src/main/trayModel.ts`, `trayIcon.ts`, `tray.ts` | tray menu model, template icon bitmap, the Electron tray |
| `apps/desktop/src/main/windows.ts`, `menu.ts`, `log.ts`, `index.ts` | windows, CSP and app protocol, menu, log, app lifecycle |
| `apps/desktop/src/preload/index.ts` | contextBridge |
| `apps/desktop/src/renderer/**` | React app: bridge, stores, router, tokens, components, shell, onboarding, interim screens |
| `apps/desktop/e2e/smoke.e2e.test.ts` | Playwright end-to-end test against a real deskd |

---

### Task 1: Settings patches must not fill defaults (backend bug fix)

In zod 4, `ProjectSettings.partial()` still applies each field's `.default()`. So `PATCH /v1/projects/:id { settings: { check_in } }` parses into a *complete* settings object, and the projection merges it over the stored settings. Every other setting (models, policy, review rounds) is reset to its default. The desktop Settings screen patches single fields, so this fix comes first.

**Files:**
- Modify: `packages/protocol/src/settings.ts`, `packages/protocol/src/settings.test.ts`, `apps/daemon/src/app.test.ts`

**Interfaces:**
- Produces: `ProjectSettingsPatch`, which parses to exactly the given keys. `ProjectSettings` and `resolveSettings` keep their behaviour.

- [ ] **Step 1: Write the failing tests**

Append to the `describe('ProjectSettings', …)` block in `packages/protocol/src/settings.test.ts`, and add `ProjectSettingsPatch` and `UpdateProjectRequest` to its import:

```ts
  it('parses patches to exactly the given keys (no defaults)', () => {
    expect(ProjectSettingsPatch.parse({ check_in: 'minimal' })).toEqual({ check_in: 'minimal' });
    expect(UpdateProjectRequest.parse({ settings: { review_rounds: 3 } })).toEqual({ settings: { review_rounds: 3 } });
    expect(ProjectSettingsPatch.parse({})).toEqual({});
    expect(() => ProjectSettingsPatch.parse({ check_in: 'loud' })).toThrow();
  });
```

The import line becomes:

```ts
import { DEFAULT_POLICY, EventBody, ProjectSettings, ProjectSettingsPatch, resolveSettings, UpdateProjectRequest } from '@desk/protocol';
```

Append to `describe('projects', …)` in `apps/daemon/src/app.test.ts`:

```ts
  it('patches one setting without resetting the others', async () => {
    const { api } = await setup();
    const { project } = await newProject(api, { settings: { thread_model: FAKE_MODEL.id, review_rounds: 5, policy: [] } });
    await api('PATCH', `/projects/${project.id}`, { settings: { check_in: 'minimal' } });
    const s = (await api('GET', `/projects/${project.id}`)).body.project.settings;
    expect(s).toMatchObject({ check_in: 'minimal', review_rounds: 5, thread_model: FAKE_MODEL.id, policy: [] });
  });
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run packages/protocol/src/settings.test.ts apps/daemon/src/app.test.ts`
Expected: FAIL: the patch contains every default, and `review_rounds` comes back as 2.

- [ ] **Step 3: Implement**

In `packages/protocol/src/settings.ts`, replace the `ProjectSettings` and `ProjectSettingsPatch` definitions with:

```ts
/** Field validators without defaults, shared by the full settings and patches. */
const settingsFields = {
  desk_model: z.string().min(1),
  thread_model: z.string().min(1),
  fallback_model: z.string().min(1).nullable(),
  max_concurrent_threads: z.number().int().min(1).max(32),
  check_in: z.enum(['minimal', 'normal', 'detailed']),
  autonomy: z.enum(['dispatch-freely', 'ask-before-dispatch']),
  review_rounds: z.number().int().min(0).max(10),
  policy: z.array(PolicyRule),
};

export const ProjectSettings = z.object({
  desk_model: settingsFields.desk_model.default('claude-opus-5-5'),
  thread_model: settingsFields.thread_model.default('claude-opus-5-5'),
  fallback_model: settingsFields.fallback_model.default(null),
  max_concurrent_threads: settingsFields.max_concurrent_threads.default(4),
  check_in: settingsFields.check_in.default('normal'),
  autonomy: settingsFields.autonomy.default('dispatch-freely'),
  review_rounds: settingsFields.review_rounds.default(2),
  policy: settingsFields.policy.default(() => DEFAULT_POLICY.map((r) => ({ ...r }))),
});
export type ProjectSettings = z.infer<typeof ProjectSettings>;

/** A settings patch: only the given keys (zod 4 would apply `.default()`s inside `.partial()`, resetting the rest). */
export const ProjectSettingsPatch = z.object(settingsFields).partial();
export type ProjectSettingsPatch = z.input<typeof ProjectSettingsPatch>;
```

- [ ] **Step 4: Run tests**

Run: `pnpm test && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/protocol apps/daemon/src/app.test.ts
git commit -m "fix(protocol): settings patches carry only the given keys

zod 4 applies .default() inside .partial(), so PATCH /projects/:id with one
setting reset every other setting (models, policy) to its default.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Desktop workspace scaffold

**Files:**
- Create:
  - `apps/desktop/package.json`, `apps/desktop/tsconfig.json`, `apps/desktop/vite.config.ts`
  - `apps/desktop/scripts/build.mjs`, `apps/desktop/scripts/dev.mjs`
  - `apps/desktop/src/renderer/index.html`, and temporary `src/renderer/main.tsx`, `src/main/index.ts` and `src/preload/index.ts`
  - `apps/desktop/src/shared/channels.ts`, `apps/desktop/src/shared/channels.test.ts`
  - `vitest.e2e.config.ts`
- Modify: `package.json`, `tsconfig.json`, `vitest.config.ts`, `.gitignore`

**Interfaces:**
- Produces:
  - `pnpm --filter @desk/desktop build`, which writes `apps/desktop/dist/{main.cjs,preload.cjs,renderer/}`
  - `pnpm desktop`, the dev app with Vite HMR
  - `pnpm typecheck`, which now also checks `apps/desktop`
  - Vitest picks up `*.test.tsx`
  - `INVOKE_CHANNEL`, `PUSH_CHANNELS` and `PushChannel`

- [ ] **Step 1: Write the failing test** — `apps/desktop/src/shared/channels.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { INVOKE_CHANNEL, PUSH_CHANNELS } from './channels';

describe('channels', () => {
  it('names the bridge channels', () => {
    expect(INVOKE_CHANNEL).toBe('desk:invoke');
    expect(PUSH_CHANNELS).toEqual(['desk:global', 'desk:event', 'desk:ephemeral', 'desk:navigate']);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop`
Expected: FAIL: `./channels` is missing.

- [ ] **Step 3: Implement**

`apps/desktop/package.json`:

```json
{
  "name": "@desk/desktop",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "main": "dist/main.cjs",
  "scripts": {
    "build": "node scripts/build.mjs",
    "dev": "node scripts/dev.mjs",
    "start": "electron ."
  },
  "dependencies": {
    "@desk/client": "workspace:*",
    "@desk/protocol": "workspace:*",
    "@fontsource-variable/geist": "^5.3.0",
    "@fontsource-variable/geist-mono": "^5.3.0",
    "@fontsource-variable/newsreader": "^5.3.0",
    "react": "^19.3.0",
    "react-dom": "^19.3.0",
    "react-markdown": "^10.1.0",
    "remark-gfm": "^4.0.1",
    "zod": "^4.6.5"
  },
  "devDependencies": {
    "@desk/core": "workspace:*",
    "@desk/daemon": "workspace:*",
    "@desk/fake-model": "workspace:*",
    "@testing-library/react": "^16.3.3",
    "@testing-library/user-event": "^14.6.7",
    "@types/react": "^19.3.0",
    "@types/react-dom": "^19.3.0",
    "@vitejs/plugin-react": "^6.1.1",
    "electron": "^44.4.5",
    "esbuild": "^0.28.2",
    "playwright": "^1.63.0",
    "vite": "^8.3.0"
  }
}
```

`apps/desktop/tsconfig.json`:

```json
{
  "extends": "../../tsconfig.json",
  "compilerOptions": {
    "lib": ["ES2023", "DOM", "DOM.Iterable"],
    "jsx": "react-jsx",
    "types": ["node"]
  },
  "include": ["src", "e2e", "vite.config.ts"],
  "exclude": ["dist", "node_modules"]
}
```

`apps/desktop/vite.config.ts`:

```ts
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  root: fileURLToPath(new URL('./src/renderer', import.meta.url)),
  base: './',
  plugins: [react()],
  build: { outDir: fileURLToPath(new URL('./dist/renderer', import.meta.url)), emptyOutDir: true, sourcemap: true },
  server: { port: 5173, strictPort: true },
});
```

`apps/desktop/scripts/build.mjs`:

```js
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const root = fileURLToPath(new URL('..', import.meta.url));

/** Main and preload: CommonJS bundles (Electron's sandboxed preload must be one file). */
export async function buildMainAndPreload() {
  const common = { bundle: true, platform: 'node', format: 'cjs', target: 'node22', external: ['electron'], sourcemap: true, logLevel: 'warning' };
  await Promise.all([
    build({ ...common, entryPoints: [join(root, 'src/main/index.ts')], outfile: join(root, 'dist/main.cjs') }),
    build({ ...common, entryPoints: [join(root, 'src/preload/index.ts')], outfile: join(root, 'dist/preload.cjs') }),
  ]);
}

export async function buildRenderer() {
  const { build: viteBuild } = await import('vite');
  await viteBuild({ configFile: join(root, 'vite.config.ts'), logLevel: 'warn' });
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  await buildMainAndPreload();
  await buildRenderer();
  console.log(`built ${join(root, 'dist')}`);
}
```

`apps/desktop/scripts/dev.mjs`:

```js
import { spawn } from 'node:child_process';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { buildMainAndPreload } from './build.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
await buildMainAndPreload();
const server = await createServer({ configFile: join(root, 'vite.config.ts') });
await server.listen();
const url = server.resolvedUrls?.local[0] ?? 'http://localhost:5173/';
const { default: electronPath } = await import('electron');
const child = spawn(electronPath, [root], { stdio: 'inherit', env: { ...process.env, DESK_RENDERER_URL: url } });
child.on('exit', async (code) => {
  await server.close();
  process.exit(code ?? 0);
});
```

`apps/desktop/src/shared/channels.ts`:

```ts
/** The single renderer → main channel; the first argument names the operation (see ipc.ts). */
export const INVOKE_CHANNEL = 'desk:invoke';

/** Main → renderer pushes: global state, project events, streamed text, and navigation requests. */
export const PUSH_CHANNELS = ['desk:global', 'desk:event', 'desk:ephemeral', 'desk:navigate'] as const;
export type PushChannel = (typeof PUSH_CHANNELS)[number];
```

`apps/desktop/src/renderer/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Desk</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="./main.tsx"></script>
  </body>
</html>
```

These three temporary entry points are replaced in Tasks 7–8.

`src/renderer/main.tsx`:

```tsx
import { createRoot } from 'react-dom/client';

createRoot(document.getElementById('root')!).render(<p>Desk</p>);
```

`src/main/index.ts`:

```ts
import { join } from 'node:path';
import { app, BrowserWindow } from 'electron';

app.whenReady().then(() => {
  const win = new BrowserWindow({ width: 800, height: 600, webPreferences: { preload: join(__dirname, 'preload.cjs'), sandbox: true, contextIsolation: true } });
  void win.loadURL(process.env.DESK_RENDERER_URL ?? `file://${join(__dirname, 'renderer', 'index.html')}`);
});
```

`src/preload/index.ts`:

```ts
import { contextBridge } from 'electron';

contextBridge.exposeInMainWorld('desk', {});
```

Root `package.json`:
- Add `"jsdom": "^30.1.1"` to `devDependencies`. Vitest resolves test environments from the repo root.
- Replace the `typecheck` script and add two more:

```json
"typecheck": "tsc --noEmit -p tsconfig.json && tsc --noEmit -p apps/desktop/tsconfig.json",
"test:e2e": "pnpm --filter @desk/desktop build && vitest run --config vitest.e2e.config.ts",
"desktop": "pnpm --filter @desk/desktop dev"
```

- Set `pnpm.onlyBuiltDependencies` to `["electron", "esbuild"]`. Electron's postinstall downloads its binary.

Root `tsconfig.json`: add `"exclude": ["apps/desktop"]` and add `"vitest.e2e.config.ts"` to `include`.

`vitest.config.ts`: change the include pattern and exclude the end-to-end tests:

```ts
    include: roots.map((r) => `${r}/**/${live ? '*.live.test.ts' : '*.test.{ts,tsx}'}`),
    exclude: ['**/node_modules/**', '**/*.e2e.test.ts', ...(live ? [] : ['**/*.live.test.ts'])],
```

`vitest.e2e.config.ts`:

```ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['apps/desktop/e2e/**/*.e2e.test.ts'],
    testTimeout: 120_000,
    hookTimeout: 120_000,
    fileParallelism: false,
  },
});
```

`.gitignore`: add `apps/desktop/dist/`.

Run `pnpm install`. It is online, because Electron, React and Vite are new.

- [ ] **Step 4: Verify**

Run: `pnpm vitest run apps/desktop && pnpm typecheck && pnpm --filter @desk/desktop build && ls apps/desktop/dist`
Expected: PASS, and `main.cjs`, `preload.cjs` and `renderer/` exist.

- [ ] **Step 5: Commit**

```bash
git add package.json pnpm-lock.yaml tsconfig.json vitest.config.ts vitest.e2e.config.ts .gitignore apps/desktop
git commit -m "feat(desktop): Electron + React + Vite workspace scaffold

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: IPC contract (schemas, handlers, dispatch)

**Files:**
- Create:
  - `apps/desktop/src/shared/ipc.ts`, `apps/desktop/src/shared/state.ts`
  - `apps/desktop/src/main/errors.ts`, `apps/desktop/src/main/handlers.ts`, `apps/desktop/src/main/handlers.test.ts`

**Interfaces:**
- Consumes: `DeskClient` and the error classes from `@desk/client`, and the request schemas from `@desk/protocol`.
- Produces:

```ts
// shared/ipc.ts
export const channels: Record<Channel, ZodType>;   // see code
export type Channel = keyof typeof channels;
export type ChannelInput<C extends Channel> = z.input<(typeof channels)[C]>;
export type IpcError = { code: string; message: string; status?: number };
export type IpcResult<T> = { ok: true; value: T } | { ok: false; error: IpcError };
export const AppSettings: ZodObject<{ notifications: boolean }>; AppSettingsPatch
// shared/state.ts
export type ConnectionStatus = 'starting' | 'offline' | 'connecting' | 'live' | 'reconnecting' | 'mismatch';
export type GlobalState = { connection: { status: ConnectionStatus; detail?: string }; health: HealthResponse | null; overview: ProjectSummary[]; attention: AttentionItem[]; system: SystemState };
export function initialGlobalState(): GlobalState;
// main/errors.ts
export class UserFacingError extends Error { code: string }
// main/handlers.ts
export type HandlerContext = { senderId; client(); broker; daemon; app };
export const handlers: { [C in Channel]: (input, ctx) => unknown };
export type ChannelOutput<C extends Channel> = Awaited<ReturnType<(typeof handlers)[C]>>;
export function toIpcError(err: unknown, log?: (err: unknown) => void): IpcError;
export function dispatch(channel: unknown, input: unknown, ctx: HandlerContext, log?): Promise<IpcResult<unknown>>;
```

The `DaemonStatus` type is imported from `./daemon`, which Task 5 creates. This task adds a type-only stub, `apps/desktop/src/main/daemon.ts`:

```ts
export type DaemonMode = 'dev' | 'packaged';
export type DaemonStatus = {
  running: boolean;
  version: string | null;
  pid: number | null;
  uptime_s: number | null;
  proxy: 'up' | 'down' | 'unknown' | null;
  mode: DaemonMode;
  bundledVersion: string;
  agent: 'installed' | 'missing' | 'unsupported';
};
```

- [ ] **Step 1: Write the failing test** — `apps/desktop/src/main/handlers.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DaemonNotRunning, DeskClient } from '@desk/client';
import { createHarness, newRuntime, type Harness } from '@desk/core/testing';
import { createApp, startServer, type RunningServer } from '@desk/daemon';
import { channels } from '../shared/ipc';
import { initialGlobalState } from '../shared/state';
import { dispatch, handlers, type HandlerContext } from './handlers';

let h: Harness;
let server: RunningServer | undefined;
afterEach(async () => {
  await server?.close();
  server = undefined;
  await h?.cleanup();
});

async function setup(overrides: Partial<HandlerContext> = {}) {
  h = await createHarness();
  const runtime = newRuntime(h);
  server = await startServer({ app: createApp({ runtime, store: h.store, models: h.models, token: 't', version: '1.0.0' }), store: h.store, token: 't', port: 0 });
  const client = new DeskClient({ baseUrl: `http://127.0.0.1:${server.port}`, token: 't' });
  const opened: string[] = [];
  const watched: Array<[number, string, number]> = [];
  const ctx: HandlerContext = {
    senderId: 7,
    client: () => client,
    broker: { snapshot: () => initialGlobalState(), watch: async (s, p, a) => void watched.push([s, p, a]), unwatch: () => {} },
    daemon: { status: vi.fn(), start: vi.fn(), restart: vi.fn(), stop: vi.fn() },
    app: {
      info: () => ({ version: '1.0.0', platform: 'darwin', packaged: false, dataDir: '/d' }),
      openExternal: async (url) => void opened.push(url),
      pickFolder: async () => '/picked',
      revealLogs: async () => {},
      saveFile: async () => true,
      settings: () => ({ notifications: true }),
      updateSettings: (p) => ({ notifications: p.notifications ?? true }),
    },
    ...overrides,
  };
  return { ctx, runtime, opened, watched };
}

describe('IPC dispatch', () => {
  it('has a handler for every channel', () => {
    expect(Object.keys(handlers).sort()).toEqual(Object.keys(channels).sort());
  });

  it('runs validated operations against deskd', async () => {
    const { ctx } = await setup();
    const created = await dispatch('projects.create', { name: 'Launch', goal: 'g' }, ctx);
    expect(created).toMatchObject({ ok: true, value: { project: { name: 'Launch' } } });
    const list = await dispatch('projects.list', {}, ctx);
    expect(list.ok && (list.value as Array<{ name: string }>).map((p) => p.name)).toEqual(['Launch']);
    expect(await dispatch('broker.watch', { projectId: 'p1', afterSeq: 3 }, ctx)).toEqual({ ok: true, value: { ok: true } });
  });

  it('rejects unknown channels and invalid payloads', async () => {
    const { ctx } = await setup();
    expect(await dispatch('nope', {}, ctx)).toMatchObject({ ok: false, error: { code: 'unknown_channel' } });
    expect(await dispatch('projects.get', { id: 5 }, ctx)).toMatchObject({ ok: false, error: { code: 'invalid_request' } });
    expect(await dispatch('__proto__', {}, ctx)).toMatchObject({ ok: false, error: { code: 'unknown_channel' } });
  });

  it('maps daemon and runtime errors without leaking internals', async () => {
    const { ctx } = await setup();
    expect(await dispatch('projects.get', { id: 'missing' }, ctx)).toEqual({ ok: false, error: { code: 'not_found', message: 'Unknown project: missing', status: 404 } });
    const offline = await dispatch('projects.list', {}, { ...ctx, client: () => { throw new DaemonNotRunning('/secret/dir'); } });
    expect(offline).toEqual({ ok: false, error: { code: 'daemon_not_running', message: 'Desk is not running.' } });
    const log = vi.fn();
    const boom = await dispatch('app.revealLogs', {}, { ...ctx, app: { ...ctx.app, revealLogs: async () => { throw new Error('token=abc stack'); } } }, log);
    expect(boom).toEqual({ ok: false, error: { code: 'internal', message: 'Something went wrong. See the logs for details.' } });
    expect(log).toHaveBeenCalledOnce();
  });

  it('opens only web and mail links', async () => {
    const { ctx, opened } = await setup();
    expect(await dispatch('app.openExternal', { url: 'https://example.com/a?b=1' }, ctx)).toMatchObject({ ok: true });
    expect(await dispatch('app.openExternal', { url: 'mailto:a@b.c' }, ctx)).toMatchObject({ ok: true });
    for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'not a url']) {
      expect(await dispatch('app.openExternal', { url }, ctx)).toMatchObject({ ok: false, error: { code: 'invalid_url' } });
    }
    expect(opened).toEqual(['https://example.com/a?b=1', 'mailto:a@b.c']);
  });

  it('returns raw bytes and passes the sender to the broker', async () => {
    const { ctx, watched } = await setup();
    const { value } = (await dispatch('projects.create', { name: 'P' }, ctx)) as { value: { project: { id: string } } };
    const up = await dispatch('library.upload', { projectId: value.project.id, file: { name: 'a.txt', content_base64: Buffer.from('hi').toString('base64') } }, ctx);
    const path = (up as { value: { path: string } }).value.path;
    const file = await dispatch('library.file', { projectId: value.project.id, path }, ctx);
    expect(file.ok && new TextDecoder().decode(file.value as Uint8Array)).toBe('hi');
    await dispatch('broker.watch', { projectId: value.project.id, afterSeq: 0 }, ctx);
    expect(watched).toEqual([[7, value.project.id, 0]]);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop/src/main/handlers.test.ts`
Expected: FAIL: the modules are missing.

- [ ] **Step 3: Implement**

`apps/desktop/src/shared/ipc.ts`:

```ts
import { z } from 'zod';
import {
  AddSourceRequest,
  CreateProjectRequest,
  DaemonConfigPatch,
  LibraryUploadRequest,
  MemoryUpdateRequest,
  MemoryWriteRequest,
  ModelEndpointPutRequest,
  ModelsPutRequest,
  SkillWriteRequest,
  UpdateProjectRequest,
} from '@desk/protocol';

const id = z.string().min(1).max(200);
const text = z.string().min(1).max(100_000);
const relPath = z.string().min(1).max(4096);
const name = z.string().min(1).max(64);
const version = z.number().int().min(1);
const after = z.number().int().min(0).optional();
const limit = z.number().int().min(1).max(5000).optional();
const scope = { projectId: id.optional() };
const none = z.object({});

export const AppSettings = z.object({ notifications: z.boolean() });
export type AppSettings = z.infer<typeof AppSettings>;
export const AppSettingsPatch = AppSettings.partial();
export type AppSettingsPatch = z.input<typeof AppSettingsPatch>;

/** Every renderer → main operation and its payload schema. Validated in main before anything runs. */
export const channels = {
  health: none,
  overview: none,
  usage: z.object({ since: z.string().max(64).optional() }),

  'projects.list': z.object({ all: z.boolean().optional() }),
  'projects.create': CreateProjectRequest,
  'projects.get': z.object({ id }),
  'projects.update': z.object({ id, patch: UpdateProjectRequest }),
  'projects.archive': z.object({ id }),
  'projects.addSource': z.object({ id, source: AddSourceRequest }),
  'projects.removeSource': z.object({ id, sourceId: id }),
  'projects.send': z.object({ id, text }),
  'projects.chat': z.object({ id, after, limit }),
  'projects.plan': z.object({ id }),
  'projects.usage': z.object({ id }),
  'projects.events': z.object({ id, after, limit, types: z.array(z.string().max(64)).max(64).optional() }),

  'threads.list': z.object({ projectId: id, all: z.boolean().optional() }),
  'threads.get': z.object({ id }),
  'threads.transcript': z.object({ id, after, limit }),
  'threads.send': z.object({ id, text }),
  'threads.stop': z.object({ id }),
  'threads.archive': z.object({ id }),
  'threads.diff': z.object({ id }),
  'threads.files': z.object({ id, path: z.string().max(4096).optional() }),
  'threads.file': z.object({ id, path: relPath }),

  'approvals.list': z.object({ projectId: id, status: z.enum(['pending', 'approved', 'denied']).optional() }),
  'approvals.resolve': z.object({ id, decision: z.enum(['approved', 'denied']), note: z.string().max(2000).optional() }),

  'attention.list': z.object({ projectId: id.optional() }),
  'attention.dismiss': z.object({ id: z.string().min(1).max(400) }),

  'memory.list': z.object({ projectId: id, q: z.string().max(500).optional() }),
  'memory.add': z.object({ projectId: id, entry: MemoryWriteRequest }),
  'memory.correct': z.object({ projectId: id, memoryId: id, update: MemoryUpdateRequest }),
  'memory.remove': z.object({ projectId: id, memoryId: id }),

  'library.list': z.object({ projectId: id }),
  'library.upload': z.object({ projectId: id, file: LibraryUploadRequest }),
  'library.file': z.object({ projectId: id, path: relPath }),

  'skills.list': z.object(scope),
  'skills.get': z.object({ ...scope, name }),
  'skills.file': z.object({ ...scope, name, path: relPath }),
  'skills.save': z.object({ ...scope, name, skill: SkillWriteRequest }),
  'skills.remove': z.object({ ...scope, name }),
  'skills.history': z.object({ ...scope, name }),
  'skills.restore': z.object({ ...scope, name, version }),
  'skills.import': z.object({ ...scope, path: z.string().min(1).max(4096), name: name.optional() }),
  'skills.version': z.object({ ...scope, name, version }),
  'skills.versionFile': z.object({ ...scope, name, version, path: relPath }),

  'models.list': none,
  'models.replace': z.object({ models: ModelsPutRequest }),

  'config.get': none,
  'config.patch': DaemonConfigPatch,
  'config.endpoint': none,
  'config.saveEndpoint': ModelEndpointPutRequest,
  'config.testEndpoint': z.object({ candidate: ModelEndpointPutRequest.optional() }),

  'broker.snapshot': none,
  'broker.watch': z.object({ projectId: id, afterSeq: z.number().int().min(0) }),
  'broker.unwatch': z.object({ projectId: id }),

  'daemon.status': none,
  'daemon.start': none,
  'daemon.restart': none,
  'daemon.stop': none,

  'app.info': none,
  'app.openExternal': z.object({ url: z.string().min(1).max(4096) }),
  'app.pickFolder': z.object({ purpose: z.enum(['source', 'skill-import']) }),
  'app.revealLogs': none,
  'app.saveFile': z.object({ name: z.string().min(1).max(255), data: z.instanceof(Uint8Array) }),
  'app.settings': none,
  'app.updateSettings': AppSettingsPatch,
} as const;

export type Channel = keyof typeof channels;
export type ChannelInput<C extends Channel> = z.input<(typeof channels)[C]>;
export type IpcError = { code: string; message: string; status?: number };
export type IpcResult<T> = { ok: true; value: T } | { ok: false; error: IpcError };
```

`apps/desktop/src/shared/state.ts`:

```ts
import type { SystemState } from '@desk/client';
import type { AttentionItem, HealthResponse, ProjectSummary } from '@desk/protocol';

export type ConnectionStatus = 'starting' | 'offline' | 'connecting' | 'live' | 'reconnecting' | 'mismatch';

/** What every window and the tray share: pushed by the broker on `desk:global`. */
export type GlobalState = {
  connection: { status: ConnectionStatus; detail?: string };
  health: HealthResponse | null;
  overview: ProjectSummary[];
  attention: AttentionItem[];
  system: SystemState;
};

export const initialGlobalState = (): GlobalState => ({
  connection: { status: 'starting' },
  health: null,
  overview: [],
  attention: [],
  system: { proxy: 'unknown', notices: [], lastSeq: 0 },
});
```

`apps/desktop/src/main/errors.ts`:

```ts
/** An error whose message is safe and useful to show in the UI. */
export class UserFacingError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = 'UserFacingError';
  }
}
```

`apps/desktop/src/main/handlers.ts`:

```ts
import { z } from 'zod';
import { ApiError, DaemonNotRunning, DaemonUnavailable, ProtocolMismatch, type DeskClient } from '@desk/client';
import { channels, type AppSettings, type AppSettingsPatch, type Channel, type IpcError, type IpcResult } from '../shared/ipc';
import type { GlobalState } from '../shared/state';
import type { DaemonStatus } from './daemon';
import { UserFacingError } from './errors';

export type AppInfo = { version: string; platform: NodeJS.Platform; packaged: boolean; dataDir: string };

/** What a handler can reach. Built per call in main; faked in tests. */
export type HandlerContext = {
  senderId: number;
  client(): DeskClient;
  broker: {
    snapshot(): GlobalState;
    watch(senderId: number, projectId: string, afterSeq: number): Promise<void>;
    unwatch(senderId: number, projectId: string): void;
  };
  daemon: { status(): Promise<DaemonStatus>; start(): Promise<DaemonStatus>; restart(): Promise<DaemonStatus>; stop(): Promise<DaemonStatus> };
  app: {
    info(): AppInfo;
    openExternal(url: string): Promise<void>;
    pickFolder(purpose: 'source' | 'skill-import'): Promise<string | null>;
    revealLogs(): Promise<void>;
    saveFile(name: string, data: Uint8Array): Promise<boolean>;
    settings(): AppSettings;
    updateSettings(patch: AppSettingsPatch): AppSettings;
  };
};

type In<C extends Channel> = z.output<(typeof channels)[C]>;
type HandlerMap = { [C in Channel]: (input: In<C>, ctx: HandlerContext) => unknown };

const scope = (i: { projectId?: string | undefined }) => (i.projectId ? { projectId: i.projectId } : {});
const ok = { ok: true as const };

function safeExternalUrl(raw: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new UserFacingError('invalid_url', 'That is not a valid link.');
  }
  if (!['http:', 'https:', 'mailto:'].includes(url.protocol)) throw new UserFacingError('invalid_url', 'Only web and mail links can be opened.');
  return url.toString();
}

export const handlers = {
  health: (_i, c) => c.client().health(),
  overview: (_i, c) => c.client().overview(),
  usage: (i, c) => c.client().usage(i.since),

  'projects.list': (i, c) => c.client().projects.list(i.all ?? false),
  'projects.create': (i, c) => c.client().projects.create(i),
  'projects.get': (i, c) => c.client().projects.get(i.id),
  'projects.update': (i, c) => c.client().projects.update(i.id, i.patch),
  'projects.archive': (i, c) => c.client().projects.archive(i.id),
  'projects.addSource': (i, c) => c.client().projects.addSource(i.id, i.source),
  'projects.removeSource': (i, c) => c.client().projects.removeSource(i.id, i.sourceId),
  'projects.send': (i, c) => c.client().projects.send(i.id, i.text),
  'projects.chat': (i, c) => c.client().projects.chat(i.id, { ...(i.after !== undefined ? { after: i.after } : {}), ...(i.limit ? { limit: i.limit } : {}) }),
  'projects.plan': (i, c) => c.client().projects.plan(i.id),
  'projects.usage': (i, c) => c.client().projects.usage(i.id),
  'projects.events': (i, c) =>
    c.client().projects.events(i.id, {
      ...(i.after !== undefined ? { after: i.after } : {}),
      ...(i.limit ? { limit: i.limit } : {}),
      ...(i.types ? { types: i.types as never } : {}),
    }),

  'threads.list': (i, c) => c.client().threads.list(i.projectId, i.all ?? false),
  'threads.get': (i, c) => c.client().threads.get(i.id),
  'threads.transcript': (i, c) => c.client().threads.transcript(i.id, { ...(i.after !== undefined ? { after: i.after } : {}), ...(i.limit ? { limit: i.limit } : {}) }),
  'threads.send': (i, c) => c.client().threads.send(i.id, i.text),
  'threads.stop': (i, c) => c.client().threads.stop(i.id),
  'threads.archive': (i, c) => c.client().threads.archive(i.id),
  'threads.diff': (i, c) => c.client().threads.diff(i.id),
  'threads.files': (i, c) => c.client().threads.files(i.id, i.path ?? ''),
  'threads.file': (i, c) => c.client().threads.file(i.id, i.path),

  'approvals.list': (i, c) => c.client().approvals.list(i.projectId, i.status),
  'approvals.resolve': (i, c) => c.client().approvals.resolve(i.id, i.decision, i.note),

  'attention.list': (i, c) => c.client().attention.list(i.projectId),
  'attention.dismiss': (i, c) => c.client().attention.dismiss(i.id),

  'memory.list': (i, c) => c.client().memory.list(i.projectId, i.q),
  'memory.add': (i, c) => c.client().memory.add(i.projectId, i.entry),
  'memory.correct': (i, c) => c.client().memory.correct(i.projectId, i.memoryId, i.update),
  'memory.remove': (i, c) => c.client().memory.remove(i.projectId, i.memoryId),

  'library.list': (i, c) => c.client().library.list(i.projectId),
  'library.upload': (i, c) => c.client().library.upload(i.projectId, i.file),
  'library.file': (i, c) => c.client().library.file(i.projectId, i.path),

  'skills.list': (i, c) => c.client().skills.list(scope(i)),
  'skills.get': (i, c) => c.client().skills.get(scope(i), i.name),
  'skills.file': (i, c) => c.client().skills.file(scope(i), i.name, i.path),
  'skills.save': (i, c) => c.client().skills.save(scope(i), i.name, i.skill),
  'skills.remove': (i, c) => c.client().skills.remove(scope(i), i.name),
  'skills.history': (i, c) => c.client().skills.history(scope(i), i.name),
  'skills.restore': (i, c) => c.client().skills.restore(scope(i), i.name, i.version),
  'skills.import': (i, c) => c.client().skills.import(scope(i), { path: i.path, ...(i.name ? { name: i.name } : {}) }),
  'skills.version': (i, c) => c.client().skills.version(scope(i), i.name, i.version),
  'skills.versionFile': (i, c) => c.client().skills.versionFile(scope(i), i.name, i.version, i.path),

  'models.list': (_i, c) => c.client().models.list(),
  'models.replace': (i, c) => c.client().models.replace(i.models),

  'config.get': (_i, c) => c.client().config.get(),
  'config.patch': (i, c) => c.client().config.patch(i),
  'config.endpoint': (_i, c) => c.client().config.endpoint(),
  'config.saveEndpoint': (i, c) => c.client().config.saveEndpoint(i),
  'config.testEndpoint': (i, c) => c.client().config.testEndpoint(i.candidate),

  'broker.snapshot': (_i, c) => c.broker.snapshot(),
  'broker.watch': async (i, c) => {
    await c.broker.watch(c.senderId, i.projectId, i.afterSeq);
    return ok;
  },
  'broker.unwatch': (i, c) => {
    c.broker.unwatch(c.senderId, i.projectId);
    return ok;
  },

  'daemon.status': (_i, c) => c.daemon.status(),
  'daemon.start': (_i, c) => c.daemon.start(),
  'daemon.restart': (_i, c) => c.daemon.restart(),
  'daemon.stop': (_i, c) => c.daemon.stop(),

  'app.info': (_i, c) => c.app.info(),
  'app.openExternal': async (i, c) => {
    await c.app.openExternal(safeExternalUrl(i.url));
    return ok;
  },
  'app.pickFolder': (i, c) => c.app.pickFolder(i.purpose),
  'app.revealLogs': async (_i, c) => {
    await c.app.revealLogs();
    return ok;
  },
  'app.saveFile': (i, c) => c.app.saveFile(i.name, i.data),
  'app.settings': (_i, c) => c.app.settings(),
  'app.updateSettings': (i, c) => c.app.updateSettings(i),
} satisfies HandlerMap;

export type ChannelOutput<C extends Channel> = Awaited<ReturnType<(typeof handlers)[C]>>;

/** Maps any failure to what the renderer may see: daemon codes pass through, internals become a generic message. */
export function toIpcError(err: unknown, log?: (err: unknown) => void): IpcError {
  if (err instanceof ApiError) return { code: err.code, message: err.message, status: err.status };
  if (err instanceof UserFacingError) return { code: err.code, message: err.message };
  if (err instanceof DaemonNotRunning) return { code: 'daemon_not_running', message: 'Desk is not running.' };
  if (err instanceof DaemonUnavailable) return { code: 'daemon_unavailable', message: 'Cannot reach Desk.' };
  if (err instanceof ProtocolMismatch) return { code: 'protocol_mismatch', message: err.message };
  log?.(err);
  return { code: 'internal', message: 'Something went wrong. See the logs for details.' };
}

/** Validates and runs one renderer call. Never throws. */
export async function dispatch(channel: unknown, input: unknown, ctx: HandlerContext, log?: (err: unknown) => void): Promise<IpcResult<unknown>> {
  if (typeof channel !== 'string' || !Object.hasOwn(channels, channel)) {
    return { ok: false, error: { code: 'unknown_channel', message: `Unknown operation: ${String(channel).slice(0, 64)}` } };
  }
  const c = channel as Channel;
  const parsed = channels[c].safeParse(input ?? {});
  if (!parsed.success) return { ok: false, error: { code: 'invalid_request', message: z.prettifyError(parsed.error).slice(0, 500) } };
  try {
    const run = handlers[c] as (i: unknown, ctx: HandlerContext) => unknown;
    return { ok: true, value: await run(parsed.data, ctx) };
  } catch (err) {
    return { ok: false, error: toIpcError(err, log) };
  }
}
```

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop && pnpm typecheck`
Expected: PASS.

If `satisfies HandlerMap` does not contextually type a handler's `i` (TS reports an implicit `any`), annotate that handler as `(i: In<'name'>, c: HandlerContext) => …`.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop
git commit -m "feat(desktop): validated IPC contract over DeskClient with safe error mapping

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Broker (plus the client following a daemon to a new port)

**Files:**
- Create: `apps/desktop/src/main/broker.ts`, `apps/desktop/src/main/broker.test.ts`
- Modify: `packages/client/src/client.ts`, `packages/client/src/client.test.ts`

**Interfaces:**
- Consumes: `DeskClient`, `DeskStream`, `StreamOptions`, `checkDaemon`, `reduceSystem`, `systemFromHealth`, `affectsAttention` and `affectsOverview` from `@desk/client`; `GlobalState` and `PushChannel`.
- Produces:

```ts
export type BrokerDeps = {
  connect(): DeskClient | null;
  credentials(): Credentials | null;
  send(senderId: number, channel: PushChannel, payload: unknown): void;
  broadcast(channel: PushChannel, payload: unknown): void;
  hello(): { client: string; notifications: boolean };
  onChange?(state: GlobalState): void;
  onAttentionAdded?(items: AttentionItem[]): void;
  streamFactory?(o: StreamOptions): { start(): void; close(): void };
  debounceMs?: number; pollMs?: number;
  log?(message: string, err?: unknown): void;
};
export class Broker {
  constructor(d: BrokerDeps);
  start(): Promise<void>; stop(): void; reconnect(): Promise<void>; refresh(): Promise<void>; updateHello(): void;
  snapshot(): GlobalState; getClient(): DeskClient;
  watch(senderId: number, projectId: string, afterSeq: number): Promise<void>;
  unwatch(senderId: number, projectId: string): void; dropSender(senderId: number): void;
}
```

`DeskClient.request` now calls `refresh()` once after a network failure as well as after a 401. When the daemon restarts on another port, the client re-reads `daemon.json` and follows it.

- [ ] **Step 1: Write the failing tests**

Append to `describe('DeskClient', …)` in `packages/client/src/client.test.ts`:

```ts
  it('follows the daemon to a new port after refreshing credentials', async () => {
    const { baseUrl } = await setup();
    const client = new DeskClient({ baseUrl: 'http://127.0.0.1:9', token: TOKEN, refresh: async () => ({ baseUrl, token: TOKEN }) });
    expect(await client.projects.list()).toEqual([]);
    expect(client.baseUrl).toBe(baseUrl);
  });
```

`apps/desktop/src/main/broker.test.ts`:

```ts
import { afterEach, describe, expect, it } from 'vitest';
import { DeskClient, type StreamOptions } from '@desk/client';
import { createHarness, newRuntime, type Harness } from '@desk/core/testing';
import { createApp, startServer, type RunningServer } from '@desk/daemon';
import type { StoredEvent } from '@desk/protocol';
import { text } from '@desk/fake-model';
import type { GlobalState } from '../shared/state';
import { Broker, type BrokerDeps } from './broker';

let h: Harness;
let server: RunningServer | undefined;
let broker: Broker | undefined;
afterEach(async () => {
  broker?.stop();
  broker = undefined;
  await server?.close();
  server = undefined;
  await h?.cleanup();
});

const until = async (pred: () => boolean, ms = 3000) => {
  const end = Date.now() + ms;
  while (!pred()) {
    if (Date.now() > end) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 5));
  }
};

async function setup(extra: Partial<BrokerDeps> = {}) {
  h = await createHarness({ script: () => text('Hello from Desk') });
  const runtime = newRuntime(h);
  server = await startServer({ app: createApp({ runtime, store: h.store, models: h.models, token: 't', version: '1.0.0' }), store: h.store, token: 't', port: 0 });
  const creds = { baseUrl: `http://127.0.0.1:${server.port}`, token: 't' };
  const sent: Array<[number, string, unknown]> = [];
  const globals: GlobalState[] = [];
  let streamOpts: StreamOptions | undefined;
  const deps: BrokerDeps = {
    connect: () => new DeskClient(creds),
    credentials: () => creds,
    send: (id, ch, p) => void sent.push([id, ch, p]),
    broadcast: (ch, p) => void (ch === 'desk:global' && globals.push(p as GlobalState)),
    hello: () => ({ client: 'desktop', notifications: true }),
    streamFactory: (o) => ((streamOpts = o), { start: () => {}, close: () => {} }),
    debounceMs: 5,
    pollMs: 5,
    ...extra,
  };
  broker = new Broker(deps);
  return { runtime, sent, globals, deps, stream: () => streamOpts!, store: h.store };
}

describe('Broker', () => {
  it('loads the global snapshot, subscribes from its seq, and goes live on ready', async () => {
    const { runtime, globals, stream } = await setup();
    const p = runtime.createProject({ name: 'Launch', goal: 'g' });
    await broker!.start();
    const s = broker!.snapshot();
    expect(s.overview.map((o) => o.project.name)).toEqual(['Launch']);
    expect(stream().projectId).toBe('*');
    expect(stream().afterSeq).toBeGreaterThan(0);
    expect(stream().hello).toEqual({ client: 'desktop', notifications: true });
    stream().onReady?.(stream().afterSeq);
    expect(broker!.snapshot().connection.status).toBe('live');
    expect(globals.at(-1)!.connection.status).toBe('live');
    expect(p).toBeTruthy();
  });

  it('refetches attention after relevant events and reports new items once', async () => {
    const added: string[][] = [];
    const { runtime, stream, store } = await setup({ onAttentionAdded: (items) => void added.push(items.map((i) => i.kind)) });
    const p = runtime.createProject({ name: 'Launch', goal: 'g' });
    await broker!.start();
    const desk = broker!.snapshot().overview[0]!;
    expect(desk).toBeTruthy();
    const [q] = store.append({ project_id: p, agent_id: null, type: 'question.asked', payload: { question: 'Which first?' } });
    stream().onEvent(q as StoredEvent);
    await until(() => broker!.snapshot().attention.length === 1);
    expect(added).toEqual([['question']]);
    stream().onEvent({ ...(q as StoredEvent), id: q!.id + 100, type: 'usage', payload: { run_id: 'r', model: 'm', prompt_tokens: 1, completion_tokens: 1, estimated: false } } as StoredEvent);
    await new Promise((r) => setTimeout(r, 30));
    expect(added).toHaveLength(1);
  });

  it('backfills a watched project, then forwards live events once, plus deltas', async () => {
    const { runtime, sent, stream, store } = await setup();
    const p = runtime.createProject({ name: 'Launch', goal: 'g' });
    const other = runtime.createProject({ name: 'Other', goal: 'g' });
    await broker!.start();
    await broker!.watch(9, p, 0);
    const backfilled = sent.filter(([id, ch]) => id === 9 && ch === 'desk:event').map(([, , e]) => (e as StoredEvent).type);
    expect(backfilled).toEqual(['project.created', 'agent.created']);
    const [live] = store.append({ project_id: p, agent_id: null, type: 'message.user', payload: { text: 'hi' } });
    stream().onEvent(live as StoredEvent);
    stream().onEvent(live as StoredEvent);
    const [elsewhere] = store.append({ project_id: other, agent_id: null, type: 'message.user', payload: { text: 'x' } });
    stream().onEvent(elsewhere as StoredEvent);
    stream().onEphemeral?.({ type: 'assistant.delta', project_id: p, agent_id: 'd', payload: { run_id: 'r', text: 'Hel' } });
    const forwarded = sent.filter(([id]) => id === 9).map(([, ch, e]) => (ch === 'desk:event' ? (e as StoredEvent).id : ch));
    expect(forwarded).toEqual([...forwarded.slice(0, 2), live!.id, 'desk:ephemeral']);
    broker!.dropSender(9);
    const [later] = store.append({ project_id: p, agent_id: null, type: 'message.user', payload: { text: 'again' } });
    stream().onEvent(later as StoredEvent);
    expect(sent.filter(([id]) => id === 9)).toHaveLength(forwarded.length);
  });

  it('goes offline without daemon.json, polls, and reconnects when it appears', async () => {
    let available = false;
    const { runtime } = await setup();
    runtime.createProject({ name: 'Launch', goal: 'g' });
    const real = new DeskClient({ baseUrl: `http://127.0.0.1:${server!.port}`, token: 't' });
    broker!.stop();
    broker = new Broker({
      connect: () => (available ? real : null),
      credentials: () => (available ? real.credentials() : null),
      send: () => {},
      broadcast: () => {},
      hello: () => ({ client: 'desktop', notifications: false }),
      streamFactory: () => ({ start: () => {}, close: () => {} }),
      pollMs: 5,
    });
    await broker.start();
    expect(broker.snapshot().connection.status).toBe('offline');
    expect(() => broker!.getClient()).toThrow(/not running/);
    available = true;
    await until(() => broker!.snapshot().overview.length === 1);
    expect(broker.snapshot().connection.status).toBe('connecting');
  });

  it('blocks on a protocol mismatch', async () => {
    await setup();
    const bad = new DeskClient({
      baseUrl: 'http://x',
      token: 't',
      fetch: async () => new Response(JSON.stringify({ version: '9.0.0', protocol_version: 99 }), { headers: { 'content-type': 'application/json' } }),
    });
    broker!.stop();
    broker = new Broker({ connect: () => bad, credentials: () => bad.credentials(), send: () => {}, broadcast: () => {}, hello: () => ({ client: 'desktop', notifications: false }) });
    await broker.start();
    expect(broker.snapshot().connection).toMatchObject({ status: 'mismatch', detail: expect.stringContaining('protocol 99') });
  });

  it('streams real events end to end with the default DeskStream', async () => {
    const { runtime, sent } = await setup({ streamFactory: undefined });
    const p = runtime.createProject({ name: 'Launch', goal: 'g' });
    await broker!.start();
    await until(() => broker!.snapshot().connection.status === 'live');
    await broker!.watch(3, p, 0);
    runtime.sendToDesk(p, 'hello');
    await until(() => sent.some(([, ch, e]) => ch === 'desk:event' && (e as StoredEvent).type === 'assistant.message'));
    expect(sent.some(([, ch]) => ch === 'desk:ephemeral')).toBe(true);
    await runtime.shutdown();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop/src/main/broker.test.ts packages/client/src/client.test.ts`
Expected: FAIL: `./broker` is missing and the port-follow test throws `DaemonUnavailable`.

- [ ] **Step 3: Implement**

In `packages/client/src/client.ts`, replace the body of `request` up to the `raw` handling with:

```ts
    const send = async (): Promise<Response | null> => {
      try {
        return await (this.o.fetch ?? fetch)(`${this.creds.baseUrl}/v1${path}`, {
          method,
          headers: { authorization: `Bearer ${this.creds.token}`, ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
          ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
        });
      } catch {
        return null;
      }
    };
    let res = await send();
    // The daemon may have restarted with a new token or port: re-read credentials once and retry.
    if ((!res || res.status === 401) && (await this.refresh())) res = await send();
    if (!res) throw new DaemonUnavailable(this.creds.baseUrl);
```

`apps/desktop/src/main/broker.ts`:

```ts
import { affectsAttention, affectsOverview, DaemonNotRunning, DeskStream, ProtocolMismatch, reduceSystem, systemFromHealth, type Credentials, type DeskClient, type StreamOptions } from '@desk/client';
import { checkDaemon } from '@desk/client/node';
import type { AttentionItem, EphemeralEvent, StoredEvent } from '@desk/protocol';
import type { PushChannel } from '../shared/channels';
import { initialGlobalState, type GlobalState } from '../shared/state';

type StreamLike = { start(): void; close(): void };

export type BrokerDeps = {
  /** A client for the running daemon, or null when daemon.json is missing. */
  connect(): DeskClient | null;
  /** Fresh credentials from daemon.json (read on every stream reconnect), or null when the daemon is gone. */
  credentials(): Credentials | null;
  send(senderId: number, channel: PushChannel, payload: unknown): void;
  broadcast(channel: PushChannel, payload: unknown): void;
  hello(): { client: string; notifications: boolean };
  onChange?(state: GlobalState): void;
  onAttentionAdded?(items: AttentionItem[]): void;
  streamFactory?(o: StreamOptions): StreamLike;
  debounceMs?: number;
  pollMs?: number;
  log?(message: string, err?: unknown): void;
};

type Watch = { cursor: number; pending: StoredEvent[] | null };

/**
 * The app's single deskd connection: global state for the map, tray and notifications, and per-window
 * forwarding of watched projects' events (backfilled from /events so none are missed).
 */
export class Broker {
  private state: GlobalState = initialGlobalState();
  private client: DeskClient | null = null;
  private stream: StreamLike | null = null;
  private lastSeq = 0;
  private seen = new Set<string>();
  private watchers = new Map<number, Map<string, Watch>>();
  private refreshTimer: ReturnType<typeof setTimeout> | undefined;
  private pollTimer: ReturnType<typeof setTimeout> | undefined;
  private stopped = false;
  private generation = 0;

  constructor(private readonly d: BrokerDeps) {}

  snapshot(): GlobalState {
    return this.state;
  }

  getClient(): DeskClient {
    if (!this.client) throw new DaemonNotRunning('the data directory');
    return this.client;
  }

  async start(): Promise<void> {
    this.stopped = false;
    await this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.generation++;
    clearTimeout(this.refreshTimer);
    clearTimeout(this.pollTimer);
    this.stream?.close();
    this.stream = null;
  }

  /** Connects again from scratch (after starting, restarting or stopping the daemon). */
  async reconnect(): Promise<void> {
    this.stream?.close();
    this.stream = null;
    this.client = null;
    await this.connect();
  }

  /** Re-sends the hello (the notification setting changed) by reopening the stream where it left off. */
  updateHello(): void {
    if (this.client && this.stream) this.openStream(this.lastSeq);
  }

  private set(patch: Partial<GlobalState>): void {
    this.state = { ...this.state, ...patch };
    this.d.broadcast('desk:global', this.state);
    this.d.onChange?.(this.state);
  }

  private async connect(): Promise<void> {
    const gen = ++this.generation;
    clearTimeout(this.pollTimer);
    const client = this.d.connect();
    if (!client) return this.goOffline();
    this.client = client;
    this.set({ connection: { status: 'connecting' } });
    try {
      const health = await checkDaemon(client);
      const [overview, attention] = await Promise.all([client.overview(), client.attention.list()]);
      if (gen !== this.generation) return;
      this.seen = new Set(attention.items.map((i) => i.id));
      this.lastSeq = attention.seq;
      this.set({ health, overview, attention: attention.items, system: { ...systemFromHealth(health), notices: this.state.system.notices, lastSeq: attention.seq } });
      this.openStream(attention.seq);
    } catch (err) {
      if (gen !== this.generation) return;
      if (err instanceof ProtocolMismatch) {
        this.client = null;
        this.set({ connection: { status: 'mismatch', detail: err.message } });
        return;
      }
      this.d.log?.('connect failed', err);
      this.goOffline();
    }
  }

  private goOffline(): void {
    this.stream?.close();
    this.stream = null;
    this.client = null;
    if (this.state.connection.status !== 'offline') this.set({ connection: { status: 'offline' } });
    this.schedulePoll();
  }

  private schedulePoll(): void {
    clearTimeout(this.pollTimer);
    if (this.stopped) return;
    this.pollTimer = setTimeout(() => {
      if (this.d.credentials()) void this.connect();
      else this.schedulePoll();
    }, this.d.pollMs ?? 2000);
  }

  private openStream(afterSeq: number): void {
    this.stream?.close();
    let readyOnce = false;
    const factory = this.d.streamFactory ?? ((o: StreamOptions) => new DeskStream(o));
    const stream: StreamLike = factory({
      credentials: () => this.d.credentials() ?? this.client?.credentials() ?? { baseUrl: 'http://127.0.0.1:0', token: '' },
      refresh: async () => (this.client ? this.client.refresh() : false),
      projectId: '*',
      afterSeq,
      hello: this.d.hello(),
      onEvent: (e) => this.onEvent(e),
      onEphemeral: (e) => this.onEphemeral(e),
      onReady: () => {
        this.set({ connection: { status: 'live' } });
        if (readyOnce) this.scheduleRefresh(0);
        readyOnce = true;
      },
      onStatus: (s) => {
        if (s !== 'reconnecting' || this.stream !== stream) return;
        if (!this.d.credentials()) return this.goOffline();
        if (this.state.connection.status !== 'reconnecting') this.set({ connection: { status: 'reconnecting' } });
      },
      onError: (m) => this.d.log?.(`stream error: ${m}`),
    });
    this.stream = stream;
    stream.start();
  }

  private onEvent(e: StoredEvent): void {
    this.lastSeq = Math.max(this.lastSeq, e.id);
    if (e.type === 'system.notice') this.set({ system: reduceSystem(this.state.system, e) });
    if (affectsAttention(e) || affectsOverview(e)) this.scheduleRefresh();
    for (const [sender, watches] of this.watchers) {
      const w = watches.get(e.project_id);
      if (!w) continue;
      if (w.pending) w.pending.push(e);
      else this.deliver(sender, w, e);
    }
  }

  private onEphemeral(e: EphemeralEvent): void {
    for (const [sender, watches] of this.watchers) if (watches.has(e.project_id)) this.d.send(sender, 'desk:ephemeral', e);
  }

  private deliver(sender: number, w: Watch, e: StoredEvent): void {
    if (e.id <= w.cursor) return;
    w.cursor = e.id;
    this.d.send(sender, 'desk:event', e);
  }

  private scheduleRefresh(delay = this.d.debounceMs ?? 250): void {
    clearTimeout(this.refreshTimer);
    this.refreshTimer = setTimeout(() => void this.refresh(), delay);
  }

  /** Refetches overview, attention and health; reports attention items not seen before. */
  async refresh(): Promise<void> {
    const client = this.client;
    if (!client) return;
    try {
      const [overview, attention, health] = await Promise.all([client.overview(), client.attention.list(), client.health()]);
      if (client !== this.client) return;
      const added = attention.items.filter((i) => !this.seen.has(i.id));
      this.seen = new Set(attention.items.map((i) => i.id));
      this.set({ overview, attention: attention.items, health, system: health.proxy ? { ...this.state.system, proxy: health.proxy } : this.state.system });
      if (added.length) this.d.onAttentionAdded?.(added);
    } catch (err) {
      this.d.log?.('refresh failed', err);
    }
  }

  /** Starts forwarding a project's events after `afterSeq` to a window: backfill first, then live, never twice. */
  async watch(senderId: number, projectId: string, afterSeq: number): Promise<void> {
    const client = this.getClient();
    let watches = this.watchers.get(senderId);
    if (!watches) this.watchers.set(senderId, (watches = new Map()));
    const w: Watch = { cursor: afterSeq, pending: [] };
    watches.set(projectId, w);
    try {
      let after = afterSeq;
      while (after < this.lastSeq) {
        const page = await client.projects.events(projectId, { after, limit: 1000 });
        if (watches.get(projectId) !== w) return;
        for (const e of page.events) this.deliver(senderId, w, e);
        if (!page.events.length) break;
        after = page.next_after;
      }
    } finally {
      const pending = w.pending ?? [];
      w.pending = null;
      if (watches.get(projectId) === w) for (const e of pending) this.deliver(senderId, w, e);
    }
  }

  unwatch(senderId: number, projectId: string): void {
    this.watchers.get(senderId)?.delete(projectId);
  }

  dropSender(senderId: number): void {
    this.watchers.delete(senderId);
  }
}
```

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop packages/client && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop packages/client
git commit -m "feat(desktop): main-process broker with global state, debounced refetch and gap-free project forwarding

DeskClient also re-reads credentials after a network failure, so it follows a
restarted daemon to its new port.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Daemon lifecycle (`DaemonManager`, LaunchAgent)

**Files:**
- Create: `apps/desktop/src/main/launchd.ts`, `apps/desktop/src/main/daemon.test.ts`
- Modify: `apps/desktop/src/main/daemon.ts` (replaces the type stub)

**Interfaces:**
- Consumes: `readDaemonInfo` from `@desk/client/node`, `UserFacingError`.
- Produces:

```ts
export const LAUNCHD_LABEL = 'dev.desk.deskd';
export function launchdPlist(o: { programArguments: string[]; env: Record<string, string>; workingDirectory: string; logFile: string }): string;
export const plistPath: (home: string) => string;
export function compareVersions(a: string, b: string): number;
export class DaemonManager {
  constructor(o: DaemonManagerOptions);
  status(): Promise<DaemonStatus>; start(): Promise<DaemonStatus>; restart(): Promise<DaemonStatus>; stop(): Promise<DaemonStatus>;
  ensureCurrent(): Promise<boolean>; installAgent(): Promise<void>; plist(): string;
}
```

- [ ] **Step 1: Write the failing test** — `apps/desktop/src/main/daemon.test.ts`:

```ts
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { compareVersions, DaemonManager, type DaemonManagerOptions } from './daemon';
import { launchdPlist, plistPath } from './launchd';

let dir: string;
beforeEach(() => void (dir = mkdtempSync(join(tmpdir(), 'desk-dm-'))));
afterEach(() => rmSync(dir, { recursive: true, force: true }));

function manager(o: Partial<DaemonManagerOptions> = {}) {
  const calls: string[][] = [];
  const dataDir = join(dir, 'data');
  mkdirSync(dataDir, { recursive: true });
  const up = (pid = 42) => writeFileSync(join(dataDir, 'daemon.json'), JSON.stringify({ port: 1234, token: 't', pid, version: '1.0.0' }));
  const down = () => rmSync(join(dataDir, 'daemon.json'), { force: true });
  let version = '1.0.0';
  const m = new DaemonManager({
    dataDir,
    mode: 'dev',
    platform: 'darwin',
    home: join(dir, 'home'),
    uid: 501,
    bundledVersion: '1.0.0',
    execPath: '/Applications/Desk.app/Contents/MacOS/Desk',
    bundlePath: '/Applications/Desk.app/Contents/Resources/deskd/deskd.mjs',
    repoRoot: '/repo',
    nodePath: 'node',
    exec: async (file, args) => {
      calls.push([file, ...args]);
      if (args[0] === 'bootstrap' || args[0] === 'kickstart') {
        up(args[0] === 'kickstart' ? 43 : 42);
        version = '1.0.0';
      }
      if (args[0] === 'bootout') down();
      return { code: 0, stdout: '', stderr: '' };
    },
    spawnDetached: (file, args) => {
      calls.push(['spawn', file, ...args]);
      up();
    },
    kill: (pid) => {
      calls.push(['kill', String(pid)]);
      down();
    },
    fetchHealth: async () => ({ version, protocol_version: 1, proxy: 'up', uptime_s: 5 }),
    sleep: async () => {},
    startTimeoutMs: 200,
    ...o,
  });
  return { m, calls, up, down, dataDir, setVersion: (v: string) => void (version = v) };
}

describe('launchd plist', () => {
  it('runs the bundle with Electron as Node and escapes paths', () => {
    const xml = launchdPlist({ programArguments: ['/A & B/Desk', 'x.mjs'], env: { ELECTRON_RUN_AS_NODE: '1' }, workingDirectory: '/w', logFile: '/l/deskd.log' });
    expect(xml).toContain('<string>dev.desk.deskd</string>');
    expect(xml).toContain('<string>/A &amp; B/Desk</string>');
    expect(xml).toContain('<key>ELECTRON_RUN_AS_NODE</key>');
    expect(xml).toContain('<key>KeepAlive</key>');
  });
});

describe('DaemonManager', () => {
  it('reports status from daemon.json and health', async () => {
    const { m, up } = manager();
    expect(await m.status()).toMatchObject({ running: false, agent: 'unsupported', mode: 'dev' });
    up();
    expect(await m.status()).toMatchObject({ running: true, version: '1.0.0', pid: 42, proxy: 'up' });
  });

  it('starts the repo daemon through tsx in dev', async () => {
    const { m, calls } = manager();
    expect((await m.start()).running).toBe(true);
    expect(calls[0]).toEqual(['spawn', 'node', '--import', '/repo/node_modules/tsx/dist/loader.mjs', '/repo/apps/daemon/src/main.ts', '--data-dir', join(dir, 'data')]);
  });

  it('installs and bootstraps the LaunchAgent when packaged on macOS', async () => {
    const { m, calls } = manager({ mode: 'packaged' });
    expect((await m.start()).agent).toBe('installed');
    const file = plistPath(join(dir, 'home'));
    expect(readFileSync(file, 'utf8')).toContain('<string>/Applications/Desk.app/Contents/Resources/deskd/deskd.mjs</string>');
    expect(calls).toEqual([['launchctl', 'bootstrap', 'gui/501', file]]);
  });

  it('refreshes an older daemon under an installed agent, and leaves others alone', async () => {
    const { m, calls, up, setVersion } = manager({ mode: 'packaged' });
    up();
    setVersion('0.9.0');
    expect(await m.ensureCurrent()).toBe(false); // no agent installed: a daemon started by hand is left alone
    await m.installAgent(); // bootstrap brings up the bundled 1.0.0
    setVersion('0.9.0');
    calls.length = 0;
    expect(await m.ensureCurrent()).toBe(true);
    expect(calls.map((c) => c[1])).toEqual(['bootout', 'bootstrap']);
    calls.length = 0;
    expect(await m.ensureCurrent()).toBe(false);
    expect(calls).toEqual([]);
    expect(await manager().m.ensureCurrent()).toBe(false);
  });

  it('restarts with kickstart and stops with bootout', async () => {
    const { m, calls } = manager({ mode: 'packaged' });
    await m.start();
    calls.length = 0;
    expect((await m.restart()).pid).toBe(43);
    expect(calls[0]).toEqual(['launchctl', 'kickstart', '-k', 'gui/501/dev.desk.deskd']);
    expect((await m.stop()).running).toBe(false);
    expect(calls.at(-1)).toEqual(['launchctl', 'bootout', 'gui/501/dev.desk.deskd']);
  });

  it('stops a dev daemon by pid and times out when a start never comes up', async () => {
    const { m, calls, up } = manager();
    up();
    expect((await m.stop()).running).toBe(false);
    expect(calls).toEqual([['kill', '42']]);
    const stuck = manager({ spawnDetached: () => {} }).m;
    await expect(stuck.start()).rejects.toMatchObject({ code: 'daemon_start_timeout' });
  });

  it('refuses to start automatically where there is no LaunchAgent', async () => {
    const { m } = manager({ mode: 'packaged', platform: 'win32' });
    await expect(m.start()).rejects.toMatchObject({ code: 'unsupported' });
    expect(existsSync(plistPath(join(dir, 'home')))).toBe(false);
  });

  it('compares versions numerically', () => {
    expect(compareVersions('0.9.0', '1.0.0')).toBeLessThan(0);
    expect(compareVersions('1.10.0', '1.9.3')).toBeGreaterThan(0);
    expect(compareVersions('1.0', '1.0.0')).toBe(0);
  });
});
```

`ensureCurrent` only refreshes an installed agent, so a daemon the user started by hand is never replaced. The fake `exec` brings the daemon up at the bundled version whenever it bootstraps or kickstarts.

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop/src/main/daemon.test.ts`
Expected: FAIL: `DaemonManager` and `./launchd` are missing.

- [ ] **Step 3: Implement**

`apps/desktop/src/main/launchd.ts`:

```ts
import { join } from 'node:path';

/** Shared with `desk up --install`: one LaunchAgent runs deskd for the user. */
export const LAUNCHD_LABEL = 'dev.desk.deskd';

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

export const plistPath = (home: string) => join(home, 'Library', 'LaunchAgents', `${LAUNCHD_LABEL}.plist`);

export function launchdPlist(o: { programArguments: string[]; env: Record<string, string>; workingDirectory: string; logFile: string }): string {
  const args = o.programArguments.map((a) => `    <string>${esc(a)}</string>`).join('\n');
  const env = Object.entries(o.env)
    .map(([k, v]) => `    <key>${esc(k)}</key>\n    <string>${esc(v)}</string>`)
    .join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
${args}
  </array>
  <key>EnvironmentVariables</key>
  <dict>
${env}
  </dict>
  <key>WorkingDirectory</key>
  <string>${esc(o.workingDirectory)}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${esc(o.logFile)}</string>
  <key>StandardErrorPath</key>
  <string>${esc(o.logFile)}</string>
</dict>
</plist>
`;
}
```

`apps/desktop/src/main/daemon.ts`, which replaces the stub:

```ts
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { readDaemonInfo } from '@desk/client/node';
import type { HealthResponse } from '@desk/protocol';
import { UserFacingError } from './errors';
import { LAUNCHD_LABEL, launchdPlist, plistPath } from './launchd';

export type DaemonMode = 'dev' | 'packaged';
export type ExecResult = { code: number; stdout: string; stderr: string };

export type DaemonStatus = {
  running: boolean;
  version: string | null;
  pid: number | null;
  uptime_s: number | null;
  proxy: 'up' | 'down' | 'unknown' | null;
  mode: DaemonMode;
  bundledVersion: string;
  agent: 'installed' | 'missing' | 'unsupported';
};

export type DaemonManagerOptions = {
  dataDir: string;
  mode: DaemonMode;
  platform: NodeJS.Platform;
  home: string;
  uid: number;
  bundledVersion: string;
  /** Packaged: the app's own binary (run with ELECTRON_RUN_AS_NODE) and the bundled deskd.mjs. */
  execPath: string;
  bundlePath: string;
  /** Dev: the repo root and a Node binary; the TypeScript daemon runs through tsx. */
  repoRoot: string;
  nodePath: string;
  exec(file: string, args: string[]): Promise<ExecResult>;
  spawnDetached(file: string, args: string[], opts: { cwd: string; env: Record<string, string>; logFile: string }): void;
  kill(pid: number): void;
  fetchHealth?(port: number): Promise<HealthResponse>;
  sleep?(ms: number): Promise<void>;
  startTimeoutMs?: number;
};

export function compareVersions(a: string, b: string): number {
  const pa = a.split('.').map((n) => Number.parseInt(n, 10) || 0);
  const pb = b.split('.').map((n) => Number.parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d) return d;
  }
  return 0;
}

async function defaultFetchHealth(port: number): Promise<HealthResponse> {
  const res = await fetch(`http://127.0.0.1:${port}/v1/health`, { signal: AbortSignal.timeout(1500) });
  if (!res.ok) throw new Error(`health ${res.status}`);
  return (await res.json()) as HealthResponse;
}

/** Finds, starts, restarts and stops deskd. Quitting the app never stops it. */
export class DaemonManager {
  constructor(private readonly o: DaemonManagerOptions) {}

  private get launchd(): boolean {
    return this.o.mode === 'packaged' && this.o.platform === 'darwin';
  }

  private logsDir(): string {
    return join(this.o.dataDir, 'logs');
  }

  private sleep(ms: number): Promise<void> {
    return this.o.sleep ? this.o.sleep(ms) : new Promise((r) => setTimeout(r, ms));
  }

  private async probe(): Promise<{ health: HealthResponse; pid: number } | null> {
    const info = readDaemonInfo(this.o.dataDir);
    if (!info) return null;
    try {
      return { health: await (this.o.fetchHealth ?? defaultFetchHealth)(info.port), pid: info.pid };
    } catch {
      return null;
    }
  }

  async status(): Promise<DaemonStatus> {
    const p = await this.probe();
    return {
      running: !!p,
      version: p?.health.version ?? null,
      pid: p?.pid ?? null,
      uptime_s: p?.health.uptime_s ?? null,
      proxy: p?.health.proxy ?? null,
      mode: this.o.mode,
      bundledVersion: this.o.bundledVersion,
      agent: this.launchd ? (existsSync(plistPath(this.o.home)) ? 'installed' : 'missing') : 'unsupported',
    };
  }

  /** The LaunchAgent that runs the bundled daemon with the app's own binary. */
  plist(): string {
    return launchdPlist({
      programArguments: [this.o.execPath, this.o.bundlePath, '--data-dir', this.o.dataDir],
      env: { ELECTRON_RUN_AS_NODE: '1', DESK_BUNDLED: '1' },
      workingDirectory: dirname(this.o.bundlePath),
      logFile: join(this.logsDir(), 'deskd.launchd.log'),
    });
  }

  async start(): Promise<DaemonStatus> {
    const current = await this.status();
    if (current.running) return current;
    mkdirSync(this.logsDir(), { recursive: true });
    if (this.launchd) await this.installAgent();
    else if (this.o.mode === 'dev') this.spawnDev();
    else throw new UserFacingError('unsupported', 'Starting Desk automatically is not supported on this platform yet. Start deskd yourself, then retry.');
    return this.waitHealthy(null);
  }

  async restart(): Promise<DaemonStatus> {
    if (this.launchd) {
      if (!existsSync(plistPath(this.o.home))) return this.start();
      const before = await this.status();
      await this.launchctl(['kickstart', '-k', `${this.domain()}/${LAUNCHD_LABEL}`]);
      return this.waitHealthy(before.pid);
    }
    await this.stop();
    return this.start();
  }

  async stop(): Promise<DaemonStatus> {
    if (this.launchd && existsSync(plistPath(this.o.home))) {
      await this.launchctl(['bootout', `${this.domain()}/${LAUNCHD_LABEL}`], true);
    } else {
      const info = readDaemonInfo(this.o.dataDir);
      if (info) {
        try {
          this.o.kill(info.pid);
        } catch {
          // Already gone.
        }
      }
    }
    for (let i = 0; i < 40 && readDaemonInfo(this.o.dataDir); i++) await this.sleep(250);
    return this.status();
  }

  /** Packaged builds: when the installed agent runs an older daemon than the bundled one, reinstall it. */
  async ensureCurrent(): Promise<boolean> {
    if (!this.launchd || !existsSync(plistPath(this.o.home))) return false;
    const s = await this.status();
    if (!s.running || !s.version || compareVersions(s.version, this.o.bundledVersion) >= 0) return false;
    await this.installAgent();
    await this.waitHealthy(null, (v) => compareVersions(v, this.o.bundledVersion) >= 0);
    return true;
  }

  async installAgent(): Promise<void> {
    const file = plistPath(this.o.home);
    mkdirSync(dirname(file), { recursive: true });
    mkdirSync(this.logsDir(), { recursive: true });
    if (existsSync(file)) await this.launchctl(['bootout', `${this.domain()}/${LAUNCHD_LABEL}`], true);
    writeFileSync(file, this.plist());
    // launchd can refuse a bootstrap right after a bootout while the old job is still tearing down.
    for (let attempt = 1; ; attempt++) {
      try {
        await this.launchctl(['bootstrap', this.domain(), file]);
        return;
      } catch (err) {
        if (attempt >= 3) throw err;
        await this.sleep(500);
      }
    }
  }

  private spawnDev(): void {
    const loader = join(this.o.repoRoot, 'node_modules', 'tsx', 'dist', 'loader.mjs');
    const entry = join(this.o.repoRoot, 'apps', 'daemon', 'src', 'main.ts');
    this.o.spawnDetached(this.o.nodePath, ['--import', loader, entry, '--data-dir', this.o.dataDir], {
      cwd: this.o.repoRoot,
      env: {},
      logFile: join(this.logsDir(), 'deskd.out.log'),
    });
  }

  private domain(): string {
    return `gui/${this.o.uid}`;
  }

  private async launchctl(args: string[], ignoreFailure = false): Promise<void> {
    const r = await this.o.exec('launchctl', args);
    if (r.code !== 0 && !ignoreFailure) throw new UserFacingError('launchd_failed', `launchctl ${args[0]} failed: ${r.stderr.trim() || `exit ${r.code}`}`);
  }

  /** Waits for a healthy daemon (a new pid when restarting, a new enough version when refreshing). */
  private async waitHealthy(previousPid: number | null, versionOk: (v: string) => boolean = () => true): Promise<DaemonStatus> {
    const deadline = Date.now() + (this.o.startTimeoutMs ?? 10_000);
    for (;;) {
      const p = await this.probe();
      if (p && p.pid !== previousPid && versionOk(p.health.version)) return this.status();
      if (Date.now() > deadline) throw new UserFacingError('daemon_start_timeout', 'Desk did not start within 10 seconds. Check the logs, then retry.');
      await this.sleep(250);
    }
  }
}
```

With `sleep` stubbed to resolve immediately, the timeout loop spins until `startTimeoutMs` (200 ms in the test) passes, which is fine.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop
git commit -m "feat(desktop): daemon manager — dev spawn, LaunchAgent install/kickstart/bootout, version refresh

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: App settings, notification text, tray model and icon

**Files:**
- Create:
  - `apps/desktop/src/shared/attention.ts`
  - `apps/desktop/src/main/settings.ts`, `apps/desktop/src/main/notify.ts`, `apps/desktop/src/main/trayModel.ts`, `apps/desktop/src/main/trayIcon.ts`
  - `apps/desktop/src/main/shell.test.ts`

**Interfaces:**
- Produces:

```ts
// shared/attention.ts
export const STRIP_CODE: Record<AttentionKind, 'APR' | 'ASK' | 'DOC' | 'HLD' | 'FLD'>;
// settings.ts
export class AppSettingsStore { constructor(file: string); get(): AppSettings; update(p: AppSettingsPatch): AppSettings }
// notify.ts
export function notificationFor(item: AttentionItem): { title: string; body: string };
export function attentionRoute(item: AttentionItem): string; // '#/attention?item=…'
// trayModel.ts
export type TrayItem = { label: string; route?: string; action?: 'open' | 'quit'; enabled?: boolean } | { separator: true };
export function trayModel(s: GlobalState): { title: string; tooltip: string; items: TrayItem[] };
// trayIcon.ts
export function trayIconBitmap(size: number): Buffer; // BGRA, template (black + alpha)
```

- [ ] **Step 1: Write the failing test** — `apps/desktop/src/main/shell.test.ts`:

```ts
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { AttentionItem, ProjectSummary } from '@desk/protocol';
import { initialGlobalState } from '../shared/state';
import { attentionRoute, notificationFor } from './notify';
import { AppSettingsStore } from './settings';
import { trayIconBitmap } from './trayIcon';
import { trayModel } from './trayModel';

const item = (kind: AttentionItem['kind'], id: string, title = `${kind} title`): AttentionItem => ({
  id,
  kind,
  project_id: 'p',
  project_name: 'Onboarding',
  agent_id: null,
  title,
  detail: '',
  created_at: '2026-09-24T10:00:00.000Z',
  ref: {},
});

describe('app settings', () => {
  it('defaults, persists and survives a corrupt file', () => {
    const dir = mkdtempSync(join(tmpdir(), 'desk-settings-'));
    try {
      const file = join(dir, 'settings.json');
      expect(new AppSettingsStore(file).get()).toEqual({ notifications: true });
      new AppSettingsStore(file).update({ notifications: false });
      expect(new AppSettingsStore(file).get()).toEqual({ notifications: false });
      expect(new AppSettingsStore(file).update({})).toEqual({ notifications: false });
      writeFileSync(file, '{nope');
      expect(new AppSettingsStore(file).get()).toEqual({ notifications: true });
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe('notifications', () => {
  it('words each kind and routes to the attention item', () => {
    expect(notificationFor(item('approval', 'approval:a1', 'Signup checklist wants to run bash'))).toEqual({ title: 'Onboarding: approval needed', body: 'Signup checklist wants to run bash' });
    expect(notificationFor(item('question', 'q')).title).toBe('Onboarding: Desk has a question');
    expect(notificationFor(item('failed', 'f')).title).toBe('Onboarding: a thread failed');
    expect(notificationFor(item('question', 'q', 'x'.repeat(500))).body.length).toBeLessThanOrEqual(200);
    expect(attentionRoute(item('approval', 'approval:a/1'))).toBe('#/attention?item=approval%3Aa%2F1');
  });
});

describe('tray model', () => {
  it('shows the count, the first items, what is running, and the daemon state', () => {
    const running: ProjectSummary = {
      project: { id: 'p', name: 'Onboarding', goal: '', updated_at: '' },
      desk_status: 'idle',
      threads: [
        { id: 't1', title: 'A', status: 'running', reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: '', updated_at: '' },
        { id: 't2', title: 'B', status: 'running', reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: '', updated_at: '' },
      ],
      latest_report: null,
      plan_progress: { done: 0, total: 0 },
      attention_count: 0,
    };
    const items = Array.from({ length: 7 }, (_, i) => item(i === 0 ? 'approval' : 'needs_you', `i${i}`));
    const m = trayModel({ ...initialGlobalState(), connection: { status: 'live' }, attention: items, overview: [running], system: { proxy: 'up', notices: [], lastSeq: 0 } });
    expect(m.title).toBe('7');
    expect(m.tooltip).toBe('Desk: 7 need you');
    const labels = m.items.map((i) => ('separator' in i ? '—' : i.label));
    expect(labels[0]).toBe('7 need you');
    expect(labels[1]).toMatch(/^APR/);
    expect(labels).toContain('and 2 more…');
    expect(labels).toContain('2 threads running');
    expect(labels).toContain('deskd running · proxy up');
    expect(labels.slice(-2)).toEqual(['Open Desk', 'Quit Desk']);
    const offline = trayModel({ ...initialGlobalState(), connection: { status: 'offline' } });
    expect([offline.title, offline.tooltip]).toEqual(['', 'Desk: deskd is not running']);
    expect(offline.items.map((i) => ('separator' in i ? '—' : i.label))).toContain('deskd is not running');
  });
});

describe('tray icon', () => {
  it('draws an antialiased ring with a centre dot in template colours', () => {
    const size = 32;
    const px = trayIconBitmap(size);
    expect(px.length).toBe(size * size * 4);
    const alpha = (x: number, y: number) => px[(y * size + x) * 4 + 3]!;
    expect(alpha(16, 16)).toBe(255);
    expect(alpha(0, 0)).toBe(0);
    expect(alpha(16, 16 - Math.round((5.5 * size) / 14))).toBeGreaterThan(128);
    expect(px[(16 * size + 16) * 4]).toBe(0);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop/src/main/shell.test.ts`
Expected: FAIL: the modules are missing.

- [ ] **Step 3: Implement**

`apps/desktop/src/shared/attention.ts`:

```ts
import type { AttentionKind } from '@desk/protocol';

/** Flight-strip end-cap codes (design C): clearance, query, document hand-off, holding, failed. */
export const STRIP_CODE: Record<AttentionKind, 'APR' | 'ASK' | 'DOC' | 'HLD' | 'FLD'> = {
  approval: 'APR',
  question: 'ASK',
  needs_you: 'DOC',
  stalled: 'HLD',
  failed: 'FLD',
};
```

`apps/desktop/src/main/settings.ts`:

```ts
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { AppSettings, type AppSettingsPatch } from '../shared/ipc';

const DEFAULTS: AppSettings = { notifications: true };

function load(file: string): AppSettings {
  try {
    return AppSettings.parse({ ...DEFAULTS, ...(JSON.parse(readFileSync(file, 'utf8')) as object) });
  } catch {
    return { ...DEFAULTS };
  }
}

/** The app's own preferences (not the daemon's), in `userData/settings.json`. */
export class AppSettingsStore {
  private value: AppSettings;

  constructor(private readonly file: string) {
    this.value = load(file);
  }

  get(): AppSettings {
    return { ...this.value };
  }

  update(patch: AppSettingsPatch): AppSettings {
    const defined = Object.fromEntries(Object.entries(patch).filter(([, v]) => v !== undefined));
    this.value = AppSettings.parse({ ...this.value, ...defined });
    mkdirSync(dirname(this.file), { recursive: true });
    writeFileSync(this.file, `${JSON.stringify(this.value, null, 2)}\n`);
    return this.get();
  }
}
```

`apps/desktop/src/main/notify.ts`:

```ts
import { clip, type AttentionItem } from '@desk/protocol';

const HEADLINE: Record<AttentionItem['kind'], string> = {
  approval: 'approval needed',
  question: 'Desk has a question',
  needs_you: 'needs you',
  stalled: 'a thread stalled',
  failed: 'a thread failed',
};

/** The system notification for a new attention item (agent text is clipped, never rendered as markup). */
export function notificationFor(item: AttentionItem): { title: string; body: string } {
  return { title: clip(`${item.project_name}: ${HEADLINE[item.kind]}`, 80), body: clip(item.title, 200) };
}

export const attentionRoute = (item: AttentionItem): string => `#/attention?item=${encodeURIComponent(item.id)}`;
```

`apps/desktop/src/main/trayModel.ts`:

```ts
import { clip } from '@desk/protocol';
import { STRIP_CODE } from '../shared/attention';
import type { GlobalState } from '../shared/state';
import { attentionRoute } from './notify';

export type TrayItem = { label: string; route?: string; action?: 'open' | 'quit'; enabled?: boolean } | { separator: true };

const MAX_ITEMS = 5;

function daemonLine(s: GlobalState): string {
  switch (s.connection.status) {
    case 'offline':
      return 'deskd is not running';
    case 'mismatch':
      return 'deskd needs an update';
    case 'reconnecting':
      return 'Reconnecting to deskd…';
    case 'live':
      return `deskd running · proxy ${s.system.proxy}`;
    default:
      return 'Connecting to deskd…';
  }
}

/** The menu-bar item: a count title and a menu of what needs you and what is running. */
export function trayModel(s: GlobalState): { title: string; tooltip: string; items: TrayItem[] } {
  const count = s.attention.length;
  const running = s.overview.reduce((n, p) => n + p.threads.filter((t) => t.status === 'running').length, 0);
  const offline = s.connection.status === 'offline' || s.connection.status === 'mismatch';
  const items: TrayItem[] = [
    { label: count ? `${count} need you` : 'Nothing needs you', enabled: false },
    ...s.attention.slice(0, MAX_ITEMS).map((i) => ({ label: clip(`${STRIP_CODE[i.kind]}  ${i.project_name} · ${i.title}`, 64), route: attentionRoute(i) })),
    ...(count > MAX_ITEMS ? [{ label: `and ${count - MAX_ITEMS} more…`, route: '#/attention' }] : []),
    { separator: true },
    { label: running ? `${running} thread${running === 1 ? '' : 's'} running` : 'No threads running', enabled: false },
    { label: daemonLine(s), enabled: false },
    { separator: true },
    { label: 'Open Desk', action: 'open' },
    { label: 'Quit Desk', action: 'quit' },
  ];
  return {
    title: count ? String(count) : '',
    tooltip: offline ? `Desk: ${daemonLine(s)}` : count ? `Desk: ${count} need you` : 'Desk',
    items,
  };
}
```

`apps/desktop/src/main/trayIcon.ts`:

```ts
/**
 * The menu-bar glyph from design C (a ring with a centre dot, drawn on a 14-unit grid) as a BGRA
 * template bitmap: black with coverage in alpha, so macOS tints it for light and dark menu bars.
 */
export function trayIconBitmap(size: number): Buffer {
  const buf = Buffer.alloc(size * size * 4);
  const unit = size / 14;
  const c = size / 2;
  const clamp = (v: number) => Math.max(0, Math.min(1, v));
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const d = Math.hypot(x + 0.5 - c, y + 0.5 - c) / unit;
      const ring = clamp((0.75 - Math.abs(d - 5.5)) * unit + 0.5);
      const dot = clamp((2 - d) * unit + 0.5);
      buf[(y * size + x) * 4 + 3] = Math.round(Math.max(ring, dot) * 255);
    }
  }
  return buf;
}
```

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop
git commit -m "feat(desktop): app settings, notification text, tray model and template icon

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Electron wiring (windows, protocol and CSP, menu, tray, preload, lifecycle)

**Files:**
- Create: `apps/desktop/src/main/windows.ts`, `apps/desktop/src/main/menu.ts`, `apps/desktop/src/main/tray.ts`, `apps/desktop/src/main/log.ts`
- Replace: `apps/desktop/src/main/index.ts`, `apps/desktop/src/preload/index.ts`

**Interfaces:**
- Consumes: everything in `main/` from Tasks 3–6.
- Produces:
  - `window.desk = { invoke(channel, input): Promise<IpcResult>, on(channel, cb): () => void, platform }`.
  - Renderer URLs: `desk-app://ui/index.html#/…`, or `DESK_RENDERER_URL` in dev.
  - E2E hooks when `DESK_E2E=1`: `globalThis.__deskTest = { trayTitle(), notifications }`.
  - `DESK_USER_DATA` overrides `userData`.

These modules only run inside Electron. They are covered by `pnpm typecheck`, by `pnpm --filter @desk/desktop build`, and by the Playwright test in Task 10.

- [ ] **Step 1: Implement**

`apps/desktop/src/main/log.ts`:

```ts
import { appendFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

/** Appends timestamped lines to the app log. Never throws. */
export function createLog(file: string): (message: string, err?: unknown) => void {
  try {
    mkdirSync(dirname(file), { recursive: true });
  } catch {
    // Logging must not break the app.
  }
  return (message, err) => {
    const detail = err === undefined ? '' : ` ${err instanceof Error ? (err.stack ?? err.message) : String(err)}`;
    try {
      appendFileSync(file, `${new Date().toISOString()} ${message}${detail}\n`);
    } catch {
      // Ignore.
    }
  };
}
```

`apps/desktop/src/main/windows.ts`:

```ts
import { readFile } from 'node:fs/promises';
import { extname, join, normalize, sep } from 'node:path';
import { BrowserWindow, protocol, session } from 'electron';

export const APP_SCHEME = 'desk-app';
export const APP_ORIGIN = `${APP_SCHEME}://ui`;

const PROD_CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "frame-src 'none'",
  "form-action 'none'",
].join('; ');

/** Vite's dev server needs inline scripts (React refresh) and its HMR socket. */
const devCsp = (origin: string) =>
  [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self' data:",
    `connect-src 'self' ${origin.replace(/^http/, 'ws')}`,
    "object-src 'none'",
  ].join('; ');

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.map': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
};

/** Must run before `app.ready`. */
export function registerAppScheme(): void {
  protocol.registerSchemesAsPrivileged([{ scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
}

/** Serves the built renderer from `dir` on desk-app://ui with the production CSP. */
export function serveRenderer(dir: string): void {
  const root = normalize(dir);
  protocol.handle(APP_SCHEME, async (req) => {
    const rel = decodeURIComponent(new URL(req.url).pathname).replace(/^\/+/, '') || 'index.html';
    const file = normalize(join(root, rel));
    if (!file.startsWith(root + sep)) return new Response('Not found', { status: 404 });
    try {
      const body = await readFile(file);
      return new Response(new Uint8Array(body), {
        headers: { 'content-type': MIME[extname(file)] ?? 'application/octet-stream', 'content-security-policy': PROD_CSP },
      });
    } catch {
      return new Response('Not found', { status: 404 });
    }
  });
}

export function rendererUrl(): string {
  return process.env.DESK_RENDERER_URL ?? `${APP_ORIGIN}/index.html`;
}

/** Whether a URL belongs to the app's own renderer (and may talk to main or be navigated to). */
export function isAppUrl(url: string): boolean {
  if (url.startsWith(`${APP_ORIGIN}/`)) return true;
  const dev = process.env.DESK_RENDERER_URL;
  return !!dev && url.startsWith(`${new URL(dev).origin}/`);
}

/** Denies every permission except writing to the clipboard; applies the dev CSP when on Vite. */
export function hardenSession(): void {
  const s = session.defaultSession;
  s.setPermissionRequestHandler((_wc, permission, cb) => cb(permission === 'clipboard-sanitized-write'));
  const dev = process.env.DESK_RENDERER_URL;
  if (dev) {
    const origin = new URL(dev).origin;
    s.webRequest.onHeadersReceived({ urls: [`${origin}/*`] }, (details, cb) =>
      cb({ responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': [devCsp(origin)] } }),
    );
  }
}

export function createMainWindow(o: { preload: string; route?: string }): BrowserWindow {
  const mac = process.platform === 'darwin';
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    show: false,
    title: 'Desk',
    backgroundColor: '#EFEAE0',
    ...(mac ? { titleBarStyle: 'hiddenInset' as const, trafficLightPosition: { x: 16, y: 16 } } : {}),
    webPreferences: { preload: o.preload, contextIsolation: true, sandbox: true, nodeIntegration: false, webSecurity: true, spellcheck: true },
  });
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', (e, url) => {
    if (!isAppUrl(url)) e.preventDefault();
  });
  win.once('ready-to-show', () => win.show());
  void win.loadURL(rendererUrl() + (o.route ?? ''));
  return win;
}
```

`apps/desktop/src/main/menu.ts`:

```ts
import { Menu, type MenuItemConstructorOptions } from 'electron';

export function buildAppMenu(o: { navigate(route: string): void; dev: boolean }): Menu {
  const mac = process.platform === 'darwin';
  const go = (label: string, accelerator: string, route: string): MenuItemConstructorOptions => ({ label, accelerator, click: () => o.navigate(route) });
  const template: MenuItemConstructorOptions[] = [
    ...(mac ? [{ role: 'appMenu' } as MenuItemConstructorOptions] : []),
    { label: 'File', submenu: [go('New Project…', 'CmdOrCtrl+N', '#/map?new=1'), { type: 'separator' }, mac ? { role: 'close' } : { role: 'quit' }] },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [...(o.dev ? ([{ role: 'reload' }, { role: 'toggleDevTools' }, { type: 'separator' }] as MenuItemConstructorOptions[]) : []), { role: 'togglefullscreen' }],
    },
    { label: 'Go', submenu: [go('Map', 'CmdOrCtrl+1', '#/map'), go('Attention', 'CmdOrCtrl+2', '#/attention'), go('Skills', 'CmdOrCtrl+3', '#/skills'), go('System', 'CmdOrCtrl+4', '#/system')] },
    { role: 'windowMenu' },
  ];
  return Menu.buildFromTemplate(template);
}
```

`apps/desktop/src/main/tray.ts`:

```ts
import { Menu, nativeImage, Tray } from 'electron';
import type { GlobalState } from '../shared/state';
import { trayIconBitmap } from './trayIcon';
import { trayModel } from './trayModel';

/** The menu-bar item. The app keeps running here after its last window closes. */
export class DeskTray {
  private readonly tray: Tray;
  title = '';

  constructor(private readonly o: { open(route?: string): void; quit(): void }) {
    const icon = nativeImage.createFromBitmap(trayIconBitmap(32), { width: 32, height: 32, scaleFactor: 2 });
    icon.setTemplateImage(true);
    this.tray = new Tray(icon);
  }

  update(state: GlobalState): void {
    const m = trayModel(state);
    this.title = m.title;
    if (process.platform === 'darwin') this.tray.setTitle(m.title ? ` ${m.title}` : '');
    this.tray.setToolTip(m.tooltip);
    this.tray.setContextMenu(
      Menu.buildFromTemplate(
        m.items.map((item) =>
          'separator' in item
            ? { type: 'separator' as const }
            : { label: item.label, enabled: item.enabled ?? true, click: () => (item.action === 'quit' ? this.o.quit() : this.o.open(item.route)) },
        ),
      ),
    );
  }
}
```

`apps/desktop/src/preload/index.ts`:

```ts
import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron';
import { INVOKE_CHANNEL, PUSH_CHANNELS } from '../shared/channels';

const push = new Set<string>(PUSH_CHANNELS);

/** The only surface the renderer gets: validated calls into main and a few push channels. */
const bridge = {
  invoke: (channel: string, input: unknown) => ipcRenderer.invoke(INVOKE_CHANNEL, channel, input),
  on: (channel: string, cb: (payload: unknown) => void) => {
    if (!push.has(channel)) throw new Error(`Unknown channel: ${channel}`);
    const listener = (_e: IpcRendererEvent, payload: unknown) => cb(payload);
    ipcRenderer.on(channel, listener);
    return () => {
      ipcRenderer.removeListener(channel, listener);
    };
  },
  platform: process.platform,
};

contextBridge.exposeInMainWorld('desk', bridge);
```

`apps/desktop/src/main/index.ts`:

```ts
import { execFile, spawn } from 'node:child_process';
import { mkdirSync, openSync } from 'node:fs';
import { writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { basename, join, resolve } from 'node:path';
import { app, BrowserWindow, dialog, ipcMain, Menu, Notification, shell, webContents } from 'electron';
import { clientFromDataDir, defaultDataDir, readDaemonInfo } from '@desk/client/node';
import type { AttentionItem } from '@desk/protocol';
import { INVOKE_CHANNEL } from '../shared/channels';
import { Broker } from './broker';
import { DaemonManager } from './daemon';
import { UserFacingError } from './errors';
import { dispatch, type HandlerContext } from './handlers';
import { createLog } from './log';
import { buildAppMenu } from './menu';
import { attentionRoute, notificationFor } from './notify';
import { AppSettingsStore } from './settings';
import { DeskTray } from './tray';
import { createMainWindow, hardenSession, isAppUrl, registerAppScheme, serveRenderer } from './windows';

const e2e = process.env.DESK_E2E === '1';
if (process.env.DESK_USER_DATA) app.setPath('userData', process.env.DESK_USER_DATA);
registerAppScheme();

const testHooks = { trayTitle: () => '', notifications: [] as Array<{ title: string; body: string }> };
if (e2e) (globalThis as { __deskTest?: typeof testHooks }).__deskTest = testHooks;

async function start(): Promise<void> {
  const dataDir = defaultDataDir();
  const log = createLog(join(app.getPath('logs'), 'desktop.log'));
  const settings = new AppSettingsStore(join(app.getPath('userData'), 'settings.json'));
  const preload = join(__dirname, 'preload.cjs');

  const daemon = new DaemonManager({
    dataDir,
    mode: app.isPackaged ? 'packaged' : 'dev',
    platform: process.platform,
    home: homedir(),
    uid: process.getuid?.() ?? 0,
    bundledVersion: app.getVersion(),
    execPath: process.execPath,
    bundlePath: join(process.resourcesPath, 'deskd', 'deskd.mjs'),
    repoRoot: resolve(__dirname, '..', '..', '..'),
    nodePath: process.env.DESK_NODE ?? 'node',
    exec: (file, args) =>
      new Promise((done) =>
        execFile(file, args, (err, stdout, stderr) => done({ code: err ? (typeof err.code === 'number' ? err.code : 1) : 0, stdout: String(stdout), stderr: String(stderr) })),
      ),
    spawnDetached: (file, args, o) => {
      const fd = openSync(o.logFile, 'a');
      const env: NodeJS.ProcessEnv = { ...process.env, ...o.env };
      delete env.ELECTRON_RUN_AS_NODE;
      spawn(file, args, { cwd: o.cwd, env, detached: true, stdio: ['ignore', fd, fd] }).unref();
    },
    kill: (pid) => process.kill(pid, 'SIGTERM'),
  });

  let tray: DeskTray | null = null;
  const openRoute = (route?: string) => {
    const win = BrowserWindow.getAllWindows()[0];
    if (!win) {
      createMainWindow({ preload, ...(route ? { route } : {}) });
      return;
    }
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
    if (route) win.webContents.send('desk:navigate', route);
  };

  const notify = (items: AttentionItem[]) => {
    if (!settings.get().notifications || BrowserWindow.getFocusedWindow()) return;
    for (const item of items.slice(0, 3)) {
      const n = notificationFor(item);
      if (e2e) {
        testHooks.notifications.push(n);
        continue;
      }
      if (!Notification.isSupported()) continue;
      const note = new Notification({ title: n.title, body: n.body });
      note.on('click', () => openRoute(attentionRoute(item)));
      note.show();
    }
  };

  const broker = new Broker({
    connect: () => (readDaemonInfo(dataDir) ? clientFromDataDir(dataDir) : null),
    credentials: () => {
      const info = readDaemonInfo(dataDir);
      return info ? { baseUrl: `http://127.0.0.1:${info.port}`, token: info.token } : null;
    },
    send: (id, channel, payload) => {
      const wc = webContents.fromId(id);
      if (wc && !wc.isDestroyed()) wc.send(channel, payload);
    },
    broadcast: (channel, payload) => {
      for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed()) w.webContents.send(channel, payload);
    },
    hello: () => ({ client: 'desktop', notifications: settings.get().notifications }),
    onChange: (state) => {
      tray?.update(state);
      if (process.platform === 'darwin') app.dock?.setBadge(state.attention.length ? String(state.attention.length) : '');
    },
    onAttentionAdded: notify,
    log,
  });

  const ctx = (senderId: number): HandlerContext => ({
    senderId,
    client: () => broker.getClient(),
    broker,
    daemon: {
      status: () => daemon.status(),
      start: async () => {
        const s = await daemon.start();
        await broker.reconnect();
        return s;
      },
      restart: async () => {
        const s = await daemon.restart();
        await broker.reconnect();
        return s;
      },
      stop: async () => {
        const s = await daemon.stop();
        await broker.reconnect();
        return s;
      },
    },
    app: {
      info: () => ({ version: app.getVersion(), platform: process.platform, packaged: app.isPackaged, dataDir }),
      openExternal: (url) => shell.openExternal(url),
      pickFolder: async (purpose) => {
        const opts: Electron.OpenDialogOptions = {
          properties: ['openDirectory', 'createDirectory'],
          ...(purpose === 'skill-import' ? { title: 'Import a skill folder', defaultPath: join(homedir(), '.claude', 'skills') } : { title: 'Add a source folder' }),
        };
        const win = BrowserWindow.getFocusedWindow();
        const r = win ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts);
        return r.canceled ? null : (r.filePaths[0] ?? null);
      },
      revealLogs: async () => {
        const dir = join(dataDir, 'logs');
        mkdirSync(dir, { recursive: true });
        const err = await shell.openPath(dir);
        if (err) throw new UserFacingError('reveal_failed', err);
      },
      saveFile: async (name, data) => {
        const r = await dialog.showSaveDialog({ defaultPath: join(app.getPath('downloads'), basename(name)) });
        if (r.canceled || !r.filePath) return false;
        await writeFile(r.filePath, data);
        return true;
      },
      settings: () => settings.get(),
      updateSettings: (patch) => {
        const before = settings.get();
        const next = settings.update(patch);
        if (before.notifications !== next.notifications) broker.updateHello();
        return next;
      },
    },
  });

  ipcMain.handle(INVOKE_CHANNEL, (event, channel: unknown, input: unknown) => {
    if (!isAppUrl(event.senderFrame?.url ?? '')) return { ok: false, error: { code: 'forbidden', message: 'Untrusted sender.' } };
    return dispatch(channel, input, ctx(event.sender.id), (err) => log(`ipc ${String(channel).slice(0, 64)} failed`, err));
  });
  app.on('web-contents-created', (_e, wc) => {
    const id = wc.id;
    wc.on('destroyed', () => broker.dropSender(id));
  });

  hardenSession();
  serveRenderer(join(__dirname, 'renderer'));
  Menu.setApplicationMenu(buildAppMenu({ navigate: openRoute, dev: !app.isPackaged }));
  tray = new DeskTray({ open: openRoute, quit: () => app.quit() });
  testHooks.trayTitle = () => tray?.title ?? '';
  tray.update(broker.snapshot());

  if (app.isPackaged) await daemon.ensureCurrent().catch((err) => log('daemon refresh failed', err));
  void broker.start();
  createMainWindow({ preload });

  app.on('activate', () => openRoute());
  app.on('second-instance', () => openRoute());
  app.on('before-quit', () => broker.stop());
}

if (!e2e && !app.requestSingleInstanceLock()) {
  app.quit();
} else {
  // Closing the last window keeps Desk in the menu bar; Quit is in the tray menu and ⌘Q.
  app.on('window-all-closed', () => {});
  app.whenReady().then(start, (err) => console.error(err));
}
```

- [ ] **Step 2: Verify**

Run: `pnpm typecheck && pnpm --filter @desk/desktop build && pnpm test`
Expected: PASS. The build emits `dist/main.cjs` without `import.meta` warnings.

- [ ] **Step 3: Commit**

```bash
git add apps/desktop
git commit -m "feat(desktop): Electron main — app protocol and CSP, hardened windows, preload bridge, tray, menu, lifecycle

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Renderer foundation (bridge, stores, router, tokens, components, shell)

**Files:**
- Create under `apps/desktop/src/renderer/`:
  - `bridge.ts`, `store.ts`, `state/global.ts`, `router.ts`, `theme/tokens.css`, `vite-env.d.ts`
  - `components/{Button,Field,Sheet,ConfirmDialog,Toast,EmptyState,StatusChip,SkillBadge,CodeBlock,ExternalLink,SafeMarkdown,TitleBar,ProjectNav,ConnectionOverlay}.tsx`
  - `screens/{MapScreen,ProjectForm,Pending}.tsx`, `App.tsx`
  - `test/bridge.ts`
  - Tests: `router.test.ts`, `components/SafeMarkdown.test.tsx`, `components/TitleBar.test.tsx`, `components/Toast.test.tsx`, `components/StatusChip.test.tsx`
- Replace: `main.tsx`

**Interfaces:**
- Consumes: `Channel`, `ChannelInput`, `IpcResult` and `ChannelOutput` (types), `PushChannel`, `GlobalState`, `initialGlobalState`, `STRIP_CODE`.
- Produces:

```ts
// bridge.ts
export class DeskCallError extends Error { code: string; status: number | undefined }
export function call<C extends Channel>(channel: C, input: ChannelInput<C>): Promise<ChannelOutput<C>>;
export function onPush<T>(channel: PushChannel, cb: (payload: T) => void): () => void;
// store.ts
export function createStore<T>(initial: T): Store<T>; export function useStore<T, S>(s: Store<T>, select: (v: T) => S): S;
// state/global.ts
export const globalStore: Store<GlobalState>; export function startGlobalSync(): () => void; export function useGlobal<S>(select): S;
// router.ts
export type Route = …; export function parseRoute(hash): Route; export function href(r: Route): string; export function navigate(to: Route | string): void; export function useRoute(): Route;
// components: Button, Field, Sheet, ConfirmDialog, toast/toastError/describeError/Toaster, EmptyState,
//   StatusChip + statusLabel, SkillBadge, CodeBlock, ExternalLink, SafeMarkdown, TitleBar, ProjectNav, ConnectionOverlay
// screens: MapScreen (list view + new-project sheet), ProjectForm, Pending
```

- [ ] **Step 1: Write the failing tests**

`apps/desktop/src/renderer/test/bridge.ts`:

```ts
import type { PushChannel } from '../../shared/channels';
import type { Channel, IpcError } from '../../shared/ipc';

type Handler = (input: any) => unknown;

/** Installs a fake `window.desk` for component tests. Handlers may throw an IpcError-shaped object to fail. */
export function installBridge(handlers: Partial<Record<Channel, Handler>> = {}) {
  const listeners = new Map<string, Set<(p: unknown) => void>>();
  const calls: Array<{ channel: string; input: unknown }> = [];
  window.desk = {
    platform: 'darwin',
    invoke: async (channel: string, input: unknown) => {
      calls.push({ channel, input });
      const h = handlers[channel as Channel];
      if (!h) return { ok: false, error: { code: 'unknown_channel', message: channel } };
      try {
        return { ok: true, value: await h(input) };
      } catch (e) {
        return { ok: false, error: e as IpcError };
      }
    },
    on: (channel: PushChannel, cb: (p: unknown) => void) => {
      const set = listeners.get(channel) ?? new Set();
      listeners.set(channel, set);
      set.add(cb);
      return () => {
        set.delete(cb);
      };
    },
  };
  return { calls, emit: (channel: PushChannel, payload: unknown) => listeners.get(channel)?.forEach((l) => l(payload)) };
}
```

`apps/desktop/src/renderer/router.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { href, parseRoute, type Route } from './router';

describe('router', () => {
  it('parses and builds every route', () => {
    const routes: Route[] = [
      { name: 'onboarding' },
      { name: 'map' },
      { name: 'map', newProject: true },
      { name: 'attention' },
      { name: 'attention', item: 'approval:a/1' },
      { name: 'skills' },
      { name: 'skills', skill: 'brand-voice' },
      { name: 'system' },
      { name: 'project', id: 'p1', tab: 'conversation' },
      { name: 'project', id: 'p1', tab: 'threads', threadId: 't9' },
      { name: 'project', id: 'p1', tab: 'settings' },
    ];
    for (const r of routes) expect(parseRoute(href(r))).toEqual(r);
  });

  it('falls back to the map', () => {
    expect(parseRoute('')).toEqual({ name: 'map' });
    expect(parseRoute('#/nowhere')).toEqual({ name: 'map' });
    expect(parseRoute('#/p')).toEqual({ name: 'map' });
    expect(parseRoute('#/p/x/bogus')).toEqual({ name: 'project', id: 'x', tab: 'conversation' });
  });
});
```

`apps/desktop/src/renderer/components/SafeMarkdown.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { installBridge } from '../test/bridge';
import { SafeMarkdown } from './SafeMarkdown';

afterEach(cleanup);

describe('SafeMarkdown', () => {
  it('renders markdown and GFM', () => {
    const { container } = render(<SafeMarkdown text={'**Five drafts** ready\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- [x] done'} />);
    expect(container.querySelector('strong')?.textContent).toBe('Five drafts');
    expect(container.querySelector('table')).not.toBeNull();
  });

  it('never renders raw HTML, scripts or remote images', () => {
    const { container } = render(<SafeMarkdown text={'<script>alert(1)</script><img src=x onerror=alert(1)>\n\n![chart](https://evil.test/p.png)\n\n<b>bold?</b>'} />);
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('b')).toBeNull();
    expect(screen.getByRole('button', { name: 'Image: chart' })).toBeTruthy();
  });

  it('asks before opening a link, and only opens web links', async () => {
    const bridge = installBridge({ 'app.openExternal': () => ({ ok: true }) });
    render(<SafeMarkdown text={'See [the doc](https://example.com/doc) or [this](javascript:alert(1))'} />);
    expect(screen.queryByRole('button', { name: 'this' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'the doc' }));
    expect(screen.getByRole('dialog').textContent).toContain('https://example.com/doc');
    expect(bridge.calls).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: 'Open in browser' }));
    await waitFor(() => expect(bridge.calls).toEqual([{ channel: 'app.openExternal', input: { url: 'https://example.com/doc' } }]));
  });

  it('renders fenced code as a code block', () => {
    const { container } = render(<SafeMarkdown text={'```bash\ncurl -fsSL https://bun.sh/install | bash\n```'} />);
    expect(container.querySelector('.codeblock pre code')?.textContent).toBe('curl -fsSL https://bun.sh/install | bash');
    expect(container.querySelector('.codeblock-lang')?.textContent).toBe('bash');
  });
});
```

`apps/desktop/src/renderer/components/TitleBar.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { AttentionItem } from '@desk/protocol';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { installBridge } from '../test/bridge';
import { TitleBar } from './TitleBar';

afterEach(cleanup);
beforeEach(() => installBridge());

const item = (id: string): AttentionItem => ({ id, kind: 'approval', project_id: 'p', project_name: 'P', agent_id: null, title: 't', detail: '', created_at: '', ref: {} });

describe('TitleBar', () => {
  it('shows places, the daemon state and the attention pill', () => {
    globalStore.set({
      ...initialGlobalState(),
      connection: { status: 'live' },
      system: { proxy: 'up', notices: [], lastSeq: 0 },
      attention: [item('a'), item('b'), item('c')],
      overview: [{ project: { id: 'p1', name: 'Onboarding revamp', goal: '', updated_at: '' }, desk_status: 'idle', threads: [], latest_report: null, plan_progress: { done: 0, total: 0 }, attention_count: 0 }],
    });
    render(<TitleBar route={{ name: 'project', id: 'p1', tab: 'conversation' }} />);
    const nav = screen.getByRole('navigation', { name: 'Places' });
    expect(nav.textContent).toContain('Map');
    expect(nav.textContent).toContain('Onboarding revamp');
    expect(screen.getByRole('link', { name: 'Onboarding revamp' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByText('deskd · proxy up')).toBeTruthy();
    expect(screen.getByRole('link', { name: '3 need you' }).getAttribute('href')).toBe('#/attention');
  });

  it('says when nothing needs you and when deskd is down', () => {
    globalStore.set({ ...initialGlobalState(), connection: { status: 'offline' } });
    render(<TitleBar route={{ name: 'map' }} />);
    expect(screen.getByText('deskd not running')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'All clear' })).toBeTruthy();
  });
});
```

`apps/desktop/src/renderer/components/Toast.test.tsx`:

```tsx
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { DeskCallError } from '../bridge';
import { installBridge } from '../test/bridge';
import { describeError, toastError, Toaster } from './Toast';

afterEach(cleanup);

describe('toasts', () => {
  it('describes errors, offering the logs for server failures only', () => {
    expect(describeError(new DeskCallError({ code: 'conflict', message: 'Already decided', status: 409 }))).toEqual({ message: 'Already decided', revealLogs: false });
    expect(describeError(new DeskCallError({ code: 'internal', message: 'Something went wrong' }))).toEqual({ message: 'Something went wrong', revealLogs: true });
    expect(describeError(new DeskCallError({ code: 'x', message: 'boom', status: 502 })).revealLogs).toBe(true);
  });

  it('shows a toast with a Reveal logs action', () => {
    const bridge = installBridge({ 'app.revealLogs': () => ({ ok: true }) });
    render(<Toaster />);
    act(() => toastError(new DeskCallError({ code: 'internal', message: 'Something went wrong' })));
    expect(screen.getByText('Something went wrong')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Reveal logs' }));
    expect(bridge.calls.map((c) => c.channel)).toEqual(['app.revealLogs']);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText('Something went wrong')).toBeNull();
  });
});
```

`apps/desktop/src/renderer/components/StatusChip.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { StatusChip, statusLabel } from './StatusChip';

afterEach(cleanup);

describe('StatusChip', () => {
  it('uses calm resilience wording', () => {
    expect(statusLabel('queued', 'Recovered after daemon restart').label).toBe('Will resume');
    expect(statusLabel('queued', null).label).toBe('Queued');
    expect(statusLabel('running', null, true).label).toBe('Paused, will resume');
    expect(statusLabel('waiting', 'Waiting for approval').label).toBe('Waiting');
    expect(statusLabel('failed', 'boom').tone).toBe('fail');
  });

  it('renders the label and the reason', () => {
    render(<StatusChip status="waiting" reason="Waiting for approval" />);
    expect(screen.getByText('Waiting')).toBeTruthy();
    expect(screen.getByTitle('Waiting for approval')).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop/src/renderer`
Expected: FAIL: the modules are missing.

- [ ] **Step 3: Implement**

`apps/desktop/src/renderer/bridge.ts`:

```ts
import type { PushChannel } from '../shared/channels';
import type { Channel, ChannelInput, IpcError, IpcResult } from '../shared/ipc';
import type { ChannelOutput } from '../main/handlers';

export type DeskBridge = {
  invoke(channel: string, input: unknown): Promise<IpcResult<unknown>>;
  on(channel: PushChannel, cb: (payload: unknown) => void): () => void;
  platform: string;
};

declare global {
  interface Window {
    desk: DeskBridge;
  }
}

/** A failed call: `code` is the daemon's error code (or an app code such as daemon_not_running). */
export class DeskCallError extends Error {
  readonly code: string;
  readonly status: number | undefined;

  constructor(e: IpcError) {
    super(e.message);
    this.name = 'DeskCallError';
    this.code = e.code;
    this.status = e.status;
  }
}

export async function call<C extends Channel>(channel: C, input: ChannelInput<C>): Promise<ChannelOutput<C>> {
  const r = await window.desk.invoke(channel, input);
  if (!r.ok) throw new DeskCallError(r.error);
  return r.value as ChannelOutput<C>;
}

export function onPush<T = unknown>(channel: PushChannel, cb: (payload: T) => void): () => void {
  return window.desk.on(channel, cb as (payload: unknown) => void);
}
```

`apps/desktop/src/renderer/store.ts`:

```ts
import { useSyncExternalStore } from 'react';

export type Store<T> = { get(): T; set(next: T | ((prev: T) => T)): void; subscribe(fn: () => void): () => void };

/** A minimal external store for React (selectors must return stable values, e.g. fields, not new objects). */
export function createStore<T>(initial: T): Store<T> {
  let value = initial;
  const subs = new Set<() => void>();
  return {
    get: () => value,
    set: (next) => {
      value = typeof next === 'function' ? (next as (prev: T) => T)(value) : next;
      for (const s of subs) s();
    },
    subscribe: (fn) => {
      subs.add(fn);
      return () => {
        subs.delete(fn);
      };
    },
  };
}

export function useStore<T, S>(store: Store<T>, select: (value: T) => S): S {
  return useSyncExternalStore(store.subscribe, () => select(store.get()));
}
```

`apps/desktop/src/renderer/state/global.ts`:

```ts
import { initialGlobalState, type GlobalState } from '../../shared/state';
import { call, onPush } from '../bridge';
import { createStore, useStore } from '../store';

export const globalStore = createStore<GlobalState>(initialGlobalState());

/** Seeds from the broker's snapshot, then follows its pushes. */
export function startGlobalSync(): () => void {
  let pushed = false;
  const off = onPush<GlobalState>('desk:global', (s) => {
    pushed = true;
    globalStore.set(s);
  });
  void call('broker.snapshot', {})
    .then((s) => {
      if (!pushed) globalStore.set(s);
    })
    .catch(() => {});
  return off;
}

export const useGlobal = <S,>(select: (s: GlobalState) => S): S => useStore(globalStore, select);
```

`apps/desktop/src/renderer/router.ts`:

```ts
import { useMemo, useSyncExternalStore } from 'react';

export type ProjectTab = 'conversation' | 'threads' | 'library' | 'memory' | 'settings';
export const PROJECT_TABS: ProjectTab[] = ['conversation', 'threads', 'library', 'memory', 'settings'];

export type Route =
  | { name: 'onboarding' }
  | { name: 'map'; newProject?: boolean }
  | { name: 'attention'; item?: string }
  | { name: 'skills'; skill?: string }
  | { name: 'system' }
  | { name: 'project'; id: string; tab: ProjectTab; threadId?: string };

const enc = encodeURIComponent;

export function parseRoute(hash: string): Route {
  const [path = '', query = ''] = hash.replace(/^#/, '').split('?');
  const q = new URLSearchParams(query);
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  switch (parts[0]) {
    case 'onboarding':
      return { name: 'onboarding' };
    case 'attention': {
      const item = q.get('item');
      return item ? { name: 'attention', item } : { name: 'attention' };
    }
    case 'skills':
      return parts[1] ? { name: 'skills', skill: parts[1] } : { name: 'skills' };
    case 'system':
      return { name: 'system' };
    case 'p': {
      const id = parts[1];
      if (!id) return { name: 'map' };
      const tab = PROJECT_TABS.includes(parts[2] as ProjectTab) ? (parts[2] as ProjectTab) : 'conversation';
      return tab === 'threads' && parts[3] ? { name: 'project', id, tab, threadId: parts[3] } : { name: 'project', id, tab };
    }
    default:
      return q.get('new') === '1' ? { name: 'map', newProject: true } : { name: 'map' };
  }
}

export function href(r: Route): string {
  switch (r.name) {
    case 'onboarding':
      return '#/onboarding';
    case 'map':
      return r.newProject ? '#/map?new=1' : '#/map';
    case 'attention':
      return r.item ? `#/attention?item=${enc(r.item)}` : '#/attention';
    case 'skills':
      return r.skill ? `#/skills/${enc(r.skill)}` : '#/skills';
    case 'system':
      return '#/system';
    case 'project':
      return `#/p/${enc(r.id)}/${r.tab}${r.threadId ? `/${enc(r.threadId)}` : ''}`;
  }
}

export function navigate(to: Route | string): void {
  const target = typeof to === 'string' ? to : href(to);
  window.location.hash = target.replace(/^#/, '');
}

const subscribeHash = (fn: () => void) => {
  window.addEventListener('hashchange', fn);
  return () => window.removeEventListener('hashchange', fn);
};

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribeHash, () => window.location.hash);
  return useMemo(() => parseRoute(hash), [hash]);
}
```

`apps/desktop/src/renderer/theme/tokens.css`:

```css
:root {
  --ground: #efeae0;
  --titlebar: #e6e0d4;
  --nav: #dad3c5;
  --rule: #d6cfc1;
  --rule-soft: #e3dfd6;
  --ink: #1c1b18;
  --text: #3d3a34;
  --text-min: #4a4740;
  --run: #2f5bd3;
  --run-text: #1f45a8;
  --run-pastel: #e3e8f5;
  --run-ring: #c9d3ec;
  --wait: #a15c00;
  --wait-text: #7a4500;
  --wait-pastel: #f3e6cf;
  --muted: #8a857b;
  --muted-blue: #b7c4e6;
  --accent: #c4441c;
  --accent-tint: #c4441c14;
  --ok: #2f6b4f;
  --card: #ffffff;
  --card-shadow: 0 16px 36px rgba(28, 27, 24, 0.14);
  --radius-card: 16px;
  --code-bg: #1c1b18;
  --code-fg: #f3f0e8;
  --font-serif: 'Newsreader Variable', Newsreader, Georgia, serif;
  --font-sans: 'Geist Variable', Geist, system-ui, sans-serif;
  --font-mono: 'Geist Mono Variable', 'Geist Mono', ui-monospace, monospace;
}

*,
*::before,
*::after {
  box-sizing: border-box;
}
html,
body,
#root {
  height: 100%;
  margin: 0;
}
body {
  background: var(--ground);
  color: var(--ink);
  font-family: var(--font-sans);
  font-size: 13.5px;
  line-height: 1.45;
  -webkit-font-smoothing: antialiased;
}
button,
input,
textarea,
select {
  font: inherit;
  color: inherit;
}
a {
  color: var(--ink);
}
a:hover {
  color: var(--run);
}
:focus-visible {
  outline: 2px solid var(--run);
  outline-offset: 2px;
}
code,
kbd,
pre {
  font-family: var(--font-mono);
}

.app {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.screen {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.title {
  margin: 0;
  font-family: var(--font-serif);
  font-weight: 500;
  font-size: 36px;
  line-height: 1.1;
}
.subtitle {
  margin: 0;
  color: var(--text-min);
  max-width: 560px;
}
.eyebrow {
  margin: 0;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--text);
}
.mono {
  font-family: var(--font-mono);
}
.muted {
  color: var(--text-min);
}
.link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--ink);
  text-decoration: underline;
  cursor: pointer;
}
.link:hover {
  color: var(--run);
}

/* Title bar */
.titlebar {
  height: 44px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 0 16px;
  background: var(--titlebar);
  border-bottom: 1px solid var(--rule);
  -webkit-app-region: drag;
  user-select: none;
}
.titlebar.mac {
  padding-left: 84px;
}
.titlebar a,
.titlebar button {
  -webkit-app-region: no-drag;
}
.places {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: 9px;
  background: var(--nav);
}
.places a {
  height: 28px;
  display: flex;
  align-items: center;
  padding: 0 12px;
  border-radius: 7px;
  font-size: 12.5px;
  color: var(--text-min);
  text-decoration: none;
  white-space: nowrap;
}
.places a[aria-current='page'] {
  background: #fff;
  color: var(--ink);
  font-weight: 500;
}
.spacer {
  flex: 1;
}
.daemon {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-min);
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 4px;
  background: var(--muted);
}
.dot.ok {
  background: var(--ok);
}
.dot.warn {
  background: var(--wait);
}
.dot.bad {
  background: var(--accent);
}
.pill {
  height: 28px;
  display: flex;
  align-items: center;
  padding: 0 12px;
  border-radius: 14px;
  font-size: 12.5px;
  font-weight: 500;
  text-decoration: none;
}
.pill.needs {
  background: var(--accent);
  color: #fff;
}
.pill.needs:hover {
  color: #fff;
}
.pill.clear {
  color: var(--text-min);
}

/* Project sub-nav */
.subnav {
  height: 40px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 16px;
  border-bottom: 1px solid var(--rule);
}
.subnav a {
  height: 40px;
  display: flex;
  align-items: center;
  padding: 0 10px;
  font-size: 13px;
  color: var(--text-min);
  text-decoration: none;
  border-bottom: 2px solid transparent;
}
.subnav a[aria-current='page'] {
  color: var(--ink);
  font-weight: 500;
  border-bottom-color: var(--ink);
}

/* Buttons and fields */
.btn {
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 14px;
  border-radius: 8px;
  border: 1px solid transparent;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
}
.btn:disabled {
  opacity: 0.55;
  cursor: default;
}
.btn-sm {
  height: 28px;
  padding: 0 12px;
  font-size: 12.5px;
  border-radius: 7px;
}
.btn-primary {
  background: var(--ink);
  color: #fff;
}
.btn-secondary {
  background: #fff;
  border-color: #cfc9bd;
  color: var(--ink);
}
.btn-ghost {
  background: transparent;
  color: var(--ink);
}
.btn-ghost:hover {
  background: #0000000a;
}
.btn-danger {
  background: var(--accent);
  color: #fff;
}
.btn[aria-busy='true']::after {
  content: '…';
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field label {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--text);
}
.input,
.textarea,
.select {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid #cfc9bd;
  border-radius: 8px;
  background: #fff;
}
.textarea {
  min-height: 72px;
  resize: vertical;
}
.field-hint {
  margin: 0;
  font-size: 12px;
  color: var(--text-min);
}
.field-error {
  margin: 0;
  font-size: 12.5px;
  color: var(--accent);
}
.actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* Cards, sheets, toasts, overlays */
.card {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--radius-card);
  box-shadow: var(--card-shadow);
}
.sheet-backdrop {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(28, 27, 24, 0.28);
}
.sheet {
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 64px);
  overflow: auto;
  padding: 24px;
  background: #fff;
  border: 1px solid var(--rule);
  border-radius: var(--radius-card);
  box-shadow: var(--card-shadow);
}
.sheet-title {
  margin: 0 0 12px;
  font-family: var(--font-serif);
  font-weight: 500;
  font-size: 24px;
}
.sheet-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.sheet-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 20px;
}
.toaster {
  position: fixed;
  right: 16px;
  bottom: 16px;
  z-index: 60;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.toast {
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: 420px;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--ink);
  color: #fff;
  box-shadow: var(--card-shadow);
}
.toast .btn-ghost {
  color: #fff;
}
.toast-close {
  border: 0;
  background: none;
  color: #fff;
  cursor: pointer;
  font-size: 16px;
}
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 48px 24px;
  text-align: center;
  color: var(--text-min);
}
.empty h2 {
  margin: 0;
  font-family: var(--font-serif);
  font-weight: 500;
  font-size: 22px;
  color: var(--ink);
}
.overlay {
  position: absolute;
  inset: 0;
  z-index: 40;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(239, 234, 224, 0.86);
}
.overlay .card {
  width: 440px;
  padding: 28px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: var(--wait-pastel);
  color: var(--wait-text);
  font-size: 12.5px;
}

/* Chips and badges */
.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  padding: 0 8px;
  border-radius: 11px;
  font-size: 11.5px;
  font-weight: 500;
}
.chip-run {
  background: var(--run-pastel);
  color: var(--run-text);
}
.chip-wait {
  background: var(--wait-pastel);
  color: var(--wait-text);
}
.chip-done {
  background: #e6e1d7;
  color: var(--text);
}
.chip-fail {
  background: var(--accent-tint);
  color: var(--accent);
}
.chip-idle {
  background: #e6e1d7;
  color: var(--text-min);
}
.skill-badge {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 6px;
  border: 1px solid var(--rule);
  background: #fff;
  font-family: var(--font-mono);
  font-size: 11.5px;
}

/* Code and markdown */
.codeblock {
  border-radius: 8px;
  background: var(--code-bg);
  color: var(--code-fg);
  overflow: hidden;
}
.codeblock-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 8px 0 10px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: #bdb8ad;
}
.codeblock-copy {
  border: 0;
  background: none;
  color: #bdb8ad;
  cursor: pointer;
  font-size: 11px;
}
.codeblock pre {
  margin: 0;
  padding: 6px 10px 10px;
  overflow-x: auto;
  font-size: 12px;
  line-height: 1.5;
}
.md {
  font-size: 14px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}
.md p {
  margin: 0 0 10px;
}
.md p:last-child {
  margin-bottom: 0;
}
.md ul,
.md ol {
  margin: 0 0 10px;
  padding-left: 20px;
}
.md table {
  border-collapse: collapse;
  margin: 0 0 10px;
}
.md th,
.md td {
  padding: 4px 8px;
  border: 1px solid var(--rule);
}
.md .md-code {
  padding: 1px 4px;
  border-radius: 4px;
  background: #0000000d;
  font-size: 12.5px;
}
.md .codeblock {
  margin: 0 0 10px;
}
.md-link-disabled {
  text-decoration: underline dotted;
}

/* Onboarding and interim screens */
.onboarding {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 0 16px 48px;
}
.drag-strip {
  height: 44px;
  width: 100%;
  flex-shrink: 0;
  -webkit-app-region: drag;
}
.onboarding-card {
  width: 640px;
  max-width: 100%;
  padding: 32px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.steps {
  display: flex;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.steps li {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-min);
}
.steps li[aria-current='step'] {
  color: var(--ink);
  font-weight: 600;
}
.steps li::before {
  content: '';
  width: 8px;
  height: 8px;
  border-radius: 4px;
  border: 1.5px solid currentColor;
}
.steps li.done::before {
  background: currentColor;
}
.status-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
}
.page {
  max-width: 1100px;
  margin: 0 auto;
  padding: 28px 32px 48px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.project-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}
.project-card {
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  text-decoration: none;
  color: var(--ink);
}
.project-card h2 {
  margin: 0;
  font-family: var(--font-serif);
  font-weight: 500;
  font-size: 22px;
}
.sources {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.sources li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 10px;
  border: 1px solid var(--rule-soft);
  border-radius: 8px;
  font-family: var(--font-mono);
  font-size: 12px;
}
```

`apps/desktop/src/renderer/components/Button.tsx`:

```tsx
import type { ButtonHTMLAttributes } from 'react';

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger'; size?: 'sm' | 'md'; pending?: boolean };

/** A real button; `pending` disables it and marks it busy until the write completes. */
export function Button({ variant = 'secondary', size = 'md', pending = false, className, disabled, type = 'button', ...rest }: Props) {
  const cls = ['btn', `btn-${variant}`, size === 'sm' ? 'btn-sm' : '', className ?? ''].filter(Boolean).join(' ');
  return <button type={type} className={cls} disabled={disabled || pending} aria-busy={pending || undefined} {...rest} />;
}
```

`apps/desktop/src/renderer/components/Field.tsx`:

```tsx
import type { ReactNode } from 'react';

export function Field({ id, label, hint, error, children }: { id: string; label: string; hint?: string; error?: string | null; children: ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
      {error ? (
        <p className="field-error" role="alert" id={`${id}-error`}>
          {error}
        </p>
      ) : hint ? (
        <p className="field-hint" id={`${id}-hint`}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}
```

`apps/desktop/src/renderer/components/Sheet.tsx`:

```tsx
import { useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** A modal dialog: focus moves in, Escape or a backdrop click closes, focus returns on close. */
export function Sheet({ title, onClose, children, footer, width = 520 }: { title: string; onClose(): void; children: ReactNode; footer?: ReactNode; width?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const titleId = useId();
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>('input, textarea, select, button')?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close.current();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      previous?.focus?.();
    };
  }, []);
  return createPortal(
    <div className="sheet-backdrop" onMouseDown={(e) => e.target === e.currentTarget && close.current()}>
      <div className="sheet" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref} style={{ width }}>
        <h2 id={titleId} className="sheet-title">
          {title}
        </h2>
        <div className="sheet-body">{children}</div>
        {footer ? <div className="sheet-footer">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
```

`apps/desktop/src/renderer/components/ConfirmDialog.tsx`:

```tsx
import type { ReactNode } from 'react';
import { Button } from './Button';
import { Sheet } from './Sheet';

export function ConfirmDialog(o: { title: string; children: ReactNode; confirmLabel: string; danger?: boolean; onConfirm(): void; onCancel(): void }) {
  return (
    <Sheet
      title={o.title}
      onClose={o.onCancel}
      width={460}
      footer={
        <>
          <Button onClick={o.onCancel}>Cancel</Button>
          <Button variant={o.danger ? 'danger' : 'primary'} onClick={o.onConfirm}>
            {o.confirmLabel}
          </Button>
        </>
      }
    >
      {o.children}
    </Sheet>
  );
}
```

`apps/desktop/src/renderer/components/Toast.tsx`:

```tsx
import { call, DeskCallError } from '../bridge';
import { createStore, useStore } from '../store';

export type Toast = { id: number; tone: 'error' | 'info'; message: string; action?: { label: string; run(): void } };

export const toastStore = createStore<Toast[]>([]);
let nextId = 1;

export function dismissToast(id: number): void {
  toastStore.set((list) => list.filter((t) => t.id !== id));
}

export function toast(t: Omit<Toast, 'id'>, ms = 6000): void {
  const id = nextId++;
  toastStore.set((list) => [...list, { ...t, id }]);
  setTimeout(() => dismissToast(id), ms);
}

/** Server failures (5xx, internal) offer the logs; everything else is explained in place by its message. */
export function describeError(err: unknown): { message: string; revealLogs: boolean } {
  if (err instanceof DeskCallError) return { message: err.message, revealLogs: err.code === 'internal' || (err.status ?? 0) >= 500 };
  return { message: err instanceof Error ? err.message : String(err), revealLogs: false };
}

export function toastError(err: unknown): void {
  const d = describeError(err);
  toast({ tone: 'error', message: d.message, ...(d.revealLogs ? { action: { label: 'Reveal logs', run: () => void call('app.revealLogs', {}).catch(() => {}) } } : {}) });
}

export function Toaster() {
  const toasts = useStore(toastStore, (t) => t);
  return (
    <div className="toaster" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.tone}`}>
          <span>{t.message}</span>
          {t.action ? (
            <button type="button" className="btn btn-ghost btn-sm" onClick={t.action.run}>
              {t.action.label}
            </button>
          ) : null}
          <button type="button" className="toast-close" aria-label="Dismiss" onClick={() => dismissToast(t.id)}>
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
```

`apps/desktop/src/renderer/components/EmptyState.tsx`:

```tsx
import type { ReactNode } from 'react';

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <h2>{title}</h2>
      {children ? <p className="subtitle">{children}</p> : null}
      {action}
    </div>
  );
}
```

`apps/desktop/src/renderer/components/StatusChip.tsx`:

```tsx
import type { AgentStatus } from '@desk/protocol';

export type StatusTone = 'run' | 'wait' | 'done' | 'fail' | 'idle';

/** Calm resilience wording: a restart or a proxy outage reads as "will resume", never as an error. */
export function statusLabel(status: AgentStatus, reason?: string | null, proxyDown = false): { label: string; tone: StatusTone } {
  switch (status) {
    case 'running':
      return proxyDown ? { label: 'Paused, will resume', tone: 'wait' } : { label: 'Running', tone: 'run' };
    case 'queued':
      return /restart|shut ?down/i.test(reason ?? '') ? { label: 'Will resume', tone: 'wait' } : { label: 'Queued', tone: 'wait' };
    case 'waiting':
      return { label: 'Waiting', tone: 'wait' };
    case 'done':
      return { label: 'Done', tone: 'done' };
    case 'failed':
      return { label: 'Failed', tone: 'fail' };
    case 'cancelled':
      return { label: 'Stopped', tone: 'done' };
    default:
      return { label: 'Idle', tone: 'idle' };
  }
}

export function StatusChip({ status, reason, proxyDown }: { status: AgentStatus; reason?: string | null; proxyDown?: boolean }) {
  const s = statusLabel(status, reason, proxyDown);
  return (
    <span className={`chip chip-${s.tone}`} {...(reason ? { title: reason } : {})}>
      {s.label}
    </span>
  );
}
```

`apps/desktop/src/renderer/components/SkillBadge.tsx`:

```tsx
export function SkillBadge({ name, scope }: { name: string; scope?: 'global' | 'project' }) {
  return (
    <span className="skill-badge" {...(scope ? { title: `${scope} skill` } : {})}>
      {name}
    </span>
  );
}
```

`apps/desktop/src/renderer/components/CodeBlock.tsx`:

```tsx
import { useState } from 'react';

export function CodeBlock({ code, language }: { code: string; language?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard
      ?.writeText(code)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {});
  };
  return (
    <div className="codeblock">
      <div className="codeblock-bar">
        <span className="codeblock-lang">{language ?? ''}</span>
        <button type="button" className="codeblock-copy" onClick={copy}>
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  );
}
```

`apps/desktop/src/renderer/components/ExternalLink.tsx`:

```tsx
import { useState, type ReactNode } from 'react';
import { call } from '../bridge';
import { ConfirmDialog } from './ConfirmDialog';
import { toastError } from './Toast';

const ALLOWED = new Set(['http:', 'https:', 'mailto:']);

/** A link from agent content: web and mail links open in the system browser after a confirmation; others are inert. */
export function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  const [asking, setAsking] = useState(false);
  let url: URL | null = null;
  try {
    url = new URL(href);
  } catch {
    url = null;
  }
  if (!url || !ALLOWED.has(url.protocol)) return <span className="md-link-disabled">{children}</span>;
  const target = url.toString();
  return (
    <>
      <button type="button" className="link" title={target} onClick={() => setAsking(true)}>
        {children}
      </button>
      {asking ? (
        <ConfirmDialog
          title="Open this link?"
          confirmLabel="Open in browser"
          onCancel={() => setAsking(false)}
          onConfirm={() => {
            setAsking(false);
            void call('app.openExternal', { url: target }).catch(toastError);
          }}
        >
          <p className="subtitle">Links in agent messages can point anywhere. Check the address first.</p>
          <p className="mono" style={{ margin: 0, overflowWrap: 'anywhere' }}>
            {target}
          </p>
        </ConfirmDialog>
      ) : null}
    </>
  );
}
```

`apps/desktop/src/renderer/components/SafeMarkdown.tsx`:

```tsx
import Markdown, { defaultUrlTransform, type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { CodeBlock } from './CodeBlock';
import { ExternalLink } from './ExternalLink';

type HastNode = { type?: string; value?: string; tagName?: string; properties?: { className?: unknown }; children?: HastNode[] };

const textOf = (n: HastNode | undefined): string => (n?.type === 'text' ? (n.value ?? '') : (n?.children ?? []).map(textOf).join(''));

const components: Components = {
  a: ({ href, children }) => <ExternalLink href={href ?? ''}>{children}</ExternalLink>,
  img: ({ src, alt }) =>
    typeof src === 'string' && src ? <ExternalLink href={src}>{`Image: ${alt || src}`}</ExternalLink> : <span>{alt}</span>,
  pre: ({ node }) => {
    const code = (node as HastNode | undefined)?.children?.find((c) => c.tagName === 'code');
    const classes = Array.isArray(code?.properties?.className) ? (code.properties.className as unknown[]).map(String) : [];
    const lang = classes.find((c) => c.startsWith('language-'))?.slice('language-'.length);
    return <CodeBlock code={textOf(code).replace(/\n$/, '')} {...(lang ? { language: lang } : {})} />;
  },
  code: ({ children }) => <code className="md-code">{children}</code>,
};

/** Markdown from agents: GFM, no raw HTML, no remote images (they become links), links confirmed before opening. */
export function SafeMarkdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={`md${className ? ` ${className}` : ''}`}>
      <Markdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={defaultUrlTransform} components={components}>
        {text}
      </Markdown>
    </div>
  );
}
```

`apps/desktop/src/renderer/components/TitleBar.tsx`:

```tsx
import { href, type Route } from '../router';
import { useGlobal } from '../state/global';

function daemonLabel(status: string, proxy: string): { label: string; tone: '' | 'ok' | 'warn' | 'bad' } {
  switch (status) {
    case 'live':
      return { label: `deskd · proxy ${proxy}`, tone: proxy === 'up' ? 'ok' : 'warn' };
    case 'reconnecting':
      return { label: 'reconnecting…', tone: 'warn' };
    case 'offline':
      return { label: 'deskd not running', tone: 'bad' };
    case 'mismatch':
      return { label: 'deskd needs an update', tone: 'bad' };
    default:
      return { label: 'connecting…', tone: '' };
  }
}

/** Places nav (Map · project · Skills · System), daemon status, and the attention pill. */
export function TitleBar({ route }: { route: Route }) {
  const count = useGlobal((s) => s.attention.length);
  const status = useGlobal((s) => s.connection.status);
  const proxy = useGlobal((s) => s.system.proxy);
  const overview = useGlobal((s) => s.overview);
  const projectId = route.name === 'project' ? route.id : null;
  const project = projectId ? overview.find((p) => p.project.id === projectId) : undefined;
  const d = daemonLabel(status, proxy);
  const mac = window.desk?.platform === 'darwin';
  return (
    <header className={`titlebar${mac ? ' mac' : ''}`}>
      <nav aria-label="Places" className="places">
        <a href={href({ name: 'map' })} aria-current={route.name === 'map' ? 'page' : undefined}>
          Map
        </a>
        {project ? (
          <a href={href({ name: 'project', id: project.project.id, tab: 'conversation' })} aria-current="page">
            {project.project.name}
          </a>
        ) : null}
        <a href={href({ name: 'skills' })} aria-current={route.name === 'skills' ? 'page' : undefined}>
          Skills
        </a>
        <a href={href({ name: 'system' })} aria-current={route.name === 'system' ? 'page' : undefined}>
          System
        </a>
      </nav>
      <span className="spacer" />
      <span className="daemon">
        <span className={`dot ${d.tone}`} aria-hidden="true" />
        {d.label}
      </span>
      <a href={href({ name: 'attention' })} className={`pill ${count ? 'needs' : 'clear'}`} aria-current={route.name === 'attention' ? 'page' : undefined}>
        {count ? `${count} need you` : 'All clear'}
      </a>
    </header>
  );
}
```

`apps/desktop/src/renderer/components/ProjectNav.tsx`:

```tsx
import { href, PROJECT_TABS, type ProjectTab } from '../router';

const LABEL: Record<ProjectTab, string> = { conversation: 'Conversation', threads: 'Threads', library: 'Library', memory: 'Memory', settings: 'Settings' };

export function ProjectNav({ projectId, tab }: { projectId: string; tab: ProjectTab }) {
  return (
    <nav aria-label="Project" className="subnav">
      {PROJECT_TABS.map((t) => (
        <a key={t} href={href({ name: 'project', id: projectId, tab: t })} aria-current={t === tab ? 'page' : undefined}>
          {LABEL[t]}
        </a>
      ))}
    </nav>
  );
}
```

`apps/desktop/src/renderer/components/ConnectionOverlay.tsx`:

```tsx
import { useState } from 'react';
import { call } from '../bridge';
import { useGlobal } from '../state/global';
import { Button } from './Button';
import { describeError } from './Toast';

/** Daemon lost: an overlay with Start. Reconnecting: a calm banner. Protocol mismatch: blocks the app. */
export function ConnectionOverlay() {
  const connection = useGlobal((s) => s.connection);
  const [pending, setPending] = useState<'start' | 'restart' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = (op: 'start' | 'restart') => {
    setPending(op);
    setError(null);
    void call(op === 'start' ? 'daemon.start' : 'daemon.restart', {})
      .catch((err) => setError(describeError(err).message))
      .finally(() => setPending(null));
  };
  if (connection.status === 'reconnecting') {
    return (
      <div className="banner" role="status">
        <span className="dot warn" aria-hidden="true" />
        Reconnecting to deskd… Your threads keep running.
      </div>
    );
  }
  if (connection.status !== 'offline' && connection.status !== 'mismatch') return null;
  const mismatch = connection.status === 'mismatch';
  return (
    <div className="overlay" role="alertdialog" aria-modal="true" aria-labelledby="overlay-title">
      <div className="card">
        <h2 id="overlay-title" className="sheet-title">
          {mismatch ? 'Desk and deskd are out of step' : 'Desk isn’t running'}
        </h2>
        <p className="subtitle">
          {mismatch
            ? `${connection.detail ?? 'The daemon speaks a different protocol.'} Restart the daemon from this install, or update Desk.`
            : 'Your projects and threads are safe. Start the daemon to pick up where they left off.'}
        </p>
        {error ? (
          <p className="field-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="actions">
          <Button variant="primary" pending={pending !== null} onClick={() => run(mismatch ? 'restart' : 'start')}>
            {mismatch ? 'Restart deskd' : 'Start Desk'}
          </Button>
          <Button variant="ghost" onClick={() => void call('app.revealLogs', {}).catch(() => {})}>
            Reveal logs
          </Button>
        </div>
      </div>
    </div>
  );
}
```

`apps/desktop/src/renderer/screens/ProjectForm.tsx`:

```tsx
import { useState, type FormEvent } from 'react';
import { call, DeskCallError } from '../bridge';
import { Button } from '../components/Button';
import { Field } from '../components/Field';
import { toastError } from '../components/Toast';

/** Name, goal, instructions and source folders (native picker). Used by onboarding and the new-project sheet. */
export function ProjectForm({ onCreated, onCancel, submitLabel = 'Create project' }: { onCreated(id: string): void; onCancel?(): void; submitLabel?: string }) {
  const [name, setName] = useState('');
  const [goal, setGoal] = useState('');
  const [instructions, setInstructions] = useState('');
  const [sources, setSources] = useState<string[]>([]);
  const [nameError, setNameError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const pick = async () => {
    try {
      const path = await call('app.pickFolder', { purpose: 'source' });
      if (path && !sources.includes(path)) setSources([...sources, path]);
    } catch (err) {
      toastError(err);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setNameError('Give the project a name.');
      return;
    }
    setNameError(null);
    setFormError(null);
    setPending(true);
    try {
      const created = await call('projects.create', {
        name: name.trim(),
        goal: goal.trim(),
        ...(instructions.trim() ? { instructions: instructions.trim() } : {}),
        ...(sources.length ? { sources: sources.map((path) => ({ path })) } : {}),
      });
      onCreated(created.project.id);
    } catch (err) {
      if (err instanceof DeskCallError && err.status === 400) setFormError(err.message);
      else toastError(err);
    } finally {
      setPending(false);
    }
  };

  return (
    <form className="sheet-body" onSubmit={submit} noValidate>
      <Field id="project-name" label="Name" error={nameError}>
        <input id="project-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Onboarding revamp" />
      </Field>
      <Field id="project-goal" label="Goal" hint="What Desk should work toward. You can refine it later.">
        <textarea id="project-goal" className="textarea" value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Relaunch onboarding next month" />
      </Field>
      <Field id="project-instructions" label="Standing instructions (optional)">
        <textarea id="project-instructions" className="textarea" value={instructions} onChange={(e) => setInstructions(e.target.value)} />
      </Field>
      <div className="field">
        <span className="eyebrow">Sources</span>
        {sources.length ? (
          <ul className="sources">
            {sources.map((s) => (
              <li key={s}>
                <span>{s}</span>
                <Button size="sm" variant="ghost" aria-label={`Remove ${s}`} onClick={() => setSources(sources.filter((x) => x !== s))}>
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="field-hint">Folders or git repositories Desk and its threads can read. Optional.</p>
        )}
        <div>
          <Button size="sm" onClick={() => void pick()}>
            Add folder…
          </Button>
        </div>
      </div>
      {formError ? (
        <p className="field-error" role="alert">
          {formError}
        </p>
      ) : null}
      <div className="actions">
        <Button type="submit" variant="primary" pending={pending}>
          {submitLabel}
        </Button>
        {onCancel ? <Button onClick={onCancel}>Cancel</Button> : null}
      </div>
    </form>
  );
}
```

`apps/desktop/src/renderer/screens/MapScreen.tsx` is the interim Map: the list view. Plan 10 adds the map canvas beside it.

```tsx
import { useState } from 'react';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { Sheet } from '../components/Sheet';
import { href, navigate } from '../router';
import { useGlobal } from '../state/global';
import { ProjectForm } from './ProjectForm';

function summary(threads: Array<{ status: string }>): string {
  const count = (s: string) => threads.filter((t) => t.status === s).length;
  const parts = [
    count('running') && `${count('running')} running`,
    count('waiting') && `${count('waiting')} waiting`,
    count('done') && `${count('done')} done`,
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : 'No threads yet';
}

export function MapScreen({ newProject }: { newProject: boolean }) {
  const overview = useGlobal((s) => s.overview);
  const attention = useGlobal((s) => s.attention.length);
  const [creating, setCreating] = useState(newProject);
  const running = overview.reduce((n, p) => n + p.threads.filter((t) => t.status === 'running').length, 0);
  const close = () => {
    setCreating(false);
    if (newProject) navigate({ name: 'map' });
  };
  return (
    <div className="page">
      <div className="actions" style={{ justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div>
          <h1 className="title">Projects</h1>
          <p className="subtitle">
            {running} thread{running === 1 ? '' : 's'} running in {overview.length} project{overview.length === 1 ? '' : 's'}. {attention} thing
            {attention === 1 ? ' is' : 's are'} waiting on you.
          </p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)}>
          New project
        </Button>
      </div>
      {overview.length ? (
        <div className="project-list">
          {overview.map((p) => (
            <a key={p.project.id} className="card project-card" href={href({ name: 'project', id: p.project.id, tab: 'conversation' })}>
              <h2>{p.project.name}</h2>
              {p.project.goal ? <p className="subtitle">{p.project.goal}</p> : null}
              <span className="muted">{summary(p.threads)}</span>
              {p.latest_report ? <span>{p.latest_report.headline}</span> : null}
              {p.attention_count ? <span style={{ color: 'var(--accent)' }}>{p.attention_count} need you</span> : null}
            </a>
          ))}
        </div>
      ) : (
        <EmptyState title="No projects yet" action={<Button onClick={() => setCreating(true)}>Create a project</Button>}>
          A project is a goal Desk works toward with its own threads, library and memory.
        </EmptyState>
      )}
      {creating ? (
        <Sheet title="New project" onClose={close}>
          <ProjectForm
            onCancel={close}
            onCreated={(id) => {
              setCreating(false);
              navigate({ name: 'project', id, tab: 'conversation' });
            }}
          />
        </Sheet>
      ) : null}
    </div>
  );
}
```

`apps/desktop/src/renderer/screens/Pending.tsx` is the placeholder for screens that later plans build:

```tsx
import { EmptyState } from '../components/EmptyState';

export function Pending({ title }: { title: string }) {
  return (
    <div className="page">
      <EmptyState title={title}>This screen is on its way in the next build.</EmptyState>
    </div>
  );
}
```

`apps/desktop/src/renderer/App.tsx`:

```tsx
import { useEffect } from 'react';
import { onPush } from './bridge';
import { ConnectionOverlay } from './components/ConnectionOverlay';
import { ProjectNav } from './components/ProjectNav';
import { TitleBar } from './components/TitleBar';
import { Toaster } from './components/Toast';
import { navigate, useRoute, type Route } from './router';
import { MapScreen } from './screens/MapScreen';
import { Onboarding, isOnboarded } from './screens/Onboarding';
import { Pending } from './screens/Pending';
import { startGlobalSync } from './state/global';

function Screen({ route }: { route: Route }) {
  switch (route.name) {
    case 'map':
      return <MapScreen newProject={route.newProject ?? false} />;
    case 'attention':
      return <Pending title="Attention" />;
    case 'skills':
      return <Pending title="Skills" />;
    case 'system':
      return <Pending title="System" />;
    case 'project':
      return <Pending title={route.tab[0]!.toUpperCase() + route.tab.slice(1)} />;
    default:
      return null;
  }
}

export function App() {
  const route = useRoute();
  useEffect(() => startGlobalSync(), []);
  useEffect(() => onPush<string>('desk:navigate', (r) => navigate(r)), []);
  useEffect(() => {
    if (!isOnboarded() && route.name !== 'onboarding') navigate({ name: 'onboarding' });
  }, [route.name]);

  if (route.name === 'onboarding') {
    return (
      <>
        <Onboarding />
        <Toaster />
      </>
    );
  }
  return (
    <div className="app">
      <TitleBar route={route} />
      {route.name === 'project' ? <ProjectNav projectId={route.id} tab={route.tab} /> : null}
      <main className="screen">
        <Screen route={route} />
        <ConnectionOverlay />
      </main>
      <Toaster />
    </div>
  );
}
```

`apps/desktop/src/renderer/main.tsx`:

```tsx
import '@fontsource-variable/geist';
import '@fontsource-variable/geist-mono';
import '@fontsource-variable/newsreader';
import './theme/tokens.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

`apps/desktop/src/renderer/vite-env.d.ts` gives TypeScript the Vite client types, so CSS side-effect imports typecheck:

```ts
/// <reference types="vite/client" />
```

`App.tsx` imports `Onboarding` and `isOnboarded`, which Task 9 creates. This task adds a minimal `screens/Onboarding.tsx` so it compiles; Task 9 replaces it:

```tsx
export const isOnboarded = () => true;
export function Onboarding() {
  return null;
}
```

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop && pnpm typecheck && pnpm --filter @desk/desktop build`
Expected: PASS.

If JSX in `*.test.tsx` fails with "React is not defined", add `oxc: { jsx: { runtime: 'automatic' } }` (or `esbuild: { jsx: 'automatic' }` on an esbuild-based Vite) to the root `vitest.config.ts`.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop vitest.config.ts
git commit -m "feat(desktop): renderer foundation — bridge, stores, router, design-C tokens, SafeMarkdown and shell components

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Onboarding

**Files:**
- Replace: `apps/desktop/src/renderer/screens/Onboarding.tsx`
- Create: `apps/desktop/src/renderer/screens/Onboarding.test.tsx`

**Interfaces:**
- Consumes: `call`, `useGlobal`, `ProjectForm`, `Button`, `Field`, `describeError`, `navigate`.
- Produces: `Onboarding` and `isOnboarded()`, plus `markOnboarded()` stored in `localStorage` under `desk.onboarded`, with every access wrapped in try/catch.

The steps:
1. **Daemon:** detect it, or offer Start.
2. **Model endpoint:** show the current source and let the user test it; or take a base URL and key, save them (to the Keychain) and test; or say that this deskd manages the endpoint itself (501).
3. **First project:** create one or skip to the map.

Finishing marks onboarding done and lands on the Map.

- [ ] **Step 1: Write the failing test** — `apps/desktop/src/renderer/screens/Onboarding.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { installBridge } from '../test/bridge';
import { isOnboarded, Onboarding } from './Onboarding';

afterEach(cleanup);
beforeEach(() => {
  localStorage.clear();
  window.location.hash = '#/onboarding';
});

const live = () => globalStore.set({ ...initialGlobalState(), connection: { status: 'live' }, health: { version: '1.0.0', protocol_version: 1, proxy: 'up', uptime_s: 1 } });

describe('Onboarding', () => {
  it('starts the daemon when it is not running', async () => {
    globalStore.set({ ...initialGlobalState(), connection: { status: 'offline' } });
    const bridge = installBridge({ 'daemon.start': () => (live(), { running: true }) });
    render(<Onboarding />);
    fireEvent.click(screen.getByRole('button', { name: 'Start Desk' }));
    await waitFor(() => expect(screen.getByText('deskd 1.0.0 is running.')).toBeTruthy());
    expect(bridge.calls.map((c) => c.channel)).toEqual(['daemon.start']);
  });

  it('saves and tests a new endpoint without keeping the key, then creates the first project', async () => {
    live();
    let configured = false;
    const bridge = installBridge({
      'config.endpoint': () => ({ configured, source: configured ? 'keychain' : null, base_url: configured ? 'http://127.0.0.1:8317/v1' : null }),
      'config.saveEndpoint': () => ((configured = true), { configured: true, source: 'keychain', base_url: 'http://127.0.0.1:8317/v1' }),
      'config.testEndpoint': () => ({ ok: true, models: ['claude-opus-5-5', 'claude-sonnet-5'] }),
      'app.pickFolder': () => '/Users/me/repo',
      'projects.create': (input: { name: string }) => ({ project: { id: 'p1', name: input.name } }),
    });
    render(<Onboarding />);
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    const key = (await screen.findByLabelText('API key')) as HTMLInputElement;
    expect(key.type).toBe('password');
    fireEvent.change(key, { target: { value: 'sk-secret' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save and test' }));
    await waitFor(() => expect(screen.getByText('Connected. 2 models available.')).toBeTruthy());
    expect(screen.queryByDisplayValue('sk-secret')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Launch' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add folder…' }));
    await waitFor(() => expect(screen.getByText('/Users/me/repo')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Create project' }));
    await waitFor(() => expect(window.location.hash).toBe('#/map'));
    expect(isOnboarded()).toBe(true);
    const create = bridge.calls.find((c) => c.channel === 'projects.create');
    expect(create?.input).toEqual({ name: 'Launch', goal: '', sources: [{ path: '/Users/me/repo' }] });
    expect(bridge.calls.find((c) => c.channel === 'config.saveEndpoint')?.input).toEqual({ base_url: 'http://127.0.0.1:8317/v1', api_key: 'sk-secret' });
  });

  it('continues when this deskd manages its own endpoint', async () => {
    live();
    installBridge({ 'config.endpoint': () => Promise.reject({ code: 'unsupported', message: 'Model endpoint setup is not available', status: 501 }) });
    render(<Onboarding />);
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    await waitFor(() => expect(screen.getByText(/manages its model endpoint itself/)).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
    expect(await screen.findByRole('heading', { name: 'Create your first project' })).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop/src/renderer/screens/Onboarding.test.tsx`
Expected: FAIL: the interim `Onboarding` renders nothing.

- [ ] **Step 3: Implement** — `apps/desktop/src/renderer/screens/Onboarding.tsx`:

```tsx
import { useEffect, useState, type FormEvent } from 'react';
import type { ModelEndpointStatus, ModelEndpointTestResult } from '@desk/protocol';
import { call, DeskCallError } from '../bridge';
import { Button } from '../components/Button';
import { Field } from '../components/Field';
import { describeError } from '../components/Toast';
import { navigate } from '../router';
import { useGlobal } from '../state/global';
import { ProjectForm } from './ProjectForm';

const FLAG = 'desk.onboarded';

export function isOnboarded(): boolean {
  try {
    return localStorage.getItem(FLAG) === '1';
  } catch {
    return false;
  }
}

export function markOnboarded(): void {
  try {
    localStorage.setItem(FLAG, '1');
  } catch {
    // Onboarding shows again next launch; harmless.
  }
}

type Step = 'daemon' | 'endpoint' | 'project';
const STEPS: Array<{ id: Step; label: string }> = [
  { id: 'daemon', label: 'Daemon' },
  { id: 'endpoint', label: 'Model endpoint' },
  { id: 'project', label: 'First project' },
];

function DaemonStep({ onNext }: { onNext(): void }) {
  const status = useGlobal((s) => s.connection.status);
  const detail = useGlobal((s) => s.connection.detail);
  const version = useGlobal((s) => s.health?.version);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const start = async () => {
    setPending(true);
    setError(null);
    try {
      await call('daemon.start', {});
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setPending(false);
    }
  };
  const live = status === 'live' || status === 'connecting';
  return (
    <section className="sheet-body" aria-labelledby="step-title">
      <h1 id="step-title" className="title">
        Start the Desk daemon
      </h1>
      <p className="subtitle">Desk runs in the background as deskd, so your projects keep working when this window is closed.</p>
      <p className="status-line">
        <span className={`dot ${live ? 'ok' : status === 'mismatch' ? 'bad' : ''}`} aria-hidden="true" />
        {live ? `deskd ${version ?? ''} is running.`.replace('  ', ' ') : status === 'mismatch' ? (detail ?? 'deskd needs an update.') : status === 'offline' ? 'deskd is not running yet.' : 'Looking for deskd…'}
      </p>
      {error ? (
        <p className="field-error" role="alert">
          {error}{' '}
          <button type="button" className="link" onClick={() => void call('app.revealLogs', {}).catch(() => {})}>
            Reveal logs
          </button>
        </p>
      ) : null}
      <div className="actions">
        {live ? (
          <Button variant="primary" onClick={onNext}>
            Continue
          </Button>
        ) : (
          <Button variant="primary" pending={pending} disabled={status === 'starting'} onClick={() => void start()}>
            Start Desk
          </Button>
        )}
      </div>
    </section>
  );
}

const SOURCE_LABEL: Record<NonNullable<ModelEndpointStatus['source']>, string> = {
  env: 'the DESK_OPENAI_* environment variables',
  file: '~/.config/cliproxyapi.env',
  keychain: 'your Keychain',
};

function TestResult({ result }: { result: ModelEndpointTestResult | null }) {
  if (!result) return null;
  return result.ok ? (
    <p className="status-line">
      <span className="dot ok" aria-hidden="true" />
      {`Connected. ${result.models?.length ?? 0} model${result.models?.length === 1 ? '' : 's'} available.`}
    </p>
  ) : (
    <p className="field-error" role="alert">
      Couldn’t connect: {result.error ?? 'unknown error'}
    </p>
  );
}

function EndpointStep({ onNext }: { onNext(): void }) {
  const [status, setStatus] = useState<ModelEndpointStatus | 'unsupported' | null>(null);
  const [editing, setEditing] = useState(false);
  const [baseUrl, setBaseUrl] = useState('http://127.0.0.1:8317/v1');
  const [apiKey, setApiKey] = useState('');
  const [result, setResult] = useState<ModelEndpointTestResult | null>(null);
  const [pending, setPending] = useState<'test' | 'save' | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    call('config.endpoint', {})
      .then((s) => setStatus(s))
      .catch((err) => {
        if (err instanceof DeskCallError && (err.code === 'unsupported' || err.status === 501)) setStatus('unsupported');
        else setError(describeError(err).message);
      });
  }, []);

  const test = async () => {
    setPending('test');
    setError(null);
    try {
      setResult(await call('config.testEndpoint', {}));
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setPending(null);
    }
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setPending('save');
    setError(null);
    try {
      const saved = await call('config.saveEndpoint', { base_url: baseUrl.trim(), api_key: apiKey });
      setApiKey('');
      setStatus(saved);
      setEditing(false);
      setResult(await call('config.testEndpoint', {}));
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setPending(null);
    }
  };

  const configured = status !== null && status !== 'unsupported' && status.configured;
  return (
    <section className="sheet-body" aria-labelledby="step-title">
      <h1 id="step-title" className="title">
        Connect a model endpoint
      </h1>
      <p className="subtitle">Desk talks to an OpenAI-compatible endpoint, such as a local proxy. The key goes to your macOS Keychain and is never shown again.</p>
      {status === 'unsupported' ? <p className="status-line">This deskd manages its model endpoint itself.</p> : null}
      {configured && !editing ? (
        <>
          <p className="status-line">
            <span className="dot ok" aria-hidden="true" />
            <span>
              Using <span className="mono">{status.base_url}</span> from {status.source ? SOURCE_LABEL[status.source] : 'the daemon'}.
            </span>
          </p>
          <div className="actions">
            <Button size="sm" pending={pending === 'test'} onClick={() => void test()}>
              Test connection
            </Button>
            {status.source === 'keychain' ? (
              <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
                Change key
              </Button>
            ) : null}
          </div>
        </>
      ) : null}
      {status !== null && status !== 'unsupported' && (!configured || editing) ? (
        <form className="sheet-body" onSubmit={save} noValidate>
          <Field id="endpoint-url" label="Base URL">
            <input id="endpoint-url" className="input mono" type="url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
          </Field>
          <Field id="endpoint-key" label="API key" hint="Stored in the Keychain; Desk never displays it.">
            <input id="endpoint-key" className="input mono" type="password" autoComplete="off" spellCheck={false} value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
          </Field>
          <div className="actions">
            <Button type="submit" variant="primary" size="sm" pending={pending === 'save'} disabled={!apiKey || !baseUrl}>
              Save and test
            </Button>
          </div>
        </form>
      ) : null}
      <TestResult result={result} />
      {error ? (
        <p className="field-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="actions">
        <Button variant={configured || status === 'unsupported' ? 'primary' : 'ghost'} onClick={onNext} disabled={status === null && !error}>
          {configured || status === 'unsupported' ? 'Continue' : 'Skip for now'}
        </Button>
      </div>
    </section>
  );
}

function ProjectStep({ onDone }: { onDone(): void }) {
  const count = useGlobal((s) => s.overview.length);
  return (
    <section className="sheet-body" aria-labelledby="step-title">
      <h1 id="step-title" className="title">
        Create your first project
      </h1>
      <p className="subtitle">A project is a goal Desk works toward, with its own threads, library and memory.</p>
      {count > 0 ? (
        <p className="status-line">
          You already have {count} project{count === 1 ? '' : 's'}.{' '}
          <Button variant="ghost" size="sm" onClick={onDone}>
            Skip to the map
          </Button>
        </p>
      ) : null}
      <ProjectForm onCreated={() => onDone()} />
    </section>
  );
}

export function Onboarding() {
  const [step, setStep] = useState<Step>('daemon');
  const index = STEPS.findIndex((s) => s.id === step);
  const finish = () => {
    markOnboarded();
    navigate({ name: 'map' });
  };
  return (
    <div className="onboarding">
      <div className="drag-strip" />
      <div className="card onboarding-card">
        <p className="eyebrow">Welcome to Desk</p>
        <ol className="steps" aria-label="Setup steps">
          {STEPS.map((s, i) => (
            <li key={s.id} className={i < index ? 'done' : undefined} aria-current={i === index ? 'step' : undefined}>
              {s.label}
            </li>
          ))}
        </ol>
        {step === 'daemon' ? <DaemonStep onNext={() => setStep('endpoint')} /> : null}
        {step === 'endpoint' ? <EndpointStep onNext={() => setStep('project')} /> : null}
        {step === 'project' ? <ProjectStep onDone={finish} /> : null}
      </div>
    </div>
  );
}
```

In the endpoint test, the first "Continue" click moves from the daemon step to the endpoint step. The unconfigured endpoint then shows its form. After a successful "Save and test" the status is `configured`, so the button reads "Continue".

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop
git commit -m "feat(desktop): onboarding — daemon, model endpoint (Keychain, test), first project

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: End-to-end smoke (Playwright for Electron) and docs

**Files:**
- Create: `apps/desktop/e2e/smoke.e2e.test.ts`
- Modify: `CLAUDE.md`, `README.md`

**Interfaces:**
- Consumes:
  - `startDaemon` from `@desk/daemon` (a real deskd on a temporary data dir, with `modelConfig` pointing at the fake model)
  - the built app (`pnpm --filter @desk/desktop build`)
  - `DESK_DATA_DIR`, `DESK_USER_DATA`, `DESK_E2E`
  - `__deskTest.trayTitle()`

- [ ] **Step 1: Write the test** — `apps/desktop/e2e/smoke.e2e.test.ts`:

```ts
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
});
```

- [ ] **Step 2: Run it**

Run: `pnpm test:e2e`
Expected: PASS. A Desk window briefly opens on screen.

If `electron.launch` times out, check `DESK_E2E` handling and `app.firstWindow()`. If the renderer is blank, check `desktop.log` under `~/Library/Logs/@desk/desktop`, the protocol handler path (`dist/renderer`), and the CSP.

- [ ] **Step 3: Docs**

`CLAUDE.md`:
- Under Commands, add:

```sh
pnpm desktop      # the Electron app against the repo daemon (Vite HMR)
pnpm test:e2e     # builds the app and runs the Playwright-for-Electron smoke (opens a window)
```

- Under Layout, after `apps/cli`, add:

```
- `apps/desktop`: Electron + React app (spec: `docs/superpowers/specs/2026-09-24-desk-desktop-app-design.md`).
  - `src/main/`: the only deskd client. `broker.ts` (global state, event forwarding), `handlers.ts` (IPC ops, zod-validated in `shared/ipc.ts`), `daemon.ts` (start/LaunchAgent), tray, windows (CSP, `desk-app://`).
  - `src/preload/`: exposes only `window.desk.{invoke,on,platform}`.
  - `src/renderer/`: React UI; talks to main only through `bridge.ts`. Agent text goes through `SafeMarkdown`.
```

- Under Invariants, add:

```
- The desktop renderer never sees the daemon token; every IPC payload is validated in main.
```

`README.md`: add a "Desktop app (development)" section, with `pnpm desktop` and `pnpm test:e2e` and one line each on what they do.

- [ ] **Step 4: Verify everything**

Run: `pnpm test && pnpm typecheck && pnpm test:e2e`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop CLAUDE.md README.md
git commit -m "test(desktop): Playwright-for-Electron smoke — onboard, connect, map, live tray count; docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage (Plan 9 row: "The app launches, onboards, connects, shows the tray count"):**
  - Electron scaffold: Task 2.
  - Broker: Task 4.
  - IPC and preload: Tasks 3 and 7.
  - Daemon lifecycle (§8, and §4.5 for LaunchAgent and version drift): Task 5.
  - Tray and notifications with the hello (§7.11): Tasks 6 and 7.
  - Tokens and core components, SafeMarkdown (§5, §7 Safety): Task 8.
  - Onboarding (§7.10): Task 9.
  - Resilience states: the overlay and banner in Task 8; `StatusChip` wording; the protocol-mismatch block.
  - Error handling (§9): `toIpcError` and `describeError`/`toastError`; 400 errors are shown next to the form (`ProjectForm`).
  - End-to-end: Task 10.
- **Deferred to Plans 10–11 by design:**
  - the map canvas, conversation, threads, attention strips and the tray popover (Plan 10)
  - skills, library, memory, settings, system and ⌘K (Plan 11)
  - `Pending` placeholders keep the routes navigable until then
- **Types:**
  - `ChannelOutput` comes from `handlers`, and the renderer uses a type-only import.
  - `GlobalState` lives in `shared/state.ts` and is shared by main, the tray model and the renderer.
  - `DaemonStatus` starts as a stub in Task 3 and becomes the full module in Task 5, with the same shape.
