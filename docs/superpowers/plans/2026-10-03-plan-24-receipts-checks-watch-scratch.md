# Plan 24 · Receipts, check jobs, watching a thread, shared scratch

**Spec:** `docs/superpowers/specs/2026-10-03-receipts-checks-watch-scratch-design.md` (requested by Louis on 2026-10-03, built with the defaults it marks).

## Global constraints

- `pnpm typecheck` and `pnpm test` must pass before every commit.
- Migrations are additive (`0009_receipts_checks_watches`).
- State changes are events, and projections apply them in the same transaction.
- Tools reach the runtime only through `ctx.services`.
- Both UIs ship together.
- Check steps run sandboxed, behind `bash`'s policy gate. Their logs go to files; deskd writes them, and no step can write them.

## Tasks

### S1 · Receipts
1. Protocol: `receipt.recorded` (`receipt_id`, `tool`, `tool_call_id?`, `check_id?`, `step?`, `command`, `cwd`, `head`, `dirty`, `exit_code`, `outcome`, `duration_ms`, `output_bytes`, `output_sha256`, `started_at`). Schema: `receipts` table and projection.
2. `tools/receipts.ts`: `gitState(cwd)` (HEAD and dirty, through `safeGitArgs`, tolerant of non-git folders), and `receiptOf(result, …)`. `RuntimeServices.recordReceipt`.
3. `bash`, `bash_readonly` and `skill_run` record a receipt per call. `JobManager` reports an ending job, and `bash_background` records its receipt then.
4. `reviews/`: receipts on a submission's commit (clean). Add them to `builderReport`, `reviewSummary` and `threadReview` (`receipts`).
5. Desk tool `list_receipts`.
6. Tests: a receipt per command with head and dirty, background job receipt, list_receipts filters, receipts in the builder report.

### S2 · Check jobs
1. Protocol: `check.started`, `check.finished`, and message kind `check` (a lifecycle kind). Schema: `checks` table and projection.
2. `checks/runner.ts`:
   - Prepare the folder: scratch, a snapshot worktree, workspace or source.
   - Run the steps sequentially, sandboxed, each to its log.
   - Enforce the whole-check timeout.
   - Check the expected files.
   - Record a receipt per step.
   - Finish: remove the snapshot, append the event, send the notice to Desk.
3. Runtime: `startCheck`, `cancelCheck`, `checkLog`. At most 3 running per project. Cancel on archive and shutdown. Recovery marks a check still `running` as `interrupted`.
4. Desk tools `run_check` (with `bash`'s gate), `check_log` and `cancel_check`. `list_receipts` lists recent checks.
5. Labels: FEED `check` in both UIs.
6. Tests: passed, failing step, missing expected file, timeout, snapshot at the submitted commit with the thread workspace untouched, read-only workspace, the limit, interrupted on recovery, the notice wakes Desk.

### S3 · Watching a thread
1. Protocol: `watch.set` and `watch.ended`. Schema: `watches` table and projection.
2. Runtime: `watchThread` and `unwatchThread` (at most 10 open). A store listener fires a watch on a matching thread-to-thread message, and ends it when the thread finishes or is archived.
3. Desk tools `watch_thread` and `unwatch_thread`. `list_threads` marks watched threads.
4. Tests: fires once with a match, ignores messages to Desk, ends on finish, the cap, unwatch.

### S4 · Shared scratch
1. A thread's `readRoots` include its Desk's workspace.
2. Prompts: Desk's (threads can read your workspace; put files for them in `shared/`), and a thread's (Desk's workspace path, read-only).
3. Tests: a thread's `read_file` reads Desk's file; a thread's `write_file` there is refused.

### S5 · Clients and docs
1. The Review tab and `desk review` show receipts per submission (`ui-core` view plus both UIs).
2. `docs/api.md` (events, kinds, tools), `docs/desktop.md`, and the roadmap status.
