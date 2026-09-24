# Project services — design

**Date:** 2026-09-24 · **Status:** approved in chat (lifetime: project services; runs from: a thread's workspace; after a deskd restart: shown as stopped, one click to start)

## Problem

Desk's agents cannot keep a development environment running for the user. Threads have `bash_background`, but those jobs live only in deskd's memory, are never recorded or shown, and are killed when the thread finishes. Desk itself cannot start processes. The user wants Desk to run things like a backend and a frontend dev server, keep them running while they try the work, and see and control them in the bottom right of the Conversation screen.

## Summary

A **service** is a named, long-lived process that belongs to a project (`backend`, `frontend`, `worker`). Desk or a thread starts it with a command; it runs in a thread's workspace, sandboxed like every shell tool, and keeps running after that thread finishes, until the user or an agent stops it, its thread or project is archived, or deskd shuts down. Its lifecycle is event-sourced; its output goes to a capped log file. The Conversation screen shows a Services card under the plan with status, URL, uptime, and Open / Logs / Restart / Stop / Start.

## Model

- One service per `(project, name)`. `name` matches `^[a-z0-9][a-z0-9-]{0,31}$`. Starting a name that is running stops the old run first (`reason: restart`), then starts the new one under the same `service_id`.
- A service records: `command`, `cwd` (a relative subdirectory of the workspace, default `.`), `agent_id` (the thread whose workspace it runs in), `status` (`running` | `exited` | `stopped`), `exit_code`, `stop_reason`, `url`, `pid`, `started_at`, `ended_at`, `started_by` (`user` or `agent:<id>`).
- **Events** (`protocol/src/events.ts`), projected into a new `services` table (additive migration):
  - `service.started` `{ service_id, name, command, cwd, workspace_agent_id, pid, by }`
  - `service.url` `{ service_id, url }` — first loopback URL seen in the output of this run
  - `service.exited` `{ service_id, code: int | null, signal: string | null }` — the process ended on its own
  - `service.stopped` `{ service_id, by, reason }` with `reason` ∈ `requested` | `restart` | `thread_archived` | `project_archived` | `daemon_shutdown` | `daemon_restart`
- **Logs** never enter the event log. Each service appends stdout+stderr to `<projectDir>/services/<service_id>.log`, with a `── started <iso> · <command> ──` line per run. When the file passes 2 MB it is cut to its last 1 MB.
- **URL detection:** ANSI codes are stripped, then the first match of `https?://(localhost|127.0.0.1|0.0.0.0|[::1])(:port)?(/path)?` is recorded, with `0.0.0.0` rewritten to `localhost`. Only loopback URLs are ever recorded.

## Process management (`core/services/manager.ts`)

- `ServiceManager` spawns `shellInvocation(command, sandbox)` detached (its own process group), `cwd` = workspace/`cwd` (must stay inside the workspace), env = `scrubbedEnv(workspace)` plus the owning thread's skill env. Sandbox writable roots are the workspace and its git common dir, exactly as for the thread's own shell tools. Network is allowed (the profile only restricts writes).
- **Stop:** SIGTERM to the process group, SIGKILL after 5 s. A stop records `service.stopped`; the later `close` of that child is ignored, so a stopped service never also reports `exited`.
- **Lifetime:**
  - Independent of the thread's status: a thread finishing, failing or being cancelled leaves its services running.
  - `archiveThread` stops the thread's services (`thread_archived`) before removing the workspace; `archiveProject` stops all of the project's services (`project_archived`).
  - `Runtime.shutdown` stops every running service (`daemon_shutdown`).
  - `Runtime.recover` marks rows still `running` after an unclean exit as stopped (`daemon_restart`). Best-effort reap: if the recorded pid is alive and its command line (`ps -o command= -p <pid>`) still contains the recorded command, its process group is killed, so an orphaned dev server does not keep its port.
- There is no automatic restart after a deskd restart. The user or Desk starts services again.

## Agent tools (`core/tools/services.ts`)

Desk and threads both get:

| Tool | Input | Notes |
|---|---|---|
| `service_start` | `{ name, command, cwd?, thread_id? }` | Threads run in their own workspace (`thread_id` is refused). Desk must pass `thread_id` of a non-archived thread with a workspace. Waits up to 8 s for a URL or an early exit; returns the status, the URL, and the last 20 log lines. |
| `service_logs` | `{ name, lines? }` | Tail of the log (default 80, max 400 lines), ANSI stripped. |
| `service_stop` | `{ name }` | |
| `service_restart` | `{ name }` | Re-runs the recorded command in the recorded workspace. |
| `service_list` | `{}` | One line per service. |

- **Policy:** `service_start` is gated with subject `{ command }` and `unmatched: 'auto'`, and it is a shell tool (no sandbox → ask). Its gate also matches rules written for `bash_background` (new `ToolGate.alsoMatches`), so existing projects' saved policies (risky commands → ask) cover it without a migration. `service_restart` and the user's Start/Restart re-run a command that already passed the gate, so they are not gated. The invariant "shell tools never run unsandboxed without approval" now covers `service_start`.
- Desk's system prompt gains a **Services** section (one line per service: name, status, URL, which thread's workspace, command), so it notices a crash on its next run. `bash_background` stays for short-lived jobs that should die with the thread; the tool descriptions say which to use.

## API (`apps/daemon/src/routes/services.ts`, `docs/api.md`)

- `GET /v1/projects/:id/services` → `ServiceRow[]`; the project overview also carries `services`.
- `GET /v1/services/:id/logs?lines=` → `{ text, truncated }` (tail, ANSI stripped, max 2000 lines).
- `POST /v1/services/:id/start | stop | restart` → the row (`by: 'user'`). Start and restart need the owning thread to be unarchived (409 otherwise).
- CLI: `desk services <project>`, `desk service logs|start|stop|restart <project> <name>`.

## Desktop app

- **Services card** at the bottom of the Conversation screen's right column, under the Plan card; the plan scrolls, the services card keeps its natural height (max 40% of the column). Hidden when the project has no services.
- **Row:** status dot (running / exited-with-error red / stopped grey), name, URL port, uptime or "exited (1) · 2m ago" / "stopped · deskd restarted", "from <thread title>", and buttons **Open** (only for a recorded loopback URL), **Logs**, **Restart** + **Stop** while running, **Start** otherwise.
- **Logs** open a sheet that polls the tail every second while open, rendered as plain text (never markdown).
- State comes from the event stream (`@desk/client` project reducer); actions go through validated IPC channels `services.start|stop|restart|logs`.

## Security

- Sandboxed like every shell tool, no secrets in the environment, policy-gated for agents, loopback-only URLs, logs are untrusted plain text in the UI and in tool results.
- Services only run in thread workspaces, never in the user's source folders.

## Testing

- **Core:** start → `service.started` + log file; URL detection (`0.0.0.0` → `localhost`, non-loopback ignored); exit recorded; stop doesn't also record `exited`; start by name replaces the old run; thread finishing keeps the service, archiving stops it; shutdown and recover; policy (`bash_background` rules apply, no sandbox → ask); Desk needs `thread_id`, threads can't pass one.
- **Daemon:** routes, including 409 after archive; **client:** reducer; **desktop:** the card and the logs sheet; **e2e:** a fake-model thread starts a real `node` HTTP server, the card shows it with its URL, Stop from the card.
