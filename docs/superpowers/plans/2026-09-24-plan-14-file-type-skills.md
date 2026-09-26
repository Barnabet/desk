# Plan 14: File-type skills and view_image

- **Spec:** `docs/superpowers/specs/2026-09-24-file-type-skills-design.md`
- **Goal (user, 2026-09-24):** "make the most advanced and performant full end to end handling of any file type skills, one per file type group".
- **User constraints:**
  - skills + scripts
  - runs on Windows
  - no OCR: agents look at rendered images

## Phase 0: foundation (orchestrator)

1. **Probes.**
   - Wheel availability, checked with `uv pip compile --only-binary :all:` for every candidate package, on macOS arm64, macOS x86_64 and Windows x64.
   - Image input through the local proxy: user messages, and user messages right after tool messages.
   - The pandoc → Typst → PNG chain.
   - LibreOffice inside Desk's sandbox, using a temp profile.
2. **Shared modules.**
   - `catalog/shared/_common.py` handles CLI plumbing, safe outputs, ranges, tools and the pool.
   - `catalog/shared/_render.py` covers LibreOffice, PDF → PNG, contact sheets, Typst, pandoc and ffmpeg.
3. **Tooling.**
   - `pnpm catalog:sync` refreshes the copies of the shared modules inside skills.
   - `packages/core/src/catalog/firstparty.test.ts` checks:
     - that shared copies are identical
     - SKILL.md frontmatter
     - that every script SKILL.md names exists
     - `__main__` guards
     - a lint for code that won't run on Windows
4. **Dev harness** (in the scratchpad, `skilldev.sh`). It builds the skill runtime exactly as Desk does, then runs commands:
   - in Desk's real sandbox profile, with a read-only skill directory
   - with a scrubbed environment
   - with `DESK_SOFFICE=none` available to test the built-in renderers
5. **Catalog category `files`** ("Files & media"):
   - the protocol enum
   - the desktop `BAYS`
   - the CLI `CATEGORY_TITLES`

## Phase 1: build, accept, fix (workflow `file-type-skills-build`)

Each item goes through a pipeline: **build → independent acceptance → fix**. Items don't wait for each other between stages.

**Skill builders** (one agent per skill; 11 skills). Each one:
- writes the skill in `catalog/skills/<id>/`
- pins its packages in the dev harness
- writes a `selftest.py` that runs every script end to end
- tests on real public sample files
- measures performance

**Acceptance testers** act as a Desk agent that knows only SKILL.md. Each performs six real tasks:
- create something and inspect the render visually
- edit a real file
- a round-trip
- a large input
- a malformed input

Testers report defects only; they fix nothing.

**Fixers** fix the defects, add regression checks, and re-run the selftest and the lint.

**Core** follows the same pipeline:
1. `view_image` is built test-first: protocol, tool, attachment store, transcript injection with an 8-image window, compaction, route, client, desktop thumbnails, CLI and prompts, plus a live test.
2. An adversarial reviewer checks it.
3. A fixer resolves the findings.

## Phase 1b: big files (workflow `file-skills-big-files`, added 2026-09-24 at the user's request)

This runs after phase 1. The spec is §4b; the orchestrator wrote `catalog/shared/_cache.py` and tested it with 16 checks in Desk's sandbox, including multi-process races, invalidation, low disk and eviction.

Each of the 11 skills goes through **retrofit → big-file acceptance → fix**. The retrofit brings:
- the cache on every expensive path
- map-first reads with stable addresses, `--find`, budgets with continuation, and CSV for medium extracts
- streaming reads, and surgical `.xlsx` edits
- benchmarks on real big fixtures, recorded in `references/performance.md`
- selftest checks for cache hits

Fixtures live in scratch space and are deleted after each run, because disk is tight.

## Phase 2: integrate (orchestrator)

1. Read every report and inspect every skill (SKILL.md, scripts, selftest), then run each selftest myself through the harness, with LibreOffice and without it.
2. Write the catalog entries:
   - 9 new, and 2 rewritten (`category: files`)
   - pinned packages
   - smoke `python3 scripts/selftest.py`
   - caveats
3. Run `pnpm catalog:pin` for the 11 skills, update `catalog.test.ts` (29 entries), and update the e2e bay list and count.
4. Run `pnpm catalog:check` for the 11 skills in the real sandbox. This is the release gate.
5. Run `pnpm typecheck`, `pnpm test` and a live `view_image` test.
6. Update the docs:
   - the catalog spec's §2 table
   - `docs/desktop.md` (bays and counts)
   - `docs/api.md` (attachments)
   - CLAUDE.md (`catalog/shared`, `catalog:sync`, the new skills)
7. Commit in logical commits:
   - the foundation and tooling
   - `view_image`
   - the skills
   - the catalog

## Phase 3: adversarial review (workflow)

A final review pass with independent lenses, followed by fixes and a re-run of the gates:
- correctness on real files
- Windows portability
- security:
  - archive extraction
  - path handling
  - redaction completeness
  - never overwriting inputs
- SKILL.md clarity for agents

## Build notes (2026-09-25)

- **Crashes.** The 8 GB build Mac crashed three times, twice as kernel watchdog panics caused by running out of memory:
  - about 12 concurrent agents;
  - a Python process at 11 GB (the spreadsheets far-cell case, since fixed);
  - four `bsdtar` processes at 17.8 GB on hostile archive samples.
- **What changed afterwards.**
  - At most two agents ran at once, across every session.
  - A watchdog killed any skill-test process above 2.5 GB, or the biggest one when free memory fell under 12 %.
  - Agents followed stricter build rules: one LibreOffice, Chrome, Whisper or decompressor at a time, fixtures of at most 300 MB, targeted tests only.
  - The product fixes are in spec §4c.
- **Resuming.** Workflow resume is prefix-based, so after edits the remaining work ran through a script that took an explicit list of stages per skill. Earlier reports were kept in durable files.
- **Whisper.** The real Whisper transcription test was deferred: the model download (about 75 MB) needs the user's go-ahead.

## Result (2026-09-26)

- **Skills.** Eleven file-type skills plus web-research (Plan 15), each built, accepted adversarially and fixed. Selftest checks at release: file-inspector 339, word-documents 340, pdf-toolkit 233, spreadsheets 619, presentations 265, images 492, audio-video 212, data-files 243, archives 536, markup-ebooks 343, email-calendar 482, web-research 261.
- **Release check.** `pnpm catalog:check` passed for all twelve: a real install of each pinned runtime in a fresh data dir, then its selftest in Desk's sandbox. web-research found the installed Chrome. All twelve resolve to prebuilt wheels for macOS arm64 and x86_64 and for Windows x64; audio-video on Apple silicon needs macOS 14 (PyAV), which its caveat says.
- **Final review.** One cross-cutting review (phase 3) of the finished skills found 14 issues, fixed before the commit:
  - a stranger's document could pull local files into outputs through pandoc and include directives;
  - exponential regexes on file content in file-inspector and spreadsheets (and, from a follow-up scan, in five more skills);
  - Chrome's sandbox was off;
  - LibreOffice profile leaks and concurrent runs;
  - a DOCTYPE check that padding or UTF-16 could bypass;
  - Windows file names;
  - stale caches of included files;
  - batch name clashes;
  - wording.

  The rules are in spec §4c.
- **Timing checks.** Selftests that compared a cached run with a cold one as a separate process now also assert the cache hit itself and ask for 3× at least: once cached, the run is mostly process start-up, and the fresh runtime (with precompiled packages) made the cold runs quick enough that 5× flaked.
- **Deferred:**
  - a real Whisper transcription test (needs the user's go-ahead for the model download);
  - a persistent per-skill cache dir (`DESK_SKILL_CACHE`), which needs a security review;
  - `SKILL_DIR` in plain bash for active skills (`skill_run` already sets it).
