# Automations Desktop Implementation Plan (Plan 20)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The desktop app gets an Automations tab over the Plan 19 backend:
- a list of automations;
- a React Flow graph editor with an inspector;
- Run now and Test;
- the live run view (the same graph lit with each step's state);
- versions with a structured diff;
- grants, the Turn-on dialog, and Attention cards.

Everything that is not React goes into the shared packages, so Plan 21 can port the screens to the Angular web UI.

**Architecture:**
- **Two UIs.**
  - From Plan 17 on, a UI feature ships in both the Electron renderer and the Angular web UI (CLAUDE.md, "Two UIs"). Plan 20 builds the shared parts and the React screens; Plan 21 builds the Angular screens.
  - Both plans run on one branch, `automations-ui`, which merges to master only after Plan 21.
  - Until then, the web UI's parity guard (`apps/web-ui/src/app/parity.spec.ts`) has a branch-only exemption for the automations operations, and the web UI shows a placeholder screen on the new tab. Plan 21 removes both.
- **Shared code.**
  - Operations go in `@desk/bff`: the contract in `packages/bff/src/contract/`, and the handlers in `packages/bff/src/server/handlers.ts`.
  - All logic with no framework goes in `@desk/ui-core` (`packages/ui-core/src/automation-*.ts`): formats, schedules, grants, run inputs, live-refresh rules, the graph model (dagre layout), draft operations, validation-issue mapping, template suggestions, the version diff, and run looks.
  - Styles go in `@desk/ui-styles` (`packages/ui-styles/src/automations.css`).
- **Transport.**
  - The renderer calls through `call()` in `renderer/bridge.ts`. A new `automations.*` namespace mirrors `DeskClient.automations`, and each input is validated by its schema in `dispatch`.
  - Two new app ops: `app.pickFile` (file inputs) and `app.revealPath` ("Open folder", confined to run folders).
  - The Electron host (`apps/desktop/src/main/index.ts`) and the desk web host (`apps/web-server/src/web-context.ts`) each implement them.
- **Live data.**
  - A small React hook, `useLive`, keeps a daemon snapshot (list, detail, runs, one run) current from the project session's event log.
  - Events after the snapshot fold in at once through the Plan 19 reducer (`reduceAutomations`). Events the shared rules pick reload the snapshot (debounced), so derived fields catch up.
- **Editor.**
  - The Design view edits a local draft with the pure operations from `automation-draft.ts`.
  - `toGraph` turns the draft into framework-neutral nodes and edges, and the desktop's `GraphCanvas` feeds them to React Flow.
  - The validate endpoint marks nodes red (debounced 400 ms). Save sends `base_version`, and a 409 opens the conflict dialog.
- **Run view.** The same canvas, read-only and lit from the run's step states, with the selected step's panel on the right.
- **Backend.** Three small additions the app needs:
  - `RunInfo.number` / `at_step` (the "#" and "running · <step>" columns);
  - a Stop step route;
  - `automation.grants_set.source`, so the Grants tab can say where a remembered grant came from.

**Tech Stack:**
- Electron + React 19, TypeScript (strict, `noUncheckedIndexedAccess`), zod 4.
- New dependencies:
  - `@xyflow/react` 12 (MIT) in `apps/desktop`;
  - `@dagrejs/dagre` 3 (MIT, types included) in `packages/ui-core`.
- Vitest (node) for `ui-core` and `bff`; Vitest + Testing Library (jsdom) for components; Playwright-for-Electron for the e2e test.

**Spec:** `docs/superpowers/specs/2026-09-26-automations-design.md`.
- Before starting, read §8 (desktop) and open the two HTML mockups in `docs/superpowers/specs/2026-09-26-automations-mockups/` in a browser. Read §2–§5 for what the fields mean.
- Plan 19 (`docs/superpowers/plans/2026-09-26-plan-19-automations-backend.md`) built the API, the client methods and `packages/client/src/state/automations.ts`.
- CLAUDE.md describes `@desk/bff`, `@desk/ui-core`, `@desk/ui-styles` and the parity guard.

## Global Constraints

- **Branch:**
  - Work on `automations-ui`, created from master: `git switch -c automations-ui`, the first time.
  - Plan 21 continues on the same branch, and the branch merges to master only after Plan 21.
- **Route:** `#/p/<id>/automations`, then optionally:
  - `/<aid>` (Design);
  - `/<aid>/runs[/<rid>]`;
  - `/<aid>/versions`;
  - `/<aid>/grants`.

  A local draft is `#/p/<id>/automations/new?name=<name>`. The tab sits between Threads and Library. Routes live in `@desk/ui-core` (`router.ts`).
- **Operations:**
  - Every operation is declared in `packages/bff/src/contract/ipc.ts`, with its result in `contract/types.ts` (`ChannelOutputs`) and a handler in `server/handlers.ts`. `handlers.test.ts` fails on a channel without a handler, or on a handler whose result differs from the declared one.
  - Components call operations by their literal name (`call('automations.get', { id })`), never a variable or a template string, so the parity guard can read them.
  - Neither UI ever sees the daemon token.
  - File and folder inputs reach deskd as local paths picked with `app.pickFile` / `app.pickFolder`.
  - `app.revealPath` opens only `<data>/automation-runs/<run id>`, or its `steps/<step id>`.
- **`@desk/ui-core`:**
  - One flat directory. Each module is named `automation-*.ts`, is exported from `src/index.ts` as `export * from './<module>';`, and imports no React, DOM, `window`, `document` or `node:*` (`boundaries.test.ts`).
  - Exported names must not collide with other modules' exports: this plan prefixes the generic ones (`GraphSelection`, `AutomationDoc`, `RunTone`, `SchedulePreset`, `TemplateSuggestion`…).
  - Test fixtures live in `src/testing/automations.ts`, exported as `@desk/ui-core/testing`.
- **Styles:**
  - Every rule goes in `packages/ui-styles/src/automations.css`, imported once by `index.css` (`index.test.ts` lists the sheets).
  - Renderer modules import no CSS: `main.tsx` loads `@desk/ui-styles/index.css`, and this plan adds React Flow's stylesheet there (`renderer/styles.test.ts` lists both).
  - No color literal anywhere in the renderer, `ui-core` or `ui-styles` except `tokens.css` (`ui-styles/src/tokens.test.ts` fails on `#abc`, `#aabbcc` or `rgb(`).
  - Kind colors: script `var(--text)`, agent `var(--run)`, Ask me `var(--wait)`, Wait `var(--muted)`, Run automation `var(--ok)`, Tell Desk `var(--accent)`.
  - Run states: done `--ok`, running `--run` with a `--run-ring` outline, waiting `--wait`, pending dimmed, skipped hatched (`--card`/`--sunken`), failed `--accent`.
- **Parity guard:**
  - `apps/web-ui/src/app/parity.spec.ts` gets `PLAN_21_OPS`: `automations.*`, `app.pickFile` and `app.revealPath` are left out of "calls every operation the React renderer calls".
  - The web UI shows `AutomationsPlaceholder` for the tab. Plan 21 deletes both.
- **Agent text** (summaries, errors, questions, transcripts) renders as plain text or through `SafeMarkdown`, never as HTML.
- **User-only actions.**
  - Only an explicit user click turns an automation on or off, sets grants, or answers a step. Nothing turns anything on automatically.
  - The Turn-on dialog sets grants (`reason: 'enabled'`) and then the switch.
  - *Approve and remember* is never offered while `grants_suspended` is true.
- **Names:** check with the protocol's schemas (`AutomationName`, `StepId`, `InputKey`, `RouteName`), never with hand-written regexes in components.
- **Timings:** validation debounce 400 ms, layout autosave debounce 800 ms, live refetch debounce 300 ms. Script logs are polled every 2 s while their step runs.
- **Layout:** the Start pill's key in `AutomationLayout` is `__start`, because a step may be called `start`. Node size 200×64, Start pill 240×44.
- **Visibility:** step agents never appear in the Threads tab. Their transcript opens from the run view only.
- **Commits.**
  - `pnpm test` (Vitest, then the web UI's specs, parity guard included) and `pnpm typecheck` (tsc, then ngc) pass before every commit.
  - Stage only your own files, by path: `~/desk` is a shared checkout, so never `git add -A` or `git add .`.
  - End each commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Machine:** at most one heavy process at a time on this 8 GB machine. Run single test files while iterating, and the full suite before committing.

## File Map

```
packages/protocol/src/automations.ts        RunInfo.number, RunInfo.at_step, RunSummary.number; layout key doc
packages/protocol/src/events.ts              automation.grants_set gains source?: {run_id, step_id}
packages/core/src/automations/queries.ts     runNumber()
packages/core/src/automations/views.ts       atStep(); runInfo(db, row) with number/at_step
packages/core/src/automations/service.ts     setGrants(..., source?)
packages/core/src/automations/engine.ts      stopStep(); script.remember(..., source)
packages/core/src/runtime/runtime.ts         remembered grants carry their source
apps/daemon/src/routes/automations.ts        POST /automation-runs/:rid/steps/:sid/stop
packages/client/src/client.ts                automations.stopStep
packages/client/src/state/automations.ts     last_run.number on run_started

packages/bff/src/contract/ipc.ts             automations.* channels, app.pickFile, app.revealPath, approvals.resolve remember
packages/bff/src/contract/types.ts           their ChannelOutputs
packages/bff/src/server/handlers.ts          their handlers; HandlerContext.app gains pickFile, revealRun
packages/bff/src/server/run-folder.ts        runFolder(), existingRunFolder()
apps/desktop/src/main/index.ts               Electron pickFile, revealRun
apps/web-server/src/web-context.ts           desk web pickFile (not offered), revealRun
apps/web-ui/src/app/parity.spec.ts           PLAN_21_OPS (branch only)
apps/web-ui/src/app/components/project-nav.ts, screen-for.ts, automations/automations-placeholder.ts

packages/ui-core/package.json                + @dagrejs/dagre; exports ./testing
packages/ui-core/src/router.ts               'automations' tab and its route fields
packages/ui-core/src/testing/automations.ts  fixtures (@desk/ui-core/testing)
packages/ui-core/src/
  automation-format.ts     STEP_KIND_LABEL, versionBadge, listNote, originText, triggerText, runStatusText, lastRunText, skippedText, runTook, dayTime
  automation-schedules.ts  SchedulePreset, presetOf, cronOf, describeSchedule, whenText, systemTimezone
  automation-grants.ts     grantKey, sameGrant, uniqueGrants, describeGrant, widenDomain, grantOrigins; the Grants tab's grantDraft, grantFromDraft, widenedGrants, replaceGrant, grantOriginText
  automation-inputs.ts     initialValues, runInputs
  automation-live.ts       eventsAfter, reduceAutomationList, reduceRunDetail, the refetch rules
  automation-graph.ts      START_ID, sizes, GraphSelection, ancestors, startSteps, labels, autoLayout, ensureLayout, toGraph
  automation-draft.ts      AutomationDoc and its operations
  automation-issues.ts     mapIssues
  automation-templates.ts  templateSuggestions, conditionSuggestions, activeToken, filterSuggestions, insertSuggestion, pathsIn, isKnownPath
  automation-fields.ts     skillChoices, scriptFiles, routeProblem, keyProblem, edgeRouteOptions
  automation-start.ts      INPUT_TYPE_LABEL, AFTER_RUN_LABEL, newSchedule, newInput, switchPreset, timezones, patchInput, retypeInput, inputValueOf
  automation-diff.ts       diffDefinitions
  automation-run-graph.ts  stepLook, runGraph, firedEdges, agentActivity, focusStep, askDeskText, canStopStep, relRunPath, agentTokens, outputText, runFailure
  strips.ts                stripWho names automations; automationTarget
packages/ui-styles/src/automations.css, index.css, index.test.ts

apps/desktop/package.json                    + @xyflow/react
apps/desktop/src/renderer/main.tsx           React Flow's stylesheet
apps/desktop/src/renderer/App.tsx            the Automations screen
apps/desktop/src/renderer/components/ProjectNav.tsx
apps/desktop/src/renderer/conversation/draft.ts        primeDraft() (Describe one to Desk)
apps/desktop/src/renderer/test/reactflow.ts            jsdom shims for React Flow
apps/desktop/src/renderer/test/session.ts              sessionOf(): a SessionState from events, for component tests
apps/desktop/src/renderer/automations/
  live.ts            useLive(): snapshot + event fold + debounced refetch
  data.ts            useAutomationList, useAutomation, useRuns, useRun
  AutomationsScreen.tsx   list | one automation (header + view) | draft
  AutomationList.tsx, AutomationHeader.tsx
  dialogs/RunDialog.tsx, TurnOnDialog.tsx, NameDialog.tsx
  design/flow.ts           React Flow types for the ui-core graph
  design/nodes.tsx         StepNode, StartNode, StubNode, RouteEdge
  design/GraphCanvas.tsx   the React Flow canvas (edit and read-only)
  design/TemplateField.tsx, ListEditor.tsx
  design/StepInspector.tsx, StepKindFields.tsx, EdgeInspector.tsx, StartInspector.tsx, SettingsInspector.tsx
  design/pickers.ts        useSkillChoices, useSkillScripts, useAutomationNames, useAutomationInputs
  design/DesignView.tsx, ConflictDialog.tsx
  versions/DiffView.tsx, VersionsView.tsx
  runs/RunsList.tsx, RunView.tsx, StepPanel.tsx, StepTranscript.tsx, RunFileList.tsx
  grants/GrantsView.tsx
apps/desktop/src/renderer/attention/AutomationCards.tsx   Attention cards for the four kinds, useAutomationItem
apps/desktop/src/renderer/attention/Inspector.tsx, AttentionScreen.tsx   step approvals (remember), automation targets, the legend
apps/desktop/e2e/automations.e2e.test.ts
docs/api.md, docs/desktop.md, CLAUDE.md, the automations spec status line
```

---

### Task 1: Backend additions the app needs

Start the branch first: `git switch -c automations-ui` (Global Constraints).

The run list's "#" and "running · <step>" columns need a run number and the step a run is at. The run panel's **Stop step** needs a route (`/threads/:id/stop` refuses step agents). The Grants tab needs to know which run and step a remembered grant came from.

**Files:**
- Modify: `packages/protocol/src/automations.ts` (`RunSummary`, `RunInfo`, `AutomationLayout` doc)
- Modify: `packages/protocol/src/events.ts` (`automation.grants_set`)
- Modify: `packages/core/src/automations/queries.ts` (add `runNumber`)
- Modify: `packages/core/src/automations/views.ts` (`atStep`, `runSummary`, `runInfo`, `runDetail`)
- Modify: `packages/core/src/automations/service.ts` (`setGrants`)
- Modify: `packages/core/src/automations/engine.ts` (`ScriptHost.remember`, `answerGate`, new `stopStep`)
- Modify: `packages/core/src/runtime/runtime.ts` (the `remember` host function, `resolveApproval`)
- Modify: `apps/daemon/src/routes/automations.ts` (runs list, stop route)
- Modify: `packages/client/src/client.ts` (`automations.stopStep`)
- Modify: `packages/client/src/state/automations.ts` (`run_started`)
- Modify: `docs/api.md`
- Test: `packages/core/src/automations/service.test.ts`, `packages/core/src/automations/agent-step.test.ts`, `packages/core/src/automations/e2e.test.ts`, `apps/daemon/src/routes/automations.test.ts`, `packages/client/src/client.test.ts`, `packages/client/src/state/automations.test.ts`, `apps/cli/src/format.test.ts`

**Interfaces:**
- Produces:
  - `RunInfo.number: number` (1 = the automation's oldest run), `RunInfo.at_step: string | null` (a step title), `RunSummary.number: number`.
  - `runNumber(db, run): number`, `atStep(db, run): string | null`, and `runInfo(db, row)`, which now takes `db` first.
  - `AutomationEngine.stopStep(runId, stepId): void` throws `NotFoundError` for an unknown step, and `ConflictError` unless the step is an unfinished agent step of a running run.
  - `POST /v1/automation-runs/:rid/steps/:sid/stop` → `{ ok: true }`.
  - `DeskClient.automations.stopStep(runId, stepId): Promise<{ ok: true }>`.
  - `automation.grants_set` payload `source?: { run_id: string; step_id: string }`, set on `remembered` grants.

- [ ] **Step 1: Write the failing tests**

Add to `packages/core/src/automations/service.test.ts`, inside `describe('Automations service', …)` after the last test:

```ts
  it('numbers runs, oldest first, and names the step a run is at', async () => {
    await setup();
    const { automation } = rt.automations.create(
      projectId,
      'ask',
      { title: 'Ask', steps: [{ id: 'a', title: 'Ask', kind: 'ask', question: 'OK?' }, { id: 'w', title: 'Wait', kind: 'wait', minutes: 1 }], edges: [{ from: 'a', to: 'w' }] },
      { origin: 'user', via: 'editor' },
    );
    const start = (run_id: string) =>
      h.store.append({ project_id: projectId, agent_id: null, type: 'automation.run_started', payload: { run_id, automation_id: automation.id, version: 1, trigger: 'manual', test: false, inputs: {}, by: 'user', deadline_at: '2026-09-29T06:00:00.000Z' } });
    start('r1');
    h.store.append({ project_id: projectId, agent_id: null, type: 'automation.step_changed', payload: { run_id: 'r1', step_id: 'a', attempt: 1, status: 'waiting', question: { text: 'OK?', files: [] } } });
    start('r2');
    h.store.append({ project_id: projectId, agent_id: null, type: 'automation.step_changed', payload: { run_id: 'r2', step_id: 'a', attempt: 1, status: 'failed', error: 'boom' } });
    h.store.append({ project_id: projectId, agent_id: null, type: 'automation.run_finished', payload: { run_id: 'r2', status: 'failed', summary: 'Failed', reason: 'boom' } });
    expect(runDetail(h.store.db, 'r1')).toMatchObject({ number: 1, status: 'waiting', at_step: 'Ask' });
    expect(runDetail(h.store.db, 'r2')).toMatchObject({ number: 2, status: 'failed', at_step: 'Ask' });
    expect(automationSummary(h.store.db, getAutomation(h.store.db, automation.id)!, clock.now()).last_run).toMatchObject({ id: 'r2', number: 2 });
  });
```

In `packages/core/src/automations/agent-step.test.ts`, in the test 'asks the user (never Desk) through grants and policy, and remembers an approval', right after `await rt.resolveApproval(ap!.id, 'approved', { remember: true });`, add:

```ts
    expect(h.store.list({ projectId, types: ['automation.grants_set'] }).at(-1)!.payload).toMatchObject({ reason: 'remembered', source: { run_id: r, step_id: 'sum' } });
```

In `packages/core/src/automations/e2e.test.ts`, right after `await rt.engine.answer(run!.id, 'count', { decision: 'approve', remember: true });`, add:

```ts
    expect(h.store.list({ projectId, types: ['automation.grants_set'] }).at(-1)!.payload).toMatchObject({ reason: 'remembered', source: { run_id: run!.id, step_id: 'count' } });
```

Add to `apps/daemon/src/routes/automations.test.ts`, inside `describe('automation routes', …)`:

```ts
  it('stops a running agent step: its agent stops and the step fails', async () => {
    const { api, projectId, rt } = await setup();
    const id = (await api('POST', `/projects/${projectId}/automations`, { name: 'echo', definition: { title: 'Echo', steps: [{ id: 'say', title: 'Say hi', kind: 'agent', brief: 'Say hi.' }] } })).body.automation.id;
    const runId = (await api('POST', `/automations/${id}/runs`, { inputs: {} })).body.run_id;
    await rt.whenIdle();
    // The step agent waits on its bash approval, so the run waits on the user.
    expect((await api('GET', `/automation-runs/${runId}`)).body).toMatchObject({ number: 1, status: 'waiting', at_step: 'Say hi' });
    expect((await api('POST', `/automation-runs/${runId}/steps/say/stop`)).status).toBe(200);
    await rt.whenIdle();
    const after = (await api('GET', `/automation-runs/${runId}`)).body;
    expect(after).toMatchObject({ status: 'failed', at_step: 'Say hi' });
    expect(after.steps[0]).toMatchObject({ step_id: 'say', status: 'failed', error: 'Stopped by the user' });
    expect((await api('POST', `/automation-runs/${runId}/steps/say/stop`)).status).toBe(409);
    expect((await api('POST', `/automation-runs/${runId}/steps/nope/stop`)).status).toBe(404);
    const [entry] = (await api('GET', `/automations/${id}/runs`)).body;
    expect(entry.run).toMatchObject({ number: 1, status: 'failed', at_step: 'Say hi' });
  });
```

In `packages/client/src/client.test.ts`, in the automations test, right after the line `expect((await client.automations.runs(automation.id)).map((e) => (e.kind === 'run' ? e.run.status : e.kind))).toEqual(['succeeded']);`, add:

```ts
    expect((await client.automations.runs(automation.id))[0]).toMatchObject({ kind: 'run', run: { number: 1, at_step: null } });
    await expect(client.automations.stopStep(run_id, 'ask')).rejects.toMatchObject({ status: 409 });
```

In `packages/client/src/state/automations.test.ts`, change the assertion after the `automation.run_started` event from

```ts
    expect(s.list[0]!.last_run).toMatchObject({ id: 'r1', status: 'running', trigger: 'manual', test: false, finished_at: null });
```

to

```ts
    expect(s.list[0]!.last_run).toMatchObject({ id: 'r1', number: 1, status: 'running', trigger: 'manual', test: false, finished_at: null });
```

In `apps/cli/src/format.test.ts`, the `last_run` literal (around line 125) gains the new field:

```ts
      last_run: { id: 'r', number: 3, status: 'failed', trigger: 'schedule', test: false, started_at: '2026-09-28T06:00:00.000Z', finished_at: null, summary: null, waiting_on: null },
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm vitest run packages/core/src/automations/service.test.ts apps/daemon/src/routes/automations.test.ts packages/client/src/state/automations.test.ts`
Expected: FAIL:
- the service test: `number` and `at_step` are undefined;
- the daemon test: 404 for the stop route;
- the reducer test: `number` is missing.

- [ ] **Step 3: Protocol**

In `packages/protocol/src/automations.ts`, change `RunSummary` to start:

```ts
export type RunSummary = {
  id: string;
  /** Its number among the automation's runs, oldest first (the app's "#"). */
  number: number;
  status: RunStatus;
```

and `RunInfo` to:

```ts
export type RunInfo = {
  id: string;
  /** Its number among the automation's runs, oldest first. */
  number: number;
  automation_id: string;
  project_id: string;
  version: number;
  trigger: RunTrigger;
  test: boolean;
  inputs: Record<string, InputValue>;
  by: string;
  parent_run_id: string | null;
  parent_step_id: string | null;
  due_at: string | null;
  caught_up: number;
  status: RunStatus;
  /** The title of the step a running run is at (running first, then waiting on the user), or of the step a failed run failed at. */
  at_step: string | null;
  summary: string | null;
  reason: string | null;
  started_at: string;
  finished_at: string | null;
  deadline_at: string;
};
```

Change the `AutomationLayout` doc comment to:

```ts
/** Editor positions by step id, plus `__start` for the Start pill (a step may be called `start`). Not versioned. */
```

In `packages/protocol/src/events.ts`, replace the `automation.grants_set` line with:

```ts
  event(
    'automation.grants_set',
    z.object({
      automation_id: z.string(),
      grants: z.array(Grant).max(100),
      reason: z.enum(['edited', 'remembered', 'kept', 'enabled']),
      /** The run and step whose approval a `remembered` grant came from. */
      source: z.object({ run_id: z.string(), step_id: z.string() }).optional(),
    }),
  ),
```

- [ ] **Step 4: Core: run numbers and the current step**

In `packages/core/src/automations/queries.ts`, change the drizzle import to `import { and, asc, count, desc, eq, inArray, isNull, lt, lte, ne, or } from 'drizzle-orm';` and add after `listRuns`:

```ts
/** A run's number among its automation's runs, oldest first (runs started at the same time go by id). */
export const runNumber = (db: Db, run: Pick<AutomationRunRow, 'automation_id' | 'started_at' | 'id'>): number =>
  db
    .select({ n: count() })
    .from(automationRuns)
    .where(
      and(
        eq(automationRuns.automation_id, run.automation_id),
        or(lt(automationRuns.started_at, run.started_at), and(eq(automationRuns.started_at, run.started_at), lte(automationRuns.id, run.id))),
      ),
    )
    .get()?.n ?? 0;
```

In `packages/core/src/automations/views.ts`, import `runNumber` from `./queries` with the others. Add after `waitingOn`:

```ts
/** The title of the step a run is at: a running run's running step (else the one waiting on the user), or a failed run's failing step. */
export function atStep(db: Db, run: AutomationRunRow): string | null {
  if (run.status !== 'running' && run.status !== 'failed') return null;
  const rows = stepRuns(db, run.id);
  const row =
    run.status === 'running'
      ? (rows.find((r) => effectiveStepStatus(db, r) === 'running') ?? rows.find((r) => waitsOnUser(db, r)))
      : [...rows].reverse().find((r) => r.status === 'failed' && r.route !== 'error');
  if (!row) return null;
  return getVersion(db, run.automation_id, run.version)?.definition.steps.find((s) => s.id === row.step_id)?.title ?? row.step_id;
}
```

In `runSummary`, add `number: runNumber(db, run),` after `id: run.id,`. Change `runInfo` to take the database:

```ts
export function runInfo(db: Db, row: AutomationRunRow): RunInfo {
  return {
    id: row.id,
    number: runNumber(db, row),
    automation_id: row.automation_id,
    project_id: row.project_id,
    version: row.version,
    trigger: row.trigger,
    test: row.test,
    inputs: row.inputs,
    by: row.by,
    parent_run_id: row.parent_run_id,
    parent_step_id: row.parent_step_id,
    due_at: row.due_at,
    caught_up: row.caught_up,
    status: row.status,
    at_step: atStep(db, row),
    summary: row.summary,
    reason: row.reason,
    started_at: row.started_at,
    finished_at: row.finished_at,
    deadline_at: row.deadline_at,
  };
}
```

In `runDetail`, change `...runInfo(run)` to `...runInfo(db, run)`. In `apps/daemon/src/routes/automations.ts`, change `run: { ...runInfo(run), status: effectiveRunStatus(db, run) }` to `run: { ...runInfo(db, run), status: effectiveRunStatus(db, run) }`.

- [ ] **Step 5: Core: the source of a remembered grant**

In `packages/core/src/automations/service.ts`, replace `setGrants`:

```ts
  setGrants(automationId: string, grants: Grant[], reason: 'edited' | 'remembered' | 'kept' | 'enabled', source?: { run_id: string; step_id: string }): AutomationRow {
    const a = this.require(automationId);
    const list = z.array(Grant).max(100).parse(grants);
    this.host.store.append({ project_id: a.project_id, agent_id: null, type: 'automation.grants_set', payload: { automation_id: a.id, grants: list, reason, ...(source ? { source } : {}) } });
    return getAutomation(this.db, a.id)!;
  }
```

In `packages/core/src/automations/engine.ts`, change the `remember` member of the script host type to:

```ts
  /** Adds a grant the user remembered (reason `remembered`), with the run and step it came from. */
  remember(automationId: string, grant: Grant, source: { run_id: string; step_id: string }): void;
```

In `answerGate`, change the remember call to:

```ts
      this.host.script.remember(automation.id, deriveGrant(gate.tool, { command: gate.subject }, automation.name), { run_id: run.id, step_id: row.step_id });
```

In `packages/core/src/runtime/runtime.ts`, change the host's `remember` to:

```ts
        remember: (automationId, grant, source) => {
          const a = this.automations.require(automationId);
          this.automations.setGrants(a.id, addGrant(a.grants, grant), 'remembered', source);
        },
```

and in `resolveApproval` change `this.automations.setGrants(automation.id, addGrant(automation.grants, grant.data), 'remembered');` to:

```ts
      this.automations.setGrants(automation.id, addGrant(automation.grants, grant.data), 'remembered', { run_id: link.run.id, step_id: link.step.step_id });
```

- [ ] **Step 6: Core + daemon + client: Stop step**

In `packages/core/src/automations/engine.ts`, add after `answer(…)`:

```ts
  /** The user's Stop step (spec §8.3): stops a running agent step's agent, and the step fails. */
  stopStep(runId: string, stepId: string): void {
    const run = getRun(this.db, runId);
    const row = run ? getStepRun(this.db, runId, stepId) : undefined;
    if (!run || !row) throw new NotFoundError(`Unknown step: ${runId}/${stepId}`);
    if (run.status !== 'running' || !row.agent_id || TERMINAL_STEP.has(row.status)) throw new ConflictError('Only a running agent step can be stopped');
    this.host.agent.stop(row.agent_id, 'Stopped by the user');
    this.resolveStep(runId, stepId, { status: 'failed', error: 'Stopped by the user' });
  }
```

In `apps/daemon/src/routes/automations.ts`, add after the `/automation-runs/:rid/steps/:sid/answer` route:

```ts
  r.post('/automation-runs/:rid/steps/:sid/stop', (c) => {
    engine.stopStep(requireRun(c.req.param('rid')).id, c.req.param('sid'));
    return c.json({ ok: true });
  });
```

In `packages/client/src/client.ts`, add to `automations` after `answer`:

```ts
    stopStep: (runId: string, stepId: string) => this.post<{ ok: true }>(`/automation-runs/${enc(runId)}/steps/${enc(stepId)}/stop`),
```

In `packages/client/src/state/automations.ts`, in the `automation.run_started` case, give the new last run its number:

```ts
    case 'automation.run_started': {
      const p = e.payload;
      return withSummary(s, p.automation_id, (a) => ({
        ...a,
        last_run: { id: p.run_id, number: (a.last_run?.number ?? 0) + 1, status: 'running', trigger: p.trigger, test: p.test, started_at: e.ts, finished_at: null, summary: null, waiting_on: null },
      }));
    }
```

- [ ] **Step 7: Document the API**

In `docs/api.md`:
- In the `GET /v1/automations/:aid/runs` row, after `{ kind: 'run', run }`, add: ` (with \`number\`, 1 = the oldest, and \`at_step\`: the step title a running run is at, or where a failed run failed)`.
- Add this row after the `…/steps/:sid/answer` row:

```
| POST | `/v1/automation-runs/:rid/steps/:sid/stop` | | Stops a running agent step's agent; the step fails with "Stopped by the user". 409 unless it is an unfinished agent step of a running run |
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pnpm vitest run packages/core/src/automations apps/daemon/src/routes/automations.test.ts packages/client/src apps/cli/src/format.test.ts`
Expected: PASS.

Run: `pnpm typecheck`
Expected: no errors. If a `RunInfo` or `RunSummary` fixture typed without a cast fails, add `number` (and `at_step: null` for `RunInfo`) to it. `apps/cli/src/format.test.ts` is the only known one.

- [ ] **Step 9: Commit**

```bash
pnpm test
git add packages/protocol/src/automations.ts packages/protocol/src/events.ts packages/core/src/automations/queries.ts packages/core/src/automations/views.ts packages/core/src/automations/service.ts packages/core/src/automations/engine.ts packages/core/src/runtime/runtime.ts apps/daemon/src/routes/automations.ts packages/client/src/client.ts packages/client/src/state/automations.ts docs/api.md packages/core/src/automations/service.test.ts packages/core/src/automations/agent-step.test.ts packages/core/src/automations/e2e.test.ts apps/daemon/src/routes/automations.test.ts packages/client/src/client.test.ts packages/client/src/state/automations.test.ts apps/cli/src/format.test.ts
git commit -m "feat(automations): run numbers and current step, Stop step, the source of remembered grants

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The automations operations, file picking and Open folder (both hosts)

**Files:**
- Modify: `packages/bff/src/contract/ipc.ts`, `packages/bff/src/contract/types.ts`, `packages/bff/src/contract/types.test.ts`
- Modify: `packages/bff/src/server/handlers.ts`, `packages/bff/src/server/index.ts`, `packages/bff/src/server/handlers.test.ts`
- Create: `packages/bff/src/server/run-folder.ts`, `packages/bff/src/server/run-folder.test.ts`
- Modify: `apps/desktop/src/main/index.ts`
- Modify: `apps/web-server/src/web-context.ts`, `apps/web-server/src/web-context.test.ts`
- Modify: `apps/web-ui/src/app/parity.spec.ts`

**Interfaces:**
- Consumes: `DeskClient.automations.*` (Plan 19, plus `stopStep` from Task 1), `DeskClient.approvals.resolve(id, decision, note?, remember?)`.
- Produces: these operations, used by every later task as `call('<channel>', input)`:

  | Channel | Input | Output |
  |---|---|---|
  | `automations.list` | `{ projectId }` | `AutomationSummary[]` |
  | `automations.create` | `{ projectId, req: AutomationCreateRequest }` | `{ automation: AutomationDetail, warnings }` |
  | `automations.validate` | `{ projectId, req: { definition, name? } }` | `{ errors, warnings, next_times }` |
  | `automations.import` | `{ projectId, exp: AutomationExport }` | `{ automation, warnings }` |
  | `automations.get` | `{ id }` | `AutomationDetail` |
  | `automations.save` | `{ id, req: { definition, base_version, change_note?, via? } }` | `{ automation, warnings }` (409 on a stale base) |
  | `automations.remove` | `{ id }` | `{ ok: true }` |
  | `automations.layout` | `{ id, layout }` | `{ ok: true }` |
  | `automations.versions` | `{ id }` | `AutomationVersionInfo[]` |
  | `automations.version` | `{ id, version }` | `{ version, definition, origin, change_note, via, created_at }` |
  | `automations.restore` | `{ id, version }` | `AutomationDetail` |
  | `automations.setEnabled` | `{ id, enabled }` | `AutomationDetail` |
  | `automations.setGrants` | `{ id, grants, reason?: 'edited' \| 'enabled' }` | `AutomationDetail` |
  | `automations.keepGrants` | `{ id }` | `AutomationDetail` |
  | `automations.export` | `{ id }` | `AutomationExport` |
  | `automations.run` | `{ id, req: { inputs?, test? } }` | `{ run_id }` |
  | `automations.runs` | `{ id, before?, limit? }` | `RunListEntry[]` |
  | `automations.getRun` | `{ runId }` | `RunDetail` |
  | `automations.cancelRun` | `{ runId }` | `{ ok: true }` |
  | `automations.answer` | `{ runId, stepId, req: { decision, note?, remember? } }` | `{ ok: true }` |
  | `automations.stopStep` | `{ runId, stepId }` | `{ ok: true }` |
  | `automations.log` | `{ runId, stepId }` | `string` |
  | `automations.files` | `{ runId, path? }` | `WorkspaceEntry[]` |
  | `automations.file` | `{ runId, path }` | `Uint8Array` |
  | `approvals.resolve` | gains `remember?: boolean` | |
  | `app.pickFolder` | `purpose` gains `'automation-input'` | `string \| null` |
  | `app.pickFile` | `{ purpose: 'automation-input' }` | `string \| null` (desk web: `not_offered`) |
  | `app.revealPath` | `{ runId, stepId? }` (run id: a ULID) | `{ ok: true }` (`folder_missing` when gone) |

  - `runFolder(dataDir, runId, stepId?): string` and `existingRunFolder(dataDir, runId, stepId?): Promise<string>`, from `@desk/bff/server`.
  - `HandlerContext.app` gains `pickFile(purpose)` and `revealRun(runId, stepId?)`.
  - `PLAN_21_OPS` in the parity guard.

- [ ] **Step 1: Write the failing tests**

In `packages/bff/src/server/handlers.test.ts`, first move the context literal out of `setup()` into a helper, so the automation test can build one around its own harness. Replace `setup` with:

```ts
function makeCtx(client: DeskClient, overrides: Partial<HandlerContext> = {}) {
  const opened: string[] = [];
  const watched: Array<[number, string, number]> = [];
  const ctx: HandlerContext = {
    senderId: 7,
    client: () => client,
    broker: { snapshot: () => initialGlobalState(), watch: async (s, p, a) => void watched.push([s, p, a]), unwatch: () => {} },
    daemon: { status: vi.fn(), start: vi.fn(), restart: vi.fn(), stop: vi.fn(), repair: vi.fn() },
    app: {
      info: () => ({ version: '1.0.0', platform: 'darwin', packaged: false, dataDir: '/d' }),
      openExternal: async (url) => void opened.push(url),
      pickFolder: async () => '/picked',
      pickFile: async () => '/picked/file.txt',
      revealLogs: async () => {},
      revealRun: async (runId, stepId) => void opened.push(`reveal:${runId}:${stepId ?? ''}`),
      saveFile: async () => true,
      openMain: (route) => void opened.push(`main:${route ?? ''}`),
      settings: () => ({ notifications: true, appearance: 'system' }),
      updateSettings: (p) => ({ notifications: p.notifications ?? true, appearance: p.appearance ?? 'system' }),
    },
    ...overrides,
  };
  return { ctx, opened, watched };
}

async function setup(overrides: Partial<HandlerContext> = {}, withCatalog = false) {
  h = await createHarness();
  const runtime = newRuntime(h, { builtins: { root: BUILTINS } });
  const extra = withCatalog ? builtinCatalog(runtime) : {};
  server = await startServer({ app: createApp({ runtime, store: h.store, models: h.models, token: 't', version: '1.0.0', ...extra }), store: h.store, token: 't', port: 0 });
  const client = new DeskClient({ baseUrl: `http://127.0.0.1:${server.port}`, token: 't' });
  return { ...makeCtx(client, overrides), runtime };
}
```

Change the harness import to `import { automationHarness, askDef, createHarness, encodePng, newRuntime, type Harness } from '@desk/core/testing';`.

Then add these tests inside `describe('IPC dispatch', …)`:

```ts
  it('drives automations: create, validate, save (409 on a stale base), lay out, run, answer, list runs, files, grants, export, delete', async () => {
    const r = await automationHarness();
    h = r.h;
    server = await startServer({ app: createApp({ runtime: r.rt, store: h.store, models: h.models, token: 't', version: '1.0.0' }), store: h.store, token: 't', port: 0 });
    const { ctx } = makeCtx(new DeskClient({ baseUrl: `http://127.0.0.1:${server.port}`, token: 't' }));
    const projectId = r.projectId;

    const created = await dispatch('automations.create', { projectId, req: { name: 'ask', definition: askDef(), via: 'editor' } }, ctx);
    expect(created).toMatchObject({ ok: true, value: { automation: { name: 'ask', version: 1 } } });
    const id = (created as { value: { automation: { id: string } } }).value.automation.id;
    expect(await dispatch('automations.list', { projectId }, ctx)).toMatchObject({ ok: true, value: [{ id, name: 'ask' }] });
    expect(await dispatch('automations.validate', { projectId, req: { definition: { title: 'x', steps: [] } } }, ctx)).toMatchObject({ ok: true, value: { errors: [expect.objectContaining({ path: 'steps' })] } });
    expect(await dispatch('automations.save', { id, req: { definition: askDef({ title: 'Ask 2' }), base_version: 1, via: 'editor' } }, ctx)).toMatchObject({ ok: true, value: { automation: { version: 2 } } });
    expect(await dispatch('automations.save', { id, req: { definition: askDef(), base_version: 1 } }, ctx)).toMatchObject({ ok: false, error: { status: 409 } });
    expect(await dispatch('automations.layout', { id, layout: { __start: { x: 0, y: 0 }, ask: { x: 10, y: 90 } } }, ctx)).toMatchObject({ ok: true });
    expect(await dispatch('automations.get', { id }, ctx)).toMatchObject({ ok: true, value: { layout: { ask: { x: 10, y: 90 } } } });
    expect(await dispatch('automations.versions', { id }, ctx)).toMatchObject({ ok: true, value: [{ version: 2 }, { version: 1 }] });
    expect(await dispatch('automations.version', { id, version: 1 }, ctx)).toMatchObject({ ok: true, value: { definition: { title: 'Ask then tell' } } });

    const started = await dispatch('automations.run', { id, req: { inputs: {} } }, ctx);
    const runId = (started as { value: { run_id: string } }).value.run_id;
    expect(await dispatch('automations.getRun', { runId }, ctx)).toMatchObject({ ok: true, value: { status: 'waiting', number: 1, at_step: 'Go ahead?' } });
    expect(await dispatch('automations.answer', { runId, stepId: 'ask', req: { decision: 'approve', note: 'go' } }, ctx)).toMatchObject({ ok: true });
    expect(await dispatch('automations.getRun', { runId }, ctx)).toMatchObject({ ok: true, value: { status: 'succeeded' } });
    expect(await dispatch('automations.runs', { id, limit: 5 }, ctx)).toMatchObject({ ok: true, value: [{ kind: 'run', run: { id: runId, number: 1 } }] });
    const files = await dispatch('automations.files', { runId }, ctx);
    expect(files.ok && (files.value as Array<{ name: string }>).map((f) => f.name)).toContain('inputs.json');
    const inputs = await dispatch('automations.file', { runId, path: 'inputs.json' }, ctx);
    expect(inputs.ok && new TextDecoder().decode(inputs.value as Uint8Array)).toContain('{');
    expect(await dispatch('automations.stopStep', { runId, stepId: 'ask' }, ctx)).toMatchObject({ ok: false, error: { status: 409 } });
    expect(await dispatch('automations.answer', { runId, stepId: 'Not An Id', req: { decision: 'approve' } }, ctx)).toMatchObject({ ok: false, error: { code: 'invalid_request' } });

    const grants = [{ tool: 'web_fetch', match: { domain: 'example.com' }, action: 'allow' as const }];
    expect(await dispatch('automations.setGrants', { id, grants }, ctx)).toMatchObject({ ok: true, value: { grants } });
    expect(await dispatch('automations.keepGrants', { id }, ctx)).toMatchObject({ ok: true });
    expect(await dispatch('automations.setEnabled', { id, enabled: true }, ctx)).toMatchObject({ ok: true, value: { enabled: true } });
    expect(await dispatch('automations.restore', { id, version: 1 }, ctx)).toMatchObject({ ok: true, value: { version: 3 } });
    const exp = await dispatch('automations.export', { id }, ctx);
    expect(exp).toMatchObject({ ok: true, value: { format: 'desk-automation/1', name: 'ask' } });
    expect(await dispatch('automations.import', { projectId, exp: { ...(exp as { value: object }).value, name: 'ask-copy' } }, ctx)).toMatchObject({ ok: true, value: { automation: { name: 'ask-copy' } } });
    expect(await dispatch('automations.remove', { id }, ctx)).toMatchObject({ ok: true });
    expect(await dispatch('automations.get', { id }, ctx)).toMatchObject({ ok: false, error: { status: 404 } });
  });

  it('picks files for run inputs, reveals only run folders, and accepts remember on approvals', async () => {
    const { ctx, opened } = await setup();
    const runId = '01HZX3K9Q4T6V8W2Y5B7C0D1EF';
    expect(await dispatch('app.pickFile', { purpose: 'automation-input' }, ctx)).toEqual({ ok: true, value: '/picked/file.txt' });
    expect(await dispatch('app.pickFolder', { purpose: 'automation-input' }, ctx)).toEqual({ ok: true, value: '/picked' });
    expect(await dispatch('app.revealPath', { runId, stepId: 'fetch' }, ctx)).toMatchObject({ ok: true });
    expect(await dispatch('app.revealPath', { runId }, ctx)).toMatchObject({ ok: true });
    expect(opened).toEqual([`reveal:${runId}:fetch`, `reveal:${runId}:`]);
    for (const bad of [{ runId: '../etc' }, { runId, stepId: '../../x' }, { runId: runId.toLowerCase() }]) {
      expect(await dispatch('app.revealPath', bad, ctx)).toMatchObject({ ok: false, error: { code: 'invalid_request' } });
    }
    expect(channels['approvals.resolve'].safeParse({ id: 'a', decision: 'approved', remember: true }).success).toBe(true);
  });
```

Create `packages/bff/src/server/run-folder.test.ts`:

```ts
import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { existingRunFolder, runFolder } from './run-folder';

const RUN = '01HZX3K9Q4T6V8W2Y5B7C0D1EF';
let dir: string;
beforeEach(() => void (dir = mkdtempSync(join(tmpdir(), 'desk-run-folder-'))));
afterEach(() => rmSync(dir, { recursive: true, force: true }));

describe('run folders', () => {
  it('builds a run folder or a step folder from ids only', () => {
    expect(runFolder('/d', RUN, 'fetch')).toBe(`/d/automation-runs/${RUN}/steps/fetch`);
    expect(runFolder('/d', RUN)).toBe(`/d/automation-runs/${RUN}`);
    for (const [run, step] of [['../x', undefined], [RUN, '../../x'], [RUN.toLowerCase(), undefined], [RUN, 'A']] as const) {
      expect(() => runFolder('/d', run, step)).toThrow('That is not a run folder.');
    }
  });

  it('refuses a folder that is gone, or is not a real directory', async () => {
    await expect(existingRunFolder(dir, RUN)).rejects.toMatchObject({ code: 'folder_missing' });
    mkdirSync(join(dir, 'automation-runs', RUN, 'steps', 'fetch'), { recursive: true });
    expect(await existingRunFolder(dir, RUN, 'fetch')).toBe(join(dir, 'automation-runs', RUN, 'steps', 'fetch'));
    writeFileSync(join(dir, 'automation-runs', RUN, 'steps', 'file'), 'x');
    await expect(existingRunFolder(dir, RUN, 'file')).rejects.toMatchObject({ code: 'folder_missing' });
    symlinkSync(tmpdir(), join(dir, 'automation-runs', RUN, 'steps', 'link'));
    await expect(existingRunFolder(dir, RUN, 'link')).rejects.toMatchObject({ code: 'folder_missing' });
  });
});
```

In `packages/bff/src/contract/types.test.ts`:
- change `expect(Object.keys(channels)).toHaveLength(87);` to `expect(Object.keys(channels)).toHaveLength(113);` (24 automations operations, `app.pickFile` and `app.revealPath`);
- add `Equal<ChannelOutput<'app.pickFile'>, string | null>` and `Equal<ChannelOutput<'automations.file'>, Uint8Array>` to the `checks` tuple type, with two more `true` entries in its value.

In `apps/web-server/src/web-context.test.ts`, add `mkdirSync` to the `node:fs` import, add `['app.pickFile', { purpose: 'automation-input' }],` to the `calls` list of 'refuses the operations the browser does itself, and LaunchAgent repair', and add:

```ts
  it('opens a run folder with the platform opener, only while it exists', async () => {
    const { ctx, opened } = setup();
    const runId = '01HZX3K9Q4T6V8W2Y5B7C0D1EF';
    expect(await dispatch('app.revealPath', { runId }, ctx)).toMatchObject({ ok: false, error: { code: 'folder_missing' } });
    mkdirSync(join(dataDir, 'automation-runs', runId, 'steps', 'fetch'), { recursive: true });
    expect(await dispatch('app.revealPath', { runId, stepId: 'fetch' }, ctx)).toEqual({ ok: true, value: { ok: true } });
    expect(opened).toEqual([join(dataDir, 'automation-runs', runId, 'steps', 'fetch')]);
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm vitest run packages/bff apps/web-server/src/web-context.test.ts`
Expected: FAIL:
- `Unknown operation: automations.create` (`unknown_channel`);
- `./run-folder` does not exist;
- the channel count is 87;
- `app.pickFile` is unknown.

- [ ] **Step 3: Declare the operations**

In `packages/bff/src/contract/ipc.ts`, extend the protocol import:

```ts
import {
  AddSourceRequest,
  AutomationAnswerRequest,
  AutomationCreateRequest,
  AutomationImportRequest,
  AutomationLayout,
  AutomationRunRequest,
  AutomationSaveRequest,
  AutomationValidateRequest,
  CreateProjectRequest,
  DaemonConfigPatch,
  Grant,
  LibraryUploadRequest,
  MemoryUpdateRequest,
  MemoryWriteRequest,
  ModelEndpointPutRequest,
  ModelsPutRequest,
  SkillWriteRequest,
  StepId,
  UpdateProjectRequest,
} from '@desk/protocol';
```

Add after `const none = z.object({});`:

```ts
/** Run ids are ULIDs; `app.revealPath` builds a filesystem path from one, so nothing else passes. */
const RUN_ID = /^[0-9A-HJKMNP-TV-Z]{26}$/;
```

Change the `approvals.resolve` channel to:

```ts
  /** `remember` (a step agent's approval only): also adds a grant to its automation (spec §5.3). */
  'approvals.resolve': z.object({ id, decision: z.enum(['approved', 'denied']), note: z.string().max(2000).optional(), remember: z.boolean().optional() }),
```

Add this block after the `'attention.dismiss'` line:

```ts
  'automations.list': z.object({ projectId: id }),
  'automations.create': z.object({ projectId: id, req: AutomationCreateRequest }),
  'automations.validate': z.object({ projectId: id, req: AutomationValidateRequest }),
  'automations.import': z.object({ projectId: id, exp: AutomationImportRequest }),
  'automations.get': z.object({ id }),
  'automations.save': z.object({ id, req: AutomationSaveRequest }),
  'automations.remove': z.object({ id }),
  'automations.layout': z.object({ id, layout: AutomationLayout }),
  'automations.versions': z.object({ id }),
  'automations.version': z.object({ id, version }),
  'automations.restore': z.object({ id, version }),
  'automations.setEnabled': z.object({ id, enabled: z.boolean() }),
  'automations.setGrants': z.object({ id, grants: z.array(Grant).max(100), reason: z.enum(['edited', 'enabled']).optional() }),
  'automations.keepGrants': z.object({ id }),
  'automations.export': z.object({ id }),
  'automations.run': z.object({ id, req: AutomationRunRequest }),
  'automations.runs': z.object({ id, before: z.string().max(64).optional(), limit: z.number().int().min(1).max(200).optional() }),
  'automations.getRun': z.object({ runId: id }),
  'automations.cancelRun': z.object({ runId: id }),
  'automations.answer': z.object({ runId: id, stepId: StepId, req: AutomationAnswerRequest }),
  'automations.stopStep': z.object({ runId: id, stepId: StepId }),
  'automations.log': z.object({ runId: id, stepId: StepId }),
  'automations.files': z.object({ runId: id, path: z.string().max(4096).optional() }),
  'automations.file': z.object({ runId: id, path: relPath }),
```

Change `app.pickFolder` and add the two app ops after it:

```ts
  'app.pickFolder': z.object({ purpose: z.enum(['source', 'skill-import', 'automation-input']) }),
  /** A file for a Run now / Test input: deskd copies it into the run folder. desk web answers not_offered. */
  'app.pickFile': z.object({ purpose: z.enum(['automation-input']) }),
  /** Opens a run's folder, or one step's folder in it, with the platform opener. Nothing else can be revealed. */
  'app.revealPath': z.object({ runId: z.string().regex(RUN_ID), stepId: StepId.optional() }),
```

In `packages/bff/src/contract/types.ts`, add after `'attention.dismiss'`:

```ts
  'automations.list': Out<Client['automations']['list']>;
  'automations.create': Out<Client['automations']['create']>;
  'automations.validate': Out<Client['automations']['validate']>;
  'automations.import': Out<Client['automations']['importInto']>;
  'automations.get': Out<Client['automations']['get']>;
  'automations.save': Out<Client['automations']['save']>;
  'automations.remove': Out<Client['automations']['remove']>;
  'automations.layout': Out<Client['automations']['layout']>;
  'automations.versions': Out<Client['automations']['versions']>;
  'automations.version': Out<Client['automations']['version']>;
  'automations.restore': Out<Client['automations']['restore']>;
  'automations.setEnabled': Out<Client['automations']['setEnabled']>;
  'automations.setGrants': Out<Client['automations']['setGrants']>;
  'automations.keepGrants': Out<Client['automations']['keepGrants']>;
  'automations.export': Out<Client['automations']['exportOf']>;
  'automations.run': Out<Client['automations']['run']>;
  'automations.runs': Out<Client['automations']['runs']>;
  'automations.getRun': Out<Client['automations']['getRun']>;
  'automations.cancelRun': Out<Client['automations']['cancelRun']>;
  'automations.answer': Out<Client['automations']['answer']>;
  'automations.stopStep': Out<Client['automations']['stopStep']>;
  'automations.log': Out<Client['automations']['log']>;
  'automations.files': Out<Client['automations']['files']>;
  'automations.file': Out<Client['automations']['file']>;
```

and after `'app.pickFolder': string | null;`:

```ts
  'app.pickFile': string | null;
  'app.revealPath': Ok;
```

- [ ] **Step 4: Run folders and handlers**

Create `packages/bff/src/server/run-folder.ts`:

```ts
import { lstat } from 'node:fs/promises';
import { join } from 'node:path';
import { UserFacingError } from './errors';

const RUN_ID = /^[0-9A-HJKMNP-TV-Z]{26}$/;
const STEP_ID = /^[a-z][a-z0-9_-]{0,39}$/;

/** A run's folder under the data dir, or one step's folder in it. Anything but a run id and a step id is refused. */
export function runFolder(dataDir: string, runId: string, stepId?: string): string {
  if (!RUN_ID.test(runId) || (stepId !== undefined && !STEP_ID.test(stepId))) throw new UserFacingError('invalid_path', 'That is not a run folder.');
  return stepId ? join(dataDir, 'automation-runs', runId, 'steps', stepId) : join(dataDir, 'automation-runs', runId);
}

/**
 * `runFolder`, once it is a real directory (not a symlink). deskd creates run and step folders; a step's script writes
 * inside its folder, never the folder itself. Retention deletes old ones.
 */
export async function existingRunFolder(dataDir: string, runId: string, stepId?: string): Promise<string> {
  const dir = runFolder(dataDir, runId, stepId);
  const st = await lstat(dir).catch(() => null);
  if (!st?.isDirectory()) throw new UserFacingError('folder_missing', "This run's folder is gone: Desk keeps the folders of each automation's last 20 runs or 30 days.");
  return dir;
}
```

In `packages/bff/src/server/index.ts`, add `export * from './run-folder';` after `export * from './notify';`.

In `packages/bff/src/server/handlers.ts`, change the `app` part of `HandlerContext`:

```ts
  app: {
    info(): AppInfo;
    openExternal(url: string): Promise<void>;
    pickFolder(purpose: 'source' | 'skill-import' | 'automation-input'): Promise<string | null>;
    pickFile(purpose: 'automation-input'): Promise<string | null>;
    revealLogs(): Promise<void>;
    /** Opens `existingRunFolder(dataDir, runId, stepId)` with the platform opener. */
    revealRun(runId: string, stepId?: string): Promise<void>;
    saveFile(name: string, data: Uint8Array): Promise<boolean>;
    openMain(route?: string): void;
    settings(): AppSettings;
    updateSettings(patch: AppSettingsPatch): AppSettings;
  };
```

Change the `approvals.resolve` handler to `(i, c) => c.client().approvals.resolve(i.id, i.decision, i.note, i.remember)`. Add after the `'attention.dismiss'` handler:

```ts
  'automations.list': (i, c) => c.client().automations.list(i.projectId),
  'automations.create': (i, c) => c.client().automations.create(i.projectId, i.req),
  'automations.validate': (i, c) => c.client().automations.validate(i.projectId, i.req),
  'automations.import': (i, c) => c.client().automations.importInto(i.projectId, i.exp),
  'automations.get': (i, c) => c.client().automations.get(i.id),
  'automations.save': (i, c) => c.client().automations.save(i.id, i.req),
  'automations.remove': (i, c) => c.client().automations.remove(i.id),
  'automations.layout': (i, c) => c.client().automations.layout(i.id, i.layout),
  'automations.versions': (i, c) => c.client().automations.versions(i.id),
  'automations.version': (i, c) => c.client().automations.version(i.id, i.version),
  'automations.restore': (i, c) => c.client().automations.restore(i.id, i.version),
  'automations.setEnabled': (i, c) => c.client().automations.setEnabled(i.id, i.enabled),
  'automations.setGrants': (i, c) => c.client().automations.setGrants(i.id, i.grants, i.reason ?? 'edited'),
  'automations.keepGrants': (i, c) => c.client().automations.keepGrants(i.id),
  'automations.export': (i, c) => c.client().automations.exportOf(i.id),
  'automations.run': (i, c) => c.client().automations.run(i.id, i.req),
  'automations.runs': (i, c) => c.client().automations.runs(i.id, { ...(i.before ? { before: i.before } : {}), ...(i.limit ? { limit: i.limit } : {}) }),
  'automations.getRun': (i, c) => c.client().automations.getRun(i.runId),
  'automations.cancelRun': (i, c) => c.client().automations.cancelRun(i.runId),
  'automations.answer': (i, c) => c.client().automations.answer(i.runId, i.stepId, i.req),
  'automations.stopStep': (i, c) => c.client().automations.stopStep(i.runId, i.stepId),
  'automations.log': (i, c) => c.client().automations.log(i.runId, i.stepId),
  'automations.files': (i, c) => c.client().automations.files(i.runId, i.path ?? ''),
  'automations.file': (i, c) => c.client().automations.file(i.runId, i.path),
```

Add after the `'app.pickFolder'` handler:

```ts
  'app.pickFile': (i, c) => c.app.pickFile(i.purpose),
  'app.revealPath': async (i, c) => {
    await c.app.revealRun(i.runId, i.stepId);
    return ok;
  },
```

If a `DeskClient.automations` method has another name than the one used here, the typecheck names it: use the client's name, and keep the operation's.

- [ ] **Step 5: The two hosts**

In `apps/desktop/src/main/index.ts`:
- change the bff import to `import { attentionRoute, Broker, DaemonManager, dispatch, existingRunFolder, notificationFor, UserFacingError, type HandlerContext } from '@desk/bff/server';`;
- replace `pickFolder` in the `app` context, and add `pickFile` and `revealRun`:

```ts
      pickFolder: async (purpose) => {
        const opts: Electron.OpenDialogOptions = {
          properties: ['openDirectory', 'createDirectory'],
          ...(purpose === 'skill-import'
            ? { title: 'Import a skill folder', defaultPath: join(homedir(), '.claude', 'skills') }
            : purpose === 'automation-input'
              ? { title: 'Choose a folder for this run' }
              : { title: 'Add a source folder' }),
        };
        const win = BrowserWindow.getFocusedWindow();
        const r = win ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts);
        return r.canceled ? null : (r.filePaths[0] ?? null);
      },
      pickFile: async () => {
        const opts: Electron.OpenDialogOptions = { properties: ['openFile'], title: 'Choose a file for this run' };
        const win = BrowserWindow.getFocusedWindow();
        const r = win ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts);
        return r.canceled ? null : (r.filePaths[0] ?? null);
      },
      revealRun: async (runId, stepId) => {
        const err = await shell.openPath(await existingRunFolder(dataDir, runId, stepId));
        if (err) throw new UserFacingError('reveal_failed', err);
      },
```

In `apps/web-server/src/web-context.ts`:
- change the bff import to `import { existingRunFolder, UserFacingError, type Broker, type DaemonManager, type HandlerContext } from '@desk/bff/server';`;
- change the `openPath` doc comment to `/** Opens a folder on this computer with the platform opener (app.revealLogs, app.revealPath). */`;
- add to `app`, after `pickFolder`:

```ts
      pickFile: async () => notOffered('Choosing a file'),
```

- and after `revealLogs`:

```ts
      revealRun: async (runId, stepId) => {
        await d.openPath(await existingRunFolder(d.dataDir, runId, stepId));
      },
```

- [ ] **Step 6: The branch-only parity exemption**

In `apps/web-ui/src/app/parity.spec.ts`, add after `HOST_ONLY`:

```ts
/**
 * Automations reach the web UI in Plan 21, on the same branch, before it merges to master (Plan 20's Global Constraints).
 * Until then the React renderer may call these without the web UI. Plan 21 deletes this list and its one use.
 */
const PLAN_21_OPS = (op: string) => op.startsWith('automations.') || op === 'app.pickFile' || op === 'app.revealPath';
```

and in 'calls every operation the React renderer calls, but the desktop-only ones', change the filter to:

```ts
    const missing = [...opsIn(reactSources())].filter((op) => !web.has(op) && !HOST_ONLY.includes(op) && !PLAN_21_OPS(op));
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pnpm vitest run packages/bff apps/web-server/src/web-context.test.ts`
Expected: PASS, including 'has a handler for every channel' and 'returns what the contract declares, for every operation'.

Run: `pnpm typecheck`
Expected: no errors. The desktop main's `HandlerContext` and desk web's must both have `pickFile` and `revealRun`.

- [ ] **Step 8: Commit**

```bash
pnpm test
git add packages/bff/src/contract/ipc.ts packages/bff/src/contract/types.ts packages/bff/src/contract/types.test.ts packages/bff/src/server/handlers.ts packages/bff/src/server/index.ts packages/bff/src/server/handlers.test.ts packages/bff/src/server/run-folder.ts packages/bff/src/server/run-folder.test.ts apps/desktop/src/main/index.ts apps/web-server/src/web-context.ts apps/web-server/src/web-context.test.ts apps/web-ui/src/app/parity.spec.ts
git commit -m "feat(bff): automations operations, file inputs and run folders, in both hosts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Shared logic, part 1: formats, schedules, grants, run inputs, live rules, fixtures

Pure modules in `@desk/ui-core`, which both UIs use. The React components (Task 6 on) and Plan 21's Angular ones only render what these return.

**Files:**
- Modify: `packages/ui-core/package.json` (`./testing` export), `packages/ui-core/src/index.ts`
- Create: `packages/ui-core/src/testing/automations.ts`
- Create: `packages/ui-core/src/automation-format.ts`, `automation-schedules.ts`, `automation-grants.ts`, `automation-inputs.ts`, `automation-live.ts`
- Test: `packages/ui-core/src/automation-format.test.ts`, `automation-schedules.test.ts`, `automation-grants.test.ts`, `automation-inputs.test.ts`, `automation-live.test.ts`

**Interfaces:**
- Consumes:
  - from `@desk/client`: `reduceAutomations`, `withRunDetail`, `emptyAutomations`, `affectsAutomations`, `affectsRun`;
  - from `./format`: `duration`, `since`.
- Produces:
  - `automation-format.ts`:
    - `STEP_KIND_LABEL: Record<StepKind, string>`;
    - `versionBadge(a)`, `listNote(a)`, `originText({origin, via})`, `triggerText(run)`;
    - `type RunTone = 'run'|'wait'|'done'|'fail'|'idle'`, `runStatusText(run): { text; tone: RunTone }`, `lastRunText(lastRun, now): { text; tone }`;
    - `skippedText(entry)`, `runTook(run, now)`, `dayTime(ts, now)`.
  - `automation-schedules.ts`:
    - `type SchedulePreset`, `presetOf(cron)`, `cronOf(preset)`;
    - `describeSchedule(cron, timezone, local?)`, `whenText(schedules, local?)`, `systemTimezone()`.
  - `automation-grants.ts`:
    - `grantKey`, `sameGrant`, `uniqueGrants`, `describeGrant`, `widenDomain`;
    - `type GrantOrigin`, `grantOrigins(events, automationId): Map<string, GrantOrigin>`.
  - `automation-inputs.ts`: `type InputDraft = InputValue | ''`, `initialValues(inputs)`, `runInputs(inputs, values): Record<string, InputValue> | null`.
  - `automation-live.ts`:
    - `eventsAfter(events, after)`;
    - `reduceAutomationList(list, e)`, `reduceRunDetail(run, e)`;
    - `automationListRefetch(e)`, `automationDetailRefetch(id)(e, detail)`, `automationRunsRefetch(id)(e, entries)`, `runDetailRefetch(runId)(e, run)`.
  - `@desk/ui-core/testing`: `digestDef()`, `automationSummary(over?)`, `automationDetail(over?)`, `stepRun(id, over?)`, `runDetail(over?)`.

- [ ] **Step 1: Write the fixtures**

In `packages/ui-core/package.json`, change `exports` to:

```json
  "exports": {
    ".": "./src/index.ts",
    "./testing": "./src/testing/automations.ts"
  },
```

Create `packages/ui-core/src/testing/automations.ts`. It sits in a subfolder, so `boundaries.test.ts` does not ask `index.ts` to export it:

```ts
import type { AutomationDefinition, AutomationDetail, AutomationSummary, RunDetail, StepRunInfo } from '@desk/protocol';

/** Fetch pages (script, routes changed/unchanged) → Summarise (agent, on changed) → Publish? (Ask me). */
export const digestDef = (): AutomationDefinition => ({
  title: 'Weekly digest',
  description: 'Summarises what changed on competitors’ pages.',
  inputs: [{ key: 'topic', label: 'Topic', type: 'text', required: true }],
  triggers: [{ kind: 'schedule', cron: '0 8 * * 1', timezone: 'Europe/Paris', catch_up: 'once' }],
  steps: [
    { id: 'fetch', title: 'Fetch pages', kind: 'script', skill: 'digest', script: 'scripts/fetch.py', args: ['{{inputs.topic}}'], idempotent: false, join: 'all', on_error: 'stop', routes: ['changed', 'unchanged'], publish: [] },
    { id: 'sum', title: 'Summarise', kind: 'agent', brief: 'Summarise {{steps.fetch.dir}}.', skills: ['web-research'], output_keys: [{ key: 'headline', description: 'the biggest change' }], join: 'all', on_error: 'stop', routes: [], publish: ['digest.md'] },
    { id: 'ok', title: 'Publish?', kind: 'ask', question: 'Publish "{{steps.sum.outputs.headline}}"?', show: ['digest.md'], join: 'all', on_error: 'stop', routes: [], publish: [] },
  ],
  edges: [
    { from: 'fetch', to: 'sum', route: 'changed' },
    { from: 'sum', to: 'ok' },
  ],
  after_run: 'notify',
  limits: { run_deadline_hours: 24, max_parallel_agents: 2, max_parallel_scripts: 4 },
});

export const automationSummary = (over: Partial<AutomationSummary> = {}): AutomationSummary => ({
  id: 'a1',
  project_id: 'p',
  name: 'digest',
  title: 'Weekly digest',
  description: 'Summarises what changed on competitors’ pages.',
  version: 7,
  tested_version: 6,
  enabled: true,
  grants_suspended: false,
  schedules: [{ cron: '0 8 * * 1', timezone: 'Europe/Paris' }],
  last_run: null,
  next_due: '2026-10-05T06:00:00.000Z',
  enable_requested: false,
  updated_at: '2026-09-27T10:00:00.000Z',
  ...over,
});

export const automationDetail = (over: Partial<AutomationDetail> = {}): AutomationDetail => ({
  ...automationSummary(),
  definition: digestDef(),
  layout: {},
  grants: [],
  proposed_grants: [],
  grants_set_version: null,
  enable_request: null,
  ...over,
});

export const stepRun = (step_id: string, over: Partial<StepRunInfo> = {}): StepRunInfo => ({
  step_id,
  attempt: 1,
  status: 'pending',
  route: null,
  outputs: {},
  summary: null,
  error: null,
  agent_id: null,
  child_run_id: null,
  resume_at: null,
  gate: null,
  question: null,
  note: null,
  started_at: null,
  finished_at: null,
  ...over,
});

/** Run #14, six minutes in: Fetch pages done (route changed), Summarise running as agent ag1, Publish? not reached. */
export const runDetail = (over: Partial<RunDetail> = {}): RunDetail => ({
  id: 'r14',
  number: 14,
  automation_id: 'a1',
  project_id: 'p',
  version: 7,
  trigger: 'schedule',
  test: false,
  inputs: { topic: 'robots' },
  by: 'schedule',
  parent_run_id: null,
  parent_step_id: null,
  due_at: '2026-09-28T06:00:00.000Z',
  caught_up: 0,
  status: 'running',
  at_step: 'Summarise',
  summary: null,
  reason: null,
  started_at: '2026-09-28T06:00:00.000Z',
  finished_at: null,
  deadline_at: '2026-09-29T06:00:00.000Z',
  automation_name: 'digest',
  automation_title: 'Weekly digest',
  definition: digestDef(),
  steps: [
    stepRun('fetch', { status: 'succeeded', route: 'changed', summary: '3 of 5 sites differ', started_at: '2026-09-28T06:00:00.000Z', finished_at: '2026-09-28T06:00:12.000Z' }),
    stepRun('sum', { status: 'running', agent_id: 'ag1', started_at: '2026-09-28T06:00:12.000Z' }),
    stepRun('ok', { attempt: 0 }),
  ],
  ...over,
});
```

- [ ] **Step 2: Write the failing tests**

Create `packages/ui-core/src/automation-format.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { automationSummary } from './testing/automations';
import { dayTime, lastRunText, listNote, originText, runStatusText, runTook, skippedText, triggerText, versionBadge } from './automation-format';

describe('automation formats', () => {
  it('badges versions and notes list rows', () => {
    expect(versionBadge({ version: 7, tested_version: 6 })).toBe('v7 · tested in v6');
    expect(versionBadge({ version: 7, tested_version: 7 })).toBe('v7 · tested');
    expect(versionBadge({ version: 2, tested_version: null })).toBe('v2 · untested');
    expect(listNote(automationSummary())).toBe('v7 · untested changes');
    expect(listNote(automationSummary({ version: 2, tested_version: 2, schedules: [], grants_suspended: true }))).toBe('v2 · run now only · grants suspended');
    expect(listNote(automationSummary({ tested_version: 7, enabled: false, enable_requested: true }))).toBe('v7 · Desk proposes turning it on');
  });

  it('words origins, triggers, statuses, last runs and skipped times', () => {
    expect(originText({ origin: 'agent:d1', via: 'tool' })).toBe('Desk');
    expect(originText({ origin: 'user', via: 'editor' })).toBe('You');
    expect(originText({ origin: 'user', via: 'restore' })).toBe('Restored');
    expect(originText({ origin: 'user', via: 'import' })).toBe('Imported');
    expect(triggerText({ trigger: 'test', test: true, version: 6 })).toBe('test · v6');
    expect(triggerText({ trigger: 'manual', test: false, version: 6 })).toBe('Run now');
    expect(runStatusText({ status: 'running', at_step: 'Summarise' })).toEqual({ text: 'running · Summarise', tone: 'run' });
    expect(runStatusText({ status: 'failed', at_step: 'Fetch pages' })).toEqual({ text: 'failed at Fetch pages', tone: 'fail' });
    expect(runStatusText({ status: 'waiting', at_step: null })).toEqual({ text: 'waiting on you', tone: 'wait' });
    expect(runStatusText({ status: 'cancelled', at_step: null })).toEqual({ text: 'cancelled', tone: 'idle' });
    const last = { id: 'r', number: 2, trigger: 'schedule' as const, test: false, started_at: '2026-09-28T06:00:00.000Z', summary: null };
    const now = Date.parse('2026-09-28T09:00:00.000Z');
    expect(lastRunText({ ...last, status: 'waiting', finished_at: null, waiting_on: 'Ask me: Publish?' }, now)).toEqual({ text: 'waiting on you: Ask me: Publish?', tone: 'wait' });
    expect(lastRunText({ ...last, status: 'succeeded', finished_at: '2026-09-28T07:00:00.000Z', waiting_on: null }, now)).toEqual({ text: 'succeeded · 2h ago', tone: 'done' });
    expect(lastRunText({ ...last, status: 'running', finished_at: null, waiting_on: null }, now)).toEqual({ text: 'running', tone: 'run' });
    expect(skippedText({ kind: 'skipped', automation_id: 'a1', trigger_index: 0, due_at: 't', reason: 'still_running', ts: 't' })).toBe('skipped: the previous run was still going');
  });

  it('says local days relative to now, and durations', () => {
    const now = new Date(2026, 8, 28, 9, 30).getTime(); // Monday 28 September 2026
    expect(dayTime(new Date(2026, 8, 28, 8, 0).toISOString(), now)).toBe('today 08:00');
    expect(dayTime(new Date(2026, 8, 29, 2, 0).toISOString(), now)).toBe('tomorrow 02:00');
    expect(dayTime(new Date(2026, 8, 27, 2, 0).toISOString(), now)).toBe('yesterday 02:00');
    expect(dayTime(new Date(2026, 9, 2, 8, 0).toISOString(), now)).toBe('Fri 08:00');
    expect(dayTime(new Date(2026, 9, 12, 8, 5).toISOString(), now)).toBe('Oct 12 08:05');
    expect(runTook({ started_at: '2026-09-28T06:00:00.000Z', finished_at: '2026-09-28T06:09:00.000Z' }, 0)).toBe('9m');
    expect(runTook({ started_at: '2026-09-28T06:00:00.000Z', finished_at: null }, Date.parse('2026-09-28T06:00:40.000Z'))).toBe('40s');
  });
});
```

Create `packages/ui-core/src/automation-schedules.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { cronOf, describeSchedule, presetOf, whenText, type SchedulePreset } from './automation-schedules';

describe('schedules', () => {
  it('recognises presets and builds their cron', () => {
    const cases: Array<[string, SchedulePreset]> = [
      ['0 8 * * *', { kind: 'daily', time: '08:00' }],
      ['30 7 * * 1-5', { kind: 'weekdays', time: '07:30' }],
      ['0 8 * * 1', { kind: 'weekly', day: 1, time: '08:00' }],
      ['5 9 1 * *', { kind: 'monthly', dom: 1, time: '09:05' }],
    ];
    for (const [cron, preset] of cases) {
      expect(presetOf(cron)).toEqual(preset);
      expect(cronOf(preset)).toBe(cron);
    }
    expect(presetOf('*/15 * * * *')).toEqual({ kind: 'custom', cron: '*/15 * * * *' });
    expect(presetOf('0 8 31 * *')).toEqual({ kind: 'custom', cron: '0 8 31 * *' });
    expect(cronOf({ kind: 'custom', cron: ' 0 8 * * 1 ' })).toBe('0 8 * * 1');
  });

  it('describes schedules, naming the timezone only when it is not the Mac’s', () => {
    expect(describeSchedule('0 8 * * 1', 'Europe/Paris', 'Europe/Paris')).toBe('Mondays 08:00');
    expect(describeSchedule('0 2 * * *', 'America/New_York', 'Europe/Paris')).toBe('Daily 02:00 (America/New_York)');
    expect(describeSchedule('0 9 2 * *', 'UTC', 'UTC')).toBe('Monthly on the 2nd at 09:00');
    expect(whenText([], 'UTC')).toBe('Run now only');
    expect(whenText([{ cron: '0 8 * * 1-5', timezone: 'UTC' }, { cron: '*/30 * * * *', timezone: 'UTC' }], 'UTC')).toBe('Weekdays 08:00 · cron */30 * * * *');
  });
});
```

Create `packages/ui-core/src/automation-grants.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import type { Grant } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { describeGrant, grantKey, grantOrigins, sameGrant, uniqueGrants, widenDomain } from './automation-grants';

describe('grants', () => {
  it('compares, dedupes and describes grants', () => {
    const a: Grant = { tool: 'web_fetch', match: { domain: 'example.com' }, action: 'allow' };
    expect(sameGrant(a, { tool: 'web_fetch', match: { domain: 'example.com' }, action: 'allow' })).toBe(true);
    expect(sameGrant({ tool: 'bash', action: 'allow' }, { tool: 'bash', match: {}, action: 'allow' })).toBe(true);
    expect(uniqueGrants([a, { ...a }, { tool: 'bash', action: 'deny' }])).toHaveLength(2);
    expect(describeGrant(a)).toBe('Allow web_fetch on example.com');
    expect(describeGrant({ tool: 'bash', action: 'deny' })).toBe('Deny any bash call');
    expect(describeGrant({ tool: 'git_push', match: { branch: 'desk/auto-digest-*' }, action: 'allow' })).toBe('Allow git_push on desk/auto-digest-*');
  });

  it('widens domains like the daemon', () => {
    expect(widenDomain('news.bbc.co.uk')).toEqual(['bbc.co.uk', '*.bbc.co.uk']);
    expect(widenDomain('api.example.com')).toEqual(['example.com', '*.example.com']);
    expect(widenDomain('10.0.0.1')).toEqual(['10.0.0.1']);
    expect(widenDomain('localhost')).toEqual(['localhost']);
  });

  it('traces each grant to the change that added it', () => {
    const fetch: Grant = { tool: 'web_fetch', match: { domain: 'a.com' }, action: 'allow' };
    const bash: Grant = { tool: 'bash', match: { command: '^ls$' }, action: 'allow' };
    const events = [
      ev(1, 'automation.grants_set', { automation_id: 'a1', grants: [fetch], reason: 'enabled' }),
      ev(2, 'automation.grants_set', { automation_id: 'a1', grants: [fetch, bash], reason: 'remembered', source: { run_id: 'r1', step_id: 'sum' } }),
      ev(3, 'automation.grants_set', { automation_id: 'other', grants: [], reason: 'edited' }),
      ev(4, 'automation.grants_set', { automation_id: 'a1', grants: [fetch, bash], reason: 'kept' }),
    ];
    const o = grantOrigins(events, 'a1');
    expect(o.get(grantKey(fetch))).toMatchObject({ kind: 'enabled' });
    expect(o.get(grantKey(bash))).toMatchObject({ kind: 'remembered', run_id: 'r1', step_id: 'sum' });
    const later = grantOrigins([...events, ev(5, 'automation.grants_set', { automation_id: 'a1', grants: [bash], reason: 'edited' })], 'a1');
    expect(later.has(grantKey(fetch))).toBe(false);
  });
});
```

Create `packages/ui-core/src/automation-inputs.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import type { InputSpec } from '@desk/protocol';
import { initialValues, runInputs } from './automation-inputs';

const inputs: InputSpec[] = [
  { key: 'topic', label: 'Topic', type: 'text', required: true },
  { key: 'tone', label: 'Tone', type: 'choice', required: false, options: ['dry', 'warm'], default: 'dry' },
  { key: 'count', label: 'How many', type: 'number', required: false },
  { key: 'brief', label: 'Brief file', type: 'file', required: false },
  { key: 'deep', label: 'Go deep', type: 'boolean', required: false },
];

describe('run inputs', () => {
  it('starts from defaults, with booleans unticked', () => {
    expect(initialValues(inputs)).toEqual({ topic: '', tone: 'dry', count: '', brief: '', deep: false });
  });

  it('leaves out empty optional inputs, parses numbers and refuses a missing required one', () => {
    expect(runInputs(inputs, { topic: '', tone: 'dry', count: '', brief: '', deep: false })).toBeNull();
    expect(runInputs(inputs, { topic: ' robots ', tone: 'dry', count: '3', brief: '', deep: true })).toEqual({ topic: 'robots', tone: 'dry', count: 3, deep: true });
    expect(runInputs(inputs, { topic: 'x', count: 'many' })).toBeNull();
  });
});
```

Create `packages/ui-core/src/automation-live.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { ev } from '@desk/client/testing';
import { automationDetail, automationSummary, runDetail } from './testing/automations';
import { automationDetailRefetch, automationListRefetch, automationRunsRefetch, eventsAfter, reduceAutomationList, reduceRunDetail, runDetailRefetch } from './automation-live';

describe('live rules', () => {
  it('finds the events after an id in an id-ordered log', () => {
    const log = [1, 3, 4].map((id) => ev(id, 'automation.layout_saved', { automation_id: 'a1', layout: {} }));
    expect(eventsAfter(log, 0).map((e) => e.id)).toEqual([1, 3, 4]);
    expect(eventsAfter(log, 3).map((e) => e.id)).toEqual([4]);
    expect(eventsAfter(log, 9)).toEqual([]);
  });

  it('folds list events, and reloads the list for anything but a layout', () => {
    const switched = ev(1, 'automation.switched', { automation_id: 'a1', enabled: false, by: 'user' });
    expect(reduceAutomationList([automationSummary()], switched)[0]!.enabled).toBe(false);
    expect(automationListRefetch(switched)).toBe(true);
    expect(automationListRefetch(ev(2, 'automation.layout_saved', { automation_id: 'a1', layout: {} }))).toBe(false);
    expect(automationListRefetch(ev(3, 'plan.updated', { items: [] }))).toBe(false);
  });

  it('reloads a detail for its own changes and its last run’s end', () => {
    const refetch = automationDetailRefetch('a1');
    const d = automationDetail({ last_run: { id: 'r9', number: 9, status: 'running', trigger: 'manual', test: false, started_at: 't', finished_at: null, summary: null, waiting_on: null } });
    expect(refetch(ev(1, 'automation.switched', { automation_id: 'a1', enabled: true, by: 'user' }), d)).toBe(true);
    expect(refetch(ev(2, 'automation.switched', { automation_id: 'a2', enabled: true, by: 'user' }), d)).toBe(false);
    expect(refetch(ev(3, 'automation.layout_saved', { automation_id: 'a1', layout: {} }), d)).toBe(false);
    expect(refetch(ev(4, 'automation.run_finished', { run_id: 'r9', status: 'succeeded', summary: 'ok' }), d)).toBe(true);
  });

  it('reloads runs when one starts or is skipped, and when a listed run changes', () => {
    const refetch = automationRunsRefetch('a1');
    const list = [{ kind: 'run' as const, run: runDetail() }];
    expect(refetch(ev(1, 'automation.trigger_skipped', { automation_id: 'a1', trigger_index: 0, due_at: '2026-09-28T06:00:00.000Z', reason: 'missed' }), list)).toBe(true);
    expect(refetch(ev(2, 'automation.step_changed', { run_id: 'r14', step_id: 'ok', attempt: 1, status: 'running' }), list)).toBe(true);
    expect(refetch(ev(3, 'automation.step_changed', { run_id: 'other', step_id: 'ok', attempt: 1, status: 'running' }), list)).toBe(false);
  });

  it('folds step changes into a run, and reloads it for its agents’ approvals and its end', () => {
    const run = runDetail();
    const next = reduceRunDetail(run, ev(1, 'automation.step_changed', { run_id: 'r14', step_id: 'sum', attempt: 1, status: 'succeeded', summary: 'done' }));
    expect(next.steps.find((s) => s.step_id === 'sum')).toMatchObject({ status: 'succeeded', summary: 'done' });
    const refetch = runDetailRefetch('r14');
    expect(refetch(ev(2, 'approval.requested', { approval_id: 'ap', run_id: 'x', tool_call_id: 't', tool: 'bash', arguments: '{}', reason: 'r', delegate_to_desk: false }, { agent: 'ag1' }), run)).toBe(true);
    expect(refetch(ev(3, 'automation.run_finished', { run_id: 'r14', status: 'succeeded', summary: 'ok' }), run)).toBe(true);
    expect(refetch(ev(4, 'automation.run_finished', { run_id: 'r1', status: 'succeeded', summary: 'ok' }), run)).toBe(false);
  });
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pnpm vitest run packages/ui-core/src/automation-`
Expected: FAIL: the modules do not exist.

- [ ] **Step 4: Write the modules**

Create `packages/ui-core/src/automation-format.ts`:

```ts
import type { AutomationSummary, RunInfo, RunListEntry, StepKind } from '@desk/protocol';
import { duration, since } from './format';

/** Step kinds as the editor names them. */
export const STEP_KIND_LABEL: Record<StepKind, string> = { script: 'Script', agent: 'Agent', ask: 'Ask me', wait: 'Wait', automation: 'Run automation', tell_desk: 'Tell Desk' };

/** "v7 · tested", "v7 · tested in v6" or "v7 · untested". */
export function versionBadge(a: Pick<AutomationSummary, 'version' | 'tested_version'>): string {
  if (a.tested_version === a.version) return `v${a.version} · tested`;
  return a.tested_version ? `v${a.version} · tested in v${a.tested_version}` : `v${a.version} · untested`;
}

/** The note under a title in the list: "v7 · untested changes · run now only". */
export function listNote(a: Pick<AutomationSummary, 'version' | 'tested_version' | 'schedules' | 'grants_suspended' | 'enable_requested' | 'enabled'>): string {
  const parts = [`v${a.version}`];
  if (a.tested_version !== a.version) parts.push(a.tested_version ? 'untested changes' : 'untested');
  if (!a.schedules.length) parts.push('run now only');
  if (a.grants_suspended) parts.push('grants suspended');
  if (a.enable_requested && !a.enabled) parts.push('Desk proposes turning it on');
  return parts.join(' · ');
}

/** Who saved a version. */
export function originText(v: { origin: string; via: string }): string {
  if (v.via === 'restore') return 'Restored';
  if (v.via === 'import') return 'Imported';
  if (v.origin.startsWith('agent:')) return 'Desk';
  return v.via === 'cli' ? 'You (CLI)' : 'You';
}

const TRIGGER: Record<RunInfo['trigger'], string> = { schedule: 'schedule', manual: 'Run now', desk: 'Desk', parent: 'another automation', test: 'test' };

/** The Trigger column: "schedule", "Run now", "test · v6"… */
export function triggerText(run: Pick<RunInfo, 'trigger' | 'test' | 'version'>): string {
  return run.test ? `test · v${run.version}` : TRIGGER[run.trigger];
}

export type RunTone = 'run' | 'wait' | 'done' | 'fail' | 'idle';

/** The Status column: "running · Summarise", "waiting on you · Publish?", "failed at Fetch pages". */
export function runStatusText(run: Pick<RunInfo, 'status' | 'at_step'>): { text: string; tone: RunTone } {
  switch (run.status) {
    case 'running':
      return { text: run.at_step ? `running · ${run.at_step}` : 'running', tone: 'run' };
    case 'waiting':
      return { text: run.at_step ? `waiting on you · ${run.at_step}` : 'waiting on you', tone: 'wait' };
    case 'succeeded':
      return { text: 'succeeded', tone: 'done' };
    case 'failed':
      return { text: run.at_step ? `failed at ${run.at_step}` : 'failed', tone: 'fail' };
    default:
      return { text: 'cancelled', tone: 'idle' };
  }
}

/** The list's Last run column: "waiting on you: Ask me: Publish?", "failed · 3d ago", "succeeded · 2h ago", "running". */
export function lastRunText(r: NonNullable<AutomationSummary['last_run']>, now: number): { text: string; tone: RunTone } {
  if (r.status === 'waiting') return { text: r.waiting_on ? `waiting on you: ${r.waiting_on}` : 'waiting on you', tone: 'wait' };
  const s = runStatusText({ status: r.status, at_step: null });
  return r.finished_at ? { ...s, text: `${s.text} · ${since(r.finished_at, now)}` } : s;
}

/** Why a schedule time started nothing. */
export function skippedText(e: Extract<RunListEntry, { kind: 'skipped' }>): string {
  return e.reason === 'still_running' ? 'skipped: the previous run was still going' : 'skipped: missed while Desk was off';
}

/** How long a run took, or has been going. */
export const runTook = (run: Pick<RunInfo, 'started_at' | 'finished_at'>, now: number): string => duration((run.finished_at ? Date.parse(run.finished_at) : now) - Date.parse(run.started_at));

const pad = (n: number) => String(n).padStart(2, '0');
const DAY = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTH = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** A local day and time: "today 08:00", "tomorrow 02:00", "Mon 08:00" within a week either way, else "Oct 12 08:00". */
export function dayTime(ts: string, now: number): string {
  const d = new Date(ts);
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const midnight = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((midnight(d) - midnight(new Date(now))) / 86_400_000);
  if (days === 0) return `today ${time}`;
  if (days === 1) return `tomorrow ${time}`;
  if (days === -1) return `yesterday ${time}`;
  if (Math.abs(days) < 7) return `${DAY[d.getDay()]} ${time}`;
  return `${MONTH[d.getMonth()]} ${d.getDate()} ${time}`;
}
```

Create `packages/ui-core/src/automation-schedules.ts`:

```ts
/** A schedule as the Start inspector edits it: a preset, or custom cron. `time` is HH:MM; `day` 0 = Sunday. */
export type SchedulePreset =
  | { kind: 'daily'; time: string }
  | { kind: 'weekdays'; time: string }
  | { kind: 'weekly'; day: number; time: string }
  | { kind: 'monthly'; dom: number; time: string }
  | { kind: 'custom'; cron: string };

/** The computer's timezone: the default for a new schedule. */
export const systemTimezone = (): string => Intl.DateTimeFormat().resolvedOptions().timeZone;

const DAYS = ['Sundays', 'Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays'];
const pad = (n: number) => String(n).padStart(2, '0');
const upTo = (s: string, max: number) => /^\d{1,2}$/.test(s) && Number(s) <= max;

/** The preset a cron expression is, or `custom`. Monthly presets stop at the 28th, which every month has. */
export function presetOf(cron: string): SchedulePreset {
  const f = cron.trim().split(/\s+/);
  if (f.length !== 5) return { kind: 'custom', cron };
  const [m, h, dom, mon, dow] = f as [string, string, string, string, string];
  if (!upTo(m, 59) || !upTo(h, 23) || mon !== '*') return { kind: 'custom', cron };
  const time = `${pad(Number(h))}:${pad(Number(m))}`;
  if (dom === '*' && dow === '*') return { kind: 'daily', time };
  if (dom === '*' && dow === '1-5') return { kind: 'weekdays', time };
  if (dom === '*' && /^[0-6]$/.test(dow)) return { kind: 'weekly', day: Number(dow), time };
  if (upTo(dom, 28) && Number(dom) >= 1 && dow === '*') return { kind: 'monthly', dom: Number(dom), time };
  return { kind: 'custom', cron };
}

export function cronOf(p: SchedulePreset): string {
  if (p.kind === 'custom') return p.cron.trim();
  const [h = 0, m = 0] = p.time.split(':').map(Number);
  switch (p.kind) {
    case 'daily':
      return `${m} ${h} * * *`;
    case 'weekdays':
      return `${m} ${h} * * 1-5`;
    case 'weekly':
      return `${m} ${h} * * ${p.day}`;
    case 'monthly':
      return `${m} ${h} ${p.dom} * *`;
  }
}

const ordinal = (n: number): string => {
  const teen = n % 100 >= 11 && n % 100 <= 13;
  return `${n}${teen ? 'th' : n % 10 === 1 ? 'st' : n % 10 === 2 ? 'nd' : n % 10 === 3 ? 'rd' : 'th'}`;
};

/** "Mondays 08:00", "Weekdays 07:30", "Monthly on the 1st at 09:00", else the cron; plus the timezone when it isn't the computer's. */
export function describeSchedule(cron: string, timezone: string, local = systemTimezone()): string {
  const p = presetOf(cron);
  const text =
    p.kind === 'daily'
      ? `Daily ${p.time}`
      : p.kind === 'weekdays'
        ? `Weekdays ${p.time}`
        : p.kind === 'weekly'
          ? `${DAYS[p.day]} ${p.time}`
          : p.kind === 'monthly'
            ? `Monthly on the ${ordinal(p.dom)} at ${p.time}`
            : `cron ${p.cron}`;
  return timezone === local ? text : `${text} (${timezone})`;
}

/** The list's When column. */
export function whenText(schedules: Array<{ cron: string; timezone: string }>, local = systemTimezone()): string {
  return schedules.length ? schedules.map((s) => describeSchedule(s.cron, s.timezone, local)).join(' · ') : 'Run now only';
}
```

Create `packages/ui-core/src/automation-grants.ts`:

```ts
import type { Grant, StoredEvent } from '@desk/protocol';

/** One identity per grant: tool, action and match (a missing field and an empty match are the same). */
export const grantKey = (g: Grant): string => JSON.stringify([g.tool, g.action, g.match?.domain ?? null, g.match?.command ?? null, g.match?.branch ?? null]);

export const sameGrant = (a: Grant, b: Grant): boolean => grantKey(a) === grantKey(b);

/** Grants in order, each once. */
export function uniqueGrants(list: Grant[]): Grant[] {
  const seen = new Set<string>();
  return list.filter((g) => {
    const k = grantKey(g);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

/** "Allow web_fetch on example.com", "Allow skill_run matching ^digest/fetch\.py(\s|$)", "Deny any bash call". */
export function describeGrant(g: Grant): string {
  const m = g.match;
  const what = m?.domain ? `${g.tool} on ${m.domain}` : m?.command ? `${g.tool} matching ${m.command}` : m?.branch ? `${g.tool} on ${m.branch}` : `any ${g.tool} call`;
  return `${g.action === 'allow' ? 'Allow' : 'Deny'} ${what}`;
}

const SECOND_LEVEL = new Set(['co', 'com', 'net', 'org', 'gov', 'ac', 'edu']);

/**
 * The widening the Grants tab offers for a domain (the same rule as core's `widenDomain`): `news.bbc.co.uk` →
 * `['bbc.co.uk', '*.bbc.co.uk']`. IP addresses, single labels and bare public suffixes stay as they are.
 */
export function widenDomain(host: string): string[] {
  const h = host.toLowerCase().replace(/\.$/, '');
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(h) || h.includes(':') || !h.includes('.')) return [h];
  const labels = h.split('.');
  const tld = labels.at(-1) ?? '';
  const second = labels.at(-2) ?? '';
  const n = tld.length === 2 && SECOND_LEVEL.has(second) ? 3 : 2;
  if (labels.length < n) return [h];
  const registrable = labels.slice(-n).join('.');
  return [registrable, `*.${registrable}`];
}

export type GrantOrigin = { kind: 'remembered'; run_id: string; step_id: string; ts: string } | { kind: 'edited' | 'enabled'; ts: string };

/** Where each current grant came from: the `automation.grants_set` that added it. Later sets keep the origins of grants they keep. */
export function grantOrigins(events: readonly StoredEvent[], automationId: string): Map<string, GrantOrigin> {
  const origins = new Map<string, GrantOrigin>();
  for (const e of events) {
    if (e.type !== 'automation.grants_set' || e.payload.automation_id !== automationId) continue;
    const p = e.payload;
    const now = new Set(p.grants.map(grantKey));
    for (const k of [...origins.keys()]) if (!now.has(k)) origins.delete(k);
    for (const k of now) {
      if (origins.has(k)) continue;
      origins.set(k, p.reason === 'remembered' && p.source ? { kind: 'remembered', run_id: p.source.run_id, step_id: p.source.step_id, ts: e.ts } : { kind: p.reason === 'enabled' ? 'enabled' : 'edited', ts: e.ts });
    }
  }
  return origins;
}
```

`widenDomain` repeats core's rule (`packages/core/src/automations/grants.ts`) because `ui-core` cannot import `@desk/core`. Keep the two in step.

Create `packages/ui-core/src/automation-inputs.ts`:

```ts
import type { InputSpec, InputValue } from '@desk/protocol';

/** A run input while the user fills the form: empty is `''` (unticked is `false`). */
export type InputDraft = InputValue | '';

/** Each input's default, or empty (unticked for booleans). */
export function initialValues(inputs: InputSpec[]): Record<string, InputDraft> {
  return Object.fromEntries(inputs.map((i) => [i.key, i.default ?? (i.type === 'boolean' ? false : '')]));
}

/** The inputs to send: empty optional ones left out, numbers parsed, text trimmed. Null while one is missing or not a number. */
export function runInputs(inputs: InputSpec[], values: Record<string, InputDraft>): Record<string, InputValue> | null {
  const out: Record<string, InputValue> = {};
  for (const i of inputs) {
    const v = values[i.key];
    if (v === undefined || v === '' || (typeof v === 'string' && !v.trim())) {
      if (i.required) return null;
      continue;
    }
    if (i.type === 'number') {
      const n = typeof v === 'number' ? v : Number(v);
      if (!Number.isFinite(n)) return null;
      out[i.key] = n;
    } else out[i.key] = typeof v === 'string' ? v.trim() : v;
  }
  return out;
}
```

Create `packages/ui-core/src/automation-live.ts`:

```ts
import { affectsAutomations, affectsRun, emptyAutomations, reduceAutomations, withRunDetail } from '@desk/client';
import type { AutomationDetail, AutomationSummary, RunDetail, RunListEntry, StoredEvent } from '@desk/protocol';

// How both UIs keep automation snapshots current: events after a snapshot fold in through the Plan 19 reducer, and the
// events these rules pick reload it, so fields no reducer can derive (next_due, at_step…) catch up.

/** The events after `after`, from a log in id order. */
export function eventsAfter(events: readonly StoredEvent[], after: number): StoredEvent[] {
  let lo = 0;
  let hi = events.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (events[mid]!.id <= after) lo = mid + 1;
    else hi = mid;
  }
  return events.slice(lo);
}

/** Folds one event into a project's automation list. */
export const reduceAutomationList = (list: AutomationSummary[], e: StoredEvent): AutomationSummary[] => reduceAutomations({ projectId: e.project_id, list, runs: {} }, e).list;

/** Any automation event but a layout save reloads the list. */
export const automationListRefetch = (e: StoredEvent): boolean => affectsAutomations(e) && e.type !== 'automation.layout_saved';

/** The automation an automation event is about, when its payload says. */
const automationOf = (e: StoredEvent): string | undefined => (e.type.startsWith('automation.') ? (e.payload as { automation_id?: string }).automation_id : undefined);

/** A detail reloads when the automation changes (not its layout: the editor saved it), and when its last run ends. */
export const automationDetailRefetch =
  (id: string) =>
  (e: StoredEvent, d: AutomationDetail): boolean =>
    (automationOf(e) === id && e.type !== 'automation.layout_saved') || (e.type === 'automation.run_finished' && e.payload.run_id === d.last_run?.id);

/** A run list reloads when one of the automation's runs starts or a schedule time is skipped, and when a listed run changes. */
export const automationRunsRefetch =
  (id: string) =>
  (e: StoredEvent, list: RunListEntry[]): boolean => {
    if (automationOf(e) === id && (e.type === 'automation.run_started' || e.type === 'automation.trigger_skipped')) return true;
    if (e.type !== 'automation.step_changed' && e.type !== 'automation.run_finished') return false;
    const runId = e.payload.run_id;
    return list.some((x) => x.kind === 'run' && x.run.id === runId);
  };

/** Folds one event into a loaded run (its step changes and its end). */
export const reduceRunDetail = (run: RunDetail, e: StoredEvent): RunDetail => reduceAutomations(withRunDetail(emptyAutomations(run.project_id), run), e).runs[run.id] ?? run;

/** A run reloads for its agents' approvals, a child run's end and its own end. */
export const runDetailRefetch =
  (runId: string) =>
  (e: StoredEvent, run: RunDetail): boolean =>
    affectsRun(e, run) || (e.type === 'automation.run_finished' && e.payload.run_id === runId);
```

In `packages/ui-core/src/index.ts`, add in alphabetical order (before `export * from './catalog';`):

```ts
export * from './automation-format';
export * from './automation-grants';
export * from './automation-inputs';
export * from './automation-live';
export * from './automation-schedules';
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pnpm vitest run packages/ui-core`
Expected: PASS, including `boundaries.test.ts` (every module exported, none framework- or Node-bound).

Run: `pnpm typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
pnpm test
git add packages/ui-core/package.json packages/ui-core/src/index.ts packages/ui-core/src/testing/automations.ts packages/ui-core/src/automation-format.ts packages/ui-core/src/automation-schedules.ts packages/ui-core/src/automation-grants.ts packages/ui-core/src/automation-inputs.ts packages/ui-core/src/automation-live.ts packages/ui-core/src/automation-format.test.ts packages/ui-core/src/automation-schedules.test.ts packages/ui-core/src/automation-grants.test.ts packages/ui-core/src/automation-inputs.test.ts packages/ui-core/src/automation-live.test.ts
git commit -m "feat(ui-core): automation formats, schedules, grants, run inputs and live rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Shared logic, part 2: the graph model

Pure functions, no framework. Two conventions:
- **Edges.** An edge is identified by its index in `definition.edges` (`edge-<i>`), which is also how validation paths name it (`edges[3].when`). So a duplicate edge between the same two steps, from Desk or an import, still shows.
- **Nodes and edges.** They are plain objects shaped like React Flow's (`id`, `type`, `position`, `data`, `selected`…), without importing it. The desktop passes them to React Flow as they are (Task 8), and Plan 21 maps them to its Angular canvas.

**Files:**
- Modify: `packages/ui-core/package.json`, `pnpm-lock.yaml` (dagre)
- Create: `packages/ui-core/src/automation-graph.ts`
- Modify: `packages/ui-core/src/index.ts`
- Test: `packages/ui-core/src/automation-graph.test.ts`

**Interfaces:**
- Produces (all from `automation-graph.ts`, exported by `@desk/ui-core`):
  - Constants: `START_ID = '__start'`, `NODE_W = 200`, `NODE_H = 64`, `START_W = 240`, `START_H = 44`.
  - `type GraphSelection = { kind: 'none' } | { kind: 'start' } | { kind: 'step'; id: string } | { kind: 'edge'; index: number }`.
  - `edgeId(i): string` (`edge-<i>`) and `edgeIndex(id): number | null`.
  - `startSteps(def): string[]`, `ancestors(def, id): Set<string>`, `incomingCount(def, id): number`.
  - `kindLabel(step, incoming): string`, `stepDetail(step): string`, `edgeLabel(edge): string | null`, `danglingRoutes(def, step): string[]`.
  - `autoLayout(def): AutomationLayout` and `ensureLayout(def, layout): AutomationLayout`.
  - `type NodeTone = 'ok'|'run'|'wait'|'pending'|'skipped'|'fail'|'idle'` and `type NodeRunState = { tone; badge: string; detail: string | null }`.
  - `type GraphIssues = { steps: Record<string, string[]>; edges: Record<number, string[]>; start: string[] }` and `type GraphRun = { steps: Record<string, NodeRunState>; fired: ReadonlySet<number> }`.
  - Node data types: `StepNodeData` (`{ step, kind, detail, errors, run, output }`), `StartNodeData` (`{ label, errors }`), `StubNodeData` (`{ label }`).
  - Node types: `StepGraphNode`, `StartGraphNode`, `StubGraphNode`, `GraphNode`.
  - Edge types: `RouteEdgeData` (`{ label; look: 'plain'|'fired'|'idle'|'stub'; error }`), `GraphEdge`.
  - `toGraph(def, layout, { startLabel, selection, editable, issues?, run? }): { nodes: GraphNode[]; edges: GraphEdge[] }`.

- [ ] **Step 1: Add the dependency**

Run: `pnpm --filter @desk/ui-core add @dagrejs/dagre@^3.1.1`
Expected: it appears under `dependencies` in `packages/ui-core/package.json`. It is MIT and pure JavaScript (no native build), and ships its own types.

- [ ] **Step 2: Write the failing test**

Create `packages/ui-core/src/automation-graph.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { digestDef } from './testing/automations';
import { ancestors, autoLayout, danglingRoutes, edgeIndex, edgeLabel, ensureLayout, kindLabel, START_ID, startSteps, stepDetail, toGraph } from './automation-graph';

describe('automation graph', () => {
  it('finds start steps and ancestors', () => {
    const def = digestDef();
    expect(startSteps(def)).toEqual(['fetch']);
    expect([...ancestors(def, 'ok')].sort()).toEqual(['fetch', 'sum']);
    expect(ancestors(def, 'fetch').size).toBe(0);
    expect(edgeIndex('edge-3')).toBe(3);
    expect(edgeIndex('__start->fetch')).toBeNull();
  });

  it('labels nodes and edges like the mockup', () => {
    const def = digestDef();
    const [fetch, sum, ok] = def.steps;
    expect(kindLabel(sum!, 1)).toBe('agent');
    expect(kindLabel(sum!, 2)).toBe('agent · join all');
    expect(stepDetail(fetch!)).toBe('digest: fetch.py');
    expect(stepDetail(sum!)).toBe('skills: web-research · publish');
    expect(stepDetail(ok!)).toBe('shows digest.md');
    expect(edgeLabel({ from: 'a', to: 'b', route: 'changed' })).toBe('changed');
    expect(edgeLabel({ from: 'a', to: 'b', when: 'steps.a.outputs.count > 0' })).toBe('if steps.a.outputs.count > 0');
    expect(edgeLabel({ from: 'a', to: 'b', route: 'changed', when: 'inputs.deep == true and steps.a.outputs.n > 3' })).toBe('changed · inputs.deep == true and ste…');
    expect(edgeLabel({ from: 'a', to: 'b' })).toBeNull();
    expect(danglingRoutes(def, fetch!)).toEqual(['unchanged']);
  });

  it('lays out top to bottom, and keeps saved positions', () => {
    const def = digestDef();
    const auto = autoLayout(def);
    expect(auto[START_ID]!.y).toBeLessThan(auto.fetch!.y);
    expect(auto.fetch!.y).toBeLessThan(auto.sum!.y);
    expect(auto.sum!.y).toBeLessThan(auto.ok!.y);
    const saved = { ...auto, sum: { x: 999, y: 400 } };
    expect(ensureLayout(def, saved)).toBe(saved);
    const partial = ensureLayout(def, { [START_ID]: { x: 0, y: 0 }, fetch: { x: 5, y: 90 } });
    expect(partial.fetch).toEqual({ x: 5, y: 90 });
    expect(partial.ok).toEqual(auto.ok);
  });

  it('builds nodes and edges with problems, route stubs, the selection and run looks', () => {
    const def = digestDef();
    const g = toGraph(def, {}, { startLabel: 'Mondays 08:00', selection: { kind: 'step', id: 'sum' }, issues: { steps: { ok: ['question: required'] }, edges: { 1: ['bad'] }, start: [] }, editable: true });
    expect(g.nodes.map((n) => n.id)).toEqual([START_ID, 'fetch', '__stub:fetch:unchanged', 'sum', 'ok']);
    expect(g.nodes.find((n) => n.id === 'sum')?.selected).toBe(true);
    expect(g.nodes.find((n) => n.id === 'ok')?.data).toMatchObject({ errors: ['question: required'], kind: 'ask me' });
    expect(g.edges.map((e) => [e.id, e.source, e.target, e.data.label])).toEqual([
      ['fetch->__stub:fetch:unchanged', 'fetch', '__stub:fetch:unchanged', null],
      [`${START_ID}->fetch`, START_ID, 'fetch', null],
      ['edge-0', 'fetch', 'sum', 'changed'],
      ['edge-1', 'sum', 'ok', null],
    ]);
    expect(g.edges.find((e) => e.id === 'edge-1')?.data.error).toBe(true);
    const lit = toGraph(def, {}, { startLabel: 's', selection: { kind: 'none' }, run: { steps: { fetch: { tone: 'ok', badge: '✓ 12s', detail: null } }, fired: new Set([0]) }, editable: false });
    expect(lit.edges.find((e) => e.id === 'edge-0')?.data.look).toBe('fired');
    expect(lit.edges.find((e) => e.id === 'edge-1')?.data.look).toBe('idle');
    expect(lit.nodes.find((n) => n.id === 'fetch')).toMatchObject({ deletable: false, data: { run: { badge: '✓ 12s' } } });
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pnpm vitest run packages/ui-core/src/automation-graph.test.ts`
Expected: FAIL: cannot find `./automation-graph`.

- [ ] **Step 4: Write `automation-graph.ts`**

Create `packages/ui-core/src/automation-graph.ts`:

```ts
import { Graph, layout as dagreLayout } from '@dagrejs/dagre';
import type { AutomationDefinition, AutomationEdge, AutomationLayout, Step } from '@desk/protocol';

/** The Start pill's node id and layout key. Step ids start with a letter, so none can collide. */
export const START_ID = '__start';
export const NODE_W = 200;
export const NODE_H = 64;
export const START_W = 240;
export const START_H = 44;

type GraphDef = Pick<AutomationDefinition, 'steps' | 'edges'>;

/** What the inspector shows: the automation (none), the Start pill, a step, or an edge (by index). */
export type GraphSelection = { kind: 'none' } | { kind: 'start' } | { kind: 'step'; id: string } | { kind: 'edge'; index: number };

const EDGE = 'edge-';
export const edgeId = (i: number): string => `${EDGE}${i}`;
export function edgeIndex(id: string): number | null {
  if (!id.startsWith(EDGE)) return null;
  const n = Number(id.slice(EDGE.length));
  return Number.isInteger(n) && n >= 0 ? n : null;
}

/** Steps with no incoming edge: they start when the run starts. */
export function startSteps(def: GraphDef): string[] {
  const targets = new Set(def.edges.map((e) => e.to));
  return def.steps.filter((s) => !targets.has(s.id)).map((s) => s.id);
}

/** Every step upstream of `id`. */
export function ancestors(def: GraphDef, id: string): Set<string> {
  const out = new Set<string>();
  const stack = [id];
  while (stack.length) {
    const cur = stack.pop()!;
    for (const e of def.edges) {
      if (e.to !== cur || e.from === id || out.has(e.from)) continue;
      out.add(e.from);
      stack.push(e.from);
    }
  }
  return out;
}

export const incomingCount = (def: GraphDef, id: string): number => def.edges.filter((e) => e.to === id).length;

const KIND: Record<Step['kind'], string> = { script: 'script', agent: 'agent', ask: 'ask me', wait: 'wait', automation: 'run automation', tell_desk: 'tell desk' };

/** A node's kind line: "agent", or "agent · join all" when two or more edges come in. */
export function kindLabel(step: Step, incoming: number): string {
  return incoming >= 2 ? `${KIND[step.kind]} · join ${step.join}` : KIND[step.kind];
}

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** A node's one-line detail (mockup 2). */
export function stepDetail(step: Step): string {
  const publish = step.publish.length ? ' · publish' : '';
  switch (step.kind) {
    case 'script':
      return (step.skill && step.script ? `${step.skill}: ${step.script.replace(/^scripts\//, '')}` : 'pick a skill and script') + publish;
    case 'agent':
      return (step.skills.length ? `skills: ${step.skills.join(', ')}` : 'no skills') + publish;
    case 'ask':
      return step.show.length ? `shows ${step.show.join(', ')}` : step.question ? clip(step.question, 40) : 'write the question';
    case 'wait':
      return step.until ? `until ${step.until}` : step.minutes ? `${step.minutes} min` : 'set how long';
    case 'automation':
      return step.automation ? `runs ${step.automation}` : 'pick an automation';
    case 'tell_desk':
      return step.text ? `"${clip(step.text, 34)}"` : 'write the message';
  }
}

/** An edge's label: its route and a short form of its condition. */
export function edgeLabel(e: AutomationEdge): string | null {
  const when = e.when ? clip(e.when, 28) : null;
  if (e.route && when) return `${e.route} · ${when}`;
  return e.route ?? (when ? `if ${when}` : null);
}

/** Routes a step declares that no edge takes: each ends its branch, drawn as a dashed "<route> · ends" stub. */
export function danglingRoutes(def: GraphDef, step: Step): string[] {
  const used = new Set(def.edges.filter((e) => e.from === step.id && e.route).map((e) => e.route));
  return step.routes.filter((r) => !used.has(r));
}

/** Top-to-bottom positions for the Start pill and every step (dagre). */
export function autoLayout(def: GraphDef): AutomationLayout {
  const g = new Graph();
  g.setGraph({ rankdir: 'TB', nodesep: 48, ranksep: 56, marginx: 24, marginy: 24 });
  g.setDefaultEdgeLabel(() => ({}));
  g.setNode(START_ID, { width: START_W, height: START_H });
  const ids = new Set(def.steps.map((s) => s.id));
  for (const id of ids) g.setNode(id, { width: NODE_W, height: NODE_H });
  for (const id of startSteps(def)) g.setEdge(START_ID, id);
  for (const e of def.edges) if (ids.has(e.from) && ids.has(e.to) && e.from !== e.to) g.setEdge(e.from, e.to);
  dagreLayout(g);
  const out: AutomationLayout = {};
  for (const id of [START_ID, ...ids]) {
    const n = g.node(id) as { x: number; y: number; width: number; height: number };
    out[id] = { x: Math.round(n.x - n.width / 2), y: Math.round(n.y - n.height / 2) };
  }
  return out;
}

/** The saved positions, with dagre's for any node that has none (all of them for a definition saved without a layout). */
export function ensureLayout(def: GraphDef, layout: AutomationLayout): AutomationLayout {
  const ids = [START_ID, ...def.steps.map((s) => s.id)];
  const missing = ids.filter((id) => !layout[id]);
  if (!missing.length) return layout;
  const auto = autoLayout(def);
  if (missing.length === ids.length) return auto;
  const out = { ...layout };
  for (const id of missing) out[id] = auto[id]!;
  return out;
}

/** A step's look in a run (automation-run-graph.ts computes it). */
export type NodeTone = 'ok' | 'run' | 'wait' | 'pending' | 'skipped' | 'fail' | 'idle';
export type NodeRunState = { tone: NodeTone; badge: string; detail: string | null };

export type StepNodeData = { step: Step; kind: string; detail: string; errors: string[]; run: NodeRunState | null; output: boolean };
export type StartNodeData = { label: string; errors: string[] };
export type StubNodeData = { label: string };

type NodeOf<K extends string, D> = {
  id: string;
  type: K;
  position: { x: number; y: number };
  data: D;
  selected?: boolean;
  deletable?: boolean;
  connectable?: boolean;
  selectable?: boolean;
  draggable?: boolean;
};
export type StepGraphNode = NodeOf<'step', StepNodeData>;
export type StartGraphNode = NodeOf<'start', StartNodeData>;
export type StubGraphNode = NodeOf<'stub', StubNodeData>;
/** A canvas node, shaped like React Flow's `Node` (the desktop passes it as is). */
export type GraphNode = StepGraphNode | StartGraphNode | StubGraphNode;

export type RouteEdgeData = { label: string | null; look: 'plain' | 'fired' | 'idle' | 'stub'; error: boolean };
/** A canvas edge, shaped like React Flow's `Edge`. */
export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
  type: 'route';
  selected?: boolean;
  deletable?: boolean;
  selectable?: boolean;
  data: RouteEdgeData;
};

/** Validation problems by where they show (automation-issues.ts maps paths to these). */
export type GraphIssues = { steps: Record<string, string[]>; edges: Record<number, string[]>; start: string[] };
/** A run's step looks and the edges that fired (by index). */
export type GraphRun = { steps: Record<string, NodeRunState>; fired: ReadonlySet<number> };

/** The canvas for a definition: the Start pill, steps, route stubs, and edges (Start → start steps, then the definition's). */
export function toGraph(
  def: AutomationDefinition,
  layout: AutomationLayout,
  o: { startLabel: string; selection: GraphSelection; editable: boolean; issues?: GraphIssues; run?: GraphRun },
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const pos = ensureLayout(def, layout);
  const sel = o.selection;
  const nodes: GraphNode[] = [
    { id: START_ID, type: 'start', position: pos[START_ID]!, data: { label: o.startLabel, errors: o.issues?.start ?? [] }, selected: sel.kind === 'start', deletable: false, connectable: false },
  ];
  const edges: GraphEdge[] = [];
  for (const step of def.steps) {
    const p = pos[step.id]!;
    nodes.push({
      id: step.id,
      type: 'step',
      position: p,
      data: { step, kind: kindLabel(step, incomingCount(def, step.id)), detail: stepDetail(step), errors: o.issues?.steps[step.id] ?? [], run: o.run?.steps[step.id] ?? null, output: def.output_step === step.id },
      selected: sel.kind === 'step' && sel.id === step.id,
      deletable: o.editable,
    });
    danglingRoutes(def, step).forEach((route, i) => {
      const id = `__stub:${step.id}:${route}`;
      nodes.push({ id, type: 'stub', position: { x: p.x + NODE_W + 36, y: p.y + 6 + i * 26 }, data: { label: `${route} · ends` }, selectable: false, draggable: false, deletable: false, connectable: false });
      edges.push({ id: `${step.id}->${id}`, source: step.id, sourceHandle: 'side', target: id, type: 'route', selectable: false, deletable: false, data: { label: null, look: 'stub', error: false } });
    });
  }
  for (const id of startSteps(def)) {
    edges.push({ id: `${START_ID}->${id}`, source: START_ID, target: id, type: 'route', selectable: false, deletable: false, data: { label: null, look: o.run ? 'fired' : 'plain', error: false } });
  }
  def.edges.forEach((e, i) => {
    edges.push({
      id: edgeId(i),
      source: e.from,
      target: e.to,
      type: 'route',
      selected: sel.kind === 'edge' && sel.index === i,
      deletable: o.editable,
      data: { label: edgeLabel(e), look: o.run ? (o.run.fired.has(i) ? 'fired' : 'idle') : 'plain', error: (o.issues?.edges[i]?.length ?? 0) > 0 },
    });
  });
  return { nodes, edges };
}
```

In `packages/ui-core/src/index.ts`, add `export * from './automation-graph';` after `export * from './automation-grants';`.

- [ ] **Step 5: Run the test to verify it passes**

Run: `pnpm vitest run packages/ui-core`
Expected: PASS.

Run: `pnpm typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
pnpm test
git add packages/ui-core/package.json pnpm-lock.yaml packages/ui-core/src/index.ts packages/ui-core/src/automation-graph.ts packages/ui-core/src/automation-graph.test.ts
git commit -m "feat(ui-core): the automation graph model (nodes and edges from a definition, dagre layout)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Shared logic, part 3: draft operations, validation issues, template suggestions

Pure operations over `{ def, layout }` that the Design view applies to its local draft. Also here: the mapping from validation paths to nodes, and the `{{…}}` suggestions of template fields.

**Files:**
- Create: `packages/ui-core/src/automation-draft.ts`, `automation-issues.ts`, `automation-templates.ts`
- Modify: `packages/ui-core/src/index.ts`
- Test: `packages/ui-core/src/automation-draft.test.ts`, `automation-templates.test.ts`

**Interfaces:**
- Consumes: `ancestors`, `START_ID`, `NODE_W`, `START_W`, `GraphIssues` (Task 4).
- Produces:
  - `automation-draft.ts`:
    - `type AutomationDoc = { def: AutomationDefinition; layout: AutomationLayout }`;
    - `blankDefinition(name)`, `freshStep(kind, id)`, `uniqueStepId(def, kind)`;
    - `addStep(doc, kind, after: string | null): { doc; id }`;
    - `removeSelection(doc, stepIds: string[], edgeIndices: number[]): AutomationDoc`;
    - `canConnect(def, from, to): string | null`, and `connect(doc, from, to): { doc } | { error }`;
    - `patchStep(doc, id, patch: Partial<Step>): AutomationDoc` (a field set to `undefined` is removed);
    - `renameStep(doc, from, to): { doc } | { error }`;
    - `patchEdge(doc, index, { route?, when? }): AutomationDoc`;
    - `setInputs(doc, inputs)`, `renameInput(doc, from, to): { doc } | { error }`, `setTriggers(doc, triggers)`;
    - `type AutomationMeta`, `setMeta(doc, patch)`, `moveNodes(doc, positions)`.
  - `automation-issues.ts`: `type IssueMap = GraphIssues & { general: string[]; count: number }`, and `mapIssues(def, issues: ValidationIssue[]): IssueMap`.
  - `automation-templates.ts`:
    - `type TemplateSuggestion = { path: string; label: string; open?: boolean }`. An open suggestion ends with `.`, and the user types the output key after it: script and sub-automation outputs are known only at run time;
    - `templateSuggestions(def, stepId: string | null)`: the `{{…}}` paths a step may use;
    - `conditionSuggestions(def, from)`: the `when` paths for an edge leaving `from`;
    - `activeToken(text, caret): { start; query } | null`, `filterSuggestions(list, query, max = 8)`, `insertSuggestion(text, caret, s): { text; caret }`;
    - `pathsIn(text): string[]` and `isKnownPath(path, suggestions): boolean`.

- [ ] **Step 1: Write the failing tests**

Create `packages/ui-core/src/automation-draft.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import type { AutomationDefinition } from '@desk/protocol';
import { digestDef } from './testing/automations';
import { addStep, blankDefinition, connect, patchEdge, patchStep, removeSelection, renameInput, renameStep, setMeta, type AutomationDoc } from './automation-draft';
import { START_ID } from './automation-graph';
import { mapIssues } from './automation-issues';

const doc = (def: AutomationDefinition = digestDef()): AutomationDoc => ({ def, layout: { [START_ID]: { x: 0, y: 0 }, fetch: { x: 20, y: 100 }, sum: { x: 20, y: 210 }, ok: { x: 20, y: 320 } } });

describe('automation draft', () => {
  it('starts a blank definition from a name', () => {
    expect(blankDefinition('weekly-note')).toMatchObject({ title: 'Weekly note', steps: [], edges: [], after_run: 'notify', limits: { run_deadline_hours: 24 } });
  });

  it('adds steps below the selected one, connected, with unique ids', () => {
    const one = addStep(doc(), 'agent', 'sum');
    expect(one.id).toBe('agent');
    expect(one.doc.def.edges.at(-1)).toEqual({ from: 'sum', to: 'agent' });
    expect(one.doc.layout.agent).toEqual({ x: 260, y: 320 }); // sum already has a child (ok), so one column to the right
    expect(one.doc.def.steps.at(-1)).toMatchObject({ id: 'agent', kind: 'agent', brief: '', join: 'all', on_error: 'stop', routes: [], publish: [] });
    const two = addStep(one.doc, 'agent', null);
    expect(two.id).toBe('agent_2');
    expect(two.doc.def.edges).toHaveLength(one.doc.def.edges.length);
    expect(two.doc.layout.agent_2!.y).toBe(430);
  });

  it('removes steps with their edges, positions and output step, and edges by index', () => {
    const d = setMeta(doc(), { output_step: 'sum' });
    const out = removeSelection(d, ['sum'], [0]);
    expect(out.def.steps.map((s) => s.id)).toEqual(['fetch', 'ok']);
    expect(out.def.edges).toEqual([]);
    expect(out.layout.sum).toBeUndefined();
    expect(out.def.output_step).toBeUndefined();
    expect(removeSelection(doc(), [], [1]).def.edges).toEqual([{ from: 'fetch', to: 'sum', route: 'changed' }]);
  });

  it('connects steps, refusing loops, duplicates and self-edges', () => {
    expect(connect(doc(), 'fetch', 'ok')).toMatchObject({ doc: { def: { edges: expect.arrayContaining([{ from: 'fetch', to: 'ok' }]) } } });
    expect(connect(doc(), 'ok', 'fetch')).toEqual({ error: 'That would make a loop: an automation runs top to bottom.' });
    expect(connect(doc(), 'fetch', 'sum')).toEqual({ error: 'These steps are already connected.' });
    expect(connect(doc(), 'sum', 'sum')).toEqual({ error: 'A step cannot come after itself.' });
  });

  it('patches a step, removing fields set to undefined', () => {
    const d = addStep(doc(), 'wait', null).doc;
    const w = patchStep(d, 'wait', { minutes: undefined, until: '08:00' }).def.steps.find((s) => s.id === 'wait');
    expect(w).toMatchObject({ until: '08:00' });
    expect(w && 'minutes' in w).toBe(false);
  });

  it('renames a step everywhere: edges, positions, output step and templates', () => {
    const d = setMeta(doc(), { output_step: 'fetch' });
    const withWhen = patchEdge(d, 1, { when: 'steps.fetch.outputs.count > 0' });
    const r = renameStep(withWhen, 'fetch', 'grab');
    if ('error' in r) throw new Error(r.error);
    expect(r.doc.def.steps[0]!.id).toBe('grab');
    expect(r.doc.def.edges[0]).toEqual({ from: 'grab', to: 'sum', route: 'changed' });
    expect(r.doc.def.edges[1]!.when).toBe('steps.grab.outputs.count > 0');
    expect(r.doc.def.output_step).toBe('grab');
    expect(r.doc.layout.grab).toEqual({ x: 20, y: 100 });
    expect(r.doc.def.steps.find((s) => s.id === 'sum')).toMatchObject({ brief: 'Summarise {{steps.grab.dir}}.' });
    expect(renameStep(d, 'fetch', 'sum')).toEqual({ error: 'Another step is already called sum.' });
    expect('error' in renameStep(d, 'fetch', 'Bad Id')).toBe(true);
  });

  it('renames an input in its templates and schedules', () => {
    const d = doc({ ...digestDef(), triggers: [{ kind: 'schedule', cron: '0 8 * * 1', timezone: 'UTC', catch_up: 'once', inputs: { topic: 'ai' } }] });
    const r = renameInput(d, 'topic', 'subject');
    if ('error' in r) throw new Error(r.error);
    expect(r.doc.def.inputs[0]!.key).toBe('subject');
    expect(r.doc.def.steps[0]).toMatchObject({ args: ['{{inputs.subject}}'] });
    expect(r.doc.def.triggers[0]!.inputs).toEqual({ subject: 'ai' });
  });

  it('clears an edge condition set to undefined', () => {
    const d = patchEdge(doc(), 0, { when: 'inputs.topic != ""' });
    expect(patchEdge(d, 0, { when: undefined }).def.edges[0]).toEqual({ from: 'fetch', to: 'sum', route: 'changed' });
  });
});

describe('mapIssues', () => {
  it('puts each problem on its step, edge, the Start pill or the automation', () => {
    const m = mapIssues(digestDef(), [
      { path: 'steps[2].question', message: 'Too small' },
      { path: 'steps[1].brief', message: "steps.ok.summary: 'ok' is not upstream of 'sum'" },
      { path: 'edges[0].route', message: 'fetch has no route x' },
      { path: 'triggers[0].cron', message: 'fires every minute' },
      { path: 'inputs[1].key', message: 'Use lowercase' },
      { path: 'steps', message: 'Add at least one step' },
      { path: 'limits.max_parallel_agents', message: 'Too big' },
    ]);
    expect(m.steps).toEqual({ ok: ['question: Too small'], sum: ["brief: steps.ok.summary: 'ok' is not upstream of 'sum'"] });
    expect(m.edges).toEqual({ 0: ['route: fetch has no route x'] });
    expect(m.start).toEqual(['Schedule 1: cron: fires every minute', 'Input 2: key: Use lowercase']);
    expect(m.general).toEqual(['Add at least one step', 'limits.max_parallel_agents: Too big']);
    expect(m.count).toBe(7);
  });
});
```

Create `packages/ui-core/src/automation-templates.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { digestDef } from './testing/automations';
import { activeToken, conditionSuggestions, insertSuggestion, isKnownPath, pathsIn, templateSuggestions } from './automation-templates';

describe('template suggestions', () => {
  it('offers inputs, upstream results, the run and previous runs', () => {
    const paths = templateSuggestions(digestDef(), 'ok').map((s) => s.path);
    expect(paths).toEqual(expect.arrayContaining(['inputs.topic', 'steps.fetch.summary', 'steps.fetch.dir', 'steps.fetch.route', 'steps.fetch.outputs.', 'steps.sum.outputs.headline', 'run.date', 'previous.steps.fetch.dir', 'previous.steps.ok.dir']));
    expect(paths).not.toContain('steps.ok.summary');
    expect(templateSuggestions(digestDef(), 'fetch').map((s) => s.path).filter((p) => p.startsWith('steps.'))).toEqual([]);
  });

  it('offers condition paths from the edge’s source and what is upstream of it', () => {
    const paths = conditionSuggestions(digestDef(), 'sum').map((s) => s.path);
    expect(paths).toEqual(expect.arrayContaining(['inputs.topic', 'steps.sum.status', 'steps.sum.outputs.headline', 'steps.fetch.route', 'steps.fetch.status']));
    expect(paths).not.toContain('steps.ok.status');
  });

  it('finds the partial path being typed and inserts a suggestion', () => {
    expect(activeToken('Hi {{inp', 8)).toEqual({ start: 3, query: 'inp' });
    expect(activeToken('Hi {{inputs.a}} x', 17)).toBeNull();
    expect(activeToken('Hi {{ in put', 12)).toBeNull();
    expect(insertSuggestion('Hi {{inp', 8, { path: 'inputs.topic', label: 'Topic' })).toEqual({ text: 'Hi {{inputs.topic}}', caret: 19 });
    expect(insertSuggestion('Hi {{inp}} there', 8, { path: 'inputs.topic', label: 'Topic' })).toEqual({ text: 'Hi {{inputs.topic}} there', caret: 19 });
    expect(insertSuggestion('{{steps.f', 9, { path: 'steps.fetch.outputs.', label: 'x', open: true })).toEqual({ text: '{{steps.fetch.outputs.', caret: 22 });
  });

  it('lists the paths a text uses, and knows which are available here', () => {
    const s = templateSuggestions(digestDef(), 'ok');
    expect(pathsIn('a {{inputs.topic}} b {{ steps.fetch.outputs.count }} {{inputs.topic}}')).toEqual(['inputs.topic', 'steps.fetch.outputs.count']);
    expect(isKnownPath('steps.fetch.outputs.count', s)).toBe(true);
    expect(isKnownPath('steps.sum.outputs.nope', s)).toBe(false);
    expect(isKnownPath('previous.steps.sum.outputs.headline', s)).toBe(true);
    expect(isKnownPath('previous.steps.ghost.dir', s)).toBe(false);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm vitest run packages/ui-core/src/automation-draft.test.ts packages/ui-core/src/automation-templates.test.ts`
Expected: FAIL: the modules do not exist.

- [ ] **Step 3: Write the modules**

Create `packages/ui-core/src/automation-draft.ts`:

```ts
import { InputKey, StepId, type AutomationDefinition, type AutomationEdge, type AutomationLayout, type InputSpec, type ScheduleTrigger, type Step, type StepKind } from '@desk/protocol';
import { ancestors, NODE_W, START_ID, START_W } from './automation-graph';

/** What the Design view edits: the definition and the node positions. */
export type AutomationDoc = { def: AutomationDefinition; layout: AutomationLayout };

const GAP_Y = 110;
const GAP_X = NODE_W + 40;

const dropUndefined = <T extends object>(o: T): T => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined)) as T;
const firstMessage = (r: { success: false; error: { issues: Array<{ message: string }> } }) => r.error.issues[0]?.message ?? 'Not valid';

/** A new automation's definition: the title from its name, no steps yet (saving needs one). */
export function blankDefinition(name: string): AutomationDefinition {
  const words = name.replace(/-+/g, ' ').trim();
  return {
    title: words ? `${words[0]!.toUpperCase()}${words.slice(1)}` : name,
    description: '',
    inputs: [],
    triggers: [],
    steps: [],
    edges: [],
    after_run: 'notify',
    limits: { run_deadline_hours: 24, max_parallel_agents: 2, max_parallel_scripts: 4 },
  };
}

const common = () => ({ join: 'all' as const, on_error: 'stop' as const, routes: [] as string[], publish: [] as string[] });

/** A new step of a kind, with every default filled so the draft compares equal to what the daemon returns. */
export function freshStep(kind: StepKind, id: string): Step {
  switch (kind) {
    case 'script':
      return { ...common(), id, kind, title: 'New script step', skill: '', script: '', args: [], idempotent: false };
    case 'agent':
      return { ...common(), id, kind, title: 'New agent step', brief: '', skills: [], output_keys: [] };
    case 'ask':
      return { ...common(), id, kind, title: 'Ask me', question: '', show: [] };
    case 'wait':
      return { ...common(), id, kind, title: 'Wait', minutes: 60 };
    case 'automation':
      return { ...common(), id, kind, title: 'Run another automation', automation: '', inputs: {} };
    case 'tell_desk':
      return { ...common(), id, kind, title: 'Tell Desk', text: '', attach: [] };
  }
}

/** `agent`, then `agent_2`, `agent_3`… */
export function uniqueStepId(def: AutomationDefinition, kind: StepKind): string {
  const ids = new Set(def.steps.map((s) => s.id));
  if (!ids.has(kind)) return kind;
  for (let n = 2; ; n++) if (!ids.has(`${kind}_${n}`)) return `${kind}_${n}`;
}

/** Adds a step. After a step, it is connected below it (to the right of that step's other children); otherwise it goes under everything. */
export function addStep(doc: AutomationDoc, kind: StepKind, after: string | null): { doc: AutomationDoc; id: string } {
  const id = uniqueStepId(doc.def, kind);
  const parent = after && doc.def.steps.some((s) => s.id === after) ? after : null;
  const anchor = parent ? doc.layout[parent] : undefined;
  const lowest = Object.values(doc.layout).reduce((m, p) => Math.max(m, p.y), 0);
  const siblings = parent ? doc.def.edges.filter((e) => e.from === parent).length : 0;
  const position = anchor ? { x: anchor.x + siblings * GAP_X, y: anchor.y + GAP_Y } : { x: (doc.layout[START_ID]?.x ?? 0) + (START_W - NODE_W) / 2, y: lowest + GAP_Y };
  return {
    id,
    doc: {
      def: { ...doc.def, steps: [...doc.def.steps, freshStep(kind, id)], edges: parent ? [...doc.def.edges, { from: parent, to: id }] : doc.def.edges },
      layout: { ...doc.layout, [id]: position },
    },
  };
}

/** Removes steps (with their edges and positions, and the output step if it was one) and edges by index, both from the same definition. */
export function removeSelection(doc: AutomationDoc, stepIds: string[], edgeIndices: number[]): AutomationDoc {
  const gone = new Set(stepIds);
  const cut = new Set(edgeIndices);
  const { output_step, ...rest } = doc.def;
  const def: AutomationDefinition = {
    ...rest,
    steps: doc.def.steps.filter((s) => !gone.has(s.id)),
    edges: doc.def.edges.filter((e, i) => !cut.has(i) && !gone.has(e.from) && !gone.has(e.to)),
    ...(output_step && !gone.has(output_step) ? { output_step } : {}),
  };
  return { def, layout: Object.fromEntries(Object.entries(doc.layout).filter(([k]) => !gone.has(k))) };
}

/** Why `from` → `to` cannot be added, or null. */
export function canConnect(def: AutomationDefinition, from: string, to: string): string | null {
  if (from === to) return 'A step cannot come after itself.';
  if (!def.steps.some((s) => s.id === from) || !def.steps.some((s) => s.id === to)) return 'Connect two steps.';
  if (def.edges.some((e) => e.from === from && e.to === to)) return 'These steps are already connected.';
  if (ancestors(def, from).has(to)) return 'That would make a loop: an automation runs top to bottom.';
  return null;
}

export function connect(doc: AutomationDoc, from: string, to: string): { doc: AutomationDoc } | { error: string } {
  const error = canConnect(doc.def, from, to);
  return error ? { error } : { doc: { ...doc, def: { ...doc.def, edges: [...doc.def.edges, { from, to }] } } };
}

/** Merges fields into one step (never its id or kind). A field set to undefined is removed. */
export function patchStep(doc: AutomationDoc, id: string, patch: Partial<Step>): AutomationDoc {
  return { ...doc, def: { ...doc.def, steps: doc.def.steps.map((s) => (s.id === id ? (dropUndefined({ ...s, ...patch, id: s.id, kind: s.kind }) as Step) : s)) } };
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Every templated text of a step, mapped. */
function mapText(step: Step, fn: (text: string) => string): Step {
  switch (step.kind) {
    case 'script':
      return { ...step, args: step.args.map(fn), ...(step.stdin !== undefined ? { stdin: fn(step.stdin) } : {}) };
    case 'agent':
      return { ...step, brief: fn(step.brief) };
    case 'ask':
      return { ...step, question: fn(step.question) };
    case 'automation':
      return { ...step, inputs: Object.fromEntries(Object.entries(step.inputs).map(([k, v]) => [k, fn(v)])) };
    case 'tell_desk':
      return { ...step, text: fn(step.text) };
    default:
      return step;
  }
}

/** Renames a step and every reference to it: edges, positions, the output step, `steps.<id>.` and `previous.steps.<id>.` in templates and conditions. */
export function renameStep(doc: AutomationDoc, from: string, to: string): { doc: AutomationDoc } | { error: string } {
  if (from === to) return { doc };
  const parsed = StepId.safeParse(to);
  if (!parsed.success) return { error: firstMessage(parsed) };
  if (doc.def.steps.some((s) => s.id === to)) return { error: `Another step is already called ${to}.` };
  const refs = new RegExp(`(^|[^a-z0-9_.-])((?:previous\\.)?steps\\.)${escape(from)}(?=\\.)`, 'g');
  const fix = (text: string) => text.replace(refs, `$1$2${to}`);
  const rename = (id: string) => (id === from ? to : id);
  const def: AutomationDefinition = {
    ...doc.def,
    steps: doc.def.steps.map((s) => mapText(s.id === from ? { ...s, id: to } : s, fix)),
    edges: doc.def.edges.map((e) => ({ ...e, from: rename(e.from), to: rename(e.to), ...(e.when ? { when: fix(e.when) } : {}) })),
    ...(doc.def.output_step ? { output_step: rename(doc.def.output_step) } : {}),
  };
  return { doc: { def, layout: Object.fromEntries(Object.entries(doc.layout).map(([k, v]) => [rename(k), v])) } };
}

/** Sets or clears an edge's route and condition. */
export function patchEdge(doc: AutomationDoc, index: number, patch: { route?: string | undefined; when?: string | undefined }): AutomationDoc {
  return { ...doc, def: { ...doc.def, edges: doc.def.edges.map((e, i) => (i === index ? (dropUndefined({ ...e, ...patch }) as AutomationEdge) : e)) } };
}

export const setInputs = (doc: AutomationDoc, inputs: InputSpec[]): AutomationDoc => ({ ...doc, def: { ...doc.def, inputs } });

/** Renames an input and `inputs.<key>` in templates, conditions and schedule inputs. */
export function renameInput(doc: AutomationDoc, from: string, to: string): { doc: AutomationDoc } | { error: string } {
  if (from === to) return { doc };
  const parsed = InputKey.safeParse(to);
  if (!parsed.success) return { error: firstMessage(parsed) };
  if (doc.def.inputs.some((i) => i.key === to)) return { error: `Another input is already called ${to}.` };
  const refs = new RegExp(`(^|[^a-z0-9_.-])inputs\\.${escape(from)}(?![a-z0-9_])`, 'g');
  const fix = (text: string) => text.replace(refs, `$1inputs.${to}`);
  const def: AutomationDefinition = {
    ...doc.def,
    inputs: doc.def.inputs.map((i) => (i.key === from ? { ...i, key: to } : i)),
    steps: doc.def.steps.map((s) => mapText(s, fix)),
    edges: doc.def.edges.map((e) => (e.when ? { ...e, when: fix(e.when) } : e)),
    triggers: doc.def.triggers.map((t) => (t.inputs ? { ...t, inputs: Object.fromEntries(Object.entries(t.inputs).map(([k, v]) => [k === from ? to : k, v])) } : t)),
  };
  return { doc: { ...doc, def } };
}

export const setTriggers = (doc: AutomationDoc, triggers: ScheduleTrigger[]): AutomationDoc => ({ ...doc, def: { ...doc.def, triggers } });

/** Automation-level settings. `output_step: undefined` clears it. */
export type AutomationMeta = Pick<AutomationDefinition, 'title' | 'description' | 'after_run' | 'limits'> & { output_step?: string | undefined };
export const setMeta = (doc: AutomationDoc, patch: Partial<AutomationMeta>): AutomationDoc => ({ ...doc, def: dropUndefined({ ...doc.def, ...patch }) as AutomationDefinition });

export const moveNodes = (doc: AutomationDoc, positions: AutomationLayout): AutomationDoc => ({ ...doc, layout: { ...doc.layout, ...positions } });
```

Create `packages/ui-core/src/automation-issues.ts`:

```ts
import type { AutomationDefinition, ValidationIssue } from '@desk/protocol';
import type { GraphIssues } from './automation-graph';

/** Validation issues by where the editor shows them: on a step, an edge (by index), the Start pill (inputs, schedules), or the automation. */
export type IssueMap = GraphIssues & { general: string[]; count: number };

const withField = (field: string, message: string) => (field && !message.startsWith(field) ? `${field}: ${message}` : message);

export function mapIssues(def: AutomationDefinition, issues: ValidationIssue[]): IssueMap {
  const out: IssueMap = { steps: {}, edges: {}, start: [], general: [], count: issues.length };
  for (const { path, message } of issues) {
    const step = /^steps\[(\d+)\]\.?(.*)$/.exec(path);
    if (step) {
      const id = def.steps[Number(step[1])]?.id;
      if (id) (out.steps[id] ??= []).push(withField(step[2] ?? '', message));
      else out.general.push(message);
      continue;
    }
    const edge = /^edges\[(\d+)\]\.?(.*)$/.exec(path);
    if (edge) {
      (out.edges[Number(edge[1])] ??= []).push(withField(edge[2] ?? '', message));
      continue;
    }
    const start = /^(inputs|triggers)\[(\d+)\]\.?(.*)$/.exec(path);
    if (start) {
      out.start.push(`${start[1] === 'inputs' ? 'Input' : 'Schedule'} ${Number(start[2]) + 1}: ${withField(start[3] ?? '', message)}`);
      continue;
    }
    out.general.push(path && path !== 'steps' ? withField(path, message) : message);
  }
  return out;
}
```

Create `packages/ui-core/src/automation-templates.ts`:

```ts
import type { AutomationDefinition, Step } from '@desk/protocol';
import { ancestors } from './automation-graph';

/** A path to offer. `open` paths end with `.`: the user names the output after it (scripts and sub-automations decide theirs at run time). */
export type TemplateSuggestion = { path: string; label: string; open?: boolean };

const RUN: TemplateSuggestion[] = [
  { path: 'run.date', label: "the run's date (YYYY-MM-DD)" },
  { path: 'run.dir', label: 'the run folder' },
  { path: 'run.id', label: 'the run id' },
  { path: 'run.trigger', label: 'what started the run' },
  { path: 'run.test', label: 'true in a test run' },
];

/** Outputs other steps can name, as the validator knows them (spec §3.3). */
function outputKeys(s: Step): Array<{ key: string; label: string }> {
  if (s.kind === 'agent') return s.output_keys.map((k) => ({ key: k.key, label: k.description }));
  if (s.kind === 'ask') return [{ key: 'note', label: 'your note' }];
  if (s.kind === 'automation') return [{ key: 'run_id', label: 'the child run id' }];
  return [];
}

const opensOutputs = (s: Step) => s.kind === 'script' || s.kind === 'automation';

/** What `{{…}}` may name in a step: inputs, results of steps upstream of it, the run, and the last good run's step folders. */
export function templateSuggestions(def: AutomationDefinition, stepId: string | null): TemplateSuggestion[] {
  const out: TemplateSuggestion[] = def.inputs.map((i) => ({ path: `inputs.${i.key}`, label: i.label }));
  const up = stepId ? ancestors(def, stepId) : new Set<string>();
  for (const s of def.steps) {
    if (!up.has(s.id)) continue;
    out.push({ path: `steps.${s.id}.summary`, label: `${s.title}: its summary` }, { path: `steps.${s.id}.dir`, label: `${s.title}: its folder` });
    if (s.routes.length || s.kind === 'ask' || s.on_error === 'continue') out.push({ path: `steps.${s.id}.route`, label: `${s.title}: the route it took` });
    for (const k of outputKeys(s)) out.push({ path: `steps.${s.id}.outputs.${k.key}`, label: `${s.title}: ${k.label}` });
    if (opensOutputs(s)) out.push({ path: `steps.${s.id}.outputs.`, label: `${s.title}: an output (type its name)`, open: true });
  }
  out.push(...RUN);
  for (const s of def.steps) out.push({ path: `previous.steps.${s.id}.dir`, label: `${s.title}: its folder in the last good run` });
  return out;
}

/** What a `when` on an edge leaving `from` may name: inputs, and the status, route and outputs of `from` and the steps upstream of it. */
export function conditionSuggestions(def: AutomationDefinition, from: string): TemplateSuggestion[] {
  const out: TemplateSuggestion[] = def.inputs.map((i) => ({ path: `inputs.${i.key}`, label: i.label }));
  const reach = new Set([from, ...ancestors(def, from)]);
  for (const s of def.steps) {
    if (!reach.has(s.id)) continue;
    out.push({ path: `steps.${s.id}.status`, label: `${s.title}: status` }, { path: `steps.${s.id}.route`, label: `${s.title}: route` });
    for (const k of outputKeys(s)) out.push({ path: `steps.${s.id}.outputs.${k.key}`, label: `${s.title}: ${k.label}` });
    if (opensOutputs(s)) out.push({ path: `steps.${s.id}.outputs.`, label: `${s.title}: an output (type its name)`, open: true });
  }
  return out;
}

/** The partial path typed after an unclosed `{{` before the caret, or null. */
export function activeToken(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, caret);
  const open = before.lastIndexOf('{{');
  if (open === -1 || before.indexOf('}}', open) !== -1) return null;
  const query = before.slice(open + 2).trimStart();
  return /^[a-z0-9_.-]*$/.test(query) ? { start: open, query } : null;
}

/** Suggestions matching a partial path, those starting with it first. */
export function filterSuggestions(list: TemplateSuggestion[], query: string, max = 8): TemplateSuggestion[] {
  const q = query.toLowerCase();
  return list
    .filter((s) => s.path.includes(q) || s.label.toLowerCase().includes(q))
    .sort((a, b) => Number(b.path.startsWith(q)) - Number(a.path.startsWith(q)))
    .slice(0, max);
}

/** Replaces the typed `{{partial` with `{{path}}` (reusing a `}}` right after the caret). An open path stays open for its key. */
export function insertSuggestion(text: string, caret: number, s: TemplateSuggestion): { text: string; caret: number } {
  const t = activeToken(text, caret);
  if (!t) return { text, caret };
  const after = text.slice(caret);
  const insert = s.open ? `{{${s.path}` : `{{${s.path}}}`;
  const rest = !s.open && after.startsWith('}}') ? after.slice(2) : after;
  return { text: text.slice(0, t.start) + insert + rest, caret: t.start + insert.length };
}

/** The `{{path}}`s a text uses, each once, in order. */
export function pathsIn(text: string): string[] {
  const out = new Set<string>();
  for (const m of text.matchAll(/\{\{\s*([^{}]+?)\s*\}\}/g)) out.add(m[1]!);
  return [...out];
}

/** Whether a path is one of the suggestions (an open one followed by a key), or a previous run's output of a known step. */
export function isKnownPath(path: string, suggestions: TemplateSuggestion[]): boolean {
  if (suggestions.some((s) => (s.open ? path.startsWith(s.path) && /^[a-z][a-z0-9_]*$/.test(path.slice(s.path.length)) : s.path === path))) return true;
  const prev = /^previous\.steps\.([a-z][a-z0-9_-]*)\.outputs\.[a-z][a-z0-9_]*$/.exec(path);
  return !!prev && suggestions.some((s) => s.path === `previous.steps.${prev[1]}.dir`);
}
```

In `packages/ui-core/src/index.ts`, add in alphabetical order among the other automation modules:

```ts
export * from './automation-draft';
export * from './automation-issues';
export * from './automation-templates';
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pnpm vitest run packages/ui-core`
Expected: PASS.

Run: `pnpm typecheck`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
pnpm test
git add packages/ui-core/src/index.ts packages/ui-core/src/automation-draft.ts packages/ui-core/src/automation-issues.ts packages/ui-core/src/automation-templates.ts packages/ui-core/src/automation-draft.test.ts packages/ui-core/src/automation-templates.test.ts
git commit -m "feat(ui-core): automation draft operations, validation issues and template suggestions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Desktop data hooks and dialogs: Run now / Test, Turn on, Name

**Files:**
- Create: `apps/desktop/src/renderer/automations/live.ts`, `apps/desktop/src/renderer/automations/data.ts`
- Create: `apps/desktop/src/renderer/automations/dialogs/RunDialog.tsx`, `TurnOnDialog.tsx`, `NameDialog.tsx`
- Create: `packages/ui-styles/src/automations.css`
- Modify: `packages/ui-styles/src/index.css`, `packages/ui-styles/src/index.test.ts`
- Test: `apps/desktop/src/renderer/automations/live.test.tsx`, `apps/desktop/src/renderer/automations/dialogs/dialogs.test.tsx`

**Interfaces:**
- Consumes:
  - `call('automations.run' | 'automations.validate' | 'automations.setGrants' | 'automations.setEnabled' | 'app.pickFile' | 'app.pickFolder')` (Task 2);
  - from `@desk/ui-core`: `eventsAfter`, the live rules, `initialValues`, `runInputs`, `InputDraft`, `describeGrant`, `sameGrant`, `uniqueGrants`, `describeSchedule`, `dayTime` (Task 3);
  - from `../state/session`: `SessionState` (`events`, `status`).
- Produces:
  - `live.ts`:
    - `type Live<T> = { status: 'loading'|'ready'|'missing'|'error'; value: T | null; error: string | null; reload(): void; replace(value: T): void }`;
    - `useLive<T>(o: LiveOptions<T>): Live<T>`.
  - `data.ts`:
    - `useAutomationList(projectId, s): Live<AutomationSummary[]>`;
    - `useAutomation(s, id): Live<AutomationDetail>`;
    - `useRuns(s, id, limit): Live<RunListEntry[]>`;
    - `useRun(s, runId): Live<RunDetail>`.
  - Dialogs:
    - `RunDialog({ target: RunTarget; test: boolean; onClose(); onStarted(runId: string) })`, where `type RunTarget = { id: string; name: string; title: string; definition: AutomationDefinition }` (an `AutomationDetail` fits);
    - `TurnOnDialog({ detail: AutomationDetail; onClose(); onDone(detail: AutomationDetail); onTestFirst() })`;
    - `NameDialog({ title; confirmLabel; initial?; taken: string[]; hint?; onClose(); onConfirm(name: string) })`.
  - CSS classes: `.auto-warn`, `.auto-sub`, `.auto-plain`, `.auto-check`, `.auto-quote`, `.auto-pick`.

- [ ] **Step 1: Write the failing tests**

Create `apps/desktop/src/renderer/automations/live.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { DeskCallError } from '../bridge';
import { useLive } from './live';

afterEach(cleanup);

type V = { n: number; seen: number[] };
const moved = (id: number) => ev(id, 'automation.layout_saved', { automation_id: 'a', layout: {} });
const switched = (id: number) => ev(id, 'automation.switched', { automation_id: 'a', enabled: true, by: 'user' });
const reduce = (v: V, e: StoredEvent): V => ({ ...v, seen: [...v.seen, e.id] });
const refetch = (e: StoredEvent) => e.type === 'automation.switched';

function Probe(p: { id: string; events: StoredEvent[]; ready: boolean; load(): Promise<V>; reduce?: (v: V, e: StoredEvent) => V }) {
  const live = useLive({ key: p.id, events: p.events, ready: p.ready, load: p.load, ...(p.reduce ? { reduce: p.reduce } : {}), refetch, debounceMs: 10 });
  return <output>{`${live.status}:${JSON.stringify(live.value)}`}</output>;
}

describe('useLive', () => {
  it('waits for the session, folds later events at once, and reloads once for a burst of matching ones', async () => {
    let n = 0;
    const load = vi.fn(async (): Promise<V> => ({ n: ++n, seen: [] }));
    const { rerender } = render(<Probe id="a" events={[]} ready={false} load={load} reduce={reduce} />);
    expect(load).not.toHaveBeenCalled();
    rerender(<Probe id="a" events={[moved(1)]} ready load={load} reduce={reduce} />);
    await screen.findByText('ready:{"n":1,"seen":[]}');
    rerender(<Probe id="a" events={[moved(1), moved(2)]} ready load={load} reduce={reduce} />);
    await screen.findByText('ready:{"n":1,"seen":[2]}');
    rerender(<Probe id="a" events={[moved(1), moved(2), switched(3), switched(4)]} ready load={load} reduce={reduce} />);
    await screen.findByText('ready:{"n":2,"seen":[]}');
    expect(load).toHaveBeenCalledTimes(2);
  });

  it('reports a 404 as missing', async () => {
    const load = vi.fn(async (): Promise<V> => {
      throw new DeskCallError({ code: 'not_found', message: 'gone', status: 404 });
    });
    render(<Probe id="a" events={[]} ready load={load} />);
    await screen.findByText('missing:null');
  });
});
```

Create `apps/desktop/src/renderer/automations/dialogs/dialogs.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AutomationDefinition, Grant } from '@desk/protocol';
import { automationDetail, digestDef } from '@desk/ui-core/testing';
import { installBridge } from '../../test/bridge';
import { NameDialog } from './NameDialog';
import { RunDialog } from './RunDialog';
import { TurnOnDialog } from './TurnOnDialog';

afterEach(cleanup);

const withInputs = (): AutomationDefinition => ({
  ...digestDef(),
  inputs: [
    { key: 'topic', label: 'Topic', type: 'text', required: true },
    { key: 'tone', label: 'Tone', type: 'choice', required: false, options: ['dry', 'warm'], default: 'dry' },
    { key: 'count', label: 'How many', type: 'number', required: false },
    { key: 'brief', label: 'Brief file', type: 'file', required: false },
    { key: 'deep', label: 'Go deep', type: 'boolean', required: false },
  ],
});

describe('RunDialog', () => {
  it('builds the form from the inputs, picks a file, and starts a test run', async () => {
    const bridge = installBridge({ 'app.pickFile': () => '/Users/me/brief.pdf', 'automations.run': () => ({ run_id: 'r9' }) });
    const onStarted = vi.fn();
    render(<RunDialog target={{ id: 'a1', name: 'digest', title: 'Weekly digest', definition: withInputs() }} test onClose={() => {}} onStarted={onStarted} />);
    expect(screen.getByRole('dialog', { name: 'Test Weekly digest' })).toBeTruthy();
    const start = screen.getByRole('button', { name: 'Start test' }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Topic'), { target: { value: 'robots' } });
    fireEvent.change(screen.getByLabelText('How many (optional)'), { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Choose…' }));
    await waitFor(() => expect((screen.getByLabelText('Brief file (optional)') as HTMLInputElement).value).toBe('/Users/me/brief.pdf'));
    fireEvent.click(screen.getByLabelText('Go deep'));
    fireEvent.click(start);
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith('r9'));
    expect(bridge.calls.find((c) => c.channel === 'automations.run')?.input).toEqual({
      id: 'a1',
      req: { inputs: { topic: 'robots', tone: 'dry', count: 2, brief: '/Users/me/brief.pdf', deep: true }, test: true },
    });
  });
});

describe('TurnOnDialog', () => {
  it('shows the schedule with its next time, ticks proposed grants, warns when untested, and sets grants before the switch', async () => {
    const proposed: Grant[] = [
      { tool: 'web_fetch', match: { domain: 'acme.com' }, action: 'allow' },
      { tool: 'bash', match: { command: '^ls$' }, action: 'allow' },
    ];
    const bridge = installBridge({
      'automations.validate': () => ({ errors: [], warnings: [], next_times: { '0': ['2026-10-05T06:00:00.000Z'] } }),
      'automations.setGrants': () => automationDetail(),
      'automations.setEnabled': () => automationDetail({ enabled: true }),
    });
    const onDone = vi.fn();
    const onTestFirst = vi.fn();
    const detail = automationDetail({ enabled: false, proposed_grants: proposed, enable_request: { note: 'Tested with robots.', proposed_grants: [proposed[0]!], at: 't' } });
    render(<TurnOnDialog detail={detail} onClose={() => {}} onDone={onDone} onTestFirst={onTestFirst} />);
    expect(screen.getByRole('alert').textContent).toBe("v7 hasn't been tested (v6 was).");
    expect(await screen.findByText(/· next/)).toBeTruthy();
    expect(screen.getByText('Desk: Tested with robots.')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Allow bash matching ^ls$'));
    fireEvent.click(screen.getByRole('button', { name: 'Test first' }));
    expect(onTestFirst).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Turn on anyway' }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(bridge.calls.map((c) => c.channel).filter((c) => c.startsWith('automations.set'))).toEqual(['automations.setGrants', 'automations.setEnabled']);
    expect(bridge.calls.find((c) => c.channel === 'automations.setGrants')?.input).toEqual({ id: 'a1', grants: [proposed[0]], reason: 'enabled' });
  });
});

describe('NameDialog', () => {
  it('explains a bad or taken name and confirms a good one', () => {
    const onConfirm = vi.fn();
    render(<NameDialog title="New automation" confirmLabel="Create" taken={['digest']} onClose={() => {}} onConfirm={onConfirm} />);
    const input = screen.getByLabelText('Name') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'digest' } });
    expect(screen.getByRole('alert').textContent).toBe('Another automation already has this name.');
    fireEvent.change(input, { target: { value: '-bad' } });
    expect(screen.getByRole('alert').textContent).toMatch(/lowercase/i);
    fireEvent.change(input, { target: { value: 'Weekly Note' } });
    expect(input.value).toBe('weekly-note');
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(onConfirm).toHaveBeenCalledWith('weekly-note');
  });
});
```

In `packages/ui-styles/src/index.test.ts`, change `SHEETS` to:

```ts
const SHEETS = ['tokens', 'attention', 'automations', 'conversation', 'knowledge', 'map', 'settings', 'skills', 'system', 'threads'];
```

and its doc comment to `/** The Electron renderer's cascade: tokens, then each screen's sheet (automations joined in Plan 20, after attention). */`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm vitest run apps/desktop/src/renderer/automations packages/ui-styles`
Expected: FAIL: the modules and `automations.css` do not exist.

- [ ] **Step 3: Start the stylesheet**

In `packages/ui-styles/src/index.css`, add `@import './automations.css';` right after `@import './attention.css';`.

Create `packages/ui-styles/src/automations.css`:

```css
/* Automations (spec 2026-09-26-automations-design §8). Colors come from tokens only. */

/* Dialogs */
.auto-warn {
  margin: 0 0 12px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--wait-pastel);
  color: var(--wait-text);
  font-size: 13px;
}
.auto-sub {
  margin: 14px 0 6px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
}
.auto-plain {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}
.auto-check {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  font-size: 13px;
  color: var(--ink);
}
.auto-quote {
  margin: 10px 0;
  padding: 6px 10px;
  border-left: 3px solid var(--rule-strong);
  color: var(--text);
  font-size: 13px;
}
.auto-pick {
  display: flex;
  gap: 8px;
  align-items: center;
}
.auto-pick .input {
  flex: 1;
  min-width: 0;
}
```

Every `var(--…)` in this plan is one of the tokens `tokens.css` defines (in both themes): `--ground`, `--rule`, `--rule-soft`, `--rule-strong`, `--ink`, `--text`, `--text-faint`, `--run`, `--run-text`, `--run-pastel`, `--run-ring`, `--wait`, `--wait-text`, `--wait-pastel`, `--muted`, `--muted-soft`, `--accent`, `--accent-tint`, `--ok`, `--card`, `--raised`, `--sunken`, `--paper`, `--highlight`, `--on-ink`, `--shadow-rgb`, `--card-shadow`, `--add-bg`, `--del-bg`, `--code-bg`, `--font-serif`, `--font-mono`. There is no `--ok-pastel` or `--ok-text`: use `--add-bg` and `--ok`.

- [ ] **Step 4: Write the hooks**

Create `apps/desktop/src/renderer/automations/live.ts`:

```ts
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { StoredEvent } from '@desk/protocol';
import { eventsAfter } from '@desk/ui-core';
import { DeskCallError } from '../bridge';

export type LiveStatus = 'loading' | 'ready' | 'missing' | 'error';

export type Live<T> = {
  status: LiveStatus;
  value: T | null;
  error: string | null;
  /** Loads again, keeping the current value on screen meanwhile. */
  reload(): void;
  /** Adopts a value a write returned, as of the latest event. */
  replace(value: T): void;
};

export type LiveOptions<T> = {
  /** A new key loads from scratch. */
  key: string;
  /** The project session's log, in id order. */
  events: readonly StoredEvent[];
  /** Whether the session has its backfill: loading waits for it, so the snapshot's cursor means something. */
  ready: boolean;
  load(): Promise<T>;
  /** Folds one later event into the snapshot. Pass a module-level function: it is a memo dependency. */
  reduce?: (value: T, e: StoredEvent) => T;
  /** Whether an event means the snapshot should be loaded again. */
  refetch(e: StoredEvent, value: T): boolean;
  debounceMs?: number;
};

type Snap<T> = { key: string; value: T; cursor: number };

/**
 * A daemon snapshot kept current from the project's event log: events after the snapshot fold in at once through
 * `reduce`, and events `refetch` picks reload it (debounced), so fields no reducer can derive catch up.
 */
export function useLive<T>(o: LiveOptions<T>): Live<T> {
  const [snap, setSnap] = useState<Snap<T> | null>(null);
  const [status, setStatus] = useState<LiveStatus>('loading');
  const [error, setError] = useState<string | null>(null);
  const opts = useRef(o);
  opts.current = o;
  const gen = useRef(0);
  const seen = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const fetchNow = useCallback((fresh: boolean) => {
    const g = ++gen.current;
    const key = opts.current.key;
    const cursor = opts.current.events.at(-1)?.id ?? 0;
    if (fresh) {
      setSnap(null);
      setStatus('loading');
    }
    opts.current.load().then(
      (value) => {
        if (g !== gen.current) return;
        seen.current = Math.max(seen.current, cursor);
        setSnap({ key, value, cursor });
        setStatus('ready');
        setError(null);
      },
      (err: unknown) => {
        if (g !== gen.current) return;
        if (err instanceof DeskCallError && err.status === 404) {
          setStatus('missing');
          return;
        }
        setError(err instanceof Error ? err.message : String(err));
        setStatus((s) => (s === 'ready' ? s : 'error'));
      },
    );
  }, []);

  useEffect(() => {
    seen.current = 0;
    if (o.ready) fetchNow(true);
    return () => {
      gen.current++;
      clearTimeout(timer.current);
    };
  }, [o.key, o.ready, fetchNow]);

  useEffect(() => {
    if (!snap || snap.key !== o.key) return;
    const fresh = eventsAfter(o.events, seen.current);
    if (!fresh.length) return;
    seen.current = fresh.at(-1)!.id;
    if (!fresh.some((e) => opts.current.refetch(e, snap.value))) return;
    clearTimeout(timer.current);
    timer.current = setTimeout(() => fetchNow(false), opts.current.debounceMs ?? 300);
  }, [o.events, o.key, snap, fetchNow]);

  const reduce = o.reduce;
  const value = useMemo(() => {
    if (!snap || snap.key !== o.key) return null;
    return reduce ? eventsAfter(o.events, snap.cursor).reduce(reduce, snap.value) : snap.value;
  }, [snap, o.key, o.events, reduce]);

  const reload = useCallback(() => fetchNow(false), [fetchNow]);
  const replace = useCallback((v: T) => {
    gen.current++;
    const cursor = opts.current.events.at(-1)?.id ?? 0;
    seen.current = Math.max(seen.current, cursor);
    setSnap({ key: opts.current.key, value: v, cursor });
    setStatus('ready');
  }, []);
  return { status: snap && snap.key !== o.key ? 'loading' : status, value, error, reload, replace };
}
```

Create `apps/desktop/src/renderer/automations/data.ts`:

```ts
import { useCallback, useMemo } from 'react';
import { emptyAutomations, withAutomationSummaries } from '@desk/client';
import type { AutomationDetail, AutomationSummary, RunDetail, RunListEntry } from '@desk/protocol';
import { automationDetailRefetch, automationListRefetch, automationRunsRefetch, reduceAutomationList, reduceRunDetail, runDetailRefetch } from '@desk/ui-core';
import { call } from '../bridge';
import type { SessionState } from '../state/session';
import { useLive, type Live } from './live';

/** The project's automations, by name, kept current. */
export function useAutomationList(projectId: string, s: SessionState): Live<AutomationSummary[]> {
  const load = useCallback(() => call('automations.list', { projectId }).then((list) => withAutomationSummaries(emptyAutomations(projectId), list).list), [projectId]);
  return useLive({ key: projectId, events: s.events, ready: s.status === 'ready', load, reduce: reduceAutomationList, refetch: automationListRefetch });
}

/** One automation's detail, reloaded when it changes and when its last run ends. */
export function useAutomation(s: SessionState, id: string): Live<AutomationDetail> {
  const load = useCallback(() => call('automations.get', { id }), [id]);
  const refetch = useMemo(() => automationDetailRefetch(id), [id]);
  return useLive({ key: id, events: s.events, ready: s.status === 'ready', load, refetch });
}

/** An automation's runs and skipped schedule times, newest first. */
export function useRuns(s: SessionState, id: string, limit: number): Live<RunListEntry[]> {
  const load = useCallback(() => call('automations.runs', { id, limit }), [id, limit]);
  const refetch = useMemo(() => automationRunsRefetch(id), [id]);
  return useLive({ key: `${id}:${limit}`, events: s.events, ready: s.status === 'ready', load, refetch });
}

/** One run with live steps: step changes fold in at once; its agents' approvals, a child's end and its own end reload it. */
export function useRun(s: SessionState, runId: string): Live<RunDetail> {
  const load = useCallback(() => call('automations.getRun', { runId }), [runId]);
  const refetch = useMemo(() => runDetailRefetch(runId), [runId]);
  return useLive({ key: runId, events: s.events, ready: s.status === 'ready', load, reduce: reduceRunDetail, refetch });
}
```

- [ ] **Step 5: Write the dialogs**

Create `apps/desktop/src/renderer/automations/dialogs/RunDialog.tsx`:

```tsx
import { useState } from 'react';
import type { AutomationDefinition, InputSpec } from '@desk/protocol';
import { initialValues, runInputs, type InputDraft } from '@desk/ui-core';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { Field } from '../../components/Field';
import { Sheet } from '../../components/Sheet';
import { toastError } from '../../components/Toast';

/** What Run now and Test need to know (an AutomationDetail fits). */
export type RunTarget = { id: string; name: string; title: string; definition: AutomationDefinition };

/** Run now or Test (spec §8.4): a form generated from the automation's inputs. */
export function RunDialog(o: { target: RunTarget; test: boolean; onClose(): void; onStarted(runId: string): void }) {
  const inputs = o.target.definition.inputs;
  const [values, setValues] = useState(() => initialValues(inputs));
  const [pending, setPending] = useState(false);
  const ready = runInputs(inputs, values);
  const start = async () => {
    if (!ready) return;
    setPending(true);
    try {
      const { run_id } = await call('automations.run', { id: o.target.id, req: { inputs: ready, test: o.test } });
      o.onStarted(run_id);
    } catch (err) {
      toastError(err);
      setPending(false);
    }
  };
  return (
    <Sheet
      title={`${o.test ? 'Test' : 'Run'} ${o.target.title}`}
      onClose={o.onClose}
      footer={
        <>
          <Button onClick={o.onClose}>Cancel</Button>
          <Button variant="primary" pending={pending} disabled={!ready} onClick={() => void start()}>
            {o.test ? 'Start test' : 'Run'}
          </Button>
        </>
      }
    >
      {o.test ? (
        <p className="muted small">
          A test is a real run of the current version. Scripts see DESK_TEST=1 and can do a dry run, files publish under automations/{o.target.name}/tests/, and agent and Tell Desk steps act as usual.
        </p>
      ) : null}
      {inputs.length ? (
        inputs.map((i) => <InputField key={i.key} spec={i} value={values[i.key] ?? ''} onChange={(v) => setValues((s) => ({ ...s, [i.key]: v }))} />)
      ) : (
        <p className="muted">This automation takes no inputs.</p>
      )}
    </Sheet>
  );
}

function InputField({ spec, value, onChange }: { spec: InputSpec; value: InputDraft; onChange(v: InputDraft): void }) {
  const id = `run-input-${spec.key}`;
  const label = spec.required ? spec.label : `${spec.label} (optional)`;
  const pick = async (kind: 'file' | 'folder') => {
    try {
      const path = kind === 'file' ? await call('app.pickFile', { purpose: 'automation-input' }) : await call('app.pickFolder', { purpose: 'automation-input' });
      if (path) onChange(path);
    } catch (err) {
      toastError(err);
    }
  };
  switch (spec.type) {
    case 'boolean':
      return (
        <div className="field">
          <label className="auto-check">
            <input id={id} type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} /> {spec.label}
          </label>
          {spec.description ? <p className="field-hint">{spec.description}</p> : null}
        </div>
      );
    case 'long_text':
      return (
        <Field id={id} label={label} hint={spec.description}>
          <textarea id={id} className="textarea" rows={4} value={String(value)} onChange={(e) => onChange(e.target.value)} />
        </Field>
      );
    case 'choice':
      return (
        <Field id={id} label={label} hint={spec.description}>
          <select id={id} className="select" value={String(value)} onChange={(e) => onChange(e.target.value)}>
            <option value="">{spec.required ? 'Choose…' : 'None'}</option>
            {(spec.options ?? []).map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </Field>
      );
    case 'number':
      return (
        <Field id={id} label={label} hint={spec.description}>
          <input id={id} className="input" type="number" value={String(value)} onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))} />
        </Field>
      );
    case 'file':
    case 'folder': {
      const kind = spec.type;
      return (
        <Field id={id} label={label} hint={spec.description ?? (kind === 'file' ? 'Desk copies the file into the run folder.' : 'Desk copies the folder into the run folder.')}>
          <div className="auto-pick">
            <input id={id} className="input mono" value={String(value)} placeholder={kind === 'file' ? '/path/to/file' : '/path/to/folder'} onChange={(e) => onChange(e.target.value)} />
            <Button size="sm" onClick={() => void pick(kind)}>
              Choose…
            </Button>
          </div>
        </Field>
      );
    }
    default:
      return (
        <Field id={id} label={label} hint={spec.description}>
          <input id={id} className="input" type={spec.type === 'url' ? 'url' : 'text'} value={String(value)} onChange={(e) => onChange(e.target.value)} />
        </Field>
      );
  }
}
```

Create `apps/desktop/src/renderer/automations/dialogs/TurnOnDialog.tsx`:

```tsx
import { useEffect, useMemo, useState } from 'react';
import type { AutomationDetail } from '@desk/protocol';
import { dayTime, describeGrant, describeSchedule, sameGrant, uniqueGrants } from '@desk/ui-core';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { Sheet } from '../../components/Sheet';
import { toastError } from '../../components/Toast';
import { useNow } from '../../state/now';

/**
 * Turning an automation on (spec §5.4): its schedules with their next time, the grants its runs were approved for
 * (ticked), and a warning when the current version has no succeeded test. Grants are set first, then the switch.
 */
export function TurnOnDialog(o: { detail: AutomationDetail; onClose(): void; onDone(detail: AutomationDetail): void; onTestFirst(): void }) {
  const d = o.detail;
  const now = useNow();
  const proposals = useMemo(() => uniqueGrants([...(d.enable_request?.proposed_grants ?? []), ...d.proposed_grants]).filter((g) => !d.grants.some((x) => sameGrant(x, g))), [d]);
  const [ticked, setTicked] = useState<boolean[]>(() => proposals.map(() => true));
  const [next, setNext] = useState<Record<string, string[]>>({});
  const [pending, setPending] = useState(false);
  useEffect(() => {
    let live = true;
    call('automations.validate', { projectId: d.project_id, req: { definition: d.definition, name: d.name } })
      .then((r) => live && setNext(r.next_times))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [d.project_id, d.definition, d.name]);
  const untested = d.tested_version !== d.version;
  const turnOn = async () => {
    setPending(true);
    try {
      const grants = uniqueGrants([...d.grants, ...proposals.filter((_, i) => ticked[i])]);
      await call('automations.setGrants', { id: d.id, grants, reason: 'enabled' });
      o.onDone(await call('automations.setEnabled', { id: d.id, enabled: true }));
    } catch (err) {
      toastError(err);
      setPending(false);
    }
  };
  return (
    <Sheet
      title={`Turn on ${d.title}`}
      onClose={o.onClose}
      width={560}
      footer={
        <>
          <Button onClick={o.onClose}>Cancel</Button>
          {untested ? <Button onClick={o.onTestFirst}>Test first</Button> : null}
          <Button variant="primary" pending={pending} onClick={() => void turnOn()}>
            {untested ? 'Turn on anyway' : 'Turn on'}
          </Button>
        </>
      }
    >
      {untested ? (
        <p className="auto-warn" role="alert">
          {`v${d.version} hasn't been tested${d.tested_version ? ` (v${d.tested_version} was)` : ''}.`}
        </p>
      ) : null}
      <h3 className="auto-sub">When it runs</h3>
      {d.definition.triggers.length ? (
        <ul className="auto-plain">
          {d.definition.triggers.map((t, i) => {
            const first = next[String(i)]?.[0];
            return (
              <li key={i}>
                {describeSchedule(t.cron, t.timezone)}
                {first ? <span className="muted">{` · next ${dayTime(first, now)}`}</span> : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="muted">It has no schedule, so turning it on changes nothing until you add one. Run now works either way.</p>
      )}
      {d.enable_request?.note ? <blockquote className="auto-quote">{`Desk: ${d.enable_request.note}`}</blockquote> : null}
      <h3 className="auto-sub">Grants</h3>
      {d.grants.length ? (
        <ul className="auto-plain">
          {d.grants.map((g, i) => (
            <li key={i}>
              {describeGrant(g)} <span className="muted">(kept)</span>
            </li>
          ))}
        </ul>
      ) : null}
      {proposals.length ? (
        <>
          <p className="muted small">Approved during its runs. Ticked ones let later runs go ahead without asking you.</p>
          {proposals.map((g, i) => (
            <label key={i} className="auto-check">
              <input type="checkbox" checked={ticked[i] ?? false} onChange={(e) => setTicked((t) => t.map((v, j) => (j === i ? e.target.checked : v)))} /> {describeGrant(g)}
            </label>
          ))}
        </>
      ) : !d.grants.length ? (
        <p className="muted">No grants: anything its steps need approval for will ask you when it runs.</p>
      ) : null}
    </Sheet>
  );
}
```

Create `apps/desktop/src/renderer/automations/dialogs/NameDialog.tsx`:

```tsx
import { useState } from 'react';
import { AutomationName } from '@desk/protocol';
import { Button } from '../../components/Button';
import { Field } from '../../components/Field';
import { Sheet } from '../../components/Sheet';

/** Asks for an automation name (fixed at creation): Blank automation, and importing under another name. */
export function NameDialog(o: { title: string; confirmLabel: string; initial?: string; taken: string[]; hint?: string; onClose(): void; onConfirm(name: string): void }) {
  const [name, setName] = useState(o.initial ?? '');
  const parsed = AutomationName.safeParse(name);
  const error = !name ? null : !parsed.success ? (parsed.error.issues[0]?.message ?? 'Not a valid name') : o.taken.includes(name) ? 'Another automation already has this name.' : null;
  const ok = name !== '' && error === null;
  return (
    <Sheet
      title={o.title}
      onClose={o.onClose}
      width={460}
      footer={
        <>
          <Button onClick={o.onClose}>Cancel</Button>
          <Button variant="primary" disabled={!ok} onClick={() => o.onConfirm(name)}>
            {o.confirmLabel}
          </Button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (ok) o.onConfirm(name);
        }}
      >
        <Field id="automation-name" label="Name" hint={o.hint ?? 'Lowercase letters, digits and dashes, e.g. weekly-digest. The name cannot change later; the title can.'} error={error}>
          <input id="automation-name" className="input mono" value={name} onChange={(e) => setName(e.target.value.toLowerCase().replace(/\s+/g, '-'))} />
        </Field>
      </form>
    </Sheet>
  );
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pnpm vitest run apps/desktop/src/renderer/automations packages/ui-styles apps/desktop/src/renderer/styles.test.ts`
Expected: PASS. The token test confirms no color literal crept in, and the styles test confirms no component imports CSS.

Run: `pnpm typecheck`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
pnpm test
git add apps/desktop/src/renderer/automations/live.ts apps/desktop/src/renderer/automations/data.ts apps/desktop/src/renderer/automations/live.test.tsx apps/desktop/src/renderer/automations/dialogs packages/ui-styles/src/automations.css packages/ui-styles/src/index.css packages/ui-styles/src/index.test.ts
git commit -m "feat(desktop): automation data hooks, and the Run now / Test, Turn on and name dialogs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The Automations tab: routes, list, header, and the web placeholder

This task adds the tab and its screen shell. The four views start as empty states, and later tasks replace each branch of `AutomationBody` (and `DraftAutomation`):
- Task 12: Versions;
- Task 13: Design;
- Task 14: Runs;
- Task 16: Grants.

On the web, the tab shows a placeholder until Plan 21.

**Files:**
- Modify: `packages/ui-core/src/router.ts`, `packages/ui-core/src/router.test.ts`
- Modify: `apps/desktop/src/renderer/components/ProjectNav.tsx`, `apps/desktop/src/renderer/App.tsx`
- Create: `apps/desktop/src/renderer/conversation/draft.ts`
- Modify: `apps/desktop/src/renderer/conversation/ConversationScreen.tsx` (use `draftKey`)
- Create: `apps/desktop/src/renderer/automations/AutomationsScreen.tsx`, `AutomationList.tsx`, `AutomationHeader.tsx`
- Modify: `packages/ui-styles/src/automations.css`
- Create: `apps/web-ui/src/app/automations/automations-placeholder.ts`
- Modify: `apps/web-ui/src/app/components/project-nav.ts`, `project-nav.spec.ts`, `apps/web-ui/src/app/screen-for.ts`, `screen-for.spec.ts`
- Test: `apps/desktop/src/renderer/automations/AutomationsScreen.test.tsx`

**Interfaces:**
- Consumes: Task 3 formats; Task 6 hooks and dialogs.
- Produces:
  - In `@desk/ui-core`: `type ProjectTab` gains `'automations'`; `type AutomationView = 'design' | 'runs' | 'versions' | 'grants'`; the `project` route gains `automationId?`, `view?`, `runId?`, `draft?`.
  - `primeDraft(projectId, text)` and `draftKey(projectId)` (in the desktop's `conversation/draft.ts`).
  - `AutomationsScreen({ projectId, automationId?, view?, runId?, draft? })`.
  - `AutomationHeader({ projectId, detail, view, onChange })` and `DraftHeader({ projectId, name })`.
  - `AutomationBody` and `DraftAutomation`, internal to `AutomationsScreen.tsx`. Later tasks replace their branches.
  - `AutomationsPlaceholder` (web, input `projectId`). Plan 21 replaces it.

- [ ] **Step 1: Write the failing tests**

In `packages/ui-core/src/router.test.ts`, add to the `routes` array:

```ts
      { name: 'project', id: 'p1', tab: 'automations' },
      { name: 'project', id: 'p1', tab: 'automations', draft: 'weekly-note' },
      { name: 'project', id: 'p1', tab: 'automations', automationId: 'a1', view: 'design' },
      { name: 'project', id: 'p1', tab: 'automations', automationId: 'a1', view: 'runs' },
      { name: 'project', id: 'p1', tab: 'automations', automationId: 'a1', view: 'runs', runId: 'r9' },
      { name: 'project', id: 'p1', tab: 'automations', automationId: 'a1', view: 'versions' },
      { name: 'project', id: 'p1', tab: 'automations', automationId: 'a1', view: 'grants' },
```

and to 'falls back to the map':

```ts
    expect(parseRoute('#/p/x/automations/new')).toEqual({ name: 'project', id: 'x', tab: 'automations' });
    expect(parseRoute('#/p/x/automations/a1/bogus')).toEqual({ name: 'project', id: 'x', tab: 'automations', automationId: 'a1', view: 'design' });
```

In `apps/web-ui/src/app/components/project-nav.spec.ts`, rename the test to "links the project's six tabs and marks the open one", and add `['Automations', '#/p/p%201/automations'],` after the Threads row.

In `apps/web-ui/src/app/screen-for.spec.ts`, add `import { AutomationsPlaceholder } from './automations/automations-placeholder';` and, after the threads expectation:

```ts
    expect(screenFor({ name: 'project', id: 'p', tab: 'automations', automationId: 'a1', view: 'runs' })).toEqual({ component: AutomationsPlaceholder, inputs: { projectId: 'p' } });
```

Create `apps/desktop/src/renderer/automations/AutomationsScreen.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import type { AutomationSummary, StoredEvent } from '@desk/protocol';
import { initialGlobalState } from '@desk/bff/contract';
import { automationDetail, automationSummary } from '@desk/ui-core/testing';
import { globalStore } from '../state/global';
import { resetSessions, setReleaseDelay, startSessionRouting } from '../state/session';
import { installBridge } from '../test/bridge';
import { AutomationsScreen } from './AutomationsScreen';

afterEach(cleanup);
beforeEach(() => {
  resetSessions();
  setReleaseDelay(0);
  localStorage.clear();
  window.location.hash = '#/p/p/automations';
  globalStore.set({ ...initialGlobalState(), connection: { status: 'live' } });
});

const overview = () =>
  ({
    project: { id: 'p', name: 'P', goal: '', instructions: '', settings: {}, created_at: 't', updated_at: 't', archived_at: null },
    desk: null,
    sources: [],
    plan: null,
    threads: [],
    approvals: [],
    last_seq: 0,
  }) as unknown as ProjectOverview;

function setup(handlers: Record<string, (input: any) => unknown>, events: StoredEvent[] = []) {
  const bridge = installBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    ...handlers,
  });
  startSessionRouting();
  return bridge;
}

const run = (over: Partial<NonNullable<AutomationSummary['last_run']>>): NonNullable<AutomationSummary['last_run']> => ({
  id: 'r1',
  number: 1,
  status: 'succeeded',
  trigger: 'schedule',
  test: false,
  started_at: '2026-09-24T06:00:00.000Z',
  finished_at: '2026-09-24T06:09:00.000Z',
  summary: 'ok',
  waiting_on: null,
  ...over,
});

const LIST = [
  automationSummary({ last_run: run({ id: 'r13', number: 13 }) }),
  automationSummary({ id: 'a2', name: 'deps', title: 'Nightly dependency check', version: 3, tested_version: 3, schedules: [{ cron: '0 2 * * *', timezone: 'Europe/Paris' }], last_run: run({ id: 'r2', number: 2, status: 'waiting', finished_at: null, waiting_on: 'Ask me: Open the PR?' }) }),
  automationSummary({ id: 'a3', name: 'invoices', title: 'Invoice intake', version: 2, tested_version: 2, enabled: false, schedules: [], next_due: null }),
];

describe('Automations list', () => {
  it('lists each automation with its switch, schedule, last run and next run', async () => {
    setup({ 'automations.list': () => LIST });
    render(<AutomationsScreen projectId="p" />);
    const deps = (await screen.findByRole('link', { name: 'Nightly dependency check' })).closest('tr')!;
    expect(deps.textContent).toContain('waiting on you: Ask me: Open the PR?');
    expect(deps.textContent).toContain('Daily 02:00');
    const digest = screen.getByRole('link', { name: 'Weekly digest' }).closest('tr')!;
    expect(digest.textContent).toContain('v7 · untested changes');
    expect(digest.textContent).toContain('succeeded');
    const invoices = screen.getByRole('link', { name: 'Invoice intake' }).closest('tr')!;
    expect(invoices.textContent).toContain('v2 · run now only');
    expect(within(invoices).getByRole('switch').getAttribute('aria-checked')).toBe('false');
    expect(screen.getByRole('link', { name: 'Weekly digest' }).getAttribute('href')).toBe('#/p/p/automations/a1');
  });

  it('turns an automation off at once, and on through the Turn-on dialog', async () => {
    const bridge = setup({
      'automations.list': () => LIST,
      'automations.setEnabled': () => automationDetail({ enabled: false }),
      'automations.get': () => automationDetail({ id: 'a3', title: 'Invoice intake', enabled: false }),
      'automations.validate': () => ({ errors: [], warnings: [], next_times: {} }),
    });
    render(<AutomationsScreen projectId="p" />);
    fireEvent.click(await screen.findByRole('switch', { name: 'Turn off Weekly digest' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.setEnabled')?.input).toEqual({ id: 'a1', enabled: false }));
    fireEvent.click(screen.getByRole('switch', { name: 'Turn on Invoice intake' }));
    expect(await screen.findByRole('dialog', { name: 'Turn on Invoice intake' })).toBeTruthy();
  });

  it('primes the composer for Desk, starts a blank draft, and imports an export (asking for a name when taken)', async () => {
    const bridge = setup({
      'automations.list': () => LIST,
      'automations.import': ({ exp }: { exp: { name: string } }) => {
        if (exp.name === 'digest') throw { code: 'conflict', message: 'taken', status: 409 };
        return { automation: automationDetail({ id: 'a9', name: exp.name }), warnings: [] };
      },
    });
    render(<AutomationsScreen projectId="p" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Describe one to Desk' }));
    expect(localStorage.getItem('desk.draft.p')).toBe("I'd like to automate: ");
    expect(window.location.hash).toBe('#/p/p/conversation');

    fireEvent.click(screen.getByRole('button', { name: 'Blank automation' }));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'weekly-note' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(window.location.hash).toBe('#/p/p/automations/new?name=weekly-note');

    const exp = { format: 'desk-automation/1', name: 'digest', definition: automationDetail().definition };
    fireEvent.change(screen.getByTestId('automation-import'), { target: { files: [new File([JSON.stringify(exp)], 'digest.json', { type: 'application/json' })] } });
    const dialog = await screen.findByRole('dialog', { name: 'Import as…' });
    expect((within(dialog).getByLabelText('Name') as HTMLInputElement).value).toBe('digest-2');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(window.location.hash).toBe('#/p/p/automations/a9'));
    expect(bridge.calls.filter((c) => c.channel === 'automations.import').map((c) => (c.input as { exp: { name: string } }).exp.name)).toEqual(['digest', 'digest-2']);
  });
});

describe('Automation header', () => {
  it('shows the version badge, run count and switch, and runs, exports and deletes', async () => {
    const d = automationDetail({ last_run: run({ id: 'r14', number: 14, status: 'running', finished_at: null }), grants: [{ tool: 'web_fetch', match: { domain: 'acme.com' }, action: 'allow' }] });
    const bridge = setup({
      'automations.get': () => d,
      'automations.setEnabled': () => ({ ...d, enabled: false }),
      'automations.export': () => ({ format: 'desk-automation/1', name: 'digest', definition: d.definition }),
      'app.saveFile': () => true,
      'automations.remove': () => ({ ok: true }),
    });
    render(<AutomationsScreen projectId="p" automationId="a1" view="design" />);
    expect(await screen.findByRole('heading', { name: 'Weekly digest' })).toBeTruthy();
    expect(screen.getByText('v7 · tested in v6')).toBeTruthy();
    const tabs = screen.getByRole('navigation', { name: 'Automation' });
    expect(within(tabs).getByRole('link', { name: 'Runs (14)' }).getAttribute('href')).toBe('#/p/p/automations/a1/runs');
    expect(within(tabs).getByRole('link', { name: 'Grants (1)' })).toBeTruthy();
    fireEvent.click(screen.getByRole('switch', { name: 'Turn off Weekly digest' }));
    await screen.findByRole('switch', { name: 'Turn on Weekly digest' });
    fireEvent.click(screen.getByRole('button', { name: 'Run now…' }));
    expect(screen.getByRole('dialog', { name: 'Run Weekly digest' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Export…' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'app.saveFile')?.input).toMatchObject({ name: 'digest.desk-automation.json' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete…' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Delete Weekly digest?' })).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(window.location.hash).toBe('#/p/p/automations'));
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm vitest run packages/ui-core/src/router.test.ts apps/desktop/src/renderer/automations/AutomationsScreen.test.tsx`
Expected: FAIL: the router does not know `automations`, and `AutomationsScreen` does not exist.

- [ ] **Step 3: Routes**

In `packages/ui-core/src/router.ts`, replace the tab types:

```ts
export type ProjectTab = 'conversation' | 'threads' | 'automations' | 'library' | 'memory' | 'settings';
export const PROJECT_TABS: ProjectTab[] = ['conversation', 'threads', 'automations', 'library', 'memory', 'settings'];

/** An automation's sub-tabs. */
export type AutomationView = 'design' | 'runs' | 'versions' | 'grants';
```

Replace the `project` member of `Route`, keeping its doc comment and extending it:

```ts
  /**
   * `at`: on a thread, the event id of a message to open the route at (the digest's pair lines); on the conversation,
   * the event id of a Desk stop to scroll the chat to (a stop clicked on the Threads tab's timeline).
   * Automations: `automationId` + `view` (and `runId` on runs) open one; `draft` is a new one not saved yet.
   */
  | {
      name: 'project';
      id: string;
      tab: ProjectTab;
      threadId?: string;
      at?: number;
      file?: string;
      q?: string;
      automationId?: string;
      view?: AutomationView;
      runId?: string;
      draft?: string;
    };
```

In `parseRoute`, in `case 'p'`, add right after `const atOk = …;`:

```ts
      if (tab === 'automations') {
        if (parts[3] === 'new') {
          const name = q.get('name');
          return name ? { name: 'project', id, tab, draft: name } : { name: 'project', id, tab };
        }
        const automationId = parts[3];
        if (!automationId) return { name: 'project', id, tab };
        if (parts[4] === 'runs') return parts[5] ? { name: 'project', id, tab, automationId, view: 'runs', runId: parts[5] } : { name: 'project', id, tab, automationId, view: 'runs' };
        if (parts[4] === 'versions' || parts[4] === 'grants') return { name: 'project', id, tab, automationId, view: parts[4] };
        return { name: 'project', id, tab, automationId, view: 'design' };
      }
```

Add above `href`:

```ts
/** The part of an automations route after `/automations`. */
function automationPath(r: Extract<Route, { name: 'project' }>): string {
  if (r.draft) return `/new?name=${enc(r.draft)}`;
  if (!r.automationId) return '';
  const base = `/${enc(r.automationId)}`;
  if (r.view === 'runs') return `${base}/runs${r.runId ? `/${enc(r.runId)}` : ''}`;
  if (r.view === 'versions' || r.view === 'grants') return `${base}/${r.view}`;
  return base;
}
```

and in `href`, make the `project` case start with the automations route:

```ts
    case 'project':
      if (r.tab === 'automations') return `#/p/${enc(r.id)}/automations${automationPath(r)}`;
      return `#/p/${enc(r.id)}/${r.tab}${r.threadId ? `/${enc(r.threadId)}` : ''}${r.file ? `?file=${enc(r.file)}` : r.q ? `?q=${enc(r.q)}` : r.at !== undefined ? `?at=${r.at}` : ''}`;
```

- [ ] **Step 4: The tab in both UIs**

In `apps/desktop/src/renderer/components/ProjectNav.tsx` and in `apps/web-ui/src/app/components/project-nav.ts`, change `LABEL` to:

```ts
const LABEL: Record<ProjectTab, string> = { conversation: 'Conversation', threads: 'Threads', automations: 'Automations', library: 'Library', memory: 'Memory', settings: 'Settings' };
```

Create `apps/web-ui/src/app/automations/automations-placeholder.ts`:

```ts
import { ChangeDetectionStrategy, Component, input, ViewEncapsulation } from '@angular/core';
import { EmptyState } from '../components/empty-state';

/** The Automations tab until Plan 21 ports the desktop's screens (AutomationsScreen.tsx). */
@Component({
  selector: 'desk-automations-placeholder',
  imports: [EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  template: `<div deskEmptyState title="Automations" body="Automations open in the Desk app for now. The web UI gets them next."></div>`,
})
export class AutomationsPlaceholder {
  readonly projectId = input.required<string>();
}
```

In `apps/web-ui/src/app/screen-for.ts`, import it and add to the project switch, after `threads`:

```ts
        case 'automations':
          return { component: AutomationsPlaceholder, inputs: { projectId: route.id } };
```

In `apps/desktop/src/renderer/App.tsx`, import `import { AutomationsScreen } from './automations/AutomationsScreen';` and add to `Screen`'s project case, before the library line:

```tsx
      if (route.tab === 'automations')
        return (
          <AutomationsScreen
            key={route.id}
            projectId={route.id}
            {...(route.automationId ? { automationId: route.automationId } : {})}
            {...(route.view ? { view: route.view } : {})}
            {...(route.runId ? { runId: route.runId } : {})}
            {...(route.draft ? { draft: route.draft } : {})}
          />
        );
```

- [ ] **Step 5: Priming the composer**

Create `apps/desktop/src/renderer/conversation/draft.ts`:

```ts
/** Where the conversation composer keeps a project's unsent draft (the web UI uses the same key). */
export const draftKey = (projectId: string): string => `desk.draft.${projectId}`;

/** Puts text in a project's composer (after an unsent draft, on a new paragraph). The conversation reads it on mount. */
export function primeDraft(projectId: string, text: string): void {
  try {
    const current = localStorage.getItem(draftKey(projectId)) ?? '';
    localStorage.setItem(draftKey(projectId), current.trim() ? `${current.trimEnd()}\n\n${text}` : text);
  } catch {
    // Drafts are a convenience.
  }
}
```

In `ConversationScreen.tsx`, import `draftKey` from `./draft` and change `const key = \`desk.draft.${projectId}\`;` to `const key = draftKey(projectId);`.

- [ ] **Step 6: The screen shell, list and header**

Create `apps/desktop/src/renderer/automations/AutomationsScreen.tsx`:

```tsx
import type { AutomationDetail } from '@desk/protocol';
import { href, type AutomationView } from '@desk/ui-core';
import { EmptyState } from '../components/EmptyState';
import { useSession, type SessionState } from '../state/session';
import { AutomationHeader, DraftHeader } from './AutomationHeader';
import { AutomationList } from './AutomationList';
import { useAutomation } from './data';

/** The Automations tab (spec §8.1): the list, one automation (header, then Design, Runs, Versions or Grants), or a new draft. */
export function AutomationsScreen(o: { projectId: string; automationId?: string; view?: AutomationView; runId?: string; draft?: string }) {
  const s = useSession(o.projectId);
  if (s.status === 'missing') return <EmptyState title="This project is gone" />;
  if (o.draft) return <DraftAutomation projectId={o.projectId} s={s} name={o.draft} />;
  if (!o.automationId) return <AutomationList projectId={o.projectId} s={s} />;
  return <OneAutomation projectId={o.projectId} s={s} id={o.automationId} view={o.view ?? 'design'} {...(o.runId ? { runId: o.runId } : {})} />;
}

function OneAutomation(o: { projectId: string; s: SessionState; id: string; view: AutomationView; runId?: string }) {
  const live = useAutomation(o.s, o.id);
  const all = href({ name: 'project', id: o.projectId, tab: 'automations' });
  if (live.status === 'missing') {
    return (
      <EmptyState title="This automation is gone" action={<a href={all}>All automations</a>}>
        It was deleted.
      </EmptyState>
    );
  }
  if (!live.value) {
    return live.status === 'error' ? (
      <EmptyState title="Couldn't load this automation" action={<a href={all}>All automations</a>}>
        {live.error}
      </EmptyState>
    ) : (
      <p className="muted auto-loading">Loading…</p>
    );
  }
  return (
    <div className="automation">
      <AutomationHeader projectId={o.projectId} detail={live.value} view={o.view} onChange={live.replace} />
      <div className="automation-body">
        <AutomationBody projectId={o.projectId} s={o.s} view={o.view} {...(o.runId ? { runId: o.runId } : {})} detail={live.value} onChange={live.replace} />
      </div>
    </div>
  );
}

/** One automation's current view. Tasks 12–16 replace these branches with the real views. */
function AutomationBody(o: { projectId: string; s: SessionState; view: AutomationView; runId?: string; detail: AutomationDetail; onChange(d: AutomationDetail): void }) {
  switch (o.view) {
    case 'runs':
      return <EmptyState title="Runs">Its runs show here.</EmptyState>;
    case 'versions':
      return <EmptyState title="Versions">Its saved versions show here.</EmptyState>;
    case 'grants':
      return <EmptyState title="Grants">What its runs may do without asking shows here.</EmptyState>;
    default:
      return <EmptyState title="Design">The graph editor shows here.</EmptyState>;
  }
}

/** A Blank automation before its first save (spec §8.1): only Design, on a local draft. */
function DraftAutomation(o: { projectId: string; s: SessionState; name: string }) {
  return (
    <div className="automation">
      <DraftHeader projectId={o.projectId} name={o.name} />
      <div className="automation-body">
        <EmptyState title="Design">The graph editor shows here.</EmptyState>
      </div>
    </div>
  );
}
```

Create `apps/desktop/src/renderer/automations/AutomationHeader.tsx`:

```tsx
import { useState } from 'react';
import type { AutomationDetail } from '@desk/protocol';
import { href, versionBadge, type AutomationView } from '@desk/ui-core';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { toast, toastError } from '../components/Toast';
import { navigate } from '../router';
import { RunDialog } from './dialogs/RunDialog';
import { TurnOnDialog } from './dialogs/TurnOnDialog';

type Dialog = null | 'run' | 'test' | 'turn-on' | 'delete';

/** One automation's header (mockup 2): title, version badge, the switch, Test… and Run now…, and the sub-tabs. */
export function AutomationHeader(o: { projectId: string; detail: AutomationDetail; view: AutomationView; onChange(d: AutomationDetail): void }) {
  const d = o.detail;
  const [dialog, setDialog] = useState<Dialog>(null);
  const [switching, setSwitching] = useState(false);
  const route = (view: AutomationView, runId?: string) => ({ name: 'project' as const, id: o.projectId, tab: 'automations' as const, automationId: d.id, view, ...(runId ? { runId } : {}) });

  const toggle = async () => {
    if (!d.enabled) {
      setDialog('turn-on');
      return;
    }
    setSwitching(true);
    try {
      o.onChange(await call('automations.setEnabled', { id: d.id, enabled: false }));
    } catch (err) {
      toastError(err);
    } finally {
      setSwitching(false);
    }
  };
  const exportIt = async () => {
    try {
      const exp = await call('automations.export', { id: d.id });
      await call('app.saveFile', { name: `${d.name}.desk-automation.json`, data: new TextEncoder().encode(`${JSON.stringify(exp, null, 2)}\n`) });
    } catch (err) {
      toastError(err);
    }
  };
  const remove = async () => {
    setDialog(null);
    try {
      await call('automations.remove', { id: d.id });
      toast({ tone: 'info', message: `Deleted ${d.title}.` });
      navigate({ name: 'project', id: o.projectId, tab: 'automations' });
    } catch (err) {
      toastError(err);
    }
  };
  const tab = (view: AutomationView, label: string) => (
    <a href={href(route(view))} aria-current={o.view === view ? 'page' : undefined}>
      {label}
    </a>
  );
  const runs = d.last_run?.number ?? 0;
  return (
    <header className="automation-head">
      <div className="automation-title-row">
        <a className="muted small" href={href({ name: 'project', id: o.projectId, tab: 'automations' })}>
          Automations ›
        </a>
        <h1 className="automation-title">{d.title}</h1>
        <span className={`auto-badge${d.tested_version === d.version ? ' ok' : ''}`}>{versionBadge(d)}</span>
        {d.grants_suspended ? (
          <a className="auto-badge warn" href={href(route('grants'))}>
            grants suspended
          </a>
        ) : null}
        <span className="grow" />
        <span className="muted small">{d.enabled ? 'On' : 'Off'}</span>
        <button type="button" role="switch" aria-checked={d.enabled} aria-label={d.enabled ? `Turn off ${d.title}` : `Turn on ${d.title}`} className="switch" disabled={switching} onClick={() => void toggle()}>
          <span className="switch-knob" />
        </button>
        <Button onClick={() => setDialog('test')}>Test…</Button>
        <Button variant="primary" onClick={() => setDialog('run')}>
          Run now…
        </Button>
      </div>
      <nav className="automation-tabs" aria-label="Automation">
        {tab('design', 'Design')}
        {tab('runs', runs ? `Runs (${runs})` : 'Runs')}
        {tab('versions', 'Versions')}
        {tab('grants', d.grants.length ? `Grants (${d.grants.length})` : 'Grants')}
        <span className="grow" />
        <Button size="sm" variant="ghost" onClick={() => void exportIt()}>
          Export…
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setDialog('delete')}>
          Delete…
        </Button>
      </nav>
      {dialog === 'run' || dialog === 'test' ? (
        <RunDialog
          target={d}
          test={dialog === 'test'}
          onClose={() => setDialog(null)}
          onStarted={(runId) => {
            setDialog(null);
            navigate(route('runs', runId));
          }}
        />
      ) : null}
      {dialog === 'turn-on' ? (
        <TurnOnDialog
          detail={d}
          onClose={() => setDialog(null)}
          onDone={(next) => {
            setDialog(null);
            o.onChange(next);
          }}
          onTestFirst={() => setDialog('test')}
        />
      ) : null}
      {dialog === 'delete' ? (
        <ConfirmDialog title={`Delete ${d.title}?`} confirmLabel="Delete" danger onConfirm={() => void remove()} onCancel={() => setDialog(null)}>
          Its runs are cancelled and its schedules stop. Its history, and the files its runs put in the Library, are kept.
        </ConfirmDialog>
      ) : null}
    </header>
  );
}

/** The header of a Blank automation before its first save. */
export function DraftHeader({ projectId, name }: { projectId: string; name: string }) {
  return (
    <header className="automation-head">
      <div className="automation-title-row">
        <a className="muted small" href={href({ name: 'project', id: projectId, tab: 'automations' })}>
          Automations ›
        </a>
        <h1 className="automation-title">New automation</h1>
        <span className="auto-badge mono">{name}</span>
        <span className="muted small">Not saved yet: add a step, then Save.</span>
      </div>
      <nav className="automation-tabs" aria-label="Automation">
        <a href={href({ name: 'project', id: projectId, tab: 'automations', draft: name })} aria-current="page">
          Design
        </a>
      </nav>
    </header>
  );
}
```

Create `apps/desktop/src/renderer/automations/AutomationList.tsx`:

```tsx
import { useRef, useState } from 'react';
import { AutomationExport, type AutomationDetail, type AutomationSummary } from '@desk/protocol';
import { dayTime, href, lastRunText, listNote, whenText } from '@desk/ui-core';
import { call, DeskCallError } from '../bridge';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { toast, toastError } from '../components/Toast';
import { primeDraft } from '../conversation/draft';
import { navigate } from '../router';
import { useNow } from '../state/now';
import type { SessionState } from '../state/session';
import { useAutomationList } from './data';
import { NameDialog } from './dialogs/NameDialog';
import { RunDialog } from './dialogs/RunDialog';
import { TurnOnDialog } from './dialogs/TurnOnDialog';

/** Mockup 1: every automation with its switch, when it runs, its last run and its next one. */
export function AutomationList({ projectId, s }: { projectId: string; s: SessionState }) {
  const live = useAutomationList(projectId, s);
  const now = useNow();
  const [naming, setNaming] = useState<null | { importing?: AutomationExport }>(null);
  const [turnOn, setTurnOn] = useState<AutomationDetail | null>(null);
  const [testing, setTesting] = useState<AutomationDetail | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const list = live.value ?? [];
  const at = (automationId: string, extra: { view: 'design' } | { view: 'runs'; runId: string } = { view: 'design' }) => ({ name: 'project' as const, id: projectId, tab: 'automations' as const, automationId, ...extra });

  const describe = () => {
    primeDraft(projectId, "I'd like to automate: ");
    navigate({ name: 'project', id: projectId, tab: 'conversation' });
  };
  const doImport = async (exp: AutomationExport) => {
    try {
      const r = await call('automations.import', { projectId, exp });
      toast({ tone: 'info', message: `Imported ${r.automation.title}. It stays off until you turn it on.` });
      navigate(at(r.automation.id));
    } catch (err) {
      if (err instanceof DeskCallError && err.status === 409) setNaming({ importing: exp });
      else toastError(err);
    }
  };
  const readImport = async (file: File) => {
    let raw: unknown = null;
    try {
      raw = JSON.parse(await file.text());
    } catch {
      raw = null;
    }
    const parsed = AutomationExport.safeParse(raw);
    if (!parsed.success) {
      toast({ tone: 'error', message: "That file isn't a Desk automation export." });
      return;
    }
    await doImport(parsed.data);
  };
  const toggle = async (a: AutomationSummary) => {
    setBusy(a.id);
    try {
      if (a.enabled) {
        await call('automations.setEnabled', { id: a.id, enabled: false });
        live.reload();
      } else setTurnOn(await call('automations.get', { id: a.id }));
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="auto-list">
      <header className="auto-list-head">
        <h1 className="title">Automations</h1>
        <span className="grow" />
        <Button onClick={() => fileRef.current?.click()}>Import…</Button>
        <Button onClick={() => setNaming({})}>Blank automation</Button>
        <Button variant="primary" onClick={describe}>
          Describe one to Desk
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".json,application/json"
          hidden
          data-testid="automation-import"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file) void readImport(file);
          }}
        />
      </header>
      {live.status === 'loading' ? (
        <p className="muted">Loading…</p>
      ) : live.status === 'error' && !live.value ? (
        <EmptyState title="Couldn't load automations">{live.error}</EmptyState>
      ) : list.length ? (
        <table className="auto-table">
          <thead>
            <tr>
              <th>Automation</th>
              <th>On</th>
              <th>When</th>
              <th>Last run</th>
              <th>Next</th>
            </tr>
          </thead>
          <tbody>
            {list.map((a) => {
              const last = a.last_run ? lastRunText(a.last_run, now) : null;
              return (
                <tr key={a.id}>
                  <td>
                    <a className="auto-row-title" href={href(at(a.id))}>
                      {a.title}
                    </a>
                    <div className="muted small">{listNote(a)}</div>
                  </td>
                  <td>
                    <button type="button" role="switch" aria-checked={a.enabled} aria-label={a.enabled ? `Turn off ${a.title}` : `Turn on ${a.title}`} className="switch" disabled={busy === a.id} onClick={() => void toggle(a)}>
                      <span className="switch-knob" />
                    </button>
                  </td>
                  <td>{whenText(a.schedules)}</td>
                  <td>
                    {last ? (
                      <span className={`auto-last tone-${last.tone}`}>
                        <span className="dot" aria-hidden="true" />
                        {last.text}
                      </span>
                    ) : (
                      <span className="muted">never run</span>
                    )}
                  </td>
                  <td>{a.next_due ? dayTime(a.next_due, now) : <span className="muted">n/a</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <EmptyState title="No automations yet">Describe something you do again and again, and Desk builds, tests and proposes an automation. Or start from a blank one.</EmptyState>
      )}
      {naming ? (
        <NameDialog
          title={naming.importing ? 'Import as…' : 'New automation'}
          confirmLabel={naming.importing ? 'Import' : 'Create'}
          {...(naming.importing ? { initial: `${naming.importing.name}-2`, hint: `An automation is already called ${naming.importing.name}. Pick another name for this one.` } : {})}
          taken={list.map((a) => a.name)}
          onClose={() => setNaming(null)}
          onConfirm={(name) => {
            const importing = naming.importing;
            setNaming(null);
            if (importing) void doImport({ ...importing, name });
            else navigate({ name: 'project', id: projectId, tab: 'automations', draft: name });
          }}
        />
      ) : null}
      {turnOn ? (
        <TurnOnDialog
          detail={turnOn}
          onClose={() => setTurnOn(null)}
          onDone={() => {
            setTurnOn(null);
            live.reload();
          }}
          onTestFirst={() => {
            setTesting(turnOn);
            setTurnOn(null);
          }}
        />
      ) : null}
      {testing ? <RunDialog target={testing} test onClose={() => setTesting(null)} onStarted={(runId) => navigate(at(testing.id, { view: 'runs', runId }))} /> : null}
    </div>
  );
}
```

`.switch` and `.switch-knob` come from `ui-styles/src/skills.css`, which `index.css` loads everywhere, so the switches here need no styles of their own.

Append to `packages/ui-styles/src/automations.css`:

```css
/* The list (mockup 1) */
.auto-list {
  padding: 20px 28px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.auto-list-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.auto-table {
  width: 100%;
  border-collapse: collapse;
  background: var(--card);
  border: 1px solid var(--rule-soft);
  font-size: 13px;
}
.auto-table th {
  padding: 8px 12px;
  text-align: left;
  font-size: 10.5px;
  font-weight: 500;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
  background: var(--paper);
  border-bottom: 1px solid var(--rule-soft);
}
.auto-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--rule-soft);
  vertical-align: middle;
}
.auto-table tr:last-child td {
  border-bottom: 0;
}
.auto-row-title {
  font-family: var(--font-serif);
  font-size: 15px;
  color: var(--ink);
  text-decoration: none;
}
.auto-row-title:hover {
  text-decoration: underline;
}
.auto-last {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.tone-run {
  color: var(--run-text);
}
.tone-run .dot {
  background: var(--run);
}
.tone-wait {
  color: var(--wait-text);
}
.tone-wait .dot {
  background: var(--wait);
}
.tone-done .dot {
  background: var(--ok);
}
.tone-fail {
  color: var(--accent);
}
.tone-fail .dot {
  background: var(--accent);
}
.tone-idle {
  color: var(--text-faint);
}

/* One automation (mockup 2 header) */
.automation {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.automation-head {
  padding: 14px 20px 0;
  border-bottom: 1px solid var(--rule-soft);
}
.automation-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.automation-title {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 22px;
  font-weight: 500;
  color: var(--ink);
}
.auto-badge {
  padding: 3px 8px;
  border-radius: 999px;
  background: var(--wait-pastel);
  color: var(--wait-text);
  font-family: var(--font-mono);
  font-size: 11px;
  text-decoration: none;
}
.auto-badge.ok {
  background: var(--add-bg);
  color: var(--ok);
}
.auto-badge.warn {
  background: var(--accent-tint);
  color: var(--accent);
}
.automation-tabs {
  display: flex;
  align-items: center;
  gap: 18px;
  margin-top: 10px;
  font-size: 13px;
}
.automation-tabs a {
  padding: 8px 0;
  color: var(--text-faint);
  text-decoration: none;
  border-bottom: 2px solid transparent;
}
.automation-tabs a[aria-current='page'] {
  color: var(--ink);
  border-bottom-color: var(--ink);
}
.automation-body {
  flex: 1;
  min-height: 0;
  position: relative;
  overflow: auto;
}
.auto-loading {
  padding: 20px 28px;
}
```

`.dot` (the status dot) is global too, in `tokens.css`; the `tone-*` rules above only recolor it.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pnpm vitest run packages/ui-core apps/desktop/src/renderer/automations apps/desktop/src/renderer/conversation packages/ui-styles`
Expected: PASS.

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/screen-for.spec.ts --include src/app/components/project-nav.spec.ts --include src/app/parity.spec.ts)`
Expected: PASS. The parity guard's route check finds the placeholder, and `PLAN_21_OPS` covers the operations the React list calls.

Run: `pnpm typecheck`
Expected: no errors. ngc needs the new `automations` case in `screenFor`.

- [ ] **Step 8: Look at it**

Run `pnpm desktop`. Open a project: the Automations tab sits between Threads and Library. The list shows its empty state. Blank automation → name → the draft header. Close the app.

- [ ] **Step 9: Commit**

```bash
pnpm test
git add packages/ui-core/src/router.ts packages/ui-core/src/router.test.ts apps/desktop/src/renderer/components/ProjectNav.tsx apps/desktop/src/renderer/App.tsx apps/desktop/src/renderer/conversation/draft.ts apps/desktop/src/renderer/conversation/ConversationScreen.tsx apps/desktop/src/renderer/automations/AutomationsScreen.tsx apps/desktop/src/renderer/automations/AutomationList.tsx apps/desktop/src/renderer/automations/AutomationHeader.tsx apps/desktop/src/renderer/automations/AutomationsScreen.test.tsx packages/ui-styles/src/automations.css apps/web-ui/src/app/automations/automations-placeholder.ts apps/web-ui/src/app/components/project-nav.ts apps/web-ui/src/app/components/project-nav.spec.ts apps/web-ui/src/app/screen-for.ts apps/web-ui/src/app/screen-for.spec.ts
git commit -m "feat(desktop): the Automations tab: routes, list and header; a web placeholder until Plan 21

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The canvas: React Flow, custom nodes and route edges

**Files:**
- Modify: `apps/desktop/package.json`, `pnpm-lock.yaml` (React Flow)
- Modify: `apps/desktop/src/renderer/main.tsx`, `apps/desktop/src/renderer/styles.test.ts`
- Create: `apps/desktop/src/renderer/test/reactflow.ts`
- Create: `apps/desktop/src/renderer/automations/design/flow.ts`, `nodes.tsx`, `GraphCanvas.tsx`
- Modify: `packages/ui-styles/src/automations.css`
- Test: `apps/desktop/src/renderer/automations/design/GraphCanvas.test.tsx`

**Interfaces:**
- Consumes: `toGraph`, `edgeIndex`, `canConnect`, `GraphSelection`, `GraphIssues`, `GraphRun`, and the node data types (Tasks 4–5).
- Produces:
  - `design/flow.ts`: `StepFlowNode`, `StartFlowNode`, `StubFlowNode`, `FlowNode`, `FlowEdge` (React Flow's `Node`/`Edge` over the ui-core data types).
  - `GraphCanvas(props: CanvasProps)`, with `type CanvasProps = { def; layout; startLabel: string; selection: GraphSelection; onSelect(sel): void; issues?: GraphIssues; run?: GraphRun; editable: boolean; onMove?(positions: AutomationLayout): void; onConnect?(from, to): void; onDelete?(sel: { steps: string[]; edges: number[] }): void }`.
  - Nodes carry `data-testid="node-<step id>"` and `data-testid="node-start"`. The e2e test (Task 18) clicks them.
  - `installReactFlowShims()` for component tests.

- [ ] **Step 1: Add React Flow and its stylesheet**

Run: `pnpm --filter @desk/desktop add @xyflow/react@^12.12.0`
Expected: it appears under `dependencies` in `apps/desktop/package.json` (MIT, pure JavaScript).

In `apps/desktop/src/renderer/main.tsx`, add as the first line (before `@desk/ui-styles`, so Desk's rules win):

```ts
import '@xyflow/react/dist/style.css';
```

In `apps/desktop/src/renderer/styles.test.ts`, change the test's name to 'come from @desk/ui-styles and React Flow, loaded once by main.tsx; only the tray keeps a sheet of its own', and its expectation to:

```ts
    expect(imports.sort()).toEqual(['main.tsx: @desk/ui-styles/index.css', 'main.tsx: @xyflow/react/dist/style.css', 'tray/TrayPopover.tsx: ./tray.css']);
```

- [ ] **Step 2: Write the jsdom shims and the failing test**

Create `apps/desktop/src/renderer/test/reactflow.ts`:

```ts
/** jsdom lacks what React Flow measures with. These shims (after React Flow's testing guide) let canvases render in component tests. */
class ResizeObserverShim {
  constructor(private readonly cb: ResizeObserverCallback) {}
  observe(target: Element): void {
    this.cb([{ target } as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  unobserve(): void {}
  disconnect(): void {}
}

class DOMMatrixReadOnlyShim {
  m22: number;
  constructor(transform?: string) {
    const scale = transform?.match(/scale\(([0-9.]+)\)/)?.[1];
    this.m22 = scale !== undefined ? Number(scale) : 1;
  }
}

let installed = false;

export function installReactFlowShims(): void {
  if (installed) return;
  installed = true;
  globalThis.ResizeObserver = ResizeObserverShim as unknown as typeof ResizeObserver;
  (globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly = DOMMatrixReadOnlyShim;
  Object.defineProperties(HTMLElement.prototype, {
    offsetHeight: { configurable: true, get: () => 64 },
    offsetWidth: { configurable: true, get: () => 200 },
  });
  (SVGElement.prototype as unknown as { getBBox(): DOMRect }).getBBox = () => ({ x: 0, y: 0, width: 0, height: 0 }) as DOMRect;
}
```

Create `apps/desktop/src/renderer/automations/design/GraphCanvas.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { digestDef } from '@desk/ui-core/testing';
import { installReactFlowShims } from '../../test/reactflow';
import { GraphCanvas } from './GraphCanvas';

beforeAll(installReactFlowShims);
afterEach(cleanup);

describe('GraphCanvas', () => {
  it('draws the Start pill, each step (kind, title, detail, problems) and route stubs, and reports clicks as selections', async () => {
    const onSelect = vi.fn();
    render(
      <GraphCanvas
        def={digestDef()}
        layout={{}}
        startLabel="Mondays 08:00 · or Run now"
        selection={{ kind: 'none' }}
        onSelect={onSelect}
        issues={{ steps: { ok: ['question: required'] }, edges: {}, start: [] }}
        editable
      />,
    );
    const fetch = await screen.findByTestId('node-fetch');
    expect(fetch.textContent).toContain('script');
    expect(fetch.textContent).toContain('Fetch pages');
    expect(fetch.textContent).toContain('digest: fetch.py');
    expect(screen.getByTestId('node-ok').className).toContain('invalid');
    expect(screen.getByTestId('node-start').textContent).toContain('Mondays 08:00 · or Run now');
    expect(screen.getByText('unchanged · ends')).toBeTruthy();
    fireEvent.click(screen.getByTestId('node-sum'));
    expect(onSelect).toHaveBeenLastCalledWith({ kind: 'step', id: 'sum' });
    fireEvent.click(screen.getByTestId('node-start'));
    expect(onSelect).toHaveBeenLastCalledWith({ kind: 'start' });
  });

  it('lights steps with their run state', async () => {
    render(
      <GraphCanvas
        def={digestDef()}
        layout={{}}
        startLabel="schedule"
        selection={{ kind: 'none' }}
        onSelect={() => {}}
        run={{ steps: { fetch: { tone: 'ok', badge: '✓ 12s', detail: 'route changed' }, sum: { tone: 'run', badge: '● 5m', detail: 'reading acme.md' } }, fired: new Set([0]) }}
        editable={false}
      />,
    );
    const sum = await screen.findByTestId('node-sum');
    expect(sum.className).toContain('run-run');
    expect(sum.textContent).toContain('● 5m');
    expect(sum.textContent).toContain('reading acme.md');
    expect(screen.getByTestId('node-fetch').className).toContain('run-ok');
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pnpm vitest run apps/desktop/src/renderer/automations/design/GraphCanvas.test.tsx apps/desktop/src/renderer/styles.test.ts`
Expected: FAIL: cannot find `./GraphCanvas`. The styles test passes already.

- [ ] **Step 4: Write the flow types, nodes and canvas**

Create `apps/desktop/src/renderer/automations/design/flow.ts`:

```ts
import type { Edge, Node } from '@xyflow/react';
import type { RouteEdgeData, StartNodeData, StepNodeData, StubNodeData } from '@desk/ui-core';

// React Flow's view of the ui-core graph: `toGraph`'s nodes and edges already have these shapes.
export type StepFlowNode = Node<StepNodeData, 'step'>;
export type StartFlowNode = Node<StartNodeData, 'start'>;
export type StubFlowNode = Node<StubNodeData, 'stub'>;
export type FlowNode = StepFlowNode | StartFlowNode | StubFlowNode;
export type FlowEdge = Edge<RouteEdgeData, 'route'>;
```

Create `apps/desktop/src/renderer/automations/design/nodes.tsx`:

```tsx
import { BaseEdge, EdgeLabelRenderer, getBezierPath, Handle, Position, type EdgeProps, type EdgeTypes, type NodeProps, type NodeTypes } from '@xyflow/react';
import type { Step } from '@desk/protocol';
import type { FlowEdge, StartFlowNode, StepFlowNode, StubFlowNode } from './flow';

/** Each kind's colour stripe (automations.css maps these to tokens). */
const KIND_CLASS: Record<Step['kind'], string> = { script: 'k-script', agent: 'k-agent', ask: 'k-ask', wait: 'k-wait', automation: 'k-automation', tell_desk: 'k-tell' };

/** A step (mockup 2): kind, title and a one-line detail; red when validation found a problem; lit with its state in a run. */
export function StepNode({ data, selected }: NodeProps<StepFlowNode>) {
  const { step, run } = data;
  const cls = ['auto-node', KIND_CLASS[step.kind], selected ? 'selected' : '', data.errors.length ? 'invalid' : '', run ? `run-${run.tone}` : ''].filter(Boolean).join(' ');
  return (
    <div className={cls} data-testid={`node-${step.id}`} aria-label={`${data.kind} step: ${step.title}`} title={data.errors.length ? data.errors.join('\n') : undefined}>
      <Handle type="target" position={Position.Top} />
      <div className="auto-node-kind">
        <span>{data.kind}</span>
        {run ? <b>{run.badge}</b> : data.output ? <b>result</b> : null}
      </div>
      <div className="auto-node-title">{step.title}</div>
      <div className="auto-node-detail">{run?.detail ?? data.detail}</div>
      <Handle type="source" position={Position.Bottom} />
      <Handle type="source" id="side" position={Position.Right} isConnectable={false} className="auto-handle-side" />
    </div>
  );
}

/** The Start pill: schedules and Run now. Clicking it edits schedules and inputs. */
export function StartNode({ data, selected }: NodeProps<StartFlowNode>) {
  return (
    <div className={`auto-start${selected ? ' selected' : ''}${data.errors.length ? ' invalid' : ''}`} data-testid="node-start" aria-label={`Start: ${data.label}`} title={data.errors.length ? data.errors.join('\n') : undefined}>
      <span aria-hidden="true">▶</span>
      <span className="auto-start-label">{data.label}</span>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

/** A declared route no edge takes: "<route> · ends". */
export function StubNode({ data }: NodeProps<StubFlowNode>) {
  return (
    <div className="auto-stub">
      <Handle type="target" position={Position.Left} isConnectable={false} />
      {data.label}
    </div>
  );
}

/** An edge with its route and condition as a label. Clicks go through the label to the edge. */
export function RouteEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected, markerEnd }: EdgeProps<FlowEdge>) {
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition });
  const look = data?.look ?? 'plain';
  const state = `${selected ? ' selected' : ''}${data?.error ? ' invalid' : ''}`;
  return (
    <>
      <BaseEdge id={id} path={path} {...(markerEnd ? { markerEnd } : {})} className={`auto-edge look-${look}${state}`} />
      {data?.label ? (
        <EdgeLabelRenderer>
          <div className={`auto-edge-label${state}`} style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}>
            {data.label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

export const nodeTypes: NodeTypes = { step: StepNode, start: StartNode, stub: StubNode } as NodeTypes;
export const edgeTypes: EdgeTypes = { route: RouteEdge } as EdgeTypes;
```

Create `apps/desktop/src/renderer/automations/design/GraphCanvas.tsx`:

```tsx
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { applyNodeChanges, Background, Controls, ReactFlow, ReactFlowProvider, type Connection, type Edge, type NodeChange } from '@xyflow/react';
import type { AutomationDefinition, AutomationLayout } from '@desk/protocol';
import { canConnect, edgeIndex, toGraph, type GraphIssues, type GraphRun, type GraphSelection } from '@desk/ui-core';
import type { FlowEdge, FlowNode } from './flow';
import { edgeTypes, nodeTypes } from './nodes';

export type CanvasProps = {
  def: AutomationDefinition;
  layout: AutomationLayout;
  startLabel: string;
  selection: GraphSelection;
  onSelect(sel: GraphSelection): void;
  issues?: GraphIssues;
  run?: GraphRun;
  editable: boolean;
  /** Positions of nodes the user finished dragging. */
  onMove?(positions: AutomationLayout): void;
  onConnect?(from: string, to: string): void;
  /** Delete or Backspace on the selection: steps and edges (by index) of the same definition. */
  onDelete?(sel: { steps: string[]; edges: number[] }): void;
};

/** Keeps React Flow's own node fields (measured size, an ongoing drag) when nodes are derived again from the draft. */
function mergeNodes(prev: FlowNode[], next: FlowNode[]): FlowNode[] {
  const byId = new Map(prev.map((n) => [n.id, n]));
  return next.map((n) => {
    const p = byId.get(n.id);
    if (!p) return n;
    return { ...n, ...(p.measured ? { measured: p.measured } : {}), ...(p.dragging ? { position: p.position, dragging: true } : {}) } as FlowNode;
  });
}

function Canvas(o: CanvasProps) {
  const props = useRef(o);
  props.current = o;
  const graph = useMemo(
    () => toGraph(o.def, o.layout, { startLabel: o.startLabel, selection: o.selection, editable: o.editable, ...(o.issues ? { issues: o.issues } : {}), ...(o.run ? { run: o.run } : {}) }),
    [o.def, o.layout, o.startLabel, o.selection, o.editable, o.issues, o.run],
  );
  const flowNodes = graph.nodes as FlowNode[];
  const flowEdges = graph.edges as FlowEdge[];
  const [nodes, setNodes] = useState<FlowNode[]>(flowNodes);
  useEffect(() => setNodes((prev) => mergeNodes(prev, flowNodes)), [flowNodes]);

  const onNodesChange = useCallback((changes: NodeChange<FlowNode>[]) => setNodes((ns) => applyNodeChanges(changes, ns)), []);
  const onNodeDragStop = useCallback((_e: unknown, _node: FlowNode, dragged: FlowNode[]) => {
    const moved = dragged.filter((n) => n.type !== 'stub');
    if (moved.length) props.current.onMove?.(Object.fromEntries(moved.map((n) => [n.id, { x: Math.round(n.position.x), y: Math.round(n.position.y) }])));
  }, []);
  const onNodeClick = useCallback((_e: unknown, n: FlowNode) => {
    if (n.type === 'start') props.current.onSelect({ kind: 'start' });
    else if (n.type === 'step') props.current.onSelect({ kind: 'step', id: n.id });
  }, []);
  const onEdgeClick = useCallback((_e: unknown, e: FlowEdge) => {
    const index = edgeIndex(e.id);
    if (index !== null) props.current.onSelect({ kind: 'edge', index });
  }, []);
  const onPaneClick = useCallback(() => props.current.onSelect({ kind: 'none' }), []);
  const onConnect = useCallback((c: Connection) => {
    if (c.source && c.target) props.current.onConnect?.(c.source, c.target);
  }, []);
  const isValidConnection = useCallback((c: Edge | Connection) => !!c.source && !!c.target && canConnect(props.current.def, c.source, c.target) === null, []);
  const onDelete = useCallback(({ nodes: gone, edges: cut }: { nodes: FlowNode[]; edges: FlowEdge[] }) => {
    const steps = gone.filter((n) => n.type === 'step').map((n) => n.id);
    const edges = cut.map((e) => edgeIndex(e.id)).filter((i): i is number => i !== null);
    if (steps.length || edges.length) props.current.onDelete?.({ steps, edges });
  }, []);

  return (
    <ReactFlow<FlowNode, FlowEdge>
      nodes={nodes}
      edges={flowEdges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      colorMode="system"
      onNodesChange={onNodesChange}
      onNodeDragStop={onNodeDragStop}
      onNodeClick={onNodeClick}
      onEdgeClick={onEdgeClick}
      onPaneClick={onPaneClick}
      onConnect={onConnect}
      isValidConnection={isValidConnection}
      onDelete={onDelete}
      nodesDraggable={o.editable}
      nodesConnectable={o.editable}
      elementsSelectable
      deleteKeyCode={o.editable ? ['Backspace', 'Delete'] : null}
      fitView
      fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
      minZoom={0.3}
      maxZoom={1.5}
    >
      <Background gap={18} size={1} color="var(--rule)" />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

/** The automation graph, top to bottom (spec §8.2): editable in Design, read-only and lit in a run. */
export function GraphCanvas(o: CanvasProps) {
  return (
    <div className="auto-canvas">
      <ReactFlowProvider>
        <Canvas {...o} />
      </ReactFlowProvider>
    </div>
  );
}
```

`graph.nodes as FlowNode[]` is a widening cast: the ui-core node types are React Flow's `Node` shape with fewer fields. If the typecheck accepts `const flowNodes: FlowNode[] = graph.nodes;` without it, drop the cast.

Append to `packages/ui-styles/src/automations.css`:

```css
/* The canvas (React Flow): its variables mapped to tokens */
.auto-canvas {
  position: relative;
  flex: 1;
  min-width: 0;
  min-height: 0;
  height: 100%;
}
.auto-canvas .react-flow {
  --xy-background-color: var(--ground);
  --xy-edge-stroke: var(--muted);
  --xy-edge-stroke-selected: var(--run);
  --xy-connectionline-stroke: var(--run);
  --xy-handle-background-color: var(--card);
  --xy-handle-border-color: var(--muted);
  --xy-selection-background-color: var(--run-pastel);
  --xy-selection-border: 1px dotted var(--run);
  --xy-controls-button-background-color: var(--card);
  --xy-controls-button-background-color-hover: var(--highlight);
  --xy-controls-button-color: var(--ink);
  --xy-controls-button-color-hover: var(--ink);
  --xy-controls-button-border-color: var(--rule-soft);
  --xy-controls-box-shadow: none;
  --xy-attribution-background-color: transparent;
  background: var(--ground);
}
.auto-canvas .react-flow__attribution a {
  color: var(--muted);
}
.auto-canvas .react-flow__controls {
  border: 1px solid var(--rule);
  border-radius: 8px;
  overflow: hidden;
}
.auto-canvas .react-flow__handle {
  width: 9px;
  height: 9px;
  border-width: 1.5px;
}
.auto-canvas .react-flow__handle.valid {
  border-color: var(--run);
}
.auto-canvas .auto-handle-side {
  opacity: 0;
  pointer-events: none;
}

/* Nodes: a stripe in the kind's colour (--k) */
.auto-node {
  --k: var(--text);
  position: relative;
  width: 200px;
  height: 64px;
  box-sizing: border-box;
  padding: 7px 10px 7px 14px;
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 12px;
  box-shadow: 0 4px 10px rgba(var(--shadow-rgb), 0.06);
  overflow: hidden;
}
.auto-node::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 5px;
  background: var(--k);
}
.auto-node.k-agent {
  --k: var(--run);
}
.auto-node.k-ask {
  --k: var(--wait);
}
.auto-node.k-wait {
  --k: var(--muted);
}
.auto-node.k-automation {
  --k: var(--ok);
}
.auto-node.k-tell {
  --k: var(--accent);
}
.auto-node-kind {
  display: flex;
  justify-content: space-between;
  gap: 6px;
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--k);
}
.auto-node-kind b {
  font-weight: 500;
  color: var(--text-faint);
}
.auto-node-title {
  margin-top: 1px;
  font-family: var(--font-serif);
  font-size: 14.5px;
  line-height: 1.15;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.auto-node-detail {
  margin-top: 2px;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.auto-node.selected {
  border-color: var(--run);
  outline: 3px solid var(--run-ring);
}
.auto-node.invalid {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-tint);
}
/* Run states (view A) */
.auto-node.run-ok {
  --k: var(--ok);
}
.auto-node.run-ok .auto-node-kind b {
  color: var(--ok);
}
.auto-node.run-run {
  --k: var(--run);
  border-color: var(--run);
  outline: 3px solid var(--run-ring);
}
.auto-node.run-run .auto-node-kind b {
  color: var(--run);
}
.auto-node.run-wait {
  --k: var(--wait);
  border-color: var(--wait);
  background: var(--wait-pastel);
}
.auto-node.run-wait .auto-node-kind b {
  color: var(--wait-text);
}
.auto-node.run-pending {
  opacity: 0.55;
}
.auto-node.run-skipped {
  --k: var(--muted);
  opacity: 0.45;
  background: repeating-linear-gradient(135deg, var(--card), var(--card) 6px, var(--sunken) 6px, var(--sunken) 12px);
}
.auto-node.run-fail {
  --k: var(--accent);
  border-color: var(--accent);
}
.auto-node.run-fail .auto-node-kind b {
  color: var(--accent);
}
.auto-node.run-idle {
  --k: var(--muted);
  opacity: 0.7;
}
.auto-start {
  width: 240px;
  height: 44px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 0 16px;
  border-radius: 999px;
  background: var(--ink);
  color: var(--on-ink);
  font-size: 12px;
}
.auto-start-label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.auto-start.selected {
  outline: 3px solid var(--run-ring);
}
.auto-start.invalid {
  outline: 3px solid var(--accent);
}
.auto-stub {
  padding: 2px 8px;
  border: 1px dashed var(--muted-soft);
  border-radius: 999px;
  background: var(--ground);
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-faint);
  white-space: nowrap;
}

/* Edges */
.auto-canvas .auto-edge {
  stroke: var(--muted);
  stroke-width: 1.6;
}
.auto-canvas .auto-edge.look-stub,
.auto-canvas .auto-edge.look-idle {
  stroke: var(--muted-soft);
  stroke-dasharray: 4 4;
}
.auto-canvas .auto-edge.look-fired {
  stroke: var(--ok);
  stroke-width: 1.8;
}
.auto-canvas .auto-edge.selected {
  stroke: var(--run);
  stroke-width: 2.2;
}
.auto-canvas .auto-edge.invalid {
  stroke: var(--accent);
}
.auto-edge-label {
  position: absolute;
  pointer-events: none;
  padding: 1px 5px;
  border: 1px solid var(--rule);
  border-radius: 4px;
  background: var(--ground);
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text);
  white-space: nowrap;
}
.auto-edge-label.selected {
  border-color: var(--run);
  color: var(--run-text);
}
.auto-edge-label.invalid {
  border-color: var(--accent);
  color: var(--accent);
}
```

`rgba(var(--shadow-rgb), 0.06)` passes the token test: its pattern skips `rgba(var(`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pnpm vitest run apps/desktop/src/renderer/automations/design/GraphCanvas.test.tsx apps/desktop/src/renderer/styles.test.ts packages/ui-styles`
Expected: PASS.

If React Flow in jsdom does not fire `onNodeClick` for a synthetic click on the node's content, first confirm the shims are installed (`beforeAll(installReactFlowShims)`). If it still fails, change the test to click the `.react-flow__node` wrapper: `fireEvent.click(screen.getByTestId('node-sum').closest('.react-flow__node')!)`. Keep the assertion.

Run: `pnpm typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
pnpm test
git add apps/desktop/package.json pnpm-lock.yaml apps/desktop/src/renderer/main.tsx apps/desktop/src/renderer/styles.test.ts apps/desktop/src/renderer/test/reactflow.ts apps/desktop/src/renderer/automations/design/flow.ts apps/desktop/src/renderer/automations/design/nodes.tsx apps/desktop/src/renderer/automations/design/GraphCanvas.tsx apps/desktop/src/renderer/automations/design/GraphCanvas.test.tsx packages/ui-styles/src/automations.css
git commit -m "feat(desktop): the automation canvas: React Flow, custom nodes, route edges, run looks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The template field

**Files:**
- Create: `apps/desktop/src/renderer/automations/design/TemplateField.tsx`
- Modify: `packages/ui-styles/src/automations.css`
- Test: `apps/desktop/src/renderer/automations/design/TemplateField.test.tsx`

**Interfaces:**
- Consumes: `activeToken`, `filterSuggestions`, `insertSuggestion`, `isKnownPath`, `pathsIn`, `TemplateSuggestion` (Task 5).
- Produces: `TemplateField({ id, label, value, onChange, suggestions, multiline?, rows?, placeholder?, hint?, errors?, labelHidden? })`. It is a combobox with a listbox of suggestions, plus chips for the paths the text uses. `labelHidden` keeps the label for screen readers only (list rows, Task 10).

- [ ] **Step 1: Write the failing test**

Create `apps/desktop/src/renderer/automations/design/TemplateField.test.tsx`:

```tsx
// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { templateSuggestions } from '@desk/ui-core';
import { digestDef } from '@desk/ui-core/testing';
import { TemplateField } from './TemplateField';

afterEach(cleanup);

function Harness() {
  const [v, setV] = useState('');
  return <TemplateField id="brief" label="Brief" value={v} onChange={setV} suggestions={templateSuggestions(digestDef(), 'ok')} multiline />;
}

describe('TemplateField', () => {
  it('suggests paths after {{, inserts the chosen one, and marks paths not available here', async () => {
    render(<Harness />);
    const box = screen.getByLabelText('Brief') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'Publish {{steps.sum.o' } });
    fireEvent.mouseDown(await screen.findByRole('option', { name: /steps\.sum\.outputs\.headline/ }));
    expect(box.value).toBe('Publish {{steps.sum.outputs.headline}}');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(screen.getByText('{{steps.sum.outputs.headline}}').className).toBe('auto-tpl');
    fireEvent.change(box, { target: { value: 'x {{inputs.nope}}' } });
    expect(screen.getByText('{{inputs.nope}}').className).toContain('unknown');
  });

  it('moves through suggestions with the arrow keys and picks with Enter', async () => {
    render(<Harness />);
    const box = screen.getByLabelText('Brief') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: '{{run.' } });
    const options = await screen.findAllByRole('option');
    expect(options[0]!.getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    const second = screen.getAllByRole('option')[1]!;
    expect(second.getAttribute('aria-selected')).toBe('true');
    const path = second.querySelector('.mono')!.textContent!;
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(box.value).toBe(`{{${path}}}`);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm vitest run apps/desktop/src/renderer/automations/design/TemplateField.test.tsx`
Expected: FAIL: cannot find `./TemplateField`.

- [ ] **Step 3: Write `TemplateField.tsx`**

Create `apps/desktop/src/renderer/automations/design/TemplateField.tsx`:

```tsx
import { useId, useMemo, useRef, useState, type ChangeEvent, type KeyboardEvent, type SyntheticEvent } from 'react';
import { activeToken, filterSuggestions, insertSuggestion, isKnownPath, pathsIn, type TemplateSuggestion } from '@desk/ui-core';

type Box = HTMLInputElement | HTMLTextAreaElement;

/** A text field with `{{…}}` autocomplete (inputs, upstream steps' results, the run), showing the paths it uses as chips (spec §8.2). */
export function TemplateField(o: {
  id: string;
  label: string;
  value: string;
  onChange(value: string): void;
  suggestions: TemplateSuggestion[];
  multiline?: boolean;
  rows?: number;
  placeholder?: string;
  hint?: string;
  errors?: string[];
  /** Keeps the label for screen readers only (a row of a list). */
  labelHidden?: boolean;
}) {
  const box = useRef<Box | null>(null);
  const [caret, setCaret] = useState<number | null>(null);
  const [active, setActive] = useState(0);
  const listId = useId();
  const query = caret === null ? null : (activeToken(o.value, caret)?.query ?? null);
  const options = useMemo(() => (query === null ? [] : filterSuggestions(o.suggestions, query)), [query, o.suggestions]);
  const open = options.length > 0;

  const choose = (s: TemplateSuggestion) => {
    const next = insertSuggestion(o.value, caret ?? o.value.length, s);
    o.onChange(next.text);
    setCaret(s.open ? next.caret : null);
    setActive(0);
    requestAnimationFrame(() => {
      box.current?.focus();
      box.current?.setSelectionRange(next.caret, next.caret);
    });
  };
  const track = (e: SyntheticEvent<Box>) => setCaret(e.currentTarget.selectionStart);
  const onChange = (e: ChangeEvent<Box>) => {
    o.onChange(e.target.value);
    setCaret(e.target.selectionStart);
    setActive(0);
  };
  const onKeyDown = (e: KeyboardEvent<Box>) => {
    if (!open) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => (a + (e.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length);
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault();
      const s = options[Math.min(active, options.length - 1)];
      if (s) choose(s);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setCaret(null);
    }
  };
  const shared = {
    id: o.id,
    value: o.value,
    placeholder: o.placeholder,
    role: 'combobox',
    'aria-expanded': open,
    'aria-controls': listId,
    'aria-autocomplete': 'list' as const,
    onChange,
    onKeyDown,
    onKeyUp: track,
    onClick: track,
    onBlur: () => setTimeout(() => setCaret(null), 150),
  };
  const used = pathsIn(o.value);
  return (
    <div className="field auto-template">
      <label htmlFor={o.id} className={o.labelHidden ? 'sr-only' : undefined}>
        {o.label}
      </label>
      {o.multiline ? (
        <textarea {...shared} className="textarea" rows={o.rows ?? 4} ref={(el) => void (box.current = el)} />
      ) : (
        <input {...shared} className="input" ref={(el) => void (box.current = el)} />
      )}
      {open ? (
        <ul id={listId} role="listbox" className="auto-suggest">
          {options.map((s, i) => (
            <li
              key={s.path}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(s);
              }}
            >
              <span className="mono">{s.open ? `${s.path}…` : s.path}</span>
              <span className="muted small">{s.label}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {used.length ? (
        <div className="auto-chips">
          {used.map((p) => {
            const known = isKnownPath(p, o.suggestions);
            return (
              <span key={p} className={known ? 'auto-tpl' : 'auto-tpl unknown'} title={known ? undefined : 'Not available here'}>
                {`{{${p}}}`}
              </span>
            );
          })}
        </div>
      ) : null}
      {o.errors?.length ? (
        o.errors.map((e) => (
          <p key={e} className="field-error" role="alert">
            {e}
          </p>
        ))
      ) : o.hint ? (
        <p className="field-hint">{o.hint}</p>
      ) : null}
    </div>
  );
}
```

An open suggestion (`steps.x.outputs.`) shows with an ellipsis, so the path displayed differs from the one inserted. The Enter test uses `run.` suggestions, none of which are open.

Append to `packages/ui-styles/src/automations.css`:

```css
/* Template fields */
.auto-template {
  position: relative;
}
.auto-suggest {
  position: absolute;
  z-index: 20;
  left: 0;
  right: 0;
  margin: 2px 0 0;
  padding: 4px;
  list-style: none;
  background: var(--raised);
  border: 1px solid var(--rule);
  border-radius: 8px;
  box-shadow: var(--card-shadow);
  max-height: 240px;
  overflow: auto;
}
.auto-suggest li {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}
.auto-suggest li[aria-selected='true'] {
  background: var(--run-pastel);
}
.auto-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}
.auto-tpl {
  padding: 0 4px;
  border-radius: 4px;
  background: var(--run-pastel);
  color: var(--run-text);
  font-family: var(--font-mono);
  font-size: 11px;
}
.auto-tpl.unknown {
  background: var(--accent-tint);
  color: var(--accent);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm vitest run apps/desktop/src/renderer/automations/design/TemplateField.test.tsx packages/ui-styles`
Expected: PASS.

Run: `pnpm typecheck`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
pnpm test
git add apps/desktop/src/renderer/automations/design/TemplateField.tsx apps/desktop/src/renderer/automations/design/TemplateField.test.tsx packages/ui-styles/src/automations.css
git commit -m "feat(desktop): the {{…}} template field

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The step and edge inspectors

The right-hand panel of Design when a step or an edge is selected (spec §8.2, §3–4). Pickers list the skills, scripts and automations that exist.

**Files:**
- Create: `packages/ui-core/src/automation-fields.ts`, `packages/ui-core/src/automation-fields.test.ts`
- Modify: `packages/ui-core/src/index.ts`
- Create: `apps/desktop/src/renderer/automations/design/ListEditor.tsx`, `pickers.ts`, `StepKindFields.tsx`, `StepInspector.tsx`, `EdgeInspector.tsx`
- Modify: `packages/ui-styles/src/automations.css`
- Test: `apps/desktop/src/renderer/automations/design/inspectors.test.tsx`

**Interfaces:**
- Consumes: `patchStep`, `renameStep`, `patchEdge`, `templateSuggestions`, `conditionSuggestions`, `incomingCount`, `STEP_KIND_LABEL`, `AutomationDoc` (Tasks 3–5); `TemplateField` (Task 9).
- Produces:
  - `automation-fields.ts`:
    - `type SkillChoice = { name; source: 'project'|'global'|'builtin' }`, `skillChoices(project, global, builtins): SkillChoice[]`;
    - `scriptFiles(files): string[]`;
    - `routeProblem(name): string | null`, `keyProblem(key): string | null`;
    - `type RouteOption = { value; label }`, `edgeRouteOptions(def, from, current?): RouteOption[]`.
  - `ListEditor({ id, label, values, onChange, placeholder?, hint?, addLabel?, check?, suggestions?, max? })`.
  - `pickers.ts`: `useSkillChoices(projectId)`, `useSkillScripts(projectId, choice)`, `useAutomationNames(projectId)`, `useAutomationInputs(projectId, name)`.
  - `StepInspector({ projectId, doc, stepId, selfName, sources, errors, onChange, onRenamed, onDelete })`.
  - `EdgeInspector({ doc, index, errors, onChange, onDelete })`.
  - CSS: `.auto-inspector`, `.auto-issues`, `.auto-rows`, `.auto-row`, `.auto-paths`, `.auto-path`, `.auto-inline`.

- [ ] **Step 1: Write the failing tests**

Create `packages/ui-core/src/automation-fields.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import type { SkillSummary } from '@desk/client';
import type { BuiltinSkillInfo } from '@desk/protocol';
import { digestDef } from './testing/automations';
import { edgeRouteOptions, keyProblem, routeProblem, scriptFiles, skillChoices } from './automation-fields';

const skill = (name: string, scope: 'global' | 'project', error?: string) => ({ name, scope, description: '', dir: '/x', version: 1, ...(error ? { error } : {}) }) as SkillSummary;
const builtin = (name: string, enabled = true, broken: string | null = null) => ({ name, enabled, broken }) as BuiltinSkillInfo;

describe('automation fields', () => {
  it('lists skills once, a project skill over a global one over a built-in, without broken or disabled ones', () => {
    expect(skillChoices([skill('digest', 'project'), skill('bad', 'project', 'no SKILL.md')], [skill('digest', 'global'), skill('notes', 'global')], [builtin('pdf-toolkit'), builtin('images', false), builtin('audio-video', true, 'digest mismatch'), builtin('notes')])).toEqual([
      { name: 'digest', source: 'project' },
      { name: 'notes', source: 'global' },
      { name: 'pdf-toolkit', source: 'builtin' },
    ]);
  });

  it('lists a skill’s scripts, not its helpers or caches', () => {
    expect(scriptFiles([{ path: 'SKILL.md' }, { path: 'scripts/fetch.py' }, { path: 'scripts/_common.py' }, { path: 'scripts/__pycache__/x.py' }, { path: 'run.sh' }, { path: 'scripts/lib/.hidden.js' }, { path: 'packages.txt' }])).toEqual(['run.sh', 'scripts/fetch.py']);
  });

  it('checks route names and output keys', () => {
    expect(routeProblem('changed')).toBeNull();
    expect(routeProblem('error')).toMatch(/set by Desk/);
    expect(routeProblem('Bad Name')).toMatch(/lowercase/i);
    expect(keyProblem('headline')).toBeNull();
    expect(keyProblem('Head-line')).toMatch(/lowercase/i);
  });

  it('offers the routes an edge can wait for', () => {
    const def = digestDef();
    expect(edgeRouteOptions(def, 'fetch')).toEqual([
      { value: '', label: 'When it succeeds' },
      { value: 'changed', label: 'On route changed' },
      { value: 'unchanged', label: 'On route unchanged' },
    ]);
    expect(edgeRouteOptions(def, 'ok').map((o) => o.value)).toEqual(['', 'rejected']);
    const lenient = { ...def, steps: def.steps.map((s) => (s.id === 'sum' ? { ...s, on_error: 'continue' as const } : s)) };
    expect(edgeRouteOptions(lenient, 'sum', 'gone').map((o) => o.value)).toEqual(['', 'error', 'gone']);
  });
});
```

Create `apps/desktop/src/renderer/automations/design/inspectors.test.tsx`:

```tsx
// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AutomationDefinition } from '@desk/protocol';
import { addStep, START_ID, type AutomationDoc, type GraphSelection } from '@desk/ui-core';
import { digestDef } from '@desk/ui-core/testing';
import { installBridge } from '../../test/bridge';
import { EdgeInspector } from './EdgeInspector';
import { StepInspector } from './StepInspector';

afterEach(cleanup);

const doc = (def: AutomationDefinition = digestDef()): AutomationDoc => ({ def, layout: { [START_ID]: { x: 0, y: 0 } } });

function Harness(p: { initial: AutomationDoc; select: GraphSelection; onDoc(d: AutomationDoc): void; onDelete?: () => void }) {
  const [d, setD] = useState(p.initial);
  const [sel, setSel] = useState(p.select);
  const change = (next: AutomationDoc) => {
    setD(next);
    p.onDoc(next);
  };
  if (sel.kind === 'step')
    return <StepInspector projectId="p" doc={d} stepId={sel.id} selfName="digest" sources={[{ id: 's1', label: 'site' }]} errors={sel.id === 'ok' ? ['question: required'] : []} onChange={change} onRenamed={(id) => setSel({ kind: 'step', id })} onDelete={p.onDelete ?? (() => {})} />;
  if (sel.kind === 'edge') return <EdgeInspector doc={d} index={sel.index} errors={[]} onChange={change} onDelete={p.onDelete ?? (() => {})} />;
  return null;
}

function bridge() {
  return installBridge({
    'skills.list': (i: { projectId?: string }) => (i.projectId ? [{ name: 'digest', scope: 'project', description: '', dir: '/d', version: 1 }] : []),
    'builtins.list': () => [{ name: 'pdf-toolkit', enabled: true, broken: null }],
    'skills.get': () => ({ name: 'digest', files: [{ path: 'SKILL.md', size: 1 }, { path: 'scripts/fetch.py', size: 1 }, { path: 'scripts/diff.py', size: 1 }] }),
    'automations.list': () => [{ id: 'a1', name: 'digest' }, { id: 'a2', name: 'notify' }],
    'automations.get': () => ({ definition: { inputs: [{ key: 'message', label: 'Message', type: 'text', required: true }] } }),
  });
}

describe('StepInspector', () => {
  it('edits a script step: title, id (everywhere), skill and script pickers, arguments, routes and errors', async () => {
    bridge();
    const onDoc = vi.fn();
    render(<Harness initial={doc()} select={{ kind: 'step', id: 'fetch' }} onDoc={onDoc} />);
    expect(screen.getByText('Script step')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Fetch the pages' } });
    expect(onDoc.mock.lastCall![0].def.steps[0].title).toBe('Fetch the pages');

    const id = screen.getByLabelText('Id');
    fireEvent.change(id, { target: { value: 'Bad Id' } });
    fireEvent.blur(id);
    expect(screen.getByRole('alert').textContent).toMatch(/lowercase/i);
    fireEvent.change(id, { target: { value: 'grab' } });
    fireEvent.blur(id);
    expect(onDoc.mock.lastCall![0].def.edges[0]).toEqual({ from: 'grab', to: 'sum', route: 'changed' });

    const skill = (await screen.findByRole('option', { name: 'pdf-toolkit (built-in)' })).closest('select')!;
    expect(within(skill).getAllByRole('option').map((o) => o.textContent)).toEqual(['Choose a skill…', 'digest', 'pdf-toolkit (built-in)']);
    const script = screen.getByLabelText('Script');
    await waitFor(() => expect(within(script).getAllByRole('option').map((o) => o.textContent)).toEqual(['Choose a script…', 'scripts/diff.py', 'scripts/fetch.py']));
    fireEvent.change(script, { target: { value: 'scripts/diff.py' } });
    expect(onDoc.mock.lastCall![0].def.steps[0]).toMatchObject({ skill: 'digest', script: 'scripts/diff.py' });

    fireEvent.click(screen.getByRole('button', { name: 'Add argument' }));
    fireEvent.change(screen.getByLabelText('Arguments 2'), { target: { value: '--fast' } });
    expect(onDoc.mock.lastCall![0].def.steps[0].args).toEqual(['{{inputs.topic}}', '--fast']);

    fireEvent.click(screen.getByRole('button', { name: 'Add route' }));
    fireEvent.change(screen.getByLabelText('Routes 3'), { target: { value: 'error' } });
    expect(screen.getByText(/set by Desk/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText('If it fails'), { target: { value: 'retry' } });
    expect(onDoc.mock.lastCall![0].def.steps[0].on_error).toEqual({ retry: 1 });
    fireEvent.change(screen.getByLabelText('Attempts after the first'), { target: { value: '3' } });
    expect(onDoc.mock.lastCall![0].def.steps[0].on_error).toEqual({ retry: 3 });
  });

  it('edits an agent step: brief, skills, output keys, worktree', async () => {
    bridge();
    const onDoc = vi.fn();
    render(<Harness initial={doc()} select={{ kind: 'step', id: 'sum' }} onDoc={onDoc} />);
    fireEvent.change(screen.getByLabelText('Brief'), { target: { value: 'Summarise.' } });
    expect(onDoc.mock.lastCall![0].def.steps[1].brief).toBe('Summarise.');
    fireEvent.click(await screen.findByLabelText('digest'));
    expect(onDoc.mock.lastCall![0].def.steps[1].skills).toEqual(['web-research', 'digest']);
    fireEvent.click(screen.getByRole('button', { name: 'Add output' }));
    fireEvent.change(screen.getByLabelText('Output 2 key'), { target: { value: 'count' } });
    fireEvent.change(screen.getByLabelText('Output 2 meaning'), { target: { value: 'how many changed' } });
    expect(onDoc.mock.lastCall![0].def.steps[1].output_keys).toEqual([
      { key: 'headline', description: 'the biggest change' },
      { key: 'count', description: 'how many changed' },
    ]);
    fireEvent.change(screen.getByLabelText('Work in a git worktree of'), { target: { value: 's1' } });
    expect(onDoc.mock.lastCall![0].def.steps[1].git_source_id).toBe('s1');
  });

  it('shows a step’s problems, switches a wait between minutes and a time, and picks a sub-automation’s inputs', async () => {
    bridge();
    const onDoc = vi.fn();
    const onDelete = vi.fn();
    const withMore = addStep(addStep(doc(), 'wait', 'ok').doc, 'automation', 'ok').doc;
    const { unmount } = render(<Harness initial={withMore} select={{ kind: 'step', id: 'ok' }} onDoc={onDoc} onDelete={onDelete} />);
    expect(screen.getByRole('alert').textContent).toContain('question: required');
    fireEvent.click(screen.getByRole('button', { name: 'Delete step' }));
    expect(onDelete).toHaveBeenCalled();
    unmount();

    const w = render(<Harness initial={withMore} select={{ kind: 'step', id: 'wait' }} onDoc={onDoc} />);
    fireEvent.click(screen.getByLabelText('Until a time of day'));
    expect(onDoc.mock.lastCall![0].def.steps.find((s: { id: string }) => s.id === 'wait')).toMatchObject({ until: '08:00' });
    expect('minutes' in onDoc.mock.lastCall![0].def.steps.find((s: { id: string }) => s.id === 'wait')).toBe(false);
    w.unmount();

    render(<Harness initial={withMore} select={{ kind: 'step', id: 'automation' }} onDoc={onDoc} />);
    const pick = await screen.findByLabelText('Automation to run');
    await waitFor(() => expect(within(pick).getAllByRole('option').map((o) => o.textContent)).toEqual(['Choose an automation…', 'notify']));
    fireEvent.change(pick, { target: { value: 'notify' } });
    fireEvent.change(await screen.findByLabelText('Message'), { target: { value: '{{steps.sum.summary}}' } });
    expect(onDoc.mock.lastCall![0].def.steps.find((s: { id: string }) => s.id === 'automation')).toMatchObject({ automation: 'notify', inputs: { message: '{{steps.sum.summary}}' } });
  });
});

describe('EdgeInspector', () => {
  it('sets an edge’s route and condition, offering the paths its source can see', () => {
    const onDoc = vi.fn();
    render(<Harness initial={doc()} select={{ kind: 'edge', index: 0 }} onDoc={onDoc} />);
    expect(screen.getByText('Fetch pages → Summarise')).toBeTruthy();
    const route = screen.getByLabelText('Fires');
    expect(within(route).getAllByRole('option').map((o) => o.textContent)).toEqual(['When it succeeds', 'On route changed', 'On route unchanged']);
    fireEvent.change(route, { target: { value: 'unchanged' } });
    expect(onDoc.mock.lastCall![0].def.edges[0]).toEqual({ from: 'fetch', to: 'sum', route: 'unchanged' });
    fireEvent.click(screen.getByRole('button', { name: 'steps.fetch.status' }));
    expect(onDoc.mock.lastCall![0].def.edges[0].when).toBe('steps.fetch.status');
    fireEvent.change(screen.getByLabelText('Only if'), { target: { value: '' } });
    expect(onDoc.mock.lastCall![0].def.edges[0]).toEqual({ from: 'fetch', to: 'sum', route: 'unchanged' });
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm vitest run packages/ui-core/src/automation-fields.test.ts apps/desktop/src/renderer/automations/design/inspectors.test.tsx`
Expected: FAIL: the modules do not exist.

- [ ] **Step 3: Write `automation-fields.ts`**

Create `packages/ui-core/src/automation-fields.ts`:

```ts
import type { SkillSummary } from '@desk/client';
import { InputKey, RESERVED_ROUTES, RouteName, type AutomationDefinition, type BuiltinSkillInfo } from '@desk/protocol';

/** A skill a step can name, and where the one agents would use lives. */
export type SkillChoice = { name: string; source: 'project' | 'global' | 'builtin' };

/** The skills a step can name, each once: a project skill over a global one over a built-in (as agents resolve them). Broken, disabled or malformed ones are left out. */
export function skillChoices(project: SkillSummary[], global: SkillSummary[], builtins: BuiltinSkillInfo[]): SkillChoice[] {
  const out = new Map<string, SkillChoice>();
  for (const b of builtins) if (b.enabled && !b.broken) out.set(b.name, { name: b.name, source: 'builtin' });
  for (const g of global) if (!g.error) out.set(g.name, { name: g.name, source: 'global' });
  for (const p of project) if (!p.error) out.set(p.name, { name: p.name, source: 'project' });
  return [...out.values()].sort((a, b) => a.name.localeCompare(b.name));
}

const SCRIPT = /\.(py|js|mjs|cjs|ts|sh)$/;

/** A skill's scripts, by path: files with a script extension, not helpers (`_common.py`), caches or hidden files. */
export function scriptFiles(files: Array<{ path: string }>): string[] {
  return files
    .map((f) => f.path)
    .filter((p) => SCRIPT.test(p) && !p.split('/').some((part) => part.startsWith('.') || part.startsWith('_')))
    .sort();
}

/** Why a step cannot declare a route of this name, or null. */
export function routeProblem(name: string): string | null {
  if ((RESERVED_ROUTES as readonly string[]).includes(name)) return `${name} is set by Desk itself (a failure with "continue", or a rejected question). Edges can wait for it without declaring it.`;
  const r = RouteName.safeParse(name);
  return r.success ? null : (r.error.issues[0]?.message ?? 'Not a valid route name');
}

/** Why an output key is not valid, or null. */
export function keyProblem(key: string): string | null {
  const r = InputKey.safeParse(key);
  return r.success ? null : (r.error.issues[0]?.message ?? 'Not a valid key');
}

export type RouteOption = { value: string; label: string };

/** What an edge leaving `from` can wait for: success (no route), each declared route, `error` (on error continue), `rejected` (Ask me). */
export function edgeRouteOptions(def: AutomationDefinition, from: string, current?: string): RouteOption[] {
  const s = def.steps.find((x) => x.id === from);
  const out: RouteOption[] = [{ value: '', label: 'When it succeeds' }];
  for (const r of s?.routes ?? []) out.push({ value: r, label: `On route ${r}` });
  if (s?.on_error === 'continue') out.push({ value: 'error', label: 'When it fails (route error)' });
  if (s?.kind === 'ask') out.push({ value: 'rejected', label: 'When you reject it' });
  if (current && !out.some((o) => o.value === current)) out.push({ value: current, label: `On route ${current} (not declared)` });
  return out;
}
```

`scriptFiles` drops every path part starting with `_`, so `scripts/__pycache__/…` and `scripts/_common.py` both go.

In `packages/ui-core/src/index.ts`, add `export * from './automation-fields';` among the automation modules (alphabetical: after `./automation-draft`).

- [ ] **Step 4: Write the list editor and pickers**

Create `apps/desktop/src/renderer/automations/design/ListEditor.tsx`:

```tsx
import type { TemplateSuggestion } from '@desk/ui-core';
import { Button } from '../../components/Button';
import { TemplateField } from './TemplateField';

/** A list of short texts (routes, globs, arguments): one row each, Remove per row, and Add. Rows are labelled "<label> <n>". */
export function ListEditor(o: {
  id: string;
  label: string;
  values: string[];
  onChange(values: string[]): void;
  placeholder?: string;
  hint?: string;
  addLabel?: string;
  /** Checks one value; its message shows under that row. */
  check?(value: string): string | null;
  /** Makes each row a template field (script arguments). */
  suggestions?: TemplateSuggestion[];
  max?: number;
}) {
  const set = (i: number, v: string) => o.onChange(o.values.map((x, j) => (j === i ? v : x)));
  const remove = (i: number) => o.onChange(o.values.filter((_, j) => j !== i));
  return (
    <fieldset className="field auto-rows">
      <legend>{o.label}</legend>
      {o.values.map((v, i) => {
        const rowId = `${o.id}-${i}`;
        const rowLabel = `${o.label} ${i + 1}`;
        const problem = v && o.check ? o.check(v) : null;
        return (
          <div key={i}>
            <div className="auto-row">
              {o.suggestions ? (
                <TemplateField id={rowId} label={rowLabel} labelHidden value={v} onChange={(x) => set(i, x)} suggestions={o.suggestions} {...(o.placeholder ? { placeholder: o.placeholder } : {})} />
              ) : (
                <input id={rowId} aria-label={rowLabel} className="input mono" value={v} placeholder={o.placeholder} onChange={(e) => set(i, e.target.value)} />
              )}
              <Button size="sm" variant="ghost" aria-label={`Remove ${rowLabel}`} onClick={() => remove(i)}>
                ✕
              </Button>
            </div>
            {problem ? <p className="field-error auto-row-error">{problem}</p> : null}
          </div>
        );
      })}
      {o.max === undefined || o.values.length < o.max ? (
        <div>
          <Button size="sm" onClick={() => o.onChange([...o.values, ''])}>
            {o.addLabel ?? 'Add'}
          </Button>
        </div>
      ) : null}
      {o.hint ? <p className="field-hint">{o.hint}</p> : null}
    </fieldset>
  );
}
```

A row's problem is plain text, not `role="alert"`, so the step's own problem list stays the one alert in the panel.

Create `apps/desktop/src/renderer/automations/design/pickers.ts`:

```ts
import { useEffect, useState } from 'react';
import type { InputSpec } from '@desk/protocol';
import { scriptFiles, skillChoices, type SkillChoice } from '@desk/ui-core';
import { call } from '../../bridge';

/** The skills a step can name (project, global, built-in), loaded once per project. Empty until loaded or when loading fails. */
export function useSkillChoices(projectId: string): SkillChoice[] {
  const [choices, setChoices] = useState<SkillChoice[]>([]);
  useEffect(() => {
    let live = true;
    Promise.all([call('skills.list', { projectId }), call('skills.list', {}), call('builtins.list', { projectId })])
      .then(([project, global, builtins]) => live && setChoices(skillChoices(project, global, builtins)))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [projectId]);
  return choices;
}

/** The scripts of the skill a script step names, from the copy agents would use. */
export function useSkillScripts(projectId: string, choice: SkillChoice | null): { scripts: string[]; loading: boolean } {
  const [state, setState] = useState<{ key: string; scripts: string[] } | null>(null);
  const key = choice ? `${choice.source}:${choice.name}` : '';
  useEffect(() => {
    if (!choice) return;
    let live = true;
    const load =
      choice.source === 'builtin'
        ? call('builtins.get', { name: choice.name })
        : choice.source === 'project'
          ? call('skills.get', { projectId, name: choice.name })
          : call('skills.get', { name: choice.name });
    load.then((d) => live && setState({ key, scripts: scriptFiles(d.files) })).catch(() => live && setState({ key, scripts: [] }));
    return () => {
      live = false;
    };
    // `key` names the choice; the object itself changes identity on every render of the caller.
  }, [projectId, key]);
  return { scripts: state?.key === key ? state.scripts : [], loading: !!choice && state?.key !== key };
}

/** The project's automation names (Run automation's picker). */
export function useAutomationNames(projectId: string): string[] {
  const [names, setNames] = useState<string[]>([]);
  useEffect(() => {
    let live = true;
    call('automations.list', { projectId })
      .then((list) => live && setNames(list.map((a) => a.name).sort()))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [projectId]);
  return names;
}

/** The inputs of the project's automation called `name`, or null while unknown. */
export function useAutomationInputs(projectId: string, name: string): InputSpec[] | null {
  const [inputs, setInputs] = useState<{ name: string; inputs: InputSpec[] } | null>(null);
  useEffect(() => {
    if (!name) return;
    let live = true;
    call('automations.list', { projectId })
      .then((list) => {
        const a = list.find((x) => x.name === name);
        return a ? call('automations.get', { id: a.id }).then((d) => d.definition.inputs) : [];
      })
      .then((list) => live && setInputs({ name, inputs: list }))
      .catch(() => live && setInputs({ name, inputs: [] }));
    return () => {
      live = false;
    };
  }, [projectId, name]);
  return name && inputs?.name === name ? inputs.inputs : null;
}
```

- [ ] **Step 5: Write the inspectors**

Create `apps/desktop/src/renderer/automations/design/StepKindFields.tsx`:

```tsx
import { MAX_WAIT_MINUTES, ReasoningEffort, type AgentStep, type AskStep, type ScriptStep, type Step, type SubAutomationStep, type TellDeskStep, type WaitStep } from '@desk/protocol';
import { keyProblem, type TemplateSuggestion } from '@desk/ui-core';
import { Button } from '../../components/Button';
import { Field } from '../../components/Field';
import { ListEditor } from './ListEditor';
import { useAutomationInputs, useAutomationNames, useSkillChoices, useSkillScripts } from './pickers';
import { TemplateField } from './TemplateField';

type Patch = (p: Partial<Step>) => void;
type Ctx = { projectId: string; selfName: string; sources: Array<{ id: string; label: string }>; suggest: TemplateSuggestion[]; patch: Patch };

const optNumber = (v: string): number | undefined => (v === '' ? undefined : Number(v));

/** The fields of one step kind (spec §4). */
export function StepKindFields(o: Ctx & { step: Step }) {
  switch (o.step.kind) {
    case 'script':
      return <ScriptFields {...o} step={o.step} />;
    case 'agent':
      return <AgentFields {...o} step={o.step} />;
    case 'ask':
      return <AskFields {...o} step={o.step} />;
    case 'wait':
      return <WaitFields {...o} step={o.step} />;
    case 'automation':
      return <AutomationFields {...o} step={o.step} />;
    case 'tell_desk':
      return <TellFields {...o} step={o.step} />;
  }
}

function ScriptFields({ projectId, step, suggest, patch }: Ctx & { step: ScriptStep }) {
  const choices = useSkillChoices(projectId);
  const choice = choices.find((c) => c.name === step.skill) ?? null;
  const { scripts } = useSkillScripts(projectId, choice);
  return (
    <>
      <Field id="step-skill" label="Skill" hint="The installed skill whose script runs. Threads draft skills; Desk installs them.">
        <select id="step-skill" className="select" value={step.skill} onChange={(e) => patch({ skill: e.target.value, script: '' })}>
          <option value="">Choose a skill…</option>
          {choices.map((c) => (
            <option key={c.name} value={c.name}>
              {c.source === 'builtin' ? `${c.name} (built-in)` : c.name}
            </option>
          ))}
          {step.skill && !choice ? <option value={step.skill}>{`${step.skill} (not installed)`}</option> : null}
        </select>
      </Field>
      <Field id="step-script" label="Script">
        <select id="step-script" className="select" value={step.script} disabled={!step.skill} onChange={(e) => patch({ script: e.target.value })}>
          <option value="">Choose a script…</option>
          {scripts.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
          {step.script && !scripts.includes(step.script) ? <option value={step.script}>{step.script}</option> : null}
        </select>
      </Field>
      <ListEditor
        id="step-args"
        label="Arguments"
        values={step.args}
        onChange={(args) => patch({ args })}
        suggestions={suggest}
        addLabel="Add argument"
        max={50}
        hint="Each argument goes to the script as it is, never through a shell. {{…}} fills in inputs and earlier steps' results."
      />
      <TemplateField id="step-stdin" label="Standard input (optional)" value={step.stdin ?? ''} onChange={(v) => patch({ stdin: v || undefined })} suggestions={suggest} multiline rows={3} />
      <label className="auto-check">
        <input type="checkbox" checked={step.idempotent} onChange={(e) => patch({ idempotent: e.target.checked })} /> Safe to run again after a crash
      </label>
    </>
  );
}

function AgentFields({ projectId, step, sources, suggest, patch }: Ctx & { step: AgentStep }) {
  const choices = useSkillChoices(projectId);
  const names = [...new Set([...choices.map((c) => c.name), ...step.skills])];
  const toggleSkill = (name: string, on: boolean) => patch({ skills: on ? [...step.skills, name] : step.skills.filter((s) => s !== name) });
  const setKey = (i: number, k: Partial<{ key: string; description: string }>) => patch({ output_keys: step.output_keys.map((x, j) => (j === i ? { ...x, ...k } : x)) });
  return (
    <>
      <TemplateField id="step-brief" label="Brief" value={step.brief} onChange={(v) => patch({ brief: v })} suggestions={suggest} multiline rows={6} hint="What the agent should do. It works in the step's folder and can read earlier steps' folders." />
      <fieldset className="field">
        <legend>Skills</legend>
        {names.length ? (
          names.map((n) => (
            <label key={n} className="auto-check">
              <input type="checkbox" checked={step.skills.includes(n)} disabled={!step.skills.includes(n) && step.skills.length >= 12} onChange={(e) => toggleSkill(n, e.target.checked)} /> {n}
            </label>
          ))
        ) : (
          <p className="muted small">No skills installed yet.</p>
        )}
      </fieldset>
      <fieldset className="field auto-rows">
        <legend>Outputs</legend>
        {step.output_keys.map((k, i) => (
          <div key={i}>
            <div className="auto-row">
              <input aria-label={`Output ${i + 1} key`} className="input mono" value={k.key} placeholder="key" onChange={(e) => setKey(i, { key: e.target.value })} />
              <input aria-label={`Output ${i + 1} meaning`} className="input" value={k.description} placeholder="what it holds" onChange={(e) => setKey(i, { description: e.target.value })} />
              <Button size="sm" variant="ghost" aria-label={`Remove output ${i + 1}`} onClick={() => patch({ output_keys: step.output_keys.filter((_, j) => j !== i) })}>
                ✕
              </Button>
            </div>
            {k.key && keyProblem(k.key) ? <p className="field-error auto-row-error">{keyProblem(k.key)}</p> : null}
          </div>
        ))}
        {step.output_keys.length < 20 ? (
          <div>
            <Button size="sm" onClick={() => patch({ output_keys: [...step.output_keys, { key: '', description: '' }] })}>
              Add output
            </Button>
          </div>
        ) : null}
        <p className="field-hint">What later steps can use as {'{{steps.<id>.outputs.<key>}}'}. The agent sets them when it completes.</p>
      </fieldset>
      <Field id="step-model" label="Model (optional)" hint="Empty uses the project's default.">
        <input id="step-model" className="input mono" value={step.model ?? ''} onChange={(e) => patch({ model: e.target.value || undefined })} />
      </Field>
      <Field id="step-effort" label="Reasoning effort">
        <select id="step-effort" className="select" value={step.reasoning_effort ?? ''} onChange={(e) => patch({ reasoning_effort: (e.target.value || undefined) as AgentStep['reasoning_effort'] })}>
          <option value="">Default</option>
          {ReasoningEffort.options.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </Field>
      <Field id="step-git" label="Work in a git worktree of" hint="Its branch is desk/auto-<name>-<run>. Desk never merges it.">
        <select id="step-git" className="select" value={step.git_source_id ?? ''} onChange={(e) => patch({ git_source_id: e.target.value || undefined })}>
          <option value="">No worktree (its step folder)</option>
          {sources.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      </Field>
      <Field id="step-max" label="Most tool calls (optional)">
        <input id="step-max" className="input" type="number" min={1} max={400} value={step.max_steps ?? ''} onChange={(e) => patch({ max_steps: optNumber(e.target.value) })} />
      </Field>
    </>
  );
}

function AskFields({ step, suggest, patch }: Ctx & { step: AskStep }) {
  return (
    <>
      <TemplateField id="step-question" label="Question" value={step.question} onChange={(v) => patch({ question: v })} suggestions={suggest} multiline rows={3} />
      <ListEditor id="step-show" label="Files to show" values={step.show} onChange={(show) => patch({ show })} placeholder="digest.md" addLabel="Add file" max={20} hint="Globs of files in earlier steps' folders, shown with the question." />
      <div className="auto-inline">
        <Field id="step-approve" label="Approve button">
          <input id="step-approve" className="input" value={step.approve_label ?? ''} placeholder="Approve" onChange={(e) => patch({ approve_label: e.target.value || undefined })} />
        </Field>
        <Field id="step-reject" label="Reject button">
          <input id="step-reject" className="input" value={step.reject_label ?? ''} placeholder="Reject" onChange={(e) => patch({ reject_label: e.target.value || undefined })} />
        </Field>
      </div>
      <Field id="step-expires" label="Expires after (hours, optional)" hint="Rejecting, or letting it expire, takes the route rejected.">
        <input id="step-expires" className="input" type="number" min={1} max={720} value={step.expires_after_hours ?? ''} onChange={(e) => patch({ expires_after_hours: optNumber(e.target.value) })} />
      </Field>
    </>
  );
}

function WaitFields({ step, patch }: Ctx & { step: WaitStep }) {
  const byTime = step.until !== undefined;
  return (
    <>
      <fieldset className="field">
        <legend>Wait</legend>
        <label className="auto-check">
          <input type="radio" name="wait-mode" checked={!byTime} onChange={() => patch({ minutes: 60, until: undefined })} /> For a number of minutes
        </label>
        <label className="auto-check">
          <input type="radio" name="wait-mode" checked={byTime} onChange={() => patch({ until: '08:00', minutes: undefined })} /> Until a time of day
        </label>
      </fieldset>
      {byTime ? (
        <Field id="step-until" label="Until" hint="In the automation's timezone: its first schedule's, else this computer's.">
          <input id="step-until" className="input" type="time" value={step.until ?? ''} onChange={(e) => patch({ until: e.target.value })} />
        </Field>
      ) : (
        <Field id="step-minutes" label="Minutes" hint="At most a week.">
          <input id="step-minutes" className="input" type="number" min={1} max={MAX_WAIT_MINUTES} value={step.minutes ?? ''} onChange={(e) => patch({ minutes: optNumber(e.target.value) })} />
        </Field>
      )}
    </>
  );
}

function AutomationFields({ projectId, selfName, step, suggest, patch }: Ctx & { step: SubAutomationStep }) {
  const names = useAutomationNames(projectId).filter((n) => n !== selfName);
  const inputs = useAutomationInputs(projectId, step.automation);
  const setInput = (key: string, v: string) => {
    const rest = Object.fromEntries(Object.entries(step.inputs).filter(([k]) => k !== key));
    patch({ inputs: v ? { ...rest, [key]: v } : rest });
  };
  return (
    <>
      <Field id="step-automation" label="Automation to run" hint="Its result is its output step's outputs and folder.">
        <select id="step-automation" className="select" value={step.automation} onChange={(e) => patch({ automation: e.target.value, inputs: {} })}>
          <option value="">Choose an automation…</option>
          {names.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
          {step.automation && !names.includes(step.automation) ? <option value={step.automation}>{step.automation}</option> : null}
        </select>
      </Field>
      {inputs?.map((i) => (
        <TemplateField key={i.key} id={`step-input-${i.key}`} label={i.required ? i.label : `${i.label} (optional)`} value={step.inputs[i.key] ?? ''} onChange={(v) => setInput(i.key, v)} suggestions={suggest} {...(i.description ? { hint: i.description } : {})} />
      ))}
    </>
  );
}

function TellFields({ step, suggest, patch }: Ctx & { step: TellDeskStep }) {
  return (
    <>
      <TemplateField id="step-text" label="Message to Desk" value={step.text} onChange={(v) => patch({ text: v })} suggestions={suggest} multiline rows={4} hint="Desk reads it at its next turn, as a message from this automation." />
      <ListEditor id="step-attach" label="Files to attach" values={step.attach} onChange={(attach) => patch({ attach })} placeholder="report.md" addLabel="Add file" max={20} hint="Globs of files in earlier steps' folders; Desk gets their paths." />
    </>
  );
}
```

Create `apps/desktop/src/renderer/automations/design/StepInspector.tsx`:

```tsx
import { useEffect, useState } from 'react';
import type { OnError, Step } from '@desk/protocol';
import { incomingCount, patchStep, renameStep, routeProblem, STEP_KIND_LABEL, templateSuggestions, type AutomationDoc } from '@desk/ui-core';
import { Button } from '../../components/Button';
import { Field } from '../../components/Field';
import { ListEditor } from './ListEditor';
import { StepKindFields } from './StepKindFields';

const retries = (e: OnError): number | null => (typeof e === 'object' ? e.retry : null);

/** The selected step's fields (spec §8.2): common ones, then its kind's. */
export function StepInspector(o: {
  projectId: string;
  doc: AutomationDoc;
  stepId: string;
  /** This automation's name: Run automation cannot pick it. */
  selfName: string;
  /** Git sources, for an agent's worktree. */
  sources: Array<{ id: string; label: string }>;
  errors: string[];
  onChange(doc: AutomationDoc): void;
  /** The step's id changed: the selection should follow it. */
  onRenamed(id: string): void;
  onDelete(): void;
}) {
  const step = o.doc.def.steps.find((s) => s.id === o.stepId);
  const [idDraft, setIdDraft] = useState(o.stepId);
  const [idError, setIdError] = useState<string | null>(null);
  useEffect(() => {
    setIdDraft(o.stepId);
    setIdError(null);
  }, [o.stepId]);
  if (!step) return null;
  const patch = (p: Partial<Step>) => o.onChange(patchStep(o.doc, step.id, p));
  const commitId = () => {
    const r = renameStep(o.doc, step.id, idDraft.trim());
    if ('error' in r) {
      setIdError(r.error);
      return;
    }
    setIdError(null);
    if (idDraft.trim() !== step.id) {
      o.onChange(r.doc);
      o.onRenamed(idDraft.trim());
    }
  };
  const mode = typeof step.on_error === 'object' ? 'retry' : step.on_error;
  const chooses = step.kind === 'script' || step.kind === 'agent';
  return (
    <aside className="auto-inspector" aria-label="Step">
      <p className="eyebrow">{`${STEP_KIND_LABEL[step.kind]} step`}</p>
      {o.errors.length || idError ? (
        <ul className="auto-issues" role="alert">
          {idError ? <li>{idError}</li> : null}
          {o.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}
      <Field id="step-title" label="Title">
        <input id="step-title" className="input" value={step.title} onChange={(e) => patch({ title: e.target.value })} />
      </Field>
      <Field id="step-id" label="Id" hint="Templates name this step by it; renaming updates them.">
        <input
          id="step-id"
          className="input mono"
          value={idDraft}
          onChange={(e) => setIdDraft(e.target.value)}
          onBlur={commitId}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitId();
          }}
        />
      </Field>
      <StepKindFields projectId={o.projectId} selfName={o.selfName} sources={o.sources} step={step} suggest={templateSuggestions(o.doc.def, step.id)} patch={patch} />
      {incomingCount(o.doc.def, step.id) >= 2 ? (
        <Field id="step-join" label="With several steps before it">
          <select id="step-join" className="select" value={step.join} onChange={(e) => patch({ join: e.target.value as Step['join'] })}>
            <option value="all">Wait for all of them, run if one led here</option>
            <option value="any">Run on the first that leads here</option>
          </select>
        </Field>
      ) : null}
      <div className="auto-inline">
        <Field id="step-on-error" label="If it fails">
          <select id="step-on-error" className="select" value={mode} onChange={(e) => patch({ on_error: e.target.value === 'retry' ? { retry: 1 } : (e.target.value as 'stop' | 'continue') })}>
            <option value="stop">Stop the run</option>
            <option value="continue">Continue on route error</option>
            <option value="retry">Try again</option>
          </select>
        </Field>
        {mode === 'retry' ? (
          <Field id="step-retries" label="Attempts after the first">
            <input id="step-retries" className="input" type="number" min={1} max={3} value={retries(step.on_error) ?? 1} onChange={(e) => patch({ on_error: { retry: Math.min(3, Math.max(1, Number(e.target.value) || 1)) } })} />
          </Field>
        ) : null}
      </div>
      {chooses ? (
        <>
          <Field id="step-timeout" label="Time limit in minutes (optional)" hint={step.kind === 'script' ? 'Default 10.' : 'Default 60.'}>
            <input id="step-timeout" className="input" type="number" min={1} max={1440} value={step.timeout_min ?? ''} onChange={(e) => patch({ timeout_min: e.target.value === '' ? undefined : Number(e.target.value) })} />
          </Field>
          <ListEditor id="step-routes" label="Routes" values={step.routes} onChange={(routes) => patch({ routes })} check={routeProblem} addLabel="Add route" max={10} hint="Named outcomes it can choose. A route no edge takes ends that branch." />
          <ListEditor id="step-publish" label="Publish to the Library" values={step.publish} onChange={(publish) => patch({ publish })} placeholder="digest.md" addLabel="Add file" max={20} hint="Globs in its folder, copied to the Library after it succeeds." />
        </>
      ) : null}
      <div>
        <Button variant="danger" size="sm" onClick={o.onDelete}>
          Delete step
        </Button>
      </div>
    </aside>
  );
}
```

Create `apps/desktop/src/renderer/automations/design/EdgeInspector.tsx`:

```tsx
import { conditionSuggestions, edgeRouteOptions, patchEdge, type AutomationDoc } from '@desk/ui-core';
import { Button } from '../../components/Button';
import { Field } from '../../components/Field';

/** The selected edge (spec §3.2): what its source must do for it to fire, and an optional condition. */
export function EdgeInspector(o: { doc: AutomationDoc; index: number; errors: string[]; onChange(doc: AutomationDoc): void; onDelete(): void }) {
  const edge = o.doc.def.edges[o.index];
  if (!edge) return null;
  const title = (id: string) => o.doc.def.steps.find((s) => s.id === id)?.title ?? id;
  const set = (p: { route?: string | undefined; when?: string | undefined }) => o.onChange(patchEdge(o.doc, o.index, p));
  const paths = conditionSuggestions(o.doc.def, edge.from).filter((s) => !s.open);
  const append = (path: string) => set({ when: edge.when ? `${edge.when.trimEnd()} ${path}` : path });
  return (
    <aside className="auto-inspector" aria-label="Edge">
      <p className="eyebrow">Edge</p>
      <h2>{`${title(edge.from)} → ${title(edge.to)}`}</h2>
      {o.errors.length ? (
        <ul className="auto-issues" role="alert">
          {o.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}
      <Field id="edge-route" label="Fires">
        <select id="edge-route" className="select" value={edge.route ?? ''} onChange={(e) => set({ route: e.target.value || undefined })}>
          {edgeRouteOptions(o.doc.def, edge.from, edge.route).map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
      </Field>
      <Field id="edge-when" label="Only if" hint="Optional, e.g. steps.fetch.outputs.count > 0. Use == != < <= > >= contains exists(…) and or not.">
        <input id="edge-when" className="input mono" value={edge.when ?? ''} onChange={(e) => set({ when: e.target.value.trim() ? e.target.value : undefined })} />
      </Field>
      {paths.length ? (
        <div className="auto-paths" aria-label="Paths it can use">
          {paths.map((p) => (
            <button key={p.path} type="button" className="auto-path" title={p.label} onClick={() => append(p.path)}>
              {p.path}
            </button>
          ))}
        </div>
      ) : null}
      <div>
        <Button variant="danger" size="sm" onClick={o.onDelete}>
          Delete edge
        </Button>
      </div>
    </aside>
  );
}
```

Append to `packages/ui-styles/src/automations.css`:

```css
/* The inspector (mockup 2, right) */
.auto-inspector {
  width: 340px;
  flex: none;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px 24px;
  border-left: 1px solid var(--rule-soft);
  background: var(--paper);
  overflow: auto;
}
.auto-inspector h2 {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 17px;
  font-weight: 500;
  color: var(--ink);
}
.auto-inspector fieldset {
  margin: 0;
  padding: 0;
  border: 0;
}
.auto-inspector legend {
  margin-bottom: 4px;
  font-size: 12px;
  color: var(--text);
}
.auto-issues {
  margin: 0;
  padding: 8px 10px 8px 26px;
  border-radius: 8px;
  background: var(--accent-tint);
  color: var(--accent);
  font-size: 12px;
}
.auto-rows {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.auto-row {
  display: flex;
  gap: 6px;
  align-items: flex-start;
}
.auto-row > .input,
.auto-row > .field {
  flex: 1;
  min-width: 0;
  margin: 0;
}
.auto-row-error {
  margin: 2px 0 0;
}
.auto-inline {
  display: flex;
  gap: 8px;
}
.auto-inline > .field {
  flex: 1;
  min-width: 0;
}
.auto-paths {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.auto-path {
  padding: 1px 6px;
  border: 1px solid var(--rule);
  border-radius: 4px;
  background: var(--card);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 11px;
  cursor: pointer;
}
.auto-path:hover {
  border-color: var(--run);
  color: var(--run-text);
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pnpm vitest run packages/ui-core apps/desktop/src/renderer/automations/design packages/ui-styles`
Expected: PASS.

`Field` renders `<label htmlFor={id}>`, so each control's `id` must equal the `id` passed to its `Field`: the tests find controls by label.

Run: `pnpm typecheck`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
pnpm test
git add packages/ui-core/src/automation-fields.ts packages/ui-core/src/automation-fields.test.ts packages/ui-core/src/index.ts apps/desktop/src/renderer/automations/design/ListEditor.tsx apps/desktop/src/renderer/automations/design/pickers.ts apps/desktop/src/renderer/automations/design/StepKindFields.tsx apps/desktop/src/renderer/automations/design/StepInspector.tsx apps/desktop/src/renderer/automations/design/EdgeInspector.tsx apps/desktop/src/renderer/automations/design/inspectors.test.tsx packages/ui-styles/src/automations.css
git commit -m "feat(desktop): step and edge inspectors, with skill, script and automation pickers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: The Start and automation inspectors

The Start pill's inspector edits schedules and inputs (spec §8.2):
- Each schedule is a preset (daily, weekdays, weekly or monthly at a time) or custom cron.
- Each schedule has a timezone and catch-up.
- Each schedule previews its next 3 times, taken from the validate endpoint's `next_times`.

With nothing selected, the inspector shows the automation's settings.

**Files:**
- Create: `packages/ui-core/src/automation-start.ts`, `packages/ui-core/src/automation-start.test.ts`
- Modify: `packages/ui-core/src/index.ts`
- Create: `apps/desktop/src/renderer/automations/design/StartInspector.tsx`, `SettingsInspector.tsx`
- Modify: `packages/ui-styles/src/automations.css`
- Test: `apps/desktop/src/renderer/automations/design/start.test.tsx`

**Interfaces:**
- Consumes: `presetOf`, `cronOf`, `systemTimezone`, `dayTime`, `SchedulePreset` (Task 3); `setTriggers`, `setInputs`, `renameInput`, `setMeta`, `AutomationDoc` (Task 5); `ListEditor` (Task 10).
- Produces:
  - `automation-start.ts`:
    - `INPUT_TYPE_LABEL`, `AFTER_RUN_LABEL`;
    - `newSchedule(timezone?)`, `newInput(inputs)`, `switchPreset(current, kind)`, `timezones()`;
    - `patchInput(doc, index, patch)`, `retypeInput(doc, index, type)`, `inputValueOf(type, raw)`.
  - `StartInspector({ doc, errors, nextTimes, onChange })`.
  - `SettingsInspector({ doc, issues, warnings, onChange })`.
  - CSS: `.auto-card`.

- [ ] **Step 1: Write the failing tests**

Create `packages/ui-core/src/automation-start.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { digestDef } from './testing/automations';
import { START_ID } from './automation-graph';
import { inputValueOf, newInput, newSchedule, patchInput, retypeInput, switchPreset } from './automation-start';

const doc = () => ({ def: digestDef(), layout: { [START_ID]: { x: 0, y: 0 } } });

describe('start helpers', () => {
  it('makes a new schedule and a new input with a free key', () => {
    expect(newSchedule('Europe/Paris')).toEqual({ kind: 'schedule', cron: '0 8 * * 1-5', timezone: 'Europe/Paris', catch_up: 'once' });
    expect(newInput(digestDef().inputs)).toEqual({ key: 'input', label: 'New input', type: 'text', required: false });
    expect(newInput([...digestDef().inputs, { key: 'input', label: 'x', type: 'text', required: false }]).key).toBe('input_2');
  });

  it('switches presets, keeping the time and the day where it can', () => {
    expect(switchPreset({ kind: 'daily', time: '07:30' }, 'weekly')).toEqual({ kind: 'weekly', day: 1, time: '07:30' });
    expect(switchPreset({ kind: 'weekly', day: 5, time: '07:30' }, 'monthly')).toEqual({ kind: 'monthly', dom: 1, time: '07:30' });
    expect(switchPreset({ kind: 'weekly', day: 5, time: '07:30' }, 'custom')).toEqual({ kind: 'custom', cron: '30 7 * * 5' });
    expect(switchPreset({ kind: 'custom', cron: '*/5 * * * *' }, 'daily')).toEqual({ kind: 'daily', time: '08:00' });
  });

  it('patches and retypes inputs, and reads typed values', () => {
    const d = patchInput(doc(), 0, { label: 'Subject', description: undefined });
    expect(d.def.inputs[0]).toEqual({ key: 'topic', label: 'Subject', type: 'text', required: true });
    const c = retypeInput(patchInput(doc(), 0, { default: 'robots' }), 0, 'choice');
    expect(c.def.inputs[0]).toEqual({ key: 'topic', label: 'Topic', type: 'choice', required: true, options: ['First option'] });
    expect(retypeInput(c, 0, 'text').def.inputs[0]).toEqual({ key: 'topic', label: 'Topic', type: 'text', required: true });
    expect(inputValueOf('number', '3')).toBe(3);
    expect(inputValueOf('number', 'x')).toBeUndefined();
    expect(inputValueOf('text', '  ')).toBeUndefined();
    expect(inputValueOf('boolean', true)).toBe(true);
  });
});
```

Create `apps/desktop/src/renderer/automations/design/start.test.tsx`:

```tsx
// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { START_ID, type AutomationDoc } from '@desk/ui-core';
import { digestDef } from '@desk/ui-core/testing';
import { SettingsInspector } from './SettingsInspector';
import { StartInspector } from './StartInspector';

afterEach(cleanup);

const initial = (): AutomationDoc => ({ def: digestDef(), layout: { [START_ID]: { x: 0, y: 0 } } });

function Start(p: { onDoc(d: AutomationDoc): void; next?: Record<string, string[]> }) {
  const [d, setD] = useState(initial());
  return (
    <StartInspector
      doc={d}
      errors={['Schedule 1: cron: fires every minute']}
      nextTimes={p.next ?? {}}
      onChange={(n) => {
        setD(n);
        p.onDoc(n);
      }}
    />
  );
}

describe('StartInspector', () => {
  it('edits a schedule through its preset, time, day and timezone, and previews the next times', () => {
    const onDoc = vi.fn();
    render(<Start onDoc={onDoc} next={{ '0': ['2026-10-05T06:00:00.000Z', '2026-10-12T06:00:00.000Z', '2026-10-19T06:00:00.000Z', '2026-10-26T06:00:00.000Z'] }} />);
    expect(screen.getByRole('alert').textContent).toContain('fires every minute');
    const s1 = screen.getByRole('group', { name: 'Schedule 1' });
    expect((within(s1).getByLabelText('Repeats') as HTMLSelectElement).value).toBe('weekly');
    expect((within(s1).getByLabelText('On') as HTMLSelectElement).value).toBe('1');
    expect(within(s1).getByText(/^Next: /).textContent!.split(' · ')).toHaveLength(3);
    fireEvent.change(within(s1).getByLabelText('At'), { target: { value: '09:15' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].cron).toBe('15 9 * * 1');
    fireEvent.change(within(s1).getByLabelText('Repeats'), { target: { value: 'monthly' } });
    fireEvent.change(within(s1).getByLabelText('Day of the month'), { target: { value: '15' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].cron).toBe('15 9 15 * *');
    fireEvent.change(within(s1).getByLabelText('Repeats'), { target: { value: 'custom' } });
    fireEvent.change(within(s1).getByLabelText('Cron'), { target: { value: '*/30 9-17 * * 1-5' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].cron).toBe('*/30 9-17 * * 1-5');
    fireEvent.change(within(s1).getByLabelText('Timezone'), { target: { value: 'UTC' } });
    fireEvent.change(within(s1).getByLabelText('If the computer was asleep'), { target: { value: 'skip' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0]).toMatchObject({ timezone: 'UTC', catch_up: 'skip' });
    fireEvent.change(within(s1).getByLabelText('Topic'), { target: { value: 'robots' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].inputs).toEqual({ topic: 'robots' });
    fireEvent.click(screen.getByRole('button', { name: 'Add schedule' }));
    expect(onDoc.mock.lastCall![0].def.triggers).toHaveLength(2);
    fireEvent.click(within(screen.getByRole('group', { name: 'Schedule 2' })).getByRole('button', { name: 'Remove schedule' }));
    expect(onDoc.mock.lastCall![0].def.triggers).toHaveLength(1);
  });

  it('edits inputs: key renames in templates, type, options, default', () => {
    const onDoc = vi.fn();
    render(<Start onDoc={onDoc} />);
    const i1 = screen.getByRole('group', { name: 'Input 1' });
    const key = within(i1).getByLabelText('Key');
    fireEvent.change(key, { target: { value: 'subject' } });
    fireEvent.blur(key);
    expect(onDoc.mock.lastCall![0].def.steps[0].args).toEqual(['{{inputs.subject}}']);
    fireEvent.change(within(i1).getByLabelText('Type'), { target: { value: 'choice' } });
    fireEvent.change(within(i1).getByLabelText('Options 1'), { target: { value: 'robots' } });
    fireEvent.change(within(i1).getByLabelText('Default'), { target: { value: 'robots' } });
    expect(onDoc.mock.lastCall![0].def.inputs[0]).toMatchObject({ key: 'subject', type: 'choice', options: ['robots'], default: 'robots' });
    fireEvent.click(screen.getByRole('button', { name: 'Add input' }));
    expect(onDoc.mock.lastCall![0].def.inputs.map((i: { key: string }) => i.key)).toEqual(['subject', 'input']);
  });
});

describe('SettingsInspector', () => {
  it('edits the title, after-run, result step and limits, and lists problems and warnings', () => {
    const onDoc = vi.fn();
    function S() {
      const [d, setD] = useState(initial());
      return (
        <SettingsInspector
          doc={d}
          issues={['Add at least one step']}
          warnings={[{ path: 'steps[2]', message: 'Publish? has no edges' }]}
          onChange={(n) => {
            setD(n);
            onDoc(n);
          }}
        />
      );
    }
    render(<S />);
    expect(screen.getByRole('alert').textContent).toContain('Add at least one step');
    expect(screen.getByText('Publish? has no edges')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Digest' } });
    fireEvent.change(screen.getByLabelText('After each run'), { target: { value: 'desk_review' } });
    fireEvent.change(screen.getByLabelText('Its result, when another automation runs it'), { target: { value: 'sum' } });
    fireEvent.change(screen.getByLabelText('Agents at once'), { target: { value: '3' } });
    expect(onDoc.mock.lastCall![0].def).toMatchObject({ title: 'Digest', after_run: 'desk_review', output_step: 'sum', limits: { max_parallel_agents: 3 } });
    fireEvent.change(screen.getByLabelText('Its result, when another automation runs it'), { target: { value: '' } });
    expect('output_step' in onDoc.mock.lastCall![0].def).toBe(false);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm vitest run packages/ui-core/src/automation-start.test.ts apps/desktop/src/renderer/automations/design/start.test.tsx`
Expected: FAIL: the modules do not exist.

- [ ] **Step 3: Write `automation-start.ts`**

Create `packages/ui-core/src/automation-start.ts`:

```ts
import type { AfterRun, InputSpec, InputType, InputValue, ScheduleTrigger } from '@desk/protocol';
import { setInputs, type AutomationDoc } from './automation-draft';
import { cronOf, systemTimezone, type SchedulePreset } from './automation-schedules';

export const INPUT_TYPE_LABEL: Record<InputType, string> = { text: 'Text', long_text: 'Long text', url: 'URL', file: 'File', folder: 'Folder', number: 'Number', boolean: 'Yes / no', choice: 'Choice' };

export const AFTER_RUN_LABEL: Record<AfterRun, string> = { silent: 'Nothing (failures still notify)', notify: 'Notify me with its summary', desk_review: 'Send Desk the run report' };

/** A new schedule: weekdays at 08:00 in this computer's timezone, catching up once. */
export const newSchedule = (timezone = systemTimezone()): ScheduleTrigger => ({ kind: 'schedule', cron: cronOf({ kind: 'weekdays', time: '08:00' }), timezone, catch_up: 'once' });

/** A new text input with a key no other input has: `input`, `input_2`… */
export function newInput(inputs: InputSpec[]): InputSpec {
  const keys = new Set(inputs.map((i) => i.key));
  let key = 'input';
  for (let n = 2; keys.has(key); n++) key = `input_${n}`;
  return { key, label: 'New input', type: 'text', required: false };
}

/** The preset a schedule switches to: it keeps its time, and its weekday or day of month where one applies. */
export function switchPreset(current: SchedulePreset, kind: SchedulePreset['kind']): SchedulePreset {
  const time = current.kind === 'custom' ? '08:00' : current.time;
  switch (kind) {
    case 'daily':
    case 'weekdays':
      return { kind, time };
    case 'weekly':
      return { kind, day: current.kind === 'weekly' ? current.day : 1, time };
    case 'monthly':
      return { kind, dom: current.kind === 'monthly' ? current.dom : 1, time };
    case 'custom':
      return { kind, cron: cronOf(current) };
  }
}

/** IANA timezones this runtime knows, for the timezone field's suggestions (empty where Intl cannot list them). */
export function timezones(): string[] {
  const intl = Intl as { supportedValuesOf?(key: string): string[] };
  return intl.supportedValuesOf ? intl.supportedValuesOf('timeZone') : [];
}

const dropUndefined = <T extends object>(o: T): T => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined)) as T;

/** Merges fields into one input (not its key: `renameInput` changes that everywhere). A field set to undefined is removed. */
export function patchInput(doc: AutomationDoc, index: number, patch: Partial<Omit<InputSpec, 'key'>>): AutomationDoc {
  return setInputs(
    doc,
    doc.def.inputs.map((x, i) => (i === index ? dropUndefined({ ...x, ...patch, key: x.key }) : x)),
  );
}

/** Changes an input's type: its default goes (it may not fit), and a choice gets a first option. */
export function retypeInput(doc: AutomationDoc, index: number, type: InputType): AutomationDoc {
  const x = doc.def.inputs[index];
  if (!x) return doc;
  return patchInput(doc, index, { type, default: undefined, options: type === 'choice' ? (x.options ?? ['First option']) : undefined });
}

/** The value a field holds, typed: a number parsed, a boolean as it is, empty as undefined. */
export function inputValueOf(type: InputType, raw: string | boolean): InputValue | undefined {
  if (typeof raw === 'boolean') return raw;
  if (!raw.trim()) return undefined;
  if (type === 'number') {
    const n = Number(raw);
    return Number.isFinite(n) ? n : undefined;
  }
  return raw;
}
```

In `packages/ui-core/src/index.ts`, add `export * from './automation-start';` among the automation modules (alphabetical: after `./automation-schedules`).

- [ ] **Step 4: Write the inspectors**

Create `apps/desktop/src/renderer/automations/design/StartInspector.tsx`:

```tsx
import { useEffect, useState } from 'react';
import type { InputSpec, InputType, ScheduleTrigger } from '@desk/protocol';
import {
  cronOf,
  dayTime,
  INPUT_TYPE_LABEL,
  inputValueOf,
  newInput,
  newSchedule,
  patchInput,
  presetOf,
  renameInput,
  retypeInput,
  setInputs,
  setTriggers,
  switchPreset,
  timezones,
  type AutomationDoc,
  type SchedulePreset,
} from '@desk/ui-core';
import { Button } from '../../components/Button';
import { Field } from '../../components/Field';
import { useNow } from '../../state/now';
import { ListEditor } from './ListEditor';

const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const ZONES = timezones();

/** The Start pill (spec §8.2): when it runs, and what it asks for. Run now always works. */
export function StartInspector(o: { doc: AutomationDoc; errors: string[]; nextTimes: Record<string, string[]>; onChange(doc: AutomationDoc): void }) {
  const { triggers, inputs } = o.doc.def;
  const setTrigger = (i: number, t: ScheduleTrigger) => o.onChange(setTriggers(o.doc, triggers.map((x, j) => (j === i ? t : x))));
  return (
    <aside className="auto-inspector" aria-label="Start">
      <p className="eyebrow">Start</p>
      {o.errors.length ? (
        <ul className="auto-issues" role="alert">
          {o.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}
      <h2>When it runs</h2>
      <p className="muted small">Run now always works. Schedules run it while it is on; Desk cannot wake a sleeping Mac.</p>
      {triggers.map((t, i) => (
        <ScheduleEditor key={i} index={i} trigger={t} inputs={inputs} next={o.nextTimes[String(i)] ?? []} onChange={(n) => setTrigger(i, n)} onRemove={() => o.onChange(setTriggers(o.doc, triggers.filter((_, j) => j !== i)))} />
      ))}
      {triggers.length < 10 ? (
        <div>
          <Button size="sm" onClick={() => o.onChange(setTriggers(o.doc, [...triggers, newSchedule()]))}>
            Add schedule
          </Button>
        </div>
      ) : null}
      <h2>Inputs</h2>
      <p className="muted small">What Run now and Test ask for. Templates use them as {'{{inputs.<key>}}'}.</p>
      {inputs.map((spec, i) => (
        <InputEditor key={i} doc={o.doc} index={i} spec={spec} onChange={o.onChange} />
      ))}
      {inputs.length < 20 ? (
        <div>
          <Button size="sm" onClick={() => o.onChange(setInputs(o.doc, [...inputs, newInput(inputs)]))}>
            Add input
          </Button>
        </div>
      ) : null}
      <datalist id="auto-timezones">
        {ZONES.map((z) => (
          <option key={z} value={z} />
        ))}
      </datalist>
    </aside>
  );
}

function ScheduleEditor(o: { index: number; trigger: ScheduleTrigger; inputs: InputSpec[]; next: string[]; onChange(t: ScheduleTrigger): void; onRemove(): void }) {
  const now = useNow();
  const t = o.trigger;
  // Choosing Custom keeps the cron, which may still read as a preset: remember the choice, or the menu would snap back.
  const [custom, setCustom] = useState(false);
  const derived = presetOf(t.cron);
  const preset: SchedulePreset = custom || derived.kind === 'custom' ? { kind: 'custom', cron: t.cron } : derived;
  const id = (f: string) => `sched-${o.index}-${f}`;
  const setPreset = (p: SchedulePreset) => o.onChange({ ...t, cron: cronOf(p) });
  const choose = (kind: SchedulePreset['kind']) => {
    setCustom(kind === 'custom');
    if (kind !== 'custom') setPreset(switchPreset(preset, kind));
  };
  const setInput = (key: string, v: ReturnType<typeof inputValueOf>) => {
    const rest = Object.fromEntries(Object.entries(t.inputs ?? {}).filter(([k]) => k !== key));
    const next = v === undefined ? rest : { ...rest, [key]: v };
    const { inputs: _old, ...base } = t;
    o.onChange(Object.keys(next).length ? { ...base, inputs: next } : base);
  };
  return (
    <fieldset className="auto-card" aria-label={`Schedule ${o.index + 1}`}>
      <div className="auto-inline">
        <Field id={id('kind')} label="Repeats">
          <select id={id('kind')} className="select" value={preset.kind} onChange={(e) => choose(e.target.value as SchedulePreset['kind'])}>
            <option value="daily">Every day</option>
            <option value="weekdays">Weekdays</option>
            <option value="weekly">Every week</option>
            <option value="monthly">Every month</option>
            <option value="custom">Custom (cron)</option>
          </select>
        </Field>
        {preset.kind !== 'custom' ? (
          <Field id={id('time')} label="At">
            <input id={id('time')} className="input" type="time" value={preset.time} onChange={(e) => e.target.value && setPreset({ ...preset, time: e.target.value })} />
          </Field>
        ) : null}
      </div>
      {preset.kind === 'weekly' ? (
        <Field id={id('day')} label="On">
          <select id={id('day')} className="select" value={String(preset.day)} onChange={(e) => setPreset({ ...preset, day: Number(e.target.value) })}>
            {DAY_NAMES.map((d, i) => (
              <option key={d} value={String(i)}>
                {d}
              </option>
            ))}
          </select>
        </Field>
      ) : null}
      {preset.kind === 'monthly' ? (
        <Field id={id('dom')} label="Day of the month" hint="Up to the 28th, which every month has. Use custom cron for later days.">
          <input id={id('dom')} className="input" type="number" min={1} max={28} value={preset.dom} onChange={(e) => setPreset({ ...preset, dom: Math.min(28, Math.max(1, Number(e.target.value) || 1)) })} />
        </Field>
      ) : null}
      {preset.kind === 'custom' ? (
        <Field id={id('cron')} label="Cron" hint="minute hour day-of-month month day-of-week, e.g. */30 9-17 * * 1-5. At most every 5 minutes.">
          <input id={id('cron')} className="input mono" value={t.cron} onChange={(e) => o.onChange({ ...t, cron: e.target.value })} />
        </Field>
      ) : null}
      <Field id={id('tz')} label="Timezone">
        <input id={id('tz')} className="input mono" list="auto-timezones" value={t.timezone} onChange={(e) => o.onChange({ ...t, timezone: e.target.value })} />
      </Field>
      <Field id={id('catch')} label="If the computer was asleep">
        <select id={id('catch')} className="select" value={t.catch_up} onChange={(e) => o.onChange({ ...t, catch_up: e.target.value as ScheduleTrigger['catch_up'] })}>
          <option value="once">Run once when it wakes</option>
          <option value="skip">Skip the missed times</option>
        </select>
      </Field>
      {o.inputs.length ? (
        <fieldset className="field">
          <legend>Inputs for this schedule</legend>
          {o.inputs.map((i) =>
            i.type === 'boolean' ? (
              <label key={i.key} className="auto-check">
                <input type="checkbox" checked={t.inputs?.[i.key] === true} onChange={(e) => setInput(i.key, e.target.checked || undefined)} /> {i.label}
              </label>
            ) : (
              <Field key={i.key} id={id(`in-${i.key}`)} label={i.label}>
                <input id={id(`in-${i.key}`)} className="input" type={i.type === 'number' ? 'number' : 'text'} value={String(t.inputs?.[i.key] ?? '')} onChange={(e) => setInput(i.key, inputValueOf(i.type, e.target.value))} />
              </Field>
            ),
          )}
        </fieldset>
      ) : null}
      {o.next.length ? <p className="muted small">{`Next: ${o.next.slice(0, 3).map((ts) => dayTime(ts, now)).join(' · ')}`}</p> : null}
      <div>
        <Button size="sm" variant="ghost" onClick={o.onRemove}>
          Remove schedule
        </Button>
      </div>
    </fieldset>
  );
}

function InputEditor(o: { doc: AutomationDoc; index: number; spec: InputSpec; onChange(doc: AutomationDoc): void }) {
  const { spec, index } = o;
  const [key, setKey] = useState(spec.key);
  const [keyError, setKeyError] = useState<string | null>(null);
  useEffect(() => {
    setKey(spec.key);
    setKeyError(null);
  }, [spec.key]);
  const id = (f: string) => `input-${index}-${f}`;
  const set = (p: Partial<Omit<InputSpec, 'key'>>) => o.onChange(patchInput(o.doc, index, p));
  const commitKey = () => {
    const r = renameInput(o.doc, spec.key, key.trim());
    if ('error' in r) setKeyError(r.error);
    else {
      setKeyError(null);
      if (key.trim() !== spec.key) o.onChange(r.doc);
    }
  };
  return (
    <fieldset className="auto-card" aria-label={`Input ${index + 1}`}>
      <div className="auto-inline">
        <Field id={id('label')} label="Label">
          <input id={id('label')} className="input" value={spec.label} onChange={(e) => set({ label: e.target.value })} />
        </Field>
        <Field id={id('key')} label="Key" error={keyError}>
          <input id={id('key')} className="input mono" value={key} onChange={(e) => setKey(e.target.value)} onBlur={commitKey} onKeyDown={(e) => e.key === 'Enter' && commitKey()} />
        </Field>
      </div>
      <div className="auto-inline">
        <Field id={id('type')} label="Type">
          <select id={id('type')} className="select" value={spec.type} onChange={(e) => o.onChange(retypeInput(o.doc, index, e.target.value as InputType))}>
            {Object.entries(INPUT_TYPE_LABEL).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </Field>
        <label className="auto-check">
          <input type="checkbox" checked={spec.required} onChange={(e) => set({ required: e.target.checked })} /> Required
        </label>
      </div>
      {spec.type === 'choice' ? <ListEditor id={id('options')} label="Options" values={spec.options ?? []} onChange={(options) => set({ options: options.length ? options : undefined })} addLabel="Add option" max={50} /> : null}
      {spec.type === 'boolean' ? (
        <label className="auto-check">
          <input type="checkbox" checked={spec.default === true} onChange={(e) => set({ default: e.target.checked || undefined })} /> Ticked by default
        </label>
      ) : spec.type === 'choice' ? (
        <Field id={id('default')} label="Default">
          <select id={id('default')} className="select" value={String(spec.default ?? '')} onChange={(e) => set({ default: e.target.value || undefined })}>
            <option value="">None</option>
            {(spec.options ?? []).filter(Boolean).map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </Field>
      ) : spec.type === 'file' || spec.type === 'folder' ? null : (
        <Field id={id('default')} label="Default">
          <input id={id('default')} className="input" type={spec.type === 'number' ? 'number' : 'text'} value={String(spec.default ?? '')} onChange={(e) => set({ default: inputValueOf(spec.type, e.target.value) })} />
        </Field>
      )}
      <Field id={id('description')} label="Description (optional)">
        <input id={id('description')} className="input" value={spec.description ?? ''} onChange={(e) => set({ description: e.target.value || undefined })} />
      </Field>
      <div>
        <Button size="sm" variant="ghost" onClick={() => o.onChange(setInputs(o.doc, o.doc.def.inputs.filter((_, j) => j !== index)))}>
          Remove input
        </Button>
      </div>
    </fieldset>
  );
}
```

`const { inputs: _old, ...base } = t;` drops the field; the repo runs no linter that flags the unused name.

Create `apps/desktop/src/renderer/automations/design/SettingsInspector.tsx`:

```tsx
import type { AfterRun, AutomationLimits, ValidationIssue } from '@desk/protocol';
import { AFTER_RUN_LABEL, setMeta, type AutomationDoc } from '@desk/ui-core';
import { Field } from '../../components/Field';

const clampInt = (v: string, min: number, max: number) => Math.min(max, Math.max(min, Math.round(Number(v)) || min));

/** With nothing selected: the automation's own settings (spec §2.1, §4.7), its problems and its warnings. */
export function SettingsInspector(o: { doc: AutomationDoc; issues: string[]; warnings: ValidationIssue[]; onChange(doc: AutomationDoc): void }) {
  const def = o.doc.def;
  const setLimit = (k: keyof AutomationLimits, v: number) => o.onChange(setMeta(o.doc, { limits: { ...def.limits, [k]: v } }));
  return (
    <aside className="auto-inspector" aria-label="Automation">
      <p className="eyebrow">Automation</p>
      {o.issues.length ? (
        <ul className="auto-issues" role="alert">
          {o.issues.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}
      <Field id="auto-title" label="Title">
        <input id="auto-title" className="input" value={def.title} onChange={(e) => o.onChange(setMeta(o.doc, { title: e.target.value }))} />
      </Field>
      <Field id="auto-description" label="Description">
        <textarea id="auto-description" className="textarea" rows={3} value={def.description} onChange={(e) => o.onChange(setMeta(o.doc, { description: e.target.value }))} />
      </Field>
      <Field id="auto-after" label="After each run">
        <select id="auto-after" className="select" value={def.after_run} onChange={(e) => o.onChange(setMeta(o.doc, { after_run: e.target.value as AfterRun }))}>
          {Object.entries(AFTER_RUN_LABEL).map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      </Field>
      <Field id="auto-output" label="Its result, when another automation runs it" hint="That step's outputs and folder; its summary is the run's summary.">
        <select id="auto-output" className="select" value={def.output_step ?? ''} onChange={(e) => o.onChange(setMeta(o.doc, { output_step: e.target.value || undefined }))}>
          <option value="">The last step that succeeded</option>
          {def.steps.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title}
            </option>
          ))}
        </select>
      </Field>
      <div className="auto-inline">
        <Field id="auto-deadline" label="Deadline (hours)">
          <input id="auto-deadline" className="input" type="number" min={1} max={168} value={def.limits.run_deadline_hours} onChange={(e) => setLimit('run_deadline_hours', clampInt(e.target.value, 1, 168))} />
        </Field>
        <Field id="auto-agents" label="Agents at once">
          <input id="auto-agents" className="input" type="number" min={1} max={4} value={def.limits.max_parallel_agents} onChange={(e) => setLimit('max_parallel_agents', clampInt(e.target.value, 1, 4))} />
        </Field>
        <Field id="auto-scripts" label="Scripts at once">
          <input id="auto-scripts" className="input" type="number" min={1} max={8} value={def.limits.max_parallel_scripts} onChange={(e) => setLimit('max_parallel_scripts', clampInt(e.target.value, 1, 8))} />
        </Field>
      </div>
      {o.warnings.length ? (
        <>
          <h2>Warnings</h2>
          <ul className="auto-plain muted">
            {o.warnings.map((w, i) => (
              <li key={i}>{w.message}</li>
            ))}
          </ul>
        </>
      ) : null}
      <p className="muted small">Click Start, a step or an edge to edit it.</p>
    </aside>
  );
}
```

Append to `packages/ui-styles/src/automations.css`:

```css
.auto-card {
  margin: 0;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border: 1px solid var(--rule-soft);
  border-radius: 10px;
  background: var(--card);
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pnpm vitest run packages/ui-core apps/desktop/src/renderer/automations/design packages/ui-styles`
Expected: PASS.

Run: `pnpm typecheck`
Expected: no errors. If `setPreset({ ...preset, time })` does not narrow (TypeScript keeps `preset` as the union after the `kind !== 'custom'` check in JSX), hoist the check: `const timed = preset.kind === 'custom' ? null : preset;` and use `timed`.

- [ ] **Step 6: Commit**

```bash
pnpm test
git add packages/ui-core/src/automation-start.ts packages/ui-core/src/automation-start.test.ts packages/ui-core/src/index.ts apps/desktop/src/renderer/automations/design/StartInspector.tsx apps/desktop/src/renderer/automations/design/SettingsInspector.tsx apps/desktop/src/renderer/automations/design/start.test.tsx packages/ui-styles/src/automations.css
git commit -m "feat(desktop): Start inspector (schedules, inputs) and the automation's settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Versions and the structured diff

The Versions view (spec §8.4):
- Each version shows who saved it, when, its change note and ✓ tested.
- A structured diff compares any two versions: steps added, removed or changed with the fields that changed, plus edges, inputs, schedules and settings.
- Any version can be restored.

The same `DiffView` serves the conflict dialog (Task 13) and the suspended-grants banner (Task 16).

**Files:**
- Create: `packages/ui-core/src/automation-diff.ts`, `packages/ui-core/src/automation-diff.test.ts`
- Modify: `packages/ui-core/src/index.ts`
- Create: `apps/desktop/src/renderer/automations/versions/DiffView.tsx`, `apps/desktop/src/renderer/automations/versions/VersionsView.tsx`
- Modify: `apps/desktop/src/renderer/automations/AutomationsScreen.tsx` (the `versions` branch)
- Modify: `packages/ui-styles/src/automations.css`
- Test: `apps/desktop/src/renderer/automations/versions/versions.test.tsx`

**Interfaces:**
- Consumes: `describeSchedule`, `originText`, `dayTime` (Task 3); `call('automations.versions' | 'automations.version' | 'automations.restore')` (Task 2).
- Produces:
  - `automation-diff.ts`:
    - `type FieldChange = { field; before; after }`;
    - `type StepChange = { id; title; kind; change: 'added'|'removed'|'changed'; fields: FieldChange[] }`;
    - `type InputChange = { key; label; change; fields }`;
    - `type DefinitionDiff = { settings: FieldChange[]; steps: StepChange[]; edges: { added: string[]; removed: string[] }; inputs: InputChange[]; schedules: { before: string[]; after: string[] } | null; empty: boolean }`;
    - `diffDefinitions(before, after, local?): DefinitionDiff`.
  - `DiffView({ diff, labels? })`, where `labels = { before, after }` names the two sides.
  - `VersionsView({ detail, onChange })`.

- [ ] **Step 1: Write the failing tests**

Create `packages/ui-core/src/automation-diff.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import type { AutomationDefinition } from '@desk/protocol';
import { digestDef } from './testing/automations';
import { diffDefinitions } from './automation-diff';

describe('diffDefinitions', () => {
  it('lists steps, edges, inputs, schedules and settings that changed', () => {
    const a = digestDef();
    const b: AutomationDefinition = {
      ...a,
      title: 'Digest',
      limits: { ...a.limits, max_parallel_agents: 3 },
      inputs: [{ ...a.inputs[0]!, label: 'Subject' }, { key: 'deep', label: 'Go deep', type: 'boolean', required: false }],
      triggers: [{ ...a.triggers[0]!, cron: '0 9 * * 1' }],
      steps: [
        a.steps[0]!,
        { ...a.steps[1]!, brief: 'Summarise it.', skills: [] } as AutomationDefinition['steps'][number],
        { id: 'tell', title: 'Tell Desk', kind: 'tell_desk', text: 'Done: {{steps.sum.summary}}', attach: [], join: 'all', on_error: 'stop', routes: [], publish: [] },
      ],
      edges: [a.edges[0]!, { from: 'sum', to: 'tell' }],
    };
    const d = diffDefinitions(a, b, 'UTC');
    expect(d.steps).toEqual([
      { id: 'sum', title: 'Summarise', kind: 'agent', change: 'changed', fields: [{ field: 'brief', before: 'Summarise {{steps.fetch.dir}}.', after: 'Summarise it.' }, { field: 'skills', before: 'web-research', after: '—' }] },
      { id: 'ok', title: 'Publish?', kind: 'ask', change: 'removed', fields: [] },
      { id: 'tell', title: 'Tell Desk', kind: 'tell_desk', change: 'added', fields: [] },
    ]);
    expect(d.edges).toEqual({ added: ['Summarise → Tell Desk'], removed: ['Summarise → Publish?'] });
    expect(d.inputs).toEqual([
      { key: 'topic', label: 'Subject', change: 'changed', fields: [{ field: 'label', before: 'Topic', after: 'Subject' }] },
      { key: 'deep', label: 'Go deep', change: 'added', fields: [] },
    ]);
    expect(d.schedules).toEqual({ before: ['Mondays 08:00 (Europe/Paris)'], after: ['Mondays 09:00 (Europe/Paris)'] });
    expect(d.settings).toEqual([
      { field: 'title', before: 'Weekly digest', after: 'Digest' },
      { field: 'limits.max_parallel_agents', before: '2', after: '3' },
    ]);
    expect(d.empty).toBe(false);
  });

  it('finds nothing between equal definitions, whatever their key order', () => {
    const a = digestDef();
    const reordered = JSON.parse(JSON.stringify({ ...a, steps: a.steps.map((s) => Object.fromEntries(Object.entries(s).reverse())) })) as AutomationDefinition;
    expect(diffDefinitions(a, reordered).empty).toBe(true);
    expect(diffDefinitions(a, { ...a, edges: [{ ...a.edges[0]!, when: 'inputs.topic != ""' }, a.edges[1]!] }).edges).toEqual({
      added: ['Fetch pages → Summarise on changed if inputs.topic != ""'],
      removed: ['Fetch pages → Summarise on changed'],
    });
  });
});
```

Create `apps/desktop/src/renderer/automations/versions/versions.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { automationDetail, digestDef } from '@desk/ui-core/testing';
import { installBridge } from '../../test/bridge';
import { VersionsView } from './VersionsView';

afterEach(cleanup);

const older = () => ({ ...digestDef(), steps: digestDef().steps.slice(0, 2), edges: [digestDef().edges[0]!] });

describe('VersionsView', () => {
  it('lists versions with who saved them, compares two, and restores one', async () => {
    const bridge = installBridge({
      'automations.versions': () => [
        { version: 7, origin: 'agent:d1', change_note: 'Adds a publish step', via: 'tool', created_at: '2026-09-27T09:00:00.000Z', tested: false },
        { version: 6, origin: 'user', change_note: '', via: 'editor', created_at: '2026-09-26T09:00:00.000Z', tested: true },
      ],
      'automations.version': ({ version }: { version: number }) => ({ version, definition: version === 7 ? digestDef() : older(), origin: 'user', change_note: '', via: 'editor', created_at: 't' }),
      'automations.restore': () => automationDetail({ version: 8 }),
    });
    const onChange = vi.fn();
    render(<VersionsView detail={automationDetail()} onChange={onChange} />);
    const v7 = (await screen.findByText('v7')).closest('li')!;
    expect(v7.textContent).toContain('Desk');
    expect(v7.textContent).toContain('Adds a publish step');
    expect(v7.textContent).toContain('current');
    const v6 = screen.getByText('v6').closest('li')!;
    expect(v6.textContent).toContain('You');
    expect(v6.textContent).toContain('✓ tested');
    expect((screen.getByLabelText('Compare') as HTMLSelectElement).value).toBe('6');
    expect((screen.getByLabelText('with') as HTMLSelectElement).value).toBe('7');
    const diff = await screen.findByRole('region', { name: 'Changes from v6 to v7' });
    expect(within(diff).getByText('Publish?')).toBeTruthy();
    expect(within(diff).getByText('Summarise → Publish?')).toBeTruthy();

    fireEvent.click(within(v6).getByRole('button', { name: 'Restore…' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Restore v6?' })).getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ version: 8 })));
    expect(bridge.calls.find((c) => c.channel === 'automations.restore')?.input).toEqual({ id: 'a1', version: 6 });
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm vitest run packages/ui-core/src/automation-diff.test.ts apps/desktop/src/renderer/automations/versions`
Expected: FAIL: the modules do not exist.

- [ ] **Step 3: Write `automation-diff.ts`**

Create `packages/ui-core/src/automation-diff.ts`:

```ts
import type { AutomationDefinition, AutomationEdge, InputSpec, ScheduleTrigger, StepKind } from '@desk/protocol';
import { describeSchedule, systemTimezone } from './automation-schedules';

export type FieldChange = { field: string; before: string; after: string };
export type StepChange = { id: string; title: string; kind: StepKind; change: 'added' | 'removed' | 'changed'; fields: FieldChange[] };
export type InputChange = { key: string; label: string; change: 'added' | 'removed' | 'changed'; fields: FieldChange[] };
/** What changed between two versions of a definition (spec §8.4), as text to show. */
export type DefinitionDiff = {
  settings: FieldChange[];
  steps: StepChange[];
  edges: { added: string[]; removed: string[] };
  inputs: InputChange[];
  schedules: { before: string[]; after: string[] } | null;
  empty: boolean;
};

/** JSON with object keys sorted, so key order never counts as a change. */
function stable(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(stable).join(',')}]`;
  if (v && typeof v === 'object') {
    return `{${Object.keys(v)
      .sort()
      .filter((k) => (v as Record<string, unknown>)[k] !== undefined)
      .map((k) => `${JSON.stringify(k)}:${stable((v as Record<string, unknown>)[k])}`)
      .join(',')}}`;
  }
  return JSON.stringify(v ?? null);
}

const same = (a: unknown, b: unknown) => stable(a) === stable(b);

/** A value as a line of text: "—" for nothing, lists of strings joined. */
function show(v: unknown): string {
  if (v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length)) return '—';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (Array.isArray(v) && v.every((x) => typeof x === 'string')) return v.join(', ');
  return stable(v);
}

function fieldChanges(a: object, b: object, skip: string[]): FieldChange[] {
  const ra = a as Record<string, unknown>;
  const rb = b as Record<string, unknown>;
  const keys = [...new Set([...Object.keys(ra), ...Object.keys(rb)])].filter((k) => !skip.includes(k));
  return keys.filter((k) => !same(ra[k], rb[k])).map((k) => ({ field: k, before: show(ra[k]), after: show(rb[k]) }));
}

const edgeKey = (e: AutomationEdge) => `${e.from}>${e.to}|${e.route ?? ''}|${e.when ?? ''}`;

function edgeText(def: AutomationDefinition, e: AutomationEdge): string {
  const title = (id: string) => def.steps.find((s) => s.id === id)?.title ?? id;
  return `${title(e.from)} → ${title(e.to)}${e.route ? ` on ${e.route}` : ''}${e.when ? ` if ${e.when}` : ''}`;
}

function scheduleText(t: ScheduleTrigger, local: string): string {
  const inputs = t.inputs && Object.keys(t.inputs).length ? ` with ${Object.entries(t.inputs).map(([k, v]) => `${k} = ${String(v)}`).join(', ')}` : '';
  return `${describeSchedule(t.cron, t.timezone, local)}${t.catch_up === 'skip' ? ', skipping missed times' : ''}${inputs}`;
}

export function diffDefinitions(before: AutomationDefinition, after: AutomationDefinition, local = systemTimezone()): DefinitionDiff {
  const settings = [
    ...fieldChanges(
      { title: before.title, description: before.description, after_run: before.after_run, output_step: before.output_step },
      { title: after.title, description: after.description, after_run: after.after_run, output_step: after.output_step },
      [],
    ),
    ...fieldChanges(before.limits, after.limits, []).map((c) => ({ ...c, field: `limits.${c.field}` })),
  ];

  const steps: StepChange[] = [];
  const ids = [...new Set([...before.steps.map((s) => s.id), ...after.steps.map((s) => s.id)])];
  for (const id of ids) {
    const a = before.steps.find((s) => s.id === id);
    const b = after.steps.find((s) => s.id === id);
    if (a && !b) steps.push({ id, title: a.title, kind: a.kind, change: 'removed', fields: [] });
    else if (!a && b) steps.push({ id, title: b.title, kind: b.kind, change: 'added', fields: [] });
    else if (a && b) {
      const fields = fieldChanges(a, b, ['id']);
      if (fields.length) steps.push({ id, title: b.title, kind: b.kind, change: 'changed', fields });
    }
  }

  const beforeEdges = new Set(before.edges.map(edgeKey));
  const afterEdges = new Set(after.edges.map(edgeKey));
  const edges = {
    added: after.edges.filter((e) => !beforeEdges.has(edgeKey(e))).map((e) => edgeText(after, e)),
    removed: before.edges.filter((e) => !afterEdges.has(edgeKey(e))).map((e) => edgeText(before, e)),
  };

  const inputs: InputChange[] = [];
  const keys = [...new Set([...before.inputs.map((i) => i.key), ...after.inputs.map((i) => i.key)])];
  for (const key of keys) {
    const a: InputSpec | undefined = before.inputs.find((i) => i.key === key);
    const b: InputSpec | undefined = after.inputs.find((i) => i.key === key);
    if (a && !b) inputs.push({ key, label: a.label, change: 'removed', fields: [] });
    else if (!a && b) inputs.push({ key, label: b.label, change: 'added', fields: [] });
    else if (a && b) {
      const fields = fieldChanges(a, b, ['key']);
      if (fields.length) inputs.push({ key, label: b.label, change: 'changed', fields });
    }
  }

  const sb = before.triggers.map((t) => scheduleText(t, local));
  const sa = after.triggers.map((t) => scheduleText(t, local));
  const schedules = same(sb, sa) ? null : { before: sb, after: sa };

  return { settings, steps, edges, inputs, schedules, empty: !settings.length && !steps.length && !edges.added.length && !edges.removed.length && !inputs.length && !schedules };
}
```

In `packages/ui-core/src/index.ts`, add `export * from './automation-diff';` among the automation modules, right before `./automation-draft` (the list stays alphabetical).

- [ ] **Step 4: Write the views**

Create `apps/desktop/src/renderer/automations/versions/DiffView.tsx`:

```tsx
import type { DefinitionDiff, FieldChange } from '@desk/ui-core';
import { STEP_KIND_LABEL } from '@desk/ui-core';

const TAG = { added: 'added', removed: 'removed', changed: 'changed' } as const;

function Fields({ fields }: { fields: FieldChange[] }) {
  return (
    <table className="auto-diff-fields">
      <tbody>
        {fields.map((f) => (
          <tr key={f.field}>
            <th className="mono">{f.field}</th>
            <td className="auto-before">{f.before}</td>
            <td className="auto-after">{f.after}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** A structured diff between two definitions (spec §8.4). Everything is plain text. */
export function DiffView({ diff, labels }: { diff: DefinitionDiff; labels?: { before: string; after: string } }) {
  if (diff.empty) return <p className="muted">No changes.</p>;
  return (
    <div className="auto-diff">
      {labels ? <p className="muted small">{`Red is ${labels.before}, green is ${labels.after}.`}</p> : null}
      {diff.steps.length ? (
        <section>
          <h3 className="auto-sub">Steps</h3>
          <ul className="auto-plain">
            {diff.steps.map((s) => (
              <li key={s.id} className="auto-diff-item">
                <span className={`auto-tag ${TAG[s.change]}`}>{s.change}</span> <b>{s.title}</b> <span className="muted small">{`${STEP_KIND_LABEL[s.kind]} · ${s.id}`}</span>
                {s.fields.length ? <Fields fields={s.fields} /> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {diff.edges.added.length || diff.edges.removed.length ? (
        <section>
          <h3 className="auto-sub">Edges</h3>
          <ul className="auto-plain">
            {diff.edges.removed.map((e) => (
              <li key={`-${e}`} className="auto-diff-item">
                <span className="auto-tag removed">removed</span> <span>{e}</span>
              </li>
            ))}
            {diff.edges.added.map((e) => (
              <li key={`+${e}`} className="auto-diff-item">
                <span className="auto-tag added">added</span> <span>{e}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {diff.inputs.length ? (
        <section>
          <h3 className="auto-sub">Inputs</h3>
          <ul className="auto-plain">
            {diff.inputs.map((i) => (
              <li key={i.key} className="auto-diff-item">
                <span className={`auto-tag ${TAG[i.change]}`}>{i.change}</span> <b>{i.label}</b> <span className="muted small mono">{i.key}</span>
                {i.fields.length ? <Fields fields={i.fields} /> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {diff.schedules ? (
        <section>
          <h3 className="auto-sub">Schedules</h3>
          <p className="auto-before">{diff.schedules.before.join(' · ') || 'Run now only'}</p>
          <p className="auto-after">{diff.schedules.after.join(' · ') || 'Run now only'}</p>
        </section>
      ) : null}
      {diff.settings.length ? (
        <section>
          <h3 className="auto-sub">Settings</h3>
          <Fields fields={diff.settings} />
        </section>
      ) : null}
    </div>
  );
}
```

Create `apps/desktop/src/renderer/automations/versions/VersionsView.tsx`:

```tsx
import { useEffect, useMemo, useState } from 'react';
import type { AutomationDefinition, AutomationDetail, AutomationVersionInfo } from '@desk/protocol';
import { dayTime, diffDefinitions, originText } from '@desk/ui-core';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { EmptyState } from '../../components/EmptyState';
import { toast, toastError } from '../../components/Toast';
import { useNow } from '../../state/now';
import { DiffView } from './DiffView';

/** Loads version definitions once each. */
function useDefinitions(id: string, versions: number[]): Record<number, AutomationDefinition> {
  const [defs, setDefs] = useState<Record<number, AutomationDefinition>>({});
  const want = versions.filter((v) => v > 0 && !defs[v]).join(',');
  useEffect(() => {
    if (!want) return;
    let live = true;
    for (const v of want.split(',').map(Number)) {
      call('automations.version', { id, version: v })
        .then((r) => live && setDefs((d) => ({ ...d, [v]: r.definition })))
        .catch(toastError);
    }
    return () => {
      live = false;
    };
  }, [id, want]);
  return defs;
}

/** Versions (spec §8.4): who saved each and when, its note and test, a diff between any two, and Restore. */
export function VersionsView(o: { detail: AutomationDetail; onChange(d: AutomationDetail): void }) {
  const d = o.detail;
  const now = useNow();
  const [versions, setVersions] = useState<AutomationVersionInfo[] | null>(null);
  const [from, setFrom] = useState(0);
  const [to, setTo] = useState(0);
  const [restoring, setRestoring] = useState<number | null>(null);
  useEffect(() => {
    let live = true;
    call('automations.versions', { id: d.id })
      .then((list) => {
        if (!live) return;
        setVersions(list);
        setTo(list[0]?.version ?? 0);
        setFrom(list[1]?.version ?? list[0]?.version ?? 0);
      })
      .catch(toastError);
    return () => {
      live = false;
    };
  }, [d.id, d.version]);
  const defs = useDefinitions(d.id, [from, to]);
  const diff = useMemo(() => (defs[from] && defs[to] ? diffDefinitions(defs[from]!, defs[to]!) : null), [defs, from, to]);
  const restore = async (version: number) => {
    setRestoring(null);
    try {
      const next = await call('automations.restore', { id: d.id, version });
      toast({ tone: 'info', message: `Restored v${version} as v${next.version}.` });
      o.onChange(next);
    } catch (err) {
      toastError(err);
    }
  };
  if (!versions) return <p className="muted auto-loading">Loading…</p>;
  if (!versions.length) return <EmptyState title="No versions">Saving the automation creates its first version.</EmptyState>;
  const pick = (id: string, label: string, value: number, set: (v: number) => void) => (
    <label className="auto-inline small" htmlFor={id}>
      {label}
      <select id={id} className="select" value={String(value)} onChange={(e) => set(Number(e.target.value))}>
        {versions.map((v) => (
          <option key={v.version} value={String(v.version)}>{`v${v.version}`}</option>
        ))}
      </select>
    </label>
  );
  return (
    <div className="auto-versions">
      <ul className="auto-version-list">
        {versions.map((v) => (
          <li key={v.version} className="auto-version">
            <div className="auto-version-head">
              <b className="mono">{`v${v.version}`}</b>
              <span>{originText(v)}</span>
              <span className="muted small">{dayTime(v.created_at, now)}</span>
              {v.tested ? <span className="auto-tag added">✓ tested</span> : null}
              {v.version === d.version ? <span className="auto-tag">current</span> : null}
              <span className="grow" />
              {v.version !== d.version ? (
                <Button size="sm" variant="ghost" onClick={() => setRestoring(v.version)}>
                  Restore…
                </Button>
              ) : null}
            </div>
            {v.change_note ? <p className="auto-version-note">{v.change_note}</p> : null}
          </li>
        ))}
      </ul>
      <section className="auto-version-diff" aria-label={`Changes from v${from} to v${to}`}>
        <div className="auto-inline">
          {pick('versions-from', 'Compare', from, setFrom)}
          {pick('versions-to', 'with', to, setTo)}
        </div>
        {diff ? <DiffView diff={diff} labels={{ before: `v${from}`, after: `v${to}` }} /> : <p className="muted">Loading…</p>}
      </section>
      {restoring !== null ? (
        <ConfirmDialog title={`Restore v${restoring}?`} confirmLabel="Restore" onConfirm={() => void restore(restoring)} onCancel={() => setRestoring(null)}>
          {`Its definition becomes a new version, v${d.version + 1}. Nothing is lost: v${d.version} stays in the list.`}
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
```

`<label htmlFor>` wraps the `<select>`, so `getByLabelText('Compare')` finds it: the label's own text is "Compare", and Testing Library ignores a `<select>`'s option text in a label's name.

In `apps/desktop/src/renderer/automations/AutomationsScreen.tsx`, import `VersionsView` and change the `versions` branch of `AutomationBody` to:

```tsx
    case 'versions':
      return <VersionsView detail={o.detail} onChange={o.onChange} />;
```

Append to `packages/ui-styles/src/automations.css`:

```css
/* Versions and diffs */
.auto-versions {
  display: grid;
  grid-template-columns: minmax(260px, 360px) 1fr;
  gap: 20px;
  padding: 16px 20px;
}
.auto-version-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.auto-version {
  padding: 8px 10px;
  border: 1px solid var(--rule-soft);
  border-radius: 10px;
  background: var(--card);
}
.auto-version-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.auto-version-note {
  margin: 4px 0 0;
  font-size: 12.5px;
  color: var(--text);
}
.auto-version-diff {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}
.auto-tag {
  padding: 0 6px;
  border-radius: 999px;
  background: var(--sunken);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 10.5px;
}
.auto-tag.added {
  background: var(--add-bg);
  color: var(--ok);
}
.auto-tag.removed {
  background: var(--del-bg);
  color: var(--accent);
}
.auto-tag.changed {
  background: var(--wait-pastel);
  color: var(--wait-text);
}
.auto-diff {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 13px;
}
.auto-diff-item {
  padding: 4px 0;
}
.auto-diff-fields {
  width: 100%;
  margin-top: 4px;
  border-collapse: collapse;
  font-size: 12px;
}
.auto-diff-fields th,
.auto-diff-fields td {
  padding: 3px 6px;
  text-align: left;
  vertical-align: top;
  white-space: pre-wrap;
  word-break: break-word;
}
.auto-diff-fields th {
  width: 120px;
  color: var(--muted);
  font-weight: 400;
}
.auto-before {
  background: var(--del-bg);
}
.auto-after {
  background: var(--add-bg);
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pnpm vitest run packages/ui-core apps/desktop/src/renderer/automations packages/ui-styles`
Expected: PASS.

Run: `pnpm typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
pnpm test
git add packages/ui-core/src/automation-diff.ts packages/ui-core/src/automation-diff.test.ts packages/ui-core/src/index.ts apps/desktop/src/renderer/automations/versions apps/desktop/src/renderer/automations/AutomationsScreen.tsx packages/ui-styles/src/automations.css
git commit -m "feat(desktop): automation versions with a structured diff and restore

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: The Design view: draft, validation, save and conflicts

This task puts the canvas (Task 8), the inspectors (Tasks 10–11) and the draft operations (Task 5) together (spec §8.2):
- **Draft.** Edits change a local draft.
- **Validation** runs 400 ms after the last edit and marks nodes red.
- **Save** sends `base_version`. A 409 opens the conflict dialog, with *Review changes*, *Save mine anyway* and *Discard mine*.
- **Layout.** Positions save on their own, 800 ms after a move or Tidy up.
- **Blank automation.** A draft that is not saved yet uses the same view; its first Save creates it.

**Files:**
- Create: `apps/desktop/src/renderer/automations/design/DesignView.tsx`, `apps/desktop/src/renderer/automations/design/ConflictDialog.tsx`
- Modify: `apps/desktop/src/renderer/automations/AutomationsScreen.tsx` (the `design` branch and `DraftAutomation`)
- Modify: `packages/ui-styles/src/automations.css`
- Test: `apps/desktop/src/renderer/automations/design/DesignView.test.tsx`

**Interfaces:**
- Consumes: `GraphCanvas` (Task 8); the inspectors (Tasks 10–11); `DiffView` (Task 12); from `@desk/ui-core`: `addStep`, `autoLayout`, `blankDefinition`, `connect`, `diffDefinitions`, `ensureLayout`, `mapIssues`, `moveNodes`, `removeSelection`, `originText`, `whenText`, `STEP_KIND_LABEL`.
- Produces:
  - `DesignView(props: DesignProps)`, with `type DesignProps = { projectId; sources: Array<{ id; label }> } & ({ detail: AutomationDetail; onChange(d): void } | { draftName: string })`.
  - `ConflictDialog({ conflict: Conflict; mine; saving; onSaveMine(); onDiscardMine(); onClose() })`, with `type Conflict = { theirs: AutomationDetail; by: string }`.
  - The toolbar's status (`role="status"`) reads "Checking…", "N problem(s)", "Ready to save" or "Saved as vN". The e2e test (Task 18) waits on it.

- [ ] **Step 1: Write the failing test**

Create `apps/desktop/src/renderer/automations/design/DesignView.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AutomationDefinition } from '@desk/protocol';
import { automationDetail, digestDef } from '@desk/ui-core/testing';
import { installBridge } from '../../test/bridge';
import { installReactFlowShims } from '../../test/reactflow';
import { DesignView } from './DesignView';

beforeAll(installReactFlowShims);
beforeEach(() => void (window.location.hash = '#/p/p/automations/a1'));
afterEach(cleanup);

type Req = { req: { definition: AutomationDefinition; base_version?: number; name?: string } };
const valid = () => ({ errors: [], warnings: [], next_times: {} });
const common = { 'skills.list': () => [], 'builtins.list': () => [], 'automations.layout': () => ({ ok: true }) };

describe('DesignView', () => {
  it('marks problems on their node, blocks Save, and shows them in the step inspector', async () => {
    installBridge({ ...common, 'automations.validate': () => ({ errors: [{ path: 'steps[2].question', message: 'Too small' }], warnings: [], next_times: {} }) });
    render(<DesignView projectId="p" sources={[]} detail={automationDetail()} onChange={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId('node-ok').className).toContain('invalid'));
    expect(screen.getByRole('status').textContent).toBe('1 problem');
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId('node-ok'));
    expect(screen.getByText('Ask me step')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('question: Too small');
  });

  it('adds a step from the strip, edits it and saves with a note on the current version', async () => {
    const onChange = vi.fn();
    const bridge = installBridge({ ...common, 'automations.validate': valid, 'automations.save': ({ req }: Req) => ({ automation: automationDetail({ version: 8, definition: req.definition }), warnings: [] }) });
    render(<DesignView projectId="p" sources={[]} detail={automationDetail()} onChange={onChange} />);
    await screen.findByText('Saved as v7');
    fireEvent.click(within(screen.getByRole('toolbar', { name: 'Add a step' })).getByRole('button', { name: 'Tell Desk' }));
    expect(await screen.findByTestId('node-tell_desk')).toBeTruthy();
    expect(screen.getByText('Tell Desk step')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Message to Desk'), { target: { value: 'Done.' } });
    fireEvent.change(screen.getByLabelText('Change note'), { target: { value: 'Tells Desk' } });
    await screen.findByText('Ready to save');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ version: 8 })));
    expect(bridge.calls.find((c) => c.channel === 'automations.save')?.input).toMatchObject({ id: 'a1', req: { base_version: 7, change_note: 'Tells Desk', via: 'editor' } });
    await screen.findByText('Saved as v8');
  });

  it('on a 409 shows who saved, the changes, and saves mine on the new base', async () => {
    const onChange = vi.fn();
    const theirs = automationDetail({ version: 8, definition: { ...digestDef(), title: 'Digest (Desk)' } });
    const bridge = installBridge({
      ...common,
      'automations.validate': valid,
      'automations.save': ({ req }: Req) => {
        if (req.base_version === 7) throw { code: 'conflict', message: 'stale', status: 409 };
        return { automation: automationDetail({ version: 9, definition: req.definition }), warnings: [] };
      },
      'automations.get': () => theirs,
      'automations.versions': () => [{ version: 8, origin: 'agent:d1', change_note: 'x', via: 'tool', created_at: '2026-09-27T09:00:00.000Z', tested: false }],
    });
    render(<DesignView projectId="p" sources={[]} detail={automationDetail()} onChange={onChange} />);
    await screen.findByText('Saved as v7');
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'My digest' } });
    await screen.findByText('Ready to save');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    const dialog = await screen.findByRole('dialog', { name: 'Desk saved v8 while you were editing' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Review changes' }));
    const changes = within(dialog).getByRole('region', { name: 'What saving yours changes' });
    expect(changes.textContent).toContain('Digest (Desk)');
    expect(changes.textContent).toContain('My digest');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save mine anyway' }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ version: 9 })));
    expect(bridge.calls.filter((c) => c.channel === 'automations.save').map((c) => (c.input as Req).req.base_version)).toEqual([7, 8]);
  });

  it('saves the layout on its own after Tidy up', async () => {
    const bridge = installBridge({ ...common, 'automations.validate': valid });
    render(<DesignView projectId="p" sources={[]} detail={automationDetail()} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Tidy up' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.layout')?.input).toMatchObject({ id: 'a1', layout: { __start: expect.any(Object), fetch: expect.any(Object) } }), { timeout: 2000 });
  });

  it('creates a Blank automation on its first save, with its layout, and opens it', async () => {
    const bridge = installBridge({
      ...common,
      'automations.validate': ({ req }: Req) => (req.definition.steps.length ? valid() : { errors: [{ path: 'steps', message: 'Add at least one step' }], warnings: [], next_times: {} }),
      'automations.create': ({ req }: Req) => ({ automation: automationDetail({ id: 'a9', name: req.name!, title: 'Weekly note' }), warnings: [] }),
    });
    render(<DesignView projectId="p" sources={[]} draftName="weekly-note" />);
    expect(await screen.findByText('1 problem')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('Add at least one step');
    fireEvent.click(within(screen.getByRole('toolbar', { name: 'Add a step' })).getByRole('button', { name: 'Wait' }));
    await screen.findByText('Ready to save');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(window.location.hash).toBe('#/p/p/automations/a9'));
    expect(bridge.calls.find((c) => c.channel === 'automations.create')?.input).toMatchObject({
      projectId: 'p',
      req: { name: 'weekly-note', via: 'editor', definition: { title: 'Weekly note', steps: [expect.objectContaining({ kind: 'wait', minutes: 60 })] } },
    });
    expect(bridge.calls.some((c) => c.channel === 'automations.layout' && (c.input as { id: string }).id === 'a9')).toBe(true);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm vitest run apps/desktop/src/renderer/automations/design/DesignView.test.tsx`
Expected: FAIL: cannot find `./DesignView`.

- [ ] **Step 3: Write the conflict dialog and the view**

Create `apps/desktop/src/renderer/automations/design/ConflictDialog.tsx`:

```tsx
import { useState } from 'react';
import type { AutomationDefinition, AutomationDetail } from '@desk/protocol';
import { diffDefinitions } from '@desk/ui-core';
import { Button } from '../../components/Button';
import { Sheet } from '../../components/Sheet';
import { DiffView } from '../versions/DiffView';

/** A save refused because another version landed first: that version, and who saved it ("Desk", "You (CLI)"…). */
export type Conflict = { theirs: AutomationDetail; by: string };

/** Spec §8.2: "Desk saved v8 while you were editing", with Review changes, Save mine anyway and Discard mine. */
export function ConflictDialog(o: { conflict: Conflict; mine: AutomationDefinition; saving: boolean; onSaveMine(): void; onDiscardMine(): void; onClose(): void }) {
  const [review, setReview] = useState(false);
  const { theirs, by } = o.conflict;
  const v = theirs.version;
  return (
    <Sheet
      title={by === 'Desk' ? `Desk saved v${v} while you were editing` : `v${v} was saved while you were editing`}
      onClose={o.onClose}
      width={640}
      footer={
        <>
          <Button onClick={() => setReview((r) => !r)}>{review ? 'Hide changes' : 'Review changes'}</Button>
          <Button onClick={o.onDiscardMine}>Discard mine</Button>
          <Button variant="primary" pending={o.saving} onClick={o.onSaveMine}>
            Save mine anyway
          </Button>
        </>
      }
    >
      <p>{`Saved by ${by}. Your edits started from an earlier version.`}</p>
      <p className="muted small">{`Save mine anyway makes your version v${v + 1}; v${v} stays in Versions. Discard mine loads v${v} into the editor.`}</p>
      {review ? (
        <section aria-label="What saving yours changes">
          <DiffView diff={diffDefinitions(theirs.definition, o.mine)} labels={{ before: `v${v}`, after: 'yours' }} />
        </section>
      ) : null}
    </Sheet>
  );
}
```

Create `apps/desktop/src/renderer/automations/design/DesignView.tsx`:

```tsx
import { useEffect, useMemo, useRef, useState } from 'react';
import type { AutomationDefinition, AutomationDetail, AutomationLayout, StepKind, ValidationIssue } from '@desk/protocol';
import {
  addStep,
  autoLayout,
  blankDefinition,
  connect,
  diffDefinitions,
  ensureLayout,
  mapIssues,
  moveNodes,
  originText,
  removeSelection,
  STEP_KIND_LABEL,
  whenText,
  type AutomationDoc,
  type GraphSelection,
} from '@desk/ui-core';
import { call, DeskCallError } from '../../bridge';
import { Button } from '../../components/Button';
import { toast, toastError } from '../../components/Toast';
import { navigate } from '../../router';
import { ConflictDialog, type Conflict } from './ConflictDialog';
import { EdgeInspector } from './EdgeInspector';
import { GraphCanvas } from './GraphCanvas';
import { SettingsInspector } from './SettingsInspector';
import { StartInspector } from './StartInspector';
import { StepInspector } from './StepInspector';

const KINDS: StepKind[] = ['script', 'agent', 'ask', 'wait', 'automation', 'tell_desk'];
const KIND_CLASS: Record<StepKind, string> = { script: 'k-script', agent: 'k-agent', ask: 'k-ask', wait: 'k-wait', automation: 'k-automation', tell_desk: 'k-tell' };
type Validation = { errors: ValidationIssue[]; warnings: ValidationIssue[]; next_times: Record<string, string[]> };
const NO_ISSUES: Validation = { errors: [], warnings: [], next_times: {} };

export type DesignProps = { projectId: string; sources: Array<{ id: string; label: string }> } & ({ detail: AutomationDetail; onChange(d: AutomationDetail): void } | { draftName: string });

const docOf = (def: AutomationDefinition, layout: AutomationLayout): AutomationDoc => ({ def, layout: ensureLayout(def, layout) });

/** Design (mockup 2): the canvas, the inspector, the add-step strip, validation, Save and conflicts. */
export function DesignView(o: DesignProps) {
  const detail = 'detail' in o ? o.detail : null;
  const name = detail ? detail.name : (o as { draftName: string }).draftName;
  const [doc, setDoc] = useState<AutomationDoc>(() => (detail ? docOf(detail.definition, detail.layout) : docOf(blankDefinition(name), {})));
  const [saved, setSaved] = useState<{ def: AutomationDefinition; version: number } | null>(() => (detail ? { def: detail.definition, version: detail.version } : null));
  const [selection, setSelection] = useState<GraphSelection>({ kind: 'none' });
  const [validation, setValidation] = useState<Validation>(NO_ISSUES);
  const [checked, setChecked] = useState(false);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const dirty = useMemo(() => !saved || !diffDefinitions(saved.def, doc.def).empty, [saved, doc.def]);
  const issues = useMemo(() => mapIssues(doc.def, validation.errors), [doc.def, validation.errors]);

  // Validation, 400 ms after the last edit; an answer for an older draft is dropped.
  const gen = useRef(0);
  useEffect(() => {
    const g = ++gen.current;
    setChecked(false);
    const t = setTimeout(() => {
      call('automations.validate', { projectId: o.projectId, req: { definition: doc.def, name } })
        .then((r) => {
          if (g !== gen.current) return;
          setValidation(r);
          setChecked(true);
        })
        .catch(() => {});
    }, 400);
    return () => clearTimeout(t);
  }, [doc.def, o.projectId, name]);

  // Another save landed (Desk, the CLI, a restore): adopt it unless the user has edits, which Save will meet as a 409.
  useEffect(() => {
    if (!detail || !saved || detail.version === saved.version || dirty) return;
    setSaved({ def: detail.definition, version: detail.version });
    setDoc((d) => docOf(detail.definition, d.layout));
    // Only a new version matters here.
  }, [detail?.version]);

  // Positions save on their own, 800 ms after the last move.
  const layoutTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(layoutTimer.current), []);
  const saveLayout = (layout: AutomationLayout) => {
    if (!detail) return;
    clearTimeout(layoutTimer.current);
    layoutTimer.current = setTimeout(() => void call('automations.layout', { id: detail.id, layout }).catch(toastError), 800);
  };
  const relayout = (next: AutomationDoc) => {
    setDoc(next);
    saveLayout(next.layout);
  };

  const sel: GraphSelection =
    (selection.kind === 'step' && !doc.def.steps.some((s) => s.id === selection.id)) || (selection.kind === 'edge' && !doc.def.edges[selection.index]) ? { kind: 'none' } : selection;

  const add = (kind: StepKind) => {
    const r = addStep(doc, kind, sel.kind === 'step' ? sel.id : null);
    relayout(r.doc);
    setSelection({ kind: 'step', id: r.id });
  };
  const remove = (steps: string[], edges: number[]) => {
    setDoc(removeSelection(doc, steps, edges));
    setSelection({ kind: 'none' });
  };
  const discard = () => {
    if (!saved) return;
    setDoc(docOf(saved.def, doc.layout));
    setSelection({ kind: 'none' });
  };

  const save = async (base?: number) => {
    const sent = doc.def;
    const changeNote = note.trim() ? { change_note: note.trim() } : {};
    setSaving(true);
    try {
      if (!detail) {
        const r = await call('automations.create', { projectId: o.projectId, req: { name, definition: sent, via: 'editor', ...changeNote } });
        await call('automations.layout', { id: r.automation.id, layout: doc.layout });
        toast({ tone: 'info', message: `Saved ${r.automation.title}. It stays off until you turn it on.` });
        navigate({ name: 'project', id: o.projectId, tab: 'automations', automationId: r.automation.id, view: 'design' });
        return;
      }
      const r = await call('automations.save', { id: detail.id, req: { definition: sent, base_version: base ?? saved!.version, via: 'editor', ...changeNote } });
      void call('automations.layout', { id: detail.id, layout: doc.layout }).catch(() => {});
      setSaved({ def: r.automation.definition, version: r.automation.version });
      setDoc((d) => (d.def === sent ? { ...d, def: r.automation.definition } : d));
      setNote('');
      setConflict(null);
      if ('onChange' in o) o.onChange(r.automation);
      if (r.warnings.length) toast({ tone: 'info', message: `Saved v${r.automation.version} with ${r.warnings.length} warning${r.warnings.length === 1 ? '' : 's'}: see the automation's settings.` });
    } catch (err) {
      if (detail && err instanceof DeskCallError && err.status === 409) {
        try {
          const [theirs, versions] = await Promise.all([call('automations.get', { id: detail.id }), call('automations.versions', { id: detail.id })]);
          setConflict({ theirs, by: versions[0] ? originText(versions[0]) : 'someone' });
        } catch (e) {
          toastError(e);
        }
      } else toastError(err);
    } finally {
      setSaving(false);
    }
  };
  const discardMine = () => {
    if (!conflict) return;
    const { theirs } = conflict;
    setSaved({ def: theirs.definition, version: theirs.version });
    setDoc(docOf(theirs.definition, doc.layout));
    setConflict(null);
    setSelection({ kind: 'none' });
    if ('onChange' in o) o.onChange(theirs);
  };

  const problems = validation.errors.length;
  const status = !checked ? 'Checking…' : problems ? `${problems} problem${problems === 1 ? '' : 's'}` : dirty ? 'Ready to save' : `Saved as v${saved?.version ?? 1}`;
  const startLabel = doc.def.triggers.length ? `${whenText(doc.def.triggers)} · or Run now` : 'Run now only';

  const inspector =
    sel.kind === 'start' ? (
      <StartInspector doc={doc} errors={issues.start} nextTimes={validation.next_times} onChange={setDoc} />
    ) : sel.kind === 'step' ? (
      <StepInspector
        key={sel.id}
        projectId={o.projectId}
        doc={doc}
        stepId={sel.id}
        selfName={name}
        sources={o.sources}
        errors={issues.steps[sel.id] ?? []}
        onChange={setDoc}
        onRenamed={(id) => setSelection({ kind: 'step', id })}
        onDelete={() => remove([sel.id], [])}
      />
    ) : sel.kind === 'edge' ? (
      <EdgeInspector doc={doc} index={sel.index} errors={issues.edges[sel.index] ?? []} onChange={setDoc} onDelete={() => remove([], [sel.index])} />
    ) : (
      <SettingsInspector doc={doc} issues={issues.general} warnings={validation.warnings} onChange={setDoc} />
    );

  return (
    <div className="auto-design">
      <div className="auto-design-main">
        <div className="auto-toolbar">
          <span className={`auto-status${problems ? ' bad' : ''}`} role="status">
            {status}
          </span>
          <span className="grow" />
          <Button size="sm" variant="ghost" onClick={() => relayout({ ...doc, layout: autoLayout(doc.def) })}>
            Tidy up
          </Button>
          {detail && dirty ? (
            <Button size="sm" variant="ghost" onClick={discard}>
              Discard changes
            </Button>
          ) : null}
          <input className="input auto-note" aria-label="Change note" placeholder="What changed (optional)" maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
          <Button variant="primary" size="sm" pending={saving} disabled={!dirty || !checked || problems > 0} title={problems ? 'Fix the problems first' : undefined} onClick={() => void save()}>
            Save
          </Button>
        </div>
        <GraphCanvas
          def={doc.def}
          layout={doc.layout}
          startLabel={startLabel}
          selection={sel}
          onSelect={setSelection}
          issues={issues}
          editable
          onMove={(positions) => relayout(moveNodes(doc, positions))}
          onConnect={(from, to) => {
            const r = connect(doc, from, to);
            if ('error' in r) toast({ tone: 'error', message: r.error });
            else setDoc(r.doc);
          }}
          onDelete={({ steps, edges }) => remove(steps, edges)}
        />
        <div className="auto-strip" role="toolbar" aria-label="Add a step">
          <span className="muted small">Add a step</span>
          {KINDS.map((k) => (
            <button key={k} type="button" className={`auto-add ${KIND_CLASS[k]}`} onClick={() => add(k)}>
              {STEP_KIND_LABEL[k]}
            </button>
          ))}
        </div>
      </div>
      {inspector}
      {conflict ? <ConflictDialog conflict={conflict} mine={doc.def} saving={saving} onSaveMine={() => void save(conflict.theirs.version)} onDiscardMine={discardMine} onClose={() => setConflict(null)} /> : null}
    </div>
  );
}
```

The "adopt another save" effect depends only on `detail?.version` on purpose. The repo runs no hooks linter, and the comment says why.

In `apps/desktop/src/renderer/automations/AutomationsScreen.tsx`:
- import `DesignView` from `./design/DesignView`;
- add a helper:

```tsx
/** The project's git sources, for an agent step's worktree. */
const gitSources = (s: SessionState) => (s.project?.sources ?? []).filter((x) => x.kind === 'git').map((x) => ({ id: x.id, label: x.label }));
```

- change the `default` (design) branch of `AutomationBody` to:

```tsx
    default:
      return <DesignView key={o.detail.id} projectId={o.projectId} sources={gitSources(o.s)} detail={o.detail} onChange={o.onChange} />;
```

- and `DraftAutomation`'s body to:

```tsx
      <div className="automation-body">
        <DesignView key={o.name} projectId={o.projectId} sources={gitSources(o.s)} draftName={o.name} />
      </div>
```

Append to `packages/ui-styles/src/automations.css`:

```css
/* Design (mockup 2) */
.auto-design {
  display: flex;
  height: 100%;
  min-height: 0;
}
.auto-design-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.auto-toolbar,
.auto-strip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--paper);
}
.auto-toolbar {
  border-bottom: 1px solid var(--rule-soft);
}
.auto-strip {
  border-top: 1px solid var(--rule-soft);
  flex-wrap: wrap;
}
.auto-status {
  font-size: 12px;
  color: var(--text-faint);
}
.auto-status.bad {
  color: var(--accent);
}
.auto-note {
  width: 220px;
}
.auto-add {
  --k: var(--text);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid var(--rule);
  border-radius: 999px;
  background: var(--card);
  color: var(--ink);
  font-size: 12px;
  cursor: pointer;
}
.auto-add::before {
  content: '';
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--k);
}
.auto-add:hover {
  border-color: var(--k);
}
.auto-add.k-agent {
  --k: var(--run);
}
.auto-add.k-ask {
  --k: var(--wait);
}
.auto-add.k-wait {
  --k: var(--muted);
}
.auto-add.k-automation {
  --k: var(--ok);
}
.auto-add.k-tell {
  --k: var(--accent);
}
```

`.automation-body` scrolls (`overflow: auto`); the Design view fills it exactly (`height: 100%`), so only the inspector scrolls.

- [ ] **Step 4: Run the tests to verify they pass**

The header test in `AutomationsScreen.test.tsx` renders `view="design"`, which now draws the canvas. In that file:
- add `import { installReactFlowShims } from '../test/reactflow';`;
- add `beforeAll` to its vitest import, and `beforeAll(installReactFlowShims);` after the imports;
- add `'automations.validate': () => ({ errors: [], warnings: [], next_times: {} }),` to the header test's handlers.

Run: `pnpm vitest run apps/desktop/src/renderer/automations`
Expected: PASS.

Run: `pnpm typecheck`
Expected: no errors.

- [ ] **Step 5: Look at it**

Run `pnpm desktop`, then open a project's Automations tab:
- Create a Blank automation.
- Add a Script step and an Agent step, and drag from the script's bottom handle to the agent.
- Watch the validation marks.
- Tidy up.
- Save, then reload the app: the positions stay.

Close the app.

- [ ] **Step 6: Commit**

```bash
pnpm test
git add apps/desktop/src/renderer/automations/design/DesignView.tsx apps/desktop/src/renderer/automations/design/ConflictDialog.tsx apps/desktop/src/renderer/automations/design/DesignView.test.tsx apps/desktop/src/renderer/automations/AutomationsScreen.tsx apps/desktop/src/renderer/automations/AutomationsScreen.test.tsx packages/ui-styles/src/automations.css
git commit -m "feat(desktop): the automation Design view: draft, validation, save, conflicts, layout

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Runs: the list and the live run view

Runs, spec §8.3 view A:
- **The list:** #, started, trigger (with the version for tests), status (with the current or failed step), took and summary. Skipped schedule times are lines of their own.
- **A run:** the Design graph, read-only, each node lit with its state:
  - done `--ok` with its duration;
  - running `--run` with a ring and its live activity;
  - waiting amber;
  - pending dimmed;
  - skipped hatched;
  - failed `--accent`.

  Edges that fired are solid, the others dashed.
- **The header:** *Open folder* and *Cancel run*.
- **The side panel** shows the run, or the selected step. Task 15 gives steps their full panel.

**Files:**
- Create: `packages/ui-core/src/automation-run-graph.ts`, `packages/ui-core/src/automation-run-graph.test.ts`
- Modify: `packages/ui-core/src/index.ts`
- Create: `apps/desktop/src/renderer/automations/runs/RunsList.tsx`, `apps/desktop/src/renderer/automations/runs/RunView.tsx`
- Modify: `apps/desktop/src/renderer/automations/AutomationsScreen.tsx` (the `runs` branch)
- Modify: `packages/ui-styles/src/automations.css`
- Test: `apps/desktop/src/renderer/automations/runs/runs.test.tsx`, `apps/desktop/src/renderer/test/session.ts`

**Interfaces:**
- Consumes: `useRuns`, `useRun` (Task 6); `GraphCanvas` (Task 8); `runStatusText`, `triggerText`, `runTook`, `dayTime`, `skippedText` (Task 3); `GraphRun`, `NodeRunState` (Task 4).
- Produces:
  - `automation-run-graph.ts`:
    - `stepLook(row, now, activity?): NodeRunState`;
    - `firedEdges(def, steps): Set<number>`, `runGraph(run, now, activity): GraphRun`;
    - `agentActivity(events, agentId): string | null`;
    - `focusStep(run): string | null`: the step to open first;
    - `askDeskText(run, stepTitle, error): string`, `canStopStep(run, row): boolean`, `relRunPath(path, runId): string | null`.
  - `RunsList({ projectId, s, detail })` and `RunView({ projectId, s, detail, runId })`.
  - `sessionOf(events?, over?)` (test helper): a ready `SessionState` for component tests.

- [ ] **Step 1: Write the failing tests**

Create `packages/ui-core/src/automation-run-graph.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { ev } from '@desk/client/testing';
import { runDetail, stepRun } from './testing/automations';
import { agentActivity, askDeskText, canStopStep, firedEdges, focusStep, relRunPath, runGraph, stepLook } from './automation-run-graph';

const now = Date.parse('2026-09-28T06:06:12.000Z');

describe('run looks', () => {
  it('words each step state for its node', () => {
    expect(stepLook(stepRun('a', { status: 'succeeded', route: 'changed', started_at: '2026-09-28T06:00:00.000Z', finished_at: '2026-09-28T06:00:12.000Z' }), now)).toEqual({ tone: 'ok', badge: '✓ 12s', detail: 'route changed' });
    expect(stepLook(stepRun('a', { status: 'running', started_at: '2026-09-28T06:00:12.000Z' }), now, 'web_fetch · acme.com')).toEqual({ tone: 'run', badge: '● 6m', detail: 'web_fetch · acme.com' });
    expect(stepLook(stepRun('a', { status: 'waiting', question: { text: 'OK?', files: [] } }), now)).toEqual({ tone: 'wait', badge: 'waiting', detail: 'waiting on you' });
    expect(stepLook(stepRun('a', { status: 'waiting', gate: { tool: 'skill_run', subject: 'digest/fetch.py', reason: 'r' } }), now).detail).toBe('approve skill_run');
    expect(stepLook(stepRun('a', { status: 'failed', error: 'boom\nstack' }), now)).toEqual({ tone: 'fail', badge: '✕ failed', detail: 'boom' });
    expect(stepLook(stepRun('a', { status: 'skipped' }), now).tone).toBe('skipped');
    expect(stepLook(stepRun('a', { attempt: 0 }), now)).toEqual({ tone: 'pending', badge: 'pending', detail: null });
    expect(stepLook(undefined, now).tone).toBe('pending');
  });

  it('lights the edges that fired, and the run as a whole', () => {
    const run = runDetail();
    expect([...firedEdges(run.definition, run.steps)]).toEqual([0]);
    const g = runGraph(run, now, { sum: 'reading acme.md' });
    expect(g.steps.fetch).toMatchObject({ tone: 'ok' });
    expect(g.steps.sum).toMatchObject({ tone: 'run', detail: 'reading acme.md' });
    expect(g.steps.ok).toMatchObject({ tone: 'pending' });
  });

  it('opens the step that needs the user first, then a running one, then a failed one', () => {
    expect(focusStep(runDetail())).toBe('sum');
    const waiting = runDetail({ steps: [...runDetail().steps.slice(0, 2), stepRun('ok', { status: 'waiting', question: { text: 'OK?', files: [] } })] });
    expect(focusStep(waiting)).toBe('ok');
    expect(focusStep(runDetail({ status: 'succeeded', steps: [stepRun('fetch', { status: 'succeeded' })] }))).toBeNull();
  });

  it('reads an agent’s current tool call from the log', () => {
    const events = [
      ev(1, 'tool.call', { run_id: 'x', tool_call_id: 't1', name: 'web_fetch', arguments: '{"url":"https://acme.com"}' }, { agent: 'ag1' }),
      ev(2, 'tool.result', { run_id: 'x', tool_call_id: 't1', name: 'web_fetch', status: 'ok', content: 'ok' }, { agent: 'ag1' }),
      ev(3, 'tool.call', { run_id: 'x', tool_call_id: 't2', name: 'read_file', arguments: '{"path":"acme.md"}' }, { agent: 'ag1' }),
    ];
    expect(agentActivity(events, 'ag1')).toMatch(/^read_file · /);
    expect(agentActivity(events.slice(0, 2), 'ag1')).toBeNull();
    expect(agentActivity(events, 'other')).toBeNull();
  });

  it('words the fix request, knows what can be stopped, and relativises question files', () => {
    const run = runDetail({ status: 'failed' });
    expect(askDeskText(run, 'Fetch pages', 'exit 1: timeout\nTraceback…')).toBe('Please fix digest: run #14 failed at Fetch pages. exit 1: timeout');
    const live = runDetail();
    expect(canStopStep(live, live.steps[1]!)).toBe(true);
    expect(canStopStep(live, live.steps[0]!)).toBe(false);
    expect(relRunPath('/data/automation-runs/r14/steps/sum/digest.md', 'r14')).toBe('steps/sum/digest.md');
    expect(relRunPath('/elsewhere/x.md', 'r14')).toBeNull();
  });
});
```

Create `apps/desktop/src/renderer/test/session.ts`:

```ts
import { emptyChat, emptyMessages, emptyTimeline } from '@desk/client';
import type { StoredEvent } from '@desk/protocol';
import type { SessionState } from '../state/session';

/** A loaded project session for component tests that take `s` directly. */
export function sessionOf(events: StoredEvent[] = [], over: Partial<SessionState> = {}): SessionState {
  return { status: 'ready', error: null, project: null, chat: emptyChat('p'), timeline: emptyTimeline('p'), messages: emptyMessages(), events, streams: {}, ...over };
}
```

Create `apps/desktop/src/renderer/automations/runs/runs.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { automationDetail, runDetail } from '@desk/ui-core/testing';
import { installBridge } from '../../test/bridge';
import { installReactFlowShims } from '../../test/reactflow';
import { sessionOf } from '../../test/session';
import { RunsList } from './RunsList';
import { RunView } from './RunView';

beforeAll(installReactFlowShims);
beforeEach(() => void (window.location.hash = '#/p/p/automations/a1/runs'));
afterEach(cleanup);

describe('RunsList', () => {
  it('lists runs with number, trigger, status and took, and skipped times as lines of their own', async () => {
    installBridge({
      'automations.runs': () => [
        { kind: 'run', run: runDetail() },
        { kind: 'skipped', automation_id: 'a1', trigger_index: 0, due_at: '2026-09-27T06:00:00.000Z', reason: 'still_running', ts: '2026-09-27T06:00:00.000Z' },
        { kind: 'run', run: { ...runDetail({ id: 'r13', number: 13, trigger: 'test', test: true, version: 6, status: 'failed', at_step: 'Fetch pages', finished_at: '2026-09-27T05:10:00.000Z', started_at: '2026-09-27T05:00:00.000Z', summary: 'exit 1' }) } },
      ],
    });
    render(<RunsList projectId="p" s={sessionOf()} detail={automationDetail()} />);
    const r14 = (await screen.findByRole('link', { name: '#14' })).closest('tr')!;
    expect(r14.textContent).toContain('running · Summarise');
    expect(r14.textContent).toContain('schedule');
    expect(screen.getByRole('link', { name: '#14' }).getAttribute('href')).toBe('#/p/p/automations/a1/runs/r14');
    const r13 = screen.getByRole('link', { name: '#13' }).closest('tr')!;
    expect(r13.textContent).toContain('test · v6');
    expect(r13.textContent).toContain('failed at Fetch pages');
    expect(r13.textContent).toContain('10m');
    expect(r13.textContent).toContain('exit 1');
    expect(screen.getByText(/skipped: the previous run was still going/)).toBeTruthy();
  });
});

describe('RunView', () => {
  it('lights the graph, opens the running step, opens the folder and cancels the run', async () => {
    const bridge = installBridge({ 'automations.getRun': () => runDetail(), 'app.revealPath': () => ({ ok: true }), 'automations.cancelRun': () => ({ ok: true }) });
    render(<RunView projectId="p" s={sessionOf()} detail={automationDetail()} runId="r14" />);
    expect(await screen.findByRole('heading', { name: 'Run #14' })).toBeTruthy();
    expect(screen.getByText('running · Summarise')).toBeTruthy();
    expect(screen.getByTestId('node-fetch').className).toContain('run-ok');
    expect(screen.getByTestId('node-fetch').textContent).toContain('✓ 12s');
    expect(screen.getByTestId('node-sum').className).toContain('run-run');
    expect(screen.getByTestId('node-ok').className).toContain('run-pending');
    expect(screen.getByRole('complementary', { name: 'Summarise' })).toBeTruthy();
    fireEvent.click(screen.getByTestId('node-start'));
    const panel = screen.getByRole('complementary', { name: 'Run #14' });
    expect(within(panel).getByText('robots')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Open folder' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'app.revealPath')?.input).toEqual({ runId: 'r14' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel run…' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Cancel run #14?' })).getByRole('button', { name: 'Cancel run' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.cancelRun')?.input).toEqual({ runId: 'r14' }));
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm vitest run packages/ui-core/src/automation-run-graph.test.ts apps/desktop/src/renderer/automations/runs`
Expected: FAIL: the modules do not exist.

- [ ] **Step 3: Write `automation-run-graph.ts`**

Create `packages/ui-core/src/automation-run-graph.ts`:

```ts
import { summarizeToolArgs, type AutomationDefinition, type RunDetail, type StepRunInfo, type StoredEvent } from '@desk/protocol';
import type { GraphRun, NodeRunState } from './automation-graph';
import { clock, duration } from './format';

const firstLine = (s: string) => s.split('\n')[0]!.trim();
const clipTo = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const span = (a: string | null, b: string | null, now: number) => (a ? duration((b ? Date.parse(b) : now) - Date.parse(a)) : '');

/** A step's look in a run (spec §8.3): its tone, a short badge and a one-line detail. `activity` is a running agent's tool call. */
export function stepLook(row: StepRunInfo | undefined, now: number, activity?: string | null): NodeRunState {
  if (!row || row.attempt === 0 || row.status === 'pending') return { tone: 'pending', badge: 'pending', detail: null };
  switch (row.status) {
    case 'running':
      return { tone: 'run', badge: `● ${span(row.started_at, null, now)}`.trim(), detail: activity ?? (row.attempt > 1 ? `attempt ${row.attempt}` : 'running') };
    case 'waiting':
      return {
        tone: 'wait',
        badge: 'waiting',
        detail: row.question ? 'waiting on you' : row.gate ? `approve ${row.gate.tool}` : row.resume_at ? `until ${clock(row.resume_at)}` : row.child_run_id ? 'running the other automation' : row.agent_id ? 'waiting on an approval' : 'waiting',
      };
    case 'succeeded':
      return { tone: 'ok', badge: `✓ ${span(row.started_at, row.finished_at, now)}`.trim(), detail: row.route ? `route ${row.route}` : row.summary ? clipTo(firstLine(row.summary), 60) : null };
    case 'rejected':
      return { tone: 'ok', badge: '✓ rejected', detail: 'route rejected' };
    case 'failed':
      return { tone: 'fail', badge: '✕ failed', detail: row.error ? clipTo(firstLine(row.error), 60) : null };
    case 'skipped':
      return { tone: 'skipped', badge: 'skipped', detail: null };
    default:
      return { tone: 'idle', badge: 'cancelled', detail: null };
  }
}

const REACHED = new Set(['running', 'waiting', 'succeeded', 'failed', 'rejected']);

/**
 * The edges that fired, by index: the source resolved the way the edge waits for (spec §3.2) and its target was reached.
 * `when` cannot be evaluated here; with `join: any` a target reached through another edge can light one whose `when` was false.
 */
export function firedEdges(def: AutomationDefinition, steps: StepRunInfo[]): Set<number> {
  const byId = new Map(steps.map((s) => [s.step_id, s]));
  const out = new Set<number>();
  def.edges.forEach((e, i) => {
    const from = byId.get(e.from);
    const to = byId.get(e.to);
    if (!from || !to || !(REACHED.has(to.status) || to.started_at)) return;
    const ok = e.route ? from.route === e.route : from.status === 'succeeded';
    if (ok) out.add(i);
  });
  return out;
}

/** Every step's look and the fired edges, for the run canvas. `activity` maps step ids to their agent's tool call. */
export function runGraph(run: RunDetail, now: number, activity: Record<string, string | null>): GraphRun {
  const byId = new Map(run.steps.map((s) => [s.step_id, s]));
  return {
    steps: Object.fromEntries(run.definition.steps.map((s) => [s.id, stepLook(byId.get(s.id), now, activity[s.id] ?? null)])),
    fired: firedEdges(run.definition, run.steps),
  };
}

/** A running agent's current tool call ("read_file · acme.md"), or null between calls. */
export function agentActivity(events: readonly StoredEvent[], agentId: string): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i]!;
    if (e.agent_id !== agentId) continue;
    if (e.type === 'tool.result') return null;
    if (e.type === 'tool.call') return `${e.payload.name} · ${summarizeToolArgs(e.payload.arguments)}`;
  }
  return null;
}

/** The step a run view opens on: one waiting on the user, else one running, else the one that failed. */
export function focusStep(run: RunDetail): string | null {
  const find = (pred: (s: StepRunInfo) => boolean) => run.steps.find(pred)?.step_id ?? null;
  return find((s) => s.status === 'waiting' && (s.question !== null || s.gate !== null)) ?? find((s) => s.status === 'running' || s.status === 'waiting') ?? find((s) => s.status === 'failed');
}

/** Ask Desk to fix (spec §8.3): "Please fix <automation>: run #<n> failed at <step>. <error first line>". */
export function askDeskText(run: Pick<RunDetail, 'automation_name' | 'number'>, stepTitle: string, error: string): string {
  return `Please fix ${run.automation_name}: run #${run.number} failed at ${stepTitle}. ${firstLine(error)}`;
}

/** Stop step (spec §8.3) is offered for an unfinished agent step of a run still going. */
export const canStopStep = (run: Pick<RunDetail, 'status'>, row: StepRunInfo): boolean => (run.status === 'running' || run.status === 'waiting') && row.agent_id !== null && (row.status === 'running' || row.status === 'waiting');

/** A path inside the run folder (an Ask me step's shown files are absolute) as the run files API names it, or null. */
export function relRunPath(path: string, runId: string): string | null {
  const marker = `/automation-runs/${runId}/`;
  const i = path.indexOf(marker);
  return i < 0 ? null : path.slice(i + marker.length);
}
```

`summarizeToolArgs` is the same helper the project reducer uses for a thread's activity line (`packages/protocol/src/format.ts`).

In `packages/ui-core/src/index.ts`, add `export * from './automation-run-graph';` among the automation modules (alphabetical: after `./automation-live`).

- [ ] **Step 4: Write the list and the run view**

Create `apps/desktop/src/renderer/automations/runs/RunsList.tsx`:

```tsx
import { useState } from 'react';
import type { AutomationDetail } from '@desk/protocol';
import { dayTime, href, runStatusText, runTook, skippedText, triggerText } from '@desk/ui-core';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { useNow } from '../../state/now';
import type { SessionState } from '../../state/session';
import { useRuns } from '../data';

/** The Runs tab (spec §8.3): newest first, with skipped schedule times as lines of their own. */
export function RunsList(o: { projectId: string; s: SessionState; detail: AutomationDetail }) {
  const [limit, setLimit] = useState(50);
  const live = useRuns(o.s, o.detail.id, limit);
  const now = useNow();
  const entries = live.value ?? [];
  if (live.status === 'loading' && !live.value) return <p className="muted auto-loading">Loading…</p>;
  if (!entries.length) return <EmptyState title="No runs yet">Run now or Test starts one; schedules start them while it is on.</EmptyState>;
  return (
    <div className="auto-runs">
      <table className="auto-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Started</th>
            <th>Trigger</th>
            <th>Status</th>
            <th>Took</th>
            <th>Summary</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => {
            if (e.kind === 'skipped') {
              return (
                <tr key={`skip-${e.due_at}-${e.trigger_index}`} className="auto-skipped">
                  <td />
                  <td colSpan={5} className="muted small">{`${dayTime(e.due_at, now)} · ${skippedText(e)}`}</td>
                </tr>
              );
            }
            const r = e.run;
            const st = runStatusText(r);
            return (
              <tr key={r.id}>
                <td>
                  <a className="mono" href={href({ name: 'project', id: o.projectId, tab: 'automations', automationId: o.detail.id, view: 'runs', runId: r.id })}>{`#${r.number}`}</a>
                </td>
                <td>{dayTime(r.started_at, now)}</td>
                <td>{triggerText(r)}</td>
                <td>
                  <span className={`auto-last tone-${st.tone}`}>
                    <span className="dot" aria-hidden="true" />
                    {st.text}
                  </span>
                </td>
                <td>{runTook(r, now)}</td>
                <td className="auto-summary">{r.summary ?? r.reason ?? ''}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {entries.length >= limit ? (
        <div>
          <Button size="sm" onClick={() => setLimit((l) => l + 50)}>
            Show older runs
          </Button>
        </div>
      ) : null}
    </div>
  );
}
```

Create `apps/desktop/src/renderer/automations/runs/RunView.tsx`:

```tsx
import { useMemo, useState } from 'react';
import type { AutomationDetail, RunDetail, StepRunInfo } from '@desk/protocol';
import { agentActivity, dayTime, focusStep, href, runGraph, runStatusText, runTook, stepLook, triggerText, type GraphSelection } from '@desk/ui-core';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { EmptyState } from '../../components/EmptyState';
import { toastError } from '../../components/Toast';
import { useNow } from '../../state/now';
import type { SessionState } from '../../state/session';
import { useRun } from '../data';
import { GraphCanvas } from '../design/GraphCanvas';

/** One run (spec §8.3, view A): the graph lit with each step's state, the header's Open folder and Cancel run, and a side panel. */
export function RunView(o: { projectId: string; s: SessionState; detail: AutomationDetail; runId: string }) {
  const live = useRun(o.s, o.runId);
  const now = useNow();
  const [picked, setPicked] = useState<GraphSelection | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const run = live.value;
  const activity = useMemo(() => {
    const out: Record<string, string | null> = {};
    for (const st of run?.steps ?? []) if (st.agent_id && st.status === 'running') out[st.step_id] = agentActivity(o.s.events, st.agent_id);
    return out;
  }, [run?.steps, o.s.events]);
  const graph = useMemo(() => (run ? runGraph(run, now, activity) : undefined), [run, now, activity]);
  const back = href({ name: 'project', id: o.projectId, tab: 'automations', automationId: o.detail.id, view: 'runs' });
  if (live.status === 'missing') {
    return (
      <EmptyState title="This run is gone" action={<a href={back}>All runs</a>}>
        Desk keeps each automation's last 20 runs or 30 days of runs.
      </EmptyState>
    );
  }
  if (!run || !graph) return <p className="muted auto-loading">Loading…</p>;
  const focus = focusStep(run);
  const sel: GraphSelection = picked ?? (focus ? { kind: 'step', id: focus } : { kind: 'start' });
  const st = runStatusText(run);
  const going = run.status === 'running' || run.status === 'waiting';
  const reveal = () => void call('app.revealPath', { runId: run.id }).catch(toastError);
  const cancel = async () => {
    setCancelling(false);
    try {
      await call('automations.cancelRun', { runId: run.id });
    } catch (err) {
      toastError(err);
    }
  };
  return (
    <div className="auto-run">
      <div className="auto-run-main">
        <header className="auto-run-head">
          <a className="muted small" href={back}>
            Runs ›
          </a>
          <h2>{`Run #${run.number}`}</h2>
          <span className="muted small">{`${triggerText(run)} · ${dayTime(run.started_at, now)} · ${runTook(run, now)}`}</span>
          <span className={`auto-last tone-${st.tone}`}>
            <span className="dot" aria-hidden="true" />
            {st.text}
          </span>
          {run.version !== o.detail.version ? <span className="auto-badge">{`v${run.version}`}</span> : null}
          <span className="grow" />
          <Button size="sm" onClick={reveal}>
            Open folder
          </Button>
          {going ? (
            <Button size="sm" variant="danger" onClick={() => setCancelling(true)}>
              Cancel run…
            </Button>
          ) : null}
        </header>
        <GraphCanvas def={run.definition} layout={o.detail.layout} startLabel={triggerText(run)} selection={sel} onSelect={(x) => setPicked(x.kind === 'none' ? { kind: 'start' } : x)} run={graph} editable={false} />
      </div>
      {sel.kind === 'step' ? <StepSide run={run} stepId={sel.id} now={now} /> : <RunSide run={run} />}
      {cancelling ? (
        <ConfirmDialog title={`Cancel run #${run.number}?`} confirmLabel="Cancel run" danger onConfirm={() => void cancel()} onCancel={() => setCancelling(false)}>
          Its running steps stop: agents are stopped and scripts are killed. Steps that finished keep their results.
        </ConfirmDialog>
      ) : null}
    </div>
  );
}

/** The run itself: its inputs, summary or reason. */
function RunSide({ run }: { run: RunDetail }) {
  return (
    <aside className="auto-panel" aria-label={`Run #${run.number}`}>
      <p className="eyebrow">Run</p>
      <h2>{run.automation_title}</h2>
      {run.summary ? <p>{run.summary}</p> : null}
      {run.reason ? <p className="auto-issues">{run.reason}</p> : null}
      <h3 className="auto-sub">Inputs</h3>
      {Object.keys(run.inputs).length ? (
        <dl className="auto-facts">
          {Object.entries(run.inputs).map(([k, v]) => (
            <div key={k}>
              <dt className="mono">{k}</dt>
              <dd>{String(v)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="muted small">None.</p>
      )}
    </aside>
  );
}

/** A step, briefly (Task 15 replaces this with the full step panel). */
function StepSide({ run, stepId, now }: { run: RunDetail; stepId: string; now: number }) {
  const step = run.definition.steps.find((s) => s.id === stepId);
  const row: StepRunInfo | undefined = run.steps.find((s) => s.step_id === stepId);
  const look = stepLook(row, now);
  return (
    <aside className="auto-panel" aria-label={step?.title ?? stepId}>
      <p className="eyebrow">{look.badge}</p>
      <h2>{step?.title ?? stepId}</h2>
      {row?.summary ? <p>{row.summary}</p> : null}
      {row?.error ? <p className="auto-issues">{row.error}</p> : null}
    </aside>
  );
}
```

In `apps/desktop/src/renderer/automations/AutomationsScreen.tsx`, import both and change the `runs` branch of `AutomationBody` to:

```tsx
    case 'runs':
      return o.runId ? <RunView key={o.runId} projectId={o.projectId} s={o.s} detail={o.detail} runId={o.runId} /> : <RunsList projectId={o.projectId} s={o.s} detail={o.detail} />;
```

Append to `packages/ui-styles/src/automations.css`:

```css
/* Runs (view A) */
.auto-runs {
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.auto-skipped td {
  padding-top: 4px;
  padding-bottom: 4px;
  background: var(--sunken);
}
.auto-summary {
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}
.auto-run {
  display: flex;
  height: 100%;
  min-height: 0;
}
.auto-run-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.auto-run-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--rule-soft);
  background: var(--paper);
}
.auto-run-head h2 {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 17px;
  font-weight: 500;
  color: var(--ink);
}
.auto-panel {
  width: 380px;
  flex: none;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px 24px;
  border-left: 1px solid var(--rule-soft);
  background: var(--paper);
  overflow: auto;
}
.auto-panel h2 {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 17px;
  font-weight: 500;
  color: var(--ink);
}
.auto-facts {
  margin: 0;
  display: grid;
  gap: 4px;
  font-size: 12.5px;
}
.auto-facts div {
  display: flex;
  gap: 8px;
}
.auto-facts dt {
  min-width: 90px;
  color: var(--muted);
}
.auto-facts dd {
  margin: 0;
  color: var(--ink);
  word-break: break-word;
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pnpm vitest run packages/ui-core apps/desktop/src/renderer/automations packages/ui-styles`
Expected: PASS.

Run: `pnpm typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
pnpm test
git add packages/ui-core/src/automation-run-graph.ts packages/ui-core/src/automation-run-graph.test.ts packages/ui-core/src/index.ts apps/desktop/src/renderer/test/session.ts apps/desktop/src/renderer/automations/runs apps/desktop/src/renderer/automations/AutomationsScreen.tsx packages/ui-styles/src/automations.css
git commit -m "feat(desktop): automation runs list and the live run view

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: The step panel: live output, answers, results, files and fixes

The run view's side panel for the selected step (spec §8.3):
- **While it runs:** an agent step's live output, tokens, *Open transcript*, *Stop step*, and its pending approvals. A script step's log is polled every 2 s.
- **Waiting on the user:** an Ask me step's question and files with Approve / Reject and a note; a script gate with Approve (and remember) / Reject.
- **Once done:** summary, route, outputs, files (previewed with the app's file viewer) and log.
- **Failed:** the error, and *Ask Desk to fix*.

**Files:**
- Modify: `packages/ui-core/src/automation-run-graph.ts`, `packages/ui-core/src/automation-run-graph.test.ts` (`agentTokens`, `outputText`)
- Create: `apps/desktop/src/renderer/automations/runs/StepPanel.tsx`, `StepTranscript.tsx`, `RunFileList.tsx`
- Modify: `apps/desktop/src/renderer/automations/runs/RunView.tsx` (use `StepPanel`)
- Modify: `packages/ui-styles/src/automations.css`
- Test: `apps/desktop/src/renderer/automations/runs/StepPanel.test.tsx`

**Interfaces:**
- Consumes:
  - `stepLook`, `canStopStep`, `askDeskText`, `relRunPath`, `agentActivity` (Task 14); `STEP_KIND_LABEL` (Task 3); `tokens`, `clock`, `policyReason`, `narrate`, `sentCalls` (existing `@desk/ui-core`);
  - `describeArgs` (`attention/Inspector.tsx`); `Transcript` (`threads/Transcript.tsx`); `FileViewer`, `CodeBlock`, `SafeMarkdown`;
  - `call('automations.answer' | 'automations.stopStep' | 'automations.log' | 'automations.files' | 'automations.file' | 'approvals.resolve' | 'projects.send' | 'app.revealPath')`.
- Produces:
  - `agentTokens(events, agentId): number` and `outputText(value): string` (ui-core);
  - `StepPanel({ projectId, s, run, stepId, grantsSuspended })`, `StepTranscript({ projectId, s, agentId, title, onClose })`, `RunFileList({ runId, files })`.

- [ ] **Step 1: Write the failing tests**

Add to `packages/ui-core/src/automation-run-graph.test.ts` (and add `agentTokens, outputText` to its import):

```ts
describe('step results', () => {
  it('counts an agent’s tokens and words outputs', () => {
    const events = [
      ev(1, 'usage', { run_id: 'x', model: 'm', prompt_tokens: 1000, completion_tokens: 200, estimated: false }, { agent: 'ag1' }),
      ev(2, 'usage', { run_id: 'y', model: 'm', prompt_tokens: 250, completion_tokens: 50, estimated: false }, { agent: 'ag1' }),
      ev(3, 'usage', { run_id: 'z', model: 'm', prompt_tokens: 9, completion_tokens: 9, estimated: false }, { agent: 'other' }),
    ];
    expect(agentTokens(events, 'ag1')).toBe(1500);
    expect(outputText(['a.com', 'b.com'])).toBe('a.com\nb.com');
    expect(outputText(3)).toBe('3');
    expect(outputText(null)).toBe('—');
    expect(outputText(false)).toBe('false');
  });
});
```

Create `apps/desktop/src/renderer/automations/runs/StepPanel.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { ProjectState } from '@desk/client';
import { ev } from '@desk/client/testing';
import { runDetail, stepRun } from '@desk/ui-core/testing';
import { installBridge } from '../../test/bridge';
import { sessionOf } from '../../test/session';
import { StepPanel } from './StepPanel';

afterEach(cleanup);

const waitingAsk = () =>
  runDetail({
    status: 'waiting',
    steps: [
      ...runDetail().steps.slice(0, 1),
      stepRun('sum', { status: 'succeeded', summary: 'Robots everywhere', outputs: { headline: 'Robots' } }),
      stepRun('ok', { status: 'waiting', question: { text: 'Publish "Robots"?', files: ['/d/automation-runs/r14/steps/sum/digest.md'], approve_label: 'Publish' } }),
    ],
  });

describe('StepPanel', () => {
  it('answers an Ask me step with a note, after previewing its file', async () => {
    const bridge = installBridge({ 'automations.file': () => new TextEncoder().encode('# Digest\n\nRobots.'), 'automations.answer': () => ({ ok: true }), 'automations.files': () => [] });
    render(<StepPanel projectId="p" s={sessionOf()} run={waitingAsk()} stepId="ok" grantsSuspended={false} />);
    const answer = screen.getByRole('region', { name: 'Your answer' });
    expect(within(answer).getByText('Publish "Robots"?')).toBeTruthy();
    fireEvent.click(within(answer).getByRole('button', { name: 'digest.md' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.file')?.input).toEqual({ runId: 'r14', path: 'steps/sum/digest.md' }));
    fireEvent.change(within(answer).getByLabelText('Note (optional)'), { target: { value: 'ship it' } });
    fireEvent.click(within(answer).getByRole('button', { name: 'Publish' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.answer')?.input).toEqual({ runId: 'r14', stepId: 'ok', req: { decision: 'approve', note: 'ship it' } }));
  });

  it('offers remember on a script gate, unless grants are suspended', async () => {
    const gated = runDetail({ status: 'waiting', steps: [stepRun('fetch', { status: 'waiting', gate: { tool: 'skill_run', subject: 'digest/fetch.py robots', reason: 'ask: skill_run' } }), ...runDetail().steps.slice(1)] });
    const bridge = installBridge({ 'automations.answer': () => ({ ok: true }), 'automations.files': () => [], 'automations.log': () => '' });
    const { unmount } = render(<StepPanel projectId="p" s={sessionOf()} run={gated} stepId="fetch" grantsSuspended={false} />);
    expect(screen.getByText('digest/fetch.py robots')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Approve and remember' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.answer')?.input).toEqual({ runId: 'r14', stepId: 'fetch', req: { decision: 'approve', remember: true } }));
    unmount();
    render(<StepPanel projectId="p" s={sessionOf()} run={gated} stepId="fetch" grantsSuspended />);
    expect(screen.queryByRole('button', { name: 'Approve and remember' })).toBeNull();
    expect(screen.getByText(/grants are suspended/i)).toBeTruthy();
  });

  it('shows a running agent step: live output, tokens, its approvals (with remember), transcript and Stop step', async () => {
    const bridge = installBridge({ 'approvals.resolve': () => ({ ok: true }), 'automations.stopStep': () => ({ ok: true }), 'automations.files': () => [] });
    const events = [
      ev(1, 'assistant.message', { run_id: 'x', content: 'Reading acme.md now.', tool_calls: [] }, { agent: 'ag1' }),
      ev(2, 'usage', { run_id: 'x', model: 'm', prompt_tokens: 1200, completion_tokens: 300, estimated: false }, { agent: 'ag1' }),
    ];
    const project = { approvals: [{ id: 'ap1', project_id: 'p', agent_id: 'ag1', run_id: 'x', tool_call_id: 't', tool: 'bash', arguments: '{"command":"ls"}', reason: 'ask', delegate_to_desk: false, status: 'pending', resolved_by: null, note: null, created_at: 't', resolved_at: null }] } as unknown as ProjectState;
    render(<StepPanel projectId="p" s={sessionOf(events, { project })} run={runDetail()} stepId="sum" grantsSuspended={false} />);
    expect(screen.getByText('Reading acme.md now.')).toBeTruthy();
    expect(screen.getByText('1.5k tokens')).toBeTruthy();
    const approval = screen.getByRole('region', { name: 'Approval: bash' });
    expect(within(approval).getByText('ls')).toBeTruthy();
    fireEvent.click(within(approval).getByRole('button', { name: 'Approve and remember' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'approvals.resolve')?.input).toEqual({ id: 'ap1', decision: 'approved', remember: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Open transcript' }));
    expect(screen.getByRole('dialog', { name: 'Summarise · transcript' })).toBeTruthy();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Summarise · transcript' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Stop step…' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Stop this step?' })).getByRole('button', { name: 'Stop step' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.stopStep')?.input).toEqual({ runId: 'r14', stepId: 'sum' }));
  });

  it('shows a finished script step’s summary, route, outputs, log and files', async () => {
    installBridge({
      'automations.log': () => 'fetched 5 pages\n',
      'automations.files': () => [{ name: 'out.json', path: 'steps/fetch/out.json', type: 'file', size: 10 }],
      'automations.file': () => new TextEncoder().encode('{}'),
    });
    const run = runDetail({ steps: [stepRun('fetch', { status: 'succeeded', route: 'changed', summary: '3 of 5 sites differ', outputs: { count: 3, sites: ['a.com', 'b.com'] }, started_at: '2026-09-28T06:00:00.000Z', finished_at: '2026-09-28T06:00:12.000Z' }), ...runDetail().steps.slice(1)] });
    render(<StepPanel projectId="p" s={sessionOf()} run={run} stepId="fetch" grantsSuspended={false} />);
    expect(screen.getByText('3 of 5 sites differ')).toBeTruthy();
    expect(screen.getByText('changed')).toBeTruthy();
    expect(screen.getByText('count').nextSibling?.textContent).toBe('3');
    expect(screen.getByText('sites').nextSibling?.textContent).toBe('a.com\nb.com');
    expect(await screen.findByText(/fetched 5 pages/)).toBeTruthy();
    expect(await screen.findByRole('button', { name: 'out.json' })).toBeTruthy();
  });

  it('asks Desk to fix a failed step', async () => {
    const bridge = installBridge({ 'projects.send': () => ({ ok: true }), 'automations.files': () => [], 'automations.log': () => '' });
    const run = runDetail({ status: 'failed', steps: [stepRun('fetch', { status: 'failed', error: 'exit 1\nTraceback' }), ...runDetail().steps.slice(1)] });
    render(<StepPanel projectId="p" s={sessionOf()} run={run} stepId="fetch" grantsSuspended={false} />);
    expect(screen.getByText(/exit 1/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Ask Desk to fix' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.send')?.input).toEqual({ id: 'p', text: 'Please fix digest: run #14 failed at Fetch pages. exit 1' }));
  });
});
```

`Sheet` has no close button: Escape or a click on its backdrop closes it, so the test presses Escape.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm vitest run packages/ui-core/src/automation-run-graph.test.ts apps/desktop/src/renderer/automations/runs/StepPanel.test.tsx`
Expected: FAIL: `agentTokens`, `outputText` and `./StepPanel` do not exist.

- [ ] **Step 3: Add the helpers to `automation-run-graph.ts`**

Add to `packages/ui-core/src/automation-run-graph.ts` (and `type OutputValue` to its protocol import):

```ts
/** The tokens an agent has used so far (prompt and completion, from its usage events). */
export function agentTokens(events: readonly StoredEvent[], agentId: string): number {
  let n = 0;
  for (const e of events) if (e.type === 'usage' && e.agent_id === agentId) n += e.payload.prompt_tokens + e.payload.completion_tokens;
  return n;
}

/** An output value as text: a list one item per line (as templates render it), nothing as "—". */
export function outputText(v: OutputValue): string {
  if (v === null) return '—';
  return Array.isArray(v) ? v.map(String).join('\n') : String(v);
}
```

- [ ] **Step 4: Write the panel and its parts**

Create `apps/desktop/src/renderer/automations/runs/RunFileList.tsx`:

```tsx
import { useState } from 'react';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { FileViewer } from '../../components/FileViewer';
import { toastError } from '../../components/Toast';

/** Files of a run folder as buttons; one opens in the app's file viewer below them. */
export function RunFileList({ runId, files }: { runId: string; files: Array<{ path: string; label: string }> }) {
  const [open, setOpen] = useState<{ path: string; data: Uint8Array } | null>(null);
  const show = async (path: string) => {
    try {
      setOpen({ path, data: await call('automations.file', { runId, path }) });
    } catch (err) {
      toastError(err);
    }
  };
  return (
    <div className="auto-files">
      <ul className="auto-plain">
        {files.map((f) => (
          <li key={f.path}>
            <button type="button" className="link mono" onClick={() => void show(f.path)}>
              {f.label}
            </button>
          </li>
        ))}
      </ul>
      {open ? (
        <FileViewer
          path={open.path}
          data={open.data}
          actions={
            <Button size="sm" variant="ghost" onClick={() => setOpen(null)}>
              Close
            </Button>
          }
        />
      ) : null}
    </div>
  );
}
```

Create `apps/desktop/src/renderer/automations/runs/StepTranscript.tsx`:

```tsx
import { useMemo, useState } from 'react';
import { narrate, sentCalls } from '@desk/ui-core';
import { Sheet } from '../../components/Sheet';
import { useTranscript, type SessionState } from '../../state/session';
import { Transcript, type Depth } from '../../threads/Transcript';

/** A step agent's transcript (the Threads tab's view), which only the run view opens: step agents are not threads. */
export function StepTranscript(o: { projectId: string; s: SessionState; agentId: string; title: string; onClose(): void }) {
  const transcript = useTranscript(o.s, o.projectId, o.agentId);
  const sent = useMemo(() => sentCalls(o.s.messages, o.agentId), [o.s.messages, o.agentId]);
  const rows = useMemo(() => narrate(transcript.entries, sent), [transcript.entries, sent]);
  const [selected, setSelected] = useState<number | null>(null);
  const [depth, setDepth] = useState<Depth>('narrative');
  return (
    <Sheet title={`${o.title} · transcript`} onClose={o.onClose} width={760}>
      <div className="auto-transcript">
        <Transcript
          projectId={o.projectId}
          threadId={o.agentId}
          rows={rows}
          entries={transcript.entries}
          reviewRounds={0}
          messages={o.s.messages}
          sent={sent}
          onPair={() => {}}
          selected={selected}
          onSelect={setSelected}
          depth={depth}
          onDepth={setDepth}
          actions={null}
          composer={{ kind: 'off', hint: 'A step agent takes no messages. Answer its approvals here, or stop the step.' }}
        />
      </div>
    </Sheet>
  );
}
```

Create `apps/desktop/src/renderer/automations/runs/StepPanel.tsx`:

```tsx
import { useEffect, useMemo, useState } from 'react';
import type { ApprovalRow } from '@desk/client';
import type { RunDetail, Step, StepRunInfo, WorkspaceEntry } from '@desk/protocol';
import { agentActivity, agentTokens, askDeskText, canStopStep, clock, outputText, policyReason, relRunPath, STEP_KIND_LABEL, stepLook, tokens } from '@desk/ui-core';
import { describeArgs } from '../../attention/Inspector';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { CodeBlock } from '../../components/CodeBlock';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Field } from '../../components/Field';
import { SafeMarkdown } from '../../components/SafeMarkdown';
import { toast, toastError } from '../../components/Toast';
import { navigate } from '../../router';
import { useNow } from '../../state/now';
import { useTranscript, type SessionState } from '../../state/session';
import { RunFileList } from './RunFileList';
import { StepTranscript } from './StepTranscript';

type Props = { projectId: string; s: SessionState; run: RunDetail; stepId: string; grantsSuspended: boolean };

/** The selected step of a run (spec §8.3): what it is doing, what it needs from you, or what it produced. */
export function StepPanel(o: Props) {
  const now = useNow();
  const step = o.run.definition.steps.find((x) => x.id === o.stepId);
  const row = o.run.steps.find((x) => x.step_id === o.stepId);
  const activity = row?.agent_id && row.status === 'running' ? agentActivity(o.s.events, row.agent_id) : null;
  if (!step) return null;
  const look = stepLook(row, now, activity);
  const done = row?.status === 'succeeded' || row?.status === 'rejected';
  return (
    <aside className="auto-panel" aria-label={step.title}>
      <p className="eyebrow">{`${STEP_KIND_LABEL[step.kind]} · ${look.badge}`}</p>
      <h2>{step.title}</h2>
      {row && row.attempt > 1 ? <p className="muted small">{`Attempt ${row.attempt}`}</p> : null}
      {row?.status === 'waiting' && row.question ? <AskAnswer run={o.run} row={row} /> : null}
      {row?.status === 'waiting' && row.gate ? <GateAnswer run={o.run} row={row} grantsSuspended={o.grantsSuspended} /> : null}
      {row?.agent_id ? <AgentPart {...o} step={step} row={row} /> : null}
      {row?.status === 'waiting' && row.resume_at ? <p>{`Waits until ${clock(row.resume_at)}.`}</p> : null}
      {row?.child_run_id ? <p className="muted small">{`Runs another automation (run ${row.child_run_id}).`}</p> : null}
      {row?.status === 'failed' ? <Failed {...o} step={step} row={row} /> : null}
      {done && row ? <Results row={row} /> : null}
      {step.kind === 'script' && row && row.attempt > 0 ? <ScriptLog runId={o.run.id} stepId={step.id} live={row.status === 'running'} /> : null}
      {row && row.attempt > 0 && row.status !== 'pending' ? <StepFiles runId={o.run.id} stepId={step.id} status={row.status} /> : null}
      {!row || row.attempt === 0 ? <p className="muted">Not reached yet.</p> : null}
      {row?.status === 'skipped' ? <p className="muted">Skipped: none of the edges into it fired.</p> : null}
    </aside>
  );
}

function AskAnswer({ run, row }: { run: RunDetail; row: StepRunInfo }) {
  const q = row.question!;
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const answer = async (decision: 'approve' | 'reject') => {
    setBusy(decision);
    try {
      await call('automations.answer', { runId: run.id, stepId: row.step_id, req: { decision, ...(note.trim() ? { note: note.trim() } : {}) } });
    } catch (err) {
      toastError(err);
      setBusy(null);
    }
  };
  const files = q.files.flatMap((f) => {
    const path = relRunPath(f, run.id);
    return path ? [{ path, label: path.split('/').at(-1)! }] : [];
  });
  return (
    <section className="auto-card" aria-label="Your answer">
      <SafeMarkdown text={q.text} />
      {files.length ? <RunFileList runId={run.id} files={files} /> : null}
      <Field id="answer-note" label="Note (optional)">
        <textarea id="answer-note" className="textarea" rows={2} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
      <div className="auto-inline">
        <Button variant="primary" pending={busy === 'approve'} disabled={busy !== null} onClick={() => void answer('approve')}>
          {q.approve_label ?? 'Approve'}
        </Button>
        <Button pending={busy === 'reject'} disabled={busy !== null} onClick={() => void answer('reject')}>
          {q.reject_label ?? 'Reject'}
        </Button>
      </div>
      <p className="muted small">Rejecting takes the route rejected. Later steps can read your note.</p>
    </section>
  );
}

function GateAnswer({ run, row, grantsSuspended }: { run: RunDetail; row: StepRunInfo; grantsSuspended: boolean }) {
  const gate = row.gate!;
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const answer = async (decision: 'approve' | 'reject', remember = false) => {
    setBusy(remember ? 'remember' : decision);
    try {
      await call('automations.answer', { runId: run.id, stepId: row.step_id, req: { decision, ...(note.trim() ? { note: note.trim() } : {}), ...(remember ? { remember: true } : {}) } });
    } catch (err) {
      toastError(err);
      setBusy(null);
    }
  };
  const why = policyReason(gate.reason);
  return (
    <section className="auto-card" aria-label="Approval">
      <p>{`Its script wants to run ${gate.tool}:`}</p>
      <pre className="command">
        <span className="command-prompt">$ </span>
        {gate.subject}
      </pre>
      <p className="muted small">{why.text}</p>
      <Field id="gate-note" label="Note (optional)">
        <input id="gate-note" className="input" maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
      <div className="auto-inline">
        <Button variant="primary" pending={busy === 'approve'} disabled={busy !== null} onClick={() => void answer('approve')}>
          Approve
        </Button>
        {grantsSuspended ? null : (
          <Button pending={busy === 'remember'} disabled={busy !== null} onClick={() => void answer('approve', true)}>
            Approve and remember
          </Button>
        )}
        <Button pending={busy === 'reject'} disabled={busy !== null} onClick={() => void answer('reject')}>
          Reject
        </Button>
      </div>
      <p className="muted small">
        {grantsSuspended ? 'Its grants are suspended until you keep them, so an approval is for this run only.' : 'Remember adds a grant, so later runs run this script without asking.'}
      </p>
    </section>
  );
}

function AgentApproval({ approval, grantsSuspended }: { approval: ApprovalRow; grantsSuspended: boolean }) {
  const [busy, setBusy] = useState<string | null>(null);
  const args = describeArgs(approval.tool, approval.arguments);
  const resolve = async (decision: 'approved' | 'denied', remember = false) => {
    setBusy(remember ? 'remember' : decision);
    try {
      await call('approvals.resolve', { id: approval.id, decision, ...(remember ? { remember: true } : {}) });
    } catch (err) {
      toastError(err);
      setBusy(null);
    }
  };
  return (
    <section className="auto-card" aria-label={`Approval: ${approval.tool}`}>
      {args.command ? (
        <pre className="command">
          <span className="command-prompt">$ </span>
          {args.command}
        </pre>
      ) : (
        <CodeBlock code={args.pretty} language={approval.tool} />
      )}
      <div className="auto-inline">
        <Button variant="primary" size="sm" pending={busy === 'approved'} disabled={busy !== null} onClick={() => void resolve('approved')}>
          Approve
        </Button>
        {grantsSuspended ? null : (
          <Button size="sm" pending={busy === 'remember'} disabled={busy !== null} onClick={() => void resolve('approved', true)}>
            Approve and remember
          </Button>
        )}
        <Button size="sm" pending={busy === 'denied'} disabled={busy !== null} onClick={() => void resolve('denied')}>
          Deny
        </Button>
      </div>
    </section>
  );
}

function AgentPart(o: Props & { step: Step; row: StepRunInfo }) {
  const agentId = o.row.agent_id!;
  const transcript = useTranscript(o.s, o.projectId, agentId);
  const last = useMemo(() => {
    for (let i = transcript.entries.length - 1; i >= 0; i--) {
      const e = transcript.entries[i]!;
      if (e.kind === 'assistant' && e.text.trim()) return e.text.trim();
    }
    return null;
  }, [transcript.entries]);
  const used = useMemo(() => agentTokens(o.s.events, agentId), [o.s.events, agentId]);
  const approvals = (o.s.project?.approvals ?? []).filter((a) => a.agent_id === agentId);
  const [open, setOpen] = useState(false);
  const [stopping, setStopping] = useState(false);
  const going = o.row.status === 'running' || o.row.status === 'waiting';
  const stop = async () => {
    setStopping(false);
    try {
      await call('automations.stopStep', { runId: o.run.id, stepId: o.row.step_id });
    } catch (err) {
      toastError(err);
    }
  };
  return (
    <>
      {approvals.map((a) => (
        <AgentApproval key={a.id} approval={a} grantsSuspended={o.grantsSuspended} />
      ))}
      {going ? (
        <section aria-label="Live output" className="auto-live">
          {last ? <SafeMarkdown text={last} /> : <p className="muted small">Starting…</p>}
        </section>
      ) : null}
      <p className="muted small">{`${tokens(used)} tokens`}</p>
      <div className="auto-inline">
        <Button size="sm" onClick={() => setOpen(true)}>
          Open transcript
        </Button>
        {canStopStep(o.run, o.row) ? (
          <Button size="sm" variant="danger" onClick={() => setStopping(true)}>
            Stop step…
          </Button>
        ) : null}
      </div>
      {open ? <StepTranscript projectId={o.projectId} s={o.s} agentId={agentId} title={o.step.title} onClose={() => setOpen(false)} /> : null}
      {stopping ? (
        <ConfirmDialog title="Stop this step?" confirmLabel="Stop step" danger onConfirm={() => void stop()} onCancel={() => setStopping(false)}>
          Its agent stops and the step fails. The run then does what the step's "If it fails" says.
        </ConfirmDialog>
      ) : null}
    </>
  );
}

function Failed(o: Props & { step: Step; row: StepRunInfo }) {
  const [sending, setSending] = useState(false);
  const error = o.row.error ?? o.run.reason ?? 'It failed.';
  const ask = async () => {
    setSending(true);
    try {
      await call('projects.send', { id: o.projectId, text: askDeskText(o.run, o.step.title, error) });
      toast({ tone: 'info', message: 'Sent to Desk.', action: { label: 'Open conversation', run: () => navigate({ name: 'project', id: o.projectId, tab: 'conversation' }) } });
    } catch (err) {
      toastError(err);
    } finally {
      setSending(false);
    }
  };
  return (
    <section className="auto-card" aria-label="Error">
      <pre className="auto-error">{error}</pre>
      <div>
        <Button variant="primary" size="sm" pending={sending} onClick={() => void ask()}>
          Ask Desk to fix
        </Button>
      </div>
    </section>
  );
}

function Results({ row }: { row: StepRunInfo }) {
  const outputs = Object.entries(row.outputs);
  return (
    <section aria-label="Results" className="auto-results">
      {row.summary ? <SafeMarkdown text={row.summary} /> : null}
      <dl className="auto-facts">
        {row.route ? (
          <div>
            <dt>route</dt>
            <dd className="mono">{row.route}</dd>
          </div>
        ) : null}
        {row.note ? (
          <div>
            <dt>your note</dt>
            <dd>{row.note}</dd>
          </div>
        ) : null}
        {outputs.map(([k, v]) => (
          <div key={k}>
            <dt className="mono">{k}</dt>
            <dd className="auto-output">{outputText(v)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function ScriptLog({ runId, stepId, live }: { runId: string; stepId: string; live: boolean }) {
  const [log, setLog] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      call('automations.log', { runId, stepId })
        .then((t) => alive && setLog(t))
        .catch(() => alive && setLog(''));
    void load();
    const timer = live ? setInterval(() => void load(), 2000) : undefined;
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [runId, stepId, live]);
  if (log === null) return null;
  return (
    <details className="auto-log" open={live || undefined}>
      <summary>Log</summary>
      {log ? <CodeBlock code={log.split('\n').slice(-400).join('\n')} language="log" /> : <p className="muted small">Nothing logged.</p>}
    </details>
  );
}

function StepFiles({ runId, stepId, status }: { runId: string; stepId: string; status: string }) {
  const [entries, setEntries] = useState<WorkspaceEntry[] | null>(null);
  useEffect(() => {
    let alive = true;
    call('automations.files', { runId, path: `steps/${stepId}` })
      .then((list) => alive && setEntries(list))
      .catch(() => alive && setEntries([]));
    return () => {
      alive = false;
    };
  }, [runId, stepId, status]);
  const files = (entries ?? []).filter((e) => e.type === 'file');
  const reveal = () => void call('app.revealPath', { runId, stepId }).catch(toastError);
  return (
    <section aria-label="Files" className="auto-results">
      <div className="auto-inline">
        <h3 className="auto-sub">Files</h3>
        <span className="grow" />
        <Button size="sm" variant="ghost" onClick={reveal}>
          Open folder
        </Button>
      </div>
      {files.length ? <RunFileList runId={runId} files={files.map((f) => ({ path: f.path, label: f.path.replace(`steps/${stepId}/`, '') }))} /> : entries ? <p className="muted small">No files.</p> : null}
    </section>
  );
}
```

`WorkspaceEntry` paths from `automations.files` are relative to the run folder (`steps/fetch/out.json`). Check one against the daemon route test's expectations (`inputs.json` at the top in Task 2) and adjust the `label` replace if they are relative to the listed folder instead.

In `apps/desktop/src/renderer/automations/runs/RunView.tsx`:
- import `StepPanel` from `./StepPanel`;
- replace `{sel.kind === 'step' ? <StepSide run={run} stepId={sel.id} now={now} /> : <RunSide run={run} />}` with:

```tsx
      {sel.kind === 'step' ? <StepPanel key={sel.id} projectId={o.projectId} s={o.s} run={run} stepId={sel.id} grantsSuspended={o.detail.grants_suspended} /> : <RunSide run={run} />}
```

- delete `StepSide`, and drop `stepLook` and the `StepRunInfo` type from its imports.

Append to `packages/ui-styles/src/automations.css`:

```css
/* The step panel */
.auto-live {
  max-height: 220px;
  overflow: auto;
  padding: 8px 10px;
  border-left: 3px solid var(--run);
  background: var(--card);
  font-size: 13px;
}
.auto-results {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.auto-output {
  white-space: pre-wrap;
}
.auto-error {
  margin: 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--accent-tint);
  color: var(--accent);
  font-family: var(--font-mono);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
.auto-log summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--text);
}
.auto-files {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.auto-transcript {
  height: 70vh;
  display: flex;
  min-height: 0;
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pnpm vitest run packages/ui-core apps/desktop/src/renderer/automations packages/ui-styles`
Expected: PASS.

Run: `pnpm typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
pnpm test
git add packages/ui-core/src/automation-run-graph.ts packages/ui-core/src/automation-run-graph.test.ts apps/desktop/src/renderer/automations/runs packages/ui-styles/src/automations.css
git commit -m "feat(desktop): the run step panel: live output, answers, approvals, results, files, fixes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Grants

The Grants tab (spec §8.4, §5.3):
- **Rules:** each rule says where it came from: remembered at a step of a run (with a link to the run), set when you turned the automation on, or added by you. A rule can be edited, removed or added.
- **Widening:** a web rule on an exact host offers *Widen to <registrable domain>*, which replaces it with the domain and every subdomain (`*.x` does not match `x` itself, so both are needed).
- **Suspended:** a banner shows the change notes of the versions saved since the grants were set and the diff, with **Keep grants**.

Every change saves at once with `automations.setGrants` (reason `edited`), which also ends a suspension (the projection clears `grants_suspended` for any reason but `remembered`).

**Files:**
- Modify: `packages/ui-core/src/automation-grants.ts`, `packages/ui-core/src/automation-grants.test.ts`
- Create: `apps/desktop/src/renderer/automations/grants/GrantsView.tsx`
- Modify: `apps/desktop/src/renderer/automations/AutomationsScreen.tsx` (the `grants` branch)
- Modify: `packages/ui-styles/src/automations.css`
- Test: `apps/desktop/src/renderer/automations/grants/GrantsView.test.tsx`

**Interfaces:**
- Consumes:
  - `grantKey`, `uniqueGrants`, `describeGrant`, `widenDomain`, `grantOrigins`, `type GrantOrigin` (Task 3); `dayTime`, `originText` (Task 3); `diffDefinitions` (Task 12); `DiffView` (Task 12);
  - `call('automations.setGrants' | 'automations.keepGrants' | 'automations.versions' | 'automations.version')`.
- Produces (ui-core, for Plan 21 too):
  - `GRANT_TOOLS: string[]`, `type GrantMatchKind = 'any' | 'domain' | 'command' | 'branch'`, `GRANT_MATCH_LABEL`, `GRANT_VALUE_LABEL`;
  - `type GrantDraft = { tool; action; kind; value }`, `grantDraft(g?)`, `grantFromDraft(d): { grant } | { problem }`;
  - `widenedGrants(g): Grant[] | null`, `replaceGrant(list, i, next): Grant[]`, `grantOriginText(origin, def, now): string | null`.
- Produces (desktop): `GrantsView({ projectId, s, detail, onChange })`.

- [ ] **Step 1: Write the failing tests**

Add to `packages/ui-core/src/automation-grants.test.ts` (extend its import with `grantDraft, grantFromDraft, grantOriginText, replaceGrant, widenedGrants`, and add `import { dayTime } from './automation-format';` and `import { digestDef } from './testing/automations';`):

```ts
describe('editing grants', () => {
  it('turns grants into drafts and drafts back into grants, or says what is wrong', () => {
    expect(grantDraft()).toEqual({ tool: '', action: 'allow', kind: 'any', value: '' });
    expect(grantDraft({ tool: 'bash', match: { command: '^ls$' }, action: 'deny' })).toEqual({ tool: 'bash', action: 'deny', kind: 'command', value: '^ls$' });
    expect(grantFromDraft({ tool: ' bash ', action: 'allow', kind: 'any', value: 'ignored' })).toEqual({ grant: { tool: 'bash', action: 'allow' } });
    expect(grantFromDraft({ tool: 'web_fetch', action: 'allow', kind: 'domain', value: ' News.BBC.co.uk ' })).toEqual({ grant: { tool: 'web_fetch', action: 'allow', match: { domain: 'news.bbc.co.uk' } } });
    expect(grantFromDraft({ tool: '', action: 'allow', kind: 'any', value: '' })).toEqual({ problem: 'Name a tool.' });
    expect(grantFromDraft({ tool: 'bash', action: 'allow', kind: 'command', value: '' })).toEqual({ problem: 'Say which command.' });
    expect(grantFromDraft({ tool: 'bash', action: 'allow', kind: 'command', value: '(' })).toEqual({ problem: 'Not a valid regular expression.' });
    expect(grantFromDraft({ tool: 'git_push', action: 'allow', kind: 'branch', value: 'x'.repeat(201) })).toEqual({ problem: 'At most 200 characters.' });
  });

  it('widens a web grant on an exact host, and replaces grants in place', () => {
    const fetch: Grant = { tool: 'web_fetch', match: { domain: 'news.bbc.co.uk' }, action: 'allow' };
    expect(widenedGrants(fetch)).toEqual([
      { tool: 'web_fetch', match: { domain: 'bbc.co.uk' }, action: 'allow' },
      { tool: 'web_fetch', match: { domain: '*.bbc.co.uk' }, action: 'allow' },
    ]);
    expect(widenedGrants({ ...fetch, match: { domain: '*.bbc.co.uk' } })).toBeNull();
    expect(widenedGrants({ ...fetch, match: { domain: 'localhost' } })).toBeNull();
    expect(widenedGrants({ tool: 'bash', match: { domain: 'a.com' }, action: 'allow' })).toBeNull();
    const bash: Grant = { tool: 'bash', action: 'allow' };
    expect(replaceGrant([fetch, bash], 0, widenedGrants(fetch)!)).toHaveLength(3);
    expect(replaceGrant([fetch, bash], 1, [])).toEqual([fetch]);
    expect(replaceGrant([fetch, bash], 2, [fetch])).toEqual([fetch, bash]);
  });

  it('words where a grant came from', () => {
    const now = Date.parse('2026-09-28T09:00:00.000Z');
    const ts = '2026-09-28T07:00:00.000Z';
    expect(grantOriginText({ kind: 'remembered', run_id: 'r1', step_id: 'sum', ts }, digestDef(), now)).toBe(`Remembered at Summarise · ${dayTime(ts, now)}`);
    expect(grantOriginText({ kind: 'remembered', run_id: 'r1', step_id: 'gone', ts }, digestDef(), now)).toBe(`Remembered at gone · ${dayTime(ts, now)}`);
    expect(grantOriginText({ kind: 'enabled', ts }, digestDef(), now)).toBe(`Set when you turned it on · ${dayTime(ts, now)}`);
    expect(grantOriginText({ kind: 'edited', ts }, digestDef(), now)).toBe(`Added by you · ${dayTime(ts, now)}`);
    expect(grantOriginText(undefined, digestDef(), now)).toBeNull();
  });
});
```

Create `apps/desktop/src/renderer/automations/grants/GrantsView.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Grant } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { automationDetail, digestDef } from '@desk/ui-core/testing';
import { installBridge } from '../../test/bridge';
import { sessionOf } from '../../test/session';
import { GrantsView } from './GrantsView';

afterEach(cleanup);

const fetch: Grant = { tool: 'web_fetch', match: { domain: 'news.bbc.co.uk' }, action: 'allow' };
const bash: Grant = { tool: 'bash', match: { command: '^ls$' }, action: 'allow' };
const events = [
  ev(1, 'automation.grants_set', { automation_id: 'a1', grants: [fetch], reason: 'enabled' }),
  ev(2, 'automation.grants_set', { automation_id: 'a1', grants: [fetch, bash], reason: 'remembered', source: { run_id: 'r1', step_id: 'sum' } }),
];
const setGrantsInput = (calls: Array<{ channel: string; input: unknown }>) => calls.find((c) => c.channel === 'automations.setGrants')?.input;

describe('GrantsView', () => {
  it('lists grants with where each came from, and links a remembered one to its run', () => {
    installBridge();
    render(<GrantsView projectId="p" s={sessionOf(events)} detail={automationDetail({ grants: [fetch, bash] })} onChange={() => {}} />);
    const web = screen.getByRole('listitem', { name: 'Allow web_fetch on news.bbc.co.uk' });
    expect(within(web).getByText(/^Set when you turned it on/)).toBeTruthy();
    const shell = screen.getByRole('listitem', { name: 'Allow bash matching ^ls$' });
    expect(within(shell).getByText(/^Remembered at Summarise/)).toBeTruthy();
    expect(within(shell).getByRole('link', { name: 'Open run' }).getAttribute('href')).toBe('#/p/p/automations/a1/runs/r1');
    expect(within(shell).queryByRole('button', { name: /Widen/ })).toBeNull();
  });

  it('widens a web grant, removes one and saves each change as edited', async () => {
    const onChange = vi.fn();
    const bridge = installBridge({ 'automations.setGrants': (i: { grants: Grant[] }) => automationDetail({ grants: i.grants }) });
    const { rerender } = render(<GrantsView projectId="p" s={sessionOf(events)} detail={automationDetail({ grants: [fetch, bash] })} onChange={onChange} />);
    fireEvent.click(within(screen.getByRole('listitem', { name: 'Allow web_fetch on news.bbc.co.uk' })).getByRole('button', { name: 'Widen to bbc.co.uk' }));
    await waitFor(() =>
      expect(setGrantsInput(bridge.calls)).toEqual({
        id: 'a1',
        grants: [
          { tool: 'web_fetch', match: { domain: 'bbc.co.uk' }, action: 'allow' },
          { tool: 'web_fetch', match: { domain: '*.bbc.co.uk' }, action: 'allow' },
          bash,
        ],
        reason: 'edited',
      }),
    );
    expect(onChange).toHaveBeenCalledTimes(1);
    bridge.calls.length = 0;
    rerender(<GrantsView projectId="p" s={sessionOf(events)} detail={automationDetail({ grants: [fetch, bash] })} onChange={onChange} />);
    fireEvent.click(within(screen.getByRole('listitem', { name: 'Allow bash matching ^ls$' })).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(setGrantsInput(bridge.calls)).toEqual({ id: 'a1', grants: [fetch], reason: 'edited' }));
  });

  it('adds a grant after checking it, and edits one', async () => {
    const bridge = installBridge({ 'automations.setGrants': (i: { grants: Grant[] }) => automationDetail({ grants: i.grants }) });
    render(<GrantsView projectId="p" s={sessionOf(events)} detail={automationDetail({ grants: [fetch] })} onChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Add grant' }));
    const form = screen.getByRole('form', { name: 'New grant' });
    fireEvent.change(within(form).getByLabelText('Tool'), { target: { value: 'skill_run' } });
    fireEvent.change(within(form).getByLabelText('Matches'), { target: { value: 'command' } });
    fireEvent.change(within(form).getByLabelText('Command (a regular expression)'), { target: { value: '(' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save grant' }));
    expect(within(form).getByRole('alert').textContent).toBe('Not a valid regular expression.');
    expect(setGrantsInput(bridge.calls)).toBeUndefined();
    fireEvent.change(within(form).getByLabelText('Command (a regular expression)'), { target: { value: '^digest/fetch\\.py(\\s|$)' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save grant' }));
    await waitFor(() =>
      expect(setGrantsInput(bridge.calls)).toEqual({ id: 'a1', grants: [fetch, { tool: 'skill_run', action: 'allow', match: { command: '^digest/fetch\\.py(\\s|$)' } }], reason: 'edited' }),
    );
    bridge.calls.length = 0;
    fireEvent.click(within(screen.getByRole('listitem', { name: 'Allow web_fetch on news.bbc.co.uk' })).getByRole('button', { name: 'Edit' }));
    const edit = screen.getByRole('form', { name: 'Edit grant' });
    expect((within(edit).getByLabelText('Domain (a host, or *.example.com)') as HTMLInputElement).value).toBe('news.bbc.co.uk');
    fireEvent.change(within(edit).getByLabelText('Action'), { target: { value: 'deny' } });
    fireEvent.click(within(edit).getByRole('button', { name: 'Save grant' }));
    await waitFor(() => expect(setGrantsInput(bridge.calls)).toEqual({ id: 'a1', grants: [{ ...fetch, action: 'deny' }], reason: 'edited' }));
  });

  it('shows the changes since the grants were set while they are suspended, and keeps them', async () => {
    const before = digestDef();
    before.steps = before.steps.filter((s) => s.id !== 'ok');
    before.edges = before.edges.filter((e) => e.to !== 'ok');
    const onChange = vi.fn();
    const bridge = installBridge({
      'automations.versions': () => [
        { version: 7, origin: 'agent:d1', via: 'tool', change_note: 'Asks you before publishing', created_at: '2026-09-27T10:00:00.000Z', tested: false },
        { version: 6, origin: 'user', via: 'editor', change_note: '', created_at: '2026-09-26T10:00:00.000Z', tested: true },
      ],
      'automations.version': () => ({ version: 6, definition: before, origin: 'user', change_note: '', via: 'editor', created_at: '2026-09-26T10:00:00.000Z' }),
      'automations.keepGrants': () => automationDetail({ grants: [fetch], grants_suspended: false, grants_set_version: 7 }),
    });
    render(<GrantsView projectId="p" s={sessionOf(events)} detail={automationDetail({ grants: [fetch], grants_suspended: true, grants_set_version: 6 })} onChange={onChange} />);
    const banner = screen.getByRole('region', { name: 'Grants suspended' });
    expect(await within(banner).findByText('Asks you before publishing')).toBeTruthy();
    const changes = within(banner).getByRole('region', { name: 'What changed since v6' });
    expect(await within(changes).findByText('Publish?')).toBeTruthy();
    fireEvent.click(within(banner).getByRole('button', { name: 'Keep grants' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.keepGrants')?.input).toEqual({ id: 'a1' }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ grants_suspended: false })));
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm vitest run packages/ui-core/src/automation-grants.test.ts apps/desktop/src/renderer/automations/grants`
Expected: FAIL: `grantDraft` and the other new helpers are not exported, and `./GrantsView` does not exist.

- [ ] **Step 3: Add the editing helpers to `automation-grants.ts`**

In `packages/ui-core/src/automation-grants.ts`, change the import line to:

```ts
import type { AutomationDefinition, Grant, StoredEvent } from '@desk/protocol';
import { dayTime } from './automation-format';
```

and append:

```ts
/** The tools a grant is usually for (the Grants tab's suggestions). Any tool name is accepted. */
export const GRANT_TOOLS = ['web_fetch', 'web_search', 'skill_run', 'bash', 'bash_background', 'git_push', 'open_pr'];

/** What a grant matches: every call of its tool, or one field of its `match`. */
export type GrantMatchKind = 'any' | 'domain' | 'command' | 'branch';

export const GRANT_MATCH_LABEL: Record<GrantMatchKind, string> = { any: 'Any call', domain: 'A domain', command: 'A command', branch: 'A branch' };

/** The value field's label for each kind of match (the policy's domain globs, command regexes and branch globs). */
export const GRANT_VALUE_LABEL: Record<Exclude<GrantMatchKind, 'any'>, string> = {
  domain: 'Domain (a host, or *.example.com)',
  command: 'Command (a regular expression)',
  branch: 'Branch (a glob)',
};

/** A grant while the user edits it. */
export type GrantDraft = { tool: string; action: Grant['action']; kind: GrantMatchKind; value: string };

export function grantDraft(g?: Grant): GrantDraft {
  if (!g) return { tool: '', action: 'allow', kind: 'any', value: '' };
  const m = g.match;
  const kind: GrantMatchKind = m?.domain ? 'domain' : m?.command ? 'command' : m?.branch ? 'branch' : 'any';
  return { tool: g.tool, action: g.action, kind, value: m?.domain ?? m?.command ?? m?.branch ?? '' };
}

/** The protocol's limits on each match field (`Grant` in `protocol/src/automations.ts`). */
const MATCH_MAX: Record<Exclude<GrantMatchKind, 'any'>, number> = { domain: 253, command: 1000, branch: 200 };

/** The grant a draft describes, or what is wrong with it: the protocol's limits, and a command must compile as a regex. */
export function grantFromDraft(d: GrantDraft): { grant: Grant } | { problem: string } {
  const tool = d.tool.trim();
  if (!tool) return { problem: 'Name a tool.' };
  if (tool.length > 60) return { problem: 'A tool name is at most 60 characters.' };
  if (d.kind === 'any') return { grant: { tool, action: d.action } };
  const raw = d.value.trim();
  const value = d.kind === 'domain' ? raw.toLowerCase() : raw;
  if (!value) return { problem: `Say which ${d.kind}.` };
  if (value.length > MATCH_MAX[d.kind]) return { problem: `At most ${MATCH_MAX[d.kind]} characters.` };
  if (d.kind === 'command') {
    try {
      new RegExp(value);
    } catch {
      return { problem: 'Not a valid regular expression.' };
    }
  }
  return { grant: { tool, action: d.action, match: { [d.kind]: value } } };
}

/**
 * A web grant on an exact host widened (spec §5.3): its registrable domain and every subdomain, two grants because
 * `*.x` does not match `x`. Null for other tools, wildcards and hosts that do not widen.
 */
export function widenedGrants(g: Grant): Grant[] | null {
  const host = g.match?.domain;
  if (!host || host.startsWith('*.') || (g.tool !== 'web_fetch' && g.tool !== 'web_search')) return null;
  const wide = widenDomain(host);
  return wide.length < 2 ? null : wide.map((domain) => ({ ...g, match: { domain } }));
}

/** `list` with the grant at `i` replaced by `next`: none removes it, `i === list.length` appends. Each grant stays once. */
export function replaceGrant(list: Grant[], i: number, next: Grant[]): Grant[] {
  return uniqueGrants([...list.slice(0, i), ...next, ...list.slice(i + 1)]);
}

/** Where a grant came from (spec §8.4), or null before the project's events have loaded. */
export function grantOriginText(origin: GrantOrigin | undefined, def: AutomationDefinition, now: number): string | null {
  if (!origin) return null;
  const when = dayTime(origin.ts, now);
  if (origin.kind === 'remembered') return `Remembered at ${def.steps.find((s) => s.id === origin.step_id)?.title ?? origin.step_id} · ${when}`;
  return `${origin.kind === 'enabled' ? 'Set when you turned it on' : 'Added by you'} · ${when}`;
}
```

- [ ] **Step 4: Write the view**

Create `apps/desktop/src/renderer/automations/grants/GrantsView.tsx`:

```tsx
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import type { AutomationDefinition, AutomationDetail, AutomationVersionInfo, Grant } from '@desk/protocol';
import {
  dayTime,
  describeGrant,
  diffDefinitions,
  GRANT_MATCH_LABEL,
  GRANT_TOOLS,
  GRANT_VALUE_LABEL,
  grantDraft,
  grantFromDraft,
  grantKey,
  grantOrigins,
  grantOriginText,
  href,
  originText,
  replaceGrant,
  widenedGrants,
  type GrantDraft,
  type GrantMatchKind,
} from '@desk/ui-core';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Field } from '../../components/Field';
import { toast, toastError } from '../../components/Toast';
import { useNow } from '../../state/now';
import type { SessionState } from '../../state/session';
import { DiffView } from '../versions/DiffView';

type Props = { projectId: string; s: SessionState; detail: AutomationDetail; onChange(d: AutomationDetail): void };

/** Grants (spec §8.4): what this automation's runs may do without asking, where each rule came from, and the suspension banner. */
export function GrantsView(o: Props) {
  const d = o.detail;
  const now = useNow();
  const origins = useMemo(() => grantOrigins(o.s.events, d.id), [o.s.events, d.id]);
  /** The row being edited; `d.grants.length` is a new one. */
  const [editing, setEditing] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async (grants: Grant[]) => {
    setBusy(true);
    try {
      o.onChange(await call('automations.setGrants', { id: d.id, grants, reason: 'edited' }));
      setEditing(null);
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="auto-grants">
      {d.grants_suspended ? <SuspendedBanner detail={d} onChange={o.onChange} /> : null}
      <p className="muted small">Grants go before the project's policy, for this automation's scripts and step agents only.</p>
      {d.grants.length === 0 && editing === null ? (
        <EmptyState title="No grants">Its runs ask you for whatever the project's policy does not allow. Approve and remember adds a grant.</EmptyState>
      ) : null}
      <ul className="auto-grant-list">
        {d.grants.map((g, i) => {
          const origin = origins.get(grantKey(g));
          const wide = widenedGrants(g);
          return (
            <li key={grantKey(g)} className="auto-grant" aria-label={describeGrant(g)}>
              {editing === i ? (
                <GrantEditor initial={g} busy={busy} onSave={(next) => void save(replaceGrant(d.grants, i, [next]))} onCancel={() => setEditing(null)} />
              ) : (
                <>
                  <div className="grow">
                    <p className={`auto-grant-rule ${g.action}`}>{describeGrant(g)}</p>
                    <p className="muted small">
                      {grantOriginText(origin, d.definition, now)}
                      {origin?.kind === 'remembered' ? (
                        <>
                          {' · '}
                          <a className="link" href={href({ name: 'project', id: o.projectId, tab: 'automations', automationId: d.id, view: 'runs', runId: origin.run_id })}>
                            Open run
                          </a>
                        </>
                      ) : null}
                    </p>
                  </div>
                  {wide ? (
                    <Button size="sm" variant="ghost" disabled={busy} title={`Allow ${wide[0]?.match?.domain} and every subdomain of it, not only ${g.match?.domain}`} onClick={() => void save(replaceGrant(d.grants, i, wide))}>
                      {`Widen to ${wide[0]?.match?.domain}`}
                    </Button>
                  ) : null}
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(i)}>
                    Edit
                  </Button>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => void save(replaceGrant(d.grants, i, []))}>
                    Remove
                  </Button>
                </>
              )}
            </li>
          );
        })}
      </ul>
      {editing === d.grants.length ? (
        <GrantEditor busy={busy} onSave={(next) => void save(replaceGrant(d.grants, d.grants.length, [next]))} onCancel={() => setEditing(null)} />
      ) : (
        <div>
          <Button disabled={busy} onClick={() => setEditing(d.grants.length)}>
            Add grant
          </Button>
        </div>
      )}
    </div>
  );
}

function GrantEditor(o: { initial?: Grant; busy: boolean; onSave(g: Grant): void; onCancel(): void }) {
  const [draft, setDraft] = useState<GrantDraft>(() => grantDraft(o.initial));
  const [problem, setProblem] = useState<string | null>(null);
  const set = (patch: Partial<GrantDraft>) => {
    setDraft((x) => ({ ...x, ...patch }));
    setProblem(null);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const r = grantFromDraft(draft);
    if ('problem' in r) setProblem(r.problem);
    else o.onSave(r.grant);
  };
  return (
    <form className="auto-grant-form" aria-label={o.initial ? 'Edit grant' : 'New grant'} onSubmit={submit}>
      <div className="auto-inline">
        <Field id="grant-action" label="Action">
          <select id="grant-action" className="select" value={draft.action} onChange={(e) => set({ action: e.target.value as Grant['action'] })}>
            <option value="allow">Allow</option>
            <option value="deny">Deny</option>
          </select>
        </Field>
        <Field id="grant-tool" label="Tool">
          <input id="grant-tool" className="input mono" list="grant-tools" maxLength={60} value={draft.tool} onChange={(e) => set({ tool: e.target.value })} />
        </Field>
        <datalist id="grant-tools">
          {GRANT_TOOLS.map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>
        <Field id="grant-kind" label="Matches">
          <select id="grant-kind" className="select" value={draft.kind} onChange={(e) => set({ kind: e.target.value as GrantMatchKind })}>
            {(Object.keys(GRANT_MATCH_LABEL) as GrantMatchKind[]).map((k) => (
              <option key={k} value={k}>
                {GRANT_MATCH_LABEL[k]}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {draft.kind !== 'any' ? (
        <Field id="grant-value" label={GRANT_VALUE_LABEL[draft.kind]}>
          <input id="grant-value" className="input mono" value={draft.value} onChange={(e) => set({ value: e.target.value })} />
        </Field>
      ) : null}
      {problem ? (
        <p className="field-error" role="alert">
          {problem}
        </p>
      ) : null}
      <div className="auto-inline">
        <Button type="submit" variant="primary" pending={o.busy}>
          Save grant
        </Button>
        <Button onClick={o.onCancel}>Cancel</Button>
      </div>
    </form>
  );
}

function SuspendedBanner({ detail: d, onChange }: { detail: AutomationDetail; onChange(d: AutomationDetail): void }) {
  const now = useNow();
  const since = d.grants_set_version;
  const [versions, setVersions] = useState<AutomationVersionInfo[] | null>(null);
  const [before, setBefore] = useState<AutomationDefinition | null>(null);
  const [keeping, setKeeping] = useState(false);
  useEffect(() => {
    let live = true;
    call('automations.versions', { id: d.id })
      .then((list) => live && setVersions(list))
      .catch(toastError);
    if (since !== null) {
      call('automations.version', { id: d.id, version: since })
        .then((v) => live && setBefore(v.definition))
        .catch(toastError);
    }
    return () => {
      live = false;
    };
  }, [d.id, d.version, since]);
  const saved = (versions ?? []).filter((v) => since === null || v.version > since);
  const diff = useMemo(() => (before ? diffDefinitions(before, d.definition) : null), [before, d.definition]);
  const keep = async () => {
    setKeeping(true);
    try {
      onChange(await call('automations.keepGrants', { id: d.id }));
      toast({ tone: 'info', message: `Grants kept for v${d.version}.` });
    } catch (err) {
      toastError(err);
    } finally {
      setKeeping(false);
    }
  };
  return (
    <section className="auto-banner" aria-label="Grants suspended">
      <p>
        <b>Grants are suspended.</b>
        {` v${d.version} was saved after they were set${since !== null ? ` in v${since}` : ''}, so its runs ask you for everything and approvals offer no remember until you keep them. Editing a grant keeps them too.`}
      </p>
      {saved.length ? (
        <ul className="auto-plain">
          {saved.map((v) => (
            <li key={v.version}>
              <b>{`v${v.version}`}</b> <span className="muted small">{`${originText(v)} · ${dayTime(v.created_at, now)}`}</span>
              {v.change_note ? <p>{v.change_note}</p> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {since !== null ? (
        <section aria-label={`What changed since v${since}`}>
          {diff ? <DiffView diff={diff} labels={{ before: `v${since}`, after: `v${d.version}` }} /> : <p className="muted">Loading…</p>}
        </section>
      ) : null}
      <div>
        <Button variant="primary" pending={keeping} onClick={() => void keep()}>
          Keep grants
        </Button>
      </div>
    </section>
  );
}
```

A change note is shown as plain text, never as markdown (Desk writes it).

In `apps/desktop/src/renderer/automations/AutomationsScreen.tsx`, import `GrantsView` from `./grants/GrantsView` and change the `grants` branch of `AutomationBody` to:

```tsx
    case 'grants':
      return <GrantsView projectId={o.projectId} s={o.s} detail={o.detail} onChange={o.onChange} />;
```

Append to `packages/ui-styles/src/automations.css`:

```css
/* Grants */
.auto-grants {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 760px;
  padding: 16px 20px;
}
.auto-grant-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.auto-grant {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--rule);
  border-radius: 8px;
  background: var(--card);
}
.auto-grant-rule {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--ink);
}
.auto-grant-rule.deny {
  color: var(--accent);
}
.auto-grant-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
}
.auto-banner {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  border: 1px solid var(--wait);
  border-radius: 10px;
  background: var(--wait-pastel);
  color: var(--ink);
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pnpm vitest run packages/ui-core apps/desktop/src/renderer/automations packages/ui-styles`
Expected: PASS.

Run: `pnpm typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
pnpm test
git add packages/ui-core/src/automation-grants.ts packages/ui-core/src/automation-grants.test.ts apps/desktop/src/renderer/automations/grants apps/desktop/src/renderer/automations/AutomationsScreen.tsx packages/ui-styles/src/automations.css
git commit -m "feat(desktop): the Grants tab: origins, edit, widen, and the suspended banner with Keep grants

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: Attention: automation cards and step-agent approvals

Attention (spec §8.4, §5.3, §4.7):
- **The four automation kinds** get cards in the inspector:
  - *automation_ask*: the Ask me question or script gate, answered in place (the step panel's own forms);
  - *automation_failed*: the reason, *Ask Desk to fix*, Dismiss;
  - *automation_enable_request*: Desk's note, *Turn on…* (the Turn-on dialog), Dismiss;
  - *automation_grants_suspended*: *Review changes* (the Grants tab), Dismiss.
- **Step agents' approvals** name "<automation> · <step>" and add **Approve and remember for this automation**, hidden while its grants are suspended.
- **Opening:** E and the Open buttons go to the run (asks, failures, step approvals), the Grants tab (suspended grants) or the automation (turn-on requests).
- **Strips** name the automation instead of "Thread", and the legend says which codes automations share.

**Files:**
- Modify: `packages/ui-core/src/strips.ts` (`stripWho` automation cases, `automationTarget`)
- Create: `packages/ui-core/src/strips.test.ts`
- Modify: `packages/ui-core/src/automation-run-graph.ts`, `automation-run-graph.test.ts` (`runFailure`)
- Create: `apps/desktop/src/renderer/attention/AutomationCards.tsx`
- Modify: `apps/desktop/src/renderer/attention/Inspector.tsx`, `apps/desktop/src/renderer/attention/AttentionScreen.tsx`
- Modify: `apps/desktop/src/renderer/automations/runs/StepPanel.tsx` (export `AskAnswer`, `GateAnswer`)
- Test: `apps/desktop/src/renderer/attention/automations.test.tsx`

**Interfaces:**
- Consumes:
  - `AskAnswer({ run, row })`, `GateAnswer({ run, row, grantsSuspended })` (Task 15, exported here); `TurnOnDialog({ detail, onClose, onDone, onTestFirst })` (Task 6); `askDeskText` (Task 14);
  - `call('automations.get' | 'automations.getRun' | 'approvals.resolve' | 'projects.send' | 'attention.dismiss')`.
- Produces:
  - ui-core: `automationTarget(item): Route | null`, `runFailure(run): { stepTitle: string; error: string }`, and `stripWho` covering the automation kinds;
  - desktop: `useAutomationItem(item): { detail, run, missing }`, `AutomationCard({ item, a, auto })`, and `InspectorActions.resolve(decision, remember?)`.

- [ ] **Step 1: Write the failing tests**

Create `packages/ui-core/src/strips.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import type { AttentionItem } from '@desk/protocol';
import { automationTarget, stripWho } from './strips';

const item = (over: Partial<AttentionItem>): AttentionItem => ({
  id: 'x',
  kind: 'automation_failed',
  project_id: 'p',
  project_name: 'Tax 2026',
  agent_id: null,
  title: 'Weekly digest failed',
  detail: '',
  created_at: '2026-09-28T06:00:00.000Z',
  ref: { automation_id: 'a1', run_id: 'r14' },
  ...over,
});
const none = () => null;

describe('automation strips', () => {
  it('names the automation, read from the daemon’s titles', () => {
    expect(stripWho(item({}), none)).toEqual({ label: 'Automation', name: 'Weekly digest', tag: 'failed' });
    expect(stripWho(item({ kind: 'automation_ask', title: 'Weekly digest: Publish "Robots"?', ref: { automation_id: 'a1', run_id: 'r14', step_id: 'ok' } }), none)).toEqual({ label: 'Automation', name: 'Weekly digest', tag: 'question' });
    expect(stripWho(item({ kind: 'automation_ask', title: 'Weekly digest · Fetch pages wants to run digest/fetch.py robots', ref: { automation_id: 'a1', run_id: 'r14', step_id: 'fetch' } }), none)).toEqual({ label: 'Automation', name: 'Weekly digest', tag: 'script' });
    expect(stripWho(item({ kind: 'automation_enable_request', title: 'Desk proposes turning on Weekly digest', ref: { automation_id: 'a1' } }), none)).toEqual({ label: 'Automation', name: 'Weekly digest', tag: 'turn on?' });
    expect(stripWho(item({ kind: 'automation_grants_suspended', title: 'Weekly digest changed: its grants are suspended', ref: { automation_id: 'a1' } }), none)).toEqual({ label: 'Automation', name: 'Weekly digest', tag: 'grants' });
    expect(stripWho(item({ kind: 'approval', agent_id: 'ag1', title: 'Weekly digest · Summarise wants to run bash', ref: { approval_id: 'ap9', automation_id: 'a1', run_id: 'r14', step_id: 'sum' } }), none)).toEqual({ label: 'Automation', name: 'Weekly digest', tag: 'bash' });
    expect(stripWho(item({ kind: 'approval', agent_id: 't', title: 'Signup wants to run bash', ref: { approval_id: 'a1', thread_id: 't' } }), () => 'Signup')).toEqual({ label: 'Thread', name: 'Signup', tag: 'bash' });
  });

  it('opens an item’s run, its grants or the automation', () => {
    expect(automationTarget(item({}))).toEqual({ name: 'project', id: 'p', tab: 'automations', automationId: 'a1', view: 'runs', runId: 'r14' });
    expect(automationTarget(item({ kind: 'approval', ref: { approval_id: 'ap9', automation_id: 'a1', run_id: 'r14', step_id: 'sum' } }))).toMatchObject({ view: 'runs', runId: 'r14' });
    expect(automationTarget(item({ kind: 'automation_grants_suspended', ref: { automation_id: 'a1' } }))).toEqual({ name: 'project', id: 'p', tab: 'automations', automationId: 'a1', view: 'grants' });
    expect(automationTarget(item({ kind: 'automation_enable_request', ref: { automation_id: 'a1' } }))).toEqual({ name: 'project', id: 'p', tab: 'automations', automationId: 'a1', view: 'design' });
    expect(automationTarget(item({ kind: 'approval', ref: { approval_id: 'a1', thread_id: 't' } }))).toBeNull();
  });
});
```

Add to `packages/ui-core/src/automation-run-graph.test.ts` (and add `runFailure` to its import; `runDetail` and `stepRun` come from `./testing/automations`, which Task 14's test already imports):

```ts
describe('runFailure', () => {
  it('names the step that failed and its error, else the run’s reason', () => {
    const failed = runDetail({ status: 'failed', reason: 'step fetch failed', steps: [stepRun('fetch', { status: 'failed', error: 'exit 1\nTraceback' }), ...runDetail().steps.slice(1)] });
    expect(runFailure(failed)).toEqual({ stepTitle: 'Fetch pages', error: 'exit 1\nTraceback' });
    const late = runDetail({ status: 'failed', at_step: 'Summarise', reason: 'past its deadline' });
    expect(runFailure(late)).toEqual({ stepTitle: 'Summarise', error: 'past its deadline' });
    expect(runFailure(runDetail({ status: 'failed', at_step: null, reason: null }))).toEqual({ stepTitle: 'Weekly digest', error: 'It failed.' });
  });
});
```

Create `apps/desktop/src/renderer/attention/automations.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import type { AttentionItem, StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { initialGlobalState } from '@desk/bff/contract';
import { automationDetail, runDetail, stepRun } from '@desk/ui-core/testing';
import { toastStore } from '../components/Toast';
import { useRoute } from '../router';
import { globalStore } from '../state/global';
import { resetSessions, setReleaseDelay, startSessionRouting } from '../state/session';
import { installBridge } from '../test/bridge';
import { AttentionScreen } from './AttentionScreen';

afterEach(cleanup);
beforeEach(() => {
  resetSessions();
  setReleaseDelay(0);
  window.location.hash = '#/attention';
  toastStore.set([]);
});

const recent = new Date(Date.now() - 4 * 60_000).toISOString();
const base = { project_id: 'p', project_name: 'Tax 2026', detail: '', created_at: recent };
const stepApproval: AttentionItem = { ...base, id: 'approval:ap9', kind: 'approval', agent_id: 'ag1', title: 'Weekly digest · Summarise wants to run bash', detail: 'Policy rule {"tool":"bash"} → ask', ref: { approval_id: 'ap9', automation_id: 'a1', run_id: 'r14', step_id: 'sum' } };
const ask: AttentionItem = { ...base, id: 'automation_ask:r14:ok', kind: 'automation_ask', agent_id: null, title: 'Weekly digest: Publish "Robots"?', ref: { automation_id: 'a1', run_id: 'r14', step_id: 'ok' } };
const failed: AttentionItem = { ...base, id: 'automation_failed:r14', kind: 'automation_failed', agent_id: null, title: 'Weekly digest failed', detail: 'step fetch failed', ref: { automation_id: 'a1', run_id: 'r14' } };
const enable: AttentionItem = { ...base, id: 'automation_enable:a1:t', kind: 'automation_enable_request', agent_id: null, title: 'Desk proposes turning on Weekly digest', detail: '0 8 * * 1 (Europe/Paris); 0 proposed grant(s). Tested twice.', ref: { automation_id: 'a1' } };
const suspended: AttentionItem = { ...base, id: 'automation_grants:a1:7', kind: 'automation_grants_suspended', agent_id: null, title: 'Weekly digest changed: its grants are suspended', detail: 'Desk saved v7.', ref: { automation_id: 'a1' } };

const overview = () =>
  ({
    project: { id: 'p', name: 'Tax 2026', goal: 'g', instructions: '', settings: { desk_model: 'm', thread_model: 'm', fallback_model: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, policy: [] }, created_at: 't', updated_at: 't', archived_at: null },
    desk: null,
    sources: [],
    plan: null,
    threads: [],
    approvals: [],
    last_seq: 0,
  }) as unknown as ProjectOverview;

const events: StoredEvent[] = [
  ev(1, 'assistant.message', { run_id: 'x', content: 'Checking the pages now.', tool_calls: [] }, { agent: 'ag1' }),
  ev(2, 'approval.requested', { approval_id: 'ap9', run_id: 'x', tool_call_id: 'c', tool: 'bash', arguments: '{"command":"ls steps"}', reason: 'Policy rule {"tool":"bash"} → ask', delegate_to_desk: false }, { agent: 'ag1' }),
];

const waitingAsk = () =>
  runDetail({
    status: 'waiting',
    steps: [
      ...runDetail().steps.slice(0, 1),
      stepRun('sum', { status: 'succeeded', summary: 'Robots everywhere', outputs: { headline: 'Robots' } }),
      stepRun('ok', { status: 'waiting', question: { text: 'Publish "Robots"?', files: [], approve_label: 'Publish' } }),
    ],
  });

function Routed() {
  const r = useRoute();
  return <AttentionScreen {...(r.name === 'attention' && r.item ? { itemId: r.item } : {})} />;
}

function setup(list: AttentionItem[], extra: Record<string, (input: any) => unknown> = {}) {
  globalStore.set({ ...initialGlobalState(), connection: { status: 'live' }, attention: list });
  const bridge = installBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    'automations.get': () => automationDetail(),
    'automations.getRun': () => runDetail(),
    ...extra,
  });
  startSessionRouting();
  render(<Routed />);
  return bridge;
}

const input = (bridge: ReturnType<typeof setup>, channel: string) => bridge.calls.find((c) => c.channel === channel)?.input;

describe('Attention: automations', () => {
  it('names a step agent’s approval by automation and step, and approves it with remember', async () => {
    const bridge = setup([stepApproval], { 'approvals.resolve': () => ({ ok: true }) });
    expect(screen.getByRole('button', { name: /Automation Weekly digest\./ })).toBeTruthy();
    const insp = await screen.findByRole('article', { name: /clearance request/ });
    const link = await within(insp).findByRole('link', { name: 'Weekly digest · Summarise' });
    expect(link.getAttribute('href')).toBe('#/p/p/automations/a1/runs/r14');
    expect(within(insp).getByText('What the step agent said')).toBeTruthy();
    fireEvent.click(await within(insp).findByRole('button', { name: 'Approve and remember for this automation' }));
    await waitFor(() => expect(input(bridge, 'approvals.resolve')).toEqual({ id: 'ap9', decision: 'approved', remember: true }));
  });

  it('offers no remember while the automation’s grants are suspended', async () => {
    setup([stepApproval], { 'automations.get': () => automationDetail({ grants_suspended: true }) });
    const insp = await screen.findByRole('article', { name: /clearance request/ });
    await within(insp).findByRole('link', { name: 'Weekly digest · Summarise' });
    expect(within(insp).queryByRole('button', { name: 'Approve and remember for this automation' })).toBeNull();
    expect(within(insp).getByText(/grants are suspended/)).toBeTruthy();
  });

  it('answers an Ask me step in place, and E opens the run', async () => {
    const bridge = setup([ask], { 'automations.getRun': () => waitingAsk(), 'automations.answer': () => ({ ok: true }) });
    const insp = await screen.findByRole('article', { name: /automation question/ });
    expect(await within(insp).findByRole('heading', { name: 'Weekly digest · Publish?' })).toBeTruthy();
    fireEvent.click(within(within(insp).getByRole('region', { name: 'Your answer' })).getByRole('button', { name: 'Publish' }));
    await waitFor(() => expect(input(bridge, 'automations.answer')).toEqual({ runId: 'r14', stepId: 'ok', req: { decision: 'approve' } }));
    fireEvent.keyDown(window, { key: 'e' });
    expect(window.location.hash).toBe('#/p/p/automations/a1/runs/r14');
  });

  it('asks Desk to fix a failed run, and dismisses it', async () => {
    const run = runDetail({ status: 'failed', steps: [stepRun('fetch', { status: 'failed', error: 'exit 1\nTraceback' }), ...runDetail().steps.slice(1)] });
    const bridge = setup([failed], { 'automations.getRun': () => run, 'projects.send': () => ({ ok: true }), 'attention.dismiss': () => ({ ok: true }) });
    const insp = await screen.findByRole('article', { name: /failed automation/ });
    expect(within(insp).getByText('step fetch failed')).toBeTruthy();
    const fix = within(insp).getByRole('button', { name: 'Ask Desk to fix' });
    await waitFor(() => expect((fix as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(fix);
    await waitFor(() => expect(input(bridge, 'projects.send')).toEqual({ id: 'p', text: 'Please fix digest: run #14 failed at Fetch pages. exit 1' }));
    fireEvent.click(within(insp).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(input(bridge, 'attention.dismiss')).toEqual({ id: 'automation_failed:r14' }));
  });

  it('opens the Turn-on dialog from Desk’s request', async () => {
    setup([enable], {
      'automations.get': () => automationDetail({ enabled: false, enable_requested: true }),
      'automations.validate': () => ({ errors: [], warnings: [], next_times: {} }),
    });
    const insp = await screen.findByRole('article', { name: /automation to turn on/ });
    expect(within(insp).getByText(/Tested twice\./)).toBeTruthy();
    const turnOn = within(insp).getByRole('button', { name: 'Turn on…' });
    await waitFor(() => expect((turnOn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(turnOn);
    expect(await screen.findByRole('dialog', { name: 'Turn on Weekly digest' })).toBeTruthy();
  });

  it('opens the Grants tab for suspended grants, and the legend names what automations share', async () => {
    setup([suspended]);
    await screen.findByRole('article', { name: /grants suspended/ });
    expect(document.querySelector('.strip-legend')!.textContent).toContain('FLDFailed thread or automation');
    fireEvent.keyDown(window, { key: 'e' });
    expect(window.location.hash).toBe('#/p/p/automations/a1/grants');
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pnpm vitest run packages/ui-core/src/strips.test.ts packages/ui-core/src/automation-run-graph.test.ts apps/desktop/src/renderer/attention`
Expected: FAIL: `automationTarget` and `runFailure` are not exported, strips still say "Thread" for automations, and the inspector has no automation cards. The existing `AttentionScreen.test.tsx` still passes.

- [ ] **Step 3: The ui-core helpers**

In `packages/ui-core/src/strips.ts`, add `import type { Route } from './router';` and, above `stripWho`:

```ts
/** The automation an item is about, read from the daemon's titles (`packages/core/src/state/attention.ts`). */
function automationName(i: AttentionItem): string {
  switch (i.kind) {
    case 'automation_failed':
      return i.title.replace(/ failed$/, '');
    case 'automation_enable_request':
      return i.title.replace(/^Desk proposes turning on /, '');
    case 'automation_grants_suspended':
      return i.title.replace(/ changed: its grants are suspended$/, '');
    default:
      // Asks ("<title>: <question>", "<title> · <step> wants to run …") and step agents' approvals ("<title> · <step> wants to run …").
      return /^(.*?)(?: · |: )/.exec(i.title)?.[1] ?? i.title;
  }
}

/**
 * Where an automation item opens (spec §8.4): the run for asks, failures and step agents' approvals, the Grants tab
 * for suspended grants, the automation for Desk's turn-on request. Null for items about no automation.
 */
export function automationTarget(i: AttentionItem): Route | null {
  const automationId = i.ref.automation_id;
  if (!automationId) return null;
  const at = { name: 'project' as const, id: i.project_id, tab: 'automations' as const, automationId };
  if (i.kind === 'automation_grants_suspended') return { ...at, view: 'grants' };
  if (i.ref.run_id && i.kind !== 'automation_enable_request') return { ...at, view: 'runs', runId: i.ref.run_id };
  return { ...at, view: 'design' };
}
```

In `stripWho`, change the `approval` case to:

```ts
    case 'approval': {
      const tool = /wants to run (.+)$/.exec(i.title)?.[1] ?? '';
      if (i.ref.automation_id) return { label: 'Automation', name: automationName(i), tag: tool };
      return i.ref.thread_id ? { label: 'Thread', name: threadTitle(i.ref.thread_id) ?? 'A thread', tag: tool } : { label: 'Asked by', name: 'Desk', tag: tool };
    }
```

and add these cases before `default`:

```ts
    case 'automation_ask':
      return { label: 'Automation', name: automationName(i), tag: / wants to run /.test(i.title) ? 'script' : 'question' };
    case 'automation_failed':
      return { label: 'Automation', name: automationName(i), tag: 'failed' };
    case 'automation_enable_request':
      return { label: 'Automation', name: automationName(i), tag: 'turn on?' };
    case 'automation_grants_suspended':
      return { label: 'Automation', name: automationName(i), tag: 'grants' };
```

The web UI's strips use the same `stripWho`, so they name automations too.

Append to `packages/ui-core/src/automation-run-graph.ts`:

```ts
/** What made a run fail, for Ask Desk to fix: the step that failed and its error, else the step it was at and the run's reason. */
export function runFailure(run: RunDetail): { stepTitle: string; error: string } {
  const row = run.steps.find((s) => s.status === 'failed');
  if (row) return { stepTitle: run.definition.steps.find((s) => s.id === row.step_id)?.title ?? row.step_id, error: row.error ?? run.reason ?? 'It failed.' };
  return { stepTitle: run.at_step ?? run.automation_title, error: run.reason ?? 'It failed.' };
}
```

- [ ] **Step 4: The cards, the inspector and the screen**

In `apps/desktop/src/renderer/automations/runs/StepPanel.tsx`, change `function AskAnswer(` to `export function AskAnswer(` and `function GateAnswer(` to `export function GateAnswer(`.

Create `apps/desktop/src/renderer/attention/AutomationCards.tsx`:

```tsx
import { useEffect, useState } from 'react';
import type { AttentionItem, AutomationDetail, RunDetail } from '@desk/protocol';
import { askDeskText, runFailure } from '@desk/ui-core';
import { AskAnswer, GateAnswer } from '../automations/runs/StepPanel';
import { TurnOnDialog } from '../automations/dialogs/TurnOnDialog';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { toast, toastError } from '../components/Toast';
import { navigate } from '../router';
import type { InspectorActions } from './Inspector';

export type AutomationItemData = { detail: AutomationDetail | null; run: RunDetail | null; missing: boolean };

/** An item's automation and run, when it names them (both null until loaded; `missing` once either is gone). */
export function useAutomationItem(i: AttentionItem): AutomationItemData {
  const [detail, setDetail] = useState<AutomationDetail | null>(null);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const automationId = i.ref.automation_id;
  const runId = i.ref.run_id;
  useEffect(() => {
    let live = true;
    if (automationId) {
      call('automations.get', { id: automationId })
        .then((d) => live && setDetail(d))
        .catch(() => live && setMissing(true));
    }
    if (runId) {
      call('automations.getRun', { runId })
        .then((r) => live && setRun(r))
        .catch(() => live && setMissing(true));
    }
    return () => {
      live = false;
    };
  }, [automationId, runId]);
  return { detail, run, missing };
}

/** The inspector's body for the four automation kinds (spec §8.4). */
export function AutomationCard({ item: i, a, auto }: { item: AttentionItem; a: InspectorActions; auto: AutomationItemData }) {
  const { detail, run } = auto;
  const [turningOn, setTurningOn] = useState(false);
  const [asking, setAsking] = useState(false);
  const row = run && i.ref.step_id ? run.steps.find((s) => s.step_id === i.ref.step_id) : undefined;
  const stepTitle = row ? (run?.definition.steps.find((s) => s.id === row.step_id)?.title ?? row.step_id) : null;
  const gone = auto.missing ? <p className="muted">It could not be loaded: the automation or its run may have been deleted.</p> : null;

  const askFix = async () => {
    if (!run) return;
    setAsking(true);
    try {
      const f = runFailure(run);
      await call('projects.send', { id: i.project_id, text: askDeskText(run, f.stepTitle, f.error) });
      toast({ tone: 'info', message: 'Sent to Desk.', action: { label: 'Open conversation', run: () => navigate({ name: 'project', id: i.project_id, tab: 'conversation' }) } });
    } catch (err) {
      toastError(err);
    } finally {
      setAsking(false);
    }
  };

  switch (i.kind) {
    case 'automation_ask':
      return (
        <>
          <h2 className="inspector-title">{run && stepTitle ? `${run.automation_title} · ${stepTitle}` : i.title}</h2>
          {gone ??
            (!run || !row ? (
              <p className="muted">Loading the step…</p>
            ) : row.status !== 'waiting' ? (
              <p className="muted">It has been answered.</p>
            ) : row.question ? (
              <AskAnswer run={run} row={row} />
            ) : row.gate ? (
              <GateAnswer run={run} row={row} grantsSuspended={detail?.grants_suspended ?? true} />
            ) : null)}
          <div className="inspector-actions">
            <Button variant="ghost" onClick={a.open}>
              Open run <kbd>E</kbd>
            </Button>
          </div>
        </>
      );
    case 'automation_failed':
      return (
        <>
          <h2 className="inspector-title">{i.title}</h2>
          {i.detail ? <pre className="auto-error">{i.detail}</pre> : null}
          {gone}
          <div className="inspector-actions">
            <Button variant="primary" onClick={a.open}>
              Open run <kbd>E</kbd>
            </Button>
            <Button pending={asking} disabled={!run} onClick={() => void askFix()}>
              Ask Desk to fix
            </Button>
            <Button pending={a.busy === 'dismiss'} disabled={a.busy !== null} onClick={a.dismiss}>
              Dismiss
            </Button>
          </div>
          <p className="muted small">Dismissing hides this here. The automation is not changed, and its next run starts on time.</p>
        </>
      );
    case 'automation_enable_request':
      return (
        <>
          <h2 className="inspector-title">{i.title}</h2>
          {i.detail ? <p>{i.detail}</p> : null}
          {gone}
          <div className="inspector-actions">
            <Button variant="primary" disabled={!detail} onClick={() => setTurningOn(true)}>
              Turn on…
            </Button>
            <Button variant="ghost" onClick={a.open}>
              Open automation <kbd>E</kbd>
            </Button>
            <Button pending={a.busy === 'dismiss'} disabled={a.busy !== null} onClick={a.dismiss}>
              Dismiss
            </Button>
          </div>
          <p className="muted small">Only you turn automations on. Dismissing leaves it off; Desk is not told.</p>
          {turningOn && detail ? (
            <TurnOnDialog
              detail={detail}
              onClose={() => setTurningOn(false)}
              onDone={() => setTurningOn(false)}
              onTestFirst={() => {
                setTurningOn(false);
                a.open();
              }}
            />
          ) : null}
        </>
      );
    default:
      return (
        <>
          <h2 className="inspector-title">{i.title}</h2>
          {i.detail ? <p>{i.detail}</p> : null}
          <div className="inspector-actions">
            <Button variant="primary" onClick={a.open}>
              Review changes <kbd>E</kbd>
            </Button>
            <Button pending={a.busy === 'dismiss'} disabled={a.busy !== null} onClick={a.dismiss}>
              Dismiss
            </Button>
          </div>
          <p className="muted small">Until you keep them on its Grants tab, its runs ask you for everything.</p>
        </>
      );
  }
}
```

The item's `detail` is the daemon's text (Desk's turn-on note, a run's reason). It stays plain text, like every other card.

In `apps/desktop/src/renderer/attention/Inspector.tsx`:
- Add `automationTarget` to the `@desk/ui-core` import, and `import { AutomationCard, useAutomationItem } from './AutomationCards';`.
- Change `resolve` in `InspectorActions` to `resolve(decision: 'approved' | 'denied', remember?: boolean): void;`.
- Right after `const why = policyReason(i.detail || approval?.reason || '');`, add:

```tsx
  const auto = useAutomationItem(i);
  const target = automationTarget(i);
  /** A step agent's approval names its step (spec §8.4). */
  const step = i.kind === 'approval' && i.ref.automation_id ? (auto.detail?.definition.steps.find((x) => x.id === i.ref.step_id)?.title ?? i.ref.step_id ?? 'a step') : null;
  const suspended = auto.detail?.grants_suspended ?? true;
```

- Replace the `THREAD` fact:

```tsx
            <Fact label={agent?.role === 'desk' ? 'ASKED BY' : 'THREAD'}>{threadHref ? <a href={threadHref}>{agent?.title ?? 'Thread'}</a> : 'Desk'}</Fact>
```

with:

```tsx
            {step !== null ? (
              <Fact label="AUTOMATION">{target && auto.detail ? <a href={href(target)}>{`${auto.detail.title} · ${step}`}</a> : step}</Fact>
            ) : (
              <Fact label={agent?.role === 'desk' ? 'ASKED BY' : 'THREAD'}>{threadHref ? <a href={threadHref}>{agent?.title ?? 'Thread'}</a> : 'Desk'}</Fact>
            )}
```

- Change `<span className="why-label">What the thread said</span>` to `<span className="why-label">{step !== null ? 'What the step agent said' : 'What the thread said'}</span>`.
- Right after the *Approve once* button, add:

```tsx
            {step !== null && !suspended ? (
              <Button pending={a.busy === 'remember'} disabled={a.busy !== null} onClick={() => a.resolve('approved', true)}>
                Approve and remember for this automation
              </Button>
            ) : null}
```

- Replace the approval's closing line (`<p className="muted small">The thread resumes as soon as you decide. If you deny, it's told why and tries another way.</p>`) with:

```tsx
          <p className="muted small">
            {step === null
              ? "The thread resumes as soon as you decide. If you deny, it's told why and tries another way."
              : auto.detail?.grants_suspended
                ? 'The step resumes as soon as you decide. Its grants are suspended until you keep them, so nothing is remembered.'
                : 'The step resumes as soon as you decide. Remember adds a grant, so its later runs do this without asking.'}
          </p>
```

- Before `) : i.kind === 'paused' ? (`, add the automation branch:

```tsx
      ) : i.kind.startsWith('automation_') ? (
        <AutomationCard item={i} a={a} auto={auto} />
```

While the automation is loading, `suspended` is true, so *remember* shows only once the daemon says the grants are live.

In `apps/desktop/src/renderer/attention/AttentionScreen.tsx`:
- Add `automationTarget` to the `@desk/ui-core` import.
- Start `openTarget` with:

```ts
  const automation = automationTarget(i);
  if (automation) return href(automation);
```

- Replace the `resolve` callback's head and its call so it can remember:

```tsx
  const resolve = useCallback(
    async (decision: 'approved' | 'denied', remember = false) => {
      if (!selected || selected.kind !== 'approval' || !selected.ref.approval_id || busy) return;
      setBusy(remember ? 'remember' : decision);
      try {
        await call('approvals.resolve', { id: selected.ref.approval_id, decision, ...(note.trim() ? { note: note.trim() } : {}), ...(remember ? { remember: true } : {}) });
```

  (the rest of the callback is unchanged).
- In the `Inspector` props, change `resolve: (d) => void resolve(d)` to `resolve: (d, remember) => void resolve(d, remember)`.
- In the legend, change the four shared codes' labels:

```tsx
          <span>
            <span className="strip-code-badge code-question">{STRIP_CODE.question}</span>Question (Desk or an automation)
          </span>
          <span>
            <span className="strip-code-badge code-needs_you">{STRIP_CODE.needs_you}</span>From a report, or an automation to turn on
          </span>
          <span>
            <span className="strip-code-badge code-stalled">{STRIP_CODE.stalled}</span>Stalled thread, or suspended grants
          </span>
          <span>
            <span className="strip-code-badge code-failed">{STRIP_CODE.failed}</span>Failed thread or automation
          </span>
```

⌘⏎ and ⌘⌫ still decide approvals only; they never answer an Ask me step, which needs its own buttons.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pnpm vitest run packages/ui-core apps/desktop/src/renderer/attention apps/desktop/src/renderer/automations`
Expected: PASS, including the existing `AttentionScreen.test.tsx`.

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/attention)`
Expected: PASS. The web strips read `stripWho` too, and no web spec pins a label for an automation item.

Run: `pnpm typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
pnpm test
git add packages/ui-core/src/strips.ts packages/ui-core/src/strips.test.ts packages/ui-core/src/automation-run-graph.ts packages/ui-core/src/automation-run-graph.test.ts apps/desktop/src/renderer/attention apps/desktop/src/renderer/automations/runs/StepPanel.tsx
git commit -m "feat(desktop): Attention cards for automations; step approvals name their step and can be remembered

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: End to end, and the docs

One Playwright-for-Electron test drives the real app against a real deskd and the fake model through spec §10's desktop flow:
- create an automation in the editor and save it;
- run it now with an input, and watch the graph light up;
- answer its Ask me step, see the run succeed, and find the published file in the Library.

Then the docs say what Plan 20 built and that Plan 21 follows.

**Files:**
- Create: `apps/desktop/e2e/automations.e2e.test.ts`
- Modify: `docs/desktop.md` (Screens, Architecture, the e2e table), `CLAUDE.md` (the ui-core, bff and renderer bullets, and the parity invariant), `docs/superpowers/specs/2026-09-26-automations-design.md` (status line)

**Interfaces:**
- Consumes: everything above; the labels come from Tasks 6–17. They are:
  - naming and inputs: `Blank automation`, the `New automation` dialog's `Name` and `Create`, `node-start`, `Add input`, the `Input 1` group's `Label` and `Key`;
  - steps and saving: the `Add a step` toolbar, `Brief`, `Add file`, `Publish to the Library 1`, `Question`, `Ready to save`, `Save`;
  - running: `Run now…`, the `Run <title>` dialog and its `Run` button, `Run #1`, `node-agent`, `node-ask` with its `run-<tone>` class, the `Your answer` region, `Approve`, the `Files` region.
- Produces: nothing new.

- [ ] **Step 1: Write the end-to-end test**

Create `apps/desktop/e2e/automations.e2e.test.ts`:

```ts
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { _electron as electron, type ElectronApplication, type Page } from 'playwright';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { clientFromDataDir } from '@desk/client/node';
import { startDaemon, type RunningDaemon } from '@desk/daemon';
import { call, startFakeModel, text, tools, type ChatRequest, type FakeModelServer, type FakeReply } from '@desk/fake-model';

const appDir = fileURLToPath(new URL('..', import.meta.url));
let dir: string;
let fake: FakeModelServer;
let daemon: RunningDaemon;
let app: ElectronApplication;

const content = (m: Record<string, unknown> | undefined) => (typeof m?.content === 'string' ? m.content : JSON.stringify(m?.content ?? ''));
const system = (req: ChatRequest) => content(req.messages[0]);

/** The step agent writes the digest into its step folder, then completes. Anyone else (Desk) only acknowledges. */
function stepAgent(req: ChatRequest): FakeReply {
  const calls = req.messages.filter((m) => m.role === 'assistant').length;
  if (calls === 0) return tools(call('write_file', { path: 'digest.md', content: '# Robots\n\nRobots had a big week.\n' }));
  if (calls === 1) return tools(call('complete', { summary: 'Wrote digest.md about robots.' }));
  return text('Done.');
}

beforeAll(async () => {
  dir = mkdtempSync(join(tmpdir(), 'desk-automations-'));
  fake = await startFakeModel((req) => (system(req).includes('You are one step of the automation') ? stepAgent(req) : text('Noted.')));
  daemon = await startDaemon({ dataDir: join(dir, 'data'), port: 0, modelConfig: { baseURL: fake.url, apiKey: 'test' } });
  const { default: electronPath } = await import('electron');
  app = await electron.launch({
    executablePath: electronPath as unknown as string,
    args: [appDir],
    colorScheme: process.env.DESK_E2E_SCHEME === 'dark' ? 'dark' : 'light',
    env: { ...process.env, DESK_DATA_DIR: join(dir, 'data'), DESK_USER_DATA: join(dir, 'user'), DESK_E2E: '1' },
  });
});

afterAll(async () => {
  await app?.close();
  await daemon?.stop();
  await fake?.close();
  rmSync(dir, { recursive: true, force: true });
});

async function shot(page: Page, name: string) {
  const out = process.env.DESK_E2E_SHOTS;
  if (out) await page.screenshot({ path: join(out, `${name}.png`) });
}

describe('automations, end to end', () => {
  it('builds an automation in the editor, runs it with an input, answers its Ask me step and publishes its file', async () => {
    const client = clientFromDataDir(join(dir, 'data'));
    const { project } = await client.projects.create({ name: 'Digest', goal: 'A weekly digest' });
    const page = await app.firstWindow();
    await page.evaluate(() => localStorage.setItem('desk.onboarded', '1'));
    await page.evaluate((h) => (window.location.hash = h), `#/p/${project.id}/automations`);

    // A Blank automation, named once.
    await page.getByRole('button', { name: 'Blank automation' }).click();
    const naming = page.getByRole('dialog', { name: 'New automation' });
    await naming.getByLabel('Name').fill('digest');
    await naming.getByRole('button', { name: 'Create' }).click();

    // Start: one text input.
    await page.getByTestId('node-start').click();
    await page.getByRole('button', { name: 'Add input' }).click();
    const input = page.getByRole('group', { name: 'Input 1' });
    await input.getByLabel('Label').fill('Topic');
    await input.getByLabel('Key').fill('topic');
    await input.getByLabel('Key').press('Tab');

    // An agent step that publishes digest.md, then (added while it is selected, so connected under it) an Ask me step.
    const strip = page.getByRole('toolbar', { name: 'Add a step' });
    await strip.getByRole('button', { name: 'Agent' }).click();
    await page.getByLabel('Brief', { exact: true }).fill('Write digest.md about {{inputs.topic}}.');
    await page.getByRole('button', { name: 'Add file' }).click();
    await page.getByLabel('Publish to the Library 1').fill('digest.md');
    await strip.getByRole('button', { name: 'Ask me' }).click();
    await page.getByLabel('Question', { exact: true }).fill('Publish the digest?');
    await page.getByText('Ready to save').waitFor({ timeout: 10_000 });
    await shot(page, 'a1-design');

    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await page.waitForFunction(() => /^#\/p\/[^/]+\/automations\/[^/?]+$/.test(window.location.hash), undefined, { timeout: 10_000 });
    const [automation] = await client.automations.list(project.id);
    expect(automation).toMatchObject({ name: 'digest', version: 1 });
    const saved = await client.automations.get(automation!.id);
    expect(saved.definition.inputs).toMatchObject([{ key: 'topic', label: 'Topic' }]);
    expect(saved.definition.edges).toEqual([{ from: 'agent', to: 'ask' }]);

    // Run now with an input: the run view opens and the graph lights up.
    await page.getByRole('button', { name: 'Run now…' }).click();
    const runDialog = page.getByRole('dialog', { name: /^Run / });
    await runDialog.getByLabel('Topic').fill('robots');
    await runDialog.getByRole('button', { name: 'Run', exact: true }).click();
    await page.getByRole('heading', { name: 'Run #1' }).waitFor({ timeout: 10_000 });
    await expect.poll(() => page.getByTestId('node-agent').getAttribute('class'), { timeout: 30_000 }).toMatch(/run-(run|ok)/);
    await expect.poll(() => page.getByTestId('node-ask').getAttribute('class'), { timeout: 30_000 }).toMatch(/run-wait/);
    await shot(page, 'a2-waiting');

    // The run view opens on the waiting step: answer it there.
    const answer = page.getByRole('region', { name: 'Your answer' });
    await answer.getByText('Publish the digest?').waitFor();
    await answer.getByRole('button', { name: 'Approve' }).click();
    await page.locator('.auto-run-head').getByText('succeeded').waitFor({ timeout: 30_000 });
    expect(await page.getByTestId('node-ask').getAttribute('class')).toMatch(/run-ok/);
    await shot(page, 'a3-succeeded');

    // The agent step's file, in its panel and in the Library.
    await page.getByTestId('node-agent').click();
    await page.getByRole('region', { name: 'Files' }).getByRole('button', { name: 'digest.md' }).waitFor({ timeout: 10_000 });
    await expect
      .poll(async () => (await client.library.list(project.id)).map((a) => a.path).find((p) => p.endsWith('/digest.md')) ?? null, { timeout: 15_000 })
      .toMatch(/^automations\/digest\/.+\/digest\.md$/);
    const [entry] = await client.automations.runs(automation!.id);
    expect(entry).toMatchObject({ kind: 'run', run: { number: 1, status: 'succeeded', inputs: { topic: 'robots' } } });
  });
});
```

- [ ] **Step 2: Run it**

Run: `pnpm --filter @desk/desktop build && pnpm vitest run --config vitest.e2e.config.ts apps/desktop/e2e/automations.e2e.test.ts`
Expected: PASS. It opens a window; nothing else heavy may run meanwhile (the 8 GB rule).

If it fails, fix what it found. Do not loosen the test.
- A label mismatch means a task above diverged from this plan: use the label the component really has, and check its unit test agrees.
- A step agent that writes elsewhere means `write_file`'s relative path is not the step folder: read `packages/core/src/automations/agent-step.test.ts` for where step agents write, and write there.

Run: `pnpm test:e2e`
Expected: the whole suite passes, the new file included.

- [ ] **Step 3: The docs**

In `docs/desktop.md`, in the Screens table, add a row after **Threads**:

```markdown
| **Automations** | A project tab listing its automations. Each row has its switch, its schedules, its last run, and a note for untested changes, suspended grants or Desk's turn-on request; there are Blank automation, Import and Export. One automation has four sub-tabs. **Design** is a graph editor (React Flow): the Start pill with schedules and inputs, one node per step, routes on edges, an inspector for the selection, a strip to add steps, Tidy up, live validation, and Save with a change note (a newer version saved meanwhile shows the diff first). **Runs** lists runs and skipped schedule times. A run shows the graph lit by each step's state, and a step panel with its live output, the question or script gate to answer, its results, files and log, Stop step, and Ask Desk to fix. **Versions** shows who saved each version, compares any two and restores. **Grants** shows where each rule came from, edits them and widens web domains; while grants are suspended it shows what changed, with Keep grants. Run now… and Test… open a form built from the inputs. Turn on shows the next times, the proposed grants and the untested warning. |
```

In the **Attention** row, append:

```markdown
 Automations have cards of their own: an Ask me question or script gate answered in place, a failed run (Ask Desk to fix), Desk's turn-on request (Turn on…) and suspended grants (Review changes). A step agent's approval names its automation and step, and offers Approve and remember for this automation unless its grants are suspended.
```

In Architecture, under **`src/renderer`**, add after the hash-router bullet:

```markdown
  - `automations/`: the Automations tab: the list and header, `design/` (the React Flow canvas, its nodes and the inspectors), `runs/` (the run list, the run view and the step panel), `versions/`, `grants/` and `dialogs/`. Its logic is in `@desk/ui-core`'s `automation-*.ts`, so the web UI (Plan 21) shares it.
```

In the end-to-end table, add:

```markdown
| `automations.e2e.test.ts` | A Blank automation built in the editor (an input, an agent step that publishes a file, an Ask me step under it), saved, and run now with an input. The graph lights up, the Ask me step is answered in the run view, the run succeeds, and the file is in the step panel and the Library |
```

In `CLAUDE.md`:
- In the `packages/ui-core` bullet, change `…, waits, and `createStore`.` to:

```markdown
…, waits, `createStore`, and automations (`automation-*.ts`: formats, schedules, grants, run inputs, live updates, the graph and its dagre layout, the editor's draft operations, issues, template suggestions, step fields, the Start card, version diffs and run graphs; fixtures at `@desk/ui-core/testing`).
```

- In the `@desk/bff/server` bullet, append: `` `run-folder.ts` (`existingRunFolder`: a run's folder, or one step's, for `app.revealPath`). ``
- In the `src/renderer/` bullet, append: `` `automations/` is the Automations tab (spec `2026-09-26-automations-design.md` §8). ``
- In Invariants, after the parity bullet (the one that begins "UI features ship in both UIs."), add:

```markdown
- Until Plan 21 ports the Automations screens to the web UI (branch `automations-ui`), the parity guard's `PLAN_21_OPS` also exempts the `automations.*` operations, `app.pickFile` and `app.revealPath`, and the web Automations tab is a placeholder. Plan 21 removes both before the branch merges.
```

In `docs/superpowers/specs/2026-09-26-automations-design.md`, change the status line to:

```markdown
- **Status:** backend implemented (Plan 19); desktop app implemented (Plan 20, branch `automations-ui`); the web UI port (Plan 21) follows on the same branch, and both merge together.
```

- [ ] **Step 4: Verify and commit**

Run: `pnpm test`
Expected: PASS.

Run: `pnpm typecheck`
Expected: no errors.

```bash
git add apps/desktop/e2e/automations.e2e.test.ts docs/desktop.md CLAUDE.md docs/superpowers/specs/2026-09-26-automations-design.md
git commit -m "test(desktop): automations end to end; docs: the Automations tab, Plan 21's parity exemption

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Exit check

Plan 20 is done when:
1. `pnpm test`, `pnpm typecheck` and `pnpm test:e2e` pass on `automations-ui`.
2. `pnpm desktop` shows each piece of spec §8:
   - the Automations tab (§8.1);
   - the editor: add, connect, inspect, validate, save, and a conflict (§8.2);
   - a live run with the step panel (§8.3);
   - Versions, Grants, the Run and Turn-on dialogs, and the Attention cards (§8.4).

   Check each in light and dark, then close the app.
3. `git log master..automations-ui` holds only this plan's commits. The branch stays unmerged: Plan 21 ports the screens to Angular on it, removes `PLAN_21_OPS` and the placeholder, and then both merge.

## Deviations found while executing

- **Task 2.** Two more `HandlerContext`s exist: `apps/web-server/src/testing.ts` (the desk web test stub) and `apps/desktop/src/main/desktop.live.test.ts`. Both gained `pickFile` and `revealRun`.
- **Task 8.** React Flow 12.12's pan-zoom reads `contentRect` from the ResizeObserver entry (`@xyflow/system`'s extent observer), so the jsdom shim in `test/reactflow.ts` passes one (800×600).
- **Task 12.** `v7` and `v6` also appear as options in the Compare menus, so `versions.test.tsx` reads the list's `<b>` labels (`{ selector: 'b' }`). The diff region renders at once and fills in once both versions load, so the test waits for its content (`findByText`). The second fix landed with Task 17, when the suite's timing exposed the race.
- **Task 18.**
  - Playwright's `getByLabel` matches substrings: "Publish to the Library 1" also matched the row's "Remove Publish to the Library 1" button. The e2e passes `{ exact: true }` for list rows and the input's Label and Key.
  - The e2e's screenshots showed a stale header ("waiting on you · New agent step" once the Ask me step was waiting): `runDetailRefetch` did not reload a run on its own step changes, so `at_step` kept the snapshot's value. It now reloads on them (debounced like every refetch). A unit test and an e2e assertion ("waiting on you · Ask me") cover it.
- **Tasks 7 and 13, "Look at it".** These were checked through the e2e's screenshots (`DESK_E2E_SHOTS`: the editor, the waiting run, the finished run) rather than `pnpm desktop`, which runs against the real data dir.
