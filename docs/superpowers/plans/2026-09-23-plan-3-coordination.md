# Desk Plan 3 — Coordination Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. TDD per task (tests listed below first, red, implement, green, `pnpm typecheck`, commit). Code lives in the repo.

**Goal:** Every project gets a Desk agent that scopes requests, spawns and steers threads in isolated workspaces (scratch dirs or git worktrees), receives their lifecycle events, reviews and sends work back, keeps a plan, curates shared memory, publishes to a library, and reports to the user.

**Architecture:** `Runtime` gains a `dataDir` and implements a `RuntimeServices` interface exposed to tools through `ToolContext.services`. Agent↔agent communication is a new inbox event, `message.agent` (recipient = `agent_id`), rendered in transcripts with an origin tag. Thread lifecycle (completed/failed/cancelled/approval/stalled) becomes `message.agent` to the parent Desk, which wakes it; bursts coalesce naturally at step boundaries. Memory (with FTS5), library, plan, sources and reports are event-sourced projections. System prompts are built per role from live project state.

**Spec:** §4 (Source, Agent, Plan, Memory, Artifact), §6.1 (thread coordination, git, memory, library tools), §6.2 (Desk tools), §8 (Desk behaviour), §10 (worktree errors).

## Global Constraints

All Plan 1–2 constraints, plus:
- Layout under `dataDir`: `projects/<project_id>/library/`, `projects/<project_id>/desk/` (Desk scratch), `workspaces/<thread_id>/`.
- Worktree branches: `desk/<slug>-<last 6 of thread id, lowercase>` off the source repo's current `HEAD`; branches are kept when a thread is archived.
- Read roots: own workspace + all project sources + project library. Library is written only via `library_publish` / runtime API.
- Memory digest in every system prompt: all active `preference` + `decision` entries, then newest other entries, capped at 16,000 characters.
- Review send-backs are capped by `settings.review_rounds`.
- Desk never merges; it reports merge order.
- Default policy gains `bash_readonly` risky-command → `ask` (after the bash rules).

---

### Task 1: Data dir, sources, Desk agent, agent messaging
**Files:** protocol events (`source.added`, `source.removed`, `message.agent`); schema (`sources`, agents + `review_round`, `result_summary`, `result_artifacts`, `git_*`); projections; queries (`getDeskAgent`, `listSources`, `listThreads`); `agent/transcript.ts` (origin tags); `agent/inbox.ts` (`message.agent` is an inbox type); runtime (`dataDir`, `createProject` creates Desk + dirs, `sendToDesk`, `addSource`, `removeSource`, `sendAgentMessage`).
**Tests:** createProject creates exactly one Desk agent with `desk_model` and a Desk dir; addSource detects `git` vs `folder`, rejects missing paths; `message.agent` wakes the recipient and renders as `[from Desk — revision] …` / `[from thread "T" (id) — completed] …`; user messages stay untagged.

### Task 2: Memory
**Files:** protocol (`MemoryKind`, `memory.written`, `memory.deleted`); schema `memory`; FTS5 table created in `openDb`; projections; `memory/memory.ts` (`searchMemory`, `activeMemory`, `memoryDigest`); `tools/memory.ts` (`memory_search`, `memory_write`); runtime (`writeMemory`, `deleteMemory`).
**Tests:** write/supersede keeps one active entry and links both ways; delete removes from search; FTS search ranks matches, tolerates punctuation (`"release: friday?"`); digest puts preferences/decisions first and caps size; tool writes carry `source: agent:<id>`.

### Task 3: Library
**Files:** protocol (`artifact.published`); schema `artifacts`; projections; `library/library.ts` (`libraryDir`, `publishFile`, `listArtifacts`); `tools/library.ts` (`library_list`, `library_read`, `library_publish`); runtime (`addLibraryFile`).
**Tests:** publish copies a workspace file, records origin, de-duplicates names (`report.md` → `report-2.md`); publishing a path outside the workspace is denied; `library_read` cannot escape the library; user upload recorded with `origin: user`.

### Task 4: Workspaces, git worktrees, git tools, bash_readonly
**Files:** `workspaces/workspaces.ts` (`createWorkspace`, `removeWorkspace`, `threadBranchName`); `tools/git.ts` (`git_status`, `git_diff`, `git_commit`, `git_push`, `open_pr`); `tools/bash.ts` (+`bash_readonly`); policy `SHELL_TOOLS` + default rule; `agent/context.ts` (git common dir writable for worktree threads).
**Tests (temp git repos):** worktree created on `desk/<slug>-<id>` at source HEAD; dirty source still works (worktree from HEAD); non-git source → scratch dir; `git_commit` commits in the worktree only; `git_diff` shows changes vs base; `git_push` to a local bare remote succeeds on `desk/*` (policy allow) and is denied for other branches; `open_pr` is gated `ask`; `bash_readonly` cannot write the workspace; removeWorkspace deletes the worktree but keeps the branch.

### Task 5: Thread coordination and lifecycle notifications
**Files:** protocol (`agent.result`); `tools/thread.ts` (`message_desk`, `wait_for_reply`, `complete {summary, artifacts}`); `RuntimeServices`; runtime `afterRun` notifications + approval notifications + `checkStalls(now)`.
**Tests:** `complete` stores result and notifies Desk (`completed` with summary + artifacts); failure notifies `failed`; stop notifies `cancelled`; `message_desk(question)` + `wait_for_reply` → thread `waiting`, Desk woken; Desk reply wakes the thread; approval raised by a thread notifies Desk with approval id; `checkStalls` notifies once per stall.

### Task 6: Desk tools
**Files:** protocol (`plan.updated`, `report`, `question.asked`, `agent.revision`, `PlanItem`); schema `plans`; `tools/desk.ts` (`spawn_thread`, `message_thread`, `stop_thread`, `list_threads`, `read_thread`, `review_diff`, `wait_for_threads`, `update_plan`, `ask_user`, `resolve_approval`, `report`, `update_settings`); runtime (`spawnThread`, `archiveThread`).
**Tests:** spawn creates workspace + thread with brief and wakes it; spawn with git source → worktree; `message_thread(kind: revision)` reopens a done thread and increments `review_round`, refusing past `review_rounds`; `resolve_approval` only for `delegate_to_desk` approvals; `update_plan`/`report`/`ask_user` emit events (ask_user yields `waiting`); `update_settings` validates; `read_thread` summary includes status, brief and result.

### Task 7: Role prompts and tool sets
**Files:** `agent/prompts.ts` (`deskSystemPrompt`, `threadSystemPrompt` with state), `runtime/toolsets.ts` (`deskTools`, `threadToolsFor(agent)`), runtime wiring.
**Tests:** Desk prompt contains workflow rules, plan, roster, pending approvals, settings, memory digest, sources; thread prompt contains brief, workspace, git branch, review feedback round, memory digest; thread toolset includes git tools only for worktree threads; Desk toolset has no write_file/bash.

### Task 8: End-to-end coordination scenario + live Desk smoke
**Tests:** fake-model scripted scenario through `Runtime`: user → Desk spawns two threads → thread A asks Desk a question, Desk answers, A completes → B completes → Desk sends B back once → B completes again → Desk updates plan and reports; assert events, statuses, memory write, library artifacts. Live (`DESK_LIVE=1`, Opus): project "write two short files", Desk dispatches ≥1 thread, threads complete, Desk reports.

## Done Criteria
`pnpm test`, `pnpm typecheck` green; live Desk smoke passes on `claude-opus-5-5`.
