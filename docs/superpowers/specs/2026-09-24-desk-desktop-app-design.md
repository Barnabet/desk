# Desk — Desktop App Design (v1.1)

- **Date:** 2026-09-24
- **Status:** Approved design, not yet implemented
- **Scope:** the Desk desktop app (`apps/desktop`), the shared client library (`packages/client`), and the backend additions the app needs (Plan 7).
- **Inputs:** `docs/ui-handoff.md` (what the UI must cover), `docs/api.md` (the contract), and the design canvas "Desk — Mac app design" (https://claude.ai/artifact/8vzwDC11ows5KJVq8tmV3Y), row **C · Orbit map, refined**. That row is the visual source of truth.

---

## 1. Decisions

| Decision | Choice | Why |
|---|---|---|
| Stack | **Electron + React + Vite** (electron-vite; electron-builder) | Runs on macOS and Windows from one codebase and imports `@desk/protocol` directly. Needs no native compile, which matters because the Xcode licence is not accepted on the build Mac. |
| Platforms | App code is **portable**; we **ship and test macOS** | deskd depends on `sandbox-exec` and launchd, so porting the daemon to Windows is a separate project. The app itself avoids macOS-only APIs unless it has a fallback. |
| Client architecture | **The main process is the only deskd client** (approach A) | The bearer token never reaches the renderer, which renders untrusted agent content. The event-to-state logic is plain TypeScript and easy to test. |
| Visual direction | **C · Orbit map, refined** | Maps are used only where they help: the projects map, a thread's route and the skills map. Other surfaces use related metaphors: a transit line diagram for the conversation and flight strips for attention. |
| Backend gaps | **All implemented** (Plan 7) | Attention, read endpoints, model-endpoint setup, notifications from the daemon, and a bundled daemon. |
| Screens without mockups | Library, Memory, Settings, System, Onboarding | Built from C's components and reviewed in the running app. |

Unchanged from the daemon design: local-first, one user, the app talks to deskd only through its API, no optimistic updates to agent state, and agent text is treated as untrusted.

---

## 2. Repository layout

```
packages/client/            @desk/client — plain TS, runs in Node and the browser, no Electron
  src/client.ts             DeskClient: typed REST for every endpoint, ApiError, re-reads the token on 401
  src/discover.ts           daemon.json locator (per-platform data dir), health/protocol check
  src/stream.ts             Stream: WS subscribe, replay via after_seq, backoff reconnect, resume, ephemeral deltas
  src/state/*.ts            pure reducers (event → state), one per concern (§6)
apps/desktop/               @desk/desktop — Electron
  src/main/                 daemon.ts, broker.ts, ipc.ts, tray.ts, notify.ts, windows.ts, menu.ts, updates of the LaunchAgent
  src/preload/index.ts      contextBridge → window.desk (typed calls + on(event)); nothing else is exposed
  src/shared/ipc.ts         channel map + zod schemas shared by main and preload
  src/renderer/             React app: app shell, routes, stores, components, screens, tokens, bundled fonts
apps/daemon/                + the new routes (§4), an esbuild bundle target, the notifier
packages/core/              + attention derivation, overview, thread diff and files, skill versions, usage, endpoint config
apps/cli/                   switches to @desk/client (drops its own client.ts)
```

The renderer runs with `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false` and a strict CSP (`default-src 'self'`; no remote scripts, fonts or images). Fonts are bundled: Newsreader, Geist and Geist Mono (all under the OFL).

---

## 3. Data flow

1. **Discovery (main).** Read `<dataDir>/daemon.json`, where dataDir is `$DESK_DATA_DIR`, `~/Library/Application Support/Desk` on macOS, or `%APPDATA%\Desk` on Windows. Then call `GET /v1/health` and compare `protocol_version`. On a 401, re-read the file. If the file is missing, the daemon is not running (§8).
2. **Broker (main).** One `DeskClient` and one `Stream` subscribed to `*`. The broker:
   - keeps a global store (the overview, attention, system state) for the map, the tray and notifications;
   - records which projects each window has open and forwards only those projects' events (plus global ones) with `webContents.send`;
   - refetches `/v1/attention` and `/v1/overview`, debounced at 250 ms, after any event that affects them.
3. **IPC.** Each renderer call is `invoke(channel, payload)`. The payload is validated with zod in main, and only then does the broker run the matching `DeskClient` method. The token, the data dir and raw daemon errors never cross the bridge. Errors come back as `{ code, message }` using the daemon's error codes.
4. **Renderer stores.** Opening a project calls `projects.get(id)`, seeds the store from that overview, and tells the broker to subscribe from `last_seq`. From then on the store is a fold of the `@desk/client` reducers. `assistant.delta` is buffered per `run_id` and replaced by the `assistant.message` of the same run.
5. **Writes** return 202 or 201 quickly. The UI shows a pending state on the control that triggered the write until the confirming event arrives. Agent state is never changed optimistically.
6. **Local-only state:** drafts, selection, which map node is highlighted, window layout, and the last event seen per project (for unread markers). It lives in renderer `localStorage`, wrapped in try/catch.

---

## 4. Backend additions (Plan 7)

These follow `CLAUDE.md`: zod bodies in `@desk/protocol/api.ts`, runtime methods that throw `NotFoundError`, `ConflictError` or `ValidationError`, new state recorded as events and projected into tables, and `docs/api.md` updated.

### 4.1 Attention
- `GET /v1/attention?project_id=` → `{ items: AttentionItem[], seq }`. Each item is `{ id, kind, project_id, project_name, agent_id|null, title, detail, created_at, ref }`, where `ref` holds whatever is needed to act on it (`approval_id`, `event_id`, `thread_id`). The kinds, derived on the server:
  - `approval`: pending approvals with `delegate_to_desk` false (the ones Desk decides never reach the user).
  - `question`: the latest `question.asked` in each project that no `message.user` has followed. Its `detail` includes `options`.
  - `needs_you`: each string in the latest `report.needs_you` of each project. The id is `report:<event_id>:<index>`.
  - `stalled`: a thread whose latest `message.agent` of kind `stalled` has had no activity from that thread since.
  - `failed`: threads with status `failed` that are not archived.
- `POST /v1/attention/:id/dismiss` appends `attention.dismissed { item_id }`, which is projected into `attention_dismissals`. Only `needs_you`, `stalled` and `failed` items can be dismissed; the others return 409, because they leave the list by being answered.

### 4.2 Read endpoints
- `GET /v1/overview` → `ProjectOverviewSummary[]`, one per non-archived project: `{ project, desk_status, threads: [{ id, title, status, reason?, activity?, model, git_branch?, skills }], latest_report?: { headline, ts }, plan_progress: { done, total }, attention_count }`. `activity` is the name and short arguments of the thread's latest `tool.call`, when that call has no `tool.result` yet.
- `GET /v1/threads/:id/diff` → `{ base, branch, files: [{ path, status, additions, deletions }], patch }`. It reuses the `review_diff` code path and returns 409 when the thread has no git worktree.
- `GET /v1/threads/:id/files?path=` → `[{ name, path, type, size }]`, and `GET /v1/threads/:id/files/raw/<path>` returns the raw file. Both are confined to the workspace: a path that escapes it gets 403, and an archived thread (workspace removed) gets 409.
- `GET /v1/skills/:name/versions/:v` (and the same under the project route) → the skill detail as it was at version v. `GET …/versions/:v/files/<path>` returns that version's raw file. Both read `.history/<name>/<v>/`, or the live directory for the current version.
- `GET /v1/usage?since=<iso>` → `{ rows: [{ project_id, model, prompt_tokens, completion_tokens }], totals }` across all projects (the usage table does not track cached tokens).
- `GET /v1/health` adds `proxy: 'up'|'down'|'unknown'` (taken from the last `proxy_*` notice) and `uptime_s`. It stays unauthenticated and includes nothing sensitive.

### 4.3 Model endpoint setup (secret-safe)
- `GET /v1/config/model-endpoint` → `{ configured, source: 'env'|'file'|'keychain'|null, base_url|null }`. It **never** includes the key.
- `PUT /v1/config/model-endpoint { base_url, api_key }` is write-only.
  - On macOS it stores the key in the Keychain (service `Desk model endpoint`, account `default`). It does this with `security add-generic-password -U`, passing the key on stdin, never in argv.
  - On other platforms it returns 501.
  - `base_url` goes into `<dataDir>/config.json`.
- `POST /v1/config/model-endpoint/test` → `{ ok, models?: string[], error? }`. It calls `GET {base_url}/models` with the resolved key.
- The key is resolved in this order: `DESK_OPENAI_*` environment variables, then `~/.config/cliproxyapi.env`, then the Keychain. A new key takes effect on the next model call without a restart.
- These routes are excluded from request-body logging. Tests assert that the key never appears in logs, events, `daemon.json`, any response, or a tool environment.

### 4.4 Notifications from the daemon
- A notifier subscribes to the event store and posts macOS notifications through `osascript -e 'display notification …'`. The text is escaped and passed as argv, never through a shell. It fires for `approval.requested` (unless delegated to Desk), `question.asked`, a report with `needs_you`, a `stalled` message, and a thread moving to `failed`.
- It stays quiet while at least one WebSocket client has sent `{ hello: { client: 'desktop', notifications: true } }`.
- The daemon setting `notifications: 'auto'|'off'` (in `config.json`, editable through `PATCH /v1/config { notifications }`) turns it off. On non-macOS platforms the notifier is a no-op.

### 4.5 Bundled daemon
- `pnpm --filter @desk/daemon bundle` runs esbuild and produces `apps/daemon/dist/deskd.mjs`, with `better-sqlite3` marked external. Its N-API prebuild runs under both Node and Electron.
- The app ships `resources/deskd/{deskd.mjs, node_modules/better-sqlite3/…}` and the drizzle migrations.
- The app runs deskd with its own binary: `ELECTRON_RUN_AS_NODE=1 <App>/Contents/MacOS/Desk <resources>/deskd/deskd.mjs`. On macOS the LaunchAgent `dev.desk.deskd` (the label the CLI already uses) points there.
- On launch the app compares the bundle version with the version the running daemon reports (from `daemon.json`). If they differ it rewrites the plist and runs `launchctl kickstart -k`.
- Development mode keeps using the repo daemon (`desk up`).
- On Windows the locator and starter are stubbed: a scheduled task at logon, not implemented.

---

## 5. Visual system (from canvas row C)

- **Tokens:**
  - ground `#EFEAE0`, title bar `#E6E0D4`, rules `#D6CFC1` and `#E3DFD6`
  - ink `#1C1B18`; text `#3D3A34`, with `#4A4740` as the minimum for small text
  - running: blue `#2F5BD3`, text `#1F45A8`, pastel `#E3E8F5`
  - waiting: amber `#A15C00`, text `#7A4500`, pastel `#F3E6CF`
  - done or muted: `#8A857B` and `#B7C4E6`
  - needs you: accent `#C4441C`, tint = accent at 8% alpha
  - cards: white, 1px `#D6CFC1`, radius 16, shadow `0 16px 36px rgba(28,27,24,.14)`
- **Type:** Newsreader for titles and Desk's voice; Geist for UI; Geist Mono for tools, files, commands and times.
- **Components:** TitleBar with the Places nav, attention pill and daemon status dot · MapCanvas (an SVG layer plus HTML nodes positioned absolutely, with pan and zoom) · Territory · Node (ink / outline / dashed-shadowed, with the selected double ring) · Callout · Legend · InspectorCard · LineDiagram (lanes, forks and rejoins, stations, trains, signals, detours, a now line) · Route (numbered stops, detour, U-turn, next stops) · FlightStrip (end-cap code APR/ASK/DOC/HLD/FLD, fields, wait gauge) and StripRack · Waypoints (horizontal and vertical) · ReportCard · QuestionCard · Composer · TranscriptEntry (numbered, highlightable) · ToolGroup · StatusChip · SkillBadge · CodeBlock · SafeMarkdown · Sheet · CommandPalette · Toast · EmptyState.
- Every map has a **List** alternative. All controls are real buttons, links or inputs, keyboard-reachable, with labels on icon-only buttons and text contrast of at least 4.5:1. Colours that must be told apart also differ in lightness.
- The mockups are fixed 1440×900. The app is responsive from 1100×700 upward, so maps scale and inspectors turn into drawers at narrow widths.

---

## 6. State reducers (`@desk/client/state`)

Each reducer is `(state, event) → state`: pure, tested with recorded event sequences, and shared by the broker and the renderer.

| Reducer | Consumes | Produces |
|---|---|---|
| `projectOverview` | `project.*`, `source.*`, `agent.created/status_changed/result/revision/model_switched/skills_changed/archived`, `plan.updated`, `tool.call/result`, `approval.*` | project, desk, sources, the thread roster (status + reason, current activity, elapsed, model, branch, skills, revision round), plan, pending approvals |
| `chat` | `message.user`, `message.agent`, `assistant.message`, `assistant.delta` (ephemeral), `report`, `question.asked`, `system.notice`, `context.compacted` (Desk) | ordered chat items with streaming text merged in; each question marked answered or not |
| `timeline` | `agent.created`, `agent.status_changed`, `agent.result`, `agent.revision`, `agent.model_switched`, `approval.*`, `report`, `question.asked`, `message.user` | lanes for the line diagram: Desk trunk stations; per-thread segments with fork, rejoin, revision loop, detour, signal and current train; a now marker |
| `transcript` | every event of one thread | numbered narrative entries (brief, text, tool groups joined by `tool_call_id`, revision, steering, compaction divider, detour, result) plus the full every-step list |
| `attention` | `/v1/attention` snapshots + `approval.resolved` / `message.user` / `attention.dismissed` | strips grouped by kind; the selected item |
| `skills` | skill list/detail snapshots + `skill.saved/deleted`, `agent.skills_changed` | the skill graph (scope, shadowing, versions, in-use-by) |
| `system` | `/v1/health`, `system.notice`, `usage` | daemon and proxy state, notices log, usage |

Tricky cases the tests must cover: a revision loop (a done thread sent back and running again), compaction mid-transcript, a fallback-model detour, a thread `queued` after a daemon restart, an `interrupted` tool result, replay after reconnect with no duplicate or missing events, a delta stream cut off by a run error, and an approval resolved by Desk.

---

## 7. Screens: the functionality checklist

Every item below must work against a real deskd. The checklist is the acceptance list for Plans 10 and 11.

**Shell.**
- The title bar holds the Places nav (Map · project switcher ⌘P · Skills · System), the attention pill and a daemon dot.
- Project sub-nav: Conversation · Threads · Library · Memory · Settings.
- The ⌘K palette searches projects, threads, skills and library titles, and runs memory search through `?q=`.
- Unread dots on projects.
- Toasts for write errors.

**1. Map (home).**
- Projects are territories sized by activity, with Desk nodes, orbiting threads, needs-you pins and the deskd "sun".
- Selecting a project fills the inspector: report headline, plan waypoints, threads, needs-you items, "Open conversation".
- A List view.
- New project sheet: name, goal, instructions, sources via a native folder picker, optional settings.
- Archiving a project (from Settings).

**2. Conversation.**
- A line diagram with stations that link to chat items and trains and signals that link to threads.
- The chat shows:
  - your messages and Desk's replies, including streaming text
  - the thread↔Desk feed (every `message.agent` kind, styled by kind)
  - report cards: headline, progress, results linked to the Library, needs-you items with actions, merge order
  - question cards: clicking an option sends it as a `message.user`; free text also works
  - system notices in a calm band
- Composer: send, attach (uploads to the Library, then references the file in the message), and "Turn this into a skill".
- A plan route panel.

**3. Threads.**
- A roster of cards: status and reason, current activity, elapsed time, model, branch, skills and revision round.
- A route view with numbered stops that sync with the transcript.
- Transcript in Narrative and Every-step depths, with tool arguments and results, `denied` and `interrupted` states, and a compaction divider.
- Steering, labelled as steering.
- Stop (when running, waiting or queued).
- Archive (when done, failed or cancelled), with a note that the branch is kept.
- Tabs:
  - Result: summary and artifacts
  - Diff (git threads)
  - Files (workspace browser and viewer)
  - Skill drafts
  - Usage
- "Turn into a skill" on finished threads.

**4. Attention.**
- A strip rack in bays: CLEARANCE, QUERIES, HANDOFFS, HOLDING (stalled or failed).
- Approval inspector:
  - the exact tool and its arguments (pretty and raw)
  - who asked, the project, the branch and the workdir
  - the policy reason, and the thread's last words
  - a note field, then Approve once or Deny
- Question: answer in place.
- Needs-you, stalled and failed items: open or dismiss.
- Keys: J/K, ⌘⏎, ⌘⌫, E. A 409 on resolve shows "Already decided by …".

**5. Skills.**
- A map and a list view, with global, project and shadowed skills and what's in use.
- Detail: instructions (safe Markdown), the files tree and viewer, and where the skill is used.
- History: a diff between any two versions, restore, and change notes.
- Create or edit by hand: SKILL.md and files, with add, remove and upload.
- Import a folder (default `~/.claude/skills`).
- Delete.
- Scope switcher: global or the current project.
- Ask Desk to build or refine a skill; this sends Desk a message that names the skill.

**6. Library.**
- A grid and preview. The preview handles safe Markdown, text and code, images from the daemon and a download fallback.
- Each file shows its origin (you or a thread, linked) and its kind.
- Upload by drag and drop or a picker.
- Download.

**7. Memory.**
- Entries grouped by kind, with search.
- Add; correct (supersede) with the supersession chain shown; delete.

**8. Project settings.**
- Name, goal and instructions.
- Sources: add or remove; kind is detected.
- `check_in`, `autonomy`, `review_rounds`.
- Models for Desk, threads and the fallback, chosen from the registry.
- `max_concurrent_threads`, shown as slots.
- An ordered editor for policy rules (tool, match on branch/command/domain, action, delegate to Desk), with the default policy shown and a reset button.
- Archive project.

**9. System.**
- Daemon: running or not, version, uptime, with Start, Restart and Install/Repair of the LaunchAgent.
- Proxy state.
- Model endpoint: source, base URL, change the key, test.
- An editor for the model registry (`PUT /models`).
- Usage by model and by project.
- A log of system notices.
- The notifications setting, for the app and the daemon.
- Data directory (read-only), and "Reveal logs" (opens the logs folder).

**10. Onboarding.**
- Detect the daemon, or install and start the bundled one.
- Detect the model endpoint, or enter one (stored in the Keychain), then test it.
- Create the first project.
- Land on the Map.

**11. Menu bar / tray.**
- A count badge.
- A mini line diagram of today.
- Compact strips, with approve and deny in place on approvals.
- What's running now, and Open Desk.
- Notifications: via the app while it runs (it sends the hello, so the daemon stays quiet). Clicking a notification opens the item.
- The app keeps running in the tray when its last window closes. Quit is in the tray menu and ⌘Q.

**Resilience states.**
- `proxy_down` shows "Paused, will resume", never as an error.
- `queued` after a restart shows "Will resume".
- `agent.model_switched` shows a detour.
- If the daemon is lost, a reconnecting overlay appears with Start.
- A `protocol_version` mismatch blocks the app with a clear message.

**Safety.**
- SafeMarkdown: no raw HTML, remote images replaced by links, links open in the system browser after a confirmation.
- The token never leaves main.
- Nothing resembling a key is displayed.

---

## 8. Daemon lifecycle (app side)

- **At launch:** discover the daemon.
  - If it isn't running, the app offers "Start Desk". In a packaged build this installs or refreshes the LaunchAgent and kickstarts it; in dev it runs `desk up`.
  - The app waits for `daemon.json` and a healthy `/v1/health`, with a 10-second timeout that shows an error with Retry and Reveal logs.
- **Version drift:** in a packaged build, a daemon version older than the app's bundled one triggers a plist refresh and a kickstart.
- **Closing the app:** quitting never stops the daemon; work continues. System has a "Stop daemon" action (`launchctl bootout`) behind a confirmation.

---

## 9. Error handling

- `DeskClient` turns the error body into `ApiError { status, code, message, details }`. A 401 re-reads `daemon.json` once and retries, and a network failure raises `DaemonUnavailable`.
- The broker reconnects the WebSocket with jittered backoff (0.5 s to 10 s) and resumes from the last applied id. Close code 4401 means re-read the token.
- The renderer shows a 409 as a quiet inline explanation, a 400 next to the form fields, a 404 as an empty state with a way back, and a 5xx as a toast with Reveal logs.

---

## 10. Testing

- **`@desk/client` (Vitest):**
  - reducers against recorded event sequences, covering every tricky case in §6
  - `DeskClient` and `Stream` against an in-process deskd (`createHarness`) with the fake model, including reconnect and resume
- **Backend (Vitest, existing style):** every new route and runtime method. Tests for secret safety, confinement (path traversal on files and versions) and notifier gating.
- **Desktop:**
  - an IPC contract test: each channel's zod validation and error mapping
  - component tests (Vitest + Testing Library, jsdom) for each screen's main actions
  - **Playwright for Electron** end to end, against a real deskd plus the fake model, following the §7 checklist: create a project → brief Desk → threads fork on the line diagram → approve from Attention → answer a question → steer a thread → a revision loop shows up → refine a skill and restore v1 → upload to the Library → correct a memory entry → change settings → the tray count updates
- **Visual QA:** screenshots of each screen at 1440×900 compared by eye against canvas row C, with discrepancies fixed.
- **Gates:** `pnpm test` and `pnpm typecheck` stay green at every commit. `pnpm test:live` gains a desktop smoke test.

---

## 11. Build sequence

| Plan | Contents | Done when |
|---|---|---|
| 7 · Backend for the UI | §4 in full | Tests are green and `docs/api.md` is updated |
| 8 · `@desk/client` | DeskClient, discover, Stream, all reducers; the CLI migrated | Reducer tests cover §6's tricky cases |
| 9 · Desktop shell | Electron scaffold, broker, IPC, preload, daemon lifecycle, tray, notifications, tokens and core components, SafeMarkdown, onboarding | The app launches, onboards, connects, shows the tray count |
| 10 · Core screens | Map, Conversation, Threads, Attention | §7 items 1–4 pass end to end |
| 11 · Knowledge and control | Skills, Library, Memory, Settings, System, ⌘K | §7 items 5–9 pass end to end |
| 12 · Packaging and hardening | electron-builder `.dmg`/`.zip` with the bundled deskd, visual QA, the full end-to-end suite, docs (README, `docs/desktop.md`) | The installed app works from a clean data dir |

---

## 12. Out of scope

- Porting deskd to Windows. The Windows build target is configured but untested.
- MCP connectors (v1.1), scheduled wakeups (v1.2), phone access, cost in currency.
- Code signing and notarisation, which need the user's Apple developer identity. The `.dmg` is unsigned and opens with right-click → Open.
- Multi-user, sync, cloud.
