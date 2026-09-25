# Angular web UI — design

**Status:** approved by the user on 2026-09-25; revised after review (§12). Branch `web-ui` (worktree `~/desk-web`). Implementation: Plan 17.

Desk gets a second frontend: an Angular app served to the browser by a new `desk web` server on this computer. It sits alongside the Electron desktop app and reaches full parity with it in phases.

## 0. Decisions (from the user)

| Question | Decision |
|---|---|
| Relationship to the Electron app | **Alongside it.** Both clients stay; they share deskd's API, `@desk/client`, the backend-for-frontend layer and the styles. |
| Who can open it | **This computer only.** Loopback, one-time login link, then a session secret bound to the origin (§4). |
| Scope | **Full parity, in phases** (W0–W3, §7). |
| Serving | **A separate `desk web` server** that holds the deskd token and proxies a curated operation set, like the Electron main process. |
| Downloads | Approved: Angular 22 and its build tooling, `marked`, and Playwright's Chromium for e2e. (DOMPurify is no longer needed, §4.10.) |
| Node and TypeScript | **Open: needs the user before W0.** Angular 22.x requires Node `^22.22.3 \|\| ^24.15.0 \|\| >=26` and TypeScript `>=6.0 <6.1`. This machine runs Node 22.21.0, and the repo's TypeScript 7.0.2 (the native compiler) has no compiler API for `ngc`. Default: upgrade Node to ≥22.22.3, raise the root `engines`, and give `apps/web-ui` its own devDependency `typescript ~6.0.3` (pnpm resolves it per package). If the Node upgrade is declined: Angular 21.2 (Node `^22.12`, TypeScript `>=5.9 <6.0`). Either way the Angular app is type-checked by `ngc`, not by the root `tsc`. |

## 1. Architecture

```
browser (Angular app)  ── http://127.0.0.1:<port> ──►  desk web (Node)  ── bearer token ──►  deskd (127.0.0.1)
   DeskBridge.call(op)   POST /rpc/<op>   (x-desk-session)          @desk/bff handlers           /v1/* REST
   DeskBridge.onPush(ch) WebSocket /push  (secret in 1st frame)     @desk/bff broker             /v1/stream WS
```

- **`packages/bff` (`@desk/bff`)**: the backend-for-frontend that today lives in `apps/desktop/src/main` and `apps/desktop/src/shared`. It moves into a package without behaviour changes, and both hosts use it. It has two entry points:
  - `@desk/bff/contract`, the only part browser code imports (the React renderer, the preload, the Angular app). It never imports `node:*` or relies on Node types: `ipc.ts` (operation schemas, `IpcResult`), `channels.ts` (push channels), `state.ts` (`GlobalState`, `initialGlobalState`, `runtimeKey`), `attention.ts` (`STRIP_CODE`), `safeExternalUrl`, the `DaemonStatus` type, and a type-only `ChannelOutput` that does not pull Node types into a browser build (for example an output map that `handlers` must satisfy).
  - `@desk/bff/server`: `broker.ts` (global state for connection, health, overview, attention, system and runtimes; per-sender project watches with batched backfill; event forwarding), `handlers.ts` (the ~80 operations over `DeskClient`, each input validated by its zod schema before anything runs), `errors.ts`, and `daemon.ts` with `launchd.ts` (`DaemonManager`; Node-only, with exec, spawn and kill injected). Their tests move with them.
  - It never imports `electron`. Everything host-specific reaches it through `HandlerContext` (`client`, `broker`, `daemon`, `app`).
- **Electron main** keeps windows, tray, menus, notifications and its `HandlerContext` implementation, and imports the rest (`DaemonManager` included) from `@desk/bff/server`. Its behaviour does not change.
- **`apps/web-server` (`@desk/web-server`)**: the `desk web` host.
  - A Hono app on Node's HTTP server, bound to `127.0.0.1`.
  - It serves the built Angular app, `POST /rpc/:op`, the `/push` WebSocket, and `/login`.
  - It implements `HandlerContext` for the web (§3), and one broker sender per WebSocket connection.
- **`apps/web-ui` (`@desk/web-ui`)**: the Angular 22 app.
  - Standalone components, signals, zoneless change detection.
  - It is built with `@angular/build` (esbuild) and tested with Vitest through `@angular/build:unit-test`.
  - Its specs are `*.spec.ts`, and the root `tsconfig.json` excludes `apps/web-ui` (as it excludes `apps/desktop`), so root Vitest and root `tsc` never pick it up (§8).
- **`packages/ui-core` (`@desk/ui-core`)**: framework-neutral UI logic, moved out of the React renderer and used by both UIs:
  - the pure half of `router.ts` (Route, `parseRoute`, `href`), `format.ts` (with `plural`), `palette.ts`, `policyReason.ts`, `files.ts` (`asText`, `fileToBase64`, `MAX_UPLOAD`, `imageMime`);
  - `lineGeometry.ts`, `map/layout.ts`, `skills/skillsMap.ts`, `skills/diff.ts`, and `skillKey`/`parseSkillKey` from `skills/data.ts`;
  - `threads/route.ts`, with `toolNames` moved out of `components/ToolGroup.tsx` (React) into ui-core, and `attention/strips.ts`;
  - `knowledge/library.ts` and `memory.ts` (pure parts only);
  - the store primitive `createStore`, split out of `store.ts` (no React).

  React hooks (`useRoute`, `useStore`, `useWidth`, …) stay in the React app, and Angular gets signal-based wrappers. `tray/miniLine.ts` stays too: only the tray uses it.
- **`packages/ui-styles` (`@desk/ui-styles`)**: the CSS, moved out of the renderer: `tokens.css` and the per-screen stylesheets (conversation, threads, map, attention, knowledge, settings, skills, system). Both apps import it globally, so class names, and therefore the look, are identical. The fontsource imports and `tokens.test.ts` move with the CSS. Angular components use `ViewEncapsulation.None` and the same class names as their React counterparts.

## 2. `desk web`

- **Command:** `desk web [--port <n>] [--no-open]`, in the CLI, which calls `startWebServer()` from `@desk/web-server`.
  - The default port is 7434 (deskd's is 7433; common dev servers use neither). A `--port` choice is saved in `web-settings.json` and reused, because the origin, and every piece of browser state keyed to it (the session secret, onboarding, drafts, last project), depends on the port.
  - If the port is taken, `desk web` exits with a message naming `--port`. It never moves to another port on its own.
  - **One instance.** `desk web` writes `<data>/web.json` (pid and port, mode 0600, no secrets). A second `desk web` that finds a live instance says so and exits instead of starting another server. Pressing Enter in the running server's terminal prints, and opens, a fresh login link (for example after the browser's storage was cleared).
- **Finding deskd:** `clientFromDataDir()` (`@desk/client/node`) reads `daemon.json`, as the CLI does. deskd is usually started by the Electron app's LaunchAgent or by the CLI.
  - If deskd is down, the UI shows the offline state that `ConnectionOverlay` already has, with a **Start deskd** button.
  - `daemon.*` use `DaemonManager` from `@desk/bff/server` in a new `web` mode (§3), which starts the repo's deskd, detached, and waits for `/v1/health`.
- **Lifecycle:** it runs in the foreground; Ctrl-C stops it and removes `web.json`. It holds nothing durable except `web-settings.json` and `web.json` in the Desk data dir.
- **Routes:**

| Route | Purpose |
|---|---|
| `GET /login?code=<one-time>` | Redeems the code and returns a small page (`Cache-Control: no-store`) that carries a new session secret in a `<meta>` tag. Its external script `/login.js` stores the secret in `localStorage`, then calls `location.replace('/')`. Codes are 32 random bytes, single use, valid for 2 minutes. |
| `GET /` and static assets | The Angular build (`apps/web-ui/dist/browser`); `/` is sent with `Cache-Control: no-store`. The build holds no secrets, so it is served without a session. Without a valid session secret, the app shows only the "Open Desk from your terminal with `desk web`" state. |
| `POST /rpc/:op` | Runs one `@desk/bff` handler, or a web-only `webChannels` one (§3). It requires `x-desk-session` (§4). The body is the operation's input (JSON), validated by its schema. The response is the `IpcResult` JSON the Electron IPC returns. One codec, shared by the server and DeskBridge, walks the whole value and tags every `Uint8Array` as `{ "$bytes": base64 }` in both directions; input is decoded before validation. Five operations return bytes (`threads.file`, `library.file`, `skills.file`, `skills.versionFile`, `catalog.file`). The request body limit is explicit: 40 MB, since a 25 MB library upload is about 34 MB as base64 JSON. `broker.watch` and `broker.unwatch` are refused here: they run on `/push`. |
| `GET /push` (WebSocket) | The first client frame must be `{ session: <secret> }` within 5 seconds, or the socket closes with 4401. One broker sender per connection. Server frames are `{ channel, payload }` for the 5 push channels (`desk:global`, `desk:event`, `desk:events`, `desk:ephemeral`, `desk:navigate`) and the web-only `desk:notify` (§5). The client sends `{ op, id, input }` for `broker.watch` and `broker.unwatch`, validated by the same `channels` schemas with this socket as the sender. The server answers `{ ack: id, result }` after the backfill frames, on the same socket, which keeps Electron's order (the backfill arrives before `watch` resolves). The client also sends `{ notifyPermission }` on connect and whenever it changes. Closing the socket unwatches everything the sender watched. |
| `GET /healthz` | `{ ok: true }` for the e2e harness. It needs no session and returns nothing sensitive. |

## 3. Web versions of the host operations (`HandlerContext` for the web)

| Operation | Electron | Web |
|---|---|---|
| `app.info` | version, platform, packaged, dataDir | the same; `packaged: false` |
| `app.openExternal` | `shell.openExternal` after `safeExternalUrl` | Handled in the web DeskBridge and never sent to the server: `safeExternalUrl` (from `@desk/bff/contract`), then `window.open(url, '_blank', 'noopener,noreferrer')`, synchronously inside the click that confirmed it (an `await` first would trip Safari's popup blocker). |
| `app.pickFolder` | native dialog | Handled in the web DeskBridge: it opens the **folder browser** sheet and resolves with the chosen path or `null`, the same contract as the native dialog. The sheet also accepts a typed path, which deskd checks (§4.7). |
| — (new) `fs.listDirs` | — | Web only, in a `webChannels` map that the web server validates and dispatches exactly as it does `channels`: `{ path?, hidden? }` → `{ path, parent, dirs: [{ name, path }] }`. It lists directories only, hidden ones only with `hidden: true` (for `~/.claude/skills`), under the user's home or an existing source. It resolves symlinks and never lists the Desk data dir. |
| `app.revealLogs` | reveal in Finder | Opens the logs folder with the platform opener (`open`, `explorer`, `xdg-open`) on this computer. |
| `app.saveFile` | save dialog | Handled in the web DeskBridge: a Blob of type `application/octet-stream` and `<a download>`, revoked right after the click. |
| `app.openMain` | focus the window | Not called. |
| `app.settings` / `updateSettings` | `{ notifications }` in the app's settings file | The same shape, in `<data>/web-settings.json`. `notifications` means browser notifications (§5), and the browser also asks for permission. |
| `daemon.status/start/restart/stop` | LaunchAgent or bundled deskd | `DaemonManager` in a new `web` mode. Status comes from health and `daemon.json`. When the Electron app's LaunchAgent is installed (macOS), start and restart go through `launchctl` (bootstrap, or `kickstart -k`, of the existing plist) and stop is `launchctl bootout`, because `KeepAlive` would undo a signal. The web host never writes or removes the plist. Otherwise start spawns the repo's deskd through tsx, as dev mode does, and stop signals the pid in `daemon.json`. deskd has no shutdown route. |
| `daemon.repair` | reinstall the LaunchAgent | Not offered: it returns a clear error, and the UI hides the button. |

The web `HandlerContext` answers `app.openExternal`, `app.pickFolder`, `app.saveFile` and `app.openMain` with a clear "not offered" error, since the web UI never sends them. `broker.snapshot` needs no sender and runs on `/rpc`. `broker.watch` and `unwatch` run on `/push` (§2), so their sender is always the socket they arrived on, and the client never names one.

## 4. Security (this computer only)

The threat model is other web pages open in the user's browser (CSRF, DNS rebinding, clickjacking), other servers on `127.0.0.1` that the browser visits (cookies are not isolated by port), other OS users on the same machine (loopback is shared), agent processes (sandboxed, same OS user), and agent-written text and files (XSS).

1. **Bind** only to `127.0.0.1`.
2. **Login and session.**
   - `desk web` creates a one-time code and prints `http://127.0.0.1:<port>/login?code=…` in the user's terminal. Unless `--no-open` is passed, it opens the link without putting the code on a command line (on Linux `/proc/<pid>/cmdline` is world-readable): it writes a 0600 `<data>/web-login-<random>.html` that redirects to the link, opens that file, and deletes it once the code is redeemed or expires.
   - `/login` answers with a session secret (32 random bytes), which its page stores in `localStorage`. `localStorage` is per origin (scheme, host and port), so only pages that desk web serves on this port can read it, and it survives browser restarts. Sessions live in memory and end when `desk web` stops.
   - There is no cookie. Cookies are not isolated by port, and SameSite counts every `127.0.0.1:<port>` as same-site, so a cookie would reach every local server the browser visits, including agent-run project services opened from the Services card.
   - Every `/rpc` call carries `x-desk-session`, and `/push` takes the secret in its first frame. The server compares it in constant time. Without a valid secret, `/rpc` returns 401 and `/push` closes with 4401. DeskBridge then drops the stored secret and shows the "Open Desk from your terminal" state.
   - A spent code presented again revokes the session it created and prints a warning in the terminal. Another OS user cannot log in without the code.
3. **Host check.** Every request, the WebSocket upgrade included, must carry `Host: 127.0.0.1:<port>`. Anything else gets 421, which blocks DNS rebinding. That includes `localhost`: it can resolve to `::1`, where another process may hold the same port. desk web always prints and opens `127.0.0.1`.
4. **Origin check.** Every `POST /rpc` and every `/push` upgrade must carry `Origin: http://127.0.0.1:<port>`. Requests without it are refused (403). `/rpc` also requires `content-type: application/json`, and its `x-desk-session` header already rules out a simple cross-site request.
5. **The deskd token never leaves the server.** The browser gets only `IpcResult` values, which already hide the token, as they do for the Electron renderer. The `CLAUDE.md` invariant "The desktop renderer never sees the daemon token" extends to the web UI.
6. **Agents cannot reach either server.** Today a sandboxed command can read `daemon.json` and call deskd over loopback, because the profile only denies writes. It could therefore approve its own agent's unsandboxed request. W0 closes this in `packages/core/src/tools/sandbox.ts` before `desk web` ships:
   - `SandboxSpec` gains `dataDir` and the two ports. deskd reads desk web's port from `web.json` when it builds a profile.
   - After `(allow default)`, the profile adds `(deny file-read* (literal "<data>/daemon.json"))` and `(deny network-outbound (remote ip "localhost:<deskd port>") (remote ip "localhost:<web port>"))`. The network deny is the real control: it also covers a session secret read from the browser's profile on disk.
   - Sandbox tests: `cat daemon.json` and `curl 127.0.0.1:<port>/v1/health` both fail inside the sandbox.
7. **Sources cannot cover Desk's own data.** `projects.addSource` takes any path and makes it agent-writable by default, so the folder browser's confinement is a convenience, not a boundary. deskd enforces it instead, which covers Electron too:
   - `addSource` resolves the realpath and refuses the filesystem root, the home directory, the data dir, and any path inside or containing the data dir (`ValidationError`).
   - `writeRoots` skips existing sources that match, so older projects are covered too.
8. **Validation.** Every operation's input is parsed by its schema (`channels` or `webChannels`) before a handler runs, as in Electron main. Unknown operations get 404.
9. **Headers on every response:**
   - `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' ws://127.0.0.1:<port>; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'` (Electron's `PROD_CSP`, plus `frame-ancestors` and the explicit socket origin)
   - `X-Content-Type-Options: nosniff`
   - `Referrer-Policy: no-referrer`
   - `Cross-Origin-Opener-Policy: same-origin`
   - `Cross-Origin-Resource-Policy: same-origin`

   The Angular build has no inline scripts or event-handler attributes (§9):
   - Critical-CSS inlining is off, because it loads the global stylesheet through an inline `onload` handler, which `script-src 'self'` blocks, leaving the app unstyled.
   - `autoCsp` is off, because its `<meta>` hashes cannot loosen the header.
   - A web-server test fails if the built `index.html` has an inline `<script>` body or an `on*=` attribute.

   Inline styles are allowed because Angular injects component styles and sets style bindings.
10. **Agent text.** `SafeMarkdownComponent` never uses `innerHTML`. It tokenises with `marked.lexer(text, { gfm: true })` and renders the tokens with recursive Angular templates, so all text goes through interpolation.
    - `html` tokens are dropped, as the React `skipHtml` does.
    - Links become `ExternalLinkComponent` (a confirm dialog; `http`, `https` and `mailto` only; opened as in §3). Images become an "Image: alt" external link. Fenced code becomes `CodeBlockComponent`.
    - A test fails if `[innerHTML]` or `bypassSecurityTrust` appears anywhere under `apps/web-ui/src`.

    The React `SafeMarkdown` test cases are ported as they are.
11. **Agent files.** Unlike Electron, a browser lets the user open an image in a new tab, and a `blob:` URL document has desk web's origin. So the web `FileViewer` shows SVG as source text, never as an image. Image Blobs use raster types only (`png`, `jpeg`, `gif`, `webp`). Download Blobs are `application/octet-stream` and are revoked right after the click.
12. **The folder browser** lists directory names only, only under the user's home or registered sources, and never the data dir. A typed path is checked by deskd (item 7).

## 5. The Angular app

- **Bootstrap:** `provideZonelessChangeDetection()`. There is no Angular Router: the app keeps the desktop's hash URLs (`#/map`, `#/p/<id>/conversation`, …) through `@desk/ui-core`'s `parseRoute` and `href`, so links and deep links are identical in both UIs. A `RouteService` exposes the current route as a signal.
- **`DeskBridge` service:**
  - `call(op, input)` is `fetch('/rpc/<op>')` with `x-desk-session`. It returns the value or throws `DeskCallError(code, message, status)`, like the React bridge. The host operations of §3 are handled locally, and `broker.watch` and `unwatch` go over the socket and resolve on their `ack`.
  - `onPush(channel, cb)` runs over one WebSocket whose first frame is the session secret, and which reconnects with backoff. A reconnect is a new sender, so it sends `broker.watch` again for every open project session, from that session's cursor.
- **State services,** each mirroring a React state module:
  - `GlobalStore` holds `GlobalState` from `desk:global` pushes.
  - `SessionService` mirrors `renderer/state/session.ts`: acquire and release with the 30-second keep-warm, batched `desk:events`, and a single store update per burst, using the same `@desk/client` reducers (project, chat, timeline, transcript).
  - `LastProject`, `Unread`, `Now` (a 15-second clock), `Media`, and a ResizeObserver width helper for `state/width.ts`.
- **Notifications:**
  - The web broker's stream hello sets `notifications` only while the setting is on and at least one connected `/push` socket reports `Notification.permission === 'granted'`. It calls `broker.updateHello()` whenever that count crosses zero. Otherwise deskd keeps posting its own notifications, since it suppresses them only while a client claims them.
  - `onAttentionAdded` sends the new items on the web-only `desk:notify` push channel. The client shows them unless the page has focus, as Electron skips a focused window, with the item id as the notification `tag` so that several tabs show one notification.
- **Components:** one per React component, with the same file names in kebab case, under `apps/web-ui/src/app/<area>/`: `components/`, `conversation/`, `threads/`, `map/`, `attention/`, `knowledge/`, `settings/`, `skills/` (and `skills/catalog/`), `system/`, `screens/`.
  - Templates render the same DOM structure and class names. Components never add a wrapper element: they use attribute selectors on the native root element (for example `header[deskTitleBar]`), so the flex layouts (`.app` is a flex column of the header, nav and `main.screen`) and structural selectors (`>`, `:last-child`) in the shared CSS apply unchanged.
  - SVG parts (line diagram, orbit map, skills map) are Angular SVG templates driven by the shared geometry functions.
- **Keyboard:**
  - ⌘K / Ctrl-K opens the command palette.
  - ⌘P / Ctrl-P opens the project switcher.
  - J/K, ⌘⏎ and ⌘⌫ work in Attention, as in the desktop app.
- **Not ported:**
  - the tray popover, native menus and window management;
  - launch at login and LaunchAgent repair;
  - the desktop's native notification pipeline (replaced by browser notifications, above).

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

**Parity guard.** A unit test in `apps/web-ui` scans the non-test sources of both UIs for literal operation names. It fails when:
- an operation the React renderer calls is not called by the web UI, unless it is on the documented host-only list (`app.openMain`, which only the tray calls, and `daemon.repair`);
- a `webChannels` operation is not called by the web UI;
- a `Route` name other than `tray` has no screen.

Dynamic calls such as ``call(`daemon.${what}`)`` in `SystemScreen` become literal names in both UIs, so the scan sees them. Operations that neither UI calls (`health`, `projects.list`, …) are not the guard's concern.

## 7. Phases (each ships something usable)

- **W0: foundation.**
  - Before any code: the Node and TypeScript decision (§0), and a spike that `@angular/build` compiles the TypeScript-source workspace packages. If it reports them "missing from the TypeScript compilation", `apps/web-ui`'s tsconfig adds `paths` to their `src` entries.
  - The sandbox and `addSource` changes in deskd (§4.6, §4.7), with their tests, land before `desk web` does.
  - `@desk/bff`, `@desk/ui-core` and `@desk/ui-styles` are extracted. The Electron app and its unit and e2e tests pass unchanged.
  - `desk web` has login, the Host and Origin checks, headers, `/rpc` and `/push`.
  - The Angular shell: title bar, navigation, the connection overlay with Start deskd, onboarding with the folder browser (the project form picks a folder), and an empty map.
  - The Playwright/Chromium e2e harness, with a smoke test: login, connect, create a project.
- **W1: the core loop.** Map, project switcher, conversation (all parts), attention.
  - e2e: brief Desk, the thread forks on the line diagram, answer Desk's question, approve from Attention, the report arrives. This mirrors `flows.e2e.test.ts`.
- **W2: work and knowledge.** Threads, library, memory, and project settings (sources use the W0 folder browser).
  - e2e: thread detail and diff, library upload and preview, add a source.
- **W3: skills and system.** Skills and catalog, system, palette, shortcuts, notifications, and the parity guard green.
  - e2e: install a catalog skill with a fake catalog, the models editor.

## 8. Testing

- **`@desk/bff`**: the existing broker and handler tests move with it.
- **`@desk/core`**: the sandbox tests of §4.6, and `addSource` refusals (root, home, data dir, inside or containing it).
- **`@desk/web-server`** (Vitest):
  - login codes: single use, expiry, and a replayed code revoking its session;
  - no `Set-Cookie` anywhere; a missing or wrong session secret gets 401 on `/rpc` and 4401 on `/push`;
  - Host (421, `localhost` included) and Origin (403) refusals on `/rpc` and `/push`;
  - schema refusals and unknown operations, in `channels` and `webChannels`;
  - `/rpc` round trips through a fake `DeskClient`, including `$bytes` round trips for the five byte-returning operations, and the body limit;
  - push fan-out; `broker.watch` over `/push` with the ack after the backfill; unwatch on close; a reconnect watching again as a new sender;
  - the stream hello claiming notifications only while a socket reports permission;
  - `fs.listDirs` confinement (home and sources, hidden dirs, never the data dir);
  - the security headers, and the built `index.html` without inline scripts or `on*=` attributes.
- **`@desk/web-ui`** (Vitest through `@angular/build:unit-test`, jsdom, `*.spec.ts`): component tests ported from the React tests, screen by screen, asserting the same visible text and roles, plus the SafeMarkdown XSS cases, the no-`innerHTML` check and the parity guard.
- **e2e** (`pnpm test:web-e2e`, which builds the UI first and has its own `vitest.web-e2e.config.ts`, since `vitest.e2e.config.ts` only includes `apps/desktop/e2e`): Playwright with Chromium, against a real deskd started in-process (`startDaemon`) with the fake model, then `startWebServer`. The browser logs in through the one-time link. The scenarios are listed in §7.
- `apps/web-server` and `apps/web-ui` sit one level under `apps/`, so the existing `apps/*` globs in `pnpm-workspace.yaml`, `vitest.config.ts` and `tsconfig.json` pick up the server; the root `tsconfig.json` excludes `apps/web-ui`.
- `pnpm test` becomes `vitest run && pnpm --filter @desk/web-ui test` (`ng test --watch=false`); root Vitest covers bff, ui-core and the server. `pnpm typecheck` adds `ngc -p apps/web-ui/tsconfig.app.json --noEmit` (the TypeScript of §0).

## 9. Dev and packaging

- `pnpm web` runs `ng build --watch` for `apps/web-ui` and `desk web --dev`. `--dev` serves `dist/` and reloads on change through an external `/__dev/reload.js` over a same-origin socket, never an inline script. It keeps every security check.
- The production configuration in `angular.json` sets `"optimization": { "scripts": true, "styles": { "minify": true, "inlineCritical": false }, "fonts": false }` and does not enable `security.autoCsp` (§4.9).
- `pnpm --filter @desk/web-ui build` produces `apps/web-ui/dist/browser`, which `desk web` serves.
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

## 12. Review notes

Reviewers' gaps (2026-09-25), each checked against the code:

1. **The session cookie reaches every `127.0.0.1` server (critical).** Fixed: no cookie. A per-origin secret lives in `localStorage` and is sent as `x-desk-session` and as the first `/push` frame (§4.2).
2. **Sandboxed agents can read `daemon.json` and reach deskd (critical, pre-existing).** Fixed: W0 prerequisite. The profile denies reading `daemon.json` and connecting to the deskd and desk web ports, with tests (§4.6).
3. **Folder-browser confinement is no boundary; `addSource` accepts `~` or the data dir.** Fixed in deskd: `addSource` refuses root, home and anything inside or containing the data dir, and `writeRoots` skips such sources (§4.7). A web-only `skills.import` confinement was not added, because import only reads and the session holder is the user.
4. **DOMPurify and `innerHTML` cannot match the React `SafeMarkdown`.** Fixed: `marked.lexer` tokens are rendered by Angular templates, with no `innerHTML`, plus a lint test (§4.10).
5. **`script-src 'self'` breaks Angular's critical-CSS `onload`, and `autoCsp` cannot help.** Fixed: `inlineCritical: false`, no `autoCsp`, an index.html test, an external dev reload, and the CSP aligned with Electron's (§4.9, §9).
6. **Nothing ties `/rpc` `broker.watch` to a socket.** Fixed: watch and unwatch travel on `/push` as request and ack frames (§2, §3).
7. **The `localhost` allowlist and default port 4321.** Fixed: only `127.0.0.1:<port>` is accepted, and the default port is 7434 (§2, §4.3). The optional `[::1]` placeholder bind was not adopted, because nothing uses `localhost` any more.
8. **The login code appears in the opener's argv.** Fixed: a 0600 redirect file is opened instead, and a replayed code revokes its session (§4.2).
9. **`blob:` image documents, including SVG, run with desk web's origin.** Fixed: SVG shows as text, image Blobs are raster only, and downloads are `application/octet-stream` and revoked (§4.11).
10. **There is no `/v1/shutdown`, and a SIGTERM is undone by `KeepAlive`.** Fixed: `DaemonManager` moves to `@desk/bff/server` with a `web` mode that uses `launchctl` when the plist exists (§3).
11. **The shared handlers cannot return the web shapes, and `fs.listDirs` is outside `channels`.** Fixed differently from the suggestion: the bff contract stays unchanged, the web DeskBridge handles host operations, and `fs.listDirs` sits in a validated `webChannels` map (§3).
12. **No way to log in again; a second instance clobbers the first; 401s unspecified.** Fixed: the `localStorage` secret survives browser restarts, Enter prints a new link, `web.json` refuses a second instance, `/` is `no-store`, and 401/4401 are specified (§2, §4.2).
13. **Angular 22 needs Node ≥22.22.3 and TypeScript 6.0; the machine has Node 22.21.0 and the repo has TypeScript 7 (critical).** Fixed as a blocking decision in §0, which needs the user before W0. Angular 21.2 is the fallback. Its peer range is TypeScript `>=5.9 <6.0`, not `<6.1` as the review said.
14. **`apps/web/*` is outside the workspace, Vitest and tsconfig globs.** Fixed: flattened to `apps/web-server` and `apps/web-ui`, with the test and typecheck scripts chained (§1, §8).
15. **The CLI start and stop routine is misdescribed; `DaemonStatus` lives in `daemon.ts`.** Fixed together with 10. The `DaemonStatus` type moves to the contract (§1).
16. **`watch` ordering: backfill before resolve.** Fixed together with 6: the ack follows the backfill on the same socket.
17. **Five operations return `Uint8Array`, not just `app.saveFile`.** Fixed: one whole-value codec, decoded before validation, round-trip tests and a 40 MB body limit (§2).
18. **Host-operation shapes, Safari's popup blocking, the folder browser needed in W0, no log-tail operation.** Fixed: operations are handled in DeskBridge with a synchronous `window.open`, the folder browser moves to W0, and the in-app log-tail claim is dropped (§3, §7).
19. **The `SafeMarkdown` tests cannot pass with `innerHTML`.** Fixed together with 4.
20. **CSP versus the production build and dev reload.** Fixed together with 5.
21. **Extraction boundaries: Node imports reach the browser, ui-core modules import React, and host elements break the CSS.** Fixed: the `@desk/bff/contract` and `/server` subpaths, a corrected ui-core list, attribute-selector components, and a W0 build spike (§1, §5, §7).
22. **The web hello silences deskd's notifications, and browsers never learn of new attention.** Fixed: the hello claims notifications only while a granted socket is connected, and the `desk:notify` channel respects focus (§5).
23. **The parity guard can never pass.** Fixed: it checks parity with the React renderer's calls, uses literal operation names, and exempts `tray` (§6).
24. **Port fallback changes the origin; re-login; cookies not port-isolated.** Fixed together with 1, 7 and 12: the port is saved and a taken port is an error (§2).
