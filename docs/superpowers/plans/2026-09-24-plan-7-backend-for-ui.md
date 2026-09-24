# Plan 7 · Backend for the UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add everything the desktop app needs from deskd: attention (with dismissal), a cross-project overview, thread diff and workspace files, historical skill versions, global usage, a richer health endpoint, secret-safe model-endpoint setup (Keychain), notifications posted by the daemon itself, and a bundled daemon.

**Architecture:** Every feature follows the existing layering: zod schemas in `@desk/protocol`, derivation and queries in `@desk/core` (`state/`, `skills/`, `workspaces/`, `model/`), thin Hono routes in `apps/daemon/src/routes/`, and new state recorded as events projected into tables. The daemon also gains a notifier and an esbuild bundle target.

**Tech Stack:** TypeScript (strict, `noUncheckedIndexedAccess`), zod 4, drizzle-orm + better-sqlite3, Hono, ws, Vitest, esbuild.

**Spec:** `docs/superpowers/specs/2026-09-24-desk-desktop-app-design.md` §4. Read `CLAUDE.md` first.

## Global Constraints

- `pnpm test` and `pnpm typecheck` must pass before every commit.
- State changes are events appended through `EventStore.append`; projection tables are written only in `events/projections.ts`.
- Runtime methods throw `NotFoundError` (404), `ConflictError` (409) or `ValidationError` (400) from `packages/core/src/errors.ts`. Any other HTTP status uses `HttpError` from `apps/daemon/src/http.ts`.
- The model API key must never appear in logs, events, `daemon.json`, API responses or tool environments.
- Schema change → regenerate the single squashed migration: `cd packages/core && rm -rf drizzle && npx drizzle-kit generate --name init` (no live data dir exists yet, so squashing is still safe).
- Don't add native dependencies. `esbuild` is already in the lockfile (via vite/tsx).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work on the `desktop-app` branch.

## File map

| File | Responsibility |
|---|---|
| `packages/protocol/src/events.ts` | + `attention.dismissed` event |
| `packages/protocol/src/api.ts` | + attention, overview, diff, files, usage, health, endpoint, config, stream hello schemas |
| `packages/core/src/db/schema.ts` | + `attention_dismissals` table |
| `packages/core/src/events/projections.ts` | + project `attention.dismissed` |
| `packages/core/src/state/queries.ts` | + `lastProjectEvent`, `hasProjectEventAfter`, `lastStallFor`, `getUsageByProject` |
| `packages/core/src/state/attention.ts` | derive attention items |
| `packages/core/src/state/overview.ts` | cross-project summary |
| `packages/core/src/workspaces/inspect.ts` | thread diff, workspace listing and file resolution |
| `packages/core/src/skills/store.ts` | + `getVersion` |
| `packages/core/src/model/config.ts` | split env/file loaders |
| `packages/core/src/model/endpoint.ts` | Keychain access, resolution order, connection test |
| `packages/core/src/model/switchable.ts` | adapter that can be (re)configured at runtime |
| `packages/core/src/runtime/runtime.ts` | + `dismissAttention`, `getSkillVersion`, `proxyState`, thread inspect wrappers |
| `packages/core/src/db/open.ts` | optional migrations folder |
| `apps/daemon/src/routes/ui.ts` | attention, overview, threads diff/files, skill versions, usage |
| `apps/daemon/src/routes/config.ts` | daemon config + model endpoint routes |
| `apps/daemon/src/config-file.ts` | `config.json` read/write |
| `apps/daemon/src/notifier.ts` | event → macOS notification |
| `apps/daemon/src/stream.ts` / `server.ts` | hello message, counting clients that notify |
| `apps/daemon/src/daemon.ts` / `main.ts` / `app.ts` / `paths.ts` | wiring |
| `apps/daemon/scripts/bundle.mjs` | esbuild bundle |
| `docs/api.md`, `CLAUDE.md` | docs |

---

### Task 1: Protocol additions

**Files:**
- Modify: `packages/protocol/src/events.ts`, `packages/protocol/src/api.ts`
- Test: `packages/protocol/src/api.test.ts`, `packages/protocol/src/events.test.ts`

**Interfaces:**
- Produces (exported from `@desk/protocol`): `AttentionKind`, `AttentionItem`, `AttentionResponse`, `OverviewThread`, `ProjectSummary`, `ThreadDiff`, `WorkspaceEntry`, `UsageResponse`, `HealthResponse` (extended), `ModelEndpointStatus`, `ModelEndpointPutRequest`, `ModelEndpointTestRequest`, `ModelEndpointTestResult`, `DaemonConfig`, `DaemonConfigPatch`, `StreamClientMessage` (now a union with `hello`), and the event type `'attention.dismissed'` with payload `{ item_id: string }`.

- [ ] **Step 1: Write failing tests**

Append to `packages/protocol/src/api.test.ts`:

```ts
import { AttentionItem, ModelEndpointPutRequest, StreamClientMessage, DaemonConfigPatch } from './api';

describe('UI API schemas', () => {
  it('parses an attention item', () => {
    const item = AttentionItem.parse({
      id: 'approval:ap1', kind: 'approval', project_id: 'p1', project_name: 'Demo', agent_id: 't1',
      title: 'Signup checklist wants to run bash', detail: 'rule 1', created_at: '2026-09-24T10:00:00.000Z',
      ref: { approval_id: 'ap1', thread_id: 't1' },
    });
    expect(item.kind).toBe('approval');
  });

  it('accepts hello and subscribe stream messages', () => {
    expect(StreamClientMessage.parse({ hello: { client: 'desktop', notifications: true } })).toEqual({ hello: { client: 'desktop', notifications: true } });
    expect(StreamClientMessage.parse({ subscribe: { project_id: '*', after_seq: 0 } })).toMatchObject({ subscribe: { project_id: '*' } });
    expect(() => StreamClientMessage.parse({ nope: 1 })).toThrow();
  });

  it('rejects API keys with whitespace, quotes or backslashes', () => {
    const ok = { base_url: 'http://127.0.0.1:8317/v1', api_key: 'sk-abc_123.XYZ' };
    expect(ModelEndpointPutRequest.parse(ok)).toEqual(ok);
    for (const bad of ['has space', 'quote"d', "single'q", 'back\\slash', 'new\nline', '']) {
      expect(() => ModelEndpointPutRequest.parse({ ...ok, api_key: bad })).toThrow();
    }
    expect(() => ModelEndpointPutRequest.parse({ ...ok, base_url: 'not a url' })).toThrow();
  });

  it('patches daemon config', () => {
    expect(DaemonConfigPatch.parse({ notifications: 'off' })).toEqual({ notifications: 'off' });
    expect(() => DaemonConfigPatch.parse({ notifications: 'sometimes' })).toThrow();
  });
});
```

Append to `packages/protocol/src/events.test.ts` (inside the file's existing top-level `describe`, or a new one):

```ts
import { EventBody } from './events';

describe('attention.dismissed', () => {
  it('is a valid event body', () => {
    expect(EventBody.parse({ type: 'attention.dismissed', payload: { item_id: 'report:12:0' } })).toMatchObject({ type: 'attention.dismissed' });
    expect(() => EventBody.parse({ type: 'attention.dismissed', payload: { item_id: '' } })).toThrow();
  });
});
```

(If `EventBody` or `describe/it/expect` are already imported at the top of the file, don't import them twice.)

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run packages/protocol`
Expected: FAIL: `AttentionItem` (and the others) are not exported, and `attention.dismissed` is an invalid discriminator.

- [ ] **Step 3: Implement**

In `packages/protocol/src/events.ts`, add this entry to the `EventBody` discriminated union, right after the `'system.notice'` entry:

```ts
  event('attention.dismissed', z.object({ item_id: z.string().min(1) })),
```

In `packages/protocol/src/api.ts`, replace the existing `HealthResponse` and `StreamClientMessage` definitions with the ones below, and append the rest. Also add `AgentStatus` to the imports from `./domain` if it isn't there already.

```ts
export const HealthResponse = z.object({
  version: z.string(),
  protocol_version: z.number().int(),
  proxy: z.enum(['up', 'down', 'unknown']).optional(),
  uptime_s: z.number().int().min(0).optional(),
});
export type HealthResponse = z.infer<typeof HealthResponse>;

/** Client → server on the /v1/stream WebSocket. `project_id` may be '*' for every project. */
export const StreamClientMessage = z.union([
  z.object({ subscribe: z.object({ project_id: z.string().min(1), after_seq: z.number().int().min(0) }) }),
  /** Identifies the client; a desktop client that shows notifications silences the daemon's own notifier. */
  z.object({ hello: z.object({ client: z.string().min(1).max(64), notifications: z.boolean().default(false) }) }),
]);
export type StreamClientMessage = z.infer<typeof StreamClientMessage>;

// ── UI endpoints ─────────────────────────────────────────────────────

export const AttentionKind = z.enum(['approval', 'question', 'needs_you', 'stalled', 'failed']);
export type AttentionKind = z.infer<typeof AttentionKind>;

/** One thing that needs the user. `id` is stable: `approval:<id>`, `question:<event>`, `report:<event>:<i>`, `stalled:<thread>:<event>`, `failed:<thread>`. */
export const AttentionItem = z.object({
  id: z.string(),
  kind: AttentionKind,
  project_id: z.string(),
  project_name: z.string(),
  agent_id: z.string().nullable(),
  title: z.string(),
  detail: z.string(),
  created_at: z.string(),
  ref: z.object({
    approval_id: z.string().optional(),
    event_id: z.number().int().optional(),
    thread_id: z.string().optional(),
    options: z.array(z.string()).optional(),
  }),
});
export type AttentionItem = z.infer<typeof AttentionItem>;

export const AttentionResponse = z.object({ items: z.array(AttentionItem), seq: z.number().int() });
export type AttentionResponse = z.infer<typeof AttentionResponse>;

export const OverviewThread = z.object({
  id: z.string(),
  title: z.string().nullable(),
  status: AgentStatus,
  reason: z.string().nullable(),
  /** The tool call in flight, e.g. `bash · python3 funnel.py`; null when none. */
  activity: z.string().nullable(),
  model: z.string(),
  git_branch: z.string().nullable(),
  skills: z.array(z.string()),
  review_round: z.number().int(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type OverviewThread = z.infer<typeof OverviewThread>;

export const ProjectSummary = z.object({
  project: z.object({ id: z.string(), name: z.string(), goal: z.string(), updated_at: z.string() }),
  desk_status: AgentStatus,
  threads: z.array(OverviewThread),
  latest_report: z.object({ headline: z.string(), ts: z.string() }).nullable(),
  plan_progress: z.object({ done: z.number().int(), total: z.number().int() }),
  attention_count: z.number().int(),
});
export type ProjectSummary = z.infer<typeof ProjectSummary>;

export const DiffFileStatus = z.enum(['added', 'modified', 'deleted']);
export const ThreadDiff = z.object({
  base: z.string(),
  branch: z.string(),
  files: z.array(z.object({ path: z.string(), status: DiffFileStatus, additions: z.number().int().nullable(), deletions: z.number().int().nullable() })),
  patch: z.string(),
});
export type ThreadDiff = z.infer<typeof ThreadDiff>;

export const WorkspaceEntry = z.object({ name: z.string(), path: z.string(), type: z.enum(['file', 'dir']), size: z.number().int() });
export type WorkspaceEntry = z.infer<typeof WorkspaceEntry>;

export const UsageResponse = z.object({
  rows: z.array(z.object({ project_id: z.string(), model: z.string(), prompt_tokens: z.number().int(), completion_tokens: z.number().int() })),
  totals: z.object({ prompt_tokens: z.number().int(), completion_tokens: z.number().int() }),
});
export type UsageResponse = z.infer<typeof UsageResponse>;

export const ModelEndpointStatus = z.object({
  configured: z.boolean(),
  source: z.enum(['env', 'file', 'keychain']).nullable(),
  base_url: z.string().nullable(),
});
export type ModelEndpointStatus = z.infer<typeof ModelEndpointStatus>;

/** Printable ASCII, no whitespace, quotes or backslashes (keys travel through `security -i`). */
const ApiKey = z
  .string()
  .min(1)
  .max(512)
  .regex(/^[\x21-\x7E]+$/, 'API keys are printable ASCII without spaces')
  .refine((k) => !/["'\\]/.test(k), 'API keys cannot contain quotes or backslashes');

export const ModelEndpointPutRequest = z.object({ base_url: z.url(), api_key: ApiKey });
export type ModelEndpointPutRequest = z.input<typeof ModelEndpointPutRequest>;

/** Test a candidate endpoint before saving it, or the current one when omitted. */
export const ModelEndpointTestRequest = ModelEndpointPutRequest.optional();
export type ModelEndpointTestRequest = z.input<typeof ModelEndpointTestRequest>;

export const ModelEndpointTestResult = z.object({ ok: z.boolean(), models: z.array(z.string()).optional(), error: z.string().optional() });
export type ModelEndpointTestResult = z.infer<typeof ModelEndpointTestResult>;

export const DaemonConfig = z.object({ notifications: z.enum(['auto', 'off']) });
export type DaemonConfig = z.infer<typeof DaemonConfig>;
export const DaemonConfigPatch = DaemonConfig.partial();
export type DaemonConfigPatch = z.input<typeof DaemonConfigPatch>;
```

`StreamClientMessage` is now a union, so update its one consumer, `apps/daemon/src/stream.ts`. Replace the body of the `ws.on('message', …)` handler from `const { project_id, after_seq } = parsed.subscribe;` onward with:

```ts
    if ('hello' in parsed) {
      hello = parsed.hello;
      return;
    }
    const { project_id, after_seq } = parsed.subscribe;
```

In `serve()`, declare `let hello: { client: string; notifications: boolean } | null = null;` next to `let sub`. It is used in Task 11; until then add `void hello;` after the declaration so lint stays quiet. Update the error text to `'Expected {"subscribe":{…}} or {"hello":{…}}'`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run packages/protocol apps/daemon/src/stream.test.ts && pnpm typecheck`
Expected: PASS. (`apps/daemon/src/app.test.ts` still expects the old health body; that changes in Task 8.)

- [ ] **Step 5: Commit**

```bash
git add packages/protocol apps/daemon/src/stream.ts
git commit -m "feat(protocol): UI endpoint schemas, attention.dismissed event, stream hello

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Attention derivation and dismissal

**Files:**
- Modify: `packages/core/src/db/schema.ts`, `packages/core/src/events/projections.ts`, `packages/core/src/state/queries.ts`, `packages/core/src/runtime/runtime.ts`, `packages/core/src/index.ts`
- Create: `packages/core/src/state/attention.ts`
- Regenerate: `packages/core/drizzle/`
- Test: `packages/core/src/state/attention.test.ts`

**Interfaces:**
- Consumes: `AttentionItem` from `@desk/protocol` (Task 1).
- Produces:
  - `listAttention(db: Db, opts?: { projectId?: string }): AttentionItem[]`, sorted by `created_at` ascending.
  - `Runtime.dismissAttention(itemId: string): void`
  - queries: `lastProjectEvent<T extends EventType>(db, projectId, type: T): EventOf<T> | undefined`, `hasProjectEventAfter(db, projectId, type, afterId): boolean`, `lastStallFor(db, projectId, threadId): EventOf<'message.agent'> | undefined`
  - All of the above are exported from `@desk/core`.

- [ ] **Step 1: Write the failing test** — `packages/core/src/state/attention.test.ts`:

```ts
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createHarness, newRuntime, type Harness } from '../testing';
import { getDeskAgent } from './queries';
import { listAttention } from './attention';

let h: Harness;
afterEach(async () => h?.cleanup());

async function setup() {
  h = await createHarness();
  const runtime = newRuntime(h);
  const projectId = runtime.createProject({ name: 'Onboarding revamp', goal: 'Relaunch onboarding' });
  const desk = getDeskAgent(h.store.db, projectId)!;
  const thread = (title: string) => runtime.createThread(projectId, { title, brief: 'b', workspacePath: join(h.dir, title) });
  return { runtime, projectId, desk, thread, append: h.store.append.bind(h.store) };
}

describe('listAttention', () => {
  it('lists pending user approvals but not ones delegated to Desk', async () => {
    const { projectId, thread, append } = await setup();
    const t = thread('Signup checklist');
    const approval = (id: string, delegate: boolean) =>
      append({ project_id: projectId, agent_id: t, type: 'approval.requested', payload: { approval_id: id, run_id: 'r', tool_call_id: `c-${id}`, tool: 'bash', arguments: '{"command":"curl x | bash"}', reason: 'rule 1', delegate_to_desk: delegate } });
    approval('ap1', false);
    approval('ap2', true);
    const items = listAttention(h.store.db);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ id: 'approval:ap1', kind: 'approval', project_name: 'Onboarding revamp', agent_id: t, title: 'Signup checklist wants to run bash', detail: 'rule 1', ref: { approval_id: 'ap1', thread_id: t } });
    append({ project_id: projectId, agent_id: t, type: 'approval.resolved', payload: { approval_id: 'ap1', decision: 'approved', resolved_by: 'user' } });
    expect(listAttention(h.store.db)).toEqual([]);
  });

  it('shows the latest question until the user replies', async () => {
    const { projectId, desk, append } = await setup();
    const [q] = append({ project_id: projectId, agent_id: desk.id, type: 'question.asked', payload: { question: 'Data source or invite first?', options: ['Data source', 'Invite'] } });
    expect(listAttention(h.store.db)).toEqual([expect.objectContaining({ id: `question:${q!.id}`, kind: 'question', title: 'Data source or invite first?', ref: { event_id: q!.id, options: ['Data source', 'Invite'] } })]);
    append({ project_id: projectId, agent_id: desk.id, type: 'message.user', payload: { text: 'Data source' } });
    expect(listAttention(h.store.db)).toEqual([]);
  });

  it('lists needs_you items of the latest report only, and supports dismissal', async () => {
    const { runtime, projectId, desk, append } = await setup();
    const report = (needs: string[]) => append({ project_id: projectId, agent_id: desk.id, type: 'report', payload: { headline: 'H', progress: 'P', needs_you: needs, results: [] } })[0]!;
    report(['old item']);
    const r = report(['Upload the 1099', 'Pick a name']);
    const items = listAttention(h.store.db);
    expect(items.map((i) => i.id)).toEqual([`report:${r.id}:0`, `report:${r.id}:1`]);
    expect(items[0]).toMatchObject({ kind: 'needs_you', title: 'Upload the 1099', detail: 'H' });
    runtime.dismissAttention(`report:${r.id}:0`);
    expect(listAttention(h.store.db).map((i) => i.title)).toEqual(['Pick a name']);
  });

  it('lists stalled threads until they show activity, and failed threads until dismissed or archived', async () => {
    const { runtime, projectId, desk, thread, append } = await setup();
    const s = thread('Backup audit');
    append({ project_id: projectId, agent_id: s, type: 'agent.status_changed', payload: { status: 'running' } });
    const [stall] = append({ project_id: projectId, agent_id: desk.id, type: 'message.agent', payload: { from_agent_id: s, from_label: 'thread', kind: 'stalled', text: 'No activity for 20 minutes (status: running).' } });
    const f = thread('Deploy');
    append({ project_id: projectId, agent_id: f, type: 'agent.status_changed', payload: { status: 'failed', reason: 'Model error' } });
    let items = listAttention(h.store.db);
    expect(items.map((i) => [i.kind, i.id])).toEqual([
      ['stalled', `stalled:${s}:${stall!.id}`],
      ['failed', `failed:${f}`],
    ]);
    expect(items[1]).toMatchObject({ title: 'Deploy failed', detail: 'Model error', ref: { thread_id: f } });
    append({ project_id: projectId, agent_id: s, type: 'tool.call', payload: { run_id: 'r', tool_call_id: 'c', name: 'bash', arguments: '{}' } });
    runtime.dismissAttention(`failed:${f}`);
    items = listAttention(h.store.db);
    expect(items).toEqual([]);
  });

  it('refuses to dismiss approvals, questions and unknown items', async () => {
    const { runtime } = await setup();
    expect(() => runtime.dismissAttention('approval:x')).toThrow(/answered/);
    expect(() => runtime.dismissAttention('question:1')).toThrow(/answered/);
    expect(() => runtime.dismissAttention('report:999:0')).toThrow(/No attention item/);
  });

  it('filters by project and skips archived projects', async () => {
    const { runtime, projectId, desk, append } = await setup();
    const other = runtime.createProject({ name: 'Other', goal: 'g' });
    const otherDesk = getDeskAgent(h.store.db, other)!;
    append({ project_id: projectId, agent_id: desk.id, type: 'question.asked', payload: { question: 'A?' } });
    append({ project_id: other, agent_id: otherDesk.id, type: 'question.asked', payload: { question: 'B?' } });
    expect(listAttention(h.store.db, { projectId: other }).map((i) => i.title)).toEqual(['B?']);
    runtime.archiveProject(other);
    expect(listAttention(h.store.db).map((i) => i.title)).toEqual(['A?']);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run packages/core/src/state/attention.test.ts`
Expected: FAIL: `Cannot find module './attention'`.

- [ ] **Step 3: Implement**

`packages/core/src/db/schema.ts`: append this table:

```ts
/** Attention items the user dismissed (report needs_you, stalled and failed threads). */
export const attentionDismissals = sqliteTable('attention_dismissals', {
  item_id: text('item_id').primaryKey(),
  project_id: text('project_id').notNull(),
  dismissed_at: text('dismissed_at').notNull(),
});
```

`packages/core/src/events/projections.ts`: add `attentionDismissals` to the schema import, then add this case before `default:`:

```ts
    case 'attention.dismissed':
      tx.insert(attentionDismissals).values({ item_id: ev.payload.item_id, project_id: ev.project_id, dismissed_at: ev.ts }).onConflictDoNothing().run();
      return;
```

Regenerate the migration:

```bash
cd packages/core && rm -rf drizzle && npx drizzle-kit generate --name init && cd ../..
```

`packages/core/src/state/queries.ts`: add `sql` to the drizzle import and `EventOf` to the `@desk/protocol` type import, then append:

```ts
/** The project's most recent event of `type`. */
export function lastProjectEvent<T extends EventType>(db: Db, projectId: string, type: T): EventOf<T> | undefined {
  const row = db
    .select()
    .from(events)
    .where(and(eq(events.project_id, projectId), eq(events.type, type)))
    .orderBy(desc(events.id))
    .limit(1)
    .get();
  return row ? ({ ...row } as unknown as EventOf<T>) : undefined;
}

/** Whether the project has an event of `type` after event id `afterId`. */
export function hasProjectEventAfter(db: Db, projectId: string, type: EventType, afterId: number): boolean {
  return (
    db
      .select({ id: events.id })
      .from(events)
      .where(and(eq(events.project_id, projectId), eq(events.type, type), gt(events.id, afterId)))
      .limit(1)
      .get() !== undefined
  );
}

/** The latest `stalled` notice sent on behalf of `threadId`. */
export function lastStallFor(db: Db, projectId: string, threadId: string): EventOf<'message.agent'> | undefined {
  const row = db
    .select()
    .from(events)
    .where(
      and(
        eq(events.project_id, projectId),
        eq(events.type, 'message.agent'),
        sql`json_extract(${events.payload}, '$.kind') = 'stalled'`,
        sql`json_extract(${events.payload}, '$.from_agent_id') = ${threadId}`,
      ),
    )
    .orderBy(desc(events.id))
    .limit(1)
    .get();
  return row ? ({ ...row } as unknown as EventOf<'message.agent'>) : undefined;
}
```

(Add `gt` to the drizzle-orm import too.)

Create `packages/core/src/state/attention.ts`:

```ts
import type { AttentionItem } from '@desk/protocol';
import type { Db } from '../db/open';
import { attentionDismissals } from '../db/schema';
import {
  hasProjectEventAfter,
  lastEvent,
  lastProjectEvent,
  lastStallFor,
  listAgents,
  listApprovals,
  listProjects,
  listThreads,
  type AgentRow,
} from './queries';

const label = (agent: AgentRow | undefined) => (agent?.role === 'desk' ? 'Desk' : (agent?.title ?? 'A thread'));

/**
 * Everything that currently needs the user, derived from state (see the design spec §4.1).
 * Approvals and questions leave the list when answered; the other kinds can also be dismissed.
 */
export function listAttention(db: Db, opts: { projectId?: string } = {}): AttentionItem[] {
  const dismissed = new Set(db.select({ id: attentionDismissals.item_id }).from(attentionDismissals).all().map((r) => r.id));
  const items: AttentionItem[] = [];
  for (const p of listProjects(db)) {
    if (opts.projectId && p.id !== opts.projectId) continue;
    const base = { project_id: p.id, project_name: p.name };
    const agents = new Map(listAgents(db, p.id).map((a) => [a.id, a]));

    for (const a of listApprovals(db, p.id, 'pending')) {
      if (a.delegate_to_desk) continue;
      const agent = agents.get(a.agent_id);
      items.push({
        ...base,
        id: `approval:${a.id}`,
        kind: 'approval',
        agent_id: a.agent_id,
        title: `${label(agent)} wants to run ${a.tool}`,
        detail: a.reason,
        created_at: a.created_at,
        ref: { approval_id: a.id, ...(agent?.role === 'thread' ? { thread_id: agent.id } : {}) },
      });
    }

    const q = lastProjectEvent(db, p.id, 'question.asked');
    if (q && !hasProjectEventAfter(db, p.id, 'message.user', q.id)) {
      items.push({
        ...base,
        id: `question:${q.id}`,
        kind: 'question',
        agent_id: q.agent_id,
        title: q.payload.question,
        detail: '',
        created_at: q.ts,
        ref: { event_id: q.id, ...(q.payload.options ? { options: q.payload.options } : {}) },
      });
    }

    const report = lastProjectEvent(db, p.id, 'report');
    report?.payload.needs_you.forEach((text, i) => {
      const id = `report:${report.id}:${i}`;
      if (dismissed.has(id)) return;
      items.push({ ...base, id, kind: 'needs_you', agent_id: report.agent_id, title: text, detail: report.payload.headline, created_at: report.ts, ref: { event_id: report.id } });
    });

    for (const t of listThreads(db, p.id)) {
      if (t.archived_at) continue;
      if (t.status === 'failed') {
        const id = `failed:${t.id}`;
        if (dismissed.has(id)) continue;
        const status = lastEvent(db, t.id, 'agent.status_changed');
        const reason = status?.type === 'agent.status_changed' ? (status.payload.reason ?? '') : '';
        items.push({ ...base, id, kind: 'failed', agent_id: t.id, title: `${t.title ?? 'Thread'} failed`, detail: reason, created_at: t.updated_at, ref: { thread_id: t.id } });
        continue;
      }
      if (t.status !== 'running' && t.status !== 'waiting') continue;
      const stall = lastStallFor(db, p.id, t.id);
      if (!stall) continue;
      const last = lastEvent(db, t.id);
      if (last && last.id > stall.id) continue;
      const id = `stalled:${t.id}:${stall.id}`;
      if (dismissed.has(id)) continue;
      items.push({ ...base, id, kind: 'stalled', agent_id: t.id, title: `${t.title ?? 'Thread'} has stalled`, detail: stall.payload.text, created_at: stall.ts, ref: { thread_id: t.id, event_id: stall.id } });
    }
  }
  return items.sort((a, b) => a.created_at.localeCompare(b.created_at));
}
```

`lastEvent(db, t.id)` looks up events by `agent_id`. The stall notice is stored under Desk's `agent_id`, so a thread's own events are what clear it, which is correct.

`packages/core/src/runtime/runtime.ts`: import `listAttention` from `'../state/attention'` and add this method in the "projects & agents" section:

```ts
  /** Dismisses a needs_you, stalled or failed attention item. Approvals and questions leave the list when answered. */
  dismissAttention(itemId: string): void {
    const kind = itemId.split(':')[0];
    if (kind === 'approval' || kind === 'question') throw new ConflictError(`${kind} items leave the list when they are answered`);
    const item = listAttention(this.o.store.db).find((i) => i.id === itemId);
    if (!item) throw new NotFoundError(`No attention item ${itemId}`);
    this.o.store.append({ project_id: item.project_id, agent_id: null, type: 'attention.dismissed', payload: { item_id: itemId } });
  }
```

`packages/core/src/index.ts`: add `hasProjectEventAfter, lastProjectEvent, lastStallFor` to the `./state/queries` export list, and add `export { listAttention } from './state/attention';`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run packages/core && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): derive attention items; attention.dismissed event and projection

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Cross-project overview

**Files:**
- Create: `packages/core/src/state/overview.ts`
- Modify: `packages/core/src/index.ts`
- Test: `packages/core/src/state/overview.test.ts`

**Interfaces:**
- Consumes: `listAttention` (Task 2); `ProjectSummary` (Task 1).
- Produces: `listOverview(db: Db): ProjectSummary[]` (non-archived projects, oldest first); `summarizeToolArgs(args: string, max?: number): string`.

- [ ] **Step 1: Write the failing test** — `packages/core/src/state/overview.test.ts`:

```ts
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createHarness, newRuntime, type Harness } from '../testing';
import { getDeskAgent } from './queries';
import { listOverview, summarizeToolArgs } from './overview';

let h: Harness;
afterEach(async () => h?.cleanup());

describe('listOverview', () => {
  it('summarises projects with threads, activity, report, plan and attention', async () => {
    h = await createHarness();
    const runtime = newRuntime(h);
    const p = runtime.createProject({ name: 'Onboarding revamp', goal: 'Relaunch' });
    const desk = getDeskAgent(h.store.db, p)!;
    const t = runtime.createThread(p, { title: 'Funnel analysis', brief: 'b', workspacePath: join(h.dir, 'f') });
    const append = h.store.append.bind(h.store);
    append({ project_id: p, agent_id: t, type: 'agent.status_changed', payload: { status: 'running' } });
    append({ project_id: p, agent_id: t, type: 'tool.call', payload: { run_id: 'r', tool_call_id: 'c1', name: 'bash', arguments: JSON.stringify({ command: 'python3 funnel.py --by-week' }) } });
    append({ project_id: p, agent_id: desk.id, type: 'report', payload: { headline: 'Research is in', progress: '', needs_you: ['Approve bun'], results: [] } });
    append({
      project_id: p,
      agent_id: desk.id,
      type: 'plan.updated',
      payload: { items: [
        { id: '1', title: 'A', status: 'done', thread_ids: [], notes: '' },
        { id: '2', title: 'B', status: 'in_progress', thread_ids: [t], notes: '' },
        { id: '3', title: 'C', status: 'dropped', thread_ids: [], notes: '' },
      ] },
    });

    const [s] = listOverview(h.store.db);
    expect(s).toMatchObject({
      project: { id: p, name: 'Onboarding revamp', goal: 'Relaunch' },
      desk_status: 'idle',
      latest_report: { headline: 'Research is in' },
      plan_progress: { done: 1, total: 2 },
      attention_count: 1,
    });
    expect(s!.threads).toEqual([expect.objectContaining({ id: t, title: 'Funnel analysis', status: 'running', activity: 'bash · python3 funnel.py --by-week', skills: [], review_round: 0 })]);

    append({ project_id: p, agent_id: t, type: 'tool.result', payload: { run_id: 'r', tool_call_id: 'c1', name: 'bash', status: 'ok', content: 'done' } });
    expect(listOverview(h.store.db)[0]!.threads[0]!.activity).toBeNull();
  });

  it('shows the waiting reason and skips archived threads and projects', async () => {
    h = await createHarness();
    const runtime = newRuntime(h);
    const p = runtime.createProject({ name: 'P', goal: 'g' });
    const t = runtime.createThread(p, { title: 'W', brief: 'b', workspacePath: join(h.dir, 'w') });
    h.store.append({ project_id: p, agent_id: t, type: 'agent.status_changed', payload: { status: 'waiting', reason: 'Waiting for approval' } });
    expect(listOverview(h.store.db)[0]!.threads[0]).toMatchObject({ status: 'waiting', reason: 'Waiting for approval' });
    h.store.append({ project_id: p, agent_id: t, type: 'agent.archived', payload: {} });
    expect(listOverview(h.store.db)[0]!.threads).toEqual([]);
    runtime.archiveProject(p);
    expect(listOverview(h.store.db)).toEqual([]);
  });
});

describe('summarizeToolArgs', () => {
  it('uses the first string argument, collapsed and truncated', () => {
    expect(summarizeToolArgs('{"path":"emails/04.md","content":"x"}')).toBe('emails/04.md');
    expect(summarizeToolArgs('{"n":1}')).toBe('');
    expect(summarizeToolArgs('not json')).toBe('not json');
    expect(summarizeToolArgs(JSON.stringify({ command: 'a\n  b' }))).toBe('a b');
    expect(summarizeToolArgs(JSON.stringify({ command: 'x'.repeat(200) }), 10)).toBe('xxxxxxxxx…');
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run packages/core/src/state/overview.test.ts`
Expected: FAIL: module not found.

- [ ] **Step 3: Implement** — `packages/core/src/state/overview.ts`:

```ts
import type { OverviewThread, ProjectSummary } from '@desk/protocol';
import type { Db } from '../db/open';
import { getPlan } from '../coordination/plan';
import { listAttention } from './attention';
import { getDeskAgent, lastEvent, lastProjectEvent, listProjects, listThreads, type AgentRow } from './queries';

/** A short, single-line rendering of a tool call's arguments: its first string value. */
export function summarizeToolArgs(args: string, max = 80): string {
  let text: string;
  try {
    const parsed = JSON.parse(args) as Record<string, unknown>;
    text = String(Object.values(parsed).find((v) => typeof v === 'string') ?? '');
  } catch {
    text = args;
  }
  text = text.replace(/\s+/g, ' ').trim();
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function activity(db: Db, agentId: string): string | null {
  const call = lastEvent(db, agentId, 'tool.call');
  if (call?.type !== 'tool.call') return null;
  const result = lastEvent(db, agentId, 'tool.result');
  if (result && result.id > call.id) return null;
  return `${call.payload.name} · ${summarizeToolArgs(call.payload.arguments)}`;
}

function reason(db: Db, t: AgentRow): string | null {
  if (!['waiting', 'queued', 'failed', 'cancelled'].includes(t.status)) return null;
  const ev = lastEvent(db, t.id, 'agent.status_changed');
  return ev?.type === 'agent.status_changed' ? (ev.payload.reason ?? null) : null;
}

/** One summary per open project: enough to draw the map, the home screen and the tray without per-project calls. */
export function listOverview(db: Db): ProjectSummary[] {
  const attention = listAttention(db);
  return listProjects(db).map((p) => {
    const threads: OverviewThread[] = listThreads(db, p.id)
      .filter((t) => !t.archived_at)
      .map((t) => ({
        id: t.id,
        title: t.title,
        status: t.status,
        reason: reason(db, t),
        activity: activity(db, t.id),
        model: t.model,
        git_branch: t.git_branch,
        skills: t.active_skills,
        review_round: t.review_round,
        created_at: t.created_at,
        updated_at: t.updated_at,
      }));
    const items = (getPlan(db, p.id)?.items ?? []).filter((i) => i.status !== 'dropped');
    const report = lastProjectEvent(db, p.id, 'report');
    return {
      project: { id: p.id, name: p.name, goal: p.goal, updated_at: p.updated_at },
      desk_status: getDeskAgent(db, p.id)?.status ?? 'idle',
      threads,
      latest_report: report ? { headline: report.payload.headline, ts: report.ts } : null,
      plan_progress: { done: items.filter((i) => i.status === 'done').length, total: items.length },
      attention_count: attention.filter((a) => a.project_id === p.id).length,
    };
  });
}
```

`packages/core/src/index.ts`: add `export { listOverview, summarizeToolArgs } from './state/overview';`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run packages/core/src/state && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): cross-project overview with thread activity, report and plan progress

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Thread diff and workspace files

**Files:**
- Create: `packages/core/src/workspaces/inspect.ts`
- Modify: `packages/core/src/index.ts`
- Test: `packages/core/src/workspaces/inspect.test.ts`

**Interfaces:**
- Consumes: `git()` from `./workspaces`; `resolveInside` from `../tools/paths`; `ToolDenied`.
- Produces:
  - `threadDiff(t: AgentRow): Promise<ThreadDiff>`. Throws `ConflictError` for a non-git thread and for an archived or removed workspace.
  - `listWorkspace(t: AgentRow, rel?: string): Promise<WorkspaceEntry[]>`
  - `resolveWorkspaceFile(t: AgentRow, rel: string): Promise<string>`. Throws `ToolDenied` for paths outside the workspace and `NotFoundError` for missing files.

- [ ] **Step 1: Write the failing test** — `packages/core/src/workspaces/inspect.test.ts`:

```ts
import { execFileSync } from 'node:child_process';
import { mkdir, mkdtemp, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { AgentRow } from '../state/queries';
import { ToolDenied } from '../tools/types';
import { createWorkspace } from './workspaces';
import { listWorkspace, resolveWorkspaceFile, threadDiff } from './inspect';

let dir: string;
beforeEach(async () => {
  dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-inspect-')));
});
afterEach(async () => rm(dir, { recursive: true, force: true }));

const g = (cwd: string, ...args: string[]) => execFileSync('git', ['-c', 'user.email=t@t', '-c', 'user.name=t', ...args], { cwd, stdio: 'pipe' }).toString();

function row(over: Partial<AgentRow>): AgentRow {
  return {
    id: 't1', project_id: 'p', role: 'thread', status: 'running', model: 'm', title: 'T', brief: 'b', workspace_path: null, parent_id: null,
    inbox_cursor: 0, review_round: 0, result_summary: null, result_artifacts: null, active_skills: [], git_source_id: null, git_branch: null,
    git_base: null, git_common_dir: null, archived_at: null, created_at: '', updated_at: '', ...over,
  };
}

async function gitThread() {
  const repo = join(dir, 'repo');
  await mkdir(repo);
  g(repo, 'init', '-q', '-b', 'main');
  await writeFile(join(repo, 'a.txt'), 'one\ntwo\n');
  await writeFile(join(repo, 'gone.txt'), 'bye\n');
  g(repo, 'add', '.');
  g(repo, 'commit', '-qm', 'init');
  const ws = join(dir, 'ws');
  const { git } = await createWorkspace({ path: ws, git: { sourcePath: repo, branch: 'desk/test' } });
  return row({ workspace_path: ws, git_branch: git!.branch, git_base: git!.base, git_common_dir: git!.common_dir });
}

describe('threadDiff', () => {
  it('reports added, modified and deleted files with line counts and a patch', async () => {
    const t = await gitThread();
    await writeFile(join(t.workspace_path!, 'a.txt'), 'one\nTWO\nthree\n');
    await writeFile(join(t.workspace_path!, 'new.md'), '# hi\n');
    await rm(join(t.workspace_path!, 'gone.txt'));
    const d = await threadDiff(t);
    expect(d).toMatchObject({ base: t.git_base, branch: 'desk/test' });
    expect(d.files).toEqual([
      { path: 'a.txt', status: 'modified', additions: 2, deletions: 1 },
      { path: 'gone.txt', status: 'deleted', additions: 0, deletions: 1 },
      { path: 'new.md', status: 'added', additions: 1, deletions: 0 },
    ]);
    expect(d.patch).toContain('+TWO');
  });

  it('refuses non-git and archived threads', async () => {
    await expect(threadDiff(row({ workspace_path: dir }))).rejects.toThrow(/does not work in git/);
    const t = await gitThread();
    await expect(threadDiff({ ...t, archived_at: 'x' })).rejects.toThrow(/archived/);
  });
});

describe('workspace files', () => {
  it('lists directories first, hides .git, and resolves files inside the workspace only', async () => {
    const ws = join(dir, 'plain');
    await mkdir(join(ws, 'emails'), { recursive: true });
    await writeFile(join(ws, 'emails', '01.md'), 'hello');
    await writeFile(join(ws, 'notes.txt'), 'n');
    await mkdir(join(ws, '.git'));
    await symlink('/etc', join(ws, 'escape'));
    const t = row({ workspace_path: ws });
    expect(await listWorkspace(t)).toEqual([
      { name: 'emails', path: 'emails', type: 'dir', size: 0 },
      { name: 'escape', path: 'escape', type: 'dir', size: 0 },
      { name: 'notes.txt', path: 'notes.txt', type: 'file', size: 1 },
    ]);
    expect(await listWorkspace(t, 'emails')).toEqual([{ name: '01.md', path: 'emails/01.md', type: 'file', size: 5 }]);
    expect(await resolveWorkspaceFile(t, 'emails/01.md')).toBe(join(ws, 'emails', '01.md'));
    await expect(resolveWorkspaceFile(t, '../repo')).rejects.toBeInstanceOf(ToolDenied);
    await expect(resolveWorkspaceFile(t, 'escape/hosts')).rejects.toBeInstanceOf(ToolDenied);
    await expect(listWorkspace(t, 'escape')).rejects.toBeInstanceOf(ToolDenied);
    await expect(resolveWorkspaceFile(t, 'missing.txt')).rejects.toThrow(/No such file/);
    await expect(listWorkspace({ ...t, archived_at: 'x' })).rejects.toThrow(/archived/);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run packages/core/src/workspaces/inspect.test.ts`
Expected: FAIL: module not found.

- [ ] **Step 3: Implement** — `packages/core/src/workspaces/inspect.ts`:

```ts
import { existsSync } from 'node:fs';
import { readdir, stat } from 'node:fs/promises';
import { join, relative } from 'node:path';
import type { ThreadDiff, WorkspaceEntry } from '@desk/protocol';
import { ConflictError, NotFoundError } from '../errors';
import type { AgentRow } from '../state/queries';
import { resolveInside } from '../tools/paths';
import { git } from './workspaces';

function requireWorkspace(t: AgentRow): string {
  if (!t.workspace_path) throw new ConflictError(`Thread ${t.id} has no workspace`);
  if (t.archived_at || !existsSync(t.workspace_path)) {
    throw new ConflictError(`Thread ${t.id} is archived; its workspace was removed${t.git_branch ? ` (branch ${t.git_branch} is kept)` : ''}`);
  }
  return t.workspace_path;
}

const STATUS: Record<string, ThreadDiff['files'][number]['status']> = { A: 'added', M: 'modified', D: 'deleted' };

/** The thread's changes against its base commit, including uncommitted and untracked files. */
export async function threadDiff(t: AgentRow): Promise<ThreadDiff> {
  if (!t.git_base || !t.git_branch) throw new ConflictError(`Thread ${t.id} does not work in git`);
  const ws = requireWorkspace(t);
  await git(ws, ['add', '--intent-to-add', '--all']);
  const names = await git(ws, ['diff', '--no-renames', '--name-status', t.git_base]);
  const counts = await git(ws, ['diff', '--no-renames', '--numstat', t.git_base]);
  const patch = await git(ws, ['diff', '--no-renames', t.git_base]);
  const numbers = new Map<string, [number | null, number | null]>();
  for (const line of counts.split('\n').filter(Boolean)) {
    const [add, del, ...path] = line.split('\t');
    numbers.set(path.join('\t'), [add === '-' ? null : Number(add), del === '-' ? null : Number(del)]);
  }
  const files = names
    .split('\n')
    .filter(Boolean)
    .map((line) => {
      const [code, ...path] = line.split('\t');
      const p = path.join('\t');
      const [additions, deletions] = numbers.get(p) ?? [null, null];
      return { path: p, status: STATUS[code?.[0] ?? 'M'] ?? 'modified', additions, deletions };
    })
    .sort((a, b) => a.path.localeCompare(b.path));
  return { base: t.git_base, branch: t.git_branch, files, patch };
}

/** Lists one directory of the workspace (directories first, `.git` hidden). Symlinks show as entries but cannot be followed out. */
export async function listWorkspace(t: AgentRow, rel = ''): Promise<WorkspaceEntry[]> {
  const ws = requireWorkspace(t);
  const root = await resolveInside('.', [ws], ws);
  const dir = await resolveInside(rel || '.', [ws], ws);
  const entries = await readdir(dir, { withFileTypes: true }).catch(() => {
    throw new NotFoundError(`No such directory: ${rel}`);
  });
  const out: WorkspaceEntry[] = [];
  for (const e of entries) {
    if (e.name === '.git') continue;
    const full = join(dir, e.name);
    const s = await stat(full).catch(() => null);
    const isDir = s ? s.isDirectory() : e.isDirectory();
    out.push({ name: e.name, path: relative(root, full).split('\\').join('/'), type: isDir ? 'dir' : 'file', size: isDir || !s ? 0 : s.size });
  }
  return out.sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'dir' ? -1 : 1));
}

/** Resolves a workspace file for reading; refuses anything that leads outside the workspace. */
export async function resolveWorkspaceFile(t: AgentRow, rel: string): Promise<string> {
  const ws = requireWorkspace(t);
  const file = await resolveInside(rel, [ws], ws);
  const s = await stat(file).catch(() => null);
  if (!s?.isFile()) throw new NotFoundError(`No such file in the workspace: ${rel}`);
  return file;
}
```

`root` is the workspace's real path, so relative paths stay correct even when `workspace_path` goes through a symlink (on macOS `/var` is a symlink to `/private/var`).

`packages/core/src/index.ts`: add `export { listWorkspace, resolveWorkspaceFile, threadDiff } from './workspaces/inspect';`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run packages/core/src/workspaces && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): thread diff and confined workspace browsing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Historical skill versions

**Files:**
- Modify: `packages/core/src/skills/store.ts`, `packages/core/src/runtime/runtime.ts`
- Test: `packages/core/src/skills/store.test.ts`

**Interfaces:**
- Produces:
  - `SkillStore.getVersion(scope: SkillScope, name: string, version: number, projectId?: string): SkillDetail | undefined`, where `dir` is the version's directory (history or live).
  - `Runtime.getSkillVersion(name: string, version: number, opts: { scope?: SkillScope; projectId?: string }): SkillDetail`. It throws `NotFoundError`.

- [ ] **Step 1: Write the failing test** — add inside `describe('SkillStore', …)` in `packages/core/src/skills/store.test.ts`:

```ts
  it('reads any past version with its instructions and files', async () => {
    store.save({ scope: 'global', ...basic, instructions: 'first', files: [{ path: 'notes.md', content: 'v1 notes' }] });
    store.save({ scope: 'global', ...basic, instructions: 'second' });
    const v1 = store.getVersion('global', 'weekly-report', 1)!;
    expect(v1).toMatchObject({ version: 1, instructions: 'first', description: basic.description });
    expect(v1.files.map((f) => f.path)).toContain('notes.md');
    expect(await readFile(store.filePath(v1, 'notes.md'), 'utf8')).toBe('v1 notes');
    expect(store.getVersion('global', 'weekly-report', 2)).toMatchObject({ version: 2, instructions: 'second' });
    expect(store.getVersion('global', 'weekly-report', 3)).toBeUndefined();
    expect(() => store.getVersion('global', '../x', 1)).toThrow();
  });
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run packages/core/src/skills/store.test.ts`
Expected: FAIL: `store.getVersion is not a function`.

- [ ] **Step 3: Implement** — in `SkillStore`, after `get()`:

```ts
  /** A skill as it was at `version` (history), or the live skill when `version` is current. */
  getVersion(scope: SkillScope, name: string, version: number, projectId?: string): SkillDetail | undefined {
    checkSkillName(name);
    const live = join(this.root(scope, projectId), name);
    if (version === this.currentVersion(scope, name, projectId) && existsSync(live)) return this.get(scope, name, projectId);
    const dir = join(this.historyDir(scope, name, projectId), String(version));
    if (!existsSync(dir)) return undefined;
    let parsed: ParsedSkillMd = { frontmatter: {}, instructions: '' };
    let description = '';
    let error: string | undefined;
    try {
      parsed = parseSkillMd(readFileSync(join(dir, SKILL_FILE), 'utf8'));
      description = checkDescription(parsed.frontmatter.description);
    } catch (e) {
      error = (e as Error).message;
    }
    return { name, scope, dir, version, description, ...(error ? { error } : {}), ...parsed, files: listFiles(dir) };
  }
```

In `Runtime`, after `skillHistory`:

```ts
  /** A skill as it was at `version`; the scope resolves like getSkill when omitted. */
  getSkillVersion(name: string, version: number, opts: { scope?: SkillScope; projectId?: string } = {}): SkillDetail {
    const scopes: SkillScope[] = opts.scope ? [opts.scope] : opts.projectId ? ['project', 'global'] : ['global'];
    for (const scope of scopes) {
      const v = this.skills.getVersion(scope, name, version, scope === 'project' ? opts.projectId : undefined);
      if (v) return v;
    }
    throw new NotFoundError(`Skill ${name} has no version ${version}`);
  }
```

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run packages/core/src/skills && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): read historical skill versions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Model endpoint resolution, Keychain and switchable adapter

**Files:**
- Modify: `packages/core/src/model/config.ts`, `packages/core/src/index.ts`
- Create: `packages/core/src/model/endpoint.ts`, `packages/core/src/model/switchable.ts`
- Test: `packages/core/src/model/endpoint.test.ts`, `packages/core/src/model/switchable.test.ts`

**Interfaces:**
- Produces:
  - `modelConfigFromEnv(env): ModelConfig | null` and `modelConfigFromFile(home): ModelConfig | null`. `loadModelConfig` keeps its behaviour, built on the two.
  - `type Keychain = { get(): string | null; set(secret: string): void }`
  - `macKeychain(exec?: KeychainExec): Keychain`, where `KeychainExec = (args: string[], input?: string) => string`
  - `resolveModelEndpoint(o: { env: NodeJS.ProcessEnv; home: string; baseUrl: string | null; keychain: Keychain | null }): { config: ModelConfig | null; source: 'env' | 'file' | 'keychain' | null }`
  - `testModelEndpoint(config: ModelConfig): Promise<ModelEndpointTestResult>`
  - `createSwitchableAdapter(config: ModelConfig | null, registry: ModelRegistry): SwitchableAdapter`, where `SwitchableAdapter = ModelAdapter & { replace(c: ModelConfig | null): void; readonly configured: boolean }`

- [ ] **Step 1: Write the failing tests**

`packages/core/src/model/endpoint.test.ts`:

```ts
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { startFakeModel } from '@desk/fake-model';
import { macKeychain, resolveModelEndpoint, testModelEndpoint, type Keychain } from './endpoint';

let home: string;
beforeEach(async () => {
  home = await mkdtemp(join(tmpdir(), 'desk-home-'));
});
afterEach(async () => rm(home, { recursive: true, force: true }));

const memKeychain = (secret: string | null = null): Keychain & { secret: string | null } => {
  const k = { secret, get: () => k.secret, set: (s: string) => void (k.secret = s) };
  return k;
};

describe('resolveModelEndpoint', () => {
  it('prefers env, then ~/.config/cliproxyapi.env, then config.json + Keychain', async () => {
    const keychain = memKeychain('kc-key');
    const base = { home, baseUrl: 'http://127.0.0.1:9000', keychain };
    expect(resolveModelEndpoint({ ...base, env: {} })).toEqual({ config: { baseURL: 'http://127.0.0.1:9000/v1', apiKey: 'kc-key' }, source: 'keychain' });
    await mkdir(join(home, '.config'));
    await writeFile(join(home, '.config', 'cliproxyapi.env'), 'CLIPROXY_BASE_URL=http://file:1\nCLIPROXY_API_KEY=file-key\n');
    expect(resolveModelEndpoint({ ...base, env: {} })).toMatchObject({ source: 'file', config: { apiKey: 'file-key' } });
    expect(resolveModelEndpoint({ ...base, env: { DESK_OPENAI_BASE_URL: 'http://env:1', DESK_OPENAI_API_KEY: 'env-key' } })).toMatchObject({ source: 'env', config: { apiKey: 'env-key' } });
  });

  it('is unconfigured without a base URL or a stored key', () => {
    expect(resolveModelEndpoint({ env: {}, home, baseUrl: null, keychain: memKeychain('k') })).toEqual({ config: null, source: null });
    expect(resolveModelEndpoint({ env: {}, home, baseUrl: 'http://x', keychain: memKeychain(null) })).toEqual({ config: null, source: null });
    expect(resolveModelEndpoint({ env: {}, home, baseUrl: 'http://x', keychain: null })).toEqual({ config: null, source: null });
  });
});

describe('macKeychain', () => {
  it('writes through `security -i` on stdin and reads with -w; the secret is never an argument', () => {
    const calls: Array<{ args: string[]; input?: string }> = [];
    const kc = macKeychain((args, input) => {
      calls.push({ args, ...(input !== undefined ? { input } : {}) });
      return args[0] === 'find-generic-password' ? 'stored-secret\n' : '';
    });
    kc.set('sk-secret');
    expect(kc.get()).toBe('stored-secret');
    expect(calls[0]!.args).toEqual(['-i']);
    expect(calls[0]!.input).toBe('add-generic-password -U -s "Desk model endpoint" -a "default" -w "sk-secret"\n');
    expect(calls[1]!.args).toEqual(['find-generic-password', '-s', 'Desk model endpoint', '-a', 'default', '-w']);
    for (const c of calls) expect(c.args.join(' ')).not.toContain('sk-secret');
  });

  it('returns null when the item is missing and refuses unsafe secrets', () => {
    const kc = macKeychain(() => {
      throw new Error('not found');
    });
    expect(kc.get()).toBeNull();
    expect(() => macKeychain(() => '').set('a"b')).toThrow(/unsafe/);
  });
});

describe('testModelEndpoint', () => {
  it('lists models on success and hides the key in errors', async () => {
    const fake = await startFakeModel();
    try {
      const ok = await testModelEndpoint({ baseURL: fake.url, apiKey: 'k' });
      expect(ok.ok).toBe(true);
      expect(Array.isArray(ok.models)).toBe(true);
    } finally {
      await fake.close();
    }
    const bad = await testModelEndpoint({ baseURL: 'http://127.0.0.1:9/v1', apiKey: 'sk-hidden-123' });
    expect(bad.ok).toBe(false);
    expect(JSON.stringify(bad)).not.toContain('sk-hidden-123');
  });
});
```

`packages/core/src/model/switchable.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { startFakeModel, text } from '@desk/fake-model';
import { FAKE_MODEL } from '../testing';
import { ModelError } from './errors';
import { ModelRegistry, SEED_MODELS } from './registry';
import { createSwitchableAdapter } from './switchable';

describe('createSwitchableAdapter', () => {
  it('reports proxy_down while unconfigured and works once configured', async () => {
    const registry = new ModelRegistry([...SEED_MODELS, FAKE_MODEL]);
    const adapter = createSwitchableAdapter(null, registry);
    expect(adapter.configured).toBe(false);
    expect(await adapter.health!()).toBe(false);
    const err = await adapter.complete({ model: FAKE_MODEL.id, messages: [], tools: [] }).catch((e) => e);
    expect(err).toBeInstanceOf(ModelError);
    expect(err.kind).toBe('proxy_down');

    const fake = await startFakeModel([text('hello')]);
    try {
      adapter.replace({ baseURL: fake.url, apiKey: 'k' });
      expect(adapter.configured).toBe(true);
      expect(await adapter.health!()).toBe(true);
      expect((await adapter.complete({ model: FAKE_MODEL.id, messages: [{ role: 'user', content: 'hi' }], tools: [] })).content).toBe('hello');
    } finally {
      await fake.close();
    }
  });
});
```

(Check `CompletionRequest` in `packages/core/src/model/types.ts`. If it has other required fields, add them to the three `complete` calls.)

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run packages/core/src/model`
Expected: FAIL: modules not found.

- [ ] **Step 3: Implement**

`packages/core/src/model/config.ts`: replace `loadModelConfig` with these three functions:

```ts
export function modelConfigFromEnv(env: NodeJS.ProcessEnv): ModelConfig | null {
  if (env.DESK_OPENAI_BASE_URL && env.DESK_OPENAI_API_KEY) return { baseURL: normalizeBaseURL(env.DESK_OPENAI_BASE_URL), apiKey: env.DESK_OPENAI_API_KEY };
  return null;
}

export function modelConfigFromFile(home: string): ModelConfig | null {
  const file = join(home, '.config', 'cliproxyapi.env');
  if (!existsSync(file)) return null;
  const vars = parseEnvFile(readFileSync(file, 'utf8'));
  return vars.CLIPROXY_BASE_URL && vars.CLIPROXY_API_KEY ? { baseURL: normalizeBaseURL(vars.CLIPROXY_BASE_URL), apiKey: vars.CLIPROXY_API_KEY } : null;
}

export function loadModelConfig(env: NodeJS.ProcessEnv = process.env, home: string = homedir()): ModelConfig {
  const config = modelConfigFromEnv(env) ?? modelConfigFromFile(home);
  if (config) return config;
  throw new Error(
    'No model access configured: set DESK_OPENAI_BASE_URL and DESK_OPENAI_API_KEY, or create ~/.config/cliproxyapi.env with CLIPROXY_BASE_URL and CLIPROXY_API_KEY',
  );
}
```

`packages/core/src/model/endpoint.ts`:

```ts
import { execFileSync } from 'node:child_process';
import OpenAI from 'openai';
import type { ModelEndpointTestResult } from '@desk/protocol';
import { modelConfigFromEnv, modelConfigFromFile, normalizeBaseURL, type ModelConfig } from './config';

export type Keychain = { get(): string | null; set(secret: string): void };
export type KeychainExec = (args: string[], input?: string) => string;

export const KEYCHAIN_SERVICE = 'Desk model endpoint';
export const KEYCHAIN_ACCOUNT = 'default';
const SAFE_SECRET = /^[\x21-\x7E]+$/;

const runSecurity: KeychainExec = (args, input) =>
  execFileSync('security', args, { encoding: 'utf8', input, stdio: [input === undefined ? 'ignore' : 'pipe', 'pipe', 'ignore'] });

/**
 * The macOS login Keychain via `/usr/bin/security`. The secret is written through `security -i` on stdin,
 * so it never appears in a process argument list.
 */
export function macKeychain(exec: KeychainExec = runSecurity): Keychain {
  return {
    get() {
      try {
        return exec(['find-generic-password', '-s', KEYCHAIN_SERVICE, '-a', KEYCHAIN_ACCOUNT, '-w']).trim() || null;
      } catch {
        return null;
      }
    },
    set(secret) {
      if (!SAFE_SECRET.test(secret) || /["'\\]/.test(secret)) throw new Error('Refusing to store an unsafe secret');
      exec(['-i'], `add-generic-password -U -s "${KEYCHAIN_SERVICE}" -a "${KEYCHAIN_ACCOUNT}" -w "${secret}"\n`);
    },
  };
}

export type EndpointSource = 'env' | 'file' | 'keychain';

/** Model access in priority order: DESK_OPENAI_* env, ~/.config/cliproxyapi.env, then config.json's base_url + the Keychain key. */
export function resolveModelEndpoint(o: {
  env: NodeJS.ProcessEnv;
  home: string;
  baseUrl: string | null;
  keychain: Keychain | null;
}): { config: ModelConfig | null; source: EndpointSource | null } {
  const env = modelConfigFromEnv(o.env);
  if (env) return { config: env, source: 'env' };
  const file = modelConfigFromFile(o.home);
  if (file) return { config: file, source: 'file' };
  const key = o.baseUrl && o.keychain ? o.keychain.get() : null;
  if (o.baseUrl && key) return { config: { baseURL: normalizeBaseURL(o.baseUrl), apiKey: key }, source: 'keychain' };
  return { config: null, source: null };
}

/** Checks an endpoint by listing its models. Error messages are scrubbed of the key. */
export async function testModelEndpoint(config: ModelConfig): Promise<ModelEndpointTestResult> {
  const client = new OpenAI({ baseURL: config.baseURL, apiKey: config.apiKey, maxRetries: 0, timeout: 5000 });
  try {
    const page = await client.models.list();
    return { ok: true, models: page.data.map((m) => m.id) };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { ok: false, error: message.split(config.apiKey).join('***') };
  }
}
```

`packages/core/src/model/switchable.ts`:

```ts
import { createModelAdapter } from './adapter';
import type { ModelConfig } from './config';
import { ModelError } from './errors';
import type { ModelRegistry } from './registry';
import type { ModelAdapter } from './types';

export type SwitchableAdapter = ModelAdapter & { replace(config: ModelConfig | null): void; readonly configured: boolean };

/**
 * A model adapter whose endpoint can be set or changed while the daemon runs.
 * Unconfigured, it behaves like an unreachable proxy: runs pause (proxy_down) and resume once configured.
 */
export function createSwitchableAdapter(config: ModelConfig | null, registry: ModelRegistry): SwitchableAdapter {
  let inner: ModelAdapter | null = config ? createModelAdapter(config, registry) : null;
  return {
    get configured() {
      return inner !== null;
    },
    replace(next) {
      inner = next ? createModelAdapter(next, registry) : null;
    },
    async health() {
      if (!inner) return false;
      return inner.health ? inner.health() : true;
    },
    complete(req, opts) {
      if (!inner) return Promise.reject(new ModelError('proxy_down', 'No model endpoint is configured'));
      return inner.complete(req, opts);
    },
  };
}
```

`packages/core/src/index.ts`:
- Extend the config export: `export { loadModelConfig, modelConfigFromEnv, modelConfigFromFile, normalizeBaseURL, parseEnvFile, type ModelConfig } from './model/config';`
- Add `export { KEYCHAIN_ACCOUNT, KEYCHAIN_SERVICE, macKeychain, resolveModelEndpoint, testModelEndpoint, type EndpointSource, type Keychain, type KeychainExec } from './model/endpoint';`
- Add `export { createSwitchableAdapter, type SwitchableAdapter } from './model/switchable';`

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run packages/core/src/model && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): model endpoint resolution with Keychain, connection test, switchable adapter

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: UI read routes (attention, overview, diff, files, skill versions, usage)

**Files:**
- Create: `apps/daemon/src/routes/ui.ts`, `apps/daemon/src/ui.test.ts`
- Modify: `apps/daemon/src/app.ts`, `packages/core/src/state/queries.ts`, `packages/core/src/index.ts`

**Interfaces:**
- Consumes: `listAttention`, `listOverview`, `threadDiff`, `listWorkspace`, `resolveWorkspaceFile`, `Runtime.dismissAttention`, `Runtime.getSkillVersion`.
- Produces the HTTP routes:
  - `GET /v1/attention?project_id=` → `AttentionResponse`
  - `POST /v1/attention/:id/dismiss` → `{ ok: true }`
  - `GET /v1/overview` → `ProjectSummary[]`
  - `GET /v1/threads/:id/diff` → `ThreadDiff`
  - `GET /v1/threads/:id/files?path=` → `WorkspaceEntry[]`
  - `GET /v1/threads/:id/files/raw/<path>` → bytes
  - `GET /v1/skills/:name/versions/:v` and `/v1/projects/:id/skills/:name/versions/:v` → skill detail
  - `…/versions/:v/files/<path>` → bytes
  - `GET /v1/usage?since=` → `UsageResponse`
- Query: `getUsageByProject(db, sinceDay?: string)`.

- [ ] **Step 1: Write the failing test** — `apps/daemon/src/ui.test.ts`:

```ts
import { execFileSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createHarness, newRuntime, type Harness } from '@desk/core/testing';
import { getDeskAgent } from '@desk/core';
import { createApp } from './app';

let h: Harness;
afterEach(async () => h?.cleanup());
const TOKEN = 't';

async function setup() {
  h = await createHarness();
  const runtime = newRuntime(h);
  const app = createApp({ runtime, store: h.store, models: h.models, token: TOKEN, version: '1.0.0' });
  const api = async (method: string, path: string, body?: unknown) => {
    const res = await app.request(`/v1${path}`, {
      method,
      headers: { authorization: `Bearer ${TOKEN}`, ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const json = (res.headers.get('content-type') ?? '').includes('json');
    return { status: res.status, body: (json ? await res.json() : await res.text()) as any };
  };
  const projectId = runtime.createProject({ name: 'Onboarding revamp', goal: 'g' });
  return { runtime, api, projectId, desk: getDeskAgent(h.store.db, projectId)! };
}

describe('attention routes', () => {
  it('lists, filters and dismisses', async () => {
    const { api, projectId, desk } = await setup();
    const [r] = h.store.append({ project_id: projectId, agent_id: desk.id, type: 'report', payload: { headline: 'H', progress: '', needs_you: ['Upload the 1099'], results: [] } });
    const list = await api('GET', '/attention');
    expect(list.status).toBe(200);
    expect(list.body.items).toEqual([expect.objectContaining({ id: `report:${r!.id}:0`, kind: 'needs_you' })]);
    expect(list.body.seq).toBeGreaterThanOrEqual(r!.id);
    expect((await api('GET', '/attention?project_id=nope')).body.items).toEqual([]);
    expect((await api('POST', `/attention/${encodeURIComponent(`report:${r!.id}:0`)}/dismiss`)).status).toBe(200);
    expect((await api('GET', '/attention')).body.items).toEqual([]);
    expect((await api('POST', '/attention/approval%3Ax/dismiss')).status).toBe(409);
    expect((await api('POST', '/attention/report%3A1%3A9/dismiss')).status).toBe(404);
  });
});

describe('overview', () => {
  it('returns one summary per project', async () => {
    const { api, projectId } = await setup();
    const r = await api('GET', '/overview');
    expect(r.body).toEqual([expect.objectContaining({ project: expect.objectContaining({ id: projectId }), threads: [], attention_count: 0 })]);
  });
});

describe('thread inspection', () => {
  it('serves the diff of a git thread and browses its workspace', async () => {
    const { runtime, api, projectId } = await setup();
    const repo = join(h.dir, 'repo');
    await mkdir(repo);
    const g = (...a: string[]) => execFileSync('git', ['-c', 'user.email=t@t', '-c', 'user.name=t', ...a], { cwd: repo, stdio: 'pipe' });
    g('init', '-q', '-b', 'main');
    await writeFile(join(repo, 'a.txt'), 'one\n');
    g('add', '.');
    g('commit', '-qm', 'init');
    const gitSourceId = await runtime.addSource(projectId, repo);
    const threadId = await runtime.services.spawnThread(getDeskAgent(h.store.db, projectId)!.id, { title: 'Checklist', brief: 'b', gitSourceId });
    const t = (await api('GET', `/threads/${threadId}`)).body;
    await writeFile(join(t.workspace_path, 'a.txt'), 'two\n');
    await mkdir(join(t.workspace_path, 'docs'));
    await writeFile(join(t.workspace_path, 'docs', 'x.md'), '# x');

    const diff = await api('GET', `/threads/${threadId}/diff`);
    expect(diff.status).toBe(200);
    expect(diff.body.files).toContainEqual({ path: 'a.txt', status: 'modified', additions: 1, deletions: 1 });
    const files = await api('GET', `/threads/${threadId}/files`);
    expect(files.body.map((f: any) => f.name)).toEqual(['docs', 'a.txt']);
    expect((await api('GET', `/threads/${threadId}/files?path=docs`)).body).toEqual([{ name: 'x.md', path: 'docs/x.md', type: 'file', size: 3 }]);
    const raw = await api('GET', `/threads/${threadId}/files/raw/docs/x.md`);
    expect(raw).toMatchObject({ status: 200, body: '# x' });
    expect((await api('GET', `/threads/${threadId}/files/raw/..%2F..%2Fetc%2Fpasswd`)).status).toBe(403);
    expect((await api('GET', `/threads/${threadId}/files/raw/nope.txt`)).status).toBe(404);
    await runtime.shutdown();
  });

  it('409s for a thread without git', async () => {
    const { runtime, api, projectId } = await setup();
    const t = runtime.createThread(projectId, { title: 'Plain', brief: 'b', workspacePath: join(h.dir, 'plain') });
    expect((await api('GET', `/threads/${t}/diff`)).status).toBe(409);
  });
});

describe('skill versions', () => {
  it('reads past versions and their files, globally and through a project', async () => {
    const { api, projectId } = await setup();
    const b64 = (s: string) => Buffer.from(s).toString('base64');
    await api('PUT', '/skills/email-sequence', { description: 'Emails', instructions: 'v1 steps', files: [{ path: 'notes.md', content_base64: b64('one') }] });
    await api('PUT', '/skills/email-sequence', { instructions: 'v2 steps', files: [{ path: 'notes.md', content_base64: b64('two') }], change_note: 'Refined' });
    const v1 = await api('GET', '/skills/email-sequence/versions/1');
    expect(v1.body).toMatchObject({ version: 1, instructions: 'v1 steps' });
    expect((await api('GET', '/skills/email-sequence/versions/1/files/notes.md')).body).toBe('one');
    expect((await api('GET', `/projects/${projectId}/skills/email-sequence/versions/2`)).body).toMatchObject({ version: 2, instructions: 'v2 steps' });
    expect((await api('GET', '/skills/email-sequence/versions/9')).status).toBe(404);
    expect((await api('GET', '/skills/email-sequence/versions/x')).status).toBe(400);
  });
});

describe('usage', () => {
  it('aggregates per project and model, optionally since a day', async () => {
    const { api, projectId, desk } = await setup();
    h.store.append({ project_id: projectId, agent_id: desk.id, type: 'usage', payload: { run_id: 'r', model: 'm1', prompt_tokens: 10, completion_tokens: 5, estimated: false } });
    h.store.append({ project_id: projectId, agent_id: desk.id, type: 'usage', payload: { run_id: 'r2', model: 'm1', prompt_tokens: 1, completion_tokens: 1, estimated: false } });
    const r = await api('GET', '/usage');
    expect(r.body).toEqual({ rows: [{ project_id: projectId, model: 'm1', prompt_tokens: 11, completion_tokens: 6 }], totals: { prompt_tokens: 11, completion_tokens: 6 } });
    expect((await api('GET', '/usage?since=2999-01-01')).body.rows).toEqual([]);
    expect((await api('GET', '/usage?since=yesterday')).status).toBe(400);
  });
});
```

`runtime.services.spawnThread(parentId, { title, brief, gitSourceId })` returns `Promise<string>` and creates the thread's git worktree on that source.

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/daemon/src/ui.test.ts`
Expected: FAIL with 404s (routes missing).

- [ ] **Step 3: Implement**

Append to `packages/core/src/state/queries.ts` (also add `gte` and `sum` to the drizzle import):

```ts
/** Token usage per project and model, optionally from `sinceDay` (YYYY-MM-DD) on. */
export function getUsageByProject(db: Db, sinceDay?: string): Array<{ project_id: string; model: string; prompt_tokens: number; completion_tokens: number }> {
  return db
    .select({
      project_id: usageTotals.project_id,
      model: usageTotals.model,
      prompt_tokens: sql<number>`sum(${usageTotals.prompt_tokens})`,
      completion_tokens: sql<number>`sum(${usageTotals.completion_tokens})`,
    })
    .from(usageTotals)
    .where(sinceDay ? gte(usageTotals.day, sinceDay) : undefined)
    .groupBy(usageTotals.project_id, usageTotals.model)
    .orderBy(asc(usageTotals.project_id), asc(usageTotals.model))
    .all()
    .map((r) => ({ ...r, prompt_tokens: Number(r.prompt_tokens), completion_tokens: Number(r.completion_tokens) }));
}
```

Export `getUsageByProject` from `packages/core/src/index.ts`. Check that `listAttention`, `listOverview`, `threadDiff`, `listWorkspace`, `resolveWorkspaceFile`, `ToolDenied` and `getAgent` are all exported there as well.

`apps/daemon/src/routes/ui.ts`:

```ts
import { readFile } from 'node:fs/promises';
import { Hono, type Context } from 'hono';
import {
  getAgent,
  getUsageByProject,
  lastSeq,
  listAttention,
  listOverview,
  listWorkspace,
  NotFoundError,
  resolveWorkspaceFile,
  threadDiff,
  ToolDenied,
  ValidationError,
  type AgentRow,
  type Db,
} from '@desk/core';
import type { AppDeps } from '../app';
import { HttpError } from '../http';
import { requireProject } from './projects';

function requireThread(db: Db, id: string): AgentRow {
  const t = getAgent(db, id);
  if (!t || t.role !== 'thread') throw new NotFoundError(`Unknown thread: ${id}`);
  return t;
}

/** Maps a path escape to 403, like the library route. */
async function confined<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn();
  } catch (err) {
    if (err instanceof ToolDenied) throw new HttpError(403, 'forbidden', 'Path is outside the workspace');
    throw err;
  }
}

/** The path after `marker` in the request URL, decoded. */
function tail(c: Context, marker: string): string {
  const p = c.req.path;
  return decodeURIComponent(p.slice(p.indexOf(marker) + marker.length));
}

function versionParam(c: Context): number {
  const v = Number(c.req.param('v'));
  if (!Number.isInteger(v) || v < 1) throw new ValidationError('Version must be a positive integer');
  return v;
}

const octet = { 'content-type': 'application/octet-stream' };

/** Read endpoints for the desktop app (design spec §4.1–4.2). */
export function uiRoutes({ runtime, store }: AppDeps): Hono {
  const r = new Hono();
  const db = store.db;

  r.get('/attention', (c) => {
    const projectId = c.req.query('project_id');
    const items = listAttention(db, projectId ? { projectId } : {});
    return c.json({ items, seq: lastSeq(db) });
  });

  r.post('/attention/:id/dismiss', (c) => {
    runtime.dismissAttention(c.req.param('id'));
    return c.json({ ok: true });
  });

  r.get('/overview', (c) => c.json(listOverview(db)));

  r.get('/threads/:id/diff', async (c) => c.json(await threadDiff(requireThread(db, c.req.param('id')))));

  r.get('/threads/:id/files', async (c) => {
    const t = requireThread(db, c.req.param('id'));
    return c.json(await confined(() => listWorkspace(t, c.req.query('path') ?? '')));
  });

  r.get('/threads/:id/files/raw/*', async (c) => {
    const t = requireThread(db, c.req.param('id'));
    const file = await confined(() => resolveWorkspaceFile(t, tail(c, '/files/raw/')));
    return c.body(new Uint8Array(await readFile(file)), 200, octet);
  });

  const skillVersions = (prefix: string, project: boolean) => {
    const opts = (c: Context) => (project ? { projectId: requireProject(db, c.req.param('id') ?? '').id } : { scope: 'global' as const });
    r.get(`${prefix}/:name/versions/:v`, (c) => c.json(runtime.getSkillVersion(c.req.param('name'), versionParam(c), opts(c))));
    r.get(`${prefix}/:name/versions/:v/files/*`, async (c) => {
      const skill = runtime.getSkillVersion(c.req.param('name'), versionParam(c), opts(c));
      const file = runtime.skills.filePath(skill, tail(c, `/versions/${c.req.param('v')}/files/`));
      return c.body(new Uint8Array(await readFile(file)), 200, octet);
    });
  };
  skillVersions('/skills', false);
  skillVersions('/projects/:id/skills', true);

  r.get('/usage', (c) => {
    const since = c.req.query('since');
    if (since !== undefined && !/^\d{4}-\d{2}-\d{2}/.test(since)) throw new ValidationError('since must be an ISO date (YYYY-MM-DD…)');
    const rows = getUsageByProject(db, since?.slice(0, 10));
    const totals = rows.reduce((t, x) => ({ prompt_tokens: t.prompt_tokens + x.prompt_tokens, completion_tokens: t.completion_tokens + x.completion_tokens }), { prompt_tokens: 0, completion_tokens: 0 });
    return c.json({ rows, totals });
  });

  return r;
}
```

Also add to `packages/core/src/state/queries.ts` (and export from `@desk/core`), then import `lastSeq` in `ui.ts`:

```ts
/** Highest event id overall (0 if none). */
export const lastSeq = (db: Db): number => db.select({ seq: max(events.id) }).from(events).get()?.seq ?? 0;
```

`apps/daemon/src/app.ts`: `import { uiRoutes } from './routes/ui';` and mount it **before** `skillRoutes`, so `…/versions/:v/files/*` is matched before the `…/:name/files/*` wildcard: `app.route('/v1', uiRoutes(deps));`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/daemon && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/daemon packages/core
git commit -m "feat(daemon): attention, overview, thread diff/files, skill versions and usage routes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Health, daemon config and model-endpoint routes (secret-safe)

**Files:**
- Create: `apps/daemon/src/config-file.ts`, `apps/daemon/src/routes/config.ts`, `apps/daemon/src/config.test.ts`
- Modify: `apps/daemon/src/app.ts`, `apps/daemon/src/daemon.ts`, `apps/daemon/src/main.ts`, `apps/daemon/src/paths.ts`, `apps/daemon/src/app.test.ts`, `packages/core/src/runtime/runtime.ts`

**Interfaces:**
- Consumes: `resolveModelEndpoint`, `testModelEndpoint`, `createSwitchableAdapter`, `macKeychain`, `Keychain` (Task 6).
- Produces:
  - `AppDeps` gains optional `health?: () => { proxy: 'up'|'down'|'unknown'; uptime_s: number }`, `config?: ConfigDeps` and `endpoint?: EndpointDeps`:

    ```ts
    export type ConfigDeps = { get(): DaemonConfig; patch(p: DaemonConfigPatch): DaemonConfig };
    export type EndpointDeps = {
      status(): ModelEndpointStatus;
      save(req: { base_url: string; api_key: string }): ModelEndpointStatus;
      test(req?: { base_url: string; api_key: string }): Promise<ModelEndpointTestResult>;
    };
    ```
  - `DaemonOptions` gains `env?: NodeJS.ProcessEnv`, `home?: string` and `keychain?: Keychain | null`.
  - `Runtime.proxyState: 'up' | 'down' | 'unknown'`.
  - `daemonPaths(dataDir).config` is `<dataDir>/config.json`.
  - `loadDaemonFile(path): { base_url: string | null; notifications: 'auto'|'off' }` and `saveDaemonFile(path, value)`.

- [ ] **Step 1: Write the failing test** — `apps/daemon/src/config.test.ts`:

```ts
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { startFakeModel, type FakeModelServer } from '@desk/fake-model';
import type { Keychain } from '@desk/core';
import { startDaemon, type RunningDaemon } from './daemon';
import { createLogger } from './logger';

let dir: string;
let daemon: RunningDaemon | undefined;
let fake: FakeModelServer | undefined;
afterEach(async () => {
  await daemon?.stop();
  daemon = undefined;
  await fake?.close();
  fake = undefined;
  await rm(dir, { recursive: true, force: true });
});

const SECRET = 'sk-desk-secret-0042';

async function start(keychain: Keychain | null) {
  dir = await mkdtemp(join(tmpdir(), 'desk-cfg-'));
  daemon = await startDaemon({ dataDir: dir, port: 0, env: {}, home: dir, keychain, sandboxAvailable: false, log: createLogger(join(dir, 'logs', 'deskd.log'), false) });
  const api = async (method: string, path: string, body?: unknown) => {
    const res = await fetch(`http://127.0.0.1:${daemon!.port}/v1${path}`, {
      method,
      headers: { authorization: `Bearer ${daemon!.token}`, ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const text = await res.text();
    return { status: res.status, text, body: text ? JSON.parse(text) : null };
  };
  return api;
}

function memKeychain(): Keychain & { secret: string | null } {
  const k = { secret: null as string | null, get: () => k.secret, set: (s: string) => void (k.secret = s) };
  return k;
}

describe('model endpoint setup', () => {
  it('starts unconfigured, stores the key in the Keychain, and never leaks it', async () => {
    const keychain = memKeychain();
    const api = await start(keychain);
    fake = await startFakeModel();
    expect((await api('GET', '/config/model-endpoint')).body).toEqual({ configured: false, source: null, base_url: null });

    const tested = await api('POST', '/config/model-endpoint/test', { base_url: fake.url, api_key: SECRET });
    expect(tested.body.ok).toBe(true);

    const saved = await api('PUT', '/config/model-endpoint', { base_url: fake.url, api_key: SECRET });
    expect(saved.body).toEqual({ configured: true, source: 'keychain', base_url: fake.url });
    expect(keychain.secret).toBe(SECRET);
    expect((await api('POST', '/config/model-endpoint/test')).body.ok).toBe(true);

    const everything = [
      saved.text,
      (await api('GET', '/config/model-endpoint')).text,
      await readFile(join(dir, 'daemon.json'), 'utf8'),
      await readFile(join(dir, 'config.json'), 'utf8'),
      await readFile(join(dir, 'logs', 'deskd.log'), 'utf8'),
      JSON.stringify(daemon!.store.list()),
    ].join('\n');
    expect(everything).not.toContain(SECRET);
  });

  it('answers 501 when no Keychain is available and 400 for unsafe keys', async () => {
    const api = await start(null);
    expect((await api('PUT', '/config/model-endpoint', { base_url: 'http://127.0.0.1:1/v1', api_key: 'sk-x' })).status).toBe(501);
    expect((await api('PUT', '/config/model-endpoint', { base_url: 'http://127.0.0.1:1/v1', api_key: 'a b' })).status).toBe(400);
  });
});

describe('daemon config and health', () => {
  it('patches notifications and reports proxy state and uptime', async () => {
    const api = await start(null);
    expect((await api('GET', '/config')).body).toEqual({ notifications: 'auto' });
    expect((await api('PATCH', '/config', { notifications: 'off' })).body).toEqual({ notifications: 'off' });
    expect(JSON.parse(await readFile(join(dir, 'config.json'), 'utf8'))).toMatchObject({ notifications: 'off' });
    const health = await (await fetch(`http://127.0.0.1:${daemon!.port}/v1/health`)).json();
    expect(health).toMatchObject({ protocol_version: 1, proxy: 'up' });
    expect(health.uptime_s).toBeGreaterThanOrEqual(0);
  });
});
```

In `apps/daemon/src/app.test.ts`, change the health assertion from `toEqual({ version: '1.0.0', protocol_version: 1 })` to `toMatchObject({ version: '1.0.0', protocol_version: 1 })`.

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/daemon/src/config.test.ts`
Expected: FAIL: `startDaemon` rejects the new options at type level, or the routes 404.

- [ ] **Step 3: Implement**

`apps/daemon/src/paths.ts`: add `config: join(dataDir, 'config.json'),` to `daemonPaths`.

`apps/daemon/src/config-file.ts`:

```ts
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { z } from 'zod';

const DaemonFile = z.object({
  base_url: z.string().nullable().default(null),
  notifications: z.enum(['auto', 'off']).default('auto'),
});
export type DaemonFile = z.infer<typeof DaemonFile>;

/** `<dataDir>/config.json`: daemon settings that are not secrets (the API key lives in the Keychain). */
export function loadDaemonFile(path: string): DaemonFile {
  if (!existsSync(path)) return DaemonFile.parse({});
  try {
    return DaemonFile.parse(JSON.parse(readFileSync(path, 'utf8')));
  } catch {
    return DaemonFile.parse({});
  }
}

export function saveDaemonFile(path: string, value: DaemonFile): void {
  writeFileSync(path, JSON.stringify(value, null, 2), { mode: 0o600 });
}
```

`packages/core/src/runtime/runtime.ts`: add this getter:

```ts
  /** Model proxy reachability as last observed ('unknown' without a health probe). */
  get proxyState(): 'up' | 'down' | 'unknown' {
    return this.proxy ? (this.proxy.isDown ? 'down' : 'up') : 'unknown';
  }
```

`apps/daemon/src/routes/config.ts`:

```ts
import { Hono } from 'hono';
import { DaemonConfigPatch, ModelEndpointPutRequest, ModelEndpointTestRequest } from '@desk/protocol';
import type { AppDeps } from '../app';
import { body, HttpError } from '../http';

/** Daemon settings and model-endpoint setup. Bodies here are never logged; the key is never echoed. */
export function configRoutes({ config, endpoint }: AppDeps): Hono {
  const r = new Hono();

  r.get('/config', (c) => {
    if (!config) throw new HttpError(501, 'unsupported', 'Daemon config is not available');
    return c.json(config.get());
  });

  r.patch('/config', async (c) => {
    if (!config) throw new HttpError(501, 'unsupported', 'Daemon config is not available');
    return c.json(config.patch(await body(c, DaemonConfigPatch)));
  });

  r.get('/config/model-endpoint', (c) => {
    if (!endpoint) throw new HttpError(501, 'unsupported', 'Model endpoint setup is not available');
    return c.json(endpoint.status());
  });

  r.put('/config/model-endpoint', async (c) => {
    if (!endpoint) throw new HttpError(501, 'unsupported', 'Model endpoint setup is not available');
    return c.json(endpoint.save(await body(c, ModelEndpointPutRequest)));
  });

  r.post('/config/model-endpoint/test', async (c) => {
    if (!endpoint) throw new HttpError(501, 'unsupported', 'Model endpoint setup is not available');
    const raw = await c.req.text();
    const req = raw.trim() ? ModelEndpointTestRequest.parse(JSON.parse(raw)) : undefined;
    return c.json(await endpoint.test(req));
  });

  return r;
}
```

`JSON.parse` of bad text throws a `SyntaxError`, which would come back as a 500. Wrap it: `let parsed: unknown; try { parsed = JSON.parse(raw); } catch { throw new ValidationError('Request body must be JSON'); }` (import `ValidationError` from `@desk/core`).

`apps/daemon/src/app.ts`:
- Import `ConfigDeps`/`EndpointDeps` types as defined in Interfaces (declare and export them in `app.ts`), plus `DaemonConfig`, `DaemonConfigPatch`, `ModelEndpointStatus` and `ModelEndpointTestResult` from `@desk/protocol`, and `configRoutes`.
- Extend `AppDeps` with `health?`, `config?` and `endpoint?`.
- Change the health route to `app.get('/v1/health', (c) => c.json({ version: deps.version, protocol_version: PROTOCOL_VERSION, ...(deps.health?.() ?? {}) }));`
- Mount `app.route('/v1', configRoutes(deps));`.

`apps/daemon/src/daemon.ts`:
- Extend `DaemonOptions` with:

  ```ts
    /** Environment and home used to resolve model access (default process.env / os.homedir()). */
    env?: NodeJS.ProcessEnv;
    home?: string;
    /** Where PUT /config/model-endpoint stores the key; null disables it (default null; main.ts passes the macOS Keychain). */
    keychain?: Keychain | null;
  ```
- Replace the adapter creation with:

  ```ts
      let file = loadDaemonFile(paths.config);
      const keychain = o.keychain ?? null;
      const resolveEndpoint = () =>
        o.modelConfig
          ? { config: o.modelConfig, source: 'env' as const }
          : resolveModelEndpoint({ env: o.env ?? process.env, home: o.home ?? homedir(), baseUrl: file.base_url, keychain });
      let endpointState = resolveEndpoint();
      if (!endpointState.config) log.info('no model endpoint configured yet; agents stay paused until one is set');
      const adapter = createSwitchableAdapter(endpointState.config, models);
  ```
- Pass `adapter` to `Runtime` as before, and record `const startedAt = Date.now();`.
- Build the deps for `createApp`:

  ```ts
      const endpointStatus = () => ({ configured: endpointState.config !== null, source: endpointState.source, base_url: endpointState.config?.baseURL ?? null });
      const app = createApp({
        runtime, store, models, token, version,
        saveModels: (m) => saveModels(paths.models, m),
        health: () => ({ proxy: runtime.proxyState, uptime_s: Math.floor((Date.now() - startedAt) / 1000) }),
        config: {
          get: () => ({ notifications: file.notifications }),
          patch: (p) => {
            file = { ...file, ...(p.notifications ? { notifications: p.notifications } : {}) };
            saveDaemonFile(paths.config, file);
            return { notifications: file.notifications };
          },
        },
        endpoint: {
          status: endpointStatus,
          save: ({ base_url, api_key }) => {
            if (!keychain) throw new HttpError(501, 'unsupported', 'Storing the key needs the macOS Keychain; use ~/.config/cliproxyapi.env instead');
            keychain.set(api_key);
            file = { ...file, base_url };
            saveDaemonFile(paths.config, file);
            endpointState = resolveEndpoint();
            adapter.replace(endpointState.config);
            log.info(`model endpoint updated (source: ${endpointState.source ?? 'none'})`);
            return endpointStatus();
          },
          test: async (req) => {
            const cfg = req ? { baseURL: normalizeBaseURL(req.base_url), apiKey: req.api_key } : endpointState.config;
            return cfg ? testModelEndpoint(cfg) : { ok: false, error: 'No model endpoint is configured' };
          },
        },
      });
  ```

  Import `homedir` from `node:os`; `createSwitchableAdapter`, `resolveModelEndpoint`, `testModelEndpoint`, `normalizeBaseURL` and `type Keychain` from `@desk/core`; `loadDaemonFile`/`saveDaemonFile` from `./config-file`; `HttpError` from `./http`. Remove the now-unused `createModelAdapter` and `loadModelConfig` imports.

`fake.url` ends in `/v1`, so `base_url` in the status echoes whatever `normalizeBaseURL` produced. The test expects `base_url: fake.url`, which is correct because `normalizeBaseURL` keeps an existing `/v1`.

`apps/daemon/src/main.ts`: pass the Keychain on macOS: `import { macKeychain } from '@desk/core';` and add `...(process.platform === 'darwin' ? { keychain: macKeychain() } : {})` to the `startDaemon` options.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/daemon && pnpm typecheck`
Expected: PASS. Existing daemon tests that relied on `loadModelConfig` throwing for missing config now get an unconfigured daemon instead. If one asserted that start-up fails, update it to assert that `GET /v1/config/model-endpoint` reports `configured: false`.

- [ ] **Step 5: Commit**

```bash
git add apps/daemon packages/core
git commit -m "feat(daemon): health proxy/uptime, daemon config and Keychain-backed model endpoint setup

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Stream hello and the daemon's notifier

**Files:**
- Create: `apps/daemon/src/notifier.ts`, `apps/daemon/src/notifier.test.ts`
- Modify: `apps/daemon/src/stream.ts`, `apps/daemon/src/server.ts`, `apps/daemon/src/daemon.ts`, `apps/daemon/src/main.ts`, `apps/daemon/src/stream.test.ts`

**Interfaces:**
- Produces:
  - `attachStream(server, store, token): { close(): void; notifyingClients(): number }`
  - `RunningServer.notifyingClients(): number`
  - `notificationFor(ev: StoredEvent, db: Db): { title: string; body: string } | null`
  - `startNotifier(o: { store: EventStore; enabled(): boolean; suppressed(): boolean; post(n: Notification): void; onError(err: unknown): void }): () => void`
  - `macNotify(n: Notification): void`
  - `DaemonOptions.notify?: (n: Notification) => void`

- [ ] **Step 1: Write the failing tests**

`apps/daemon/src/notifier.test.ts`:

```ts
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createHarness, newRuntime, type Harness } from '@desk/core/testing';
import { getDeskAgent } from '@desk/core';
import { startNotifier, type Notification } from './notifier';

let h: Harness;
afterEach(async () => h?.cleanup());

describe('notifier', () => {
  it('posts for approvals, questions, reports that need you, stalls and failures, only when enabled and not suppressed', async () => {
    h = await createHarness();
    const runtime = newRuntime(h);
    const p = runtime.createProject({ name: 'Onboarding revamp', goal: 'g' });
    const desk = getDeskAgent(h.store.db, p)!;
    const t = runtime.createThread(p, { title: 'Signup checklist', brief: 'b', workspacePath: join(h.dir, 't') });
    const posted: Notification[] = [];
    let enabled = true;
    let suppressed = false;
    const stop = startNotifier({ store: h.store, enabled: () => enabled, suppressed: () => suppressed, post: (n) => posted.push(n), onError: (e) => { throw e; } });
    const a = h.store.append.bind(h.store);
    const approval = (id: string, delegate: boolean) =>
      a({ project_id: p, agent_id: t, type: 'approval.requested', payload: { approval_id: id, run_id: 'r', tool_call_id: id, tool: 'bash', arguments: '{}', reason: 'r', delegate_to_desk: delegate } });

    approval('a1', false);
    approval('a2', true);
    a({ project_id: p, agent_id: desk.id, type: 'question.asked', payload: { question: 'Data source first?' } });
    a({ project_id: p, agent_id: desk.id, type: 'report', payload: { headline: 'Research is in', progress: '', needs_you: ['x', 'y'], results: [] } });
    a({ project_id: p, agent_id: desk.id, type: 'report', payload: { headline: 'Quiet', progress: '', needs_you: [], results: [] } });
    a({ project_id: p, agent_id: desk.id, type: 'message.agent', payload: { from_agent_id: t, from_label: 'l', kind: 'stalled', text: 'No activity' } });
    a({ project_id: p, agent_id: t, type: 'agent.status_changed', payload: { status: 'failed', reason: 'Model error' } });
    expect(posted).toEqual([
      { title: 'Desk · Onboarding revamp', body: 'Signup checklist wants to run bash' },
      { title: 'Desk · Onboarding revamp', body: 'Desk asks: Data source first?' },
      { title: 'Desk · Onboarding revamp', body: 'Research is in (2 need you)' },
      { title: 'Desk · Onboarding revamp', body: 'Signup checklist has stalled' },
      { title: 'Desk · Onboarding revamp', body: 'Signup checklist failed: Model error' },
    ]);

    suppressed = true;
    approval('a3', false);
    suppressed = false;
    enabled = false;
    approval('a4', false);
    expect(posted).toHaveLength(5);
    stop();
  });
});
```

Add to `apps/daemon/src/stream.test.ts` (inside its `describe`, reusing its `setup`/`connect` helpers):

```ts
  it('counts desktop clients that say hello with notifications', async () => {
    await setup();
    const c = await connect();
    c.ws.send(JSON.stringify({ hello: { client: 'desktop', notifications: true } }));
    await new Promise((r) => setTimeout(r, 50));
    expect(server!.notifyingClients()).toBe(1);
    c.ws.close();
    await new Promise((r) => setTimeout(r, 50));
    expect(server!.notifyingClients()).toBe(0);
  });
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/daemon/src/notifier.test.ts apps/daemon/src/stream.test.ts`
Expected: FAIL: module missing, and `notifyingClients` is not a function.

- [ ] **Step 3: Implement**

`apps/daemon/src/stream.ts`:
- Remove the `void hello;` placeholder.
- Have `serve()` take a `clients: Set<WebSocket>` argument. In the hello branch, add `if (parsed.hello.notifications) clients.add(ws); else clients.delete(ws);`. On `close`, call `clients.delete(ws)`.
- Change `attachStream`'s return value:

```ts
export function attachStream(server: Server, store: EventStore, token: string): { close(): void; notifyingClients(): number } {
  const wss = new WebSocketServer({ noServer: true });
  const notifying = new Set<WebSocket>();
  // …upgrade handler unchanged, calling serve(ws, store, notifying)…
  return {
    close: () => {
      for (const client of wss.clients) client.terminate();
      wss.close();
    },
    notifyingClients: () => notifying.size,
  };
}
```

`apps/daemon/src/server.ts`:
- Add `notifyingClients(): number` to `RunningServer`.
- Use `const stream = attachStream(…)`, then call `stream.close()` in `close` and return `notifyingClients: () => stream.notifyingClients()`.

`apps/daemon/src/notifier.ts`:

```ts
import { execFile } from 'node:child_process';
import type { StoredEvent } from '@desk/protocol';
import { getAgent, getProject, type Db, type EventStore } from '@desk/core';

export type Notification = { title: string; body: string };

const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** What (if anything) to tell the user about an event when no desktop client is showing notifications. */
export function notificationFor(ev: StoredEvent, db: Db): Notification | null {
  const project = getProject(db, ev.project_id);
  if (!project) return null;
  const title = `Desk · ${project.name}`;
  const name = (id: string | null) => {
    const a = id ? getAgent(db, id) : undefined;
    return a?.role === 'desk' ? 'Desk' : (a?.title ?? 'A thread');
  };
  switch (ev.type) {
    case 'approval.requested':
      return ev.payload.delegate_to_desk ? null : { title, body: `${name(ev.agent_id)} wants to run ${ev.payload.tool}` };
    case 'question.asked':
      return { title, body: truncate(`Desk asks: ${ev.payload.question}`, 200) };
    case 'report':
      return ev.payload.needs_you.length ? { title, body: truncate(`${ev.payload.headline} (${ev.payload.needs_you.length} need you)`, 200) } : null;
    case 'message.agent':
      return ev.payload.kind === 'stalled' ? { title, body: `${name(ev.payload.from_agent_id)} has stalled` } : null;
    case 'agent.status_changed': {
      if (ev.payload.status !== 'failed') return null;
      const a = ev.agent_id ? getAgent(db, ev.agent_id) : undefined;
      if (a?.role !== 'thread') return null;
      return { title, body: truncate(`${a.title ?? 'A thread'} failed${ev.payload.reason ? `: ${ev.payload.reason}` : ''}`, 200) };
    }
    default:
      return null;
  }
}

/** Posts notifications for new events while enabled and while no desktop client handles them. */
export function startNotifier(o: {
  store: EventStore;
  enabled(): boolean;
  suppressed(): boolean;
  post(n: Notification): void;
  onError(err: unknown): void;
}): () => void {
  return o.store.subscribe((item) => {
    if (item.kind !== 'event' || !o.enabled() || o.suppressed()) return;
    try {
      const n = notificationFor(item.event, o.store.db);
      if (n) o.post(n);
    } catch (err) {
      o.onError(err);
    }
  });
}

const SCRIPT = 'on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run';

/** macOS notification via osascript; text is passed as arguments, never interpolated into the script. */
export function macNotify(n: Notification): void {
  execFile('osascript', ['-e', SCRIPT, truncate(n.title, 100), truncate(n.body, 240)], () => {});
}
```

(The test's `onError` rethrows. That's fine, because `notificationFor` doesn't throw on valid events.)

`apps/daemon/src/daemon.ts`:
- Add `notify?: (n: Notification) => void;` to `DaemonOptions`, documented as "Posts notifications (main.ts passes macNotify on macOS; tests leave it unset)".
- After `startServer`:

```ts
    const stopNotifier = o.notify
      ? startNotifier({
          store,
          enabled: () => file.notifications === 'auto',
          suppressed: () => server.notifyingClients() > 0,
          post: o.notify,
          onError: (err) => log.error('notifier failed', err),
        })
      : () => {};
```

- Call `stopNotifier()` first thing in `stop()`.

`apps/daemon/src/main.ts`: `import { macNotify } from './notifier';` and add `...(process.platform === 'darwin' ? { notify: macNotify } : {})`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/daemon && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/daemon
git commit -m "feat(daemon): notifier for approvals/questions/needs-you/stalls/failures, silenced by desktop hello

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Bundled daemon

**Files:**
- Create: `apps/daemon/scripts/bundle.mjs`, `apps/daemon/src/bundle.test.ts`
- Modify: `packages/core/src/db/open.ts`, `apps/daemon/src/daemon.ts`, `apps/daemon/src/main.ts`, `apps/daemon/package.json`, `.gitignore`

**Interfaces:**
- Produces:
  - `openDb(file?, opts?: { migrationsFolder?: string })`
  - `DaemonOptions.migrationsDir?: string`
  - `pnpm --filter @desk/daemon bundle` → `apps/daemon/dist/{deskd.mjs, drizzle/, node_modules/better-sqlite3/{package.json, lib/, prebuilds/}}`
  - The bundle runs with `node dist/deskd.mjs --data-dir <dir> [--port <n>]`, or under Electron with `ELECTRON_RUN_AS_NODE=1`.

- [ ] **Step 1: Write the failing test** — `apps/daemon/src/bundle.test.ts`:

```ts
import { execFileSync, spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const appDir = fileURLToPath(new URL('..', import.meta.url));

describe('bundled daemon', () => {
  it('builds a self-contained deskd.mjs that starts and serves health', async () => {
    execFileSync(process.execPath, ['scripts/bundle.mjs'], { cwd: appDir, stdio: 'pipe' });
    const dist = join(appDir, 'dist');
    expect(existsSync(join(dist, 'deskd.mjs'))).toBe(true);
    expect(existsSync(join(dist, 'drizzle'))).toBe(true);
    expect(existsSync(join(dist, 'node_modules', 'better-sqlite3', 'prebuilds'))).toBe(true);

    const dataDir = await mkdtemp(join(tmpdir(), 'desk-bundle-'));
    const child = spawn(process.execPath, [join(dist, 'deskd.mjs'), '--data-dir', dataDir, '--port', '0'], {
      cwd: tmpdir(),
      env: { PATH: process.env.PATH ?? '', HOME: dataDir, DESK_OPENAI_BASE_URL: 'http://127.0.0.1:9/v1', DESK_OPENAI_API_KEY: 'x' },
      stdio: 'ignore',
    });
    try {
      const infoFile = join(dataDir, 'daemon.json');
      for (let i = 0; i < 100 && !existsSync(infoFile); i++) await new Promise((r) => setTimeout(r, 100));
      const info = JSON.parse(readFileSync(infoFile, 'utf8')) as { port: number };
      const health = await (await fetch(`http://127.0.0.1:${info.port}/v1/health`)).json();
      expect(health).toMatchObject({ protocol_version: 1 });
    } finally {
      child.kill('SIGTERM');
      await new Promise((r) => child.once('exit', r));
      await rm(dataDir, { recursive: true, force: true });
    }
  }, 60_000);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/daemon/src/bundle.test.ts`
Expected: FAIL: `scripts/bundle.mjs` not found.

- [ ] **Step 3: Implement**

`packages/core/src/db/open.ts`: change the signature to `export function openDb(file = ':memory:', opts: { migrationsFolder?: string } = {})` and use `migrate(db, { migrationsFolder: opts.migrationsFolder ?? MIGRATIONS });`.

`apps/daemon/src/daemon.ts`: add `migrationsDir?: string;` to `DaemonOptions` and call `openDb(paths.db, o.migrationsDir ? { migrationsFolder: o.migrationsDir } : {})`.

`apps/daemon/src/main.ts`: add `import { fileURLToPath } from 'node:url';` and pass

```ts
...(process.env.DESK_BUNDLED === '1' ? { migrationsDir: fileURLToPath(new URL('./drizzle', import.meta.url)) } : {}),
```

(esbuild replaces `process.env.DESK_BUNDLED` with `"1"` through `define`.)

`apps/daemon/scripts/bundle.mjs`:

```js
// Bundles deskd into dist/deskd.mjs with its migrations and the better-sqlite3 prebuilds (N-API: runs under Node and Electron).
import { cpSync, mkdirSync, realpathSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const app = fileURLToPath(new URL('..', import.meta.url));
const dist = join(app, 'dist');
rmSync(dist, { recursive: true, force: true });
mkdirSync(dist, { recursive: true });

await build({
  entryPoints: [join(app, 'src', 'main.ts')],
  outfile: join(dist, 'deskd.mjs'),
  bundle: true,
  platform: 'node',
  format: 'esm',
  target: 'node22',
  external: ['better-sqlite3', 'bufferutil', 'utf-8-validate'],
  define: { 'process.env.DESK_BUNDLED': '"1"' },
  banner: { js: "import { createRequire as __cr } from 'node:module'; const require = __cr(import.meta.url);" },
  logLevel: 'warning',
});

const require = createRequire(join(app, '..', '..', 'packages', 'core', 'package.json'));
const sqlite = dirname(realpathSync(require.resolve('better-sqlite3/package.json')));
const target = join(dist, 'node_modules', 'better-sqlite3');
for (const part of ['package.json', 'lib', 'prebuilds']) cpSync(join(sqlite, part), join(target, part), { recursive: true, dereference: true });
cpSync(join(app, '..', '..', 'packages', 'core', 'drizzle'), join(dist, 'drizzle'), { recursive: true });
console.log(`bundled ${join(dist, 'deskd.mjs')}`);
```

`apps/daemon/package.json`: add `"bundle": "node scripts/bundle.mjs"` to `scripts` and `"esbuild": "^0.28.2"` to `devDependencies`, then run `pnpm install`. (Just in case: the version is already in the store, so this needs no network.) Add `apps/daemon/dist/` to the root `.gitignore`.

If `lib/` requires `bindings` or other runtime deps, check them with `grep -n "require(" node_modules/.pnpm/better-sqlite3@13.0.3/node_modules/better-sqlite3/lib/*.js`, copy each one the same way, and add it to the test's existence checks.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/daemon/src/bundle.test.ts && pnpm test && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/daemon packages/core .gitignore pnpm-lock.yaml
git commit -m "feat(daemon): esbuild bundle with migrations and better-sqlite3 prebuilds

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Documentation

**Files:**
- Modify: `docs/api.md`, `CLAUDE.md`, `README.md`, `docs/superpowers/specs/2026-09-24-desk-desktop-app-design.md`

- [ ] **Step 1: Update `docs/api.md`**
  - Add rows to the tables for every Plan 7 route (Tasks 7 and 8), with the same bodies and status codes as the tests.
  - Add a "UI" section describing attention item ids and kinds, dismissal rules, the overview shape, the diff and file routes (409 non-git/archived, 403 escape), and skill version routes.
  - Add a "Configuration" section covering `/config`, `/config/model-endpoint` (write-only key, resolution order env → file → Keychain, 501 without a Keychain) and the `hello` stream message.
  - Add `attention.dismissed` to the event table.
  - Add `proxy` and `uptime_s` to health.

- [ ] **Step 2: Update `CLAUDE.md`**
  - Add `apps/daemon/scripts/bundle.mjs` and `pnpm --filter @desk/daemon bundle` to Commands.
  - Under Secrets, add: "`PUT /v1/config/model-endpoint` stores the key in the macOS Keychain via `security -i` (stdin), never argv."
  - Under Layout, list `state/attention.ts`, `state/overview.ts`, `workspaces/inspect.ts`, `model/endpoint.ts`, `model/switchable.ts` and `apps/daemon/src/notifier.ts`.

- [ ] **Step 3: Update `README.md`.** Add a short note that deskd starts without model access and waits (agents paused) until an endpoint is configured through the API, the env var or the env file.

- [ ] **Step 4: Update the spec.** In §4.2 of the design spec, drop `cached_tokens` from the usage rows (the `usage_totals` table doesn't track it) and note that rows are per project and model. In §4.5, change the LaunchAgent label to the existing `dev.desk.deskd`, which the CLI already uses.

- [ ] **Step 5: Verify and commit**

Run: `pnpm test && pnpm typecheck`
Expected: PASS.

```bash
git add docs CLAUDE.md README.md
git commit -m "docs: Plan 7 API additions, configuration and bundling

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage.** Each part of spec §4 maps to a task:

  | Spec | Task |
  |---|---|
  | §4.1 attention | Tasks 2 and 7 |
  | §4.2 overview | Tasks 3 and 7 |
  | §4.2 diff and files | Tasks 4 and 7 |
  | §4.2 skill versions | Tasks 5 and 7 |
  | §4.2 usage | Task 7 |
  | §4.2 health | Task 8 |
  | §4.3 model endpoint | Tasks 6 and 8 |
  | §4.4 notifier and hello | Tasks 1 and 9 |
  | §4.5 bundle | Task 10 |
  | Docs | Task 11 |

  The LaunchAgent install and version drift in §4.5 belong on the app side and are covered in Plan 9.
- **Deviations recorded:**
  - Usage has no `cached_tokens`.
  - The LaunchAgent label stays `dev.desk.deskd`.
  - The daemon starts unconfigured instead of exiting when no model config exists, as onboarding needs.
