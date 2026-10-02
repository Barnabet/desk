# Plan 23 · Reviews and acceptance — Implementation Plan

**Goal:** a thread's `complete` records a submission pinned to a commit and artifact hashes; Desk (or the user) can have it reviewed by an independent reviewer thread that cannot see the builder's report until it has formed its own view; findings carry reproducers; and Desk or the user records acceptance against that exact submission. Spec: `docs/superpowers/specs/2026-10-02-reviews-and-acceptance-design.md` (approved 2026-10-02 with every recommendation).

**Architecture:**
- `@desk/protocol`: events `submission.created`, `review.requested`, `review.assessed`, `finding.raised`, `finding.resolved`, `acceptance.recorded`; message kind `review`; `Acceptance` and `ReviewVerdict` enums; setting `review_model`; API bodies for the user's actions and the review view.
- `@desk/core`: migration `0007_reviews` (tables `submissions`, `reviews`, `findings`; `agents.acceptance`, `agents.accepted_submission_id`, `agents.reviews_submission_id`), projections, `reviews/` (queries, formatting, reviewer model choice), `Runtime.requestReview` / `acceptSubmission` / `waiveFinding`, `complete` recording submissions, reviewer tools (`raise_finding`, `submit_assessment`, `resolve_finding`), Desk tools (`request_review`, `accept_submission`), withheld context for reviewers, wake rule for `review`, prompts.
- `apps/daemon` routes, `@desk/client` methods, `@desk/bff` operations (`threads.review`, `threads.requestReview`, `threads.accept`, `threads.waiveFinding`), phone policy (only `threads.review`).
- Both UIs: acceptance chip on roster cards and the thread head, a Review tab with submissions, reviews, findings and the actions; CLI `desk review <thread>` and the chip in `desk threads`.

## Global constraints

- `pnpm typecheck` and `pnpm test` pass before every commit (the web UI needs Node ≥ 22.22.3; zsh must exist for the shell tests).
- Additive migration only, generated with drizzle-kit.
- State changes are events; projections update tables in the same transaction.
- Tools reach the runtime only through `ctx.services`.
- Both UIs ship together; operations are called by literal name (parity guard).
- Agent text (findings, reports) renders through `SafeMarkdown` / quoted lines, never as HTML or unquoted prompt lines.

## Tasks

### S1 · Submissions
1. Protocol: the six events, `Acceptance`, `ReviewVerdict`, `FindingState`; settings `review_model` (nullable, default null).
2. Schema + migration `0007_reviews`; projections for `submission.created` (insert, supersede the thread's previous submission, reset `agents.acceptance` to `none`, clear `accepted_submission_id`).
3. `complete`: optional `claims`, `limitations`, `evidence`. Git thread: refuse when `git status --porcelain` is not empty ("commit your work first with git_commit"); read `HEAD`. Hash each listed artifact (library file, through `agent-files.ts`). Append `agent.result` and `submission.created` together.
4. Completion notice: `Submission N: commit abc1234, 2 artifacts.`
5. `formatThreadLine` / `formatThreadSummary` show acceptance and the latest submission.
6. Tests: submission recorded (git and non-git), dirty worktree refused, supersede resets acceptance, notice line.

### S2 · Reviewer threads
1. `Runtime.requestReview({threadId, criteria, focus?, model?, reasoningEffort?, reviewerId?, by})`: refusals (no submission, archived, open review, reviewer not a reviewer of this builder); reviewer model choice; a new thread titled `Review: <title>` with a runtime-built brief; git submission → worktree on `desk/review-<slug>-<id>` at the commit (`createWorkspace` gains `at`); `agents.reviews_submission_id`; `review.requested`; builder acceptance `in_review`.
2. Reviewer tools in `tools/review.ts`: `raise_finding`, `submit_assessment` (first call initial → result carries the builder's report; second final → `review.assessed` final, `agent.result` with the rendered review, yields `done`), `resolve_finding`. `toolsForRole`: a thread with `reviews_submission_id` gets these instead of `complete`.
3. Withheld context until the initial assessment: thread `read_thread` on the builder shows brief and status only; `send` refuses builder ↔ reviewer messages.
4. Reviewer prompt section; builder acceptance → `reviewed` on the final assessment.
5. Re-review: `reviewerId` reopens a done reviewer with a `review` message (wake rule 4.3), resets its review branch to the new commit after `git stash -u`, new `review.requested` with `revealed: true`; the message lists its open findings and the builder's new report.
6. Tests: refusals, worktree at commit, withheld read_thread and messages, two-phase assessment, findings, re-review.

### S3 · Acceptance
1. `Runtime.acceptSubmission({threadId, decision, limitations, waive, note, by})`: latest submission; open blocking findings must be waived (Desk with reasons; the user any); `changes_requested` → revision with the open findings (review-round limit applies; refusal text now says change approach, accept with limitations or report the blocker); `acceptance.recorded`; notice to Desk when the user acts.
2. `Runtime.waiveFinding({findingId, reason, by})`.
3. Desk tools `request_review`, `accept_submission`; `update_settings` takes `review_model`.
4. Desk rule 4 rewritten; thread prompt asks for claims, limitations and evidence.
5. Tests.

### S4 · Clients
1. Daemon: `GET /threads/:id/review`, `POST /threads/:id/review`, `POST /threads/:id/accept`, `POST /findings/:id/waive`; client methods; bff ops and handlers; phone policy allows `threads.review` only.
2. `@desk/ui-core`: `acceptanceChip(agent)` and `reviewView(response)` (pure, tested).
3. Desktop and web: chip on roster cards and the thread head; Review tab (submissions, reviews, requirements, findings, actions); FEED label for `review`; specs for both.
4. CLI: chip in `desk threads`, `desk review <thread>`.
5. `docs/api.md`, `docs/desktop.md`, `docs/web.md` mentions.
