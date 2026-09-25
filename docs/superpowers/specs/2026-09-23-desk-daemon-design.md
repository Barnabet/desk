# Desk — Daemon & Agent Runtime Design (v1)

- **Date:** 2026-09-23
- **Status:** Implemented — v1.0.0 (see §14 for deviations)
- **Scope:** `deskd` daemon, agent runtime, toolset, policy gate, Desk coordinator, memory, library, public API, `desk` dev CLI.
- **Explicitly out of scope:** all UI/UX (the Electron app is designed separately and consumes the API defined here).

---

## 1. Purpose

Desk is a local-first, single-user project manager/planner/builder for macOS, inspired by the redesigned Claude Code *projects*. You describe what needs to get done; a per-project coordinator agent — **Desk** — scopes the request, delegates work to parallel **threads**, supervises them, reviews their output, assembles results, and reports back. Work continues when no client is open. Context accumulates over time through a shared project **memory** and a **library** of files and artifacts.

Projects hold **general work**: code is one kind of work among research, writing, analysis and planning. Git is a capability threads use when a project has repos attached, not the core model.

### Success criteria for v1

1. A user can create a project with a goal, attach local folders/repos, and converse with Desk through the API (and the `desk` CLI).
2. Desk spawns multiple threads that run concurrently, each in an isolated workspace, and threads can be steered directly.
3. Desk reviews thread output, can send work back, and reports assembled results — including branch/PR merge order for code work.
4. Memory persists across threads and sessions; supersession keeps it consistent.
5. The daemon survives client disconnects, app restarts, daemon crashes and reboots without losing agent progress.
6. Rate limits and proxy outages degrade gracefully (queue/backoff/fallback), never corrupt state.
7. Threads cannot write outside their workspace (OS-enforced for shell).

---

## 2. Key decisions

| Decision | Choice | Rationale |
|---|---|---|
| Deployment | Local-first on the user's Mac | Personal tool; uses local code, tools and network. |
| Process layout | Headless daemon `deskd` + clients | Agents outlive clients; headless-testable; any future client (Electron, phone, CLI) uses one API. |
| Agent engine | **Custom agent loop** on the `openai` Node SDK, **Chat Completions API only** | Full control of loop, tools, compaction and coordination. |
| Model access | Local **CLIProxyAPI** at `http://127.0.0.1:8317/v1` | Serves Claude and GPT models behind one OpenAI-compatible surface. |
| Models | Default `claude-opus-5-5`; options `claude-fable-5-1`, `gpt-6-astra`, `gpt-6-sol` | User choice. Selectable per project for Desk and threads, overridable per thread. |
| Autonomy | Autonomous inside guardrails | Workspace actions auto-approved; outward-facing actions governed by an editable policy; unmatched risk → approval. |
| Persistence | SQLite (WAL), **event-sourced** | One mechanism for durability, crash-resume, live streaming and client catch-up. |
| Language | TypeScript everywhere, pnpm monorepo | Shared zod schemas end-to-end. |

### Verified proxy behaviour (probed 2026-09-23)

- `POST /v1/chat/completions` with `tools` returns well-formed `tool_calls` for `claude-opus-5-5`, `gpt-6-astra`, `gpt-6-sol`.
- `POST /v1/responses` **fails for Claude models** (proxy does not translate it) → never use the Responses API.
- `claude-fable-5-1` returned `rate_limit_error` at probe time → rate limiting is a normal operating condition, not an edge case.
- Tool-call IDs differ by family (`toolu_…` vs `call_…`); the model adapter must treat them as opaque strings.

---

## 3. Architecture

```
┌──────────────┐  ┌──────────────┐  ┌───────────────────┐
│ desk CLI     │  │ Electron app │  │ future clients     │
│ (v1, dev)    │  │ (separate)   │  │ (phone, web…)      │
└──────┬───────┘  └──────┬───────┘  └─────────┬─────────┘
       └──── HTTP + WebSocket (127.0.0.1, bearer token) ─┘
                              │
┌─────────────────────────────▼─────────────────────────────┐
│ deskd                                                      │
│  API (Hono)  ──►  Commands  ──►  Event log (SQLite)        │
│                                   │   ▲                    │
│                     projections ◄─┘   │ append             │
│  Scheduler ──► Agent runtime (Desk + threads) ──► Tools    │
│                 │                               │          │
│           Model adapter ──► CLIProxyAPI     Policy gate    │
│                                          ► sandbox-exec    │
└────────────────────────────────────────────────────────────┘
```

### Repository layout

```
desk/
  packages/
    protocol/   zod schemas: API requests/responses, events, domain types
    core/       event store, projections, agent runtime, model adapter,
                scheduler, tools, policy gate, sandbox, memory, library,
                Desk & thread role definitions
  apps/
    daemon/     deskd: Hono HTTP/WS server, lifecycle, launchd integration
    cli/        desk: dev client over the public API
  test/
    fake-model/ scriptable OpenAI-compatible fake server
    e2e/        daemon-level scenarios through the public API
  docs/
```

`core` has no HTTP knowledge; `daemon` has no agent logic. `protocol` is the only thing clients depend on.

### Stack

- Node 22, TypeScript (strict), pnpm workspaces
- `openai` SDK (Chat Completions, streaming)
- Hono + `@hono/node-server` + `ws` for API and stream
- `better-sqlite3` + Drizzle ORM (WAL mode)
- zod for all schemas; `ulid` for IDs
- Vitest for unit/integration tests
- CLI: `commander` + a minimal line-based renderer

---

## 4. Domain model

All IDs are ULIDs. Timestamps are ISO-8601 UTC.

### Project

`id, name, goal, instructions, settings, created_at, archived_at?`

`settings`:

| Field | Default | Meaning |
|---|---|---|
| `desk_model` | `claude-opus-5-5` | Model for the coordinator |
| `thread_model` | `claude-opus-5-5` | Default model for threads |
| `fallback_model` | `null` | Model used after sustained rate limiting |
| `max_concurrent_threads` | `4` | Running (not queued) threads per project |
| `check_in` | `normal` | `minimal \| normal \| detailed` report cadence/detail |
| `autonomy` | `dispatch-freely` | or `ask-before-dispatch` |
| `review_rounds` | `2` | Max send-back rounds per thread |
| `policy` | default rules (§7.3) | Ordered policy rules |

### Source

A local path attached to a project: `id, project_id, path, kind: folder | git, label`. Sources are readable by all project agents. `git` sources let threads get worktrees.

### Agent

`id, project_id, role: desk | thread, status, model, created_at`

- Exactly one `desk` agent per project, created with the project. Its conversation **is** the project chat.
- `thread` agents additionally have: `title, brief, parent_id (desk agent), workspace_path, git: { source_id, branch, base } | null, result: { summary, artifacts[] } | null, review_round`.

Thread status machine:

```
queued ─► running ─► done
   │         │  ▲ ─► failed
   │         ▼  │ ─► cancelled
   │       waiting (on user | desk | approval)
   └───────────────► cancelled
```

`done` threads can be reopened to `queued` by Desk (send-back) or by a direct user message.

Desk status: `idle | running | waiting`.

### Event (source of truth)

`events(id INTEGER PK AUTOINCREMENT, project_id, agent_id?, type, payload JSON, ts)`

The global autoincrement `id` is the stream cursor (`seq`). Event types (payloads defined in `protocol`):

- `project.created | project.updated | source.added | source.removed`
- `agent.created | agent.status_changed | agent.model_switched`
- `message.user` (to Desk or a thread) · `message.agent` (agent→agent: desk↔thread)
- `run.started | run.finished` (with reason: `yielded | no_tool_calls | max_steps | stopped | error`)
- `assistant.delta` (streamed text; **not persisted** — broadcast only) · `assistant.message` (final, persisted)
- `tool.call | tool.result` (result includes `status: ok | error | denied | interrupted`)
- `approval.requested | approval.resolved`
- `plan.updated`
- `memory.written | memory.superseded | memory.deleted`
- `artifact.published`
- `report` (structured Desk report)
- `context.compacted`
- `usage` (tokens per model call)
- `system.notice` (proxy down/up, fallback engaged, daemon restarted)

Projections (tables rebuilt/updated from events inside the same transaction as the append): `agents`, `threads`, `transcripts` (per agent message list for model context), `plans`, `memory`, `artifacts`, `approvals`, `usage_totals`.

### Plan

One per project, owned by Desk: an ordered list of work items `{ id, title, status: todo | in_progress | done | dropped, thread_ids[], notes }`. Updated via Desk's `update_plan` tool; survives compaction because it is state, not conversation.

### Memory entry

`id, project_id, kind: fact | decision | preference | contact | note, content, source: user | agent:<id>, supersedes?: id, superseded_by?: id, created_at`

- Active memory = entries with no `superseded_by`.
- Every agent's system prompt includes a **memory digest**: all active `preference` and `decision` entries plus the most recent active `fact | contact | note` entries, capped at ~4k tokens (oldest dropped first, with a line noting that more is searchable).
- `memory_search` does case-insensitive keyword + FTS5 search over active entries (SQLite FTS5). Embeddings are out of scope for v1.

### Artifact (library item)

`id, project_id, path (relative to project library dir), title, kind: file | report | code | data | other, origin: user | agent:<id>, description, created_at`

### Skill

A reusable procedure in the Agent Skills format (compatible with Claude Code skills): a directory `<name>/` with `SKILL.md` (YAML frontmatter `name`, `description`, optional extra keys; Markdown instructions) plus optional `scripts/`, `references/`, `assets/`. Scope `global` (every project of the user) or `project` (shadows a global skill of the same name). The filesystem is the source of truth, so users can hand-edit skills; every change keeps the previous version under `.history/<name>/<version>/` (restorable). Changes emit `skill.saved` / `skill.deleted` events. Agents carry `active_skills` (set at spawn or with `skill_activate`). See §8.4.

### Approval

`id, project_id, requested_by (agent id), tool, args, reason, status: pending | approved | denied, resolved_by: user | desk, resolution_note?, created_at, resolved_at?`

### On-disk layout

```
~/Library/Application Support/Desk/
  desk.db              SQLite (WAL)
  daemon.json          { port, token, pid, version } — mode 0600
  daemon.lock          single-instance lock
  logs/deskd.log
  projects/<project_id>/library/
  projects/<project_id>/skills/<name>/   project skills (+ .history/)
  skills/<name>/                         global skills (+ .history/)
  workspaces/<thread_id>/          scratch dir or git worktree
  workspaces/<thread_id>/.desk/    truncated tool outputs, bash logs
```

---

## 5. Agent runtime

### 5.1 Inbox and runs

Each agent has an **inbox** (a projection over `message.*`, `approval.resolved`, and — for Desk — thread lifecycle events). An agent with unread inbox items and no active run becomes **runnable** and is handed to the scheduler.

A **run** is one activation:

1. Assemble context (§5.3) and drain the inbox into the conversation.
2. Call `chat.completions.create({ model, messages, tools, stream: true, ...adapterParams })`. Emit `assistant.delta` per chunk; on completion persist `assistant.message` and `usage`.
3. If the response contains tool calls: for each, pass through the policy gate (§7), execute allowed calls (**independent calls in one turn execute concurrently**), persist `tool.call`/`tool.result`. Go to 2.
4. The run ends when:
   - the model responds with no tool calls (`no_tool_calls`), or
   - the agent calls a yielding tool — `complete`, `wait_for_reply`, `wait_for_threads` (`yielded`), or
   - a tool raised an approval and the agent has nothing else to do (`yielded`, status `waiting`), or
   - step limit reached — 200 steps/run for threads, 60 for Desk (`max_steps`; the agent is told on its next activation), or
   - stopped by user/Desk (`stopped`), or
   - unrecoverable error (`error`).

### 5.2 Steering (mid-run injection)

Inbox items arriving during a run are injected **at the next step boundary** (before the next model call) as user-role messages tagged with their origin (`[from user]`, `[from Desk]`). They do not wait for the run to finish.

`stop` aborts the in-flight model request (`AbortController`), sends SIGTERM (then SIGKILL after 5s) to the agent's child processes, records `run.finished{stopped}`, and sets the agent to `cancelled` (threads) or `idle` (Desk).

### 5.3 Context assembly

`messages = [system, ...compacted_checkpoint?, ...conversation_tail, ...new_inbox]`

System prompt = role prompt (Desk or thread) + project name/goal/instructions + memory digest + role-specific state:
- Desk: current plan, thread roster (id, title, status, one-line last activity), pending approvals.
- Thread: its brief, workspace path, git info, mounted sources, review feedback if sent back.

### 5.4 Compaction

- After each model call, compare `usage.prompt_tokens` to the model's `context_window` (from the registry).
- At ≥ 70%: run a compaction call on the same model that summarises everything except the last 6 turns into a structured checkpoint (`goal`, `decisions`, `current_state`, `files_touched`, `open_questions`, `next_steps`). Persist as `context.compacted` with the checkpoint and the event-id range it replaces. Transcript projection then uses checkpoint + tail.
- Tool results longer than 20k characters are truncated in context (head + tail + note) and saved in full under `workspaces/<id>/.desk/outputs/<tool_call_id>.txt`, with the path in the truncated result.

### 5.5 Model adapter

The only module that talks to the `openai` SDK.

- Client: `new OpenAI({ baseURL, apiKey })` from config (§9.2).
- **Model registry** (persisted, editable via API): `{ id, family: claude | gpt, context_window, max_output_tokens, supports_reasoning_effort, concurrency }`. Seed entries for the four models with conservative defaults (`context_window: 200_000`, `concurrency: 4` for Opus/Astra/Sol, `2` for Fable); exact values are confirmed during implementation from the proxy/provider and edited in the registry, not hard-coded elsewhere.
- Normalises: tool-call IDs (opaque), `finish_reason` variants, streamed tool-call argument assembly, missing `usage` in stream (request `stream_options: { include_usage: true }`; if absent, estimate from characters and mark `estimated: true`).
- Passes `reasoning_effort` only when the registry says the model supports it (verified per model in the implementation spike).
- Classifies errors: `rate_limited` (429, `rate_limit_error`), `transient` (408/5xx/network), `proxy_down` (connection refused), `context_overflow`, `fatal` (4xx other).

### 5.6 Scheduler

- Global queue of runnable agents. A run starts only when both caps have capacity: **per-model concurrency** (registry) and **per-project `max_concurrent_threads`** (Desk runs don't count toward the thread cap but do count toward model concurrency).
- FIFO within priority; Desk runs have priority over thread runs so coordination never starves.
- Retries per model call: `rate_limited`/`transient` → exponential backoff with full jitter (base 2s, cap 60s, max 6 attempts). After exhausting retries on `rate_limited`, if `fallback_model` is set: emit `agent.model_switched`, retry on fallback for the remainder of this run. Otherwise the agent → `failed` (thread) or Desk posts a `system.notice` and waits.
- `context_overflow` → force compaction then retry once.
- `proxy_down` → **global pause**: stop dispatching new model calls, emit one `system.notice`, health-check `GET /v1/models` every 10s, resume all paused calls on recovery (they are retried, not failed).

### 5.7 Durability and resume

- Every event append and its projection updates happen in one SQLite transaction.
- `tool.call` is persisted **before** execution; `tool.result` after. A model response is persisted only when complete (deltas are ephemeral).
- On startup the daemon scans for runs without `run.finished`:
  - Any `tool.call` without a `tool.result` gets a synthetic `tool.result{status: interrupted, content: "Daemon restarted during execution; state of side effects unknown — verify before retrying."}`.
  - A `run.finished{reason: error, detail: "daemon_restart"}` is appended, and the agent is marked runnable again so its next run resumes from the persisted transcript.
- Background shell jobs do not survive a restart; their `bash_output` returns an interrupted status.

### 5.8 Usage

Every model call emits `usage { agent_id, model, prompt_tokens, completion_tokens, cached_tokens?, estimated }`. `usage_totals` projection aggregates by project × agent × model × day. Exposed via API. No currency cost in v1.

---

## 6. Toolset

All tools are defined once with a zod input schema (converted to JSON Schema for the model), a policy classification function, and an executor. Tools are grouped; each role receives a fixed subset.

### 6.1 Thread tools

| Tool | Notes |
|---|---|
| `read_file(path, offset?, limit?)` | Workspace, sources, library. Line-numbered output. |
| `write_file(path, content)` | Workspace only. |
| `edit_file(path, old, new, replace_all?)` | Workspace only; exact match, must be unique unless `replace_all`. |
| `list_dir(path)`, `glob(pattern, root?)`, `grep(pattern, root?, glob?)` | Readable roots only. `grep` uses ripgrep if present, else a JS fallback. |
| `view_image(paths[1–8], purpose?)` | Readable roots only. PNG, JPEG, GIF, WebP (≤ 3.75 MB and 8000 px per image, 20 MB per call); other formats get a hint naming the file skill that renders them. Images are stored content-addressed and shown to the model in a user message after the results (the 8 most recent as pixels). Added by the file-type skills spec (2026-09-24). |
| `bash(command, timeout_s?=120)` | cwd = workspace; sandboxed (§7.4); stdout/stderr streamed as tool progress events and captured. |
| `bash_background(command)` → job id; `bash_output(job_id)`; `bash_kill(job_id)` | For servers/long builds. Killed when the thread ends. |
| `web_fetch(url)` | Fetch + HTML→Markdown (Readability + Turndown), 100k char cap. On 403/404/410/451 or an unreachable public host, returns the closest Wayback Machine snapshot, labelled "Archived copy from <date>" (web research spec §4.2). |
| `web_search(query)` | Provider chain: Brave Search API when `BRAVE_API_KEY` is set, then DuckDuckGo HTML → Bing RSS → Marginalia; a provider that errors, answers 202/429 or finds nothing passes to the next, and the result names the provider that answered (web research spec §4.1). |
| `git_status`, `git_diff(ref?)`, `git_commit(message, paths?)`, `git_push()`, `open_pr(title, body, base?)` | Only when workspace is a worktree. `open_pr` uses `gh`. |
| `memory_search(query)`, `memory_write(kind, content, supersedes?)` | |
| `library_list()`, `library_read(path)`, `library_publish(workspace_path, title, kind, description)` | Publish copies a workspace file into the library. |
| `message_desk(text, kind: question \| update \| blocker)` | `question`/`blocker` should be followed by `wait_for_reply`. |
| `wait_for_reply()` | Yields; thread → `waiting`. |
| `complete(summary, artifacts[])` | Yields; thread → `done`; triggers Desk review. `artifacts` are library paths already published. |

### 6.2 Desk tools

| Tool | Notes |
|---|---|
| `spawn_thread(title, brief, source_ids?, git_source_id?, model?)` | Creates workspace (worktree on `desk/<slug>-<shortid>` off the repo's current HEAD, or scratch dir). Respects `autonomy` setting. |
| `message_thread(thread_id, text, kind?: note \| revision)` | `revision` reopens a `done` thread (send-back) and increments `review_round`. |
| `stop_thread(thread_id, reason)` | |
| `list_threads(status?)`, `read_thread(thread_id, mode: summary \| full, since?)` | |
| `review_diff(thread_id)` | `git diff base...branch` for worktree threads. |
| `wait_for_threads(thread_ids?)` | Yields until any listed thread emits a lifecycle event. |
| `update_plan(items)` | Replaces the plan. |
| `ask_user(question, options?)` | Yields; Desk → `waiting` until the user replies. |
| `resolve_approval(approval_id, decision, note)` | Only for approvals the policy delegates to Desk. |
| `report(headline, progress, needs_you[], results[])` | Emits a `report` event. |
| `update_settings(patch)` | Changes `check_in` / `autonomy` / models on user request; emits `project.updated`. |
| `memory_*`, `library_*` | As threads. |
| Read-only: `read_file`, `list_dir`, `glob`, `grep`, `view_image`, `bash_readonly`, `web_fetch`, `web_search` | `bash_readonly` runs under a sandbox profile with **no writable paths** except temp. |

---

## 7. Policy gate and sandbox

### 7.1 Classification

Before execution every tool call is classified to one of:

- **`auto`** — execute. Applies to: reads within readable roots; writes within the agent's own workspace; `bash` (sandboxed to the workspace); memory, library, messaging, plan, coordination tools.
- **`policy`** — evaluated against the project's ordered rules (§7.2); first match wins. Applies to `git_push`, `open_pr`, `web_fetch`, `web_search` (no match → `ask`) and to `bash`/`bash_background` (rules checked first; no match → `auto`, since the sandbox already confines them).
- Result `allow` → execute. `ask` → raise an approval. `deny` → return `tool.result{status: denied, reason}` to the model.

### 7.2 Rules

```ts
type PolicyRule = {
  tool: string;                          // tool name or "*"
  match?: { branch?: string;             // glob, for git_push/open_pr
            command?: string;            // regex, for bash
            domain?: string };           // glob, for web_fetch
  action: "allow" | "ask" | "deny";
  delegate_to_desk?: boolean;            // "ask" goes to Desk first
};
```

### 7.3 Default policy

1. `bash` `command` ~ `\b(sudo|rm\s+-rf\s+[/~]|mkfs|dd\s+if=|curl[^|]*\|\s*(ba)?sh|chmod\s+-R\s+777)\b` → `ask`
2. `git_push` `branch: desk/*` → `allow`
3. `git_push` → `deny` (threads never push to non-`desk/*` branches)
4. `open_pr` → `ask` (`delegate_to_desk: false`)
5. `web_fetch` → `allow`
6. `web_search` → `allow`

### 7.4 Approvals flow

1. Gate returns `ask` → persist `approval.requested`; the tool call stays pending; the thread → `waiting(approval)`; Desk's inbox receives the event.
2. If `delegate_to_desk`, Desk may call `resolve_approval`; otherwise (or if Desk escalates) the approval is surfaced to the user via the API stream.
3. On `approval.resolved`: approved → execute the pending call and append its real result; denied → append `tool.result{status: denied, reason: note}`. The thread becomes runnable.

### 7.5 Path confinement

All file tools resolve `realpath` (following symlinks) and verify the resolved path is under an allowed root for the operation. Readable roots: agent workspace, project sources, project library. Writable roots: agent workspace only (library only via `library_publish`). Violations → `denied`.

### 7.6 Shell sandbox

`bash`, `bash_background` and `bash_readonly` run as `sandbox-exec -f <generated profile> /bin/zsh -c <command>`:

- Profile: deny all file writes except the workspace, `/private/tmp`, `/private/var/folders` (per-user temp), and `/dev/null`/`/dev/tty*`. Reads allowed globally (needed for toolchains). Network allowed. `bash_readonly`: no workspace write.
- Environment is **scrubbed**: only `PATH`, `HOME`, `LANG`, `TERM`, `TMPDIR`, `USER`, `SHELL` plus `DESK_WORKSPACE`; never the proxy key or other daemon env.
- `sandbox-exec` is deprecated but functional on current macOS. If it is unavailable at startup, the daemon logs a `system.notice`, and every `bash` call is classified `ask` (degraded mode) — never silently unsandboxed.

---

## 8. Desk coordinator behaviour

Desk uses the same runtime with the Desk role prompt, Desk tools, and these triggers.

### 8.1 Triggers and batching

Desk's inbox receives: user messages; thread `complete`/`failed`/`cancelled`; `message_desk`; `approval.requested` from threads; stall notices (a running/waiting thread with no events for 15 minutes); approval resolutions; `ask_user` replies. Items arriving while Desk is running are injected at step boundaries (§5.2); items arriving while idle are coalesced into a single activation.

### 8.2 Role prompt responsibilities

The prompt encodes this workflow (the prompt text lives in `core/roles/desk.md` and is versioned with the code):

1. **Scope** — use goal, memory, library and sources (read-only tools) to understand the request. Ask the user only when genuinely blocked. On project creation, propose concrete first work items.
2. **Plan & dispatch** — maintain the plan via `update_plan`. Write self-contained briefs: objective, context, constraints, definition of done, expected return. Prefer routing follow-ups to an existing relevant thread over spawning. With `autonomy: ask-before-dispatch`, present the plan and wait for user go-ahead.
3. **Supervise** — answer thread questions from knowledge/memory when possible; escalate otherwise; redirect stalled or drifting threads.
4. **Review** — on `complete`, check the result against the brief (summary, artifacts, `review_diff` for code, optional read-only test run). Accept, or send back with specific feedback via `message_thread(kind: revision)`, up to `review_rounds`. After the limit, accept with noted caveats or escalate.
5. **Assemble & report** — combine results, publish combined artifacts, `report` to the user. For code work, list branches/PRs and the order to merge them. Merge conflicts are described, never force-resolved. Desk never merges.
6. **Curate memory** — record decisions, facts, preferences and contacts; supersede stale entries rather than contradicting them.
7. **Respect communication settings** — `check_in` governs how often Desk emits `report`s (`minimal`: on completion/blocker only; `normal`: plus milestones; `detailed`: plus each thread completion). Conversational requests ("check in less") update settings via an internal `update_settings` tool and are recorded as `preference` memory.

### 8.3 Thread role prompt

Encodes: work only toward the brief; stay in the workspace; publish outputs to the library; use `message_desk` for questions/blockers instead of guessing on consequential ambiguities; write memory for durable facts discovered; finish with `complete` including an honest summary (what was done, what was not, how it was verified).

### 8.4 Skills

Skills are how the system gets better at recurring work; they are central to both Desk and threads (user requirement, 2026-09-23).

- **Prompts.** Every agent's system prompt embeds the full instructions of its active skills (per-run snapshot, so they survive compaction; 12k chars per skill, 40k total, beyond which the agent reads them with `skill_read`) and lists the other visible skills by name and description (≤ 60; `skill_list` searches).
- **Tools (all agents).** `skill_list`, `skill_read`, `skill_activate` (returns the instructions immediately and keeps them active), `skill_run` (runs a file of the skill in the agent's shell sandbox, cwd = workspace, `SKILL_DIR` set, interpreter by shebang or extension; gated like `bash`).
- **Tools (Desk only).** `skill_write` (create/refine with a change note; `from_dir` installs a draft from a project workspace), `skill_delete` (gated `ask`). Threads cannot modify installed skills: they write drafts (`<workspace>/skill-drafts/<name>/`) and report them in `complete.skill_drafts`; the completion notice lists them for Desk to review and install — Desk supervises every installed skill.
- **Dispatch.** `spawn_thread.skills` activates skills on a new thread from its first step; `message_thread.skills` adds more.
- **Desk behaviour.** Check skills when scoping; activate relevant ones; attach relevant ones to every thread; capture requested automations and user corrections about *how* work is done as skills (scripts built and tested by a thread, then installed); refine skills from feedback; global scope for general-purpose automations, project scope for project-specific ones.
- **User surface.** HTTP `/v1/skills…` (global) and `/v1/projects/:id/skills…` (project; reads resolve project-then-global): list, show, file download, PUT, import from a local directory, delete, history, restore. CLI `desk skills`, `desk skill show|import|rm|history|restore`.

---

## 9. Daemon, API and CLI

### 9.1 Lifecycle

- `deskd` binds `127.0.0.1` on a configured port (default `7433`, fallback to an ephemeral port if taken), writes `daemon.json` (0600) with `{ port, token, pid, version }`. Token: 32 random bytes, regenerated on each start.
- Single-instance lock (`daemon.lock` with pid check).
- Installed as a **launchd user agent** (`~/Library/LaunchAgents/dev.desk.deskd.plist`, `KeepAlive`, `RunAtLoad`) by `desk up --install`; `desk up` without `--install` starts it detached for development.
- SIGTERM: stop accepting requests, abort in-flight model calls, record `run.finished{error: daemon_shutdown}` for active runs, kill child processes, close DB. Resume on next start per §5.7.
- Version handshake: `GET /v1/health` → `{ version, protocol_version }`; clients refuse on protocol mismatch.

### 9.2 Configuration

Resolution order for model access:
1. `DESK_OPENAI_BASE_URL` / `DESK_OPENAI_API_KEY` env vars
2. `~/.config/cliproxyapi.env` (`CLIPROXY_BASE_URL`, `CLIPROXY_API_KEY`) — base URL normalised to end in `/v1`

The key is held in daemon memory only: never persisted to the DB, logged, or passed to tool processes.

### 9.3 HTTP API (all under `/v1`, `Authorization: Bearer <token>`)

| Method & path | Purpose |
|---|---|
| `GET /health` | Version handshake (no auth) |
| `GET/POST /projects`, `GET/PATCH /projects/:id`, `POST /projects/:id/archive` | Projects & settings |
| `POST/DELETE /projects/:id/sources[/:sid]` | Attach/detach sources |
| `POST /projects/:id/messages` | Message Desk |
| `GET /projects/:id/plan` | Current plan |
| `GET /projects/:id/threads` | Thread roster |
| `GET /threads/:id` · `GET /threads/:id/transcript?since=` | Thread detail & transcript |
| `POST /threads/:id/messages` · `POST /threads/:id/stop` | Steer / stop |
| `GET /projects/:id/approvals?status=` · `POST /approvals/:id/resolve` | Approvals |
| `GET/POST/PATCH/DELETE /projects/:id/memory[/:mid]` | Memory CRUD (user edits emit memory events) |
| `GET /projects/:id/library` · `POST /projects/:id/library` (multipart) · `GET /projects/:id/library/*path` | Library |
| `GET /projects/:id/usage` | Usage totals |
| `GET/PUT /models` | Model registry |
| `GET /projects/:id/events?after=&limit=` | Paged raw events |

All request/response bodies are zod schemas in `packages/protocol`. Errors: `{ error: { code, message, details? } }` with appropriate HTTP status.

### 9.4 Stream

`GET /v1/stream?token=` (WebSocket). Client sends `{ subscribe: { project_id | "*", after_seq } }`. Server replays persisted events with `id > after_seq`, then streams live events (including ephemeral `assistant.delta` and tool progress, which carry the current `seq` but are not replayable). Clients resubscribe with their last seen `seq` after reconnect.

### 9.5 `desk` CLI (dev client)

- `desk up [--install] | down | status`
- `desk project new <name> --goal <text> [--source <path>...]` · `desk project list` · `desk project set <id> <key>=<value>`
- `desk chat <project>` — interactive: send messages to Desk; renders Desk output, reports, thread lifecycle lines, and pending approvals inline
- `desk threads <project>` · `desk tail <thread>` · `desk say <thread> <text>` · `desk stop <thread>`
- `desk approvals <project>` · `desk approve|deny <approval> [note]`
- `desk memory <project> [search]` · `desk library <project>` · `desk usage <project>`

The CLI uses only the public API and `protocol`; it has no access to `core`.

---

## 10. Error handling summary

| Failure | Behaviour |
|---|---|
| Model rate-limited | Backoff → fallback model (if set) → thread `failed` / Desk notice |
| Transient model/network error | Backoff retry (6 attempts) → as above |
| Proxy unreachable | Global pause, one notice, 10s health check, auto-resume |
| Context overflow | Forced compaction, retry once, then `failed` |
| Tool error | Returned to model as `tool.result{status: error}`; never crashes the run |
| Policy deny / path violation | `tool.result{status: denied}` with reason |
| Daemon crash/restart | Resume from event log; interrupted tool calls marked (§5.7) |
| Thread failure | Desk is notified and decides: retry, re-scope, or report |
| Worktree creation fails (dirty repo, missing base) | `spawn_thread` returns an error to Desk |
| Sandbox unavailable | Degraded mode: every `bash` requires approval |

Thread archive (API or Desk after assembly) removes the worktree directory; branches are kept.

---

## 11. Testing strategy

- **Fake model server** (`test/fake-model`): OpenAI-compatible `/v1/chat/completions` (streaming and non-streaming) and `/v1/models`, driven by per-test scripts keyed by agent role/brief — can emit text, tool calls, parallel tool calls, 429s, 5xx, connection drops, hangs.
- **Unit**: policy matching and classification; path confinement (symlink escapes); compaction thresholds and checkpoint assembly; event → projection reducers; model adapter normalisation (streamed tool-call assembly, error classification); scheduler caps and priority; memory digest capping and supersession.
- **Integration (daemon through public API + fake model)**:
  1. Create project → message Desk → Desk spawns two threads → one asks a question (`message_desk` + `wait_for_reply`), Desk answers → both complete → Desk sends one back once → report emitted.
  2. User steers a running thread mid-run; injected at the next step.
  3. Kill the daemon mid-tool-call → restart → interrupted result recorded → run resumes.
  4. Sustained 429 → fallback model engaged → `agent.model_switched` event.
  5. Proxy down → global pause notice → proxy back → runs continue.
  6. Policy `ask` → approval raised → resolved via API → tool executes.
  7. Stream reconnect with `after_seq` replays exactly the missed events.
- **Sandbox**: a `bash` write to `~/desk-sandbox-escape-test` fails; a write in the workspace succeeds; env does not contain the proxy key.
- **Git**: worktree thread commits, pushes to a local bare remote (allowed by policy); push to `main` is denied.
- **Live smoke (opt-in, `DESK_LIVE=1`)**: against the real proxy, one short project per model (`claude-opus-5-5`, `claude-fable-5-1`, `gpt-6-astra`, `gpt-6-sol`) exercising multi-step tool use through Chat Completions, streaming tool-call assembly, and `usage` presence.

---

## 12. Implementation-time verifications

To confirm early in implementation (spike tasks), without changing the design:

1. ✅ Verified 2026-09-23 (Plan 1 live smoke): streamed tool-call argument deltas and `stream_options.include_usage` work through the proxy for Claude and GPT families; Claude responses report `prompt_tokens_details.cached_tokens` (automatic prompt caching).
2. ✅ Verified 2026-09-23: multi-turn tool conversations (parallel `tool_calls` → `tool` results → final answer) round-trip for `claude-opus-5-5`, `gpt-6-astra`, `gpt-6-sol`.
3. ✅ Registry seed: every model at a conservative 200k `context_window` (compaction threshold and forced compaction on overflow cover any mismatch); `reasoning_effort` is not exposed in v1.0 (`supports_reasoning_effort: false`). Editable via `PUT /v1/models`.
4. ✅ Verified 2026-09-23: the SBPL profile blocks writes outside the workspace for the shell and for child processes (node, python) on the current macOS; temp dirs, devices and the git common dir of worktrees stay writable.

---

## 13. Out of scope for v1 (planned)

- **UI/UX** — Electron desktop app, designed separately against this API.
- **v1.1** — MCP connectors, plugins; thread-level subagents/sub-delegation. (Skills moved into v1.0, §8.4.)
- **v1.2** — scheduled/proactive Desk wakeups and recurring goals.
- **Later** — phone access, cost in currency, embeddings-based memory retrieval, multi-user.

---

## 14. Deviations from this design (as built, v1.0)

- **System prompt snapshot per run.** The system prompt is built once per run rather than per step: a stable prefix for prompt caching, and no mid-run confusion (an agent seeing its own fresh artifact as pre-existing). Changes made by others reach a running agent as messages.
- **Library uploads** are JSON with base64 content instead of multipart (local-only API; simpler clients). The same applies to skill files.
- **WebSocket** uses `ws` attached to the Node HTTP server's `upgrade` event instead of `@hono/node-ws`.
- **Desk can write files** (`write_file`/`edit_file`) — confined to its own scratch workspace `projects/<id>/desk/` — to draft combined documents before publishing. Its shell stays read-only.
- **Skills moved into v1.0** (§8.4), at the user's request; they were planned for v1.1.
- **Migrations are squashed** into a single `0000_init` until the first external release; there is no upgrade path from pre-1.0 databases. After v1.0 shipped in the desktop app, schema changes are additive migrations on top of it (`0001_reasoning_effort`).
- **Compaction** keeps the last 6 conversation *messages* (not turns) and renders the summarised part as plain text for the checkpoint call (no tool schemas needed); the checkpoint is merged into the next user message.
- **Orphaned processes after a hard crash.** After `SIGKILL`, shell/background processes started by agents are not reaped or reattached; recovery records their tool calls as `interrupted`.
- **Role prompts** live in `packages/core/src/agent/prompts.ts` (code) rather than `core/roles/*.md`.
- **Reasoning effort is per level, not a boolean.** Each registry model lists the levels it accepts and an optional default; projects set a level for Desk and for threads, and `spawn_thread` can set one per thread. An unaccepted level is never sent: a call falls back to that model's default, or sends none. A legacy `models.json` with `supports_reasoning_effort` is upgraded on load.
- **Agents act on the project directly** (user decision, 2026-09-24: "the desk can handle everything"). Project sources are writable by agents by default (`agent_write`, off per source in Settings), services can run there, opening a PR no longer asks, and deleting temp folders is no longer a risky command. Still asking: `sudo`, deleting `/`, `~` or paths outside temp, piping downloads into a shell, and pushing to branches Desk did not create. Desk still never merges.
