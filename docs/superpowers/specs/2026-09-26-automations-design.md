# Desk — Automations Design

- **Date:** 2026-09-26
- **Status:** approved design, not yet implemented. Plan 19 (backend) and Plan 20 (desktop) follow.
- **Goal:** a project can hold **automations**: saved workflows that run on a schedule or on demand, built and tested by Desk, edited in a visual graph editor, and run unattended within limits the user granted. The daemon design lists this as planned work (§13, "v1.2 — scheduled/proactive Desk wakeups and recurring goals").
- **User decisions (2026-09-26):**
  - **Steps, scripts and agents.** An automation is a graph of steps. Script steps run a skill's script with no model call. Agent steps are agents with a brief and skills. The simplest automation is one agent step.
  - **Triggers:** schedules, and Run now with inputs. Folder watching and webhooks are out of scope.
  - **Unattended runs use grants.** Approvals given during runs (the test run, typically) become grants for that automation. A gated action nothing grants pauses the run and asks the user.
  - **Desk builds, and there is a full visual editor too.** The user describes a process. Desk designs the steps, has a thread write and test the scripts, saves the automation, tests it end to end and proposes turning it on. The app has a graph editor for everything as well.
  - **After a run:** each automation chooses silent, notify, or Desk review. A failed run always notifies.
  - **Control flow is a graph:** parallel branches, routes (if/else), conditions and joins.
  - **Step kinds:** script, agent, Ask me (an approval gate), Run another automation, Wait, Tell Desk.
  - **Engine:** event-sourced inside deskd (approach A). Definitions are versioned events in the database.
  - **Desktop:** a top-to-bottom graph, and the run view is the same graph lit with each step's state (mockups in §8.5).

---

## 1. Decisions

| Decision | Choice | Why |
|---|---|---|
| Engine | `AutomationEngine` in core, driven by events and a 30 s tick | Durability, crash recovery, streaming and client catch-up come from the event store. The alternatives were Desk running the graph each time (token cost, not deterministic, no model-free scripts) and an embedded workflow engine (a second persistence layer, unaware of the sandbox and policy) |
| Definitions | Versioned `automation.saved` events carrying the whole graph, with a `base_version` check | History and restore like skills, and no race between the editor, Desk and hand edits on a file |
| Scope | Per project | Runs use the project's sources, library, skills and Desk |
| Scripts | Live in skills. A script step names `skill` + `script` | Inherits skill versions, managed runtimes, the sandbox and Desk's draft → install flow |
| Agent steps | A new agent role `step` | Most of the 53 existing role checks (roster, messaging, Desk's prompt, parent notification) must exclude steps. A new role excludes them by default, and thread-like behaviour opts in explicitly |
| Enabling and grants | User only, kept outside the versioned definition. A version saved by an agent suspends grants until the user keeps them | Agents never widen their own permissions. A Desk steered by injected content cannot repurpose grants |
| Schedules | 5-field cron + IANA timezone, parsed with `croner` (MIT, no dependencies). Catch up once after sleep (per schedule: `once` or `skip`). An overlapping trigger is skipped | Predictable, and a sleeping laptop is normal |
| Editor | React Flow (`@xyflow/react`, MIT) with custom nodes. `@dagrejs/dagre` (MIT) auto-lays out definitions saved without positions | Pan, zoom, connections and edge labels are solved problems |

## 2. Model

### 2.1 Automation

An automation has `id` (ULID), `project_id`, and `name` (`^[a-z0-9][a-z0-9-]{0,63}$`, unique among the project's non-deleted automations, fixed at creation; the title can change). The **versioned definition** (`AutomationDefinition`, zod in `protocol/src/automations.ts`) holds:

| Field | Meaning |
|---|---|
| `title`, `description` | Shown in lists, prompts and notifications. Also tell step agents what the automation is for |
| `inputs[]` | `{key, label, type, required, default?, options?, description?}`. `type` is one of `text`, `long_text`, `url`, `file`, `folder`, `number`, `boolean`, `choice`. `key` is `^[a-z][a-z0-9_]{0,39}$` |
| `triggers[]` | Schedules: `{kind: 'schedule', cron, timezone, catch_up: 'once' \| 'skip', inputs?: {key: value}}`. Run now is always available and is not listed |
| `steps[]` | §3 |
| `edges[]` | `{from, to, route?, when?}` (§3.2) |
| `output_step?` | The step whose outputs and folder are this automation's result when it runs as a sub-automation |
| `after_run` | `silent` \| `notify` \| `desk_review` (default `notify`) |
| `limits` | `run_deadline_hours` (default 24, max 168), `max_parallel_agents` (default 2, max 4), `max_parallel_scripts` (default 4, max 8) |

**Kept outside the versions** (each set by its own event):
- `layout`: node positions. Moving a node never creates a version.
- `enabled`: whether schedules fire. User only.
- `grants`: user only. See §5.
- `grants_suspended`: true after an agent saves a version while grants exist. Cleared when the user keeps or sets grants.
- `last_due_at`: per schedule. The engine's cursor.

A definition's **tested version** is the latest version with a succeeded `test` run. The UI shows "v7 · tested in v6".

### 2.2 Runs

- **`automation_runs`** holds:
  - `id`, `automation_id`, `version`, `inputs`, `test`;
  - `trigger`: `schedule`, `manual`, `desk`, `parent` or `test`;
  - `parent_run_id` / `parent_step_id`;
  - `due_at` and `caught_up` (the number of missed schedule times it replaces);
  - `by`: `user`, `agent:<id>` or `schedule`;
  - `status`: `running`, `waiting`, `succeeded`, `failed` or `cancelled`;
  - `summary`, `reason`, `started_at`, `finished_at`, `deadline_at`.
- **`automation_step_runs`** holds:
  - `run_id`, `step_id`, `attempt`;
  - `status`: `pending`, `running`, `waiting`, `succeeded`, `failed`, `rejected`, `skipped` or `cancelled`;
  - `route`, `outputs` (JSON), `summary`, `error`;
  - `agent_id` (agent steps), `child_run_id` (sub-automation steps), `resume_at` (waits);
  - `gate`: a pending approval of a script step (`{tool, subject, reason}`);
  - `question`: an Ask me step's rendered question and attached files;
  - `started_at`, `finished_at`.
- A run is `waiting` while at least one step waits on the user and none is running.

### 2.3 Events

All new events carry `project_id`, and `agent_id` is null unless stated otherwise. They are projected in the same transaction, as usual.

| Event | Payload | Appended by |
|---|---|---|
| `automation.saved` | `automation_id, name, version, definition, origin ('user' \| 'agent:<id>'), change_note, via ('editor' \| 'api' \| 'cli' \| 'import' \| 'restore' \| 'tool')` | user, Desk |
| `automation.layout_saved` | `automation_id, layout: {step_id: {x, y}}` | user (editor) |
| `automation.deleted` | `automation_id, origin` | user, Desk (gated `ask`) |
| `automation.switched` | `automation_id, enabled, by ('user' \| 'system')` | user. `system` only when a project is archived |
| `automation.grants_set` | `automation_id, grants, reason ('edited' \| 'remembered' \| 'kept' \| 'enabled')` | user |
| `automation.enable_requested` | `automation_id, note, proposed_grants` (`agent_id` = Desk) | Desk |
| `automation.run_started` | `run_id, automation_id, version, trigger, test, inputs, by, parent?, due_at?, caught_up?` | engine |
| `automation.step_changed` | `run_id, step_id, attempt, status` plus whichever of `route, outputs, summary, error, agent_id, child_run_id, resume_at, gate, question, answered_by, note` apply | engine, user (answers) |
| `automation.run_finished` | `run_id, status, summary, reason?` | engine |
| `automation.trigger_skipped` | `automation_id, trigger_index, due_at, reason: 'still_running' \| 'missed'` | engine |

The projection sets `grants_suspended` in two cases:
- it sets it on an `automation.saved` whose origin is `agent:*` while `grants` is non-empty;
- it clears it on an `automation.grants_set` with reason `edited`, `kept` or `enabled`. A `remembered` grant leaves it unchanged, and while grants are suspended the UI doesn't offer *remember* (§5.3).

`last_due_at` is derived from events too. Each schedule's cursor is set by:
- the `due_at` of `automation.run_started` and `automation.trigger_skipped`;
- the event time of an `automation.switched {enabled: true}`;
- the event time of an `automation.saved` that changes that schedule while the automation is on.

`artifact.published.origin` widens to `^(user|agent:.+|automation:.+)$`, and `agent.created` gains `automation?: {run_id, step_id}` for step agents.

Tables come from one additive migration: `automations`, `automation_versions`, `automation_runs`, `automation_step_runs`.

## 3. Execution

### 3.1 Steps (common fields)

`id` (`^[a-z][a-z0-9_-]{0,39}$`, unique), `title`, `kind`, plus:

| Field | Default | Meaning |
|---|---|---|
| `join` | `all` | `all`: waits until every incoming edge is decided, then runs if at least one fired (skipped if none did). `any`: runs on the first edge that fires, and later ones are ignored |
| `on_error` | `stop` | `stop`: the run fails and running steps are cancelled. `continue`: the step resolves with route `error`. `{retry: n}` (n ≤ 3): new attempts with 30 s, 2 min and 8 min backoff, then `stop` |
| `timeout_min` | script 10, agent 60 | Active time. Waits and questions have their own limits |
| `routes` | `[]` | Named outcomes the step may choose |
| `publish` | `[]` | Globs relative to the step folder, copied to the library after success |
| `idempotent` | `false` | Script steps only: whether a crash-interrupted attempt may be retried (§3.6) |

### 3.2 Edges, routes and conditions

- **A step resolves** as:
  - **succeeded**, with an optional route from its `routes`;
  - **failed**: with `on_error: continue` it takes route `error`; otherwise the run fails;
  - **rejected**: Ask me only, with route `rejected`;
  - **skipped** or **cancelled**.
- **An edge fires** when both hold:
  - without a `route`: its source *succeeded* (with any route or none). With a `route`: its source's route equals it (`error` and `rejected` included);
  - its `when`, if present, is true.
- **`when`** is a small expression language. It is parsed into an AST and evaluated, never `eval`ed:
  ```
  expr    := or
  or      := and ('or' and)*
  and     := unary ('and' unary)*
  unary   := 'not' unary | cmp
  cmp     := operand (('==' | '!=' | '<' | '<=' | '>' | '>=' | 'contains') operand)?
  operand := path | 'exists(' path ')' | string | number | 'true' | 'false' | 'null' | '(' expr ')'
  path    := 'inputs.' key | 'steps.' id '.' ('outputs.' key | 'route' | 'status')
  ```
  - A missing path evaluates to `null`. Comparing values of different types is false, except `!=`, which is true.
- **Skips spread:** a step skipped because none of its incoming edges fired makes its own outgoing edges not fire.
- **Stopping early** needs no special mechanism. A route with no outgoing edge (e.g. `nothing_new`) ends that branch, and the run still succeeds.
- **Start steps** are steps with no incoming edge. They start when the run starts.
- **A run finishes** when no step is `pending`, `running` or `waiting`. It **succeeded** if no step failed with `stop`. It **failed** if one did, with that step's error as the reason.
- **The run deadline:** when `deadline_at` passes, the run is cancelled with reason `deadline`.

### 3.3 Data between steps

- **Run folder:** `<data>/automation-runs/<run_id>/`.
  - `inputs/` holds copies of file and folder inputs.
  - `inputs.json` holds the resolved input values, with file and folder inputs as the paths of their copies.
  - `steps/<step_id>/` is each step's working folder. Only that step (its script, or its agent's workspace) can write to it. Later steps, and Desk, can read it.
- **Results:** a step's results are its folder, a `summary` (≤ 2000 characters) and `outputs`. Outputs are a JSON object of scalars and lists of scalars: ≤ 16 KB in total, strings ≤ 4000 characters, lists ≤ 200 items.
- **Templates** `{{path}}` fill agent briefs, script `args` and `stdin`, questions, Tell Desk text, and sub-automation inputs. The paths are:
  - `inputs.<key>`
  - `steps.<id>.outputs.<key>`, `.summary`, `.route`, `.dir`
  - `run.id`, `run.dir`, `run.date` (YYYY-MM-DD in the automation's first timezone, or the system timezone), `run.trigger`, `run.test` (`true` / `false`)
  - `previous.steps.<id>.dir` and `previous.steps.<id>.outputs.<key>`, from the last succeeded non-test run of the same automation. They are empty when there is none.
- **Script arguments are argv elements.** Substitution happens inside each element, and the result is never parsed by a shell (each element is shell-quoted by the existing `commandFor` + `shellQuote` path).
  - An element that is exactly `{{path}}` and resolves to a list spreads into several elements.
  - In text, a list renders one item per line.
- **Validation** (§6.1) refuses a template or `when` that references a step that is not an upstream ancestor, an unknown input, or, for an agent step, an output key it does not declare.
- **Publishing:** after a step succeeds, files matching `publish` are copied to the library under `automations/<name>/<YYYY-MM-DD HHmm>/`, through `publishToLibrary` with origin `automation:<run_id>`. Test runs publish under `automations/<name>/tests/…`.
- **Retention:** per automation, run folders are kept for the last 20 runs or 30 days, whichever keeps more. Three folders are never pruned: the run a `previous` reference would resolve to, a run that is still going, and a run with an unresolved attention item. Library copies are not pruned. Pruning happens when a run finishes, and at daemon start.

### 3.4 Concurrency and budgets

- Ready steps start in parallel, up to `max_parallel_agents` agent steps and `max_parallel_scripts` script steps per run.
- **Agent steps count** towards the project's `max_concurrent_threads` and the model's concurrency, through the existing scheduler.
- **Automation runs are not held by the project's wake pause, and don't use the wake budgets.** The user scheduled or started them, and each run is bounded by its caps and deadline. Engine-started step agents use a new wake trigger, `automation`, which `admit` lets through.
- **Deliveries to Desk** (Tell Desk, Desk review) are ordinary `lifecycle` wakes, subject to the budget and the pause.

### 3.5 Cancelling

`POST /automation-runs/:id/cancel`, `automation_cancel`, or the deadline:
- stops the run's step agents (`stopAgent`, silent to Desk);
- kills script processes;
- cancels child runs;
- marks every pending, running and waiting step `cancelled`;
- finishes the run `cancelled`.

Deleting an automation or archiving its project cancels its runs.

### 3.6 Crash recovery

At daemon start, after `runtime.recover()`, `AutomationEngine.recover()` handles each step that is still active:

| Step state | Recovery |
|---|---|
| Script step `running` | → `failed` with error `interrupted: the daemon stopped while this script ran; its side effects are unknown`, then `on_error` applies. `retry` re-runs it **only** if `idempotent: true`, and this is the only way a crash-interrupted script re-runs. The invariant "crash recovery never re-executes a tool call whose outcome is unknown" holds for steps too |
| Agent step | Its agent resumes through the existing agent recovery. The engine re-attaches to its completion |
| `waiting` (Ask me, script gate, approval) | Stays waiting |
| Wait | Stays waiting. `resume_at` in the past resumes on the first tick |
| Sub-automation | Re-attaches to the child run |

A run whose `deadline_at` passed while the daemon was down is cancelled with reason `deadline`.

## 4. Step kinds

### 4.1 Script — `{skill, script, args[], stdin?, timeout_min?, idempotent?}`

- **Resolves** the skill as agents do (project → global → built-in; a turned-off or broken built-in fails the step). Its managed runtime is ensured first, and that wait does not count against the timeout.
- **Policy:** the call is evaluated by `evaluatePolicy` as the `skill_run` tool with subject `command: "<skill>/<script> <args…>"`. The rules are the automation's grants, then the project policy.
  - `deny` fails the step.
  - `ask` sets the step to `waiting` with a `gate`. The user answers it like an Ask me step (§4.3), with *Approve*, *Deny* and *Approve and remember*.
- **Runs** under the shell sandbox as `skill_run` does:
  - working directory is the step folder, the only writable path besides temp dirs;
  - readable: the run folder, the `previous` run folder, project sources, the library;
  - environment: `scrubbedEnv` + `withSkillEnv`, plus `SKILL_DIR`, `SKILL_NAME`, `DESK_RUN_DIR`, `DESK_STEP_DIR`, `DESK_INPUTS` (the path of `inputs.json`), `DESK_OUTPUT` (a path in the step folder) and `DESK_TEST` (`1` for test runs).
- **Result:**
  - **Exit 0:** reads `$DESK_OUTPUT` if present: `{route?, outputs?, summary?}`, validated against the step's `routes` and the output limits. An invalid file fails the step. Without the file, the summary is the last line of output and there are no outputs.
  - **Non-zero exit or timeout:** fails with the last 4000 characters of output.
  - **Log:** the full output goes to `steps/<id>/.desk/log.txt`.

### 4.2 Agent — `{brief, skills[], output_keys[], model?, reasoning_effort?, git_source_id?, max_steps?}`

- **Spawns a `step` agent** through `Runtime.createStepAgent`. Its workspace is the step folder. With `git_source_id`, that folder is a git worktree on `desk/auto-<name>-<shortid>`, off the source's HEAD. It has no parent notification, and is started with a `start` delivery using trigger `automation`.
- **`output_keys`:** `[{key, description}]`. These are what downstream templates and conditions may use.
- **Model and effort** default to the project's thread settings. `max_steps` defaults to 100.
- **Prompt** (`stepSystemPrompt`, new in `agent/prompts.ts`):
  - It opens: "You are one step of the automation *<title>*. Nobody is watching this run."
  - It then gives:
    - the automation's description;
    - the step's rendered brief;
    - the run's inputs;
    - each upstream step's title, summary, outputs and folder;
    - its own folder;
    - the routes it may choose, and the output keys with their descriptions;
    - the memory digest and active skills, as for threads.
  - The rules:
    - do only this step;
    - never ask anyone;
    - verify your outputs;
    - write files only in your folder;
    - when you can't do the step correctly, call `fail_step` with the reason instead of guessing;
    - call `complete` once.
- **Tools:** the thread toolset minus `message_desk`, `message_thread`, `list_threads`, `read_thread`, `wait_for_reply`, the service tools, `memory_write` and `library_publish`, and with two replacements:
  - `complete` becomes `complete(summary, outputs?, route?)`. `outputs` are validated against `output_keys` and the limits, and `route` against `routes`.
  - `fail_step(reason)` is new. It resolves the step `failed`.
- **Policy:** the automation's grants come before the project rules. `delegate_to_desk` is ignored, so every *ask* goes to the user. While the approval is pending, the step is `waiting`.
- **Ending:** reaching `max_steps` or the timeout, or ending a turn without `complete`, fails the step. Ending a turn without `complete` gets one nudge first: "Call complete or fail_step."
- **Retries:** a retry spawns a fresh agent. The previous one is archived.
- **Visibility:** step agents never appear in the Threads tab, the line diagram, Desk's roster or other agents' Team. Their transcript opens from the run view (§8.3), and Desk reads it with `automation_read_run`.

### 4.3 Ask me — `{question, show?: globs, approve_label?, reject_label?, expires_after_hours?}`

- The step goes to `waiting` with `question: {text, files}`, where `files` holds the paths matching `show` in upstream step folders.
- The user answers through `POST /automation-runs/:id/steps/:sid/answer {decision: 'approve' | 'reject', note?}`:
  - approve: `succeeded`, with `outputs.note`;
  - reject: `rejected`, with route `rejected` and `outputs.note`.
- When `expires_after_hours` passes, the step is `rejected` with the note "expired".
- Only the user can answer. There is no agent tool for it.

### 4.4 Run another automation — `{automation, inputs: {key: template}}`

- Starts a child run of the named automation in the same project (trigger `parent`, the same `test` flag), and waits for it.
- **Result:** the step succeeds or fails with the child. Its outputs are the child's `output_step` outputs plus `run_id`, and `steps.<id>.dir` is that step's folder in the child run. Without an `output_step`, the outputs are `{run_id, status}` and `dir` is the child run folder.
- **Grants:** the child applies its own grants. Its `grants_suspended` is respected.
- **Cycles:** validation refuses a cycle across automations. At run time, depth is limited to 3.

### 4.5 Wait — `{minutes}` or `{until: "HH:MM"}`

- Sets `resume_at`: now + minutes, or the next HH:MM in the automation's first timezone (or the system timezone). At most 7 days.
- Resumes on the first engine tick at or after `resume_at`, then `succeeded`.

### 4.6 Tell Desk — `{text, attach?: globs}`

- Delivers a `message.agent` to the project's Desk. The sender label is `Automation: <title>`, and the kind is a new `automation` kind. It joins `LIFECYCLE_KINDS`, so the delivery is a `lifecycle` wake (§3.4). Run reports for `wait_for_run` and Desk review use the same kind.
- The message holds the rendered text, the run id, and the paths of attached files in upstream step folders, which Desk may read (§5.3).
- The step `succeeded` once the message is delivered. Desk's handling is outside the run.

### 4.7 After the run

- `after_run: notify`: a notification with the summary on success.
- `desk_review`: a Tell Desk delivery at run end, with the run report (§6.2).
- Failed runs always notify, and add an Attention item.
- The run `summary` is the `output_step`'s summary if set, otherwise the last succeeded step's.

## 5. Triggers, grants and turning on

### 5.1 Schedules

- **Tick:** every 30 s (unref'd, next to the stall interval in `daemon.ts`). For each enabled automation and each schedule, the engine computes `next = croner(cron, timezone).nextRun(last_due_at)`.
- **When `next` ≤ now:**
  - If a non-test run of this automation is `running` or `waiting`, the engine appends `automation.trigger_skipped (still_running)` and advances `last_due_at`.
  - Otherwise it starts a run with `due_at` = the latest due time ≤ now, and `caught_up` = the number of due times skipped over.
  - With `catch_up: skip`, a due time older than one tick interval (a missed one) starts nothing. The engine appends `automation.trigger_skipped (missed)` for the latest missed time instead.
  - Either way, `last_due_at` advances to that latest due time (§2.3).
- **Turning on, or saving a changed schedule while on,** sets `last_due_at` = now, so a schedule never fires straight away.
- **Frequency limit:** validation refuses a schedule that fires more often than every 5 minutes, checked on the next 10 occurrences.
- **Caveats (documented):** deskd cannot wake a sleeping Mac, because that needs root `pmset`. While the daemon is down, schedules catch up on the next start.

### 5.2 Run now

`POST /automations/:id/runs {inputs, test?}`, `desk automation run`, or Desk's `automation_run` / `automation_test`. It works whether or not the automation is on. Inputs are validated against the definition, and defaults are applied. File and folder inputs are local paths: deskd copies them into `inputs/` through `tools/agent-files.ts`, so regular files only (folders recursively, with the same checks per file), no symlink at the end, secrets and Desk's data dir refused, 200 MB per run.

### 5.3 Grants

- A grant is a policy rule (`PolicyRule` minus `delegate_to_desk`, action `allow` or `deny`). The grants of the automation a run belongs to go **before** the project policy, for its script gates and its step agents' tool calls.
- **Approve and remember.** `POST /approvals/:id/resolve {decision: 'approved', remember: true}` is accepted only for approvals raised by a step agent, and the step answer endpoint accepts `remember` for script gates. Either one approves, and appends `automation.grants_set (remembered)` with a grant derived from the call:

  | Tool | Derived grant |
  |---|---|
  | `web_fetch`, `web_search` | `match.domain` = the exact host (the UI offers widening to `*.<registrable domain>`) |
  | `git_push`, `open_pr` | `match.branch` = `desk/auto-<name>-*` |
  | `skill_run` (a script gate, or a step agent's call) | `match.command` = `^<skill>/<script>(\s\|$)`: that installed script with any arguments, because templated arguments change from run to run |
  | `bash`, `bash_background` | `match.command` = the exact command, regex-escaped and anchored |
  | anything else | the tool name, no match |

- **Proposed grants:** approvals resolved `approved` during this automation's runs (test runs especially) are collected, deduplicated, into proposed grants. The Turn-on dialog lists them, ticked, and `automation_request_enable` includes them.
- **Suspension:** while `grants_suspended` is true, runs behave as if there were no grants, and approval cards offer no *remember*. The UI shows the version diff since the grants were last set, with **Keep grants**, which appends `grants_set (kept)`.

### 5.4 Turning on

- `PUT /automations/:id/enabled {enabled}` (app switch, CLI `desk automation on|off`). Only the user can do this.
- Desk's `automation_request_enable(name, note)` appends `automation.enable_requested`. It shows in Attention: "Desk proposes turning on *<title>*: <schedules>, <n> grants". Its **Turn on** opens the Turn-on dialog, which sets grants (`grants_set (enabled)`) and then the switch.
- If the current version has no succeeded test run, the dialog warns ("v7 hasn't been tested"), with *Test first* or *Turn on anyway*.
- Archiving a project appends `automation.switched {enabled: false, by: 'system'}` for each of its automations, and cancels their runs.

## 6. Desk as the builder

### 6.1 Tools (Desk only, `tools/automations.ts`)

| Tool | Notes |
|---|---|
| `automation_list()` | name, title, on/off, version, tested version, schedules, last run, next due, flags (untested changes, grants suspended) |
| `automation_read(name, version?)` | The definition as JSON, the switch, grant count, the last 5 runs |
| `automation_save(name, definition, change_note, base_version?)` | Create (no `base_version`) or update. Validates everything in §6.3 and throws a readable list of errors. Returns the version and any warnings |
| `automation_test(name, inputs)` | Starts a test run of the current version and returns its run id. The result tells Desk what the test will really do: steps that act outward, and whether scripts see `DESK_TEST` |
| `automation_run(name, inputs)` | A manual run (by Desk), on the user's request |
| `wait_for_run(run_id)` | Yields. Desk is woken with the run report when the run finishes or starts waiting on the user |
| `automation_read_run(run_id, step?)` | The run report, or one step's detail: an agent step's transcript (summary or full), a script's log tail |
| `automation_cancel(run_id, reason)` | |
| `automation_request_enable(name, note)` | §5.4 |
| `automation_delete(name)` | Gated `ask` (like `skill_delete`) |

Threads get no automation tools. Desk's readable roots gain the run folders of its project's automations (`<data>/automation-runs/<run_id>/`), so it can read reports' files and Tell Desk attachments.

### 6.2 The run report

A run report is the text Desk receives from `wait_for_run`, from Desk review, and from `automation_read_run`. It holds:
- a header: automation, version, trigger, status, took, reason;
- then, per step in topological order: its status and attempt, route, summary, outputs, error (with the log tail for scripts), folder, and agent id;
- then the published library paths.

It is capped at 12 000 characters, with the longest summaries and logs trimmed first.

### 6.3 Validation (shared by `automation_save`, the API and the editor's validate endpoint)

Errors are `{path, message}`. `path` points into the definition (e.g. `steps[2].brief`) so the editor can mark the node. The checks:
- names and ids are well formed and unique;
- edges reference real steps, there is no self-loop, and the graph is acyclic;
- every edge `route` is in its source step's `routes` (or is `error` on a step with `on_error: continue`, or is `rejected` on an Ask me step);
- `when` and templates parse, and reference only inputs and upstream ancestors, with declared output keys for agent steps;
- skills exist, and script files exist in them;
- models and efforts are valid, as in `spawn_thread`;
- cron and timezone are valid, within the frequency limit;
- the sub-automation exists, with no cycle across automations, and its inputs satisfy its required inputs;
- `output_step` exists;
- limits are in range;
- at least one step.

Warnings (saving still allowed):
- a step that no start step reaches;
- a route declared with no edge (it ends that branch);
- a built-in skill that is turned off.

### 6.4 Prompt

Desk's "How you work" gets an **Automations** step after Skills:
1. **Recognise one.** When the user describes something recurring or repeatable ("every Monday…", "automate…", "whenever I get…"), or a process they have asked for more than once, propose an automation. Work out its inputs, trigger and steps.
2. **Prefer script steps** for deterministic work: fetching, converting, transforming, file operations. Use agent steps only where judgment is needed. Before any action that reaches outside, put an Ask me step, unless the user said otherwise.
3. **Build scripts through a thread,** as a skill draft. Activate the built-in `automation-scripts` on that thread. Install the draft with `skill_write from_dir`.
4. **Save, then test** with realistic inputs through `automation_test` + `wait_for_run`. Tell the user what a test run will really do.
5. **Read the report, fix, re-test.** After 3 failed rounds, report to the user instead.
6. **Report and propose.** Tell the user what the automation does, and show the test's outputs, its schedule and the grants it needs. Then call `automation_request_enable`. Never claim it is on.
7. **Maintain.** When the user asks for a fix (e.g. from **Ask Desk to fix**), read the failed run, fix it and re-test.

Desk's context gains an **Automations** section: per automation, its name, on/off, schedules, last run status, and flags. At most 20 lines, the rest countable by `automation_list`.

### 6.5 Built-in skill `automation-scripts`

`catalog/skills/automation-scripts/` contains:
- `SKILL.md`, describing the script contract:
  - arguments;
  - `DESK_INPUTS` / `DESK_OUTPUT` / `DESK_TEST`;
  - routes;
  - output limits;
  - write only to the working folder;
  - idempotence;
  - dry runs.
- `scripts/desk_step.py`, a helper: `inputs()`, `output(route=None, summary=None, **outputs)`, `is_test()`.
- `scripts/selftest.py`.

It has no Python packages. It follows the built-in rules: Windows lint and `pnpm builtins:pin automation-scripts`.

## 7. API, client, CLI

### 7.1 HTTP (under `/v1`, bearer; schemas in `protocol/src/api.ts`)

| Method & path | Purpose |
|---|---|
| `GET/POST /projects/:id/automations` | List (summaries) / create `{name, definition, change_note?}` → 201 |
| `POST /projects/:id/automations/validate` | `{definition, name?}` → `{errors, warnings}` |
| `GET/PUT/DELETE /automations/:aid` | Detail / save `{definition, change_note?, base_version}` (409 on a stale `base_version`, with the current version in the error details) / delete |
| `PUT /automations/:aid/layout` | `{layout}` |
| `GET /automations/:aid/versions`, `GET …/versions/:v`, `POST …/versions/:v/restore` | History, a version, restore (a new version, `via: 'restore'`) |
| `PUT /automations/:aid/enabled`, `PUT /automations/:aid/grants`, `POST /automations/:aid/grants/keep` | User-only switch, grants, keep |
| `GET /automations/:aid/export`, `POST /projects/:id/automations/import` | `{format: 'desk-automation/1', name, definition}`. Grants, the switch and layout are not exported (layout is re-derived) |
| `POST /automations/:aid/runs` | `{inputs, test?}` → 202 `{run_id}` |
| `GET /automations/:aid/runs?before=&limit=` | Runs and skipped triggers, newest first |
| `GET /automation-runs/:rid` | The run with its steps |
| `POST /automation-runs/:rid/cancel` | |
| `POST /automation-runs/:rid/steps/:sid/answer` | `{decision: 'approve' \| 'reject', note?, remember?}` for Ask me steps and script gates. `reject` on a gate denies the call, which fails the step |
| `GET /automation-runs/:rid/steps/:sid/log` | A script step's log |
| `GET /automation-runs/:rid/files/*path` | The run folder, confined like thread files (`workspaces/inspect.ts`) |
| `POST /approvals/:id/resolve` | Gains `remember?: boolean` (§5.3) |

**Errors:** validation failures return 400 with `details.errors: {path, message}[]`, a stale version 409, unknown ids 404.

**Attention:** new kinds, derived from the tables and dismissible like the others where it makes sense:
- `automation_ask`: a step waiting on an answer (Ask me or a script gate);
- `automation_failed`: the latest failed run per automation, until a later run succeeds or it is dismissed;
- `automation_enable_request`: until the automation is turned on or the item is dismissed;
- `automation_grants_suspended`.

Approval items raised by step agents carry `automation: {id, name, run_id, step_id}`.

**Notifier:** notifies for `automation_ask`, failed runs, and `after_run: notify` successes.

### 7.2 Client

- `DeskClient` gets a method per route.
- `state/automations.ts` is a pure reducer over the new events: the list, the current definition, runs, and live step states.
- The attention and project reducers learn the new kinds.

### 7.3 CLI

```
desk automations <project>
desk automation show <project> <name> [--version N]
desk automation run <project> <name> [--input key=value …] [--test] [--follow]
desk automation on|off <project> <name>
desk automation runs <project> <name>
desk automation cancel <run>
desk automation answer <run> <step> approve|reject [note] [--remember]
desk automation export <project> <name> > file.json
desk automation import <project> file.json
```

`--follow` streams step changes until the run finishes.

## 8. Desktop app

### 8.1 Automations tab

`PROJECT_TABS` gains `automations`, between Threads and Library. The route is `project/:id/automations[/:aid[/runs/:rid | /versions | /grants]]`.

- **List** (mockup 1): per automation, the title, version and flags, the switch, when it runs, the last run (including "waiting on you: …"), and the next run. Actions:
  - **Describe one to Desk** opens Conversation with the composer primed: "I'd like to automate: ".
  - **Blank automation** asks for a name and opens Design on a local draft with only the Start pill. Nothing is created until the first **Save**, and validation needs at least one step.
  - **Import…** reads a JSON export through the file picker.
- **Header:** the title, a version badge ("v7 · tested in v6"), the switch (opens the Turn-on dialog when turning on), **Test…**, **Run now…**. Sub-tabs: *Design · Runs · Versions · Grants*.

### 8.2 Design (mockup 2)

- **The canvas** is React Flow, flowing top to bottom:
  - A **Start** pill (schedules and inputs) connects to the start steps.
  - **Nodes** are custom and colour-coded by kind: script ink, agent `--run`, Ask me `--wait`, Wait `--muted`, Run automation `--ok`, Tell Desk `--accent`. Each shows its kind, title and a one-line detail.
  - **Edge labels** show the route and a short form of the `when`.
  - A route with no edge shows as a dashed stub, "<route> · ends".
- **Editing:**
  - The bottom strip adds steps.
  - Drag from a node's bottom edge to connect.
  - Click an edge to set its route or condition.
  - Delete removes the selection.
  - **Tidy up** re-lays out with dagre.
- **The inspector** (right) edits the selected step's fields (§3–4). Template chips (`{{…}}`) autocomplete from inputs and upstream steps' outputs. Skill and script pickers list what exists.
- **The Start pill's inspector** edits schedules (presets such as daily, weekdays, weekly or monthly at a time, or custom cron + timezone, with a preview of the next 3 times and catch-up) and inputs.
- **Draft and save:**
  - Edits change a local draft (renderer state).
  - The validate endpoint runs, debounced at 400 ms. Errors mark nodes red and show in the inspector.
  - **Save** (with an optional change note) sends `base_version`.
  - On 409: "Desk saved v8 while you were editing", with *Review changes* (the structured diff), *Save mine anyway* (re-save on the new base) and *Discard mine*.
  - Layout changes save on their own (`PUT …/layout`, debounced).

### 8.3 Runs (view A)

- **The list:** #, started, trigger (with version for tests), status (with the current or failed step), took, summary. Skipped triggers show as lines of their own.
- **A run:** the Design graph, read-only, with each node showing its state:
  - done: `--ok`, with its duration;
  - running: `--run` with a ring, and its live activity;
  - waiting: amber;
  - pending: dimmed;
  - skipped: hatched;
  - failed: `--accent`;
  - edges that fired are solid, the others dashed.
- **The side panel** shows the selected step:
  - **While it runs:** an agent step's live output (from the existing `assistant.delta` stream of its agent), tokens, folder, *Open transcript* (the existing transcript view) and *Stop step*.
  - **Once it's done:** summary, outputs, route, files (previewed with the app's file viewer), log.
  - **Failed:** the error and **Ask Desk to fix**, which sends Desk the user message "Please fix <automation>: run #<n> failed at <step>. <error first line>".
- **The header:** *Open folder* (reveal in Finder, through a new `app.revealPath` IPC op confined to the run folder) and *Cancel run*.
- **A waiting Ask me step** or script gate shows its question, files and Approve / Reject with a note (and *remember* for gates) in the panel.

### 8.4 Versions, Grants, dialogs, Attention

- **Versions:** each version shows who saved it (you, Desk, import or restore), when, its change note and ✓ tested. A structured diff between any two versions lists steps added, removed or changed with the fields that changed, plus edges, inputs and triggers. Any version can be restored.
- **Grants:** each rule shows where it came from (run and step, or edited) and can be edited, removed or added.
  - The web-domain widening toggle (§5.3) lives here.
  - While grants are suspended, a banner shows Desk's change note and the diff, with **Keep grants**.
- **Run now… / Test…** open a form generated from `inputs`. It needs a new `app.pickFile` IPC op next to `pickFolder`.
- **Turn on:** shows the schedules with the next due time, the proposed grants (ticked), and the untested warning.
- **Attention cards** for the four new kinds. Approval cards for step agents show "<automation> · <step>" and **Approve and remember for this automation**. Desktop notifications come through the existing attention-added path.
- **IPC:** an `automations.*` namespace mirroring §7.1, zod-validated in main, as for every op.

### 8.5 Mockups

`2026-09-26-automations-mockups/automations-editor.html` (the list and the Design view) and `run-view.html` (the Runs list; option A was chosen) are the visual reference. They are drawn in the app's tokens. They are HTML fragments from the brainstorming companion, and open directly in a browser.

## 9. Security

These are added to CLAUDE.md's invariants:
- Only the user turns automations on or off and sets their grants. A version saved by an agent suspends grants until the user keeps them.
- Grants apply only to runs of their automation, and only while not suspended. Step agents' approvals are never delegated to Desk.
- Script steps run sandboxed like `skill_run`, writing only their step folder. Their arguments are substituted per argv element and never parsed by a shell.
- Crash recovery never re-runs an interrupted script step unless the step is marked `idempotent`.
- Step agents cannot message other agents, publish to the library, write memory or start services. Their workspace is their step folder (or a worktree on a `desk/auto-*` branch).
- File inputs are copied through `tools/agent-files.ts`. A run folder is never a project source.
- Everything in the sandbox guard still applies to scripts and step agents: no token file, no database, no credentials, no deskd port.

## 10. Testing

- **Unit (core):**
  - graph semantics: `join` all and any, routes, `when`, skip propagation, `continue` → `error`, retries with backoff under a fake clock, cancelling, the deadline;
  - the expression parser, including hostile input;
  - templates → argv (an input like `; rm -rf ~` stays one argument), and list spreading;
  - every validation rule and warning;
  - schedules: next due, catch-up once and skip, overlap, turning on, the frequency limit, timezones and DST (with a fake clock);
  - grant derivation, suspension on agent saves, and `evaluatePolicy` with grants in front;
  - output limits;
  - retention.
- **Integration (harness + fake model):**
  - Desk builds an automation: a thread drafts a script skill and Desk installs it, then Desk saves a graph (script → agent), tests it (`wait_for_run`), reads the report and requests turning it on;
  - a scheduled run fires under a fake clock and publishes to the library;
  - an Ask me step is answered through the API, both approve and reject routes;
  - a step agent's approval with `remember` becomes a grant, and the next run doesn't ask;
  - a script gate with `remember`;
  - a sub-automation returns its `output_step` outputs;
  - Tell Desk wakes Desk;
  - `desk_review` delivers a report;
  - an agent-saved version suspends grants, and **Keep grants** restores them;
  - a daemon restart during a script step records `interrupted`, and a retry happens only when the step is idempotent;
  - a wait survives a restart;
  - cancelling stops agents and processes;
  - step agents never show in the roster, Team or Desk's prompt.
- **Sandbox:** a script step cannot write outside its folder, and can read earlier steps' folders. A step agent's file tools are confined the same way.
- **Client reducers, CLI** (`run --follow`, `answer`), **API** (routes, 409, validation details).
- **Built-in:** `automation-scripts` selftest, `catalog:check --builtins automation-scripts`, and the `builtins.test.ts` digest.
- **Desktop:**
  - component tests: node rendering, the inspector, template autocomplete, the diff;
  - e2e: create an automation in the editor, save, Run now with an input, watch the graph light up, answer an Ask me step, see the run succeed and the library file.
- **Live smoke** (`DESK_LIVE=1`): Desk builds and tests a two-step automation against the real proxy.

## 11. Delivery

- **Plan 19, backend:** protocol, events and migration; the engine and the six step kinds; schedules; grants and the policy integration; the `step` role (prompt, toolset, visibility audit of the role checks); Desk's tools and prompt; the `automation-scripts` built-in; API, attention, notifier; client; CLI; `docs/api.md`.
- **Plan 20, desktop:** the tab and routes; list; Design editor (React Flow, dagre, inspector, validation, save and 409); Runs and view A; Versions and diff; Grants; dialogs; Attention cards; IPC; `docs/desktop.md`; e2e.

## 12. Out of scope

- Other triggers: folder watching, webhooks, email.
- Loops and for-each (the graph is acyclic).
- Automations shared across projects.
- Connectors such as Slack or email for sending.
- Waking a sleeping Mac.
- Cost in currency.
- Automations in the Angular web UI on `web-ui` (a follow-up once that branch catches up).
