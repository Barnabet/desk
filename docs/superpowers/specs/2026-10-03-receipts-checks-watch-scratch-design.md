# Receipts, check jobs, watching a thread, shared scratch · Design

**Status:** Louis asked for all four on 2026-10-03 ("Let's do all those"). The defaults below are the roadmap's recommendations; each one is marked **Default** so it can be changed later without redesign.

**Roadmap items:** C1, C2, D2, D3 in `docs/superpowers/plans/2026-10-02-harness-improvements-roadmap.md`.

**Why:** Desk can now record a submission and have it reviewed (Plan 23). But "the tests pass" is still only something an agent says. Desk also pays for every check with a thread or with its own context, misses messages between threads unless it polls, and threads redo each other's intermediate files. These four features make the platform record what ran, let Desk check work cheaply, wake Desk on the traffic it cares about, and let threads read what Desk prepared.

## 1. Command receipts (C1)

Every shell command an agent runs leaves a receipt, recorded by deskd rather than written by the agent.

- **Which commands:** `bash`, `bash_readonly`, `skill_run`, a `bash_background` job when it ends, and each step of a check job (§2).
- **What a receipt holds:**
  - `command` (and `tool`), plus the agent and the tool call or check.
  - `cwd`, and `head`: the git commit of the working folder when the command started, or null outside git.
  - `dirty`: whether that folder had uncommitted changes to tracked files when the command started (null outside git).
  - `exit_code`, and `outcome`: `exit`, `timeout`, `aborted` or `killed`.
  - `duration_ms`, `output_bytes`, and `output_sha256` of the combined output (before any truncation the model sees).
  - `started_at` and `finished_at`.
- **How it is stored:** a `receipt.recorded` event, projected into a `receipts` table. The output itself stays where it already is (the tool result, or the check's log); the receipt only fingerprints it.
- **Desk's tool:** `list_receipts({ thread_id?, commit?, command?, failed_only?, limit? })` answers "what ran on commit X and how did it end" in a few lines, newest first, without reading a transcript. `commit` matches a prefix of `head`.
- **Reviews:** a reviewer's view of the builder's report, Desk's `read_thread` summary and the apps' Review tab list the commands the platform saw run on each submission's commit with a clean worktree (`head` = commit, `dirty` = false). The builder's claimed evidence then sits next to evidence the platform recorded.
- **Apps and CLI:** the Review tab shows them under each submission ("Commands run on this commit"), and `desk review <thread>` prints them.

## 2. Check jobs (C2)

A model-less background job for Desk: run commands, see whether they passed, wake Desk with a few lines.

- **Tool:** `run_check({ title, steps, where?, expect?, timeout_s? })`.
  - `steps`: shell commands run in order with zsh; the check stops at the first non-zero exit.
  - `where`:
    - `scratch` (the default): an empty folder.
    - `{ thread_id, mode: "snapshot" }`: a clean, detached git worktree at the thread's latest submitted commit (else its branch HEAD), made for the check. It is writable, and the thread's own workspace is never touched.
    - `{ thread_id, mode: "workspace" }`: the thread's workspace itself, read-only.
    - `{ source_id }`: a project source, read-only.
  - `expect`: files the steps must produce, relative to the working folder, or `$DESK_CHECK_OUT/<name>` for the output folder. Each must be a regular file (not a link) inside its folder, non-empty, and modified after the check started.
  - `timeout_s`: the whole check, 1800 s by default and at most 3600 s.
- **Where it runs:** under `<data>/projects/<project>/checks/<id>/` (Desk reads its project's `checks/`), which holds `work/` (the scratch folder or snapshot), `out/` (always writable, given to the steps as `$DESK_CHECK_OUT`) and `logs/` (one log per step, which steps cannot write). It is sandboxed like `bash`: writable roots are `work/` (unless read-only), `out/` and temp; the environment is `scrubbedEnv`. The policy gate is `bash`'s, applied to each step (the strictest decision wins), so a rule that asks before a command asks before a check that runs it; without the sandbox every check asks. Step logs go to files, never to events.
- **Results:** `check.started` and `check.finished` events, projected into a `checks` table. `check.finished` holds `passed` / `failed` / `timed_out` / `cancelled` / `interrupted`, the failing step and a reason (`exit 2`, `missing out/report.html`). Each step leaves a receipt. When it ends, Desk gets a `check` message, a lifecycle wake counted like a thread's completion. The message has the status, each step's exit and duration, and the last 20 lines of the failing step's log.
- **Desk's other tools:**
  - `check_log({ check_id, step?, lines? })` reads more of a log.
  - `cancel_check({ check_id })`.
  - `list_checks` is folded into `list_receipts`' output with a `checks` section.
- **Limits:** at most 3 running checks per project; a fourth is refused with a pointer to the running ones. **Default:** Desk only; threads keep `bash_background` for their own long commands.
- **Crash and shutdown:** a check still running at shutdown or after a crash is recorded `interrupted` and never re-run, like an interrupted tool call. Archiving the project cancels its checks. `work/` (a scratch folder or a snapshot worktree) is removed when the check ends; `out/` and `logs/` are kept with the project's other data.
- **Apps:** a check's start and end are notices in Desk's conversation ("Check passed: Run the test suite · 3 steps · 42 s"), labelled **Check**. The CLI's `desk chat` shows them like any other notice.

## 3. Watching a thread (D2)

- **Tool:** `watch_thread({ thread_id, match? })` for Desk. It wakes Desk once, the next time that thread sends a message to another thread (`match`: only a message whose text contains it, case-insensitive). Messages a thread sends to Desk already wake Desk, so a watch ignores them.
- **Lifetime:** one shot. A watch ends when it fires, when Desk calls `unwatch_thread`, or silently when the thread finishes or is archived (Desk already gets the thread's completion notice). At most 10 open watches per project.
- **Delivery:** a `reminder` message to Desk ("Watch on Eval: it sent a note to Frontend: …"), a lifecycle wake within the existing wake budget. Clients do not show reminders.
- **State:** `watch.set` and `watch.ended` (`fired`, `cancelled`, `thread_finished`) events, projected into a `watches` table. `list_threads` marks watched threads.

## 4. Read-only shared scratch (D3)

- **What:** threads can read Desk's workspace with their file tools. **Default:** Desk's only, not sibling threads'. Shell commands could already read it, because the sandbox confines writes and secrets, not reads. The file tools' `readRoots` now include it for threads.
- **Prompts:**
  - Desk's prompt says threads can read its workspace and gives the convention: put files for threads in `shared/` and give the path in the brief.
  - A thread's prompt names the path as read-only.
- **Security pass:** Desk's workspace is an ordinary workspace inside the data dir. Nothing in it is a secret by the existing invariants: no token, database, credentials or desk web file lives there, and `SandboxGuard` still refuses them by identity. Writes stay confined to each agent's own roots, so a thread cannot change what Desk shares. Step agents do not get it.

## 5. Out of scope

- Plan gates (E1) and stale results (E2). They build on receipts and checks.
- Checks for threads, and reading sibling threads' workspaces.
- Showing receipts in the transcript. The transcript already shows each command and its output.
