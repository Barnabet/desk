# Desk desktop app — handoff for the UI/UX agent

**From:** the backend build (Desk v1.0.0, tag `v1.0.0`, 2026-09-23).
**For:** the agent designing and building the Desk desktop app.
**Status of this doc:** the sections are labelled by status:
- **Decided**: choices the user made; do not reopen them.
- **Open**: choices that belong to you or the user.
- **Proposition**: suggestions only; take them or leave them.

Read alongside `README.md` (concepts, safety model) and `docs/api.md` (the full HTTP and stream contract).

---

## 1. What exists today

Desk v1.0 is a **headless backend**, and it is complete. Every feature below works end to end through the API and the `desk` CLI; the app only needs to be a client.

- **`deskd`:** a local daemon on `127.0.0.1` with a per-start bearer token. It is event-sourced on SQLite and keeps working when no client is open.
- **Projects:** goal, instructions, sources (folders and git repos, read-only for agents) and settings.
- **Desk:** the per-project coordinator agent. It scopes requests, keeps a plan, spawns parallel **threads**, answers their questions, reviews and sends work back, and assembles and reports results.
- **Threads:** full agents in isolated workspaces (scratch dirs, or git worktrees on `desk/*` branches). They can be steered, stopped and archived.
- **Approvals:** risky actions pause the agent until someone decides; policy rules can delegate some approvals to Desk.
- **Memory:** typed, searchable entries with supersession.
- **Library:** files published by agents or uploaded by the user.
- **Skills:** reusable procedures with scripts, at global or project scope. Desk authors and refines them from user requests, and threads start with the relevant ones active. Every change is versioned and can be restored.
- **Resilience:** crash recovery, a pause during proxy outages, a fallback model under rate limits, context compaction, and stall detection.
- **Live streaming:** every change is an event on a WebSocket, and assistant text streams token by token.

The CLI (`apps/cli`) is the reference client. Its renderer in `apps/cli/src/format.ts` shows how each event type is meant to be read.

## 2. Decisions already made (Decided)

| Decision | Detail |
|---|---|
| Local-first, macOS, single user | Everything runs on the user's Mac; there are no accounts and no cloud |
| General work, not code-only | Research, writing, analysis, planning and code. Git is one capability, not the organising model, so do not design an IDE |
| Architecture: headless daemon plus clients | The app is a client of `deskd`. It must not read or write `desk.db` or the data dir directly; everything goes through the API |
| Native desktop app | The user wants a native Mac desktop app. The UI/UX is designed by you, separately from the backend |
| Autonomy inside guardrails | Agents act on their own. The user steps in for approvals, questions and direction, not for every step |
| Models | Default `claude-opus-5-5`; also `claude-fable-5-1`, `gpt-6-astra` and `gpt-6-sol`, served by a local CLIProxyAPI |
| Skills are central | The user's explicit requirement: Desk and threads use skills, and Desk writes and refines global and project skills from requests. Treat skills as a first-class surface, not a settings page |
| Deferred | MCP connectors (v1.1), scheduled or proactive wakeups (v1.2), phone access, and cost in currency |

## 3. Open decisions (Open)

1. **App stack.** The spec wrote "Electron" as a placeholder; the user has only said "native desktop app".
   - **Electron** reuses the TypeScript and zod types in `@desk/protocol` and can ship the Node daemon inside the app.
   - **Tauri** gives a smaller app with the same web UI, but the Node daemon has to run as a sidecar.
   - **SwiftUI** is the most native, but the protocol types must be mirrored in Swift.
   - *Recommendation:* a TS web UI (Electron or Tauri) that imports `@desk/protocol` directly. Confirm with the user before you commit to one.
2. **Daemon lifecycle and packaging.** Today `deskd` runs from the repo through the `tsx` loader (`desk up`, or `desk up --install` for a launchd LaunchAgent). A shipped app needs a bundled daemon; see §6 item 3. Also decide who owns the daemon: the app starting it on demand, or a login LaunchAgent that is always on. *Recommendation:* the LaunchAgent, so that work continues with the app closed.
3. **Where the app lives in the macOS UI.** Choose between a window only; a window plus a menu-bar item that shows what needs the user; or notifications only.
4. **Visual design.** The design language, density and typography are all yours.

## 4. What the UI must cover

The surfaces are listed in rough priority order. The API column is the contract; see `docs/api.md` for bodies and paging.

| # | Surface | What the user does | API |
|---|---|---|---|
| 1 | **Projects home** | See all projects, what is active, and what needs attention; create a project | `GET /v1/projects`, `POST /v1/projects`, stream on `*` |
| 2 | **Desk conversation** (the heart of a project) | Brief Desk, read its replies and streaming text, see reports and questions, and answer | `POST /projects/:id/messages`, `GET /projects/:id/chat`, stream (`assistant.delta`, `assistant.message`, `report`, `question.asked`, `message.agent`) |
| 3 | **Attention: approvals and questions** | Approve or deny risky actions with context; answer Desk's `ask_user` questions (optional `options`); read the `needs_you` items in reports | `GET /projects/:id/approvals?status=pending`, `POST /approvals/:id/resolve`, events `approval.requested` / `approval.resolved`, `question.asked`, `report.needs_you` |
| 4 | **Threads** | Watch parallel threads live (status, current activity, model, branch, active skills); open one; steer it; stop or archive it | `GET /projects/:id/threads`, `GET /threads/:id`, `GET /threads/:id/transcript`, `POST /threads/:id/messages\|stop\|archive` |
| 5 | **Plan** | See Desk's plan items (`todo`, `in_progress`, `done`, `dropped`) linked to threads | `GET /projects/:id/plan`, event `plan.updated` |
| 6 | **Library** | Browse, preview and download results; upload files for agents | `GET/POST /projects/:id/library`, `GET …/library/file/<path>` |
| 7 | **Skills** | Browse global and project skills; read instructions and scripts; see where each is used (active on which threads); version history and restore; import (e.g. from `~/.claude/skills`); create or edit by hand. Also make "ask Desk to build or refine a skill" an obvious action | `/v1/skills…`, `/v1/projects/:id/skills…`, events `skill.saved` / `skill.deleted`, `agent.skills_changed` |
| 8 | **Memory** | Browse and search, add, correct (supersede) and delete entries | `/projects/:id/memory…` |
| 9 | **Project settings** | Goal and instructions, sources, `check_in`, `autonomy`, `review_rounds`, models (Desk, thread, fallback), max concurrent threads, policy rules | `PATCH /projects/:id`, `POST/DELETE /projects/:id/sources` |
| 10 | **System and health** | Daemon running or not; proxy down or up; recovered-after-crash notices; token usage per model; model registry | `GET /health`, event `system.notice` (`proxy_down`, `proxy_up`, `daemon_restart`), `GET /projects/:id/usage`, `GET/PUT /models` |
| 11 | **First run and onboarding** | Start or install the daemon, confirm the model endpoint is reachable, create the first project | Health plus a `system.notice` on the first model call; see §6 item 5 |

**Agent statuses to design for:** `idle`, `queued` (waiting for a concurrency slot or to resume after a restart), `running`, `waiting` (on a reply, an approval, its threads or the user), `done`, `failed`, `cancelled`. A `waiting` status carries the reason in `agent.status_changed.reason`.

**Message kinds** in `message.agent` (thread ↔ Desk) are `note`, `revision`, `update`, `question`, `blocker`, `completed`, `failed`, `cancelled`, `approval` and `stalled`. They are the natural source for an activity feed.

## 5. How a client talks to deskd

1. **Discover.** Read `<dataDir>/daemon.json` to get `{ port, token }`. The data dir is `$DESK_DATA_DIR` or `~/Library/Application Support/Desk`, and the file has mode 0600.
   - The token changes on every daemon start. On a 401, re-read the file.
   - If the file is missing, the daemon is not running.
2. **Check.** Call `GET /v1/health` (no auth) and compare `protocol_version` (currently 1).
3. **Load snapshots.** `GET /v1/projects/:id` returns the project, Desk, sources, plan, threads, pending approvals and `last_seq`.
4. **Subscribe.** Open `ws://127.0.0.1:<port>/v1/stream?token=…` and send `{ subscribe: { project_id, after_seq: last_seq } }`.
   - The server replays missed events without gaps, sends `ready`, then sends live events.
   - On a reconnect, resubscribe from the last `event.id` you applied.
   - Subscribe with `project_id: "*"` for the home screen and attention badges. Global skill changes carry `project_id: "_global"` and only reach `*`.
5. **Apply events to local state.** Events are append-only with increasing ids, so the UI state is a fold over the events.
   - `assistant.delta` is ephemeral: render it as streaming text for its `run_id`, then replace it with the `assistant.message` of the same run.
   - Tool activity arrives as a `tool.call` → `tool.result` pair, joined by `tool_call_id`.
6. **Write through the REST endpoints.** Writes return quickly; for example, a message returns 202 and the effects arrive on the stream. Do not update the UI optimistically for agent state; wait for the events.

**Security rules for the app:**
- Never display, log or persist the bearer token beyond memory.
- There is no API that exposes the model API key, and none should be added.
- Treat every agent-produced text (messages, tool output, library files, skill files) as untrusted content. In particular, render Markdown safely: no raw HTML and no auto-loading remote images.

## 6. Backend gaps the app will hit (Proposition — backend work)

These do not exist yet. Each is small and fits the existing patterns (see `CLAUDE.md`). Raise them with the user or implement them in the backend before depending on them.

| # | Gap | Why the UI needs it | Suggested shape |
|---|---|---|---|
| 1 | **No CORS or origin handling** | A web-based renderer (Electron `file://`/`app://`, Tauri) calling `127.0.0.1` directly is blocked by CORS | Allow a configured app origin, or route all calls through the app's main or native process (no backend change) |
| 2 | **No thread diff or workspace browsing** | Reviewing code threads (diff against base) and seeing files a thread produced that are not in the library | `GET /threads/:id/diff` (wraps the existing `review_diff` logic); `GET /threads/:id/files[/<path>]`, confined to the workspace |
| 3 | **No bundled daemon build** | A shipped app cannot depend on the repo, `tsx` and `pnpm` | An esbuild bundle of `apps/daemon` plus the `better-sqlite3` prebuild, and a launchd plist pointing at it |
| 4 | **No server-side "attention" or unread state** | Badges and "needs you" lists across projects, consistent between windows or clients | A derived `GET /v1/attention`: pending approvals, open questions, `report.needs_you`, failed threads. Read markers can stay client-side at first |
| 5 | **Model endpoint is not configurable through the API** | Onboarding when `~/.config/cliproxyapi.env` is missing. The key is a secret, so this is deliberately file- or env-only | A write-only `PUT /v1/config/model-endpoint` that stores the key in the macOS Keychain, or keep this as a documented manual step |
| 6 | **No notification when the app is closed** | Approvals can wait for hours unnoticed | A menu-bar helper that subscribes to `*`; or the daemon posting macOS notifications itself |
| 7 | **Chat history has no cross-project search** | "Where did we decide X?" | Memory search covers durable decisions. A general `GET /v1/search` over events is optional later |

## 7. UX propositions (Proposition)

These are ideas from building the backend and watching it run. They are yours to accept or reject.

- **Desk is a person, and the conversation is the home of a project.** Keep chat as the primary pane, with threads, plan and attention as companions rather than competing tabs.
  - Reports (`headline`, `progress`, `results`, `needs_you`) deserve a distinct rich card, not a plain chat bubble.
- **Make parallelism visible.** A live roster of thread "cards" makes the product legible at a glance. Each card shows title, status, the current tool activity in one line, elapsed time, and a skills badge.
- **Give attention one home.** Approvals, questions, stalled or failed threads and `needs_you` items all share one queue, with macOS notifications and a menu-bar count.
  - An approval must show exactly what will run (`tool` plus arguments), who asked, and why (the policy reason), with approve and deny one keystroke away.
- **Transcripts in two depths.** By default, show a readable narrative: messages plus collapsed "used 4 tools" groups. On demand, show every tool call and result.
  - `context.compacted` is a natural "earlier conversation summarised" divider.
- **Put skills forward.**
  - Show which skills a thread started with.
  - Offer "turn this into a skill" on a finished thread or conversation; it sends Desk a message asking it to capture the procedure.
  - Show when Desk created or refined a skill (`skill.saved` has a `change_note`), and make history and restore easy.
  - Global vs project scope should read clearly: a project skill shadows a global skill of the same name.
- **Steering is a first-class action.** A thread's chat box sends `POST /threads/:id/messages`, and the agent picks the message up at its next step. Label it as steering, not a new task.
- **Resilience states should be calm.**
  - `proxy_down` means paused, not failed.
  - `queued` after a restart means it will resume.
  - `agent.model_switched` means it continued on the fallback model.
  - None of these should look like errors.
- **Code work.** Show each thread's branch (`git_branch`) and Desk's reported merge order. Desk never merges, so the UI should not imply that it will.

## 8. Things to avoid

- Reading or writing `desk.db`, workspaces, skill folders or the library directly. Always use the API, which keeps events, history and policy consistent.
- Designing around code or an IDE as the default mental model; code is one kind of work.
- Showing secrets: the token, or anything resembling the proxy key.
- Blocking the UI on agent completion. Every agent action is asynchronous, and the event stream is the source of truth.

## 9. References

- `docs/api.md`: the full endpoint and stream reference.
- `packages/protocol/src/events.ts`: event payloads (normative). `api.ts`: request bodies. `settings.ts`: settings and default policy. `domain.ts`: enums.
- `apps/cli/src/client.ts`: a minimal typed client (REST plus WebSocket) to copy from.
- `apps/cli/src/format.ts`: how each event type is rendered in the terminal.
- `docs/superpowers/specs/2026-09-23-desk-daemon-design.md`: the full design. §8 covers Desk behaviour, including §8.4 on skills, and §14 lists deviations.
- `README.md`: concepts, safety model, data layout and resilience.
