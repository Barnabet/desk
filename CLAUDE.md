# Desk — notes for coding agents

Desk is a local-first macOS daemon (`deskd`) plus a CLI (`desk`). A per-project coordinator agent ("Desk") dispatches parallel worker agents ("threads"). It also manages shared memory, a library, and skills (instructions plus scripts). There is no UI in this repo; the UI is designed separately against `docs/api.md`.

- **Design:** `docs/superpowers/specs/2026-09-23-desk-daemon-design.md`. §14 lists deviations from the original design.
- **Plans:** `docs/superpowers/plans/`.
- **API:** `docs/api.md`.

## Commands

```sh
pnpm test         # all unit + integration tests; must pass before any commit
pnpm typecheck    # tsc --noEmit; must pass before any commit
pnpm test:live    # live smokes against the local model proxy (slow; DESK_LIVE=1)
bin/desk …        # CLI (tsx loader, no build step)
```

There is no build step: TypeScript runs through the `tsx` loader, and packages export `src/*.ts` directly.

## Layout

- `packages/protocol`: zod schemas shared by everything.
  - `events.ts`: the event union.
  - `settings.ts`: project settings and the default policy.
  - `api.ts`: HTTP bodies.
  - `domain.ts`
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
  - `testing/`: the harness, exported as `@desk/core/testing`.
- `apps/daemon`: Hono app (`app.ts`, `routes/*`), WebSocket stream, daemon lifecycle.
- `apps/cli`: commander CLI over `DeskClient`.
- `test/fake-model`: a scriptable OpenAI-compatible server. Every non-live test talks to it.

## Conventions

- **Event sourcing.**
  - State changes are events appended through `EventStore.append`. Projections in `events/projections.ts` update tables in the same transaction.
  - A new event means adding it to `protocol/src/events.ts`, then projecting it if it has table state.
  - Never write projection tables directly.
- **Schema changes.** Edit `db/schema.ts`, then regenerate the single migration:
  ```sh
  cd packages/core && rm -rf drizzle && npx drizzle-kit generate --name init
  ```
  Migrations stay squashed until the first external release.
- **Tools.**
  - Define tools with `defineTool` (zod input); throwing returns an error result to the model.
  - Tools that touch the outside world declare a `gate` so the policy decides auto/allow/ask/deny.
  - Tools reach runtime features only through `ctx.services`.
- **Errors.** Runtime methods throw `NotFoundError`, `ConflictError` or `ValidationError` from `core/errors.ts`, and the daemon maps them to 404, 409 or 400.
- **Tests.**
  - Use Vitest with the harness (`createHarness`, `newRuntime`, `seedThread`) and fake-model scripts. Route by system prompt, or by the number of assistant messages in the request, rather than by call order when agents run concurrently.
  - Sandbox tests skip when `sandbox-exec` is unavailable.
  - Live tests are `*.live.test.ts`.
- **Secrets.** The model API key comes from `DESK_OPENAI_*` or `~/.config/cliproxyapi.env`. It must never reach logs, events, `daemon.json`, API responses or tool environments; `scrubbedEnv` builds tool envs.
- **Style.** Strict TS with `noUncheckedIndexedAccess`, ESM, and short doc comments on non-obvious exports. Match the surrounding code.

## Invariants worth protecting

- Crash recovery never re-executes a tool call whose outcome is unknown; it records `interrupted` instead.
- An agent with pending approvals is never scheduled.
- Graceful shutdown leaves agents `queued`, not `cancelled`.
- Shell tools (`bash`, `bash_background`, `bash_readonly`, `skill_run`) never run unsandboxed without approval.
- Threads cannot modify installed skills. They submit drafts, which Desk installs with `skill_write from_dir`.
- Desk never merges branches.
