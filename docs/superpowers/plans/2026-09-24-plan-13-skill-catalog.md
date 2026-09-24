# Plan 13 · Skill catalog: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a curated catalog of 20 Agent Skills that the user reviews and installs from the desktop app (or the CLI). Each skill is pinned to a commit and a digest, and gets a Desk-managed Python/Node runtime.

**Architecture:**
- `packages/core/src/catalog/` holds the catalog file and these modules:
  - `tar.ts`: streaming extraction of a subtree from a `.tar.gz`, with caps
  - `digest.ts`: the normalised tree digest
  - `review.ts`: the warning scan
  - `service.ts`: `CatalogService` (list with states, prepare, install)
  - `runtimes.ts`: `SkillRuntimes` (per-skill Python via uv, Node via a lockfile installer and a node shim)
- Install goes through the existing `Runtime.saveSkill`. The catalog origin is `catalog:<id>@<sha>`, so history, restore and compare work unchanged. Install state is derived from the `skill.saved` log, and runtime state from `skill.runtime_changed` events. No new tables.
- The daemon mounts `/v1/catalog` and the runtime routes. The client, desktop IPC, the Skills "Catalog" view and the CLI build on them.

**Tech Stack:** Node `zlib` and `crypto` (no tar dependency: a small streaming ustar/pax reader), uv (bundled), the npm registry over `fetch` with SRI integrity, zod, Hono, React. Tests use Vitest with local HTTP fixtures (a tarball server and an npm-registry stub) and a uv stub.

**Spec:** `docs/superpowers/specs/2026-09-24-skill-catalog-design.md`

## Global Constraints

- `pnpm test` and `pnpm typecheck` must pass before every commit. `pnpm test:e2e` must pass at the end of the plan.
- **Pinning:** every GitHub entry has a full 40-hex commit SHA and a `sha256:` digest. An install whose digest doesn't match aborts, and nothing is staged.
- **Caps**, the same as the skill store:
  - download ≤ 50 MB
  - extracted ≤ 10 MB
  - ≤ 200 files
  - ≤ 2 MB per file
  - Symlinks, hardlinks, devices, absolute paths and `..` segments are rejected.
- **Runtimes:**
  - `--only-binary :all:`, exact `==` pins, and `--exclude-newer <catalog.updated>`.
  - Node tarballs are checked against their SRI integrity, and **lifecycle scripts never run**.
  - Nothing is installed system-wide.
- **Trust boundary unchanged:**
  - Only the user installs.
  - Scripts run only through `skill_run` and `bash` inside `sandbox-exec`.
  - `<data>/runtimes` is read-only to agents.
  - `` !`cmd` `` blocks are never executed.
- **Secrets:** no credentials are sent anywhere. GitHub, npm and PyPI are all fetched anonymously.
- **Licences:** only the entries in spec §2. Proprietary or unlicensed skills are never fetched by the product.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Deviation from the spec, decided while planning

- **Install state comes from the event log, not from frontmatter.** `SkillStore.save` re-serialises SKILL.md, so a digest of the installed tree never equals the catalog digest, and writing `metadata.desk-catalog` into the file would be circular. Instead, the latest `skill.saved` for the skill decides the state:
  - Origin `catalog:<id>@<sha>` → `installed`, or `update_available` if the SHA differs from the entry's.
  - An earlier catalog version followed by a non-catalog save → `modified`.
  - No catalog versions → `name_taken`.
  - No skill → `not_installed`.
- **The origin carries the full SHA.** The UI shows 7 characters.

## File map

| File | Responsibility |
|---|---|
| `packages/protocol/src/catalog.ts` | `CatalogEntry`, `CatalogFile`, `CatalogReview`, `CatalogItem` (entry and states), `CatalogInstallRequest`, `RuntimeState`, `RuntimesReport` |
| `packages/protocol/src/events.ts` | `skill.runtime_changed` (stored); ephemeral `skill.runtime_progress` |
| `packages/core/src/catalog/tar.ts` | `extractSubtree(stream, prefix, caps)`: streaming gunzip and ustar/pax/GNU reader with the rejections |
| `packages/core/src/catalog/digest.ts` | `treeDigest(files)` / `dirDigest(dir)`: `sha256` over sorted `path\0size\0sha256\n` records |
| `packages/core/src/catalog/review.ts` | `scanSkill(files)`, returning `ReviewWarning[]` |
| `packages/core/src/catalog/service.ts` | `CatalogService`: `list()`, `prepare(id)`, `install(id, opts)`, with state derivation |
| `packages/core/src/catalog/runtimes.ts` | `SkillRuntimes`: `setup`, `retry`, `remove`, `binDir`, `report`, `cleanup`; uv and npm-lock installers; node shim |
| `packages/core/src/catalog/catalog.json` | the 20 pinned entries |
| `catalog/skills/word-documents/`, `catalog/skills/pdf-toolkit/` | the first-party skills |
| `apps/daemon/src/routes/catalog.ts` | `/v1/catalog`, prepare, install; runtime retry; `/v1/system/runtimes` |
| `packages/client/src/client.ts` | `catalog.*`, `skills.runtimeRetry`, `system.runtimes*` |
| `scripts/catalog/pin.ts`, `scripts/catalog/check.ts` | curation tooling (`pnpm catalog:pin`, `pnpm catalog:check`) |
| `apps/desktop/src/renderer/skills/catalog/*` | Catalog view, cards, review sheet, runtime line |
| `apps/cli/src/commands/catalog.ts` | `desk catalog`, `show`, `install` |
| `apps/desktop/scripts/package.mjs` | bundle uv and `catalog/skills` into Resources |

## Tasks

The Task 1 and 2 listings are written in full below. Tasks 3–6 are specified by their interfaces and tests here; their full listings are appended from the shipped code as each lands, as in Plans 10 and 11.

### Task 1: Protocol schemas, tar subtree extraction, digest, review scan

**Files:** Create `packages/protocol/src/catalog.ts`, `packages/core/src/catalog/{tar,digest,review}.ts` and their tests. Modify `packages/protocol/src/{index,events}.ts`.

**Interfaces (produces):**

```ts
// protocol/catalog.ts
export const CatalogCategory = z.enum(['research', 'documents', 'writing', 'planning', 'code']);
export const CatalogSource = z.discriminatedUnion('type', [
  z.object({ type: z.literal('github'), repo: z.string().regex(/^[\w.-]+\/[\w.-]+$/), path: z.string(), sha: z.string().regex(/^[0-9a-f]{40}$/) }),
  z.object({ type: z.literal('builtin'), path: z.string() }),
]);
export const NodeLockEntry = z.object({ name: z.string(), version: z.string(), integrity: z.string().regex(/^sha(256|384|512)-/), path: z.string() });
export const CatalogRuntime = z.object({
  python: z.object({ version: z.string(), packages: z.array(z.string().regex(/^[A-Za-z0-9._\-\[\],]+==[^\s=]+$/)) }).optional(),
  node: z.object({ lock: z.array(NodeLockEntry) }).optional(),
  extras: z.array(z.enum(['playwright-chromium'])).optional(),
});
export const CatalogEntry = z.object({ id: SkillName, title, category: CatalogCategory, summary: z.string().max(200), license, homepage, source: CatalogSource,
  digest: z.string().regex(/^sha256:[0-9a-f]{64}$/), files, bytes, runtime: CatalogRuntime.default({}), smoke: z.array(z.string()).optional(), caveats: z.array(z.string()).default([]) });
export const CatalogFile = z.object({ version: z.literal(1), updated: z.string().regex(/^\d{4}-\d{2}-\d{2}$/), entries: z.array(CatalogEntry) });
export const ReviewWarning = z.object({ file: z.string(), line: z.number().int(), kind: z.enum(['exec-block', 'pipe-to-shell', 'base64-blob', 'invisible-unicode', 'paste-site', 'memory-write']), excerpt: z.string() });
export const CatalogReview = z.object({ entry: CatalogEntry, source_url: z.string(), files: z.array(z.object({ path: z.string(), size: z.number(), script: z.boolean() })), skill_md: z.string(), license_text: z.string().nullable(), warnings: z.array(ReviewWarning) });
export const RuntimeState = z.enum(['none', 'preparing', 'ready', 'failed']);
export const CatalogInstallState = z.enum(['not_installed', 'installed', 'update_available', 'modified', 'name_taken']);
export const CatalogItem = CatalogEntry.extend({ installs: z.array(z.object({ scope: SkillScope, project_id: z.string().nullable(), state: CatalogInstallState, sha: z.string().nullable(), runtime: RuntimeState, runtime_reason: z.string().nullable() })) });
export const CatalogInstallRequest = z.object({ scope: SkillScope.default('global'), project_id: z.string().optional(), replace_modified: z.boolean().default(false) });
```

```ts
// core/catalog/tar.ts
export type ExtractCaps = { maxDownload: number; maxBytes: number; maxFiles: number; maxFileBytes: number };
export const DEFAULT_CAPS: ExtractCaps; // 50 MB, 10 MB, 200, 2 MB
export type ExtractedFile = { path: string; content: Buffer; mode: number };
/** Streams a .tar.gz and returns the regular files under `prefix` (relative to it). Throws ValidationError on any cap or unsafe entry under the prefix. */
export function extractSubtree(input: AsyncIterable<Uint8Array>, prefix: string, caps?: Partial<ExtractCaps>): Promise<ExtractedFile[]>;
// core/catalog/digest.ts
export function treeDigest(files: Array<{ path: string; content: Buffer }>): string; // 'sha256:<hex>'
export function readTree(dir: string): Array<{ path: string; content: Buffer; mode: number }>;
// core/catalog/review.ts
export function scanSkill(files: Array<{ path: string; content: Buffer }>): ReviewWarning[];
export function isScript(path: string, mode: number): boolean;
```

**Tests:**
- **`tar.test.ts`:** builds `.tar.gz` fixtures in memory with a small ustar writer in the test file. Covers:
  - the files under a prefix, with pax long paths and GNU `L` names
  - other prefixes ignored
  - a symlink, a hardlink or `..` under the prefix rejected
  - an absolute path rejected
  - each cap
  - a truncated archive rejected
- **`digest.test.ts`:** the digest is order-independent and changes with content or path.
- **`review.test.ts`:** each warning kind is caught on its line; clean Markdown and code produce no warnings.

**Steps:** write the tests, confirm they fail, implement, confirm `pnpm vitest run packages/core/src/catalog packages/protocol` passes, run typecheck, commit `feat(core): catalog schemas, safe tar.gz subtree extraction, tree digest, review scan`.

### Task 2: CatalogService (list, prepare, install), routes, client, API docs

**Interfaces (produces):**

```ts
export type CatalogFetch = (url: string) => Promise<AsyncIterable<Uint8Array>>; // throws on non-2xx; retries 429/5xx with jitter
export class CatalogService {
  constructor(o: { runtime: Runtime; store: EventStore; dataDir: string; catalog: CatalogFile; builtinRoot: string; fetch?: CatalogFetch; onInstalled?: (s: { scope; name; projectId?; entry }) => void });
  list(): CatalogItem[];
  prepare(id: string): Promise<CatalogReview>;           // stages <data>/catalog/staging/<id>@<sha7>
  install(id: string, req: CatalogInstallRequest): Promise<{ skill: { name; version; scope }; state: CatalogInstallState }>;
}
export function loadCatalog(path?: string): CatalogFile;   // the bundled catalog.json by default
```

- **Rules:**
  - `install` calls `prepare` when nothing is staged.
  - It throws `ConflictError('name_taken')` when a non-catalog skill has the name.
  - It throws `ConflictError('catalog_modified')` when the skill is `modified` and `replace_modified` isn't set.
  - It saves with `{origin: 'catalog:<id>@<sha>', changeNote: 'Installed from the catalog (<repo>@<sha7>)'}` (or 'Updated from…'), then calls `onInstalled`.
- **Routes:** `GET /v1/catalog`, `POST /v1/catalog/:id/prepare`, `POST /v1/catalog/:id/install`. Client: `catalog.list()`, `catalog.prepare(id)`, `catalog.install(id, req)`.
- **Tests:**
  - a local HTTP server serves fixture tarballs for github-style `/<repo>/tar.gz/<sha>`
  - a builtin fixture
  - digest mismatch
  - the state transitions: not_installed → installed → (catalog SHA bumped) update_available → (user edit) modified → install with replace → installed; name_taken
  - project scope
  - routes end to end

### Task 3: Runtimes

**Interfaces (produces):**

```ts
export class SkillRuntimes {
  constructor(o: { dataDir: string; store: EventStore; uv?: string | null; nodeExec: { command: string; env: Record<string, string> }; fetch?: typeof fetch; exec?: (cmd, args, opts) => Promise<{ code; stdout; stderr }>; registry?: string });
  setup(ref: { scope; name; projectId? }, runtime: CatalogRuntime, updated: string): Promise<void>; // emits skill.runtime_changed + skill.runtime_progress
  retry(ref): Promise<void>;
  remove(ref): void;
  binDir(ref): string | null;          // only when ready
  state(ref): { state: RuntimeState; reason: string | null };
  report(): { bytes: number; envs: Array<{ scope; project_id: string | null; name: string; bytes: number; orphan: boolean }> };
  cleanup(): { removed: number; bytes: number };
}
```

- **Wiring:** `CatalogService.onInstalled` calls `runtimes.setup`. `Runtime.deleteSkill` calls `runtimes.remove`. `RuntimeServices.skillBinDirs(agentId)` feeds PATH in `bash` and `skill_run`. `skill_run` throws "runtime is not ready" when the skill's runtime is `failed` or `preparing`.
- **Routes:** `POST /v1/skills/:name/runtime/retry` (and the project variant), `GET /v1/system/runtimes`, `POST /v1/system/runtimes/cleanup`.
- **Desk's prompt** (`agent/prompts.ts`): the skill-authoring guidance gains "If a catalog skill fits, suggest it to the user by name; you cannot install it."
- **Tests:**
  - a uv stub script records its argv and creates `py/bin/python3`
  - an npm-registry stub serves tarballs with correct and wrong integrity
  - the states and reasons
  - PATH in `skill_run`
  - removal on delete
  - the report and cleanup of orphans

### Task 4: Curation — first-party skills, pin tooling, the 20 entries, sandbox check

- `catalog/skills/word-documents` (python-docx) and `catalog/skills/pdf-toolkit` (pypdf, pdfplumber, reportlab). Each has a SKILL.md, CLI scripts with `--help` and JSON output, and `references/`.
- `pnpm catalog:pin` and `pnpm catalog:check` as in spec §7. `catalog.json` gets all 20 entries. Any entry whose smoke test fails in the real sandbox is replaced by the next runner-up, and the swap is recorded in the spec's §2.
- **Tests:** the `check.ts` helpers are covered by unit tests against the fixture server. The real run is the curation step itself, and its output goes in the task notes.

### Task 5: Desktop Catalog view, review sheet, runtime line, ⌘K, System → Data; CLI

- **IPC:** `catalog.list`, `catalog.prepare`, `catalog.install`, `skills.runtimeRetry`, `system.runtimes`, `system.runtimesCleanup`.
- **Renderer:**
  - `skills/catalog/CatalogView.tsx`: bays and cards
  - `ReviewSheet.tsx`
  - the runtime line and "From catalog" chip in `SkillPanel`
  - catalog entries in ⌘K
  - the runtimes block in System → Data
- **CLI:** `desk catalog`, `desk catalog show <id>`, `desk catalog install <id> [-p] [--yes]`.
- **Tests:** component tests for the cards and states, the review sheet (files, warnings, scope, install, progress) and the retry; CLI command tests.

### Task 6: Packaging, e2e, live smoke, docs, self-review

- `package.mjs` downloads the pinned uv release (SHA-256 checked) into `Resources/bin/uv` with its licences, and copies `catalog/skills` into `Resources/catalog/skills`. The daemon finds both from `DESK_BUNDLED`.
- **e2e** (`catalog.e2e.test.ts`) against the fixture server: browse, review, install, "Ready", the chip, update, compare.
- **Live** (`catalog.live.test.ts`): install paper-lookup and pretty-mermaid from the real catalog and run their smoke commands.
- **Docs:** `docs/api.md`, `docs/desktop.md`, README, CLAUDE.md.
- Self-review against the spec.
