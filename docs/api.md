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
| GET | `/v1/health` | Unauthenticated. `{ version, protocol_version, proxy: up\|down\|unknown, uptime_s }` |
| GET | `/v1/usage` | `?since=YYYY-MM-DD`. `{ rows: [{ project_id, model, prompt_tokens, completion_tokens }], totals }` across projects |
| GET | `/v1/models` | The model registry (`ModelInfo[]`: id, family, context_window, max_output_tokens, supports_reasoning_effort, concurrency) |
| PUT | `/v1/models` | Replace the registry (`ModelInfo[]`, validated). The registry is persisted to `models.json` |

## Projects

| Method | Path | Body / query | Notes |
|---|---|---|---|
| GET | `/v1/projects` | `?all=1` includes archived | |
| POST | `/v1/projects` | `CreateProjectRequest { name, goal?, instructions?, settings?, sources?[{path,label?}] }` | 201 with the overview. Creates the Desk agent |
| GET | `/v1/projects/:id` | | Overview: project, desk, sources, plan, threads, pending approvals, `last_seq` (the stream cursor to resume from) |
| PATCH | `/v1/projects/:id` | `UpdateProjectRequest { name?, goal?, instructions?, settings? }` | `settings` is a partial patch |
| POST | `/v1/projects/:id/archive` | | Stops every agent and hides the project |
| POST | `/v1/projects/:id/sources` | `{ path, label? }` | Detects `git` vs `folder`. 201 |
| DELETE | `/v1/projects/:id/sources/:sid` | | |
| POST | `/v1/projects/:id/messages` | `{ text }` | Message to Desk. 202; Desk wakes |
| GET | `/v1/projects/:id/chat` | paging | Desk conversation events: user and agent messages, assistant messages, reports, questions, notices |
| GET | `/v1/projects/:id/plan` | | `{ items: PlanItem[] }` or `null` |
| GET | `/v1/projects/:id/usage` | | `{ rows (per model), totals }` |
| GET | `/v1/projects/:id/events` | paging + `types` | Raw event log (limit ≤ 5000, default 500) |

**Project settings** (`ProjectSettings`, with defaults):

| Setting | Default | Notes |
|---|---|---|
| `desk_model`, `thread_model` | `claude-opus-5-5` | |
| `fallback_model` | `null` | Used for the rest of a run after sustained rate limiting |
| `max_concurrent_threads` | 4 | |
| `check_in` | `normal` | `minimal`, `normal` or `detailed` |
| `autonomy` | `dispatch-freely` | Or `ask-before-dispatch` |
| `review_rounds` | 2 | Maximum send-backs per thread |
| `policy` | see README | Ordered `PolicyRule[]`; the first match wins |

## Threads and approvals

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/projects/:id/threads` | `?all=1` includes archived |
| GET | `/v1/threads/:id` | Thread row: status, brief, result, git info, `active_skills`, … |
| GET | `/v1/threads/:id/transcript` | paging; every event of the thread |
| POST | `/v1/threads/:id/messages` | `{ text }`: steer the thread directly. 202 |
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

Global skills live under `/v1/skills`, project skills under `/v1/projects/:id/skills`. On project routes, `GET` resolves project-then-global, the same way agents see skills. Writes target the route's scope.

| Method | Path suffix | Body | Notes |
|---|---|---|---|
| GET | `` | | `SkillSummary[]`: name, scope, description, dir, version, `error?` (broken SKILL.md) |
| GET | `/:name` | | Detail: summary + instructions, frontmatter, files |
| GET | `/:name/files/<path>` | | Raw file; escaping paths return 400 |
| PUT | `/:name` | `SkillWriteRequest { description?, instructions?, files?[{path, content_base64}], remove_files?, change_note? }` | Creates the skill (201) or refines it (200). The previous version goes to history. A `SKILL.md` may be sent as a file |
| DELETE | `/:name` | | The last version stays in history |
| GET | `/:name/history` | | `[{ version, description, current, change_note, origin, ts }]`. `origin` is `user`, `agent:<id>` or `catalog:<id>@<sha>`; `origin` and `ts` are null for versions with no `skill.saved` record |
| POST | `/:name/restore` | `{ version }` | Restores that version as the newest one |
| POST | `/import` | `{ path, name? }` | Copies a local skill directory (e.g. `~/.claude/skills/x`). The name defaults to the frontmatter `name`. 201 |

Skill names match `^[a-z0-9]+(-[a-z0-9]+)*$` (≤ 64 chars); descriptions are ≤ 1024 chars. Size limits: at most 2 MB per file, 200 files and 10 MB per skill. Symlinks are skipped on install.

## Skill catalog

Reviewed skills pinned to a commit and a content digest, installed only at the user's request. The design is in `docs/superpowers/specs/2026-09-24-skill-catalog-design.md`.

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/v1/catalog` | | `CatalogItem[]`: the entry (`id`, `title`, `category`, `summary`, `license`, `homepage`, `source`, `digest`, `files`, `bytes`, `runtime`, `caveats`) plus `installs[]`. Each install is `{ scope, project_id, state, sha, runtime, runtime_reason }`, where `state` is `installed`, `update_available`, `modified` (edited after install) or `name_taken` (a same-named skill that isn't from the catalog). Scopes with nothing of that name are omitted |
| POST | `/v1/catalog/:id/prepare` | | Downloads the pinned archive (GitHub codeload) or reads the builtin skill, verifies the digest, and stages it. Returns `CatalogReview { entry, source_url, files[{path, size, script}], skill_md, license_text, warnings[{file, line, kind, excerpt}] }`. A digest mismatch is a 400 and nothing is staged |
| POST | `/v1/catalog/:id/install` | `{ scope?: 'global'\|'project', project_id?, replace_modified? }` | Installs through the skill store with origin `catalog:<id>@<sha>`, and returns `{ skill, state, runtime }` (201). A 409 means `name_taken`, or `modified` without `replace_modified: true` |

Warning kinds: `exec-block` (`` !`cmd` `` or ```` ```! ````, which Desk never runs), `pipe-to-shell`, `base64-blob`, `invisible-unicode`, `paste-site` and `memory-write`. The limits are the skill store's: 50 MB download, 10 MB, 200 files, 2 MB per file, and no links or special files.

## Desktop app endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/overview` | One `ProjectSummary` per open project: `project`, `desk_status`, `threads` (status, `reason`, current `activity` like `bash · python3 x.py`, model, `git_branch`, `skills`, `review_round`), `latest_report`, `plan_progress { done, total }` (dropped items excluded), `attention_count` |
| GET | `/v1/attention` | `?project_id=`. `{ items: AttentionItem[], seq }`, oldest first |
| POST | `/v1/attention/:id/dismiss` | Only `needs_you`, `stalled`, `failed` items (409 for `approval`/`question`, 404 if not currently listed). Appends `attention.dismissed` |
| GET | `/v1/threads/:id/diff` | `{ base, branch, files: [{ path, status: added\|modified\|deleted, additions, deletions }], patch }` against the thread's base, including uncommitted and untracked files. 409 for non-git or archived threads |
| GET | `/v1/threads/:id/files` | `?path=` a directory in the workspace. `[{ name, path, type: file\|dir, size }]`, directories first, `.git` hidden |
| GET | `/v1/threads/:id/files/raw/<path>` | Raw file. 403 if the path (or a symlink) leads outside the workspace, 404 if missing, 409 once archived |
| GET | `/v1/skills/:name/versions/:v` | A skill as it was at version `v` (same shape as the skill detail). Also under `/v1/projects/:id/skills/…`, resolving project then global |
| GET | `/v1/skills/:name/versions/:v/files/<path>` | Raw file of that version |

**Attention items** are derived, not stored: `{ id, kind, project_id, project_name, agent_id, title, detail, created_at, ref }`.

| kind | id | Present while | ref |
|---|---|---|---|
| `approval` | `approval:<approval_id>` | pending and not delegated to Desk | `approval_id`, `thread_id` |
| `question` | `question:<event_id>` | it is the project's latest `question.asked` and no `message.user` followed | `event_id`, `options` |
| `needs_you` | `report:<event_id>:<i>` | it is in the project's latest `report`, not dismissed | `event_id` |
| `stalled` | `stalled:<thread>:<event_id>` | the thread is running/waiting, its latest `stalled` notice has no thread activity after it, not dismissed | `thread_id`, `event_id` |
| `failed` | `failed:<thread>` | the thread is `failed`, not archived, not dismissed | `thread_id` |

## Configuration

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/v1/config` | | `{ notifications: auto\|off }` |
| PATCH | `/v1/config` | `{ notifications? }` | Saved to `<dataDir>/config.json` |
| GET | `/v1/config/model-endpoint` | | `{ configured, source: env\|file\|keychain\|null, base_url }`. Never includes the key |
| PUT | `/v1/config/model-endpoint` | `{ base_url, api_key }` | Write-only. Stores the key in the macOS Keychain (`security -i`, stdin) and `base_url` in `config.json`, then reconfigures the model adapter without a restart. 501 without a Keychain. Keys are printable ASCII without spaces, quotes or backslashes |
| POST | `/v1/config/model-endpoint/test` | optional `{ base_url, api_key }` | Tests the candidate (or current) endpoint by listing models: `{ ok, models?, error? }`. Errors are scrubbed of the key |

Model access resolves in this order: `DESK_OPENAI_BASE_URL`/`DESK_OPENAI_API_KEY`, then `~/.config/cliproxyapi.env`, then `config.json`'s `base_url` with the Keychain key. Without any of them the daemon starts anyway; model calls behave like a proxy outage (agents pause) until an endpoint is set.

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
| Projects | `project.created`, `project.updated`, `project.archived`, `source.added`, `source.removed` |
| Agents | `agent.created`, `agent.status_changed`, `agent.result`, `agent.revision`, `agent.model_switched`, `agent.skills_changed`, `agent.archived` |
| Coordination | `plan.updated`, `report`, `question.asked` |
| Messages and runs | `message.user`, `message.agent`, `inbox.drained`, `run.started`, `run.finished`, `assistant.message`, `tool.call`, `tool.result`, `context.compacted`, `usage` |
| Approvals | `approval.requested`, `approval.resolved` |
| Knowledge | `memory.written`, `memory.deleted`, `artifact.published`, `skill.saved`, `skill.deleted` |
| System | `system.notice`: `proxy_down`, `proxy_up`, `daemon_restart`, …; `attention.dismissed` |

**Agent statuses:** `idle`, `queued`, `running`, `waiting` (on a reply, an approval or threads), `done`, `failed`, `cancelled`.
