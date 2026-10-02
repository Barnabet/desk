# Reviews and acceptance — design

**Status:** approved by Louis on 2026-10-02 with every recommendation in §10. Phase B of the harness roadmap (`docs/superpowers/plans/2026-10-02-harness-improvements-roadmap.md`). Implementation: Plan 23 (`docs/superpowers/plans/2026-10-02-plan-23-reviews-and-acceptance.md`).

Today a thread's `complete` is the end of the story: Desk reads the summary, maybe sends a revision, and the thread is "done". This design makes three facts separate, recorded and visible in Desk:

- **Submitted:** a thread says "this exact version is my work" (B1).
- **Reviewed:** an independent reviewer thread checked that version against the brief and filed findings (B2).
- **Accepted:** Desk (or the user) recorded a decision against that version, with any limitations (B3).

Nothing here replaces threads, revisions or the review-round limit. It adds records and two tools around them.

## 0. Key decisions

1. **A submission is a version.** Every `complete` records a submission: the worktree's HEAD commit for a git thread, and a SHA-256 for each library artifact it lists. A later `complete` by the same thread (after a revision or a resume) is a new submission that supersedes the earlier one. Acceptance, reviews and findings always name a submission, never just a thread.
2. **Thread status does not change.** `done` still means the thread finished its run. Acceptance is a separate field (`none`, `in_review`, `reviewed`, `changes_requested`, `accepted`, `accepted_with_limitations`), shown next to the status. `reviewed` means a reviewer filed its final assessment and Desk has not decided yet.
3. **A reviewer is a thread with a link.** `request_review` spawns an ordinary thread (same scheduler, workspace, sandbox, wake rules, usage) whose row records the submission it reviews. It gets its own tools for its verdict and cannot `complete` in the usual way.
4. **Independence is enforced, not requested.** Until it files its first assessment, a reviewer cannot read the builder's summary, transcript or messages, and the builder cannot message it. After that the runtime shows it the builder's summary and limitations, and it files its final review.
5. **Only Desk or the user accepts.** A thread can never accept or waive anything. Desk cannot accept a submission with an open blocking finding unless it waives that finding with a reason; the user can always override.
6. **Bounded repair.** Findings flow back to the builder as a revision (one round), the fix is a new submission, and the same reviewer re-checks it. At the review-round limit, the revision tool refuses as today; its text changes from "accept the work with noted caveats or escalate to the user" to "change the approach (a new thread with a simpler plan), accept with limitations, or report the blocker to the user".

## 1. Records

All state is events, projected into three new tables (one additive migration).

### 1.1 Events (`protocol/src/events.ts`)

| Event | Stream | Payload |
|---|---|---|
| `submission.created` | builder thread | `submission_id`, `seq` (1, 2, … per thread), `commit` (null without git), `base` (null without git), `artifacts: [{path, sha256}]`, `claims: string[]` (requirements the thread says it met), `limitations: string[]`, `evidence: string` (how it verified) |
| `review.requested` | reviewer thread | `review_id`, `submission_id`, `criteria: string[]`, `focus` (optional question), `requested_by` (`desk` or `user`) |
| `review.assessed` | reviewer thread | `review_id`, `phase` (`initial` or `final`), `verdict` (`no_material_issues`, `issues_found`, `could_not_review`), `requirements: [{criterion, met: yes/no/unknown, note}]`, `not_checked: string[]` |
| `finding.raised` | reviewer thread | `finding_id`, `review_id`, `submission_id`, `title`, `detail`, `blocking: boolean`, `reproducer` (a command, a file and line, or steps) |
| `finding.resolved` | builder or Desk | `finding_id`, `how` (`fixed_in` a later submission id, `waived` with `reason` and `by`, or `withdrawn` by its reviewer) |
| `acceptance.recorded` | builder thread | `submission_id`, `decision` (`accepted`, `accepted_with_limitations`, `changes_requested`), `limitations: string[]`, `by` (`desk` or `user`), `note` |

`agent.result` stays as it is (the summary and artifacts keep driving notices and the UI). `submission.created` is appended in the same transaction.

### 1.2 Tables (projections)

- `submissions(id, project_id, thread_id, seq, commit, base, artifacts json, claims json, limitations json, evidence, superseded_by, created_at)`
- `reviews(id, project_id, submission_id, reviewer_id, criteria json, phase, verdict, requirements json, not_checked json, created_at, updated_at)`
- `findings(id, project_id, review_id, submission_id, title, detail, blocking, reproducer, state, resolved_how json, created_at)`
- `agents` gains `acceptance` (text, default `none`), `accepted_submission_id`, and for reviewers `reviews_submission_id`.

## 2. Submitting (B1)

`complete` keeps its inputs and gains three optional ones: `claims`, `limitations`, `evidence`. The thread prompt asks for them ("list each requirement of your brief you met, what you did not do, and how you checked").

For a git thread, the runtime reads `HEAD` of the worktree after the thread's last tool call. If the worktree has uncommitted changes, it refuses `complete` with "commit your work first" (§10 decision 1). For library artifacts it hashes each file listed (`tools/agent-files.ts` reads, as today).

The completion notice to Desk adds one line: `Submission 2: commit 3f9a1c0, 2 artifacts.` A revision or a resume leaves the old submission in place; the next `complete` supersedes it, and any acceptance of the old one stays recorded but no longer applies (the thread's `acceptance` goes back to `none`).

## 3. Reviewing (B2)

### 3.1 `request_review` (Desk tool; the user gets the same action in the thread's page)

Inputs: `thread_id`, `criteria: string[]` (acceptance criteria; at least one), `focus` (optional narrow question, such as "does the comparator drop duplicate records?"), `model` and `reasoning_effort` (optional), `reviewer` (optional: the id of an earlier reviewer to reuse, §3.4).

It refuses a thread with no submission, a superseded submission, and a reviewer for a submission that already has an open review.

It spawns a thread titled `Review: <builder title>` with:

- **Brief, built by the runtime:** the builder's original brief, the criteria, the focus, and the submission (commit and artifact paths). Desk's own words go in the criteria and focus only.
- **Workspace:** for a git submission, a new worktree on its own review branch (`desk/review-…`) created **at the submitted commit**, so it can build and test exactly that version without touching the builder's branch (a branch rather than a detached HEAD, so every git tool works as in any thread). Without git, an empty workspace; the artifacts are already immutable library copies, and the project's sources are readable as for any thread.
- **Model:** as given, else the project's `review_model` setting (new), else a different model family from the builder's (§10 decision 3).
- **Skills:** the builder's active skills, so it can open the same file types.

### 3.2 What the reviewer cannot see before its first assessment

- `read_thread` on the builder returns its brief and status only (no result, no last message).
- The Team section lists the builder by title and brief only.
- The builder cannot `message_thread` the reviewer, and the reviewer cannot message the builder.
- Desk's messages to the reviewer are allowed (it may need to answer "where is the input file?"), but the Desk prompt says not to pass on the builder's claims.

Memory and the library stay visible: they are project knowledge, not the builder's argument. A memory entry the builder wrote is already marked "by thread …".

### 3.3 The reviewer's tools

Reviewer threads get every normal thread tool except `complete`, plus:

- `raise_finding(title, detail, blocking, reproducer)`: one material problem, with how to see it.
- `submit_assessment(verdict, requirements, not_checked)`: called twice. The first call (`initial`) unlocks the builder's summary, claims, limitations and evidence, which come back as that call's result. The second (`final`) reconciles them and ends the review: the thread is `done`, and its result (the completion notice Desk reads) is the review.
- `resolve_finding(finding_id, outcome: fixed | withdrawn, note)`: on a re-review, a finding the new submission fixes; or one of its own findings it no longer stands by.

The reviewer prompt says plainly that `no_material_issues` is a good outcome when it is true, that each finding needs a reproducer it ran itself, and that "not checked" must list what it skipped. It must not edit the builder's branch (its worktree is detached; it may change files there to test a hypothesis, and nothing it does there reaches the builder).

### 3.4 Re-review

After a revision, the builder submits again. Desk calls `request_review` with `reviewer` set to the earlier reviewer: that reviewer thread is reopened by a message of the new kind `review` (like a revision, without spending anyone's rounds), its review branch is moved to the new commit (its own local changes are kept in `git stash`), and it receives the builder's new report and the list of its open findings. It marks each one fixed (`resolve_finding`) or leaves it open, may raise new ones, and files one final assessment (the builder's report is already in front of it, so there is no initial phase). This keeps the same critic on the same problem, as the coordinators asked.

## 4. Accepting (B3)

`accept_submission` (Desk tool; the user's Accept button calls the same operation):

- Inputs: `thread_id`, `decision` (`accepted`, `accepted_with_limitations`, `changes_requested`), `limitations` (required for `accepted_with_limitations`), `waive: [{finding_id, reason}]`, `note`.
- It always applies to the thread's latest submission.
- Refused when a blocking finding on that submission is open and not in `waive`. The user can waive without a reason.
- `changes_requested` sends the open findings (plus `note`) to the builder as a revision, counted against `review_rounds` as today, and the thread's acceptance becomes `changes_requested`.
- Desk is never the builder, and threads do not get this tool, so nobody accepts their own work.

What acceptance changes (a label only, §10 decision 4):

- The thread list and the thread page show it: a chip `Accepted`, `Accepted · 2 limitations`, `Changes requested`, `In review`.
- Desk's `report` lists each branch with its acceptance state. Unaccepted branches are listed separately as "not accepted".
- Plan items linked to a thread show its acceptance (prepares E1 gates).

## 5. Lifecycle notices to Desk

- Reviewer finished: `Review of "<builder>" (submission 2): issues found, 1 blocking. Findings: #f1 "Comparator drops duplicate fact IDs" (blocking), #f2 … Not checked: …`
- Builder submitted again while a review of the old submission is still running: the reviewer gets a note that its submission was superseded, and Desk is told.
- The user accepted or waived something: a notice to Desk, so its plan stays in line.

## 6. Prompts

- **Desk rule 4 (Review)** becomes: check every result against its brief. For work whose correctness matters (code others build on, numbers people act on, anything the user will rely on), request an independent review with explicit criteria rather than trusting the summary or the test count. Accept against the submission, with limitations when only part is established. At the round limit, change the approach or report the blocker.
- **Thread prompt:** `complete` asks for claims, limitations and evidence; it says the result will be reviewed independently.
- **Reviewer prompt:** a new role section (§3.3).

## 7. UI (both apps, parity rules apply)

- **Threads list and roster cards:** the acceptance chip, and for a reviewer `Reviewing <builder>`.
- **Thread page:** a new **Review** tab: submissions (newest first, with commit and artifacts), each with its reviews, requirements table and findings (blocking first, with reproducer and state). Buttons: Request review, Accept, Accept with limitations, Request changes, Waive (per finding).
- **Line diagram:** a reviewer forks from the builder's lane, not from Desk's trunk.
- **Attention:** nothing new by default. If Desk asks the user to decide (for example, to waive a blocking finding), it does so with `ask_user` as today.
- **Phone policy:** viewing reviews is allowed; requesting reviews, accepting and waiving stay off the phone allow list (§10 decision 5).
- **CLI:** `desk threads` shows the chip; `desk review <thread>` prints the Review tab.

## 8. Safety

- No new process or shell path: reviewers are threads under the same sandbox and policy.
- A detached worktree is a normal worktree of the same source: it is created and removed like a thread's (`workspaces.ts`), and its archive removes it.
- Agents still never merge. Acceptance is a record, not a merge.
- Reviewer text is agent text: findings render through `SafeMarkdown`, and Desk treats them like any thread claim (rule 12).

## 9. Slices

| Slice | Scope |
|---|---|
| S1 | Events, migration, projections; `complete` records submissions; notice line; API read endpoints; tests. No UI. |
| S2 | `request_review`, reviewer role (detached worktree, withheld context, two-phase assessment, findings); reviewer prompt; notices. |
| S3 | `accept_submission`, waivers, `changes_requested` as a revision, re-review by the same reviewer; Desk rule 4; report changes. |
| S4 | Both UIs: chips, Review tab, actions, line diagram fork; CLI; phone policy. |

S1–S3 can ship and be used by Desk before the UI lands, since Desk reads everything through its tools.

## 10. Decisions (Louis, 2026-10-02: every recommendation)

1. **Uncommitted work at `complete`:** refused until the thread commits; the thread decides what the version is.
2. **When a review is required:** never enforced. Desk decides from rule 4.
3. **Reviewer model:** the project setting `review_model`. When it is unset, the reviewer gets Desk's model if its family differs from the builder's, else the first configured model of another family, else the thread model.
4. **How hard acceptance is:** a label. Reports list unaccepted work separately; nothing is blocked.
5. **Phone:** reviews are visible on the phone; requesting a review, accepting and waiving are Mac only (not in `phone-policy.ts`).
