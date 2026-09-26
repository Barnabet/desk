# Built-in Skills Implementation Plan (Plan 18)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Desk's 12 first-party skills ship with the app as read-only, switchable built-in skills whose Python environments are built on first use, replacing their catalog entries.

**Architecture:**
- **Scope.** A third skill scope, `builtin`, is resolved last (project → global → builtin). The skills are read from the bundled `catalog/skills` (the repo's copy in dev) and verified against `packages/core/src/skills/builtins.json`.
- **Off switch.** The user turns a built-in off or on with a global event, `skill.builtin_toggled`, projected into `builtin_skill_settings`.
- **Runtimes.** `SkillRuntimes` builds a built-in's environment lazily. Building starts when an agent activates the skill, or at `skill_run`, which waits up to 5 minutes. Environments are keyed by a hash of what gets installed.
- **Adoption.** At start, unmodified catalog copies of these skills are removed so the built-ins take over.
- **Clients.** The daemon exposes `/v1/builtin-skills`; the desktop app shows a "Built into Desk" group.

**Tech Stack:**
- TypeScript (strict, ESM, tsx loader), zod, drizzle (SQLite), Hono
- Vitest with the core harness and fake model
- Electron + React for the desktop, Playwright for Electron e2e
- uv-managed Python 3.12 runtimes

**Spec:** `docs/superpowers/specs/2026-09-26-builtin-skills-design.md`. Read it first; this plan implements it.

## Global Constraints

- Built-in skills are the 12 in `catalog/skills`: `file-inspector`, `word-documents`, `pdf-toolkit`, `spreadsheets`, `presentations`, `images`, `audio-video`, `data-files`, `archives`, `markup-ebooks`, `email-calendar`, `web-research`.
- Precedence is `project` → `global` → `builtin`. A same-named user skill always wins.
- Built-ins are read-only:
  - Store write paths (`save`, `delete`, `restore`, `import`) refuse `scope: 'builtin'` with `ValidationError`.
  - Agents cannot write the built-in folder: it is added to the Runtime `readOnly` list.
- Turning a built-in off hides it from agents: it is not listed, not resolvable, not activatable, and `skill_run` refuses it. It stays in API listings with `enabled: false`.
- A broken built-in (digest mismatch or unreadable) is hidden from agents the same way. API listings show it with `broken: "<reason>"`.
- Runtimes are lazy:
  - `ensure` starts a build only when the state is `none` (missing or stale). `failed` stays failed until Retry.
  - `skill_run` waits for at most `BUILTIN_RUNTIME_WAIT_MS = 5 * 60_000`, honouring the tool's abort signal.
- A global or project skill named like a built-in, with no Desk-managed environment of its own, uses the built-in's environment.
- `GET /v1/skills` and `GET /v1/projects/:id/skills` are unchanged (user skills only). Built-ins are served only under `/v1/builtin-skills`.
- The catalog keeps the 18 third-party entries. `CatalogCategory` keeps `files` in its enum.
- The protocol gains `WritableSkillScope = z.enum(['project', 'global'])`, used wherever a skill is written.
- Never squash or edit existing migrations. Add one with `cd packages/core && npx drizzle-kit generate --name builtin_skill_settings`.
- `pnpm typecheck` and `pnpm test` must pass before every commit.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The checkout at `~/desk` may hold another session's uncommitted work. Stage only this task's files by path; never `git add -A`.
- The Mac has 8 GB of RAM:
  - Run the full suite once per task at most.
  - Run `catalog:check --builtins` only in Task 12, one skill at a time (the script is sequential).

---

## File Structure

| File | Responsibility |
|---|---|
| `packages/protocol/src/domain.ts` | `SkillScope` gains `builtin`; new `WritableSkillScope` |
| `packages/protocol/src/builtins.ts` (new) | `BuiltinEntry`, `BuiltinsFile`, `BuiltinSkillInfo`, `BuiltinToggleRequest`, `BuiltinDuplicateRequest` |
| `packages/protocol/src/events.ts` | `skill.builtin_toggled` event |
| `packages/protocol/src/catalog.ts` | install scopes use `WritableSkillScope` |
| `packages/core/src/db/schema.ts`, `drizzle/0005_*.sql` | `builtin_skill_settings` table |
| `packages/core/src/events/projections.ts` | projects the toggle |
| `packages/core/src/skills/builtins.ts` (new) | manifest loading, verification, enabled state, runtime spec |
| `packages/core/src/skills/builtins.json` (new) | the manifest |
| `packages/core/src/skills/store.ts` | the `builtin` scope in resolution and listing; read-only guard |
| `packages/core/src/catalog/runtimes.ts` | `RuntimeSpec`, builtin scope, `ensure`, `waitFor`, stale key, `recoverInterrupted` |
| `packages/core/src/runtime/runtime.ts` | built-in facade methods, activation ensure, `prepareSkillRuntime`, `adoptBuiltins` |
| `packages/core/src/tools/skills.ts`, `tools/types.ts`, `agent/prompts.ts`, `testing/context.ts` | agent-facing behaviour |
| `packages/core/scripts/catalog.ts`, `package.json` | `builtins:pin`, `catalog:check --builtins` |
| `catalog/skills/<id>/packages.txt` (new, ×12) | each skill's pinned Python packages |
| `packages/core/src/catalog/catalog.json` | the 12 entries removed |
| `apps/daemon/src/daemon.ts`, `routes/builtins.ts` (new), `app.ts` | wiring, routes, adoption and recovery at start |
| `packages/client/src/client.ts`, `apps/cli/src/commands.ts` | client and CLI |
| `apps/desktop/src/shared/ipc.ts`, `main/handlers.ts`, `renderer/skills/builtins/*` (new), `SkillsScreen.tsx`, `catalog/data.ts`, `components/CommandPalette.tsx` | desktop |
| `docs/api.md`, `docs/desktop.md`, `CLAUDE.md` | docs |

---

### Task 1: Protocol: scopes, built-in schemas and the toggle event

**Files:**
- Modify: `packages/protocol/src/domain.ts:98-99`
- Create: `packages/protocol/src/builtins.ts`
- Modify: `packages/protocol/src/index.ts` (add the export)
- Modify: `packages/protocol/src/events.ts` (after `skill.runtime_changed`, around line 95)
- Modify: `packages/protocol/src/catalog.ts:126,141,149` (`CatalogInstall.scope`, `CatalogInstallRequest.scope`, `CatalogInstallResult.skill.scope`)
- Modify: `packages/core/src/tools/skills.ts:165,196` (the `skill_write` and `skill_delete` inputs)
- Test: `packages/protocol/src/builtins.test.ts`

**Interfaces:**
- Produces:
  - `SkillScope` (`'project' | 'global' | 'builtin'`) and `WritableSkillScope` (`'project' | 'global'`)
  - `BuiltinEntry`, `BuiltinsFile`, `BuiltinSkillInfo`, `BuiltinToggleRequest`, `BuiltinDuplicateRequest` (zod schemas and types)
  - the event `skill.builtin_toggled` with payload `{ name: SkillName; enabled: boolean }`

- [ ] **Step 1: Write the failing test**

```ts
// packages/protocol/src/builtins.test.ts
import { describe, expect, it } from 'vitest';
import { BuiltinDuplicateRequest, BuiltinsFile, BuiltinSkillInfo, SkillScope, WritableSkillScope, EventInput } from './index';

const entry = {
  name: 'pdf-toolkit',
  title: 'PDF toolkit',
  summary: 'Read and write PDFs. Written by Desk.',
  caveats: [],
  digest: `sha256:${'a'.repeat(64)}`,
  files: 3,
  bytes: 100,
  scripts: 2,
  runtime: { python: { version: '3.12', packages: ['pypdf==6.19.0'] } },
  smoke: ['python3', 'scripts/selftest.py'],
};

describe('built-in skill schemas', () => {
  it('adds the builtin scope, but only project and global are writable', () => {
    expect(SkillScope.parse('builtin')).toBe('builtin');
    expect(WritableSkillScope.safeParse('builtin').success).toBe(false);
  });

  it('parses a manifest', () => {
    const m = BuiltinsFile.parse({ version: 1, updated: '2026-09-26', skills: [entry] });
    expect(m.skills[0]!.runtime.python!.packages).toEqual(['pypdf==6.19.0']);
  });

  it('refuses a duplicate into a project without its id', () => {
    expect(BuiltinDuplicateRequest.safeParse({ scope: 'project' }).success).toBe(false);
    expect(BuiltinDuplicateRequest.parse({})).toEqual({ scope: 'global' });
  });

  it('describes a built-in for clients', () => {
    const info = BuiltinSkillInfo.parse({
      name: 'pdf-toolkit', title: 'PDF toolkit', summary: 's', caveats: [], description: 'd', scripts: 2,
      enabled: true, broken: null, shadowed_by: null, runtime: { state: 'none', reason: null },
    });
    expect(info.shadowed_by).toBeNull();
  });

  it('has a toggle event', () => {
    expect(EventInput.parse({ project_id: '_global', agent_id: null, type: 'skill.builtin_toggled', payload: { name: 'pdf-toolkit', enabled: false } }).type).toBe('skill.builtin_toggled');
  });
});
```

Before writing the test, check the name of the event-input schema exported from `events.ts` (`grep -n "export const .*Event" packages/protocol/src/events.ts`). If it is not `EventInput`, use the exported union that `EventStore.append` validates against.

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run packages/protocol/src/builtins.test.ts`
Expected: FAIL (the `./builtins` exports do not exist).

- [ ] **Step 3: Implement**

`packages/protocol/src/domain.ts`:

```ts
export const SkillScope = z.enum(['project', 'global', 'builtin']);
export type SkillScope = z.infer<typeof SkillScope>;
/** Scopes a skill can be written to; built-in skills ship with Desk and are read-only. */
export const WritableSkillScope = z.enum(['project', 'global']);
export type WritableSkillScope = z.infer<typeof WritableSkillScope>;
```

`packages/protocol/src/builtins.ts`:

```ts
import { z } from 'zod';
import { CatalogRuntime, RuntimeState } from './catalog';
import { SkillName, WritableSkillScope } from './domain';

/** One of Desk's own skills as its manifest records it (spec 2026-09-26-builtin-skills-design §1). */
export const BuiltinEntry = z.object({
  name: SkillName,
  title: z.string().min(1).max(80),
  summary: z.string().min(1).max(200),
  caveats: z.array(z.string()).default([]),
  digest: z.string().regex(/^sha256:[0-9a-f]{64}$/),
  files: z.number().int().min(1),
  bytes: z.number().int().min(1),
  scripts: z.number().int().min(0),
  runtime: CatalogRuntime.default({}),
  /** Command run by `catalog:check --builtins` inside the sandbox, from the skill directory. */
  smoke: z.array(z.string()).optional(),
});
export type BuiltinEntry = z.infer<typeof BuiltinEntry>;

export const BuiltinsFile = z.object({
  version: z.literal(1),
  /** Pin date; also the `--exclude-newer` bound for their Python packages. */
  updated: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  skills: z.array(BuiltinEntry),
});
export type BuiltinsFile = z.infer<typeof BuiltinsFile>;

/** A built-in skill as clients see it (GET /v1/builtin-skills). */
export const BuiltinSkillInfo = z.object({
  name: SkillName,
  title: z.string(),
  summary: z.string(),
  caveats: z.array(z.string()),
  description: z.string(),
  scripts: z.number().int(),
  enabled: z.boolean(),
  /** Why the shipped copy cannot be used (digest mismatch, unreadable), or null. */
  broken: z.string().nullable(),
  /** The user's own skill of the same name that agents use instead, if any. */
  shadowed_by: WritableSkillScope.nullable(),
  runtime: z.object({ state: RuntimeState, reason: z.string().nullable() }),
});
export type BuiltinSkillInfo = z.infer<typeof BuiltinSkillInfo>;

export const BuiltinToggleRequest = z.object({ enabled: z.boolean() });
export type BuiltinToggleRequest = z.infer<typeof BuiltinToggleRequest>;

export const BuiltinDuplicateRequest = z
  .object({ scope: WritableSkillScope.default('global'), project_id: z.string().min(1).optional() })
  .refine((r) => r.scope !== 'project' || !!r.project_id, { message: 'A project copy needs project_id', path: ['project_id'] });
export type BuiltinDuplicateRequest = z.input<typeof BuiltinDuplicateRequest>;
```

In `packages/protocol/src/index.ts`, add `export * from './builtins';` after `export * from './catalog';`.

In `packages/protocol/src/events.ts`, add `SkillName` to the imports if it is missing, and add after the `skill.runtime_changed` event:

```ts
  /** The user turned one of Desk's built-in skills off or on (global; project_id is GLOBAL_PROJECT_ID). */
  event('skill.builtin_toggled', z.object({ name: SkillName, enabled: z.boolean() })),
```

In `packages/protocol/src/catalog.ts`, import `WritableSkillScope` from `./domain` and replace `SkillScope` with `WritableSkillScope` at:
- `CatalogInstall.scope` (line 126)
- `CatalogInstallRequest.scope` (line 141, keeping `.default('global')`)
- `CatalogInstallResult.skill.scope` (line 149)

Leave `RuntimesReport.envs[].scope` as `SkillScope`, since built-in environments appear there.

In `packages/core/src/tools/skills.ts`, import `WritableSkillScope` from `@desk/protocol` and use it for the inputs:
- `skill_write`: `scope: WritableSkillScope.default('project')`
- `skill_delete`: `scope: WritableSkillScope`

- [ ] **Step 4: Run the tests and the typecheck**

Run: `npx vitest run packages/protocol/src/builtins.test.ts && pnpm typecheck`
Expected: PASS.

The typecheck may flag exhaustive checks on `SkillScope` elsewhere, for example a `Record<SkillScope, …>` or a switch. Fix each by handling `'builtin'` explicitly. Only `SkillStore.root` really needs behaviour; it is done in Task 4, so for now throw `new ValidationError('Built-in skills have no writable root')` there.

- [ ] **Step 5: Commit**

```bash
git add packages/protocol/src/domain.ts packages/protocol/src/builtins.ts packages/protocol/src/builtins.test.ts packages/protocol/src/index.ts packages/protocol/src/events.ts packages/protocol/src/catalog.ts packages/core/src/tools/skills.ts
git commit -m "feat(protocol): built-in skill scope, manifest and toggle event

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Also stage any file you touched to satisfy the typecheck in Step 4.

---

### Task 2: The on/off switch: table, migration and projection

**Files:**
- Modify: `packages/core/src/db/schema.ts` (append the table)
- Create: `packages/core/drizzle/0005_builtin_skill_settings.sql` (generated) plus the `drizzle/meta` updates
- Modify: `packages/core/src/events/projections.ts` (import the table; add a case before `default`)
- Create: `packages/core/src/skills/builtins.ts` (only `builtinEnabled` for now)
- Test: `packages/core/src/skills/builtins.test.ts`

**Interfaces:**
- Consumes: the `skill.builtin_toggled` event (Task 1).
- Produces:
  - `builtinSkillSettings` (drizzle table)
  - `builtinEnabled(db: Db, name: string): boolean` from `packages/core/src/skills/builtins.ts`, which is true when there is no row

- [ ] **Step 1: Write the failing test**

```ts
// packages/core/src/skills/builtins.test.ts
import { GLOBAL_PROJECT_ID } from '@desk/protocol';
import { describe, expect, it } from 'vitest';
import { openDb } from '../db/open';
import { EventStore } from '../events/store';
import { builtinEnabled } from './builtins';

describe('built-in skill switch', () => {
  it('is on by default and follows the latest toggle', () => {
    const { db, close } = openDb(':memory:');
    const store = new EventStore(db);
    expect(builtinEnabled(db, 'pdf-toolkit')).toBe(true);
    store.append({ project_id: GLOBAL_PROJECT_ID, agent_id: null, type: 'skill.builtin_toggled', payload: { name: 'pdf-toolkit', enabled: false } });
    expect(builtinEnabled(db, 'pdf-toolkit')).toBe(false);
    store.append({ project_id: GLOBAL_PROJECT_ID, agent_id: null, type: 'skill.builtin_toggled', payload: { name: 'pdf-toolkit', enabled: true } });
    expect(builtinEnabled(db, 'pdf-toolkit')).toBe(true);
    expect(builtinEnabled(db, 'images')).toBe(true);
    close();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run packages/core/src/skills/builtins.test.ts`
Expected: FAIL (`./builtins` is missing).

- [ ] **Step 3: Implement**

Append to `packages/core/src/db/schema.ts`:

```ts
/** The user's switch for each of Desk's built-in skills; no row means on. Projected from `skill.builtin_toggled`. */
export const builtinSkillSettings = sqliteTable('builtin_skill_settings', {
  name: text('name').primaryKey(),
  enabled: integer('enabled', { mode: 'boolean' }).notNull(),
  updated_at: text('updated_at').notNull(),
});
```

Generate the migration:

```bash
cd packages/core && npx drizzle-kit generate --name builtin_skill_settings
```

Check that `drizzle/0005_builtin_skill_settings.sql` only creates `builtin_skill_settings`.

In `packages/core/src/events/projections.ts`, add `builtinSkillSettings` to the schema import and this case before `default:`:

```ts
    case 'skill.builtin_toggled':
      tx.insert(builtinSkillSettings)
        .values({ name: ev.payload.name, enabled: ev.payload.enabled, updated_at: ev.ts })
        .onConflictDoUpdate({ target: builtinSkillSettings.name, set: { enabled: ev.payload.enabled, updated_at: ev.ts } })
        .run();
      return;
```

Create `packages/core/src/skills/builtins.ts`:

```ts
import { eq } from 'drizzle-orm';
import type { Db } from '../db/open';
import { builtinSkillSettings } from '../db/schema';

/** Whether the user left a built-in skill on (the default). */
export function builtinEnabled(db: Db, name: string): boolean {
  const row = db.select({ enabled: builtinSkillSettings.enabled }).from(builtinSkillSettings).where(eq(builtinSkillSettings.name, name)).get();
  return row?.enabled ?? true;
}
```

- [ ] **Step 4: Run the tests**

Run: `npx vitest run packages/core/src/skills/builtins.test.ts packages/core/src/db && pnpm typecheck`
Expected: PASS. Any migration test must also pass.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/db/schema.ts packages/core/drizzle packages/core/src/events/projections.ts packages/core/src/skills/builtins.ts packages/core/src/skills/builtins.test.ts
git commit -m "feat(core): the user's switch for built-in skills (event, table, migration)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Manifest, packages.txt, builtins:pin and the catalog trimmed to 18

**Files:**
- Create: `catalog/skills/<id>/packages.txt` for all 12 skills
- Create: `packages/core/src/skills/builtins.json`
- Modify: `packages/core/src/catalog/catalog.json` (remove the 12 builtin entries)
- Modify: `packages/core/scripts/catalog.ts` (the `builtins-pin` command)
- Modify: `package.json` (the `builtins:pin` script)
- Modify: `packages/core/src/skills/builtins.ts` (`BuiltinSkills`, `loadBuiltinsManifest`, `runtimeKey`)
- Modify: `packages/core/src/catalog/catalog.test.ts` (18 entries; drop the builtin digest check, which moves)
- Modify: `apps/daemon/src/daemon.test.ts:63` (`toBe(18)`)
- Test: `packages/core/src/skills/builtins.test.ts` (extend)

**Interfaces:**
- Consumes: `BuiltinsFile`, `BuiltinEntry` (Task 1); `readTree`, `treeDigest` (`catalog/digest.ts`); `isScript` (`catalog/review.ts`).
- Produces (all from `packages/core/src/skills/builtins.ts`):
  - `loadBuiltinsManifest(data?: unknown): BuiltinsFile`
  - `runtimeKey(rt: CatalogRuntime): string`
  - `type RuntimeSpec = { digest: string; runtime: CatalogRuntime }`
  - `class BuiltinSkills`, constructed with `{ root: string; manifest?: BuiltinsFile; enabled?: (name: string) => boolean }` and exposing:
    - `root: string` and `updated: string`
    - `names(): string[]` and `entry(name): BuiltinEntry | undefined`
    - `dir(name): string`
    - `verify(): void` and `broken(name): string | null`
    - `enabled(name): boolean` and `usable(name): boolean`
    - `runtimeSpec(name): RuntimeSpec`

- [ ] **Step 1: Create the manifest and packages.txt from the current catalog**

Run once from the repo root:

```bash
node --input-type=module -e "
import { readFileSync, writeFileSync } from 'node:fs';
const cat = JSON.parse(readFileSync('packages/core/src/catalog/catalog.json', 'utf8'));
const mine = cat.entries.filter((e) => e.source.type === 'builtin');
if (mine.length !== 12) throw new Error('expected 12 builtin entries, got ' + mine.length);
for (const e of mine) writeFileSync('catalog/skills/' + e.id + '/packages.txt', e.runtime.python.packages.join('\n') + '\n');
const skills = mine.map((e) => ({ name: e.id, title: e.title, summary: e.summary, caveats: e.caveats, digest: e.digest, files: e.files, bytes: e.bytes, scripts: e.scripts, runtime: e.runtime, smoke: e.smoke }));
writeFileSync('packages/core/src/skills/builtins.json', JSON.stringify({ version: 1, updated: cat.updated, skills }, null, 2) + '\n');
cat.entries = cat.entries.filter((e) => e.source.type !== 'builtin');
writeFileSync('packages/core/src/catalog/catalog.json', JSON.stringify(cat, null, 2) + '\n');
console.log('builtins', skills.length, 'catalog', cat.entries.length);
"
```

Expected output: `builtins 12 catalog 18`.

- [ ] **Step 2: Add `builtins-pin` to the curation script**

In `packages/core/scripts/catalog.ts`:
- Import `BuiltinsFile` from `@desk/protocol`.
- Add the constant `const BUILTINS = join(here, '..', 'src', 'skills', 'builtins.json');`.
- Add the command to the dispatcher: `else if (command === 'builtins-pin') builtinsPin();`, and put `builtins-pin` in the usage line.
- Add the function:

```ts
/** Rewrites builtins.json from catalog/skills: digests, counts and each skill's packages.txt (spec 2026-09-26 §5). */
function builtinsPin(): void {
  const raw = JSON.parse(readFileSync(BUILTINS, 'utf8')) as { version: 1; updated: string; skills: Array<Record<string, any>> };
  raw.updated = new Date().toISOString().slice(0, 10);
  for (const s of raw.skills.filter((x) => !ids.length || ids.includes(x.name))) {
    const files = readTree(join(BUILTIN, s.name));
    const junk = files.find((f) => /(^|\/)(__pycache__|\.DS_Store)(\/|$)|\.pyc$/.test(f.path));
    if (junk) throw new Error(`remove ${s.name}/${junk.path} first: it would become part of the built-in skill`);
    const pk = files.find((f) => f.path === 'packages.txt');
    const packages = pk ? pk.content.toString('utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#')) : [];
    s.runtime = { ...(s.runtime ?? {}), python: { version: s.runtime?.python?.version ?? '3.12', packages } };
    s.digest = treeDigest(files);
    s.files = files.length;
    s.bytes = files.reduce((n, f) => n + f.content.length, 0);
    s.scripts = files.filter((f) => isScript(f.path, f.mode, f.content)).length;
    console.log(`pinned ${s.name}: ${s.files} files · ${packages.length} packages`);
  }
  writeFileSync(BUILTINS, `${JSON.stringify(raw, null, 2)}\n`);
  BuiltinsFile.parse(raw);
}
```

In the root `package.json`, add next to `catalog:sync`:

```json
"builtins:pin": "node --import tsx packages/core/scripts/catalog.ts builtins-pin"
```

Run `pnpm builtins:pin`. Each skill now contains `packages.txt`, so every digest changes once.

- [ ] **Step 3: Write the failing tests**

Append to `packages/core/src/skills/builtins.test.ts`:

```ts
import { existsSync, readdirSync, readFileSync, writeFileSync, cpSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readTree, treeDigest } from '../catalog/digest';
import { loadCatalog } from '../catalog/service';
import { BuiltinSkills, loadBuiltinsManifest, runtimeKey } from './builtins';

const SKILLS = join(import.meta.dirname, '..', '..', '..', '..', 'catalog', 'skills');

describe('the built-in manifest', () => {
  const manifest = loadBuiltinsManifest();

  it('lists exactly the skills in catalog/skills', () => {
    const dirs = readdirSync(SKILLS).filter((d) => existsSync(join(SKILLS, d, 'SKILL.md'))).sort();
    expect(manifest.skills.map((s) => s.name).sort()).toEqual(dirs);
    expect(dirs).toHaveLength(12);
  });

  it('matches every tree (run pnpm builtins:pin after editing a skill)', () => {
    for (const s of manifest.skills) expect(treeDigest(readTree(join(SKILLS, s.name))), s.name).toBe(s.digest);
  });

  it("uses each skill's packages.txt", () => {
    for (const s of manifest.skills) {
      const lines = readFileSync(join(SKILLS, s.name, 'packages.txt'), 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#'));
      expect(s.runtime.python?.packages, s.name).toEqual(lines);
    }
  });

  it('is no longer in the catalog', () => {
    expect(loadCatalog().entries.some((e) => e.source.type === 'builtin')).toBe(false);
  });
});

describe('BuiltinSkills', () => {
  const manifest = loadBuiltinsManifest();

  it('verifies trees and hides tampered, broken or switched-off skills', () => {
    const root = mkdtempSync(join(tmpdir(), 'desk-builtins-'));
    cpSync(join(SKILLS, 'file-inspector'), join(root, 'file-inspector'), { recursive: true });
    cpSync(join(SKILLS, 'images'), join(root, 'images'), { recursive: true });
    writeFileSync(join(root, 'images', 'SKILL.md'), `${readFileSync(join(root, 'images', 'SKILL.md'), 'utf8')}\ntampered\n`);
    const off = new Set(['data-files']);
    const b = new BuiltinSkills({ root, manifest, enabled: (n) => !off.has(n) });
    b.verify();
    expect(b.broken('file-inspector')).toBeNull();
    expect(b.usable('file-inspector')).toBe(true);
    expect(b.broken('images')).toMatch(/does not match/);
    expect(b.usable('images')).toBe(false);
    expect(b.broken('pdf-toolkit')).toMatch(/cannot be read/); // not copied into root
    expect(b.enabled('data-files')).toBe(false);
    expect(b.usable('nope')).toBe(false);
  });

  it('keys runtimes by what gets installed, not by the scripts', () => {
    const b = new BuiltinSkills({ root: SKILLS, manifest });
    const spec = b.runtimeSpec('pdf-toolkit');
    expect(spec.digest).toBe(runtimeKey(manifest.skills.find((s) => s.name === 'pdf-toolkit')!.runtime));
    expect(spec.digest).not.toBe(manifest.skills.find((s) => s.name === 'pdf-toolkit')!.digest);
  });
});
```

- [ ] **Step 4: Run them to verify they fail**

Run: `npx vitest run packages/core/src/skills/builtins.test.ts`
Expected: FAIL (`BuiltinSkills` and `loadBuiltinsManifest` are missing).

- [ ] **Step 5: Implement `BuiltinSkills`**

Add to `packages/core/src/skills/builtins.ts`:

```ts
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { BuiltinsFile, type BuiltinEntry, type CatalogRuntime } from '@desk/protocol';
import { readTree, treeDigest } from '../catalog/digest';
import manifestData from './builtins.json' with { type: 'json' };

export function loadBuiltinsManifest(data: unknown = manifestData): BuiltinsFile {
  return BuiltinsFile.parse(data);
}

/** What a runtime is built from; `digest` identifies the build, so a different one means rebuild. */
export type RuntimeSpec = { digest: string; runtime: CatalogRuntime };

/** A built-in environment's key: what gets installed. A change to scripts alone keeps the environment. */
export function runtimeKey(rt: CatalogRuntime): string {
  const spec = JSON.stringify({ python: rt.python ?? null, node: rt.node?.lock ?? null, extras: rt.extras ?? [] });
  return `sha256:${createHash('sha256').update(spec).digest('hex')}`;
}

/**
 * Desk's own skills, shipped with the app in `root` (the bundled catalog/skills; the repo's in development) and
 * listed in builtins.json. Read-only; `verify()` checks each tree against its digest once, at start.
 */
export class BuiltinSkills {
  readonly root: string;
  private readonly manifest: BuiltinsFile;
  private readonly byName: Map<string, BuiltinEntry>;
  private readonly damage = new Map<string, string>();

  constructor(private readonly o: { root: string; manifest?: BuiltinsFile; enabled?: (name: string) => boolean }) {
    this.root = o.root;
    this.manifest = o.manifest ?? loadBuiltinsManifest();
    this.byName = new Map(this.manifest.skills.map((s) => [s.name, s]));
  }

  get updated(): string {
    return this.manifest.updated;
  }

  names(): string[] {
    return [...this.byName.keys()].sort();
  }

  entry(name: string): BuiltinEntry | undefined {
    return this.byName.get(name);
  }

  dir(name: string): string {
    return join(this.root, name);
  }

  verify(): void {
    this.damage.clear();
    for (const e of this.manifest.skills) {
      try {
        if (treeDigest(readTree(this.dir(e.name))) !== e.digest) {
          this.damage.set(e.name, `${e.name} does not match the copy Desk shipped with. Reinstall Desk (in development: pnpm builtins:pin).`);
        }
      } catch (err) {
        this.damage.set(e.name, `${e.name} cannot be read: ${(err as Error).message}`);
      }
    }
  }

  broken(name: string): string | null {
    return this.damage.get(name) ?? null;
  }

  enabled(name: string): boolean {
    return this.o.enabled?.(name) ?? true;
  }

  /** In the manifest, intact and switched on: what agents may see and use. */
  usable(name: string): boolean {
    return this.byName.has(name) && !this.damage.has(name) && this.enabled(name);
  }

  runtimeSpec(name: string): RuntimeSpec {
    const e = this.byName.get(name);
    if (!e) throw new Error(`Unknown built-in skill: ${name}`);
    return { digest: runtimeKey(e.runtime), runtime: e.runtime };
  }
}
```

Merge the `builtinEnabled` imports from Task 2 into this file's imports.

Export from `packages/core/src/index.ts`:

```ts
export { BuiltinSkills, builtinEnabled, loadBuiltinsManifest, runtimeKey, type RuntimeSpec } from './skills/builtins';
```

In `packages/core/src/catalog/catalog.test.ts`:
- Change the entry count to `toBe(18)`.
- Remove the builtin digest check (the one that reads `BUILTIN` / `catalog/skills`), which `builtins.test.ts` now covers.
- Keep the checks that still apply to third-party entries.

In `apps/daemon/src/daemon.test.ts:63`, change `toBe(30)` to `toBe(18)`.

- [ ] **Step 6: Run the tests**

Run: `npx vitest run packages/core/src/skills packages/core/src/catalog apps/daemon/src/daemon.test.ts && pnpm typecheck`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add catalog/skills/*/packages.txt packages/core/src/skills/builtins.ts packages/core/src/skills/builtins.json packages/core/src/skills/builtins.test.ts packages/core/src/catalog/catalog.json packages/core/src/catalog/catalog.test.ts packages/core/scripts/catalog.ts package.json packages/core/src/index.ts apps/daemon/src/daemon.test.ts
git commit -m "feat(core): the built-in skills manifest, builtins:pin, and a catalog of 18

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: SkillStore: the built-in scope

**Files:**
- Modify: `packages/core/src/skills/store.ts`
- Test: `packages/core/src/skills/store.builtin.test.ts`

**Interfaces:**
- Consumes: `BuiltinSkills` (Task 3).
- Produces:
  - `new SkillStore(dataDir, guard?, builtins?: BuiltinSkills)`
  - `list(projectId?: string, opts?: { builtins?: boolean }): SkillSummary[]`. Built-ins are included only with `builtins: true`, and only usable, unshadowed ones.
  - `resolve(name, projectId?)`, which falls back to a usable built-in
  - `listScope('builtin')`, which lists every manifest skill with `error` set when broken
  - `get('builtin', name)` for manifest names only
  - `save`, `delete` and `restore` throw `ValidationError` for `'builtin'`

- [ ] **Step 1: Write the failing test**

```ts
// packages/core/src/skills/store.builtin.test.ts
import { cpSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { BuiltinSkills, loadBuiltinsManifest } from './builtins';
import { SkillStore } from './store';

const SKILLS = join(import.meta.dirname, '..', '..', '..', '..', 'catalog', 'skills');

function setup(off: string[] = []) {
  const data = mkdtempSync(join(tmpdir(), 'desk-store-'));
  const b = new BuiltinSkills({ root: SKILLS, manifest: loadBuiltinsManifest(), enabled: (n) => !off.includes(n) });
  b.verify();
  return { data, store: new SkillStore(data, undefined, b) };
}

describe('SkillStore with built-in skills', () => {
  it('resolves project, then global, then built-in', () => {
    const { store } = setup();
    expect(store.resolve('pdf-toolkit', 'p1')?.scope).toBe('builtin');
    store.save({ scope: 'global', name: 'pdf-toolkit', description: 'mine', instructions: 'Do it my way.' });
    expect(store.resolve('pdf-toolkit', 'p1')?.scope).toBe('global');
    store.save({ scope: 'project', projectId: 'p1', name: 'pdf-toolkit', description: 'ours', instructions: 'Project way.' });
    expect(store.resolve('pdf-toolkit', 'p1')?.scope).toBe('project');
  });

  it('lists built-ins for agents only when asked, without shadowed or switched-off ones', () => {
    const { store } = setup(['images']);
    store.save({ scope: 'global', name: 'archives', description: 'mine', instructions: 'x' });
    expect(store.list().some((s) => s.scope === 'builtin')).toBe(false);
    const agentView = store.list(undefined, { builtins: true });
    expect(agentView.filter((s) => s.scope === 'builtin').map((s) => s.name)).not.toContain('images');
    expect(agentView.find((s) => s.name === 'archives')?.scope).toBe('global');
    expect(agentView.filter((s) => s.name === 'archives')).toHaveLength(1);
    expect(store.resolve('images')).toBeUndefined();
  });

  it('reads built-ins but never writes them', () => {
    const { store } = setup();
    const d = store.get('builtin', 'word-documents')!;
    expect(d.instructions.length).toBeGreaterThan(100);
    expect(d.dir).toBe(join(SKILLS, 'word-documents'));
    expect(store.get('builtin', 'not-shipped')).toBeUndefined();
    expect(() => store.save({ scope: 'builtin', name: 'word-documents', instructions: 'x' })).toThrow(/read-only/);
    expect(() => store.delete('builtin', 'word-documents')).toThrow(/read-only/);
    expect(() => store.restore('builtin', 'word-documents', 1)).toThrow(/read-only/);
    expect(store.listScope('builtin')).toHaveLength(12);
  });

  it('works unchanged without built-ins', () => {
    const store = new SkillStore(mkdtempSync(join(tmpdir(), 'desk-store-')));
    expect(store.resolve('pdf-toolkit')).toBeUndefined();
    expect(store.list(undefined, { builtins: true })).toEqual([]);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run packages/core/src/skills/store.builtin.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement in `packages/core/src/skills/store.ts`**

Constructor and helpers:

```ts
import type { BuiltinSkills } from './builtins';

  constructor(
    private readonly dataDir: string,
    private readonly guard?: SandboxGuard,
    /** Desk's own skills, resolved after project and global ones (read-only). */
    private readonly builtins?: BuiltinSkills,
  ) {}

  root(scope: SkillScope, projectId?: string): string {
    if (scope === 'builtin') {
      if (!this.builtins) throw new ValidationError('This daemon has no built-in skills');
      return this.builtins.root;
    }
    if (scope === 'global') return join(this.dataDir, 'skills');
    if (!projectId) throw new ValidationError('Project skills need a project id');
    return join(this.dataDir, 'projects', projectId, 'skills');
  }

  private writable(scope: SkillScope): void {
    if (scope === 'builtin') throw new ValidationError('Built-in skills are read-only. Duplicate one to make your own version.');
  }
```

Replace `summarize` so that a damaged built-in keeps its description and reports the damage in `error`:

```ts
  private summarize(scope: SkillScope, name: string, projectId?: string): SkillSummary {
    const dir = join(this.root(scope, projectId), name);
    const base = { name, scope, dir, version: this.currentVersion(scope, name, projectId) };
    const damage = scope === 'builtin' ? (this.builtins?.broken(name) ?? null) : null;
    try {
      const { frontmatter } = parseSkillMd(readFileSync(join(dir, SKILL_FILE), 'utf8'));
      const description = checkDescription(frontmatter.description);
      return damage ? { ...base, description, error: damage } : { ...base, description };
    } catch (e) {
      const error = damage ?? (existsSync(join(dir, SKILL_FILE)) ? (e as Error).message : 'SKILL.md is missing');
      return { ...base, description: '', error };
    }
  }
```

`listScope`, `list` and `resolve`:

```ts
  listScope(scope: SkillScope, projectId?: string): SkillSummary[] {
    if (scope === 'builtin') return this.builtins ? this.builtins.names().map((n) => this.summarize('builtin', n)) : [];
    // … existing body unchanged …
  }

  /**
   * Skills visible to a project (project skills shadow global ones), or only global skills without a project.
   * With `builtins`, as agents see them: plus Desk's usable built-in skills that no own skill shadows.
   */
  list(projectId?: string, opts: { builtins?: boolean } = {}): SkillSummary[] {
    const project = projectId ? this.listScope('project', projectId) : [];
    const own = new Set(project.map((s) => s.name));
    const mine = [...project, ...this.listScope('global').filter((s) => !own.has(s.name))];
    if (opts.builtins && this.builtins) {
      const taken = new Set(mine.map((s) => s.name));
      for (const n of this.builtins.names()) if (!taken.has(n) && this.builtins.usable(n)) mine.push(this.summarize('builtin', n));
    }
    return mine.sort((a, b) => a.name.localeCompare(b.name));
  }

  resolve(name: string, projectId?: string): SkillSummary | undefined {
    if (!SkillName.safeParse(name).success) return undefined;
    for (const scope of projectId ? (['project', 'global'] as const) : (['global'] as const)) {
      if (existsSync(join(this.root(scope, projectId), name))) return this.summarize(scope, name, projectId);
    }
    return this.builtins?.usable(name) ? this.summarize('builtin', name) : undefined;
  }
```

In `get`, after `checkSkillName(name)`, add: `if (scope === 'builtin' && !this.builtins?.entry(name)) return undefined;`.

Call `this.writable(...)` first in each write path:
- `save`: `this.writable(input.scope);`
- `delete`: `this.writable(scope);`
- `restore`: `this.writable(scope);`

- [ ] **Step 4: Run the tests**

Run: `npx vitest run packages/core/src/skills && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/skills/store.ts packages/core/src/skills/store.builtin.test.ts
git commit -m "feat(core): skills resolve project, then global, then built-in; built-ins are read-only

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: SkillRuntimes: built-in environments, ensure, waitFor, stale keys and crash recovery

**Files:**
- Modify: `packages/core/src/catalog/runtimes.ts`
- Modify: `packages/core/src/catalog/service.ts:32-35` (`CatalogRuntimes.setup` takes `RuntimeSpec`)
- Test: `packages/core/src/catalog/runtimes.test.ts` (extend; reuse its `uvStub` and `make` helpers)

**Interfaces:**
- Consumes: `RuntimeSpec`, `runtimeKey` (Task 3).
- Produces (on `SkillRuntimes` and `SkillEnvProvider`):
  - `setup(ref: SkillRef, spec: RuntimeSpec, updated: string): void`. A `CatalogEntry` is a `RuntimeSpec`.
  - `env(ref: SkillRef, expect?: string): SkillEnv` and `state(ref, expect?)`. A ready environment whose recorded digest differs from `expect` reports `none`.
  - `ensure(ref: SkillRef, spec: RuntimeSpec, updated: string): void`, which calls `setup` only when the state for `spec.digest` is `none`
  - `waitFor(ref: SkillRef, timeoutMs: number, signal?: AbortSignal): Promise<boolean>`, which is true when no setup is pending or it finished in time
  - `recoverInterrupted(): number`
  - Built-in environments live at `<data>/runtimes/builtin/_global/<name>`, and `report()` and `cleanup()` include them.

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/src/catalog/runtimes.test.ts`, reusing the file's existing `make`, `uvStub` and `entry` helpers. Read the top of the file first.

```ts
describe('built-in skill environments', () => {
  const bref = { scope: 'builtin' as const, name: 'pdf-toolkit' };
  const spec = (pkgs: string[]) => ({ digest: runtimeKey({ python: { version: '3.12', packages: pkgs } }), runtime: { python: { version: '3.12', packages: pkgs } } });

  it('builds once on ensure, then keeps it', async () => {
    const { uv, log } = uvStub();
    const r = make({ uv });
    r.ensure(bref, spec(['pypdf==6.19.0']), '2026-09-26');
    expect(r.state(bref).state).toBe('preparing');
    await r.settled(bref);
    expect(r.state(bref, spec(['pypdf==6.19.0']).digest).state).toBe('ready');
    r.ensure(bref, spec(['pypdf==6.19.0']), '2026-09-26');
    expect(readFileSync(log, 'utf8').trim().split('\n').filter((l) => l.startsWith('venv'))).toHaveLength(1);
    expect(r.dir(bref)).toMatch(/runtimes\/builtin\/_global\/pdf-toolkit$/);
  });

  it('rebuilds when the package list changes', async () => {
    const { uv } = uvStub();
    const r = make({ uv });
    r.ensure(bref, spec(['pypdf==6.19.0']), '2026-09-26');
    await r.settled(bref);
    const next = spec(['pypdf==6.20.0']);
    expect(r.state(bref, next.digest).state).toBe('none');
    r.ensure(bref, next, '2026-09-27');
    await r.settled(bref);
    expect(r.state(bref, next.digest).state).toBe('ready');
  });

  it('does not retry a failed build by itself', async () => {
    const r = make({ uv: null });
    r.ensure(bref, spec(['pypdf==6.19.0']), '2026-09-26');
    await r.settled(bref);
    expect(r.state(bref).state).toBe('failed');
    r.ensure(bref, spec(['pypdf==6.19.0']), '2026-09-26');
    expect(r.state(bref).state).toBe('failed');
  });

  it('waits for a pending build, up to a limit', async () => {
    const { uv } = uvStub({ delayMs: 300 });
    const r = make({ uv });
    expect(await r.waitFor(bref, 10)).toBe(true); // nothing pending
    r.ensure(bref, spec(['pypdf==6.19.0']), '2026-09-26');
    expect(await r.waitFor(bref, 20)).toBe(false);
    expect(await r.waitFor(bref, 5_000)).toBe(true);
    expect(r.state(bref).state).toBe('ready');
  });

  it('records setups cut short by a restart as removed', () => {
    const { uv } = uvStub();
    const first = make({ uv });
    first.ensure(bref, spec(['pypdf==6.19.0']), '2026-09-26'); // appends `preparing`
    const second = make({ uv, store: first.storeForTest }); // a new daemon over the same event log
    expect(second.recoverInterrupted()).toBe(1);
    expect(second.state(bref).state).toBe('none');
    expect(second.recoverInterrupted()).toBe(0);
  });

  it('reports built-in environments', async () => {
    const { uv } = uvStub();
    const r = make({ uv, exists: (ref) => ref.scope === 'builtin' });
    r.ensure(bref, spec(['pypdf==6.19.0']), '2026-09-26');
    await r.settled(bref);
    expect(r.report().envs).toContainEqual(expect.objectContaining({ scope: 'builtin', name: 'pdf-toolkit', orphan: false }));
  });
});
```

Adapt the helpers where needed:
- If `uvStub` has no `delayMs` option, add one: the stub script runs `sleep` for that long before writing its files.
- If `make` does not expose its store, give it an optional `store` parameter and return it as `storeForTest`, so the recovery test can build a second `SkillRuntimes` over the same `EventStore`.
- Import `runtimeKey` from `../skills/builtins`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run packages/core/src/catalog/runtimes.test.ts`
Expected: FAIL (`ensure`, `waitFor` and `recoverInterrupted` are missing).

- [ ] **Step 3: Implement in `packages/core/src/catalog/runtimes.ts`**

Replace `CatalogEntry` with `RuntimeSpec` in the signatures of `setup`, `build`, `writeCompatShims` and `runtimeNote`, and import it with `import type { RuntimeSpec } from '../skills/builtins';`. Each of these reads only `digest` and `runtime`. In `service.ts`, change `CatalogRuntimes.setup` to `setup(ref: SkillRef, entry: RuntimeSpec, updated: string): void`.

Update the provider interface:

```ts
/** What the agent runtime needs: environments of skills, built lazily for built-in ones, and removal. */
export interface SkillEnvProvider {
  /** `expect`: the digest the environment must have been built for; another one reads as `none` (stale). */
  env(ref: SkillRef, expect?: string): SkillEnv;
  remove(ref: SkillRef): void;
  /** Builds the environment when it is missing or stale; a failed one waits for an explicit retry. */
  ensure(ref: SkillRef, spec: RuntimeSpec, updated: string): void;
  /** Resolves true when no setup is pending or it finished within `timeoutMs`, false on timeout or abort. */
  waitFor(ref: SkillRef, timeoutMs: number, signal?: AbortSignal): Promise<boolean>;
}
```

Add these methods to `SkillRuntimes`:

```ts
  state(ref: SkillRef, expect?: string): { state: RuntimeState; reason: string | null } {
    const { state, reason } = this.current(ref, expect);
    return { state, reason };
  }

  env(ref: SkillRef, expect?: string): SkillEnv {
    const { state, reason, file } = this.current(ref, expect);
    if (!file) return { state, reason, bins: [], vars: {}, note: null };
    return { state, reason, bins: file.bins, vars: file.vars, note: file.note ?? null };
  }

  ensure(ref: SkillRef, spec: RuntimeSpec, updated: string): void {
    if (this.current(ref, spec.digest).state === 'none') this.setup(ref, spec, updated);
  }

  async waitFor(ref: SkillRef, timeoutMs: number, signal?: AbortSignal): Promise<boolean> {
    const job = this.pending.get(this.key(ref));
    if (!job) return true;
    let timer: NodeJS.Timeout | undefined;
    let onAbort: (() => void) | undefined;
    const stop = new Promise<false>((resolve) => {
      timer = setTimeout(() => resolve(false), timeoutMs);
      onAbort = () => resolve(false);
      signal?.addEventListener('abort', onAbort, { once: true });
    });
    try {
      return await Promise.race([job.then(() => true as const, () => true as const), stop]);
    } finally {
      clearTimeout(timer);
      if (onAbort) signal?.removeEventListener('abort', onAbort);
    }
  }

  /** At start: setups a crash or restart cut short are recorded as removed ("interrupted"), their folders deleted. */
  recoverInterrupted(): number {
    const last = new Map<string, { ref: SkillRef; state: string }>();
    for (const e of this.o.store.list({ types: ['skill.runtime_changed'] })) {
      if (e.type !== 'skill.runtime_changed') continue;
      const ref: SkillRef = { scope: e.payload.scope, name: e.payload.name, ...(e.payload.scope === 'project' ? { projectId: e.project_id } : {}) };
      last.set(this.key(ref), { ref, state: e.payload.state });
    }
    let n = 0;
    for (const [key, { ref, state }] of last) {
      if (state !== 'preparing' || this.pending.has(key)) continue;
      rmSync(this.dir(ref), { recursive: true, force: true });
      this.record(ref, 'removed', 'interrupted');
      n++;
    }
    return n;
  }
```

In `current(ref, expect?: string)`, after reading the env file (`if (!file) …`), add:

```ts
    if (expect && file.digest !== expect) return { state: 'none', reason: null, file: null };
```

In `listEnvs`, iterate `['global', 'project', 'builtin'] as SkillScope[]`.

`dir()` already maps non-project scopes to `_global`, so it needs no change.

Update the test doubles that implement `SkillEnvProvider`: run `grep -rn "SkillEnvProvider\|skillEnv:" packages apps --include=*.ts`. Each double must also provide `ensure(){}` and `waitFor: async () => true`.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run packages/core/src/catalog && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/catalog/runtimes.ts packages/core/src/catalog/runtimes.test.ts packages/core/src/catalog/service.ts
git commit -m "feat(core): lazy built-in environments: ensure, waitFor, stale keys, crash recovery

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Also add any test doubles you updated in Step 3.

---

### Task 6: Runtime facade: built-ins, activation, prepareSkillRuntime and adoption

**Files:**
- Modify: `packages/core/src/runtime/runtime.ts`: options (around line 105), constructor (around line 201), the services object (around line 205), the skills section (lines 640-798) and `spawnThread` (around line 559)
- Modify: `packages/core/src/tools/types.ts` (the `RuntimeServices` interface, near `skillEnv`)
- Modify: `packages/core/src/testing/context.ts` (the proxy default for `prepareSkillRuntime`)
- Test: `packages/core/src/runtime/builtins.test.ts`

**Interfaces:**
- Consumes: Tasks 2 to 5.
- Produces:
  - `RuntimeOptions.builtins?: { root: string; manifest?: BuiltinsFile }`, and `RuntimeOptions.builtinRuntimeWaitMs?: number` (tests only; the default is `BUILTIN_RUNTIME_WAIT_MS`)
  - `export const BUILTIN_RUNTIME_WAIT_MS = 5 * 60_000`
  - `runtime.builtins?: BuiltinSkills`
  - The built-in methods:
    - `listBuiltins(projectId?: string): BuiltinSkillInfo[]`
    - `getBuiltin(name: string): SkillDetail`
    - `setBuiltinEnabled(name: string, enabled: boolean): BuiltinSkillInfo`
    - `duplicateBuiltin(name: string, to: { scope: WritableSkillScope; projectId?: string })` returns the `saveSkill` result
    - `retryBuiltinRuntime(name: string): { state: RuntimeState; reason: string | null }`
  - `prepareSkillRuntime(agentId, skill: { scope: SkillScope; name: string }, signal?)` returns `Promise<{ waitedMs: number }>`. It is also on `RuntimeServices`.
  - `adoptBuiltins(): string[]`, the removed skills as `scope:projectId:name` keys

- [ ] **Step 1: Write the failing tests**

```ts
// packages/core/src/runtime/builtins.test.ts
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { GLOBAL_PROJECT_ID } from '@desk/protocol';
import { createHarness, newRuntime, seedThread, type Harness } from '../testing';
import type { SkillEnvProvider, SkillRef } from '../catalog/runtimes';

const SKILLS = join(import.meta.dirname, '..', '..', '..', '..', 'catalog', 'skills');

/** A provider that records ensures and lets tests decide when a build finishes. */
function fakeRuntimes() {
  const states = new Map<string, { state: 'none' | 'preparing' | 'ready' | 'failed'; digest?: string }>();
  const ensured: string[] = [];
  let finish: (() => void) | null = null;
  const key = (r: SkillRef) => `${r.scope}/${r.name}`;
  const p: SkillEnvProvider & { ensured: string[]; finish(): void; states: typeof states } = {
    ensured,
    states,
    finish: () => finish?.(),
    env: (ref, expect) => {
      const s = states.get(key(ref));
      const state = !s ? 'none' : expect && s.state === 'ready' && s.digest !== expect ? 'none' : s.state;
      return { state, reason: null, bins: state === 'ready' ? ['/rt/bin'] : [], vars: {}, note: null };
    },
    remove: (ref) => void states.delete(key(ref)),
    ensure: (ref, spec) => {
      if ((p.env(ref, spec.digest).state) !== 'none') return;
      ensured.push(key(ref));
      states.set(key(ref), { state: 'preparing', digest: spec.digest });
    },
    waitFor: (ref, ms) =>
      new Promise((resolve) => {
        const t = setTimeout(() => resolve(false), ms);
        finish = () => {
          clearTimeout(t);
          states.set(key(ref), { ...states.get(key(ref))!, state: 'ready' });
          resolve(true);
        };
      }),
  };
  return p;
}

describe('built-in skills in the runtime', () => {
  let h: Harness;
  beforeEach(async () => (h = await createHarness()));
  afterEach(async () => h.close());

  it('lists built-ins with their switch, shadowing and runtime', () => {
    const rt = newRuntime(h, { builtins: { root: SKILLS } });
    const pdf = () => rt.listBuiltins().find((b) => b.name === 'pdf-toolkit')!;
    expect(rt.listBuiltins()).toHaveLength(12);
    expect(pdf()).toMatchObject({ enabled: true, broken: null, shadowed_by: null, runtime: { state: 'none' } });
    rt.setBuiltinEnabled('pdf-toolkit', false);
    expect(pdf().enabled).toBe(false);
    expect(rt.skills.resolve('pdf-toolkit')).toBeUndefined();
    rt.setBuiltinEnabled('pdf-toolkit', true);
    rt.saveSkill({ scope: 'global', name: 'pdf-toolkit', description: 'mine', instructions: 'x' });
    expect(pdf().shadowed_by).toBe('global');
    expect(() => rt.setBuiltinEnabled('nope', false)).toThrow(/Unknown built-in/);
  });

  it('duplicates a built-in into an ordinary skill that shadows it', () => {
    const rt = newRuntime(h, { builtins: { root: SKILLS } });
    const r = rt.duplicateBuiltin('images', { scope: 'global' });
    expect(r.created).toBe(true);
    expect(rt.skills.resolve('images')?.scope).toBe('global');
    expect(rt.skillHistory('global', 'images')[0]?.origin).toMatch(/^builtin:images@[0-9a-f]{12}$/);
    expect(() => rt.duplicateBuiltin('images', { scope: 'global' })).toThrow(/already exists/);
    rt.deleteSkill('global', 'images');
    expect(rt.skills.resolve('images')?.scope).toBe('builtin');
  });

  it('starts the environment when an agent activates a built-in, and skill runs wait for it', async () => {
    const env = fakeRuntimes();
    const rt = newRuntime(h, { builtins: { root: SKILLS }, skillEnv: env, builtinRuntimeWaitMs: 2_000 });
    const t = await seedThread(h, rt);
    rt.activateSkills(t.id, ['pdf-toolkit']);
    expect(env.ensured).toEqual(['builtin/pdf-toolkit']);
    const waiting = rt.prepareSkillRuntime(t.id, { scope: 'builtin', name: 'pdf-toolkit' });
    setTimeout(() => env.finish(), 50);
    expect((await waiting).waitedMs).toBeGreaterThanOrEqual(40);
    expect(rt.skillEnv(t.id, { scope: 'builtin', name: 'pdf-toolkit' }).bins).toEqual(['/rt/bin']);
  });

  it('gives up waiting at the limit and says the environment is still setting up', async () => {
    const env = fakeRuntimes();
    const rt = newRuntime(h, { builtins: { root: SKILLS }, skillEnv: env, builtinRuntimeWaitMs: 30 });
    const t = await seedThread(h, rt);
    await rt.prepareSkillRuntime(t.id, { scope: 'builtin', name: 'images' });
    expect(rt.skillEnv(t.id, { scope: 'builtin', name: 'images' }).blocked).toMatch(/still setting up its Python environment \(first use only\)/);
  });

  it("gives a copy of a built-in the built-in's environment", async () => {
    const env = fakeRuntimes();
    const rt = newRuntime(h, { builtins: { root: SKILLS }, skillEnv: env, builtinRuntimeWaitMs: 2_000 });
    const t = await seedThread(h, rt);
    rt.duplicateBuiltin('archives', { scope: 'global' });
    rt.activateSkills(t.id, ['archives']);
    expect(env.ensured).toEqual(['builtin/archives']);
  });

  it('removes unmodified catalog copies at start and keeps edited ones', () => {
    const rt = newRuntime(h, { builtins: { root: SKILLS } });
    // What a catalog install recorded: saveSkill with a catalog origin.
    rt.saveSkill({ scope: 'global', name: 'pdf-toolkit', fromDir: join(SKILLS, 'pdf-toolkit') }, { origin: 'catalog:pdf-toolkit@builtin-0123456789ab' });
    rt.saveSkill({ scope: 'global', name: 'file-inspector', fromDir: join(SKILLS, 'file-inspector') }, { origin: 'catalog:file-inspector@builtin-0123456789ab' });
    rt.saveSkill({ scope: 'global', name: 'file-inspector', instructions: 'My changes.' }, { origin: 'user' });
    expect(rt.adoptBuiltins()).toEqual([`global::pdf-toolkit`]);
    expect(rt.skills.resolve('pdf-toolkit')?.scope).toBe('builtin');
    expect(rt.skills.resolve('file-inspector')?.scope).toBe('global');
    expect(rt.adoptBuiltins()).toEqual([]);
  });

  it('never shows a switched-off built-in to agents', async () => {
    const rt = newRuntime(h, { builtins: { root: SKILLS } });
    const t = await seedThread(h, rt);
    rt.setBuiltinEnabled('audio-video', false);
    expect(() => rt.activateSkills(t.id, ['audio-video'])).toThrow(/Unknown skill/);
  });
});
```

Adapt the harness helpers to their real signatures. Check `packages/core/src/testing/harness.ts`: `seedThread(h, …)` may take options rather than a runtime, and it returns ids. Use whatever it returns to get the thread's agent id, and keep the assertions unchanged.

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run packages/core/src/runtime/builtins.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement in `packages/core/src/runtime/runtime.ts`**

Options, constructor and services:

```ts
import { BuiltinSkills, builtinEnabled } from '../skills/builtins';
import type { BuiltinSkillInfo, BuiltinsFile, RuntimeState, WritableSkillScope } from '@desk/protocol';
import type { SkillRef } from '../catalog/service';

/** How long a skill run waits for a built-in skill's first-use environment before asking the agent to retry. */
export const BUILTIN_RUNTIME_WAIT_MS = 5 * 60_000;

// in RuntimeOptions:
  /** Desk's own skills: the folder they ship in (the bundled catalog/skills) and, in tests, another manifest. */
  builtins?: { root: string; manifest?: BuiltinsFile };
  /** How long skill_run waits for a built-in's environment (default BUILTIN_RUNTIME_WAIT_MS; tests shorten it). */
  builtinRuntimeWaitMs?: number;

// class field:
  readonly builtins?: BuiltinSkills;

// constructor, replacing `this.skills = new SkillStore(o.dataDir, this.guard);`:
    if (o.builtins) {
      this.builtins = new BuiltinSkills({ root: o.builtins.root, ...(o.builtins.manifest ? { manifest: o.builtins.manifest } : {}), enabled: (n) => builtinEnabled(o.store.db, n) });
      this.builtins.verify();
    }
    this.skills = new SkillStore(o.dataDir, this.guard, this.builtins);

// services object, next to skillEnv:
      prepareSkillRuntime: (agentId, skill, signal) => this.prepareSkillRuntime(agentId, skill, signal),
```

Methods, added in the skills section:

```ts
  // ── built-in skills (spec 2026-09-26-builtin-skills-design) ─────────────

  private requireBuiltin(name: string): BuiltinSkills {
    if (!this.builtins?.entry(name)) throw new NotFoundError(`Unknown built-in skill: ${name}`);
    return this.builtins;
  }

  /** Desk's built-in skills with their switch, damage, shadowing (for `projectId`, else global) and environment. */
  listBuiltins(projectId?: string): BuiltinSkillInfo[] {
    if (projectId) this.requireOpenProject(projectId);
    const b = this.builtins;
    if (!b) return [];
    return b.names().map((name) => {
      const e = b.entry(name)!;
      const shadowed_by =
        projectId && existsSync(join(this.skills.root('project', projectId), name)) ? 'project' : existsSync(join(this.skills.root('global'), name)) ? 'global' : null;
      const env = this.o.skillEnv?.env({ scope: 'builtin', name }, b.runtimeSpec(name).digest);
      return {
        name,
        title: e.title,
        summary: e.summary,
        caveats: e.caveats,
        description: this.skills.get('builtin', name)?.description ?? '',
        scripts: e.scripts,
        enabled: b.enabled(name),
        broken: b.broken(name),
        shadowed_by,
        runtime: env ? { state: env.state, reason: env.reason } : { state: 'none' as const, reason: null },
      };
    });
  }

  getBuiltin(name: string): SkillDetail {
    this.requireBuiltin(name);
    return this.skills.get('builtin', name)!;
  }

  setBuiltinEnabled(name: string, enabled: boolean): BuiltinSkillInfo {
    this.requireBuiltin(name);
    this.o.store.append({ project_id: GLOBAL_PROJECT_ID, agent_id: null, type: 'skill.builtin_toggled', payload: { name, enabled } });
    return this.listBuiltins().find((b) => b.name === name)!;
  }

  /** An ordinary, editable copy of a built-in skill; it shadows the built-in until deleted. */
  duplicateBuiltin(name: string, to: { scope: WritableSkillScope; projectId?: string }) {
    const b = this.requireBuiltin(name);
    if (existsSync(join(this.skills.root(to.scope, to.projectId), name))) throw new ConflictError(`A ${to.scope} skill named ${name} already exists`);
    return this.saveSkill(
      { scope: to.scope, name, fromDir: b.dir(name), ...(to.projectId ? { projectId: to.projectId } : {}) },
      { origin: `builtin:${name}@${b.entry(name)!.digest.slice(7, 19)}`, changeNote: 'Duplicated from the built-in skill' },
    );
  }

  retryBuiltinRuntime(name: string): { state: RuntimeState; reason: string | null } {
    const b = this.requireBuiltin(name);
    const provider = this.o.skillEnv;
    if (!provider) throw new ConflictError('Skill runtimes are not available in this daemon');
    const ref: SkillRef = { scope: 'builtin', name };
    provider.remove(ref);
    provider.ensure(ref, b.runtimeSpec(name), b.updated);
    const e = provider.env(ref);
    return { state: e.state, reason: e.reason };
  }

  /**
   * Whose environment a skill runs with: its own (catalog installs); a built-in's for the built-in itself; and a
   * built-in's for an own skill of the same name that has none (a duplicate), so copies keep their packages.
   */
  private runtimeTarget(skill: { scope: SkillScope; name: string }, projectId: string): { ref: SkillRef; builtin: boolean } {
    const own: SkillRef = { scope: skill.scope, name: skill.name, ...(skill.scope === 'project' ? { projectId } : {}) };
    if (!this.builtins?.entry(skill.name)) return { ref: own, builtin: false };
    if (skill.scope === 'builtin') return { ref: own, builtin: true };
    const provider = this.o.skillEnv;
    if (provider && provider.env(own).state === 'none') return { ref: { scope: 'builtin', name: skill.name }, builtin: true };
    return { ref: own, builtin: false };
  }

  private ensureRuntimeFor(skill: { scope: SkillScope; name: string }, projectId: string): void {
    const provider = this.o.skillEnv;
    if (!provider || !this.builtins) return;
    const t = this.runtimeTarget(skill, projectId);
    if (t.builtin) provider.ensure(t.ref, this.builtins.runtimeSpec(skill.name), this.builtins.updated);
  }

  /** Before a skill run: starts a built-in's environment if needed and waits for it (up to the limit). */
  async prepareSkillRuntime(agentId: string, skill: { scope: SkillScope; name: string }, signal?: AbortSignal): Promise<{ waitedMs: number }> {
    const provider = this.o.skillEnv;
    if (!provider || !this.builtins) return { waitedMs: 0 };
    const agent = this.requireAgent(agentId);
    const t = this.runtimeTarget(skill, agent.project_id);
    if (!t.builtin) return { waitedMs: 0 };
    const spec = this.builtins.runtimeSpec(skill.name);
    provider.ensure(t.ref, spec, this.builtins.updated);
    if (provider.env(t.ref, spec.digest).state !== 'preparing') return { waitedMs: 0 };
    const start = Date.now();
    await provider.waitFor(t.ref, this.o.builtinRuntimeWaitMs ?? BUILTIN_RUNTIME_WAIT_MS, signal);
    return { waitedMs: Date.now() - start };
  }

  /** Removes catalog copies of Desk's own skills that nobody edited, so the built-ins take over (spec §2.3). */
  adoptBuiltins(): string[] {
    const b = this.builtins;
    if (!b) return [];
    const last = new Map<string, { origin: string; deleted: boolean }>();
    for (const e of this.o.store.list({ types: ['skill.saved', 'skill.deleted'] })) {
      if (e.type !== 'skill.saved' && e.type !== 'skill.deleted') continue;
      if (!b.entry(e.payload.name) || e.payload.scope === 'builtin') continue;
      const key = `${e.payload.scope}:${e.payload.scope === 'project' ? e.project_id : ''}:${e.payload.name}`;
      last.set(key, { origin: e.payload.origin, deleted: e.type === 'skill.deleted' });
    }
    const removed: string[] = [];
    for (const [key, l] of last) {
      const [scope, projectId, name] = key.split(':') as ['global' | 'project', string, string];
      if (l.deleted || !l.origin.startsWith(`catalog:${name}@`)) continue;
      try {
        if (!existsSync(join(this.skills.root(scope, projectId || undefined), name))) continue;
        this.deleteSkill(scope, name, projectId || undefined, { origin: 'desk:builtin' });
        removed.push(key);
      } catch (err) {
        this.o.onError?.(err, `adopting built-in ${name}`);
      }
    }
    return removed;
  }
```

Change `skillEnv` to use `runtimeTarget` and the built-in wording:

```ts
  skillEnv(agentId: string, only?: { scope: SkillScope; name: string }) {
    const out = { bins: [] as string[], vars: {} as Record<string, string>, blocked: null as string | null, note: null as string | null };
    const provider = this.o.skillEnv;
    if (!provider) return out;
    const agent = this.requireAgent(agentId);
    const envOf = (s: { scope: SkillScope; name: string }) => {
      const t = this.runtimeTarget(s, agent.project_id);
      return { ...provider.env(t.ref, t.builtin ? this.builtins!.runtimeSpec(s.name).digest : undefined), builtin: t.builtin };
    };
    if (only) {
      const e = envOf(only);
      if (e.state === 'preparing') {
        out.blocked = e.builtin
          ? `${only.name} is still setting up its Python environment (first use only). Try again in a minute.`
          : `${only.name}'s runtime is still being set up. Try again in a minute.`;
      } else if (e.state === 'failed') {
        out.blocked = `${only.name}'s runtime is not ready: ${e.reason ?? 'setup failed'}. Ask the user to retry it in Skills.`;
      }
      return { ...out, bins: e.bins, vars: e.vars, note: e.note };
    }
    for (const name of agent.active_skills) {
      const skill = this.skills.resolve(name, agent.project_id);
      if (!skill) continue;
      const e = envOf(skill);
      if (e.state !== 'ready') continue;
      out.bins.push(...e.bins);
      out.vars = { ...e.vars, ...out.vars };
    }
    return out;
  }
```

A built-in whose environment has never been built reports `none`, so `blocked` stays null. `skill_run` always calls `prepareSkillRuntime` first (Task 7), which moves the environment out of `none`.

Activation:
- In `activateSkills`, after computing `resolved`, add: `for (const s of resolved) this.ensureRuntimeFor(s, agent.project_id);`.
- In `spawnThread`, after `const skills = this.requireUsableSkills(…)`, add: `for (const n of skills) { const s = this.skills.resolve(n, project.id); if (s) this.ensureRuntimeFor(s, project.id); }`.

`message_thread` already goes through `activateSkills`, so it needs no change.

In `packages/core/src/tools/types.ts`, add to `RuntimeServices` after `skillEnv`:

```ts
  /** Before a skill run: builds a built-in skill's environment on first use and waits for it (up to 5 minutes). */
  prepareSkillRuntime(agentId: string, skill: { scope: SkillScope; name: string }, signal?: AbortSignal): Promise<{ waitedMs: number }>;
```

In `packages/core/src/testing/context.ts`, next to the `skillEnv` default, add:

```ts
    if (prop === 'prepareSkillRuntime') return async () => ({ waitedMs: 0 });
```

Export `BUILTIN_RUNTIME_WAIT_MS` from `packages/core/src/index.ts` next to `Runtime`.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run packages/core/src/runtime packages/core/src/skills packages/core/src/tools && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/runtime/runtime.ts packages/core/src/runtime/builtins.test.ts packages/core/src/tools/types.ts packages/core/src/testing/context.ts packages/core/src/index.ts
git commit -m "feat(core): built-in skills in the runtime: switch, duplicate, lazy environments, adoption

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Agents: skill_run waits, built-ins listed, prompt text

**Files:**
- Modify: `packages/core/src/tools/skills.ts` (`skill_list`, `skill_run`)
- Modify: `packages/core/src/agent/prompts.ts` (`skillsSections`; the Desk skills guidance around line 202)
- Test: `packages/core/src/tools/skills.builtin.test.ts`

**Interfaces:**
- Consumes: `store.list(projectId, { builtins: true })` (Task 4); `services.prepareSkillRuntime` (Task 6).

- [ ] **Step 1: Write the failing test**

```ts
// packages/core/src/tools/skills.builtin.test.ts
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { BuiltinSkills, loadBuiltinsManifest } from '../skills/builtins';
import { SkillStore } from '../skills/store';
import { toolContext } from '../testing/context';
import { skillListTool, skillRunTool } from './skills';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';

const SKILLS = join(import.meta.dirname, '..', '..', '..', '..', 'catalog', 'skills');

function store() {
  const b = new BuiltinSkills({ root: SKILLS, manifest: loadBuiltinsManifest() });
  b.verify();
  return new SkillStore(mkdtempSync(join(tmpdir(), 'desk-tools-')), undefined, b);
}

describe('skill tools and built-in skills', () => {
  it('skill_list shows built-ins', async () => {
    const ctx = toolContext({ services: { skills: store() } });
    const out = await skillListTool.execute({}, ctx);
    expect(out).toMatch(/- pdf-toolkit \(builtin, v1\)/);
  });

  it('skill_run says when it set up the environment first', async () => {
    const ctx = toolContext({ services: { skills: store(), prepareSkillRuntime: async () => ({ waitedMs: 42_000 }) } });
    const out = await skillRunTool.execute({ name: 'file-inspector', script: 'file_identify.py', args: ['--help'], timeout_s: 60 }, ctx);
    expect(out).toMatch(/^\(Set up file-inspector's Python environment first: first use only, 42 s\.\)/);
  });
});
```

Check the real test-context helper in `packages/core/src/testing/context.ts`. If it is named differently or takes other arguments, use it as the existing `packages/core/src/tools/skills.test.ts` does, and copy that file's setup for `skill_run` (workspace, sandbox off).

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run packages/core/src/tools/skills.builtin.test.ts`
Expected: FAIL (no built-ins listed; no setup prefix).

- [ ] **Step 3: Implement**

In `packages/core/src/tools/skills.ts`:
- In `skill_list`, change `.list(ctx.projectId)` to `.list(ctx.projectId, { builtins: true })`, and update its description to "List the skills available in this project: project skills shadow global ones, which shadow Desk's built-in skills."
- In `skill_run.execute`, after `locateScript`:

```ts
    const { waitedMs } = await ctx.services.prepareSkillRuntime(ctx.agentId, { scope: skill.scope, name: skill.name }, ctx.signal);
```

Then build the result as:

```ts
    const setup = waitedMs >= 1000 ? `(Set up ${skill.name}'s Python environment first: first use only, ${Math.round(waitedMs / 1000)} s.)\n` : '';
    return `${setup}[${status}]\n${r.output}`;
```

In `packages/core/src/agent/prompts.ts`:
- In `skillsSections`, change `const visible = skills.list(project.id);` to `const visible = skills.list(project.id, { builtins: true });`.
- In the Desk guidance, add after the `Catalog:` line:

```ts
        '   - Built in: Desk\'s own skills are always available: every file type (documents, PDFs, spreadsheets, slides, images, audio and video, data files, archives, markup and e-books, email and calendars; file-inspector routes unknown files) and web research. Activate them whenever the work touches such files or the web; never ask the user to install them. The first script run of one may take a minute while its Python environment is set up.',
```

The thread guidance (around line 305) mentions activating skills. If it says anything about installing, add: "Desk's built-in skills (file types, web research) are always available to activate."

- [ ] **Step 4: Run the tests**

Run: `npx vitest run packages/core/src/tools packages/core/src/agent && pnpm typecheck`
Expected: PASS. If a prompt snapshot test fails because of the new line, update the snapshot and check that the diff is only that line.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/tools/skills.ts packages/core/src/tools/skills.builtin.test.ts packages/core/src/agent/prompts.ts
git commit -m "feat(core): agents see and run built-in skills; skill_run waits for a first-use setup

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Also stage any updated snapshots.

---

### Task 8: Daemon: wiring, routes, adoption and recovery at start

**Files:**
- Modify: `apps/daemon/src/daemon.ts` (around lines 90-125)
- Create: `apps/daemon/src/routes/builtins.ts`
- Modify: `apps/daemon/src/app.ts:64` (mount it)
- Modify: `docs/api.md` (a "Built-in skills" section next to Skills)
- Test: `apps/daemon/src/builtins.test.ts`, `packages/core/src/tools/sandbox.test.ts` (extend)

**Interfaces:**
- Consumes: the Runtime methods (Task 6); `BuiltinToggleRequest` and `BuiltinDuplicateRequest` (Task 1).
- Produces the HTTP routes:
  - `GET /v1/builtin-skills[?project_id=]`
  - `GET /v1/builtin-skills/:name`
  - `GET /v1/builtin-skills/:name/files/*`
  - `PUT /v1/builtin-skills/:name` with `{enabled}`
  - `POST /v1/builtin-skills/:name/duplicate` with `{scope, project_id?}` (201)
  - `POST /v1/builtin-skills/:name/runtime/retry`

- [ ] **Step 1: Write the failing test**

Model it on `apps/daemon/src/catalog.test.ts`: start the daemon the same way, with its uv stub and temp data dir, and use the same `api` helper.

```ts
// apps/daemon/src/builtins.test.ts (structure; reuse catalog.test.ts's setup helpers verbatim)
describe('built-in skills API', () => {
  it('lists, switches, duplicates and serves built-ins', async () => {
    const list = await api('GET', '/builtin-skills');
    expect(list.status).toBe(200);
    expect(list.body).toHaveLength(12);
    expect(list.body.find((b: any) => b.name === 'pdf-toolkit')).toMatchObject({ enabled: true, broken: null, shadowed_by: null, runtime: { state: 'none' } });

    expect((await api('GET', '/skills')).body.some((s: any) => s.name === 'pdf-toolkit')).toBe(false); // user skills only

    const off = await api('PUT', '/builtin-skills/pdf-toolkit', { enabled: false });
    expect(off.body.enabled).toBe(false);

    const detail = await api('GET', '/builtin-skills/word-documents');
    expect(detail.body.scope).toBe('builtin');
    const file = await raw('GET', '/builtin-skills/word-documents/files/SKILL.md');
    expect(file).toMatch(/^---/);

    const dup = await api('POST', '/builtin-skills/images/duplicate', {});
    expect(dup.status).toBe(201);
    expect((await api('GET', '/builtin-skills')).body.find((b: any) => b.name === 'images').shadowed_by).toBe('global');
    expect((await api('POST', '/builtin-skills/images/duplicate', {})).status).toBe(409);
    expect((await api('PUT', '/builtin-skills/nope', { enabled: false })).status).toBe(404);
  });

  it('adopts an unmodified catalog install at start', async () => {
    // Seed a data dir with a global skill saved with origin catalog:pdf-toolkit@builtin-…, then start the daemon on it.
    // Expect GET /skills to no longer list pdf-toolkit, and GET /builtin-skills to show pdf-toolkit with shadowed_by null.
  });
});
```

Also add a sandbox test next to the existing sandbox tests in `packages/core/src/tools/sandbox.test.ts`, skipped when `sandbox-exec` is unavailable, as those are. Build a Runtime with `builtins: { root: <a temp copy of catalog/skills> }` and `readOnly: [<that root>]`. Run `bash` (sandboxed) in a thread, with the command `touch <root>/pdf-toolkit/x`, and assert that it fails and that the file does not exist. Copy the existing tests' way of running a sandboxed bash command for a harness thread.

Write the adoption test with real calls. First start a daemon on a temp data dir, then `PUT /v1/skills/pdf-toolkit`; plain PUT records origin `user`, so that isn't enough. Instead, create a `Runtime` from `@desk/core` over the same data dir and database before starting the daemon, and call `saveSkill(…, { origin: 'catalog:pdf-toolkit@builtin-0123456789ab' })`. Look for an existing daemon test that seeds data before start (`grep -rn "openDb\|EventStore" apps/daemon/src/*.test.ts`) and copy its approach.

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run apps/daemon/src/builtins.test.ts`
Expected: FAIL (404s).

- [ ] **Step 3: Implement**

`apps/daemon/src/routes/builtins.ts`:

```ts
import { readFile } from 'node:fs/promises';
import { Hono } from 'hono';
import { BuiltinDuplicateRequest, BuiltinToggleRequest } from '@desk/protocol';
import type { AppDeps } from '../app';
import { body } from '../http';

/** Desk's own skills (spec 2026-09-26-builtin-skills-design §3): read-only, switchable, duplicable. */
export function builtinRoutes({ runtime }: AppDeps): Hono {
  const r = new Hono();
  r.get('/builtin-skills', (c) => c.json(runtime.listBuiltins(c.req.query('project_id') || undefined)));
  r.get('/builtin-skills/:name', (c) => c.json(runtime.getBuiltin(c.req.param('name'))));
  r.get('/builtin-skills/:name/files/*', async (c) => {
    const skill = runtime.getBuiltin(c.req.param('name'));
    const marker = `/builtin-skills/${c.req.param('name')}/files/`;
    const rel = decodeURIComponent(c.req.path.slice(c.req.path.indexOf(marker) + marker.length));
    return c.body(new Uint8Array(await readFile(runtime.skills.filePath(skill, rel))), 200, { 'content-type': 'application/octet-stream' });
  });
  r.put('/builtin-skills/:name', async (c) => {
    const req = await body(c, BuiltinToggleRequest);
    return c.json(runtime.setBuiltinEnabled(c.req.param('name'), req.enabled));
  });
  r.post('/builtin-skills/:name/duplicate', async (c) => {
    const req = await body(c, BuiltinDuplicateRequest);
    const out = runtime.duplicateBuiltin(c.req.param('name'), { scope: req.scope, ...(req.project_id ? { projectId: req.project_id } : {}) });
    return c.json(out, 201);
  });
  r.post('/builtin-skills/:name/runtime/retry', (c) => c.json(runtime.retryBuiltinRuntime(c.req.param('name'))));
  return r;
}
```

`body(c, BuiltinDuplicateRequest)`: if `body` uses `.parse`, then `{}` becomes `{scope: 'global'}` and the refine applies. Check that `body` accepts a refined schema (a `ZodEffects`); if its type parameter is `ZodObject`, loosen it to `z.ZodTypeAny`.

In `apps/daemon/src/app.ts`, add `app.route('/v1', builtinRoutes(deps));` after `skillRoutes`, with its import.

In `apps/daemon/src/daemon.ts`:

```ts
    const builtinRoot = o.catalog?.builtinRoot ?? fileURLToPath(new URL('../../../catalog/skills', import.meta.url));
    // In SkillRuntimes' options:
      exists: (ref) => (ref.scope === 'builtin' ? !!runtimeRef?.builtins?.entry(ref.name) : !!runtimeRef?.skills.get(ref.scope, ref.name, ref.projectId)),
    // In Runtime's options:
      builtins: { root: builtinRoot },
      readOnly: [join(home, '.gitconfig'), join(process.env.XDG_CONFIG_HOME ?? join(home, '.config'), 'git'), join(home, '.ssh'), builtinRoot],
    // Pass builtinRoot to CatalogService (builtinRoot: builtinRoot) instead of recomputing it.
    // After `runtimeRef = runtime;`:
    const interrupted = skillRuntimes.recoverInterrupted();
    if (interrupted) log.info(`${interrupted} skill environment setup(s) were interrupted; they will be set up again when needed`);
    const adopted = runtime.adoptBuiltins();
    if (adopted.length) log.info(`now built into Desk, removed catalog copies: ${adopted.join(', ')}`);
```

In `docs/api.md`, add a "Built-in skills" section after Skills that documents the six routes, their bodies and responses (`BuiltinSkillInfo`), and the 404/409/400 cases. State that `GET /v1/skills` lists only the user's own skills.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run apps/daemon && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/daemon/src/routes/builtins.ts apps/daemon/src/app.ts apps/daemon/src/daemon.ts apps/daemon/src/builtins.test.ts docs/api.md packages/core/src/tools/sandbox.test.ts
git commit -m "feat(daemon): /v1/builtin-skills, adoption of catalog copies and setup recovery at start

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Client and CLI

**Files:**
- Modify: `packages/client/src/client.ts` (add a `builtins` group after `catalog`, around line 255)
- Modify: `apps/cli/src/commands.ts` (the `skills` listing at line 440; `skill` subcommands after `restore`, around line 476)
- Test: `packages/client/src/client.test.ts` (extend) and `apps/cli/src/commands.test.ts` if one exists (otherwise the client test is enough)

**Interfaces:**
- Produces `client.builtins`:
  - `list(s?: SkillScopeRef): Promise<BuiltinSkillInfo[]>`
  - `get(name): Promise<SkillDetail>`
  - `file(name, path)`
  - `setEnabled(name, enabled): Promise<BuiltinSkillInfo>`
  - `duplicate(name, req: BuiltinDuplicateRequest): Promise<SkillSaveResult>`
  - `retryRuntime(name): Promise<{ state; reason }>`

- [ ] **Step 1: Write the failing test**

Follow the existing client tests, which stub `fetch` and assert the method and URL:

```ts
it('calls the built-in skill routes', async () => {
  const { client, calls } = stubClient(); // the helper the file already uses
  await client.builtins.list({ projectId: 'p 1' });
  await client.builtins.setEnabled('pdf-toolkit', false);
  await client.builtins.duplicate('images', { scope: 'project', project_id: 'p1' });
  await client.builtins.retryRuntime('images');
  expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
    'GET /v1/builtin-skills?project_id=p%201',
    'PUT /v1/builtin-skills/pdf-toolkit',
    'POST /v1/builtin-skills/images/duplicate',
    'POST /v1/builtin-skills/images/runtime/retry',
  ]);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run packages/client`
Expected: FAIL.

- [ ] **Step 3: Implement**

`packages/client/src/client.ts`. Add `BuiltinSkillInfo` and `BuiltinDuplicateRequest` to the protocol imports.

```ts
  /** Desk's own skills: read-only, switchable, duplicable into ordinary skills (spec 2026-09-26-builtin-skills-design). */
  builtins = {
    list: (s: SkillScopeRef = {}) => this.get<BuiltinSkillInfo[]>(`/builtin-skills${s.projectId ? `?project_id=${enc(s.projectId)}` : ''}`),
    get: (name: string) => this.get<SkillDetail>(`/builtin-skills/${enc(name)}`),
    file: (name: string, path: string) => this.raw(`/builtin-skills/${enc(name)}/files/${encPath(path)}`),
    setEnabled: (name: string, enabled: boolean) => this.put<BuiltinSkillInfo>(`/builtin-skills/${enc(name)}`, { enabled }),
    duplicate: (name: string, req: BuiltinDuplicateRequest = {}) => this.post<SkillSaveResult>(`/builtin-skills/${enc(name)}/duplicate`, req),
    retryRuntime: (name: string) => this.request<{ state: RuntimeState; reason: string | null }>('POST', `/builtin-skills/${enc(name)}/runtime/retry`),
  };
```

`apps/cli/src/commands.ts`. In the `skills [project]` action, after printing the user skills:

```ts
      const builtins = await c.builtins.list(ref ? { projectId: (await resolveProject(c, ref)).id } : {});
      if (builtins.length) {
        say('\nBuilt into Desk:');
        for (const b of builtins) {
          const notes = [b.enabled ? null : 'off', b.broken ? 'damaged' : null, b.shadowed_by ? `shadowed by your ${b.shadowed_by} skill` : null, `environment ${b.runtime.state === 'none' ? 'set up on first use' : b.runtime.state}`].filter(Boolean);
          say(`  ${b.name} — ${b.title} (${notes.join(', ')})`);
        }
      }
```

Add these subcommands after `skill restore`:

```ts
  skill.command('off <name>').description("Turn off one of Desk's built-in skills").action(async (name: string) => {
    const c = await client();
    await c.builtins.setEnabled(name, false);
    say(`Turned off ${name}. Agents no longer see it.`);
  });
  skill.command('on <name>').description("Turn a built-in skill back on").action(async (name: string) => {
    const c = await client();
    await c.builtins.setEnabled(name, true);
    say(`Turned on ${name}.`);
  });
  skill
    .command('duplicate <name>')
    .description('Copy a built-in skill into your own (global unless --project) to customise it')
    .option('-p, --project <project>')
    .action(async (name: string, opts: { project?: string }) => {
      const c = await client();
      const req = opts.project ? { scope: 'project' as const, project_id: (await resolveProject(c, opts.project)).id } : { scope: 'global' as const };
      const r = await c.builtins.duplicate(name, req);
      say(`Duplicated ${name} to ${r.dir}. Your copy is used instead of the built-in.`);
    });
```

The CLI's client accessor is not necessarily called `client()`. Use whatever the surrounding `skill` subcommands use.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run packages/client apps/cli && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/client/src/client.ts packages/client/src/client.test.ts apps/cli/src/commands.ts
git commit -m "feat(client,cli): built-in skills: list, off/on, duplicate, retry

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Desktop: IPC, data and the catalog without Files & media

**Files:**
- Modify: `apps/desktop/src/shared/ipc.ts:96` (new ops)
- Modify: `apps/desktop/src/main/handlers.ts:130` (the handlers)
- Create: `apps/desktop/src/renderer/skills/builtins/data.ts`
- Modify: `apps/desktop/src/renderer/skills/catalog/data.ts:8-15` (remove the `files` bay)
- Modify: `apps/desktop/src/renderer/components/CommandPalette.tsx` (turn-off and turn-on entries)
- Test: `apps/desktop/src/main/handlers.test.ts` (extend) and `apps/desktop/src/renderer/skills/builtins/data.test.ts`

**Interfaces:**
- Produces the IPC ops:
  - `builtins.list` `{ projectId? }`
  - `builtins.get` `{ name }`
  - `builtins.file` `{ name, path }`
  - `builtins.setEnabled` `{ name, enabled }`
  - `builtins.duplicate` `{ name, projectId? }`
  - `builtins.retry` `{ name }`
- Produces from `builtins/data.ts`:
  - `useBuiltins(): { status; error; items: BuiltinSkillInfo[]; refresh(): Promise<void> }`
  - `builtinKey(name) = 'builtin:' + name`
  - `parseBuiltinKey(key): string | null`
  - `runtimeLabel(b: BuiltinSkillInfo, progress?): string`

- [ ] **Step 1: Write the failing tests**

In `apps/desktop/src/main/handlers.test.ts`, following its existing style of a stub client with call recording:

```ts
it('forwards built-in skill ops to the client', async () => {
  await invoke('builtins.setEnabled', { name: 'pdf-toolkit', enabled: false });
  await invoke('builtins.duplicate', { name: 'images', projectId: 'p1' });
  expect(stub.calls).toContainEqual(['builtins.setEnabled', 'pdf-toolkit', false]);
  expect(stub.calls).toContainEqual(['builtins.duplicate', 'images', { scope: 'project', project_id: 'p1' }]);
});
it('refuses a bad built-in name', async () => {
  await expect(invoke('builtins.setEnabled', { name: '../x', enabled: false })).rejects.toThrow();
});
```

`apps/desktop/src/renderer/skills/builtins/data.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { builtinKey, parseBuiltinKey, runtimeLabel } from './data';

const b = (state: 'none' | 'preparing' | 'ready' | 'failed', reason: string | null = null) =>
  ({ name: 'pdf-toolkit', title: 'PDF toolkit', summary: '', caveats: [], description: '', scripts: 1, enabled: true, broken: null, shadowed_by: null, runtime: { state, reason } });

describe('built-in skill helpers', () => {
  it('round-trips keys', () => {
    expect(parseBuiltinKey(builtinKey('pdf-toolkit'))).toBe('pdf-toolkit');
    expect(parseBuiltinKey('global:pdf-toolkit')).toBeNull();
  });
  it('words the environment state', () => {
    expect(runtimeLabel(b('none'))).toBe('Set up on first use');
    expect(runtimeLabel(b('preparing'), { step: 'Installing 6 Python packages' })).toBe('Setting up… Installing 6 Python packages');
    expect(runtimeLabel(b('ready'))).toBe('Ready');
    expect(runtimeLabel(b('failed', 'no network'))).toBe('Setup failed: no network');
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run apps/desktop/src/main/handlers.test.ts apps/desktop/src/renderer/skills/builtins`
Expected: FAIL.

- [ ] **Step 3: Implement**

`apps/desktop/src/shared/ipc.ts`, after `skills.runtimeRetry`:

```ts
  'builtins.list': z.object({ projectId: id.optional() }),
  'builtins.get': z.object({ name }),
  'builtins.file': z.object({ name, path: relPath }),
  'builtins.setEnabled': z.object({ name, enabled: z.boolean() }),
  'builtins.duplicate': z.object({ name, projectId: id.optional() }),
  'builtins.retry': z.object({ name }),
```

Add the result types wherever the file maps ops to results, following the neighbouring `skills.*` entries.

`apps/desktop/src/main/handlers.ts`:

```ts
  'builtins.list': (i, c) => c.client().builtins.list(i.projectId ? { projectId: i.projectId } : {}),
  'builtins.get': (i, c) => c.client().builtins.get(i.name),
  'builtins.file': (i, c) => c.client().builtins.file(i.name, i.path),
  'builtins.setEnabled': (i, c) => c.client().builtins.setEnabled(i.name, i.enabled),
  'builtins.duplicate': (i, c) => c.client().builtins.duplicate(i.name, i.projectId ? { scope: 'project', project_id: i.projectId } : { scope: 'global' }),
  'builtins.retry': (i, c) => c.client().builtins.retryRuntime(i.name),
```

`apps/desktop/src/renderer/skills/builtins/data.ts`:

```ts
import { useCallback, useEffect, useState } from 'react';
import type { BuiltinSkillInfo } from '@desk/protocol';
import { call } from '../../bridge';
import { describeError } from '../../components/Toast';

export const builtinKey = (name: string) => `builtin:${name}`;
export const parseBuiltinKey = (key: string): string | null => (key.startsWith('builtin:') && key.length > 8 ? key.slice(8) : null);

/** The environment line of a built-in skill card. */
export function runtimeLabel(b: BuiltinSkillInfo, progress?: { step: string } | null): string {
  switch (b.runtime.state) {
    case 'none':
      return 'Set up on first use';
    case 'preparing':
      return `Setting up…${progress ? ` ${progress.step}` : ''}`;
    case 'ready':
      return 'Ready';
    default:
      return `Setup failed${b.runtime.reason ? `: ${b.runtime.reason}` : ''}`;
  }
}

/** Desk's built-in skills; refetches on focus, every 30 s, and every 2 s while one is setting up. */
export function useBuiltins(): { status: 'loading' | 'ready' | 'error'; error: string | null; items: BuiltinSkillInfo[]; refresh(): Promise<void> } {
  const [items, setItems] = useState<BuiltinSkillInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try {
      setItems(await call('builtins.list', {}));
      setError(null);
    } catch (err) {
      setError(describeError(err).message);
    }
  }, []);
  const preparing = items?.some((b) => b.runtime.state === 'preparing') ?? false;
  useEffect(() => {
    void refresh();
    const onFocus = () => void refresh();
    window.addEventListener('focus', onFocus);
    const t = setInterval(() => void refresh(), preparing ? 2_000 : 30_000);
    return () => {
      window.removeEventListener('focus', onFocus);
      clearInterval(t);
    };
  }, [refresh, preparing]);
  return { status: error && !items ? 'error' : items ? 'ready' : 'loading', error, items: items ?? [], refresh };
}
```

In `apps/desktop/src/renderer/skills/catalog/data.ts`, delete the `files` line from `BAYS`, since the catalog no longer has entries in that category.

In `apps/desktop/src/renderer/components/CommandPalette.tsx`:
- In the loader, add `call('builtins.list', {}).catch(() => [] as BuiltinSkillInfo[])` to the `Promise.all`, and store the result as `builtins`.
- Add entries after the catalog entries:

```ts
    for (const b of loaded?.builtins ?? []) {
      all.push({
        id: `builtin:${b.name}`,
        group: 'Built-in skills',
        title: `${b.enabled ? 'Turn off' : 'Turn on'} ${b.title}`,
        detail: b.summary,
        keywords: `${b.name} built-in skill ${b.enabled ? 'disable off' : 'enable on'}`,
        run: async () => {
          await call('builtins.setEnabled', { name: b.name, enabled: !b.enabled });
          toast({ tone: 'info', message: `${b.enabled ? 'Turned off' : 'Turned on'} ${b.title}.` });
        },
      });
    }
```

The palette entries so far use `route`. If entries cannot take a `run` callback, give these a route to the skill instead (`href({ name: 'skills', skill: builtinKey(b.name) })`), and leave the switch to the panel.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run apps/desktop && pnpm typecheck`
Expected: PASS. Update `Catalog.test.tsx` if it counts bays.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/shared/ipc.ts apps/desktop/src/main/handlers.ts apps/desktop/src/main/handlers.test.ts apps/desktop/src/renderer/skills/builtins apps/desktop/src/renderer/skills/catalog/data.ts apps/desktop/src/renderer/components/CommandPalette.tsx
git commit -m "feat(desktop): built-in skill IPC and data; the catalog's five bays

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Also stage any updated tests.

---

### Task 11: Desktop: the "Built into Desk" group and panel

**Files:**
- Create: `apps/desktop/src/renderer/skills/builtins/BuiltinGroup.tsx`
- Create: `apps/desktop/src/renderer/skills/builtins/BuiltinPanel.tsx`
- Modify: `apps/desktop/src/renderer/skills/SkillsScreen.tsx` (render the group above the map and the list; select `builtin:` keys; render the panel)
- Modify: `apps/desktop/src/renderer/skills/SkillPanel.tsx` (the "Customised from the built-in skill" line)
- Modify: `apps/desktop/src/renderer/skills/skills.css`
- Test: `apps/desktop/src/renderer/skills/builtins/Builtins.test.tsx`
- Modify: `apps/desktop/e2e/catalog.e2e.test.ts`; Create: `apps/desktop/e2e/builtins.e2e.test.ts`
- Modify: `docs/desktop.md` (the Skills row)

**Interfaces:**
- Consumes: `useBuiltins`, `builtinKey`, `parseBuiltinKey`, `runtimeLabel` (Task 10); the IPC ops `builtins.*`.

- [ ] **Step 1: Write the failing component test**

Model it on `SkillsScreen.test.tsx`, which mocks `call` the same way:

```tsx
// apps/desktop/src/renderer/skills/builtins/Builtins.test.tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { BuiltinGroup } from './BuiltinGroup';
import { BuiltinPanel } from './BuiltinPanel';

const call = vi.fn();
vi.mock('../../bridge', () => ({ call: (...a: unknown[]) => call(...a) }));

const item = (over = {}) => ({
  name: 'pdf-toolkit', title: 'PDF toolkit', summary: 'Read and write PDFs.', caveats: ['No OCR.'], description: 'd', scripts: 20,
  enabled: true, broken: null, shadowed_by: null, runtime: { state: 'none', reason: null }, ...over,
});

describe('built-in skills UI', () => {
  it('shows each built-in with its switch and environment', async () => {
    call.mockResolvedValue(item({ enabled: false }));
    const onChanged = vi.fn();
    render(<BuiltinGroup items={[item(), item({ name: 'images', title: 'Images and fonts', broken: 'images does not match' })]} selected={null} onSelect={() => {}} onChanged={onChanged} />);
    expect(screen.getByRole('region', { name: 'Built into Desk' })).toBeTruthy();
    expect(screen.getByText('Set up on first use')).toBeTruthy();
    expect(screen.getByText(/Damaged: reinstall Desk/)).toBeTruthy();
    fireEvent.click(screen.getByRole('switch', { name: 'PDF toolkit' }));
    await waitFor(() => expect(call).toHaveBeenCalledWith('builtins.setEnabled', { name: 'pdf-toolkit', enabled: false }));
    expect(onChanged).toHaveBeenCalled();
  });

  it('duplicates from the panel', async () => {
    call.mockImplementation(async (op: string) => (op === 'builtins.get' ? { name: 'pdf-toolkit', scope: 'builtin', instructions: '# PDF', files: [{ path: 'SKILL.md', size: 10 }], description: 'd', version: 1, dir: '/x', frontmatter: {} } : { dir: '/data/skills/pdf-toolkit', created: true, version: 1 }));
    const onDuplicated = vi.fn();
    render(<BuiltinPanel item={item()} projects={[]} onDuplicated={onDuplicated} onChanged={() => {}} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Duplicate to my skills' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Duplicate' }));
    await waitFor(() => expect(call).toHaveBeenCalledWith('builtins.duplicate', { name: 'pdf-toolkit' }));
    expect(onDuplicated).toHaveBeenCalledWith({ scope: 'global', name: 'pdf-toolkit' });
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run apps/desktop/src/renderer/skills/builtins`
Expected: FAIL.

- [ ] **Step 3: Implement the components**

`BuiltinGroup.tsx`:

```tsx
import type { BuiltinSkillInfo } from '@desk/protocol';
import { call } from '../../bridge';
import { toastError } from '../../components/Toast';
import { builtinKey, runtimeLabel } from './data';

/** Desk's own skills above the user's: a card each with its environment line and an on/off switch. */
export function BuiltinGroup(o: { items: BuiltinSkillInfo[]; selected: string | null; onSelect(key: string): void; onChanged(): void }) {
  const toggle = async (b: BuiltinSkillInfo) => {
    try {
      await call('builtins.setEnabled', { name: b.name, enabled: !b.enabled });
      o.onChanged();
    } catch (err) {
      toastError(err);
    }
  };
  return (
    <section className="builtin-group" aria-label="Built into Desk">
      <header className="builtin-head">
        <h2>Built into Desk</h2>
        <span className="muted small">Any file type: read, create, edit, convert — and see it; plus web research · {o.items.length}</span>
      </header>
      <ul className="builtin-cards">
        {o.items.map((b) => (
          <li key={b.name} className={`builtin-card${o.selected === builtinKey(b.name) ? ' selected' : ''}${b.enabled ? '' : ' off'}`}>
            <button type="button" className="builtin-open" onClick={() => o.onSelect(builtinKey(b.name))}>
              <span className="builtin-title">{b.title}</span>
              <span className="chip">Built in</span>
              {b.shadowed_by ? <span className="chip">Shadowed by your {b.shadowed_by} skill</span> : null}
              <span className={`runtime-line ${b.runtime.state}`}>{b.broken ? 'Damaged: reinstall Desk' : runtimeLabel(b)}</span>
            </button>
            {b.broken ? null : (
              <button type="button" role="switch" aria-checked={b.enabled} aria-label={b.title} className="switch" onClick={() => void toggle(b)}>
                <span className="switch-knob" aria-hidden="true" />
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
```

`BuiltinPanel.tsx`: a side panel with the same shell as `SkillPanel`. Copy its outer markup and classes: header, close button, sections.

```tsx
import { useEffect, useState } from 'react';
import type { SkillDetail } from '@desk/client';
import type { BuiltinSkillInfo } from '@desk/protocol';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { Sheet } from '../../components/Sheet';
import { toast, toastError } from '../../components/Toast';
import { SafeMarkdown } from '../../components/SafeMarkdown';
import type { SkillRef } from '../data';
import { runtimeLabel } from './data';

export function BuiltinPanel(o: { item: BuiltinSkillInfo; projects: Array<{ id: string; name: string }>; onDuplicated(ref: SkillRef): void; onChanged(): void; onClose(): void }) {
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [dup, setDup] = useState(false);
  const [target, setTarget] = useState('global');
  const [pending, setPending] = useState(false);
  useEffect(() => {
    void call('builtins.get', { name: o.item.name }).then(setDetail, toastError);
  }, [o.item.name]);
  const retry = async () => {
    try {
      await call('builtins.retry', { name: o.item.name });
      o.onChanged();
    } catch (err) {
      toastError(err);
    }
  };
  const duplicate = async () => {
    setPending(true);
    try {
      const projectId = target === 'global' ? undefined : target;
      await call('builtins.duplicate', { name: o.item.name, ...(projectId ? { projectId } : {}) });
      toast({ tone: 'info', message: `Duplicated ${o.item.name}. Your copy is used instead of the built-in.` });
      setDup(false);
      o.onDuplicated(projectId ? { scope: 'project', projectId, name: o.item.name } : { scope: 'global', name: o.item.name });
    } catch (err) {
      toastError(err);
    } finally {
      setPending(false);
    }
  };
  return (
    <aside className="skill-panel" aria-label={`Built-in skill ${o.item.name}`}>
      <header className="skill-panel-head">
        <h2>{o.item.title}</h2>
        <span className="chip">Built in</span>
        <Button size="sm" onClick={o.onClose} aria-label="Close">×</Button>
      </header>
      <p className="muted">{o.item.summary}</p>
      {o.item.shadowed_by ? <p role="note">Shadowed by your {o.item.shadowed_by} skill “{o.item.name}”: agents use yours.</p> : null}
      <p className={`runtime-line ${o.item.runtime.state}`} role="status">
        {o.item.broken ? `Damaged: ${o.item.broken}` : runtimeLabel(o.item)}
        {o.item.runtime.state === 'failed' ? <Button size="sm" onClick={() => void retry()}>Retry</Button> : null}
      </p>
      {o.item.caveats.length ? <ul className="caveats">{o.item.caveats.map((c) => <li key={c}>{c}</li>)}</ul> : null}
      <div className="skill-panel-actions">
        <Button onClick={() => setDup(true)}>Duplicate to my skills</Button>
      </div>
      {detail ? (
        <>
          <h3>Instructions</h3>
          <SafeMarkdown text={detail.instructions} />
          <h3>Files</h3>
          <ul className="file-list">{detail.files.map((f) => <li key={f.path}>{f.path}</li>)}</ul>
        </>
      ) : (
        <p className="muted">Loading…</p>
      )}
      {dup ? (
        <Sheet
          title={`Duplicate ${o.item.name}`}
          onClose={() => setDup(false)}
          footer={
            <>
              <Button onClick={() => setDup(false)}>Cancel</Button>
              <Button variant="primary" pending={pending} onClick={() => void duplicate()}>Duplicate</Button>
            </>
          }
        >
          <p>Your copy is an ordinary skill you can edit. Agents use it instead of the built-in until you delete it.</p>
          <label>
            Where
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="global">My skills (every project)</option>
              {o.projects.map((p) => <option key={p.id} value={p.id}>{p.name} only</option>)}
            </select>
          </label>
        </Sheet>
      ) : null}
    </aside>
  );
}
```

Match the real components' APIs:
- Check the `SafeMarkdown` import path and its prop name.
- Check `Button`'s `size` and `pending` props.
- Check `Sheet`'s `footer`.
- If `SkillPanel` renders files with its own component, use that component for the files list too.

In `SkillsScreen.tsx`:
- Call `const builtins = useBuiltins();`.
- Parse the selected key with `parseBuiltinKey(skill ?? '')`. When it names a built-in, render `<BuiltinPanel item={…} projects={projects} onDuplicated={(r) => (changed(), builtins.refresh(), select(skillKey(r)))} onChanged={() => void builtins.refresh()} onClose={() => select(null)} />` instead of `SkillPanel`. Guard the existing `ref` / `SkillPanel` path so it only runs for `parseSkillKey` results.
- In the non-catalog branch, render `<BuiltinGroup items={builtins.items} selected={skill ?? null} onSelect={(k) => select(k === skill ? null : k)} onChanged={() => void builtins.refresh()} />` at the top of `skills-body` in both the list and the map views, and also in the empty state, so a user with no skills of their own still sees the built-ins. For the map view, put the group in a strip above the map container.
- Call `builtins.refresh()` inside `changed()`, so a delete that restores a built-in updates its "shadowed" chip.

In `SkillPanel.tsx`: when the skill's history shows an origin starting with `builtin:`, show the line "Customised from the built-in skill {name}" with a link that selects `builtinKey(name)`. The history is already fetched for the History tab; reuse it.

In `skills.css`, add styles for:
- `.builtin-group`, `.builtin-head`, `.builtin-cards` (a grid, like the catalog cards)
- `.builtin-card` (`.selected`, `.off` with reduced opacity)
- `.builtin-open`, `.builtin-title`
- `.switch` / `.switch-knob`, a pill toggle driven by `[aria-checked="true"]`

Use the existing colour tokens, and check dark mode too.

- [ ] **Step 4: Run the component tests**

Run: `npx vitest run apps/desktop && pnpm typecheck`
Expected: PASS. Update `SkillsScreen.test.tsx`: its `call` mock must answer `builtins.list` with `[]`, or with one item where the test checks the group.

- [ ] **Step 5: Update the e2e tests**

In `apps/desktop/e2e/catalog.e2e.test.ts`:
- Use the bays `['Research', 'Documents & data', 'Writing & diagrams', 'Planning', 'Code']` with a count of `18`.
- The install flow uses Word documents, which is now built in. Switch the flow to a third-party entry with a Python runtime (for example `excel-automation`) and adjust the review-sheet text it waits for (open the entry's first script and wait for a line of it).

Create `apps/desktop/e2e/builtins.e2e.test.ts`, using the same launch helpers as the catalog e2e:
1. Open Skills (list view) and check that the region "Built into Desk" shows 12 cards.
2. Toggle the "PDF toolkit" switch off, and check with `client.builtins.list()` that `enabled` is false.
3. Open "Images and fonts", click "Duplicate to my skills", then "Duplicate". Check that the global skill `images` exists (`client.skills.list({})`) and that the Images card shows "Shadowed by your global skill".
4. Take screenshots with the existing `shot()` helper: `b1-builtins`, `b2-duplicate`.

- [ ] **Step 6: Run the desktop e2e (opens windows; one run)**

Run: `pnpm test:e2e`
Expected: PASS for `catalog.e2e.test.ts` and `builtins.e2e.test.ts`. The other e2e files keep passing.

- [ ] **Step 7: Docs and commit**

In `docs/desktop.md`, replace the Skills row's catalog sentence with this text: "**Built into Desk** lists Desk's own skills (every file type and web research). Each has an on/off switch, its environment ("Set up on first use", "Setting up…", "Ready", or failed with Retry), and a panel with its instructions and files and **Duplicate to my skills**. **Catalog** shows 18 pinned third-party skills in five bays."

```bash
git add apps/desktop/src/renderer/skills apps/desktop/e2e/catalog.e2e.test.ts apps/desktop/e2e/builtins.e2e.test.ts docs/desktop.md
git commit -m "feat(desktop): the Built into Desk group: switches, environments, duplicate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Release check, CLAUDE.md and the full verification

**Files:**
- Modify: `packages/core/scripts/catalog.ts` (the `check` command gains `--builtins`)
- Modify: `CLAUDE.md` (commands, layout, invariants)
- Modify: `docs/superpowers/specs/2026-09-26-builtin-skills-design.md` (the Status line: implemented)

- [ ] **Step 1: Implement `catalog:check --builtins`**

In `packages/core/scripts/catalog.ts`, split the smoke-in-sandbox block of `check()` into a helper, `smokeIn(dir, env, smoke, name)`. Then add `--builtins` handling at the top of `check()`:

```ts
  if (process.argv.includes('--builtins')) {
    const manifest = loadBuiltinsManifest();
    const selected = manifest.skills.filter((s) => !ids.length || ids.includes(s.name));
    let ok = true;
    for (const s of selected) {
      const ref = { scope: 'builtin' as const, name: s.name };
      const t0 = Date.now();
      try {
        runtimes.setup(ref, { digest: runtimeKey(s.runtime), runtime: s.runtime }, manifest.updated);
        await runtimes.settled(ref);
        const st = runtimes.state(ref);
        if (st.state !== 'ready') throw new Error(`runtime ${st.state}: ${st.reason}`);
        const smoke = s.smoke?.length ? await smokeIn(join(BUILTIN, s.name), runtimes.env(ref), s.smoke, s.name) : 'no smoke command';
        console.log(`PASS ${s.name} · runtime ready · ${smoke} · ${((Date.now() - t0) / 1000).toFixed(1)}s`);
      } catch (err) {
        ok = false;
        console.log(`FAIL ${s.name}: ${(err as Error).message}`);
      }
    }
    // Shut down and clean up exactly as the catalog path does below.
    return ok;
  }
```

Filter `--builtins` out of `ids` (the arguments that start with `--` already are). `smokeIn` is the existing smoke block, unchanged: a sandbox writable only to a fresh workspace, `SKILL_DIR` set, and a 180 s timeout.

- [ ] **Step 2: Update CLAUDE.md**

- **Commands:** add `pnpm builtins:pin [names]` ("re-pin Desk's built-in skills: digests, counts, packages from each skill's packages.txt") and `pnpm catalog:check --builtins [names]`. Change the catalog:pin line to say third-party entries.
- **Layout:**
  - The `catalog/` line: "`catalog.json` holds the 18 pinned third-party entries".
  - Add `skills/builtins.ts` + `builtins.json`: Desk's built-in skills, the manifest, verification and the switch.
  - The `catalog/skills/` bullet: "Desk's built-in skills (shipped with the app, read-only; spec `2026-09-26-builtin-skills-design.md`)", and "After editing one, run `pnpm builtins:pin <name>`; `builtins.test.ts` fails when a digest is stale".
  - The `catalog/shared/` line: add `_pandoc_safe.py`.
- **Invariants:** add the paragraph from spec §6, and keep "Only the user installs catalog skills".

- [ ] **Step 3: Real environments for all 12 (one at a time)**

Run: `memory_pressure -Q` (continue if free memory is 30% or more), then:

```bash
PATH="/Library/Frameworks/Python.framework/Versions/3.11/bin:$PATH" pnpm catalog:check --builtins
```

Expected: `PASS` for all 12. The audio-video wheels need macOS 14 on Apple silicon, which the dev Mac meets. Fix any failure in the skill, then run `pnpm builtins:pin <name>` and re-check that skill alone.

- [ ] **Step 4: Full verification**

Run: `pnpm typecheck && pnpm test`
Expected: PASS.

Then run `pnpm package:desktop`, and check that the packaged app lists the 12 built-ins as not broken:
- Start the packaged app's daemon.
- Call `GET /v1/builtin-skills` with the token from `daemon.json`, or extend `apps/desktop/e2e/packaged.e2e.test.ts` with that assertion and run it (`pnpm test:e2e` includes it once packaged).
- If `bundle.mjs` copied `__pycache__`-free trees, all 12 are intact. Stop if any is broken.

- [ ] **Step 5: Commit**

```bash
git add packages/core/scripts/catalog.ts CLAUDE.md docs/superpowers/specs/2026-09-26-builtin-skills-design.md
git commit -m "chore: catalog:check --builtins, CLAUDE.md and the spec status for built-in skills

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Also stage `apps/desktop/e2e/packaged.e2e.test.ts` if you extended it.
