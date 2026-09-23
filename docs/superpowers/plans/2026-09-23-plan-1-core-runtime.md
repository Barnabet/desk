# Desk Plan 1 — Core Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tested `@desk/core` library that runs a single agent (thread role) against models served by the local CLIProxyAPI — streaming, parallel tool calls, file and shell tools, retries, concurrency caps — with every step persisted to an event-sourced SQLite store.

**Architecture:** pnpm monorepo. `@desk/protocol` holds zod schemas for events and domain types. `@desk/core` holds the event store (append-only `events` table + synchronous projections in the same transaction), an OpenAI-SDK model adapter (Chat Completions, streaming), a tool framework with path confinement, the agent run loop, a scheduler, and a `Runtime` facade. `@desk/fake-model` is a scriptable OpenAI-compatible HTTP server used by tests.

**Tech Stack:** Node 22, TypeScript 7 (strict), pnpm 9 workspaces, Vitest 5, zod 4, openai 7, better-sqlite3 13 + drizzle-orm 0.45 (+ drizzle-kit 0.31), ulid 3, fast-glob 3, ripgrep (optional, for `grep`).

**Spec:** `docs/superpowers/specs/2026-09-23-desk-daemon-design.md` (this plan implements §4 event model subset, §5.1–5.3, §5.5, §5.6 caps+retry, §5.8, §6.1 file/shell/complete tools, §7.5, §9.2).

**Plan series:** 1 Core runtime (this) → 2 Safety (policy gate, sandbox-exec, approvals, bash_background) → 3 Coordination (Desk, threads, workspaces/worktrees, memory, library, plan, review) → 4 Daemon (HTTP/WS API, CLI, launchd) → 5 Resilience (compaction, crash-resume, proxy-down pause, fallback, full e2e).

## Global Constraints

- Node `>=22.12`; ESM only (`"type": "module"`); TypeScript `strict: true`, `noUncheckedIndexedAccess: true`.
- Model access uses **Chat Completions only** (`/v1/chat/completions`), never the Responses API.
- Default model id: `claude-opus-5-5`. Other registry seeds: `claude-fable-5-1`, `gpt-6-astra`, `gpt-6-sol`.
- Proxy credentials resolution: `DESK_OPENAI_BASE_URL`/`DESK_OPENAI_API_KEY`, else `~/.config/cliproxyapi.env` (`CLIPROXY_BASE_URL`, `CLIPROXY_API_KEY`). The key is never persisted, logged, or passed to tool processes.
- All IDs are ULIDs; timestamps ISO-8601 UTC.
- Events are the source of truth; projections are updated in the same SQLite transaction as the append.
- Tool-call IDs are opaque strings (`toolu_…` and `call_…` both occur).
- Tool output over 20,000 characters is truncated in context and saved in full to `<workspace>/.desk/outputs/<tool_call_id>.txt`.
- Retry: full-jitter exponential backoff, base 2000 ms, cap 60000 ms, max 6 attempts, for `rate_limited` and `transient` errors only.
- Step limits per run: 200 (thread), 60 (desk).
- Shell env passed to tools contains only `PATH, HOME, LANG, TERM, TMPDIR, USER, SHELL` plus `DESK_WORKSPACE`.
- Every commit message ends with the trailer line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

## File Structure

```
package.json                     root scripts (test, test:live, typecheck), dev deps
pnpm-workspace.yaml              packages/*, apps/*, test/*
tsconfig.json                    single strict config covering all src dirs
vitest.config.ts                 unit/integration by default; *.live.test.ts only with DESK_LIVE=1
.gitignore
README.md

packages/protocol/
  package.json
  src/index.ts                   re-exports
  src/version.ts                 PROTOCOL_VERSION
  src/domain.ts                  AgentRole, AgentStatus, RunFinishReason, ToolResultStatus, ToolCall, ModelInfo
  src/events.ts                  EventBody union, EventInput, StoredEvent, EventOf, EphemeralEvent, INBOX_EVENT_TYPES

test/fake-model/
  package.json
  src/index.ts                   startFakeModel + reply builders

packages/core/
  package.json
  drizzle.config.ts
  drizzle/                       generated SQL migrations
  src/index.ts                   public exports
  src/ids.ts                     newId()
  src/db/schema.ts               drizzle tables: events, projects, agents, usage_totals
  src/db/open.ts                 openDb(): WAL, migrations; Db and Tx types
  src/events/store.ts            EventStore: append (transactional), list, subscribe, publishEphemeral
  src/events/projections.ts      applyProjections(tx, event)
  src/state/queries.ts           getProject, getAgent, listAgents, getUsageTotals; row types
  src/model/types.ts             ChatMessage, ToolSpec, CompletionRequest/Result, ModelAdapter
  src/model/config.ts            loadModelConfig, parseEnvFile, normalizeBaseURL
  src/model/registry.ts          SEED_MODELS, DEFAULT_MODEL_ID, ModelRegistry
  src/model/errors.ts            ModelError, classifyModelError
  src/model/adapter.ts           createModelAdapter (streaming, tool-call assembly, usage)
  src/model/retry.ts             withRetry, abortableSleep
  src/tools/types.ts             Tool, ToolContext, ToolOutput, ToolResult, ToolDenied, defineTool
  src/tools/paths.ts             resolveInside (realpath-based confinement)
  src/tools/registry.ts          toToolSpecs, executeToolCall (validation, errors, truncation)
  src/tools/process.ts           runProcess (process-group kill, timeout, abort)
  src/tools/fs.ts                read_file, write_file, edit_file, list_dir, glob, grep
  src/tools/bash.ts              bash tool, scrubbedEnv
  src/tools/thread.ts            complete tool
  src/agent/transcript.ts        buildConversation(events) → ChatMessage[]
  src/agent/inbox.ts             drainInbox, hasPendingInbox
  src/agent/prompts.ts           threadSystemPrompt
  src/agent/run.ts               runAgent loop
  src/runtime/scheduler.ts       Scheduler (model + project caps, priority, stop, whenIdle)
  src/runtime/runtime.ts         Runtime facade
  src/testing/harness.ts         test harness (fake model + in-memory db + tmp dir)
  src/live.live.test.ts          opt-in live smoke test against the proxy
```

---

### Task 1: Monorepo scaffold

**Files:**
- Create: `package.json`, `pnpm-workspace.yaml`, `tsconfig.json`, `vitest.config.ts`, `.gitignore`, `README.md`
- Create: `packages/protocol/package.json`, `packages/protocol/src/index.ts`, `packages/protocol/src/version.ts`
- Create: `packages/core/package.json`, `packages/core/src/index.ts`
- Create: `test/fake-model/package.json`, `test/fake-model/src/index.ts`
- Test: `packages/protocol/src/version.test.ts`

**Interfaces:**
- Produces: workspace packages `@desk/protocol`, `@desk/core`, `@desk/fake-model` resolvable by name (each `exports` `./src/index.ts`); root scripts `pnpm test`, `pnpm test:live`, `pnpm typecheck`; `PROTOCOL_VERSION = 1` from `@desk/protocol`.

- [ ] **Step 1: Create root files**

`package.json`:
```json
{
  "name": "desk",
  "private": true,
  "type": "module",
  "packageManager": "pnpm@9.12.1",
  "engines": { "node": ">=22.12" },
  "scripts": {
    "test": "vitest run",
    "test:live": "DESK_LIVE=1 vitest run",
    "typecheck": "tsc --noEmit -p tsconfig.json"
  },
  "devDependencies": {
    "@types/node": "^22.10.0",
    "tsx": "^4.23.15",
    "typescript": "^7.0.2",
    "vitest": "^5.0.1"
  },
  "pnpm": {
    "onlyBuiltDependencies": ["esbuild"]
  }
}
```

`pnpm-workspace.yaml`:
```yaml
packages:
  - packages/*
  - apps/*
  - test/*
```

`tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2023",
    "lib": ["ES2023"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "types": ["node"],
    "noEmit": true
  },
  "include": ["packages/*/src", "apps/*/src", "test/*/src", "vitest.config.ts"]
}
```

`vitest.config.ts`:
```ts
import { defineConfig } from 'vitest/config';

const live = process.env.DESK_LIVE === '1';
const roots = ['packages/*/src', 'apps/*/src', 'test/*/src'];

export default defineConfig({
  test: {
    include: roots.map((r) => `${r}/**/${live ? '*.live.test.ts' : '*.test.ts'}`),
    exclude: ['**/node_modules/**', ...(live ? [] : ['**/*.live.test.ts'])],
    testTimeout: live ? 300_000 : 15_000,
  },
});
```

`.gitignore`:
```
node_modules/
*.db
*.db-wal
*.db-shm
.DS_Store
coverage/
```

`README.md`:
```markdown
# Desk

Local-first project coordinator: a per-project **Desk** agent scopes work, dispatches parallel **threads**, reviews and assembles results.

- Spec: `docs/superpowers/specs/2026-09-23-desk-daemon-design.md`
- Plans: `docs/superpowers/plans/`

## Development

    pnpm install
    pnpm test          # unit + integration (fake model server)
    pnpm test:live     # live smoke tests against the local CLIProxyAPI
    pnpm typecheck
```

- [ ] **Step 2: Create package skeletons**

`packages/protocol/package.json`:
```json
{
  "name": "@desk/protocol",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "exports": { ".": "./src/index.ts" },
  "dependencies": { "zod": "^4.6.5" }
}
```

`packages/protocol/src/version.ts`:
```ts
export const PROTOCOL_VERSION = 1;
```

`packages/protocol/src/index.ts`:
```ts
export * from './version';
```

`packages/core/package.json`:
```json
{
  "name": "@desk/core",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "exports": { ".": "./src/index.ts" },
  "dependencies": {
    "@desk/protocol": "workspace:*",
    "better-sqlite3": "^13.0.3",
    "drizzle-orm": "^0.45.3",
    "fast-glob": "^3.3.3",
    "openai": "^7.23.0",
    "ulid": "^3.0.2",
    "zod": "^4.6.5"
  },
  "devDependencies": {
    "@desk/fake-model": "workspace:*",
    "@types/better-sqlite3": "^9.6.0",
    "drizzle-kit": "^0.31.11"
  }
}
```

`packages/core/src/index.ts`:
```ts
export {};
```

`test/fake-model/package.json`:
```json
{
  "name": "@desk/fake-model",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "exports": { ".": "./src/index.ts" },
  "devDependencies": { "openai": "^7.23.0" }
}
```

`test/fake-model/src/index.ts`:
```ts
export {};
```

- [ ] **Step 3: Write the failing test**

`packages/protocol/src/version.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { PROTOCOL_VERSION } from '@desk/protocol';

describe('protocol', () => {
  it('exposes the protocol version', () => {
    expect(PROTOCOL_VERSION).toBe(1);
  });
});
```

- [ ] **Step 4: Install and run**

Run: `pnpm install`
Expected: completes. `better-sqlite3` v13 ships N-API prebuilds inside the package, so it is deliberately **not** in `onlyBuiltDependencies` (a source build needs Xcode and is unnecessary).

Run: `pnpm test`
Expected: PASS — 1 test (`exposes the protocol version`). This verifies workspace-name resolution through `exports` → `.ts` source works under Vitest.

Run: `pnpm typecheck`
Expected: exits 0.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: scaffold pnpm monorepo (protocol, core, fake-model)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Protocol — domain and event schemas

**Files:**
- Create: `packages/protocol/src/domain.ts`, `packages/protocol/src/events.ts`
- Modify: `packages/protocol/src/index.ts`
- Test: `packages/protocol/src/events.test.ts`

**Interfaces:**
- Produces (all exported from `@desk/protocol`, each as both zod schema and inferred type of the same name):
  - `AgentRole = 'desk' | 'thread'`
  - `AgentStatus = 'idle' | 'queued' | 'running' | 'waiting' | 'done' | 'failed' | 'cancelled'`
  - `RunFinishReason = 'yielded' | 'no_tool_calls' | 'max_steps' | 'stopped' | 'error'`
  - `ToolResultStatus = 'ok' | 'error' | 'denied' | 'interrupted'`
  - `ToolCall = { id: string; name: string; arguments: string }`
  - `ModelInfo = { id; family: 'claude' | 'gpt'; context_window; max_output_tokens; supports_reasoning_effort; concurrency }`
  - `EventBody` (discriminated union on `type`), `EventType`, `EventInput = EventBody & { project_id: string; agent_id: string | null }`, `StoredEvent = EventInput & { id: number; ts: string }`, `EventOf<T>`, `EphemeralEvent` (`assistant.delta`), `INBOX_EVENT_TYPES = ['message.user']`.

- [ ] **Step 1: Write the failing test**

`packages/protocol/src/events.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { EventBody, EphemeralEvent, INBOX_EVENT_TYPES } from '@desk/protocol';

describe('EventBody', () => {
  it('parses a tool.result event', () => {
    const parsed = EventBody.parse({
      type: 'tool.result',
      payload: { run_id: 'r1', tool_call_id: 'toolu_1', name: 'read_file', status: 'ok', content: 'hi' },
    });
    expect(parsed.type).toBe('tool.result');
  });

  it('parses an assistant.message with tool calls', () => {
    const parsed = EventBody.parse({
      type: 'assistant.message',
      payload: { run_id: 'r1', content: null, tool_calls: [{ id: 'call_1', name: 'bash', arguments: '{}' }] },
    });
    expect(parsed.type === 'assistant.message' && parsed.payload.tool_calls).toHaveLength(1);
  });

  it('rejects unknown event types', () => {
    expect(() => EventBody.parse({ type: 'nope', payload: {} })).toThrow();
  });

  it('rejects an empty user message', () => {
    expect(() => EventBody.parse({ type: 'message.user', payload: { text: '' } })).toThrow();
  });

  it('rejects an invalid agent status', () => {
    expect(() => EventBody.parse({ type: 'agent.status_changed', payload: { status: 'sleeping' } })).toThrow();
  });
});

describe('EphemeralEvent', () => {
  it('parses assistant.delta', () => {
    const e = EphemeralEvent.parse({ type: 'assistant.delta', project_id: 'p', agent_id: 'a', payload: { run_id: 'r', text: 'he' } });
    expect(e.payload.text).toBe('he');
  });
});

describe('INBOX_EVENT_TYPES', () => {
  it('contains message.user', () => {
    expect(INBOX_EVENT_TYPES).toContain('message.user');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run packages/protocol/src/events.test.ts`
Expected: FAIL — `EventBody` is not exported.

- [ ] **Step 3: Implement**

`packages/protocol/src/domain.ts`:
```ts
import { z } from 'zod';

export const AgentRole = z.enum(['desk', 'thread']);
export type AgentRole = z.infer<typeof AgentRole>;

export const AgentStatus = z.enum(['idle', 'queued', 'running', 'waiting', 'done', 'failed', 'cancelled']);
export type AgentStatus = z.infer<typeof AgentStatus>;

export const RunFinishReason = z.enum(['yielded', 'no_tool_calls', 'max_steps', 'stopped', 'error']);
export type RunFinishReason = z.infer<typeof RunFinishReason>;

export const ToolResultStatus = z.enum(['ok', 'error', 'denied', 'interrupted']);
export type ToolResultStatus = z.infer<typeof ToolResultStatus>;

export const ToolCall = z.object({ id: z.string(), name: z.string(), arguments: z.string() });
export type ToolCall = z.infer<typeof ToolCall>;

export const ModelInfo = z.object({
  id: z.string().min(1),
  family: z.enum(['claude', 'gpt']),
  context_window: z.number().int().positive(),
  max_output_tokens: z.number().int().positive(),
  supports_reasoning_effort: z.boolean(),
  concurrency: z.number().int().min(1),
});
export type ModelInfo = z.infer<typeof ModelInfo>;
```

`packages/protocol/src/events.ts`:
```ts
import { z } from 'zod';
import { AgentRole, AgentStatus, RunFinishReason, ToolCall, ToolResultStatus } from './domain';

const event = <T extends string, P extends z.ZodType>(type: T, payload: P) =>
  z.object({ type: z.literal(type), payload });

export const EventBody = z.discriminatedUnion('type', [
  event('project.created', z.object({ name: z.string().min(1), goal: z.string(), instructions: z.string() })),
  event(
    'agent.created',
    z.object({
      role: AgentRole,
      model: z.string().min(1),
      title: z.string().nullable(),
      brief: z.string().nullable(),
      workspace_path: z.string().nullable(),
      parent_id: z.string().nullable(),
    }),
  ),
  event('agent.status_changed', z.object({ status: AgentStatus, reason: z.string().optional() })),
  event('message.user', z.object({ text: z.string().min(1) })),
  event('inbox.drained', z.object({ run_id: z.string(), up_to: z.number().int() })),
  event('run.started', z.object({ run_id: z.string(), model: z.string() })),
  event('run.finished', z.object({ run_id: z.string(), reason: RunFinishReason, detail: z.string().optional() })),
  event(
    'assistant.message',
    z.object({ run_id: z.string(), content: z.string().nullable(), tool_calls: z.array(ToolCall) }),
  ),
  event(
    'tool.call',
    z.object({ run_id: z.string(), tool_call_id: z.string(), name: z.string(), arguments: z.string() }),
  ),
  event(
    'tool.result',
    z.object({
      run_id: z.string(),
      tool_call_id: z.string(),
      name: z.string(),
      status: ToolResultStatus,
      content: z.string(),
    }),
  ),
  event(
    'usage',
    z.object({
      run_id: z.string(),
      model: z.string(),
      prompt_tokens: z.number().int().nonnegative(),
      completion_tokens: z.number().int().nonnegative(),
      cached_tokens: z.number().int().nonnegative().optional(),
      estimated: z.boolean(),
    }),
  ),
]);
export type EventBody = z.infer<typeof EventBody>;
export type EventType = EventBody['type'];

export type EventInput = EventBody & { project_id: string; agent_id: string | null };
export type StoredEvent = EventInput & { id: number; ts: string };
export type EventOf<T extends EventType> = Extract<StoredEvent, { type: T }>;

export const EphemeralEvent = z.discriminatedUnion('type', [
  z.object({
    type: z.literal('assistant.delta'),
    project_id: z.string(),
    agent_id: z.string(),
    payload: z.object({ run_id: z.string(), text: z.string() }),
  }),
]);
export type EphemeralEvent = z.infer<typeof EphemeralEvent>;

/** Event types that land in an agent's inbox and trigger/steer runs. */
export const INBOX_EVENT_TYPES = ['message.user'] as const satisfies readonly EventType[];
```

`packages/protocol/src/index.ts`:
```ts
export * from './version';
export * from './domain';
export * from './events';
```

- [ ] **Step 4: Run tests and typecheck**

Run: `pnpm vitest run packages/protocol && pnpm typecheck`
Expected: PASS (8 tests), typecheck exits 0.

- [ ] **Step 5: Commit**

```bash
git add packages/protocol
git commit -m "feat(protocol): domain and event schemas" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Fake OpenAI-compatible model server

**Files:**
- Modify: `test/fake-model/src/index.ts`
- Test: `test/fake-model/src/server.test.ts`

**Interfaces:**
- Produces (from `@desk/fake-model`):
  - `type FakeToolCall = { name: string; args: Record<string, unknown>; id?: string }`
  - `type FakeReply = { kind: 'reply'; text?: string; toolCalls?: FakeToolCall[]; usage?: { prompt_tokens: number; completion_tokens: number } | 'omit'; delayMs?: number } | { kind: 'error'; status: number; type: string; message: string } | { kind: 'hang' }`
  - `type ChatRequest` (parsed request body), `type Script = (req: ChatRequest, index: number) => FakeReply`
  - builders: `text(t, extra?)`, `tools(...calls)`, `call(name, args?, id?)`, `error(status, type, message?)`, `hang()`
  - `startFakeModel(script?: Script | FakeReply[]): Promise<FakeModelServer>` where `FakeModelServer = { url: string /* ends with /v1 */; requests: ChatRequest[]; readonly inFlight: number; readonly maxInFlight: number; setScript(s): void; close(): Promise<void> }`
  - An array script is consumed in order; when exhausted it answers HTTP 500 `script_exhausted`.

- [ ] **Step 1: Write the failing test**

`test/fake-model/src/server.test.ts`:
```ts
import OpenAI from 'openai';
import { afterEach, describe, expect, it } from 'vitest';
import { call, error, startFakeModel, text, tools, type FakeModelServer } from '@desk/fake-model';

let fake: FakeModelServer;
afterEach(async () => fake?.close());

const client = () => new OpenAI({ baseURL: fake.url, apiKey: 'test', maxRetries: 0 });

describe('fake model server', () => {
  it('streams text and usage', async () => {
    fake = await startFakeModel([text('hello world, streaming', { usage: { prompt_tokens: 7, completion_tokens: 3 } })]);
    const stream = await client().chat.completions.create({
      model: 'fake-model',
      messages: [{ role: 'user', content: 'hi' }],
      stream: true,
      stream_options: { include_usage: true },
    });
    let content = '';
    let usage: unknown;
    for await (const chunk of stream) {
      content += chunk.choices[0]?.delta?.content ?? '';
      if (chunk.usage) usage = chunk.usage;
    }
    expect(content).toBe('hello world, streaming');
    expect(usage).toMatchObject({ prompt_tokens: 7, completion_tokens: 3 });
    expect(fake.requests).toHaveLength(1);
  });

  it('streams parallel tool calls with split arguments', async () => {
    fake = await startFakeModel([tools(call('read_file', { path: 'a.md' }, 'toolu_1'), call('list_dir', { path: 'src' }))]);
    const stream = await client().chat.completions.create({ model: 'fake-model', messages: [{ role: 'user', content: 'x' }], stream: true });
    const calls: Record<number, { id: string; name: string; args: string }> = {};
    let finish = '';
    for await (const chunk of stream) {
      const choice = chunk.choices[0];
      if (choice?.finish_reason) finish = choice.finish_reason;
      for (const tc of choice?.delta?.tool_calls ?? []) {
        const c = (calls[tc.index] ??= { id: '', name: '', args: '' });
        if (tc.id) c.id = tc.id;
        if (tc.function?.name) c.name += tc.function.name;
        if (tc.function?.arguments) c.args += tc.function.arguments;
      }
    }
    expect(finish).toBe('tool_calls');
    expect(calls[0]).toEqual({ id: 'toolu_1', name: 'read_file', args: '{"path":"a.md"}' });
    expect(calls[1]?.name).toBe('list_dir');
    expect(calls[1]?.id).toMatch(/^call_fake_/);
  });

  it('returns errors with status and type', async () => {
    fake = await startFakeModel([error(429, 'rate_limit_error', 'slow down')]);
    await expect(
      client().chat.completions.create({ model: 'fake-model', messages: [{ role: 'user', content: 'x' }] }),
    ).rejects.toMatchObject({ status: 429 });
  });

  it('answers 500 when the script is exhausted', async () => {
    fake = await startFakeModel([]);
    await expect(
      client().chat.completions.create({ model: 'fake-model', messages: [{ role: 'user', content: 'x' }] }),
    ).rejects.toMatchObject({ status: 500 });
  });

  it('tracks max in-flight requests', async () => {
    fake = await startFakeModel(() => text('ok', { delayMs: 100 }));
    const c = client();
    await Promise.all(
      [1, 2, 3].map(() => c.chat.completions.create({ model: 'fake-model', messages: [{ role: 'user', content: 'x' }] })),
    );
    expect(fake.maxInFlight).toBe(3);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run test/fake-model`
Expected: FAIL — `startFakeModel` is not exported.

- [ ] **Step 3: Implement**

`test/fake-model/src/index.ts`:
```ts
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';

export type FakeToolCall = { name: string; args: Record<string, unknown>; id?: string };
export type FakeReply =
  | {
      kind: 'reply';
      text?: string;
      toolCalls?: FakeToolCall[];
      usage?: { prompt_tokens: number; completion_tokens: number } | 'omit';
      delayMs?: number;
    }
  | { kind: 'error'; status: number; type: string; message: string }
  | { kind: 'hang' };
export type ChatRequest = {
  model: string;
  messages: Array<Record<string, any>>;
  tools?: Array<Record<string, any>>;
  stream?: boolean;
  stream_options?: { include_usage?: boolean };
  [key: string]: unknown;
};
export type Script = (req: ChatRequest, index: number) => FakeReply;

type ReplyExtra = Omit<Extract<FakeReply, { kind: 'reply' }>, 'kind' | 'text'>;
export const text = (t: string, extra: ReplyExtra = {}): FakeReply => ({ kind: 'reply', text: t, ...extra });
export const tools = (...calls: FakeToolCall[]): FakeReply => ({ kind: 'reply', toolCalls: calls });
export const call = (name: string, args: Record<string, unknown> = {}, id?: string): FakeToolCall => ({
  name,
  args,
  ...(id ? { id } : {}),
});
export const error = (status: number, type: string, message = type): FakeReply => ({ kind: 'error', status, type, message });
export const hang = (): FakeReply => ({ kind: 'hang' });

export interface FakeModelServer {
  url: string;
  requests: ChatRequest[];
  readonly inFlight: number;
  readonly maxInFlight: number;
  setScript(script: Script | FakeReply[]): void;
  close(): Promise<void>;
}

function toScript(s: Script | FakeReply[]): Script {
  if (typeof s === 'function') return s;
  const queue = [...s];
  return () => queue.shift() ?? { kind: 'error', status: 500, type: 'script_exhausted', message: 'Fake model script exhausted' };
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (c: Buffer) => (data += c.toString('utf8')));
    req.on('end', () => resolve(data));
    req.on('error', reject);
  });
}

function json(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(JSON.stringify(body));
}

function sse(res: ServerResponse, body: unknown): void {
  res.write(`data: ${JSON.stringify(body)}\n\n`);
}

function split(s: string, size: number): string[] {
  const out: string[] = [];
  for (let i = 0; i < s.length; i += size) out.push(s.slice(i, i + size));
  return out;
}

export async function startFakeModel(initial: Script | FakeReply[] = []): Promise<FakeModelServer> {
  let script = toScript(initial);
  const requests: ChatRequest[] = [];
  let inFlight = 0;
  let maxInFlight = 0;
  let callIndex = 0;
  let idCounter = 0;

  const server = createServer(async (req, res) => {
    if (req.method === 'GET' && req.url === '/v1/models') {
      return json(res, 200, { object: 'list', data: [{ id: 'fake-model', object: 'model', owned_by: 'fake' }] });
    }
    if (req.method !== 'POST' || req.url !== '/v1/chat/completions') {
      return json(res, 404, { error: { type: 'not_found', message: 'not found' } });
    }
    let body: ChatRequest;
    try {
      body = JSON.parse(await readBody(req)) as ChatRequest;
    } catch {
      return json(res, 400, { error: { type: 'invalid_request_error', message: 'bad json' } });
    }
    requests.push(body);
    inFlight++;
    maxInFlight = Math.max(maxInFlight, inFlight);
    res.on('close', () => {
      inFlight--;
    });

    const reply = script(body, callIndex++);
    if (reply.kind === 'hang') return;
    if (reply.kind === 'error') return json(res, reply.status, { error: { type: reply.type, message: reply.message } });
    if (reply.delayMs) await sleep(reply.delayMs);
    if (res.destroyed) return;

    const toolCalls = (reply.toolCalls ?? []).map((c) => ({
      id: c.id ?? `call_fake_${++idCounter}`,
      name: c.name,
      arguments: JSON.stringify(c.args),
    }));
    const usage =
      reply.usage === 'omit'
        ? undefined
        : (reply.usage ?? { prompt_tokens: Math.ceil(JSON.stringify(body.messages).length / 4), completion_tokens: 10 });
    const usageBody = usage ? { ...usage, total_tokens: usage.prompt_tokens + usage.completion_tokens } : undefined;
    const finish = toolCalls.length ? 'tool_calls' : 'stop';

    if (!body.stream) {
      return json(res, 200, {
        id: 'chatcmpl-fake',
        object: 'chat.completion',
        created: 0,
        model: body.model,
        choices: [
          {
            index: 0,
            message: {
              role: 'assistant',
              content: reply.text ?? null,
              ...(toolCalls.length
                ? {
                    tool_calls: toolCalls.map((tc) => ({
                      id: tc.id,
                      type: 'function',
                      function: { name: tc.name, arguments: tc.arguments },
                    })),
                  }
                : {}),
            },
            finish_reason: finish,
          },
        ],
        ...(usageBody ? { usage: usageBody } : {}),
      });
    }

    res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive' });
    const chunk = (delta: object, finish_reason: string | null = null) =>
      sse(res, {
        id: 'chatcmpl-fake',
        object: 'chat.completion.chunk',
        created: 0,
        model: body.model,
        choices: [{ index: 0, delta, finish_reason }],
      });
    chunk({ role: 'assistant', content: '' });
    for (const piece of split(reply.text ?? '', 8)) chunk({ content: piece });
    toolCalls.forEach((tc, index) => {
      chunk({ tool_calls: [{ index, id: tc.id, type: 'function', function: { name: tc.name, arguments: '' } }] });
      for (const piece of split(tc.arguments, Math.max(1, Math.ceil(tc.arguments.length / 2)))) {
        chunk({ tool_calls: [{ index, function: { arguments: piece } }] });
      }
    });
    chunk({}, finish);
    if (usageBody && body.stream_options?.include_usage) {
      sse(res, { id: 'chatcmpl-fake', object: 'chat.completion.chunk', created: 0, model: body.model, choices: [], usage: usageBody });
    }
    res.write('data: [DONE]\n\n');
    res.end();
  });

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;

  return {
    url: `http://127.0.0.1:${port}/v1`,
    requests,
    get inFlight() {
      return inFlight;
    },
    get maxInFlight() {
      return maxInFlight;
    },
    setScript(s) {
      script = toScript(s);
      callIndex = 0;
    },
    close: () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections();
        server.close(() => resolve());
      }),
  };
}
```

- [ ] **Step 4: Run tests and typecheck**

Run: `pnpm vitest run test/fake-model && pnpm typecheck`
Expected: PASS (5 tests), typecheck exits 0.

- [ ] **Step 5: Commit**

```bash
git add test/fake-model
git commit -m "test: scriptable OpenAI-compatible fake model server" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Database, event store and projections

**Files:**
- Create: `packages/core/src/ids.ts`, `packages/core/src/db/schema.ts`, `packages/core/src/db/open.ts`, `packages/core/drizzle.config.ts`, `packages/core/drizzle/*` (generated)
- Create: `packages/core/src/events/store.ts`, `packages/core/src/events/projections.ts`, `packages/core/src/state/queries.ts`
- Test: `packages/core/src/events/store.test.ts`

**Interfaces:**
- Consumes: `EventBody, EventInput, StoredEvent, EventType, EphemeralEvent` from `@desk/protocol`.
- Produces:
  - `newId(): string` (ULID)
  - `openDb(file?: string): { db: Db; close(): void }` — default `':memory:'`, WAL, migrations applied; `type Db`, `type Tx`
  - `class EventStore { constructor(db: Db, now?: () => Date); readonly db: Db; append(input: EventInput | EventInput[]): StoredEvent[]; list(q?: ListQuery): StoredEvent[]; subscribe(listener: (item: StreamItem) => void): () => void; publishEphemeral(e: EphemeralEvent): void }`
  - `type ListQuery = { projectId?: string; agentId?: string; after?: number; types?: readonly EventType[]; limit?: number }`
  - `type StreamItem = { kind: 'event'; event: StoredEvent } | { kind: 'ephemeral'; event: EphemeralEvent }`
  - `getProject(db, id): ProjectRow | undefined`, `getAgent(db, id): AgentRow | undefined`, `listAgents(db, projectId): AgentRow[]`, `getUsageTotals(db, projectId): UsageRow[]`
  - `AgentRow` fields: `id, project_id, role, status, model, title, brief, workspace_path, parent_id, inbox_cursor, created_at, updated_at`; `ProjectRow`: `id, name, goal, instructions, created_at`; `UsageRow`: `project_id, agent_id, model, day, prompt_tokens, completion_tokens`.

- [ ] **Step 1: Write the failing test**

`packages/core/src/events/store.test.ts`:
```ts
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { EventInput } from '@desk/protocol';
import { openDb } from '../db/open';
import { getAgent, getProject, getUsageTotals, listAgents } from '../state/queries';
import { EventStore, type StreamItem } from './store';

let close: () => void;
let store: EventStore;

beforeEach(() => {
  const opened = openDb(':memory:');
  close = opened.close;
  store = new EventStore(opened.db, () => new Date('2026-09-23T10:00:00.000Z'));
});
afterEach(() => close());

const project = (): EventInput => ({
  project_id: 'p1',
  agent_id: null,
  type: 'project.created',
  payload: { name: 'Demo', goal: 'Ship it', instructions: '' },
});
const agent = (id = 'a1'): EventInput => ({
  project_id: 'p1',
  agent_id: id,
  type: 'agent.created',
  payload: { role: 'thread', model: 'claude-opus-5-5', title: 'T', brief: 'B', workspace_path: '/tmp/w', parent_id: null },
});

describe('EventStore', () => {
  it('assigns increasing ids and timestamps', () => {
    const [a, b] = store.append([project(), agent()]);
    expect(a!.id).toBeLessThan(b!.id);
    expect(a!.ts).toBe('2026-09-23T10:00:00.000Z');
  });

  it('lists events filtered by agent, type and cursor', () => {
    const [, created] = store.append([project(), agent()]);
    store.append({ project_id: 'p1', agent_id: 'a1', type: 'message.user', payload: { text: 'hi' } });
    expect(store.list({ agentId: 'a1' }).map((e) => e.type)).toEqual(['agent.created', 'message.user']);
    expect(store.list({ types: ['message.user'] })).toHaveLength(1);
    expect(store.list({ after: created!.id }).map((e) => e.type)).toEqual(['message.user']);
    expect(store.list({ limit: 1 })).toHaveLength(1);
  });

  it('projects projects and agents', () => {
    store.append([project(), agent()]);
    expect(getProject(store.db, 'p1')?.name).toBe('Demo');
    const a = getAgent(store.db, 'a1');
    expect(a).toMatchObject({ role: 'thread', status: 'idle', inbox_cursor: 0, workspace_path: '/tmp/w' });
    expect(listAgents(store.db, 'p1')).toHaveLength(1);
  });

  it('projects status changes and inbox cursor', () => {
    store.append([project(), agent()]);
    store.append({ project_id: 'p1', agent_id: 'a1', type: 'agent.status_changed', payload: { status: 'running' } });
    store.append({ project_id: 'p1', agent_id: 'a1', type: 'inbox.drained', payload: { run_id: 'r', up_to: 42 } });
    expect(getAgent(store.db, 'a1')).toMatchObject({ status: 'running', inbox_cursor: 42 });
  });

  it('aggregates usage per day', () => {
    store.append([project(), agent()]);
    const usage = (p: number, c: number): EventInput => ({
      project_id: 'p1',
      agent_id: 'a1',
      type: 'usage',
      payload: { run_id: 'r', model: 'claude-opus-5-5', prompt_tokens: p, completion_tokens: c, estimated: false },
    });
    store.append([usage(100, 10), usage(50, 5)]);
    expect(getUsageTotals(store.db, 'p1')).toEqual([
      { project_id: 'p1', agent_id: 'a1', model: 'claude-opus-5-5', day: '2026-09-23', prompt_tokens: 150, completion_tokens: 15 },
    ]);
  });

  it('rejects invalid payloads and rolls back the whole batch', () => {
    expect(() =>
      store.append([project(), { project_id: 'p1', agent_id: 'a1', type: 'message.user', payload: { text: '' } }]),
    ).toThrow();
    expect(store.list()).toHaveLength(0);
    expect(getProject(store.db, 'p1')).toBeUndefined();
  });

  it('notifies subscribers after commit, including ephemeral events', () => {
    const seen: StreamItem[] = [];
    const unsubscribe = store.subscribe((i) => seen.push(i));
    store.append(project());
    store.publishEphemeral({ type: 'assistant.delta', project_id: 'p1', agent_id: 'a1', payload: { run_id: 'r', text: 'x' } });
    unsubscribe();
    store.append(agent());
    expect(seen.map((i) => i.kind)).toEqual(['event', 'ephemeral']);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run packages/core/src/events`
Expected: FAIL — cannot resolve `../db/open`.

- [ ] **Step 3: Implement schema and generate the migration**

`packages/core/src/ids.ts`:
```ts
import { ulid } from 'ulid';

export const newId = (): string => ulid();
```

`packages/core/src/db/schema.ts` (no imports from workspace packages — drizzle-kit loads this file directly):
```ts
import { index, integer, primaryKey, sqliteTable, text } from 'drizzle-orm/sqlite-core';

export const events = sqliteTable(
  'events',
  {
    id: integer('id').primaryKey({ autoIncrement: true }),
    project_id: text('project_id').notNull(),
    agent_id: text('agent_id'),
    type: text('type').notNull(),
    payload: text('payload', { mode: 'json' }).notNull(),
    ts: text('ts').notNull(),
  },
  (t) => [index('events_project_idx').on(t.project_id, t.id), index('events_agent_idx').on(t.agent_id, t.id)],
);

export const projects = sqliteTable('projects', {
  id: text('id').primaryKey(),
  name: text('name').notNull(),
  goal: text('goal').notNull(),
  instructions: text('instructions').notNull(),
  created_at: text('created_at').notNull(),
});

export const agents = sqliteTable(
  'agents',
  {
    id: text('id').primaryKey(),
    project_id: text('project_id').notNull(),
    role: text('role', { enum: ['desk', 'thread'] }).notNull(),
    status: text('status', { enum: ['idle', 'queued', 'running', 'waiting', 'done', 'failed', 'cancelled'] }).notNull(),
    model: text('model').notNull(),
    title: text('title'),
    brief: text('brief'),
    workspace_path: text('workspace_path'),
    parent_id: text('parent_id'),
    inbox_cursor: integer('inbox_cursor').notNull().default(0),
    created_at: text('created_at').notNull(),
    updated_at: text('updated_at').notNull(),
  },
  (t) => [index('agents_project_idx').on(t.project_id)],
);

export const usageTotals = sqliteTable(
  'usage_totals',
  {
    project_id: text('project_id').notNull(),
    agent_id: text('agent_id').notNull(),
    model: text('model').notNull(),
    day: text('day').notNull(),
    prompt_tokens: integer('prompt_tokens').notNull(),
    completion_tokens: integer('completion_tokens').notNull(),
  },
  (t) => [primaryKey({ columns: [t.project_id, t.agent_id, t.model, t.day] })],
);
```

`packages/core/drizzle.config.ts`:
```ts
import { defineConfig } from 'drizzle-kit';

export default defineConfig({
  dialect: 'sqlite',
  schema: './src/db/schema.ts',
  out: './drizzle',
});
```

Run: `pnpm --filter @desk/core exec drizzle-kit generate --name init`
Expected: creates `packages/core/drizzle/0000_init.sql` and `packages/core/drizzle/meta/`. Inspect the SQL: it must contain `CREATE TABLE \`events\``, `\`projects\``, `\`agents\``, `\`usage_totals\``.

- [ ] **Step 4: Implement open, store, projections, queries**

`packages/core/src/db/open.ts`:
```ts
import { fileURLToPath } from 'node:url';
import Database from 'better-sqlite3';
import { drizzle, type BetterSQLite3Database } from 'drizzle-orm/better-sqlite3';
import { migrate } from 'drizzle-orm/better-sqlite3/migrator';
import * as schema from './schema';

export type Db = BetterSQLite3Database<typeof schema>;
export type Tx = Parameters<Parameters<Db['transaction']>[0]>[0];

const MIGRATIONS = fileURLToPath(new URL('../../drizzle', import.meta.url));

export function openDb(file = ':memory:'): { db: Db; close(): void } {
  const sqlite = new Database(file);
  sqlite.pragma('journal_mode = WAL');
  sqlite.pragma('busy_timeout = 5000');
  const db = drizzle(sqlite, { schema });
  migrate(db, { migrationsFolder: MIGRATIONS });
  return { db, close: () => sqlite.close() };
}
```

`packages/core/src/events/projections.ts`:
```ts
import { eq, sql } from 'drizzle-orm';
import type { StoredEvent } from '@desk/protocol';
import type { Tx } from '../db/open';
import { agents, projects, usageTotals } from '../db/schema';

function requireAgentId(ev: StoredEvent): string {
  if (!ev.agent_id) throw new Error(`${ev.type} requires agent_id`);
  return ev.agent_id;
}

/** Applies one event to the projection tables. Runs inside the append transaction. */
export function applyProjections(tx: Tx, ev: StoredEvent): void {
  switch (ev.type) {
    case 'project.created':
      tx.insert(projects)
        .values({ id: ev.project_id, name: ev.payload.name, goal: ev.payload.goal, instructions: ev.payload.instructions, created_at: ev.ts })
        .run();
      return;
    case 'agent.created':
      tx.insert(agents)
        .values({
          id: requireAgentId(ev),
          project_id: ev.project_id,
          role: ev.payload.role,
          status: 'idle',
          model: ev.payload.model,
          title: ev.payload.title,
          brief: ev.payload.brief,
          workspace_path: ev.payload.workspace_path,
          parent_id: ev.payload.parent_id,
          inbox_cursor: 0,
          created_at: ev.ts,
          updated_at: ev.ts,
        })
        .run();
      return;
    case 'agent.status_changed':
      tx.update(agents).set({ status: ev.payload.status, updated_at: ev.ts }).where(eq(agents.id, requireAgentId(ev))).run();
      return;
    case 'inbox.drained':
      tx.update(agents).set({ inbox_cursor: ev.payload.up_to, updated_at: ev.ts }).where(eq(agents.id, requireAgentId(ev))).run();
      return;
    case 'usage':
      tx.insert(usageTotals)
        .values({
          project_id: ev.project_id,
          agent_id: requireAgentId(ev),
          model: ev.payload.model,
          day: ev.ts.slice(0, 10),
          prompt_tokens: ev.payload.prompt_tokens,
          completion_tokens: ev.payload.completion_tokens,
        })
        .onConflictDoUpdate({
          target: [usageTotals.project_id, usageTotals.agent_id, usageTotals.model, usageTotals.day],
          set: {
            prompt_tokens: sql`${usageTotals.prompt_tokens} + ${ev.payload.prompt_tokens}`,
            completion_tokens: sql`${usageTotals.completion_tokens} + ${ev.payload.completion_tokens}`,
          },
        })
        .run();
      return;
    default:
      return;
  }
}
```

`packages/core/src/events/store.ts`:
```ts
import { EventEmitter } from 'node:events';
import { and, asc, eq, gt, inArray, type SQL } from 'drizzle-orm';
import { EventBody, type EphemeralEvent, type EventInput, type EventType, type StoredEvent } from '@desk/protocol';
import type { Db } from '../db/open';
import { events } from '../db/schema';
import { applyProjections } from './projections';

export type StreamItem = { kind: 'event'; event: StoredEvent } | { kind: 'ephemeral'; event: EphemeralEvent };
export type ListQuery = { projectId?: string; agentId?: string; after?: number; types?: readonly EventType[]; limit?: number };

export class EventStore {
  private readonly emitter = new EventEmitter();

  constructor(
    readonly db: Db,
    private readonly now: () => Date = () => new Date(),
  ) {
    this.emitter.setMaxListeners(0);
  }

  /** Validates, persists and projects events atomically, then notifies subscribers. */
  append(input: EventInput | EventInput[]): StoredEvent[] {
    const inputs = Array.isArray(input) ? input : [input];
    const stored = this.db.transaction((tx) =>
      inputs.map((e) => {
        EventBody.parse({ type: e.type, payload: e.payload });
        const ts = this.now().toISOString();
        const row = tx
          .insert(events)
          .values({ project_id: e.project_id, agent_id: e.agent_id, type: e.type, payload: e.payload, ts })
          .returning({ id: events.id })
          .get();
        const ev = { ...e, id: row.id, ts } as StoredEvent;
        applyProjections(tx, ev);
        return ev;
      }),
    );
    for (const event of stored) this.emitter.emit('item', { kind: 'event', event } satisfies StreamItem);
    return stored;
  }

  /** Broadcasts a non-persisted event (e.g. streamed text deltas). */
  publishEphemeral(event: EphemeralEvent): void {
    this.emitter.emit('item', { kind: 'ephemeral', event } satisfies StreamItem);
  }

  list(q: ListQuery = {}): StoredEvent[] {
    const conds: SQL[] = [];
    if (q.projectId) conds.push(eq(events.project_id, q.projectId));
    if (q.agentId) conds.push(eq(events.agent_id, q.agentId));
    if (q.after !== undefined) conds.push(gt(events.id, q.after));
    if (q.types) conds.push(inArray(events.type, [...q.types]));
    let query = this.db.select().from(events).where(and(...conds)).orderBy(asc(events.id)).$dynamic();
    if (q.limit) query = query.limit(q.limit);
    return query.all().map(
      (row) => ({ id: row.id, project_id: row.project_id, agent_id: row.agent_id, type: row.type, payload: row.payload, ts: row.ts }) as StoredEvent,
    );
  }

  /** Listeners run synchronously after commit and must not throw. */
  subscribe(listener: (item: StreamItem) => void): () => void {
    this.emitter.on('item', listener);
    return () => this.emitter.off('item', listener);
  }
}
```

`packages/core/src/state/queries.ts`:
```ts
import { eq } from 'drizzle-orm';
import type { Db } from '../db/open';
import { agents, projects, usageTotals } from '../db/schema';

export type ProjectRow = typeof projects.$inferSelect;
export type AgentRow = typeof agents.$inferSelect;
export type UsageRow = typeof usageTotals.$inferSelect;

export const getProject = (db: Db, id: string): ProjectRow | undefined =>
  db.select().from(projects).where(eq(projects.id, id)).get();

export const getAgent = (db: Db, id: string): AgentRow | undefined => db.select().from(agents).where(eq(agents.id, id)).get();

export const listAgents = (db: Db, projectId: string): AgentRow[] =>
  db.select().from(agents).where(eq(agents.project_id, projectId)).all();

export const getUsageTotals = (db: Db, projectId: string): UsageRow[] =>
  db.select().from(usageTotals).where(eq(usageTotals.project_id, projectId)).all();
```

- [ ] **Step 5: Run tests and typecheck**

Run: `pnpm vitest run packages/core/src/events && pnpm typecheck`
Expected: PASS (7 tests), typecheck exits 0.

- [ ] **Step 6: Commit**

```bash
git add packages/core
git commit -m "feat(core): event-sourced SQLite store with projections" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Model config and registry

**Files:**
- Create: `packages/core/src/model/config.ts`, `packages/core/src/model/registry.ts`
- Test: `packages/core/src/model/config.test.ts`, `packages/core/src/model/registry.test.ts`

**Interfaces:**
- Consumes: `ModelInfo` from `@desk/protocol`.
- Produces:
  - `type ModelConfig = { baseURL: string; apiKey: string }`
  - `parseEnvFile(text: string): Record<string, string>`
  - `normalizeBaseURL(url: string): string` (ensures trailing `/v1`, no trailing slash)
  - `loadModelConfig(env?: NodeJS.ProcessEnv, home?: string): ModelConfig` (throws if nothing configured)
  - `DEFAULT_MODEL_ID = 'claude-opus-5-5'`, `SEED_MODELS: ModelInfo[]`
  - `class ModelRegistry { constructor(models?: ModelInfo[]); get(id): ModelInfo /* throws on unknown */; has(id): boolean; list(): ModelInfo[]; upsert(m: ModelInfo): void }`

- [ ] **Step 1: Write the failing tests**

`packages/core/src/model/config.test.ts`:
```ts
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { loadModelConfig, normalizeBaseURL, parseEnvFile } from './config';

let home: string;
beforeEach(async () => {
  home = await mkdtemp(join(tmpdir(), 'desk-home-'));
});
afterEach(async () => rm(home, { recursive: true, force: true }));

describe('parseEnvFile', () => {
  it('handles export prefixes, quotes and comments', () => {
    expect(parseEnvFile('# c\nexport A=1\nB="two"\nC=\'three\'\n\nD=x=y\n')).toEqual({ A: '1', B: 'two', C: 'three', D: 'x=y' });
  });
});

describe('normalizeBaseURL', () => {
  it('appends /v1 once and strips trailing slashes', () => {
    expect(normalizeBaseURL('http://127.0.0.1:8317')).toBe('http://127.0.0.1:8317/v1');
    expect(normalizeBaseURL('http://127.0.0.1:8317/')).toBe('http://127.0.0.1:8317/v1');
    expect(normalizeBaseURL('http://127.0.0.1:8317/v1/')).toBe('http://127.0.0.1:8317/v1');
  });
});

describe('loadModelConfig', () => {
  it('prefers DESK_OPENAI_* env vars', () => {
    const cfg = loadModelConfig({ DESK_OPENAI_BASE_URL: 'http://h:1', DESK_OPENAI_API_KEY: 'k' }, home);
    expect(cfg).toEqual({ baseURL: 'http://h:1/v1', apiKey: 'k' });
  });

  it('falls back to ~/.config/cliproxyapi.env', async () => {
    await mkdir(join(home, '.config'), { recursive: true });
    await writeFile(join(home, '.config', 'cliproxyapi.env'), 'export CLIPROXY_API_KEY=secret\nexport CLIPROXY_BASE_URL=http://127.0.0.1:8317\n');
    expect(loadModelConfig({}, home)).toEqual({ baseURL: 'http://127.0.0.1:8317/v1', apiKey: 'secret' });
  });

  it('throws a helpful error when nothing is configured', () => {
    expect(() => loadModelConfig({}, home)).toThrow(/DESK_OPENAI_BASE_URL/);
  });
});
```

`packages/core/src/model/registry.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { DEFAULT_MODEL_ID, ModelRegistry, SEED_MODELS } from './registry';

describe('ModelRegistry', () => {
  it('seeds the four supported models with opus 5.5 as default', () => {
    const r = new ModelRegistry();
    expect(DEFAULT_MODEL_ID).toBe('claude-opus-5-5');
    expect(r.list().map((m) => m.id)).toEqual(['claude-opus-5-5', 'claude-fable-5-1', 'gpt-6-astra', 'gpt-6-sol']);
    expect(SEED_MODELS.find((m) => m.id === 'claude-fable-5-1')?.concurrency).toBe(2);
  });

  it('throws on unknown models and supports upsert', () => {
    const r = new ModelRegistry([]);
    expect(() => r.get('x')).toThrow(/Unknown model: x/);
    r.upsert({ id: 'x', family: 'gpt', context_window: 1000, max_output_tokens: 100, supports_reasoning_effort: false, concurrency: 1 });
    expect(r.has('x')).toBe(true);
    expect(r.get('x').context_window).toBe(1000);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pnpm vitest run packages/core/src/model`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

`packages/core/src/model/config.ts`:
```ts
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

export type ModelConfig = { baseURL: string; apiKey: string };

export function parseEnvFile(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split('\n')) {
    const line = raw.trim().replace(/^export\s+/, '');
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

export function normalizeBaseURL(url: string): string {
  const trimmed = url.replace(/\/+$/, '');
  return trimmed.endsWith('/v1') ? trimmed : `${trimmed}/v1`;
}

export function loadModelConfig(env: NodeJS.ProcessEnv = process.env, home: string = homedir()): ModelConfig {
  if (env.DESK_OPENAI_BASE_URL && env.DESK_OPENAI_API_KEY) {
    return { baseURL: normalizeBaseURL(env.DESK_OPENAI_BASE_URL), apiKey: env.DESK_OPENAI_API_KEY };
  }
  const file = join(home, '.config', 'cliproxyapi.env');
  if (existsSync(file)) {
    const vars = parseEnvFile(readFileSync(file, 'utf8'));
    if (vars.CLIPROXY_BASE_URL && vars.CLIPROXY_API_KEY) {
      return { baseURL: normalizeBaseURL(vars.CLIPROXY_BASE_URL), apiKey: vars.CLIPROXY_API_KEY };
    }
  }
  throw new Error(
    'No model access configured: set DESK_OPENAI_BASE_URL and DESK_OPENAI_API_KEY, or create ~/.config/cliproxyapi.env with CLIPROXY_BASE_URL and CLIPROXY_API_KEY',
  );
}
```

`packages/core/src/model/registry.ts`:
```ts
import type { ModelInfo } from '@desk/protocol';

export const DEFAULT_MODEL_ID = 'claude-opus-5-5';

/** Conservative defaults; exact context windows are confirmed in Plan 5 and edited in the registry, not elsewhere. */
export const SEED_MODELS: ModelInfo[] = [
  { id: 'claude-opus-5-5', family: 'claude', context_window: 200_000, max_output_tokens: 32_000, supports_reasoning_effort: false, concurrency: 4 },
  { id: 'claude-fable-5-1', family: 'claude', context_window: 200_000, max_output_tokens: 32_000, supports_reasoning_effort: false, concurrency: 2 },
  { id: 'gpt-6-astra', family: 'gpt', context_window: 200_000, max_output_tokens: 32_000, supports_reasoning_effort: false, concurrency: 4 },
  { id: 'gpt-6-sol', family: 'gpt', context_window: 200_000, max_output_tokens: 32_000, supports_reasoning_effort: false, concurrency: 4 },
];

export class ModelRegistry {
  private readonly models = new Map<string, ModelInfo>();

  constructor(models: ModelInfo[] = SEED_MODELS) {
    for (const m of models) this.models.set(m.id, m);
  }

  get(id: string): ModelInfo {
    const m = this.models.get(id);
    if (!m) throw new Error(`Unknown model: ${id}`);
    return m;
  }

  has(id: string): boolean {
    return this.models.has(id);
  }

  list(): ModelInfo[] {
    return [...this.models.values()];
  }

  upsert(m: ModelInfo): void {
    this.models.set(m.id, m);
  }
}
```

- [ ] **Step 4: Run tests and typecheck**

Run: `pnpm vitest run packages/core/src/model && pnpm typecheck`
Expected: PASS (7 tests), typecheck exits 0.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/model
git commit -m "feat(core): model config loading and registry" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Model adapter, error classification and retry

**Files:**
- Create: `packages/core/src/model/types.ts`, `packages/core/src/model/errors.ts`, `packages/core/src/model/adapter.ts`, `packages/core/src/model/retry.ts`
- Test: `packages/core/src/model/adapter.test.ts`, `packages/core/src/model/retry.test.ts`

**Interfaces:**
- Consumes: `ModelConfig` (Task 5), `ModelRegistry` (Task 5), `ToolCall` from `@desk/protocol`, `newId` (Task 4), `@desk/fake-model` (tests).
- Produces:
  - `type ChatToolCall = { id: string; type: 'function'; function: { name: string; arguments: string } }`
  - `type ChatMessage = { role: 'system'; content: string } | { role: 'user'; content: string } | { role: 'assistant'; content: string | null; tool_calls?: ChatToolCall[] } | { role: 'tool'; tool_call_id: string; content: string }`
  - `type ToolSpec = { type: 'function'; function: { name: string; description: string; parameters: Record<string, unknown> } }`
  - `type CompletionRequest = { model: string; messages: ChatMessage[]; tools: ToolSpec[]; reasoningEffort?: 'low' | 'medium' | 'high' }`
  - `type CompletionUsage = { prompt_tokens: number; completion_tokens: number; cached_tokens?: number; estimated: boolean }`
  - `type CompletionResult = { content: string | null; toolCalls: ToolCall[]; finishReason: string; usage: CompletionUsage }`
  - `interface ModelAdapter { complete(req: CompletionRequest, opts?: { signal?: AbortSignal; onText?: (delta: string) => void }): Promise<CompletionResult> }`
  - `createModelAdapter(config: ModelConfig, registry: ModelRegistry): ModelAdapter` — throws only `ModelError`
  - `type ModelErrorKind = 'rate_limited' | 'transient' | 'proxy_down' | 'context_overflow' | 'aborted' | 'fatal'`; `class ModelError extends Error { kind; status? }`; `classifyModelError(err: unknown): ModelError`
  - `type RetryOptions = { maxAttempts?: number; baseMs?: number; capMs?: number; sleep?: (ms: number, signal?: AbortSignal) => Promise<void>; random?: () => number; signal?: AbortSignal; onRetry?: (err: ModelError, attempt: number, delayMs: number) => void }`
  - `withRetry<T>(fn: () => Promise<T>, opts?: RetryOptions): Promise<T>`; `abortableSleep(ms, signal?)`

- [ ] **Step 1: Write the failing tests**

`packages/core/src/model/adapter.test.ts`:
```ts
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { afterEach, describe, expect, it } from 'vitest';
import { call, error, hang, startFakeModel, text, tools, type FakeModelServer } from '@desk/fake-model';
import { createModelAdapter } from './adapter';
import { ModelError } from './errors';
import { ModelRegistry } from './registry';

const registry = new ModelRegistry([
  { id: 'fake-model', family: 'claude', context_window: 100_000, max_output_tokens: 4096, supports_reasoning_effort: false, concurrency: 4 },
]);
const req = { model: 'fake-model', messages: [{ role: 'user' as const, content: 'hi' }], tools: [] };

let fake: FakeModelServer;
afterEach(async () => fake?.close());

describe('model adapter', () => {
  it('streams text through onText and returns usage', async () => {
    fake = await startFakeModel([text('hello there friend', { usage: { prompt_tokens: 12, completion_tokens: 4 } })]);
    const adapter = createModelAdapter({ baseURL: fake.url, apiKey: 't' }, registry);
    const deltas: string[] = [];
    const r = await adapter.complete(req, { onText: (d) => deltas.push(d) });
    expect(r.content).toBe('hello there friend');
    expect(deltas.join('')).toBe('hello there friend');
    expect(deltas.length).toBeGreaterThan(1);
    expect(r.toolCalls).toEqual([]);
    expect(r.finishReason).toBe('stop');
    expect(r.usage).toEqual({ prompt_tokens: 12, completion_tokens: 4, estimated: false });
    expect(fake.requests[0]).toMatchObject({ stream: true, stream_options: { include_usage: true } });
    expect(fake.requests[0]?.tools).toBeUndefined();
  });

  it('assembles parallel streamed tool calls in index order', async () => {
    fake = await startFakeModel([tools(call('read_file', { path: 'a.md' }, 'toolu_A'), call('bash', { command: 'ls' }))]);
    const adapter = createModelAdapter({ baseURL: fake.url, apiKey: 't' }, registry);
    const spec = { type: 'function' as const, function: { name: 'read_file', description: 'r', parameters: { type: 'object' } } };
    const r = await adapter.complete({ ...req, tools: [spec] });
    expect(r.content).toBeNull();
    expect(r.finishReason).toBe('tool_calls');
    expect(r.toolCalls).toEqual([
      { id: 'toolu_A', name: 'read_file', arguments: '{"path":"a.md"}' },
      { id: expect.stringMatching(/^call_fake_/), name: 'bash', arguments: '{"command":"ls"}' },
    ]);
    expect(fake.requests[0]?.tools).toHaveLength(1);
  });

  it('estimates usage when the server omits it', async () => {
    fake = await startFakeModel([text('abc', { usage: 'omit' })]);
    const adapter = createModelAdapter({ baseURL: fake.url, apiKey: 't' }, registry);
    const r = await adapter.complete(req);
    expect(r.usage.estimated).toBe(true);
    expect(r.usage.prompt_tokens).toBeGreaterThan(0);
  });

  it.each([
    [error(429, 'rate_limit_error', 'slow'), 'rate_limited'],
    [error(503, 'overloaded', 'busy'), 'transient'],
    [error(400, 'invalid_request_error', 'prompt is too long: 250000 tokens > 200000 maximum'), 'context_overflow'],
    [error(401, 'authentication_error', 'bad key'), 'fatal'],
  ] as const)('classifies %j as %s', async (reply, kind) => {
    fake = await startFakeModel([reply]);
    const adapter = createModelAdapter({ baseURL: fake.url, apiKey: 't' }, registry);
    const err = await adapter.complete(req).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ModelError);
    expect((err as ModelError).kind).toBe(kind);
  });

  it('classifies a refused connection as proxy_down', async () => {
    const probe = createServer();
    await new Promise<void>((r) => probe.listen(0, '127.0.0.1', r));
    const { port } = probe.address() as AddressInfo;
    await new Promise<void>((r) => probe.close(() => r()));
    const adapter = createModelAdapter({ baseURL: `http://127.0.0.1:${port}/v1`, apiKey: 't' }, registry);
    const err = await adapter.complete(req).catch((e: unknown) => e);
    expect((err as ModelError).kind).toBe('proxy_down');
  });

  it('classifies an aborted request as aborted', async () => {
    fake = await startFakeModel([hang()]);
    const adapter = createModelAdapter({ baseURL: fake.url, apiKey: 't' }, registry);
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 50);
    const err = await adapter.complete(req, { signal: controller.signal }).catch((e: unknown) => e);
    expect((err as ModelError).kind).toBe('aborted');
  });
});
```

`packages/core/src/model/retry.test.ts`:
```ts
import { describe, expect, it, vi } from 'vitest';
import { ModelError } from './errors';
import { abortableSleep, withRetry } from './retry';

const noSleep = { sleep: async () => {}, random: () => 1 };

describe('withRetry', () => {
  it('retries transient errors then succeeds', async () => {
    const fn = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new ModelError('transient', 'blip'))
      .mockRejectedValueOnce(new ModelError('rate_limited', 'slow'))
      .mockResolvedValue('ok');
    await expect(withRetry(fn, noSleep)).resolves.toBe('ok');
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it('gives up after maxAttempts', async () => {
    const fn = vi.fn<() => Promise<string>>().mockRejectedValue(new ModelError('rate_limited', 'slow'));
    await expect(withRetry(fn, { ...noSleep, maxAttempts: 3 })).rejects.toMatchObject({ kind: 'rate_limited' });
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it('does not retry fatal errors', async () => {
    const fn = vi.fn<() => Promise<string>>().mockRejectedValue(new ModelError('fatal', 'nope'));
    await expect(withRetry(fn, noSleep)).rejects.toMatchObject({ kind: 'fatal' });
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('uses full-jitter exponential backoff capped at capMs', async () => {
    const delays: number[] = [];
    const fn = vi.fn<() => Promise<string>>().mockRejectedValue(new ModelError('transient', 'x'));
    await withRetry(fn, {
      maxAttempts: 6,
      baseMs: 2000,
      capMs: 10_000,
      random: () => 0.5,
      sleep: async () => {},
      onRetry: (_e, _a, d) => delays.push(d),
    }).catch(() => {});
    expect(delays).toEqual([1000, 2000, 4000, 5000, 5000]);
  });
});

describe('abortableSleep', () => {
  it('rejects with an aborted ModelError when the signal fires', async () => {
    const c = new AbortController();
    const p = abortableSleep(10_000, c.signal);
    c.abort();
    await expect(p).rejects.toMatchObject({ kind: 'aborted' });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pnpm vitest run packages/core/src/model/adapter.test.ts packages/core/src/model/retry.test.ts`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

`packages/core/src/model/types.ts`:
```ts
import type { ToolCall } from '@desk/protocol';

export type ChatToolCall = { id: string; type: 'function'; function: { name: string; arguments: string } };

export type ChatMessage =
  | { role: 'system'; content: string }
  | { role: 'user'; content: string }
  | { role: 'assistant'; content: string | null; tool_calls?: ChatToolCall[] }
  | { role: 'tool'; tool_call_id: string; content: string };

export type ToolSpec = {
  type: 'function';
  function: { name: string; description: string; parameters: Record<string, unknown> };
};

export type CompletionRequest = {
  model: string;
  messages: ChatMessage[];
  tools: ToolSpec[];
  reasoningEffort?: 'low' | 'medium' | 'high';
};

export type CompletionUsage = { prompt_tokens: number; completion_tokens: number; cached_tokens?: number; estimated: boolean };

export type CompletionResult = { content: string | null; toolCalls: ToolCall[]; finishReason: string; usage: CompletionUsage };

export interface ModelAdapter {
  complete(
    req: CompletionRequest,
    opts?: { signal?: AbortSignal; onText?: (delta: string) => void },
  ): Promise<CompletionResult>;
}
```

`packages/core/src/model/errors.ts`:
```ts
import OpenAI from 'openai';

export type ModelErrorKind = 'rate_limited' | 'transient' | 'proxy_down' | 'context_overflow' | 'aborted' | 'fatal';

export class ModelError extends Error {
  constructor(
    readonly kind: ModelErrorKind,
    message: string,
    readonly status?: number,
    options?: { cause?: unknown },
  ) {
    super(message, options);
    this.name = 'ModelError';
  }
}

function findErrorCode(err: unknown): string | undefined {
  let current: unknown = err;
  for (let depth = 0; depth < 6 && current; depth++) {
    const code = (current as { code?: unknown }).code;
    if (typeof code === 'string') return code;
    current = (current as { cause?: unknown }).cause;
  }
  return undefined;
}

const CONTEXT_OVERFLOW = /prompt is too long|context (length|window)|maximum context|too many tokens/i;

export function classifyModelError(err: unknown): ModelError {
  if (err instanceof ModelError) return err;
  if (err instanceof OpenAI.APIUserAbortError || (err instanceof Error && err.name === 'AbortError')) {
    return new ModelError('aborted', 'Request aborted', undefined, { cause: err });
  }
  if (err instanceof OpenAI.APIConnectionTimeoutError) {
    return new ModelError('transient', err.message, undefined, { cause: err });
  }
  if (err instanceof OpenAI.APIConnectionError) {
    const kind = findErrorCode(err) === 'ECONNREFUSED' ? 'proxy_down' : 'transient';
    return new ModelError(kind, err.message, undefined, { cause: err });
  }
  if (err instanceof OpenAI.APIError) {
    const status = err.status;
    const message = err.message ?? 'Model API error';
    const type = (err as { type?: unknown }).type ?? (err.error as { type?: unknown } | undefined)?.type;
    if (status === 429 || type === 'rate_limit_error') return new ModelError('rate_limited', message, status, { cause: err });
    if (status === 400 && CONTEXT_OVERFLOW.test(message)) return new ModelError('context_overflow', message, status, { cause: err });
    if (status === 408 || status === 409 || (status !== undefined && status >= 500)) {
      return new ModelError('transient', message, status, { cause: err });
    }
    return new ModelError('fatal', message, status, { cause: err });
  }
  return new ModelError('fatal', err instanceof Error ? err.message : String(err), undefined, { cause: err });
}
```

`packages/core/src/model/adapter.ts`:
```ts
import OpenAI from 'openai';
import type { ToolCall } from '@desk/protocol';
import { newId } from '../ids';
import type { ModelConfig } from './config';
import { classifyModelError } from './errors';
import type { ModelRegistry } from './registry';
import type { ChatMessage, CompletionUsage, ModelAdapter } from './types';

export function estimateUsage(messages: ChatMessage[], content: string, toolCalls: ToolCall[]): CompletionUsage {
  return {
    prompt_tokens: Math.ceil(JSON.stringify(messages).length / 4),
    completion_tokens: Math.ceil((content.length + JSON.stringify(toolCalls).length) / 4),
    estimated: true,
  };
}

export function createModelAdapter(config: ModelConfig, registry: ModelRegistry): ModelAdapter {
  const client = new OpenAI({ baseURL: config.baseURL, apiKey: config.apiKey, maxRetries: 0, timeout: 10 * 60_000 });

  return {
    async complete(req, { signal, onText } = {}) {
      const info = registry.get(req.model);
      try {
        const stream = await client.chat.completions.create(
          {
            model: req.model,
            messages: req.messages,
            stream: true,
            stream_options: { include_usage: true },
            ...(req.tools.length ? { tools: req.tools } : {}),
            ...(req.reasoningEffort && info.supports_reasoning_effort ? { reasoning_effort: req.reasoningEffort } : {}),
          },
          { signal },
        );

        let content = '';
        let finishReason = 'stop';
        let usage: CompletionUsage | undefined;
        const calls = new Map<number, ToolCall>();

        for await (const chunk of stream) {
          if (chunk.usage) {
            const cached = chunk.usage.prompt_tokens_details?.cached_tokens;
            usage = {
              prompt_tokens: chunk.usage.prompt_tokens,
              completion_tokens: chunk.usage.completion_tokens,
              ...(cached ? { cached_tokens: cached } : {}),
              estimated: false,
            };
          }
          const choice = chunk.choices[0];
          if (!choice) continue;
          if (choice.finish_reason) finishReason = choice.finish_reason;
          const delta = choice.delta;
          if (delta?.content) {
            content += delta.content;
            onText?.(delta.content);
          }
          for (const tc of delta?.tool_calls ?? []) {
            const current = calls.get(tc.index) ?? { id: '', name: '', arguments: '' };
            if (tc.id) current.id = tc.id;
            if (tc.function?.name) current.name += tc.function.name;
            if (tc.function?.arguments) current.arguments += tc.function.arguments;
            calls.set(tc.index, current);
          }
        }

        const toolCalls = [...calls.entries()]
          .sort(([a], [b]) => a - b)
          .map(([, c]) => ({ id: c.id || `call_${newId()}`, name: c.name, arguments: c.arguments || '{}' }));

        return {
          content: content.length ? content : null,
          toolCalls,
          finishReason,
          usage: usage ?? estimateUsage(req.messages, content, toolCalls),
        };
      } catch (err) {
        throw classifyModelError(err);
      }
    },
  };
}
```

`packages/core/src/model/retry.ts`:
```ts
import { classifyModelError, ModelError } from './errors';

export type RetryOptions = {
  maxAttempts?: number;
  baseMs?: number;
  capMs?: number;
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
  random?: () => number;
  signal?: AbortSignal;
  onRetry?: (err: ModelError, attempt: number, delayMs: number) => void;
};

export function abortableSleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new ModelError('aborted', 'Aborted during backoff'));
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new ModelError('aborted', 'Aborted during backoff'));
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/** Retries rate_limited and transient errors with full-jitter exponential backoff. Always throws ModelError. */
export async function withRetry<T>(fn: () => Promise<T>, opts: RetryOptions = {}): Promise<T> {
  const { maxAttempts = 6, baseMs = 2000, capMs = 60_000, sleep = abortableSleep, random = Math.random } = opts;
  for (let attempt = 1; ; attempt++) {
    try {
      return await fn();
    } catch (e) {
      const err = classifyModelError(e);
      const retryable = err.kind === 'rate_limited' || err.kind === 'transient';
      if (!retryable || attempt >= maxAttempts) throw err;
      const delay = Math.floor(random() * Math.min(capMs, baseMs * 2 ** (attempt - 1)));
      opts.onRetry?.(err, attempt, delay);
      await sleep(delay, opts.signal);
    }
  }
}
```

- [ ] **Step 4: Run tests and typecheck**

Run: `pnpm vitest run packages/core/src/model && pnpm typecheck`
Expected: PASS (all model tests), typecheck exits 0. If the `proxy_down` test fails, log `findErrorCode` input chain for the thrown `APIConnectionError` and adjust `findErrorCode` to reach the `ECONNREFUSED` code — do not weaken the test.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/model
git commit -m "feat(core): streaming model adapter, error classification, retry" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Tool framework and path confinement

**Files:**
- Create: `packages/core/src/tools/types.ts`, `packages/core/src/tools/paths.ts`, `packages/core/src/tools/registry.ts`
- Test: `packages/core/src/tools/paths.test.ts`, `packages/core/src/tools/registry.test.ts`

**Interfaces:**
- Consumes: `ToolCall, AgentStatus, ToolResultStatus` from `@desk/protocol`; `ToolSpec` (Task 6).
- Produces:
  - `type ToolContext = { projectId: string; agentId: string; runId: string; toolCallId: string; workspace: string; readRoots: string[]; signal: AbortSignal }`
  - `type ToolYield = { status: AgentStatus; reason?: string }`
  - `type ToolOutput = string | { content: string; yield?: ToolYield }`
  - `type Tool<I = any> = { name: string; description: string; input: z.ZodType<I>; execute(input: I, ctx: ToolContext): Promise<ToolOutput> }`
  - `defineTool(def): Tool` (infers input type from the zod schema)
  - `class ToolDenied extends Error`
  - `type ToolResult = { status: ToolResultStatus; content: string; yield?: ToolYield }`
  - `resolveInside(path: string, roots: string[], cwd: string): Promise<string>` — returns the real absolute path or throws `ToolDenied`; works for not-yet-existing paths.
  - `MAX_TOOL_OUTPUT_CHARS = 20_000`, `toToolSpecs(tools: Tool[]): ToolSpec[]`, `executeToolCall(tools: Tool[], call: ToolCall, ctx: ToolContext): Promise<ToolResult>` (never throws)

- [ ] **Step 1: Write the failing tests**

`packages/core/src/tools/paths.test.ts`:
```ts
import { mkdir, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { resolveInside } from './paths';
import { ToolDenied } from './types';

let base: string;
let ws: string;
let outside: string;
beforeEach(async () => {
  base = await mkdtemp(join(tmpdir(), 'desk-paths-'));
  ws = join(base, 'ws');
  outside = join(base, 'outside');
  await mkdir(ws);
  await mkdir(outside);
  await writeFile(join(ws, 'a.txt'), 'a');
  await writeFile(join(outside, 'secret.txt'), 's');
});
afterEach(async () => rm(base, { recursive: true, force: true }));

describe('resolveInside', () => {
  it('resolves relative paths against cwd', async () => {
    expect(await resolveInside('a.txt', [ws], ws)).toMatch(/ws\/a\.txt$/);
  });

  it('allows not-yet-existing paths inside a root', async () => {
    expect(await resolveInside('new/dir/file.txt', [ws], ws)).toMatch(/ws\/new\/dir\/file\.txt$/);
  });

  it('denies absolute paths outside roots', async () => {
    await expect(resolveInside(join(outside, 'secret.txt'), [ws], ws)).rejects.toBeInstanceOf(ToolDenied);
  });

  it('denies ../ escapes', async () => {
    await expect(resolveInside('../outside/secret.txt', [ws], ws)).rejects.toBeInstanceOf(ToolDenied);
  });

  it('denies symlinks that point outside', async () => {
    await symlink(outside, join(ws, 'link'));
    await expect(resolveInside('link/secret.txt', [ws], ws)).rejects.toBeInstanceOf(ToolDenied);
  });

  it('denies sibling directories sharing a prefix', async () => {
    await mkdir(join(base, 'ws2'));
    await expect(resolveInside(join(base, 'ws2', 'x'), [ws], ws)).rejects.toBeInstanceOf(ToolDenied);
  });
});
```

`packages/core/src/tools/registry.test.ts`:
```ts
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { z } from 'zod';
import { executeToolCall, MAX_TOOL_OUTPUT_CHARS, toToolSpecs } from './registry';
import { defineTool, ToolDenied, type ToolContext } from './types';

let ws: string;
let ctx: ToolContext;
beforeEach(async () => {
  ws = await mkdtemp(join(tmpdir(), 'desk-reg-'));
  ctx = { projectId: 'p', agentId: 'a', runId: 'r', toolCallId: 'toolu_1', workspace: ws, readRoots: [ws], signal: new AbortController().signal };
});
afterEach(async () => rm(ws, { recursive: true, force: true }));

const echo = defineTool({
  name: 'echo',
  description: 'Echo text',
  input: z.object({ text: z.string(), times: z.number().int().default(1) }),
  async execute({ text, times }) {
    return text.repeat(times);
  },
});
const denied = defineTool({ name: 'denied', description: 'd', input: z.object({}), async execute() { throw new ToolDenied('not allowed'); } });
const boom = defineTool({ name: 'boom', description: 'b', input: z.object({}), async execute() { throw new Error('kaboom'); } });
const finish = defineTool({
  name: 'finish',
  description: 'f',
  input: z.object({}),
  async execute() {
    return { content: 'bye', yield: { status: 'done' as const, reason: 'finished' } };
  },
});
const all = [echo, denied, boom, finish];
const run = (name: string, args: unknown) => executeToolCall(all, { id: 'toolu_1', name, arguments: JSON.stringify(args) }, ctx);

describe('toToolSpecs', () => {
  it('produces function specs with input-side JSON schema and no $schema key', () => {
    const [spec] = toToolSpecs([echo]);
    expect(spec?.function.name).toBe('echo');
    expect(spec?.function.parameters).not.toHaveProperty('$schema');
    expect(spec?.function.parameters).toMatchObject({ type: 'object', required: ['text'] });
  });
});

describe('executeToolCall', () => {
  it('runs a tool with validated, defaulted input', async () => {
    expect(await run('echo', { text: 'ab', times: 2 })).toEqual({ status: 'ok', content: 'abab' });
    expect(await run('echo', { text: 'x' })).toEqual({ status: 'ok', content: 'x' });
  });

  it('reports unknown tools, bad JSON and invalid args as errors', async () => {
    expect((await run('nope', {})).status).toBe('error');
    const bad = await executeToolCall(all, { id: 'x', name: 'echo', arguments: '{not json' }, ctx);
    expect(bad).toMatchObject({ status: 'error', content: expect.stringContaining('Invalid JSON') });
    expect(await run('echo', { text: 1 })).toMatchObject({ status: 'error', content: expect.stringContaining('Invalid arguments') });
  });

  it('maps ToolDenied to denied and other throws to error', async () => {
    expect(await run('denied', {})).toEqual({ status: 'denied', content: 'not allowed' });
    expect(await run('boom', {})).toEqual({ status: 'error', content: 'kaboom' });
  });

  it('passes through yields', async () => {
    expect(await run('finish', {})).toEqual({ status: 'ok', content: 'bye', yield: { status: 'done', reason: 'finished' } });
  });

  it('truncates long output and saves the full text in the workspace', async () => {
    const big = 'x'.repeat(MAX_TOOL_OUTPUT_CHARS + 5000);
    const r = await run('echo', { text: big });
    expect(r.content.length).toBeLessThan(MAX_TOOL_OUTPUT_CHARS + 500);
    expect(r.content).toContain('characters truncated');
    expect(await readFile(join(ws, '.desk', 'outputs', 'toolu_1.txt'), 'utf8')).toBe(big);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pnpm vitest run packages/core/src/tools`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

`packages/core/src/tools/types.ts`:
```ts
import type { z } from 'zod';
import type { AgentStatus, ToolResultStatus } from '@desk/protocol';

export type ToolContext = {
  projectId: string;
  agentId: string;
  runId: string;
  toolCallId: string;
  workspace: string;
  readRoots: string[];
  signal: AbortSignal;
};

export type ToolYield = { status: AgentStatus; reason?: string };
export type ToolOutput = string | { content: string; yield?: ToolYield };
export type ToolResult = { status: ToolResultStatus; content: string; yield?: ToolYield };

export type Tool<I = any> = {
  name: string;
  description: string;
  input: z.ZodType<I>;
  execute(input: I, ctx: ToolContext): Promise<ToolOutput>;
};

export function defineTool<S extends z.ZodType>(def: {
  name: string;
  description: string;
  input: S;
  execute(input: z.output<S>, ctx: ToolContext): Promise<ToolOutput>;
}): Tool<z.output<S>> {
  return def as Tool<z.output<S>>;
}

/** Thrown by tools when an action is not permitted; surfaces as tool.result status "denied". */
export class ToolDenied extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ToolDenied';
  }
}
```

`packages/core/src/tools/paths.ts`:
```ts
import { realpath } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, resolve, sep } from 'node:path';
import { ToolDenied } from './types';

async function realpathLenient(abs: string): Promise<string> {
  let current = abs;
  const missing: string[] = [];
  for (;;) {
    try {
      const real = await realpath(current);
      return missing.length ? join(real, ...missing.reverse()) : real;
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== 'ENOENT') throw err;
      const parent = dirname(current);
      if (parent === current) throw err;
      missing.push(basename(current));
      current = parent;
    }
  }
}

/** Resolves `p` (following symlinks) and ensures it lies within one of `roots`. */
export async function resolveInside(p: string, roots: string[], cwd: string): Promise<string> {
  const abs = isAbsolute(p) ? resolve(p) : resolve(cwd, p);
  const real = await realpathLenient(abs);
  const realRoots = await Promise.all(roots.map((r) => realpath(r)));
  if (!realRoots.some((root) => real === root || real.startsWith(root + sep))) {
    throw new ToolDenied(`Path is outside the allowed directories: ${p}`);
  }
  return real;
}
```

`packages/core/src/tools/registry.ts`:
```ts
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { z } from 'zod';
import type { ToolCall } from '@desk/protocol';
import type { ToolSpec } from '../model/types';
import { ToolDenied, type Tool, type ToolContext, type ToolResult } from './types';

export const MAX_TOOL_OUTPUT_CHARS = 20_000;

export function toToolSpecs(tools: Tool[]): ToolSpec[] {
  return tools.map((t) => {
    const { $schema: _ignored, ...parameters } = z.toJSONSchema(t.input, { io: 'input' }) as Record<string, unknown>;
    return { type: 'function', function: { name: t.name, description: t.description, parameters } };
  });
}

async function truncateOutput(content: string, ctx: ToolContext): Promise<string> {
  if (content.length <= MAX_TOOL_OUTPUT_CHARS) return content;
  const dir = join(ctx.workspace, '.desk', 'outputs');
  await mkdir(dir, { recursive: true });
  const file = join(dir, `${ctx.toolCallId.replace(/[^\w-]/g, '_')}.txt`);
  await writeFile(file, content);
  const half = MAX_TOOL_OUTPUT_CHARS / 2;
  return `${content.slice(0, half)}\n\n[... ${content.length - MAX_TOOL_OUTPUT_CHARS} characters truncated; full output saved to ${file} ...]\n\n${content.slice(-half)}`;
}

/** Validates and executes one tool call. Never throws. */
export async function executeToolCall(tools: Tool[], call: ToolCall, ctx: ToolContext): Promise<ToolResult> {
  const tool = tools.find((t) => t.name === call.name);
  if (!tool) return { status: 'error', content: `Unknown tool: ${call.name}` };

  let raw: unknown;
  try {
    raw = JSON.parse(call.arguments || '{}');
  } catch {
    return { status: 'error', content: `Invalid JSON arguments for ${call.name}` };
  }
  const parsed = tool.input.safeParse(raw);
  if (!parsed.success) {
    return { status: 'error', content: `Invalid arguments for ${call.name}:\n${z.prettifyError(parsed.error)}` };
  }

  try {
    const out = await tool.execute(parsed.data, ctx);
    const res = typeof out === 'string' ? { content: out } : out;
    const content = await truncateOutput(res.content, ctx);
    return res.yield ? { status: 'ok', content, yield: res.yield } : { status: 'ok', content };
  } catch (err) {
    if (err instanceof ToolDenied) return { status: 'denied', content: err.message };
    return { status: 'error', content: err instanceof Error ? err.message : String(err) };
  }
}
```

- [ ] **Step 4: Run tests and typecheck**

Run: `pnpm vitest run packages/core/src/tools && pnpm typecheck`
Expected: PASS (12 tests), typecheck exits 0.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/tools
git commit -m "feat(core): tool framework with validation, truncation and path confinement" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: File tools

**Files:**
- Create: `packages/core/src/tools/process.ts`, `packages/core/src/tools/fs.ts`
- Test: `packages/core/src/tools/fs.test.ts`

**Interfaces:**
- Consumes: `defineTool, ToolContext, ToolDenied` (Task 7), `resolveInside` (Task 7).
- Produces:
  - `type ProcessResult = { exitCode: number | null; output: string; timedOut: boolean; aborted: boolean }`
  - `runProcess(opts: { command: string; args: string[]; cwd: string; env?: NodeJS.ProcessEnv; timeoutMs?: number; signal?: AbortSignal; maxOutputChars?: number }): Promise<ProcessResult>` — spawns in its own process group; kills the whole group on timeout/abort; rejects on spawn error (e.g. `ENOENT`).
  - Tools: `readFileTool` (`read_file`), `writeFileTool` (`write_file`), `editFileTool` (`edit_file`), `listDirTool` (`list_dir`), `globTool` (`glob`), `grepTool` (`grep`); `fileTools: Tool[]` (all six); `grepFallback(pattern, dir, glob?): Promise<string>`.
  - Read operations are confined to `ctx.readRoots`; write operations to `[ctx.workspace]`.

- [ ] **Step 1: Write the failing test**

`packages/core/src/tools/fs.test.ts`:
```ts
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { editFileTool, globTool, grepFallback, grepTool, listDirTool, readFileTool, writeFileTool } from './fs';
import { ToolDenied, type ToolContext } from './types';

let base: string;
let ws: string;
let src: string;
let ctx: ToolContext;

beforeEach(async () => {
  base = await mkdtemp(join(tmpdir(), 'desk-fs-'));
  ws = join(base, 'ws');
  src = join(base, 'src');
  await mkdir(ws);
  await mkdir(join(src, 'lib'), { recursive: true });
  await writeFile(join(src, 'readme.md'), 'line one\nline two\nline three');
  await writeFile(join(src, 'lib', 'util.ts'), 'export const answer = 42;\n');
  ctx = { projectId: 'p', agentId: 'a', runId: 'r', toolCallId: 't', workspace: ws, readRoots: [ws, src], signal: new AbortController().signal };
});
afterEach(async () => rm(base, { recursive: true, force: true }));

describe('read_file', () => {
  it('returns numbered lines', async () => {
    expect(await readFileTool.execute({ path: join(src, 'readme.md') }, ctx)).toBe('1\tline one\n2\tline two\n3\tline three');
  });

  it('supports offset and limit with a range note', async () => {
    expect(await readFileTool.execute({ path: join(src, 'readme.md'), offset: 2, limit: 1 }, ctx)).toBe('2\tline two\n[lines 2-2 of 3]');
  });

  it('denies reads outside read roots', async () => {
    await expect(readFileTool.execute({ path: join(base, 'x.txt') }, ctx)).rejects.toBeInstanceOf(ToolDenied);
  });
});

describe('write_file', () => {
  it('writes into the workspace, creating directories', async () => {
    await writeFileTool.execute({ path: 'out/notes.md', content: 'hello' }, ctx);
    expect(await readFile(join(ws, 'out', 'notes.md'), 'utf8')).toBe('hello');
  });

  it('denies writes to read-only sources', async () => {
    await expect(writeFileTool.execute({ path: join(src, 'readme.md'), content: 'x' }, ctx)).rejects.toBeInstanceOf(ToolDenied);
  });
});

describe('edit_file', () => {
  beforeEach(async () => writeFile(join(ws, 'f.txt'), 'a b a c'));

  it('replaces a unique match', async () => {
    await writeFile(join(ws, 'g.txt'), 'hello world');
    await editFileTool.execute({ path: 'g.txt', old_string: 'world', new_string: 'desk' }, ctx);
    expect(await readFile(join(ws, 'g.txt'), 'utf8')).toBe('hello desk');
  });

  it('rejects ambiguous matches unless replace_all', async () => {
    await expect(editFileTool.execute({ path: 'f.txt', old_string: 'a', new_string: 'z' }, ctx)).rejects.toThrow(/matches 2 times/);
    await editFileTool.execute({ path: 'f.txt', old_string: 'a', new_string: 'z', replace_all: true }, ctx);
    expect(await readFile(join(ws, 'f.txt'), 'utf8')).toBe('z b z c');
  });

  it('rejects missing matches', async () => {
    await expect(editFileTool.execute({ path: 'f.txt', old_string: 'q', new_string: 'z' }, ctx)).rejects.toThrow(/not found/);
  });

  it('treats $ in new_string literally', async () => {
    await writeFile(join(ws, 'h.txt'), 'price');
    await editFileTool.execute({ path: 'h.txt', old_string: 'price', new_string: '$& $1' }, ctx);
    expect(await readFile(join(ws, 'h.txt'), 'utf8')).toBe('$& $1');
  });
});

describe('list_dir and glob', () => {
  it('lists entries with trailing slash for directories', async () => {
    expect(await listDirTool.execute({ path: src }, ctx)).toBe('lib/\nreadme.md');
  });

  it('globs relative to root', async () => {
    expect(await globTool.execute({ pattern: '**/*.ts', root: src }, ctx)).toBe('lib/util.ts');
    expect(await globTool.execute({ pattern: '*.none', root: src }, ctx)).toBe('No files matched');
  });
});

describe('grep', () => {
  it('finds matches with file and line', async () => {
    const out = await grepTool.execute({ pattern: 'answer', root: src }, ctx);
    expect(out).toBe('lib/util.ts:1:export const answer = 42;');
  });

  it('reports no matches', async () => {
    expect(await grepTool.execute({ pattern: 'zzz_nope', root: src }, ctx)).toBe('No matches');
  });

  it('has a JS fallback with the same output shape', async () => {
    expect(await grepFallback('two', src)).toBe('readme.md:2:line two');
    expect(await grepFallback('answer', src, '*.ts')).toBe('lib/util.ts:1:export const answer = 42;');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run packages/core/src/tools/fs.test.ts`
Expected: FAIL — module `./fs` not found.

- [ ] **Step 3: Implement**

`packages/core/src/tools/process.ts`:
```ts
import { spawn } from 'node:child_process';

export type ProcessResult = { exitCode: number | null; output: string; timedOut: boolean; aborted: boolean };

export type ProcessOptions = {
  command: string;
  args: string[];
  cwd: string;
  env?: NodeJS.ProcessEnv;
  timeoutMs?: number;
  signal?: AbortSignal;
  maxOutputChars?: number;
};

/** Runs a process in its own process group; stdout and stderr are combined. Rejects only on spawn failure. */
export function runProcess(opts: ProcessOptions): Promise<ProcessResult> {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(opts.command, opts.args, {
      cwd: opts.cwd,
      env: opts.env ?? process.env,
      detached: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const max = opts.maxOutputChars ?? 1_000_000;
    let output = '';
    let timedOut = false;
    let aborted = false;

    const onData = (buf: Buffer) => {
      if (output.length < max) output += buf.toString('utf8');
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);

    const killGroup = () => {
      const pid = child.pid;
      if (pid === undefined) return;
      try {
        process.kill(-pid, 'SIGTERM');
      } catch {}
      setTimeout(() => {
        try {
          process.kill(-pid, 'SIGKILL');
        } catch {}
      }, 5000).unref();
    };

    const timer = opts.timeoutMs
      ? setTimeout(() => {
          timedOut = true;
          killGroup();
        }, opts.timeoutMs)
      : undefined;
    const onAbort = () => {
      aborted = true;
      killGroup();
    };
    opts.signal?.addEventListener('abort', onAbort, { once: true });
    if (opts.signal?.aborted) onAbort();

    const cleanup = () => {
      if (timer) clearTimeout(timer);
      opts.signal?.removeEventListener('abort', onAbort);
    };
    child.on('error', (err) => {
      cleanup();
      reject(err);
    });
    child.on('close', (code) => {
      cleanup();
      resolvePromise({ exitCode: code, output: output.slice(0, max), timedOut, aborted });
    });
  });
}
```

`packages/core/src/tools/fs.ts`:
```ts
import { mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import fg from 'fast-glob';
import { z } from 'zod';
import { resolveInside } from './paths';
import { runProcess } from './process';
import { defineTool, type Tool, type ToolContext } from './types';

const DEFAULT_READ_LIMIT = 2000;
const MAX_LIST_RESULTS = 500;
const IGNORE = ['**/node_modules/**', '**/.git/**'];

const readable = (p: string, ctx: ToolContext) => resolveInside(p, ctx.readRoots, ctx.workspace);
const writable = (p: string, ctx: ToolContext) => resolveInside(p, [ctx.workspace], ctx.workspace);

export const readFileTool = defineTool({
  name: 'read_file',
  description: 'Read a text file. Returns numbered lines (line<TAB>text). Use offset (1-based) and limit for large files.',
  input: z.object({
    path: z.string().describe('Absolute path, or relative to your workspace'),
    offset: z.number().int().min(1).optional(),
    limit: z.number().int().min(1).max(10_000).optional(),
  }),
  async execute({ path, offset = 1, limit = DEFAULT_READ_LIMIT }, ctx) {
    const file = await readable(path, ctx);
    const lines = (await readFile(file, 'utf8')).split('\n');
    const slice = lines.slice(offset - 1, offset - 1 + limit);
    const body = slice.map((line, i) => `${offset + i}\t${line}`).join('\n');
    const end = offset - 1 + slice.length;
    return end < lines.length || offset > 1 ? `${body}\n[lines ${offset}-${end} of ${lines.length}]` : body;
  },
});

export const writeFileTool = defineTool({
  name: 'write_file',
  description: 'Create or overwrite a file in your workspace. Parent directories are created.',
  input: z.object({ path: z.string(), content: z.string() }),
  async execute({ path, content }, ctx) {
    const file = await writable(path, ctx);
    await mkdir(dirname(file), { recursive: true });
    await writeFile(file, content);
    return `Wrote ${Buffer.byteLength(content)} bytes to ${file}`;
  },
});

export const editFileTool = defineTool({
  name: 'edit_file',
  description:
    'Replace an exact string in a workspace file. old_string must match exactly once unless replace_all is true. Include surrounding context to make it unique.',
  input: z.object({
    path: z.string(),
    old_string: z.string().min(1),
    new_string: z.string(),
    replace_all: z.boolean().optional(),
  }),
  async execute({ path, old_string, new_string, replace_all = false }, ctx) {
    const file = await writable(path, ctx);
    const original = await readFile(file, 'utf8');
    const count = original.split(old_string).length - 1;
    if (count === 0) throw new Error(`old_string not found in ${file}`);
    if (count > 1 && !replace_all) {
      throw new Error(`old_string matches ${count} times in ${file}; include more context or set replace_all`);
    }
    const updated = replace_all ? original.split(old_string).join(new_string) : original.replace(old_string, () => new_string);
    await writeFile(file, updated);
    const n = replace_all ? count : 1;
    return `Edited ${file} (${n} replacement${n === 1 ? '' : 's'})`;
  },
});

export const listDirTool = defineTool({
  name: 'list_dir',
  description: 'List a directory. Directories end with "/".',
  input: z.object({ path: z.string().default('.') }),
  async execute({ path }, ctx) {
    const dir = await readable(path, ctx);
    const entries = await readdir(dir, { withFileTypes: true });
    const names = entries.map((e) => (e.isDirectory() ? `${e.name}/` : e.name)).sort();
    return names.length ? names.join('\n') : '(empty directory)';
  },
});

export const globTool = defineTool({
  name: 'glob',
  description: 'Find files by glob pattern (e.g. "**/*.ts"). Paths are returned relative to root. Skips node_modules and .git.',
  input: z.object({ pattern: z.string(), root: z.string().default('.') }),
  async execute({ pattern, root }, ctx) {
    const dir = await readable(root, ctx);
    const files = (await fg(pattern, { cwd: dir, onlyFiles: true, dot: false, ignore: IGNORE })).sort();
    if (!files.length) return 'No files matched';
    const shown = files.slice(0, MAX_LIST_RESULTS);
    const more = files.length - shown.length;
    return shown.join('\n') + (more ? `\n[${more} more not shown]` : '');
  },
});

export async function grepFallback(pattern: string, dir: string, glob?: string): Promise<string> {
  const re = new RegExp(pattern);
  const files = (await fg(glob ? `**/${glob}` : '**/*', { cwd: dir, onlyFiles: true, ignore: IGNORE })).sort();
  const out: string[] = [];
  for (const f of files) {
    if (out.length >= MAX_LIST_RESULTS) break;
    let text: string;
    try {
      text = await readFile(join(dir, f), 'utf8');
    } catch {
      continue;
    }
    text.split('\n').forEach((line, i) => {
      if (out.length < MAX_LIST_RESULTS && re.test(line)) out.push(`${f}:${i + 1}:${line}`);
    });
  }
  return out.length ? out.join('\n') : 'No matches';
}

export const grepTool = defineTool({
  name: 'grep',
  description: 'Search file contents with a regular expression. Output: path:line:text, paths relative to root.',
  input: z.object({
    pattern: z.string().describe('Regular expression'),
    root: z.string().default('.'),
    glob: z.string().optional().describe('Only search files whose name matches this glob, e.g. "*.ts"'),
  }),
  async execute({ pattern, root, glob }, ctx) {
    const dir = await readable(root, ctx);
    try {
      const r = await runProcess({
        command: 'rg',
        args: ['-n', '--no-heading', '--color', 'never', '--max-count', '50', ...(glob ? ['--glob', glob] : []), '-e', pattern, '.'],
        cwd: dir,
        timeoutMs: 30_000,
        signal: ctx.signal,
      });
      if (r.exitCode === 1) return 'No matches';
      if (r.exitCode !== 0) throw new Error(`grep failed: ${r.output.trim()}`);
      return r.output.replace(/^\.\//gm, '').trimEnd().split('\n').sort().join('\n');
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === 'ENOENT') return grepFallback(pattern, dir, glob);
      throw err;
    }
  },
});

export const fileTools: Tool[] = [readFileTool, writeFileTool, editFileTool, listDirTool, globTool, grepTool];
```

- [ ] **Step 4: Run tests and typecheck**

Run: `pnpm vitest run packages/core/src/tools && pnpm typecheck`
Expected: PASS (all tool tests), typecheck exits 0.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/tools
git commit -m "feat(core): file tools (read, write, edit, list, glob, grep)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Bash tool

**Files:**
- Create: `packages/core/src/tools/bash.ts`
- Test: `packages/core/src/tools/bash.test.ts`

**Interfaces:**
- Consumes: `runProcess` (Task 8), `defineTool` (Task 7).
- Produces: `SAFE_ENV_KEYS`, `scrubbedEnv(workspace: string, source?: NodeJS.ProcessEnv): NodeJS.ProcessEnv`, `bashTool` (`bash`, input `{ command: string; timeout_s?: number /* 1..600, default 120 */ }`, output `"[exit code N]\n<output>"` or `"[timed out after Ns]\n…"` or `"[aborted]\n…"`). Runs `/bin/zsh -f -c <command>` (no user rc files, for determinism; PATH comes from the daemon env) with cwd = workspace. (Plan 2 wraps this in `sandbox-exec`.)

- [ ] **Step 1: Write the failing test**

`packages/core/src/tools/bash.test.ts`:
```ts
import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { bashTool, scrubbedEnv } from './bash';
import type { ToolContext } from './types';

let ws: string;
let ctx: ToolContext;
beforeEach(async () => {
  ws = await realpath(await mkdtemp(join(tmpdir(), 'desk-bash-')));
  ctx = { projectId: 'p', agentId: 'a', runId: 'r', toolCallId: 't', workspace: ws, readRoots: [ws], signal: new AbortController().signal };
});
afterEach(async () => rm(ws, { recursive: true, force: true }));

describe('bash', () => {
  it('runs in the workspace and reports exit code', async () => {
    expect(await bashTool.execute({ command: 'pwd', timeout_s: 10 }, ctx)).toBe(`[exit code 0]\n${ws}\n`);
    expect(await bashTool.execute({ command: 'echo oops >&2; exit 3', timeout_s: 10 }, ctx)).toBe('[exit code 3]\noops\n');
  });

  it('kills the process group on timeout', async () => {
    const started = Date.now();
    const out = await bashTool.execute({ command: 'sleep 30 & sleep 30; echo never', timeout_s: 1 }, ctx);
    expect(out).toMatch(/^\[timed out after 1s\]/);
    expect(Date.now() - started).toBeLessThan(8000);
  });

  it('kills the process on abort', async () => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 100);
    const out = await bashTool.execute({ command: 'sleep 30', timeout_s: 60 }, { ...ctx, signal: controller.signal });
    expect(out).toMatch(/^\[aborted\]/);
  });

  it('does not leak daemon env vars into commands', async () => {
    process.env.DESK_TEST_SECRET = 'leaky';
    try {
      const out = await bashTool.execute({ command: 'echo "[$DESK_TEST_SECRET][$DESK_WORKSPACE]"', timeout_s: 10 }, ctx);
      expect(out).toBe(`[exit code 0]\n[][${ws}]\n`);
    } finally {
      delete process.env.DESK_TEST_SECRET;
    }
  });
});

describe('scrubbedEnv', () => {
  it('keeps only safe keys plus DESK_WORKSPACE', () => {
    expect(scrubbedEnv('/w', { PATH: '/bin', HOME: '/h', OPENAI_API_KEY: 'x', CLIPROXY_API_KEY: 'y' })).toEqual({
      PATH: '/bin',
      HOME: '/h',
      DESK_WORKSPACE: '/w',
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run packages/core/src/tools/bash.test.ts`
Expected: FAIL — module `./bash` not found.

- [ ] **Step 3: Implement**

`packages/core/src/tools/bash.ts`:
```ts
import { z } from 'zod';
import { runProcess } from './process';
import { defineTool } from './types';

export const SAFE_ENV_KEYS = ['PATH', 'HOME', 'LANG', 'TERM', 'TMPDIR', 'USER', 'SHELL'] as const;

export function scrubbedEnv(workspace: string, source: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {};
  for (const key of SAFE_ENV_KEYS) {
    const value = source[key];
    if (value !== undefined) env[key] = value;
  }
  env.DESK_WORKSPACE = workspace;
  return env;
}

export const bashTool = defineTool({
  name: 'bash',
  description:
    'Run a shell command with zsh in your workspace directory. stdout and stderr are combined. Default timeout 120s (max 600s). Avoid interactive commands.',
  input: z.object({
    command: z.string().min(1),
    timeout_s: z.number().int().min(1).max(600).default(120),
  }),
  async execute({ command, timeout_s }, ctx) {
    const r = await runProcess({
      command: '/bin/zsh',
      args: ['-f', '-c', command],
      cwd: ctx.workspace,
      env: scrubbedEnv(ctx.workspace),
      timeoutMs: timeout_s * 1000,
      signal: ctx.signal,
    });
    const status = r.aborted ? 'aborted' : r.timedOut ? `timed out after ${timeout_s}s` : `exit code ${r.exitCode}`;
    return `[${status}]\n${r.output}`;
  },
});
```

- [ ] **Step 4: Run tests and typecheck**

Run: `pnpm vitest run packages/core/src/tools/bash.test.ts && pnpm typecheck`
Expected: PASS (5 tests), typecheck exits 0.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/tools/bash.ts packages/core/src/tools/bash.test.ts
git commit -m "feat(core): bash tool with process-group kill and env scrubbing" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Transcript builder and inbox

**Files:**
- Create: `packages/core/src/agent/transcript.ts`, `packages/core/src/agent/inbox.ts`
- Test: `packages/core/src/agent/transcript.test.ts`, `packages/core/src/agent/inbox.test.ts`

**Interfaces:**
- Consumes: `StoredEvent, INBOX_EVENT_TYPES` from `@desk/protocol`; `ChatMessage` (Task 6); `EventStore`, `openDb`, `getAgent` (Task 4).
- Produces:
  - `buildConversation(events: StoredEvent[]): ChatMessage[]` — user messages appear at the point an `inbox.drained` event covers them (batch joined with blank lines into one user message); undrained messages are excluded; assistant messages with no content and no tool calls are skipped.
  - `drainInbox(store: EventStore, agentId: string, runId: string): number` — appends `inbox.drained { up_to }` if there are inbox events after the agent's cursor; returns the count drained.
  - `hasPendingInbox(store: EventStore, agentId: string): boolean`

- [ ] **Step 1: Write the failing tests**

`packages/core/src/agent/transcript.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import type { EventBody, StoredEvent } from '@desk/protocol';
import { buildConversation } from './transcript';

let seq = 0;
const ev = (body: EventBody): StoredEvent => ({ ...body, id: ++seq, project_id: 'p', agent_id: 'a', ts: 't' }) as StoredEvent;

describe('buildConversation', () => {
  it('orders a full tool loop correctly', () => {
    seq = 0;
    const events = [
      ev({ type: 'message.user', payload: { text: 'do it' } }),
      ev({ type: 'inbox.drained', payload: { run_id: 'r', up_to: 1 } }),
      ev({ type: 'assistant.message', payload: { run_id: 'r', content: null, tool_calls: [{ id: 'c1', name: 'bash', arguments: '{"command":"ls"}' }] } }),
      ev({ type: 'tool.call', payload: { run_id: 'r', tool_call_id: 'c1', name: 'bash', arguments: '{"command":"ls"}' } }),
      ev({ type: 'tool.result', payload: { run_id: 'r', tool_call_id: 'c1', name: 'bash', status: 'ok', content: 'a.txt' } }),
      ev({ type: 'assistant.message', payload: { run_id: 'r', content: 'done', tool_calls: [] } }),
    ];
    expect(buildConversation(events)).toEqual([
      { role: 'user', content: 'do it' },
      { role: 'assistant', content: null, tool_calls: [{ id: 'c1', type: 'function', function: { name: 'bash', arguments: '{"command":"ls"}' } }] },
      { role: 'tool', tool_call_id: 'c1', content: 'a.txt' },
      { role: 'assistant', content: 'done' },
    ]);
  });

  it('places a mid-run message where it was drained, after tool results', () => {
    seq = 0;
    const events = [
      ev({ type: 'message.user', payload: { text: 'start' } }),
      ev({ type: 'inbox.drained', payload: { run_id: 'r', up_to: 1 } }),
      ev({ type: 'assistant.message', payload: { run_id: 'r', content: null, tool_calls: [{ id: 'c1', name: 'x', arguments: '{}' }] } }),
      ev({ type: 'message.user', payload: { text: 'also do Y' } }),
      ev({ type: 'message.user', payload: { text: 'and Z' } }),
      ev({ type: 'tool.result', payload: { run_id: 'r', tool_call_id: 'c1', name: 'x', status: 'ok', content: 'ok' } }),
      ev({ type: 'inbox.drained', payload: { run_id: 'r', up_to: 5 } }),
    ];
    expect(buildConversation(events).map((m) => m.role)).toEqual(['user', 'assistant', 'tool', 'user']);
    expect(buildConversation(events).at(-1)).toEqual({ role: 'user', content: 'also do Y\n\nand Z' });
  });

  it('excludes undrained messages and empty assistant messages', () => {
    seq = 0;
    const events = [
      ev({ type: 'message.user', payload: { text: 'a' } }),
      ev({ type: 'inbox.drained', payload: { run_id: 'r', up_to: 1 } }),
      ev({ type: 'assistant.message', payload: { run_id: 'r', content: null, tool_calls: [] } }),
      ev({ type: 'message.user', payload: { text: 'later' } }),
    ];
    expect(buildConversation(events)).toEqual([{ role: 'user', content: 'a' }]);
  });
});
```

`packages/core/src/agent/inbox.test.ts`:
```ts
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { openDb } from '../db/open';
import { EventStore } from '../events/store';
import { getAgent } from '../state/queries';
import { drainInbox, hasPendingInbox } from './inbox';

let close: () => void;
let store: EventStore;
beforeEach(() => {
  const o = openDb(':memory:');
  close = o.close;
  store = new EventStore(o.db);
  store.append([
    { project_id: 'p', agent_id: null, type: 'project.created', payload: { name: 'P', goal: '', instructions: '' } },
    { project_id: 'p', agent_id: 'a', type: 'agent.created', payload: { role: 'thread', model: 'm', title: null, brief: null, workspace_path: '/w', parent_id: null } },
  ]);
});
afterEach(() => close());

describe('inbox', () => {
  it('drains pending messages up to the latest id', () => {
    expect(hasPendingInbox(store, 'a')).toBe(false);
    const [m1] = store.append({ project_id: 'p', agent_id: 'a', type: 'message.user', payload: { text: 'one' } });
    const [m2] = store.append({ project_id: 'p', agent_id: 'a', type: 'message.user', payload: { text: 'two' } });
    expect(hasPendingInbox(store, 'a')).toBe(true);
    expect(drainInbox(store, 'a', 'r1')).toBe(2);
    expect(getAgent(store.db, 'a')?.inbox_cursor).toBe(m2!.id);
    expect(m1!.id).toBeLessThan(m2!.id);
    expect(hasPendingInbox(store, 'a')).toBe(false);
    expect(drainInbox(store, 'a', 'r1')).toBe(0);
    expect(store.list({ types: ['inbox.drained'] })).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pnpm vitest run packages/core/src/agent`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

`packages/core/src/agent/transcript.ts`:
```ts
import type { EventOf, StoredEvent } from '@desk/protocol';
import type { ChatMessage } from '../model/types';

/** Rebuilds the model conversation for one agent from its events (in id order). */
export function buildConversation(events: StoredEvent[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  const pending: EventOf<'message.user'>[] = [];

  for (const ev of events) {
    switch (ev.type) {
      case 'message.user':
        pending.push(ev);
        break;
      case 'inbox.drained': {
        const batch: string[] = [];
        while (pending.length && pending[0]!.id <= ev.payload.up_to) batch.push(pending.shift()!.payload.text);
        if (batch.length) out.push({ role: 'user', content: batch.join('\n\n') });
        break;
      }
      case 'assistant.message': {
        const { content, tool_calls } = ev.payload;
        if (!content && tool_calls.length === 0) break;
        out.push(
          tool_calls.length
            ? {
                role: 'assistant',
                content,
                tool_calls: tool_calls.map((tc) => ({ id: tc.id, type: 'function' as const, function: { name: tc.name, arguments: tc.arguments } })),
              }
            : { role: 'assistant', content },
        );
        break;
      }
      case 'tool.result':
        out.push({ role: 'tool', tool_call_id: ev.payload.tool_call_id, content: ev.payload.content });
        break;
      default:
        break;
    }
  }
  return out;
}
```

`packages/core/src/agent/inbox.ts`:
```ts
import { INBOX_EVENT_TYPES } from '@desk/protocol';
import type { EventStore } from '../events/store';
import { getAgent } from '../state/queries';

function pending(store: EventStore, agentId: string) {
  const agent = getAgent(store.db, agentId);
  if (!agent) throw new Error(`Unknown agent: ${agentId}`);
  return { agent, events: store.list({ agentId, after: agent.inbox_cursor, types: INBOX_EVENT_TYPES }) };
}

export function hasPendingInbox(store: EventStore, agentId: string): boolean {
  return pending(store, agentId).events.length > 0;
}

/** Marks all pending inbox events as delivered into the conversation at this point. */
export function drainInbox(store: EventStore, agentId: string, runId: string): number {
  const { agent, events } = pending(store, agentId);
  const last = events.at(-1);
  if (!last) return 0;
  store.append({ project_id: agent.project_id, agent_id: agentId, type: 'inbox.drained', payload: { run_id: runId, up_to: last.id } });
  return events.length;
}
```

- [ ] **Step 4: Run tests and typecheck**

Run: `pnpm vitest run packages/core/src/agent && pnpm typecheck`
Expected: PASS (4 tests), typecheck exits 0.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent
git commit -m "feat(core): transcript builder and inbox draining" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Agent run loop

**Files:**
- Create: `packages/core/src/tools/thread.ts`, `packages/core/src/agent/prompts.ts`, `packages/core/src/agent/run.ts`, `packages/core/src/testing/harness.ts`
- Test: `packages/core/src/agent/run.test.ts`

**Interfaces:**
- Consumes: everything from Tasks 4–10.
- Produces:
  - `completeTool` (`complete`, input `{ summary: string }`, yields `{ status: 'done', reason: summary }`)
  - `threadSystemPrompt(agent: AgentRow, project: ProjectRow): string`
  - `type RunDeps = { store: EventStore; adapter: ModelAdapter; tools: Tool[]; systemPrompt: (agent: AgentRow, project: ProjectRow) => string; maxSteps: number; retry?: RetryOptions }`
  - `type RunOutcome = { reason: RunFinishReason; status: AgentStatus }`
  - `runAgent(deps: RunDeps, agentId: string, signal: AbortSignal): Promise<RunOutcome>` — never throws; always appends `run.started` … `run.finished` and a final `agent.status_changed`.
  - Outcomes: text-only reply → `no_tool_calls` / `idle`; yielding tool → `yielded` / yield status; step limit → `max_steps` / `idle`; abort → `stopped` / `cancelled`; non-retryable or exhausted model error → `error` / `failed`.
  - Test harness: `FAKE_MODEL: ModelInfo` (id `fake-model`), `noSleep: RetryOptions`, `createHarness(opts?: { script?: Script | FakeReply[]; concurrency?: number }): Promise<Harness>` with `Harness = { fake; dir; store; models; adapter; cleanup(): Promise<void> }`, `seedThread(store, dir, opts?: { model?: string; brief?: string; projectId?: string }): Promise<{ projectId: string; agentId: string; workspace: string }>`.

- [ ] **Step 1: Write the harness and the failing test**

`packages/core/src/testing/harness.ts`:
```ts
import { mkdir, mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { ModelInfo } from '@desk/protocol';
import { startFakeModel, type FakeModelServer, type FakeReply, type Script } from '@desk/fake-model';
import { openDb } from '../db/open';
import { EventStore } from '../events/store';
import { newId } from '../ids';
import { createModelAdapter } from '../model/adapter';
import { ModelRegistry } from '../model/registry';
import type { RetryOptions } from '../model/retry';
import type { ModelAdapter } from '../model/types';

export const FAKE_MODEL: ModelInfo = {
  id: 'fake-model',
  family: 'claude',
  context_window: 100_000,
  max_output_tokens: 4096,
  supports_reasoning_effort: false,
  concurrency: 4,
};

export const noSleep: RetryOptions = { sleep: async () => {}, random: () => 0 };

export type Harness = {
  fake: FakeModelServer;
  dir: string;
  store: EventStore;
  models: ModelRegistry;
  adapter: ModelAdapter;
  cleanup(): Promise<void>;
};

export async function createHarness(opts: { script?: Script | FakeReply[]; concurrency?: number } = {}): Promise<Harness> {
  const fake = await startFakeModel(opts.script ?? []);
  const dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-test-')));
  const { db, close } = openDb(':memory:');
  const store = new EventStore(db);
  const models = new ModelRegistry([{ ...FAKE_MODEL, concurrency: opts.concurrency ?? FAKE_MODEL.concurrency }]);
  const adapter = createModelAdapter({ baseURL: fake.url, apiKey: 'test' }, models);
  return {
    fake,
    dir,
    store,
    models,
    adapter,
    async cleanup() {
      await fake.close();
      close();
      await rm(dir, { recursive: true, force: true });
    },
  };
}

export async function seedThread(
  store: EventStore,
  dir: string,
  opts: { model?: string; brief?: string; projectId?: string } = {},
): Promise<{ projectId: string; agentId: string; workspace: string }> {
  const projectId = opts.projectId ?? newId();
  const agentId = newId();
  const workspace = join(dir, 'workspaces', agentId);
  await mkdir(workspace, { recursive: true });
  if (!opts.projectId) {
    store.append({ project_id: projectId, agent_id: null, type: 'project.created', payload: { name: 'Test project', goal: 'Test goal', instructions: '' } });
  }
  store.append({
    project_id: projectId,
    agent_id: agentId,
    type: 'agent.created',
    payload: { role: 'thread', model: opts.model ?? FAKE_MODEL.id, title: 'Test thread', brief: opts.brief ?? 'Do the test task', workspace_path: workspace, parent_id: null },
  });
  return { projectId, agentId, workspace };
}
```

`packages/core/src/agent/run.test.ts`:
```ts
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { z } from 'zod';
import { call, error, hang, text, tools } from '@desk/fake-model';
import type { EphemeralEvent } from '@desk/protocol';
import { getAgent } from '../state/queries';
import { createHarness, noSleep, seedThread, type Harness } from '../testing/harness';
import { fileTools } from '../tools/fs';
import { completeTool } from '../tools/thread';
import { defineTool } from '../tools/types';
import { threadSystemPrompt } from './prompts';
import { runAgent, type RunDeps } from './run';

let h: Harness;
afterEach(async () => h?.cleanup());

const deps = (extra: Partial<RunDeps> = {}): RunDeps => ({
  store: h.store,
  adapter: h.adapter,
  tools: [...fileTools, completeTool],
  systemPrompt: threadSystemPrompt,
  maxSteps: 20,
  retry: noSleep,
  ...extra,
});
const say = (agentId: string, projectId: string, text: string) =>
  h.store.append({ project_id: projectId, agent_id: agentId, type: 'message.user', payload: { text } });
const types = (agentId: string) => h.store.list({ agentId }).map((e) => e.type);

describe('runAgent', () => {
  it('handles a text-only reply', async () => {
    h = await createHarness({ script: [text('Hello!', { usage: { prompt_tokens: 50, completion_tokens: 5 } })] });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'hi');
    const outcome = await runAgent(deps(), agentId, new AbortController().signal);
    expect(outcome).toEqual({ reason: 'no_tool_calls', status: 'idle' });
    expect(types(agentId)).toEqual([
      'agent.created', 'message.user', 'run.started', 'agent.status_changed', 'inbox.drained',
      'assistant.message', 'usage', 'run.finished', 'agent.status_changed',
    ]);
    expect(getAgent(h.store.db, agentId)?.status).toBe('idle');
    const req = h.fake.requests[0]!;
    expect(req.messages[0]).toMatchObject({ role: 'system', content: expect.stringContaining('Do the test task') });
    expect(req.messages[1]).toEqual({ role: 'user', content: 'hi' });
  });

  it('executes tools and yields on complete', async () => {
    h = await createHarness({
      script: [
        tools(call('write_file', { path: 'out.txt', content: 'result' })),
        tools(call('complete', { summary: 'Wrote out.txt' })),
      ],
    });
    const { agentId, projectId, workspace } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'go');
    const outcome = await runAgent(deps(), agentId, new AbortController().signal);
    expect(outcome).toEqual({ reason: 'yielded', status: 'done' });
    expect(await readFile(join(workspace, 'out.txt'), 'utf8')).toBe('result');
    const results = h.store.list({ agentId, types: ['tool.result'] });
    expect(results.map((r) => r.type === 'tool.result' && r.payload.status)).toEqual(['ok', 'ok']);
    const finished = h.store.list({ agentId, types: ['agent.status_changed'] }).at(-1);
    expect(finished?.type === 'agent.status_changed' && finished.payload).toEqual({ status: 'done', reason: 'Wrote out.txt' });
  });

  it('runs parallel tool calls and sends results in call order', async () => {
    h = await createHarness({
      script: [tools(call('write_file', { path: 'a.txt', content: 'A' }, 'c1'), call('write_file', { path: 'b.txt', content: 'B' }, 'c2')), text('both written')],
    });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'go');
    await runAgent(deps(), agentId, new AbortController().signal);
    const second = h.fake.requests[1]!.messages;
    expect(second.slice(-3).map((m) => [m.role, m.tool_call_id])).toEqual([
      ['assistant', undefined],
      ['tool', 'c1'],
      ['tool', 'c2'],
    ]);
  });

  it('injects a message that arrives mid-run at the next step', async () => {
    h = await createHarness({ script: [tools(call('poke', {})), text('noted')] });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    const poke = defineTool({
      name: 'poke',
      description: 'p',
      input: z.object({}),
      async execute() {
        say(agentId, projectId, 'change of plan');
        return 'poked';
      },
    });
    say(agentId, projectId, 'go');
    await runAgent(deps({ tools: [poke] }), agentId, new AbortController().signal);
    const second = h.fake.requests[1]!.messages;
    expect(second.at(-1)).toEqual({ role: 'user', content: 'change of plan' });
    expect(second.at(-2)).toMatchObject({ role: 'tool', content: 'poked' });
  });

  it('stops at the step limit', async () => {
    h = await createHarness({ script: () => tools(call('list_dir', {})) });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'loop');
    expect(await runAgent(deps({ maxSteps: 3 }), agentId, new AbortController().signal)).toEqual({ reason: 'max_steps', status: 'idle' });
    expect(h.fake.requests).toHaveLength(3);
  });

  it('stops when aborted', async () => {
    h = await createHarness({ script: [hang()] });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'go');
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 50);
    expect(await runAgent(deps(), agentId, controller.signal)).toEqual({ reason: 'stopped', status: 'cancelled' });
  });

  it('retries rate limits then succeeds', async () => {
    h = await createHarness({ script: [error(429, 'rate_limit_error'), text('ok')] });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'go');
    expect((await runAgent(deps(), agentId, new AbortController().signal)).reason).toBe('no_tool_calls');
    expect(h.fake.requests).toHaveLength(2);
  });

  it('fails on fatal model errors', async () => {
    h = await createHarness({ script: [error(401, 'authentication_error', 'bad key')] });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    say(agentId, projectId, 'go');
    expect(await runAgent(deps(), agentId, new AbortController().signal)).toEqual({ reason: 'error', status: 'failed' });
    const fin = h.store.list({ agentId, types: ['run.finished'] })[0];
    expect(fin?.type === 'run.finished' && fin.payload.detail).toContain('bad key');
  });

  it('publishes streamed text as ephemeral deltas', async () => {
    h = await createHarness({ script: [text('a fairly long streamed reply')] });
    const { agentId, projectId } = await seedThread(h.store, h.dir);
    const deltas: EphemeralEvent[] = [];
    h.store.subscribe((i) => i.kind === 'ephemeral' && deltas.push(i.event));
    say(agentId, projectId, 'go');
    await runAgent(deps(), agentId, new AbortController().signal);
    expect(deltas.map((d) => d.payload.text).join('')).toBe('a fairly long streamed reply');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run packages/core/src/agent/run.test.ts`
Expected: FAIL — modules `./run`, `./prompts`, `../tools/thread` not found.

- [ ] **Step 3: Implement**

`packages/core/src/tools/thread.ts`:
```ts
import { z } from 'zod';
import { defineTool } from './types';

export const completeTool = defineTool({
  name: 'complete',
  description:
    'Finish your assignment. Call exactly once, when the work is done or cannot be done, with an honest summary: what was done, what was not, and how it was verified.',
  input: z.object({ summary: z.string().min(1) }),
  async execute({ summary }) {
    return { content: 'Completion recorded.', yield: { status: 'done', reason: summary } };
  },
});
```

`packages/core/src/agent/prompts.ts`:
```ts
import type { AgentRow, ProjectRow } from '../state/queries';

export function threadSystemPrompt(agent: AgentRow, project: ProjectRow): string {
  return [
    'You are a Desk thread: an autonomous agent working on one assignment inside a larger project.',
    '',
    `Project: ${project.name}`,
    `Project goal: ${project.goal}`,
    ...(project.instructions ? ['', 'Project instructions:', project.instructions] : []),
    '',
    `Your assignment (${agent.title ?? 'untitled'}):`,
    agent.brief ?? '(no brief provided)',
    '',
    `Your workspace — the only directory you can write to: ${agent.workspace_path}`,
    '',
    'Work step by step with your tools. Verify your work before finishing.',
    'When the assignment is done (or cannot be done), call `complete` with an honest summary.',
  ].join('\n');
}
```

`packages/core/src/agent/run.ts`:
```ts
import type { AgentStatus, EventInput, RunFinishReason } from '@desk/protocol';
import type { EventStore } from '../events/store';
import { newId } from '../ids';
import { classifyModelError } from '../model/errors';
import { withRetry, type RetryOptions } from '../model/retry';
import type { ChatMessage, ModelAdapter } from '../model/types';
import { getAgent, getProject, type AgentRow, type ProjectRow } from '../state/queries';
import { executeToolCall, toToolSpecs } from '../tools/registry';
import type { Tool } from '../tools/types';
import { drainInbox } from './inbox';
import { buildConversation } from './transcript';

export type RunDeps = {
  store: EventStore;
  adapter: ModelAdapter;
  tools: Tool[];
  systemPrompt: (agent: AgentRow, project: ProjectRow) => string;
  maxSteps: number;
  retry?: RetryOptions;
};

export type RunOutcome = { reason: RunFinishReason; status: AgentStatus };

/** Runs one activation of an agent until it yields, replies without tools, hits the step limit, is aborted, or fails. Never throws. */
export async function runAgent(deps: RunDeps, agentId: string, signal: AbortSignal): Promise<RunOutcome> {
  const { store } = deps;
  const agent = getAgent(store.db, agentId);
  if (!agent) throw new Error(`Unknown agent: ${agentId}`);
  const project = getProject(store.db, agent.project_id);
  if (!project) throw new Error(`Unknown project: ${agent.project_id}`);

  const runId = newId();
  const base = { project_id: agent.project_id, agent_id: agentId };
  store.append([
    { ...base, type: 'run.started', payload: { run_id: runId, model: agent.model } },
    { ...base, type: 'agent.status_changed', payload: { status: 'running' } },
  ]);

  const finish = (reason: RunFinishReason, status: AgentStatus, detail?: string): RunOutcome => {
    store.append([
      { ...base, type: 'run.finished', payload: { run_id: runId, reason, ...(detail ? { detail } : {}) } },
      { ...base, type: 'agent.status_changed', payload: { status, ...(detail ? { reason: detail } : {}) } },
    ]);
    return { reason, status };
  };

  const workspace = agent.workspace_path;
  if (!workspace) return finish('error', 'failed', 'Agent has no workspace');
  const specs = toToolSpecs(deps.tools);

  try {
    for (let step = 0; step < deps.maxSteps; step++) {
      if (signal.aborted) return finish('stopped', 'cancelled');
      drainInbox(store, agentId, runId);

      const current = getAgent(store.db, agentId)!;
      const messages: ChatMessage[] = [
        { role: 'system', content: deps.systemPrompt(current, project) },
        ...buildConversation(store.list({ agentId })),
      ];

      const result = await withRetry(
        () =>
          deps.adapter.complete(
            { model: current.model, messages, tools: specs },
            {
              signal,
              onText: (text) =>
                store.publishEphemeral({ type: 'assistant.delta', project_id: agent.project_id, agent_id: agentId, payload: { run_id: runId, text } }),
            },
          ),
        { ...deps.retry, signal },
      );

      store.append([
        { ...base, type: 'assistant.message', payload: { run_id: runId, content: result.content, tool_calls: result.toolCalls } },
        {
          ...base,
          type: 'usage',
          payload: {
            run_id: runId,
            model: current.model,
            prompt_tokens: result.usage.prompt_tokens,
            completion_tokens: result.usage.completion_tokens,
            ...(result.usage.cached_tokens !== undefined ? { cached_tokens: result.usage.cached_tokens } : {}),
            estimated: result.usage.estimated,
          },
        },
      ]);

      if (result.toolCalls.length === 0) return finish('no_tool_calls', 'idle');

      store.append(
        result.toolCalls.map(
          (tc): EventInput => ({ ...base, type: 'tool.call', payload: { run_id: runId, tool_call_id: tc.id, name: tc.name, arguments: tc.arguments } }),
        ),
      );
      const results = await Promise.all(
        result.toolCalls.map((tc) =>
          executeToolCall(deps.tools, tc, {
            projectId: agent.project_id,
            agentId,
            runId,
            toolCallId: tc.id,
            workspace,
            readRoots: [workspace],
            signal,
          }),
        ),
      );
      store.append(
        result.toolCalls.map(
          (tc, i): EventInput => ({
            ...base,
            type: 'tool.result',
            payload: { run_id: runId, tool_call_id: tc.id, name: tc.name, status: results[i]!.status, content: results[i]!.content },
          }),
        ),
      );

      const yielded = results.find((r) => r.yield)?.yield;
      if (yielded) return finish('yielded', yielded.status, yielded.reason);
    }
    return finish('max_steps', 'idle', `Reached the limit of ${deps.maxSteps} steps`);
  } catch (e) {
    const err = classifyModelError(e);
    if (err.kind === 'aborted') return finish('stopped', 'cancelled');
    return finish('error', 'failed', err.message);
  }
}
```

- [ ] **Step 4: Run tests and typecheck**

Run: `pnpm vitest run packages/core/src/agent && pnpm typecheck`
Expected: PASS (all agent tests), typecheck exits 0.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/agent packages/core/src/tools/thread.ts packages/core/src/testing
git commit -m "feat(core): agent run loop with streaming, parallel tools, steering and yields" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Scheduler

**Files:**
- Create: `packages/core/src/runtime/scheduler.ts`
- Test: `packages/core/src/runtime/scheduler.test.ts`

**Interfaces:**
- Consumes: `AgentRole` from `@desk/protocol`.
- Produces:
  - `type Job = { agentId: string; projectId: string; model: string; role: AgentRole }`
  - `type SchedulerOptions = { modelConcurrency: (model: string) => number; projectConcurrency: (projectId: string) => number; run: (job: Job, signal: AbortSignal) => Promise<void>; afterRun?: (job: Job) => void }`
  - `class Scheduler { enqueue(job: Job): void /* no-op if already queued or running */; isActive(agentId): boolean; stop(agentId): 'queued' | 'running' | 'none'; whenIdle(): Promise<void>; readonly runningCount: number; readonly queuedCount: number }`
  - Rules: desk jobs are ordered before thread jobs (FIFO within role); a job starts only if running jobs on its model < `modelConcurrency(model)`; thread jobs additionally need running **thread** jobs in its project < `projectConcurrency(projectId)` (desk jobs don't count toward or against the project cap); a blocked job does not block later jobs that fit. `afterRun` is called after a job is removed from the running set and before the queue is pumped again.

- [ ] **Step 1: Write the failing test**

`packages/core/src/runtime/scheduler.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { Scheduler, type Job } from './scheduler';

type Gate = { job: Job; release: () => void; signal: AbortSignal };

function setup(opts: { model?: number; project?: number } = {}) {
  const started: Gate[] = [];
  const after: string[] = [];
  const s = new Scheduler({
    modelConcurrency: () => opts.model ?? 10,
    projectConcurrency: () => opts.project ?? 10,
    run: (job, signal) =>
      new Promise<void>((resolve) => {
        started.push({ job, release: resolve, signal });
        signal.addEventListener('abort', () => resolve());
      }),
    afterRun: (job) => after.push(job.agentId),
  });
  return { s, started, after };
}
const job = (agentId: string, extra: Partial<Job> = {}): Job => ({ agentId, projectId: 'p', model: 'm', role: 'thread', ...extra });
const tick = () => new Promise((r) => setTimeout(r, 0));

describe('Scheduler', () => {
  it('respects per-model concurrency', async () => {
    const { s, started } = setup({ model: 1 });
    s.enqueue(job('a'));
    s.enqueue(job('b'));
    await tick();
    expect(started.map((g) => g.job.agentId)).toEqual(['a']);
    started[0]!.release();
    await tick();
    expect(started.map((g) => g.job.agentId)).toEqual(['a', 'b']);
  });

  it('respects per-project thread concurrency but lets desk jobs through', async () => {
    const { s, started } = setup({ project: 1 });
    s.enqueue(job('t1'));
    s.enqueue(job('t2'));
    s.enqueue(job('d', { role: 'desk' }));
    await tick();
    expect(started.map((g) => g.job.agentId).sort()).toEqual(['d', 't1']);
  });

  it('does not let a blocked job block others', async () => {
    const { s, started } = setup({ model: 1 });
    s.enqueue(job('a', { model: 'm1' }));
    s.enqueue(job('b', { model: 'm1' }));
    s.enqueue(job('c', { model: 'm2' }));
    await tick();
    expect(started.map((g) => g.job.agentId)).toEqual(['a', 'c']);
  });

  it('orders desk jobs before thread jobs', async () => {
    const { s, started } = setup({ model: 1 });
    s.enqueue(job('blocker'));
    s.enqueue(job('t'));
    s.enqueue(job('d', { role: 'desk' }));
    await tick();
    started[0]!.release();
    await tick();
    expect(started.map((g) => g.job.agentId)).toEqual(['blocker', 'd']);
  });

  it('dedupes active agents and calls afterRun', async () => {
    const { s, started, after } = setup();
    s.enqueue(job('a'));
    s.enqueue(job('a'));
    await tick();
    expect(started).toHaveLength(1);
    expect(s.isActive('a')).toBe(true);
    started[0]!.release();
    await s.whenIdle();
    expect(after).toEqual(['a']);
    expect(s.isActive('a')).toBe(false);
  });

  it('stops queued and running jobs', async () => {
    const { s, started } = setup({ model: 1 });
    s.enqueue(job('a'));
    s.enqueue(job('b'));
    await tick();
    expect(s.stop('b')).toBe('queued');
    expect(s.stop('a')).toBe('running');
    expect(started[0]!.signal.aborted).toBe(true);
    expect(s.stop('zzz')).toBe('none');
    await s.whenIdle();
    expect(started).toHaveLength(1);
  });

  it('whenIdle resolves immediately when nothing is active', async () => {
    const { s } = setup();
    await expect(s.whenIdle()).resolves.toBeUndefined();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run packages/core/src/runtime/scheduler.test.ts`
Expected: FAIL — module `./scheduler` not found.

- [ ] **Step 3: Implement**

`packages/core/src/runtime/scheduler.ts`:
```ts
import type { AgentRole } from '@desk/protocol';

export type Job = { agentId: string; projectId: string; model: string; role: AgentRole };

export type SchedulerOptions = {
  modelConcurrency: (model: string) => number;
  projectConcurrency: (projectId: string) => number;
  run: (job: Job, signal: AbortSignal) => Promise<void>;
  afterRun?: (job: Job) => void;
};

type Running = { job: Job; controller: AbortController };

export class Scheduler {
  private queue: Job[] = [];
  private readonly running = new Map<string, Running>();
  private idleWaiters: Array<() => void> = [];

  constructor(private readonly opts: SchedulerOptions) {}

  get runningCount(): number {
    return this.running.size;
  }

  get queuedCount(): number {
    return this.queue.length;
  }

  enqueue(job: Job): void {
    if (this.isActive(job.agentId)) return;
    this.queue.push(job);
    // Stable sort: desk before thread, FIFO within each role.
    this.queue.sort((a, b) => Number(b.role === 'desk') - Number(a.role === 'desk'));
    this.pump();
  }

  isActive(agentId: string): boolean {
    return this.running.has(agentId) || this.queue.some((j) => j.agentId === agentId);
  }

  stop(agentId: string): 'queued' | 'running' | 'none' {
    const index = this.queue.findIndex((j) => j.agentId === agentId);
    if (index >= 0) {
      this.queue.splice(index, 1);
      this.pump();
      return 'queued';
    }
    const running = this.running.get(agentId);
    if (running) {
      running.controller.abort();
      return 'running';
    }
    return 'none';
  }

  whenIdle(): Promise<void> {
    if (this.queue.length === 0 && this.running.size === 0) return Promise.resolve();
    return new Promise((resolve) => this.idleWaiters.push(resolve));
  }

  private canStart(job: Job): boolean {
    const active = [...this.running.values()].map((r) => r.job);
    if (active.filter((j) => j.model === job.model).length >= this.opts.modelConcurrency(job.model)) return false;
    if (job.role === 'thread') {
      const threads = active.filter((j) => j.role === 'thread' && j.projectId === job.projectId).length;
      if (threads >= this.opts.projectConcurrency(job.projectId)) return false;
    }
    return true;
  }

  private start(job: Job): void {
    const controller = new AbortController();
    this.running.set(job.agentId, { job, controller });
    Promise.resolve()
      .then(() => this.opts.run(job, controller.signal))
      .catch(() => {})
      .finally(() => {
        this.running.delete(job.agentId);
        this.opts.afterRun?.(job);
        this.pump();
      });
  }

  private pump(): void {
    for (let i = 0; i < this.queue.length; ) {
      const job = this.queue[i]!;
      if (this.canStart(job)) {
        this.queue.splice(i, 1);
        this.start(job);
      } else {
        i++;
      }
    }
    if (this.queue.length === 0 && this.running.size === 0) {
      const waiters = this.idleWaiters;
      this.idleWaiters = [];
      for (const w of waiters) w();
    }
  }
}
```

- [ ] **Step 4: Run tests and typecheck**

Run: `pnpm vitest run packages/core/src/runtime && pnpm typecheck`
Expected: PASS (7 tests), typecheck exits 0.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/runtime
git commit -m "feat(core): scheduler with model/project caps and desk priority" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Runtime facade and public exports

**Files:**
- Create: `packages/core/src/runtime/runtime.ts`
- Modify: `packages/core/src/index.ts`
- Test: `packages/core/src/runtime/runtime.test.ts`

**Interfaces:**
- Consumes: `Scheduler` (Task 12), `runAgent` (Task 11), `hasPendingInbox` (Task 10), `fileTools`, `bashTool`, `completeTool`, `threadSystemPrompt`, `ModelRegistry`, `DEFAULT_MODEL_ID`, `EventStore`, `getAgent`.
- Produces:
  - `defaultThreadTools: Tool[]` = `[...fileTools, bashTool, completeTool]`
  - `type RuntimeOptions = { store: EventStore; adapter: ModelAdapter; models: ModelRegistry; toolsFor?: (agent: AgentRow) => Tool[]; systemPrompt?: (agent: AgentRow, project: ProjectRow) => string; maxSteps?: { desk: number; thread: number }; maxConcurrentThreads?: number; retry?: RetryOptions }`
  - `class Runtime { constructor(o: RuntimeOptions); readonly scheduler: Scheduler; createProject(input: { name: string; goal: string; instructions?: string }): string; createThread(projectId: string, input: { title: string; brief: string; workspacePath: string; model?: string }): string; sendMessage(agentId: string, text: string): void; stop(agentId: string): void; whenIdle(): Promise<void> }`
  - Behaviour: `sendMessage` appends `message.user`; if the agent is not active it appends `agent.status_changed{queued}` and enqueues. After each run, if the agent has pending inbox items and its status is not `cancelled`, it is re-queued. `stop` on a queued agent appends `agent.status_changed{cancelled}`; on a running agent it aborts (the run loop records `cancelled`). `createThread` creates the workspace directory and validates the model against the registry.
  - `@desk/core` index re-exports the public API listed in the File Structure.

- [ ] **Step 1: Write the failing test**

`packages/core/src/runtime/runtime.test.ts`:
```ts
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { hang, text } from '@desk/fake-model';
import { getAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, noSleep, type Harness } from '../testing/harness';
import { Runtime } from './runtime';

let h: Harness;
afterEach(async () => h?.cleanup());

const makeRuntime = (extra: { maxConcurrentThreads?: number } = {}) =>
  new Runtime({ store: h.store, adapter: h.adapter, models: h.models, retry: noSleep, ...extra });

const statuses = (agentId: string) =>
  h.store.list({ agentId, types: ['agent.status_changed'] }).map((e) => (e.type === 'agent.status_changed' ? e.payload.status : ''));

describe('Runtime', () => {
  it('runs a thread when messaged', async () => {
    h = await createHarness({ script: [text('hi back')] });
    const rt = makeRuntime();
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const agentId = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w1'), model: FAKE_MODEL.id });
    rt.sendMessage(agentId, 'hello');
    await rt.whenIdle();
    expect(statuses(agentId)).toEqual(['queued', 'running', 'idle']);
    expect(getAgent(h.store.db, agentId)?.status).toBe('idle');
  });

  it('re-runs when a message arrives after the last step', async () => {
    h = await createHarness({ script: [text('first', { delayMs: 100 }), text('second')] });
    const rt = makeRuntime();
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const agentId = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w1'), model: FAKE_MODEL.id });
    rt.sendMessage(agentId, 'one');
    await new Promise((r) => setTimeout(r, 30));
    rt.sendMessage(agentId, 'two');
    await rt.whenIdle();
    expect(h.fake.requests).toHaveLength(2);
    expect(h.fake.requests[1]!.messages.at(-1)).toEqual({ role: 'user', content: 'two' });
    expect(h.store.list({ agentId, types: ['run.started'] })).toHaveLength(2);
  });

  it('enforces model concurrency across threads', async () => {
    h = await createHarness({ script: () => text('ok', { delayMs: 50 }), concurrency: 1 });
    const rt = makeRuntime();
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const ids = [1, 2, 3].map((n) => rt.createThread(projectId, { title: `T${n}`, brief: 'B', workspacePath: join(h.dir, `w${n}`), model: FAKE_MODEL.id }));
    ids.forEach((id) => rt.sendMessage(id, 'go'));
    await rt.whenIdle();
    expect(h.fake.requests).toHaveLength(3);
    expect(h.fake.maxInFlight).toBe(1);
  });

  it('enforces the per-project thread cap', async () => {
    h = await createHarness({ script: () => text('ok', { delayMs: 50 }) });
    const rt = makeRuntime({ maxConcurrentThreads: 1 });
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const ids = [1, 2].map((n) => rt.createThread(projectId, { title: `T${n}`, brief: 'B', workspacePath: join(h.dir, `w${n}`), model: FAKE_MODEL.id }));
    ids.forEach((id) => rt.sendMessage(id, 'go'));
    await rt.whenIdle();
    expect(h.fake.maxInFlight).toBe(1);
  });

  it('stops running and queued threads', async () => {
    h = await createHarness({ script: () => hang(), concurrency: 1 });
    const rt = makeRuntime();
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    const a = rt.createThread(projectId, { title: 'A', brief: 'B', workspacePath: join(h.dir, 'wa'), model: FAKE_MODEL.id });
    const b = rt.createThread(projectId, { title: 'B', brief: 'B', workspacePath: join(h.dir, 'wb'), model: FAKE_MODEL.id });
    rt.sendMessage(a, 'go');
    rt.sendMessage(b, 'go');
    await new Promise((r) => setTimeout(r, 50));
    rt.stop(b);
    rt.stop(a);
    await rt.whenIdle();
    expect(getAgent(h.store.db, a)?.status).toBe('cancelled');
    expect(getAgent(h.store.db, b)?.status).toBe('cancelled');
    expect(h.store.list({ agentId: b, types: ['run.started'] })).toHaveLength(0);
  });

  it('rejects unknown models', async () => {
    h = await createHarness();
    const rt = makeRuntime();
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    expect(() => rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'w'), model: 'nope' })).toThrow(/Unknown model/);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run packages/core/src/runtime/runtime.test.ts`
Expected: FAIL — module `./runtime` not found.

- [ ] **Step 3: Implement**

`packages/core/src/runtime/runtime.ts`:
```ts
import { mkdirSync } from 'node:fs';
import { runAgent } from '../agent/run';
import { hasPendingInbox } from '../agent/inbox';
import { threadSystemPrompt } from '../agent/prompts';
import type { EventStore } from '../events/store';
import { newId } from '../ids';
import { DEFAULT_MODEL_ID, type ModelRegistry } from '../model/registry';
import type { RetryOptions } from '../model/retry';
import type { ModelAdapter } from '../model/types';
import { getAgent, type AgentRow, type ProjectRow } from '../state/queries';
import { bashTool } from '../tools/bash';
import { fileTools } from '../tools/fs';
import { completeTool } from '../tools/thread';
import type { Tool } from '../tools/types';
import { Scheduler } from './scheduler';

export const defaultThreadTools: Tool[] = [...fileTools, bashTool, completeTool];

export type RuntimeOptions = {
  store: EventStore;
  adapter: ModelAdapter;
  models: ModelRegistry;
  toolsFor?: (agent: AgentRow) => Tool[];
  systemPrompt?: (agent: AgentRow, project: ProjectRow) => string;
  maxSteps?: { desk: number; thread: number };
  maxConcurrentThreads?: number;
  retry?: RetryOptions;
};

export class Runtime {
  readonly scheduler: Scheduler;

  constructor(private readonly o: RuntimeOptions) {
    this.scheduler = new Scheduler({
      modelConcurrency: (model) => o.models.get(model).concurrency,
      projectConcurrency: () => o.maxConcurrentThreads ?? 4,
      run: (job, signal) => this.execute(job.agentId, signal),
      afterRun: (job) => this.afterRun(job.agentId),
    });
  }

  createProject(input: { name: string; goal: string; instructions?: string }): string {
    const id = newId();
    this.o.store.append({
      project_id: id,
      agent_id: null,
      type: 'project.created',
      payload: { name: input.name, goal: input.goal, instructions: input.instructions ?? '' },
    });
    return id;
  }

  createThread(projectId: string, input: { title: string; brief: string; workspacePath: string; model?: string }): string {
    const model = input.model ?? DEFAULT_MODEL_ID;
    this.o.models.get(model);
    mkdirSync(input.workspacePath, { recursive: true });
    const id = newId();
    this.o.store.append({
      project_id: projectId,
      agent_id: id,
      type: 'agent.created',
      payload: { role: 'thread', model, title: input.title, brief: input.brief, workspace_path: input.workspacePath, parent_id: null },
    });
    return id;
  }

  sendMessage(agentId: string, text: string): void {
    const agent = this.requireAgent(agentId);
    this.o.store.append({ project_id: agent.project_id, agent_id: agentId, type: 'message.user', payload: { text } });
    if (!this.scheduler.isActive(agentId)) this.schedule(agent);
  }

  stop(agentId: string): void {
    const agent = this.requireAgent(agentId);
    if (this.scheduler.stop(agentId) === 'queued') {
      this.o.store.append({
        project_id: agent.project_id,
        agent_id: agentId,
        type: 'agent.status_changed',
        payload: { status: 'cancelled', reason: 'Stopped before starting' },
      });
    }
  }

  whenIdle(): Promise<void> {
    return this.scheduler.whenIdle();
  }

  private requireAgent(agentId: string): AgentRow {
    const agent = getAgent(this.o.store.db, agentId);
    if (!agent) throw new Error(`Unknown agent: ${agentId}`);
    return agent;
  }

  private schedule(agent: AgentRow): void {
    this.o.store.append({ project_id: agent.project_id, agent_id: agent.id, type: 'agent.status_changed', payload: { status: 'queued' } });
    this.scheduler.enqueue({ agentId: agent.id, projectId: agent.project_id, model: agent.model, role: agent.role });
  }

  private async execute(agentId: string, signal: AbortSignal): Promise<void> {
    const agent = this.requireAgent(agentId);
    const maxSteps = this.o.maxSteps ?? { desk: 60, thread: 200 };
    await runAgent(
      {
        store: this.o.store,
        adapter: this.o.adapter,
        tools: this.o.toolsFor?.(agent) ?? defaultThreadTools,
        systemPrompt: this.o.systemPrompt ?? threadSystemPrompt,
        maxSteps: agent.role === 'desk' ? maxSteps.desk : maxSteps.thread,
        ...(this.o.retry ? { retry: this.o.retry } : {}),
      },
      agentId,
      signal,
    );
  }

  private afterRun(agentId: string): void {
    const agent = this.requireAgent(agentId);
    if (agent.status !== 'cancelled' && hasPendingInbox(this.o.store, agentId)) this.schedule(agent);
  }
}
```

`packages/core/src/index.ts`:
```ts
export { newId } from './ids';
export { openDb, type Db, type Tx } from './db/open';
export { EventStore, type ListQuery, type StreamItem } from './events/store';
export { getAgent, getProject, getUsageTotals, listAgents, type AgentRow, type ProjectRow, type UsageRow } from './state/queries';
export { loadModelConfig, normalizeBaseURL, parseEnvFile, type ModelConfig } from './model/config';
export { DEFAULT_MODEL_ID, ModelRegistry, SEED_MODELS } from './model/registry';
export { classifyModelError, ModelError, type ModelErrorKind } from './model/errors';
export { createModelAdapter } from './model/adapter';
export { abortableSleep, withRetry, type RetryOptions } from './model/retry';
export type { ChatMessage, CompletionRequest, CompletionResult, CompletionUsage, ModelAdapter, ToolSpec } from './model/types';
export { defineTool, ToolDenied, type Tool, type ToolContext, type ToolOutput, type ToolResult } from './tools/types';
export { executeToolCall, MAX_TOOL_OUTPUT_CHARS, toToolSpecs } from './tools/registry';
export { resolveInside } from './tools/paths';
export { runProcess, type ProcessResult } from './tools/process';
export { fileTools } from './tools/fs';
export { bashTool, scrubbedEnv } from './tools/bash';
export { completeTool } from './tools/thread';
export { buildConversation } from './agent/transcript';
export { drainInbox, hasPendingInbox } from './agent/inbox';
export { threadSystemPrompt } from './agent/prompts';
export { runAgent, type RunDeps, type RunOutcome } from './agent/run';
export { Scheduler, type Job, type SchedulerOptions } from './runtime/scheduler';
export { defaultThreadTools, Runtime, type RuntimeOptions } from './runtime/runtime';
```

- [ ] **Step 4: Run the full suite and typecheck**

Run: `pnpm test && pnpm typecheck`
Expected: all tests PASS across protocol, fake-model and core; typecheck exits 0.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src
git commit -m "feat(core): Runtime facade and public exports" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Live smoke test against the proxy

**Files:**
- Create: `packages/core/src/live.live.test.ts`
- Modify: `docs/superpowers/specs/2026-09-23-desk-daemon-design.md` (§12: mark items 1–2 verified)

**Interfaces:**
- Consumes: `@desk/core` public API (Task 13), real CLIProxyAPI at the configured base URL.
- Produces: `pnpm test:live` — one end-to-end thread run per model (`claude-opus-5-5`, `gpt-6-astra`, `gpt-6-sol`, `claude-fable-5-1`); a model that is rate-limited for the whole retry budget is reported as **skipped**, not failed.

- [ ] **Step 1: Write the live test**

`packages/core/src/live.live.test.ts`:
```ts
import { mkdtemp, readFile, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { createModelAdapter, EventStore, getAgent, loadModelConfig, ModelRegistry, openDb, Runtime } from './index';

const MODELS = ['claude-opus-5-5', 'gpt-6-astra', 'gpt-6-sol', 'claude-fable-5-1'];

describe.each(MODELS)('live: %s', (model) => {
  it('completes a small file task end to end', async (ctx) => {
    const dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-live-')));
    const { db, close } = openDb(':memory:');
    const store = new EventStore(db);
    const models = new ModelRegistry();
    const runtime = new Runtime({ store, adapter: createModelAdapter(loadModelConfig(), models), models, retry: { maxAttempts: 3 } });
    try {
      const projectId = runtime.createProject({ name: 'Live smoke', goal: 'Verify the Desk runtime end to end' });
      const workspace = join(dir, 'ws');
      const expected = `hello from ${model}`;
      const agentId = runtime.createThread(projectId, {
        title: 'Hello file',
        brief: `Create a file named hello.txt in your workspace containing exactly this text and nothing else: ${expected}\nThen read it back with read_file to verify, then call complete.`,
        workspacePath: workspace,
        model,
      });
      runtime.sendMessage(agentId, 'Start now.');
      await runtime.whenIdle();

      const finished = store.list({ agentId, types: ['run.finished'] }).at(-1);
      if (finished?.type === 'run.finished' && finished.payload.reason === 'error' && /rate limit|429/i.test(finished.payload.detail ?? '')) {
        ctx.skip();
      }

      expect(getAgent(db, agentId)?.status).toBe('done');
      expect((await readFile(join(workspace, 'hello.txt'), 'utf8')).trim()).toBe(expected);
      const toolNames = store.list({ agentId, types: ['tool.call'] }).map((e) => (e.type === 'tool.call' ? e.payload.name : ''));
      expect(toolNames).toContain('read_file');
      expect(toolNames.at(-1)).toBe('complete');
      const usage = store.list({ agentId, types: ['usage'] });
      expect(usage.length).toBeGreaterThan(1);
      expect(usage.every((u) => u.type === 'usage' && !u.payload.estimated && u.payload.prompt_tokens > 0)).toBe(true);
    } finally {
      close();
      await rm(dir, { recursive: true, force: true });
    }
  });
});
```

- [ ] **Step 2: Confirm it is excluded from the default suite**

Run: `pnpm test`
Expected: PASS; output does not list `live.live.test.ts`.

- [ ] **Step 3: Run it live**

Run: `pnpm test:live`
Expected: `claude-opus-5-5`, `gpt-6-astra`, `gpt-6-sol` PASS. `claude-fable-5-1` PASS or SKIPPED (rate-limited). Any other failure is a real defect: diagnose with the persisted events (`store.list({ agentId })`) before changing code.

- [ ] **Step 4: Record verification in the spec**

In `docs/superpowers/specs/2026-09-23-desk-daemon-design.md` §12, change items 1 and 2 to:

```markdown
1. ✅ Verified 2026-09-23 (Plan 1 live smoke): streamed tool-call argument deltas and `stream_options.include_usage` work through the proxy for Claude and GPT families; Claude responses report `prompt_tokens_details.cached_tokens` (automatic prompt caching).
2. ✅ Verified 2026-09-23: multi-turn tool conversations (parallel `tool_calls` → `tool` results → final answer) round-trip for `claude-opus-5-5`, `gpt-6-astra`, `gpt-6-sol`.
```

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/live.live.test.ts docs/superpowers/specs/2026-09-23-desk-daemon-design.md
git commit -m "test(core): live smoke test per model against CLIProxyAPI" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Plan 1 Done Criteria

- `pnpm test` and `pnpm typecheck` pass from a clean checkout after `pnpm install`.
- `pnpm test:live` passes for Opus 5.5, Astra and Sol (Fable pass or skip).
- A thread can be created, messaged, steered mid-run, stopped, and run to `complete` through `Runtime`, with every step visible in the `events` table.
