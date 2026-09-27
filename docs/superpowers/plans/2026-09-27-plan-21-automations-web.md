# Plan 21 · Automations in the web UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The web UI (`desk web`) gets the Automations tab that Plan 20 built for the desktop app. It covers the list, one automation's Design, Runs, Versions and Grants, the dialogs, and the Attention cards. With that, the parity guard's `PLAN_21_OPS` exemption and the placeholder screen can go.

**Architecture:**
- Every React component in `apps/desktop/src/renderer/automations/` gets an Angular port in `apps/web-ui/src/app/automations/`. So do `attention/AutomationCards.tsx` and `conversation/draft.ts`. Each port keeps the same file name in kebab case and the same DOM and class names.
- All logic already lives in `@desk/ui-core` (`automation-*.ts`), and all styles in `@desk/ui-styles/automations.css`.
- React Flow has no Angular version, so the web canvas is hand-built:
  - the geometry (fit view, zoom around a point, React Flow's bezier edges, handle anchors, drop targets) goes in a new framework-neutral `@desk/ui-core` module, `automation-canvas.ts`;
  - the Angular `GraphCanvas` renders React Flow's DOM class names on a `.desk-flow` root, so the shared CSS (and a small web-only structural block in `automations.css`) gives it the desktop's look.
- `app.pickFile` becomes the web folder browser in a file mode: `fs.listDirs` learns to list files, the same way `app.pickFolder` already works on the web.

**Tech Stack:** Angular 22.2 (zoneless, standalone components, signals, OnPush), TypeScript ~6.0 (ngc, strict templates), Vitest 5 through `@angular/build:unit-test` with jsdom, `@testing-library/angular` 19.5, Playwright's Chromium for the web e2e, and Vitest at the root for ui-core and web-server.

**Spec:** `docs/superpowers/specs/2026-09-26-automations-design.md` §8 (the UI). The port rules come from `docs/superpowers/specs/2026-09-25-angular-web-ui-design.md` and Plan 17's "Port conventions" (`docs/superpowers/plans/2026-09-25-plan-17-angular-web-ui.md`). The React sources being ported are Plan 20's (`docs/superpowers/plans/2026-09-27-plan-20-automations-desktop.md`), now on master.

## Global Constraints

- `pnpm typecheck` and `pnpm test` pass before every commit. Commit only your own files, by path (never `git add -A`): `~/desk` is a shared checkout.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work on the branch `automations-web`, created from master (`git switch -c automations-web master` the first time). Nothing merges to master until the user says so.
- Every `apps/web-ui` command (Angular CLI, `ngc`, its specs and build) goes through `scripts/ng.mjs`. In a Claude shell, prefix `export PATH=~/.nvm/versions/node/v22.23.3/bin:$PATH`.
- A UI feature ships in both UIs, from the same ui-core logic and ui-styles CSS. The desktop's behaviour does not change: its unit and e2e tests stay as they are.
- `apps/web-ui` never turns text into HTML: no `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `setHTMLUnsafe`, `parseHTMLUnsafe`, `DOMParser`, `DomSanitizer`, `bypassSecurityTrust*`, `srcdoc`, `eval` or `document.write` (`security.spec.ts`). Agent text goes through `SafeMarkdown` or plain interpolation.
- Every web UI operation is called by its literal name (`this.bridge.call('automations.get', …)`), so `parity.spec.ts` can read it.
- No colour literals outside `tokens.css` (`packages/ui-styles/src/tokens.test.ts` scans `apps/web-ui/src` too). The canvas's colours are the `--xy-*` variables `automations.css` maps to tokens.
- Only the user turns automations on or sets their grants. The web UI calls `automations.setEnabled` and `automations.setGrants` only from the user's clicks, as the desktop does.
- Neither UI sees the daemon token. The web UI reaches desk web only through `DeskBridge`.
- The machine is shared (8 GB): at most 2 agents at once, and one Chromium or Electron run at a time. Check `df -h ~` before installing, building or running e2e, and stop if less than 500 MB is free.
- TypeScript is strict with `noUncheckedIndexedAccess`, in `ngc` as well.

## Port conventions (Plan 17's, repeated where this plan relies on them)

- **Component shape.**
  - Standalone, with `changeDetection: ChangeDetectionStrategy.OnPush` and `encapsulation: ViewEncapsulation.None`.
  - Inputs and outputs use `input()` / `input.required()` / `output()`. Templates use `@if` / `@for` / `@switch`.
  - Host bindings and listeners go in `host`, never in decorators.
- **No wrapper element.** The attribute selector sits on the React component's root element (`aside[deskStepInspector]`, `header[deskAutomationHeader]`). So the host is the root, and the shared CSS applies unchanged.
  - A React component that renders a fragment gets a `display: contents` host.
  - A component whose React root is a `Sheet` (the dialogs) is a `display: contents` `div` holding the `div[deskSheet]`, like `ConfirmDialog`.
- **Names.**
  - Class names are the React names, and file names are those names in kebab case.
  - Inputs keep the React prop names.
  - A React value-and-setter pair becomes an input plus an output: `onChange` becomes `change`… but outputs are never named after bubbling DOM events (`select`, `click`, `change`, `input`, `submit`). So here:
    - `onChange(doc)` becomes `docChange`, and `onChange(detail)` becomes `detailChange`;
    - `onSelect` becomes `pick`, `onClose` becomes `close`, `onConfirm` becomes `confirm`, `onDelete` becomes `remove`;
    - the rest keep their name without `on` (`started`, `done`, `testFirst`, `renamed`, `saveMine`, `discardMine`, `moved`, `connect`).
  - An input named like a native attribute (`title`, `id`) makes the host drop that attribute (`'[attr.title]': 'null'`, `'[attr.id]': 'null'`).
- **Classes.** When a React `className` mixes fixed and dynamic parts, the fixed classes go in `class` and only the dynamic ones in `[class]` or `[class.x]`, so the element reads as React's does (`class="auto-node" [class]="kindClass()" [class.selected]="…"`).
- **Signals.**
  - A `linkedSignal` of user-editable state derives from a `computed` primitive or an id, never from a whole object that is rebuilt on every push. For example, `idDraft = linkedSignal(() => this.stepId())`.
  - React's `useEffect` + fetch with a `live` flag becomes an `effect` that reads its keys, bumps a sequence number and ignores stale answers.
- **Shared services.**
  - `inject(ToastService)`: `.toast({…})` and `.error(err)` stand in for the desktop's `toast` and `toastError`.
  - `inject(RouteService).navigate(route)` stands in for `navigate`.
  - `inject(NowService).now` stands in for `useNow()`.
  - `inject(DeskBridge).call('<op>', …)` stands in for `call`, and `DeskCallError` comes from `../core/desk-bridge`.
- **Selects.** A `<select>` binds `[selected]` on each `<option>`, never `[value]` on the select (the idiom of `settings-fields.ts` and `skill-panel.ts`). That way an option that arrives after the first render (skills, scripts, automation names) still shows as chosen. Its `(change)` reads `($event.target as HTMLSelectElement).value` through a `val($event)` helper.
- **Braces and angle brackets in text.** A template's text cannot hold `{{` (it starts an interpolation) or `<id>` (it parses as an element). Such copy, like "{{steps.<id>.outputs.<key>}}", lives in a `protected readonly` constant and is interpolated.
- **Spaces between elements.** Angular drops whitespace-only text between two elements. So where JSX keeps a space between inline tags on one line (`<span>…</span> <b>…</b>`), the template writes `&ngsp;` (`<span>…</span>&ngsp;<b>…</b>`). Text next to an element keeps its own space (`Open run <kbd>E</kbd>`).
- **Remounting.** React's `key={…}` remounts a component when an identity changes (`<DesignView key={detail.id}>`, `<StepInspector key={sel.id}>`, `<RunView key={runId}>`, `<StepPanel key={sel.id}>`). The port renders it inside `@for (k of [key]; track k)`, as `App` does for screens, so its local state starts fresh.
- **Text.** Templates keep exact text tight (`<b>{{ … }}</b>`, no stray spaces). Several ported assertions compare `textContent`.
- **Specs.**
  - Specs are `*.spec.ts` next to the code, written with `@testing-library/angular`: `render(Component, { inputs, providers })`, or a template host when the element or its outputs matter.
  - Every spec uses `new FakeDeskBridge(handlers)` with `providers: bridge.providers`. A spec that renders `injectSession` also provides `{ provide: SESSION_RELEASE_DELAY, useValue: 0 }`.
  - A ported React test keeps its cases, visible text and roles. `fireEvent.change` on a text box becomes `fireEvent.input`; on a `select` or checkbox it stays `change`.
  - Find dialogs with `findByRole`, because `Sheet` moves itself into `document.body` after its first render.
- **Runs.** While iterating, run only the named specs, then the typecheck:
  ```bash
  (cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include <spec>)
  pnpm --filter @desk/web-ui typecheck
  ```
  At the root, run `pnpm vitest run <files> --maxWorkers=2`.

## Decisions recorded while planning

- **The canvas is hand-built** (the user chose it over ngx-vflow and over `@xyflow/system`).
  - Positions come from the layout, and node sizes are fixed (`NODE_W`×`NODE_H`, `START_W`×`START_H`), so handle anchors and edges are computed and never measured.
  - React Flow's bezier and fit-view formulas are reproduced exactly, from `@xyflow/system` 0.0.83's `getBezierPath` and `getViewportForBounds`.
  - The canvas supports what the desktop's `GraphCanvas` uses:
    - pan by dragging the background, and zoom with the wheel, around the pointer;
    - Zoom In, Zoom Out and Fit View buttons;
    - drag a node in edit mode;
    - connect by dragging from a step's bottom handle to another step's top handle, within React Flow's 20 px connection radius, highlighting the handle `valid` or `invalid`;
    - select a node, an edge or the pane by clicking;
    - Delete or Backspace on the selection in edit mode;
    - the dotted background.
  - It leaves out what the desktop never turns on: selection boxes, multi-select, the minimap and the attribution link (React Flow's own).
- **The same DOM class names.**
  - The Angular canvas renders React Flow's class names (`react-flow`, `react-flow__pane`, `react-flow__viewport`, `react-flow__node`, `react-flow__handle`, `react-flow__edge-path`, `react-flow__controls`…), so the rules in `automations.css` apply unchanged, including `.auto-canvas .react-flow`'s `--xy-*` token mapping and the handle sizes.
  - The structural rules React Flow's own stylesheet gives the desktop are copied into `automations.css` under `.desk-flow`, a class only the web canvas has. They use the `--xy-*` variables with token fallbacks and no colour literals.
- **Nodes are components, edges are not.**
  - React's `StepNode`, `StartNode` and `StubNode` become `div[deskStepNode]`, `div[deskStartNode]` and `div[deskStubNode]`.
  - React's `RouteEdge` has no component of its own. Its path sits in the canvas's `<svg>`, and its label sits in the label layer, which is React's `EdgeLabelRenderer` portal. An Angular component inside an `<svg>` would need an `svg:g` host, and the label would still have to render elsewhere.
- **Clicks select; pointer moves drag.**
  - The canvas selects on `click`, and pans, drags and connects with pointer events whose `pointermove` and `pointerup` listeners sit on `window` (jsdom has no pointer capture).
  - A click that ends a drag or a pan is swallowed, as d3's is.
  - `pointermove` listeners are added with `addEventListener`, never in the template (Plan 17's high-frequency-event rule).
- **`app.pickFile` on the web** is the folder browser in file mode.
  - `DeskBridge` answers `app.pickFile` in the browser, as it does `app.pickFolder`: `folderRequest()` becomes `{ purpose, file: true }`.
  - `FolderBrowser` lists files too (`fs.listDirs` gains `files: true`, with the same confinement), a click puts a file's path in the box, and "Choose this file" returns it.
  - As with folders, a typed absolute path is returned as typed, and deskd checks it (`automations.run` copies the file into the run folder).
- **`s: SessionState` is an input.** `AutomationsScreen` holds the session through `injectSession` and passes `s()` down, as the React screen passes `s`. Components that React tests with `sessionOf(events)` take `s` as an input, and their specs pass `sessionOf(...)` from a new `testing/session.ts`.
- **Transcripts from `s`.** `StepPanel` and `StepTranscript` fold an agent's transcript from `s` with the existing `transcriptOf(events, stream, projectId, agentId)` from `core/session.service.ts`, the desktop's `useTranscript(s, …)`.
- **Order.** The leaves come first (geometry, the file picker, live data, the canvas, fields, inspectors, the Design view, versions, dialogs, runs, grants), then the screen that wires them. So no task ships a placeholder body. Attention, parity, docs and the e2e come last.

## File Structure

### `packages/ui-core`

- Create `src/automation-canvas.ts`: the canvas geometry (viewport, anchors, bezier, bounds, fit, zoom at a point, wheel steps, drop targets, the dot pattern), with its test `src/automation-canvas.test.ts`.
- Modify `src/index.ts`: add `export * from './automation-canvas';`.

### `packages/ui-styles`

- Modify `src/automations.css`: add the web canvas's structural block, `.desk-flow …`.

### `apps/web-server`

- Modify `src/web-channels.ts`: `fs.listDirs` takes `files?: boolean`, and `DirListing` gains `files?: Array<{ name; path }>`.
- Modify `src/list-dirs.ts` and `src/list-dirs.test.ts`: list files when asked.

### `apps/web-ui/src/app`

- Modify `core/desk-bridge.ts` and `core/desk-bridge.spec.ts`: `app.pickFile` opens a file request.
- Modify `testing/fake-bridge.ts`: the same, without a handler.
- Modify `components/folder-browser.ts` and `components/folder-browser.spec.ts`: file mode.
- Modify `app.ts`: pass `[file]` to the folder browser.
- Create `testing/session.ts`: `sessionOf`.
- Create `conversation/draft.ts`: `draftKey`, `primeDraft`. Modify `conversation/conversation-screen.ts` to import `draftKey` from it.
- Create in `automations/`:
  - `live.ts` + `live.spec.ts`: `injectLive`.
  - `data.ts`: `injectAutomationList`, `injectAutomation`, `injectRuns`, `injectRun`.
  - `automations-screen.ts` + `automations-screen.spec.ts`: `AutomationsScreen`, `OneAutomation`, `DraftAutomation`.
  - `automation-list.ts`: `AutomationList`.
  - `automation-header.ts`: `AutomationHeader`, `DraftHeader`.
  - `dialogs/name-dialog.ts`, `dialogs/run-dialog.ts` (`RunDialog`, `InputField`), `dialogs/turn-on-dialog.ts`, `dialogs/dialogs.spec.ts`.
  - `design/`:
    - `graph-canvas.ts` + `graph-canvas.spec.ts`, and `nodes.ts` (`StepNode`, `StartNode`, `StubNode`);
    - `template-field.ts` + `template-field.spec.ts`, `list-editor.ts`, `pickers.ts`;
    - `step-kind-fields.ts`, `step-inspector.ts`, `edge-inspector.ts`, `inspectors.spec.ts`;
    - `start-inspector.ts` (`StartInspector`, `ScheduleEditor`, `InputEditor`), `settings-inspector.ts`, `start.spec.ts`;
    - `conflict-dialog.ts`, `design-view.ts` + `design-view.spec.ts`.
  - `versions/diff-view.ts`, `versions/versions-view.ts` + `versions/versions.spec.ts`.
  - `runs/`:
    - `run-file-list.ts`, `step-transcript.ts`;
    - `step-panel.ts` (`StepPanel`, `AskAnswer`, `GateAnswer`, `AgentApproval`, `AgentPart`, `Failed`, `Results`, `ScriptLog`, `StepFiles`) + `step-panel.spec.ts`;
    - `runs-list.ts`, `run-view.ts` (`RunView`, `RunSide`), `runs.spec.ts`.
  - `grants/grants-view.ts` (`GrantsView`, `GrantEditor`, `SuspendedBanner`) + `grants/grants-view.spec.ts`.
- Delete `automations/automations-placeholder.ts`.
- Create `attention/automation-cards.ts` (`AutomationCard`, `injectAutomationItem`) + `attention/automations.spec.ts`. Modify `attention/inspector.ts` and `attention/attention-screen.ts`.
- Modify `screen-for.ts`, `screen-for.spec.ts` and `parity.spec.ts` (delete `PLAN_21_OPS`).

### `apps/web-ui/e2e`

- Create `automations.e2e.test.ts`.

### Docs

- Modify `CLAUDE.md`: the web UI bullet, and drop the `PLAN_21_OPS` invariant.
- Modify `docs/web.md`: the Automations tab. Modify `docs/desktop.md`: drop "(Plan 21)".
- Modify the spec's status line.
- Add this plan's "Deviations found while executing".

---

### Task 0: The branch

- [ ] **Step 1: Check the disk and create the branch**

```bash
df -h ~ | tail -1
cd ~/desk && git status --short && git switch -c automations-web master
```

Expected: at least 500 MB free. The untracked entries are this plan and `screen.png`; leave `screen.png` alone. Git says `Switched to a new branch 'automations-web'`.

- [ ] **Step 2: Baseline**

```bash
export PATH=~/.nvm/versions/node/v22.23.3/bin:$PATH
pnpm typecheck && pnpm test 2>&1 | grep -E "Test Files|Tests " | tail -4
```

Expected: typecheck passes, and all tests pass (1628 + 94 web spec files on master at c278dc9).

- [ ] **Step 3: Commit the plan on the branch**

```bash
git add docs/superpowers/plans/2026-09-27-plan-21-automations-web.md && git commit -m "docs(plan): Plan 21, the Automations screens in the web UI: canvas, editor, runs, versions, grants, Attention, e2e

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: `automation-canvas.ts`, the canvas geometry

**Files:**
- Create: `packages/ui-core/src/automation-canvas.ts`
- Create: `packages/ui-core/src/automation-canvas.test.ts`
- Modify: `packages/ui-core/src/index.ts`

**Interfaces:**
- Consumes: `GraphNode`, `GraphEdge`, `NODE_W`, `NODE_H`, `START_W`, `START_H`, `toGraph` from `automation-graph.ts`.
- Produces (used by Task 4's `GraphCanvas`):
  - `type Viewport = { x: number; y: number; zoom: number }`, `type Point = { x: number; y: number }`, `type Rect = { x: number; y: number; width: number; height: number }`, `type Side = 'top' | 'bottom' | 'left' | 'right'`, `type Anchor = Point & { side: Side }`, `type EdgeGeometry = { path: string; labelX: number; labelY: number }`
  - `MIN_ZOOM = 0.3`, `MAX_ZOOM = 1.5`, `STUB_H = 18`, `HANDLE_HALF = 6`, `CONNECT_RADIUS = 20`
  - `nodeSize(n: GraphNode): { width: number; height: number }`
  - `sourceAnchor(n: GraphNode, handle?: string): Anchor`, `targetAnchor(n: GraphNode): Anchor`
  - `bezierPath(s: Anchor, t: Anchor, curvature?: number): EdgeGeometry`
  - `edgeGeometry(e: GraphEdge, byId: ReadonlyMap<string, GraphNode>): EdgeGeometry | null`
  - `graphBounds(nodes: readonly GraphNode[]): Rect`
  - `fitViewport(bounds: Rect, width: number, height: number, o?: { padding?: number; minZoom?: number; maxZoom?: number }): Viewport`
  - `zoomAt(v: Viewport, zoom: number, at: Point, min?: number, max?: number): Viewport`
  - `wheelFactor(deltaY: number, deltaMode: number, ctrlKey: boolean): number`
  - `toGraphPoint(v: Viewport, p: Point): Point`
  - `dropTarget(nodes: readonly GraphNode[], p: Point, radius: number): string | null`
  - `dotPattern(v: Viewport, gap?: number, size?: number): { x: number; y: number; cell: number; r: number }`

- [ ] **Step 1: Write the failing test**

`packages/ui-core/src/automation-canvas.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import {
  bezierPath,
  dotPattern,
  dropTarget,
  edgeGeometry,
  fitViewport,
  graphBounds,
  HANDLE_HALF,
  nodeSize,
  sourceAnchor,
  STUB_H,
  targetAnchor,
  toGraphPoint,
  wheelFactor,
  zoomAt,
} from './automation-canvas';
import { NODE_H, NODE_W, START_H, START_W, toGraph, type GraphNode } from './automation-graph';
import { digestDef } from './testing/automations';

const step = (id: string, x: number, y: number): GraphNode => ({ id, type: 'step', position: { x, y }, data: { step: digestDef().steps[0]!, kind: 'script', detail: '', errors: [], run: null, output: false } });

describe('the canvas geometry', () => {
  it('sizes each kind of node: steps and Start fixed, stubs by their label', () => {
    expect(nodeSize(step('a', 0, 0))).toEqual({ width: NODE_W, height: NODE_H });
    expect(nodeSize({ id: '__start', type: 'start', position: { x: 0, y: 0 }, data: { label: 'Run now only', errors: [] } })).toEqual({ width: START_W, height: START_H });
    const stub = nodeSize({ id: 's', type: 'stub', position: { x: 0, y: 0 }, data: { label: 'unchanged · ends' } });
    expect(stub.height).toBe(STUB_H);
    expect(stub.width).toBeGreaterThan(80);
  });

  it('anchors edges on the handles: out of the bottom (or the side, to a stub), into the top (or a stub’s left)', () => {
    const n = step('a', 100, 50);
    expect(sourceAnchor(n)).toEqual({ x: 100 + NODE_W / 2, y: 50 + NODE_H + HANDLE_HALF, side: 'bottom' });
    expect(sourceAnchor(n, 'side')).toEqual({ x: 100 + NODE_W + HANDLE_HALF, y: 50 + NODE_H / 2, side: 'right' });
    expect(targetAnchor(n)).toEqual({ x: 100 + NODE_W / 2, y: 50 - HANDLE_HALF, side: 'top' });
    expect(targetAnchor({ id: 's', type: 'stub', position: { x: 300, y: 56 }, data: { label: 'x · ends' } })).toEqual({ x: 300 - HANDLE_HALF, y: 56 + STUB_H / 2, side: 'left' });
  });

  it('draws React Flow’s bezier: half the gap forward, a curvature-scaled loop backward, and the label at t = 0.5', () => {
    expect(bezierPath({ x: 0, y: 0, side: 'bottom' }, { x: 100, y: 100, side: 'top' })).toEqual({ path: 'M0,0 C0,50 100,50 100,100', labelX: 50, labelY: 50 });
    expect(bezierPath({ x: 0, y: 100, side: 'bottom' }, { x: 0, y: 0, side: 'top' }).path).toBe('M0,100 C0,162.5 0,-62.5 0,0');
    expect(bezierPath({ x: 0, y: 0, side: 'right' }, { x: 40, y: 0, side: 'left' }).path).toBe('M0,0 C20,0 20,0 40,0');
  });

  it('computes an edge between two laid-out nodes, and nothing for a missing end', () => {
    const g = toGraph(digestDef(), {}, { startLabel: 's', selection: { kind: 'none' }, editable: true });
    const byId = new Map(g.nodes.map((n) => [n.id, n]));
    const e = g.edges.find((x) => x.source === 'fetch' && x.target === 'sum')!;
    const from = sourceAnchor(byId.get('fetch')!);
    expect(edgeGeometry(e, byId)!.path.startsWith(`M${from.x},${from.y} C`)).toBe(true);
    expect(edgeGeometry({ ...e, target: 'nope' }, byId)).toBeNull();
  });

  it('bounds the nodes and fits them like React Flow (padding 0.2, zoom capped at 1)', () => {
    expect(graphBounds([])).toEqual({ x: 0, y: 0, width: 0, height: 0 });
    const b = graphBounds([step('a', 0, 0), step('b', 300, 200)]);
    expect(b).toEqual({ x: 0, y: 0, width: 300 + NODE_W, height: 200 + NODE_H });
    expect(fitViewport({ x: 0, y: 0, width: 200, height: 64 }, 800, 600)).toEqual({ x: 300, y: 268, zoom: 1 });
    const wide = fitViewport({ x: 0, y: 0, width: 4000, height: 100 }, 800, 600);
    expect(wide.zoom).toBe(0.3);
  });

  it('zooms around a point, within the limits, and maps screen points back to the graph', () => {
    const v = zoomAt({ x: 0, y: 0, zoom: 1 }, 2, { x: 100, y: 100 }, 0.3, 1.5);
    expect(v.zoom).toBe(1.5);
    expect(toGraphPoint(v, { x: 100, y: 100 })).toEqual({ x: 100, y: 100 });
    const out = zoomAt({ x: 10, y: 20, zoom: 1 }, 0.5, { x: 0, y: 0 });
    expect(out).toEqual({ x: 5, y: 10, zoom: 0.5 });
    expect(zoomAt(out, 0.01, { x: 0, y: 0 }).zoom).toBe(0.3);
  });

  it('steps a wheel like d3-zoom: pixels, lines and pinch', () => {
    expect(wheelFactor(100, 0, false)).toBeCloseTo(2 ** -0.2);
    expect(wheelFactor(-3, 1, false)).toBeCloseTo(2 ** 0.15);
    expect(wheelFactor(10, 0, true)).toBeCloseTo(2 ** -0.2);
  });

  it('drops a connection on the nearest step’s top handle within the radius', () => {
    const nodes = [step('a', 0, 0), step('b', 0, 200), { id: '__start', type: 'start', position: { x: 0, y: -100 }, data: { label: 's', errors: [] } } as GraphNode];
    const top = targetAnchor(nodes[1]!);
    expect(dropTarget(nodes, { x: top.x + 5, y: top.y - 5 }, 20)).toBe('b');
    expect(dropTarget(nodes, { x: top.x + 50, y: top.y }, 20)).toBeNull();
    expect(dropTarget(nodes, targetAnchor(nodes[2]!), 20)).toBeNull();
  });

  it('scales the dot pattern with the zoom and offsets it with the pan', () => {
    expect(dotPattern({ x: 40, y: -10, zoom: 2 })).toEqual({ x: 4, y: -10, cell: 36, r: 1 });
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pnpm vitest run packages/ui-core/src/automation-canvas.test.ts`
Expected: FAIL, `Failed to resolve import "./automation-canvas"`.

- [ ] **Step 3: Write the module**

`packages/ui-core/src/automation-canvas.ts`:

```ts
import { NODE_H, NODE_W, START_H, START_W, type GraphEdge, type GraphNode } from './automation-graph';

/** The canvas's pan and zoom: a graph point p shows at (p.x × zoom + x, p.y × zoom + y). */
export type Viewport = { x: number; y: number; zoom: number };
export type Point = { x: number; y: number };
export type Rect = { x: number; y: number; width: number; height: number };
export type Side = 'top' | 'bottom' | 'left' | 'right';
/** Where an edge leaves or meets a node: the handle's outer edge, and the side it sits on. */
export type Anchor = Point & { side: Side };
export type EdgeGeometry = { path: string; labelX: number; labelY: number };

export const MIN_ZOOM = 0.3;
export const MAX_ZOOM = 1.5;
/** A route stub's height (automations.css: 10px mono, 2px padding, 1px border); its width follows its text. */
export const STUB_H = 18;
/** Half a handle's box (9px plus a 1.5px border each side): React Flow's edges end on the handle's outer edge. */
export const HANDLE_HALF = 6;
/** How near a target handle a dropped connection must land, in screen pixels (React Flow's connectionRadius). */
export const CONNECT_RADIUS = 20;
/** The width of one 10px mono character, for a stub's box in the canvas bounds. */
const STUB_CHAR_W = 6.2;

const between = (v: number, min: number, max: number): number => Math.min(max, Math.max(min, v));

/** A node's box. Steps and Start have fixed sizes (automations.css); a stub is as wide as its label. */
export function nodeSize(n: GraphNode): { width: number; height: number } {
  switch (n.type) {
    case 'step':
      return { width: NODE_W, height: NODE_H };
    case 'start':
      return { width: START_W, height: START_H };
    case 'stub':
      return { width: Math.ceil(n.data.label.length * STUB_CHAR_W) + 18, height: STUB_H };
  }
}

/** Where an edge leaves a node: its bottom handle, or its side handle (the edge to a route stub). */
export function sourceAnchor(n: GraphNode, handle?: string): Anchor {
  const { width, height } = nodeSize(n);
  if (handle === 'side') return { x: n.position.x + width + HANDLE_HALF, y: n.position.y + height / 2, side: 'right' };
  return { x: n.position.x + width / 2, y: n.position.y + height + HANDLE_HALF, side: 'bottom' };
}

/** Where an edge meets a node: its top handle, or a stub's left one. */
export function targetAnchor(n: GraphNode): Anchor {
  const { width, height } = nodeSize(n);
  if (n.type === 'stub') return { x: n.position.x - HANDLE_HALF, y: n.position.y + height / 2, side: 'left' };
  return { x: n.position.x + width / 2, y: n.position.y - HANDLE_HALF, side: 'top' };
}

const controlOffset = (distance: number, curvature: number): number => (distance >= 0 ? 0.5 * distance : curvature * 25 * Math.sqrt(-distance));

function control(a: Anchor, b: Point, curvature: number): Point {
  switch (a.side) {
    case 'left':
      return { x: a.x - controlOffset(a.x - b.x, curvature), y: a.y };
    case 'right':
      return { x: a.x + controlOffset(b.x - a.x, curvature), y: a.y };
    case 'top':
      return { x: a.x, y: a.y - controlOffset(a.y - b.y, curvature) };
    case 'bottom':
      return { x: a.x, y: a.y + controlOffset(b.y - a.y, curvature) };
  }
}

/** React Flow's bezier edge (`getBezierPath`, curvature 0.25): the SVG path, and its point at t = 0.5 for the label. */
export function bezierPath(s: Anchor, t: Anchor, curvature = 0.25): EdgeGeometry {
  const sc = control(s, t, curvature);
  const tc = control(t, s, curvature);
  return {
    path: `M${s.x},${s.y} C${sc.x},${sc.y} ${tc.x},${tc.y} ${t.x},${t.y}`,
    labelX: s.x * 0.125 + sc.x * 0.375 + tc.x * 0.375 + t.x * 0.125,
    labelY: s.y * 0.125 + sc.y * 0.375 + tc.y * 0.375 + t.y * 0.125,
  };
}

/** An edge between its nodes' handles, or null when a node is missing. */
export function edgeGeometry(e: GraphEdge, byId: ReadonlyMap<string, GraphNode>): EdgeGeometry | null {
  const s = byId.get(e.source);
  const t = byId.get(e.target);
  return s && t ? bezierPath(sourceAnchor(s, e.sourceHandle), targetAnchor(t)) : null;
}

/** The box around every node (all zeros for none). */
export function graphBounds(nodes: readonly GraphNode[]): Rect {
  if (!nodes.length) return { x: 0, y: 0, width: 0, height: 0 };
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const n of nodes) {
    const { width, height } = nodeSize(n);
    x0 = Math.min(x0, n.position.x);
    y0 = Math.min(y0, n.position.y);
    x1 = Math.max(x1, n.position.x + width);
    y1 = Math.max(y1, n.position.y + height);
  }
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

/** React Flow's fitView (`getViewportForBounds` with a numeric padding): the bounds centred, zoomed to fit within the limits. */
export function fitViewport(bounds: Rect, width: number, height: number, o: { padding?: number; minZoom?: number; maxZoom?: number } = {}): Viewport {
  const padding = o.padding ?? 0.2;
  const px = Math.floor((width - width / (1 + padding)) * 0.5);
  const py = Math.floor((height - height / (1 + padding)) * 0.5);
  const fit = Math.min((width - 2 * px) / Math.max(bounds.width, 1), (height - 2 * py) / Math.max(bounds.height, 1));
  const zoom = between(fit, o.minZoom ?? MIN_ZOOM, o.maxZoom ?? 1);
  return { x: width / 2 - (bounds.x + bounds.width / 2) * zoom, y: height / 2 - (bounds.y + bounds.height / 2) * zoom, zoom };
}

/** A new zoom (clamped) that keeps the graph point under `at` (screen, relative to the canvas) where it is. */
export function zoomAt(v: Viewport, zoom: number, at: Point, min = MIN_ZOOM, max = MAX_ZOOM): Viewport {
  const z = between(zoom, min, max);
  const k = z / v.zoom;
  return { zoom: z, x: at.x - (at.x - v.x) * k, y: at.y - (at.y - v.y) * k };
}

/** d3-zoom's wheel step, which React Flow uses: the zoom factor for one wheel event (ctrl is a trackpad pinch). */
export function wheelFactor(deltaY: number, deltaMode: number, ctrlKey: boolean): number {
  return 2 ** (-deltaY * (deltaMode === 1 ? 0.05 : deltaMode ? 1 : 0.002) * (ctrlKey ? 10 : 1));
}

/** A screen point (relative to the canvas) in graph coordinates. */
export function toGraphPoint(v: Viewport, p: Point): Point {
  return { x: (p.x - v.x) / v.zoom, y: (p.y - v.y) / v.zoom };
}

/** The step whose top handle is nearest `p` (graph coordinates) within `radius`, or null: where a dragged connection lands. */
export function dropTarget(nodes: readonly GraphNode[], p: Point, radius: number): string | null {
  let best: { id: string; d: number } | null = null;
  for (const n of nodes) {
    if (n.type !== 'step') continue;
    const a = targetAnchor(n);
    const d = Math.hypot(a.x - p.x, a.y - p.y);
    if (d <= radius && (!best || d < best.d)) best = { id: n.id, d };
  }
  return best?.id ?? null;
}

/** React Flow's dot background (gap 18, size 1) on screen: the pattern's offset and cell, and the dot's radius. */
export function dotPattern(v: Viewport, gap = 18, size = 1): { x: number; y: number; cell: number; r: number } {
  const cell = gap * v.zoom;
  return { x: v.x % cell, y: v.y % cell, cell, r: (size * v.zoom) / 2 };
}
```

Add `export * from './automation-canvas';` to `packages/ui-core/src/index.ts`. The list is sorted, so it goes first, above `automation-diff`:

```ts
export * from './automation-canvas';
export * from './automation-diff';
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pnpm vitest run packages/ui-core/src/automation-canvas.test.ts packages/ui-core/src/boundaries.test.ts`
Expected: PASS. Note that `-10 % 36` is `-10` in JavaScript, which the dot-pattern case expects.

- [ ] **Step 5: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add packages/ui-core/src/automation-canvas.ts packages/ui-core/src/automation-canvas.test.ts packages/ui-core/src/index.ts && git commit -m "feat(ui-core): the automation canvas's geometry: anchors, React Flow's bezier and fit view, zoom at a point, drop targets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 2: `app.pickFile` on the web: the folder browser picks a file

**Files:**
- Modify: `apps/web-server/src/web-channels.ts`
- Modify: `apps/web-server/src/list-dirs.ts`
- Modify: `apps/web-server/src/list-dirs.test.ts`
- Modify: `apps/web-ui/src/app/core/desk-bridge.ts`
- Modify: `apps/web-ui/src/app/core/desk-bridge.spec.ts`
- Modify: `apps/web-ui/src/app/testing/fake-bridge.ts`
- Modify: `apps/web-ui/src/app/components/folder-browser.ts`
- Modify: `apps/web-ui/src/app/components/folder-browser.spec.ts`
- Modify: `apps/web-ui/src/app/app.ts`

**Interfaces:**
- Produces:
  - `fs.listDirs` input `{ path?, hidden?, files?: boolean }` → `DirListing` with `files?: Array<{ name: string; path: string }>` (present only when `files` was asked).
  - `FolderRequest = { purpose: FolderPurpose; file?: true }`.
  - `DeskBridge.call('app.pickFile', { purpose: 'automation-input' })` resolves with the chosen path or null.
  - `FolderBrowser` input `file = input(false)`.
- Consumed by Task 10's `RunDialog` (`app.pickFile`, `app.pickFolder` with purpose `automation-input`).

- [ ] **Step 1: Write the failing server test**

Append inside `describe('fs.listDirs', …)` in `apps/web-server/src/list-dirs.test.ts`:

```ts
  it('lists files too when asked, hidden ones only with hidden, and never through a link that leaves the allowed folders', async () => {
    const { home, outside, deps } = layout();
    writeFileSync(join(home, '.env'), 'x');
    writeFileSync(join(outside, 'secret', 'key.txt'), 'x');
    const l = await listDirs({ files: true }, deps);
    expect(l.files).toEqual([{ name: 'notes.txt', path: join(home, 'notes.txt') }]);
    expect(names(l)).toEqual(['code', 'code-link', 'Documents', 'Library']);
    expect((await listDirs({}, deps)).files).toBeUndefined();
    expect((await listDirs({ files: true, hidden: true }, deps)).files?.map((f) => f.name)).toEqual(['.env', 'notes.txt']);
    // File symlinks need privileges on Windows; the confinement is the folders' own, which the tests above cover there.
    if (process.platform !== 'win32') {
      symlinkSync(join(outside, 'secret', 'key.txt'), join(home, 'key-link.txt'));
      symlinkSync(join(home, 'notes.txt'), join(home, 'notes-link.txt'));
      const linked = await listDirs({ files: true }, deps);
      expect(linked.files?.map((f) => f.name).sort()).toEqual(['notes-link.txt', 'notes.txt']);
      expect(linked.files?.find((f) => f.name === 'notes-link.txt')?.path).toBe(join(home, 'notes.txt'));
    }
  });
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pnpm vitest run apps/web-server/src/list-dirs.test.ts`
Expected: FAIL. `files` is undefined, and TypeScript flags `files` as unknown in the input only under typecheck.

- [ ] **Step 3: Implement the server side**

In `apps/web-server/src/web-channels.ts`, replace the `fs.listDirs` schema and `DirListing`:

```ts
  /** Folder names (and file names when asked), under the home folder or a project source, never the Desk data dir (the folder browser). */
  'fs.listDirs': z.object({ path: z.string().min(1).max(4096).optional(), hidden: z.boolean().optional(), files: z.boolean().optional() }),
```

```ts
/** One folder listing; `parent` is null when the folder above may not be browsed. `files` is there when asked for (file pickers). */
export type DirListing = { path: string; parent: string | null; dirs: Array<{ name: string; path: string }>; files?: Array<{ name: string; path: string }> };
```

In `apps/web-server/src/list-dirs.ts`:
- Change the signature to `export async function listDirs(input: { path?: string | undefined; hidden?: boolean | undefined; files?: boolean | undefined }, d: ListDirsDeps): Promise<DirListing>`.
- Update its doc comment to `The folders in `path` (home by default), and its files when `input.files`, for the folder browser. …` and keep the rest of the comment.
- Replace the loop and the return with:

```ts
  const dirs: DirListing['dirs'] = [];
  const files: NonNullable<DirListing['files']> = [];
  for (const e of entries) {
    if (!input.hidden && e.name.startsWith('.')) continue;
    const link = e.isSymbolicLink();
    if (!e.isDirectory() && !link && !(input.files && e.isFile())) continue;
    const path = await real(join(target, e.name));
    if (!path || !allowed(path)) continue;
    const info = link ? await stat(path).catch(() => null) : null;
    if (link ? info?.isDirectory() : e.isDirectory()) dirs.push({ name: e.name, path });
    else if (input.files && (link ? info?.isFile() : e.isFile())) files.push({ name: e.name, path });
  }
  const byName = (a: { name: string }, b: { name: string }) => a.name.localeCompare(b.name, 'en', { sensitivity: 'base' });
  dirs.sort(byName);
  files.sort(byName);
  const up = dirname(target);
  return { path: target, parent: up !== target && allowed(up) ? up : null, dirs, ...(input.files ? { files } : {}) };
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pnpm vitest run apps/web-server/src/list-dirs.test.ts apps/web-server/src/contract.test.ts apps/web-server/src/rpc.test.ts`
Expected: PASS.

- [ ] **Step 5: Write the failing web specs**

In `apps/web-ui/src/app/core/desk-bridge.spec.ts`, add after the case `'asks the folder browser for a folder, one request at a time'` (same `describe`, which defines `fetchMock`):

```ts
  it('asks the folder browser for a file for app.pickFile', async () => {
    const bridge = TestBed.inject(DeskBridge);
    const picked = bridge.call('app.pickFile', { purpose: 'automation-input' });
    expect(bridge.folderRequest()).toEqual({ purpose: 'automation-input', file: true });
    bridge.answerFolder('/Users/me/brief.pdf');
    await expect(picked).resolves.toBe('/Users/me/brief.pdf');
    expect(bridge.folderRequest()).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
```

Append to `apps/web-ui/src/app/components/folder-browser.spec.ts`:

```ts
describe('FolderBrowser for a file', () => {
  const withFiles = (input: ListInput & { files?: boolean }): DirListing => ({ ...listDirs(input), ...(input.files ? { files: [{ name: 'brief.pdf', path: '/Users/me/brief.pdf' }] } : {}) });

  async function setupFile() {
    const bridge = new FakeDeskBridge({ 'fs.listDirs': withFiles });
    const picked: Array<string | null> = [];
    await render(FolderBrowser, { inputs: { purpose: 'automation-input', file: true }, providers: bridge.providers, on: { picked: (p: string | null) => picked.push(p) } });
    const dialog = await screen.findByRole('dialog', { name: 'Choose a file' });
    return { bridge, picked, dialog, user: userEvent.setup() };
  }

  it('lists files beside folders, puts a clicked file in the box, and chooses it', async () => {
    const { bridge, picked, dialog, user } = await setupFile();
    expect(within(dialog).getByRole('list', { name: 'Folders in /Users/me' })).toBeTruthy();
    await user.click(await within(dialog).findByRole('button', { name: 'brief.pdf' }));
    expect((within(dialog).getByLabelText('Path') as HTMLInputElement).value).toBe('/Users/me/brief.pdf');
    expect(within(dialog).getByRole('button', { name: 'brief.pdf' }).getAttribute('aria-pressed')).toBe('true');
    await user.click(within(dialog).getByRole('button', { name: 'Choose this file' }));
    expect(picked).toEqual(['/Users/me/brief.pdf']);
    expect(bridge.calls[0]?.input).toEqual({ hidden: false, files: true });
  });

  it('asks for a file while the box holds the folder it shows, and takes a typed full path as it is', async () => {
    const { picked, dialog, user } = await setupFile();
    await within(dialog).findByRole('button', { name: 'brief.pdf' });
    await user.click(within(dialog).getByRole('button', { name: 'Choose this file' }));
    expect(within(dialog).getByRole('alert').textContent).toBe('Pick a file in the list, or type its full path.');
    expect(picked).toEqual([]);
    const box = within(dialog).getByLabelText('Path') as HTMLInputElement;
    await user.clear(box);
    await user.type(box, '/Volumes/share/brief.pdf');
    await user.click(within(dialog).getByRole('button', { name: 'Choose this file' }));
    expect(picked).toEqual(['/Volumes/share/brief.pdf']);
  });
});
```

- [ ] **Step 6: Run them to verify they fail**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/core/desk-bridge.spec.ts --include src/app/components/folder-browser.spec.ts)`
Expected: FAIL. `app.pickFile` goes to `/rpc`, and `file` is not an input of `FolderBrowser`.

- [ ] **Step 7: Implement the web side**

In `apps/web-ui/src/app/core/desk-bridge.ts`:
- `FolderRequest` becomes:

```ts
/** An open `app.pickFolder` (or, with `file`, `app.pickFile`) request, shown by the folder browser. */
export type FolderRequest = { purpose: FolderPurpose; file?: true };
```

- In `call`, after the `app.pickFolder` line:

```ts
      if (op === 'app.pickFile') return this.pickFolder((input as ChannelInput<'app.pickFile'>).purpose, true);
```

- `pickFolder` becomes:

```ts
  private pickFolder(purpose: FolderPurpose, file = false): Promise<string | null> {
    this.answerFolder(null);
    return new Promise((resolve) => {
      this.settleFolder = resolve;
      this.folderState.set(file ? { purpose, file: true } : { purpose });
    });
  }
```

- The `folderRequest` doc comment becomes `/** The open \`app.pickFolder\` or \`app.pickFile\` request, for the folder browser; \`answerFolder\` settles it. */`.

In `apps/web-ui/src/app/testing/fake-bridge.ts`:
- In `call`, after the `app.pickFolder` line:

```ts
      if (op === 'app.pickFile') return this.pickFolder((input as { purpose: FolderPurpose }).purpose, true);
```

- Its `pickFolder(purpose: FolderPurpose, file = false)` sets `this.folderState.set(file ? { purpose, file: true } : { purpose })`.
- The class comment says `app.pickFolder` or `app.pickFile` without one opens a folder request.

In `apps/web-ui/src/app/components/folder-browser.ts`:
- Add `readonly file = input(false);` after `purpose`.
- The heading becomes:

```ts
  protected readonly heading = computed(() => (this.file() ? 'Choose a file' : this.purpose() === 'skill-import' ? 'Choose a skill folder' : 'Choose a folder'));
```

- In the template, the path field and the Choose button follow the mode:

```html
        <div deskField id="folder-path" [label]="file() ? 'Path' : 'Folder'" [hint]="file() ? 'Type a full path, or click a file below.' : 'Type a full path, or ~ for your home folder.'">
```

```html
        <button deskButton variant="primary" [pending]="loading() || checking()" (click)="choose()">{{ file() ? 'Choose this file' : 'Choose this folder' }}</button>
```

- After the folders list's `@if (l.dirs.length) { … } @else { … }` block, still inside `@if (listing(); as l)`:

```html
        @if (file()) {
          @let files = l.files ?? [];
          @if (files.length) {
            <ul class="sources folder-list" [attr.aria-label]="'Files in ' + l.path">
              @for (f of files; track f.path) {
                <li><button type="button" class="link mono" [attr.aria-pressed]="typed() === f.path" (click)="pickFile(f.path)">{{ f.name }}</button></li>
              }
            </ul>
          } @else {
            <p class="field-hint">No files here.</p>
          }
        }
```

- `list()` asks for files in file mode:

```ts
      const l = await this.bridge.call('fs.listDirs', { ...(path ? { path } : {}), hidden: this.hidden(), ...(this.file() ? { files: true } : {}) });
```

- Add the method, and file mode at the top of `choose()`:

```ts
  /** File mode: a click puts the file's path in the box; Choose returns it. */
  protected pickFile(path: string): void {
    this.typed.set(path);
    this.error.set(null);
  }
```

```ts
  protected async choose(): Promise<void> {
    if (this.file()) {
      // A listed file's path, or a typed full path, which deskd checks when the run starts (it copies the file).
      const typed = this.typed().trim();
      if (typed && isAbsolutePath(typed) && typed !== this.listing()?.path) this.picked.emit(typed);
      else this.error.set('Pick a file in the list, or type its full path.');
      return;
    }
    // …the folder mode, unchanged
```

- The class comment gains one sentence: `With \`file\`, it answers \`app.pickFile\`: files are listed too, and one is chosen.`

In `apps/web-ui/src/app/app.ts`, the folder browser line becomes:

```html
        <div deskFolderBrowser [purpose]="request.purpose" [file]="request.file ?? false" (picked)="bridge.answerFolder($event)"></div>
```

- [ ] **Step 8: Run them to verify they pass**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/core/desk-bridge.spec.ts --include src/app/components/folder-browser.spec.ts --include src/app/app.spec.ts) && pnpm --filter @desk/web-ui typecheck`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-server/src/web-channels.ts apps/web-server/src/list-dirs.ts apps/web-server/src/list-dirs.test.ts apps/web-ui/src/app/core/desk-bridge.ts apps/web-ui/src/app/core/desk-bridge.spec.ts apps/web-ui/src/app/testing/fake-bridge.ts apps/web-ui/src/app/components/folder-browser.ts apps/web-ui/src/app/components/folder-browser.spec.ts apps/web-ui/src/app/app.ts && git commit -m "feat(web): app.pickFile is the folder browser in file mode; fs.listDirs lists files when asked

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 3: `injectLive`, the automation data, and `sessionOf`

**Files:**
- Create: `apps/web-ui/src/app/automations/live.ts`
- Create: `apps/web-ui/src/app/automations/live.spec.ts`
- Create: `apps/web-ui/src/app/automations/data.ts`
- Create: `apps/web-ui/src/app/testing/session.ts`

**Interfaces:**
- Consumes: `eventsAfter`, `automationListRefetch`, `automationDetailRefetch`, `automationRunsRefetch`, `runDetailRefetch`, `reduceAutomationList`, `reduceRunDetail` (`@desk/ui-core`); `emptyAutomations`, `withAutomationSummaries` (`@desk/client`); `SessionState` (`core/session.service.ts`).
- Produces:
  - `type LiveStatus = 'loading' | 'ready' | 'missing' | 'error'`
  - `type Live<T> = { status: Signal<LiveStatus>; value: Signal<T | null>; error: Signal<string | null>; reload(): void; replace(value: T): void }`
  - `type LiveOptions<T> = { key: string; events: readonly StoredEvent[]; ready: boolean; load(): Promise<T>; reduce?: (value: T, e: StoredEvent) => T; refetch(e: StoredEvent, value: T): boolean; debounceMs?: number }`
  - `injectLive<T>(options: () => LiveOptions<T>): Live<T>`, called in an injection context. `options` is read reactively: its signals are the keys.
  - `injectAutomationList(projectId: () => string, s: () => SessionState): Live<AutomationSummary[]>`
  - `injectAutomation(s: () => SessionState, id: () => string): Live<AutomationDetail>`
  - `injectRuns(s: () => SessionState, id: () => string, limit: () => number): Live<RunListEntry[]>`
  - `injectRun(s: () => SessionState, runId: () => string): Live<RunDetail>`
  - `sessionOf(events?: StoredEvent[], over?: Partial<SessionState>): SessionState` (specs only)

- [ ] **Step 1: Write the failing spec (a port of `live.test.tsx`)**

`apps/web-ui/src/app/automations/live.spec.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { render, screen } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import type { StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { DeskCallError } from '../core/desk-bridge';
import { injectLive } from './live';

type V = { n: number; seen: number[] };
const moved = (id: number) => ev(id, 'automation.layout_saved', { automation_id: 'a', layout: {} });
const switched = (id: number) => ev(id, 'automation.switched', { automation_id: 'a', enabled: true, by: 'user' });
const reduce = (v: V, e: StoredEvent): V => ({ ...v, seen: [...v.seen, e.id] });
const refetch = (e: StoredEvent) => e.type === 'automation.switched';

@Component({
  selector: 'desk-live-probe',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<output>{{ text() }}</output>`,
})
class Probe {
  readonly id = input.required<string>();
  readonly events = input.required<StoredEvent[]>();
  readonly ready = input.required<boolean>();
  readonly load = input.required<() => Promise<V>>();
  readonly reduce = input<((v: V, e: StoredEvent) => V) | undefined>(undefined);
  private readonly live = injectLive<V>(() => {
    const r = this.reduce();
    return { key: this.id(), events: this.events(), ready: this.ready(), load: this.load(), ...(r ? { reduce: r } : {}), refetch, debounceMs: 10 };
  });
  protected readonly text = computed(() => `${this.live.status()}:${JSON.stringify(this.live.value())}`);
}

describe('injectLive', () => {
  it('waits for the session, folds later events at once, and reloads once for a burst of matching ones', async () => {
    let n = 0;
    const load = vi.fn(async (): Promise<V> => ({ n: ++n, seen: [] }));
    const view = await render(Probe, { inputs: { id: 'a', events: [], ready: false, load, reduce } });
    expect(load).not.toHaveBeenCalled();
    await view.rerender({ inputs: { id: 'a', events: [moved(1)], ready: true, load, reduce } });
    await screen.findByText('ready:{"n":1,"seen":[]}');
    await view.rerender({ inputs: { id: 'a', events: [moved(1), moved(2)], ready: true, load, reduce } });
    await screen.findByText('ready:{"n":1,"seen":[2]}');
    await view.rerender({ inputs: { id: 'a', events: [moved(1), moved(2), switched(3), switched(4)], ready: true, load, reduce } });
    await screen.findByText('ready:{"n":2,"seen":[]}');
    expect(load).toHaveBeenCalledTimes(2);
  });

  it('reports a 404 as missing', async () => {
    const load = vi.fn(async (): Promise<V> => {
      throw new DeskCallError({ code: 'not_found', message: 'gone', status: 404 });
    });
    await render(Probe, { inputs: { id: 'a', events: [], ready: true, load } });
    await screen.findByText('missing:null');
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/live.spec.ts)`
Expected: FAIL, `Could not resolve "./live"`.

- [ ] **Step 3: Write `live.ts`, `data.ts` and `testing/session.ts`**

`apps/web-ui/src/app/automations/live.ts`:

```ts
import { computed, DestroyRef, effect, inject, signal, untracked, type Signal } from '@angular/core';
import type { StoredEvent } from '@desk/protocol';
import { eventsAfter } from '@desk/ui-core';
import { DeskCallError } from '../core/desk-bridge';

export type LiveStatus = 'loading' | 'ready' | 'missing' | 'error';

export type Live<T> = {
  status: Signal<LiveStatus>;
  value: Signal<T | null>;
  error: Signal<string | null>;
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
  /** Folds one later event into the snapshot. */
  reduce?: (value: T, e: StoredEvent) => T;
  /** Whether an event means the snapshot should be loaded again. */
  refetch(e: StoredEvent, value: T): boolean;
  debounceMs?: number;
};

type Snap<T> = { key: string; value: T; cursor: number };

/**
 * A daemon snapshot kept current from the project's event log (the desktop's `useLive`): events after the snapshot fold in
 * at once through `reduce`, and events `refetch` picks reload it (debounced), so fields no reducer can derive catch up.
 * `options` is read in reactive contexts, so the signals it reads (the key, the session) drive it.
 */
export function injectLive<T>(options: () => LiveOptions<T>): Live<T> {
  const opts = computed(options);
  const key = computed(() => opts().key);
  const ready = computed(() => opts().ready);
  const events = computed(() => opts().events);
  const snap = signal<Snap<T> | null>(null);
  const status = signal<LiveStatus>('loading');
  const error = signal<string | null>(null);
  let gen = 0;
  let seen = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const fetchNow = (fresh: boolean): void => {
    const g = ++gen;
    const o = untracked(opts);
    const cursor = o.events.at(-1)?.id ?? 0;
    if (fresh) {
      snap.set(null);
      status.set('loading');
    }
    o.load().then(
      (value) => {
        if (g !== gen) return;
        seen = Math.max(seen, cursor);
        snap.set({ key: o.key, value, cursor });
        status.set('ready');
        error.set(null);
      },
      (err: unknown) => {
        if (g !== gen) return;
        if (err instanceof DeskCallError && err.status === 404) {
          status.set('missing');
          return;
        }
        error.set(err instanceof Error ? err.message : String(err));
        status.update((s) => (s === 'ready' ? s : 'error'));
      },
    );
  };

  // A new key, or the session's backfill arriving, loads from scratch.
  effect((onCleanup) => {
    key();
    const r = ready();
    untracked(() => {
      seen = 0;
      if (r) fetchNow(true);
    });
    onCleanup(() => {
      gen++;
      clearTimeout(timer);
    });
  });

  // Events after the ones already seen: a burst that `refetch` picks reloads once.
  effect(() => {
    const s = snap();
    const k = key();
    const evs = events();
    if (!s || s.key !== k) return;
    untracked(() => {
      const fresh = eventsAfter(evs, seen);
      if (!fresh.length) return;
      seen = fresh.at(-1)!.id;
      const o = opts();
      if (!fresh.some((e) => o.refetch(e, s.value))) return;
      clearTimeout(timer);
      timer = setTimeout(() => fetchNow(false), o.debounceMs ?? 300);
    });
  });

  inject(DestroyRef).onDestroy(() => {
    gen++;
    clearTimeout(timer);
  });

  const value = computed(() => {
    const s = snap();
    if (!s || s.key !== key()) return null;
    const reduce = untracked(opts).reduce;
    return reduce ? eventsAfter(events(), s.cursor).reduce(reduce, s.value) : s.value;
  });

  return {
    status: computed(() => {
      const s = snap();
      return s && s.key !== key() ? 'loading' : status();
    }),
    value,
    error: error.asReadonly(),
    reload: () => fetchNow(false),
    replace: (v: T) => {
      gen++;
      const o = untracked(opts);
      const cursor = o.events.at(-1)?.id ?? 0;
      seen = Math.max(seen, cursor);
      snap.set({ key: o.key, value: v, cursor });
      status.set('ready');
    },
  };
}
```

`apps/web-ui/src/app/automations/data.ts`:

```ts
import { inject } from '@angular/core';
import { emptyAutomations, withAutomationSummaries } from '@desk/client';
import type { AutomationDetail, AutomationSummary, RunDetail, RunListEntry } from '@desk/protocol';
import { automationDetailRefetch, automationListRefetch, automationRunsRefetch, reduceAutomationList, reduceRunDetail, runDetailRefetch } from '@desk/ui-core';
import { DeskBridge } from '../core/desk-bridge';
import type { SessionState } from '../core/session.service';
import { injectLive, type Live } from './live';

/** The project's automations, by name, kept current. */
export function injectAutomationList(projectId: () => string, s: () => SessionState): Live<AutomationSummary[]> {
  const bridge = inject(DeskBridge);
  return injectLive(() => {
    const id = projectId();
    const st = s();
    return {
      key: id,
      events: st.events,
      ready: st.status === 'ready',
      load: () => bridge.call('automations.list', { projectId: id }).then((list) => withAutomationSummaries(emptyAutomations(id), list).list),
      reduce: reduceAutomationList,
      refetch: automationListRefetch,
    };
  });
}

/** One automation's detail, reloaded when it changes and when its last run ends. */
export function injectAutomation(s: () => SessionState, id: () => string): Live<AutomationDetail> {
  const bridge = inject(DeskBridge);
  return injectLive(() => {
    const a = id();
    const st = s();
    return { key: a, events: st.events, ready: st.status === 'ready', load: () => bridge.call('automations.get', { id: a }), refetch: automationDetailRefetch(a) };
  });
}

/** An automation's runs and skipped schedule times, newest first. */
export function injectRuns(s: () => SessionState, id: () => string, limit: () => number): Live<RunListEntry[]> {
  const bridge = inject(DeskBridge);
  return injectLive(() => {
    const a = id();
    const n = limit();
    const st = s();
    return { key: `${a}:${n}`, events: st.events, ready: st.status === 'ready', load: () => bridge.call('automations.runs', { id: a, limit: n }), refetch: automationRunsRefetch(a) };
  });
}

/** One run with live steps: step changes fold in at once; its agents' approvals, a child's end and its own end reload it. */
export function injectRun(s: () => SessionState, runId: () => string): Live<RunDetail> {
  const bridge = inject(DeskBridge);
  return injectLive(() => {
    const r = runId();
    const st = s();
    return { key: r, events: st.events, ready: st.status === 'ready', load: () => bridge.call('automations.getRun', { runId: r }), reduce: reduceRunDetail, refetch: runDetailRefetch(r) };
  });
}
```

`apps/web-ui/src/app/testing/session.ts`:

```ts
import { emptyChat, emptyMessages, emptyTimeline } from '@desk/client';
import type { StoredEvent } from '@desk/protocol';
import type { SessionState } from '../core/session.service';

/** A loaded project session, for specs of components that take `s` directly (the desktop's test/session.ts). */
export function sessionOf(events: StoredEvent[] = [], over: Partial<SessionState> = {}): SessionState {
  return { status: 'ready', error: null, project: null, chat: emptyChat('p'), timeline: emptyTimeline('p'), messages: emptyMessages(), events, streams: {}, ...over };
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/live.spec.ts) && pnpm --filter @desk/web-ui typecheck`
Expected: PASS. `data.ts` has no spec of its own: Tasks 12 and 14 exercise it through the screens.

- [ ] **Step 5: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-ui/src/app/automations/live.ts apps/web-ui/src/app/automations/live.spec.ts apps/web-ui/src/app/automations/data.ts apps/web-ui/src/app/testing/session.ts && git commit -m "feat(web): injectLive and the automation data, kept current from the session's log

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 4: `GraphCanvas` and its nodes, the hand-built canvas

**Files:**
- Create: `apps/web-ui/src/app/automations/design/nodes.ts`
- Create: `apps/web-ui/src/app/automations/design/graph-canvas.ts`
- Create: `apps/web-ui/src/app/automations/design/graph-canvas.spec.ts`
- Modify: `packages/ui-styles/src/automations.css`

**Interfaces:**
- Consumes: Task 1's geometry; `toGraph`, `canConnect`, `edgeIndex`, `GraphSelection`, `GraphIssues`, `GraphRun`, `StepNodeData`, `StartNodeData`, `StubNodeData` (`@desk/ui-core`).
- Produces (used by Task 8's `DesignView` and Task 12's `RunView`):
  - `div[deskGraphCanvas]` (`GraphCanvas`). Inputs: `def: AutomationDefinition`, `layout: AutomationLayout`, `startLabel: string`, `selection: GraphSelection`, `issues?: GraphIssues`, `run?: GraphRun`, `editable = false`.
  - Its outputs:
    - `pick: GraphSelection`, React's `onSelect`;
    - `moved: AutomationLayout`, the positions of a node the user finished dragging (React's `onMove`);
    - `connect: { from: string; to: string }`, React's `onConnect`;
    - `remove: { steps: string[]; edges: number[] }`, Delete or Backspace on the selection (React's `onDelete`).
  - `div[deskStepNode]` (`StepNode`), `div[deskStartNode]` (`StartNode`) and `div[deskStubNode]` (`StubNode`), with the same DOM, classes, test ids and aria labels as `nodes.tsx`.

- [ ] **Step 1: Write the failing spec**

It keeps the two cases of `GraphCanvas.test.tsx`, and adds what React Flow did for the desktop: pan, zoom, drag, connect, delete, clicks.

`apps/web-ui/src/app/automations/design/graph-canvas.spec.ts`:

```ts
import { fireEvent, render, screen } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import type { AutomationLayout } from '@desk/protocol';
import { autoLayout, edgeId, targetAnchor, toGraph, type GraphSelection } from '@desk/ui-core';
import { digestDef } from '@desk/ui-core/testing';
import { GraphCanvas } from './graph-canvas';

type Inputs = { selection?: GraphSelection; editable?: boolean; layout?: AutomationLayout };

async function canvas(o: Inputs = {}) {
  const outputs = { pick: vi.fn(), moved: vi.fn(), connect: vi.fn(), remove: vi.fn() };
  const layout = o.layout ?? autoLayout(digestDef());
  const view = await render(GraphCanvas, {
    inputs: { def: digestDef(), layout, startLabel: 'Mondays 08:00 · or Run now', selection: o.selection ?? { kind: 'none' }, editable: o.editable ?? true },
    on: outputs,
  });
  await view.fixture.whenStable();
  return { ...outputs, view, layout, flow: view.container.querySelector<HTMLElement>('.desk-flow')! };
}

/** The viewport's translate and scale, as the canvas renders them. */
function viewportOf(root: ParentNode): { x: number; y: number; zoom: number } {
  const t = root.querySelector<HTMLElement>('.react-flow__viewport')!.style.transform;
  const m = /translate\((-?[\d.]+)px, (-?[\d.]+)px\) scale\(([\d.]+)\)/.exec(t);
  if (!m) throw new Error(`No viewport transform in "${t}"`);
  return { x: Number(m[1]), y: Number(m[2]), zoom: Number(m[3]) };
}

describe('GraphCanvas', () => {
  it('draws the Start pill, each step (kind, title, detail, problems) and route stubs, and reports clicks as selections', async () => {
    const outputs = { pick: vi.fn() };
    const view = await render(GraphCanvas, {
      inputs: { def: digestDef(), layout: {}, startLabel: 'Mondays 08:00 · or Run now', selection: { kind: 'none' }, issues: { steps: { ok: ['question: required'] }, edges: {}, start: [] }, editable: true },
      on: outputs,
    });
    await view.fixture.whenStable();
    const fetch = await screen.findByTestId('node-fetch');
    expect(fetch.textContent).toContain('script');
    expect(fetch.textContent).toContain('Fetch pages');
    expect(fetch.textContent).toContain('digest: fetch.py');
    expect(screen.getByTestId('node-ok').className).toContain('invalid');
    expect(screen.getByTestId('node-start').textContent).toContain('Mondays 08:00 · or Run now');
    expect(screen.getByText('unchanged · ends')).toBeTruthy();
    fireEvent.click(screen.getByTestId('node-sum'));
    expect(outputs.pick).toHaveBeenLastCalledWith({ kind: 'step', id: 'sum' });
    fireEvent.click(screen.getByTestId('node-start'));
    expect(outputs.pick).toHaveBeenLastCalledWith({ kind: 'start' });
  });

  it('lights steps with their run state', async () => {
    const view = await render(GraphCanvas, {
      inputs: {
        def: digestDef(),
        layout: {},
        startLabel: 'schedule',
        selection: { kind: 'none' },
        run: { steps: { fetch: { tone: 'ok', badge: '✓ 12s', detail: 'route changed' }, sum: { tone: 'run', badge: '● 5m', detail: 'reading acme.md' } }, fired: new Set([0]) },
        editable: false,
      },
    });
    await view.fixture.whenStable();
    const sum = await screen.findByTestId('node-sum');
    expect(sum.className).toContain('run-run');
    expect(sum.textContent).toContain('● 5m');
    expect(sum.textContent).toContain('reading acme.md');
    expect(screen.getByTestId('node-fetch').className).toContain('run-ok');
    expect(view.container.querySelector(`[data-id="${edgeId(0)}"] .auto-edge`)!.getAttribute('class')).toContain('look-fired');
  });

  it('selects an edge and the pane by clicking, and marks the selection', async () => {
    const c = await canvas({ selection: { kind: 'edge', index: 1 } });
    expect(c.view.container.querySelector(`[data-id="${edgeId(1)}"] .auto-edge`)!.getAttribute('class')).toContain('selected');
    fireEvent.click(c.view.container.querySelector(`[data-id="${edgeId(0)}"]`)!);
    expect(c.pick).toHaveBeenLastCalledWith({ kind: 'edge', index: 0 });
    expect(c.view.container.querySelector('.auto-edge-label')!.textContent).toBe('changed');
    fireEvent.click(c.view.container.querySelector('.react-flow__pane')!);
    expect(c.pick).toHaveBeenLastCalledWith({ kind: 'none' });
  });

  it('drags a step in edit mode, reports its rounded position, and swallows the click that ends the drag', async () => {
    const c = await canvas();
    const v = viewportOf(c.view.container);
    const node = screen.getByTestId('node-fetch');
    fireEvent.pointerDown(node, { button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { clientX: 150, clientY: 130 });
    fireEvent.pointerUp(window, { clientX: 150, clientY: 130 });
    const from = c.layout['fetch']!;
    expect(c.moved).toHaveBeenCalledWith({ fetch: { x: Math.round(from.x + 50 / v.zoom), y: Math.round(from.y + 30 / v.zoom) } });
    fireEvent.click(node);
    expect(c.pick).not.toHaveBeenCalled();
  });

  it('pans instead of dragging when it is read-only', async () => {
    const c = await canvas({ editable: false });
    const before = viewportOf(c.view.container);
    fireEvent.pointerDown(screen.getByTestId('node-fetch'), { button: 0, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { clientX: 140, clientY: 90 });
    fireEvent.pointerUp(window, { clientX: 140, clientY: 90 });
    expect(c.moved).not.toHaveBeenCalled();
    expect(viewportOf(c.view.container)).toEqual({ ...before, x: before.x + 40, y: before.y - 10 });
  });

  it('connects a step’s bottom handle to another step’s top handle, and only where the connection may go', async () => {
    const c = await canvas();
    const byId = new Map(toGraph(digestDef(), c.layout, { startLabel: 's', selection: { kind: 'none' }, editable: true }).nodes.map((n) => [n.id, n]));
    const v = viewportOf(c.view.container);
    const screenOf = (id: string) => {
      const a = targetAnchor(byId.get(id)!);
      return { clientX: a.x * v.zoom + v.x + 3, clientY: a.y * v.zoom + v.y };
    };
    const fetchOut = screen.getByTestId('node-fetch').querySelector('.react-flow__handle-bottom')!;
    fireEvent.pointerDown(fetchOut, { button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(window, screenOf('ok'));
    expect(screen.getByTestId('node-ok').querySelector('.react-flow__handle-top')!.className).toContain('valid');
    expect(c.view.container.querySelector('.react-flow__connection-path')).toBeTruthy();
    fireEvent.pointerUp(window, screenOf('ok'));
    expect(c.connect).toHaveBeenCalledWith({ from: 'fetch', to: 'ok' });
    expect(c.view.container.querySelector('.react-flow__connection-path')).toBeNull();

    const okOut = screen.getByTestId('node-ok').querySelector('.react-flow__handle-bottom')!;
    fireEvent.pointerDown(okOut, { button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(window, screenOf('fetch'));
    expect(screen.getByTestId('node-fetch').querySelector('.react-flow__handle-top')!.className).not.toContain('valid');
    fireEvent.pointerUp(window, screenOf('fetch'));
    expect(c.connect).toHaveBeenCalledTimes(1);
  });

  it('deletes the selected step or edge with Delete or Backspace, only in edit mode', async () => {
    const c = await canvas({ selection: { kind: 'step', id: 'sum' } });
    fireEvent.keyDown(c.flow, { key: 'Delete' });
    expect(c.remove).toHaveBeenLastCalledWith({ steps: ['sum'], edges: [] });
    await c.view.rerender({ inputs: { selection: { kind: 'edge', index: 0 } }, partialUpdate: true });
    fireEvent.keyDown(c.flow, { key: 'Backspace' });
    expect(c.remove).toHaveBeenLastCalledWith({ steps: [], edges: [0] });
    await c.view.rerender({ inputs: { editable: false }, partialUpdate: true });
    fireEvent.keyDown(c.flow, { key: 'Delete' });
    expect(c.remove).toHaveBeenCalledTimes(2);
  });

  it('zooms with the controls and the wheel, and fits the view again', async () => {
    const c = await canvas();
    const fitted = viewportOf(c.view.container);
    fireEvent.click(screen.getByRole('button', { name: 'Zoom Out' }));
    expect(viewportOf(c.view.container).zoom).toBeCloseTo(fitted.zoom / 1.2);
    fireEvent.wheel(c.flow, { deltaY: -100, clientX: 10, clientY: 10 });
    expect(viewportOf(c.view.container).zoom).toBeCloseTo((fitted.zoom / 1.2) * 2 ** 0.2);
    fireEvent.click(screen.getByRole('button', { name: 'Fit View' }));
    expect(viewportOf(c.view.container)).toEqual(fitted);
    fireEvent.click(screen.getByRole('button', { name: 'Zoom In' }));
    expect(viewportOf(c.view.container).zoom).toBeCloseTo(Math.min(1.5, fitted.zoom * 1.2));
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/design/graph-canvas.spec.ts)`
Expected: FAIL, `Could not resolve "./graph-canvas"`.

- [ ] **Step 3: Write the nodes**

`apps/web-ui/src/app/automations/design/nodes.ts`:

```ts
import { booleanAttribute, ChangeDetectionStrategy, Component, computed, input, ViewEncapsulation } from '@angular/core';
import type { Step } from '@desk/protocol';
import type { StartNodeData, StepNodeData, StubNodeData } from '@desk/ui-core';

/** Each kind's colour stripe (automations.css maps these to tokens). */
const KIND_CLASS: Record<Step['kind'], string> = { script: 'k-script', agent: 'k-agent', ask: 'k-ask', wait: 'k-wait', automation: 'k-automation', tell_desk: 'k-tell' };

/**
 * A step (mockup 2): kind, title and a one-line detail; red when validation found a problem; lit with its state in a run.
 * Its handles are React Flow's: the canvas starts connections from the bottom one (`connectable`) and marks the top one
 * `valid` while a connection that may land hovers it.
 */
@Component({
  selector: 'div[deskStepNode]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: {
    class: 'auto-node',
    '[class]': 'look()',
    '[class.selected]': 'selected()',
    '[class.invalid]': 'data().errors.length > 0',
    '[attr.data-testid]': "'node-' + data().step.id",
    '[attr.aria-label]': "data().kind + ' step: ' + data().step.title",
    '[attr.title]': "data().errors.length ? data().errors.join('\\n') : null",
  },
  template: `
    <div class="react-flow__handle react-flow__handle-top target" [class.valid]="dropValid()" data-handlepos="top" [attr.data-nodeid]="data().step.id"></div>
    <div class="auto-node-kind"><span>{{ data().kind }}</span>@if (data().run; as run) {<b>{{ run.badge }}</b>} @else if (data().output) {<b>result</b>}</div>
    <div class="auto-node-title">{{ data().step.title }}</div>
    <div class="auto-node-detail">{{ data().run?.detail ?? data().detail }}</div>
    <div class="react-flow__handle react-flow__handle-bottom source" [class.connectable]="connectable()" data-handlepos="bottom" [attr.data-nodeid]="data().step.id"></div>
    <div class="react-flow__handle react-flow__handle-right source auto-handle-side" data-handlepos="right"></div>
  `,
})
export class StepNode {
  readonly data = input.required<StepNodeData>();
  readonly selected = input(false, { transform: booleanAttribute });
  readonly connectable = input(false, { transform: booleanAttribute });
  readonly dropValid = input(false, { transform: booleanAttribute });
  protected readonly look = computed(() => {
    const d = this.data();
    return d.run ? `${KIND_CLASS[d.step.kind]} run-${d.run.tone}` : KIND_CLASS[d.step.kind];
  });
}

/** The Start pill: schedules and Run now. Clicking it edits schedules and inputs. */
@Component({
  selector: 'div[deskStartNode]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: {
    class: 'auto-start',
    '[class.selected]': 'selected()',
    '[class.invalid]': 'data().errors.length > 0',
    'data-testid': 'node-start',
    '[attr.aria-label]': "'Start: ' + data().label",
    '[attr.title]': "data().errors.length ? data().errors.join('\\n') : null",
  },
  template: `<span aria-hidden="true">▶</span><span class="auto-start-label">{{ data().label }}</span><div class="react-flow__handle react-flow__handle-bottom source" data-handlepos="bottom"></div>`,
})
export class StartNode {
  readonly data = input.required<StartNodeData>();
  readonly selected = input(false, { transform: booleanAttribute });
}

/** A declared route no edge takes: "<route> · ends". */
@Component({
  selector: 'div[deskStubNode]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-stub' },
  template: `<div class="react-flow__handle react-flow__handle-left target" data-handlepos="left"></div>{{ data().label }}`,
})
export class StubNode {
  readonly data = input.required<StubNodeData>();
}
```

- [ ] **Step 4: Write the canvas**

`apps/web-ui/src/app/automations/design/graph-canvas.ts`:

```ts
import { afterNextRender, ChangeDetectionStrategy, Component, computed, DestroyRef, ElementRef, inject, input, output, signal, viewChild, ViewEncapsulation } from '@angular/core';
import type { AutomationDefinition, AutomationLayout } from '@desk/protocol';
import {
  bezierPath,
  canConnect,
  CONNECT_RADIUS,
  dotPattern,
  dropTarget,
  edgeGeometry,
  edgeIndex,
  fitViewport,
  graphBounds,
  sourceAnchor,
  toGraph,
  toGraphPoint,
  wheelFactor,
  zoomAt,
  type GraphIssues,
  type GraphNode,
  type GraphRun,
  type GraphSelection,
  type Point,
  type Viewport,
} from '@desk/ui-core';
import { StartNode, StepNode, StubNode } from './nodes';

/** A pointer that went down on a node: a drag once it moves 3 px (a click before that). */
type Drag = { id: string; startX: number; startY: number; origin: Point; position: Point; moved: boolean };
/** A pointer that went down on the background: a pan once it moves 3 px (a pane click before that). */
type Pan = { startX: number; startY: number; origin: Viewport; moved: boolean };
/** A connection being dragged from `from`'s bottom handle; `at` is the pointer in graph coordinates. */
type Link = { from: string; at: Point; target: string | null; valid: boolean };

/** Pointer travel below this is a click, not a drag or a pan. */
const DRAG_THRESHOLD = 3;
let nextPattern = 0;

/**
 * The automation graph, top to bottom (spec §8.2): editable in Design, read-only and lit in a run. The web's own canvas for
 * what React Flow does on the desktop, with React Flow's class names so the shared CSS applies (Plan 21's decisions).
 */
@Component({
  selector: 'div[deskGraphCanvas]',
  imports: [StartNode, StepNode, StubNode],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-canvas' },
  template: `
    @let l = link();
    <div #flow class="react-flow desk-flow" tabindex="0" (keydown)="onKey($event)" (wheel)="onWheel($event)">
      <svg class="react-flow__background" aria-hidden="true">
        <pattern [id]="patternId" patternUnits="userSpaceOnUse" [attr.x]="dots().x" [attr.y]="dots().y" [attr.width]="dots().cell" [attr.height]="dots().cell">
          <circle class="react-flow__background-pattern dots" [attr.cx]="dots().r" [attr.cy]="dots().r" [attr.r]="dots().r" />
        </pattern>
        <rect x="0" y="0" width="100%" height="100%" [attr.fill]="'url(#' + patternId + ')'" />
      </svg>
      <div class="react-flow__renderer">
        <div class="react-flow__pane draggable" [class.dragging]="pan()?.moved ?? false" (pointerdown)="onPointerDown($event)" (click)="onClick($event)">
          <div class="react-flow__viewport" [style.transform]="transform()">
            <div class="react-flow__edges">
              <svg>
                @for (e of edges(); track e.id) {
                  <g class="react-flow__edge react-flow__edge-route" [class.selectable]="e.selectable !== false" [class.selected]="e.selected ?? false" [attr.data-id]="e.id" [attr.aria-label]="'Edge from ' + e.source + ' to ' + e.target">
                    <path class="react-flow__edge-path auto-edge" [class]="'look-' + e.data.look" [class.selected]="e.selected ?? false" [class.invalid]="e.data.error" [attr.d]="e.path" />
                    @if (e.selectable !== false) {
                      <path class="react-flow__edge-interaction" fill="none" stroke-opacity="0" stroke-width="20" [attr.d]="e.path" />
                    }
                  </g>
                }
                @if (linkPath(); as p) {
                  <path class="react-flow__connection-path" [attr.d]="p" />
                }
              </svg>
            </div>
            <div class="react-flow__edgelabel-renderer">
              @for (e of labelled(); track e.id) {
                <div class="auto-edge-label" [class.selected]="e.selected ?? false" [class.invalid]="e.data.error" [style.transform]="'translate(-50%, -50%) translate(' + e.labelX + 'px, ' + e.labelY + 'px)'">{{ e.data.label }}</div>
              }
            </div>
            <div class="react-flow__nodes">
              @for (n of nodes(); track n.id) {
                <div
                  class="react-flow__node"
                  [class]="'react-flow__node-' + n.type"
                  [class.selectable]="n.selectable !== false"
                  [class.draggable]="editable() && n.draggable !== false"
                  [class.selected]="n.selected ?? false"
                  [class.dragging]="drag()?.id === n.id && (drag()?.moved ?? false)"
                  [attr.data-id]="n.id"
                  [style.transform]="'translate(' + n.position.x + 'px, ' + n.position.y + 'px)'"
                >
                  @switch (n.type) {
                    @case ('step') {
                      <div deskStepNode [data]="asStep(n).data" [selected]="n.selected ?? false" [connectable]="editable()" [dropValid]="l !== null && l.valid && l.target === n.id"></div>
                    }
                    @case ('start') {
                      <div deskStartNode [data]="asStart(n).data" [selected]="n.selected ?? false"></div>
                    }
                    @case ('stub') {
                      <div deskStubNode [data]="asStub(n).data"></div>
                    }
                  }
                </div>
              }
            </div>
          </div>
        </div>
      </div>
      <div class="react-flow__panel react-flow__controls vertical bottom left">
        <button type="button" class="react-flow__controls-button react-flow__controls-zoomin" title="Zoom In" aria-label="Zoom In" (click)="zoomBy(1.2)">
          <svg viewBox="0 0 32 32" aria-hidden="true"><path d="M32 18.133H18.133V32h-4.266V18.133H0v-4.266h13.867V0h4.266v13.867H32z" /></svg>
        </button>
        <button type="button" class="react-flow__controls-button react-flow__controls-zoomout" title="Zoom Out" aria-label="Zoom Out" (click)="zoomBy(1 / 1.2)">
          <svg viewBox="0 0 32 5" aria-hidden="true"><path d="M0 0h32v4.2H0z" /></svg>
        </button>
        <button type="button" class="react-flow__controls-button react-flow__controls-fitview" title="Fit View" aria-label="Fit View" (click)="fit()">
          <svg viewBox="0 0 32 30" aria-hidden="true">
            <path
              d="M3.692 4.63c0-.53.4-.938.939-.938h5.215V0H4.708C2.13 0 0 2.054 0 4.63v5.216h3.692V4.631zM27.354 0h-5.2v3.692h5.17c.53 0 .984.4.984.939v5.215H32V4.631A4.624 4.624 0 0027.354 0zm.954 24.83c0 .532-.4.94-.939.94h-5.215v3.768h5.215c2.577 0 4.631-2.13 4.631-4.707v-5.139h-3.692v5.139zm-23.677.94c-.531 0-.939-.4-.939-.94v-5.138H0v5.139c0 2.577 2.13 4.707 4.708 4.707h5.138V25.77H4.631z"
            />
          </svg>
        </button>
      </div>
    </div>
  `,
})
export class GraphCanvas {
  readonly def = input.required<AutomationDefinition>();
  readonly layout = input.required<AutomationLayout>();
  readonly startLabel = input.required<string>();
  readonly selection = input.required<GraphSelection>();
  readonly issues = input<GraphIssues | undefined>(undefined);
  readonly run = input<GraphRun | undefined>(undefined);
  readonly editable = input(false);
  /** React's onSelect: a node, an edge, or the pane (`none`). */
  readonly pick = output<GraphSelection>();
  /** Positions of a node the user finished dragging (React's onMove). */
  readonly moved = output<AutomationLayout>();
  readonly connect = output<{ from: string; to: string }>();
  /** Delete or Backspace on the selection: steps and edges (by index) of the same definition (React's onDelete). */
  readonly remove = output<{ steps: string[]; edges: number[] }>();

  private readonly flow = viewChild.required<ElementRef<HTMLElement>>('flow');
  protected readonly patternId = `desk-dots-${++nextPattern}`;
  protected readonly viewport = signal<Viewport>({ x: 0, y: 0, zoom: 1 });
  protected readonly drag = signal<Drag | null>(null);
  protected readonly pan = signal<Pan | null>(null);
  protected readonly link = signal<Link | null>(null);
  /** Set when a pointer gesture ends in a drag, a pan or a connection: the click that follows it is not a selection. */
  private swallowClick = false;

  private readonly graph = computed(() => {
    const issues = this.issues();
    const run = this.run();
    return toGraph(this.def(), this.layout(), { startLabel: this.startLabel(), selection: this.selection(), editable: this.editable(), ...(issues ? { issues } : {}), ...(run ? { run } : {}) });
  });
  /** The graph's nodes, with a node being dragged where the pointer has it. */
  protected readonly nodes = computed(() => {
    const d = this.drag();
    const nodes = this.graph().nodes;
    return d?.moved ? nodes.map((n) => (n.id === d.id ? ({ ...n, position: d.position } as GraphNode) : n)) : nodes;
  });
  protected readonly edges = computed(() => {
    const byId = new Map(this.nodes().map((n) => [n.id, n]));
    return this.graph().edges.flatMap((e) => {
      const g = edgeGeometry(e, byId);
      return g ? [{ ...e, ...g }] : [];
    });
  });
  protected readonly labelled = computed(() => this.edges().filter((e) => e.data.label));
  protected readonly transform = computed(() => {
    const v = this.viewport();
    return `translate(${v.x}px, ${v.y}px) scale(${v.zoom})`;
  });
  protected readonly dots = computed(() => dotPattern(this.viewport()));
  protected readonly linkPath = computed(() => {
    const l = this.link();
    const from = l ? this.nodes().find((n) => n.id === l.from) : undefined;
    return l && from ? bezierPath(sourceAnchor(from), { ...l.at, side: 'top' }).path : null;
  });

  private readonly onMoveListener = (e: PointerEvent) => this.onMove(e);
  private readonly onUpListener = () => this.onUp();

  constructor() {
    // React Flow's fitView, once the canvas has a size.
    afterNextRender(() => this.fit());
    inject(DestroyRef).onDestroy(() => this.untrack());
  }

  protected asStep(n: GraphNode) {
    return n as Extract<GraphNode, { type: 'step' }>;
  }
  protected asStart(n: GraphNode) {
    return n as Extract<GraphNode, { type: 'start' }>;
  }
  protected asStub(n: GraphNode) {
    return n as Extract<GraphNode, { type: 'stub' }>;
  }

  /** The canvas's size; jsdom has none, so specs get 800×600 (the React tests' ResizeObserver shim). */
  private size(): { width: number; height: number } {
    const el = this.flow().nativeElement;
    return { width: el.clientWidth || 800, height: el.clientHeight || 600 };
  }

  /** A pointer event's position relative to the canvas. */
  private local(e: { clientX: number; clientY: number }): Point {
    const r = this.flow().nativeElement.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  protected fit(): void {
    const { width, height } = this.size();
    this.viewport.set(fitViewport(graphBounds(this.graph().nodes), width, height));
  }

  protected zoomBy(factor: number): void {
    const { width, height } = this.size();
    const v = this.viewport();
    this.viewport.set(zoomAt(v, v.zoom * factor, { x: width / 2, y: height / 2 }));
  }

  protected onWheel(e: WheelEvent): void {
    e.preventDefault();
    const v = this.viewport();
    this.viewport.set(zoomAt(v, v.zoom * wheelFactor(e.deltaY, e.deltaMode, e.ctrlKey), this.local(e)));
  }

  protected onKey(e: KeyboardEvent): void {
    if (!this.editable() || (e.key !== 'Delete' && e.key !== 'Backspace')) return;
    const sel = this.selection();
    if (sel.kind === 'step') {
      e.preventDefault();
      this.remove.emit({ steps: [sel.id], edges: [] });
    } else if (sel.kind === 'edge') {
      e.preventDefault();
      this.remove.emit({ steps: [], edges: [sel.index] });
    }
  }

  protected onPointerDown(e: PointerEvent): void {
    if (e.button !== 0) return;
    this.swallowClick = false;
    const target = e.target as Element;
    const handle = target.closest('.react-flow__handle.connectable');
    const from = handle?.getAttribute('data-nodeid');
    if (from && this.editable()) {
      e.preventDefault();
      this.link.set({ from, at: toGraphPoint(this.viewport(), this.local(e)), target: null, valid: false });
      this.track();
      return;
    }
    if (target.closest('.react-flow__edge')) return;
    const id = target.closest<HTMLElement>('.react-flow__node')?.dataset['id'];
    const node = id ? this.nodes().find((n) => n.id === id) : undefined;
    if (node && this.editable() && node.draggable !== false) {
      this.drag.set({ id: node.id, startX: e.clientX, startY: e.clientY, origin: node.position, position: node.position, moved: false });
    } else {
      this.pan.set({ startX: e.clientX, startY: e.clientY, origin: this.viewport(), moved: false });
    }
    this.track();
  }

  private onMove(e: PointerEvent): void {
    const link = this.link();
    if (link) {
      const v = this.viewport();
      const at = toGraphPoint(v, this.local(e));
      const target = dropTarget(this.nodes(), at, CONNECT_RADIUS / v.zoom);
      this.link.set({ ...link, at, target, valid: target !== null && canConnect(this.def(), link.from, target) === null });
      return;
    }
    const drag = this.drag();
    if (drag) {
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;
      if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
      const zoom = this.viewport().zoom;
      this.drag.set({ ...drag, moved: true, position: { x: drag.origin.x + dx / zoom, y: drag.origin.y + dy / zoom } });
      return;
    }
    const pan = this.pan();
    if (pan) {
      const dx = e.clientX - pan.startX;
      const dy = e.clientY - pan.startY;
      if (!pan.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
      this.pan.set({ ...pan, moved: true });
      this.viewport.set({ ...pan.origin, x: pan.origin.x + dx, y: pan.origin.y + dy });
    }
  }

  private onUp(): void {
    this.untrack();
    const link = this.link();
    if (link) {
      this.link.set(null);
      this.swallowClick = true;
      if (link.target && link.valid) this.connect.emit({ from: link.from, to: link.target });
      return;
    }
    const drag = this.drag();
    if (drag) {
      if (drag.moved) {
        this.swallowClick = true;
        this.moved.emit({ [drag.id]: { x: Math.round(drag.position.x), y: Math.round(drag.position.y) } });
      }
      this.drag.set(null);
      return;
    }
    const pan = this.pan();
    if (pan) {
      if (pan.moved) this.swallowClick = true;
      this.pan.set(null);
    }
  }

  protected onClick(e: MouseEvent): void {
    if (this.swallowClick) {
      this.swallowClick = false;
      return;
    }
    const target = e.target as Element;
    const id = target.closest<HTMLElement>('.react-flow__node')?.dataset['id'];
    if (id) {
      const node = this.nodes().find((n) => n.id === id);
      if (node?.type === 'start') this.pick.emit({ kind: 'start' });
      else if (node?.type === 'step') this.pick.emit({ kind: 'step', id: node.id });
      return;
    }
    const edge = target.closest('.react-flow__edge');
    if (edge) {
      const index = edgeIndex(edge.getAttribute('data-id') ?? '');
      if (index !== null) this.pick.emit({ kind: 'edge', index });
      return;
    }
    this.pick.emit({ kind: 'none' });
  }

  /** Moves and releases are followed on the window, so a gesture that leaves the canvas still ends (jsdom has no pointer capture). */
  private track(): void {
    window.addEventListener('pointermove', this.onMoveListener);
    window.addEventListener('pointerup', this.onUpListener);
    window.addEventListener('pointercancel', this.onUpListener);
  }

  private untrack(): void {
    window.removeEventListener('pointermove', this.onMoveListener);
    window.removeEventListener('pointerup', this.onUpListener);
    window.removeEventListener('pointercancel', this.onUpListener);
  }
}
```

- [ ] **Step 5: Add the web canvas's structural CSS**

In `packages/ui-styles/src/automations.css`, insert this block right after the `.auto-canvas { … }` rule and before `.auto-canvas .react-flow {`. It comes first so that the canvas rules after it (the handle size, `.auto-edge`'s stroke) still win at equal specificity.

```css
/*
 * The web canvas (apps/web-ui's GraphCanvas) is not React Flow, so it gets React Flow's structural rules here, on a class
 * only it has; the desktop gets them from @xyflow/react's stylesheet. Colours are the --xy-* variables mapped below.
 */
.desk-flow {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  z-index: 0;
  direction: ltr;
  outline: none;
}
.desk-flow:focus-visible {
  box-shadow: inset 0 0 0 2px var(--run-ring);
}
.desk-flow .react-flow__background {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: -1;
}
.desk-flow .react-flow__background-pattern.dots {
  fill: var(--rule);
}
.desk-flow .react-flow__renderer,
.desk-flow .react-flow__pane {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
}
.desk-flow .react-flow__renderer {
  z-index: 4;
}
.desk-flow .react-flow__pane {
  z-index: 1;
  touch-action: none;
}
.desk-flow .react-flow__pane.draggable {
  cursor: grab;
}
.desk-flow .react-flow__pane.dragging {
  cursor: grabbing;
}
.desk-flow .react-flow__viewport {
  position: absolute;
  top: 0;
  left: 0;
  transform-origin: 0 0;
  z-index: 2;
  pointer-events: none;
}
.desk-flow .react-flow__edges {
  position: absolute;
}
.desk-flow .react-flow__edges svg {
  position: absolute;
  overflow: visible;
  pointer-events: none;
}
.desk-flow .react-flow__edge {
  pointer-events: visibleStroke;
}
.desk-flow .react-flow__edge.selectable {
  cursor: pointer;
}
.desk-flow .react-flow__edge-path {
  stroke: var(--xy-edge-stroke);
  stroke-width: 1;
  fill: none;
}
.desk-flow .react-flow__connection-path {
  stroke: var(--xy-connectionline-stroke);
  stroke-width: 1;
  fill: none;
}
.desk-flow .react-flow__edgelabel-renderer {
  position: absolute;
  width: 100%;
  height: 100%;
  pointer-events: none;
  user-select: none;
}
.desk-flow .react-flow__nodes {
  pointer-events: none;
  transform-origin: 0 0;
}
.desk-flow .react-flow__node {
  position: absolute;
  top: 0;
  left: 0;
  box-sizing: border-box;
  transform-origin: 0 0;
  pointer-events: all;
  user-select: none;
  cursor: default;
}
.desk-flow .react-flow__node.selectable {
  cursor: pointer;
}
.desk-flow .react-flow__node.draggable {
  cursor: grab;
}
.desk-flow .react-flow__node.dragging {
  cursor: grabbing;
}
.desk-flow .react-flow__handle {
  position: absolute;
  pointer-events: none;
  min-width: 5px;
  min-height: 5px;
  width: 6px;
  height: 6px;
  background-color: var(--xy-handle-background-color);
  border: 1px solid var(--xy-handle-border-color);
  border-radius: 100%;
}
.desk-flow .react-flow__handle.connectable {
  pointer-events: all;
  cursor: crosshair;
}
.desk-flow .react-flow__handle-bottom {
  top: auto;
  left: 50%;
  bottom: 0;
  transform: translate(-50%, 50%);
}
.desk-flow .react-flow__handle-top {
  top: 0;
  left: 50%;
  transform: translate(-50%, -50%);
}
.desk-flow .react-flow__handle-left {
  top: 50%;
  left: 0;
  transform: translate(-50%, -50%);
}
.desk-flow .react-flow__handle-right {
  top: 50%;
  right: 0;
  transform: translate(50%, -50%);
}
.desk-flow .react-flow__panel {
  position: absolute;
  z-index: 5;
  margin: 15px;
}
.desk-flow .react-flow__panel.bottom {
  bottom: 0;
}
.desk-flow .react-flow__panel.left {
  left: 0;
}
.desk-flow .react-flow__controls {
  display: flex;
  flex-direction: column;
  box-shadow: var(--xy-controls-box-shadow);
}
.desk-flow .react-flow__controls-button {
  display: flex;
  justify-content: center;
  align-items: center;
  width: 26px;
  height: 26px;
  padding: 4px;
  border: none;
  border-bottom: 1px solid var(--xy-controls-button-border-color);
  background: var(--xy-controls-button-background-color);
  color: var(--xy-controls-button-color);
  cursor: pointer;
  user-select: none;
}
.desk-flow .react-flow__controls-button:last-child {
  border-bottom: none;
}
.desk-flow .react-flow__controls-button:hover {
  background: var(--xy-controls-button-background-color-hover);
  color: var(--xy-controls-button-color-hover);
}
.desk-flow .react-flow__controls-button svg {
  width: 100%;
  max-width: 12px;
  max-height: 12px;
  fill: currentColor;
}
```

- [ ] **Step 6: Run the spec to verify it passes**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/design/graph-canvas.spec.ts) && pnpm --filter @desk/web-ui typecheck && pnpm vitest run packages/ui-styles`
Expected: PASS, including `tokens.test.ts` (no colour literals: the block uses `var(…)` only).

If a pointer case fails because this jsdom build lacks `PointerEvent`, `fireEvent.pointerDown` falls back to a plain `Event` with no `button` or `clientX`. Check `typeof PointerEvent`. If it is missing, add a small polyfill in the spec's `beforeAll`: `class PointerEvent extends MouseEvent {}` assigned to `globalThis.PointerEvent`. Record it as a deviation.

- [ ] **Step 7: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-ui/src/app/automations/design/nodes.ts apps/web-ui/src/app/automations/design/graph-canvas.ts apps/web-ui/src/app/automations/design/graph-canvas.spec.ts packages/ui-styles/src/automations.css && git commit -m "feat(web): the automation canvas: nodes, bezier edges, pan and zoom, drag, connect, delete, with React Flow's class names

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 5: `TemplateField`, `ListEditor` and the pickers

**Files:**
- Create: `apps/web-ui/src/app/automations/design/template-field.ts`
- Create: `apps/web-ui/src/app/automations/design/template-field.spec.ts`
- Create: `apps/web-ui/src/app/automations/design/list-editor.ts`
- Create: `apps/web-ui/src/app/automations/design/pickers.ts`

**Interfaces:**
- Consumes: `activeToken`, `filterSuggestions`, `insertSuggestion`, `isKnownPath`, `pathsIn`, `TemplateSuggestion`, `scriptFiles`, `skillChoices`, `SkillChoice` (`@desk/ui-core`); `Button`.
- Produces (used by Tasks 6–7):
  - `div[deskTemplateField]` (`TemplateField`). Inputs: `id`, `label`, `value`, `suggestions: TemplateSuggestion[]`, `multiline = false`, `rows = 4`, `placeholder?`, `hint?`, `errors: string[] = []`, `labelHidden = false`. Output: `valueChange: string`.
  - `fieldset[deskListEditor]` (`ListEditor`). Inputs: `id`, `label`, `values: string[]`, `placeholder?`, `hint?`, `addLabel?`, `check?: (value: string) => string | null`, `suggestions?: TemplateSuggestion[]`, `max?: number`. Output: `valuesChange: string[]`.
  - `injectSkillChoices(projectId: () => string): Signal<SkillChoice[]>`
  - `injectSkillScripts(projectId: () => string, choice: () => SkillChoice | null): Signal<{ scripts: string[]; loading: boolean }>`
  - `injectAutomationNames(projectId: () => string): Signal<string[]>`
  - `injectAutomationInputs(projectId: () => string, name: () => string): Signal<InputSpec[] | null>`

- [ ] **Step 1: Write the failing spec (a port of `TemplateField.test.tsx`)**

`apps/web-ui/src/app/automations/design/template-field.spec.ts`:

```ts
import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { fireEvent, render, screen } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { templateSuggestions } from '@desk/ui-core';
import { digestDef } from '@desk/ui-core/testing';
import { TemplateField } from './template-field';

@Component({
  selector: 'desk-template-harness',
  imports: [TemplateField],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<div deskTemplateField id="brief" label="Brief" [value]="v()" (valueChange)="v.set($event)" [suggestions]="suggestions" [multiline]="true"></div>`,
})
class Harness {
  readonly v = signal('');
  readonly suggestions = templateSuggestions(digestDef(), 'ok');
}

describe('TemplateField', () => {
  it('suggests paths after {{, inserts the chosen one, and marks paths not available here', async () => {
    await render(Harness);
    const box = screen.getByLabelText('Brief') as HTMLTextAreaElement;
    fireEvent.input(box, { target: { value: 'Publish {{steps.sum.o' } });
    fireEvent.mouseDown(await screen.findByRole('option', { name: /steps\.sum\.outputs\.headline/ }));
    expect(box.value).toBe('Publish {{steps.sum.outputs.headline}}');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(screen.getByText('{{steps.sum.outputs.headline}}').className).toBe('auto-tpl');
    fireEvent.input(box, { target: { value: 'x {{inputs.nope}}' } });
    expect(screen.getByText('{{inputs.nope}}').className).toContain('unknown');
  });

  it('moves through suggestions with the arrow keys and picks with Enter', async () => {
    await render(Harness);
    const box = screen.getByLabelText('Brief') as HTMLTextAreaElement;
    fireEvent.input(box, { target: { value: '{{run.' } });
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

- [ ] **Step 2: Run it to verify it fails**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/design/template-field.spec.ts)`
Expected: FAIL, `Could not resolve "./template-field"`.

- [ ] **Step 3: Write the three modules**

`apps/web-ui/src/app/automations/design/template-field.ts`:

```ts
import { booleanAttribute, ChangeDetectionStrategy, Component, computed, ElementRef, input, output, signal, viewChild, ViewEncapsulation } from '@angular/core';
import { activeToken, filterSuggestions, insertSuggestion, isKnownPath, pathsIn, type TemplateSuggestion } from '@desk/ui-core';

type Box = HTMLInputElement | HTMLTextAreaElement;
let nextList = 0;

/** A text field with `{{…}}` autocomplete (inputs, upstream steps' results, the run), showing the paths it uses as chips (spec §8.2). */
@Component({
  selector: 'div[deskTemplateField]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'field auto-template', '[attr.id]': 'null' },
  template: `
    <label [attr.for]="id()" [class.sr-only]="labelHidden()">{{ label() }}</label>
    @if (multiline()) {
      <textarea
        #box
        class="textarea"
        role="combobox"
        aria-autocomplete="list"
        [id]="id()"
        [rows]="rows()"
        [value]="value()"
        [attr.placeholder]="placeholder() ?? null"
        [attr.aria-expanded]="open()"
        [attr.aria-controls]="listId"
        (input)="onInput($event)"
        (keydown)="onKeyDown($event)"
        (keyup)="track($event)"
        (click)="track($event)"
        (blur)="onBlur()"
      ></textarea>
    } @else {
      <input
        #box
        class="input"
        role="combobox"
        aria-autocomplete="list"
        [id]="id()"
        [value]="value()"
        [attr.placeholder]="placeholder() ?? null"
        [attr.aria-expanded]="open()"
        [attr.aria-controls]="listId"
        (input)="onInput($event)"
        (keydown)="onKeyDown($event)"
        (keyup)="track($event)"
        (click)="track($event)"
        (blur)="onBlur()"
      />
    }
    @if (open()) {
      <ul role="listbox" class="auto-suggest" [id]="listId">
        @for (s of options(); track s.path; let i = $index) {
          <li role="option" [attr.aria-selected]="i === active()" (mousedown)="$event.preventDefault(); choose(s)"><span class="mono">{{ s.open ? s.path + '…' : s.path }}</span><span class="muted small">{{ s.label }}</span></li>
        }
      </ul>
    }
    @if (chips().length) {
      <div class="auto-chips">
        @for (c of chips(); track c.path) {
          <span class="auto-tpl" [class.unknown]="!c.known" [attr.title]="c.known ? null : 'Not available here'">{{ c.text }}</span>
        }
      </div>
    }
    @if (errors().length) {
      @for (e of errors(); track e) {
        <p class="field-error" role="alert">{{ e }}</p>
      }
    } @else if (hint()) {
      <p class="field-hint">{{ hint() }}</p>
    }
  `,
})
export class TemplateField {
  readonly id = input.required<string>();
  readonly label = input.required<string>();
  readonly value = input.required<string>();
  readonly suggestions = input.required<TemplateSuggestion[]>();
  readonly multiline = input(false, { transform: booleanAttribute });
  readonly rows = input(4);
  readonly placeholder = input<string | undefined>(undefined);
  readonly hint = input<string | undefined>(undefined);
  readonly errors = input<string[]>([]);
  /** Keeps the label for screen readers only (a row of a list). */
  readonly labelHidden = input(false, { transform: booleanAttribute });
  /** React's onChange. */
  readonly valueChange = output<string>();

  private readonly box = viewChild<ElementRef<Box>>('box');
  protected readonly listId = `auto-suggest-${++nextList}`;
  protected readonly caret = signal<number | null>(null);
  protected readonly active = signal(0);
  private readonly query = computed(() => {
    const c = this.caret();
    return c === null ? null : (activeToken(this.value(), c)?.query ?? null);
  });
  protected readonly options = computed(() => {
    const q = this.query();
    return q === null ? [] : filterSuggestions(this.suggestions(), q);
  });
  protected readonly open = computed(() => this.options().length > 0);
  /** The `{{…}}` paths the value uses, each marked when this step cannot see it. */
  protected readonly chips = computed(() => pathsIn(this.value()).map((path) => ({ path, text: `{{${path}}}`, known: isKnownPath(path, this.suggestions()) })));

  protected choose(s: TemplateSuggestion): void {
    const next = insertSuggestion(this.value(), this.caret() ?? this.value().length, s);
    this.valueChange.emit(next.text);
    this.caret.set(s.open ? next.caret : null);
    this.active.set(0);
    requestAnimationFrame(() => {
      const el = this.box()?.nativeElement;
      el?.focus();
      el?.setSelectionRange(next.caret, next.caret);
    });
  }

  protected track(e: Event): void {
    this.caret.set((e.target as Box).selectionStart);
  }

  protected onInput(e: Event): void {
    const el = e.target as Box;
    this.valueChange.emit(el.value);
    this.caret.set(el.selectionStart);
    this.active.set(0);
  }

  protected onKeyDown(e: KeyboardEvent): void {
    const options = this.options();
    if (!options.length) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      this.active.update((a) => (a + (e.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length);
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault();
      const s = options[Math.min(this.active(), options.length - 1)];
      if (s) this.choose(s);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      this.caret.set(null);
    }
  }

  protected onBlur(): void {
    setTimeout(() => this.caret.set(null), 150);
  }
}
```

`apps/web-ui/src/app/automations/design/list-editor.ts`:

```ts
import { ChangeDetectionStrategy, Component, input, output, ViewEncapsulation } from '@angular/core';
import type { TemplateSuggestion } from '@desk/ui-core';
import { Button } from '../../components/button';
import { TemplateField } from './template-field';

/** A list of short texts (routes, globs, arguments): one row each, Remove per row, and Add. Rows are labelled "<label> <n>". */
@Component({
  selector: 'fieldset[deskListEditor]',
  imports: [Button, TemplateField],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'field auto-rows', '[attr.id]': 'null' },
  template: `
    <legend>{{ label() }}</legend>
    @for (v of values(); track $index; let i = $index) {
      <div>
        <div class="auto-row">
          @if (suggestions(); as sug) {
            <div deskTemplateField [id]="id() + '-' + i" [label]="label() + ' ' + (i + 1)" [labelHidden]="true" [value]="v" [suggestions]="sug" [placeholder]="placeholder()" (valueChange)="set(i, $event)"></div>
          } @else {
            <input class="input mono" [id]="id() + '-' + i" [attr.aria-label]="label() + ' ' + (i + 1)" [value]="v" [attr.placeholder]="placeholder() ?? null" (input)="set(i, val($event))" />
          }
          <button deskButton size="sm" variant="ghost" [attr.aria-label]="'Remove ' + label() + ' ' + (i + 1)" (click)="remove(i)">✕</button>
        </div>
        @if (problem(v); as p) {
          <p class="field-error auto-row-error">{{ p }}</p>
        }
      </div>
    }
    @if (max() === undefined || values().length < max()!) {
      <div><button deskButton size="sm" (click)="valuesChange.emit([...values(), ''])">{{ addLabel() ?? 'Add' }}</button></div>
    }
    @if (hint()) {
      <p class="field-hint">{{ hint() }}</p>
    }
  `,
})
export class ListEditor {
  readonly id = input.required<string>();
  readonly label = input.required<string>();
  readonly values = input.required<string[]>();
  readonly placeholder = input<string | undefined>(undefined);
  readonly hint = input<string | undefined>(undefined);
  readonly addLabel = input<string | undefined>(undefined);
  /** Checks one value; its message shows under that row. */
  readonly check = input<((value: string) => string | null) | undefined>(undefined);
  /** Makes each row a template field (script arguments). */
  readonly suggestions = input<TemplateSuggestion[] | undefined>(undefined);
  readonly max = input<number | undefined>(undefined);
  /** React's onChange. */
  readonly valuesChange = output<string[]>();

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  protected set(i: number, v: string): void {
    this.valuesChange.emit(this.values().map((x, j) => (j === i ? v : x)));
  }

  protected remove(i: number): void {
    this.valuesChange.emit(this.values().filter((_, j) => j !== i));
  }

  protected problem(v: string): string | null {
    const check = this.check();
    return v && check ? check(v) : null;
  }
}
```

`apps/web-ui/src/app/automations/design/pickers.ts`:

```ts
import { computed, effect, inject, signal, untracked, type Signal } from '@angular/core';
import type { InputSpec } from '@desk/protocol';
import { scriptFiles, skillChoices, type SkillChoice } from '@desk/ui-core';
import { DeskBridge } from '../../core/desk-bridge';

/** The skills a step can name (project, global, built-in), loaded once per project. Empty until loaded or when loading fails. */
export function injectSkillChoices(projectId: () => string): Signal<SkillChoice[]> {
  const bridge = inject(DeskBridge);
  const choices = signal<SkillChoice[]>([]);
  let seq = 0;
  effect(() => {
    const id = projectId();
    const n = ++seq;
    Promise.all([bridge.call('skills.list', { projectId: id }), bridge.call('skills.list', {}), bridge.call('builtins.list', { projectId: id })])
      .then(([project, global, builtins]) => {
        if (n === seq) choices.set(skillChoices(project, global, builtins));
      })
      .catch(() => {});
  });
  return choices.asReadonly();
}

/** The scripts of the skill a script step names, from the copy agents would use. */
export function injectSkillScripts(projectId: () => string, choice: () => SkillChoice | null): Signal<{ scripts: string[]; loading: boolean }> {
  const bridge = inject(DeskBridge);
  const state = signal<{ key: string; scripts: string[] } | null>(null);
  // The key names the choice; the object itself is rebuilt whenever the skill list is.
  const key = computed(() => {
    const c = choice();
    return c ? `${c.source}:${c.name}` : '';
  });
  let seq = 0;
  effect(() => {
    const k = key();
    const id = projectId();
    const c = untracked(choice);
    if (!c) return;
    const n = ++seq;
    const load = c.source === 'builtin' ? bridge.call('builtins.get', { name: c.name }) : c.source === 'project' ? bridge.call('skills.get', { projectId: id, name: c.name }) : bridge.call('skills.get', { name: c.name });
    load.then(
      (d) => {
        if (n === seq) state.set({ key: k, scripts: scriptFiles(d.files) });
      },
      () => {
        if (n === seq) state.set({ key: k, scripts: [] });
      },
    );
  });
  return computed(() => {
    const k = key();
    const s = state();
    return { scripts: s?.key === k ? s.scripts : [], loading: k !== '' && s?.key !== k };
  });
}

/** The project's automation names (Run automation's picker). */
export function injectAutomationNames(projectId: () => string): Signal<string[]> {
  const bridge = inject(DeskBridge);
  const names = signal<string[]>([]);
  let seq = 0;
  effect(() => {
    const id = projectId();
    const n = ++seq;
    bridge
      .call('automations.list', { projectId: id })
      .then((list) => {
        if (n === seq) names.set(list.map((a) => a.name).sort());
      })
      .catch(() => {});
  });
  return names.asReadonly();
}

/** The inputs of the project's automation called `name`, or null while unknown. */
export function injectAutomationInputs(projectId: () => string, name: () => string): Signal<InputSpec[] | null> {
  const bridge = inject(DeskBridge);
  const state = signal<{ name: string; inputs: InputSpec[] } | null>(null);
  let seq = 0;
  effect(() => {
    const id = projectId();
    const want = name();
    if (!want) return;
    const n = ++seq;
    bridge
      .call('automations.list', { projectId: id })
      .then((list) => {
        const a = list.find((x) => x.name === want);
        return a ? bridge.call('automations.get', { id: a.id }).then((d) => d.definition.inputs) : [];
      })
      .then(
        (inputs) => {
          if (n === seq) state.set({ name: want, inputs });
        },
        () => {
          if (n === seq) state.set({ name: want, inputs: [] });
        },
      );
  });
  return computed(() => {
    const want = name();
    const s = state();
    return want && s?.name === want ? s.inputs : null;
  });
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/design/template-field.spec.ts) && pnpm --filter @desk/web-ui typecheck`
Expected: PASS. `ListEditor` and the pickers are covered by Task 6's inspector specs.

- [ ] **Step 5: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-ui/src/app/automations/design/template-field.ts apps/web-ui/src/app/automations/design/template-field.spec.ts apps/web-ui/src/app/automations/design/list-editor.ts apps/web-ui/src/app/automations/design/pickers.ts && git commit -m "feat(web): the template field with {{…}} suggestions, the list editor, and the skill and automation pickers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 6: `StepKindFields`, `StepInspector` and `EdgeInspector`

**Files:**
- Create: `apps/web-ui/src/app/automations/design/step-kind-fields.ts`
- Create: `apps/web-ui/src/app/automations/design/step-inspector.ts`
- Create: `apps/web-ui/src/app/automations/design/edge-inspector.ts`
- Create: `apps/web-ui/src/app/automations/design/inspectors.spec.ts`

**Interfaces:**
- Consumes: Task 5's `TemplateField`, `ListEditor`, `injectSkillChoices`, `injectSkillScripts`, `injectAutomationNames`, `injectAutomationInputs`; `patchStep`, `renameStep`, `routeProblem`, `incomingCount`, `templateSuggestions`, `STEP_KIND_LABEL`, `keyProblem`, `conditionSuggestions`, `edgeRouteOptions`, `patchEdge`, `AutomationDoc` (`@desk/ui-core`); `MAX_WAIT_MINUTES`, `ReasoningEffort` (`@desk/protocol`); `Button`, `Field`.
- Produces (used by Task 8's `DesignView`):
  - `aside[deskStepInspector]` (`StepInspector`). Inputs: `projectId`, `doc: AutomationDoc`, `stepId`, `selfName`, `sources: Array<{ id: string; label: string }>`, `errors: string[]`. Outputs: `docChange: AutomationDoc`, `renamed: string`, `remove: void`.
  - `aside[deskEdgeInspector]` (`EdgeInspector`). Inputs: `doc`, `index: number`, `errors`. Outputs: `docChange`, `remove`.
  - `div[deskStepKindFields]` (`StepKindFields`). Inputs: `projectId`, `selfName`, `sources`, `step: Step`, `suggest: TemplateSuggestion[]`. Output: `patch: Partial<Step>`. It renders `div[deskScriptFields]`, `div[deskAgentFields]`, `div[deskAskFields]`, `div[deskWaitFields]`, `div[deskAutomationFields]` or `div[deskTellFields]`, each a `display: contents` host.

- [ ] **Step 1: Write the failing spec (a port of `inspectors.test.tsx`)**

`apps/web-ui/src/app/automations/design/inspectors.spec.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, input, linkedSignal } from '@angular/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import type { AutomationDefinition } from '@desk/protocol';
import { addStep, START_ID, type AutomationDoc, type GraphSelection } from '@desk/ui-core';
import { digestDef } from '@desk/ui-core/testing';
import { FakeDeskBridge } from '../../testing/fake-bridge';
import { EdgeInspector } from './edge-inspector';
import { StepInspector } from './step-inspector';

const doc = (def: AutomationDefinition = digestDef()): AutomationDoc => ({ def, layout: { [START_ID]: { x: 0, y: 0 } } });

/** The React Harness: holds the doc and the selection, as DesignView would. */
@Component({
  selector: 'desk-inspector-harness',
  imports: [StepInspector, EdgeInspector],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (stepId(); as id) {
      <aside deskStepInspector projectId="p" [doc]="d()" [stepId]="id" selfName="digest" [sources]="sources" [errors]="id === 'ok' ? okErrors : noErrors" (docChange)="change($event)" (renamed)="sel.set({ kind: 'step', id: $event })" (remove)="deleted()()"></aside>
    } @else if (edge() !== null) {
      <aside deskEdgeInspector [doc]="d()" [index]="edge() ?? 0" [errors]="noErrors" (docChange)="change($event)" (remove)="deleted()()"></aside>
    }
  `,
})
class Harness {
  readonly initial = input.required<AutomationDoc>();
  readonly select = input.required<GraphSelection>();
  readonly onDoc = input.required<(d: AutomationDoc) => void>();
  readonly deleted = input<() => void>(() => {});
  protected readonly d = linkedSignal(() => this.initial());
  protected readonly sel = linkedSignal(() => this.select());
  protected readonly stepId = computed(() => {
    const s = this.sel();
    return s.kind === 'step' ? s.id : null;
  });
  protected readonly edge = computed(() => {
    const s = this.sel();
    return s.kind === 'edge' ? s.index : null;
  });
  protected readonly sources = [{ id: 's1', label: 'site' }];
  protected readonly okErrors = ['question: required'];
  protected readonly noErrors: string[] = [];

  protected change(next: AutomationDoc): void {
    this.d.set(next);
    this.onDoc()(next);
  }
}

function bridge() {
  return new FakeDeskBridge({
    'skills.list': (i: { projectId?: string }) => (i.projectId ? [{ name: 'digest', scope: 'project', description: '', dir: '/d', version: 1 }] : []),
    'builtins.list': () => [{ name: 'pdf-toolkit', enabled: true, broken: null }],
    'skills.get': () => ({ name: 'digest', files: [{ path: 'SKILL.md', size: 1 }, { path: 'scripts/fetch.py', size: 1 }, { path: 'scripts/diff.py', size: 1 }] }),
    'automations.list': () => [
      { id: 'a1', name: 'digest' },
      { id: 'a2', name: 'notify' },
    ],
    'automations.get': () => ({ definition: { inputs: [{ key: 'message', label: 'Message', type: 'text', required: true }] } }),
  });
}

const show = (initial: AutomationDoc, select: GraphSelection, onDoc: (d: AutomationDoc) => void, deleted: () => void = () => {}) =>
  render(Harness, { inputs: { initial, select, onDoc, deleted }, providers: bridge().providers });

describe('StepInspector', () => {
  it('edits a script step: title, id (everywhere), skill and script pickers, arguments, routes and errors', async () => {
    const onDoc = vi.fn();
    await show(doc(), { kind: 'step', id: 'fetch' }, onDoc);
    expect(screen.getByText('Script step')).toBeTruthy();
    fireEvent.input(screen.getByLabelText('Title'), { target: { value: 'Fetch the pages' } });
    expect(onDoc.mock.lastCall![0].def.steps[0].title).toBe('Fetch the pages');

    const id = screen.getByLabelText('Id');
    fireEvent.input(id, { target: { value: 'Bad Id' } });
    fireEvent.blur(id);
    expect(screen.getByRole('alert').textContent).toMatch(/lowercase/i);
    fireEvent.input(id, { target: { value: 'grab' } });
    fireEvent.blur(id);
    expect(onDoc.mock.lastCall![0].def.edges[0]).toEqual({ from: 'grab', to: 'sum', route: 'changed' });

    const skill = (await screen.findByRole('option', { name: 'pdf-toolkit (built-in)' })).closest('select')!;
    expect(within(skill).getAllByRole('option').map((o) => o.textContent)).toEqual(['Choose a skill…', 'digest', 'pdf-toolkit (built-in)']);
    const script = screen.getByLabelText('Script');
    await waitFor(() => expect(within(script).getAllByRole('option').map((o) => o.textContent)).toEqual(['Choose a script…', 'scripts/diff.py', 'scripts/fetch.py']));
    fireEvent.change(script, { target: { value: 'scripts/diff.py' } });
    expect(onDoc.mock.lastCall![0].def.steps[0]).toMatchObject({ skill: 'digest', script: 'scripts/diff.py' });

    fireEvent.click(screen.getByRole('button', { name: 'Add argument' }));
    fireEvent.input(screen.getByLabelText('Arguments 2'), { target: { value: '--fast' } });
    expect(onDoc.mock.lastCall![0].def.steps[0].args).toEqual(['{{inputs.topic}}', '--fast']);

    fireEvent.click(screen.getByRole('button', { name: 'Add route' }));
    fireEvent.input(screen.getByLabelText('Routes 3'), { target: { value: 'error' } });
    expect(screen.getByText(/set by Desk/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText('If it fails'), { target: { value: 'retry' } });
    expect(onDoc.mock.lastCall![0].def.steps[0].on_error).toEqual({ retry: 1 });
    fireEvent.input(screen.getByLabelText('Attempts after the first'), { target: { value: '3' } });
    expect(onDoc.mock.lastCall![0].def.steps[0].on_error).toEqual({ retry: 3 });
  });

  it('edits an agent step: brief, skills, output keys, worktree', async () => {
    const onDoc = vi.fn();
    await show(doc(), { kind: 'step', id: 'sum' }, onDoc);
    fireEvent.input(screen.getByLabelText('Brief'), { target: { value: 'Summarise.' } });
    expect(onDoc.mock.lastCall![0].def.steps[1].brief).toBe('Summarise.');
    fireEvent.click(await screen.findByLabelText('digest'));
    expect(onDoc.mock.lastCall![0].def.steps[1].skills).toEqual(['web-research', 'digest']);
    fireEvent.click(screen.getByRole('button', { name: 'Add output' }));
    fireEvent.input(screen.getByLabelText('Output 2 key'), { target: { value: 'count' } });
    fireEvent.input(screen.getByLabelText('Output 2 meaning'), { target: { value: 'how many changed' } });
    expect(onDoc.mock.lastCall![0].def.steps[1].output_keys).toEqual([
      { key: 'headline', description: 'the biggest change' },
      { key: 'count', description: 'how many changed' },
    ]);
    fireEvent.change(screen.getByLabelText('Work in a git worktree of'), { target: { value: 's1' } });
    expect(onDoc.mock.lastCall![0].def.steps[1].git_source_id).toBe('s1');
  });

  it('shows a step’s problems, switches a wait between minutes and a time, and picks a sub-automation’s inputs', async () => {
    const onDoc = vi.fn();
    const onDelete = vi.fn();
    const withMore = addStep(addStep(doc(), 'wait', 'ok').doc, 'automation', 'ok').doc;
    const view = await show(withMore, { kind: 'step', id: 'ok' }, onDoc, onDelete);
    expect(screen.getByRole('alert').textContent).toContain('question: required');
    fireEvent.click(screen.getByRole('button', { name: 'Delete step' }));
    expect(onDelete).toHaveBeenCalled();

    await view.rerender({ inputs: { select: { kind: 'step', id: 'wait' } }, partialUpdate: true });
    fireEvent.click(screen.getByLabelText('Until a time of day'));
    expect(onDoc.mock.lastCall![0].def.steps.find((s: { id: string }) => s.id === 'wait')).toMatchObject({ until: '08:00' });
    expect('minutes' in onDoc.mock.lastCall![0].def.steps.find((s: { id: string }) => s.id === 'wait')).toBe(false);

    await view.rerender({ inputs: { select: { kind: 'step', id: 'automation' } }, partialUpdate: true });
    const pick = await screen.findByLabelText('Automation to run');
    await waitFor(() => expect(within(pick).getAllByRole('option').map((o) => o.textContent)).toEqual(['Choose an automation…', 'notify']));
    fireEvent.change(pick, { target: { value: 'notify' } });
    fireEvent.input(await screen.findByLabelText('Message'), { target: { value: '{{steps.sum.summary}}' } });
    expect(onDoc.mock.lastCall![0].def.steps.find((s: { id: string }) => s.id === 'automation')).toMatchObject({ automation: 'notify', inputs: { message: '{{steps.sum.summary}}' } });
  });
});

describe('EdgeInspector', () => {
  it('sets an edge’s route and condition, offering the paths its source can see', async () => {
    const onDoc = vi.fn();
    await show(doc(), { kind: 'edge', index: 0 }, onDoc);
    expect(screen.getByText('Fetch pages → Summarise')).toBeTruthy();
    const route = screen.getByLabelText('Fires');
    expect(within(route).getAllByRole('option').map((o) => o.textContent)).toEqual(['When it succeeds', 'On route changed', 'On route unchanged']);
    fireEvent.change(route, { target: { value: 'unchanged' } });
    expect(onDoc.mock.lastCall![0].def.edges[0]).toEqual({ from: 'fetch', to: 'sum', route: 'unchanged' });
    fireEvent.click(screen.getByRole('button', { name: 'steps.fetch.status' }));
    expect(onDoc.mock.lastCall![0].def.edges[0].when).toBe('steps.fetch.status');
    fireEvent.input(screen.getByLabelText('Only if'), { target: { value: '' } });
    expect(onDoc.mock.lastCall![0].def.edges[0]).toEqual({ from: 'fetch', to: 'sum', route: 'unchanged' });
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/design/inspectors.spec.ts)`
Expected: FAIL, `Could not resolve "./edge-inspector"`.

- [ ] **Step 3: Write `step-kind-fields.ts`**

`apps/web-ui/src/app/automations/design/step-kind-fields.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, input, output, ViewEncapsulation } from '@angular/core';
import { MAX_WAIT_MINUTES, ReasoningEffort, type AgentStep, type AskStep, type ScriptStep, type Step, type SubAutomationStep, type TellDeskStep, type WaitStep } from '@desk/protocol';
import { keyProblem, type TemplateSuggestion } from '@desk/ui-core';
import { Button } from '../../components/button';
import { Field } from '../../components/field';
import { ListEditor } from './list-editor';
import { injectAutomationInputs, injectAutomationNames, injectSkillChoices, injectSkillScripts } from './pickers';
import { TemplateField } from './template-field';

type Sources = Array<{ id: string; label: string }>;
const optNumber = (v: string): number | undefined => (v === '' ? undefined : Number(v));
const val = (e: Event): string => (e.target as HTMLInputElement).value;
const checked = (e: Event): boolean => (e.target as HTMLInputElement).checked;

@Component({
  selector: 'div[deskScriptFields]',
  imports: [Field, ListEditor, TemplateField],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskField id="step-skill" label="Skill" hint="The installed skill whose script runs. Threads draft skills; Desk installs them.">
      <select id="step-skill" class="select" (change)="patch.emit({ skill: val($event), script: '' })">
        <option value="" [selected]="!step().skill">Choose a skill…</option>
        @for (c of choices(); track c.name) {
          <option [value]="c.name" [selected]="c.name === step().skill">{{ c.source === 'builtin' ? c.name + ' (built-in)' : c.name }}</option>
        }
        @if (step().skill && !choice()) {
          <option [value]="step().skill" [selected]="true">{{ step().skill + ' (not installed)' }}</option>
        }
      </select>
    </div>
    <div deskField id="step-script" label="Script">
      <select id="step-script" class="select" [disabled]="!step().skill" (change)="patch.emit({ script: val($event) })">
        <option value="" [selected]="!step().script">Choose a script…</option>
        @for (s of scripts().scripts; track s) {
          <option [value]="s" [selected]="s === step().script">{{ s }}</option>
        }
        @if (step().script && !scripts().scripts.includes(step().script)) {
          <option [value]="step().script" [selected]="true">{{ step().script }}</option>
        }
      </select>
    </div>
    <fieldset deskListEditor id="step-args" label="Arguments" [values]="step().args" [suggestions]="suggest()" addLabel="Add argument" [max]="50" [hint]="argsHint" (valuesChange)="patch.emit({ args: $event })"></fieldset>
    <div deskTemplateField id="step-stdin" label="Standard input (optional)" [value]="step().stdin ?? ''" [suggestions]="suggest()" [multiline]="true" [rows]="3" (valueChange)="patch.emit({ stdin: $event || undefined })"></div>
    <label class="auto-check"><input type="checkbox" [checked]="step().idempotent" (change)="patch.emit({ idempotent: checked($event) })" /> Safe to run again after a crash</label>
  `,
})
export class ScriptFields {
  readonly projectId = input.required<string>();
  readonly step = input.required<ScriptStep>();
  readonly suggest = input.required<TemplateSuggestion[]>();
  readonly patch = output<Partial<Step>>();
  protected readonly val = val;
  protected readonly checked = checked;
  protected readonly argsHint = "Each argument goes to the script as it is, never through a shell. {{…}} fills in inputs and earlier steps' results.";
  protected readonly choices = injectSkillChoices(() => this.projectId());
  protected readonly choice = computed(() => this.choices().find((c) => c.name === this.step().skill) ?? null);
  protected readonly scripts = injectSkillScripts(() => this.projectId(), () => this.choice());
}

@Component({
  selector: 'div[deskAgentFields]',
  imports: [Button, Field, TemplateField],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskTemplateField id="step-brief" label="Brief" [value]="step().brief" [suggestions]="suggest()" [multiline]="true" [rows]="6" hint="What the agent should do. It works in the step's folder and can read earlier steps' folders." (valueChange)="patch.emit({ brief: $event })"></div>
    <fieldset class="field">
      <legend>Skills</legend>
      @for (n of names(); track n) {
        <label class="auto-check"><input type="checkbox" [checked]="step().skills.includes(n)" [disabled]="!step().skills.includes(n) && step().skills.length >= 12" (change)="toggleSkill(n, checked($event))" /> {{ n }}</label>
      } @empty {
        <p class="muted small">No skills installed yet.</p>
      }
    </fieldset>
    <fieldset class="field auto-rows">
      <legend>Outputs</legend>
      @for (k of step().output_keys; track $index; let i = $index) {
        <div>
          <div class="auto-row">
            <input class="input mono" placeholder="key" [attr.aria-label]="'Output ' + (i + 1) + ' key'" [value]="k.key" (input)="setKey(i, { key: val($event) })" />
            <input class="input" placeholder="what it holds" [attr.aria-label]="'Output ' + (i + 1) + ' meaning'" [value]="k.description" (input)="setKey(i, { description: val($event) })" />
            <button deskButton size="sm" variant="ghost" [attr.aria-label]="'Remove output ' + (i + 1)" (click)="removeKey(i)">✕</button>
          </div>
          @if (k.key && keyProblem(k.key); as problem) {
            <p class="field-error auto-row-error">{{ problem }}</p>
          }
        </div>
      }
      @if (step().output_keys.length < 20) {
        <div><button deskButton size="sm" (click)="patch.emit({ output_keys: [...step().output_keys, { key: '', description: '' }] })">Add output</button></div>
      }
      <p class="field-hint">{{ outputsHint }}</p>
    </fieldset>
    <div deskField id="step-model" label="Model (optional)" hint="Empty uses the project's default.">
      <input id="step-model" class="input mono" [value]="step().model ?? ''" (input)="patch.emit({ model: val($event) || undefined })" />
    </div>
    <div deskField id="step-effort" label="Reasoning effort">
      <select id="step-effort" class="select" (change)="setEffort(val($event))">
        <option value="" [selected]="!step().reasoning_effort">Default</option>
        @for (r of efforts; track r) {
          <option [value]="r" [selected]="r === step().reasoning_effort">{{ r }}</option>
        }
      </select>
    </div>
    <div deskField id="step-git" label="Work in a git worktree of" [hint]="gitHint">
      <select id="step-git" class="select" (change)="patch.emit({ git_source_id: val($event) || undefined })">
        <option value="" [selected]="!step().git_source_id">No worktree (its step folder)</option>
        @for (s of sources(); track s.id) {
          <option [value]="s.id" [selected]="s.id === step().git_source_id">{{ s.label }}</option>
        }
      </select>
    </div>
    <div deskField id="step-max" label="Most tool calls (optional)">
      <input id="step-max" class="input" type="number" min="1" max="400" [value]="step().max_steps ?? ''" (input)="patch.emit({ max_steps: optNumber(val($event)) })" />
    </div>
  `,
})
export class AgentFields {
  readonly projectId = input.required<string>();
  readonly sources = input.required<Sources>();
  readonly step = input.required<AgentStep>();
  readonly suggest = input.required<TemplateSuggestion[]>();
  readonly patch = output<Partial<Step>>();
  protected readonly val = val;
  protected readonly checked = checked;
  protected readonly optNumber = optNumber;
  protected readonly keyProblem = keyProblem;
  protected readonly efforts = ReasoningEffort.options;
  protected readonly outputsHint = 'What later steps can use as {{steps.<id>.outputs.<key>}}. The agent sets them when it completes.';
  protected readonly gitHint = 'Its branch is desk/auto-<name>-<run>. Desk never merges it.';
  private readonly choices = injectSkillChoices(() => this.projectId());
  protected readonly names = computed(() => [...new Set([...this.choices().map((c) => c.name), ...this.step().skills])]);

  protected toggleSkill(name: string, on: boolean): void {
    const skills = this.step().skills;
    this.patch.emit({ skills: on ? [...skills, name] : skills.filter((s) => s !== name) });
  }

  protected setKey(i: number, k: Partial<{ key: string; description: string }>): void {
    this.patch.emit({ output_keys: this.step().output_keys.map((x, j) => (j === i ? { ...x, ...k } : x)) });
  }

  protected removeKey(i: number): void {
    this.patch.emit({ output_keys: this.step().output_keys.filter((_, j) => j !== i) });
  }

  protected setEffort(v: string): void {
    this.patch.emit({ reasoning_effort: (v || undefined) as AgentStep['reasoning_effort'] });
  }
}

@Component({
  selector: 'div[deskAskFields]',
  imports: [Field, ListEditor, TemplateField],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskTemplateField id="step-question" label="Question" [value]="step().question" [suggestions]="suggest()" [multiline]="true" [rows]="3" (valueChange)="patch.emit({ question: $event })"></div>
    <fieldset deskListEditor id="step-show" label="Files to show" [values]="step().show" placeholder="digest.md" addLabel="Add file" [max]="20" hint="Globs of files in earlier steps' folders, shown with the question." (valuesChange)="patch.emit({ show: $event })"></fieldset>
    <div class="auto-inline">
      <div deskField id="step-approve" label="Approve button">
        <input id="step-approve" class="input" placeholder="Approve" [value]="step().approve_label ?? ''" (input)="patch.emit({ approve_label: val($event) || undefined })" />
      </div>
      <div deskField id="step-reject" label="Reject button">
        <input id="step-reject" class="input" placeholder="Reject" [value]="step().reject_label ?? ''" (input)="patch.emit({ reject_label: val($event) || undefined })" />
      </div>
    </div>
    <div deskField id="step-expires" label="Expires after (hours, optional)" hint="Rejecting, or letting it expire, takes the route rejected.">
      <input id="step-expires" class="input" type="number" min="1" max="720" [value]="step().expires_after_hours ?? ''" (input)="patch.emit({ expires_after_hours: optNumber(val($event)) })" />
    </div>
  `,
})
export class AskFields {
  readonly step = input.required<AskStep>();
  readonly suggest = input.required<TemplateSuggestion[]>();
  readonly patch = output<Partial<Step>>();
  protected readonly val = val;
  protected readonly optNumber = optNumber;
}

@Component({
  selector: 'div[deskWaitFields]',
  imports: [Field],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <fieldset class="field">
      <legend>Wait</legend>
      <label class="auto-check"><input type="radio" name="wait-mode" [checked]="!byTime()" (change)="patch.emit({ minutes: 60, until: undefined })" /> For a number of minutes</label>
      <label class="auto-check"><input type="radio" name="wait-mode" [checked]="byTime()" (change)="patch.emit({ until: '08:00', minutes: undefined })" /> Until a time of day</label>
    </fieldset>
    @if (byTime()) {
      <div deskField id="step-until" label="Until" hint="In the automation's timezone: its first schedule's, else this computer's.">
        <input id="step-until" class="input" type="time" [value]="step().until ?? ''" (input)="patch.emit({ until: val($event) })" />
      </div>
    } @else {
      <div deskField id="step-minutes" label="Minutes" hint="At most a week.">
        <input id="step-minutes" class="input" type="number" min="1" [attr.max]="maxMinutes" [value]="step().minutes ?? ''" (input)="patch.emit({ minutes: optNumber(val($event)) })" />
      </div>
    }
  `,
})
export class WaitFields {
  readonly step = input.required<WaitStep>();
  readonly patch = output<Partial<Step>>();
  protected readonly val = val;
  protected readonly optNumber = optNumber;
  protected readonly maxMinutes = MAX_WAIT_MINUTES;
  protected readonly byTime = computed(() => this.step().until !== undefined);
}

@Component({
  selector: 'div[deskAutomationFields]',
  imports: [Field, TemplateField],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskField id="step-automation" label="Automation to run" hint="Its result is its output step's outputs and folder.">
      <select id="step-automation" class="select" (change)="patch.emit({ automation: val($event), inputs: {} })">
        <option value="" [selected]="!step().automation">Choose an automation…</option>
        @for (n of names(); track n) {
          <option [value]="n" [selected]="n === step().automation">{{ n }}</option>
        }
        @if (step().automation && !names().includes(step().automation)) {
          <option [value]="step().automation" [selected]="true">{{ step().automation }}</option>
        }
      </select>
    </div>
    @for (i of inputs() ?? []; track i.key) {
      <div deskTemplateField [id]="'step-input-' + i.key" [label]="i.required ? i.label : i.label + ' (optional)'" [value]="step().inputs[i.key] ?? ''" [suggestions]="suggest()" [hint]="i.description" (valueChange)="setInput(i.key, $event)"></div>
    }
  `,
})
export class AutomationFields {
  readonly projectId = input.required<string>();
  readonly selfName = input.required<string>();
  readonly step = input.required<SubAutomationStep>();
  readonly suggest = input.required<TemplateSuggestion[]>();
  readonly patch = output<Partial<Step>>();
  protected readonly val = val;
  private readonly allNames = injectAutomationNames(() => this.projectId());
  protected readonly names = computed(() => this.allNames().filter((n) => n !== this.selfName()));
  protected readonly inputs = injectAutomationInputs(() => this.projectId(), () => this.step().automation);

  protected setInput(key: string, v: string): void {
    const rest = Object.fromEntries(Object.entries(this.step().inputs).filter(([k]) => k !== key));
    this.patch.emit({ inputs: v ? { ...rest, [key]: v } : rest });
  }
}

@Component({
  selector: 'div[deskTellFields]',
  imports: [ListEditor, TemplateField],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskTemplateField id="step-text" label="Message to Desk" [value]="step().text" [suggestions]="suggest()" [multiline]="true" [rows]="4" hint="Desk reads it at its next turn, as a message from this automation." (valueChange)="patch.emit({ text: $event })"></div>
    <fieldset deskListEditor id="step-attach" label="Files to attach" [values]="step().attach" placeholder="report.md" addLabel="Add file" [max]="20" hint="Globs of files in earlier steps' folders; Desk gets their paths." (valuesChange)="patch.emit({ attach: $event })"></fieldset>
  `,
})
export class TellFields {
  readonly step = input.required<TellDeskStep>();
  readonly suggest = input.required<TemplateSuggestion[]>();
  readonly patch = output<Partial<Step>>();
}

/** The fields of one step kind (spec §4). */
@Component({
  selector: 'div[deskStepKindFields]',
  imports: [AgentFields, AskFields, AutomationFields, ScriptFields, TellFields, WaitFields],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @if (script(); as s) {
      <div deskScriptFields [projectId]="projectId()" [step]="s" [suggest]="suggest()" (patch)="patch.emit($event)"></div>
    } @else if (agent(); as s) {
      <div deskAgentFields [projectId]="projectId()" [sources]="sources()" [step]="s" [suggest]="suggest()" (patch)="patch.emit($event)"></div>
    } @else if (ask(); as s) {
      <div deskAskFields [step]="s" [suggest]="suggest()" (patch)="patch.emit($event)"></div>
    } @else if (wait(); as s) {
      <div deskWaitFields [step]="s" (patch)="patch.emit($event)"></div>
    } @else if (sub(); as s) {
      <div deskAutomationFields [projectId]="projectId()" [selfName]="selfName()" [step]="s" [suggest]="suggest()" (patch)="patch.emit($event)"></div>
    } @else if (tell(); as s) {
      <div deskTellFields [step]="s" [suggest]="suggest()" (patch)="patch.emit($event)"></div>
    }
  `,
})
export class StepKindFields {
  readonly projectId = input.required<string>();
  readonly selfName = input.required<string>();
  readonly sources = input.required<Sources>();
  readonly step = input.required<Step>();
  readonly suggest = input.required<TemplateSuggestion[]>();
  readonly patch = output<Partial<Step>>();
  protected readonly script = computed(() => narrow(this.step(), 'script'));
  protected readonly agent = computed(() => narrow(this.step(), 'agent'));
  protected readonly ask = computed(() => narrow(this.step(), 'ask'));
  protected readonly wait = computed(() => narrow(this.step(), 'wait'));
  protected readonly sub = computed(() => narrow(this.step(), 'automation'));
  protected readonly tell = computed(() => narrow(this.step(), 'tell_desk'));
}

/** The step as its kind's type, or null (templates cannot narrow a signal's value). */
function narrow<K extends Step['kind']>(step: Step, kind: K): Extract<Step, { kind: K }> | null {
  return step.kind === kind ? (step as Extract<Step, { kind: K }>) : null;
}
```

- [ ] **Step 4: Write `step-inspector.ts` and `edge-inspector.ts`**

`apps/web-ui/src/app/automations/design/step-inspector.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, input, linkedSignal, output, ViewEncapsulation } from '@angular/core';
import type { OnError, Step } from '@desk/protocol';
import { incomingCount, patchStep, renameStep, routeProblem, STEP_KIND_LABEL, templateSuggestions, type AutomationDoc } from '@desk/ui-core';
import { Button } from '../../components/button';
import { Field } from '../../components/field';
import { ListEditor } from './list-editor';
import { StepKindFields } from './step-kind-fields';

const retries = (e: OnError): number | null => (typeof e === 'object' ? e.retry : null);

/** The selected step's fields (spec §8.2): common ones, then its kind's. */
@Component({
  selector: 'aside[deskStepInspector]',
  imports: [Button, Field, ListEditor, StepKindFields],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-inspector', 'aria-label': 'Step' },
  template: `
    @if (step(); as step) {
      <p class="eyebrow">{{ kindLabel() + ' step' }}</p>
      @if (errors().length || idError()) {
        <ul class="auto-issues" role="alert">
          @if (idError()) {
            <li>{{ idError() }}</li>
          }
          @for (e of errors(); track e) {
            <li>{{ e }}</li>
          }
        </ul>
      }
      <div deskField id="step-title" label="Title">
        <input id="step-title" class="input" [value]="step.title" (input)="patch({ title: val($event) })" />
      </div>
      <div deskField id="step-id" label="Id" hint="Templates name this step by it; renaming updates them.">
        <input id="step-id" class="input mono" [value]="idDraft()" (input)="idDraft.set(val($event))" (blur)="commitId()" (keydown.enter)="commitId()" />
      </div>
      <div deskStepKindFields [projectId]="projectId()" [selfName]="selfName()" [sources]="sources()" [step]="step" [suggest]="suggest()" (patch)="patch($event)"></div>
      @if (joins()) {
        <div deskField id="step-join" label="With several steps before it">
          <select id="step-join" class="select" (change)="setJoin(val($event))">
            <option value="all" [selected]="step.join === 'all'">Wait for all of them, run if one led here</option>
            <option value="any" [selected]="step.join === 'any'">Run on the first that leads here</option>
          </select>
        </div>
      }
      <div class="auto-inline">
        <div deskField id="step-on-error" label="If it fails">
          <select id="step-on-error" class="select" (change)="setOnError(val($event))">
            <option value="stop" [selected]="mode() === 'stop'">Stop the run</option>
            <option value="continue" [selected]="mode() === 'continue'">Continue on route error</option>
            <option value="retry" [selected]="mode() === 'retry'">Try again</option>
          </select>
        </div>
        @if (mode() === 'retry') {
          <div deskField id="step-retries" label="Attempts after the first">
            <input id="step-retries" class="input" type="number" min="1" max="3" [value]="attempts()" (input)="setRetries(val($event))" />
          </div>
        }
      </div>
      @if (chooses()) {
        <div deskField id="step-timeout" label="Time limit in minutes (optional)" [hint]="step.kind === 'script' ? 'Default 10.' : 'Default 60.'">
          <input id="step-timeout" class="input" type="number" min="1" max="1440" [value]="step.timeout_min ?? ''" (input)="setTimeoutMin(val($event))" />
        </div>
        <fieldset deskListEditor id="step-routes" label="Routes" [values]="step.routes" [check]="routeProblem" addLabel="Add route" [max]="10" hint="Named outcomes it can choose. A route no edge takes ends that branch." (valuesChange)="patch({ routes: $event })"></fieldset>
        <fieldset deskListEditor id="step-publish" label="Publish to the Library" [values]="step.publish" placeholder="digest.md" addLabel="Add file" [max]="20" hint="Globs in its folder, copied to the Library after it succeeds." (valuesChange)="patch({ publish: $event })"></fieldset>
      }
      <div><button deskButton variant="danger" size="sm" (click)="remove.emit()">Delete step</button></div>
    }
  `,
})
export class StepInspector {
  readonly projectId = input.required<string>();
  readonly doc = input.required<AutomationDoc>();
  readonly stepId = input.required<string>();
  /** This automation's name: Run automation cannot pick it. */
  readonly selfName = input.required<string>();
  /** Git sources, for an agent's worktree. */
  readonly sources = input.required<Array<{ id: string; label: string }>>();
  readonly errors = input.required<string[]>();
  /** React's onChange. */
  readonly docChange = output<AutomationDoc>();
  /** The step's id changed: the selection should follow it. */
  readonly renamed = output<string>();
  /** React's onDelete. */
  readonly remove = output<void>();

  protected readonly routeProblem = routeProblem;
  protected readonly step = computed(() => this.doc().def.steps.find((s) => s.id === this.stepId()));
  protected readonly idDraft = linkedSignal(() => this.stepId());
  protected readonly idError = linkedSignal<string, string | null>({ source: this.stepId, computation: () => null });
  protected readonly kindLabel = computed(() => {
    const s = this.step();
    return s ? STEP_KIND_LABEL[s.kind] : '';
  });
  protected readonly suggest = computed(() => templateSuggestions(this.doc().def, this.stepId()));
  protected readonly joins = computed(() => incomingCount(this.doc().def, this.stepId()) >= 2);
  protected readonly mode = computed(() => {
    const e = this.step()?.on_error ?? 'stop';
    return typeof e === 'object' ? 'retry' : e;
  });
  protected readonly attempts = computed(() => retries(this.step()?.on_error ?? 'stop') ?? 1);
  protected readonly chooses = computed(() => {
    const kind = this.step()?.kind;
    return kind === 'script' || kind === 'agent';
  });

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  protected patch(p: Partial<Step>): void {
    const step = this.step();
    if (step) this.docChange.emit(patchStep(this.doc(), step.id, p));
  }

  protected commitId(): void {
    const step = this.step();
    if (!step) return;
    const next = this.idDraft().trim();
    const r = renameStep(this.doc(), step.id, next);
    if ('error' in r) {
      this.idError.set(r.error);
      return;
    }
    this.idError.set(null);
    if (next !== step.id) {
      this.docChange.emit(r.doc);
      this.renamed.emit(next);
    }
  }

  protected setJoin(v: string): void {
    this.patch({ join: v as Step['join'] });
  }

  protected setOnError(v: string): void {
    this.patch({ on_error: v === 'retry' ? { retry: 1 } : (v as 'stop' | 'continue') });
  }

  protected setRetries(v: string): void {
    this.patch({ on_error: { retry: Math.min(3, Math.max(1, Number(v) || 1)) } });
  }

  protected setTimeoutMin(v: string): void {
    this.patch({ timeout_min: v === '' ? undefined : Number(v) });
  }
}
```

`apps/web-ui/src/app/automations/design/edge-inspector.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, input, output, ViewEncapsulation } from '@angular/core';
import { conditionSuggestions, edgeRouteOptions, patchEdge, type AutomationDoc } from '@desk/ui-core';
import { Button } from '../../components/button';
import { Field } from '../../components/field';

/** The selected edge (spec §3.2): what its source must do for it to fire, and an optional condition. */
@Component({
  selector: 'aside[deskEdgeInspector]',
  imports: [Button, Field],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-inspector', 'aria-label': 'Edge' },
  template: `
    @if (edge(); as edge) {
      <p class="eyebrow">Edge</p>
      <h2>{{ title(edge.from) + ' → ' + title(edge.to) }}</h2>
      @if (errors().length) {
        <ul class="auto-issues" role="alert">
          @for (e of errors(); track e) {
            <li>{{ e }}</li>
          }
        </ul>
      }
      <div deskField id="edge-route" label="Fires">
        <select id="edge-route" class="select" (change)="set({ route: val($event) || undefined })">
          @for (r of routeOptions(); track r.value) {
            <option [value]="r.value" [selected]="r.value === (edge.route ?? '')">{{ r.label }}</option>
          }
        </select>
      </div>
      <div deskField id="edge-when" label="Only if" [hint]="whenHint">
        <input id="edge-when" class="input mono" [value]="edge.when ?? ''" (input)="setWhen(val($event))" />
      </div>
      @if (paths().length) {
        <div class="auto-paths" aria-label="Paths it can use">
          @for (p of paths(); track p.path) {
            <button type="button" class="auto-path" [attr.title]="p.label" (click)="append(p.path)">{{ p.path }}</button>
          }
        </div>
      }
      <div><button deskButton variant="danger" size="sm" (click)="remove.emit()">Delete edge</button></div>
    }
  `,
})
export class EdgeInspector {
  readonly doc = input.required<AutomationDoc>();
  readonly index = input.required<number>();
  readonly errors = input.required<string[]>();
  /** React's onChange. */
  readonly docChange = output<AutomationDoc>();
  /** React's onDelete. */
  readonly remove = output<void>();

  protected readonly whenHint = 'Optional, e.g. steps.fetch.outputs.count > 0. Use == != < <= > >= contains exists(…) and or not.';
  protected readonly edge = computed(() => this.doc().def.edges[this.index()]);
  protected readonly routeOptions = computed(() => {
    const e = this.edge();
    return e ? edgeRouteOptions(this.doc().def, e.from, e.route) : [];
  });
  protected readonly paths = computed(() => {
    const e = this.edge();
    return e ? conditionSuggestions(this.doc().def, e.from).filter((s) => !s.open) : [];
  });

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  protected title(id: string): string {
    return this.doc().def.steps.find((s) => s.id === id)?.title ?? id;
  }

  protected set(p: { route?: string | undefined; when?: string | undefined }): void {
    this.docChange.emit(patchEdge(this.doc(), this.index(), p));
  }

  protected setWhen(v: string): void {
    this.set({ when: v.trim() ? v : undefined });
  }

  protected append(path: string): void {
    const when = this.edge()?.when;
    this.set({ when: when ? `${when.trimEnd()} ${path}` : path });
  }
}
```

- [ ] **Step 5: Run it to verify it passes**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/design/inspectors.spec.ts) && pnpm --filter @desk/web-ui typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-ui/src/app/automations/design/step-kind-fields.ts apps/web-ui/src/app/automations/design/step-inspector.ts apps/web-ui/src/app/automations/design/edge-inspector.ts apps/web-ui/src/app/automations/design/inspectors.spec.ts && git commit -m "feat(web): the step and edge inspectors, with each step kind's fields

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 7: `StartInspector` and `SettingsInspector`

**Files:**
- Create: `apps/web-ui/src/app/automations/design/start-inspector.ts`
- Create: `apps/web-ui/src/app/automations/design/settings-inspector.ts`
- Create: `apps/web-ui/src/app/automations/design/start.spec.ts`

**Interfaces:**
- Consumes: `cronOf`, `dayTime`, `INPUT_TYPE_LABEL`, `inputValueOf`, `newInput`, `newSchedule`, `patchInput`, `presetOf`, `renameInput`, `retypeInput`, `setInputs`, `setTriggers`, `switchPreset`, `timezones`, `SchedulePreset`, `AFTER_RUN_LABEL`, `setMeta`, `AutomationDoc` (`@desk/ui-core`); Task 5's `ListEditor`; `NowService`; `Button`, `Field`.
- Produces (used by Task 8's `DesignView`):
  - `aside[deskStartInspector]` (`StartInspector`). Inputs: `doc`, `errors: string[]`, `nextTimes: Record<string, string[]>`. Output: `docChange`. It renders `fieldset[deskScheduleEditor]` (`ScheduleEditor`) and `fieldset[deskInputEditor]` (`InputEditor`).
  - `aside[deskSettingsInspector]` (`SettingsInspector`). Inputs: `doc`, `issues: string[]`, `warnings: ValidationIssue[]`. Output: `docChange`.

- [ ] **Step 1: Write the failing spec (a port of `start.test.tsx`)**

`apps/web-ui/src/app/automations/design/start.spec.ts`:

```ts
import { ChangeDetectionStrategy, Component, input, signal } from '@angular/core';
import { fireEvent, render, screen, within } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { START_ID, type AutomationDoc } from '@desk/ui-core';
import { digestDef } from '@desk/ui-core/testing';
import { SettingsInspector } from './settings-inspector';
import { StartInspector } from './start-inspector';

const initial = (): AutomationDoc => ({ def: digestDef(), layout: { [START_ID]: { x: 0, y: 0 } } });

@Component({
  selector: 'desk-start-harness',
  imports: [StartInspector],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<aside deskStartInspector [doc]="d()" [errors]="errors" [nextTimes]="next()" (docChange)="change($event)"></aside>`,
})
class Start {
  readonly onDoc = input.required<(d: AutomationDoc) => void>();
  readonly next = input<Record<string, string[]>>({});
  protected readonly d = signal(initial());
  protected readonly errors = ['Schedule 1: cron: fires every minute'];
  protected change(n: AutomationDoc): void {
    this.d.set(n);
    this.onDoc()(n);
  }
}

@Component({
  selector: 'desk-settings-harness',
  imports: [SettingsInspector],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<aside deskSettingsInspector [doc]="d()" [issues]="issues" [warnings]="warnings" (docChange)="change($event)"></aside>`,
})
class Settings {
  readonly onDoc = input.required<(d: AutomationDoc) => void>();
  protected readonly d = signal(initial());
  protected readonly issues = ['Add at least one step'];
  protected readonly warnings = [{ path: 'steps[2]', message: 'Publish? has no edges' }];
  protected change(n: AutomationDoc): void {
    this.d.set(n);
    this.onDoc()(n);
  }
}

describe('StartInspector', () => {
  it('edits a schedule through its preset, time, day and timezone, and previews the next times', async () => {
    const onDoc = vi.fn();
    await render(Start, { inputs: { onDoc, next: { '0': ['2026-10-05T06:00:00.000Z', '2026-10-12T06:00:00.000Z', '2026-10-19T06:00:00.000Z', '2026-10-26T06:00:00.000Z'] } } });
    expect(screen.getByRole('alert').textContent).toContain('fires every minute');
    const s1 = screen.getByRole('group', { name: 'Schedule 1' });
    expect((within(s1).getByLabelText('Repeats') as HTMLSelectElement).value).toBe('weekly');
    expect((within(s1).getByLabelText('On') as HTMLSelectElement).value).toBe('1');
    expect(within(s1).getByText(/^Next: /).textContent!.split(' · ')).toHaveLength(3);
    fireEvent.input(within(s1).getByLabelText('At'), { target: { value: '09:15' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].cron).toBe('15 9 * * 1');
    fireEvent.change(within(s1).getByLabelText('Repeats'), { target: { value: 'monthly' } });
    fireEvent.input(within(s1).getByLabelText('Day of the month'), { target: { value: '15' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].cron).toBe('15 9 15 * *');
    fireEvent.change(within(s1).getByLabelText('Repeats'), { target: { value: 'custom' } });
    fireEvent.input(within(s1).getByLabelText('Cron'), { target: { value: '*/30 9-17 * * 1-5' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].cron).toBe('*/30 9-17 * * 1-5');
    fireEvent.input(within(s1).getByLabelText('Timezone'), { target: { value: 'UTC' } });
    fireEvent.change(within(s1).getByLabelText('If the computer was asleep'), { target: { value: 'skip' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0]).toMatchObject({ timezone: 'UTC', catch_up: 'skip' });
    fireEvent.input(within(s1).getByLabelText('Topic'), { target: { value: 'robots' } });
    expect(onDoc.mock.lastCall![0].def.triggers[0].inputs).toEqual({ topic: 'robots' });
    fireEvent.click(screen.getByRole('button', { name: 'Add schedule' }));
    expect(onDoc.mock.lastCall![0].def.triggers).toHaveLength(2);
    fireEvent.click(within(screen.getByRole('group', { name: 'Schedule 2' })).getByRole('button', { name: 'Remove schedule' }));
    expect(onDoc.mock.lastCall![0].def.triggers).toHaveLength(1);
  });

  it('edits inputs: key renames in templates, type, options, default', async () => {
    const onDoc = vi.fn();
    await render(Start, { inputs: { onDoc } });
    const i1 = screen.getByRole('group', { name: 'Input 1' });
    const key = within(i1).getByLabelText('Key');
    fireEvent.input(key, { target: { value: 'subject' } });
    fireEvent.blur(key);
    expect(onDoc.mock.lastCall![0].def.steps[0].args).toEqual(['{{inputs.subject}}']);
    fireEvent.change(within(i1).getByLabelText('Type'), { target: { value: 'choice' } });
    fireEvent.input(within(i1).getByLabelText('Options 1'), { target: { value: 'robots' } });
    fireEvent.change(within(i1).getByLabelText('Default'), { target: { value: 'robots' } });
    expect(onDoc.mock.lastCall![0].def.inputs[0]).toMatchObject({ key: 'subject', type: 'choice', options: ['robots'], default: 'robots' });
    fireEvent.click(screen.getByRole('button', { name: 'Add input' }));
    expect(onDoc.mock.lastCall![0].def.inputs.map((i: { key: string }) => i.key)).toEqual(['subject', 'input']);
  });
});

describe('SettingsInspector', () => {
  it('edits the title, after-run, result step and limits, and lists problems and warnings', async () => {
    const onDoc = vi.fn();
    await render(Settings, { inputs: { onDoc } });
    expect(screen.getByRole('alert').textContent).toContain('Add at least one step');
    expect(screen.getByText('Publish? has no edges')).toBeTruthy();
    fireEvent.input(screen.getByLabelText('Title'), { target: { value: 'Digest' } });
    fireEvent.change(screen.getByLabelText('After each run'), { target: { value: 'desk_review' } });
    fireEvent.change(screen.getByLabelText('Its result, when another automation runs it'), { target: { value: 'sum' } });
    fireEvent.input(screen.getByLabelText('Agents at once'), { target: { value: '3' } });
    expect(onDoc.mock.lastCall![0].def).toMatchObject({ title: 'Digest', after_run: 'desk_review', output_step: 'sum', limits: { max_parallel_agents: 3 } });
    fireEvent.change(screen.getByLabelText('Its result, when another automation runs it'), { target: { value: '' } });
    expect('output_step' in onDoc.mock.lastCall![0].def).toBe(false);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/design/start.spec.ts)`
Expected: FAIL, `Could not resolve "./settings-inspector"`.

- [ ] **Step 3: Write `start-inspector.ts`**

`apps/web-ui/src/app/automations/design/start-inspector.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, inject, input, linkedSignal, output, signal, ViewEncapsulation } from '@angular/core';
import type { InputSpec, InputType, InputValue, ScheduleTrigger } from '@desk/protocol';
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
import { Button } from '../../components/button';
import { Field } from '../../components/field';
import { NowService } from '../../core/now.service';
import { ListEditor } from './list-editor';

const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const ZONES = timezones();
const val = (e: Event): string => (e.target as HTMLInputElement).value;
const checked = (e: Event): boolean => (e.target as HTMLInputElement).checked;

/** One schedule: its preset (or custom cron), time, timezone, catch-up, and the inputs it runs with. */
@Component({
  selector: 'fieldset[deskScheduleEditor]',
  imports: [Button, Field],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-card', '[attr.aria-label]': "'Schedule ' + (index() + 1)" },
  template: `
    <div class="auto-inline">
      <div deskField [id]="fid('kind')" label="Repeats">
        <select class="select" [id]="fid('kind')" (change)="choose(val($event))">
          <option value="daily" [selected]="preset().kind === 'daily'">Every day</option>
          <option value="weekdays" [selected]="preset().kind === 'weekdays'">Weekdays</option>
          <option value="weekly" [selected]="preset().kind === 'weekly'">Every week</option>
          <option value="monthly" [selected]="preset().kind === 'monthly'">Every month</option>
          <option value="custom" [selected]="preset().kind === 'custom'">Custom (cron)</option>
        </select>
      </div>
      @if (timed(); as p) {
        <div deskField [id]="fid('time')" label="At">
          <input class="input" type="time" [id]="fid('time')" [value]="p.time" (input)="setTime(p, val($event))" />
        </div>
      }
    </div>
    @if (weekly(); as p) {
      <div deskField [id]="fid('day')" label="On">
        <select class="select" [id]="fid('day')" (change)="setPreset({ kind: 'weekly', time: p.time, day: toNumber(val($event)) })">
          @for (d of days; track d; let i = $index) {
            <option [value]="'' + i" [selected]="p.day === i">{{ d }}</option>
          }
        </select>
      </div>
    }
    @if (monthly(); as p) {
      <div deskField [id]="fid('dom')" label="Day of the month" hint="Up to the 28th, which every month has. Use custom cron for later days.">
        <input class="input" type="number" min="1" max="28" [id]="fid('dom')" [value]="p.dom" (input)="setDom(p, val($event))" />
      </div>
    }
    @if (preset().kind === 'custom') {
      <div deskField [id]="fid('cron')" label="Cron" hint="minute hour day-of-month month day-of-week, e.g. */30 9-17 * * 1-5. At most every 5 minutes.">
        <input class="input mono" [id]="fid('cron')" [value]="trigger().cron" (input)="triggerChange.emit({ ...trigger(), cron: val($event) })" />
      </div>
    }
    <div deskField [id]="fid('tz')" label="Timezone">
      <input class="input mono" list="auto-timezones" [id]="fid('tz')" [value]="trigger().timezone" (input)="triggerChange.emit({ ...trigger(), timezone: val($event) })" />
    </div>
    <div deskField [id]="fid('catch')" label="If the computer was asleep">
      <select class="select" [id]="fid('catch')" (change)="setCatchUp(val($event))">
        <option value="once" [selected]="trigger().catch_up === 'once'">Run once when it wakes</option>
        <option value="skip" [selected]="trigger().catch_up === 'skip'">Skip the missed times</option>
      </select>
    </div>
    @if (inputs().length) {
      <fieldset class="field">
        <legend>Inputs for this schedule</legend>
        @for (i of inputs(); track i.key) {
          @if (i.type === 'boolean') {
            <label class="auto-check"><input type="checkbox" [checked]="trigger().inputs?.[i.key] === true" (change)="setInput(i.key, checked($event) || undefined)" /> {{ i.label }}</label>
          } @else {
            <div deskField [id]="fid('in-' + i.key)" [label]="i.label">
              <input class="input" [id]="fid('in-' + i.key)" [type]="i.type === 'number' ? 'number' : 'text'" [value]="stringOf(trigger().inputs?.[i.key])" (input)="setInput(i.key, inputValueOf(i.type, val($event)))" />
            </div>
          }
        }
      </fieldset>
    }
    @if (nextText(); as t) {
      <p class="muted small">{{ t }}</p>
    }
    <div><button deskButton size="sm" variant="ghost" (click)="remove.emit()">Remove schedule</button></div>
  `,
})
export class ScheduleEditor {
  readonly index = input.required<number>();
  readonly trigger = input.required<ScheduleTrigger>();
  readonly inputs = input.required<InputSpec[]>();
  readonly next = input.required<string[]>();
  /** React's onChange. */
  readonly triggerChange = output<ScheduleTrigger>();
  /** React's onRemove. */
  readonly remove = output<void>();

  private readonly now = inject(NowService).now;
  protected readonly val = val;
  protected readonly checked = checked;
  protected readonly inputValueOf = inputValueOf;
  protected readonly days = DAY_NAMES;
  /** Choosing Custom keeps the cron, which may still read as a preset: remember the choice, or the menu would snap back. */
  private readonly custom = signal(false);
  protected readonly preset = computed((): SchedulePreset => {
    const t = this.trigger();
    const derived = presetOf(t.cron);
    return this.custom() || derived.kind === 'custom' ? { kind: 'custom', cron: t.cron } : derived;
  });
  protected readonly timed = computed(() => {
    const p = this.preset();
    return p.kind === 'custom' ? null : p;
  });
  protected readonly weekly = computed(() => {
    const p = this.preset();
    return p.kind === 'weekly' ? p : null;
  });
  protected readonly monthly = computed(() => {
    const p = this.preset();
    return p.kind === 'monthly' ? p : null;
  });
  protected readonly nextText = computed(() => {
    const next = this.next();
    return next.length ? `Next: ${next.slice(0, 3).map((ts) => dayTime(ts, this.now())).join(' · ')}` : null;
  });

  protected fid(f: string): string {
    return `sched-${this.index()}-${f}`;
  }

  protected toNumber(v: string): number {
    return Number(v);
  }

  protected stringOf(v: InputValue | undefined): string {
    return String(v ?? '');
  }

  protected setPreset(p: SchedulePreset): void {
    this.triggerChange.emit({ ...this.trigger(), cron: cronOf(p) });
  }

  protected choose(kind: string): void {
    const k = kind as SchedulePreset['kind'];
    this.custom.set(k === 'custom');
    if (k !== 'custom') this.setPreset(switchPreset(this.preset(), k));
  }

  protected setTime(p: Exclude<SchedulePreset, { kind: 'custom' }>, v: string): void {
    if (v) this.setPreset({ ...p, time: v });
  }

  protected setDom(p: Extract<SchedulePreset, { kind: 'monthly' }>, v: string): void {
    this.setPreset({ ...p, dom: Math.min(28, Math.max(1, Number(v) || 1)) });
  }

  protected setCatchUp(v: string): void {
    this.triggerChange.emit({ ...this.trigger(), catch_up: v as ScheduleTrigger['catch_up'] });
  }

  protected setInput(key: string, v: InputValue | undefined): void {
    const t = this.trigger();
    const rest = Object.fromEntries(Object.entries(t.inputs ?? {}).filter(([k]) => k !== key));
    const next = v === undefined ? rest : { ...rest, [key]: v };
    const { inputs: _old, ...base } = t;
    this.triggerChange.emit(Object.keys(next).length ? { ...base, inputs: next } : base);
  }
}

/** One input: label, key (renames templates on commit), type, required, options, default and description. */
@Component({
  selector: 'fieldset[deskInputEditor]',
  imports: [Button, Field, ListEditor],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-card', '[attr.aria-label]': "'Input ' + (index() + 1)" },
  template: `
    <div class="auto-inline">
      <div deskField [id]="fid('label')" label="Label">
        <input class="input" [id]="fid('label')" [value]="spec().label" (input)="set({ label: val($event) })" />
      </div>
      <div deskField [id]="fid('key')" label="Key" [error]="keyError()">
        <input class="input mono" [id]="fid('key')" [value]="key()" (input)="key.set(val($event))" (blur)="commitKey()" (keydown.enter)="commitKey()" />
      </div>
    </div>
    <div class="auto-inline">
      <div deskField [id]="fid('type')" label="Type">
        <select class="select" [id]="fid('type')" (change)="retype(val($event))">
          @for (t of types; track t.value) {
            <option [value]="t.value" [selected]="t.value === spec().type">{{ t.label }}</option>
          }
        </select>
      </div>
      <label class="auto-check"><input type="checkbox" [checked]="spec().required" (change)="set({ required: checked($event) })" /> Required</label>
    </div>
    @if (spec().type === 'choice') {
      <fieldset deskListEditor label="Options" addLabel="Add option" [id]="fid('options')" [values]="spec().options ?? []" [max]="50" (valuesChange)="set({ options: $event.length ? $event : undefined })"></fieldset>
    }
    @switch (defaultKind()) {
      @case ('boolean') {
        <label class="auto-check"><input type="checkbox" [checked]="spec().default === true" (change)="set({ default: checked($event) || undefined })" /> Ticked by default</label>
      }
      @case ('choice') {
        <div deskField [id]="fid('default')" label="Default">
          <select class="select" [id]="fid('default')" (change)="set({ default: val($event) || undefined })">
            <option value="" [selected]="!spec().default">None</option>
            @for (opt of choiceOptions(); track opt) {
              <option [value]="opt" [selected]="opt === spec().default">{{ opt }}</option>
            }
          </select>
        </div>
      }
      @case ('value') {
        <div deskField [id]="fid('default')" label="Default">
          <input class="input" [id]="fid('default')" [type]="spec().type === 'number' ? 'number' : 'text'" [value]="stringOf(spec().default)" (input)="set({ default: inputValueOf(spec().type, val($event)) })" />
        </div>
      }
    }
    <div deskField [id]="fid('description')" label="Description (optional)">
      <input class="input" [id]="fid('description')" [value]="spec().description ?? ''" (input)="set({ description: val($event) || undefined })" />
    </div>
    <div><button deskButton size="sm" variant="ghost" (click)="removeInput()">Remove input</button></div>
  `,
})
export class InputEditor {
  readonly doc = input.required<AutomationDoc>();
  readonly index = input.required<number>();
  readonly spec = input.required<InputSpec>();
  /** React's onChange. */
  readonly docChange = output<AutomationDoc>();

  protected readonly val = val;
  protected readonly checked = checked;
  protected readonly inputValueOf = inputValueOf;
  protected readonly types = (Object.entries(INPUT_TYPE_LABEL) as Array<[InputType, string]>).map(([value, label]) => ({ value, label }));
  private readonly specKey = computed(() => this.spec().key);
  protected readonly key = linkedSignal(() => this.specKey());
  protected readonly keyError = linkedSignal<string, string | null>({ source: this.specKey, computation: () => null });
  protected readonly defaultKind = computed(() => {
    const type = this.spec().type;
    return type === 'boolean' ? 'boolean' : type === 'choice' ? 'choice' : type === 'file' || type === 'folder' ? 'none' : 'value';
  });
  protected readonly choiceOptions = computed(() => (this.spec().options ?? []).filter(Boolean));

  protected fid(f: string): string {
    return `input-${this.index()}-${f}`;
  }

  protected stringOf(v: InputValue | undefined): string {
    return String(v ?? '');
  }

  protected set(p: Partial<Omit<InputSpec, 'key'>>): void {
    this.docChange.emit(patchInput(this.doc(), this.index(), p));
  }

  protected retype(v: string): void {
    this.docChange.emit(retypeInput(this.doc(), this.index(), v as InputType));
  }

  protected commitKey(): void {
    const next = this.key().trim();
    const r = renameInput(this.doc(), this.spec().key, next);
    if ('error' in r) this.keyError.set(r.error);
    else {
      this.keyError.set(null);
      if (next !== this.spec().key) this.docChange.emit(r.doc);
    }
  }

  protected removeInput(): void {
    this.docChange.emit(setInputs(this.doc(), this.doc().def.inputs.filter((_, j) => j !== this.index())));
  }
}

/** The Start pill (spec §8.2): when it runs, and what it asks for. Run now always works. */
@Component({
  selector: 'aside[deskStartInspector]',
  imports: [Button, InputEditor, ScheduleEditor],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-inspector', 'aria-label': 'Start' },
  template: `
    <p class="eyebrow">Start</p>
    @if (errors().length) {
      <ul class="auto-issues" role="alert">
        @for (e of errors(); track e) {
          <li>{{ e }}</li>
        }
      </ul>
    }
    <h2>When it runs</h2>
    <p class="muted small">Run now always works. Schedules run it while it is on; Desk cannot wake a sleeping Mac.</p>
    @for (t of doc().def.triggers; track $index; let i = $index) {
      <fieldset deskScheduleEditor [index]="i" [trigger]="t" [inputs]="doc().def.inputs" [next]="nextOf(i)" (triggerChange)="setTrigger(i, $event)" (remove)="removeTrigger(i)"></fieldset>
    }
    @if (doc().def.triggers.length < 10) {
      <div><button deskButton size="sm" (click)="addSchedule()">Add schedule</button></div>
    }
    <h2>Inputs</h2>
    <p class="muted small">{{ inputsNote }}</p>
    @for (spec of doc().def.inputs; track $index; let i = $index) {
      <fieldset deskInputEditor [doc]="doc()" [index]="i" [spec]="spec" (docChange)="docChange.emit($event)"></fieldset>
    }
    @if (doc().def.inputs.length < 20) {
      <div><button deskButton size="sm" (click)="addInput()">Add input</button></div>
    }
    <datalist id="auto-timezones">
      @for (z of zones; track z) {
        <option [value]="z"></option>
      }
    </datalist>
  `,
})
export class StartInspector {
  readonly doc = input.required<AutomationDoc>();
  readonly errors = input.required<string[]>();
  readonly nextTimes = input.required<Record<string, string[]>>();
  /** React's onChange. */
  readonly docChange = output<AutomationDoc>();

  protected readonly zones = ZONES;
  protected readonly inputsNote = 'What Run now and Test ask for. Templates use them as {{inputs.<key>}}.';

  protected nextOf(i: number): string[] {
    return this.nextTimes()[String(i)] ?? [];
  }

  protected setTrigger(i: number, t: ScheduleTrigger): void {
    this.docChange.emit(setTriggers(this.doc(), this.doc().def.triggers.map((x, j) => (j === i ? t : x))));
  }

  protected removeTrigger(i: number): void {
    this.docChange.emit(setTriggers(this.doc(), this.doc().def.triggers.filter((_, j) => j !== i)));
  }

  protected addSchedule(): void {
    this.docChange.emit(setTriggers(this.doc(), [...this.doc().def.triggers, newSchedule()]));
  }

  protected addInput(): void {
    const inputs = this.doc().def.inputs;
    this.docChange.emit(setInputs(this.doc(), [...inputs, newInput(inputs)]));
  }
}
```

- [ ] **Step 4: Write `settings-inspector.ts`**

`apps/web-ui/src/app/automations/design/settings-inspector.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, input, output, ViewEncapsulation } from '@angular/core';
import type { AfterRun, AutomationLimits, ValidationIssue } from '@desk/protocol';
import { AFTER_RUN_LABEL, setMeta, type AutomationDoc } from '@desk/ui-core';
import { Field } from '../../components/field';

const clampInt = (v: string, min: number, max: number): number => Math.min(max, Math.max(min, Math.round(Number(v)) || min));

/** With nothing selected: the automation's own settings (spec §2.1, §4.7), its problems and its warnings. */
@Component({
  selector: 'aside[deskSettingsInspector]',
  imports: [Field],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-inspector', 'aria-label': 'Automation' },
  template: `
    <p class="eyebrow">Automation</p>
    @if (issues().length) {
      <ul class="auto-issues" role="alert">
        @for (e of issues(); track e) {
          <li>{{ e }}</li>
        }
      </ul>
    }
    <div deskField id="auto-title" label="Title">
      <input id="auto-title" class="input" [value]="def().title" (input)="meta({ title: val($event) })" />
    </div>
    <div deskField id="auto-description" label="Description">
      <textarea id="auto-description" class="textarea" rows="3" [value]="def().description" (input)="meta({ description: val($event) })"></textarea>
    </div>
    <div deskField id="auto-after" label="After each run">
      <select id="auto-after" class="select" (change)="setAfter(val($event))">
        @for (a of afterRuns; track a.value) {
          <option [value]="a.value" [selected]="a.value === def().after_run">{{ a.label }}</option>
        }
      </select>
    </div>
    <div deskField id="auto-output" label="Its result, when another automation runs it" hint="That step's outputs and folder; its summary is the run's summary.">
      <select id="auto-output" class="select" (change)="meta({ output_step: val($event) || undefined })">
        <option value="" [selected]="!def().output_step">The last step that succeeded</option>
        @for (s of def().steps; track s.id) {
          <option [value]="s.id" [selected]="s.id === def().output_step">{{ s.title }}</option>
        }
      </select>
    </div>
    <div class="auto-inline">
      <div deskField id="auto-deadline" label="Deadline (hours)">
        <input id="auto-deadline" class="input" type="number" min="1" max="168" [value]="def().limits.run_deadline_hours" (input)="setLimit('run_deadline_hours', clampInt(val($event), 1, 168))" />
      </div>
      <div deskField id="auto-agents" label="Agents at once">
        <input id="auto-agents" class="input" type="number" min="1" max="4" [value]="def().limits.max_parallel_agents" (input)="setLimit('max_parallel_agents', clampInt(val($event), 1, 4))" />
      </div>
      <div deskField id="auto-scripts" label="Scripts at once">
        <input id="auto-scripts" class="input" type="number" min="1" max="8" [value]="def().limits.max_parallel_scripts" (input)="setLimit('max_parallel_scripts', clampInt(val($event), 1, 8))" />
      </div>
    </div>
    @if (warnings().length) {
      <h2>Warnings</h2>
      <ul class="auto-plain muted">
        @for (w of warnings(); track $index) {
          <li>{{ w.message }}</li>
        }
      </ul>
    }
    <p class="muted small">Click Start, a step or an edge to edit it.</p>
  `,
})
export class SettingsInspector {
  readonly doc = input.required<AutomationDoc>();
  readonly issues = input.required<string[]>();
  readonly warnings = input.required<ValidationIssue[]>();
  /** React's onChange. */
  readonly docChange = output<AutomationDoc>();

  protected readonly clampInt = clampInt;
  protected readonly afterRuns = (Object.entries(AFTER_RUN_LABEL) as Array<[AfterRun, string]>).map(([value, label]) => ({ value, label }));
  protected readonly def = computed(() => this.doc().def);

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  protected meta(patch: Parameters<typeof setMeta>[1]): void {
    this.docChange.emit(setMeta(this.doc(), patch));
  }

  protected setAfter(v: string): void {
    this.meta({ after_run: v as AfterRun });
  }

  protected setLimit(k: keyof AutomationLimits, v: number): void {
    this.meta({ limits: { ...this.def().limits, [k]: v } });
  }
}
```

- [ ] **Step 5: Run it to verify it passes**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/design/start.spec.ts) && pnpm --filter @desk/web-ui typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-ui/src/app/automations/design/start-inspector.ts apps/web-ui/src/app/automations/design/settings-inspector.ts apps/web-ui/src/app/automations/design/start.spec.ts && git commit -m "feat(web): the Start inspector (schedules and inputs) and the automation's settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 8: `DiffView`, `ConflictDialog` and `DesignView`

**Files:**
- Create: `apps/web-ui/src/app/automations/versions/diff-view.ts`
- Create: `apps/web-ui/src/app/automations/design/conflict-dialog.ts`
- Create: `apps/web-ui/src/app/automations/design/design-view.ts`
- Create: `apps/web-ui/src/app/automations/design/design-view.spec.ts`

**Interfaces:**
- Consumes: Tasks 4–7 (`GraphCanvas`, `StepInspector`, `EdgeInspector`, `StartInspector`, `SettingsInspector`); `addStep`, `autoLayout`, `blankDefinition`, `connect`, `diffDefinitions`, `ensureLayout`, `mapIssues`, `moveNodes`, `originText`, `removeSelection`, `STEP_KIND_LABEL`, `whenText`, `AutomationDoc`, `GraphSelection`, `DefinitionDiff`, `FieldChange` (`@desk/ui-core`); `DeskBridge`, `DeskCallError`, `ToastService`, `RouteService`; `Button`, `Sheet`.
- Produces:
  - `div[deskDiffView]` (`DiffView`). Inputs: `diff: DefinitionDiff`, `labels?: { before: string; after: string }`. Used by Tasks 9 and 13.
  - `div[deskConflictDialog]` (`ConflictDialog`), and `type Conflict = { theirs: AutomationDetail; by: string }`.
  - `div[deskDesignView]` (`DesignView`). Inputs: `projectId`, `sources`, `detail: AutomationDetail | null = null`, `draftName: string | null = null`. Output: `detailChange: AutomationDetail`. Task 14's screen renders it keyed by the automation id or the draft name.

- [ ] **Step 1: Write the failing spec (a port of `DesignView.test.tsx`)**

`apps/web-ui/src/app/automations/design/design-view.spec.ts`:

```ts
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AutomationDefinition, AutomationDetail } from '@desk/protocol';
import { automationDetail, digestDef } from '@desk/ui-core/testing';
import { FakeDeskBridge, type FakeHandlers } from '../../testing/fake-bridge';
import { DesignView } from './design-view';

beforeEach(() => void (window.location.hash = '#/p/p/automations/a1'));

type Req = { req: { definition: AutomationDefinition; base_version?: number; name?: string } };
const valid = () => ({ errors: [], warnings: [], next_times: {} });
const common: FakeHandlers = { 'skills.list': () => [], 'builtins.list': () => [], 'automations.layout': () => ({ ok: true }) };

async function design(handlers: FakeHandlers, inputs: { detail?: AutomationDetail; draftName?: string }, onChange = vi.fn()) {
  const bridge = new FakeDeskBridge({ ...common, ...handlers });
  await render(DesignView, { inputs: { projectId: 'p', sources: [], ...inputs }, on: { detailChange: onChange }, providers: bridge.providers });
  return bridge;
}

describe('DesignView', () => {
  it('marks problems on their node, blocks Save, and shows them in the step inspector', async () => {
    await design({ 'automations.validate': () => ({ errors: [{ path: 'steps[2].question', message: 'Too small' }], warnings: [], next_times: {} }) }, { detail: automationDetail() });
    await waitFor(() => expect(screen.getByTestId('node-ok').className).toContain('invalid'));
    expect(screen.getByRole('status').textContent).toBe('1 problem');
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId('node-ok'));
    expect(screen.getByText('Ask me step')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('question: Too small');
  });

  it('adds a step from the strip, edits it and saves with a note on the current version', async () => {
    const onChange = vi.fn();
    const bridge = await design({ 'automations.validate': valid, 'automations.save': ({ req }: Req) => ({ automation: automationDetail({ version: 8, definition: req.definition }), warnings: [] }) }, { detail: automationDetail() }, onChange);
    await screen.findByText('Saved as v7');
    fireEvent.click(within(screen.getByRole('toolbar', { name: 'Add a step' })).getByRole('button', { name: 'Tell Desk' }));
    expect(await screen.findByTestId('node-tell_desk')).toBeTruthy();
    expect(screen.getByText('Tell Desk step')).toBeTruthy();
    fireEvent.input(screen.getByLabelText('Message to Desk'), { target: { value: 'Done.' } });
    fireEvent.input(screen.getByLabelText('Change note'), { target: { value: 'Tells Desk' } });
    await screen.findByText('Ready to save');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ version: 8 })));
    expect(bridge.calls.find((c) => c.channel === 'automations.save')?.input).toMatchObject({ id: 'a1', req: { base_version: 7, change_note: 'Tells Desk', via: 'editor' } });
    await screen.findByText('Saved as v8');
  });

  it('on a 409 shows who saved, the changes, and saves mine on the new base', async () => {
    const onChange = vi.fn();
    const theirs = automationDetail({ version: 8, definition: { ...digestDef(), title: 'Digest (Desk)' } });
    const bridge = await design(
      {
        'automations.validate': valid,
        'automations.save': ({ req }: Req) => {
          if (req.base_version === 7) throw { code: 'conflict', message: 'stale', status: 409 };
          return { automation: automationDetail({ version: 9, definition: req.definition }), warnings: [] };
        },
        'automations.get': () => theirs,
        'automations.versions': () => [{ version: 8, origin: 'agent:d1', change_note: 'x', via: 'tool', created_at: '2026-09-27T09:00:00.000Z', tested: false }],
      },
      { detail: automationDetail() },
      onChange,
    );
    await screen.findByText('Saved as v7');
    fireEvent.input(screen.getByLabelText('Title'), { target: { value: 'My digest' } });
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
    const bridge = await design({ 'automations.validate': valid }, { detail: automationDetail() });
    fireEvent.click(screen.getByRole('button', { name: 'Tidy up' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.layout')?.input).toMatchObject({ id: 'a1', layout: { __start: expect.any(Object), fetch: expect.any(Object) } }), { timeout: 2000 });
  });

  it('creates a Blank automation on its first save, with its layout, and opens it', async () => {
    const bridge = await design(
      {
        'automations.validate': ({ req }: Req) => (req.definition.steps.length ? valid() : { errors: [{ path: 'steps', message: 'Add at least one step' }], warnings: [], next_times: {} }),
        'automations.create': ({ req }: Req) => ({ automation: automationDetail({ id: 'a9', name: req.name!, title: 'Weekly note' }), warnings: [] }),
      },
      { draftName: 'weekly-note' },
    );
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

- [ ] **Step 2: Run it to verify it fails**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/design/design-view.spec.ts)`
Expected: FAIL, `Could not resolve "./design-view"`.

- [ ] **Step 3: Write `diff-view.ts`**

`apps/web-ui/src/app/automations/versions/diff-view.ts`:

```ts
import { ChangeDetectionStrategy, Component, input, ViewEncapsulation } from '@angular/core';
import { STEP_KIND_LABEL, type DefinitionDiff, type FieldChange } from '@desk/ui-core';

const TAG = { added: 'added', removed: 'removed', changed: 'changed' } as const;

/** A table of field changes: the field, its value before (red) and after (green). */
@Component({
  selector: 'table[deskDiffFields]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-diff-fields' },
  template: `
    <tbody>
      @for (f of fields(); track f.field) {
        <tr>
          <th class="mono">{{ f.field }}</th>
          <td class="auto-before">{{ f.before }}</td>
          <td class="auto-after">{{ f.after }}</td>
        </tr>
      }
    </tbody>
  `,
})
export class Fields {
  readonly fields = input.required<FieldChange[]>();
}

/** A structured diff between two definitions (spec §8.4). Everything is plain text. */
@Component({
  selector: 'div[deskDiffView]',
  imports: [Fields],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @if (diff().empty) {
      <p class="muted">No changes.</p>
    } @else {
      <div class="auto-diff">
        @if (labels(); as l) {
          <p class="muted small">{{ 'Red is ' + l.before + ', green is ' + l.after + '.' }}</p>
        }
        @if (diff().steps.length) {
          <section>
            <h3 class="auto-sub">Steps</h3>
            <ul class="auto-plain">
              @for (s of diff().steps; track s.id) {
                <li class="auto-diff-item">
                  <span class="auto-tag" [class]="tag[s.change]">{{ s.change }}</span>&ngsp;<b>{{ s.title }}</b>&ngsp;<span class="muted small">{{ kindLabel[s.kind] + ' · ' + s.id }}</span>
                  @if (s.fields.length) {
                    <table deskDiffFields [fields]="s.fields"></table>
                  }
                </li>
              }
            </ul>
          </section>
        }
        @if (diff().edges.added.length || diff().edges.removed.length) {
          <section>
            <h3 class="auto-sub">Edges</h3>
            <ul class="auto-plain">
              @for (e of diff().edges.removed; track e) {
                <li class="auto-diff-item"><span class="auto-tag removed">removed</span>&ngsp;<span>{{ e }}</span></li>
              }
              @for (e of diff().edges.added; track e) {
                <li class="auto-diff-item"><span class="auto-tag added">added</span>&ngsp;<span>{{ e }}</span></li>
              }
            </ul>
          </section>
        }
        @if (diff().inputs.length) {
          <section>
            <h3 class="auto-sub">Inputs</h3>
            <ul class="auto-plain">
              @for (i of diff().inputs; track i.key) {
                <li class="auto-diff-item">
                  <span class="auto-tag" [class]="tag[i.change]">{{ i.change }}</span>&ngsp;<b>{{ i.label }}</b>&ngsp;<span class="muted small mono">{{ i.key }}</span>
                  @if (i.fields.length) {
                    <table deskDiffFields [fields]="i.fields"></table>
                  }
                </li>
              }
            </ul>
          </section>
        }
        @if (diff().schedules; as sched) {
          <section>
            <h3 class="auto-sub">Schedules</h3>
            <p class="auto-before">{{ sched.before.join(' · ') || 'Run now only' }}</p>
            <p class="auto-after">{{ sched.after.join(' · ') || 'Run now only' }}</p>
          </section>
        }
        @if (diff().settings.length) {
          <section>
            <h3 class="auto-sub">Settings</h3>
            <table deskDiffFields [fields]="diff().settings"></table>
          </section>
        }
      </div>
    }
  `,
})
export class DiffView {
  readonly diff = input.required<DefinitionDiff>();
  readonly labels = input<{ before: string; after: string } | undefined>(undefined);
  protected readonly tag = TAG;
  protected readonly kindLabel = STEP_KIND_LABEL;
}
```

- [ ] **Step 4: Write `conflict-dialog.ts`**

`apps/web-ui/src/app/automations/design/conflict-dialog.ts`:

```ts
import { booleanAttribute, ChangeDetectionStrategy, Component, computed, input, output, signal, ViewEncapsulation } from '@angular/core';
import type { AutomationDefinition, AutomationDetail } from '@desk/protocol';
import { diffDefinitions } from '@desk/ui-core';
import { Button } from '../../components/button';
import { Sheet } from '../../components/sheet';
import { DiffView } from '../versions/diff-view';

/** A save refused because another version landed first: that version, and who saved it ("Desk", "You (CLI)"…). */
export type Conflict = { theirs: AutomationDetail; by: string };

/** Spec §8.2: "Desk saved v8 while you were editing", with Review changes, Save mine anyway and Discard mine. */
@Component({
  selector: 'div[deskConflictDialog]',
  imports: [Button, DiffView, Sheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskSheet [title]="title()" [width]="640" (close)="close.emit()">
      <p>{{ 'Saved by ' + conflict().by + '. Your edits started from an earlier version.' }}</p>
      <p class="muted small">{{ 'Save mine anyway makes your version v' + (v() + 1) + '; v' + v() + ' stays in Versions. Discard mine loads v' + v() + ' into the editor.' }}</p>
      @if (review()) {
        <section aria-label="What saving yours changes">
          <div deskDiffView [diff]="diff()" [labels]="labels()"></div>
        </section>
      }
      <div class="sheet-footer">
        <button deskButton (click)="review.set(!review())">{{ review() ? 'Hide changes' : 'Review changes' }}</button>
        <button deskButton (click)="discardMine.emit()">Discard mine</button>
        <button deskButton variant="primary" [pending]="saving()" (click)="saveMine.emit()">Save mine anyway</button>
      </div>
    </div>
  `,
})
export class ConflictDialog {
  readonly conflict = input.required<Conflict>();
  readonly mine = input.required<AutomationDefinition>();
  readonly saving = input(false, { transform: booleanAttribute });
  readonly saveMine = output<void>();
  readonly discardMine = output<void>();
  readonly close = output<void>();
  protected readonly review = signal(false);
  protected readonly v = computed(() => this.conflict().theirs.version);
  protected readonly title = computed(() => (this.conflict().by === 'Desk' ? `Desk saved v${this.v()} while you were editing` : `v${this.v()} was saved while you were editing`));
  protected readonly diff = computed(() => diffDefinitions(this.conflict().theirs.definition, this.mine()));
  protected readonly labels = computed(() => ({ before: `v${this.v()}`, after: 'yours' }));
}
```

- [ ] **Step 5: Write `design-view.ts`**

`apps/web-ui/src/app/automations/design/design-view.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, DestroyRef, effect, inject, input, linkedSignal, output, signal, untracked, ViewEncapsulation } from '@angular/core';
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
import { Button } from '../../components/button';
import { ToastService } from '../../components/toast';
import { DeskBridge, DeskCallError } from '../../core/desk-bridge';
import { RouteService } from '../../core/route.service';
import { ConflictDialog, type Conflict } from './conflict-dialog';
import { EdgeInspector } from './edge-inspector';
import { GraphCanvas } from './graph-canvas';
import { SettingsInspector } from './settings-inspector';
import { StartInspector } from './start-inspector';
import { StepInspector } from './step-inspector';

const KINDS: StepKind[] = ['script', 'agent', 'ask', 'wait', 'automation', 'tell_desk'];
const KIND_CLASS: Record<StepKind, string> = { script: 'k-script', agent: 'k-agent', ask: 'k-ask', wait: 'k-wait', automation: 'k-automation', tell_desk: 'k-tell' };
type Validation = { errors: ValidationIssue[]; warnings: ValidationIssue[]; next_times: Record<string, string[]> };
const NO_ISSUES: Validation = { errors: [], warnings: [], next_times: {} };

const docOf = (def: AutomationDefinition, layout: AutomationLayout): AutomationDoc => ({ def, layout: ensureLayout(def, layout) });

/**
 * Design (mockup 2): the canvas, the inspector, the add-step strip, validation, Save and conflicts. It edits a saved
 * automation (`detail`), or a Blank one before its first save (`draftName`). The screen remounts it for another automation.
 */
@Component({
  selector: 'div[deskDesignView]',
  imports: [Button, ConflictDialog, EdgeInspector, GraphCanvas, SettingsInspector, StartInspector, StepInspector],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-design' },
  template: `
    <div class="auto-design-main">
      <div class="auto-toolbar">
        <span class="auto-status" role="status" [class.bad]="problems() > 0">{{ status() }}</span>
        <span class="grow"></span>
        <button deskButton size="sm" variant="ghost" (click)="relayout({ def: doc().def, layout: autoLayout(doc().def) })">Tidy up</button>
        @if (detail() && dirty()) {
          <button deskButton size="sm" variant="ghost" (click)="discard()">Discard changes</button>
        }
        <input class="input auto-note" aria-label="Change note" placeholder="What changed (optional)" maxlength="2000" [value]="note()" (input)="note.set(val($event))" />
        <button deskButton variant="primary" size="sm" [pending]="saving()" [disabled]="!dirty() || !checked() || problems() > 0" [attr.title]="problems() ? 'Fix the problems first' : null" (click)="save()">Save</button>
      </div>
      <div
        deskGraphCanvas
        [def]="doc().def"
        [layout]="doc().layout"
        [startLabel]="startLabel()"
        [selection]="sel()"
        [issues]="issues()"
        [editable]="true"
        (pick)="selection.set($event)"
        (moved)="relayout(moveNodes(doc(), $event))"
        (connect)="onConnect($event.from, $event.to)"
        (remove)="remove($event.steps, $event.edges)"
      ></div>
      <div class="auto-strip" role="toolbar" aria-label="Add a step">
        <span class="muted small">Add a step</span>
        @for (k of kinds; track k) {
          <button type="button" class="auto-add" [class]="kindClass[k]" (click)="add(k)">{{ kindLabel[k] }}</button>
        }
      </div>
    </div>
    @switch (sel().kind) {
      @case ('start') {
        <aside deskStartInspector [doc]="doc()" [errors]="issues().start" [nextTimes]="validation().next_times" (docChange)="doc.set($event)"></aside>
      }
      @case ('step') {
        @for (id of stepKey(); track id) {
          <aside
            deskStepInspector
            [projectId]="projectId()"
            [doc]="doc()"
            [stepId]="id"
            [selfName]="name()"
            [sources]="sources()"
            [errors]="issues().steps[id] ?? noErrors"
            (docChange)="doc.set($event)"
            (renamed)="selection.set({ kind: 'step', id: $event })"
            (remove)="remove([id], [])"
          ></aside>
        }
      }
      @case ('edge') {
        <aside deskEdgeInspector [doc]="doc()" [index]="edgeKey()" [errors]="issues().edges[edgeKey()] ?? noErrors" (docChange)="doc.set($event)" (remove)="remove([], [edgeKey()])"></aside>
      }
      @default {
        <aside deskSettingsInspector [doc]="doc()" [issues]="issues().general" [warnings]="validation().warnings" (docChange)="doc.set($event)"></aside>
      }
    }
    @if (conflict(); as c) {
      <div deskConflictDialog [conflict]="c" [mine]="doc().def" [saving]="saving()" (saveMine)="save(c.theirs.version)" (discardMine)="discardMine()" (close)="conflict.set(null)"></div>
    }
  `,
})
export class DesignView {
  readonly projectId = input.required<string>();
  readonly sources = input.required<Array<{ id: string; label: string }>>();
  readonly detail = input<AutomationDetail | null>(null);
  readonly draftName = input<string | null>(null);
  /** React's onChange: a saved automation's new detail (a save, or theirs after Discard mine). */
  readonly detailChange = output<AutomationDetail>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly routes = inject(RouteService);
  protected readonly kinds = KINDS;
  protected readonly kindClass = KIND_CLASS;
  protected readonly kindLabel = STEP_KIND_LABEL;
  protected readonly autoLayout = autoLayout;
  protected readonly moveNodes = moveNodes;
  protected readonly noErrors: string[] = [];

  protected readonly name = computed(() => this.detail()?.name ?? this.draftName() ?? '');
  /** Read once, on first use (React's useState initializer): a later detail is adopted by the effect below, not here. */
  protected readonly doc = linkedSignal<AutomationDoc>(() =>
    untracked(() => {
      const d = this.detail();
      return d ? docOf(d.definition, d.layout) : docOf(blankDefinition(this.name()), {});
    }),
  );
  protected readonly saved = linkedSignal<{ def: AutomationDefinition; version: number } | null>(() =>
    untracked(() => {
      const d = this.detail();
      return d ? { def: d.definition, version: d.version } : null;
    }),
  );
  protected readonly selection = signal<GraphSelection>({ kind: 'none' });
  protected readonly validation = signal<Validation>(NO_ISSUES);
  protected readonly checked = signal(false);
  protected readonly note = signal('');
  protected readonly saving = signal(false);
  protected readonly conflict = signal<Conflict | null>(null);

  /** The definition alone: moving a node changes the layout, which neither validation nor dirtiness looks at. */
  private readonly def = computed(() => this.doc().def);
  protected readonly dirty = computed(() => {
    const s = this.saved();
    return !s || !diffDefinitions(s.def, this.def()).empty;
  });
  protected readonly issues = computed(() => mapIssues(this.def(), this.validation().errors));
  protected readonly problems = computed(() => this.validation().errors.length);
  protected readonly sel = computed((): GraphSelection => {
    const s = this.selection();
    const def = this.def();
    return (s.kind === 'step' && !def.steps.some((x) => x.id === s.id)) || (s.kind === 'edge' && !def.edges[s.index]) ? { kind: 'none' } : s;
  });
  protected readonly stepKey = computed(() => {
    const s = this.sel();
    return s.kind === 'step' ? [s.id] : [];
  });
  protected readonly edgeKey = computed(() => {
    const s = this.sel();
    return s.kind === 'edge' ? s.index : 0;
  });
  protected readonly status = computed(() => {
    const n = this.problems();
    if (!this.checked()) return 'Checking…';
    if (n) return `${n} problem${n === 1 ? '' : 's'}`;
    return this.dirty() ? 'Ready to save' : `Saved as v${this.saved()?.version ?? 1}`;
  });
  protected readonly startLabel = computed(() => {
    const triggers = this.def().triggers;
    return triggers.length ? `${whenText(triggers)} · or Run now` : 'Run now only';
  });

  private gen = 0;
  private layoutTimer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    // Validation, 400 ms after the last edit; an answer for an older draft is dropped.
    effect((onCleanup) => {
      const definition = this.def();
      const projectId = this.projectId();
      const name = this.name();
      const g = ++this.gen;
      this.checked.set(false);
      const t = setTimeout(() => {
        this.bridge
          .call('automations.validate', { projectId, req: { definition, name } })
          .then((r) => {
            if (g !== this.gen) return;
            this.validation.set(r);
            this.checked.set(true);
          })
          .catch(() => {});
      }, 400);
      onCleanup(() => clearTimeout(t));
    });

    // Another save landed (Desk, the CLI, a restore): adopt it unless the user has edits, which Save will meet as a 409.
    const version = computed(() => this.detail()?.version);
    effect(() => {
      const v = version();
      untracked(() => {
        const d = this.detail();
        const saved = this.saved();
        if (!d || v === undefined || !saved || v === saved.version || this.dirty()) return;
        this.saved.set({ def: d.definition, version: d.version });
        this.doc.update((x) => docOf(d.definition, x.layout));
      });
    });

    inject(DestroyRef).onDestroy(() => clearTimeout(this.layoutTimer));
  }

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  /** Positions save on their own, 800 ms after the last move. */
  private saveLayout(layout: AutomationLayout): void {
    const d = this.detail();
    if (!d) return;
    clearTimeout(this.layoutTimer);
    this.layoutTimer = setTimeout(() => void this.bridge.call('automations.layout', { id: d.id, layout }).catch((err: unknown) => this.toasts.error(err)), 800);
  }

  protected relayout(next: AutomationDoc): void {
    this.doc.set(next);
    this.saveLayout(next.layout);
  }

  protected add(kind: StepKind): void {
    const s = this.sel();
    const r = addStep(this.doc(), kind, s.kind === 'step' ? s.id : null);
    this.relayout(r.doc);
    this.selection.set({ kind: 'step', id: r.id });
  }

  protected remove(steps: string[], edges: number[]): void {
    this.doc.set(removeSelection(this.doc(), steps, edges));
    this.selection.set({ kind: 'none' });
  }

  protected onConnect(from: string, to: string): void {
    const r = connect(this.doc(), from, to);
    if ('error' in r) this.toasts.toast({ tone: 'error', message: r.error });
    else this.doc.set(r.doc);
  }

  protected discard(): void {
    const s = this.saved();
    if (!s) return;
    this.doc.set(docOf(s.def, this.doc().layout));
    this.selection.set({ kind: 'none' });
  }

  protected async save(base?: number): Promise<void> {
    const detail = this.detail();
    const sent = this.doc().def;
    const note = this.note().trim();
    const changeNote = note ? { change_note: note } : {};
    this.saving.set(true);
    try {
      if (!detail) {
        const r = await this.bridge.call('automations.create', { projectId: this.projectId(), req: { name: this.name(), definition: sent, via: 'editor', ...changeNote } });
        await this.bridge.call('automations.layout', { id: r.automation.id, layout: this.doc().layout });
        this.toasts.toast({ tone: 'info', message: `Saved ${r.automation.title}. It stays off until you turn it on.` });
        this.routes.navigate({ name: 'project', id: this.projectId(), tab: 'automations', automationId: r.automation.id, view: 'design' });
        return;
      }
      const r = await this.bridge.call('automations.save', { id: detail.id, req: { definition: sent, base_version: base ?? this.saved()!.version, via: 'editor', ...changeNote } });
      void this.bridge.call('automations.layout', { id: detail.id, layout: this.doc().layout }).catch(() => {});
      this.saved.set({ def: r.automation.definition, version: r.automation.version });
      this.doc.update((d) => (d.def === sent ? { ...d, def: r.automation.definition } : d));
      this.note.set('');
      this.conflict.set(null);
      this.detailChange.emit(r.automation);
      if (r.warnings.length) this.toasts.toast({ tone: 'info', message: `Saved v${r.automation.version} with ${r.warnings.length} warning${r.warnings.length === 1 ? '' : 's'}: see the automation's settings.` });
    } catch (err) {
      if (detail && err instanceof DeskCallError && err.status === 409) {
        try {
          const [theirs, versions] = await Promise.all([this.bridge.call('automations.get', { id: detail.id }), this.bridge.call('automations.versions', { id: detail.id })]);
          this.conflict.set({ theirs, by: versions[0] ? originText(versions[0]) : 'someone' });
        } catch (e) {
          this.toasts.error(e);
        }
      } else this.toasts.error(err);
    } finally {
      this.saving.set(false);
    }
  }

  protected discardMine(): void {
    const c = this.conflict();
    if (!c) return;
    this.saved.set({ def: c.theirs.definition, version: c.theirs.version });
    this.doc.set(docOf(c.theirs.definition, this.doc().layout));
    this.conflict.set(null);
    this.selection.set({ kind: 'none' });
    this.detailChange.emit(c.theirs);
  }
}
```

- [ ] **Step 6: Run it to verify it passes**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/design/design-view.spec.ts) && pnpm --filter @desk/web-ui typecheck`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-ui/src/app/automations/versions/diff-view.ts apps/web-ui/src/app/automations/design/conflict-dialog.ts apps/web-ui/src/app/automations/design/design-view.ts apps/web-ui/src/app/automations/design/design-view.spec.ts && git commit -m "feat(web): Design: the canvas with its inspectors, validation, Save, the layout, and conflicts; the structured diff

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 9: `VersionsView`

**Files:**
- Create: `apps/web-ui/src/app/automations/versions/versions-view.ts`
- Create: `apps/web-ui/src/app/automations/versions/versions.spec.ts`

**Interfaces:**
- Consumes: Task 8's `DiffView`; `dayTime`, `diffDefinitions`, `originText` (`@desk/ui-core`); `NowService`, `DeskBridge`, `ToastService`; `Button`, `ConfirmDialog`, `EmptyState`.
- Produces: `div[deskVersionsView]` (`VersionsView`). Input: `detail: AutomationDetail`. Output: `detailChange: AutomationDetail` (a restore). Used by Task 14.

- [ ] **Step 1: Write the failing spec (a port of `versions.test.tsx`)**

`apps/web-ui/src/app/automations/versions/versions.spec.ts`:

```ts
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { automationDetail, digestDef } from '@desk/ui-core/testing';
import { FakeDeskBridge } from '../../testing/fake-bridge';
import { VersionsView } from './versions-view';

const older = () => ({ ...digestDef(), steps: digestDef().steps.slice(0, 2), edges: [digestDef().edges[0]!] });

describe('VersionsView', () => {
  it('lists versions with who saved them, compares two, and restores one', async () => {
    const bridge = new FakeDeskBridge({
      'automations.versions': () => [
        { version: 7, origin: 'agent:d1', change_note: 'Adds a publish step', via: 'tool', created_at: '2026-09-27T09:00:00.000Z', tested: false },
        { version: 6, origin: 'user', change_note: '', via: 'editor', created_at: '2026-09-26T09:00:00.000Z', tested: true },
      ],
      'automations.version': ({ version }: { version: number }) => ({ version, definition: version === 7 ? digestDef() : older(), origin: 'user', change_note: '', via: 'editor', created_at: 't' }),
      'automations.restore': () => automationDetail({ version: 8 }),
    });
    const onChange = vi.fn();
    await render(VersionsView, { inputs: { detail: automationDetail() }, on: { detailChange: onChange }, providers: bridge.providers });
    const v7 = (await screen.findByText('v7', { selector: 'b' })).closest('li')!;
    expect(v7.textContent).toContain('Desk');
    expect(v7.textContent).toContain('Adds a publish step');
    expect(v7.textContent).toContain('current');
    const v6 = screen.getByText('v6', { selector: 'b' }).closest('li')!;
    expect(v6.textContent).toContain('You');
    expect(v6.textContent).toContain('✓ tested');
    expect((screen.getByLabelText('Compare') as HTMLSelectElement).value).toBe('6');
    expect((screen.getByLabelText('with') as HTMLSelectElement).value).toBe('7');
    const diff = await screen.findByRole('region', { name: 'Changes from v6 to v7' });
    // The region shows at once; its diff arrives once both versions have loaded.
    expect(await within(diff).findByText('Publish?')).toBeTruthy();
    expect(within(diff).getByText('Summarise → Publish?')).toBeTruthy();

    fireEvent.click(within(v6).getByRole('button', { name: 'Restore…' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Restore v6?' })).getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ version: 8 })));
    expect(bridge.calls.find((c) => c.channel === 'automations.restore')?.input).toEqual({ id: 'a1', version: 6 });
    expect(bridge.calls.filter((c) => c.channel === 'automations.version').map((c) => (c.input as { version: number }).version).sort()).toEqual([6, 7]);
  });
});
```

The last line is new. The port asks for each version once, where the React hook could ask again while a first answer was on its way.

- [ ] **Step 2: Run it to verify it fails**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/versions/versions.spec.ts)`
Expected: FAIL, `Could not resolve "./versions-view"`.

- [ ] **Step 3: Write `versions-view.ts`**

`apps/web-ui/src/app/automations/versions/versions-view.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, effect, inject, input, output, signal, ViewEncapsulation } from '@angular/core';
import type { AutomationDefinition, AutomationDetail, AutomationVersionInfo } from '@desk/protocol';
import { dayTime, diffDefinitions, originText } from '@desk/ui-core';
import { Button } from '../../components/button';
import { ConfirmDialog } from '../../components/confirm-dialog';
import { EmptyState } from '../../components/empty-state';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { NowService } from '../../core/now.service';
import { DiffView } from './diff-view';

/** Versions (spec §8.4): who saved each and when, its note and test, a diff between any two, and Restore. */
@Component({
  selector: 'div[deskVersionsView]',
  imports: [Button, ConfirmDialog, DiffView, EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @if (versions(); as list) {
      @if (list.length) {
        <div class="auto-versions">
          <ul class="auto-version-list">
            @for (v of list; track v.version) {
              <li class="auto-version">
                <div class="auto-version-head">
                  <b class="mono">{{ 'v' + v.version }}</b>
                  <span>{{ originText(v) }}</span>
                  <span class="muted small">{{ dayTime(v.created_at, now()) }}</span>
                  @if (v.tested) {
                    <span class="auto-tag added">✓ tested</span>
                  }
                  @if (v.version === detail().version) {
                    <span class="auto-tag">current</span>
                  }
                  <span class="grow"></span>
                  @if (v.version !== detail().version) {
                    <button deskButton size="sm" variant="ghost" (click)="restoring.set(v.version)">Restore…</button>
                  }
                </div>
                @if (v.change_note) {
                  <p class="auto-version-note">{{ v.change_note }}</p>
                }
              </li>
            }
          </ul>
          <section class="auto-version-diff" [attr.aria-label]="'Changes from v' + from() + ' to v' + to()">
            <div class="auto-inline">
              <label class="auto-inline small" for="versions-from"
                >Compare<select id="versions-from" class="select" (change)="from.set(num($event))">
                  @for (v of list; track v.version) {
                    <option [value]="'' + v.version" [selected]="v.version === from()">{{ 'v' + v.version }}</option>
                  }
                </select></label
              >
              <label class="auto-inline small" for="versions-to"
                >with<select id="versions-to" class="select" (change)="to.set(num($event))">
                  @for (v of list; track v.version) {
                    <option [value]="'' + v.version" [selected]="v.version === to()">{{ 'v' + v.version }}</option>
                  }
                </select></label
              >
            </div>
            @if (diff(); as d) {
              <div deskDiffView [diff]="d" [labels]="labels()"></div>
            } @else {
              <p class="muted">Loading…</p>
            }
          </section>
          @if (restoring() !== null) {
            <div deskConfirmDialog [title]="'Restore v' + restoring() + '?'" confirmLabel="Restore" (confirm)="restore(restoring() ?? 0)" (cancel)="restoring.set(null)">{{ restoreNote() }}</div>
          }
        </div>
      } @else {
        <div deskEmptyState title="No versions" body="Saving the automation creates its first version."></div>
      }
    } @else {
      <p class="muted auto-loading">Loading…</p>
    }
  `,
})
export class VersionsView {
  readonly detail = input.required<AutomationDetail>();
  /** React's onChange: the automation after a restore. */
  readonly detailChange = output<AutomationDetail>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly now = inject(NowService).now;
  protected readonly originText = originText;
  protected readonly dayTime = dayTime;
  protected readonly versions = signal<AutomationVersionInfo[] | null>(null);
  protected readonly from = signal(0);
  protected readonly to = signal(0);
  protected readonly restoring = signal<number | null>(null);
  /** Definitions by `<automation id>:<version>`, each asked for once. */
  private readonly defs = signal<Record<string, AutomationDefinition>>({});
  private readonly asked = new Set<string>();
  private readonly listKey = computed(() => `${this.detail().id}:${this.detail().version}`);
  protected readonly diff = computed(() => {
    const id = this.detail().id;
    const a = this.defs()[`${id}:${this.from()}`];
    const b = this.defs()[`${id}:${this.to()}`];
    return a && b ? diffDefinitions(a, b) : null;
  });
  protected readonly labels = computed(() => ({ before: `v${this.from()}`, after: `v${this.to()}` }));
  protected readonly restoreNote = computed(() => {
    const v = this.detail().version;
    return `Its definition becomes a new version, v${v + 1}. Nothing is lost: v${v} stays in the list.`;
  });
  private seq = 0;

  constructor() {
    // The list, again after each new version.
    effect(() => {
      this.listKey();
      const id = this.detail().id;
      const n = ++this.seq;
      this.bridge
        .call('automations.versions', { id })
        .then((list) => {
          if (n !== this.seq) return;
          this.versions.set(list);
          this.to.set(list[0]?.version ?? 0);
          this.from.set(list[1]?.version ?? list[0]?.version ?? 0);
        })
        .catch((err: unknown) => this.toasts.error(err));
    });
    // The two compared versions' definitions.
    effect(() => {
      const id = this.detail().id;
      for (const v of [this.from(), this.to()]) {
        const key = `${id}:${v}`;
        if (v <= 0 || this.asked.has(key)) continue;
        this.asked.add(key);
        this.bridge
          .call('automations.version', { id, version: v })
          .then((r) => this.defs.update((d) => ({ ...d, [key]: r.definition })))
          .catch((err: unknown) => {
            this.asked.delete(key);
            this.toasts.error(err);
          });
      }
    });
  }

  protected num(e: Event): number {
    return Number((e.target as HTMLSelectElement).value);
  }

  protected async restore(version: number): Promise<void> {
    this.restoring.set(null);
    try {
      const next = await this.bridge.call('automations.restore', { id: this.detail().id, version });
      this.toasts.toast({ tone: 'info', message: `Restored v${version} as v${next.version}.` });
      this.detailChange.emit(next);
    } catch (err) {
      this.toasts.error(err);
    }
  }
}
```

The `>Compare<select` and `></label\n>` line breaks keep the label's text tight against the select, as JSX renders it (`{label}<select>`), without a stray space.

- [ ] **Step 4: Run it to verify it passes**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/versions/versions.spec.ts) && pnpm --filter @desk/web-ui typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-ui/src/app/automations/versions/versions-view.ts apps/web-ui/src/app/automations/versions/versions.spec.ts && git commit -m "feat(web): Versions: who saved each, a diff between any two, and Restore

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: `NameDialog`, `RunDialog` and `TurnOnDialog`

**Files:**
- Create: `apps/web-ui/src/app/automations/dialogs/name-dialog.ts`
- Create: `apps/web-ui/src/app/automations/dialogs/run-dialog.ts`
- Create: `apps/web-ui/src/app/automations/dialogs/turn-on-dialog.ts`
- Create: `apps/web-ui/src/app/automations/dialogs/dialogs.spec.ts`

**Interfaces:**
- Consumes: `AutomationName` (`@desk/protocol`); `initialValues`, `runInputs`, `InputDraft`, `dayTime`, `describeGrant`, `describeSchedule`, `sameGrant`, `uniqueGrants` (`@desk/ui-core`); Task 2's `app.pickFile`; `DeskBridge`, `ToastService`, `NowService`; `Button`, `Field`, `Sheet`.
- Produces (used by Tasks 14 and 15):
  - `div[deskNameDialog]` (`NameDialog`). Inputs: `title`, `confirmLabel`, `initial = ''`, `taken: string[]`, `hint?`. Outputs: `close`, `confirm: string`.
  - `div[deskRunDialog]` (`RunDialog`), and `type RunTarget = { id: string; name: string; title: string; definition: AutomationDefinition }`. Inputs: `target: RunTarget`, `test = false`. Outputs: `close`, `started: string` (the run id).
  - `div[deskTurnOnDialog]` (`TurnOnDialog`). Input: `detail: AutomationDetail`. Outputs: `close`, `done: AutomationDetail`, `testFirst`.

- [ ] **Step 1: Write the failing spec (a port of `dialogs.test.tsx`)**

`apps/web-ui/src/app/automations/dialogs/dialogs.spec.ts`:

```ts
import { fireEvent, render, screen, waitFor } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import type { AutomationDefinition, Grant } from '@desk/protocol';
import { automationDetail, digestDef } from '@desk/ui-core/testing';
import { FakeDeskBridge } from '../../testing/fake-bridge';
import { NameDialog } from './name-dialog';
import { RunDialog } from './run-dialog';
import { TurnOnDialog } from './turn-on-dialog';

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
    const bridge = new FakeDeskBridge({ 'app.pickFile': () => '/Users/me/brief.pdf', 'automations.run': () => ({ run_id: 'r9' }) });
    const onStarted = vi.fn();
    await render(RunDialog, { inputs: { target: { id: 'a1', name: 'digest', title: 'Weekly digest', definition: withInputs() }, test: true }, on: { started: onStarted }, providers: bridge.providers });
    expect(await screen.findByRole('dialog', { name: 'Test Weekly digest' })).toBeTruthy();
    const start = screen.getByRole('button', { name: 'Start test' }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    fireEvent.input(screen.getByLabelText('Topic'), { target: { value: 'robots' } });
    fireEvent.input(screen.getByLabelText('How many (optional)'), { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Choose…' }));
    await waitFor(() => expect((screen.getByLabelText('Brief file (optional)') as HTMLInputElement).value).toBe('/Users/me/brief.pdf'));
    fireEvent.click(screen.getByLabelText('Go deep'));
    await waitFor(() => expect(start.disabled).toBe(false));
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
    const bridge = new FakeDeskBridge({
      'automations.validate': () => ({ errors: [], warnings: [], next_times: { '0': ['2026-10-05T06:00:00.000Z'] } }),
      'automations.setGrants': () => automationDetail(),
      'automations.setEnabled': () => automationDetail({ enabled: true }),
    });
    const onDone = vi.fn();
    const onTestFirst = vi.fn();
    const detail = automationDetail({ enabled: false, proposed_grants: proposed, enable_request: { note: 'Tested with robots.', proposed_grants: [proposed[0]!], at: 't' } });
    await render(TurnOnDialog, { inputs: { detail }, on: { done: onDone, testFirst: onTestFirst }, providers: bridge.providers });
    expect((await screen.findByRole('alert')).textContent).toBe("v7 hasn't been tested (v6 was).");
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
  it('explains a bad or taken name and confirms a good one', async () => {
    const onConfirm = vi.fn();
    await render(NameDialog, { inputs: { title: 'New automation', confirmLabel: 'Create', taken: ['digest'] }, on: { confirm: onConfirm } });
    const input = (await screen.findByLabelText('Name')) as HTMLInputElement;
    fireEvent.input(input, { target: { value: 'digest' } });
    expect(screen.getByRole('alert').textContent).toBe('Another automation already has this name.');
    fireEvent.input(input, { target: { value: '-bad' } });
    expect(screen.getByRole('alert').textContent).toMatch(/lowercase/i);
    fireEvent.input(input, { target: { value: 'Weekly Note' } });
    expect(input.value).toBe('weekly-note');
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(onConfirm).toHaveBeenCalledWith('weekly-note');
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/dialogs/dialogs.spec.ts)`
Expected: FAIL, `Could not resolve "./name-dialog"`.

- [ ] **Step 3: Write the dialogs**

`apps/web-ui/src/app/automations/dialogs/name-dialog.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, input, linkedSignal, output, untracked, ViewEncapsulation } from '@angular/core';
import { AutomationName } from '@desk/protocol';
import { Button } from '../../components/button';
import { Field } from '../../components/field';
import { Sheet } from '../../components/sheet';

/** Asks for an automation name (fixed at creation): Blank automation, and importing under another name. */
@Component({
  selector: 'div[deskNameDialog]',
  imports: [Button, Field, Sheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents', '[attr.title]': 'null' },
  template: `
    <div deskSheet [title]="title()" [width]="460" (close)="close.emit()">
      <form (submit)="submit($event)">
        <div deskField id="automation-name" label="Name" [hint]="hint() ?? defaultHint" [error]="error()">
          <input id="automation-name" class="input mono" [value]="name()" (input)="setName($event)" />
        </div>
      </form>
      <div class="sheet-footer">
        <button deskButton (click)="close.emit()">Cancel</button>
        <button deskButton variant="primary" [disabled]="!ok()" (click)="confirm.emit(name())">{{ confirmLabel() }}</button>
      </div>
    </div>
  `,
})
export class NameDialog {
  readonly title = input.required<string>();
  readonly confirmLabel = input.required<string>();
  readonly initial = input('');
  readonly taken = input.required<string[]>();
  readonly hint = input<string | undefined>(undefined);
  readonly close = output<void>();
  readonly confirm = output<string>();

  protected readonly defaultHint = 'Lowercase letters, digits and dashes, e.g. weekly-digest. The name cannot change later; the title can.';
  protected readonly name = linkedSignal(() => untracked(() => this.initial()));
  protected readonly error = computed(() => {
    const name = this.name();
    if (!name) return null;
    const parsed = AutomationName.safeParse(name);
    if (!parsed.success) return parsed.error.issues[0]?.message ?? 'Not a valid name';
    return this.taken().includes(name) ? 'Another automation already has this name.' : null;
  });
  protected readonly ok = computed(() => this.name() !== '' && this.error() === null);

  /** Lowercase, spaces as dashes, as it is typed (React's controlled input). */
  protected setName(e: Event): void {
    const el = e.target as HTMLInputElement;
    const v = el.value.toLowerCase().replace(/\s+/g, '-');
    if (el.value !== v) el.value = v;
    this.name.set(v);
  }

  protected submit(e: Event): void {
    e.preventDefault();
    if (this.ok()) this.confirm.emit(this.name());
  }
}
```

`apps/web-ui/src/app/automations/dialogs/run-dialog.ts`:

```ts
import { booleanAttribute, ChangeDetectionStrategy, Component, computed, inject, input, linkedSignal, output, signal, untracked, ViewEncapsulation } from '@angular/core';
import type { AutomationDefinition, InputSpec } from '@desk/protocol';
import { initialValues, runInputs, type InputDraft } from '@desk/ui-core';
import { Button } from '../../components/button';
import { Field } from '../../components/field';
import { Sheet } from '../../components/sheet';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';

/** What Run now and Test need to know (an AutomationDetail fits). */
export type RunTarget = { id: string; name: string; title: string; definition: AutomationDefinition };

/** One input's control, by its type: a box, a list, a checkbox, or a path with Choose…. */
@Component({
  selector: 'div[deskInputField]',
  imports: [Button, Field],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @switch (spec().type) {
      @case ('boolean') {
        <div class="field">
          <label class="auto-check"><input type="checkbox" [id]="fid()" [checked]="value() === true" (change)="valueChange.emit(checked($event))" /> {{ spec().label }}</label>
          @if (spec().description) {
            <p class="field-hint">{{ spec().description }}</p>
          }
        </div>
      }
      @case ('long_text') {
        <div deskField [id]="fid()" [label]="label()" [hint]="spec().description">
          <textarea class="textarea" rows="4" [id]="fid()" [value]="text()" (input)="valueChange.emit(val($event))"></textarea>
        </div>
      }
      @case ('choice') {
        <div deskField [id]="fid()" [label]="label()" [hint]="spec().description">
          <select class="select" [id]="fid()" (change)="valueChange.emit(val($event))">
            <option value="" [selected]="text() === ''">{{ spec().required ? 'Choose…' : 'None' }}</option>
            @for (opt of spec().options ?? []; track opt) {
              <option [value]="opt" [selected]="opt === text()">{{ opt }}</option>
            }
          </select>
        </div>
      }
      @case ('number') {
        <div deskField [id]="fid()" [label]="label()" [hint]="spec().description">
          <input class="input" type="number" [id]="fid()" [value]="text()" (input)="setNumber(val($event))" />
        </div>
      }
      @case ('file') {
        <div deskField [id]="fid()" [label]="label()" [hint]="spec().description ?? 'Desk copies the file into the run folder.'">
          <div class="auto-pick">
            <input class="input mono" placeholder="/path/to/file" [id]="fid()" [value]="text()" (input)="valueChange.emit(val($event))" />
            <button deskButton size="sm" (click)="pick('file')">Choose…</button>
          </div>
        </div>
      }
      @case ('folder') {
        <div deskField [id]="fid()" [label]="label()" [hint]="spec().description ?? 'Desk copies the folder into the run folder.'">
          <div class="auto-pick">
            <input class="input mono" placeholder="/path/to/folder" [id]="fid()" [value]="text()" (input)="valueChange.emit(val($event))" />
            <button deskButton size="sm" (click)="pick('folder')">Choose…</button>
          </div>
        </div>
      }
      @default {
        <div deskField [id]="fid()" [label]="label()" [hint]="spec().description">
          <input class="input" [id]="fid()" [type]="spec().type === 'url' ? 'url' : 'text'" [value]="text()" (input)="valueChange.emit(val($event))" />
        </div>
      }
    }
  `,
})
export class InputField {
  readonly spec = input.required<InputSpec>();
  readonly value = input.required<InputDraft>();
  /** React's onChange. */
  readonly valueChange = output<InputDraft>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly fid = computed(() => `run-input-${this.spec().key}`);
  protected readonly label = computed(() => (this.spec().required ? this.spec().label : `${this.spec().label} (optional)`));
  protected readonly text = computed(() => String(this.value()));

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  protected checked(e: Event): boolean {
    return (e.target as HTMLInputElement).checked;
  }

  protected setNumber(v: string): void {
    this.valueChange.emit(v === '' ? '' : Number(v));
  }

  protected async pick(kind: 'file' | 'folder'): Promise<void> {
    try {
      const path = kind === 'file' ? await this.bridge.call('app.pickFile', { purpose: 'automation-input' }) : await this.bridge.call('app.pickFolder', { purpose: 'automation-input' });
      if (path) this.valueChange.emit(path);
    } catch (err) {
      this.toasts.error(err);
    }
  }
}

/** Run now or Test (spec §8.4): a form generated from the automation's inputs. */
@Component({
  selector: 'div[deskRunDialog]',
  imports: [Button, InputField, Sheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskSheet [title]="(test() ? 'Test ' : 'Run ') + target().title" (close)="close.emit()">
      @if (test()) {
        <p class="muted small">{{ testNote() }}</p>
      }
      @for (i of inputs(); track i.key) {
        <div deskInputField [spec]="i" [value]="values()[i.key] ?? ''" (valueChange)="setValue(i.key, $event)"></div>
      } @empty {
        <p class="muted">This automation takes no inputs.</p>
      }
      <div class="sheet-footer">
        <button deskButton (click)="close.emit()">Cancel</button>
        <button deskButton variant="primary" [pending]="pending()" [disabled]="!ready()" (click)="start()">{{ test() ? 'Start test' : 'Run' }}</button>
      </div>
    </div>
  `,
})
export class RunDialog {
  readonly target = input.required<RunTarget>();
  readonly test = input(false, { transform: booleanAttribute });
  readonly close = output<void>();
  /** React's onStarted: the new run's id. */
  readonly started = output<string>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly inputs = computed(() => this.target().definition.inputs);
  /** The form's values, from the inputs' defaults once (a refreshed target keeps what was typed). */
  protected readonly values = linkedSignal(() => untracked(() => initialValues(this.inputs())));
  protected readonly ready = computed(() => runInputs(this.inputs(), this.values()));
  protected readonly pending = signal(false);
  protected readonly testNote = computed(
    () => `A test is a real run of the current version. Scripts see DESK_TEST=1 and can do a dry run, files publish under automations/${this.target().name}/tests/, and agent and Tell Desk steps act as usual.`,
  );

  protected setValue(key: string, v: InputDraft): void {
    this.values.update((s) => ({ ...s, [key]: v }));
  }

  protected async start(): Promise<void> {
    const ready = this.ready();
    if (!ready) return;
    this.pending.set(true);
    try {
      const { run_id } = await this.bridge.call('automations.run', { id: this.target().id, req: { inputs: ready, test: this.test() } });
      this.started.emit(run_id);
    } catch (err) {
      this.toasts.error(err);
      this.pending.set(false);
    }
  }
}
```

`apps/web-ui/src/app/automations/dialogs/turn-on-dialog.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, effect, inject, input, linkedSignal, output, signal, untracked, ViewEncapsulation } from '@angular/core';
import type { AutomationDetail } from '@desk/protocol';
import { dayTime, describeGrant, describeSchedule, sameGrant, uniqueGrants } from '@desk/ui-core';
import { Button } from '../../components/button';
import { Sheet } from '../../components/sheet';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { NowService } from '../../core/now.service';

/**
 * Turning an automation on (spec §5.4): its schedules with their next time, the grants its runs were approved for
 * (ticked), and a warning when the current version has no succeeded test. Grants are set first, then the switch.
 */
@Component({
  selector: 'div[deskTurnOnDialog]',
  imports: [Button, Sheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskSheet [title]="'Turn on ' + detail().title" [width]="560" (close)="close.emit()">
      @if (untested()) {
        <p class="auto-warn" role="alert">{{ warning() }}</p>
      }
      <h3 class="auto-sub">When it runs</h3>
      @if (detail().definition.triggers.length) {
        <ul class="auto-plain">
          @for (t of detail().definition.triggers; track $index; let i = $index) {
            <li>{{ describeSchedule(t.cron, t.timezone) }}@if (firstNext(i); as first) {<span class="muted">{{ ' · next ' + dayTime(first, now()) }}</span>}</li>
          }
        </ul>
      } @else {
        <p class="muted">It has no schedule, so turning it on changes nothing until you add one. Run now works either way.</p>
      }
      @if (detail().enable_request?.note; as note) {
        <blockquote class="auto-quote">{{ 'Desk: ' + note }}</blockquote>
      }
      <h3 class="auto-sub">Grants</h3>
      @if (detail().grants.length) {
        <ul class="auto-plain">
          @for (g of detail().grants; track $index) {
            <li>{{ describeGrant(g) }} <span class="muted">(kept)</span></li>
          }
        </ul>
      }
      @if (proposals().length) {
        <p class="muted small">Approved during its runs. Ticked ones let later runs go ahead without asking you.</p>
        @for (g of proposals(); track $index; let i = $index) {
          <label class="auto-check"><input type="checkbox" [checked]="ticked()[i] ?? false" (change)="tick(i, $event)" /> {{ describeGrant(g) }}</label>
        }
      } @else if (!detail().grants.length) {
        <p class="muted">No grants: anything its steps need approval for will ask you when it runs.</p>
      }
      <div class="sheet-footer">
        <button deskButton (click)="close.emit()">Cancel</button>
        @if (untested()) {
          <button deskButton (click)="testFirst.emit()">Test first</button>
        }
        <button deskButton variant="primary" [pending]="pending()" (click)="turnOn()">{{ untested() ? 'Turn on anyway' : 'Turn on' }}</button>
      </div>
    </div>
  `,
})
export class TurnOnDialog {
  readonly detail = input.required<AutomationDetail>();
  readonly close = output<void>();
  /** React's onDone: the automation, now on. */
  readonly done = output<AutomationDetail>();
  /** React's onTestFirst. */
  readonly testFirst = output<void>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly now = inject(NowService).now;
  protected readonly dayTime = dayTime;
  protected readonly describeGrant = describeGrant;
  protected readonly describeSchedule = describeSchedule;
  protected readonly proposals = computed(() => {
    const d = this.detail();
    return uniqueGrants([...(d.enable_request?.proposed_grants ?? []), ...d.proposed_grants]).filter((g) => !d.grants.some((x) => sameGrant(x, g)));
  });
  protected readonly ticked = linkedSignal(() => untracked(() => this.proposals().map(() => true)));
  protected readonly pending = signal(false);
  private readonly next = signal<Record<string, string[]>>({});
  protected readonly untested = computed(() => this.detail().tested_version !== this.detail().version);
  protected readonly warning = computed(() => {
    const d = this.detail();
    return `v${d.version} hasn't been tested${d.tested_version ? ` (v${d.tested_version} was)` : ''}.`;
  });
  /** What validate needs, compared by value, so a refreshed detail with the same definition does not ask again. */
  private readonly check = computed(
    () => {
      const d = this.detail();
      return { projectId: d.project_id, definition: d.definition, name: d.name };
    },
    { equal: (a, b) => a.projectId === b.projectId && a.definition === b.definition && a.name === b.name },
  );
  private seq = 0;

  constructor() {
    effect(() => {
      const c = this.check();
      const n = ++this.seq;
      this.bridge
        .call('automations.validate', { projectId: c.projectId, req: { definition: c.definition, name: c.name } })
        .then((r) => {
          if (n === this.seq) this.next.set(r.next_times);
        })
        .catch(() => {});
    });
  }

  protected firstNext(i: number): string | undefined {
    return this.next()[String(i)]?.[0];
  }

  protected tick(i: number, e: Event): void {
    const on = (e.target as HTMLInputElement).checked;
    this.ticked.update((t) => t.map((v, j) => (j === i ? on : v)));
  }

  protected async turnOn(): Promise<void> {
    const d = this.detail();
    this.pending.set(true);
    try {
      const grants = uniqueGrants([...d.grants, ...this.proposals().filter((_, i) => this.ticked()[i])]);
      await this.bridge.call('automations.setGrants', { id: d.id, grants, reason: 'enabled' });
      this.done.emit(await this.bridge.call('automations.setEnabled', { id: d.id, enabled: true }));
    } catch (err) {
      this.toasts.error(err);
      this.pending.set(false);
    }
  }
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/dialogs/dialogs.spec.ts) && pnpm --filter @desk/web-ui typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-ui/src/app/automations/dialogs/name-dialog.ts apps/web-ui/src/app/automations/dialogs/run-dialog.ts apps/web-ui/src/app/automations/dialogs/turn-on-dialog.ts apps/web-ui/src/app/automations/dialogs/dialogs.spec.ts && git commit -m "feat(web): the Name, Run now / Test and Turn-on dialogs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 11: `StepPanel`, `StepTranscript` and `RunFileList`

**Files:**
- Create: `apps/web-ui/src/app/automations/runs/run-file-list.ts`
- Create: `apps/web-ui/src/app/automations/runs/step-transcript.ts`
- Create: `apps/web-ui/src/app/automations/runs/step-panel.ts`
- Create: `apps/web-ui/src/app/automations/runs/step-panel.spec.ts`

**Interfaces:**
- Consumes: `agentActivity`, `agentTokens`, `askDeskText`, `canStopStep`, `clock`, `outputText`, `policyReason`, `relRunPath`, `STEP_KIND_LABEL`, `stepLook`, `tokens`, `narrate`, `sentCalls` (`@desk/ui-core`); `transcriptOf`, `SessionState` (`core/session.service.ts`); `describeArgs` (`attention/inspector.ts`); `Transcript`, `Depth`, `ComposerMode` (`threads/transcript.ts`); `Button`, `CodeBlock`, `ConfirmDialog`, `Field`, `FileViewer`, `SafeMarkdown`, `Sheet`; `DeskBridge`, `ToastService`, `RouteService`, `NowService`; Task 3's `sessionOf` in the spec.
- Produces:
  - `aside[deskStepPanel]` (`StepPanel`). Inputs: `projectId`, `s: SessionState`, `run: RunDetail`, `stepId`, `grantsSuspended: boolean`. Used by Task 12's `RunView`.
  - `section[deskAskAnswer]` (`AskAnswer`, inputs `run`, `row`) and `section[deskGateAnswer]` (`GateAnswer`, inputs `run`, `row`, `grantsSuspended`). Used by Task 15's Attention cards.
  - `div[deskRunFileList]` (`RunFileList`). Inputs: `runId`, `files: Array<{ path: string; label: string }>`.
  - `div[deskStepTranscript]` (`StepTranscript`). Inputs: `projectId`, `s`, `agentId`, `title`. Output: `close`.

- [ ] **Step 1: Write the failing spec (a port of `StepPanel.test.tsx`)**

`apps/web-ui/src/app/automations/runs/step-panel.spec.ts`:

```ts
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import type { ProjectState } from '@desk/client';
import { ev } from '@desk/client/testing';
import type { RunDetail } from '@desk/protocol';
import { runDetail, stepRun } from '@desk/ui-core/testing';
import type { SessionState } from '../../core/session.service';
import { FakeDeskBridge, type FakeHandlers } from '../../testing/fake-bridge';
import { sessionOf } from '../../testing/session';
import { StepPanel } from './step-panel';

const waitingAsk = () =>
  runDetail({
    status: 'waiting',
    steps: [
      ...runDetail().steps.slice(0, 1),
      stepRun('sum', { status: 'succeeded', summary: 'Robots everywhere', outputs: { headline: 'Robots' } }),
      stepRun('ok', { status: 'waiting', question: { text: 'Publish "Robots"?', files: ['/d/automation-runs/r14/steps/sum/digest.md'], approve_label: 'Publish' } }),
    ],
  });

async function panel(handlers: FakeHandlers, run: RunDetail, stepId: string, o: { s?: SessionState; grantsSuspended?: boolean } = {}) {
  const bridge = new FakeDeskBridge(handlers);
  const view = await render(StepPanel, { inputs: { projectId: 'p', s: o.s ?? sessionOf(), run, stepId, grantsSuspended: o.grantsSuspended ?? false }, providers: bridge.providers });
  return { bridge, view };
}

describe('StepPanel', () => {
  it('answers an Ask me step with a note, after previewing its file', async () => {
    const { bridge } = await panel({ 'automations.file': () => new TextEncoder().encode('# Digest\n\nRobots.'), 'automations.answer': () => ({ ok: true }), 'automations.files': () => [] }, waitingAsk(), 'ok');
    const answer = screen.getByRole('region', { name: 'Your answer' });
    expect(within(answer).getByText('Publish "Robots"?')).toBeTruthy();
    fireEvent.click(within(answer).getByRole('button', { name: 'digest.md' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.file')?.input).toEqual({ runId: 'r14', path: 'steps/sum/digest.md' }));
    fireEvent.input(within(answer).getByLabelText('Note (optional)'), { target: { value: 'ship it' } });
    fireEvent.click(within(answer).getByRole('button', { name: 'Publish' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.answer')?.input).toEqual({ runId: 'r14', stepId: 'ok', req: { decision: 'approve', note: 'ship it' } }));
  });

  it('offers remember on a script gate, unless grants are suspended', async () => {
    const gated = runDetail({ status: 'waiting', steps: [stepRun('fetch', { status: 'waiting', gate: { tool: 'skill_run', subject: 'digest/fetch.py robots', reason: 'ask: skill_run' } }), ...runDetail().steps.slice(1)] });
    const handlers: FakeHandlers = { 'automations.answer': () => ({ ok: true }), 'automations.files': () => [], 'automations.log': () => '' };
    const { bridge, view } = await panel(handlers, gated, 'fetch');
    expect(screen.getByText('digest/fetch.py robots')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Approve and remember' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.answer')?.input).toEqual({ runId: 'r14', stepId: 'fetch', req: { decision: 'approve', remember: true } }));
    await view.rerender({ inputs: { grantsSuspended: true }, partialUpdate: true });
    expect(screen.queryByRole('button', { name: 'Approve and remember' })).toBeNull();
    expect(screen.getByText(/grants are suspended/i)).toBeTruthy();
  });

  it('shows a running agent step: live output, tokens, its approvals (with remember), transcript and Stop step', async () => {
    const events = [
      ev(1, 'assistant.message', { run_id: 'x', content: 'Reading acme.md now.', tool_calls: [] }, { agent: 'ag1' }),
      ev(2, 'usage', { run_id: 'x', model: 'm', prompt_tokens: 1200, completion_tokens: 300, estimated: false }, { agent: 'ag1' }),
    ];
    const project = { approvals: [{ id: 'ap1', project_id: 'p', agent_id: 'ag1', run_id: 'x', tool_call_id: 't', tool: 'bash', arguments: '{"command":"ls"}', reason: 'ask', delegate_to_desk: false, status: 'pending', resolved_by: null, note: null, created_at: 't', resolved_at: null }] } as unknown as ProjectState;
    const { bridge } = await panel({ 'approvals.resolve': () => ({ ok: true }), 'automations.stopStep': () => ({ ok: true }), 'automations.files': () => [] }, runDetail(), 'sum', { s: sessionOf(events, { project }) });
    expect(screen.getByText('Reading acme.md now.')).toBeTruthy();
    expect(screen.getByText('1.5k tokens')).toBeTruthy();
    const approval = screen.getByRole('region', { name: 'Approval: bash' });
    expect(within(approval).getByText('ls')).toBeTruthy();
    fireEvent.click(within(approval).getByRole('button', { name: 'Approve and remember' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'approvals.resolve')?.input).toEqual({ id: 'ap1', decision: 'approved', remember: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Open transcript' }));
    expect(await screen.findByRole('dialog', { name: 'Summarise · transcript' })).toBeTruthy();
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Summarise · transcript' })).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Stop step…' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Stop this step?' })).getByRole('button', { name: 'Stop step' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.stopStep')?.input).toEqual({ runId: 'r14', stepId: 'sum' }));
  });

  it('shows a finished script step’s summary, route, outputs, log and files', async () => {
    const run = runDetail({ steps: [stepRun('fetch', { status: 'succeeded', route: 'changed', summary: '3 of 5 sites differ', outputs: { count: 3, sites: ['a.com', 'b.com'] }, started_at: '2026-09-28T06:00:00.000Z', finished_at: '2026-09-28T06:00:12.000Z' }), ...runDetail().steps.slice(1)] });
    await panel(
      {
        'automations.log': () => 'fetched 5 pages\n',
        'automations.files': () => [{ name: 'out.json', path: 'steps/fetch/out.json', type: 'file', size: 10 }],
        'automations.file': () => new TextEncoder().encode('{}'),
      },
      run,
      'fetch',
    );
    expect(screen.getByText('3 of 5 sites differ')).toBeTruthy();
    expect(screen.getByText('changed')).toBeTruthy();
    expect(screen.getByText('count').nextSibling?.textContent).toBe('3');
    expect(screen.getByText('sites').nextSibling?.textContent).toBe('a.com\nb.com');
    expect(await screen.findByText(/fetched 5 pages/)).toBeTruthy();
    expect(await screen.findByRole('button', { name: 'out.json' })).toBeTruthy();
  });

  it('asks Desk to fix a failed step', async () => {
    const run = runDetail({ status: 'failed', steps: [stepRun('fetch', { status: 'failed', error: 'exit 1\nTraceback' }), ...runDetail().steps.slice(1)] });
    const { bridge } = await panel({ 'projects.send': () => ({ ok: true }), 'automations.files': () => [], 'automations.log': () => '' }, run, 'fetch');
    expect(screen.getByText(/exit 1/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Ask Desk to fix' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.send')?.input).toEqual({ id: 'p', text: 'Please fix digest: run #14 failed at Fetch pages. exit 1' }));
  });
});
```

The React test unmounts and renders again for the suspended case. The port re-renders with `grantsSuspended: true`, the same assertion on the same panel.

- [ ] **Step 2: Run it to verify it fails**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/runs/step-panel.spec.ts)`
Expected: FAIL, `Could not resolve "./step-panel"`.

- [ ] **Step 3: Write `run-file-list.ts` and `step-transcript.ts`**

`apps/web-ui/src/app/automations/runs/run-file-list.ts`:

```ts
import { ChangeDetectionStrategy, Component, inject, input, signal, ViewEncapsulation } from '@angular/core';
import { Button } from '../../components/button';
import { FileViewer } from '../../components/file-viewer';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';

/** Files of a run folder as buttons; one opens in the app's file viewer below them. */
@Component({
  selector: 'div[deskRunFileList]',
  imports: [Button, FileViewer],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-files' },
  template: `
    <ul class="auto-plain">
      @for (f of files(); track f.path) {
        <li><button type="button" class="link mono" (click)="show(f.path)">{{ f.label }}</button></li>
      }
    </ul>
    @if (open(); as o) {
      <div deskFileViewer [path]="o.path" [data]="o.data"><button deskButton size="sm" variant="ghost" (click)="open.set(null)">Close</button></div>
    }
  `,
})
export class RunFileList {
  readonly runId = input.required<string>();
  readonly files = input.required<Array<{ path: string; label: string }>>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly open = signal<{ path: string; data: Uint8Array } | null>(null);

  protected async show(path: string): Promise<void> {
    try {
      this.open.set({ path, data: await this.bridge.call('automations.file', { runId: this.runId(), path }) });
    } catch (err) {
      this.toasts.error(err);
    }
  }
}
```

`apps/web-ui/src/app/automations/runs/step-transcript.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, input, output, signal, ViewEncapsulation } from '@angular/core';
import { narrate, sentCalls } from '@desk/ui-core';
import { Sheet } from '../../components/sheet';
import { transcriptOf, type SessionState } from '../../core/session.service';
import { Transcript, type ComposerMode, type Depth } from '../../threads/transcript';

/** A step agent's transcript (the Threads tab's view), which only the run view opens: step agents are not threads. */
@Component({
  selector: 'div[deskStepTranscript]',
  imports: [Sheet, Transcript],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents', '[attr.title]': 'null' },
  template: `
    <div deskSheet [title]="title() + ' · transcript'" [width]="760" (close)="close.emit()">
      <div class="auto-transcript">
        <aside
          deskTranscript
          [projectId]="projectId()"
          [threadId]="agentId()"
          [rows]="rows()"
          [entries]="transcript().entries"
          [reviewRounds]="0"
          [messages]="s().messages"
          [sent]="sent()"
          [selected]="selected()"
          [(depth)]="depth"
          [composer]="composer"
          (selectStop)="selected.set($event)"
        ></aside>
      </div>
    </div>
  `,
})
export class StepTranscript {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly agentId = input.required<string>();
  readonly title = input.required<string>();
  readonly close = output<void>();
  protected readonly transcript = computed(() => transcriptOf(this.s().events, this.s().streams[this.agentId()], this.projectId(), this.agentId()));
  protected readonly sent = computed(() => sentCalls(this.s().messages, this.agentId()));
  protected readonly rows = computed(() => narrate(this.transcript().entries, this.sent()));
  protected readonly selected = signal<number | null>(null);
  protected readonly depth = signal<Depth>('narrative');
  protected readonly composer: ComposerMode = { kind: 'off', hint: 'A step agent takes no messages. Answer its approvals here, or stop the step.' };
}
```

- [ ] **Step 4: Write `step-panel.ts`**

`apps/web-ui/src/app/automations/runs/step-panel.ts`:

```ts
import { booleanAttribute, ChangeDetectionStrategy, Component, computed, effect, inject, input, signal, ViewEncapsulation } from '@angular/core';
import type { ApprovalRow } from '@desk/client';
import type { RunDetail, Step, StepRunInfo, WorkspaceEntry } from '@desk/protocol';
import { agentActivity, agentTokens, askDeskText, canStopStep, clock, outputText, policyReason, relRunPath, STEP_KIND_LABEL, stepLook, tokens } from '@desk/ui-core';
import { describeArgs } from '../../attention/inspector';
import { Button } from '../../components/button';
import { CodeBlock } from '../../components/code-block';
import { ConfirmDialog } from '../../components/confirm-dialog';
import { Field } from '../../components/field';
import { SafeMarkdown } from '../../components/safe-markdown';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { NowService } from '../../core/now.service';
import { RouteService } from '../../core/route.service';
import { transcriptOf, type SessionState } from '../../core/session.service';
import { RunFileList } from './run-file-list';
import { StepTranscript } from './step-transcript';

const val = (e: Event): string => (e.target as HTMLInputElement).value;

/** An Ask me step waiting on the user: its question, the files it shows, a note, and its two buttons. */
@Component({
  selector: 'section[deskAskAnswer]',
  imports: [Button, Field, RunFileList, SafeMarkdown],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-card', 'aria-label': 'Your answer' },
  template: `
    <div deskSafeMarkdown [text]="question().text"></div>
    @if (files().length) {
      <div deskRunFileList [runId]="run().id" [files]="files()"></div>
    }
    <div deskField id="answer-note" label="Note (optional)">
      <textarea id="answer-note" class="textarea" rows="2" maxlength="2000" [value]="note()" (input)="note.set(val($event))"></textarea>
    </div>
    <div class="auto-inline">
      <button deskButton variant="primary" [pending]="busy() === 'approve'" [disabled]="busy() !== null" (click)="answer('approve')">{{ question().approve_label ?? 'Approve' }}</button>
      <button deskButton [pending]="busy() === 'reject'" [disabled]="busy() !== null" (click)="answer('reject')">{{ question().reject_label ?? 'Reject' }}</button>
    </div>
    <p class="muted small">Rejecting takes the route rejected. Later steps can read your note.</p>
  `,
})
export class AskAnswer {
  readonly run = input.required<RunDetail>();
  readonly row = input.required<StepRunInfo>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly val = val;
  protected readonly note = signal('');
  protected readonly busy = signal<string | null>(null);
  protected readonly question = computed(() => this.row().question ?? { text: '', files: [] });
  protected readonly files = computed(() =>
    this.question().files.flatMap((f) => {
      const path = relRunPath(f, this.run().id);
      return path ? [{ path, label: path.split('/').at(-1)! }] : [];
    }),
  );

  protected async answer(decision: 'approve' | 'reject'): Promise<void> {
    this.busy.set(decision);
    const note = this.note().trim();
    try {
      await this.bridge.call('automations.answer', { runId: this.run().id, stepId: this.row().step_id, req: { decision, ...(note ? { note } : {}) } });
    } catch (err) {
      this.toasts.error(err);
      this.busy.set(null);
    }
  }
}

/** A script step's approval: the command, why it asks, and Approve (and remember, unless grants are suspended) or Reject. */
@Component({
  selector: 'section[deskGateAnswer]',
  imports: [Button, Field],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-card', 'aria-label': 'Approval' },
  template: `
    <p>{{ 'Its script wants to run ' + gate().tool + ':' }}</p>
    <pre class="command"><span class="command-prompt">$ </span>{{ gate().subject }}</pre>
    <p class="muted small">{{ why().text }}</p>
    <div deskField id="gate-note" label="Note (optional)">
      <input id="gate-note" class="input" maxlength="2000" [value]="note()" (input)="note.set(val($event))" />
    </div>
    <div class="auto-inline">
      <button deskButton variant="primary" [pending]="busy() === 'approve'" [disabled]="busy() !== null" (click)="answer('approve')">Approve</button>
      @if (!grantsSuspended()) {
        <button deskButton [pending]="busy() === 'remember'" [disabled]="busy() !== null" (click)="answer('approve', true)">Approve and remember</button>
      }
      <button deskButton [pending]="busy() === 'reject'" [disabled]="busy() !== null" (click)="answer('reject')">Reject</button>
    </div>
    <p class="muted small">{{ grantsSuspended() ? 'Its grants are suspended until you keep them, so an approval is for this run only.' : 'Remember adds a grant, so later runs run this script without asking.' }}</p>
  `,
})
export class GateAnswer {
  readonly run = input.required<RunDetail>();
  readonly row = input.required<StepRunInfo>();
  readonly grantsSuspended = input(false, { transform: booleanAttribute });
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly val = val;
  protected readonly note = signal('');
  protected readonly busy = signal<string | null>(null);
  protected readonly gate = computed(() => this.row().gate ?? { tool: '', subject: '', reason: '' });
  protected readonly why = computed(() => policyReason(this.gate().reason));

  protected async answer(decision: 'approve' | 'reject', remember = false): Promise<void> {
    this.busy.set(remember ? 'remember' : decision);
    const note = this.note().trim();
    try {
      await this.bridge.call('automations.answer', { runId: this.run().id, stepId: this.row().step_id, req: { decision, ...(note ? { note } : {}), ...(remember ? { remember: true } : {}) } });
    } catch (err) {
      this.toasts.error(err);
      this.busy.set(null);
    }
  }
}

/** A step agent's pending approval, answered in place (remember adds a grant, unless grants are suspended). */
@Component({
  selector: 'section[deskAgentApproval]',
  imports: [Button, CodeBlock],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-card', '[attr.aria-label]': "'Approval: ' + approval().tool" },
  template: `
    @if (args().command; as command) {
      <pre class="command"><span class="command-prompt">$ </span>{{ command }}</pre>
    } @else {
      <div deskCodeBlock [code]="args().pretty" [language]="approval().tool"></div>
    }
    <div class="auto-inline">
      <button deskButton variant="primary" size="sm" [pending]="busy() === 'approved'" [disabled]="busy() !== null" (click)="resolve('approved')">Approve</button>
      @if (!grantsSuspended()) {
        <button deskButton size="sm" [pending]="busy() === 'remember'" [disabled]="busy() !== null" (click)="resolve('approved', true)">Approve and remember</button>
      }
      <button deskButton size="sm" [pending]="busy() === 'denied'" [disabled]="busy() !== null" (click)="resolve('denied')">Deny</button>
    </div>
  `,
})
export class AgentApproval {
  readonly approval = input.required<ApprovalRow>();
  readonly grantsSuspended = input(false, { transform: booleanAttribute });
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly busy = signal<string | null>(null);
  protected readonly args = computed(() => describeArgs(this.approval().tool, this.approval().arguments));

  protected async resolve(decision: 'approved' | 'denied', remember = false): Promise<void> {
    this.busy.set(remember ? 'remember' : decision);
    try {
      await this.bridge.call('approvals.resolve', { id: this.approval().id, decision, ...(remember ? { remember: true } : {}) });
    } catch (err) {
      this.toasts.error(err);
      this.busy.set(null);
    }
  }
}

/** A step's agent: its approvals, what it last said, its tokens, the transcript and Stop step. */
@Component({
  selector: 'div[deskAgentPart]',
  imports: [AgentApproval, Button, ConfirmDialog, SafeMarkdown, StepTranscript],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @for (a of approvals(); track a.id) {
      <section deskAgentApproval [approval]="a" [grantsSuspended]="grantsSuspended()"></section>
    }
    @if (going()) {
      <section aria-label="Live output" class="auto-live">
        @if (last(); as text) {
          <div deskSafeMarkdown [text]="text"></div>
        } @else {
          <p class="muted small">Starting…</p>
        }
      </section>
    }
    <p class="muted small">{{ usedText() }}</p>
    <div class="auto-inline">
      <button deskButton size="sm" (click)="open.set(true)">Open transcript</button>
      @if (canStop()) {
        <button deskButton size="sm" variant="danger" (click)="stopping.set(true)">Stop step…</button>
      }
    </div>
    @if (open()) {
      <div deskStepTranscript [projectId]="projectId()" [s]="s()" [agentId]="agentId()" [title]="step().title" (close)="open.set(false)"></div>
    }
    @if (stopping()) {
      <div deskConfirmDialog title="Stop this step?" confirmLabel="Stop step" [danger]="true" (confirm)="stop()" (cancel)="stopping.set(false)">Its agent stops and the step fails. The run then does what the step's "If it fails" says.</div>
    }
  `,
})
export class AgentPart {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly run = input.required<RunDetail>();
  readonly step = input.required<Step>();
  readonly row = input.required<StepRunInfo>();
  readonly agentId = input.required<string>();
  readonly grantsSuspended = input(false, { transform: booleanAttribute });
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly open = signal(false);
  protected readonly stopping = signal(false);
  private readonly transcript = computed(() => transcriptOf(this.s().events, this.s().streams[this.agentId()], this.projectId(), this.agentId()));
  protected readonly last = computed(() => {
    const entries = this.transcript().entries;
    for (let i = entries.length - 1; i >= 0; i--) {
      const e = entries[i]!;
      if (e.kind === 'assistant' && e.text.trim()) return e.text.trim();
    }
    return null;
  });
  protected readonly usedText = computed(() => `${tokens(agentTokens(this.s().events, this.agentId()))} tokens`);
  protected readonly approvals = computed(() => (this.s().project?.approvals ?? []).filter((a) => a.agent_id === this.agentId()));
  protected readonly going = computed(() => this.row().status === 'running' || this.row().status === 'waiting');
  protected readonly canStop = computed(() => canStopStep(this.run(), this.row()));

  protected async stop(): Promise<void> {
    this.stopping.set(false);
    try {
      await this.bridge.call('automations.stopStep', { runId: this.run().id, stepId: this.row().step_id });
    } catch (err) {
      this.toasts.error(err);
    }
  }
}

/** A failed step: its error, and Ask Desk to fix. */
@Component({
  selector: 'section[deskFailed]',
  imports: [Button],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-card', 'aria-label': 'Error' },
  template: `
    <pre class="auto-error">{{ error() }}</pre>
    <div><button deskButton variant="primary" size="sm" [pending]="sending()" (click)="ask()">Ask Desk to fix</button></div>
  `,
})
export class Failed {
  readonly projectId = input.required<string>();
  readonly run = input.required<RunDetail>();
  readonly step = input.required<Step>();
  readonly row = input.required<StepRunInfo>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly routes = inject(RouteService);
  protected readonly sending = signal(false);
  protected readonly error = computed(() => this.row().error ?? this.run().reason ?? 'It failed.');

  protected async ask(): Promise<void> {
    this.sending.set(true);
    const projectId = this.projectId();
    try {
      await this.bridge.call('projects.send', { id: projectId, text: askDeskText(this.run(), this.step().title, this.error()) });
      this.toasts.toast({ tone: 'info', message: 'Sent to Desk.', action: { label: 'Open conversation', run: () => this.routes.navigate({ name: 'project', id: projectId, tab: 'conversation' }) } });
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.sending.set(false);
    }
  }
}

/** What a finished step produced: its summary, route, your note and its outputs. */
@Component({
  selector: 'section[deskResults]',
  imports: [SafeMarkdown],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-results', 'aria-label': 'Results' },
  template: `
    @if (row().summary; as summary) {
      <div deskSafeMarkdown [text]="summary"></div>
    }
    <dl class="auto-facts">
      @if (row().route; as route) {
        <div><dt>route</dt><dd class="mono">{{ route }}</dd></div>
      }
      @if (row().note; as note) {
        <div><dt>your note</dt><dd>{{ note }}</dd></div>
      }
      @for (o of outputs(); track o.key) {
        <div><dt class="mono">{{ o.key }}</dt><dd class="auto-output">{{ o.text }}</dd></div>
      }
    </dl>
  `,
})
export class Results {
  readonly row = input.required<StepRunInfo>();
  protected readonly outputs = computed(() => Object.entries(this.row().outputs).map(([key, v]) => ({ key, text: outputText(v) })));
}

/** A script step's log: polled every 2 s while it runs, open then. Hidden until the first answer. */
@Component({
  selector: 'details[deskScriptLog]',
  imports: [CodeBlock],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-log', '[attr.open]': "live() ? '' : null", '[hidden]': 'log() === null' },
  template: `
    <summary>Log</summary>
    @if (log()) {
      <div deskCodeBlock [code]="tail()" language="log"></div>
    } @else {
      <p class="muted small">Nothing logged.</p>
    }
  `,
})
export class ScriptLog {
  readonly runId = input.required<string>();
  readonly stepId = input.required<string>();
  readonly live = input(false, { transform: booleanAttribute });
  private readonly bridge = inject(DeskBridge);
  protected readonly log = signal<string | null>(null);
  protected readonly tail = computed(() => (this.log() ?? '').split('\n').slice(-400).join('\n'));

  constructor() {
    effect((onCleanup) => {
      const runId = this.runId();
      const stepId = this.stepId();
      const live = this.live();
      let alive = true;
      const load = () =>
        this.bridge.call('automations.log', { runId, stepId }).then(
          (t) => {
            if (alive) this.log.set(t);
          },
          () => {
            if (alive) this.log.set('');
          },
        );
      void load();
      const timer = live ? setInterval(() => void load(), 2000) : undefined;
      onCleanup(() => {
        alive = false;
        clearInterval(timer);
      });
    });
  }
}

/** A step's folder: its files (opening in the viewer) and Open folder. */
@Component({
  selector: 'section[deskStepFiles]',
  imports: [Button, RunFileList],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-results', 'aria-label': 'Files' },
  template: `
    <div class="auto-inline">
      <h3 class="auto-sub">Files</h3>
      <span class="grow"></span>
      <button deskButton size="sm" variant="ghost" (click)="reveal()">Open folder</button>
    </div>
    @if (files().length) {
      <div deskRunFileList [runId]="runId()" [files]="files()"></div>
    } @else if (entries()) {
      <p class="muted small">No files.</p>
    }
  `,
})
export class StepFiles {
  readonly runId = input.required<string>();
  readonly stepId = input.required<string>();
  readonly status = input.required<string>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly entries = signal<WorkspaceEntry[] | null>(null);
  protected readonly files = computed(() => {
    const prefix = `steps/${this.stepId()}/`;
    return (this.entries() ?? []).filter((e) => e.type === 'file').map((f) => ({ path: f.path, label: f.path.replace(prefix, '') }));
  });

  constructor() {
    effect((onCleanup) => {
      const runId = this.runId();
      const stepId = this.stepId();
      this.status();
      let alive = true;
      this.bridge.call('automations.files', { runId, path: `steps/${stepId}` }).then(
        (list) => {
          if (alive) this.entries.set(list);
        },
        () => {
          if (alive) this.entries.set([]);
        },
      );
      onCleanup(() => {
        alive = false;
      });
    });
  }

  protected reveal(): void {
    void this.bridge.call('app.revealPath', { runId: this.runId(), stepId: this.stepId() }).catch((err: unknown) => this.toasts.error(err));
  }
}

/** The selected step of a run (spec §8.3): what it is doing, what it needs from you, or what it produced. */
@Component({
  selector: 'aside[deskStepPanel]',
  imports: [AgentPart, AskAnswer, Failed, GateAnswer, Results, ScriptLog, StepFiles],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-panel', '[attr.aria-label]': 'step()?.title ?? null' },
  template: `
    @if (step(); as step) {
      <p class="eyebrow">{{ eyebrow() }}</p>
      <h2>{{ step.title }}</h2>
      @if (row(); as row) {
        @if (row.attempt > 1) {
          <p class="muted small">{{ 'Attempt ' + row.attempt }}</p>
        }
        @if (row.status === 'waiting' && row.question) {
          <section deskAskAnswer [run]="run()" [row]="row"></section>
        }
        @if (row.status === 'waiting' && row.gate) {
          <section deskGateAnswer [run]="run()" [row]="row" [grantsSuspended]="grantsSuspended()"></section>
        }
        @if (row.agent_id; as agentId) {
          <div deskAgentPart [projectId]="projectId()" [s]="s()" [run]="run()" [step]="step" [row]="row" [agentId]="agentId" [grantsSuspended]="grantsSuspended()"></div>
        }
        @if (row.status === 'waiting' && row.resume_at) {
          <p>{{ 'Waits until ' + clock(row.resume_at) + '.' }}</p>
        }
        @if (row.child_run_id) {
          <p class="muted small">{{ 'Runs another automation (run ' + row.child_run_id + ').' }}</p>
        }
        @if (row.status === 'failed') {
          <section deskFailed [projectId]="projectId()" [run]="run()" [step]="step" [row]="row"></section>
        }
        @if (row.status === 'succeeded' || row.status === 'rejected') {
          <section deskResults [row]="row"></section>
        }
        @if (step.kind === 'script' && row.attempt > 0) {
          <details deskScriptLog [runId]="run().id" [stepId]="step.id" [live]="row.status === 'running'"></details>
        }
        @if (row.attempt > 0 && row.status !== 'pending') {
          <section deskStepFiles [runId]="run().id" [stepId]="step.id" [status]="row.status"></section>
        }
      }
      @if (!row() || row()?.attempt === 0) {
        <p class="muted">Not reached yet.</p>
      }
      @if (row()?.status === 'skipped') {
        <p class="muted">Skipped: none of the edges into it fired.</p>
      }
    }
  `,
})
export class StepPanel {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly run = input.required<RunDetail>();
  readonly stepId = input.required<string>();
  readonly grantsSuspended = input(false, { transform: booleanAttribute });
  private readonly now = inject(NowService).now;
  protected readonly clock = clock;
  protected readonly step = computed(() => this.run().definition.steps.find((x) => x.id === this.stepId()));
  protected readonly row = computed(() => this.run().steps.find((x) => x.step_id === this.stepId()));
  protected readonly eyebrow = computed(() => {
    const step = this.step();
    const row = this.row();
    const activity = row?.agent_id && row.status === 'running' ? agentActivity(this.s().events, row.agent_id) : null;
    return step ? `${STEP_KIND_LABEL[step.kind]} · ${stepLook(row, this.now(), activity).badge}` : '';
  });
}
```

- [ ] **Step 5: Run it to verify it passes**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/runs/step-panel.spec.ts) && pnpm --filter @desk/web-ui typecheck`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-ui/src/app/automations/runs/run-file-list.ts apps/web-ui/src/app/automations/runs/step-transcript.ts apps/web-ui/src/app/automations/runs/step-panel.ts apps/web-ui/src/app/automations/runs/step-panel.spec.ts && git commit -m "feat(web): the run step panel: answers, approvals, live output, transcript, results, log, files, fixes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 12: `RunsList` and `RunView`

**Files:**
- Create: `apps/web-ui/src/app/automations/runs/runs-list.ts`
- Create: `apps/web-ui/src/app/automations/runs/run-view.ts`
- Create: `apps/web-ui/src/app/automations/runs/runs.spec.ts`

**Interfaces:**
- Consumes: Task 3's `injectRuns`, `injectRun`; Task 4's `GraphCanvas`; Task 11's `StepPanel`; `agentActivity`, `dayTime`, `focusStep`, `href`, `runGraph`, `runStatusText`, `runTook`, `skippedText`, `triggerText`, `GraphSelection` (`@desk/ui-core`); `NowService`, `DeskBridge`, `ToastService`; `Button`, `ConfirmDialog`, `EmptyState`.
- Produces (used by Task 14):
  - `div[deskRunsList]` (`RunsList`). Inputs: `projectId`, `s`, `detail`.
  - `div[deskRunView]` (`RunView`). Inputs: `projectId`, `s`, `detail`, `runId`. The screen keys it by `runId`.
  - `aside[deskRunSide]` (`RunSide`). Input: `run`.

- [ ] **Step 1: Write the failing spec (a port of `runs.test.tsx`)**

`apps/web-ui/src/app/automations/runs/runs.spec.ts`:

```ts
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it } from 'vitest';
import { automationDetail, runDetail } from '@desk/ui-core/testing';
import { FakeDeskBridge } from '../../testing/fake-bridge';
import { sessionOf } from '../../testing/session';
import { RunsList } from './runs-list';
import { RunView } from './run-view';

beforeEach(() => void (window.location.hash = '#/p/p/automations/a1/runs'));

describe('RunsList', () => {
  it('lists runs with number, trigger, status and took, and skipped times as lines of their own', async () => {
    const bridge = new FakeDeskBridge({
      'automations.runs': () => [
        { kind: 'run', run: runDetail() },
        { kind: 'skipped', automation_id: 'a1', trigger_index: 0, due_at: '2026-09-27T06:00:00.000Z', reason: 'still_running', ts: '2026-09-27T06:00:00.000Z' },
        { kind: 'run', run: { ...runDetail({ id: 'r13', number: 13, trigger: 'test', test: true, version: 6, status: 'failed', at_step: 'Fetch pages', finished_at: '2026-09-27T05:10:00.000Z', started_at: '2026-09-27T05:00:00.000Z', summary: 'exit 1' }) } },
      ],
    });
    await render(RunsList, { inputs: { projectId: 'p', s: sessionOf(), detail: automationDetail() }, providers: bridge.providers });
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
    const bridge = new FakeDeskBridge({ 'automations.getRun': () => runDetail(), 'app.revealPath': () => ({ ok: true }), 'automations.cancelRun': () => ({ ok: true }), 'automations.files': () => [] });
    await render(RunView, { inputs: { projectId: 'p', s: sessionOf(), detail: automationDetail(), runId: 'r14' }, providers: bridge.providers });
    expect(await screen.findByRole('heading', { name: 'Run #14' })).toBeTruthy();
    expect(screen.getByText('running · Summarise')).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId('node-fetch').className).toContain('run-ok'));
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
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Cancel run #14?' })).getByRole('button', { name: 'Cancel run' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.cancelRun')?.input).toEqual({ runId: 'r14' }));
  });
});
```

The React test's bridge has no `automations.files`, and the React panel's failed call is swallowed. The port answers it, so the spec's log stays free of "unknown_channel". Nothing else changes.

- [ ] **Step 2: Run it to verify it fails**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/runs/runs.spec.ts)`
Expected: FAIL, `Could not resolve "./runs-list"`.

- [ ] **Step 3: Write `runs-list.ts` and `run-view.ts`**

`apps/web-ui/src/app/automations/runs/runs-list.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, inject, input, signal, ViewEncapsulation } from '@angular/core';
import type { AutomationDetail } from '@desk/protocol';
import { dayTime, href, runStatusText, runTook, skippedText, triggerText } from '@desk/ui-core';
import { Button } from '../../components/button';
import { EmptyState } from '../../components/empty-state';
import { NowService } from '../../core/now.service';
import type { SessionState } from '../../core/session.service';
import { injectRuns } from '../data';

/** The Runs tab (spec §8.3): newest first, with skipped schedule times as lines of their own. */
@Component({
  selector: 'div[deskRunsList]',
  imports: [Button, EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @if (live.status() === 'loading' && !live.value()) {
      <p class="muted auto-loading">Loading…</p>
    } @else if (!rows().length) {
      <div deskEmptyState title="No runs yet" body="Run now or Test starts one; schedules start them while it is on."></div>
    } @else {
      <div class="auto-runs">
        <table class="auto-table">
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
            @for (e of rows(); track e.key) {
              @if (e.run; as r) {
                <tr>
                  <td><a class="mono" [href]="r.href">{{ '#' + r.number }}</a></td>
                  <td>{{ r.started }}</td>
                  <td>{{ r.trigger }}</td>
                  <td><span class="auto-last" [class]="'tone-' + r.tone"><span class="dot" aria-hidden="true"></span>{{ r.status }}</span></td>
                  <td>{{ r.took }}</td>
                  <td class="auto-summary">{{ r.summary }}</td>
                </tr>
              } @else {
                <tr class="auto-skipped">
                  <td></td>
                  <td colspan="5" class="muted small">{{ e.skipped }}</td>
                </tr>
              }
            }
          </tbody>
        </table>
        @if (rows().length >= limit()) {
          <div><button deskButton size="sm" (click)="limit.set(limit() + 50)">Show older runs</button></div>
        }
      </div>
    }
  `,
})
export class RunsList {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly detail = input.required<AutomationDetail>();
  private readonly now = inject(NowService).now;
  protected readonly limit = signal(50);
  protected readonly live = injectRuns(() => this.s(), () => this.detail().id, () => this.limit());
  protected readonly rows = computed(() => {
    const now = this.now();
    return (this.live.value() ?? []).map((e) => {
      if (e.kind === 'skipped') return { key: `skip-${e.due_at}-${e.trigger_index}`, skipped: `${dayTime(e.due_at, now)} · ${skippedText(e)}`, run: null };
      const r = e.run;
      const st = runStatusText(r);
      return {
        key: r.id,
        skipped: null,
        run: {
          href: href({ name: 'project', id: this.projectId(), tab: 'automations', automationId: this.detail().id, view: 'runs', runId: r.id }),
          number: r.number,
          started: dayTime(r.started_at, now),
          trigger: triggerText(r),
          tone: st.tone,
          status: st.text,
          took: runTook(r, now),
          summary: r.summary ?? r.reason ?? '',
        },
      };
    });
  });
}
```

`apps/web-ui/src/app/automations/runs/run-view.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, inject, input, signal, ViewEncapsulation } from '@angular/core';
import type { AutomationDetail, RunDetail } from '@desk/protocol';
import { agentActivity, dayTime, focusStep, href, runGraph, runStatusText, runTook, triggerText, type GraphSelection } from '@desk/ui-core';
import { Button } from '../../components/button';
import { ConfirmDialog } from '../../components/confirm-dialog';
import { EmptyState } from '../../components/empty-state';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { NowService } from '../../core/now.service';
import type { SessionState } from '../../core/session.service';
import { injectRun } from '../data';
import { GraphCanvas } from '../design/graph-canvas';
import { StepPanel } from './step-panel';

/** The run itself: its inputs, summary or reason. */
@Component({
  selector: 'aside[deskRunSide]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-panel', '[attr.aria-label]': "'Run #' + run().number" },
  template: `
    <p class="eyebrow">Run</p>
    <h2>{{ run().automation_title }}</h2>
    @if (run().summary) {
      <p>{{ run().summary }}</p>
    }
    @if (run().reason) {
      <p class="auto-issues">{{ run().reason }}</p>
    }
    <h3 class="auto-sub">Inputs</h3>
    @if (inputs().length) {
      <dl class="auto-facts">
        @for (i of inputs(); track i.key) {
          <div><dt class="mono">{{ i.key }}</dt><dd>{{ i.value }}</dd></div>
        }
      </dl>
    } @else {
      <p class="muted small">None.</p>
    }
  `,
})
export class RunSide {
  readonly run = input.required<RunDetail>();
  protected readonly inputs = computed(() => Object.entries(this.run().inputs).map(([key, v]) => ({ key, value: String(v) })));
}

/** One run (spec §8.3, view A): the graph lit with each step's state, the header's Open folder and Cancel run, and a side panel. */
@Component({
  selector: 'div[deskRunView]',
  imports: [Button, ConfirmDialog, EmptyState, GraphCanvas, RunSide, StepPanel],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @if (live.status() === 'missing') {
      <div deskEmptyState title="This run is gone" body="Desk keeps each automation's last 20 runs or 30 days of runs."><a [href]="back()">All runs</a></div>
    } @else {
      @if (run(); as run) {
        <div class="auto-run">
          <div class="auto-run-main">
            <header class="auto-run-head">
              <a class="muted small" [href]="back()">Runs ›</a>
              <h2>{{ 'Run #' + run.number }}</h2>
              <span class="muted small">{{ meta() }}</span>
              <span class="auto-last" [class]="'tone-' + status().tone"><span class="dot" aria-hidden="true"></span>{{ status().text }}</span>
              @if (run.version !== detail().version) {
                <span class="auto-badge">{{ 'v' + run.version }}</span>
              }
              <span class="grow"></span>
              <button deskButton size="sm" (click)="reveal()">Open folder</button>
              @if (going()) {
                <button deskButton size="sm" variant="danger" (click)="cancelling.set(true)">Cancel run…</button>
              }
            </header>
            <div deskGraphCanvas [def]="run.definition" [layout]="detail().layout" [startLabel]="trigger()" [selection]="sel()" [run]="graph() ?? undefined" [editable]="false" (pick)="pick($event)"></div>
          </div>
          @for (id of panelKey(); track id) {
            <aside deskStepPanel [projectId]="projectId()" [s]="s()" [run]="run" [stepId]="id" [grantsSuspended]="detail().grants_suspended"></aside>
          } @empty {
            <aside deskRunSide [run]="run"></aside>
          }
          @if (cancelling()) {
            <div deskConfirmDialog [title]="'Cancel run #' + run.number + '?'" confirmLabel="Cancel run" [danger]="true" (confirm)="cancel()" (cancel)="cancelling.set(false)">Its running steps stop: agents are stopped and scripts are killed. Steps that finished keep their results.</div>
          }
        </div>
      } @else {
        <p class="muted auto-loading">Loading…</p>
      }
    }
  `,
})
export class RunView {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly detail = input.required<AutomationDetail>();
  readonly runId = input.required<string>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly now = inject(NowService).now;
  protected readonly live = injectRun(() => this.s(), () => this.runId());
  protected readonly run = computed(() => this.live.value());
  protected readonly picked = signal<GraphSelection | null>(null);
  protected readonly cancelling = signal(false);
  private readonly activity = computed(() => {
    const out: Record<string, string | null> = {};
    const events = this.s().events;
    for (const st of this.run()?.steps ?? []) if (st.agent_id && st.status === 'running') out[st.step_id] = agentActivity(events, st.agent_id);
    return out;
  });
  protected readonly graph = computed(() => {
    const run = this.run();
    return run ? runGraph(run, this.now(), this.activity()) : null;
  });
  protected readonly sel = computed((): GraphSelection => {
    const picked = this.picked();
    if (picked) return picked;
    const run = this.run();
    const focus = run ? focusStep(run) : null;
    return focus ? { kind: 'step', id: focus } : { kind: 'start' };
  });
  protected readonly panelKey = computed(() => {
    const s = this.sel();
    return s.kind === 'step' ? [s.id] : [];
  });
  protected readonly status = computed(() => {
    const run = this.run();
    return run ? runStatusText(run) : { text: '', tone: 'idle' as const };
  });
  protected readonly trigger = computed(() => {
    const run = this.run();
    return run ? triggerText(run) : '';
  });
  protected readonly meta = computed(() => {
    const run = this.run();
    return run ? `${triggerText(run)} · ${dayTime(run.started_at, this.now())} · ${runTook(run, this.now())}` : '';
  });
  protected readonly going = computed(() => {
    const status = this.run()?.status;
    return status === 'running' || status === 'waiting';
  });
  protected readonly back = computed(() => href({ name: 'project', id: this.projectId(), tab: 'automations', automationId: this.detail().id, view: 'runs' }));

  protected pick(x: GraphSelection): void {
    this.picked.set(x.kind === 'none' ? { kind: 'start' } : x);
  }

  protected reveal(): void {
    const run = this.run();
    if (run) void this.bridge.call('app.revealPath', { runId: run.id }).catch((err: unknown) => this.toasts.error(err));
  }

  protected async cancel(): Promise<void> {
    this.cancelling.set(false);
    const run = this.run();
    if (!run) return;
    try {
      await this.bridge.call('automations.cancelRun', { runId: run.id });
    } catch (err) {
      this.toasts.error(err);
    }
  }
}
```

`RunTone` (`automation-format.ts`) includes `'idle'`, so the placeholder before the run loads keeps `status` to one type. Aliases (`; as x`) appear only on a plain `@if`, never on `@else if`, as elsewhere in `apps/web-ui`.

- [ ] **Step 4: Run it to verify it passes**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/runs/runs.spec.ts) && pnpm --filter @desk/web-ui typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-ui/src/app/automations/runs/runs-list.ts apps/web-ui/src/app/automations/runs/run-view.ts apps/web-ui/src/app/automations/runs/runs.spec.ts && git commit -m "feat(web): the Runs tab and one run: the lit graph, Open folder, Cancel run and the side panel

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: `GrantsView`

**Files:**
- Create: `apps/web-ui/src/app/automations/grants/grants-view.ts`
- Create: `apps/web-ui/src/app/automations/grants/grants-view.spec.ts`

**Interfaces:**
- Consumes: Task 8's `DiffView`; `dayTime`, `describeGrant`, `diffDefinitions`, `GRANT_MATCH_LABEL`, `GRANT_TOOLS`, `GRANT_VALUE_LABEL`, `grantDraft`, `grantFromDraft`, `grantKey`, `grantOrigins`, `grantOriginText`, `href`, `originText`, `replaceGrant`, `widenedGrants`, `GrantDraft`, `GrantMatchKind` (`@desk/ui-core`); `NowService`, `DeskBridge`, `ToastService`; `Button`, `EmptyState`, `Field`.
- Produces: `div[deskGrantsView]` (`GrantsView`). Inputs: `projectId`, `s`, `detail`. Output: `detailChange`. Used by Task 14. Inside it: `form[deskGrantEditor]` (`GrantEditor`) and `section[deskSuspendedBanner]` (`SuspendedBanner`).

- [ ] **Step 1: Write the failing spec (a port of `GrantsView.test.tsx`)**

`apps/web-ui/src/app/automations/grants/grants-view.spec.ts`:

```ts
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import type { AutomationDetail, Grant } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { automationDetail, digestDef } from '@desk/ui-core/testing';
import { FakeDeskBridge, type FakeHandlers } from '../../testing/fake-bridge';
import { sessionOf } from '../../testing/session';
import { GrantsView } from './grants-view';

const fetch: Grant = { tool: 'web_fetch', match: { domain: 'news.bbc.co.uk' }, action: 'allow' };
const bash: Grant = { tool: 'bash', match: { command: '^ls$' }, action: 'allow' };
const events = [
  ev(1, 'automation.grants_set', { automation_id: 'a1', grants: [fetch], reason: 'enabled' }),
  ev(2, 'automation.grants_set', { automation_id: 'a1', grants: [fetch, bash], reason: 'remembered', source: { run_id: 'r1', step_id: 'sum' } }),
];
const setGrantsInput = (calls: Array<{ channel: string; input: unknown }>) => calls.find((c) => c.channel === 'automations.setGrants')?.input;

async function grants(detail: AutomationDetail, handlers: FakeHandlers = {}, onChange = vi.fn()) {
  const bridge = new FakeDeskBridge(handlers);
  const view = await render(GrantsView, { inputs: { projectId: 'p', s: sessionOf(events), detail }, on: { detailChange: onChange }, providers: bridge.providers });
  return { bridge, view };
}

describe('GrantsView', () => {
  it('lists grants with where each came from, and links a remembered one to its run', async () => {
    await grants(automationDetail({ grants: [fetch, bash] }));
    const web = screen.getByRole('listitem', { name: 'Allow web_fetch on news.bbc.co.uk' });
    expect(within(web).getByText(/^Set when you turned it on/)).toBeTruthy();
    const shell = screen.getByRole('listitem', { name: 'Allow bash matching ^ls$' });
    expect(within(shell).getByText(/^Remembered at Summarise/)).toBeTruthy();
    expect(within(shell).getByRole('link', { name: 'Open run' }).getAttribute('href')).toBe('#/p/p/automations/a1/runs/r1');
    expect(within(shell).queryByRole('button', { name: /Widen/ })).toBeNull();
  });

  it('widens a web grant, removes one and saves each change as edited', async () => {
    const onChange = vi.fn();
    const { bridge, view } = await grants(automationDetail({ grants: [fetch, bash] }), { 'automations.setGrants': (i: { grants: Grant[] }) => automationDetail({ grants: i.grants }) }, onChange);
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
    await view.rerender({ inputs: { detail: automationDetail({ grants: [fetch, bash] }) }, partialUpdate: true });
    fireEvent.click(within(screen.getByRole('listitem', { name: 'Allow bash matching ^ls$' })).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(setGrantsInput(bridge.calls)).toEqual({ id: 'a1', grants: [fetch], reason: 'edited' }));
  });

  it('adds a grant after checking it, and edits one', async () => {
    const { bridge } = await grants(automationDetail({ grants: [fetch] }), { 'automations.setGrants': (i: { grants: Grant[] }) => automationDetail({ grants: i.grants }) });
    fireEvent.click(screen.getByRole('button', { name: 'Add grant' }));
    const form = screen.getByRole('form', { name: 'New grant' });
    fireEvent.input(within(form).getByLabelText('Tool'), { target: { value: 'skill_run' } });
    fireEvent.change(within(form).getByLabelText('Matches'), { target: { value: 'command' } });
    fireEvent.input(within(form).getByLabelText('Command (a regular expression)'), { target: { value: '(' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save grant' }));
    expect(within(form).getByRole('alert').textContent).toBe('Not a valid regular expression.');
    expect(setGrantsInput(bridge.calls)).toBeUndefined();
    fireEvent.input(within(form).getByLabelText('Command (a regular expression)'), { target: { value: '^digest/fetch\\.py(\\s|$)' } });
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
    const { bridge } = await grants(
      automationDetail({ grants: [fetch], grants_suspended: true, grants_set_version: 6 }),
      {
        'automations.versions': () => [
          { version: 7, origin: 'agent:d1', via: 'tool', change_note: 'Asks you before publishing', created_at: '2026-09-27T10:00:00.000Z', tested: false },
          { version: 6, origin: 'user', via: 'editor', change_note: '', created_at: '2026-09-26T10:00:00.000Z', tested: true },
        ],
        'automations.version': () => ({ version: 6, definition: before, origin: 'user', change_note: '', via: 'editor', created_at: '2026-09-26T10:00:00.000Z' }),
        'automations.keepGrants': () => automationDetail({ grants: [fetch], grants_suspended: false, grants_set_version: 7 }),
      },
      onChange,
    );
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

- [ ] **Step 2: Run it to verify it fails**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/grants/grants-view.spec.ts)`
Expected: FAIL, `Could not resolve "./grants-view"`.

- [ ] **Step 3: Write `grants-view.ts`**

`apps/web-ui/src/app/automations/grants/grants-view.ts`:

```ts
import { booleanAttribute, ChangeDetectionStrategy, Component, computed, effect, inject, input, linkedSignal, output, signal, untracked, ViewEncapsulation } from '@angular/core';
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
import { Button } from '../../components/button';
import { EmptyState } from '../../components/empty-state';
import { Field } from '../../components/field';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { NowService } from '../../core/now.service';
import type { SessionState } from '../../core/session.service';
import { DiffView } from '../versions/diff-view';

const val = (e: Event): string => (e.target as HTMLInputElement).value;

/** A new grant, or an edit: action, tool, what it matches, checked by grantFromDraft before it saves. */
@Component({
  selector: 'form[deskGrantEditor]',
  imports: [Button, Field],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-grant-form', '[attr.aria-label]': "initial() ? 'Edit grant' : 'New grant'", '(submit)': 'submit($event)' },
  template: `
    <div class="auto-inline">
      <div deskField id="grant-action" label="Action">
        <select id="grant-action" class="select" (change)="setAction(val($event))">
          <option value="allow" [selected]="draft().action === 'allow'">Allow</option>
          <option value="deny" [selected]="draft().action === 'deny'">Deny</option>
        </select>
      </div>
      <div deskField id="grant-tool" label="Tool">
        <input id="grant-tool" class="input mono" list="grant-tools" maxlength="60" [value]="draft().tool" (input)="set({ tool: val($event) })" />
      </div>
      <datalist id="grant-tools">
        @for (t of tools; track t) {
          <option [value]="t"></option>
        }
      </datalist>
      <div deskField id="grant-kind" label="Matches">
        <select id="grant-kind" class="select" (change)="setKind(val($event))">
          @for (k of kinds; track k.value) {
            <option [value]="k.value" [selected]="k.value === draft().kind">{{ k.label }}</option>
          }
        </select>
      </div>
    </div>
    @if (draft().kind !== 'any') {
      <div deskField id="grant-value" [label]="valueLabel()">
        <input id="grant-value" class="input mono" [value]="draft().value" (input)="set({ value: val($event) })" />
      </div>
    }
    @if (problem()) {
      <p class="field-error" role="alert">{{ problem() }}</p>
    }
    <div class="auto-inline">
      <button deskButton type="submit" variant="primary" [pending]="busy()">Save grant</button>
      <button deskButton (click)="cancel.emit()">Cancel</button>
    </div>
  `,
})
export class GrantEditor {
  readonly initial = input<Grant | undefined>(undefined);
  readonly busy = input(false, { transform: booleanAttribute });
  /** React's onSave: the checked grant. */
  readonly save = output<Grant>();
  readonly cancel = output<void>();
  protected readonly val = val;
  protected readonly tools = GRANT_TOOLS;
  protected readonly kinds = (Object.keys(GRANT_MATCH_LABEL) as GrantMatchKind[]).map((value) => ({ value, label: GRANT_MATCH_LABEL[value] }));
  protected readonly draft = linkedSignal<GrantDraft>(() => untracked(() => grantDraft(this.initial())));
  protected readonly problem = signal<string | null>(null);
  protected readonly valueLabel = computed(() => {
    const kind = this.draft().kind;
    return kind === 'any' ? '' : GRANT_VALUE_LABEL[kind];
  });

  protected set(patch: Partial<GrantDraft>): void {
    this.draft.update((x) => ({ ...x, ...patch }));
    this.problem.set(null);
  }

  protected setAction(v: string): void {
    this.set({ action: v as Grant['action'] });
  }

  protected setKind(v: string): void {
    this.set({ kind: v as GrantMatchKind });
  }

  protected submit(e: Event): void {
    e.preventDefault();
    const r = grantFromDraft(this.draft());
    if ('problem' in r) this.problem.set(r.problem);
    else this.save.emit(r.grant);
  }
}

/** Grants are suspended: the versions saved since they were set, what changed, and Keep grants. */
@Component({
  selector: 'section[deskSuspendedBanner]',
  imports: [Button, DiffView],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-banner', 'aria-label': 'Grants suspended' },
  template: `
    <p><b>Grants are suspended.</b>{{ text() }}</p>
    @if (saved().length) {
      <ul class="auto-plain">
        @for (v of saved(); track v.version) {
          <li>
            <b>{{ 'v' + v.version }}</b>&ngsp;<span class="muted small">{{ originText(v) + ' · ' + dayTime(v.created_at, now()) }}</span>
            @if (v.change_note) {
              <p>{{ v.change_note }}</p>
            }
          </li>
        }
      </ul>
    }
    @if (since() !== null) {
      <section [attr.aria-label]="'What changed since v' + since()">
        @if (diff(); as d) {
          <div deskDiffView [diff]="d" [labels]="labels()"></div>
        } @else {
          <p class="muted">Loading…</p>
        }
      </section>
    }
    <div><button deskButton variant="primary" [pending]="keeping()" (click)="keep()">Keep grants</button></div>
  `,
})
export class SuspendedBanner {
  readonly detail = input.required<AutomationDetail>();
  /** React's onChange: the automation with its grants kept. */
  readonly detailChange = output<AutomationDetail>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly now = inject(NowService).now;
  protected readonly originText = originText;
  protected readonly dayTime = dayTime;
  protected readonly versions = signal<AutomationVersionInfo[] | null>(null);
  protected readonly before = signal<AutomationDefinition | null>(null);
  protected readonly keeping = signal(false);
  protected readonly since = computed(() => this.detail().grants_set_version);
  protected readonly saved = computed(() => {
    const since = this.since();
    return (this.versions() ?? []).filter((v) => since === null || v.version > since);
  });
  protected readonly diff = computed(() => {
    const before = this.before();
    return before ? diffDefinitions(before, this.detail().definition) : null;
  });
  protected readonly labels = computed(() => ({ before: `v${this.since()}`, after: `v${this.detail().version}` }));
  protected readonly text = computed(() => {
    const d = this.detail();
    const since = this.since();
    return ` v${d.version} was saved after they were set${since !== null ? ` in v${since}` : ''}, so its runs ask you for everything and approvals offer no remember until you keep them. Editing a grant keeps them too.`;
  });
  private readonly key = computed(() => `${this.detail().id}:${this.detail().version}:${this.since()}`);
  private seq = 0;

  constructor() {
    effect(() => {
      this.key();
      const id = this.detail().id;
      const since = this.since();
      const n = ++this.seq;
      this.bridge
        .call('automations.versions', { id })
        .then((list) => {
          if (n === this.seq) this.versions.set(list);
        })
        .catch((err: unknown) => this.toasts.error(err));
      if (since !== null) {
        this.bridge
          .call('automations.version', { id, version: since })
          .then((v) => {
            if (n === this.seq) this.before.set(v.definition);
          })
          .catch((err: unknown) => this.toasts.error(err));
      }
    });
  }

  protected async keep(): Promise<void> {
    this.keeping.set(true);
    const d = this.detail();
    try {
      this.detailChange.emit(await this.bridge.call('automations.keepGrants', { id: d.id }));
      this.toasts.toast({ tone: 'info', message: `Grants kept for v${d.version}.` });
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.keeping.set(false);
    }
  }
}

/** Grants (spec §8.4): what this automation's runs may do without asking, where each rule came from, and the suspension banner. */
@Component({
  selector: 'div[deskGrantsView]',
  imports: [Button, EmptyState, GrantEditor, SuspendedBanner],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-grants' },
  template: `
    @if (detail().grants_suspended) {
      <section deskSuspendedBanner [detail]="detail()" (detailChange)="detailChange.emit($event)"></section>
    }
    <p class="muted small">Grants go before the project's policy, for this automation's scripts and step agents only.</p>
    @if (detail().grants.length === 0 && editing() === null) {
      <div deskEmptyState title="No grants" body="Its runs ask you for whatever the project's policy does not allow. Approve and remember adds a grant."></div>
    }
    <ul class="auto-grant-list">
      @for (g of rows(); track g.key; let i = $index) {
        <li class="auto-grant" [attr.aria-label]="g.text">
          @if (editing() === i) {
            <form deskGrantEditor [initial]="g.grant" [busy]="busy()" (save)="save(replace(i, [$event]))" (cancel)="editing.set(null)"></form>
          } @else {
            <div class="grow">
              <p class="auto-grant-rule" [class]="g.grant.action">{{ g.text }}</p>
              <p class="muted small">{{ g.origin }}@if (g.runHref; as runHref) { · <a class="link" [href]="runHref">Open run</a>}</p>
            </div>
            @if (g.wide; as wide) {
              <button deskButton size="sm" variant="ghost" [disabled]="busy()" [attr.title]="wide.title" (click)="save(replace(i, wide.grants))">{{ wide.label }}</button>
            }
            <button deskButton size="sm" variant="ghost" [disabled]="busy()" (click)="editing.set(i)">Edit</button>
            <button deskButton size="sm" variant="ghost" [disabled]="busy()" (click)="save(replace(i, []))">Remove</button>
          }
        </li>
      }
    </ul>
    @if (editing() === detail().grants.length) {
      <form deskGrantEditor [busy]="busy()" (save)="save(replace(detail().grants.length, [$event]))" (cancel)="editing.set(null)"></form>
    } @else {
      <div><button deskButton [disabled]="busy()" (click)="editing.set(detail().grants.length)">Add grant</button></div>
    }
  `,
})
export class GrantsView {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly detail = input.required<AutomationDetail>();
  /** React's onChange. */
  readonly detailChange = output<AutomationDetail>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly now = inject(NowService).now;
  /** The row being edited; `detail().grants.length` is a new one. */
  protected readonly editing = signal<number | null>(null);
  protected readonly busy = signal(false);
  private readonly origins = computed(() => grantOrigins(this.s().events, this.detail().id));
  protected readonly rows = computed(() => {
    const d = this.detail();
    const now = this.now();
    return d.grants.map((grant) => {
      const origin = this.origins().get(grantKey(grant));
      const wide = widenedGrants(grant);
      const domain = wide?.[0]?.match?.domain;
      return {
        key: grantKey(grant),
        grant,
        text: describeGrant(grant),
        origin: grantOriginText(origin, d.definition, now),
        runHref: origin?.kind === 'remembered' ? href({ name: 'project', id: this.projectId(), tab: 'automations', automationId: d.id, view: 'runs', runId: origin.run_id }) : null,
        wide: wide ? { grants: wide, label: `Widen to ${domain}`, title: `Allow ${domain} and every subdomain of it, not only ${grant.match?.domain}` } : null,
      };
    });
  });

  protected replace(i: number, next: Grant[]): Grant[] {
    return replaceGrant(this.detail().grants, i, next);
  }

  protected async save(grants: Grant[]): Promise<void> {
    this.busy.set(true);
    try {
      this.detailChange.emit(await this.bridge.call('automations.setGrants', { id: this.detail().id, grants, reason: 'edited' }));
      this.editing.set(null);
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.busy.set(false);
    }
  }
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/grants/grants-view.spec.ts) && pnpm --filter @desk/web-ui typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-ui/src/app/automations/grants/grants-view.ts apps/web-ui/src/app/automations/grants/grants-view.spec.ts && git commit -m "feat(web): the Grants tab: origins, edit, widen, and the suspended banner with Keep grants

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 14: The Automations screen, its list and header, `draft.ts`, and the route

**Files:**
- Create: `apps/web-ui/src/app/conversation/draft.ts`
- Modify: `apps/web-ui/src/app/conversation/conversation-screen.ts:41` (import `draftKey` instead of defining it)
- Create: `apps/web-ui/src/app/automations/automation-list.ts`
- Create: `apps/web-ui/src/app/automations/automation-header.ts`
- Create: `apps/web-ui/src/app/automations/automations-screen.ts`
- Create: `apps/web-ui/src/app/automations/automations-screen.spec.ts`
- Modify: `apps/web-ui/src/app/screen-for.ts:4,52`, `apps/web-ui/src/app/screen-for.spec.ts:6,33`
- Delete: `apps/web-ui/src/app/automations/automations-placeholder.ts`

**Interfaces:**
- Consumes:
  - Task 3's `injectAutomationList` and `injectAutomation`.
  - Task 8's `DesignView`, Task 9's `VersionsView`, Task 10's `NameDialog`, `RunDialog` and `TurnOnDialog`, Task 12's `RunsList` and `RunView`, Task 13's `GrantsView`.
  - From `@desk/ui-core`: `dayTime`, `href`, `lastRunText`, `listNote`, `versionBadge`, `whenText`, `AutomationView`, `Route`.
  - `AutomationExport` (`@desk/protocol`: a zod schema and its type), `DeskCallError`, `RouteService`, `NowService`, `ToastService`, `injectSession`.
- Produces:
  - `draftKey(projectId: string): string` and `primeDraft(projectId: string, text: string): void` (`conversation/draft.ts`). Task 15 does not use them; the conversation screen reads the key.
  - `div[deskAutomationsScreen]` (`AutomationsScreen`). Inputs: `projectId` (required), and `automationId`, `view`, `runId`, `draft` (each optional). `screenFor` passes all five.

- [ ] **Step 1: Write the failing spec (a port of `AutomationsScreen.test.tsx`)**

`apps/web-ui/src/app/automations/automations-screen.spec.ts`:

```ts
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { ProjectOverview } from '@desk/client';
import type { AutomationSummary, StoredEvent } from '@desk/protocol';
import { automationDetail, automationSummary } from '@desk/ui-core/testing';
import { SESSION_RELEASE_DELAY } from '../core/session.service';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../testing/fake-bridge';
import { AutomationsScreen } from './automations-screen';

beforeEach(() => {
  localStorage.clear();
  window.location.hash = '#/p/p/automations';
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

async function setup(handlers: FakeHandlers, inputs: Record<string, unknown> = { projectId: 'p' }, events: StoredEvent[] = []) {
  const bridge: FakeDeskBridge = new FakeDeskBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    ...handlers,
  });
  await render(AutomationsScreen, {
    inputs,
    providers: [...bridge.providers, provideGlobal({ ...initialGlobalState(), connection: { status: 'live' } }), { provide: SESSION_RELEASE_DELAY, useValue: 0 }],
  });
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
    await setup({ 'automations.list': () => LIST });
    const deps = (await screen.findByRole('link', { name: 'Nightly dependency check' })).closest('tr')!;
    expect(deps.textContent).toContain('waiting on you: Ask me: Open the PR?');
    expect(deps.textContent).toContain('Daily 02:00');
    const digest = screen.getByRole('link', { name: 'Weekly digest' }).closest('tr')!;
    expect(digest.textContent).toContain('v7 · untested changes');
    expect(digest.textContent).toContain('succeeded');
    const invoices = screen.getByRole('link', { name: 'Invoice intake' }).closest('tr')!;
    expect(invoices.textContent).toContain('v2 · run now only');
    expect(within(invoices).getByRole('switch').getAttribute('aria-checked')).toBe('false');
    expect(invoices.textContent).toContain('n/a');
    expect(screen.getByRole('link', { name: 'Weekly digest' }).getAttribute('href')).toBe('#/p/p/automations/a1');
  });

  it('turns an automation off at once, and on through the Turn-on dialog', async () => {
    const bridge = await setup({
      'automations.list': () => LIST,
      'automations.setEnabled': () => automationDetail({ enabled: false }),
      'automations.get': () => automationDetail({ id: 'a3', title: 'Invoice intake', enabled: false }),
      'automations.validate': () => ({ errors: [], warnings: [], next_times: {} }),
    });
    fireEvent.click(await screen.findByRole('switch', { name: 'Turn off Weekly digest' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'automations.setEnabled')?.input).toEqual({ id: 'a1', enabled: false }));
    fireEvent.click(screen.getByRole('switch', { name: 'Turn on Invoice intake' }));
    expect(await screen.findByRole('dialog', { name: 'Turn on Invoice intake' })).toBeTruthy();
  });

  it('primes the composer for Desk, starts a blank draft, and imports an export (asking for a name when taken)', async () => {
    const bridge = await setup({
      'automations.list': () => LIST,
      'automations.import': ({ exp }: { exp: { name: string } }) => {
        if (exp.name === 'digest') throw { code: 'conflict', message: 'taken', status: 409 };
        return { automation: automationDetail({ id: 'a9', name: exp.name }), warnings: [] };
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Describe one to Desk' }));
    expect(localStorage.getItem('desk.draft.p')).toBe("I'd like to automate: ");
    expect(window.location.hash).toBe('#/p/p/conversation');

    fireEvent.click(screen.getByRole('button', { name: 'Blank automation' }));
    fireEvent.input(screen.getByLabelText('Name'), { target: { value: 'weekly-note' } });
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

  it('shows a draft with only Design, and says when the project is gone', async () => {
    await setup({ 'automations.validate': () => ({ errors: [], warnings: [], next_times: {} }) }, { projectId: 'p', draft: 'weekly-note' });
    expect(await screen.findByRole('heading', { name: 'New automation' })).toBeTruthy();
    const tabs = screen.getByRole('navigation', { name: 'Automation' });
    expect(within(tabs).getAllByRole('link').map((a) => a.textContent)).toEqual(['Design']);
    expect(within(tabs).getByRole('link', { name: 'Design' }).getAttribute('aria-current')).toBe('page');
  });
});

describe('Automation header', () => {
  it('shows the version badge, run count and switch, and runs, exports and deletes', async () => {
    const d = automationDetail({ last_run: run({ id: 'r14', number: 14, status: 'running', finished_at: null }), grants: [{ tool: 'web_fetch', match: { domain: 'acme.com' }, action: 'allow' }] });
    const bridge = await setup(
      {
        'automations.get': () => d,
        'automations.validate': () => ({ errors: [], warnings: [], next_times: {} }),
        'automations.setEnabled': () => ({ ...d, enabled: false }),
        'automations.export': () => ({ format: 'desk-automation/1', name: 'digest', definition: d.definition }),
        'app.saveFile': () => true,
        'automations.remove': () => ({ ok: true }),
      },
      { projectId: 'p', automationId: 'a1', view: 'design' },
    );
    expect(await screen.findByRole('heading', { name: 'Weekly digest' })).toBeTruthy();
    expect(screen.getByText('v7 · tested in v6')).toBeTruthy();
    const tabs = screen.getByRole('navigation', { name: 'Automation' });
    expect(within(tabs).getByRole('link', { name: 'Runs (14)' }).getAttribute('href')).toBe('#/p/p/automations/a1/runs');
    expect(within(tabs).getByRole('link', { name: 'Grants (1)' })).toBeTruthy();
    expect(within(tabs).getByRole('link', { name: 'Design' }).getAttribute('aria-current')).toBe('page');
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

The React test has four cases. The port adds a fourth list case for the draft (`DraftHeader` has no React test of its own) and an `n/a` and `aria-current` check. Every React assertion is kept.

- [ ] **Step 2: Run it to verify it fails**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/automations-screen.spec.ts)`
Expected: FAIL, `Could not resolve "./automations-screen"`.

- [ ] **Step 3: Write `draft.ts` and point the conversation at it**

`apps/web-ui/src/app/conversation/draft.ts` (the desktop's `conversation/draft.ts`, word for word):

```ts
/** Where the conversation composer keeps a project's unsent draft (the desktop uses the same key). */
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

In `apps/web-ui/src/app/conversation/conversation-screen.ts`, delete the line

```ts
const draftKey = (projectId: string) => `desk.draft.${projectId}`;
```

and add, with the other relative imports (sorted, after `./composer`):

```ts
import { draftKey } from './draft';
```

- [ ] **Step 4: Write `automation-list.ts`**

`apps/web-ui/src/app/automations/automation-list.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, inject, input, signal, ViewEncapsulation } from '@angular/core';
import { AutomationExport, type AutomationDetail, type AutomationSummary } from '@desk/protocol';
import { dayTime, href, lastRunText, listNote, whenText, type Route } from '@desk/ui-core';
import { Button } from '../components/button';
import { EmptyState } from '../components/empty-state';
import { ToastService } from '../components/toast';
import { primeDraft } from '../conversation/draft';
import { DeskBridge, DeskCallError } from '../core/desk-bridge';
import { NowService } from '../core/now.service';
import { RouteService } from '../core/route.service';
import type { SessionState } from '../core/session.service';
import { injectAutomationList } from './data';
import { NameDialog } from './dialogs/name-dialog';
import { RunDialog } from './dialogs/run-dialog';
import { TurnOnDialog } from './dialogs/turn-on-dialog';

/** Mockup 1: every automation with its switch, when it runs, its last run and its next one. */
@Component({
  selector: 'div[deskAutomationList]',
  imports: [Button, EmptyState, NameDialog, RunDialog, TurnOnDialog],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-list' },
  template: `
    <header class="auto-list-head">
      <h1 class="title">Automations</h1>
      <span class="grow"></span>
      <button deskButton (click)="file.click()">Import…</button>
      <button deskButton (click)="naming.set({})">Blank automation</button>
      <button deskButton variant="primary" (click)="describe()">Describe one to Desk</button>
      <input #file type="file" accept=".json,application/json" hidden data-testid="automation-import" (change)="picked($event)" />
    </header>
    @if (live.status() === 'loading') {
      <p class="muted">Loading…</p>
    } @else if (live.status() === 'error' && !live.value()) {
      <div deskEmptyState title="Couldn't load automations" [body]="live.error()"></div>
    } @else if (rows().length) {
      <table class="auto-table">
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
          @for (a of rows(); track a.id) {
            <tr>
              <td>
                <a class="auto-row-title" [href]="a.href">{{ a.summary.title }}</a>
                <div class="muted small">{{ a.note }}</div>
              </td>
              <td>
                <button type="button" role="switch" class="switch" [attr.aria-checked]="a.summary.enabled" [attr.aria-label]="a.switchLabel" [disabled]="busy() === a.id" (click)="toggle(a.summary)">
                  <span class="switch-knob"></span>
                </button>
              </td>
              <td>{{ a.when }}</td>
              <td>
                @if (a.last; as last) {
                  <span class="auto-last" [class]="'tone-' + last.tone"><span class="dot" aria-hidden="true"></span>{{ last.text }}</span>
                } @else {
                  <span class="muted">never run</span>
                }
              </td>
              <td>{{ a.next }}@if (!a.next) {<span class="muted">n/a</span>}</td>
            </tr>
          }
        </tbody>
      </table>
    } @else {
      <div deskEmptyState title="No automations yet" body="Describe something you do again and again, and Desk builds, tests and proposes an automation. Or start from a blank one."></div>
    }
    @if (naming(); as n) {
      <div
        deskNameDialog
        [title]="n.importing ? 'Import as…' : 'New automation'"
        [confirmLabel]="n.importing ? 'Import' : 'Create'"
        [initial]="n.importing ? n.importing.name + '-2' : ''"
        [hint]="n.importing ? 'An automation is already called ' + n.importing.name + '. Pick another name for this one.' : undefined"
        [taken]="taken()"
        (close)="naming.set(null)"
        (confirm)="named($event)"
      ></div>
    }
    @if (turnOn(); as d) {
      <div deskTurnOnDialog [detail]="d" (close)="turnOn.set(null)" (done)="turnedOn()" (testFirst)="testFirst(d)"></div>
    }
    @if (testing(); as d) {
      <div deskRunDialog [target]="d" [test]="true" (close)="testing.set(null)" (started)="started(d.id, $event)"></div>
    }
  `,
})
export class AutomationList {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly routes = inject(RouteService);
  private readonly now = inject(NowService).now;
  protected readonly live = injectAutomationList(() => this.projectId(), () => this.s());
  protected readonly naming = signal<null | { importing?: AutomationExport }>(null);
  protected readonly turnOn = signal<AutomationDetail | null>(null);
  protected readonly testing = signal<AutomationDetail | null>(null);
  protected readonly busy = signal<string | null>(null);
  protected readonly taken = computed(() => (this.live.value() ?? []).map((a) => a.name));
  protected readonly rows = computed(() => {
    const now = this.now();
    return (this.live.value() ?? []).map((a) => ({
      id: a.id,
      summary: a,
      href: href(this.at(a.id)),
      note: listNote(a),
      switchLabel: a.enabled ? `Turn off ${a.title}` : `Turn on ${a.title}`,
      when: whenText(a.schedules),
      last: a.last_run ? lastRunText(a.last_run, now) : null,
      next: a.next_due ? dayTime(a.next_due, now) : null,
    }));
  });

  private at(automationId: string, extra: { view: 'design' } | { view: 'runs'; runId: string } = { view: 'design' }): Route {
    return { name: 'project', id: this.projectId(), tab: 'automations', automationId, ...extra };
  }

  protected describe(): void {
    primeDraft(this.projectId(), "I'd like to automate: ");
    this.routes.navigate({ name: 'project', id: this.projectId(), tab: 'conversation' });
  }

  protected picked(e: Event): void {
    const el = e.target as HTMLInputElement;
    const file = el.files?.[0];
    el.value = '';
    if (file) void this.readImport(file);
  }

  private async readImport(file: File): Promise<void> {
    let raw: unknown = null;
    try {
      raw = JSON.parse(await file.text());
    } catch {
      raw = null;
    }
    const parsed = AutomationExport.safeParse(raw);
    if (!parsed.success) {
      this.toasts.toast({ tone: 'error', message: "That file isn't a Desk automation export." });
      return;
    }
    await this.doImport(parsed.data);
  }

  private async doImport(exp: AutomationExport): Promise<void> {
    try {
      const r = await this.bridge.call('automations.import', { projectId: this.projectId(), exp });
      this.toasts.toast({ tone: 'info', message: `Imported ${r.automation.title}. It stays off until you turn it on.` });
      this.routes.navigate(this.at(r.automation.id));
    } catch (err) {
      if (err instanceof DeskCallError && err.status === 409) this.naming.set({ importing: exp });
      else this.toasts.error(err);
    }
  }

  protected named(name: string): void {
    const importing = this.naming()?.importing;
    this.naming.set(null);
    if (importing) void this.doImport({ ...importing, name });
    else this.routes.navigate({ name: 'project', id: this.projectId(), tab: 'automations', draft: name });
  }

  protected async toggle(a: AutomationSummary): Promise<void> {
    this.busy.set(a.id);
    try {
      if (a.enabled) {
        await this.bridge.call('automations.setEnabled', { id: a.id, enabled: false });
        this.live.reload();
      } else this.turnOn.set(await this.bridge.call('automations.get', { id: a.id }));
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.busy.set(null);
    }
  }

  protected turnedOn(): void {
    this.turnOn.set(null);
    this.live.reload();
  }

  protected testFirst(d: AutomationDetail): void {
    this.testing.set(d);
    this.turnOn.set(null);
  }

  protected started(automationId: string, runId: string): void {
    this.routes.navigate(this.at(automationId, { view: 'runs', runId }));
  }
}
```

The last cell writes `{{ a.next }}` then `@if`, not `@if (a.next; as next) {{{ next }}}`: three braces in a row are ambiguous to Angular's block parser (Port conventions, Braces). `null` interpolates as nothing.

- [ ] **Step 5: Write `automation-header.ts`**

`apps/web-ui/src/app/automations/automation-header.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, inject, input, output, signal, ViewEncapsulation } from '@angular/core';
import type { AutomationDetail } from '@desk/protocol';
import { href, versionBadge, type AutomationView, type Route } from '@desk/ui-core';
import { Button } from '../components/button';
import { ConfirmDialog } from '../components/confirm-dialog';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { RouteService } from '../core/route.service';
import { RunDialog } from './dialogs/run-dialog';
import { TurnOnDialog } from './dialogs/turn-on-dialog';

type Dialog = null | 'run' | 'test' | 'turn-on' | 'delete';

/** One automation's header (mockup 2): title, version badge, the switch, Test… and Run now…, and the sub-tabs. */
@Component({
  selector: 'header[deskAutomationHeader]',
  imports: [Button, ConfirmDialog, RunDialog, TurnOnDialog],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'automation-head' },
  template: `
    <div class="automation-title-row">
      <a class="muted small" [href]="all()">Automations ›</a>
      <h1 class="automation-title">{{ detail().title }}</h1>
      <span class="auto-badge" [class.ok]="detail().tested_version === detail().version">{{ badge() }}</span>
      @if (detail().grants_suspended) {
        <a class="auto-badge warn" [href]="grantsHref()">grants suspended</a>
      }
      <span class="grow"></span>
      <span class="muted small">{{ detail().enabled ? 'On' : 'Off' }}</span>
      <button type="button" role="switch" class="switch" [attr.aria-checked]="detail().enabled" [attr.aria-label]="switchLabel()" [disabled]="switching()" (click)="toggle()">
        <span class="switch-knob"></span>
      </button>
      <button deskButton (click)="dialog.set('test')">Test…</button>
      <button deskButton variant="primary" (click)="dialog.set('run')">Run now…</button>
    </div>
    <nav class="automation-tabs" aria-label="Automation">
      @for (t of tabs(); track t.view) {
        <a [href]="t.href" [attr.aria-current]="view() === t.view ? 'page' : null">{{ t.label }}</a>
      }
      <span class="grow"></span>
      <button deskButton size="sm" variant="ghost" (click)="exportIt()">Export…</button>
      <button deskButton size="sm" variant="ghost" (click)="dialog.set('delete')">Delete…</button>
    </nav>
    @if (dialog() === 'run' || dialog() === 'test') {
      <div deskRunDialog [target]="detail()" [test]="dialog() === 'test'" (close)="dialog.set(null)" (started)="started($event)"></div>
    }
    @if (dialog() === 'turn-on') {
      <div deskTurnOnDialog [detail]="detail()" (close)="dialog.set(null)" (done)="turnedOn($event)" (testFirst)="dialog.set('test')"></div>
    }
    @if (dialog() === 'delete') {
      <div deskConfirmDialog [title]="'Delete ' + detail().title + '?'" confirmLabel="Delete" [danger]="true" (confirm)="remove()" (cancel)="dialog.set(null)">Its runs are cancelled and its schedules stop. Its history, and the files its runs put in the Library, are kept.</div>
    }
  `,
})
export class AutomationHeader {
  readonly projectId = input.required<string>();
  readonly detail = input.required<AutomationDetail>();
  readonly view = input.required<AutomationView>();
  /** React's onChange. */
  readonly detailChange = output<AutomationDetail>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly routes = inject(RouteService);
  protected readonly dialog = signal<Dialog>(null);
  protected readonly switching = signal(false);
  protected readonly all = computed(() => href({ name: 'project', id: this.projectId(), tab: 'automations' }));
  protected readonly badge = computed(() => versionBadge(this.detail()));
  protected readonly switchLabel = computed(() => (this.detail().enabled ? `Turn off ${this.detail().title}` : `Turn on ${this.detail().title}`));
  protected readonly grantsHref = computed(() => href(this.route('grants')));
  protected readonly tabs = computed(() => {
    const d = this.detail();
    const runs = d.last_run?.number ?? 0;
    const tab = (view: AutomationView, label: string) => ({ view, label, href: href(this.route(view)) });
    return [tab('design', 'Design'), tab('runs', runs ? `Runs (${runs})` : 'Runs'), tab('versions', 'Versions'), tab('grants', d.grants.length ? `Grants (${d.grants.length})` : 'Grants')];
  });

  private route(view: AutomationView, runId?: string): Route {
    return { name: 'project', id: this.projectId(), tab: 'automations', automationId: this.detail().id, view, ...(runId ? { runId } : {}) };
  }

  protected async toggle(): Promise<void> {
    const d = this.detail();
    if (!d.enabled) {
      this.dialog.set('turn-on');
      return;
    }
    this.switching.set(true);
    try {
      this.detailChange.emit(await this.bridge.call('automations.setEnabled', { id: d.id, enabled: false }));
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.switching.set(false);
    }
  }

  protected async exportIt(): Promise<void> {
    const d = this.detail();
    try {
      const exp = await this.bridge.call('automations.export', { id: d.id });
      await this.bridge.call('app.saveFile', { name: `${d.name}.desk-automation.json`, data: new TextEncoder().encode(`${JSON.stringify(exp, null, 2)}\n`) });
    } catch (err) {
      this.toasts.error(err);
    }
  }

  protected async remove(): Promise<void> {
    this.dialog.set(null);
    const d = this.detail();
    try {
      await this.bridge.call('automations.remove', { id: d.id });
      this.toasts.toast({ tone: 'info', message: `Deleted ${d.title}.` });
      this.routes.navigate({ name: 'project', id: this.projectId(), tab: 'automations' });
    } catch (err) {
      this.toasts.error(err);
    }
  }

  protected started(runId: string): void {
    this.dialog.set(null);
    this.routes.navigate(this.route('runs', runId));
  }

  protected turnedOn(next: AutomationDetail): void {
    this.dialog.set(null);
    this.detailChange.emit(next);
  }
}

/** The header of a Blank automation before its first save. */
@Component({
  selector: 'header[deskDraftHeader]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'automation-head' },
  template: `
    <div class="automation-title-row">
      <a class="muted small" [href]="all()">Automations ›</a>
      <h1 class="automation-title">New automation</h1>
      <span class="auto-badge mono">{{ name() }}</span>
      <span class="muted small">Not saved yet: add a step, then Save.</span>
    </div>
    <nav class="automation-tabs" aria-label="Automation">
      <a [href]="self()" aria-current="page">Design</a>
    </nav>
  `,
})
export class DraftHeader {
  readonly projectId = input.required<string>();
  readonly name = input.required<string>();
  protected readonly all = computed(() => href({ name: 'project', id: this.projectId(), tab: 'automations' }));
  protected readonly self = computed(() => href({ name: 'project', id: this.projectId(), tab: 'automations', draft: this.name() }));
}
```

- [ ] **Step 6: Write `automations-screen.ts`**

`apps/web-ui/src/app/automations/automations-screen.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, input, ViewEncapsulation } from '@angular/core';
import { href, type AutomationView } from '@desk/ui-core';
import { EmptyState } from '../components/empty-state';
import { injectSession, type SessionState } from '../core/session.service';
import { AutomationHeader, DraftHeader } from './automation-header';
import { AutomationList } from './automation-list';
import { injectAutomation } from './data';
import { DesignView } from './design/design-view';
import { GrantsView } from './grants/grants-view';
import { RunsList } from './runs/runs-list';
import { RunView } from './runs/run-view';
import { VersionsView } from './versions/versions-view';

/** The project's git sources, for an agent step's worktree. */
const gitSources = (s: SessionState) => (s.project?.sources ?? []).filter((x) => x.kind === 'git').map((x) => ({ id: x.id, label: x.label }));

/** One automation: its header, then Design, Runs (or one run), Versions or Grants. */
@Component({
  selector: 'div[deskOneAutomation]',
  imports: [AutomationHeader, DesignView, EmptyState, GrantsView, RunsList, RunView, VersionsView],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @if (live.status() === 'missing') {
      <div deskEmptyState title="This automation is gone" body="It was deleted."><a [href]="all()">All automations</a></div>
    } @else {
      @if (live.value(); as d) {
        <div class="automation">
          <header deskAutomationHeader [projectId]="projectId()" [detail]="d" [view]="view()" (detailChange)="live.replace($event)"></header>
          <div class="automation-body">
            @switch (view()) {
              @case ('runs') {
                @if (runId(); as rid) {
                  @for (k of [rid]; track k) {
                    <div deskRunView [projectId]="projectId()" [s]="s()" [detail]="d" [runId]="k"></div>
                  }
                } @else {
                  <div deskRunsList [projectId]="projectId()" [s]="s()" [detail]="d"></div>
                }
              }
              @case ('versions') {
                <div deskVersionsView [detail]="d" (detailChange)="live.replace($event)"></div>
              }
              @case ('grants') {
                <div deskGrantsView [projectId]="projectId()" [s]="s()" [detail]="d" (detailChange)="live.replace($event)"></div>
              }
              @default {
                @for (k of [d.id]; track k) {
                  <div deskDesignView [projectId]="projectId()" [sources]="sources()" [detail]="d" (detailChange)="live.replace($event)"></div>
                }
              }
            }
          </div>
        </div>
      } @else if (live.status() === 'error') {
        <div deskEmptyState title="Couldn't load this automation" [body]="live.error()"><a [href]="all()">All automations</a></div>
      } @else {
        <p class="muted auto-loading">Loading…</p>
      }
    }
  `,
})
export class OneAutomation {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly id = input.required<string>();
  readonly view = input.required<AutomationView>();
  readonly runId = input<string | undefined>(undefined);
  protected readonly live = injectAutomation(() => this.s(), () => this.id());
  protected readonly sources = computed(() => gitSources(this.s()));
  protected readonly all = computed(() => href({ name: 'project', id: this.projectId(), tab: 'automations' }));
}

/** A Blank automation before its first save (spec §8.1): only Design, on a local draft. */
@Component({
  selector: 'div[deskDraftAutomation]',
  imports: [DesignView, DraftHeader],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'automation' },
  template: `
    <header deskDraftHeader [projectId]="projectId()" [name]="name()"></header>
    <div class="automation-body">
      @for (k of [name()]; track k) {
        <div deskDesignView [projectId]="projectId()" [sources]="sources()" [draftName]="k"></div>
      }
    </div>
  `,
})
export class DraftAutomation {
  readonly projectId = input.required<string>();
  readonly s = input.required<SessionState>();
  readonly name = input.required<string>();
  protected readonly sources = computed(() => gitSources(this.s()));
}

/** The Automations tab (spec §8.1): the list, one automation (header, then Design, Runs, Versions or Grants), or a new draft. */
@Component({
  selector: 'div[deskAutomationsScreen]',
  imports: [AutomationList, DraftAutomation, EmptyState, OneAutomation],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @let draftName = draft();
    @let oneId = automationId();
    @if (s().status === 'missing') {
      <div deskEmptyState title="This project is gone"></div>
    } @else if (draftName) {
      <div deskDraftAutomation [projectId]="projectId()" [s]="s()" [name]="draftName"></div>
    } @else if (oneId) {
      <div deskOneAutomation [projectId]="projectId()" [s]="s()" [id]="oneId" [view]="view() ?? 'design'" [runId]="runId()"></div>
    } @else {
      <div deskAutomationList [projectId]="projectId()" [s]="s()"></div>
    }
  `,
})
export class AutomationsScreen {
  readonly projectId = input.required<string>();
  readonly automationId = input<string | undefined>(undefined);
  readonly view = input<AutomationView | undefined>(undefined);
  readonly runId = input<string | undefined>(undefined);
  readonly draft = input<string | undefined>(undefined);
  protected readonly s = injectSession(() => this.projectId());
}
```

`@let` values narrow in `@if` and `@else if` conditions like locals, so `[name]="draftName"` is a `string`. The `@for (k of [key]; track k)` blocks are React's `key=` remounts (Port conventions, Remounting): a new run or a new automation gets a fresh view with fresh local state.

- [ ] **Step 7: Route to it and delete the placeholder**

In `apps/web-ui/src/app/screen-for.ts`, replace the import

```ts
import { AutomationsPlaceholder } from './automations/automations-placeholder';
```

with

```ts
import { AutomationsScreen } from './automations/automations-screen';
```

and the automations case with

```ts
        case 'automations':
          return { component: AutomationsScreen, inputs: { projectId: route.id, automationId: route.automationId, view: route.view, runId: route.runId, draft: route.draft } };
```

In `apps/web-ui/src/app/screen-for.spec.ts`, make the same import change, and replace the automations expectation with

```ts
    expect(screenFor({ name: 'project', id: 'p', tab: 'automations', automationId: 'a1', view: 'runs', runId: 'r1' })).toEqual({ component: AutomationsScreen, inputs: { projectId: 'p', automationId: 'a1', view: 'runs', runId: 'r1', draft: undefined } });
    expect(screenFor({ name: 'project', id: 'p', tab: 'automations', draft: 'weekly-note' })).toEqual({ component: AutomationsScreen, inputs: { projectId: 'p', automationId: undefined, view: undefined, runId: undefined, draft: 'weekly-note' } });
```

Then delete the placeholder:

```bash
git rm apps/web-ui/src/app/automations/automations-placeholder.ts
```

- [ ] **Step 8: Run the specs and the typecheck**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/automations/automations-screen.spec.ts --include src/app/screen-for.spec.ts --include src/app/conversation/conversation-screen.spec.ts) && pnpm --filter @desk/web-ui typecheck`
Expected: PASS. `conversation-screen.spec.ts` keeps passing: the key is the same string.

Then `grep -rn "AutomationsPlaceholder\|automations-placeholder" apps/web-ui/src` prints nothing.

- [ ] **Step 9: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-ui/src/app/conversation/draft.ts apps/web-ui/src/app/conversation/conversation-screen.ts apps/web-ui/src/app/automations/automation-list.ts apps/web-ui/src/app/automations/automation-header.ts apps/web-ui/src/app/automations/automations-screen.ts apps/web-ui/src/app/automations/automations-screen.spec.ts apps/web-ui/src/app/screen-for.ts apps/web-ui/src/app/screen-for.spec.ts && git commit -m "feat(web): the Automations tab: the list, one automation's header and views, drafts; the placeholder goes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

The `git rm` in Step 7 already staged the placeholder's deletion, so this commit includes it.

---
### Task 15: Attention cards for automations

**Files:**
- Create: `apps/web-ui/src/app/attention/automation-cards.ts`
- Create: `apps/web-ui/src/app/attention/automations.spec.ts`
- Modify: `apps/web-ui/src/app/attention/inspector.ts`
- Modify: `apps/web-ui/src/app/attention/attention-screen.ts`

**Interfaces:**
- Consumes:
  - Task 10's `TurnOnDialog`, and Task 11's `AskAnswer` (`section[deskAskAnswer]`, inputs `run` and `row`) and `GateAnswer` (the same plus `grantsSuspended`).
  - From `@desk/ui-core`: `askDeskText`, `automationTarget`, `runFailure`. `stripWho`, `KIND_NAME` and `STRIP_CODE` already know the four automation kinds.
- Produces:
  - `type AutomationItemData = { detail: AutomationDetail | null; run: RunDetail | null; missing: boolean }`.
  - `injectAutomationItem(item: () => AttentionItem): Signal<AutomationItemData>`.
  - `div[deskAutomationCard]` (`AutomationCard`). Inputs: `item`, `auto`, `busy`. Outputs: `open`, `dismiss`.
  - A new `Inspector` output, `remember: void` ("Approve and remember for this automation").

- [ ] **Step 1: Write the failing spec (a port of `attention/automations.test.tsx`)**

`apps/web-ui/src/app/attention/automations.spec.ts`:

```ts
import { Component, computed, inject } from '@angular/core';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { ProjectOverview } from '@desk/client';
import { ev } from '@desk/client/testing';
import type { AttentionItem, StoredEvent } from '@desk/protocol';
import { automationDetail, runDetail, stepRun } from '@desk/ui-core/testing';
import { RouteService } from '../core/route.service';
import { SESSION_RELEASE_DELAY } from '../core/session.service';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../testing/fake-bridge';
import { AttentionScreen } from './attention-screen';

beforeEach(() => void (window.location.hash = '#/attention'));

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

/** The desktop test's Routed: AttentionScreen with the route's item. */
@Component({
  selector: 'desk-routed',
  imports: [AttentionScreen],
  template: `<div deskAttentionScreen [itemId]="item()"></div>`,
})
class Routed {
  private readonly route = inject(RouteService).route;
  protected readonly item = computed(() => {
    const r = this.route();
    return r.name === 'attention' ? r.item : undefined;
  });
}

async function setup(list: AttentionItem[], extra: FakeHandlers = {}) {
  const bridge: FakeDeskBridge = new FakeDeskBridge({
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
  await render(Routed, {
    providers: [...bridge.providers, provideGlobal({ ...initialGlobalState(), connection: { status: 'live' }, attention: list }), { provide: SESSION_RELEASE_DELAY, useValue: 0 }],
  });
  return bridge;
}

const input = (bridge: FakeDeskBridge, channel: string) => bridge.calls.find((c) => c.channel === channel)?.input;

describe('Attention: automations', () => {
  it('names a step agent’s approval by automation and step, and approves it with remember', async () => {
    const bridge = await setup([stepApproval], { 'approvals.resolve': () => ({ ok: true }) });
    expect(screen.getByRole('button', { name: /Automation Weekly digest\./ })).toBeTruthy();
    const insp = await screen.findByRole('article', { name: /clearance request/ });
    const link = await within(insp).findByRole('link', { name: 'Weekly digest · Summarise' });
    expect(link.getAttribute('href')).toBe('#/p/p/automations/a1/runs/r14');
    expect(within(insp).getByText('What the step agent said')).toBeTruthy();
    fireEvent.click(await within(insp).findByRole('button', { name: 'Approve and remember for this automation' }));
    await waitFor(() => expect(input(bridge, 'approvals.resolve')).toEqual({ id: 'ap9', decision: 'approved', remember: true }));
  });

  it('offers no remember while the automation’s grants are suspended', async () => {
    await setup([stepApproval], { 'automations.get': () => automationDetail({ grants_suspended: true }) });
    const insp = await screen.findByRole('article', { name: /clearance request/ });
    await within(insp).findByRole('link', { name: 'Weekly digest · Summarise' });
    expect(within(insp).queryByRole('button', { name: 'Approve and remember for this automation' })).toBeNull();
    expect(within(insp).getByText(/grants are suspended/)).toBeTruthy();
  });

  it('answers an Ask me step in place, and E opens the run', async () => {
    const bridge = await setup([ask], { 'automations.getRun': () => waitingAsk(), 'automations.answer': () => ({ ok: true }) });
    const insp = await screen.findByRole('article', { name: /automation question/ });
    expect(await within(insp).findByRole('heading', { name: 'Weekly digest · Publish?' })).toBeTruthy();
    fireEvent.click(within(within(insp).getByRole('region', { name: 'Your answer' })).getByRole('button', { name: 'Publish' }));
    await waitFor(() => expect(input(bridge, 'automations.answer')).toEqual({ runId: 'r14', stepId: 'ok', req: { decision: 'approve' } }));
    fireEvent.keyDown(window, { key: 'e' });
    expect(window.location.hash).toBe('#/p/p/automations/a1/runs/r14');
  });

  it('asks Desk to fix a failed run, and dismisses it', async () => {
    const run = runDetail({ status: 'failed', steps: [stepRun('fetch', { status: 'failed', error: 'exit 1\nTraceback' }), ...runDetail().steps.slice(1)] });
    const bridge = await setup([failed], { 'automations.getRun': () => run, 'projects.send': () => ({ ok: true }), 'attention.dismiss': () => ({ ok: true }) });
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
    await setup([enable], {
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
    await setup([suspended]);
    await screen.findByRole('article', { name: /grants suspended/ });
    expect(document.querySelector('.strip-legend')!.textContent).toContain('FLDFailed thread or automation');
    fireEvent.keyDown(window, { key: 'e' });
    expect(window.location.hash).toBe('#/p/p/automations/a1/grants');
  });
});
```

`Button`'s `disabled` input binds the native `disabled` property, so the `(fix as HTMLButtonElement).disabled` checks read the same thing they read in React.

- [ ] **Step 2: Run it to verify it fails**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/attention/automations.spec.ts)`
Expected: FAIL. The first case finds no link named 'Weekly digest · Summarise'. The ask case finds no heading 'Weekly digest · Publish?', because today's `@default` renders `i.title`.

- [ ] **Step 3: Write `automation-cards.ts`**

`apps/web-ui/src/app/attention/automation-cards.ts`:

```ts
import { ChangeDetectionStrategy, Component, computed, effect, inject, input, output, signal, untracked, ViewEncapsulation, type Signal } from '@angular/core';
import type { AttentionItem, AutomationDetail, RunDetail } from '@desk/protocol';
import { askDeskText, runFailure } from '@desk/ui-core';
import { TurnOnDialog } from '../automations/dialogs/turn-on-dialog';
import { AskAnswer, GateAnswer } from '../automations/runs/step-panel';
import { Button } from '../components/button';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { RouteService } from '../core/route.service';

export type AutomationItemData = { detail: AutomationDetail | null; run: RunDetail | null; missing: boolean };

/**
 * An item's automation and run, when it names them (both null until loaded; `missing` once either is gone): the
 * desktop's useAutomationItem. Call it in a field initializer. It asks again only when the ids change, not on every push.
 */
export function injectAutomationItem(item: () => AttentionItem): Signal<AutomationItemData> {
  const bridge = inject(DeskBridge);
  const state = signal<AutomationItemData>({ detail: null, run: null, missing: false });
  const automationId = computed(() => item().ref.automation_id);
  const runId = computed(() => item().ref.run_id);
  effect((onCleanup) => {
    const a = automationId();
    const r = runId();
    let live = true;
    onCleanup(() => {
      live = false;
    });
    untracked(() => {
      const gone = () => {
        if (live) state.update((s) => ({ ...s, missing: true }));
      };
      if (a) {
        bridge
          .call('automations.get', { id: a })
          .then((detail) => {
            if (live) state.update((s) => ({ ...s, detail }));
          })
          .catch(gone);
      }
      if (r) {
        bridge
          .call('automations.getRun', { runId: r })
          .then((run) => {
            if (live) state.update((s) => ({ ...s, run }));
          })
          .catch(gone);
      }
    });
  });
  return state.asReadonly();
}

/** The inspector's body for the four automation kinds (spec §8.4). */
@Component({
  selector: 'div[deskAutomationCard]',
  imports: [AskAnswer, Button, GateAnswer, TurnOnDialog],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @switch (item().kind) {
      @case ('automation_ask') {
        <h2 class="inspector-title">{{ askTitle() }}</h2>
        @if (auto().missing) {
          <p class="muted">{{ goneText }}</p>
        } @else {
          @if (auto().run; as run) {
            @if (row(); as row) {
              @if (row.status !== 'waiting') {
                <p class="muted">It has been answered.</p>
              } @else if (row.question) {
                <section deskAskAnswer [run]="run" [row]="row"></section>
              } @else if (row.gate) {
                <section deskGateAnswer [run]="run" [row]="row" [grantsSuspended]="auto().detail?.grants_suspended ?? true"></section>
              }
            } @else {
              <p class="muted">Loading the step…</p>
            }
          } @else {
            <p class="muted">Loading the step…</p>
          }
        }
        <div class="inspector-actions">
          <button deskButton variant="ghost" (click)="open.emit()">Open run <kbd>E</kbd></button>
        </div>
      }
      @case ('automation_failed') {
        <h2 class="inspector-title">{{ item().title }}</h2>
        @if (item().detail) {
          <pre class="auto-error">{{ item().detail }}</pre>
        }
        @if (auto().missing) {
          <p class="muted">{{ goneText }}</p>
        }
        <div class="inspector-actions">
          <button deskButton variant="primary" (click)="open.emit()">Open run <kbd>E</kbd></button>
          <button deskButton [pending]="asking()" [disabled]="!auto().run" (click)="askFix()">Ask Desk to fix</button>
          <button deskButton [pending]="busy() === 'dismiss'" [disabled]="busy() !== null" (click)="dismiss.emit()">Dismiss</button>
        </div>
        <p class="muted small">Dismissing hides this here. The automation is not changed, and its next run starts on time.</p>
      }
      @case ('automation_enable_request') {
        <h2 class="inspector-title">{{ item().title }}</h2>
        @if (item().detail) {
          <p>{{ item().detail }}</p>
        }
        @if (auto().missing) {
          <p class="muted">{{ goneText }}</p>
        }
        <div class="inspector-actions">
          <button deskButton variant="primary" [disabled]="!auto().detail" (click)="turningOn.set(true)">Turn on…</button>
          <button deskButton variant="ghost" (click)="open.emit()">Open automation <kbd>E</kbd></button>
          <button deskButton [pending]="busy() === 'dismiss'" [disabled]="busy() !== null" (click)="dismiss.emit()">Dismiss</button>
        </div>
        <p class="muted small">Only you turn automations on. Dismissing leaves it off; Desk is not told.</p>
        @if (turningOn()) {
          @if (auto().detail; as detail) {
            <div deskTurnOnDialog [detail]="detail" (close)="turningOn.set(false)" (done)="turningOn.set(false)" (testFirst)="testFirst()"></div>
          }
        }
      }
      @default {
        <h2 class="inspector-title">{{ item().title }}</h2>
        @if (item().detail) {
          <p>{{ item().detail }}</p>
        }
        <div class="inspector-actions">
          <button deskButton variant="primary" (click)="open.emit()">Review changes <kbd>E</kbd></button>
          <button deskButton [pending]="busy() === 'dismiss'" [disabled]="busy() !== null" (click)="dismiss.emit()">Dismiss</button>
        </div>
        <p class="muted small">Until you keep them on its Grants tab, its runs ask you for everything.</p>
      }
    }
  `,
})
export class AutomationCard {
  readonly item = input.required<AttentionItem>();
  readonly auto = input.required<AutomationItemData>();
  readonly busy = input<string | null>(null);
  readonly open = output<void>();
  readonly dismiss = output<void>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  private readonly routes = inject(RouteService);
  protected readonly goneText = 'It could not be loaded: the automation or its run may have been deleted.';
  protected readonly turningOn = signal(false);
  protected readonly asking = signal(false);
  protected readonly row = computed(() => {
    const run = this.auto().run;
    const stepId = this.item().ref.step_id;
    return run && stepId ? run.steps.find((s) => s.step_id === stepId) : undefined;
  });
  protected readonly askTitle = computed(() => {
    const run = this.auto().run;
    const row = this.row();
    const stepTitle = row ? (run?.definition.steps.find((s) => s.id === row.step_id)?.title ?? row.step_id) : null;
    return run && stepTitle ? `${run.automation_title} · ${stepTitle}` : this.item().title;
  });

  protected async askFix(): Promise<void> {
    const run = this.auto().run;
    if (!run) return;
    const projectId = this.item().project_id;
    this.asking.set(true);
    try {
      const f = runFailure(run);
      await this.bridge.call('projects.send', { id: projectId, text: askDeskText(run, f.stepTitle, f.error) });
      this.toasts.toast({ tone: 'info', message: 'Sent to Desk.', action: { label: 'Open conversation', run: () => this.routes.navigate({ name: 'project', id: projectId, tab: 'conversation' }) } });
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.asking.set(false);
    }
  }

  protected testFirst(): void {
    this.turningOn.set(false);
    this.open.emit();
  }
}
```

The React card sits in the inspector as a fragment. Here the host is `display: contents`, and `attention.css` has no child combinators under `.inspector`, so the look is the same.

- [ ] **Step 4: Teach the Inspector about automations**

In `apps/web-ui/src/app/attention/inspector.ts`:

1. Imports. Change the ui-core import to

```ts
import { automationTarget, clock, href, KIND_NAME, policyReason, STRIP_CODE, waited } from '@desk/ui-core';
```

add, after the `session.service` import,

```ts
import { AutomationCard, injectAutomationItem } from './automation-cards';
```

and make the component's `imports` `[AutomationCard, Button, CodeBlock, SafeMarkdown]`.

2. In the `approval` case, replace the THREAD fact

```html
          <div class="fact"><span class="fact-label">{{ agent()?.role === 'desk' ? 'ASKED BY' : 'THREAD' }}</span><span class="fact-value">@if (threadHref(); as h) {<a [href]="h">{{ agent()?.title ?? 'Thread' }}</a>} @else {<ng-container>Desk</ng-container>}</span></div>
```

with

```html
          @if (step() !== null) {
            <div class="fact"><span class="fact-label">AUTOMATION</span><span class="fact-value">@if (automationLink(); as l) {<a [href]="l.href">{{ l.text }}</a>} @else {<ng-container>{{ step() }}</ng-container>}</span></div>
          } @else {
            <div class="fact"><span class="fact-label">{{ agent()?.role === 'desk' ? 'ASKED BY' : 'THREAD' }}</span><span class="fact-value">@if (threadHref(); as h) {<a [href]="h">{{ agent()?.title ?? 'Thread' }}</a>} @else {<ng-container>Desk</ng-container>}</span></div>
          }
```

3. In the same case, replace `<span class="why-label">What the thread said</span>` with

```html
            <span class="why-label">{{ step() !== null ? 'What the step agent said' : 'What the thread said' }}</span>
```

Leave the `@default` case's `detailLabel()` alone.

4. Replace the Approve once button line with it plus the remember button:

```html
          <button deskButton variant="primary" [pending]="busy() === 'approved'" [disabled]="busy() !== null" (click)="resolve.emit('approved')">Approve once <kbd>⌘⏎</kbd></button>
          @if (step() !== null && !suspended()) {
            <button deskButton [pending]="busy() === 'remember'" [disabled]="busy() !== null" (click)="remember.emit()">Approve and remember for this automation</button>
          }
```

5. Replace the approval case's last line, `<p class="muted small">The thread resumes as soon as you decide. If you deny, it's told why and tries another way.</p>`, with

```html
        <p class="muted small">{{ footnote() }}</p>
```

6. Before `@case ('paused') {`, add the four automation kinds (React's `i.kind.startsWith('automation_')` branch; `@switch` compares exact values):

```html
      @case ('automation_ask') {
        <div deskAutomationCard [item]="item()" [auto]="auto()" [busy]="busy()" (open)="open.emit()" (dismiss)="dismiss.emit()"></div>
      }
      @case ('automation_failed') {
        <div deskAutomationCard [item]="item()" [auto]="auto()" [busy]="busy()" (open)="open.emit()" (dismiss)="dismiss.emit()"></div>
      }
      @case ('automation_enable_request') {
        <div deskAutomationCard [item]="item()" [auto]="auto()" [busy]="busy()" (open)="open.emit()" (dismiss)="dismiss.emit()"></div>
      }
      @case ('automation_grants_suspended') {
        <div deskAutomationCard [item]="item()" [auto]="auto()" [busy]="busy()" (open)="open.emit()" (dismiss)="dismiss.emit()"></div>
      }
```

7. After `readonly resolve = output<'approved' | 'denied'>();`, add

```ts
  /** Approve and remember for this automation: a step agent's approval, while its grants are not suspended. */
  readonly remember = output<void>();
```

8. After `protected readonly detailLabel = …;`, add

```ts
  protected readonly auto = injectAutomationItem(() => this.item());
  /** A step agent's approval names its step (spec §8.4); null for any other item. */
  protected readonly step = computed(() => {
    const i = this.item();
    if (i.kind !== 'approval' || !i.ref.automation_id) return null;
    return this.auto().detail?.definition.steps.find((x) => x.id === i.ref.step_id)?.title ?? i.ref.step_id ?? 'a step';
  });
  protected readonly suspended = computed(() => this.auto().detail?.grants_suspended ?? true);
  protected readonly automationLink = computed(() => {
    const target = automationTarget(this.item());
    const d = this.auto().detail;
    return target && d ? { href: href(target), text: `${d.title} · ${this.step()}` } : null;
  });
  protected readonly footnote = computed(() => {
    if (this.step() === null) return "The thread resumes as soon as you decide. If you deny, it's told why and tries another way.";
    return this.auto().detail?.grants_suspended
      ? 'The step resumes as soon as you decide. Its grants are suspended until you keep them, so nothing is remembered.'
      : 'The step resumes as soon as you decide. Remember adds a grant, so its later runs do this without asking.';
  });
```

`suspended` defaults to true before the automation loads, so remember is offered only once the automation is known. The footnote reads `grants_suspended` without that default, as the desktop's does.

- [ ] **Step 5: Teach the AttentionScreen**

In `apps/web-ui/src/app/attention/attention-screen.ts`:

1. Change the ui-core import to `import { automationTarget, href, plural, rackOrder, STRIP_CODE, waited } from '@desk/ui-core';` and make `openTarget`

```ts
/** Where E goes: an automation item's run, Grants tab or automation; the thread for approvals and stuck threads; the conversation otherwise. */
function openTarget(i: AttentionItem): string {
  const automation = automationTarget(i);
  if (automation) return href(automation);
  if ((i.kind === 'stalled' || i.kind === 'failed' || i.kind === 'approval') && i.ref.thread_id) return href({ name: 'project', id: i.project_id, tab: 'threads', threadId: i.ref.thread_id });
  return href({ name: 'project', id: i.project_id, tab: 'conversation' });
}
```

2. In the legend, change four labels to the desktop's:

```html
        <span><span class="strip-code-badge code-question">{{ codes.question }}</span>Question (Desk or an automation)</span>
        <span><span class="strip-code-badge code-needs_you">{{ codes.needs_you }}</span>From a report, or an automation to turn on</span>
        <span><span class="strip-code-badge code-stalled">{{ codes.stalled }}</span>Stalled thread, or suspended grants</span>
        <span><span class="strip-code-badge code-failed">{{ codes.failed }}</span>Failed thread or automation</span>
```

3. On the inspector, after `(resolve)="resolve($event)"`, add `(remember)="resolve('approved', true)"`.

4. Make `resolve` take `remember`:

```ts
  protected async resolve(decision: 'approved' | 'denied', remember = false): Promise<void> {
    const selected = this.selected();
    const approvalId = selected?.kind === 'approval' ? selected.ref.approval_id : undefined;
    if (!selected || !approvalId || this.busy()) return;
    const note = this.note().trim();
    this.busy.set(remember ? 'remember' : decision);
    try {
      await this.bridge.call('approvals.resolve', { id: approvalId, decision, ...(note ? { note } : {}), ...(remember ? { remember: true } : {}) });
```

The rest of `resolve` stays as it is.

- [ ] **Step 6: Run the attention specs and the typecheck**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/attention/automations.spec.ts --include src/app/attention/attention-screen.spec.ts --include src/app/attention/inspector.spec.ts) && pnpm --filter @desk/web-ui typecheck`
Expected: PASS. The existing specs keep passing: items without an `automation_id` ask for nothing, and the legend's old labels are still prefixes of the new ones (`GNDPaused project` is unchanged).

- [ ] **Step 7: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-ui/src/app/attention/automation-cards.ts apps/web-ui/src/app/attention/automations.spec.ts apps/web-ui/src/app/attention/inspector.ts apps/web-ui/src/app/attention/attention-screen.ts && git commit -m "feat(web): Attention cards for automations; step approvals name their step and can be remembered

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 16: The parity guard without its exemption, and the docs

**Files:**
- Modify: `apps/web-ui/src/app/parity.spec.ts:15-19,158`
- Modify: `CLAUDE.md` (the `packages/ui-core` and `apps/web-ui` bullets, and the last invariant)
- Modify: `docs/web.md` (What differs, End-to-end tests)
- Modify: `docs/desktop.md:71`
- Modify: `docs/superpowers/specs/2026-09-26-automations-design.md:4`

**Interfaces:**
- Consumes: every earlier task. The guard now fails if the web UI misses any operation the React renderer calls, `automations.*`, `app.pickFile` and `app.revealPath` included.
- Produces: nothing new.

- [ ] **Step 1: Delete the exemption**

In `apps/web-ui/src/app/parity.spec.ts`, delete these lines:

```ts
/**
 * Automations reach the web UI in Plan 21. Plan 20 merged to master first, so until Plan 21 lands the React renderer
 * may call these without the web UI. Plan 21 deletes this list and its one use.
 */
const PLAN_21_OPS = (op: string) => op.startsWith('automations.') || op === 'app.pickFile' || op === 'app.revealPath';
```

and make the filter in `calls every operation the React renderer calls, but the desktop-only ones`

```ts
    const missing = [...opsIn(reactSources())].filter((op) => !web.has(op) && !HOST_ONLY.includes(op));
```

- [ ] **Step 2: Run the guard**

Run: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include src/app/parity.spec.ts --include src/app/security.spec.ts)`
Expected: PASS. If `missing` lists an operation, find the React file that calls it (`git grep -n "'<op>'" apps/desktop/src/renderer`) and add the literal call to the web component its task ported. Never add a new exemption.

Then: `git grep -n "PLAN_21_OPS\|AutomationsPlaceholder\|@xyflow" apps/web-ui` prints nothing.

- [ ] **Step 3: CLAUDE.md**

In the `packages/ui-core` bullet, replace `the editor's draft operations, issues, template suggestions, step fields, the Start card, version diffs and run graphs;` with `the editor's draft operations, issues, template suggestions, step fields, the Start card, version diffs, run graphs and the web canvas's geometry;`.

In the `apps/web-ui` bullet, after the `src/app/app.ts` sub-bullet, add:

```md
  - `src/app/automations/`: the Automations tab, as in the renderer. Design's graph is the web UI's own canvas (`design/graph-canvas.ts`): React Flow's class names and math, its geometry in `@desk/ui-core`'s `automation-canvas.ts`, no new dependency. `app.pickFile` opens the folder browser in file mode (`fs.listDirs` with `files`).
```

and replace `` `smoke`, `flows`, `knowledge`, `catalog`, `system`. `` with `` `smoke`, `flows`, `knowledge`, `catalog`, `system`, `automations`. ``.

Delete the last invariant, the whole line that begins `- Until Plan 21 ports the Automations screens to the web UI,`.

- [ ] **Step 4: docs/web.md, docs/desktop.md and the spec**

In `docs/web.md`, in the What differs table, after the `Choosing a folder` row, add:

```md
| Choosing a file (an automation's file input) | The system dialog | The same folder browser, listing files as well: click one or type a full path |
| Design's graph (Automations) | React Flow | Desk's own canvas with the same look and keys: drag a step, drag from a handle to connect, drag the background to pan, wheel or the buttons to zoom, Delete or Backspace removes the selection |
```

In the End-to-end tests table, after the `system.e2e.test.ts` row, add:

```md
| `automations.e2e.test.ts` | A Blank automation built on the canvas (an input, an agent step, an Ask me step), saved, run with an input, answered in the run view, its file in the Library; a file input chosen in the folder browser |
```

In `docs/desktop.md`, replace `so the web UI (Plan 21) shares it.` with `so the web UI shares it.`.

In `docs/superpowers/specs/2026-09-26-automations-design.md`, make the status line

```md
- **Status:** backend implemented (Plan 19); desktop app implemented (Plan 20); web UI implemented (Plan 21).
```

- [ ] **Step 5: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-ui/src/app/parity.spec.ts CLAUDE.md docs/web.md docs/desktop.md docs/superpowers/specs/2026-09-26-automations-design.md && git commit -m "test(web): the parity guard covers automations; docs: the web Automations tab

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: Automations end to end in the browser

**Files:**
- Create: `apps/web-ui/e2e/automations.e2e.test.ts`

**Interfaces:**
- Consumes: `startWebE2E({ script })`, `e2e.signIn()`, `e2e.client()`, `e2e.shot(page, name)`, `e2e.home` (`apps/web-ui/e2e/harness.ts`); `call`, `text`, `tools`, `ChatRequest`, `FakeReply` (`@desk/fake-model`).
- Produces: nothing used later.

The first case is the desktop's `apps/desktop/e2e/automations.e2e.test.ts`, step for step, in Chromium. The second covers what only the browser does: a file input chosen in the folder browser (Task 2).

- [ ] **Step 1: Check the disk and build the web UI**

Run: `df -h / | tail -1`. Stop if under 500 MB free. Then: `pnpm --filter @desk/web-ui build`
Expected: the build succeeds (`apps/web-ui/dist/browser`).

- [ ] **Step 2: Write the test**

`apps/web-ui/e2e/automations.e2e.test.ts`:

```ts
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { call, text, tools, type ChatRequest, type FakeReply } from '@desk/fake-model';
import { startWebE2E, type SignedIn, type WebE2E } from './harness';

let e2e: WebE2E;

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
  e2e = await startWebE2E({ script: (req) => (system(req).includes('You are one step of the automation') ? stepAgent(req) : text('Noted.')) });
});

afterAll(async () => {
  await e2e?.close();
});

/** A signed-in browser past onboarding, on `hash`. */
async function openAt(hash: string): Promise<SignedIn> {
  const signed = await e2e.signIn();
  await signed.page.evaluate(() => localStorage.setItem('desk.onboarded', '1'));
  await signed.page.evaluate((h) => (window.location.hash = h), hash);
  return signed;
}

describe('automations in the browser', () => {
  it('builds an automation in the editor, runs it with an input, answers its Ask me step and publishes its file', async () => {
    const client = e2e.client();
    const { project } = await client.projects.create({ name: 'Digest', goal: 'A weekly digest' });
    const { context, page, problems } = await openAt(`#/p/${project.id}/automations`);

    // A Blank automation, named once.
    await page.getByRole('button', { name: 'Blank automation' }).click();
    const naming = page.getByRole('dialog', { name: 'New automation' });
    await naming.getByLabel('Name').fill('digest');
    await naming.getByRole('button', { name: 'Create' }).click();

    // Start: one text input.
    await page.getByTestId('node-start').click();
    await page.getByRole('button', { name: 'Add input' }).click();
    const input = page.getByRole('group', { name: 'Input 1' });
    await input.getByLabel('Label', { exact: true }).fill('Topic');
    await input.getByLabel('Key', { exact: true }).fill('topic');
    await input.getByLabel('Key', { exact: true }).press('Tab');

    // An agent step that publishes digest.md, then (added while it is selected, so connected under it) an Ask me step.
    const strip = page.getByRole('toolbar', { name: 'Add a step' });
    await strip.getByRole('button', { name: 'Agent' }).click();
    await page.getByLabel('Brief', { exact: true }).fill('Write digest.md about {{inputs.topic}}.');
    await page.getByRole('button', { name: 'Add file' }).click();
    await page.getByLabel('Publish to the Library 1', { exact: true }).fill('digest.md');
    await strip.getByRole('button', { name: 'Ask me' }).click();
    await page.getByLabel('Question', { exact: true }).fill('Publish the digest?');
    await page.getByText('Ready to save').waitFor({ timeout: 10_000 });
    await e2e.shot(page, 'automations-design');

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
    await page.locator('.auto-run-head').getByText('waiting on you · Ask me').waitFor({ timeout: 10_000 });
    await e2e.shot(page, 'automations-waiting');

    // The run view opens on the waiting step: answer it there.
    const answer = page.getByRole('region', { name: 'Your answer' });
    await answer.getByText('Publish the digest?').waitFor();
    await answer.getByRole('button', { name: 'Approve' }).click();
    await page.locator('.auto-run-head').getByText('succeeded').waitFor({ timeout: 30_000 });
    expect(await page.getByTestId('node-ask').getAttribute('class')).toMatch(/run-ok/);
    await e2e.shot(page, 'automations-succeeded');

    // The agent step's file, in its panel and in the Library.
    await page.getByTestId('node-agent').click();
    await page.getByRole('region', { name: 'Files' }).getByRole('button', { name: 'digest.md' }).waitFor({ timeout: 10_000 });
    await expect
      .poll(async () => (await client.library.list(project.id)).map((a) => a.path).find((p) => p.endsWith('/digest.md')) ?? null, { timeout: 15_000 })
      .toMatch(/^automations\/digest\/.+\/digest\.md$/);
    const [entry] = await client.automations.runs(automation!.id);
    expect(entry).toMatchObject({ kind: 'run', run: { number: 1, status: 'succeeded', inputs: { topic: 'robots' } } });

    expect(problems).toEqual([]);
    await context.close();
  }, 180_000);

  it('chooses a file input in the folder browser, and the run gets a copy of the file', async () => {
    const client = e2e.client();
    const { project } = await client.projects.create({ name: 'Briefs', goal: 'Check each brief' });
    const brief = join(e2e.home, 'Documents', 'brief.txt');
    writeFileSync(brief, 'The brief.\n');
    const { automation } = await client.automations.create(project.id, {
      name: 'check-brief',
      definition: {
        title: 'Check a brief',
        inputs: [{ key: 'brief', label: 'Brief', type: 'file', required: true }],
        steps: [{ id: 'ok', title: 'Looks right?', kind: 'ask', question: 'Is this the right brief?' }],
      },
    });
    const { context, page, problems } = await openAt(`#/p/${project.id}/automations/${automation.id}`);

    await page.getByRole('button', { name: 'Run now…' }).click();
    const runDialog = page.getByRole('dialog', { name: 'Run Check a brief' });
    await runDialog.getByRole('button', { name: 'Choose…' }).click();
    const files = page.getByRole('dialog', { name: 'Choose a file' });
    await files.getByRole('button', { name: 'Documents', exact: true }).click();
    await files.getByRole('button', { name: 'brief.txt', exact: true }).click();
    await expect.poll(() => files.getByLabel('Path', { exact: true }).inputValue()).toBe(brief);
    await e2e.shot(page, 'automations-file-input');
    await files.getByRole('button', { name: 'Choose this file' }).click();
    await files.waitFor({ state: 'detached' });
    await expect.poll(() => runDialog.getByLabel('Brief').inputValue()).toBe(brief);
    await runDialog.getByRole('button', { name: 'Run', exact: true }).click();

    await page.getByRole('heading', { name: 'Run #1' }).waitFor({ timeout: 10_000 });
    await page.locator('.auto-run-head').getByText('waiting on you · Ask me').waitFor({ timeout: 30_000 });
    const [entry] = await client.automations.runs(automation.id);
    expect(entry?.kind).toBe('run');
    const given = entry?.kind === 'run' ? entry.run.inputs['brief'] : undefined;
    expect(typeof given).toBe('string');
    expect(readFileSync(String(given), 'utf8')).toBe('The brief.\n');

    expect(problems).toEqual([]);
    await context.close();
  }, 120_000);
});
```

deskd copies a file input into the run folder (`prepareRunFolder` in `packages/core/src/automations/folders.ts`). The last check reads the path the run records, so it holds whether that is the copy or the original. The folder browser is a `Sheet` appended to `document.body` when it opens, so it sits above the Run dialog.

- [ ] **Step 3: Run it**

Run: `pnpm exec vitest run --config vitest.web-e2e.config.ts apps/web-ui/e2e/automations.e2e.test.ts`
Expected: PASS, 2 tests. It is the only Chromium running; wait for any other e2e run to end first.

If a locator in the first case finds nothing, compare the web component's text and roles with the React one it ports. Fix the component, not the test: the test is the desktop's, word for word.

- [ ] **Step 4: Look at the screenshots**

Run: `mkdir -p /tmp/claude-501/shots21 && DESK_E2E_SHOTS=/tmp/claude-501/shots21 pnpm exec vitest run --config vitest.web-e2e.config.ts apps/web-ui/e2e/automations.e2e.test.ts`
Then read `/tmp/claude-501/shots21/web-automations-design.png`, `web-automations-waiting.png`, `web-automations-succeeded.png` and `web-automations-file-input.png`.
- The canvas shows the Start pill, the Agent and Ask me nodes, and a curved edge between them, on the dotted background.
- The controls sit bottom left, and the inspector on the right.
- The run's nodes are lit as the desktop's are.

Fix what differs from the desktop's look in `automations.css`'s `.desk-flow` block, or in the component.

- [ ] **Step 5: Commit**

```bash
pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1 && git add apps/web-ui/e2e/automations.e2e.test.ts && git commit -m "test(web): automations end to end in the browser; a file input from the folder browser

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: Exit check

**Files:**
- Modify: `docs/superpowers/plans/2026-09-27-plan-21-automations-web.md` (append "Deviations found while executing")

- [ ] **Step 1: The whole suite**

Run: `df -h / | tail -1` (stop under 500 MB), then `pnpm typecheck && pnpm test > /tmp/claude-501/t21.log 2>&1; echo exit $?; tail -30 /tmp/claude-501/t21.log`
Expected: `exit 0`. `pnpm test` includes `ng test` for every web spec, including the parity guard, the security scan and `tokens.test.ts`.

- [ ] **Step 2: Every web e2e file**

Run: `pnpm test:web-e2e`
Expected: PASS: `smoke`, `flows`, `knowledge`, `catalog`, `system`, `automations`, and `built-ui`. It builds the web UI first. It is the only Chromium running.

- [ ] **Step 3: What must be gone**

Run: `git grep -n "PLAN_21_OPS\|AutomationsPlaceholder\|automations-placeholder\|Plan 21 ports" -- . ':!docs/superpowers/plans'`
Expected: no output.

Run: `git grep -n "@xyflow" apps/web-ui packages/ui-core packages/ui-styles`
Expected: no output. The web canvas adds no dependency, and `apps/web-ui/package.json` is unchanged by this plan (`git diff master --stat -- apps/web-ui/package.json pnpm-lock.yaml` prints nothing).

- [ ] **Step 4: Record deviations**

Append to this plan:

```md
## Deviations found while executing

- <one line per place where the code had to differ from this plan, and why; "None." if there were none>
```

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-09-27-plan-21-automations-web.md && git commit -m "docs: Plan 21's deviations

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Nothing merges to master until the user says so. Offer the finishing-a-development-branch options.

## Deviations found while executing

- Task 4: `.desk-flow` also sets `background-color: var(--xy-background-color)`: React Flow's style.css paints the canvas ground, and the plan's block left it out.
- Task 4: `@testing-library/angular`'s `rerender` unsubscribes every output its `on` leaves out, so specs that re-render and then expect outputs pass `on` again (graph-canvas.spec's delete case).
- Task 15: `attention/automations.spec.ts`'s Routed shows AttentionScreen only while the route is #/attention, as App does (and as `attention-screen.spec.ts`'s Routed does). The desktop test's Routed renders it on every route, which works there only because React reads the hash change after the assertion; the web route is a signal read at once, so the screen put its item back in the route after E.
- Task 15: `describeArgs` moved from `attention/inspector.ts` to `attention/describe-args.ts` (the Inspector re-exports it). The Inspector now imports the automation cards, which import the run step panel, which imported `describeArgs` from the Inspector: an import cycle React tolerates (components are read at render) but Angular does not (`imports: [...]` is read when the class is defined), and the full `ng test` run failed with "Cannot read properties of undefined (reading 'ɵcmp')".
- Task 17: the file-input case waits for "waiting on you · Looks right?", not "· Ask me": the run's status names the waiting step, whose title there is "Looks right?".
- Exit check: typecheck, 252 unit and integration files (1639 tests) and 107 web spec files (585 tests) green; `pnpm test:web-e2e` green on all 7 files (18 tests). jsdom had `PointerEvent`, so the canvas spec needed no polyfill.
