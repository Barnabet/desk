# Plan 15: Web research skill and web-tool fallbacks

- **Spec:** `docs/superpowers/specs/2026-09-24-web-research-design.md`, approved by the user on 2026-09-24.
- **Order:** starts after Plan 14 (the file-type skills) lands. The user chose this to limit disk and CPU contention.

## Phase 1: build (workflow, three builders in parallel)

1. **Core: web tools.**
   - `web_search` gets its provider chain: DuckDuckGo html → Bing RSS → Marginalia. `BRAVE_API_KEY` still comes first when set.
   - `web_fetch` gets the Wayback fallback.
   - Written test-first against fake provider servers. The tool result names the provider that answered.
2. **Core: the `browser` runtime extra.**
   - Protocol: `CatalogRuntime.extras` accepts `browser`.
   - `runtimes.ts` detects Chrome or Edge on macOS, Windows and Linux. It records `DESK_BROWSER` when it finds one, and runs `playwright install chromium` when it doesn't.
   - Also covered: the runtime note, `runtimeWords` in the desktop app, and tests with stubbed detection and a stubbed Playwright.
3. **Skill: `web-research`**, in `catalog/skills/web-research`.
   - Scripts per spec §3, with `_net.py` (client, truststore, SQLite cache and limiter, retries) and `_engines.py` (isolated parsers with saved fixture pages).
   - An offline selftest against a local HTTP server, plus a live `--engines-status`.
   - The system Chrome is tested under Desk's sandbox profile.

## Phase 2: accept and fix (per item, pipelined)

- An acceptance tester, acting as a Desk agent that has read only SKILL.md, answers five real research questions:
  - news from this week
  - a technical how-to
  - a scholarly question
  - a product comparison
  - a fact whose sources disagree
- Every quote must be verified through `sources.py`, and the tester reports defects.
- The core changes get an adversarial code review.
- Fixers resolve the findings and add regression tests.

## Phase 3: integrate (orchestrator)

1. A catalog entry for `web-research` (category `research`, extras `["browser"]`, smoke: the selftest). Run `pnpm catalog:pin web-research`, then `pnpm catalog:check web-research`.
2. Run `pnpm typecheck` and `pnpm test`, and live-check the core fallbacks by forcing DuckDuckGo to fail.
3. Write the engine-health results into spec §1. Update `docs/api.md` if tool output changed, plus `docs/desktop.md` and CLAUDE.md.
4. Commit.
