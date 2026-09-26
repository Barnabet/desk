# Desk — Built-in Skills Design

- **Date:** 2026-09-26
- **Status:** implemented (Plan 18, branch `builtin-skills`), 2026-09-26.
- **Goal:** Desk's 12 first-party skills are part of the app. Agents can use them without the user installing anything:
  - the file-type skills: `file-inspector`, `word-documents`, `pdf-toolkit`, `spreadsheets`, `presentations`, `images`, `audio-video`, `data-files`, `archives`, `markup-ebooks`, `email-calendar`
  - `web-research`
- **User decisions:**
  - **Runtimes on first use.** A skill's Python environment is built the first time it is needed, not at launch.
  - **Built in, can be turned off.** Built-in skills are read-only and update with Desk releases. The user can turn any of them off. To customise one, the user duplicates it into an ordinary skill, which shadows it.

---

## 1. Decisions

| Decision | Choice | Why |
|---|---|---|
| Model | A third skill scope, `builtin`, read from the app, below `global` and `project` | Read-only by construction, updated with the app, and the user's own skills still win. The alternatives were a hidden auto-install into global skills or pre-installed catalog entries. Both make the skills editable files that every release must reconcile |
| Location | Packaged: the bundled `Resources/deskd/catalog/skills`, which is already shipped. Dev: the repo's `catalog/skills` | No new packaging |
| Manifest | `packages/core/src/skills/builtins.json`: per skill, its digest, file count, script count and pinned Python packages (plus `extras`, e.g. `["browser"]` for web-research) | Replaces the 12 builtin entries in `catalog.json`. The catalog returns to the 18 third-party skills |
| Integrity | At daemon start, each built-in tree is digested and compared with the manifest. A mismatch marks the skill `broken`, and agents never see it | The same guarantee as a catalog install, without a copy |
| Precedence | `project` → `global` → `builtin`. A same-named user skill shadows a built-in | Duplicate-to-customise works through the existing resolution |
| Off switch | A global event `skill.builtin_toggled {name, enabled}`, projected into a small table | Event sourcing. The switch applies to every project |
| Runtimes | Lazy. The build starts when the skill is activated for an agent (`skill_activate`, `spawn_thread skills`, `message_thread skills`), or at `skill_run` if it has not started. Environments are keyed by the manifest's package list and extras, so an app update that changes them rebuilds on next use | Disk grows only with the formats the user works with. Activation usually happens before the first script call, so the build overlaps the agent's work |

## 2. Core

### 2.1 Store and resolution (`skills/store.ts`, `skills/builtins.ts`)

- **Scope.** `SkillScope` (protocol `domain.ts`) becomes `['project', 'global', 'builtin']`. The new `WritableSkillScope` (`['project', 'global']`) is used wherever a skill is written: `skill_write`, `skill_delete` and catalog installs. A broken built-in carries its reason in `error`.
- **The `BuiltinSkills` module** (new, `skills/builtins.ts`):
  - It loads the manifest and verifies each tree at start with `readTree` + `treeDigest`, the helpers the catalog already uses.
  - It exposes `list()`, `get(name)`, `dir(name)` and `entry(name)`, the last returning the manifest record.
  - It reads the enabled state from the projection.
- **`SkillStore.list/resolve`** takes `BuiltinSkills` as a lowest-precedence source.
  - A built-in that is turned off or broken:
    - is left out of `resolve`, `requireUsableSkills` and the prompt's "Available skills";
    - still appears in API listings, with its state.
- **Read-only.** The store's write paths refuse `scope: 'builtin'` with `ValidationError`. The write paths are `save`, `delete`, `restore` and `import`.
  - `skill_write` from a thread draft named like a built-in installs a global skill, which shadows the built-in. The built-in's files are never written.
  - The sandbox already denies agents writes to the app bundle. In dev, `catalog/skills` is inside the repo, so it is added to `SandboxSpec` deny-write paths the same way Desk's data dir is.

### 2.2 Runtimes (`catalog/runtimes.ts`)

- **Ref.** `SkillRuntimes` accepts a built-in ref `{scope: 'builtin', name}`. Its environment lives at `<data>/runtimes/builtin/<name>`. `setup(ref, entry, updated)` takes the manifest record as its entry. The `--exclude-newer` date is the manifest's `updated`.
- **Keying.** `desk-env.json` records a hash of `{packages, extras, python}`. A ready environment whose hash differs from the manifest reports `none`, so the next use rebuilds it.
- **`ensure(ref)`** (new): `setup` if the state is `none` or stale. No-op when `preparing` or `ready`. Called by:
  - `Runtime.activateSkills`
  - `spawnThread` / `message_thread` skill activation
  - `skill_run`
- **Waiting.** `skill_run` on a built-in whose runtime is `preparing` waits for it, up to `BUILTIN_RUNTIME_WAIT_MS` (5 min), honouring the tool's abort signal. It then:
  - runs, if the runtime is ready;
  - returns "`<name>` is still setting up its Python environment (first use only). Try again in a minute.", if the runtime is still preparing;
  - returns the existing failure message with the reason, if the build failed.

  A run that had to wait prefixes its result with "Set up `<name>`'s Python environment (first use, <n> s)."
- **Copies share the built-in's environment.** A global or project skill named like a built-in, which has no Desk-managed environment of its own, uses the built-in's. The main case is a skill duplicated from a built-in. A copy of `pdf-toolkit` therefore still has pypdf and the rest.
- **`bash` with active built-ins.** `skillEnv` keeps its current behaviour: only ready runtimes join PATH. Activation has already called `ensure`.
- **Crash recovery.** At daemon start, any runtime whose last state is `preparing`, catalog or built-in, is recorded as `none` with reason "interrupted", and its partial directory is removed. This fixes the existing stuck-`preparing` gap.

### 2.3 Migration (one-time, at daemon start)

For each first-party id that exists as a global or project skill:
- **Unmodified catalog copy** (last `skill.saved` origin is `catalog:<id>@…`): delete it through `Runtime.deleteSkill` with the change note "Now built into Desk", and remove its runtime. The built-in takes over.
- **Edited copy** (a non-catalog origin after a catalog one): keep it. It shadows the built-in, and the UI says so.
- **Never came from the catalog:** keep it. It shadows the built-in.

The migration is idempotent: a second run finds nothing to do. It needs no marker event.

### 2.4 Catalog

- The 12 entries leave `catalog.json`, and the `files` category leaves the catalog UI. `CatalogCategory` keeps `files` in the enum so old events and clients still parse.
- `CatalogService` source type `builtin` stays for tests. No shipped entry uses it.
- The prompt text at `prompts.ts:202` keeps "the user installs catalog skills". It adds: "Desk's own skills (file types and web research) are always available: activate them, never ask the user to install them."

## 3. Events, API and CLI

- **Event:** `skill.builtin_toggled {name, enabled}` (project_id null), projected into `builtin_skill_settings(name, enabled)`. This needs an additive migration.
- **`GET /v1/skills` and `GET /v1/projects/:id/skills` are unchanged:** they list the user's own skills. Existing clients treat every non-project skill there as an editable global one, so built-ins get their own list.
- **New routes:**
  - `GET /v1/builtin-skills[?project_id=]` lists built-ins, each with:
    - `name`, `title`, `summary`, `caveats`, `description`, `scripts`
    - `enabled`, `broken`, `shadowed_by` (for that project, when one is given)
    - `runtime` (state and reason)
  - `GET /v1/builtin-skills/:name` returns detail.
  - `GET /v1/builtin-skills/:name/files/*` returns files.
  - `PUT /v1/builtin-skills/:name` with `{enabled}` turns a built-in off or on.
  - `POST /v1/builtin-skills/:name/duplicate` with `{scope: 'global' | 'project', project_id?}` saves an ordinary copy with origin `builtin:<name>@<digest12>`. Returns 409 if the name already exists in that scope.
  - `POST /v1/builtin-skills/:name/runtime/retry`.

  They live under their own prefix, so they cannot collide with `/v1/skills/:name/<sub>` routes.
- **`@desk/client`:** matching `DeskClient` methods.
- **CLI:**
  - `desk skills` lists built-ins in their own group.
  - `desk skills off <name>` and `desk skills on <name>` turn one off or on.
  - `desk skills duplicate <name> [--project <id>]` makes a copy.
- **Docs:** `docs/api.md` documents all of the above.

## 4. Desktop

- **Skills screen: "Built into Desk" group** (list and map), with the blurb "Any file type: read, create, edit, convert — and see it; plus web research." Each card shows:
  - a **Built in** chip
  - the runtime line: "Set up on first use", "Setting up…", "Ready", or failed with **Retry**
  - an on/off **switch**
  - **Damaged: reinstall Desk** instead of the switch, when broken
- **Detail panel.** The existing read-only instructions and files view, with **Duplicate to my skills** (global or the current project) instead of Edit/Delete.
  - A shadowed built-in says "Shadowed by your skill `<name>`", with a link to it.
  - The copy says "Customised from the built-in skill", with a link back.
  - Deleting the copy brings the built-in back.
- **Catalog tab** lists the 18 third-party skills in five bays.
- **Command palette:** "Turn off `<skill>`" and "Turn on `<skill>`".
- **IPC:** new ops in `shared/ipc.ts`, zod-validated in main (`skills.builtinToggle`, `skills.builtinDuplicate`, `skills.builtinRetry`).
- **`docs/desktop.md`:** the Skills row is updated.

## 5. Tooling

- **`pnpm builtins:pin [names]`** (in `scripts/catalog.ts`) rewrites `builtins.json` from `catalog/skills`:
  - digests, file and script counts
  - `updated`
  - packages, from each skill's `packages.txt`
  - extras

  It refuses `__pycache__`, `.pyc` and `.DS_Store`, as `pin` does. Each skill gains a `packages.txt` at its root: one pinned requirement per line, the package list that used to sit in `catalog.json`. The skills' digests therefore change once.
- **`pnpm catalog:check --builtins [names]`** builds each built-in's runtime in a fresh data dir, then runs `python3 scripts/selftest.py` in the sandbox, as today.
- **`pnpm catalog:sync`** is unchanged.
- **CLAUDE.md:** the commands, layout and invariants are updated.

## 6. Invariants

Added to CLAUDE.md:
- Built-in skills are read-only.
  - They are verified against `builtins.json` at start, and a mismatch is never offered to agents.
  - Only the user turns them off or on.
  - Agents shadow them only through drafts Desk installs as ordinary skills.
- Only the user installs catalog skills (unchanged).
- Runtimes are built by deskd, never by agents. `<data>/runtimes` stays read-only to agents.

## 7. Verification

- **Core (Vitest, harness, fake model):**
  - resolution order and shadowing
  - off: not listed, not activatable, `skill_run` refused
  - a tampered tree is broken and hidden
  - activation starts the build
  - `skill_run` waits, then gives up at the cap (with a short test cap)
  - a changed package hash rebuilds
  - `preparing` resets at start
  - migration (unmodified removed, edited kept, idempotent)
  - duplicate shadows the built-in; deleting the copy restores it
  - store write paths refuse `builtin`
  - a draft named like a built-in becomes a global skill
  - sandbox: agents cannot write the built-in folder or its runtime (skipped without `sandbox-exec`)
- **Manifest:** `builtins.test.ts`: digests and counts match `catalog/skills`, and each package list matches `packages.txt`. `catalog.test.ts` expects 18 entries.
- **Daemon:** the API tests above, and the migration through a real `startDaemon` with a seeded catalog install.
- **Desktop:**
  - component tests for the group, switch, runtime line and duplicate
  - the catalog e2e expects 18 cards and five bays
  - a new e2e step turns a built-in off and duplicates one
  - the packaged e2e checks the 12 built-ins are listed and verified from `Resources`
- **Real run:** `pnpm catalog:check --builtins` for all 12.
- **Gates:** `pnpm typecheck` and `pnpm test` before every commit.

## 8. Out of scope

- Per-project off switches. The switch is global; a project can shadow a built-in with its own skill.
- Prebuilding environments in the background, and bundling wheels in the app.
- The Whisper model download and a persistent per-skill cache (`DESK_SKILL_CACHE`), both still deferred from Plan 14.
