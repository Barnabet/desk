# Desk

Desk is a local-first project coordinator for macOS. Each project has a coordinator agent, **Desk**. You brief it like a chief of staff. It scopes the work, plans it, and dispatches parallel **threads**, which are full agents with their own isolated workspaces. Desk then supervises the threads, reviews their output and sends work back when needed. It assembles the results and reports.

Knowledge accumulates across the work:
- **Memory** holds durable facts, decisions and preferences.
- The **library** holds files and artifacts.
- **Skills** are reusable procedures with prepared scripts. Desk writes and refines them from your requests, and threads start with the relevant ones already active.

Work is general (research, writing, analysis, planning, code); git is a capability, not the core model.

Desk has three parts:
- the `deskd` daemon, with an HTTP and WebSocket API ([reference](docs/api.md))
- the `desk` CLI
- the **Desk desktop app** (Electron, macOS): a map of your projects, a line diagram of each conversation, thread routes, an attention rack, skills, library, memory, settings and system, plus a menu-bar popover. See [`docs/desktop.md`](docs/desktop.md).

## Requirements

- macOS. Shell commands are sandboxed with `sandbox-exec`.
- Node ≥ 22.12 and pnpm 9.
- An OpenAI-compatible Chat Completions endpoint. By default that is a local CLIProxyAPI serving `claude-opus-5-5` (the default model), `claude-fable-5-1`, `gpt-6-astra` and `gpt-6-sol`.

## Install

**The app:** run `pnpm install && pnpm package:desktop`, then open `apps/desktop/release/Desk-<version>-<arch>.dmg` and drag Desk to Applications. The first launch needs right-click → Open, because the build is unsigned. Onboarding starts the bundled deskd and sets up the model endpoint. The rest of this README covers the daemon and CLI; the app is described in [`docs/desktop.md`](docs/desktop.md).

**From source (daemon and CLI):**

```sh
pnpm install
```

Tell Desk where the model endpoint is, either way:

- `~/.config/cliproxyapi.env` with `CLIPROXY_BASE_URL=http://127.0.0.1:8317` and `CLIPROXY_API_KEY=…`.
- The environment variables `DESK_OPENAI_BASE_URL` and `DESK_OPENAI_API_KEY`, which take precedence.
- Through the API: `PUT /v1/config/model-endpoint` stores the base URL in `config.json` and the key in the macOS Keychain. This is how the desktop app's onboarding does it. Env and file take precedence over the Keychain.

Without any of these, deskd still starts: agents stay paused (as during a proxy outage) until an endpoint is configured.

The key is only read by the daemon. It never appears in `daemon.json`, logs, API responses or tool environments.

## Quick start

```sh
bin/desk up                                   # start deskd in the background
bin/desk project new Launch --goal "Ship the v2 launch" --source ~/code/app
bin/desk say Launch "Draft the launch plan and audit the signup flow for bugs"
bin/desk threads Launch                       # what is running
bin/desk tail <thread-id> -f                  # follow one thread
bin/desk approvals Launch                     # anything waiting for you
bin/desk approve <approval-id>
bin/desk chat Launch                          # interactive conversation with Desk
bin/desk down
```

`bin/desk up --install` installs deskd as a login LaunchAgent (`dev.desk.deskd`, KeepAlive), so work continues across reboots. `bin/desk` is `pnpm desk` without the pnpm overhead.

### CLI reference

| Command | |
|---|---|
| `up [--install] [--port n]`, `down`, `status` | Daemon lifecycle |
| `project new <name> [--goal] [--source path…]`, `project list`, `project show <p>`, `project set <p> key=value…`, `project archive <p>` | Projects and settings. Keys: `goal`, `instructions`, and settings such as `check_in=minimal` or `thread_model=gpt-6-sol` |
| `source add <p> <path>`, `source rm <p> <id>` | Attach folders or git repos. Sources are read-only for agents |
| `say <p> <text> [--no-wait] [--timeout s]`, `chat <p>` | Talk to Desk |
| `threads <p>`, `tail <thread> [-f]`, `tell <thread> <text>`, `stop <thread>` | Watch and steer threads |
| `approvals <p>`, `approve <id> [note]`, `deny <id> [note]` | Approvals |
| `memory <p> [query]`, `remember <p> <kind> <text>`, `forget <p> <id>` | Project memory |
| `library <p>`, `upload <p> <file>` | Library |
| `skills [p]`, `skill show\|import\|rm\|history\|restore … [-p project]` | Skills (global unless `-p`) |
| `catalog`, `catalog show <id>`, `catalog install <id> [-p project] [--yes] [--replace]` | The skill catalog: list with install states, print the review, install after confirming |
| `usage <p>` | Token usage per model |

Projects are referenced by id or by name.

## Concepts

- **Project**: a goal and instructions, plus sources (folders or git repos), settings, a plan, memory, a library and project skills.
- **Desk**: the per-project coordinator. It scopes requests, keeps the plan current, spawns threads with self-contained briefs, answers their questions, and reviews results; it sends work back up to `review_rounds` times. It reports in line with `check_in` (`minimal` / `normal` / `detailed`) and `autonomy` (`dispatch-freely` / `ask-before-dispatch`). Desk never merges branches; for code work it reports the merge order.
- **Thread**: a full agent working on one assignment. Its workspace is a scratch directory or, for repo work, a git worktree on the branch `desk/<slug>-<id>`. It messages Desk with questions and blockers and finishes with `complete`. You can steer a thread directly at any time.
- **Memory**: typed entries (`fact`, `decision`, `preference`, `contact`, `note`) with full-text search and supersession. A digest goes into every system prompt.
- **Library**: files published by agents or uploaded by you. Every agent can read it.
- **Skills**: directories in the Agent Skills format (compatible with Claude Code skills), containing `SKILL.md` plus `scripts/` and `references/`.
  - **Scope:** global skills apply to every project; project skills override a global skill of the same name.
  - **Activation:** the instructions of an agent's active skills are part of its system prompt. Desk activates skills on threads when it spawns them (`spawn_thread skills`), and any agent can call `skill_activate`.
  - **Scripts:** agents run skill scripts with `skill_run`, in the same sandbox as the shell.
  - **Authoring:** Desk creates and refines skills with `skill_write` when you ask for an automation or correct how a kind of task should be done. For skills with scripts, a thread writes and tests a draft in its workspace and Desk reviews and installs it.
  - **History:** every change is versioned, and you can restore an earlier version or import existing skills with `desk skill import ~/.claude/skills/<name>`.
- **Skill catalog**: 20 skills worth having, installable from the app (Skills → Catalog) or with `desk catalog install`.
  - **Contents:** research, documents and data (including Desk's own `word-documents` and `pdf-toolkit`), writing and diagrams, planning, and code.
  - **Pinning:** each entry is pinned to an exact commit and content digest. You review its source, licence, files and anything unusual before installing, and only you can install; Desk can suggest a skill but not install it.
  - **Runtimes:** skills with scripts get a Desk-managed environment. Python comes from a bundled uv (prebuilt wheels, exact pins); Node packages come from a lockfile checked against integrity hashes, with no install scripts, running on the app's own Node. Nothing is installed system-wide.
  - **Curation:** see `pnpm catalog:pin` and `pnpm catalog:check` in the spec (`docs/superpowers/specs/2026-09-24-skill-catalog-design.md`).

## Architecture

```
 desk CLI / Desk desktop app (Electron main process)
        │  HTTP + WebSocket (127.0.0.1, bearer token)
        ▼
 deskd (apps/daemon) ── Hono routes, ws stream, lock, launchd, stall timer
        │
 @desk/core (packages/core)
   Runtime ── Scheduler (per-model + per-project concurrency, Desk priority)
      │        ProxyGate (pause/resume on proxy outage)
      ├─ agent loop (run.ts): inbox → model call → tools in parallel → yields
      │     compaction · retries/backoff · fallback model · crash repair
      ├─ tools: files, shell (sandbox-exec), background jobs, web, git,
      │         memory, library, skills, coordination
      ├─ policy gate (ordered rules → auto / allow / ask / deny) + approvals
      └─ event store: SQLite (WAL), append-only events + projections
        │
 OpenAI-compatible endpoint (CLIProxyAPI) ── Claude / GPT models
```

- **Event-sourced:** every change is an event in `desk.db`, and projections are updated in the same transaction. Clients rebuild any state from the stream, and replay from a cursor has no gaps.
- **Custom agent loop:** built on the `openai` SDK, Chat Completions only, with streaming and parallel tool calls. The system prompt is snapshotted once per run, so the prompt prefix stays stable for caching.
- **Packages:**
  - `@desk/protocol`: zod schemas for events, settings and API bodies.
  - `@desk/core`: runtime, tools, policy, stores.
  - `@desk/daemon`, `@desk/cli`.
  - `@desk/fake-model`: a scriptable OpenAI-compatible server used in tests.

## Configuration and data

The data directory is `$DESK_DATA_DIR`, or `~/Library/Application Support/Desk` by default:

```
desk.db                         SQLite (WAL): events + projections
daemon.json                     { port, token, pid, version } — mode 0600, removed on stop
daemon.lock                     single-instance lock (stale locks are taken over)
models.json                     model registry (edit via PUT /v1/models)
config.json                     daemon settings: model base_url, notifications (no secrets)
logs/deskd.log
skills/<name>/                  global skills (+ .history/)
projects/<id>/library/          library files
projects/<id>/skills/<name>/    project skills (+ .history/)
projects/<id>/desk/             Desk's scratch workspace
workspaces/<thread-id>/         thread workspaces (scratch dirs or git worktrees)
workspaces/<thread-id>/.desk/   full outputs of truncated tool results
catalog/staging/                catalog skills fetched for review
runtimes/                       catalog skill environments (uv cache and managed Python under runtimes/uv)
```

The model registry sets each model's `context_window` (used for compaction) and `concurrency` (the scheduler cap). The daemon listens on port 7433 by default (`--port`), and falls back to an ephemeral port if 7433 is taken.

## Safety model

- **Workspaces:** agents write only inside their own workspace. File tools confine paths, including symlinks. Sources, the library and skills are read-only for agents; Desk writes the library and skills only through dedicated tools.
- **Shell:** `bash`, `bash_background` and `skill_run` run under a `sandbox-exec` profile. It allows writes only to the workspace, temp dirs and (for worktrees) the repo's git dir. The environment is scrubbed of secrets, and package caches are redirected to temp.
  - If the sandbox is unavailable, every shell call needs approval (degraded mode). Commands are never run silently unsandboxed.
  - Desk's own shell is read-only.
- **Policy:** ordered rules per project; the first match wins. The defaults:
  - Risky shell commands (`sudo`, `mkfs`, `dd if=`, recursive `rm` of `/` or `~`, `curl | sh`) → ask.
  - `git_push` to `desk/*` → allow; any other `git_push` → deny.
  - `open_pr` → ask.
  - Web fetch and search → allow.
  - `skill_delete` → ask.
- **Approvals:** you resolve them through the API or CLI. Rules may delegate an approval to Desk. An agent with pending approvals is never scheduled.
- **Network:** deskd binds `127.0.0.1` only. Every route except health needs the per-start bearer token.

## Resilience

- **Graceful stop:** interrupted runs end resumable (the agent is left `queued`) and continue on the next start.
- **Crash recovery:** after a hard crash, tool calls whose outcome is unknown are recorded as `interrupted`, never re-executed, and handed back to the model. The run then resumes, and a `daemon_restart` notice is recorded.
- **Proxy outage:** model calls pause and are health-checked every 10 s. There is one `proxy_down` / `proxy_up` notice per project, and no agent fails because of the outage.
- **Rate limits:** jittered exponential backoff. When it is exhausted, the rest of the run uses the project's `fallback_model`, if one is set.
- **Long conversations:** at 70% of the context window, or once on an overflow error, everything except the last 6 messages is summarised into a structured checkpoint. The original events stay in the log.
- **Stalled threads:** a thread with no activity for 15 minutes is reported to Desk once.
- **Notifications:** on macOS the daemon posts notifications for approvals, questions, reports that need you, and stalled or failed threads, unless a desktop client that shows its own notifications is connected or `notifications` is `off` (`PATCH /v1/config`).

## Development

```sh
pnpm test          # unit + integration tests (fake model server; real sandbox and git where available)
pnpm test:live     # live smokes against the configured endpoint (DESK_LIVE=1)
pnpm catalog:pin   # re-pin catalog entries (commit SHAs, digests, Node locks)
pnpm catalog:check # install every catalog entry for real and run its smoke command in the sandbox
pnpm typecheck
pnpm deskd --data-dir /tmp/desk-dev --port 0      # run the daemon in the foreground
```

### Desktop app (development)

```sh
pnpm desktop            # Electron app with Vite HMR; finds (or starts) the repo daemon
pnpm test:e2e           # builds the app, then Playwright-for-Electron flows against a real deskd + fake model
pnpm package:desktop    # apps/desktop/release/Desk-<v>-<arch>.dmg and .zip, with the bundled deskd
```

The app keeps running in the menu bar when its window closes. It stores its own preferences in Electron's `userData` folder and never stores the daemon token. Architecture, security rules and packaging are covered in [`docs/desktop.md`](docs/desktop.md).

The spec is in [`docs/superpowers/specs/2026-09-23-desk-daemon-design.md`](docs/superpowers/specs/2026-09-23-desk-daemon-design.md) and the implementation plans are in [`docs/superpowers/plans/`](docs/superpowers/plans/). Contributor notes for agents are in [`CLAUDE.md`](CLAUDE.md).

## Known limitations (v1.0)

- After a hard crash (SIGKILL), background processes started by agents keep running as orphans; the recovered run does not reattach to them.
- There are no MCP connectors yet (planned for v1.1).
- The desktop build is ad-hoc signed, not notarised. The Windows target is configured but untested, because deskd is macOS-only.
- Cost is reported in tokens, not currency.
