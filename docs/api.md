# deskd API reference (v1, protocol version 1)

`deskd` listens on `127.0.0.1` only. The default port is 7433; if that port is taken, deskd uses an ephemeral port. Clients find the port and token in `<dataDir>/daemon.json`, which has mode 0600:

```json
{ "port": 7433, "token": "<64 hex chars>", "pid": 12345, "version": "1.0.0", "started_at": "…" }
```

**Authentication.** Every route except `GET /v1/health` needs `Authorization: Bearer <token>`. The WebSocket takes the token as `?token=`. The token is regenerated on every daemon start.

**Bodies.** Request bodies are JSON and are validated with the zod schemas in `@desk/protocol` (`packages/protocol/src/api.ts`); those schemas are the normative definitions.

**Errors.** Every error uses the same body: `{ "error": { "code", "message", "details"? } }`.

| Status | code | When |
|---|---|---|
| 400 | `invalid` | Invalid body (with zod `details`), name or path |
| 401 | `unauthorized` | Missing or wrong token |
| 404 | `not_found` | Unknown project, thread, approval, memory entry, skill or route |
| 409 | `conflict` | Invalid state, e.g. an approval already resolved or a project archived |
| 500 | `internal` | Unexpected error (details are in `logs/deskd.log`) |

**Paging.** Event lists (`/chat`, `/events`, `/threads/:id/transcript`) return `{ events, next_after }`. They accept these query parameters:
- `after=<event id>`: only events after this id.
- `limit=<n>`: maximum number of events.
- `types=a,b`: filter by type (only on `/events`).

Pass `next_after` as the next `after` to continue.

## System

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/health` | Unauthenticated. `{ version, protocol_version, build, proxy: up\|down\|unknown, uptime_s }`. `build` is the bundled deskd's build id, or `null` when running from source |
| GET | `/v1/usage` | `?since=YYYY-MM-DD`. `{ rows: [{ project_id, model, prompt_tokens, completion_tokens }], totals }` across projects |
| GET | `/v1/models` | The model registry (`ModelInfo[]`: id, family, context_window, max_output_tokens, reasoning_efforts, default_reasoning_effort, concurrency, vision). `reasoning_efforts` lists the levels the model accepts (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`); an empty list means Desk never sends a level. `default_reasoning_effort` is `null` (the endpoint's default) or one of those levels. `vision` (default `true`) says whether the model takes images: without it, `view_image` refuses and earlier images reach the model as text references |
| PUT | `/v1/models` | Replace the registry (`ModelInfo[]`, validated). The registry is persisted to `models.json` |

## Projects

| Method | Path | Body / query | Notes |
|---|---|---|---|
| GET | `/v1/projects` | `?all=1` includes archived | |
| POST | `/v1/projects` | `CreateProjectRequest { name, goal?, instructions?, settings?, sources?[{path,label?}] }` | 201 with the overview. Creates the Desk agent |
| GET | `/v1/projects/:id` | | Overview: project, desk, sources, plan, threads (archived ones included, with `archived_at` set), pending approvals, services, `whats_up` (`{ text, ts }` or `null`: Desk's latest What's up), `last_seq` (the stream cursor to resume from) |
| PATCH | `/v1/projects/:id` | `UpdateProjectRequest { name?, goal?, instructions?, settings? }` | `settings` is a partial patch |
| POST | `/v1/projects/:id/archive` | | Archives the project first (no agent is woken after that), then stops its agents and services, and hides it |
| POST | `/v1/projects/:id/sources` | `{ path, label?, agent_write? }` | Detects `git` vs `folder`. `agent_write` (default `true`): Desk and its threads may write there (sandboxed) and run services there. 201 |
| PATCH | `/v1/projects/:id/sources/:sid` | `{ agent_write }` | Turns agents' write access to the folder on or off (`source.updated`) |
| DELETE | `/v1/projects/:id/sources/:sid` | | |
| POST | `/v1/projects/:id/messages` | `{ text }` | Message to Desk. 202; Desk wakes. 400 with `question: true`: only a thread can be asked |
| GET | `/v1/projects/:id/chat` | paging | Desk's own stream: the user's and agents' messages to Desk, assistant messages, tool calls and results, reports, questions, status changes and run endings. Project notices and messages between threads are not included; read them from `/events` |
| GET | `/v1/projects/:id/plan` | | `{ items: PlanItem[] }` or `null` |
| GET | `/v1/projects/:id/usage` | | `{ rows (per model), totals }` |
| GET | `/v1/projects/:id/events` | paging + `types` | Raw event log (limit ≤ 5000, default 500) |

**Project settings** (`ProjectSettings`, with defaults):

| Setting | Default | Notes |
|---|---|---|
| `desk_model`, `thread_model` | `claude-opus-5-5` | |
| `fallback_model` | `null` | Used for the rest of a run after sustained rate limiting |
| `desk_reasoning_effort`, `thread_reasoning_effort` | `null` | A level the model takes (checked against the registry; 400 otherwise). `null` means the model's default. A thread's own level (`spawn_thread reasoning_effort`, stored on `agent.created`) overrides `thread_reasoning_effort`. When a call goes to a model that does not take the level (e.g. the fallback), that model's default is sent instead, or nothing. `run.started` records the level sent to the run's model |
| `max_concurrent_threads` | 4 | |
| `check_in` | `normal` | `minimal`, `normal` or `detailed` |
| `autonomy` | `dispatch-freely` | Or `ask-before-dispatch` |
| `review_rounds` | 2 | Maximum send-backs per thread |
| `policy` | see README | Ordered `PolicyRule[]`; the first match wins |

## Services

Project services are long-lived processes (dev servers, APIs, workers) that Desk or a thread starts with `service_start`, in a thread's workspace or in a project source folder that allows agents to write (`source_id`). They run sandboxed, keep running after the thread finishes, and stop when the user or an agent stops them, when their thread or project is archived, or when deskd shuts down. A service row: `{ id, project_id, name, command, cwd, agent_id (the workspace's thread, or who started a source service), source_id, status: running|exited|stopped, pid, exit_code, exit_signal, stop_reason, url, started_by, started_at, ended_at }`. `url` is the first loopback URL the current run printed.

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/projects/:id/services` | `ServiceRow[]`, by name. The project overview carries them too (`services`) |
| GET | `/v1/services/:id/logs` | `?lines=` (default 200, max 2000). `{ text, truncated }`: the log tail, ANSI stripped. Logs are capped at 2 MB per service |
| POST | `/v1/services/:id/start` | Runs the recorded command again. 409 when its thread is archived |
| POST | `/v1/services/:id/restart` | Stops the current run, then starts it again |
| POST | `/v1/services/:id/stop` | |

## Threads and approvals

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/projects/:id/threads` | `?all=1` includes archived |
| GET | `/v1/threads/:id` | Thread row: status, brief, result, git info, `active_skills`, … |
| GET | `/v1/threads/:id/transcript` | paging; every event of the thread |
| POST | `/v1/threads/:id/messages` | `{ text, question? }`: steer the thread directly. With `question: true`, the user's Ask: an idle, done or failed thread answers it in a short read-only run and keeps its status, result and branch; any other thread reads it as a message. 202. 409 when the thread or its project is archived |
| POST | `/v1/threads/:id/stop` | Cancels the thread; Desk is notified |
| POST | `/v1/threads/:id/archive` | Finished threads only. Removes the workspace and keeps the git branch |
| GET | `/v1/projects/:id/approvals` | `?status=pending\|approved\|denied` |
| POST | `/v1/approvals/:id/resolve` | `{ decision: "approved"\|"denied", note? }`. A second resolve returns 409 |

## Knowledge: memory and library

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/projects/:id/memory` | Active entries; `?q=` runs a full-text search |
| POST | `/v1/projects/:id/memory` | `{ kind: fact\|decision\|preference\|contact\|note, content, supersedes? }`. 201 |
| PATCH | `/v1/projects/:id/memory/:mid` | `{ content, kind? }` writes a new entry that supersedes the old one |
| DELETE | `/v1/projects/:id/memory/:mid` | |
| GET | `/v1/projects/:id/library` | Artifacts: path, title, kind, origin (`user` or `agent:<id>`) |
| POST | `/v1/projects/:id/library` | `{ name, content_base64, title?, kind?, description? }`. Names are de-duplicated |
| GET | `/v1/projects/:id/library/file/<path>` | Raw file; a path that escapes the library returns 403 |

## Skills

Global skills live under `/v1/skills`, project skills under `/v1/projects/:id/skills`. These routes list and change only the user's own skills; Desk's built-in skills have their own routes (next section). On project routes, `GET` resolves project-then-global. Writes target the route's scope.

| Method | Path suffix | Body | Notes |
|---|---|---|---|
| GET | `` | | `SkillSummary[]`: name, scope, description, dir, version, `error?` (broken SKILL.md) |
| GET | `/:name` | | Detail: summary + instructions, frontmatter, files |
| GET | `/:name/files/<path>` | | Raw file; escaping paths return 400 |
| PUT | `/:name` | `SkillWriteRequest { description?, instructions?, files?[{path, content_base64}], remove_files?, change_note? }` | Creates the skill (201) or refines it (200). The previous version goes to history. A `SKILL.md` may be sent as a file |
| DELETE | `/:name` | | The last version stays in history |
| GET | `/:name/history` | | `[{ version, description, current, change_note, origin, ts }]`. `origin` is `user`, `agent:<id>`, `catalog:<id>@<sha>` or `builtin:<name>@<digest12>` (a duplicated built-in); `origin` and `ts` are null for versions with no `skill.saved` record |
| POST | `/:name/restore` | `{ version }` | Restores that version as the newest one |
| POST | `/import` | `{ path, name? }` | Copies a local skill directory (e.g. `~/.claude/skills/x`). The name defaults to the frontmatter `name`. 201 |

Skill names match `^[a-z0-9]+(-[a-z0-9]+)*$` (≤ 64 chars); descriptions are ≤ 1024 chars. Size limits: at most 2 MB per file, 200 files and 10 MB per skill. Symlinks are skipped on install.

## Built-in skills

Desk's own skills (every file type, and web research) ship with the app, read-only, and are verified against their manifest (`packages/core/src/skills/builtins.json`) at start. Agents resolve skills project → global → built-in, so a user skill of the same name shadows one. A built-in that is turned off or damaged is hidden from agents. The design is in `docs/superpowers/specs/2026-09-26-builtin-skills-design.md`.

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/v1/builtin-skills[?project_id=]` | | `BuiltinSkillInfo[]`: `name`, `title`, `summary`, `caveats`, `description`, `scripts`, `enabled`, `broken` (reason or null), `shadowed_by` (`global`, `project` (with `project_id`) or null), `runtime { state: none\|preparing\|ready\|failed, reason }` |
| GET | `/v1/builtin-skills/:name` | | Detail like a skill's (`scope: 'builtin'`); 404 for unknown names |
| GET | `/v1/builtin-skills/:name/files/<path>` | | Raw file; escaping paths return 400 |
| PUT | `/v1/builtin-skills/:name` | `{ enabled }` | Turns it off or on for every project (event `skill.builtin_toggled`); returns the `BuiltinSkillInfo` |
| POST | `/v1/builtin-skills/:name/duplicate` | `{ scope?: 'global'\|'project', project_id? }` | Saves an ordinary, editable copy (origin `builtin:<name>@<digest12>`) that shadows the built-in until deleted; 201. 409 when a skill of that name exists in the scope; 400 for a project copy without `project_id` |
| POST | `/v1/builtin-skills/:name/runtime/retry` | | Rebuilds its environment: `{ state, reason }`. 409 when this daemon has no skill runtimes |

**Environments on first use.** A built-in's Python environment (`<data>/runtimes/builtin/_global/<name>`) is built when an agent first activates the skill, or at its first `skill_run`, which waits up to 5 minutes and then asks the agent to try again; a run that waited starts with `(Set up <name>'s Python environment first: first use only, <n> s.)`. The environment is keyed by the packages it installs, so a Desk update that changes them rebuilds it on next use, and a copy of a built-in without an environment of its own uses the built-in's. At start, setups a crash cut short are recorded as removed, and unmodified catalog copies of these skills (from before they were built in) are deleted so the built-ins take over.

## Skill catalog

Reviewed skills pinned to a commit and a content digest, installed only at the user's request. The design is in `docs/superpowers/specs/2026-09-24-skill-catalog-design.md`.

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/v1/catalog` | | `CatalogItem[]`: the entry (`id`, `title`, `category`, `summary`, `license`, `homepage`, `source`, `digest`, `files`, `bytes`, `runtime`, `caveats`) plus `installs[]`. Each install is `{ scope, project_id, state, sha, runtime, runtime_reason }`, where `state` is `installed`, `update_available`, `modified` (edited after install) or `name_taken` (a same-named skill that isn't from the catalog). Scopes with nothing of that name are omitted |
| POST | `/v1/catalog/:id/prepare` | | Downloads the pinned archive (GitHub codeload) or reads the builtin skill, verifies the digest, and stages it. Returns `CatalogReview { entry, source_url, files[{path, size, script}], skill_md, license_text, warnings[{file, line, kind, excerpt}] }`. A digest mismatch is a 400 and nothing is staged |
| GET | `/v1/catalog/:id/files/<path>` | | Raw bytes of one staged file, so the user can read it before installing. The skill is staged again if needed; paths outside the skill are refused |
| POST | `/v1/catalog/:id/install` | `{ scope?: 'global'\|'project', project_id?, replace_modified? }` | Installs through the skill store with origin `catalog:<id>@<sha>`, and returns `{ skill, state, runtime }` (201). A 409 means `name_taken`, or `modified` without `replace_modified: true` |

**Runtimes.** Entries with `runtime.python`, `runtime.node` or `runtime.extras` get a Desk-managed environment under `<data>/runtimes/<scope>/<project|_global>/<name>`, built in the background after install.
- Python comes from uv, with prebuilt wheels only and exact pins.
- Node packages come from the lock, checked against their integrity hashes; install scripts never run.
- `extras`: `playwright-chromium` downloads Playwright's Chromium into the environment (`PLAYWRIGHT_BROWSERS_PATH`). `browser` uses an installed Chrome, Edge or Chromium instead (`DESK_BROWSER=<executable>`, nothing downloaded), keeping only one that answers `--version` (not run on Windows, where it opens a window), and falls back to the same download when none does; the `Runtime:` line names the browser used. A ready runtime whose `DESK_BROWSER` has since been removed reports `failed`, and Retry looks again. Both extras require `runtime.python` with a `playwright==` pin (the catalog schema refuses entries without it).
- The environment's `bin/` also has stand-ins for `uv run`, `uv pip install`, `pip install`, `npm install` and `npx <bin>`, which run from the environment or report that the packages are already installed. Once the runtime is ready, `skill_read` and `skill_activate` show a `Runtime:` line naming the pinned packages.
- State is stored as `skill.runtime_changed { scope, name, state: preparing|ready|failed|removed, reason }`, and progress streams as the ephemeral `skill.runtime_progress { scope, name, step, done?, total? }` (`agent_id: null`).
- `skill_run` refuses a skill whose runtime is `preparing` or `failed`. When the runtime is ready, `bash` and `skill_run` put its `bin` directories first on PATH.

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/skills/:name/runtime/retry`, `/v1/projects/:id/skills/:name/runtime/retry` | Rebuilds the runtime of a catalog-installed skill; returns `{ state, reason }`. 404 for skills not from the catalog |
| GET | `/v1/system/runtimes` | `{ bytes, envs[{ scope, project_id, name, bytes, orphan }] }` |
| POST | `/v1/system/runtimes/cleanup` | Removes environments whose skill no longer exists: `{ removed, bytes }` |

Warning kinds: `exec-block` (`` !`cmd` `` or ```` ```! ````, which Desk never runs), `pipe-to-shell`, `base64-blob`, `invisible-unicode`, `paste-site` and `memory-write`. The limits are the skill store's: 50 MB download, 10 MB, 200 files, 2 MB per file, and no links or special files.

## Desktop app endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/overview` | One `ProjectSummary` per open project: `project`, `desk_status`, `threads` (status, `reason`, current `activity` like `bash · python3 x.py`, model, `git_branch`, `skills`, `review_round`), `latest_report`, `plan_progress { done, total }` (dropped items excluded), `attention_count` |
| GET | `/v1/attention` | `?project_id=`. `{ items: AttentionItem[], seq }`, oldest first |
| POST | `/v1/attention/:id/dismiss` | Only `needs_you`, `stalled`, `failed` and `paused` items (409 for `approval`/`question`, 404 if not currently listed). Appends `attention.dismissed`. Dismissing `paused` is Resume: the project's automatic wakes resume and every agent decides again |
| GET | `/v1/threads/:id/diff` | `{ base, branch, files: [{ path, status: added\|modified\|deleted, additions, deletions }], patch }` against the thread's base, including uncommitted and untracked files. 409 for non-git or archived threads |
| GET | `/v1/threads/:id/files` | `?path=` a directory in the workspace. `[{ name, path, type: file\|dir, size }]`, directories first, `.git` hidden |
| GET | `/v1/threads/:id/files/raw/<path>` | Raw file. 403 if the path (or a symlink) leads outside the workspace, 404 if missing, 409 once archived |
| GET | `/v1/skills/:name/versions/:v` | A skill as it was at version `v` (same shape as the skill detail). Also under `/v1/projects/:id/skills/…`, resolving project then global |
| GET | `/v1/skills/:name/versions/:v/files/<path>` | Raw file of that version |

**Attention items** are derived, not stored: `{ id, kind, project_id, project_name, agent_id, title, detail, created_at, ref }`.

| kind | id | Present while | ref |
|---|---|---|---|
| `approval` | `approval:<approval_id>` | pending and not delegated to Desk | `approval_id`, `thread_id` |
| `question` | `question:<event_id>` | it is the project's latest `question.asked` and no `message.user` to Desk followed (a message to a thread does not answer Desk) | `event_id`, `options` |
| `needs_you` | `report:<event_id>:<i>` | it is in the project's latest `report`, not dismissed | `event_id` |
| `stalled` | `stalled:<thread>:<event_id>` | the thread is running/waiting, its latest `stalled` notice has no thread activity after it, not dismissed | `thread_id`, `event_id` |
| `failed` | `failed:<thread>` | the thread is `failed`, not archived, not dismissed | `thread_id` |
| `paused` | `paused:<event_id>` | the project's latest `system.notice` `wakes_paused` (agents woke each other 60 times, or were woken by thread starts and notices 150 times, in the last hour) has no `message.user` of the project after it, not dismissed. While it holds, only the user's messages and already queued runs start anything; writing to any agent of the project or dismissing the item resumes | `event_id` |

## Configuration

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/v1/config` | | `{ notifications: auto\|off }` |
| PATCH | `/v1/config` | `{ notifications? }` | Saved to `<dataDir>/config.json` |
| GET | `/v1/config/model-endpoint` | | `{ configured, source: env\|file\|keychain\|null, base_url }`. Never includes the key |
| PUT | `/v1/config/model-endpoint` | `{ base_url, api_key }` | Write-only. Stores the key in the macOS Keychain (`security -i`, stdin) and `base_url` in `config.json`, then reconfigures the model adapter without a restart. 501 without a Keychain. Keys are printable ASCII without spaces, quotes or backslashes |
| POST | `/v1/config/model-endpoint/test` | optional `{ base_url, api_key }` | Tests the candidate (or current) endpoint by listing models: `{ ok, models?, error? }`. Errors are scrubbed of the key |

Model access resolves in this order: `DESK_OPENAI_BASE_URL`/`DESK_OPENAI_API_KEY`, then `~/.config/cliproxyapi.env`, then `config.json`'s `base_url` with the Keychain key. Without any of them the daemon starts anyway; model calls behave like a proxy outage (agents pause) until an endpoint is set.

## Attachments

Images that agents looked at with `view_image`. Each one is copied to `<dataDir>/attachments/<sha256>.<ext>` when viewed, so transcripts keep showing what the agent saw even after the workspace file changes. A `tool.result` event lists its images in `payload.images: [{ sha256, media_type, width, height, bytes, name }]`; `name` is the path the agent used (relative to its workspace when inside it).

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/attachments/:sha256` | The image bytes (streamed), with its `Content-Type` (`image/png`, `image/jpeg`, `image/gif` or `image/webp`) and `Cache-Control: private, max-age=31536000, immutable` (content-addressed, so it never changes). 400 unless the id is 64 lowercase hex characters; 404 when no such attachment is stored |

An agent can also view an image another agent of its project looked at: `view_image` takes `attachment:<sha256>` as a path (Desk's `read_thread` shows these references). Only images shown in the project's 5,000 most recent tool results are found this way.

When the model endpoint refuses a request because of an image in it (for example `400 Could not process image`), the agent loop retries with some of its images sent as text: first the newest group, then only the older ones, then both. It records nothing until a retry goes through. It then appends `images.withheld { run_id, images: [{ tool_call_id, sha256, name }], reason }` for each group that held a refused image. Those showings are sent as text from then on, and `view_image` refuses an image that was withheld on its own. After a `413` or "too large" answer, the loop halves the bytes of images it sends instead. A lower budget that went through is kept for that model until the daemon restarts.

## Event stream (WebSocket)

Connect to `ws://127.0.0.1:<port>/v1/stream?token=<token>`. A bad token closes the socket with code 4401.

The client sends:

```json
{ "subscribe": { "project_id": "<id>" | "*", "after_seq": 0 } }
```

It may also identify itself; a client that shows its own notifications silences the daemon's notifier while connected:

```json
{ "hello": { "client": "desktop", "notifications": true } }
```

The server sends:

- `{ kind: "event", event }` for each persisted event after `after_seq`, replayed synchronously without gaps, then live.
- `{ kind: "ready", seq }` once the replay is complete.
- `{ kind: "ephemeral", event }`: live only, e.g. `assistant.delta { run_id, text }` token streaming.
- `{ kind: "error", message }`.

To resume after a disconnect, subscribe again with the last `event.id` you received; you get exactly the missed events. Events about global skills changed outside any project carry `project_id: "_global"` and only reach `*` subscribers.

**Stored event** shape: `{ id, project_id, agent_id | null, ts, type, payload }`. Types, with the normative payloads in `packages/protocol/src/events.ts`:

| Area | Types |
|---|---|
| Projects | `project.created`, `project.updated`, `project.archived`, `source.added`, `source.updated`, `source.removed` |
| Agents | `agent.created`, `agent.status_changed`, `agent.result`, `agent.revision`, `agent.model_switched`, `agent.skills_changed`, `agent.archived` |
| Coordination | `plan.updated`, `report`, `question.asked`, `whats_up.updated` (Desk's What's up, written with `update_whats_up`; when Desk ends a turn after changing things without rewriting it, the runtime sends Desk a `message.agent` of kind `reminder`, which clients do not show) |
| Messages and runs | `message.user` (`question: true` for the user's Ask), `message.agent` (see Messages below), `inbox.drained`, `run.started` (`answering` on an answer run), `run.finished`, `assistant.message`, `tool.call`, `tool.result` (with `images` for `view_image`, see Attachments), `images.withheld`, `context.compacted`, `usage` |
| Approvals | `approval.requested`, `approval.resolved` |
| Services | `service.started`, `service.url`, `service.exited`, `service.stopped` (reason `requested`, `restart`, `thread_archived`, `project_archived`, `daemon_shutdown` or `daemon_restart`) |
| Knowledge | `memory.written`, `memory.deleted`, `artifact.published`, `skill.saved`, `skill.deleted` |
| System | `system.notice`: `proxy_down`, `proxy_up`, `daemon_restart`, `wakes_paused`, …; `attention.dismissed` |

**Agent statuses:** `idle`, `queued`, `running`, `waiting` (on a reply, an approval or threads), `done`, `failed`, `cancelled`.

**Messages.** A `message.agent` is stored on its recipient's stream (`agent_id`) with `from_agent_id`, `from_label` (`Desk`, or `thread "<title>" (<id>)`), `kind` and `text`. Kinds: `note`, `update`, `question`, `blocker`, `revision`, `answer`, `start` (Desk's opening message to a new thread, which clients hide), the runtime's notices (`completed`, `failed`, `cancelled`, `approval`, `stalled`) and `reminder` (for Desk alone). Optional fields:

- `tracked: true`: a question whose state the runtime follows: open, then answered, closed (the runtime wrote the answer) or withdrawn (the asker finished).
- `reply_to`: on an `answer`, the id of the question it answers. A note or update to an agent whose question the sender has seen is stored as its answer.
- `auto: true`: an answer the runtime wrote because the recipient could not answer (it was stopped, archived, hit its step limit or failed); its text says why, in parentheses.
- `tool_call_id`: the tool call that sent the message (absent on runtime notices and closures).

A tracked question to an idle, done or failed thread, or another thread's question to a waiting one, starts an **answer run** (Desk's question to a waiting thread runs it instead). So does a question a thread read in a full run and left open, once that run ends. The user's Ask (`message.user` with `question: true`, sent with `POST /v1/threads/:id/messages`) starts one only on an idle, done or failed thread: a running, queued, waiting or cancelled thread reads it as an ordinary message, which may resume a stopped thread and change its work. In an answer run, `run.started` carries `answering: <question id>`, the thread answers from its context without changing its status (a waiting thread keeps waiting), result or branch, and the same run's `run.finished` ends it. Clients show "answering …" from the start until that end. `@desk/protocol` folds these events into one view for clients and the runtime (`foldMessages` in `messages.ts`): who asked whom, what is still open, and which thread is answering.
