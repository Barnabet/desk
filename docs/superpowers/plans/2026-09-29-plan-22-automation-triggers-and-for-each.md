# Plan 22 · Folder and webhook triggers, and for-each steps — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automations can start when a file lands in a folder or when a local tool calls a webhook, and a new step kind, **For each**, runs another automation once per item of a list or per file in a folder. All three ship in the backend, the API, client and CLI, Desk's tools and prompt, and both UIs (desktop and web) together.

**Architecture:**
- **Two new trigger kinds** join `schedule` in `AutomationDefinition.triggers`: `folder` and `webhook`. Unlike schedules, they carry a stable `id`, because a webhook's URL and a folder's history of files seen must survive edits that reorder triggers.
- **One queue for event triggers.** A settled file or a webhook call appends `automation.trigger_queued`. The engine starts the oldest queued item whenever the automation has no non-test run going, so a burst of files becomes a line of runs, never lost and never overlapping. Schedules keep their skip-when-running rule (spec §5.1).
- **The folder watcher** (`automations/watch.ts`) is a hint plus a scan. `fs.watch` (FSEvents on macOS) wakes a debounced scan, and the engine's 30 s tick scans too, so a missed or coalesced watch event only delays a file by one tick. What counts as a new file is decided by a pure function (`watch-scan.ts`) from the listing, the trigger's `watch_since` cursor and the files already seen, which are projected from events.
- **Webhooks** are served by deskd at `POST /hooks/:automation_id/:trigger_id`, outside `/v1` and its bearer token. Each webhook trigger has its own secret, which only the user can create (it is shown once) and whose SHA-256 is all deskd keeps. deskd still listens on 127.0.0.1 only, and the route refuses browser requests.
- **What an agent changes, the user re-approves.** A version saved by an agent that adds or changes a folder or webhook trigger *holds* those triggers (`triggers_held`) until the user keeps them, the way an agent save suspends grants (spec §5.3).
- **For each** is a step kind that reuses sub-automation runs: one child run per item (trigger `parent`), at most `parallel` at a time, with `{{item}}` and `{{item_index}}` in the child's input templates. Its outputs are counts, the child run ids, and optionally lists collected from the children's `output_step` outputs. The graph stays acyclic: a loop body is always another automation.

**Tech Stack:**
- TypeScript (strict, ESM, tsx loader), zod 4, drizzle (SQLite), Hono, commander
- Node's `fs.watch` with `recursive` (FSEvents on macOS) and `fast-glob` (already a dependency of `@desk/core`); no new dependency
- React 19 + React Flow in the desktop renderer; Angular 22 (zoneless, signals, OnPush) in `apps/web-ui`
- Vitest with the core harness (`automationHarness`, `FakeClock`), the fake model, `@testing-library/react` and `@testing-library/angular`, Playwright for both e2e suites

**Spec:** `docs/superpowers/specs/2026-09-26-automations-design.md`. Read it first, with Plan 19 (backend), Plan 20 (desktop) and Plan 21 (web UI), which this plan extends. The spec's §12 listed these features as out of scope; the design below is this plan's, and Task 22 folds it into the spec as §14 once it ships.

## Global Constraints

- `pnpm typecheck` and `pnpm test` pass before every commit. Run a single root test file with `pnpm vitest run <path> --maxWorkers=2`, and a single web spec with `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include <spec>)`.
- Commit only your own files, by path (never `git add -A`): `~/desk` is a shared checkout. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work on the branch `automation-triggers`, created from master (`git switch -c automation-triggers master` the first time). Nothing merges to master until the user says so.
- Never squash or edit existing migrations. Add one with `cd packages/core && npx drizzle-kit generate --name automation_triggers`.
- zod 4: object defaults use `.prefault({})`, not `.default({})`.
- **Two UIs.** Every UI change ships in the React renderer and in the Angular web UI in the same task, from logic in `@desk/ui-core` and CSS in `@desk/ui-styles`. New operations go in `@desk/bff/contract` and are called by their literal name in both UIs (`call('automations.createHookSecret', …)`), so `parity.spec.ts` sees them. Plan 17's and Plan 21's port conventions apply to every Angular component (see Plan 21, "Port conventions").
- **Only the user** turns automations on, sets grants, creates or rotates a webhook secret, and keeps held triggers. None of these has an agent tool, and the web UI and desktop call them only from the user's clicks.
- **Secrets.** A webhook secret is returned once, by the call that creates it, and is never written to events, logs, `daemon.json`, `config.json`, other API responses or tool environments. Events and tables hold only `sha256(secret)` in hex.
- **deskd reads what agents or other programs control only through `tools/agent-files.ts`.** A watched folder is listed with `lstat` (no symlink followed), and a file that triggers a run reaches the run only as a `file` input, copied by `prepareRunFolder` as today.
- The machine has 8 GB of RAM: run the full suite once per task at most, never two suites at once, and one Electron or Chromium run at a time. Check `df -h ~` before building or running e2e, and stop if less than 500 MB is free.
- In a Claude shell, `apps/web-ui` commands need `export PATH=~/.nvm/versions/node/v22.23.3/bin:$PATH` first (`node scripts/ng.mjs --which` prints the Node it uses).

## Design (decisions recorded while planning)

These are this plan's choices where the spec is silent. Each one names what was weighed.

### D1. Triggers

The trigger union becomes:

```ts
triggers: Array<ScheduleTrigger | FolderTrigger | WebhookTrigger>   // at most 10, as today
```

| Field | `folder` | `webhook` |
|---|---|---|
| `id` | `^[a-z][a-z0-9_-]{0,39}$`, unique among the definition's folder and webhook triggers | same |
| `path` | an absolute folder path (`~/` expands to the home folder at validation and at run time) | — |
| `pattern` | globs relative to `path`, default `['*']`; `**` needs `recursive` | — |
| `recursive` | default `false`; scans at most 4 levels deep | — |
| `on` | `['added']` (default) or `['added', 'changed']` | — |
| `settle_seconds` | default 5 (1–300): a file triggers once its size and mtime stay the same that long | — |
| `input` | the key of a `file` input that receives the file | — |
| `inputs` | fixed values for other inputs, as on schedules | defaults for inputs the call leaves out |

- **Why a stable `id`.** Schedules are addressed by index (`last_due`, `trigger_index`), which is fine because an index change only resets a cursor. A webhook's URL is given to other programs, and a folder's files-seen history must not re-fire every file after the user reorders triggers. Schedules keep their index; `trigger_index` stays on `run_started` and `trigger_skipped` for them.
- **One run per file.** A run gets one file through the named `file` input. Batching ("all the files that arrived in the last hour") is a schedule plus a folder input, which already exists.
- **Arrival time** is `max(mtimeMs, ctimeMs, birthtimeMs)`. Finder copies keep the source's mtime, but set birthtime; moves within a volume update ctime. A file triggers only when its arrival time is after the trigger's `watch_since`.
- **Ignored names:** dotfiles, `~$*`, and `*.crdownload`, `*.download`, `*.part`, `*.partial`, `*.tmp`, `*.icloud` (half-written downloads and cloud placeholders), plus anything that is not a regular file after `lstat`.
- **Seen files** are `(automation_id, trigger_id, path, size, mtime_ms)` rows projected from `trigger_queued` and folder `trigger_skipped` events. With `on: ['added']`, a path seen once never triggers again; with `changed`, a new size or mtime triggers again.
- **`watch_since`** is set per folder trigger, like a schedule's `last_due` (spec §2.3): by the event time of `automation.switched {enabled: true}`, and of an `automation.saved` that adds the trigger or changes its `path`, `pattern` or `recursive` while the automation is on. Files already in the folder when it is turned on never start runs (the same rule as "a schedule never fires straight away").
- **While deskd is down**, files that arrive are picked up by the first scan after start: their arrival time is after `watch_since`, and they are not seen.
- **Limits.** A scan looks at 2000 entries at most, and queues at most 50 files per automation (see D3). A scan that hits the entry cap reports `too_many_files` in the trigger's watch status and handles the oldest arrivals first.
- **Folder rules** (validation, and again before each scan, on the real path): not the filesystem root, not the home folder or a folder containing it, not Desk's data folder or anything inside or around it (the checks of `Runtime.unsafeSource`, which moves to `tools/sandbox.ts` as `unsafeFolder` so both use it), and not a path covered by the sandbox guard's secrets or `secretPatterns`. A missing folder is not an error at save time (a drive may be unplugged); the watch status says `missing` and the watcher retries on each tick.
- **Watch status** is in memory, per trigger: `watching`, `missing`, `refused` (with the reason), `too_many_files`, `held`, or `off`. The automation detail carries it, so the Start inspector and the Turn-on dialog can show it.

A webhook:

- **URL:** `http://127.0.0.1:<port>/hooks/<automation_id>/<trigger_id>`, `<port>` being deskd's current port (from `daemon.json`; the detail view says when it is not the default 7433, since then the URL changes when deskd restarts).
- **Call:** `POST` with `Authorization: Bearer <secret>`, `Content-Type: application/json`, a body of at most 64 KB: `{"inputs": {"key": value}}` or `{}`. Values are checked by `resolveInputs`. `file` and `folder` inputs cannot come from a webhook (400): a leaked secret must not let a caller copy arbitrary local files into a run.
- **Answers:** `202 {status: 'started', run_id}`, `202 {status: 'queued', position}`, `400` bad body or inputs, `401` missing or wrong secret (also when no secret was created), `403` a request carrying an `Origin` header, `404` unknown automation or trigger (or a deleted automation), `409 {code: 'off' | 'held' | 'queue_full'}`, `413` body too big, `421` a `Host` other than `127.0.0.1:<port>` or `localhost:<port>`, `429` more than 30 calls in a minute on that trigger.
- **Why these checks.** deskd listens on 127.0.0.1, so only local programs reach it. A web page can still send a request to 127.0.0.1 (CSRF), or reach it under its own hostname (DNS rebinding). Browsers always send `Origin` on cross-origin and non-GET requests and put the page's hostname in `Host`, so the route refuses both; the secret covers everything else. Agents cannot call webhooks: the sandbox already blocks deskd's port.
- **Secrets.** `POST /v1/automations/:aid/triggers/:tid/secret` (user only) makes 32 random bytes (base64url), appends `automation.hook_secret_set {automation_id, trigger_id, hash}`, and returns `{secret, url}` once. Calling it again rotates. Comparing uses `timingSafeEqual` on the hashes. A save that removes a webhook trigger drops its hash (the projection), so the route 404s and a trigger re-added with the same id needs a new secret.
- **Out of reach on purpose:** calls from other machines. deskd never listens beyond loopback. A user who wants a public webhook runs their own tunnel; the docs say so and say what that exposes.

### D2. Held triggers

- `automation.saved` with origin `agent:*` sets `triggers_held` when its folder or webhook triggers differ from the previous version's (added, removed fields aside, or any field of one with the same id changed). Removing a trigger never holds.
- While held, folder and webhook triggers do not fire (webhook calls get `409 held`, scans are skipped, and the watch status says `held`). Schedules and Run now are unaffected.
- `POST /v1/automations/:aid/triggers/keep` (user) appends `automation.triggers_kept`, which clears it. So does `automation.switched {enabled: true}`, because the Turn-on dialog lists every trigger with its folder or URL.
- The Grants tab's suspended banner has a sibling, "Desk changed what starts this automation", with the trigger diff and **Keep triggers**. Attention gains `automation_triggers_held`, and Desk's `automation_list` flags it.
- **Why.** A folder trigger decides which of the user's files get copied into runs that agents read. A Desk steered by injected content must not be able to point it at another folder.

### D3. The queue

- `automation.trigger_queued {automation_id, trigger_id, queue_id, source, inputs}` with `source` = `{kind: 'folder', path, size, mtime_ms}` or `{kind: 'webhook'}`, projected into `automation_trigger_queue` (and, for folders, `automation_watch_seen`).
- The engine's `drainQueue(automationId)` runs on each tick, after a run of that automation finishes, and right after a queued event is appended. When the automation has no non-test run going (`runningRunOf`), it starts the oldest queued item. `run_started` carries `queue_id` and `trigger_id`, and its projection deletes the queue row.
- **Cap:** 50 queued items per automation. Over the cap, a folder file or a webhook call appends `automation.trigger_skipped {trigger_id, reason: 'queue_full'}` (a folder file is still marked seen, so it does not retry forever).
- **Turned off, deleted, or held:** turning an automation off keeps its queue; turning it on drains it. Deleting drops the queue (projection). A held trigger's queued items stay until the triggers are kept or the item's trigger disappears from the definition (then the drain drops it with `trigger_skipped {reason: 'trigger_removed'}`).
- **A run that cannot start** (the file vanished before the copy, an input no longer valid) is recorded as started and failed, like a scheduled run (`startRun` already does this for `schedule` and `parent`; it learns `folder` and `webhook`).
- **Why a queue and not skip.** A schedule that fires while a run is going will fire again; a file or a webhook call will not.

### D4. For each

```ts
ForEachStep = {
  ...stepBase, kind: 'for_each',
  over: { list: string }                     // exactly one {{path}} to a list output, or to a text input (one item per non-empty line)
      | { files: string[]; in: string },     // globs, in the folder named by exactly one {{path}}: a step's dir or a folder input
  automation: AutomationName,                // the automation each item runs
  inputs: Record<InputKey, string>,          // templates; {{item}} and {{item_index}} (1-based) are allowed here only
  parallel: 1..4 (default 1),
  max_items: 1..200 (default 50),            // more items than this fails the step: never silently truncated
  item_errors: 'stop' | 'continue' (default 'stop'),
  collect: InputKey[] (default [], at most 10),  // child output keys gathered into lists, one entry per item
}
```

- **Items** are resolved once, when the step starts, and recorded on the step (`items` on `automation.step_changed`, at most 200 strings, 16 KB in total like outputs), so recovery and retries see the same list. File items are absolute paths of files inside the run folder or a descendant run folder (`matchIn`, so no symlinks and never `.desk/`); an `in` folder outside those is refused at run time.
- **Running.** One child run per item, trigger `parent`, the parent's `test` flag and `by`, at most `parallel` at once. Children count towards `MAX_SUB_DEPTH` like sub-automations, and their agent steps towards the project's thread cap as usual. `run_started.parent` gains `attempt` and `item` (`{index}`), so a retry's children are told apart from an earlier attempt's.
- **Ending.** With `item_errors: 'stop'`, the first failed item cancels the running children, leaves the rest unstarted and fails the step with that item's reason (then `on_error` applies). With `continue`, the step succeeds once every item has ended, and downstream edges test `steps.<id>.outputs.failed > 0`.
- **Outputs:** `count`, `succeeded`, `failed` (numbers), `run_ids` (list, item order), and one list per `collect` key, holding each item's `output_step` value for that key (`''` for an item that failed or has no such output; a list value is joined with newlines). Outputs over the limits fail the step with a message naming the key.
- **Folder:** `steps.<id>.dir` is the step's own folder, where deskd writes `items.json`: `[{index, item, run_id, status, dir}]`, `dir` being each child's result folder. Nothing else ever writes a for-each step's folder.
- **Progress** for the UI: `automation.step_changed` gains `progress: {total, done, failed}`, projected to a new `progress` column.
- **Validation:** `automation` exists and is not the automation itself; no cycle across automations (the existing `subAutomationCycle` follows `for_each` too); `inputs` keys are the child's inputs, and its required inputs are covered; `over.list` names an upstream step's output (declared, for an agent step) or a `text` or `long_text` input; `over.in` names an upstream step's `dir` or a `folder` input; `{{item}}` appears only in a for-each step's `inputs`; `collect` keys are declared by the child's `output_step` when that step is an agent step.
- **Why a child automation per item** rather than a loop over steps in the same graph: the graph, its validation, the run view and recovery all stay acyclic and unchanged, each item gets its own run folder, report and transcript, and "for each" in the editor is one node with one inspector. The cost is that a loop body must be saved as its own automation; Desk's prompt says so, and the inspector links to the child.

### D5. What stays out

- Email triggers, cross-project automations, and Slack or email connectors (the spec's other §12 items).
- Webhooks reachable from other machines (D1).
- Watching a folder on a network share: FSEvents does not report remote changes, so it works only through the tick's scan. The docs say so; nothing refuses it.

## File Structure

### `packages/protocol`

- Modify `src/automations.ts`: `FolderTrigger`, `WebhookTrigger`, `AutomationTrigger` as a discriminated union, `TriggerId`, `ForEachStep` in `Step`, `RunTrigger` gains `folder` and `webhook`, limits (`MAX_QUEUED_TRIGGERS`, `FOREACH_MAX_ITEMS`, `WEBHOOK_BODY_MAX`, `WEBHOOK_RATE_PER_MIN`, `WATCH_SCAN_MAX`), views (`TriggerSummary`, `WatchStatus`, `ForEachItemInfo`, fields on `AutomationSummary`, `AutomationDetail`, `RunInfo`, `StepRunInfo`, `RunListEntry`).
- Modify `src/events.ts`: `automation.trigger_queued`, `automation.hook_secret_set`, `automation.triggers_kept`; `run_started` gains `trigger_id`, `queue_id`, `parent.attempt`, `parent.item` and `by` values `folder` and `webhook`; `step_changed` gains `items` and `progress`; `trigger_skipped` gains `trigger_id` and reasons `queue_full`, `trigger_removed`.
- Modify `src/api.ts`: `HookSecretResponse`, `WebhookCallBody`; attention kind `automation_triggers_held`.

### `packages/core`

- Modify `src/db/schema.ts`, add `drizzle/0007_automation_triggers.sql`: `automations.watch_since`, `automations.hook_hashes`, `automations.triggers_held`; `automation_runs.trigger_id`, `parent_attempt`, `item_index`; `automation_step_runs.items`, `progress`; tables `automation_trigger_queue`, `automation_watch_seen`.
- Modify `src/events/projections.ts`: the new events and fields; `carryLastDue` keyed to schedules only; `carryWatchSince`; `triggers_held`.
- Modify `src/automations/queries.ts`: queue, seen files, hook hashes, children of a for-each step.
- Create `src/automations/triggers.ts`: `schedulesOf`, `eventTriggersOf`, `triggerDiff`, `folderProblem`, `expandHome`.
- Create `src/automations/watch-scan.ts` + test: the pure scan decision (`scanFolder` listing, `decide`).
- Create `src/automations/watch.ts` + test: `FolderWatcher` (fs.watch hints, debounced scans, settle tracking, status).
- Create `src/automations/queue.ts` + test: `enqueue`, `drainQueue` helpers used by the engine.
- Create `src/automations/foreach.ts` + test: item resolution, outputs, `items.json`.
- Modify `src/automations/template.ts`: `item`, `item_index` paths, allowed only where the caller says.
- Modify `src/automations/validate.ts`: folder and webhook triggers, `for_each`, cycles through `for_each`.
- Modify `src/automations/scope.ts`: `timezoneOf` reads the first schedule; for-each item scope; `stepResultDir` for `for_each`.
- Modify `src/automations/engine.ts`: the queue, event triggers in the tick, `startRun` options, the `for_each` executor, its cancel and recovery, `childFinished` for items.
- Modify `src/automations/views.ts`, `service.ts`, `report.ts`: triggers in summaries and details, watch status, webhook URLs, held triggers, for-each progress in reports.
- Modify `src/tools/sandbox.ts`, `src/runtime/runtime.ts`: `unsafeFolder` shared; `hookSecret`, `keepTriggers`, `webhookCall`; watcher lifecycle (start, archive, shutdown).
- Modify `src/tools/automations.ts`, `src/agent/prompts.ts`: triggers in Desk's tools and context; the prompt's Automations step.
- Modify `src/state/attention.ts`: `automation_triggers_held`.
- Modify `src/testing/automations.ts`: `folderDef`, `webhookDef`, `forEachDefs`, a temp watched folder helper.
- Modify `catalog/skills/automation-scripts/SKILL.md` (a for-each note and `DESK_INPUTS` for file triggers), then `pnpm builtins:pin automation-scripts`.

### `apps/daemon`

- Create `src/routes/hooks.ts` + test: the webhook route.
- Modify `src/app.ts`: mount `/hooks` before the `/v1/*` bearer middleware.
- Modify `src/routes/automations.ts` + test: `POST …/triggers/:tid/secret`, `POST …/triggers/keep`.
- Modify `src/daemon.ts`: pass deskd's port to the runtime for webhook URLs; the watcher stops on shutdown.
- Modify `src/notifier.ts`: notify for `automation_triggers_held`.

### `packages/client`, `apps/cli`

- Modify `packages/client/src/client.ts`, `types.ts`, `state/automations.ts`, `state/attention.ts`.
- Modify `apps/cli/src/commands.ts`, `format.ts`: triggers in `show`, `hook-secret`, `keep-triggers`, for-each progress in `run --follow`.

### `packages/bff`

- Modify `src/contract/ipc.ts`, `src/contract/types.ts`, `src/server/handlers.ts` + test: `automations.createHookSecret`, `automations.keepTriggers`.

### `packages/ui-core`

- Create `src/automation-triggers.ts` + test: `newFolderTrigger`, `newWebhookTrigger`, `triggerText`, `whenText` over all kinds, `watchStatusText`, `curlExample`, `triggerDiff`.
- Modify `src/automation-schedules.ts`: `whenText` moves to `automation-triggers.ts` (re-exported here for existing imports).
- Modify `src/automation-format.ts`, `automation-graph.ts`, `automation-draft.ts`, `automation-fields.ts`, `automation-templates.ts`, `automation-run-graph.ts`, `automation-diff.ts` and their tests: the `for_each` kind, new run triggers, item suggestions, progress badges, trigger diffs.
- Modify `src/testing/…` fixtures: a folder trigger, a webhook trigger, a for-each step and its run.

### `packages/ui-styles`

- Modify `src/automations.css`: the `for_each` node colour (`--run` outline, as a group), the trigger rows in the Start inspector, the secret box, the items table.

### `apps/desktop/src/renderer/automations`

- Modify `design/StartInspector.tsx`, `design/StepKindFields.tsx`, `design/nodes.tsx`, `design/DesignView.tsx`, `dialogs/TurnOnDialog.tsx`, `grants/GrantsView.tsx`, `runs/StepPanel.tsx`, `runs/RunsList.tsx`, and their tests.
- Create `design/TriggerFields.tsx` (folder and webhook rows), `design/HookSecret.tsx`, `runs/ForEachItems.tsx`.
- Modify `attention/AutomationCards.tsx` + test: the held-triggers card.

### `apps/web-ui/src/app/automations`

- The Angular ports of every file above, same names in kebab case: `design/trigger-fields.ts`, `design/hook-secret.ts`, `runs/for-each-items.ts`, and edits to `start-inspector.ts`, `step-kind-fields.ts`, `nodes`, `design-view.ts`, `dialogs/turn-on-dialog.ts`, `grants/grants-view.ts`, `runs/step-panel.ts`, `runs/runs-list.ts`, `attention/automation-cards.ts`, with specs.
- Modify `e2e/automations.e2e.test.ts`.

### Docs

- Modify `docs/api.md` (the hooks route, the two user routes, events, fields), `docs/desktop.md` and `docs/web.md` (Start inspector, For each, held triggers), `CLAUDE.md` (layout lines and invariants), the automations spec (status line, §12, a new §14), and `docs/superpowers/specs/2026-09-23-desk-daemon-design.md` §14 (deviations: an unauthenticated-by-bearer route on deskd).

## Sections

- **A · Foundations** (Tasks 1–3): protocol, tables and projections, templates and validation.
- **B · Triggers** (Tasks 4–9): trigger helpers, the queue, the folder scan and watcher, webhooks, held triggers.
- **C · For each** (Tasks 10–12): items, the executor, recovery and reports.
- **D · Desk and surfaces** (Tasks 13–15): Desk's tools and prompt, attention and notifier, client and CLI.
- **E · The two UIs** (Tasks 16–20): ui-core, the Start inspector and secrets, For each in the editor, runs, Turn on, grants and attention.
- **F · End to end and docs** (Tasks 21–23).

---

## Section A · Foundations

### Task 0: The branch

- [ ] **Step 1:** `git switch -c automation-triggers master`, then `pnpm typecheck && pnpm test` to confirm a green start. Record the counts of test files and tests; the exit check compares against them.

### Task 1: Protocol: triggers, For each, events, views

**Files:**
- Modify: `packages/protocol/src/automations.ts`, `packages/protocol/src/events.ts`, `packages/protocol/src/api.ts`
- Test: `packages/protocol/src/automations.test.ts` (create if absent; otherwise add cases)

- [ ] **Step 1: Write the failing tests**

```ts
import { describe, expect, it } from 'vitest';
import { AutomationDefinition, AutomationTrigger, ForEachStep } from './automations';

const base = { title: 'T', steps: [{ id: 'a', title: 'A', kind: 'wait', minutes: 1 }] };

describe('triggers', () => {
  it('keeps schedules valid without an id', () => {
    const d = AutomationDefinition.parse({ ...base, triggers: [{ kind: 'schedule', cron: '0 8 * * *', timezone: 'Europe/Paris' }] });
    expect(d.triggers[0]).toMatchObject({ kind: 'schedule', catch_up: 'once' });
  });
  it('fills folder defaults', () => {
    const t = AutomationTrigger.parse({ kind: 'folder', id: 'inbox', path: '~/Downloads/invoices', input: 'file' });
    expect(t).toEqual({ kind: 'folder', id: 'inbox', path: '~/Downloads/invoices', pattern: ['*'], recursive: false, on: ['added'], settle_seconds: 5, input: 'file' });
  });
  it('refuses a relative folder path', () => {
    expect(() => AutomationTrigger.parse({ kind: 'folder', id: 'x', path: 'Downloads', input: 'file' })).toThrow(/absolute/);
  });
  it('parses a webhook with an id only', () => {
    expect(AutomationTrigger.parse({ kind: 'webhook', id: 'new-invoice' })).toEqual({ kind: 'webhook', id: 'new-invoice' });
  });
});

describe('for_each', () => {
  it('fills defaults', () => {
    const s = ForEachStep.parse({ id: 'each', title: 'Each', kind: 'for_each', over: { list: '{{steps.a.outputs.urls}}' }, automation: 'one' });
    expect(s).toMatchObject({ parallel: 1, max_items: 50, item_errors: 'stop', collect: [], inputs: {} });
  });
  it('takes files in a folder', () => {
    const s = ForEachStep.parse({ id: 'each', title: 'Each', kind: 'for_each', over: { files: ['*.pdf'], in: '{{inputs.folder}}' }, automation: 'one' });
    expect(s.over).toEqual({ files: ['*.pdf'], in: '{{inputs.folder}}' });
  });
  it('caps parallel at 4 and max_items at 200', () => {
    expect(() => ForEachStep.parse({ id: 'e', title: 'E', kind: 'for_each', over: { list: '{{inputs.x}}' }, automation: 'one', parallel: 5 })).toThrow();
    expect(() => ForEachStep.parse({ id: 'e', title: 'E', kind: 'for_each', over: { list: '{{inputs.x}}' }, automation: 'one', max_items: 201 })).toThrow();
  });
});
```

- [ ] **Step 2:** Run `pnpm vitest run packages/protocol/src/automations.test.ts --maxWorkers=2`. Expected: FAIL (`ForEachStep` is not exported; folder triggers do not parse).

- [ ] **Step 3: Implement the schemas**

In `automations.ts`, after `ScheduleTrigger`:

```ts
export const MAX_QUEUED_TRIGGERS = 50;
export const WATCH_SCAN_MAX = 2000;
export const WATCH_MAX_DEPTH = 4;
export const WEBHOOK_BODY_MAX = 64 * 1024;
export const WEBHOOK_RATE_PER_MIN = 30;
export const FOREACH_MAX_ITEMS = 200;

/** A folder or webhook trigger's id: stable across edits (a webhook's URL, a folder's files seen). */
export const TriggerId = z.string().regex(/^[a-z][a-z0-9_-]{0,39}$/, 'Use lowercase letters, digits, _ and -, starting with a letter (at most 40 characters)');

export const FolderTrigger = z.object({
  kind: z.literal('folder'),
  id: TriggerId,
  /** An absolute folder; `~/` is the home folder. */
  path: z.string().min(1).max(1024).refine((p) => p.startsWith('/') || p.startsWith('~/'), 'Use an absolute path, or one starting with ~/'),
  /** Globs relative to the folder. */
  pattern: z.array(z.string().min(1).max(200)).min(1).max(10).default(['*']),
  recursive: z.boolean().default(false),
  on: z.array(z.enum(['added', 'changed'])).min(1).default(['added']),
  /** Seconds a file's size and mtime must stay the same before it triggers. */
  settle_seconds: z.number().int().min(1).max(300).default(5),
  /** The `file` input that receives the file. */
  input: InputKey,
  inputs: z.record(InputKey, InputValue).optional(),
});
export type FolderTrigger = z.infer<typeof FolderTrigger>;

export const WebhookTrigger = z.object({
  kind: z.literal('webhook'),
  id: TriggerId,
  /** Values for inputs a call leaves out. */
  inputs: z.record(InputKey, InputValue).optional(),
});
export type WebhookTrigger = z.infer<typeof WebhookTrigger>;

/** How an automation starts besides Run now (always available). */
export const AutomationTrigger = z.discriminatedUnion('kind', [ScheduleTrigger, FolderTrigger, WebhookTrigger]);
export type AutomationTrigger = z.infer<typeof AutomationTrigger>;
export type EventTrigger = FolderTrigger | WebhookTrigger;
```

Remove the old `export const AutomationTrigger = ScheduleTrigger;`. Add `ForEachStep` before `Step`, and add it to the union:

```ts
export const ForEachStep = z.object({
  ...stepBase,
  kind: z.literal('for_each'),
  over: z.union([
    z.object({ list: z.string().trim().min(5).max(200) }).strict(),
    z.object({ files: z.array(z.string().min(1).max(200)).min(1).max(10), in: z.string().trim().min(5).max(200) }).strict(),
  ]),
  automation: AutomationName,
  inputs: z.record(InputKey, z.string().max(10_000)).default({}),
  parallel: z.number().int().min(1).max(4).default(1),
  max_items: z.number().int().min(1).max(FOREACH_MAX_ITEMS).default(50),
  item_errors: z.enum(['stop', 'continue']).default('stop'),
  /** Child output keys gathered into lists, one entry per item. */
  collect: z.array(InputKey).max(10).default([]),
});
export type ForEachStep = z.infer<typeof ForEachStep>;
```

`RunTrigger` gains `'folder'` and `'webhook'`. Add the view types:

```ts
export type WatchStatus = { state: 'watching' | 'missing' | 'too_many_files' | 'held' | 'off' | 'refused'; detail: string | null; checked_at: string | null };
export type TriggerSummary =
  | { kind: 'schedule'; index: number; cron: string; timezone: string }
  | { kind: 'folder'; id: string; path: string; pattern: string[] }
  | { kind: 'webhook'; id: string };
export type ForEachProgress = { total: number; done: number; failed: number };
export type ForEachItemInfo = { index: number; item: string; run_id: string | null; status: RunStatus | 'not_started'; dir: string | null };
```

- `AutomationSummary` gains `triggers: TriggerSummary[]`, `triggers_held: boolean` and `queued: number` (`schedules` stays, for the clients that read it).
- `AutomationDetail` gains `watch: Record<string, WatchStatus>` (by trigger id), `webhooks: Array<{ trigger_id: string; url: string; secret_set: boolean; default_port: boolean }>` and `triggers_kept_version: number | null`.
- `RunInfo` gains `trigger_id: string | null`, `trigger_file: string | null` (the path as it was in the watched folder), `item_index: number | null`.
- `StepRunInfo` gains `items: string[] | null` and `progress: ForEachProgress | null`.
- `RunListEntry`'s skipped entry gains `trigger_id: string | null`, and `reason` widens to `'still_running' | 'missed' | 'queue_full' | 'trigger_removed'`; `trigger_index` becomes `number | null`.

In `events.ts`:

```ts
event('automation.trigger_queued', z.object({
  automation_id: z.string(), trigger_id: TriggerId, queue_id: z.string(),
  source: z.discriminatedUnion('kind', [
    z.object({ kind: z.literal('folder'), path: z.string().max(4096), size: z.number().int().min(0), mtime_ms: z.number() }),
    z.object({ kind: z.literal('webhook') }),
  ]),
  inputs: z.record(z.string(), InputValue),
})),
event('automation.hook_secret_set', z.object({ automation_id: z.string(), trigger_id: TriggerId, hash: z.string().regex(/^[0-9a-f]{64}$/) })),
event('automation.triggers_kept', z.object({ automation_id: z.string(), version: z.number().int().min(1) })),
```

- `run_started`: `by` regex becomes `^(user|schedule|folder|webhook|agent:.+)$`; add `trigger_id?: TriggerId`, `queue_id?: string`; `parent` becomes `{run_id, step_id, attempt?: int ≥ 1, item?: {index: int ≥ 1}}`.
- `step_changed`: add `items?: z.array(z.string().max(OUTPUT_STRING_MAX)).max(FOREACH_MAX_ITEMS)` (refined to 16 KB serialized) and `progress?: {total, done, failed}`.
- `trigger_skipped`: `trigger_index` optional, add `trigger_id?: TriggerId`, reasons add `queue_full` and `trigger_removed`, and a refine that exactly one of `trigger_index` and `trigger_id` is set.

In `api.ts`: `HookSecretResponse = { secret: string; url: string }`, `WebhookCallBody = z.object({ inputs: z.record(InputKey, InputValue).optional() }).strict()`, and `automation_triggers_held` in the attention kinds.

- [ ] **Step 4:** Run the test again. Expected: PASS. Then `pnpm typecheck`. Expected: errors wherever code reads `t.cron` or `t.timezone` from `triggers` (the grep below lists the readers). Make this commit compile by filtering to schedules inline where the typecheck complains (`t.kind === 'schedule'`); Task 4 replaces each filter with `schedulesOf`:

```bash
git grep -n "\.triggers\b" -- packages apps ':!**/*.test.*' ':!**/*.spec.*'
```

- [ ] **Step 5: Commit**

```bash
git add packages/protocol/src/automations.ts packages/protocol/src/events.ts packages/protocol/src/api.ts packages/protocol/src/automations.test.ts <each file Step 4 touched>
git commit -m "feat(protocol): folder and webhook triggers, for-each steps, the trigger queue events

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: Core: migration, projections, queries

**Files:**
- Modify: `packages/core/src/db/schema.ts`, `packages/core/src/events/projections.ts`, `packages/core/src/automations/queries.ts`
- Create: `packages/core/drizzle/0007_automation_triggers.sql` (generated)
- Test: `packages/core/src/automations/projections.test.ts`

- [ ] **Step 1: Write the failing tests** in `projections.test.ts`, with the existing helpers there:
  - a `trigger_queued` (folder) inserts a queue row and a seen row; a `run_started` with its `queue_id` deletes the queue row and keeps the seen row;
  - a `trigger_skipped {trigger_id, reason: 'queue_full'}` for a folder file inserts a seen row (so the file does not retry);
  - `switched {enabled: true}` sets `watch_since[id]` to the event time for every folder trigger, and `last_due[index]` for schedules only (a definition `[folder, schedule]` gets `last_due = {"1": ts}`);
  - a save while on that changes a folder trigger's `path` moves its `watch_since`; one that changes only `settle_seconds` keeps it; one that moves a schedule from index 0 to 1 resets that schedule's cursor (as today);
  - an agent save that adds a webhook trigger sets `triggers_held`; a user save does not; an agent save that only removes one does not; `triggers_kept` and `switched {enabled: true}` clear it;
  - `hook_secret_set` stores the hash in `hook_hashes[trigger_id]`; a save that removes that webhook trigger drops it; `automation.deleted` drops the queue and seen rows;
  - `step_changed` with `items` and `progress` stores both.

- [ ] **Step 2:** `pnpm vitest run packages/core/src/automations/projections.test.ts --maxWorkers=2`. Expected: FAIL.

- [ ] **Step 3: Implement.**
  - `schema.ts`: on `automations`, `watch_since` (json `Record<string,string>`, default `{}`), `hook_hashes` (json `Record<string,string>`, default `{}`), `triggers_held` (boolean, default false), `triggers_kept_version` (integer, nullable). On `automation_runs`, `trigger_id`, `queue_id`, `trigger_file` (text, nullable), `parent_attempt`, `item_index` (integer, nullable). On `automation_step_runs`, `items` (json `string[] | null`), `progress` (json, nullable).
  - New `automation_trigger_queue`: `queue_id` (pk), `automation_id`, `project_id`, `trigger_id`, `source` (json), `inputs` (json), `queued_at`; index on `(automation_id, queued_at)`.
  - New `automation_watch_seen`: `automation_id`, `trigger_id`, `path`, `size`, `mtime_ms`, `seen_at`; primary key `(automation_id, trigger_id, path)` (an update replaces size and mtime).
  - `cd packages/core && npx drizzle-kit generate --name automation_triggers`, and check the SQL is additive only (`ALTER TABLE … ADD`, `CREATE TABLE`, `CREATE INDEX`).
  - `projections.ts`: `carryLastDue` compares by index **among schedules only** (`kind === 'schedule'` at the same index in both), and writes cursors only for schedule indexes. New `carryWatchSince(prev, next, since, ts)`: per folder trigger id, keep the cursor when a trigger with that id existed with the same `path`, `pattern` and `recursive`, else `ts`. `triggers_held` uses `eventTriggerChanges(prev, next)` from Task 4's `triggers.ts` (write it here and test it there).
  - `queries.ts`: `queuedOf(db, automationId)` (oldest first), `queueLength`, `seenFiles(db, automationId, triggerId): Map<path, {size, mtime_ms}>`, `hookHash(db, automationId, triggerId)`, `itemRuns(db, runId, stepId, attempt)` (children with that `parent_attempt`, by `item_index`).

- [ ] **Step 4:** Run the test. Expected: PASS. Then `pnpm typecheck`.

- [ ] **Step 5: Commit** (`feat(core): tables and projections for the trigger queue, watched files, webhook secrets and for-each progress`).

### Task 3: Templates and validation

**Files:**
- Modify: `packages/core/src/automations/template.ts`, `packages/core/src/automations/validate.ts`
- Test: `template.test.ts`, `validate.test.ts`

- [ ] **Step 1: Write the failing tests.**

`template.test.ts`:
- `parseTemplate('{{item}}', { item: true })` is one path; without the option it throws a `TemplateError` whose message says `{{item}} is only for a For each step's inputs`.
- `renderText('{{item_index}}. {{item}}', scope)` with `scope.item = { value: 'a.pdf', index: 3 }` gives `3. a.pdf`.

`validate.test.ts`, one case per rule:
- a folder trigger whose `input` is missing, or names a non-`file` input: error at `triggers[i].input`;
- a folder trigger path `/`, `~`, `/Users` (a folder containing the home folder), or inside the data dir: error at `triggers[i].path` with `unsafeFolder`'s wording;
- two event triggers with the same id: error at `triggers[j].id`;
- a folder trigger that leaves a required input without a default or a value: error (as for schedules), but not for its own `input`;
- a webhook trigger's `inputs` naming an unknown key, or giving a `file` input: error;
- `pattern` with `..` or a leading `/`: error; `**` without `recursive`: warning ("`**` only matches this folder unless recursive is on");
- a `for_each` naming itself, an unknown automation, or forming a cycle through another automation's `for_each` or sub-automation step: error at `steps[i].automation`;
- `over.list` that is not exactly one path, or names a non-ancestor step, or an agent step's undeclared key, or a `number` input: error at `steps[i].over.list`;
- `over.in` naming a `text` input (must be a step dir or a `folder` input): error;
- `inputs` with an unknown child key, or missing a required child input: error; `{{item}}` in another step's brief: error;
- `collect` key not declared by the child's agent `output_step`: error; a child whose `output_step` is a script: no error;
- a valid definition with all three passes with no errors.

- [ ] **Step 2:** Run both files. Expected: FAIL.

- [ ] **Step 3: Implement.**
  - `template.ts`: `ITEM_PATH = /^item(_index)?$/`. `parseTemplate(src, { item?: boolean })` accepts it only with `item`. `TemplateScope` gains `item?: { value: string; index: number }`. Rendering `item` gives the value, `item_index` the number.
  - `validate.ts`: a `// ── triggers ──` block replacing `// ── schedules ──` that switches on `t.kind`; the schedule branch is today's code. Folder checks call `unsafeFolder(realOrSelf(expandHome(path)), guard)` when the path exists and the same check on the path as written otherwise (so a missing folder still can't be `/`). `ValidateContext` gains `guard` and `home`. The `for_each` branch reuses the sub-automation input checks (extract them into `checkChildInputs(at, childName, inputs)`), and `subAutomationCycle` follows `s.kind === 'automation' || s.kind === 'for_each'`.

- [ ] **Step 4:** Run the tests. Expected: PASS. `pnpm typecheck`.

- [ ] **Step 5: Commit** (`feat(core): validate folder and webhook triggers and for-each steps; {{item}} templates`).

---

## Section B · Triggers

### Task 4: Trigger helpers, and every schedules-only reader

**Files:**
- Create: `packages/core/src/automations/triggers.ts`, `triggers.test.ts`
- Modify: `packages/core/src/tools/sandbox.ts`, `packages/core/src/runtime/runtime.ts`, `packages/core/src/automations/scope.ts`, `views.ts`, `service.ts`, `engine.ts` (`fireSchedules`), `packages/core/src/agent/prompts.ts`, `packages/core/src/tools/automations.ts`, `packages/core/src/state/attention.ts`, `packages/client/src/state/automations.ts`, `apps/cli/src/commands.ts`

- [ ] **Step 1: Write the failing tests** (`triggers.test.ts`):
  - `schedulesOf(def)` returns `[{ index, trigger }]` for schedules only, with their index in `def.triggers`;
  - `eventTriggersOf(def)` returns folder and webhook triggers;
  - `eventTriggerChanges(prev, next)` returns `[]` for identical lists, for a reorder, and for a removal; returns the id for an added trigger and for a changed `path`, `pattern`, `recursive`, `on`, `input` or `inputs`;
  - `expandHome('~/x', '/Users/me')` is `/Users/me/x`; other paths are unchanged;
  - `unsafeFolder` (moved to `sandbox.ts`) keeps `Runtime.unsafeSource`'s three answers, and adds "a secret Desk keeps" for a path covered by `guard.secrets` or `guard.secretPatterns`.

- [ ] **Step 2:** Run it. Expected: FAIL.

- [ ] **Step 3: Implement**, then move every reader of `triggers` that assumes schedules to `schedulesOf` (the Task 1 grep lists them): `scope.ts` `timezoneOf` (first *schedule's* timezone), `views.ts` (`schedules`, `next_due`, plus the new `triggers` summary), `service.ts`, `engine.ts` `fireSchedules`, Desk's prompt and `automation_list`, attention's enable request text, the client reducer's `schedules`, the CLI's `show`. Each text that listed schedules now lists every trigger through one formatter, `triggerLine(t)`: `0 8 * * 1-5 (Europe/Paris)`, `files in ~/Downloads/invoices matching *.pdf`, `webhook new-invoice`. `Runtime.unsafeSource` calls `unsafeFolder`.

- [ ] **Step 4:** Run `triggers.test.ts` and the tests of each file touched (`views`, `service`, `schedules`, `engine`, the prompt's, `attention`, the client reducer's, the CLI's). Expected: PASS, and no existing expectation changes except where a line now also mentions non-schedule triggers.

- [ ] **Step 5: Commit** (`refactor(core): trigger helpers; schedules-only readers skip folder and webhook triggers`).

### Task 5: The queue

**Files:**
- Create: `packages/core/src/automations/queue.ts`, `queue.test.ts`
- Modify: `packages/core/src/automations/engine.ts`, `packages/core/src/testing/automations.ts`

- [ ] **Step 1: Write the failing tests** with `automationHarness` (a `webhookDef` fixture: one webhook trigger `hook`, one `wait 1` step, and an optional `text` input `note`):
  - `engine.enqueue(a.id, 'hook', { kind: 'webhook' }, { note: 'x' })` while nothing runs starts a run at once: `run_started` has `trigger: 'webhook'`, `by: 'webhook'`, `trigger_id: 'hook'`, `queue_id`, inputs `{note: 'x'}`; the queue is empty; it returns `{status: 'started', run_id}`;
  - a second enqueue while that run waits returns `{status: 'queued', position: 1}`; finishing the first run (advance the fake clock a minute and tick) starts the second;
  - the 51st queued item appends `trigger_skipped {trigger_id: 'hook', reason: 'queue_full'}` and returns `{status: 'skipped'}`;
  - a test run going does not hold the queue;
  - an automation turned off keeps its queue and does not drain; turning it on drains;
  - a queued item whose trigger id was removed by a later save is dropped with `trigger_removed`;
  - a folder item whose file input no longer exists starts a run that fails at once with "Could not start: …" (not a thrown error).

- [ ] **Step 2:** Run it. Expected: FAIL.

- [ ] **Step 3: Implement.**

```ts
// queue.ts
export type EnqueueResult = { status: 'started'; run_id: string } | { status: 'queued'; position: number } | { status: 'skipped'; reason: 'queue_full' };
```

- `AutomationEngine.enqueue(automationId, triggerId, source, inputs)`: appends `trigger_queued` (or `trigger_skipped (queue_full)` at `MAX_QUEUED_TRIGGERS`), then `drainQueue` and reports what happened to *this* item.
- `drainQueue(automationId)`: skips while `runningRunOf` (non-test), the automation is off, deleted or in an archived project, or `triggers_held`; drops items of removed triggers; otherwise starts the oldest with `startRun(id, { trigger: kind, test: false, inputs, by: kind, triggerId, queueId, triggerFile })`. Guard it with a per-automation flag so two drains never start two runs.
- Call `drainQueue` from `onRunFinished` (top-level runs), from `onTick` for every automation with a queue, and after `automation.switched {enabled: true}` and `triggers_kept`.
- `startRun`'s tolerated-failure branch (`opts.trigger !== 'schedule' && opts.trigger !== 'parent'`) also tolerates `folder` and `webhook`.

- [ ] **Step 4:** Run it. Expected: PASS. Run `engine.test.ts` and `schedules.test.ts` too.

- [ ] **Step 5: Commit** (`feat(core): the trigger queue: one run at a time for folder and webhook triggers`).

### Task 6: The folder scan (pure)

**Files:**
- Create: `packages/core/src/automations/watch-scan.ts`, `watch-scan.test.ts`

The decision is pure so it can be tested without timers or a real folder:

```ts
export type Listed = { path: string; size: number; mtime_ms: number; arrival_ms: number };
export type Settling = Map<string, { size: number; mtime_ms: number; since_ms: number }>;
export type Decision = { ready: Listed[]; settling: Settling };

/**
 * Which listed files trigger now. A file is a candidate when it arrived after `sinceMs` and is new (or, with `changed`,
 * its size or mtime differ from what was seen). A candidate is ready once it has looked the same for `settleMs`;
 * until then it waits in `settling`. Ready files come oldest arrival first.
 */
export function decide(o: { listed: Listed[]; seen: Map<string, { size: number; mtime_ms: number }>; settling: Settling; sinceMs: number; nowMs: number; settleMs: number; changed: boolean }): Decision;

/** Lists regular files under `dir` matching `pattern` (no symlinks followed, ignored names skipped, depth ≤ 4, at most WATCH_SCAN_MAX entries). */
export function listFolder(dir: string, trigger: Pick<FolderTrigger, 'pattern' | 'recursive'>): { files: Listed[]; truncated: boolean };
```

- [ ] **Step 1: Write the failing tests:**
  - a file with arrival ≤ `sinceMs` is never a candidate;
  - a new file first goes to `settling`, and is ready on a later call once `nowMs - since_ms ≥ settleMs` with the same size and mtime; a size change restarts its settle time;
  - a seen path is not a candidate without `changed`; with `changed`, a new mtime makes it one;
  - a path that left the listing leaves `settling`;
  - ready files are sorted by arrival;
  - `listFolder` on a temp dir: matches `*.pdf`, skips `.DS_Store`, `~$doc.docx`, `a.crdownload`, a symlink to a file, and a sub-folder's files without `recursive`; with `recursive` and `**/*.pdf` finds them to depth 4 but not 5; reports `truncated` past the cap (use a small cap through an optional parameter in the test).

- [ ] **Step 2–4:** Run (FAIL), implement with `fast-glob` (`onlyFiles`, `followSymbolicLinks: false`, `dot: false`, `deep`, `stats: true`, `ignore` for the patterns in D1) and an `lstat` check per entry, run (PASS).

- [ ] **Step 5: Commit** (`feat(core): the folder scan: settled new files after a trigger's cursor`).

### Task 7: The folder watcher

**Files:**
- Create: `packages/core/src/automations/watch.ts`, `watch.test.ts`
- Modify: `packages/core/src/automations/engine.ts`, `packages/core/src/runtime/runtime.ts`, `apps/daemon/src/daemon.ts`

- [ ] **Step 1: Write the failing tests** (`automationHarness`, a temp folder under the test's tmp dir, `FakeClock`; the watcher's `scanNow()` is awaited directly so no test depends on FSEvents timing):
  - turning on a `folderDef` automation with a file already in the folder: two scans 10 s apart start nothing;
  - a file written after turning on: the first scan settles it, the scan 5 s later queues it and a run starts with the file copied into `inputs/` and `trigger_file` set to its original path;
  - three files at once: one run, two queued; they run one after another;
  - rewriting a seen file with `on: ['added']` starts nothing; with `['added', 'changed']` it starts a run;
  - a folder that does not exist: status `missing`, no error thrown; creating it and adding a file later works;
  - a folder path that resolves (through a symlink) to the home folder: status `refused`, nothing listed;
  - `triggers_held`: status `held`, nothing queued; keeping the triggers resumes, and the file (which arrived while held) is picked up;
  - turning the automation off: status `off`; files that arrive while off and are older than the next turn-on never trigger;
  - after a simulated restart (a new runtime on the same data dir), a file added while "down" is picked up by the first scan;
  - one real `fs.watch` smoke: write a file and wait (with `vi.waitFor`, 5 s max) for the run to start with a 1 s settle. Skip it where `fs.watch` with `recursive` is unsupported (`process.platform` not `darwin`, `linux` on Node ≥ 20 or `win32`).

- [ ] **Step 2:** Run it. Expected: FAIL.

- [ ] **Step 3: Implement** `FolderWatcher`:
  - `sync()`: compares the enabled, not-held folder triggers of live projects with the open watchers; opens `fs.watch(real, { recursive, persistent: false })` for new ones (errors → status `missing` or `refused`), closes stale ones. Called on each tick and after saves, switches, `triggers_kept`, archive and delete (the engine's hooks).
  - A watch event debounces a `scan(automationId, triggerId)` by 1 s; each tick scans every open trigger too.
  - `scan` re-checks `unsafeFolder` on the real path, lists (Task 6), calls `decide` with `watch_since`, the seen map and its in-memory `settling`, and calls `engine.enqueue(…, { kind: 'folder', path, size, mtime_ms }, { ...trigger.inputs, [trigger.input]: path })` for each ready file, oldest first. At most one scan per trigger at a time.
  - `status(automationId)` returns the `WatchStatus` map; `close()` closes every watcher (shutdown, and in tests).
  - The Runtime creates it next to the engine, passes it to views for `watch`, and closes it in `shutdown`. The daemon needs no change beyond the existing tick, which calls `engine.tick()`, whose tick hooks now include `watcher.sync()` and `watcher.scanAll()`.

- [ ] **Step 4:** Run it (PASS). Then `engine.test.ts`, `lifecycle.test.ts`.

- [ ] **Step 5: Commit** (`feat(core): folder triggers: watch, settle and queue new files`).

### Task 8: Webhooks

**Files:**
- Create: `apps/daemon/src/routes/hooks.ts`, `apps/daemon/src/routes/hooks.test.ts`
- Modify: `apps/daemon/src/app.ts`, `apps/daemon/src/routes/automations.ts` + test, `apps/daemon/src/daemon.ts`, `packages/core/src/runtime/runtime.ts`, `packages/core/src/automations/service.ts`, `views.ts`

- [ ] **Step 1: Write the failing tests** (`hooks.test.ts`, on `createApp` with the core harness, as the other route tests do):
  - `POST /v1/automations/:aid/triggers/hook/secret` with the daemon token returns `{secret, url}`; `url` is `http://127.0.0.1:<port>/hooks/<aid>/hook`; the event log holds the hash and never the secret (`JSON.stringify(events)` does not contain it); a second call rotates (the old secret now gets 401);
  - the same call for a trigger id that is not a webhook: 404; without the token: 401;
  - `POST /hooks/:aid/hook` with the secret and `{"inputs": {"note": "hi"}}` → 202 `{status: 'started', run_id}`, and the run has `inputs.note = 'hi'`;
  - wrong or missing secret → 401; no secret created yet → 401; an `Origin` header → 403; `Host: evil.example` → 421; a 70 KB body → 413; `{"inputs": {"doc": "/etc/hosts"}}` for a `file` input → 400; an unknown key → 400; non-JSON → 400;
  - the automation off → 409 `off`; triggers held → 409 `held`; 51 calls while a run waits → the last is 409 `queue_full`; 31 calls in a minute → 429;
  - a deleted automation or a removed trigger → 404;
  - the route does not accept the daemon token in place of the secret (401).

- [ ] **Step 2:** Run it. Expected: FAIL.

- [ ] **Step 3: Implement.**
  - `hooks.ts`: `hookRoutes(deps)` mounted in `createApp` with `app.route('/', hookRoutes(deps))` **before** `app.use('/v1/*', bearerAuth(...))`. Order: Host check (421), `Origin` present (403), body length from `Content-Length` and the read body (413), rate limit per `automationId/triggerId` in a sliding one-minute window in memory (429), then `runtime.webhookCall(aid, tid, secret, body)`.
  - `Runtime.webhookCall`: loads the automation (404 if unknown or deleted, or no webhook with that id in its definition), compares `sha256(secret)` with `hook_hashes[tid]` using `timingSafeEqual` (401), checks on (409 `off`) and held (409 `held`), parses `WebhookCallBody`, refuses `file` and `folder` keys (400), merges `trigger.inputs` under the body's inputs, runs `resolveInputs` for early 400s, then `engine.enqueue`. A `queue_full` result maps to 409.
  - `Runtime.createHookSecret(aid, tid)` (user only: only the route calls it, with the daemon token): `randomBytes(32).toString('base64url')`, appends `hook_secret_set` with the hex SHA-256, returns `{ secret, url }` from the port the daemon passes in (`runtime.setHookBase('http://127.0.0.1:' + port)` once it listens).
  - The detail view's `webhooks` list: one row per webhook trigger with `url`, `secret_set` and `default_port` (`port === DEFAULT_PORT`).
  - Nothing logs a request's `Authorization` header or body: check `apps/daemon/src/logger.ts` call sites in `hooks.ts` log only the status, the automation id and the trigger id.

- [ ] **Step 4:** Run `hooks.test.ts` and `routes/automations.test.ts`. Expected: PASS.

- [ ] **Step 5: Commit** (`feat(daemon): webhook triggers on 127.0.0.1 with per-trigger secrets`).

### Task 9: Held triggers

**Files:**
- Modify: `packages/core/src/automations/service.ts`, `views.ts`, `packages/core/src/runtime/runtime.ts`, `apps/daemon/src/routes/automations.ts` + test
- Test: `packages/core/src/automations/service.test.ts`

- [ ] **Step 1: Write the failing tests:**
  - Desk's `automation_save` adding a folder trigger to an automation that is on: `triggers_held` is true, the watcher's status is `held`, and a webhook call is 409 `held`;
  - `POST /v1/automations/:aid/triggers/keep` appends `triggers_kept {version}` and clears it; the detail's `triggers_kept_version` is that version;
  - the user saving the same change never holds;
  - Desk's tools have no way to keep triggers (the toolset test lists Desk's automation tools; the list is unchanged).

- [ ] **Step 2–4:** Run (FAIL), implement `Automations.keepTriggers(aid)` and the route, run (PASS).

- [ ] **Step 5: Commit** (`feat(core): hold folder and webhook triggers an agent changed until the user keeps them`).

---

## Section C · For each

### Task 10: Items and outputs (pure)

**Files:**
- Create: `packages/core/src/automations/foreach.ts`, `foreach.test.ts`
- Modify: `packages/core/src/automations/scope.ts`

```ts
/** The items of a for-each step: a list output as is, a text value one per non-empty line, or the files matching `over.files` in `over.in`. */
export function resolveItems(step: ForEachStep, scope: TemplateScope, allowedRoots: string[]): { items: string[] } | { error: string };
/** The step's outputs from its items' child runs, in item order. */
export function forEachOutputs(step: ForEachStep, items: Array<{ run: AutomationRunRow | null; outputs: Outputs | null }>): { outputs: Outputs } | { error: string };
/** The `items.json` rows. */
export function itemsManifest(...): ForEachItemInfo[];
```

- [ ] **Step 1: Write the failing tests:**
  - a list output `['a', 'b']` gives two items; a text input `"a\n\n b \n"` gives `['a', 'b']`; a number gives an error "`{{inputs.n}}` is not a list or text";
  - `files: ['*.pdf']` in a step dir gives sorted absolute paths; an `in` that resolves outside `allowedRoots` gives an error; a symlink in the folder is not an item;
  - more than `max_items` items: error "12 items; this step takes at most 10";
  - items over 16 KB serialized: error;
  - `forEachOutputs`: counts, `run_ids`, `collect` lists with `''` for failed items and newline-joined lists; outputs over the limits: error naming the key.

- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS). `scope.ts`: `stepResultDir` returns the step's own folder for a `for_each` row; `templateScope` takes an optional `item`.

- [ ] **Step 5: Commit** (`feat(core): for-each items and outputs`).

### Task 11: The For each executor

**Files:**
- Modify: `packages/core/src/automations/engine.ts`, `packages/core/src/testing/automations.ts`
- Test: create `packages/core/src/automations/foreach-run.test.ts`

- [ ] **Step 1: Write the failing tests** with `forEachDefs()`: a child `one` (input `url`, a `script` step writing `{"title": "<url>"}` to `DESK_OUTPUT`, `output_step`) and a parent with a script step `list` outputting `urls: ['a', 'b', 'c']`, then `each` (`over: {list: '{{steps.list.outputs.urls}}'}`, `inputs: {url: '{{item}}'}`, `collect: ['title']`), then a `wait` step:
  - the run succeeds; `each` has outputs `{count: 3, succeeded: 3, failed: 0, run_ids: [...], title: ['a', 'b', 'c']}`; three child runs with `item_index` 1–3 and `parent_attempt` 1; `items.json` in `steps/each/`;
  - with `parallel: 1`, children never overlap (compare `started_at` and `finished_at`); with `parallel: 2`, at most two run at once;
  - `step_changed` progress goes `0/3` → `3/3`;
  - item 2 failing with `item_errors: 'stop'`: item 3 never starts, a running item is cancelled, the step fails with "Item 2 (b) failed: …"; with `continue`: the step succeeds with `failed: 1` and `title: ['a', '', 'c']`, and an edge `when: steps.each.outputs.failed > 0` fires;
  - `on_error: {retry: 1}` after a `stop` failure: attempt 2 starts fresh children with `parent_attempt: 2`, and the attempt-1 children do not count;
  - cancelling the parent run cancels its running children and leaves the others unstarted;
  - zero items: the step succeeds at once with `count: 0`;
  - depth: a for-each at depth 3 fails with the sub-automation depth message;
  - test runs: children are test runs too.

- [ ] **Step 2:** Run it. Expected: FAIL.

- [ ] **Step 3: Implement** `forEach(ctx)`:
  - resolve items with `templateScope` and `allowedRoots = [runDir(run), ...descendantRunDirs(run)]`; on error, fail (not retryable when the error is `max_items`);
  - `stepChanged(status: 'running', items, progress: {total, done: 0, failed: 0})`, write an initial `items.json` (every item `not_started`);
  - `startItems(run, step, attempt)`: while running children < `parallel` and unstarted items remain, render `step.inputs` with `{ ...scope, item: { value, index } }` (a value rendering to `''` leaves the child's default, as in `subAutomation`), and `startRun(child, { trigger: 'parent', test: run.test, inputs, by: run.by, parent: { runId, stepId, attempt, item: index } })`;
  - `childFinished`: when the parent step is a `for_each` (the child's `parent.item` is set), update progress and `items.json`, then either stop (`item_errors: 'stop'` and failed: mark the parent ended for the other children, cancel them, resolve failed), start more, or, when all have ended, resolve succeeded with `forEachOutputs` (or failed with its error);
  - `onCancelStep`: cancel every running child of that step (`parentEnded` as for sub-automations).

- [ ] **Step 4:** Run it, then `sub.test.ts` and `engine.test.ts`. Expected: PASS.

- [ ] **Step 5: Commit** (`feat(core): for-each steps run another automation once per item`).

### Task 12: For each in recovery, the run report and retention

**Files:**
- Modify: `packages/core/src/automations/engine.ts` (`recover`), `report.ts`, `folders.ts`
- Test: `lifecycle.test.ts`, `report.test.ts`, `folders.test.ts`

- [ ] **Step 1: Write the failing tests:**
  - a restart while a for-each step runs (two items done, one running, two unstarted): the running child re-attaches (its own recovery), and when it finishes, the unstarted ones start; nothing starts twice;
  - a restart after the last child finished but before the parent step resolved: recovery resolves it;
  - the run report lists a for-each step with `3/3 items`, its outputs, and one line per failed item (`#2 b: failed: …`) within the 12 000 cap;
  - retention never prunes a child run of a run that is still going, and prunes item runs with their parent (they are `parent` runs of the same project, already covered by `protectedRuns`; the test pins it).

- [ ] **Step 2–4:** Run (FAIL), implement: `recover` gets a `case 'for_each'` that calls `startItems` or resolves from `itemRuns`; the report adds `progress` and failed items; run (PASS).

- [ ] **Step 5: Commit** (`feat(core): for-each steps survive restarts and show in run reports`).

---

## Section D · Desk and surfaces

### Task 13: Desk's tools, prompt and the built-in skill

**Files:**
- Modify: `packages/core/src/tools/automations.ts`, `packages/core/src/agent/prompts.ts`, `catalog/skills/automation-scripts/SKILL.md`, `packages/core/src/skills/builtins.json`
- Test: `packages/core/src/tools/automations.test.ts`, the prompt's test

- [ ] **Step 1: Write the failing tests:**
  - `automation_list` shows each trigger line (Task 4's `triggerLine`), `queued: N` when non-zero, and the flag `triggers held (the user must keep them)`;
  - `automation_read` shows the watch status of each folder trigger and, for webhooks, whether a secret exists, never a secret;
  - `automation_save` of a definition with a webhook trigger returns, after the version, the line "The user must create this webhook's secret in the app (Design → Start) before anything can call it.";
  - Desk's prompt's Automations step 1 mentions files arriving in a folder and calls from other apps, and step 2 says: a loop over items is a For each step that runs another automation, so build the per-item automation first, test it on one item, then the parent.

- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS). Edit `SKILL.md`: a "Started by a file" paragraph (`DESK_INPUTS` holds the copy's path under the input key; the original path is not given to the script) and a "For each" paragraph (the child automation's script sees one item). Then `pnpm builtins:pin automation-scripts` and run `packages/core/src/skills/builtins.test.ts`.

- [ ] **Step 5: Commit** (`feat(core): Desk knows folder and webhook triggers and for-each steps`).

### Task 14: Attention and notifier

**Files:**
- Modify: `packages/core/src/state/attention.ts` + test, `apps/daemon/src/notifier.ts` + test

- [ ] **Step 1–4:** Tests first: an agent save that holds triggers adds `automation_triggers_held` ("Desk changed what starts *<title>*: <trigger lines>"), removed by `triggers_kept`, by turning on, or by dismissing; the notifier notifies it once. Implement; PASS.

- [ ] **Step 5: Commit** (`feat(core): attention for held triggers`).

### Task 15: Client and CLI

**Files:**
- Modify: `packages/client/src/client.ts`, `types.ts`, `state/automations.ts`, `state/attention.ts` and their tests; `apps/cli/src/commands.ts`, `format.ts` and their tests

- [ ] **Step 1: Write the failing tests:**
  - `DeskClient.automations.createHookSecret(aid, tid)` and `keepTriggers(aid)` hit the two routes;
  - the reducer: `trigger_queued` bumps `queued`, `run_started` with a `queue_id` lowers it; `step_changed` with `progress` updates the live step; `triggers_kept` clears `triggers_held`;
  - `desk automation show` lists triggers (schedules, folders with their watch status, webhooks with their URL and whether a secret exists);
  - `desk automation hook-secret <project> <name> <trigger>` prints the secret and a `curl` example once, and a warning that it will not be shown again;
  - `desk automation keep-triggers <project> <name>`;
  - `run --follow` prints `each: 2/5 items` lines as progress changes.

- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS).

- [ ] **Step 5: Commit** (`feat(client,cli): webhook secrets, held triggers, for-each progress`).

---

## Section E · The two UIs

Every task in this section changes the React renderer and the Angular web UI together, with shared logic in `@desk/ui-core` and CSS in `@desk/ui-styles`. A ported React test keeps its cases, text and roles in the Angular spec.

### Task 16: ui-core: triggers and For each

**Files:**
- Create: `packages/ui-core/src/automation-triggers.ts`, `automation-triggers.test.ts`
- Modify: `automation-schedules.ts`, `automation-format.ts`, `automation-graph.ts`, `automation-draft.ts`, `automation-fields.ts`, `automation-templates.ts`, `automation-run-graph.ts`, `automation-diff.ts`, `index.ts`, the `testing` fixtures, and their tests

- [ ] **Step 1: Write the failing tests:**
  - `newFolderTrigger(def)` picks an id no trigger has (`folder`, `folder_2`…) and the first `file` input, or `null` input and an issue when there is none; `newWebhookTrigger(def)` likewise (`webhook`, `webhook_2`…);
  - `triggerText(t)`: `Weekdays 08:00`, `New files in ~/Downloads/invoices (*.pdf)`, `Webhook "new-invoice"`; `whenText(triggers)` joins them, and `Run now only` with none;
  - `watchStatusText`: `Watching`, `Folder not found`, `Can't watch this folder: <reason>`, `Held until you keep Desk's changes`, `Too many files: the oldest are handled first`;
  - `curlExample(url, inputs)` gives `curl -X POST -H 'Authorization: Bearer <secret>' -H 'Content-Type: application/json' -d '{"inputs":{"note":"…"}}' <url>`, with the literal `<secret>` placeholder (never a real secret);
  - `triggerText` for runs: `triggerText({trigger: 'folder', …})` is `New file`, `webhook` is `Webhook`;
  - `STEP_KIND_LABEL.for_each` is `For each`; `stepDetail` of a for-each is `each of steps.list.outputs.urls → one` or `each *.pdf in inputs.folder → one`;
  - `newStep('for_each', def)` fills `over: {list: ''}` with an issue until set;
  - template suggestions inside a for-each step's `inputs` offer `item` and `item_index` first, and nowhere else;
  - `stepLook` for a running for-each: badge `2/5`;
  - `diffDefinitions` lists trigger changes by id ("folder inbox: path changed") and step changes of for-each fields.

- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS): `pnpm vitest run packages/ui-core --maxWorkers=2`.

- [ ] **Step 5: Commit** (`feat(ui-core): triggers, webhook examples and for-each steps`).

### Task 17: The Start inspector: folder and webhook triggers, secrets

**Files:**
- Desktop: modify `design/StartInspector.tsx`, `design/DesignView.tsx`; create `design/TriggerFields.tsx`, `design/HookSecret.tsx`; tests `design/start.test.tsx`
- Web: modify `design/start-inspector.ts`, `design/design-view.ts`; create `design/trigger-fields.ts`, `design/hook-secret.ts`; spec `design/start-inspector.spec.ts`
- Contract: modify `packages/bff/src/contract/ipc.ts`, `types.ts`, `src/server/handlers.ts` + test (`automations.createHookSecret`, `automations.keepTriggers`)
- Styles: `packages/ui-styles/src/automations.css`

- [ ] **Step 1: Write the failing tests** (the React test first, then the same cases in the Angular spec):
  - the Start inspector's **Add** menu offers *Schedule*, *New files in a folder* and *Webhook*;
  - a folder row: a path field with **Choose…** (`app.pickFolder`), patterns, *Include sub-folders*, *Also when a file changes*, settle seconds, and a select of the `file` inputs (with "Add a file input" when there is none, which adds one named `file`); its watch status line reads from `detail.watch`;
  - a webhook row: the URL (read-only, with **Copy**), "No secret yet" and **Create secret** on a saved automation (a draft says "Save first"), and after creating: the secret in a box with **Copy** and "You won't see it again", plus the `curlExample`; **Rotate secret** asks for confirmation first; a note when `default_port` is false;
  - the Start pill's label uses `whenText` over every trigger.

- [ ] **Step 2:** Run the React test and the Angular spec. Expected: FAIL.

- [ ] **Step 3: Implement.** The secret lives only in the component's state (a React `useState`, an Angular `signal`), is cleared when the inspector closes, and is never put in a store, a toast or the URL. `HookSecret` copies with `navigator.clipboard.writeText`.

- [ ] **Step 4:** Run both. Then `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/parity.spec.ts)`: both new operations are called by both UIs. Expected: PASS.

- [ ] **Step 5: Commit** (`feat(ui): folder and webhook triggers in the Start inspector, in both UIs`).

### Task 18: For each in the editor

**Files:**
- Desktop: `design/StepKindFields.tsx`, `design/nodes.tsx`, `design/DesignView.tsx` (the bottom strip), `design/inspectors.test.tsx`
- Web: `design/step-kind-fields.ts`, `design/nodes/*`, `design/design-view.ts`, specs
- Styles: `automations.css` (`.auto-node.kind-for_each`)

- [ ] **Step 1: Write the failing tests:**
  - the bottom strip adds a *For each* step;
  - its fields: *Over* (a list from an upstream step or input, as a template field that accepts one path; or *Files in a folder* with globs and a folder template field), *Automation* (the project's automations except this one), the child's inputs as template fields with `{{item}}` suggested first, *At once* (1–4), *At most* items, *If an item fails* (Stop / Keep going), and *Collect outputs* (the child's `output_step` output keys, when known);
  - **Open** next to the automation select navigates to that automation's Design;
  - the node shows `For each` and the detail from `stepDetail`.

- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS) in both UIs.

- [ ] **Step 5: Commit** (`feat(ui): the For each step in the editor, in both UIs`).

### Task 19: Runs: new triggers and For each items

**Files:**
- Desktop: `runs/RunsList.tsx`, `runs/StepPanel.tsx`, create `runs/ForEachItems.tsx`, `runs/runs.test.tsx`, `runs/StepPanel.test.tsx`
- Web: `runs/runs-list.ts`, `runs/step-panel.ts`, create `runs/for-each-items.ts`, specs

- [ ] **Step 1: Write the failing tests:**
  - the runs list's trigger column shows `New file` with the file name (full path on hover) and `Webhook`; skipped lines show `Queue full` and `Trigger removed`; the header shows `3 queued` when `queued > 0`;
  - the run graph's for-each node shows `2/5` while running;
  - the step panel of a for-each step lists items (`#`, item, status, took) with **Open run** per started item (navigates to the child run), and the collected outputs once done.

- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS) in both UIs.

- [ ] **Step 5: Commit** (`feat(ui): runs show file and webhook triggers and for-each items, in both UIs`).

### Task 20: Turn on, Grants and Attention

**Files:**
- Desktop: `dialogs/TurnOnDialog.tsx`, `grants/GrantsView.tsx`, `attention/AutomationCards.tsx`, tests
- Web: `dialogs/turn-on-dialog.ts`, `grants/grants-view.ts`, `attention/automation-cards.ts`, specs

- [ ] **Step 1: Write the failing tests:**
  - the Turn-on dialog lists every trigger: schedules with their next time (as today), folders with their path and watch status ("Files already in this folder won't start runs"), webhooks with "needs a secret" when none is set;
  - the Grants tab shows, when `triggers_held`, a banner "Desk changed what starts this automation" with the trigger diff since `triggers_kept_version` and **Keep triggers** (`automations.keepTriggers`);
  - the `automation_triggers_held` attention card has **Review** (opens Grants) and **Keep triggers**.

- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS) in both UIs; then the parity spec.

- [ ] **Step 5: Commit** (`feat(ui): triggers in Turn on, held triggers in Grants and Attention, in both UIs`).

---

## Section F · End to end and docs

### Task 21: End to end

**Files:**
- Modify: `packages/core/src/automations/e2e.test.ts`, `apps/desktop/e2e/automations.e2e.test.ts` (or the file Plan 20 added), `apps/web-ui/e2e/automations.e2e.test.ts`

- [ ] **Step 1: Core integration** (harness + fake model). Desk saves three automations:
  - `summarise-file`: a `file` input, a folder trigger on a temp folder, and one agent step that summarises the file (`output_keys: summary`, `output_step`);
  - `summarise-folder`: a `folder` input and one `for_each` step over `*.txt` in `{{inputs.folder}}` that runs `summarise-file` with `{file: '{{item}}'}` and `collect: ['summary']`;
  - `note`: a webhook trigger, a `text` input and a Tell Desk step.

  The user turns them on and creates the webhook's secret through the API. Then:
  - a file dropped in the watched folder starts a `summarise-file` run whose agent sees the copy, not the original;
  - Run now on `summarise-folder` with a folder of three files gives three child runs and three collected summaries;
  - a webhook call with the secret starts `note`, and Desk is woken with its text.

- [ ] **Step 2: Desktop e2e** (`pnpm test:e2e`, with `DESK_E2E_SHOTS` for the record): add a webhook trigger in the Start inspector, save, create its secret, `fetch` the URL from the test with the secret, and watch the run appear and succeed; add a for-each step and see its items in the run view.

- [ ] **Step 3: Web e2e** (`pnpm test:web-e2e`): the same scenario in Chromium.

- [ ] **Step 4:** Run the three. Expected: PASS. One Electron or Chromium run at a time.

- [ ] **Step 5: Commit** (`test: folder and webhook triggers and for-each steps end to end`).

### Task 22: Docs

**Files:**
- Modify: `docs/api.md`, `docs/desktop.md`, `docs/web.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-09-26-automations-design.md`, `docs/superpowers/specs/2026-09-23-desk-daemon-design.md`

- [ ] **Step 1:** `docs/api.md`: the `/hooks` route (its answers, headers, the `Origin` and `Host` rules, a `curl` example), the two user routes, the new events and fields, the new attention kind.
- [ ] **Step 2:** `docs/desktop.md` and `docs/web.md`: the Start inspector's trigger kinds, secrets, For each, held triggers.
- [ ] **Step 3:** `CLAUDE.md`: the layout lines for `automations/` (`watch.ts`, `watch-scan.ts`, `queue.ts`, `foreach.ts`, `triggers.ts`) and `routes/hooks.ts`; new invariants:
  - "Only the user creates or rotates a webhook secret or keeps held triggers. deskd keeps only the secret's SHA-256, answers webhooks only on 127.0.0.1 with the trigger's own secret, and refuses requests carrying `Origin` or another `Host`. Webhooks never pass file or folder inputs."
  - "A folder trigger lists its folder without following symlinks, never watches the root, the home folder or around it, Desk's data folder or a secret, and hands a file to a run only as a `file` input copied through `tools/agent-files.ts`. Files present when it is turned on never start runs."
  - "A version an agent saves that adds or changes a folder or webhook trigger holds those triggers until the user keeps them."
- [ ] **Step 4:** The automations spec: the status line gains "folder and webhook triggers and for-each steps implemented (Plan 22)"; §12 drops folder watching, webhooks and for-each; a new **§14, Folder and webhook triggers, and For each (Plan 22)**, carries D1–D4 in the spec's own style. The daemon design's §14 records the unauthenticated-by-bearer `/hooks` route as a deviation.
- [ ] **Step 5: Commit** (`docs: folder and webhook triggers and for-each steps`).

### Task 23: Exit check

- [ ] **Step 1:** `pnpm typecheck && pnpm test`. Expected: green, with more test files and tests than Task 0's counts.
- [ ] **Step 2:** `pnpm test:e2e`, then `pnpm test:web-e2e`. Expected: green.
- [ ] **Step 3:** `git grep -n "hook_secret\|secret" -- packages/core/src/events apps/daemon/src/logger.ts` shows no place that stores or logs a raw secret; `git diff master --stat -- package.json pnpm-lock.yaml` prints nothing (no new dependency).
- [ ] **Step 4:** Append "## Deviations found while executing" to this plan, one line per place the code had to differ and why ("None." if none), and commit it.

Nothing merges to master until the user says so. Offer the finishing-a-development-branch options.
