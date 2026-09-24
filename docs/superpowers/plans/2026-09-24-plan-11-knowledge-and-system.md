# Plan 11 · Skills, Library, Memory, Settings, System and ⌘K: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the rest of the desktop app's functionality checklist (spec §7 items 5–9 and the ⌘K palette), in design direction C, wired to deskd.

**Architecture:**
- Knowledge screens derive their lists from the project session's event log (`memory.written` / `memory.deleted` / `artifact.published`) through pure folds, so they stay live without polling.
- Skills and System fetch over IPC and refetch after writes and on window focus.
- One new IPC channel, `daemon.repair`, reinstalls the LaunchAgent.
- The new-project sheet reuses the settings fields under "More options".

**Tech Stack:** as in Plan 9 and Plan 10: React 19 with the tiny store, the hash router, `@desk/client` reducers, and Vitest with jsdom and the fake bridge. The e2e suite uses Playwright for Electron.

**Spec:** `docs/superpowers/specs/2026-09-24-desk-desktop-app-design.md` (§7 items 5–9, "Shell", "Safety").

## Global Constraints

- `pnpm test` and `pnpm typecheck` must pass before every commit. `pnpm test:e2e` must pass at the end of the plan.
- **No optimistic agent state.** Writes show a pending control and refetch, or wait for the stream.
- **Untrusted content.** Agent and user content (skill instructions, library files, memory) is rendered only through `SafeMarkdown` or as plain text, and images are shown only from daemon bytes, never from remote URLs.
- **No secrets in the UI.** The model API key is never displayed or echoed back; the endpoint form only writes it.
- Store selectors must return stable references; derive lists with `useMemo`.
- Every map has a List alternative. Every control is a real button, link or input, with labels on icon-only buttons.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File map

| File | Responsibility |
|---|---|
| `renderer/knowledge/library.ts` | fold `artifact.published` into library items (pure) |
| `renderer/knowledge/LibraryScreen.tsx` | grid, preview (Markdown, text/code, images from bytes, download), upload by drop and picker |
| `renderer/knowledge/memory.ts` | fold `memory.*` into entries with supersession chains (pure) |
| `renderer/knowledge/MemoryScreen.tsx` | grouped by kind, search (`?q=`), add, correct, delete |
| `renderer/settings/SettingsFields.tsx` | check-in, autonomy, review rounds, models, slots (shared with the new-project sheet) |
| `renderer/settings/PolicyEditor.tsx` | ordered policy rules with the default shown and a reset button |
| `renderer/settings/SettingsScreen.tsx` | name/goal/instructions, sources, fields, policy, archive |
| `renderer/skills/*` | `SkillsScreen` (map and list), `SkillDetail`, `SkillEditor`, `SkillHistory` (diff/restore), `skillsMap.ts` (pure layout), `diff.ts` (pure line diff) |
| `renderer/system/SystemScreen.tsx` | daemon, proxy, endpoint, models registry, usage, notices, notifications, data dir and logs |
| `renderer/components/CommandPalette.tsx` | ⌘K over projects, threads, skills, library titles, and memory search |
| `main/daemon.ts`, `shared/ipc.ts`, `main/handlers.ts` | `daemon.repair` |
| `e2e/knowledge.e2e.test.ts` | refine a skill and restore v1, upload to the Library, correct a memory, change settings |

## Tasks

1. Library
2. Memory
3. Project settings, and "More options" in the new-project sheet
4. Skills
5. System, with `daemon.repair`
6. ⌘K palette
7. End-to-end flows for items 5–9

The tasks are appended below as each one is implemented, with the code that shipped.

---

