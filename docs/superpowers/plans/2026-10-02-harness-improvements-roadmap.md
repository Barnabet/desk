# Harness improvements · Roadmap

**Status:** A1 (resume) shipped with this roadmap. Phase B (submissions, reviews, acceptance) shipped with Plan 23. Every other item gets its own spec and numbered plan before any code.

**Source:** feedback from two of Desk's own coordinators (GPT-6 Astra, then Claude Fable 5.1) after a long financial-checker project, pasted by Louis on 2026-10-02. Their shared conclusion: threads build well but certify their own work badly, and the coordinator pays for every independent check with its own context. Desk should make "I produced it", "I checked it" and "it is accepted" three separate, recorded facts.

This document checks each suggestion against the code on `master` (`002337f`), then proposes an order.

## 1. What Desk has today, per suggestion

| # | Suggestion | What Desk already has | What is missing |
|---|---|---|---|
| 1 | Independent review, acceptance tied to a version | Desk reviews each result itself (Desk rule 4 in `agent/prompts.ts`), `review_diff`, and `message_thread kind: "revision"` capped by `settings.review_rounds` (default 2, `runtime.ts` `sendRevision`). `spawn_thread` can pick a different model, so a reviewer can already be GPT when the builder was Claude. | No reviewer role. `agent.result` records a summary and library paths, not a commit or content hash. "Done" is the only end state: there is no submitted / accepted / changes-requested, and nothing goes stale when the code moves. A reviewer thread cannot read the builder's worktree (`readRoots` gives a thread only its own workspace, sources, library and skills); for git work it can only `git checkout --detach` the builder's branch by hand, and for documents only what was published. |
| 2 | Execution receipts from the platform | Every tool call and result is an event (`tool.call`, `tool.result`); `bash` puts `[exit code N]` at the top of its output; Desk can read a whole transcript with `read_thread mode: full`. | Exit status is text inside `content`, not a field. No cwd, git HEAD, duration, input or output hashes. Nothing for Desk to query ("which commands did Eval run on commit X, and did they pass?") short of reading the transcript into its context. |
| 3 | "Resume" instead of "revision" after a crash | The user's own message reopens any thread (`wakeDecision` 4.1). | Desk can reopen a failed thread only with a revision, which tells the thread its work fell short and spends a review round (`runtime.ts` `send`: a note to a done or failed thread is refused). |
| 4 | Resource locks and a scheduler | `max_concurrent_threads` (default 4) and the per-project wake budgets. | No named resources ("one heavy job at a time"), no disk headroom check, nothing in the Threads panel showing who holds what. Today this lives in chat and in scripts threads wrote. |
| 5 | Editable memory | `memory_write` with `supersedes`; the user can edit entries from the app (stored as a supersede) and delete them. | Agents cannot edit in place, retire an entry without replacing it, or change several at once (cleaning up meant ~15 one-by-one supersedes). No current/historical flag, no links to a commit, thread or library file. |
| 6 | Cheap check jobs that are not threads | Desk has `bash_readonly` (synchronous, up to 600 s, no writes outside temp, output lands in Desk's own context). Automations have script steps with logs, but only the user turns an automation on. | A background, model-less "run this, tell me how it ended" job: no workspace, no brief, no model; it writes to a scratch folder, fails on a non-zero exit or a missing output, and wakes Desk with a short result. |
| 7 | Cost per thread | `usage` events, `usage_totals` per agent, model and day, the thread's Usage tab (tokens), `desk usage <project>`. | Not in the Threads list and not in `list_threads`, so Desk budgets blind. No prices: the model registry has none, so dollars would need a per-model price the user sets (tokens are the honest default, since the endpoint may be a subscription proxy). |
| 8 | A project ledger | The plan (`update_plan`: title, status, thread ids, notes), What's up, memory, library, reports. | Nothing durable ties a plan item to the submissions, reviews and receipts behind it, so the coordinator keeps that in its context or an untracked `docs/THREADS.md`. Items 1, 2 and 9 together are that ledger. |
| 9 | Gates in the plan | Plan item statuses: todo, in_progress, done, dropped. | No gates ("G1–G5 before fast-forward"). A gate would be a named condition on a plan item that an acceptance (item 1) or a receipt (item 2) satisfies. |
| 10 | Shared read-only scratch | The library, for published artifacts. | A thread cannot read Desk's workspace or a sibling thread's workspace, so intermediate files (renders, extracts) are redone or published just to be shared. |
| 11 | Subscriptions on thread traffic | Desk sees the latest 12 thread-to-thread messages under "Thread traffic" in its prompt, by design without being woken (messaging spec §6.2); `wait_for_threads` wakes on reports, questions and approvals. | No "wake me when Eval posts X". Desk either polls or waits for a formal report. |
| 12 | Dependencies and stale results | Nothing. | Recording that a score used evaluator X and answer key Y, and flagging conclusions whose inputs changed. Needs receipts (hashes) and versioned acceptance first. |

## 2. Proposed order

Grouped so each phase ships on its own and later phases build on earlier ones.

### Phase A · Quick fixes (small, independent)

- **A1 · Resume a failed thread. Done.** `message_thread kind: "resume"` (Desk only) reopens a `failed` thread with a plain "continue where you left off" message. No review round, no "fell short" framing. A note to a failed thread is refused with a pointer to `resume`, and resume to any other status is refused (`runtime.ts` `checkResume`, wake rule 4.3).
- **A2 · Tokens per thread where Desk and the user look.** Add prompt and completion totals to the Threads list (both UIs) and to `list_threads` / `read_thread`. Optional per-model prices in Settings → Models turn them into dollars.
- **A3 · Memory maintenance for agents.** `memory_update` (edit in place, keeps history as events), `memory_retire` (mark historical without a replacement), several changes in one call, and optional links (thread, commit, library path) shown with the entry.

### Phase B · Produced, checked, accepted (the core ask)

**Done** (Plan 23): spec `docs/superpowers/specs/2026-10-02-reviews-and-acceptance-design.md`, approved with every recommendation.

Both coordinators ranked this first.

- **B1 · Submissions pinned to a version.** `complete` records what it submits: the worktree's HEAD commit (refused if there are uncommitted changes on a git thread) and a hash of each library artifact. A later commit on that branch marks the submission superseded.
- **B2 · Review threads.** `spawn_review(thread, question, criteria)` starts a reviewer that gets the original brief and criteria, a read-only snapshot of the submitted commit or artifacts, and permission to run its own checks, but not the builder's completion summary or transcript until it has filed a first assessment. It ends with `submit_review`: per requirement satisfied or not, findings (blocking or not, with a reproducer), what it did not check, and a verdict where "no material issues found" is a normal answer. The reviewer can use a different model than the builder.
- **B3 · Acceptance.** Desk records accepted, accepted with limitations ("UI accepted; engine accuracy not established") or changes requested, always against a specific submission. A builder never accepts its own work. Open blocking findings stay visible on the thread until fixed or waived. Fixes go back to the same reviewer. After the review-round limit, Desk must change approach or report the blocker instead of sending another revision. Threads show their acceptance state in both UIs; the report's merge order lists only accepted branches unless the user overrides.

### Phase C · Evidence the platform produces

- **C1 · Execution receipts.** `bash` and `bash_background` results get structured fields: cwd, git HEAD of the workspace, exit code, duration, output size and hash. A Desk tool, `list_receipts(thread, filter)`, answers "what ran on commit X and how did it end" without reading the transcript.
- **C2 · Check jobs.** `run_check(command, inputs, expected_outputs)` for Desk (and threads): a model-less background job in a fresh scratch folder, sandboxed like `bash`. It fails when any stage exits non-zero or an expected output is missing or empty, records a receipt with input and output hashes, and wakes the caller with a few lines. This is the cheap independent verification the coordinators asked for, and the building block for gates.

### Phase D · Coordination

- **D1 · Resource claims.** Named project resources with a capacity ("heavy-job: 1", optionally "needs 1.5 GiB free"), `claim_resource` / `release_resource`, a queue, automatic release when the holder finishes or is stopped, and the holder shown in the Threads panel.
- **D2 · Watch a thread.** `watch_thread(thread, until?)` wakes Desk once on that thread's next message (optionally matching a phrase), counted against the lifecycle wake budget.
- **D3 · Read-only scratch.** Threads can read Desk's workspace and, optionally, their siblings' workspaces, read-only (sandbox profile change; needs a security pass against the invariants in `CLAUDE.md`).

### Phase E · Ledger, gates, staleness

- **E1 · Gates on plan items.** A plan item lists gates, each satisfied by an acceptance (B3) or a passing receipt or check (C1, C2). The plan view shows gate state; a plan item cannot be marked done with an open gate unless the user waives it. Plan, submissions, reviews and receipts together are the project ledger.
- **E2 · Dependencies and staleness.** Receipts and acceptances already record the versions and hashes they used. When one of those inputs changes, everything that depended on it is flagged stale in the plan and in Desk's prompt.

## 3. Recommended first step

Start Phase A1 (resume) right away: it is small, removes a real misuse of revisions, and needs no design choices. In parallel, write the spec for Phase B (B1–B3), since it touches events, the thread lifecycle, both UIs and the coordinator prompt, and has choices Louis should see before code (for example whether acceptance should ever block a merge order, and how much of the builder's summary a reviewer sees, and when).

Phase C1 is the strongest candidate to follow B, because receipts make reviews cheaper and gates possible. D and E wait until B and C are in use on a real project.

## 4. Decisions for Louis

1. **Order:** Phase A1 now plus the Phase B spec (recommended), or a different first item.
2. **Phase A2 cost:** tokens only (recommended, since the model endpoint may be a flat-rate proxy), or tokens plus user-set prices.
3. **Phase D3 scratch:** Desk's workspace only (recommended), or sibling threads' workspaces too.
