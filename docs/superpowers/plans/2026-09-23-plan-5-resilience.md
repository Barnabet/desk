# Desk Plan 5 — Resilience & v1.0 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. TDD per task; commit only when `pnpm test` and `pnpm typecheck` both exit 0.

**Goal:** Desk survives hard crashes, proxy outages, sustained rate limits and long conversations without losing or corrupting agent state; ship v1.0 with docs.

**Spec:** §5.4 (compaction), §5.6 (proxy-down pause, fallback), §5.7 (crash resume), §10, §11 (resilience scenarios), §12 item 3.

## Global Constraints
All earlier constraints, plus:
- Crash recovery never re-executes a tool call whose outcome is unknown: it is recorded as `interrupted` and handed back to the model.
- A proxy outage pauses model calls (health-checked every 10 s) instead of failing agents; one `system.notice` per project per outage (down and up).
- Fallback is run-scoped: after the retry budget is exhausted on `rate_limited`, the rest of that run uses `settings.fallback_model` (if set and different); the agent's configured model is unchanged.
- Compaction triggers when `prompt_tokens ≥ 70%` of the model's `context_window`, and once (forced) on `context_overflow`; it summarises everything except the last 6 conversation messages into a structured checkpoint; originals stay in the event log.

---

### Task 1: Crash recovery
**Files:** protocol (`system.notice`); `runtime.recover()` handles agents left `running` (no `run.finished`): synthetic `tool.result{interrupted}` for every `tool.call` without a result, `run.finished{error, daemon_restart}`, status `queued`, reschedule.
**Tests:** simulated crash (events written as a killed daemon would leave them) → recover appends interrupted results in order and resumes; the model sees the interrupted result; approvals pending across a crash stay pending (agent not rescheduled). **Real crash:** spawn `deskd` as a child process against the fake model, start a thread whose tool hangs, `SIGKILL` the daemon, restart it, assert the interrupted result and completion.

### Task 2: Proxy-down pause
**Files:** `runtime/proxy-gate.ts` (`ProxyGate`: `markDown`, `waitUntilUp(signal)`, health probe injected); run loop retries `proxy_down` through the gate without consuming retry attempts; `system.notice` events.
**Tests:** fake model stops → agent pauses (no failure), one `down` notice per project; fake model restarts on the same port → agent completes, one `up` notice; stop during the pause cancels cleanly.

### Task 3: Fallback model on sustained rate limits
**Files:** protocol (`agent.model_switched {from, to, reason, scope: 'run'}`); run loop.
**Tests:** primary returns 429 until the retry budget is exhausted → `agent.model_switched` event, remaining calls in the run use the fallback, `agent.model` unchanged; no fallback configured → agent `failed` with a rate-limit detail; fallback equal to primary → no switch.

### Task 4: Context compaction
**Files:** protocol (`context.compacted {checkpoint, up_to}`); `agent/compaction.ts` (`shouldCompact`, `compactionPrompt`, split point that never separates an assistant tool-call message from its tool results); transcript builder uses the latest checkpoint + later events; run loop triggers after model calls and on `context_overflow` (once per call).
**Tests:** transcript with a checkpoint renders `[checkpoint]` + tail only; split never orphans tool results; threshold at 70% triggers exactly one compaction call and the next request is smaller; `context_overflow` forces compaction then retries once, a second overflow fails the run; compaction failure leaves the conversation intact.

### Task 5: v1.0 docs and release
**Files:** `README.md` (install, `desk` usage, architecture, config, data layout, safety model), `docs/api.md` (HTTP + stream reference), spec §12/§13 updates and deviations list, `CLAUDE.md` for future agents, root `version` 1.0.0, git tag `v1.0.0`.
**Checks:** full test suite ×2, typecheck, `pnpm test:live` (single-thread + Desk smoke), real `bin/desk` smoke.
