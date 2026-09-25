# Angular web UI — design

**Status:** approved by the user on 2026-09-25. Branch `web-ui` (worktree `~/desk-web`). Implementation: Plan 17.

Desk gets a second frontend: an Angular app served to the browser by a new `desk web` server on this computer. It sits alongside the Electron desktop app and reaches full parity with it in phases.

## 0. Decisions (from the user)

| Question | Decision |
|---|---|
| Relationship to the Electron app | **Alongside it.** Both clients stay; they share deskd's API, `@desk/client`, the backend-for-frontend layer and the styles. |
| Who can open it | **This computer only.** Loopback, one-time login link, session cookie. |
| Scope | **Full parity, in phases** (W0–W3, §7). |
| Serving | **A separate `desk web` server** that holds the deskd token and proxies a curated operation set, like the Electron main process. |
| Downloads | Approved: Angular 22 and its build tooling, `marked`, `dompurify`, and Playwright's Chromium for e2e. |

## 1. Architecture

```
browser (Angular app)  ── http://127.0.0.1:<port> ──►  desk web (Node)  ── bearer token ──►  deskd (127.0.0.1)
   DeskBridge.call(op)   POST /rpc/<op>   (session cookie)          @desk/bff handlers           /v1/* REST
   DeskBridge.onPush(ch) WebSocket /push  (session cookie)          @desk/bff broker             /v1/stream WS
```

- **`packages/bff` (`@desk/bff`)**: the backend-for-frontend that today lives in `apps/desktop/src/main` and `apps/desktop/src/shared`. It moves unchanged into a package, and both hosts use it:
  - `broker.ts`: global state (connection, health, overview, attention, system, runtimes), per-sender project watches with batched backfill, event forwarding.
  - `handlers.ts`: the ~80 operations over `DeskClient`, each input validated by its zod schema in `ipc.ts` before anything runs.
  - `ipc.ts` (operation schemas, `IpcResult`), `channels.ts` (push channels), `state.ts` (`GlobalState`), `errors.ts`.
  - It never imports `electron`. Everything host-specific reaches it through `HandlerContext` (`client`, `broker`, `daemon`, `app`).
- **Electron main** keeps windows, tray, menus, notifications, the LaunchAgent and its `HandlerContext` implementation, and imports the rest from `@desk/bff`. Its behaviour does not change.
- **`apps/web/server` (`@desk/web-server`)**: the `desk web` host.
  - A Hono app on Node's HTTP server, bound to `127.0.0.1`.
  - It serves the built Angular app, `POST /rpc/:op`, the `/push` WebSocket, and `/login`.
  - It implements `HandlerContext` for the web (§3), and one broker sender per WebSocket connection.
- **`apps/web/ui` (`@desk/web-ui`)**: the Angular 22 app.
  - Standalone components, signals, zoneless change detection.
  - It is built with `@angular/build` (esbuild) and tested with Vitest through `@angular/build:unit-test`.
- **`packages/ui-core` (`@desk/ui-core`)**: framework-neutral UI logic, moved out of the React renderer and used by both UIs:
  - `router.ts` (Route, `parseRoute`, `href`), `format.ts`, `palette.ts`, `policyReason.ts`;
  - `lineGeometry.ts`, `map/layout.ts`, `skills/skillsMap.ts`, `skills/diff.ts`;
  - `threads/route.ts`, `attention/strips.ts`, `tray/miniLine.ts`;
  - `knowledge/library.ts` and `memory.ts` (pure parts only);
  - the store primitive `createStore` (no React).

  React hooks stay in the React app, and Angular gets signal-based wrappers.
- **`packages/ui-styles` (`@desk/ui-styles`)**: the CSS, moved out of the renderer: `tokens.css` and the per-screen stylesheets (conversation, threads, map, attention, knowledge, settings, skills, system). Both apps import it globally, so class names, and therefore the look, are identical. Angular components use `ViewEncapsulation.None` and the same class names as their React counterparts.

## 2. `desk web`

- **Command:** `desk web [--port <n>] [--no-open]`, in the CLI, which calls `startWebServer()` from `@desk/web-server`. The default port is 4321. If it is taken, `desk web` picks an ephemeral one and prints it.
- **Finding deskd:** `clientFromDataDir()` (`@desk/client/node`) reads `daemon.json`, as the CLI does. deskd is usually started by the Electron app's LaunchAgent or by the CLI.
  - If deskd is down, the UI shows the offline state that `ConnectionOverlay` already has, with a **Start deskd** button.
  - `daemon.start` runs the same start routine the CLI uses (the repo's or the bundled deskd, detached, waiting for `/v1/health`).
- **Lifecycle:** it runs in the foreground; Ctrl-C stops it. It holds nothing durable except `web-settings.json` in the Desk data dir.
- **Routes:**

| Route | Purpose |
|---|---|
| `GET /login?code=<one-time>` | Exchanges the code for a session cookie, then redirects to `/`. Codes are 32 random bytes, single use, valid for 2 minutes. |
| `GET /` and static assets | The Angular build (`apps/web/ui/dist/browser`). Unauthenticated requests get a small "Open Desk from your terminal with `desk web`" page and no app. |
| `POST /rpc/:op` | Runs one `@desk/bff` handler. The body is the operation's input (JSON), validated by `channels[op]`. The response is the `IpcResult` JSON the Electron IPC returns. `Uint8Array` values (only `app.saveFile` today) are tagged `{ "$bytes": base64 }` in both directions. |
| `GET /push` (WebSocket) | One broker sender per connection. Frames are `{ channel, payload }` for the 5 push channels (`desk:global`, `desk:event`, `desk:events`, `desk:ephemeral`, `desk:navigate`). Closing the socket unwatches everything the sender watched. |
| `GET /healthz` | `{ ok: true }` for the e2e harness. It needs no session and returns nothing sensitive. |

## 3. Web versions of the host operations (`HandlerContext` for the web)

| Operation | Electron | Web |
|---|---|---|
| `app.info` | version, platform, packaged, dataDir | the same; `packaged: false` |
| `app.openExternal` | `shell.openExternal` after `safeExternalUrl` | The server still runs `safeExternalUrl` and returns `{ url }`. The client opens it with `window.open(url, '_blank', 'noopener,noreferrer')`. |
| `app.pickFolder` | native dialog | `{ path: null, browse: true }`. The client opens the **folder browser** sheet. |
| — (new) `fs.listDirs` | — | Web only: `{ path? }` → `{ path, parent, dirs: [{ name, path }] }`. It lists directories only (no files, no hidden dirs unless asked), under the user's home or an existing source, and resolves symlinks. |
| `app.revealLogs` | reveal in Finder | Opens the logs folder with the platform opener (`open`, `explorer`, `xdg-open`) on this computer. The System screen also shows the log tail in-app. |
| `app.saveFile` | save dialog | Not called: the client downloads the file (Blob and `<a download>`). |
| `app.openMain` | focus the window | Not called. |
| `app.settings` / `updateSettings` | `{ notifications }` in the app's settings file | The same shape, in `<data>/web-settings.json`. `notifications` means browser notifications, and the browser also asks for permission. |
| `daemon.status/start/restart/stop` | LaunchAgent or bundled deskd | Status from health and `daemon.json`. Start and restart use the CLI's start routine; stop uses `POST /v1/shutdown`, as the CLI does. |
| `daemon.repair` | reinstall the LaunchAgent | Not offered: it returns a clear error, and the UI hides the button. |

`broker.watch`, `unwatch` and `snapshot` map to the connection's sender id.

## 4. Security (this computer only)

The threat model is other web pages open in the user's browser (CSRF, DNS rebinding, clickjacking), other OS users on the same machine (loopback is shared), and agent-written text (XSS).

1. **Bind** only to `127.0.0.1`.
2. **Login.**
   - `desk web` creates a one-time code, prints `http://127.0.0.1:<port>/login?code=…` in the user's terminal, and opens it unless `--no-open` is passed.
   - `/login` sets `desk_session` (32 random bytes): HttpOnly, SameSite=Strict, Path=/, no Max-Age (it lasts the browser session). Sessions live in memory and end when `desk web` stops.
   - Another OS user cannot log in without the code, which only the invoking user sees.
3. **Host check.** Every request, the WebSocket upgrade included, must carry `Host: 127.0.0.1:<port>` or `localhost:<port>`. Anything else gets 421, which blocks DNS rebinding.
4. **Origin check.** Every `POST /rpc` and every `/push` upgrade must carry an `Origin` equal to one of those two origins. Requests without one are refused (403). `/rpc` also requires `content-type: application/json` and the custom header `x-desk: 1`, so a simple cross-site form cannot send it.
5. **The deskd token never leaves the server.** The browser gets only `IpcResult` values, which already hide the token, as they do for the Electron renderer. The `CLAUDE.md` invariant "The desktop renderer never sees the daemon token" extends to the web UI.
6. **Validation.** Every operation's input is parsed by its schema before a handler runs, as in Electron main. Unknown operations get 404.
7. **Headers on every response:**
   - `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`
   - `X-Content-Type-Options: nosniff`
   - `Referrer-Policy: no-referrer`
   - `Cross-Origin-Opener-Policy: same-origin`
   - `Cross-Origin-Resource-Policy: same-origin`

   The Angular build uses `autoCsp` or has no inline scripts. Inline styles are allowed because Angular sets style bindings.
8. **Agent text.** `SafeMarkdownComponent` renders with `marked` (GFM), then sanitises with DOMPurify:
   - an allowlist of tags and attributes, with no raw HTML (the same guarantees as the React `SafeMarkdown` with `skipHtml`);
   - links limited to `http`, `https` and `mailto`, opened through `app.openExternal`;
   - no `bypassSecurityTrust*` anywhere except on DOMPurify's output.

   The React `SafeMarkdown` test cases are ported as they are.
9. **The folder browser** lists directory names only, and only under the user's home or registered sources.

## 5. The Angular app

- **Bootstrap:** `provideZonelessChangeDetection()`. There is no Angular Router: the app keeps the desktop's hash URLs (`#/map`, `#/p/<id>/conversation`, …) through `@desk/ui-core`'s `parseRoute` and `href`, so links and deep links are identical in both UIs. A `RouteService` exposes the current route as a signal.
- **`DeskBridge` service:**
  - `call(op, input)` is `fetch('/rpc/<op>')` with `x-desk: 1`. It returns the value or throws `DeskCallError(code, message, status)`, like the React bridge.
  - `onPush(channel, cb)` runs over one WebSocket that reconnects with backoff. On reconnect it sends `broker.watch` again for every open project session, from that session's cursor.
- **State services,** each mirroring a React state module:
  - `GlobalStore` holds `GlobalState` from `desk:global` pushes.
  - `SessionService` mirrors `renderer/state/session.ts`: acquire and release with the 30-second keep-warm, batched `desk:events`, and a single store update per burst, using the same `@desk/client` reducers (project, chat, timeline, transcript).
  - `LastProject`, `Unread`, `Now` (a 15-second clock) and `Media`.
- **Components:** one per React component, with the same file names in kebab case, under `apps/web/ui/src/app/<area>/`: `components/`, `conversation/`, `threads/`, `map/`, `attention/`, `knowledge/`, `settings/`, `skills/` (and `skills/catalog/`), `system/`, `screens/`.
  - Templates render the same DOM structure and class names, so the shared CSS applies unchanged.
  - SVG parts (line diagram, orbit map, skills map) are Angular SVG templates driven by the shared geometry functions.
- **Keyboard:**
  - ⌘K / Ctrl-K opens the command palette.
  - ⌘P / Ctrl-P opens the project switcher.
  - J/K, ⌘⏎ and ⌘⌫ work in Attention, as in the desktop app.
- **Not ported:**
  - the tray popover, native menus and window management;
  - launch at login and LaunchAgent repair;
  - the desktop's native notification pipeline (replaced by browser notifications, §3).

## 6. Parity inventory

Every React screen and component has an Angular counterpart in the listed phase. S = screen, C = component.

| Area | React (apps/desktop/src/renderer) | Phase |
|---|---|---|
| Shell | App (+ ErrorBoundary per screen), TitleBar, ProjectSwitcher, ProjectNav, ConnectionOverlay, Toast, ConfirmDialog, Sheet, Button, Field, EmptyState, StatusChip, ExternalLink, CodeBlock, SafeMarkdown | W0 |
| Onboarding | Onboarding, ProjectForm, EndpointPanel | W0 |
| Map | MapScreen, MapCanvas, OrbitMap, ProjectList, TerritoryInspector | W1 |
| Conversation | ConversationScreen, ChatItems, Composer, LineDiagram, PlanPanel, ServicesCard (+ LogsSheet), WhatsUp, ToolGroup, ImageThumbs | W1 |
| Attention | AttentionScreen, StripRack, FlightStrip, Inspector | W1 |
| Threads | ThreadsScreen, ThreadRoster, ThreadDetail, RouteView, Transcript, the threads/tabs (result, diff, files, skill drafts, usage), FileViewer | W2 |
| Knowledge | LibraryScreen (upload, preview), MemoryScreen (add, correct, remove) | W2 |
| Settings | SettingsScreen, SettingsFields, PolicyEditor, sources with the folder browser and "Agents can write here" | W2 |
| Skills | SkillsScreen, SkillsMapView, SkillList, SkillPanel, SkillEditor, AskDesk, SkillBadge; history, compare and restore; import (folder browser) | W3 |
| Catalog | CatalogView and review, install and runtime progress | W3 |
| System | SystemScreen, ModelsEditor, EndpointPanel, runtimes cleanup, deskd controls, logs | W3 |
| Global | CommandPalette, keyboard shortcuts, browser notifications | W3 |

**Parity guard.** A unit test in `apps/web/ui` lists every operation in `@desk/bff`'s `channels` and every `Route` name. It fails when:
- an operation is not called anywhere in the web UI and is not on the documented not-called list (`app.saveFile`, `app.openMain`, `daemon.repair`);
- a route has no screen.

## 7. Phases (each ships something usable)

- **W0: foundation.**
  - `@desk/bff`, `@desk/ui-core` and `@desk/ui-styles` are extracted. The Electron app and its unit and e2e tests pass unchanged.
  - `desk web` has login, the Host and Origin checks, headers, `/rpc` and `/push`.
  - The Angular shell: title bar, navigation, the connection overlay with Start deskd, onboarding, and an empty map.
  - The Playwright/Chromium e2e harness, with a smoke test: login, connect, create a project.
- **W1: the core loop.** Map, project switcher, conversation (all parts), attention.
  - e2e: brief Desk, the thread forks on the line diagram, answer Desk's question, approve from Attention, the report arrives. This mirrors `flows.e2e.test.ts`.
- **W2: work and knowledge.** Threads, library, memory, project settings, the folder browser.
  - e2e: thread detail and diff, library upload and preview, add a source.
- **W3: skills and system.** Skills and catalog, system, palette, shortcuts, notifications, and the parity guard green.
  - e2e: install a catalog skill with a fake catalog, the models editor.

## 8. Testing

- **`@desk/bff`**: the existing broker and handler tests move with it.
- **`@desk/web-server`** (Vitest):
  - login codes: single use, expiry;
  - the session cookie's attributes;
  - Host (421) and Origin (403) refusals on `/rpc` and `/push`;
  - a missing `x-desk` header;
  - schema refusals and unknown operations;
  - `/rpc` round trips through a fake `DeskClient`;
  - push fan-out and unwatch on close;
  - `fs.listDirs` confinement;
  - the security headers.
- **`@desk/web-ui`** (Vitest through `@angular/build:unit-test`, jsdom): component tests ported from the React tests, screen by screen, asserting the same visible text and roles, plus the SafeMarkdown XSS cases and the parity guard.
- **e2e** (`pnpm test:web-e2e`): Playwright with Chromium, against a real deskd started in-process (`startDaemon`) with the fake model, then `startWebServer`. The browser logs in through the one-time link. The scenarios are listed in §7.
- `pnpm test` includes the bff, server and web-ui unit tests. `pnpm typecheck` covers the new packages.

## 9. Dev and packaging

- `pnpm web` runs `ng build --watch` for `apps/web/ui` and `desk web --dev`. `--dev` serves `dist/` and reloads on change. It keeps every security check.
- `pnpm --filter @desk/web-ui build` produces `apps/web/ui/dist/browser`, which `desk web` serves.
- Cross-platform: no macOS-only tools. The folder opener is chosen per platform, and paths use `node:path`.
- Bundling `desk web` into the packaged Electron app is out of scope.

## 10. Non-goals

- Access from other devices, HTTPS, multiple users, or accounts.
- Replacing or changing the Electron app.
- Offline and PWA support.
- A tray equivalent.
- Server-side rendering.

## 11. Risks

- **Parity drift.** Every new UI feature, such as the messaging P0 UI, must now land in both UIs. Mitigations:
  - the shared `ui-core`, `ui-styles` and operation contract;
  - the parity guard test;
  - a line in `CLAUDE.md` saying that UI features ship in both apps.
- **Merge timing.**
  - The extraction moves files that another session is editing (`handlers.ts`, `ipc.ts`). The branch rebases onto master when that work lands, and the extraction is redone mechanically if it conflicts.
  - The messaging branch (Plan 16) adds React UI, and its Angular counterparts join the messaging UI slices.
- **Size.** About 8,000 lines of UI to port. It is built in phases, with at most 2 agents at a time on this machine.
