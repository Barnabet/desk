# Desk Plan 2 — Safety Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. TDD per task: write the listed tests first, see them fail, implement, see them pass, `pnpm typecheck`, commit. Code lives in the repo; this plan fixes task boundaries, interfaces and test scenarios.

**Goal:** Every tool call passes a policy gate; risky calls become approvals resolved through the Runtime; shell commands are confined by macOS `sandbox-exec`; long-running shell jobs and web tools are available to threads.

**Architecture:** Project settings (incl. ordered policy rules) become part of the project projection. Tools may declare a `gate` (policy subject + unmatched default). `runAgent` prepares each call (parse + validate), evaluates policy, executes allowed calls, records `approval.requested` for `ask`, and yields `waiting` when any call is pending. `Runtime.resolveApproval` executes or denies the pending call, appends its `tool.result`, and reschedules the agent. `bash`/`bash_background` run under a generated SBPL profile (writable: workspace + temp); if `sandbox-exec` is unavailable, every shell call is classified `ask`.

**Tech Stack:** as Plan 1, plus `linkedom`, `@mozilla/readability`, `turndown` (pure JS).

**Spec:** `docs/superpowers/specs/2026-09-23-desk-daemon-design.md` §4 (Project settings, Approval), §6.1 (bash_background, web_fetch, web_search), §7 (entire).

## Global Constraints

All Plan 1 constraints, plus:
- Default policy (spec §7.3), evaluated first-match-wins; unmatched policy-class tools → `ask`; unmatched shell → `auto` (sandboxed) or `ask` (degraded).
- The sandbox is never silently skipped: no `sandbox-exec` ⇒ every `bash`/`bash_background` call needs approval.
- Shell env: Plan 1 safe keys + `DESK_WORKSPACE` + cache redirects (`XDG_CACHE_HOME`, `npm_config_cache`, `npm_config_store_dir`, `PIP_CACHE_DIR`, `YARN_CACHE_FOLDER`) under `$TMPDIR/desk-cache`.
- An agent with a pending approval is never scheduled; messages to it queue in its inbox.

---

### Task 1: Project settings and policy schemas

**Files:** `packages/protocol/src/settings.ts` (new), `events.ts`, `index.ts`; `packages/core/src/db/schema.ts` (+`projects.settings` json), new migration; `events/projections.ts`; `state/queries.ts`; `runtime/runtime.ts` (`createProject` accepts `settings?: Partial<ProjectSettings>`, `updateProject(id, patch)`).

**Interfaces:**
- `PolicyRule = { tool: string; match?: { branch?: string; command?: string; domain?: string }; action: 'allow'|'ask'|'deny'; delegate_to_desk?: boolean }`
- `ProjectSettings` (zod with defaults per spec §4), `DEFAULT_POLICY: PolicyRule[]`, `resolveSettings(partial?): ProjectSettings`
- Events: `project.created.payload.settings?: Partial<ProjectSettings>`; `project.updated { name?, goal?, instructions?, settings?: Partial<ProjectSettings> }` (settings patch is shallow-merged, then validated; `policy` replaces wholesale).
- `ProjectRow.settings: ProjectSettings` (always fully resolved).

**Tests:** defaults resolve (desk/thread model `claude-opus-5-5`, cap 4, `check_in: normal`, policy = DEFAULT_POLICY); invalid settings rejected; `project.updated` merges and validates; migration applies on fresh DB.

### Task 2: Policy evaluation

**Files:** `packages/core/src/policy/evaluate.ts` (new); `tools/types.ts` (+ `gate` on Tool, `ToolGate`, `PolicySubject`).

**Interfaces:**
- `type PolicySubject = { branch?: string; command?: string; domain?: string }`
- `Tool.gate?: { subject: (input) => PolicySubject; unmatched: 'ask' | 'auto' }`
- `type PolicyDecision = { action: 'auto'|'allow'|'ask'|'deny'; rule?: PolicyRule; delegateToDesk: boolean; reason: string }`
- `evaluatePolicy(tool: Tool, input: unknown, rules: PolicyRule[], opts: { sandboxAvailable: boolean }): PolicyDecision`
- `globToRegExp(glob): RegExp` (`*` → `.*`, `?` → `.`, anchored)

**Tests:** ungated tool → `auto`; `git_push` branch `desk/x` → allow, `main` → deny (default policy); `open_pr` → ask; `web_fetch` → allow; bash `sudo ls`, `rm -rf /`, `rm -rf ~/x`, `curl x | sh` → ask; `rm -rf build`, `echo sudoku`, `ls` → auto; rule with `match.domain: '*.example.com'` matches `api.example.com` only; rule whose match key the subject lacks never matches; degraded mode → unmatched bash is `ask`; invalid regex in a rule is treated as non-matching (never throws).

### Task 3: Sandbox + bash under sandbox-exec

**Files:** `packages/core/src/tools/sandbox.ts` (new); `tools/types.ts` (ToolContext + `sandbox: SandboxSpec`); `tools/bash.ts`; update Plan 1 test ctxs.

**Interfaces:**
- `type SandboxSpec = { enabled: boolean; writable: string[] }`, `NO_SANDBOX: SandboxSpec = { enabled: false, writable: [] }`
- `buildSandboxProfile(writable: string[]): string` (SBPL; paths escaped)
- `detectSandbox(): Promise<boolean>`
- `shellInvocation(command: string, sandbox: SandboxSpec): { command: string; args: string[] }`
- `scrubbedEnv` adds cache redirects.

**Tests (skipped when `detectSandbox()` is false):** write inside workspace succeeds; write to `$HOME/desk-sandbox-escape-test` fails and file does not exist; write to `$TMPDIR` succeeds; `writable: []` blocks workspace writes (bash_readonly shape); profile escapes `"` in paths; env contains cache redirects and not `CLIPROXY_API_KEY`.

### Task 4: Background shell jobs

**Files:** `packages/core/src/tools/jobs.ts` (new: `JobManager`, tools `bash_background`, `bash_output`, `bash_kill`); ToolContext + `jobs: JobManager`.

**Interfaces:** `class JobManager { start(agentId, opts: { command; cwd; env; sandbox }): string; read(agentId, jobId): { status: 'running'|'exited'|'killed'; exitCode: number|null; output: string /* new since last read */ }; kill(agentId, jobId): boolean; killAll(agentId): void }`. Output buffer capped at 1 MB (keeps tail). Jobs are scoped to their agent (reading another agent's job → error).

**Tests:** start `echo a; sleep 0.2; echo b` → read returns incremental output, eventually `exited` code 0; kill a `sleep 30` job → `killed`; killAll kills every job for the agent; cross-agent read rejected; bash_background is gated like bash.

### Task 5: Web tools

**Files:** `packages/core/src/tools/web.ts` (new).

**Interfaces:** `webFetchTool` (`web_fetch {url}`; gate subject `{domain: hostname}`, unmatched ask); `type SearchProvider = (query: string, signal: AbortSignal) => Promise<Array<{ title; url; snippet }>>`; `duckDuckGoProvider(baseUrl?)`, `braveProvider(apiKey, baseUrl?)`, `defaultSearchProvider(env)`; `createWebSearchTool(provider): Tool` (`web_search {query}`; gate subject `{}`, unmatched ask); `webSearchTool` (default provider).

**Tests (local HTTP server):** HTML article → markdown containing heading and paragraph, no `<script>` content; JSON/text returned raw; output capped at 100k chars; non-2xx → error; non-http(s) URL rejected; DDG HTML fixture parsed into results with decoded `uddg` URLs; Brave JSON fixture parsed; tool output formats results as numbered list.

### Task 6: Gate integration in the run loop + approvals in Runtime

**Files:** `packages/protocol/src/events.ts` (+`approval.requested`, `approval.resolved`); `db/schema.ts` (+`approvals` table) + migration; projections; queries (`getApproval`, `listApprovals(projectId, status?)`, `pendingApprovalsFor(agentId)`); `tools/registry.ts` (split `prepareToolCall` / `runPreparedTool`, keep `executeToolCall`); `agent/run.ts`; `runtime/runtime.ts` (+`resolveApproval`, sandbox detection, JobManager ownership, `stop` denies pending approvals and kills jobs); `index.ts` exports.

**Interfaces:**
- `approval.requested { approval_id, run_id, tool_call_id, tool, arguments, reason, delegate_to_desk }`; `approval.resolved { approval_id, decision: 'approved'|'denied', resolved_by: 'user'|'desk'|'system', note? }`
- `RunDeps` + `gate: (tool: Tool, input: unknown, project: ProjectRow) => PolicyDecision` and `toolContext: (agent: AgentRow, runId: string, toolCallId: string, signal: AbortSignal) => ToolContext`
- `Runtime.resolveApproval(approvalId: string, decision: 'approved'|'denied', opts?: { by?: 'user'|'desk'; note?: string }): Promise<void>`; `RuntimeOptions.sandboxAvailable?: boolean` (default: detected at `Runtime.init()`); `Runtime.init(): Promise<void>`.

**Tests (fake model):** `ask` → approval requested, other parallel calls still executed, agent `waiting`, no second model call; approve → tool executes, `tool.result ok`, agent rescheduled and model sees the result; deny with note → `tool.result denied` containing note; message sent while approval pending is queued and not run until resolution; `deny` rule → `tool.result denied` without approval; stop on waiting agent → approvals resolved `denied` by `system`, status `cancelled`; resolving twice → error; degraded sandbox → bash call requires approval.

---

## Done Criteria
`pnpm test`, `pnpm typecheck` green; sandbox tests pass on this Mac; `pnpm test:live` still green.
