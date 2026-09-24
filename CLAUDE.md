# Desk — notes for coding agents

Desk is a local-first macOS daemon (`deskd`), a CLI (`desk`) and an Electron desktop app (`apps/desktop`, see `docs/desktop.md`). A per-project coordinator agent ("Desk") dispatches parallel worker agents ("threads"). It also manages shared memory, a library, and skills (instructions plus scripts). Every client goes through the API in `docs/api.md`.

- **Design:** `docs/superpowers/specs/2026-09-23-desk-daemon-design.md`. §14 lists deviations from the original design.
- **Plans:** `docs/superpowers/plans/`.
- **API:** `docs/api.md`.

## Commands

```sh
pnpm test         # all unit + integration tests; must pass before any commit
pnpm typecheck    # tsc --noEmit; must pass before any commit
pnpm test:live    # live smokes against the local model proxy (slow; DESK_LIVE=1)
bin/desk …        # CLI (tsx loader, no build step)
pnpm --filter @desk/daemon bundle   # esbuild bundle → apps/daemon/dist/deskd.mjs (+ migrations, better-sqlite3 prebuilds)
pnpm desktop      # the Electron app against the repo daemon (Vite HMR)
pnpm test:e2e     # builds the app and runs the Playwright-for-Electron suite (opens windows; set DESK_E2E_SHOTS=<dir> for screenshots)
pnpm package:desktop   # unsigned (ad hoc) Desk.app → apps/desktop/release/*.dmg + .zip with the bundled deskd and pinned uv; enables e2e/packaged
pnpm catalog:pin [ids]    # re-pin catalog.json entries: commit SHAs, digests, script counts, Node locks (lock_from)
pnpm catalog:check [ids]  # install each catalog entry for real (uv on PATH or DESK_UV), build its runtime, run its smoke command sandboxed
```

There is no build step: TypeScript runs through the `tsx` loader, and packages export `src/*.ts` directly. The desktop app is the exception: esbuild bundles its main and preload, and Vite builds its renderer.

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
  - `catalog/`: the skill catalog. `catalog.json` holds the 20 pinned entries; `service.ts` fetches, verifies, stages and installs them; `runtimes.ts` builds Desk-managed environments (uv Python, npm lock, node shim, compat shims); `tar.ts`, `digest.ts`, `review.ts`; `curation.ts` holds helpers for `scripts/catalog.ts`.
  - `model/endpoint.ts`, `model/switchable.ts`: endpoint resolution (env → file → Keychain) and a runtime-configurable adapter.
  - `testing/`: the harness, exported as `@desk/core/testing`.
- `apps/daemon`: Hono app (`app.ts`, `routes/*`), WebSocket stream, daemon lifecycle, `notifier.ts` (macOS notifications), `scripts/bundle.mjs`.
- `apps/cli`: commander CLI over `DeskClient`.
- `apps/desktop`: Electron + React app (spec: `docs/superpowers/specs/2026-09-24-desk-desktop-app-design.md`).
  - `src/main/`: the only deskd client. `broker.ts` (global state, event forwarding), `handlers.ts` (IPC ops, zod-validated in `shared/ipc.ts`), `daemon.ts` (start/LaunchAgent), tray, windows (CSP, `desk-app://`).
  - `src/preload/`: exposes only `window.desk.{invoke,on,platform}`.
  - `src/renderer/`: React UI; talks to main only through `bridge.ts`. Agent text goes through `SafeMarkdown`.
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
- **Secrets.** The model API key comes from `DESK_OPENAI_*`, `~/.config/cliproxyapi.env`, or the macOS Keychain (written by `PUT /v1/config/model-endpoint` via `security -i` on stdin, never argv). It must never reach logs, events, `daemon.json`, `config.json`, API responses or tool environments; `scrubbedEnv` builds tool envs.
- **Style.** Strict TS with `noUncheckedIndexedAccess`, ESM, and short doc comments on non-obvious exports. Match the surrounding code.

## Invariants worth protecting

- Crash recovery never re-executes a tool call whose outcome is unknown; it records `interrupted` instead.
- An agent with pending approvals is never scheduled.
- Graceful shutdown leaves agents `queued`, not `cancelled`.
- Shell tools (`bash`, `bash_background`, `bash_readonly`, `skill_run`, `service_start`) never run unsandboxed without approval.
- Agents write only to their workspace, temp dirs, and project sources with `agent_write` (on by default; the user can turn it off per source). Project services run in a thread workspace or such a source (sandboxed, no secrets), outlive their thread, and are stopped on archive and shutdown; their output goes to log files, never to events.
- Threads cannot modify installed skills. They submit drafts, which Desk installs with `skill_write from_dir`.
- Only the user installs catalog skills; every install is checked against its pinned digest, and `<data>/runtimes` is read-only to agents. Installers get a minimal environment (no secrets), npm install scripts never run, and `` !`cmd` `` blocks in skills are never executed.
- Desk never merges branches.
- The desktop renderer never sees the daemon token; every IPC payload is validated in main.
