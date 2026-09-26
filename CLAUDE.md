# Desk — notes for coding agents

Desk is a local-first macOS daemon (`deskd`), a CLI (`desk`) and an Electron desktop app (`apps/desktop`, see `docs/desktop.md`). A per-project coordinator agent ("Desk") dispatches parallel worker agents ("threads"). It also manages shared memory, a library, and skills (instructions plus scripts). Every client goes through the API in `docs/api.md`.

- **Design:** `docs/superpowers/specs/2026-09-23-desk-daemon-design.md`. §14 lists deviations from the original design.
- **Plans:** `docs/superpowers/plans/`.
- **API:** `docs/api.md`.

## Commands

```sh
pnpm test         # all unit + integration tests, then the web UI's specs (ng test); must pass before any commit
pnpm typecheck    # tsc --noEmit, then ngc for apps/web-ui; must pass before any commit
pnpm test:live    # live smokes against the local model proxy (slow; DESK_LIVE=1)
bin/desk …        # CLI (tsx loader, no build step)
bin/desk web      # the web UI on http://127.0.0.1:7434: prints a one-time login link (Enter prints another); --port, --no-open, --dev
pnpm --filter @desk/daemon bundle   # esbuild bundle → apps/daemon/dist/deskd.mjs (+ migrations, better-sqlite3 prebuilds)
pnpm desktop      # the Electron app against the repo daemon (Vite HMR)
pnpm web          # the web UI while you work on it: ng build --watch plus desk web --dev (reloads the page); extra args go to desk web
pnpm test:e2e     # builds the app and runs the Playwright-for-Electron suite (opens windows; set DESK_E2E_SHOTS=<dir> for screenshots)
pnpm package:desktop   # unsigned (ad hoc) Desk.app → apps/desktop/release/*.dmg + .zip with the bundled deskd and pinned uv; enables e2e/packaged
pnpm catalog:pin [ids]    # re-pin catalog.json entries: commit SHAs, digests, script counts, Node locks (lock_from)
pnpm catalog:check [ids]  # install each catalog entry for real (uv on PATH or DESK_UV), build its runtime, run its smoke command sandboxed
```

There is no build step: TypeScript runs through the `tsx` loader, and packages export `src/*.ts` directly. The desktop app is the exception: esbuild bundles its main and preload, and Vite builds its renderer. So is the web UI: `apps/web-ui` is built by the Angular CLI with its own TypeScript 6 (ngc), and every `ng`/`ngc` command goes through `scripts/ng.mjs`, which runs it under a Node Angular accepts (`^22.22.3 || ^24.15.0 || >=26`, found in nvm when the current Node is older; `node scripts/ng.mjs --which` prints it).

## Layout

- `packages/protocol`: zod schemas shared by everything.
  - `events.ts`: the event union.
  - `settings.ts`: project settings and the default policy.
  - `api.ts`: HTTP bodies.
  - `domain.ts`
- `packages/client`: `@desk/client`, the environment-neutral client used by the CLI and the desktop app.
  - `client.ts`: typed REST (`DeskClient`); `stream.ts`: `DeskStream` (resume, dedupe, reconnect).
  - `state/*`: pure reducers (event → UI state): project, chat, transcript, timeline, system; attention and skill-graph helpers.
  - `node.ts` (`@desk/client/node`): data-dir discovery and `daemon.json`. Keep `node:*` imports out of every other file.
- `packages/bff`: `@desk/bff`, the backend-for-frontend every UI host runs (Electron main, and `desk web` from Plan 17).
  - `@desk/bff/contract` (`src/contract/`): what browser code may import. `ipc.ts` (every operation's zod schema), `channels.ts` (push channels), `state.ts` (`GlobalState`), `attention.ts` (`STRIP_CODE`), `safeExternalUrl`, and `types.ts` (`DaemonStatus`, and `ChannelOutput`, each operation's declared result, which `handlers` must satisfy). No `node:*` imports or Node types (`boundaries.test.ts`).
  - `@desk/bff/server` (`src/server/`): `Broker`, `handlers` and `dispatch` (each input validated by its schema first), `toIpcError`, `DaemonManager` and `launchd.ts`. Node only, never Electron; a host supplies a `HandlerContext`.
- `packages/ui-core`: `@desk/ui-core`, UI logic with no framework, shared by both UIs: routes (`parseRoute`, `href`), formatting, palette ranking, the line diagram, map and skills-map geometry, skill keys and diffs, thread routes, attention strips, library and memory views, chat row views, pair sheets, waits, and `createStore`. No React, Angular, DOM or Node (`boundaries.test.ts`).
- `packages/ui-styles`: `@desk/ui-styles`, the CSS both UIs load: `tokens.css` and one sheet per screen, loaded with the fonts by `index.css` in the renderer's cascade order. Class names are shared, so the look is too.
- `packages/core`: the runtime.
  - `agent/run.ts`: the agent loop.
  - `agent/prompts.ts`: the role prompts.
  - `agent/transcript.ts`: events → messages, including checkpoints.
  - `agent/compaction.ts`
  - `runtime/runtime.ts`: the `Runtime` facade and `RuntimeServices` used by tools.
  - `runtime/toolsets.ts`: which tools each role gets.
  - `tools/*`: one module per tool family.
  - `policy/`, `skills/store.ts`, `memory/`, `library/`, `workspaces/`.
  - `events/`: the store and projections.
  - `db/`: the drizzle schema and migrations.
  - `state/attention.ts`, `state/overview.ts`: what needs the user, and the cross-project summary (desktop app).
  - `workspaces/inspect.ts`: thread diff and confined workspace browsing.
  - `services/manager.ts`: project services' processes (spawn, capped logs, loopback URL detection, orphan reaping); `tools/services.ts` holds the agent tools.
  - `attachments/`: images agents looked at, content-addressed in `<data>/attachments` (`tools/vision.ts` is `view_image`; `agent/transcript.ts` shows the 8 most recent as pixels).
  - `catalog/`: the skill catalog. `catalog.json` holds the 20 pinned entries; `service.ts` fetches, verifies, stages and installs them; `runtimes.ts` builds Desk-managed environments (uv Python, npm lock, node shim, compat shims); `tar.ts`, `digest.ts`, `review.ts`; `curation.ts` holds helpers for `scripts/catalog.ts`.
  - `model/endpoint.ts`, `model/switchable.ts`: endpoint resolution (env → file → Keychain) and a runtime-configurable adapter.
  - `testing/`: the harness, exported as `@desk/core/testing`.
- `apps/daemon`: Hono app (`app.ts`, `routes/*`), WebSocket stream, daemon lifecycle, `notifier.ts` (macOS notifications), `scripts/bundle.mjs`.
- `apps/cli`: commander CLI over `DeskClient`.
- `apps/web-server`: `@desk/web-server`, the `desk web` host (spec `2026-09-25-angular-web-ui-design.md`). Hono on 127.0.0.1 only: the Angular build (`apps/web-ui/dist/browser`), `/login` (one-time codes; session secrets in memory, sent as `x-desk-session` and as `/push`'s first frame; no cookie), `POST /rpc/:op` (the `@desk/bff` handlers plus the web-only `webChannels`, with the `$bytes` codec), `/push` (`PushHub`: one broker sender per socket, watch acks after the backfill, `desk:notify`), the web `HandlerContext` (`web-context.ts`), and `web.json`, `web-settings.json` and `web-login-*.html` in the data dir. `@desk/web-server/contract` is the browser-safe part the Angular app imports.
- `apps/desktop`: Electron + React app (spec: `docs/superpowers/specs/2026-09-24-desk-desktop-app-design.md`).
  - `src/main/`: the only deskd client, through `@desk/bff/server` (`Broker`, `dispatch`, `DaemonManager`); it keeps the Electron `HandlerContext`, tray, notifications, menus and windows (CSP, `desk-app://`).
  - `src/preload/`: exposes only `window.desk.{invoke,on,platform}`.
  - `src/renderer/`: React UI; talks to main only through `bridge.ts`. Agent text goes through `SafeMarkdown`. The logic it shares with the web UI lives in `@desk/ui-core`, its CSS in `@desk/ui-styles` (`main.tsx` loads it once; only `tray/tray.css` stays).
- `apps/web-ui`: `@desk/web-ui`, the Angular 22 web UI that `desk web` serves from `dist/browser` (zoneless, standalone components, signals, OnPush, no Router: the desktop's hash routes through `@desk/ui-core`).
  - `src/app/core/`: `DeskBridge`, the only way to desk web (`/rpc` with the session secret, the `/push` socket, and the host operations done in the browser), and one service per renderer state module (`GlobalStore`, `SessionService`, `RouteService`, `LastProject`, `Unread`, …), plus `WebNotifications`.
  - `src/app/components/` and one folder per place, as in the renderer: one component per React component, same file name in kebab case, same DOM and class names, the attribute selector on the React root element (`header[deskTitleBar]`), inputs named like the React props.
  - `src/app/app.ts`: the shell. `src/app/screen-for.ts`: the component each route shows (`NotYet` until its phase lands).
  - `src/app/testing/fake-bridge.ts`: `FakeDeskBridge` and `provideGlobal` for specs.
- `test/fake-model`: a scriptable OpenAI-compatible server. Every non-live test talks to it.
- `catalog/skills/`: Desk's first-party catalog skills (`word-documents`, `pdf-toolkit`). After editing them, run `pnpm catalog:pin word-documents pdf-toolkit`; `catalog.test.ts` fails when a digest is stale.

## Conventions

- **Event sourcing.**
  - State changes are events appended through `EventStore.append`. Projections in `events/projections.ts` update tables in the same transaction.
  - A new event means adding it to `protocol/src/events.ts`, then projecting it if it has table state.
  - Never write projection tables directly.
- **Schema changes.** Edit `db/schema.ts`, then add an additive migration (the packaged app has live databases, so never squash or edit existing ones):
  ```sh
  cd packages/core && npx drizzle-kit generate --name <what_changed>
  ```
- **Tools.**
  - Define tools with `defineTool` (zod input); throwing returns an error result to the model.
  - Tools that touch the outside world declare a `gate` so the policy decides auto/allow/ask/deny.
  - Tools reach runtime features only through `ctx.services`.
- **Errors.** Runtime methods throw `NotFoundError`, `ConflictError` or `ValidationError` from `core/errors.ts`, and the daemon maps them to 404, 409 or 400.
- **Tests.**
  - Use Vitest with the harness (`createHarness`, `newRuntime`, `seedThread`) and fake-model scripts. Route by system prompt, or by the number of assistant messages in the request, rather than by call order when agents run concurrently.
  - Sandbox tests skip when `sandbox-exec` is unavailable.
  - Live tests are `*.live.test.ts`.
  - Web UI specs are `apps/web-ui/src/**/*.spec.ts` (Vitest through `@angular/build:unit-test`, jsdom), written with `@testing-library/angular` and `FakeDeskBridge`; they need no daemon or fake model. A port of a React test keeps its cases, visible text and roles. Run one with `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include <spec>)`.
- **Secrets.** The model API key comes from `DESK_OPENAI_*`, `~/.config/cliproxyapi.env`, or the macOS Keychain (written by `PUT /v1/config/model-endpoint` via `security -i` on stdin, never argv). It must never reach logs, events, `daemon.json`, `config.json`, API responses or tool environments; `scrubbedEnv` builds tool envs.
- **Two UIs.** From Plan 17 on, a UI feature ships in the Electron renderer and in the web UI together. Put its logic in `@desk/ui-core`, its styles in `@desk/ui-styles` and any new operation in `@desk/bff/contract`, so the two UIs only differ in their components.
- **Style.** Strict TS with `noUncheckedIndexedAccess`, ESM, and short doc comments on non-obvious exports. Match the surrounding code.

## Invariants worth protecting

- Crash recovery never re-executes a tool call whose outcome is unknown; it records `interrupted` instead.
- An agent with pending approvals is never scheduled.
- Graceful shutdown leaves agents `queued`, not `cancelled`.
- Shell tools (`bash`, `bash_background`, `bash_readonly`, `skill_run`, `service_start`) never run unsandboxed without approval.
- Agents write only to their workspace, temp dirs, and project sources with `agent_write` (on by default; the user can turn it off per source). Project services run in a thread workspace or such a source (sandboxed, no secrets), outlive their thread, and are stopped on archive and shutdown; their output goes to log files, never to events.
- Agents' commands and file tools never read deskd's token file, its database, the model credentials file or desk web's one-time login files (`<data>/web-login-*.html`, a `SandboxGuard.secretPatterns` entry), never write Desk's data dir outside their own workspace (even through a source that contains it) or the git and ssh config deskd's git runs with, never connect to deskd's port or to desk web's (named in `<data>/web.json`, which the profile reads each time it is built, so a command or service started before desk web took its port can reach it until it restarts; desk web still asks it for a session), and never run `/usr/bin/security` (`SandboxGuard` in `tools/sandbox.ts`, applied by the sandbox profile and `resolveForTool`). A project source is never the filesystem root, the home folder or above, or Desk's data dir or anything inside or around it (`unsafeSource`; older ones are never writable). deskd reads and writes files agents control only through `tools/agent-files.ts` (no symlink at the end, regular files only, the opened file checked, secrets refused by identity), so a symlink swapped in after a path check cannot redirect it. deskd runs git with hooks, fsmonitor, external diff and textconv off and without model credentials (`safeGitArgs`, `gitEnv`), agents cannot change the config, hooks or `.git` links of the repos deskd runs git in (`SandboxSpec.gitDirs`), and file tools never write inside `.git`. macOS lets any same-user process read another's environment and the sandbox cannot stop it, so Desk never puts a secret in an environment variable it sets (`DESK_OPENAI_*` is for development).
- Threads cannot modify installed skills. They submit drafts, which Desk installs with `skill_write from_dir`.
- Only the user installs catalog skills; every install is checked against its pinned digest, and `<data>/runtimes` is read-only to agents. Installers get a minimal environment (no secrets), npm install scripts never run, and `` !`cmd` `` blocks in skills are never executed.
- Desk never merges branches.
- The desktop renderer never sees the daemon token; every IPC payload is validated in main.
- desk web never gives the browser the daemon token either. It answers only `Host: 127.0.0.1:<port>` (421 otherwise), takes `/rpc` and `/push` only from `Origin: http://127.0.0.1:<port>` with a session secret, validates every payload by its schema, and never sets a cookie.
- The web UI never turns text into HTML: no `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `DomSanitizer`, `bypassSecurityTrust*`, `srcdoc`, `eval` or `document.write` in `apps/web-ui/src` (`security.spec.ts` fails on them). Agent markdown goes through `SafeMarkdown`, which renders `marked`'s tokens with Angular templates, and links through `ExternalLink` (`safeExternalUrl`, then a confirmation).
