# Desk Plan 4 — Daemon, API and CLI Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. TDD per task; commit only when `pnpm test` and `pnpm typecheck` both exit 0.

**Goal:** `deskd` — a long-running local daemon exposing the Runtime over an authenticated HTTP + WebSocket API — plus the `desk` CLI dev client and launchd integration.

**Architecture:** `apps/daemon` builds a Hono app over a `Runtime` and serves it with `@hono/node-server`; the event stream is a `ws` WebSocketServer attached to the same HTTP server's `upgrade` event. All request bodies and stream messages are zod schemas in `@desk/protocol` (`api.ts`). Runtime errors carry a typed code (`NotFoundError`, `ConflictError`, `ValidationError` in core) mapped to 404/409/400. The daemon owns lifecycle: data dir, single-instance lock, `daemon.json` (port, token, pid, version; mode 0600), persisted model registry (`models.json`), stall checks every minute, graceful shutdown that leaves agents resumable. `apps/cli` is a thin client over the same API.

**Tech Stack:** hono 4, @hono/node-server 2, ws 8, commander 15.

**Spec:** §9 (entire), §4 on-disk layout, §5.7 shutdown half (crash recovery is Plan 5).

## Global Constraints
- Bind `127.0.0.1` only. Default port 7433; if taken, an ephemeral port. Port and token are published in `daemon.json` (0600). Token = 32 random bytes hex, regenerated per start.
- Every route except `GET /v1/health` requires `Authorization: Bearer <token>`; the WebSocket uses `?token=`.
- Data dir: `$DESK_DATA_DIR` or `~/Library/Application Support/Desk`.
- Error body: `{ error: { code, message, details? } }`.
- The proxy key never appears in responses, logs or `daemon.json`.
- Graceful shutdown never cancels agents: interrupted runs end with `run.finished{reason: error, detail: daemon_shutdown}` and the agent is left `queued`, to be rescheduled on the next start.
- Library uploads are JSON with base64 content (local-only API; simpler than multipart).

---

### Task 1: Protocol API schemas + core error types + project archive
**Files:** `packages/protocol/src/api.ts`; `packages/core/src/errors.ts` (`DeskError` base with `code`; `NotFoundError`, `ConflictError`, `ValidationError`); runtime throws typed errors; `project.archived` event + `Runtime.archiveProject` (stops every agent, sets `archived_at`).
**Tests:** api schemas accept/reject representative bodies; runtime unknown ids → `NotFoundError`; double approval → `ConflictError`; archiveProject stops running threads and hides the project from `listProjects`.

### Task 2: Graceful shutdown + startup recovery of queued agents
**Files:** `runtime.shutdown()`, `runtime.recover()`, `agent/run.ts` shutdown abort reason.
**Tests:** shutdown mid model call → `run.finished{error, daemon_shutdown}`, status `queued`, no `cancelled`; a fresh Runtime on the same DB `recover()`s and completes the run; shutdown kills background jobs.

### Task 3: HTTP app
**Files:** `apps/daemon/src/app.ts` (Hono routes), `apps/daemon/src/errors.ts`, `apps/daemon/src/models-file.ts`.
**Routes:** health; projects (list/create/get overview/patch/archive); sources (add/remove); `POST /projects/:id/messages`; `GET /projects/:id/chat`; plan; threads (list/get/transcript/message/stop/archive); approvals (list/resolve); memory (list+search/create/supersede via PATCH/delete); library (list/upload/download); usage; events (paged, type filter); models (GET, PUT replace-all with validation, persisted).
**Tests:** through `app.request()` with a fake-model Runtime: auth (401 without/with wrong token, health open); project lifecycle; message → Desk runs → chat shows assistant message; thread endpoints; approvals resolve; memory CRUD/search; library round-trip of binary content; models persisted to file and rejected when invalid; 404/409/400 mapping; events paging with `after`/`limit`/`types`.

### Task 4: WebSocket stream
**Files:** `apps/daemon/src/stream.ts`.
**Protocol:** client → `{ subscribe: { project_id: string | '*', after_seq: number } }`; server → `{ kind: 'event', event }` (replayed then live), `{ kind: 'ephemeral', event }` (live only), `{ kind: 'ready', seq }` after replay, `{ kind: 'error', message }`.
**Tests (real server on port 0):** bad token → close 4401; replay of events after cursor then `ready`; live events and deltas delivered; project filter; reconnect with last seq receives exactly the missed events.

### Task 5: Daemon process
**Files:** `apps/daemon/src/paths.ts`, `lock.ts`, `daemon.ts` (`startDaemon(opts) → { port, token, stop() }`), `main.ts`, `logger.ts`.
**Tests:** `startDaemon` on a temp data dir writes `daemon.json` with mode 0600 and no secrets; a second `startDaemon` on the same dir fails with "already running"; stale lock (dead pid) is taken over; `stop()` shuts down gracefully and removes `daemon.json`; stall checker runs on an interval (injected clock).

### Task 6: CLI
**Files:** `apps/cli/src/client.ts` (typed API client + WS), `commands.ts` (`runCli(argv, io)`), `chat.ts` (interactive renderer), `launchd.ts` (plist generation, install/uninstall), `main.ts`; `bin/desk` shim.
**Commands:** `up [--install]`, `down`, `status`, `project new|list|show|set`, `source add|rm`, `chat <project>`, `say <project> <text>`, `threads <project>`, `tail <thread>`, `tell <thread> <text>`, `stop <thread>`, `approvals <project>`, `approve|deny <id> [note]`, `memory <project> [query]`, `remember <project> <kind> <text>`, `library <project>`, `usage <project>`.
**Tests:** against a test daemon: project new/list/show output; say + threads; approvals listing and approve; memory remember/search; usage table; chat renderer formats event types (unit, no TTY); plist contains node path, entry, KeepAlive, log paths.

## Done Criteria
Tests + typecheck green; a real daemon started via `pnpm desk up` answers `pnpm desk status`, runs a live Desk request from `pnpm desk say`, and stops with `pnpm desk down`.
