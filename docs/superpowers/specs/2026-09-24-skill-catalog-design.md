# Desk — Skill Catalog Design

- **Date:** 2026-09-24
- **Status:** Approved design, not yet implemented (Plan 13)
- **Scope:** a curated catalog of 20 third-party and first-party Agent Skills that the user installs from the desktop app (or the CLI). The catalog covers fetching, review, install, update, per-skill runtimes (Python and Node) and curation tooling.
- **Inputs:**
  - Research, 2026-09-24:
    - the anthropics/skills audit
    - a community skill survey
    - the distribution and security survey: the Agent Skills spec, Claude Code marketplaces, the Agent Skills Discovery RFC, ClawHavoc and Snyk ToxicSkills
  - The existing skill store (`packages/core/src/skills/store.ts`), sandbox (`tools/sandbox.ts`) and Skills screen (`apps/desktop/src/renderer/skills`)

---

## 1. Decisions

| Decision | Choice | Why |
|---|---|---|
| Sourcing | **Pinned catalog**: a reviewed `catalog.json` shipped with the app. Each entry is pinned to a full commit SHA plus a content digest, and fetched from GitHub at install time. | Nothing third-party is redistributed inside the app. Every install is byte-identical to what was reviewed. Updates arrive as reviewed catalog changes. |
| Arbitrary URLs | **Not in this version** | Unvetted skills are the main supply-chain risk (ClawHub: 341 malicious skills; Snyk: 13.4% of 3,984 skills critical). They can be added later on the same review pipeline. |
| Runtimes | **Desk-managed.** uv is bundled for Python and the Electron binary serves as Node. Each skill gets its own environment with pinned, prebuilt-only packages and no install scripts. | Works on any Mac: this Mac's `python3` is 3.9, several skills need 3.10 or later, and a packaged app can't assume Node. Nothing is installed system-wide. |
| Who installs | **The user only**, from the app or the CLI | Agents never install software. Desk may *suggest* a catalog skill by name. |
| Proprietary skills | **Excluded** | anthropics/skills `docx`, `pdf`, `pptx` and `xlsx` are "© Anthropic, all rights reserved" and forbid copying or redistribution. `doc-coauthoring` has no licence. Desk ships its own Word and PDF skills instead, written from scratch. |
| Trust boundary | **Unchanged** | Catalog skills get no special powers. Scripts run only through `skill_run` and `bash`, sandboxed. Threads can't modify installed skills. `` !`cmd` `` blocks are never executed. |

---

## 2. The first catalog (20 entries)

All licences below were verified on 2026-09-24. Any entry that fails `catalog:check` in the sandbox (§7) is replaced by a runner-up, and the swap is recorded here.

**Curation result (2026-09-24):** all 20 entries pass `catalog:check` under the real sandbox profile, so no runner-up was swapped in. That includes #20: Playwright's Chromium starts and renders inside `sandbox-exec`.

| # | id | Source (repo → path) | Licence | Category | Runtime |
|---|---|---|---|---|---|
| 1 | paper-lookup | K-Dense-AI/scientific-agent-skills → `skills/paper-lookup` | MIT | Research | Python 3.12, stdlib |
| 2 | citation-management | K-Dense-AI/scientific-agent-skills → `skills/citation-management` | MIT | Research | Python 3.12 + `requests` |
| 3 | deep-research | daymade/claude-code-skills → `deep-research` | MIT | Research | none |
| 4 | fact-checker | daymade/claude-code-skills → `fact-checker` | MIT | Research | none |
| 5 | defuddle | kepano/obsidian-skills → `skills/defuddle` | MIT | Research | Node + `defuddle` |
| 6 | markitdown | K-Dense-AI/scientific-agent-skills → `skills/markitdown` | MIT | Documents & data | Python 3.12 + `markitdown[all]` |
| 7 | exploratory-data-analysis | K-Dense-AI/scientific-agent-skills → `skills/exploratory-data-analysis` | MIT | Documents & data | Python 3.12, stdlib |
| 8 | excel-automation | daymade/claude-code-skills → `daymade-docs/excel-automation` | MIT | Documents & data | Python 3.12 + `openpyxl` |
| 9 | frontend-slides | zarazhangrui/frontend-slides → `plugins/frontend-slides/skills/frontend-slides` | MIT | Documents & data | Python 3.12 + `python-pptx` (PDF export and deploy are optional and not set up) |
| 10 | word-documents | **Desk (first-party)** → `catalog/skills/word-documents` | MIT | Documents & data | Python 3.12 + `python-docx` |
| 11 | pdf-toolkit | **Desk (first-party)** → `catalog/skills/pdf-toolkit` | MIT | Documents & data | Python 3.12 + `pypdf`, `pdfplumber`, `reportlab` |
| 12 | humanizer | blader/humanizer → root | MIT | Writing & diagrams | none |
| 13 | writing-clearly-and-concisely | softaworks/agent-toolkit → `skills/writing-clearly-and-concisely` | MIT | Writing & diagrams | none |
| 14 | pretty-mermaid | imxv/Pretty-mermaid-skills → root | MIT | Writing & diagrams | Node + `beautiful-mermaid`, `@resvg/resvg-js` |
| 15 | summarize-meeting | phuryn/pm-skills → `pm-execution/skills/summarize-meeting` | MIT | Planning | none |
| 16 | pre-mortem | phuryn/pm-skills → `pm-execution/skills/pre-mortem` | MIT | Planning | none |
| 17 | systematic-debugging | obra/superpowers → `skills/systematic-debugging` | MIT | Code | bash |
| 18 | verification-before-completion | obra/superpowers → `skills/verification-before-completion` | MIT | Code | none |
| 19 | differential-review | trailofbits/skills → `plugins/differential-review/skills/differential-review` | CC-BY-SA-4.0 | Code | none |
| 20 | webapp-testing | anthropics/skills → `skills/webapp-testing` | Apache-2.0 | Code | Python 3.12 + `playwright` + Chromium ⚠ |

**Later additions (2026-09-24):**
- **Plan 14** rewrote #10 and #11 and moved them to a new **Files & media** category (`files`).
- **Plan 14** also added nine first-party file-type skills: `file-inspector`, `spreadsheets`, `presentations`, `images`, `audio-video`, `data-files`, `archives`, `markup-ebooks` and `email-calendar`. The spec is `2026-09-24-file-type-skills-design.md`.
- **Plan 15** added the first-party `web-research` skill (Research), with a new `browser` runtime extra. The spec is `2026-09-24-web-research-design.md`.
- The catalog now has 30 entries.

**Runners-up, in order:**
1. baoyu-translate (JimLiu/baoyu-skills, MIT)
2. gh-fix-ci (openai/skills, Apache-2.0; needs a `gh` login)
3. agent-browser (vercel-labs, Apache-2.0; Chromium caveat)
4. property-based-testing (trailofbits, CC-BY-SA)
5. create-prd (phuryn/pm-skills, MIT)

**Chromium (#20):** Playwright's Chromium brings its own macOS sandbox, which may not start inside `sandbox-exec`. #20 ships only if its smoke test passes under Desk's real profile. If it fails, the first runner-up replaces it. We don't loosen the sandbox to make it pass.

**First-party skills (#10, #11)** are written from scratch against the public documentation of their libraries, all permissively licensed: python-docx (MIT), pypdf (BSD-3), pdfplumber (MIT) and reportlab (BSD). No text or code comes from Anthropic's proprietary skills. Each has a SKILL.md, `scripts/`, which are small CLIs with `--help` and JSON output, and `references/`.

---

## 3. Catalog format

`packages/core/src/catalog/catalog.json`, validated by a zod schema in `@desk/protocol` (`CatalogFile`):

```jsonc
{
  "version": 1,
  "updated": "2026-09-24",                        // also the uv --exclude-newer date
  "entries": [{
    "id": "paper-lookup",                         // = the installed skill name (Agent Skills name rules)
    "title": "Paper lookup",
    "category": "research",                       // research | documents | writing | planning | code
    "summary": "Search 18 free scholarly APIs …", // ≤ 200 chars, shown on the card
    "license": "MIT",                             // SPDX
    "homepage": "https://github.com/K-Dense-AI/scientific-agent-skills",
    "source": { "type": "github", "repo": "K-Dense-AI/scientific-agent-skills", "path": "skills/paper-lookup",
                "sha": "<40 hex>" },              // or { "type": "builtin", "path": "catalog/skills/word-documents" }
    "digest": "sha256:<hex>",                     // over the normalised tree (§4)
    "files": 7, "bytes": 48213,
    "runtime": {
      "python": { "version": "3.12", "packages": ["requests==2.32.5"] },          // optional
      "node": { "lock": [{ "name": "defuddle", "version": "0.6.4",
                            "integrity": "sha512-…", "path": "node_modules/defuddle" }] }, // optional, flat
      "extras": ["playwright-chromium"]           // optional, a closed set Desk knows how to provision: playwright-chromium | browser
    },
    "smoke": ["python3", "scripts/lookup.py", "--help"],  // run by catalog:check inside the sandbox
    "caveats": ["Google Scholar lookups may be blocked; the other sources work without keys."]
  }]
}
```

**Builtin skills** live in `catalog/skills/<id>/` at the repo root. For the packaged app they are copied into `Resources/catalog/skills/`.

---

## 4. Fetch, review, install (core and daemon)

**Module:** `packages/core/src/catalog/`
- `catalog.ts`: load and validate the catalog
- `fetch.ts`: download and extract
- `review.ts`: the scan
- `runtime.ts`: §5
- `install.ts`

**Fetch** (`prepare`)
1. **GitHub source:** stream `https://codeload.github.com/<repo>/tar.gz/<sha>`. This is one request per install, outside GitHub's 60/h unauthenticated API quota. A 429 backs off with jitter.
   - **Builtin source:** read the files from disk.
2. Extract only entries under `<repo>-<sha>/<path>/`, streaming, with these caps:
   - download ≤ 50 MB
   - extracted ≤ 10 MB
   - ≤ 200 files
   - ≤ 2 MB per file

   These are the skill store's existing limits.
   - **Rejected:** symlinks, hardlinks, device files, absolute paths and `..` segments.
3. **Digest:** SHA-256 over the sorted list of `path\0size\0sha256(content)\n` records. A mismatch aborts with `catalog_digest_mismatch`. No partial staging is kept.
4. **Stage:** into `<data>/catalog/staging/<id>@<sha7>/`, then validate:
   - SKILL.md frontmatter per the Agent Skills spec
   - `name` equals the entry id and the folder name
   - `description` is 1–1024 characters
5. **Review scan** (`review.ts`). These are warnings, not blocks: the catalog was already reviewed, and the scan shows the user what to look at.
   - `` !`…` `` and ```` ```! ```` blocks
   - `curl … | sh` and `wget … | bash`
   - base64 blobs over 200 characters
   - invisible or bidirectional Unicode
   - URLs to paste sites
   - instructions that write to memory files

**`prepare` returns a `CatalogReview`:**
- the entry
- the file list, with sizes and an `executable`/`script` flag
- the SKILL.md text
- the warnings, as `{file, line, kind, excerpt}`
- the licence text
- the pinned source URL

**Install**
- `Runtime.installCatalogSkill(id, {scope, projectId?})` takes the staged directory and saves it through `SkillStore.save({fromDir})`.
- The resulting `skill.saved` event has:
  - `origin: "catalog:<id>@<sha7>"` (this widens the `origin` regex in `events.ts`)
  - `change_note: "Installed from the catalog (<repo>@<sha7>)"`
- The frontmatter gains `metadata.desk-catalog: "<id>@<sha>"`, so the install state can be derived without a separate table.

**Install states,** computed per scope in `listCatalog()`:

| State | Meaning |
|---|---|
| `not_installed` | no skill with this name exists in that scope |
| `installed` | the current version's `metadata.desk-catalog` equals `id@sha` and the tree digest equals the entry's |
| `update_available` | the installed catalog SHA differs from the entry's |
| `modified` | the digest differs from the catalog SHA it came from |
| `name_taken` | a non-catalog skill with the same name exists |

- **Update:** `prepare` again, then install. It creates a new version, and history and Compare work unchanged.
- **Local edits:** if the skill is `modified`, the install request must carry `replace_modified: true`, which the review sheet asks for explicitly.
- **Uninstall:** the existing skill delete, which also removes the runtime.

---

## 5. Runtimes

**Tools**
- **uv:** `Resources/bin/uv`, bundled.
  - Pinned version, with a SHA-256 checked by `scripts/package.mjs`. The download source is Astral's GitHub release. The MIT and Apache licences go into `Resources/licenses/`.
  - **Dev:** `DESK_UV`, or `uv` on PATH. Without uv, runtime setup fails with a clear message and the skill is still installed.
- **Node:** a shim at `<data>/runtimes/bin/node`, written by the daemon at start. It runs the daemon's own executable with `ELECTRON_RUN_AS_NODE=1` in a packaged build, or `process.execPath` in dev.

**Per-skill environment**
- **Location:** `<data>/runtimes/<scope>/<projectId|_global>/<skill>/`, with `bin/` put first on PATH.
- **Python:**
  1. `uv venv --python <version> --managed-python <env>/py`. uv caches the standalone CPython under `<data>/runtimes/uv`.
  2. `uv pip install --python <env>/py --only-binary :all: --compile-bytecode --exclude-newer <catalog.updated> <packages>`
  3. The `bin/` links point at `py/bin`.
  4. `UV_CACHE_DIR` and `UV_PYTHON_INSTALL_DIR` are set under `<data>/runtimes/uv`.
  5. Tools get `VIRTUAL_ENV`, `PYTHONDONTWRITEBYTECODE=1` (installed skills stay unchanged; step 2 precompiles the packages) and `PYTHONUTF8=1` (UTF-8 text I/O on Windows too).
- **Node:** for each lock entry:
  1. fetch the tarball from `registry.npmjs.org`
  2. verify its `integrity`
  3. extract it to `<env>/node/<path>`

  Lifecycle scripts never run. `bin/` gets links from each package's `bin` field, wrapped to use the node shim.
- **Extras:**
  - `playwright-chromium` runs `playwright install chromium` with `PLAYWRIGHT_BROWSERS_PATH=<env>/browsers`.
  - `browser` (added for web research, `2026-09-24-web-research-design.md` §4.3) uses an installed Chrome, Edge or Chromium: `DESK_BROWSER=<executable>` goes in the environment and nothing is downloaded. Without one, it does exactly what `playwright-chromium` does. Detection: `/Applications` or `~/Applications` on macOS; `%ProgramFiles%`, `%ProgramFiles(x86)%` or `%LOCALAPPDATA%` on Windows; `google-chrome`, `chromium` or `microsoft-edge` on PATH on Linux. Each match is tried in order and kept only if `<exe> --version` answers with a version (skipped on Windows, where `chrome.exe --version` opens a window), so a blocked or broken browser falls through to the next one or to the download. A ready runtime whose `DESK_BROWSER` no longer exists reports `failed`; Retry detects again. Both extras need `runtime.python` with a `playwright==` pin, which the schema enforces.
- **Node resolution:** each environment's `bin/node` exports `NODE_OPTIONS=--import file://…/resolve-register.mjs`. The hook retries failed bare imports from the environment, and because it is set in NODE_OPTIONS, Node processes a script spawns inherit it.
- **Compat shims:** skills written for other agents often start with install steps, so `bin/` holds stand-ins:
  - `uv run [--with …] script.py` runs the environment's python.
  - `uv pip install`, `pip install` and `npm install` report that the packages are already installed.
  - `npx <bin>` runs a bin from the environment.

  None of them reach the network.
- **Runtime note:** once the environment is ready, `skill_read`, `skill_activate` and the active-skills prompt show a `Runtime:` line naming the pinned packages, and tell the agent to skip install steps.

**Setup and state**
- Setup is async, per skill, and never blocks install.
- The stored event is `skill.runtime_changed {scope, name, project_id?, state: preparing|ready|failed, reason?}`. It is projected onto the skill row as `runtime_state`.
- Progress is sent as the ephemeral `skill.runtime_progress {name, step, done?, total?}`.

**Use**
- `skill_run` and `bash` put the active skills' `bin/` directories first on PATH, and add `SKILL_ENV` for the skill being run.
- The existing sandbox profile already allows reads and execution everywhere and writes only to the workspace and temp, so `<data>/runtimes` is read-only to agents.
- If a skill whose runtime isn't `ready` is used, `skill_run` returns a clear error ("paper-lookup's runtime is not ready: <reason>"). It doesn't fall back to the system interpreters.

**Cleanup:** deleting a skill removes its environment. `GET /v1/system/runtimes` reports the total size and any orphaned environments, and `POST /v1/system/runtimes/cleanup` removes the orphans.

---

## 6. API, CLI, app

**API** (documented in `docs/api.md`):

| Route | |
|---|---|
| `GET /v1/catalog` | entries plus `install` (per scope: global, and each project where installed) and `runtime` state |
| `POST /v1/catalog/:id/prepare` | → `CatalogReview` |
| `POST /v1/catalog/:id/install` `{scope, project_id?, replace_modified?}` | → the skill and its runtime state; 409 `catalog_modified` or `name_taken` |
| `POST /v1/skills/:name/runtime/retry`, `POST /v1/projects/:id/skills/:name/runtime/retry` | re-run setup |
| `GET /v1/system/runtimes`, `POST /v1/system/runtimes/cleanup` | sizes and cleanup |

**Client and desktop**
- **Client:** matching `DeskClient.catalog.*` methods. The system reducer projects `skill.runtime_changed`.
- **Desktop IPC:** `catalog.list`, `catalog.prepare`, `catalog.install`, `skills.runtimeRetry`, `system.runtimes` and `system.runtimesCleanup`, zod-validated as usual.

**CLI**
- `desk catalog` lists the catalog with its states.
- `desk catalog show <id>` prints the review as text.
- `desk catalog install <id> [-p project] [--yes]` shows the review and asks for confirmation unless `--yes`.

**App, in direction C**
- **Catalog view** in Skills, alongside Map and List:
  - Five bays: Research, Documents & data, Writing & diagrams, Planning, Code.
  - Each card shows:
    - the title and summary
    - `source` and `licence` chips
    - a "scripts" badge
    - the runtime in plain words ("Python 3.12 · set up by Desk")
    - an action: **Install**, "Installed", **Update** or "Modified"
  - The cards have a List alternative.
- **Review sheet:**
  - the source, linked to the exact commit
  - the licence
  - a file tree with the existing `FileViewer`, and scripts marked
  - warnings in a calm band, each linking to its line
  - scope (Global, or a project)
  - the caveats
  - an **Install** button, then inline progress
- **Installed catalog skills:**
  - a "From catalog" chip on the map node, the list row and the detail
  - a runtime line in the detail: Ready, Setting up…, or Failed with Retry
  - History shows the `catalog:` origin
- **Palette:** ⌘K lists catalog entries ("Install paper-lookup").
- **System → Data:** the size of skill environments, and "Clean up unused".
- **Desk's prompt:** the skill-authoring guidance gains one line: "If a catalog skill fits, suggest it to the user by name; you cannot install it." The catalog itself isn't injected into the prompt.

---

## 7. Curation tooling and verification

- **`pnpm catalog:pin [id…] [--ref <ref>]`** works on entries already written in `catalog.json`. It resolves each ref to a SHA with `git ls-remote`, fetches the skill, and writes its digest, file count and size. A repository too large for the 50 MB archive cap switches to files mode: one tree listing, then each file from raw.githubusercontent.com. Runtime fields are edited by hand, except Node locks: `runtime.node.lock_from` (a `package-lock.json` in the skill, or `npm:<spec> …`) is resolved into the flat lock.
- **`pnpm catalog:check [id…]`** runs, for each entry:
  1. fetch and verify the digest
  2. validate against the spec
  3. check the licence file and its SPDX match
  4. run the review scan and print the warnings
  5. build the runtime with uv and the node shim
  6. run `smoke` under `buildSandboxProfile` with the scrubbed env

  Non-zero exit on any failure. This is the gate for every catalog change, and it runs over all 20 in the first curation pass.
- **Automated tests** (`pnpm test`, offline):
  - **fetch and extract**, against a local tarball server:
    - caps, links, traversal and absolute paths
    - digest mismatch and truncation
    - a name that doesn't match its folder
  - **review scan:** each pattern, plus clean files that shouldn't trigger it
  - **install, update, modified and name_taken states:** origin and history
  - **runtime:** a uv stub and a local npm-registry stub; argument lists, integrity failure, retry, removal
  - **routes and client**
  - **desktop:** catalog cards, the review sheet and progress, via component tests
  - **e2e:** browse, review, install, "Ready" and the chip, update, compare, all against a fixture catalog pointing at the local tarball server
- **`pnpm test:live`:** install `paper-lookup` (Python) and `pretty-mermaid` (Node) from the real catalog, then run their smoke commands.

---

## 8. Build sequence (Plan 13)

| Step | Contents |
|---|---|
| 1 | Protocol schemas; `catalog/` fetch, digest, review and install in core; routes; the client; `docs/api.md` |
| 2 | Runtimes: uv and Node lock installer, the node shim, `skill.runtime_changed`, PATH in `skill_run` and `bash`, retry and cleanup |
| 3 | Curation: `catalog:pin` and `catalog:check`; write #10 and #11; pin and check all 20 (swapping any that fail) |
| 4 | Desktop: Catalog view, review sheet, chips, runtime line, ⌘K, System → Data; the CLI `catalog` commands |
| 5 | Packaging: bundle uv and the builtin catalog skills into `Resources`; e2e; live smoke; docs (`docs/desktop.md`, README) |

## 9. Out of scope

- Installing from arbitrary URLs or third-party registries (skills.sh, SkillsMP, claude-plugins.dev)
- A remote catalog that updates without an app release
- Signing catalog entries
- Windows runtimes
