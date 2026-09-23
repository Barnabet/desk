# Desk Plan 6 — Skills Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. TDD per task; commit only when `pnpm test` and `pnpm typecheck` both exit 0.

**Goal:** Skills — reusable procedures (instructions + prepared scripts) — are a first-class capability of Desk and its threads. Desk uses them itself, attaches them to threads at spawn so they start with the skill active, and authors and refines project and global (user-wide) skills from user requests and from what threads learn, with thread-built scripts tested before Desk installs them.

**User requirement (2026-09-23):** "Skills must be key to both the desk and subagents": a complete skills + prepared-scripts system supervised by Desk: used by Desk for itself, added to a subagent's context right away (as if activated), and above all written dynamically as project or global user skills + scripts from user requests and refinement, arming agents for general custom task automation.

## Design

**Format.** Agent Skills layout (compatible with Claude Code skills, so they can be imported):
```
<name>/SKILL.md          YAML frontmatter (name, description, optional metadata) + Markdown instructions
<name>/scripts/…         prepared scripts (made executable on install)
<name>/references/…      optional extra docs;  <name>/assets/… optional files
```
Name: `^[a-z0-9]+(-[a-z0-9]+)*$`, ≤ 64 chars. Description: 1–1024 chars, says what it does and when to use it.

**Scopes and storage.** The filesystem is the source of truth (users may hand-edit skills):
- global: `<dataDir>/skills/<name>/` — visible to every project;
- project: `<dataDir>/projects/<id>/skills/<name>/` — shadows a global skill with the same name.
Every update or delete first moves the previous version to `<root>/.history/<name>/<n>/`; versions are restorable. Changes are recorded as events (`skill.saved`, `skill.deleted`; global changes made outside a project use project id `_global`).

**Activation.** Agents have `active_skills` (from `agent.created.skills` and `agent.skills_changed`). The system prompt of an agent embeds the full instructions of its active skills (per-run snapshot, so they survive compaction), capped at 12k chars per skill / 40k total (beyond that: "read with skill_read"). All other visible skills are listed by name + description (≤ 60 entries; `skill_list` searches).

**Tools.**
- Everyone: `skill_list {query?}`, `skill_read {name, path?}`, `skill_activate {name}` (adds to active skills, returns instructions immediately), `skill_run {name, script, args[], stdin?, timeout_s}` — runs a file of the skill in the agent's shell sandbox (cwd = workspace, writes only to the workspace/temp, env `SKILL_DIR`, interpreter by extension or shebang). Gated like `bash` (risky-pattern rule, ask when the sandbox is unavailable).
- Desk only: `skill_write {name, scope, description?, instructions?, files?[{path, content}], from_dir?, remove_files?, change_note}` — create or refine; `from_dir` installs a draft directory from a thread's workspace (or Desk's). `skill_delete {name, scope}` — gated `ask`.
- `spawn_thread {…, skills[]}` activates skills on the new thread; `message_thread {…, skills[]}` activates more.
- Threads draft skills in `<workspace>/skill-drafts/<name>/` and report them via `complete {…, skill_drafts[]}`; the completion notice to Desk lists the drafts for review and installation. Threads cannot write installed skills directly — Desk supervises.

**Desk behaviour.** Check skills when scoping; activate relevant ones for itself; attach relevant ones to every thread; capture repeatable automations and user preferences about *how* a kind of task is done as skills; get scripts built and tested by a thread, review, install with `from_dir`; refine skills (with a change note) when the user corrects the approach or threads report problems; global scope for general-purpose automations, project scope for project-specific ones.

**User surface.** Runtime/HTTP/CLI: list (global, project-merged), show, import from a local directory (e.g. `~/.claude/skills/x`), delete, history, restore.

## Tasks

### Task 1: Skill store
**Files:** protocol (`skill.saved`, `skill.deleted`, `agent.skills_changed`, `agent.created.skills?`, `SkillScope`, `SkillName`); schema `agents.active_skills`; projections; `skills/store.ts` (`SkillStore`: `roots`, `list(projectId?)`, `resolve(name, projectId?)`, `read`, `save`, `delete`, `history`, `restore`, `import`; frontmatter parse/serialize with `yaml`).
**Tests:** save creates SKILL.md + files, scripts executable; project shadows global; update moves previous version to history, restore brings it back (as a new version); invalid names / paths escaping the skill dir / oversized files rejected; import copies a Claude-Code-style skill dir and validates frontmatter; hand-edited skills on disk are listed; malformed SKILL.md reported, not crashing the listing.

### Task 2: Skill tools and activation
**Files:** `tools/skills.ts`; `RuntimeServices` (`skills`, `activateSkills`, `installSkillFromDir`); toolsets; default policy rule for `skill_run` risky commands; policy degraded mode covers `skill_run`; read roots include skill roots.
**Tests:** list/read/activate (event + immediate instructions); `skill_run` executes a script with args/stdin in the workspace, in the sandbox (cannot write outside the workspace), picks interpreters; unknown script → error; `skill_write` creates/refines with change note and version; `from_dir` only from project workspaces; `skill_delete` gated ask.

### Task 3: Prompts and coordination
**Files:** `agent/prompts.ts` (skills sections + Desk workflow step + thread rules), `tools/desk.ts` (`spawn_thread.skills`, `message_thread.skills`), `tools/thread.ts` (`complete.skill_drafts`), runtime notifications.
**Tests:** thread spawned with skills has them active and their instructions in its prompt; Desk prompt lists skills and skill-curation rules; unknown skill names rejected at spawn; completion notice lists skill drafts; instructions cap respected.

### Task 4: API and CLI
**Files:** protocol `api.ts` (`SkillWriteRequest`, `SkillImportRequest`, `SkillRestoreRequest`); daemon `routes/skills.ts`; CLI `skills`, `skill show|import|rm|history|restore`.
**Tests:** HTTP list/show/put/import/delete/history/restore for both scopes; CLI import + list + show.

### Task 5: End-to-end skill authoring scenario + live smoke
**Tests:** fake-model scenario: user asks Desk for a reusable automation → Desk spawns a thread → thread drafts SKILL.md + script, runs it, completes with `skill_drafts` → Desk installs it globally → in a second project, Desk spawns a thread with the skill → the thread's prompt contains the instructions and it runs the script with `skill_run`. Live (`DESK_LIVE=1`, Opus): Desk creates a small global skill with a script on request and a thread uses it.

## Done Criteria
Tests + typecheck green; live skill smoke passes; spec updated with a Skills section.
