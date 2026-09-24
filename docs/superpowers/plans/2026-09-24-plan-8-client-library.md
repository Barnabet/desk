# Plan 8 · `@desk/client` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A shared, environment-neutral client library. It contains a typed REST client for every deskd endpoint, a reconnecting event stream, and pure reducers that turn events into UI state (project, chat, transcript, line-diagram timeline, system), plus attention and skill-graph helpers. The CLI switches to it.

**Architecture:** `packages/client` depends only on `@desk/protocol` at runtime and uses the global `fetch` and `WebSocket` (Node 22, Electron and browsers all have them). Row types are imported as *types* from `@desk/core` (erased at runtime). Anything that needs the filesystem (finding `daemon.json`) lives in a separate `@desk/client/node` entry, so the root entry stays safe to bundle for a browser renderer.

**Tech Stack:** TypeScript (strict, `noUncheckedIndexedAccess`), zod 4, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-24-desk-desktop-app-design.md` §2, §3, §6, §9.

## Global Constraints

- `pnpm test` and `pnpm typecheck` must pass before every commit.
- `packages/client/src/**`, except `node.ts`, must not import `node:*` modules or runtime values from `@desk/core` (`import type` only).
- Reducers are pure: `(state, event) → new state`. They never mutate their input, and they ignore events with `id <= state.lastSeq`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work on the `desktop-app` branch.

## File map

| File | Responsibility |
|---|---|
| `packages/protocol/src/format.ts` | `summarizeToolArgs`, `clip`, shared by core and client |
| `packages/client/package.json` | package `@desk/client` with the `.` and `./node` entries |
| `packages/client/src/types.ts` | response types (row types re-exported from core as types) |
| `packages/client/src/errors.ts` | `ApiError`, `DaemonUnavailable`, `DaemonNotRunning`, `ProtocolMismatch` |
| `packages/client/src/client.ts` | `DeskClient` |
| `packages/client/src/stream.ts` | `DeskStream` |
| `packages/client/src/node.ts` | `defaultDataDir`, `readDaemonInfo`, `clientFromDataDir`, `checkDaemon` |
| `packages/client/src/state/project.ts` | `projectFromOverview`, `reduceProject` |
| `packages/client/src/state/chat.ts` | `emptyChat`, `reduceChat`, `applyChatDelta` |
| `packages/client/src/state/transcript.ts` | `emptyTranscript`, `reduceTranscript`, `applyTranscriptDelta` |
| `packages/client/src/state/timeline.ts` | `emptyTimeline`, `reduceTimeline` |
| `packages/client/src/state/system.ts` | `systemFromHealth`, `reduceSystem` |
| `packages/client/src/state/attention.ts` | `groupAttention`, `affectsAttention`, `affectsOverview` |
| `packages/client/src/state/skills.ts` | `buildSkillGraph` |
| `packages/client/src/index.ts` | root exports |
| `packages/client/src/testing.ts` | `ev()` event builder for tests (exported as `@desk/client/testing`) |
| `apps/cli/src/client.ts` | re-exports from `@desk/client` |

---

### Task 1: Package scaffold, shared formatting, errors and types

**Files:**
- Create: `packages/protocol/src/format.ts`, `packages/protocol/src/format.test.ts`, `packages/client/package.json`, `packages/client/src/{index,types,errors,testing}.ts`, `packages/client/src/errors.test.ts`
- Modify: `packages/protocol/src/index.ts`, `packages/core/src/state/overview.ts`, `packages/core/src/state/overview.test.ts`, `packages/core/src/index.ts`

**Interfaces:**
- Produces:
  - `@desk/protocol`: `clip(s: string, max: number): string` and `summarizeToolArgs(args: string, max = 80): string`, with the same behaviour as core's current function. Core now re-exports them from protocol.
  - `@desk/client`: the error classes and all types in `types.ts`.
  - `@desk/client/testing`: `ev<T extends EventType>(id: number, type: T, payload: EventOf<T>['payload'], opts?: { agent?: string | null; project?: string; ts?: string }): EventOf<T>`

- [ ] **Step 1: Write the failing tests**

`packages/protocol/src/format.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { clip, summarizeToolArgs } from '@desk/protocol';

describe('format', () => {
  it('clips to a single line with an ellipsis', () => {
    expect(clip('a\n  b', 10)).toBe('a b');
    expect(clip('x'.repeat(20), 10)).toBe('xxxxxxxxx…');
  });

  it('summarises tool arguments by their first string value', () => {
    expect(summarizeToolArgs('{"path":"emails/04.md","content":"x"}')).toBe('emails/04.md');
    expect(summarizeToolArgs('{"n":1}')).toBe('');
    expect(summarizeToolArgs('not json')).toBe('not json');
    expect(summarizeToolArgs(JSON.stringify({ command: 'a\n  b' }))).toBe('a b');
    expect(summarizeToolArgs(JSON.stringify({ command: 'x'.repeat(200) }), 10)).toBe('xxxxxxxxx…');
  });
});
```

`packages/client/src/errors.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { ApiError, DaemonNotRunning, DaemonUnavailable, ProtocolMismatch } from './errors';
import { ev } from './testing';

describe('errors', () => {
  it('carry their details', () => {
    const e = new ApiError(409, 'conflict', 'Already decided', { by: 'desk' });
    expect(e).toBeInstanceOf(Error);
    expect([e.status, e.code, e.message, e.details]).toEqual([409, 'conflict', 'Already decided', { by: 'desk' }]);
    expect(new DaemonUnavailable('http://127.0.0.1:1').message).toContain('Cannot reach deskd');
    expect(new DaemonNotRunning('/data').message).toContain('/data');
    expect(new ProtocolMismatch(2, 1).message).toContain('protocol 2');
  });
});

describe('ev', () => {
  it('builds stored events with defaults', () => {
    expect(ev(3, 'message.user', { text: 'hi' }, { agent: 'd' })).toEqual({ id: 3, project_id: 'p', agent_id: 'd', ts: '2026-09-24T10:00:03.000Z', type: 'message.user', payload: { text: 'hi' } });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run packages/protocol/src/format.test.ts packages/client`
Expected: FAIL: `clip` is not exported, and the modules are missing.

- [ ] **Step 3: Implement**

`packages/protocol/src/format.ts`:

```ts
/** Collapses whitespace and truncates with an ellipsis. */
export function clip(s: string, max: number): string {
  const flat = s.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** A short, single-line rendering of a tool call's JSON arguments: its first string value. */
export function summarizeToolArgs(args: string, max = 80): string {
  let text: string;
  try {
    const parsed = JSON.parse(args) as Record<string, unknown>;
    text = String(Object.values(parsed).find((v) => typeof v === 'string') ?? '');
  } catch {
    text = args;
  }
  return clip(text, max);
}
```

`packages/protocol/src/index.ts`: add `export * from './format';`.

`packages/core/src/state/overview.ts`: delete the local `summarizeToolArgs` function and `import { summarizeToolArgs } from '@desk/protocol';` instead. In `packages/core/src/index.ts`, change the overview export to `export { listOverview } from './state/overview';`. In `packages/core/src/state/overview.test.ts`, delete the `describe('summarizeToolArgs', …)` block and drop `summarizeToolArgs` from its import (the behaviour is now tested in protocol).

`packages/client/package.json`:

```json
{
  "name": "@desk/client",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "exports": {
    ".": "./src/index.ts",
    "./node": "./src/node.ts",
    "./testing": "./src/testing.ts"
  },
  "dependencies": {
    "@desk/protocol": "workspace:*"
  },
  "devDependencies": {
    "@desk/core": "workspace:*",
    "@desk/daemon": "workspace:*",
    "@desk/fake-model": "workspace:*"
  }
}
```

Run `pnpm install --offline` so the workspace links it.

`packages/client/src/errors.ts`:

```ts
/** An error response from deskd: `{ error: { code, message, details? } }`. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** deskd did not answer (not started, crashed, or wrong port). */
export class DaemonUnavailable extends Error {
  constructor(readonly baseUrl: string) {
    super(`Cannot reach deskd at ${baseUrl}`);
    this.name = 'DaemonUnavailable';
  }
}

/** No daemon.json: deskd is not running for this data directory. */
export class DaemonNotRunning extends Error {
  constructor(readonly dataDir: string) {
    super(`deskd is not running (no daemon.json in ${dataDir}). Start it with: desk up`);
    this.name = 'DaemonNotRunning';
  }
}

/** The daemon speaks a different protocol version than this client. */
export class ProtocolMismatch extends Error {
  constructor(
    readonly daemon: number,
    readonly client: number,
  ) {
    super(`deskd speaks protocol ${daemon}, this client speaks protocol ${client}`);
    this.name = 'ProtocolMismatch';
  }
}
```

`packages/client/src/types.ts`:

```ts
import type { AgentRow, ApprovalRow, ArtifactRow, MemoryRow, PlanRow, ProjectRow, SkillDetail, SkillSummary, SourceRow, UsageRow } from '@desk/core';
import type { StoredEvent } from '@desk/protocol';

export type { AgentRow, ApprovalRow, ArtifactRow, MemoryRow, PlanRow, ProjectRow, SkillDetail, SkillSummary, SourceRow, UsageRow };

/** Contents of `<dataDir>/daemon.json`. */
export type DaemonInfo = { port: number; token: string; pid: number; version: string; started_at?: string };

export type Credentials = { baseUrl: string; token: string };

/** GET /projects/:id */
export type ProjectOverview = {
  project: ProjectRow;
  desk: AgentRow | null;
  sources: SourceRow[];
  plan: PlanRow | null;
  threads: AgentRow[];
  approvals: ApprovalRow[];
  last_seq: number;
};

/** Paged event lists (chat, transcript, events). */
export type EventPage = { events: StoredEvent[]; next_after: number };

export type SkillHistoryEntry = { version: number; description: string; current: boolean };
export type SkillSaveResult = { version: number; dir: string; created: boolean; description: string };
export type ProjectUsage = { rows: UsageRow[]; totals: { prompt_tokens: number; completion_tokens: number } };
```

`packages/client/src/testing.ts`:

```ts
import type { EventOf, EventType } from '@desk/protocol';

/** Builds a stored event for reducer tests. Timestamps advance one second per id from 10:00:00Z. */
export function ev<T extends EventType>(
  id: number,
  type: T,
  payload: EventOf<T>['payload'],
  opts: { agent?: string | null; project?: string; ts?: string } = {},
): EventOf<T> {
  const ts = opts.ts ?? new Date(Date.UTC(2026, 8, 24, 10, 0, id)).toISOString();
  return { id, project_id: opts.project ?? 'p', agent_id: opts.agent ?? null, ts, type, payload } as unknown as EventOf<T>;
}
```

`packages/client/src/index.ts`, to start with:

```ts
export * from './errors';
export * from './types';
```

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run packages/protocol packages/client packages/core/src/state && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/protocol packages/client packages/core pnpm-lock.yaml
git commit -m "feat(client): scaffold @desk/client with errors and types; shared tool-arg formatting in protocol

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `DeskClient` (typed REST) and node discovery

**Files:**
- Create: `packages/client/src/client.ts`, `packages/client/src/node.ts`, `packages/client/src/client.test.ts`
- Modify: `packages/client/src/index.ts`

**Interfaces:**
- Consumes: the types and errors from Task 1, plus the `@desk/protocol` request and response schemas.
- Produces:

```ts
export type ClientOptions = Credentials & { refresh?: () => Promise<Credentials | null>; fetch?: typeof fetch };
export type SkillScopeRef = { projectId?: string }; // omitted → global
export class DeskClient {
  constructor(o: ClientOptions);
  readonly baseUrl: string;                        // getter
  credentials(): Credentials;
  refresh(): Promise<boolean>;
  request<T>(method: string, path: string, body?: unknown, opts?: { raw?: boolean }): Promise<T>;
  get/post/patch/put/del<T>(path, body?): Promise<T>;
  stream(projectId: string, afterSeq: number, onMessage: (m: StreamServerMessage) => void): Promise<() => void>; // one-shot, used by the CLI
  health(): Promise<HealthResponse>;
  overview(): Promise<ProjectSummary[]>;
  usage(since?: string): Promise<UsageResponse>;
  projects: { list(all?), create(req), get(id), update(id, patch), archive(id), addSource(id, req), removeSource(id, sid), send(id, text), chat(id, page?), plan(id), usage(id), events(id, q?) };
  threads: { list(projectId, all?), get(id), transcript(id, page?), send(id, text), stop(id), archive(id), diff(id), files(id, path?), file(id, path) };
  approvals: { list(projectId, status?), resolve(id, decision, note?) };
  attention: { list(projectId?), dismiss(id) };
  memory: { list(projectId, q?), add(projectId, req), correct(projectId, mid, req), remove(projectId, mid) };
  library: { list(projectId), upload(projectId, req), file(projectId, path) };
  skills: { list(scope), get(scope, name), file(scope, name, path), save(scope, name, req), remove(scope, name), history(scope, name), restore(scope, name, version), import(scope, req), version(scope, name, v), versionFile(scope, name, v, path) };
  models: { list(), replace(list) };
  config: { get(), patch(p), endpoint(), saveEndpoint(req), testEndpoint(req?) };
}
```

`@desk/client/node`: `defaultDataDir(env?, platform?, home?)`, `readDaemonInfo(dataDir)`, `clientFromDataDir(dataDir, opts?)` (its `refresh` re-reads `daemon.json`), and `checkDaemon(client): Promise<HealthResponse>` (throws `ProtocolMismatch`).

Raw file methods resolve to `Uint8Array`.

- [ ] **Step 1: Write the failing test** — `packages/client/src/client.test.ts`:

```ts
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createHarness, newRuntime, type Harness } from '@desk/core/testing';
import { createApp, startServer, type RunningServer } from '@desk/daemon';
import { ApiError, DaemonUnavailable, DeskClient } from './index';
import { checkDaemon, clientFromDataDir, defaultDataDir, readDaemonInfo } from './node';

let h: Harness;
let server: RunningServer | undefined;
afterEach(async () => {
  await server?.close();
  server = undefined;
  await h?.cleanup();
});

const TOKEN = 'client-token';
async function setup() {
  h = await createHarness();
  const runtime = newRuntime(h);
  server = await startServer({ app: createApp({ runtime, store: h.store, models: h.models, token: TOKEN, version: '1.0.0' }), store: h.store, token: TOKEN, port: 0 });
  const baseUrl = `http://127.0.0.1:${server.port}`;
  return { runtime, baseUrl, client: new DeskClient({ baseUrl, token: TOKEN }) };
}

describe('DeskClient', () => {
  it('covers projects, threads, attention, skills and raw files', async () => {
    const { client, runtime } = await setup();
    expect((await client.health()).protocol_version).toBe(1);
    const created = await client.projects.create({ name: 'Onboarding', goal: 'g' });
    const id = created.project.id;
    expect((await client.projects.list()).map((p) => p.name)).toEqual(['Onboarding']);
    expect((await client.projects.get(id)).desk?.role).toBe('desk');
    expect((await client.projects.update(id, { goal: 'new' })).goal).toBe('new');
    expect(await client.overview()).toHaveLength(1);
    expect((await client.attention.list()).items).toEqual([]);
    expect(await client.projects.plan(id)).toBeNull();

    const t = runtime.createThread(id, { title: 'T', brief: 'b', workspacePath: join(h.dir, 'ws') });
    writeFileSync(join(h.dir, 'ws', 'out.txt'), 'bytes!');
    expect((await client.threads.list(id)).map((x) => x.id)).toEqual([t]);
    expect(await client.threads.files(t)).toEqual([{ name: 'out.txt', path: 'out.txt', type: 'file', size: 6 }]);
    expect(new TextDecoder().decode(await client.threads.file(t, 'out.txt'))).toBe('bytes!');

    await client.skills.save({}, 'weekly-report', { description: 'Weekly', instructions: 'v1' });
    await client.skills.save({ projectId: id }, 'weekly-report', { description: 'Project flavour', instructions: 'p1' });
    expect((await client.skills.get({ projectId: id }, 'weekly-report')).scope).toBe('project');
    expect((await client.skills.version({}, 'weekly-report', 1)).instructions).toBe('v1');
    expect((await client.skills.history({}, 'weekly-report')).map((v) => v.version)).toEqual([1]);

    const mem = await client.memory.add(id, { kind: 'fact', content: 'Launch in October' });
    expect((await client.memory.list(id, 'October')).map((m) => m.id)).toEqual([mem.id]);
    const up = await client.library.upload(id, { name: 'brief.md', content_base64: Buffer.from('# Brief').toString('base64') });
    expect(new TextDecoder().decode(await client.library.file(id, up.path))).toBe('# Brief');
    expect((await client.config.endpoint().catch((e: ApiError) => e.status))).toBe(501);
  });

  it('maps errors, retries once after refreshing credentials on 401, and reports an unreachable daemon', async () => {
    const { baseUrl } = await setup();
    let refreshed = 0;
    const client = new DeskClient({ baseUrl, token: 'stale', refresh: async () => (refreshed++, { baseUrl, token: TOKEN }) });
    expect(await client.projects.list()).toEqual([]);
    expect(refreshed).toBe(1);
    const err = await client.projects.get('nope').catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect([err.status, err.code]).toEqual([404, 'not_found']);
    const bad = await new DeskClient({ baseUrl, token: 'wrong' }).projects.list().catch((e) => e);
    expect([bad.status, bad.code]).toEqual([401, 'unauthorized']);
    await expect(new DeskClient({ baseUrl: 'http://127.0.0.1:9', token: 'x' }).projects.list()).rejects.toBeInstanceOf(DaemonUnavailable);
  });
});

describe('node discovery', () => {
  it('finds the data dir per platform and reads daemon.json', async () => {
    expect(defaultDataDir({ DESK_DATA_DIR: '/x' }, 'darwin', '/h')).toBe('/x');
    expect(defaultDataDir({}, 'darwin', '/h')).toBe('/h/Library/Application Support/Desk');
    expect(defaultDataDir({ APPDATA: 'C:\\Users\\u\\AppData\\Roaming' }, 'win32', 'C:\\Users\\u')).toBe(join('C:\\Users\\u\\AppData\\Roaming', 'Desk'));
    expect(defaultDataDir({}, 'linux', '/h')).toBe('/h/.local/share/desk');

    const { baseUrl } = await setup();
    const dir = mkdtempSync(join(tmpdir(), 'desk-client-'));
    try {
      expect(readDaemonInfo(dir)).toBeNull();
      expect(() => clientFromDataDir(dir)).toThrow(/not running/);
      const port = Number(new URL(baseUrl).port);
      writeFileSync(join(dir, 'daemon.json'), JSON.stringify({ port, token: 'old', pid: 1, version: '1.0.0' }));
      const client = clientFromDataDir(dir);
      writeFileSync(join(dir, 'daemon.json'), JSON.stringify({ port, token: TOKEN, pid: 1, version: '1.0.0' }));
      expect(await client.projects.list()).toEqual([]);
      expect((await checkDaemon(client)).protocol_version).toBe(1);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
```

`@desk/daemon`'s index must export `createApp` and `startServer`. Check with `cat apps/daemon/src/index.ts` and add any missing exports.

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run packages/client/src/client.test.ts`
Expected: FAIL: `DeskClient` is not exported.

- [ ] **Step 3: Implement**

`packages/client/src/client.ts`:

```ts
import type {
  AddSourceRequest,
  AttentionResponse,
  CreateProjectRequest,
  DaemonConfig,
  DaemonConfigPatch,
  HealthResponse,
  LibraryUploadRequest,
  MemoryUpdateRequest,
  MemoryWriteRequest,
  ModelEndpointPutRequest,
  ModelEndpointStatus,
  ModelEndpointTestResult,
  ModelInfo,
  ProjectSummary,
  SkillImportRequest,
  SkillWriteRequest,
  StreamServerMessage,
  ThreadDiff,
  UpdateProjectRequest,
  UsageResponse,
  WorkspaceEntry,
  EventType,
} from '@desk/protocol';
import { ApiError, DaemonUnavailable } from './errors';
import type {
  AgentRow,
  ApprovalRow,
  ArtifactRow,
  Credentials,
  EventPage,
  MemoryRow,
  PlanRow,
  ProjectOverview,
  ProjectRow,
  ProjectUsage,
  SkillDetail,
  SkillHistoryEntry,
  SkillSaveResult,
  SkillSummary,
  SourceRow,
} from './types';

export type ClientOptions = Credentials & {
  /** Called once after a 401: return fresh credentials (e.g. re-read daemon.json), or null. */
  refresh?: () => Promise<Credentials | null>;
  fetch?: typeof fetch;
};

/** Where a skill lives: a project (resolving project first, then global, as agents do) or, when omitted, global. */
export type SkillScopeRef = { projectId?: string };

const enc = encodeURIComponent;
const encPath = (p: string) => p.split('/').map(enc).join('/');
const page = (p?: { after?: number; limit?: number }) => {
  const q = new URLSearchParams();
  if (p?.after !== undefined) q.set('after', String(p.after));
  if (p?.limit !== undefined) q.set('limit', String(p.limit));
  const s = q.toString();
  return s ? `?${s}` : '';
};

/** Typed client for the deskd HTTP API. Environment-neutral: uses the global fetch and WebSocket. */
export class DeskClient {
  private creds: Credentials;

  constructor(private readonly o: ClientOptions) {
    this.creds = { baseUrl: o.baseUrl, token: o.token };
  }

  get baseUrl(): string {
    return this.creds.baseUrl;
  }

  credentials(): Credentials {
    return { ...this.creds };
  }

  /** Asks `refresh` for new credentials; true when they changed. */
  async refresh(): Promise<boolean> {
    const next = await this.o.refresh?.();
    if (!next) return false;
    const changed = next.baseUrl !== this.creds.baseUrl || next.token !== this.creds.token;
    this.creds = next;
    return changed;
  }

  async request<T = unknown>(method: string, path: string, body?: unknown, opts: { raw?: boolean } = {}): Promise<T> {
    const send = async () => {
      try {
        return await (this.o.fetch ?? fetch)(`${this.creds.baseUrl}/v1${path}`, {
          method,
          headers: { authorization: `Bearer ${this.creds.token}`, ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
          ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
        });
      } catch {
        throw new DaemonUnavailable(this.creds.baseUrl);
      }
    };
    let res = await send();
    if (res.status === 401 && (await this.refresh())) res = await send();
    if (opts.raw && res.ok) return new Uint8Array(await res.arrayBuffer()) as T;
    const isJson = (res.headers.get('content-type') ?? '').includes('json');
    const data: unknown = isJson ? await res.json() : await res.text();
    if (!res.ok) {
      const e = (data as { error?: { code?: string; message?: string; details?: unknown } } | null)?.error;
      throw new ApiError(res.status, e?.code ?? 'error', e?.message ?? `HTTP ${res.status}`, e?.details);
    }
    return data as T;
  }

  get = <T = any>(path: string) => this.request<T>('GET', path);
  post = <T = any>(path: string, body: unknown = {}) => this.request<T>('POST', path, body);
  patch = <T = any>(path: string, body: unknown) => this.request<T>('PATCH', path, body);
  put = <T = any>(path: string, body: unknown) => this.request<T>('PUT', path, body);
  del = <T = any>(path: string) => this.request<T>('DELETE', path);
  private raw = (path: string) => this.request<Uint8Array>('GET', path, undefined, { raw: true });

  /** One-shot subscription (no reconnect); resolves once replay is done with a closer. Prefer DeskStream in apps. */
  stream(projectId: string, afterSeq: number, onMessage: (m: StreamServerMessage) => void): Promise<() => void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(`${this.creds.baseUrl.replace(/^http/, 'ws')}/v1/stream?token=${enc(this.creds.token)}`);
      let ready = false;
      ws.onopen = () => ws.send(JSON.stringify({ subscribe: { project_id: projectId, after_seq: afterSeq } }));
      ws.onmessage = (e) => {
        const m = JSON.parse(String(e.data)) as StreamServerMessage;
        onMessage(m);
        if (m.kind === 'ready' && !ready) {
          ready = true;
          resolve(() => ws.close());
        }
      };
      ws.onerror = () => {
        if (!ready) reject(new Error('Event stream connection failed'));
      };
    });
  }

  health = () => this.get<HealthResponse>('/health');
  overview = () => this.get<ProjectSummary[]>('/overview');
  usage = (since?: string) => this.get<UsageResponse>(`/usage${since ? `?since=${enc(since)}` : ''}`);

  projects = {
    list: (all = false) => this.get<ProjectRow[]>(`/projects${all ? '?all=1' : ''}`),
    create: (req: CreateProjectRequest) => this.post<ProjectOverview>('/projects', req),
    get: (id: string) => this.get<ProjectOverview>(`/projects/${enc(id)}`),
    update: (id: string, patch: UpdateProjectRequest) => this.patch<ProjectRow>(`/projects/${enc(id)}`, patch),
    archive: (id: string) => this.post<{ ok: true }>(`/projects/${enc(id)}/archive`),
    addSource: (id: string, req: AddSourceRequest) => this.post<SourceRow>(`/projects/${enc(id)}/sources`, req),
    removeSource: (id: string, sourceId: string) => this.del<{ ok: true }>(`/projects/${enc(id)}/sources/${enc(sourceId)}`),
    send: (id: string, text: string) => this.post<{ ok: true }>(`/projects/${enc(id)}/messages`, { text }),
    chat: (id: string, p?: { after?: number; limit?: number }) => this.get<EventPage>(`/projects/${enc(id)}/chat${page(p)}`),
    plan: (id: string) => this.get<PlanRow | null>(`/projects/${enc(id)}/plan`),
    usage: (id: string) => this.get<ProjectUsage>(`/projects/${enc(id)}/usage`),
    events: (id: string, q?: { after?: number; limit?: number; types?: EventType[] }) => {
      const qs = page(q);
      const types = q?.types?.length ? `${qs ? '&' : '?'}types=${q.types.join(',')}` : '';
      return this.get<EventPage>(`/projects/${enc(id)}/events${qs}${types}`);
    },
  };

  threads = {
    list: (projectId: string, all = false) => this.get<AgentRow[]>(`/projects/${enc(projectId)}/threads${all ? '?all=1' : ''}`),
    get: (id: string) => this.get<AgentRow>(`/threads/${enc(id)}`),
    transcript: (id: string, p?: { after?: number; limit?: number }) => this.get<EventPage>(`/threads/${enc(id)}/transcript${page(p)}`),
    send: (id: string, text: string) => this.post<{ ok: true }>(`/threads/${enc(id)}/messages`, { text }),
    stop: (id: string) => this.post<{ ok: true }>(`/threads/${enc(id)}/stop`),
    archive: (id: string) => this.post<{ ok: true }>(`/threads/${enc(id)}/archive`),
    diff: (id: string) => this.get<ThreadDiff>(`/threads/${enc(id)}/diff`),
    files: (id: string, path = '') => this.get<WorkspaceEntry[]>(`/threads/${enc(id)}/files${path ? `?path=${enc(path)}` : ''}`),
    file: (id: string, path: string) => this.raw(`/threads/${enc(id)}/files/raw/${encPath(path)}`),
  };

  approvals = {
    list: (projectId: string, status?: 'pending' | 'approved' | 'denied') =>
      this.get<ApprovalRow[]>(`/projects/${enc(projectId)}/approvals${status ? `?status=${status}` : ''}`),
    resolve: (id: string, decision: 'approved' | 'denied', note?: string) =>
      this.post<{ ok: true }>(`/approvals/${enc(id)}/resolve`, { decision, ...(note ? { note } : {}) }),
  };

  attention = {
    list: (projectId?: string) => this.get<AttentionResponse>(`/attention${projectId ? `?project_id=${enc(projectId)}` : ''}`),
    dismiss: (id: string) => this.post<{ ok: true }>(`/attention/${enc(id)}/dismiss`),
  };

  memory = {
    list: (projectId: string, q?: string) => this.get<MemoryRow[]>(`/projects/${enc(projectId)}/memory${q ? `?q=${enc(q)}` : ''}`),
    add: (projectId: string, req: MemoryWriteRequest) => this.post<MemoryRow>(`/projects/${enc(projectId)}/memory`, req),
    correct: (projectId: string, memoryId: string, req: MemoryUpdateRequest) =>
      this.patch<MemoryRow>(`/projects/${enc(projectId)}/memory/${enc(memoryId)}`, req),
    remove: (projectId: string, memoryId: string) => this.del<{ ok: true }>(`/projects/${enc(projectId)}/memory/${enc(memoryId)}`),
  };

  library = {
    list: (projectId: string) => this.get<ArtifactRow[]>(`/projects/${enc(projectId)}/library`),
    upload: (projectId: string, req: LibraryUploadRequest) => this.post<ArtifactRow>(`/projects/${enc(projectId)}/library`, req),
    file: (projectId: string, path: string) => this.raw(`/projects/${enc(projectId)}/library/file/${encPath(path)}`),
  };

  private skillBase = (s: SkillScopeRef) => (s.projectId ? `/projects/${enc(s.projectId)}/skills` : '/skills');

  skills = {
    list: (s: SkillScopeRef) => this.get<SkillSummary[]>(this.skillBase(s)),
    get: (s: SkillScopeRef, name: string) => this.get<SkillDetail>(`${this.skillBase(s)}/${enc(name)}`),
    file: (s: SkillScopeRef, name: string, path: string) => this.raw(`${this.skillBase(s)}/${enc(name)}/files/${encPath(path)}`),
    save: (s: SkillScopeRef, name: string, req: SkillWriteRequest) => this.put<SkillSaveResult>(`${this.skillBase(s)}/${enc(name)}`, req),
    remove: (s: SkillScopeRef, name: string) => this.del<{ ok: true }>(`${this.skillBase(s)}/${enc(name)}`),
    history: (s: SkillScopeRef, name: string) => this.get<SkillHistoryEntry[]>(`${this.skillBase(s)}/${enc(name)}/history`),
    restore: (s: SkillScopeRef, name: string, version: number) => this.post<SkillSaveResult>(`${this.skillBase(s)}/${enc(name)}/restore`, { version }),
    import: (s: SkillScopeRef, req: SkillImportRequest) => this.post<SkillSaveResult>(`${this.skillBase(s)}/import`, req),
    version: (s: SkillScopeRef, name: string, v: number) => this.get<SkillDetail>(`${this.skillBase(s)}/${enc(name)}/versions/${v}`),
    versionFile: (s: SkillScopeRef, name: string, v: number, path: string) => this.raw(`${this.skillBase(s)}/${enc(name)}/versions/${v}/files/${encPath(path)}`),
  };

  models = {
    list: () => this.get<ModelInfo[]>('/models'),
    replace: (list: ModelInfo[]) => this.put<ModelInfo[]>('/models', list),
  };

  config = {
    get: () => this.get<DaemonConfig>('/config'),
    patch: (p: DaemonConfigPatch) => this.patch<DaemonConfig>('/config', p),
    endpoint: () => this.get<ModelEndpointStatus>('/config/model-endpoint'),
    saveEndpoint: (req: ModelEndpointPutRequest) => this.put<ModelEndpointStatus>('/config/model-endpoint', req),
    testEndpoint: (req?: ModelEndpointPutRequest) =>
      req ? this.post<ModelEndpointTestResult>('/config/model-endpoint/test', req) : this.request<ModelEndpointTestResult>('POST', '/config/model-endpoint/test'),
  };
}
```

For the `/config/model-endpoint/test` POST with no body, `request` sends no body at all, and the route treats an empty body as "test the current endpoint".

`packages/client/src/node.ts`:

```ts
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { PROTOCOL_VERSION, type HealthResponse } from '@desk/protocol';
import { DeskClient } from './client';
import { DaemonNotRunning, ProtocolMismatch } from './errors';
import type { Credentials, DaemonInfo } from './types';

/** Where deskd keeps its data (and daemon.json) on this platform. */
export function defaultDataDir(env: NodeJS.ProcessEnv = process.env, platform: NodeJS.Platform = process.platform, home: string = homedir()): string {
  if (env.DESK_DATA_DIR) return env.DESK_DATA_DIR;
  if (platform === 'darwin') return join(home, 'Library', 'Application Support', 'Desk');
  if (platform === 'win32') return join(env.APPDATA ?? join(home, 'AppData', 'Roaming'), 'Desk');
  return join(env.XDG_DATA_HOME ?? join(home, '.local', 'share'), 'desk');
}

export function readDaemonInfo(dataDir: string): DaemonInfo | null {
  const file = join(dataDir, 'daemon.json');
  if (!existsSync(file)) return null;
  try {
    return JSON.parse(readFileSync(file, 'utf8')) as DaemonInfo;
  } catch {
    return null;
  }
}

const credsOf = (info: DaemonInfo): Credentials => ({ baseUrl: `http://127.0.0.1:${info.port}`, token: info.token });

/** A client for the daemon of `dataDir`; after a 401 it re-reads daemon.json (the token changes on every daemon start). */
export function clientFromDataDir(dataDir: string, opts: { fetch?: typeof fetch } = {}): DeskClient {
  const info = readDaemonInfo(dataDir);
  if (!info) throw new DaemonNotRunning(dataDir);
  return new DeskClient({
    ...credsOf(info),
    refresh: async () => {
      const next = readDaemonInfo(dataDir);
      return next ? credsOf(next) : null;
    },
    ...(opts.fetch ? { fetch: opts.fetch } : {}),
  });
}

/** Checks the daemon answers and speaks our protocol. */
export async function checkDaemon(client: DeskClient): Promise<HealthResponse> {
  const health = await client.health();
  if (health.protocol_version !== PROTOCOL_VERSION) throw new ProtocolMismatch(health.protocol_version, PROTOCOL_VERSION);
  return health;
}

export type { DaemonInfo };
```

`packages/client/src/index.ts`: add `export * from './client';`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run packages/client && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/client apps/daemon/src/index.ts
git commit -m "feat(client): typed DeskClient for every endpoint, node discovery with token refresh

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `DeskStream` — reconnecting event stream

**Files:**
- Create: `packages/client/src/stream.ts`, `packages/client/src/stream.test.ts`
- Modify: `packages/client/src/index.ts`

**Interfaces:**
- Produces:

```ts
export type StreamStatus = 'connecting' | 'live' | 'reconnecting' | 'closed';
export type StreamOptions = {
  credentials: () => Credentials;
  refresh?: () => Promise<boolean>;
  projectId: string;               // or '*'
  afterSeq: number;
  hello?: { client: string; notifications: boolean };
  onEvent(e: StoredEvent): void;
  onEphemeral?(e: EphemeralEvent): void;
  onReady?(seq: number): void;
  onStatus?(s: StreamStatus): void;
  onError?(message: string): void;
  WebSocketImpl?: typeof WebSocket;
  backoff?: { minMs: number; maxMs: number };
  random?: () => number;
};
export class DeskStream { constructor(o: StreamOptions); readonly lastSeq: number; start(): void; close(): void; }
```

- [ ] **Step 1: Write the failing test** — `packages/client/src/stream.test.ts`:

```ts
import { afterEach, describe, expect, it } from 'vitest';
import { text } from '@desk/fake-model';
import { createHarness, newRuntime, type Harness } from '@desk/core/testing';
import { createApp, startServer, type RunningServer } from '@desk/daemon';
import type { StoredEvent } from '@desk/protocol';
import { DeskStream, type StreamStatus } from './stream';

let h: Harness;
let server: RunningServer | undefined;
afterEach(async () => {
  await server?.close();
  server = undefined;
  await h?.cleanup();
});

const until = async (pred: () => boolean, ms = 3000) => {
  const end = Date.now() + ms;
  while (!pred()) {
    if (Date.now() > end) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 10));
  }
};

describe('DeskStream against deskd', () => {
  it('replays, goes live, streams deltas, and sends hello', async () => {
    h = await createHarness({ script: () => text('streamed reply text') });
    const runtime = newRuntime(h);
    server = await startServer({ app: createApp({ runtime, store: h.store, models: h.models, token: 't', version: 'v' }), store: h.store, token: 't', port: 0 });
    const p = runtime.createProject({ name: 'P', goal: 'g' });
    const events: StoredEvent[] = [];
    const deltas: string[] = [];
    const statuses: StreamStatus[] = [];
    const s = new DeskStream({
      credentials: () => ({ baseUrl: `http://127.0.0.1:${server!.port}`, token: 't' }),
      projectId: p,
      afterSeq: 0,
      hello: { client: 'desktop', notifications: true },
      onEvent: (e) => events.push(e),
      onEphemeral: (e) => deltas.push(e.payload.text),
      onStatus: (st) => statuses.push(st),
    });
    s.start();
    await until(() => statuses.includes('live'));
    expect(events.map((e) => e.type)).toEqual(['project.created', 'agent.created']);
    expect(server!.notifyingClients()).toBe(1);
    runtime.sendToDesk(p, 'hello');
    await until(() => events.some((e) => e.type === 'assistant.message'));
    expect(deltas.join('')).toBe('streamed reply text');
    expect(s.lastSeq).toBe(events.at(-1)!.id);
    s.close();
    await until(() => statuses.at(-1) === 'closed');
    await runtime.shutdown();
  });
});

/** A scriptable WebSocket stand-in for reconnect logic. */
class FakeWS {
  static instances: FakeWS[] = [];
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) {
    FakeWS.instances.push(this);
    queueMicrotask(() => this.onopen?.());
  }
  send(s: string) {
    this.sent.push(s);
  }
  close() {
    this.onclose?.({ code: 1000 });
  }
  emit(m: unknown) {
    this.onmessage?.({ data: JSON.stringify(m) });
  }
  drop(code = 1006) {
    this.onclose?.({ code });
  }
}

describe('DeskStream reconnects', () => {
  it('resumes from the last applied event, skips duplicates, and refreshes credentials on 4401', async () => {
    FakeWS.instances = [];
    let token = 'a';
    let refreshed = 0;
    const got: number[] = [];
    const s = new DeskStream({
      credentials: () => ({ baseUrl: 'http://127.0.0.1:1', token }),
      refresh: async () => {
        refreshed++;
        token = 'b';
        return true;
      },
      projectId: '*',
      afterSeq: 5,
      onEvent: (e) => got.push(e.id),
      WebSocketImpl: FakeWS as unknown as typeof WebSocket,
      backoff: { minMs: 1, maxMs: 4 },
      random: () => 1,
    });
    s.start();
    await until(() => FakeWS.instances[0]!.sent.length === 1);
    expect(JSON.parse(FakeWS.instances[0]!.sent[0]!)).toEqual({ subscribe: { project_id: '*', after_seq: 5 } });
    const e = (id: number) => ({ kind: 'event', event: { id, project_id: 'p', agent_id: null, ts: '', type: 'message.user', payload: { text: 'x' } } });
    FakeWS.instances[0]!.emit(e(6));
    FakeWS.instances[0]!.emit(e(6));
    FakeWS.instances[0]!.emit(e(7));
    FakeWS.instances[0]!.drop();
    await until(() => FakeWS.instances.length === 2 && FakeWS.instances[1]!.sent.length === 1);
    expect(JSON.parse(FakeWS.instances[1]!.sent[0]!)).toEqual({ subscribe: { project_id: '*', after_seq: 7 } });
    FakeWS.instances[1]!.drop(4401);
    await until(() => FakeWS.instances.length === 3);
    expect(refreshed).toBe(1);
    expect(FakeWS.instances[2]!.url).toContain('token=b');
    expect(got).toEqual([6, 7]);
    s.close();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run packages/client/src/stream.test.ts`
Expected: FAIL: `./stream` is missing.

- [ ] **Step 3: Implement** — `packages/client/src/stream.ts`:

```ts
import type { EphemeralEvent, StoredEvent, StreamServerMessage } from '@desk/protocol';
import type { Credentials } from './types';

export type StreamStatus = 'connecting' | 'live' | 'reconnecting' | 'closed';

export type StreamOptions = {
  credentials: () => Credentials;
  /** Called after the server closes with 4401 (bad token); resolve true once credentials were refreshed. */
  refresh?: () => Promise<boolean>;
  projectId: string;
  afterSeq: number;
  hello?: { client: string; notifications: boolean };
  onEvent(e: StoredEvent): void;
  onEphemeral?(e: EphemeralEvent): void;
  onReady?(seq: number): void;
  onStatus?(s: StreamStatus): void;
  onError?(message: string): void;
  WebSocketImpl?: typeof WebSocket;
  backoff?: { minMs: number; maxMs: number };
  random?: () => number;
};

/**
 * The deskd event stream with resume: every (re)connection subscribes after the last applied event id,
 * so events are never missed or applied twice. Reconnects with jittered exponential backoff.
 */
export class DeskStream {
  private ws: WebSocket | null = null;
  private cursor: number;
  private closed = false;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(private readonly o: StreamOptions) {
    this.cursor = o.afterSeq;
  }

  get lastSeq(): number {
    return this.cursor;
  }

  start(): void {
    this.closed = false;
    this.connect();
  }

  close(): void {
    this.closed = true;
    clearTimeout(this.timer);
    const ws = this.ws;
    if (ws) ws.close();
    else this.o.onStatus?.('closed');
  }

  private connect(): void {
    const { baseUrl, token } = this.o.credentials();
    this.o.onStatus?.(this.attempt ? 'reconnecting' : 'connecting');
    const WS = this.o.WebSocketImpl ?? WebSocket;
    const ws = new WS(`${baseUrl.replace(/^http/, 'ws')}/v1/stream?token=${encodeURIComponent(token)}`);
    this.ws = ws;
    ws.onopen = () => {
      if (this.o.hello) ws.send(JSON.stringify({ hello: this.o.hello }));
      ws.send(JSON.stringify({ subscribe: { project_id: this.o.projectId, after_seq: this.cursor } }));
    };
    ws.onmessage = (e: MessageEvent) => {
      let m: StreamServerMessage;
      try {
        m = JSON.parse(String(e.data)) as StreamServerMessage;
      } catch {
        return;
      }
      if (m.kind === 'event') {
        if (m.event.id <= this.cursor) return;
        this.cursor = m.event.id;
        this.o.onEvent(m.event);
      } else if (m.kind === 'ephemeral') {
        this.o.onEphemeral?.(m.event);
      } else if (m.kind === 'ready') {
        this.attempt = 0;
        this.o.onStatus?.('live');
        this.o.onReady?.(m.seq);
      } else {
        this.o.onError?.(m.message);
      }
    };
    ws.onerror = () => {};
    ws.onclose = (e: { code: number }) => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (this.closed) {
        this.o.onStatus?.('closed');
        return;
      }
      if (e.code === 4401 && this.o.refresh) {
        void this.o.refresh().then(
          () => this.schedule(),
          () => this.schedule(),
        );
      } else this.schedule();
    };
  }

  private schedule(): void {
    if (this.closed) return;
    const { minMs, maxMs } = this.o.backoff ?? { minMs: 500, maxMs: 10_000 };
    const base = Math.min(maxMs, minMs * 2 ** this.attempt);
    const delay = base * (0.5 + (this.o.random ?? Math.random)() * 0.5);
    this.attempt++;
    this.o.onStatus?.('reconnecting');
    this.timer = setTimeout(() => this.connect(), delay);
  }
}
```

`packages/client/src/index.ts`: add `export * from './stream';`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run packages/client && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/client
git commit -m "feat(client): DeskStream with resume, dedupe, backoff reconnect and token refresh

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Project and chat reducers

**Files:**
- Create: `packages/client/src/state/project.ts`, `packages/client/src/state/chat.ts`, `packages/client/src/state/project.test.ts`, `packages/client/src/state/chat.test.ts`
- Modify: `packages/client/src/index.ts`

**Interfaces:**
- Produces:

```ts
// project.ts
export type ThreadView = AgentRow & { activity: string | null; reason: string | null; model_override: string | null };
export type ProjectState = { project: ProjectRow; desk: ThreadView | null; sources: SourceRow[]; plan: PlanItem[]; threads: ThreadView[]; approvals: ApprovalRow[]; lastSeq: number };
export function projectFromOverview(o: ProjectOverview): ProjectState;
export function reduceProject(s: ProjectState, e: StoredEvent): ProjectState;
// chat.ts
export type ToolCallView = { id: string; name: string; arguments: string; status: 'running' | ToolResultStatus; content: string | null };
export type ChatItem = …; // see code
export type ChatState = { agentId: string; items: ChatItem[]; lastSeq: number };
export function emptyChat(agentId: string): ChatState;
export function reduceChat(s: ChatState, e: StoredEvent): ChatState;
export function applyChatDelta(s: ChatState, e: EphemeralEvent): ChatState;
```

- [ ] **Step 1: Write the failing tests**

`packages/client/src/state/project.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { ev } from '../testing';
import type { ProjectOverview } from '../types';
import { projectFromOverview, reduceProject } from './project';

const overview: ProjectOverview = {
  project: { id: 'p', name: 'Onboarding', goal: 'g', instructions: '', settings: { desk_model: 'm', thread_model: 'm', fallback_model: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, policy: [] }, created_at: 't', updated_at: 't', archived_at: null },
  desk: { id: 'd', project_id: 'p', role: 'desk', status: 'idle', model: 'm', title: 'Desk', brief: null, workspace_path: '/w', parent_id: null, inbox_cursor: 0, review_round: 0, result_summary: null, result_artifacts: null, active_skills: [], git_source_id: null, git_branch: null, git_base: null, git_common_dir: null, archived_at: null, created_at: 't', updated_at: 't' },
  sources: [],
  plan: null,
  threads: [],
  approvals: [],
  last_seq: 2,
};

const fold = (events: Parameters<typeof reduceProject>[1][]) => events.reduce(reduceProject, projectFromOverview(overview));

describe('reduceProject', () => {
  it('tracks threads from creation through activity, approval, revision, fallback and archive', () => {
    const s = fold([
      ev(3, 'agent.created', { role: 'thread', model: 'm', title: 'Emails', brief: 'b', workspace_path: '/w/t', parent_id: 'd', skills: ['brand-voice'] }, { agent: 't' }),
      ev(4, 'agent.status_changed', { status: 'running' }, { agent: 't' }),
      ev(5, 'tool.call', { run_id: 'r', tool_call_id: 'c', name: 'write_file', arguments: '{"path":"emails/04.md","content":"…"}' }, { agent: 't' }),
      ev(6, 'approval.requested', { approval_id: 'a1', run_id: 'r', tool_call_id: 'c2', tool: 'bash', arguments: '{}', reason: 'rule 1', delegate_to_desk: false }, { agent: 't' }),
      ev(7, 'agent.status_changed', { status: 'waiting', reason: 'Waiting for approval' }, { agent: 't' }),
      ev(8, 'agent.model_switched', { from: 'm', to: 'fallback', reason: 'rate limited', scope: 'run' }, { agent: 't' }),
    ]);
    expect(s.threads).toHaveLength(1);
    expect(s.threads[0]).toMatchObject({ id: 't', status: 'waiting', reason: 'Waiting for approval', activity: 'write_file · emails/04.md', active_skills: ['brand-voice'], model_override: 'fallback' });
    expect(s.approvals.map((a) => a.id)).toEqual(['a1']);
    expect(s.lastSeq).toBe(8);

    const s2 = [
      ev(9, 'approval.resolved', { approval_id: 'a1', decision: 'approved', resolved_by: 'user' }, { agent: 't' }),
      ev(10, 'tool.result', { run_id: 'r', tool_call_id: 'c', name: 'write_file', status: 'ok', content: 'ok' }, { agent: 't' }),
      ev(11, 'agent.result', { summary: 'Five drafts', artifacts: ['emails/01.md'] }, { agent: 't' }),
      ev(12, 'agent.status_changed', { status: 'done' }, { agent: 't' }),
      ev(13, 'agent.revision', { round: 1, feedback: 'Less salesy' }, { agent: 't' }),
      ev(14, 'run.started', { run_id: 'r2', model: 'm' }, { agent: 't' }),
      ev(15, 'agent.archived', {}, { agent: 't' }),
    ].reduce(reduceProject, s);
    expect(s2.approvals).toEqual([]);
    expect(s2.threads[0]).toMatchObject({ activity: null, result_summary: 'Five drafts', review_round: 1, model_override: null, reason: null, status: 'done' });
    expect(s2.threads[0]!.archived_at).not.toBeNull();
  });

  it('updates project fields, settings, sources, plan and desk; ignores replayed events', () => {
    const s = fold([
      ev(3, 'project.updated', { goal: 'New goal', settings: { check_in: 'minimal' } }),
      ev(4, 'source.added', { source_id: 's1', path: '/repo', kind: 'git', label: 'repo' }),
      ev(5, 'plan.updated', { items: [{ id: '1', title: 'A', status: 'todo', thread_ids: [], notes: '' }] }),
      ev(6, 'agent.status_changed', { status: 'running' }, { agent: 'd' }),
      ev(7, 'source.removed', { source_id: 's1' }),
    ]);
    expect(s.project.goal).toBe('New goal');
    expect(s.project.settings.check_in).toBe('minimal');
    expect(s.project.settings.review_rounds).toBe(2);
    expect(s.sources).toEqual([]);
    expect(s.plan.map((i) => i.title)).toEqual(['A']);
    expect(s.desk?.status).toBe('running');
    expect(reduceProject(s, ev(2, 'project.updated', { goal: 'stale' }))).toBe(s);
  });
});
```

`packages/client/src/state/chat.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { ev } from '../testing';
import { applyChatDelta, emptyChat, reduceChat } from './chat';

const delta = (run: string, text: string) => ({ type: 'assistant.delta' as const, project_id: 'p', agent_id: 'd', payload: { run_id: run, text } });

describe('reduceChat', () => {
  it('builds the Desk conversation with streaming, tools, reports, questions and notices', () => {
    let s = emptyChat('d');
    s = reduceChat(s, ev(1, 'message.user', { text: 'Relaunch onboarding' }, { agent: 'd' }));
    s = applyChatDelta(s, delta('r1', 'Got '));
    s = applyChatDelta(s, delta('r1', 'it.'));
    expect(s.items.at(-1)).toMatchObject({ kind: 'assistant', runId: 'r1', text: 'Got it.', streaming: true });
    s = reduceChat(s, ev(2, 'assistant.message', { run_id: 'r1', content: 'Got it. Four threads.', tool_calls: [] }, { agent: 'd' }));
    s = reduceChat(s, ev(3, 'tool.call', { run_id: 'r1', tool_call_id: 'c1', name: 'spawn_thread', arguments: '{"title":"A"}' }, { agent: 'd' }));
    s = reduceChat(s, ev(4, 'tool.call', { run_id: 'r1', tool_call_id: 'c2', name: 'spawn_thread', arguments: '{"title":"B"}' }, { agent: 'd' }));
    s = reduceChat(s, ev(5, 'tool.result', { run_id: 'r1', tool_call_id: 'c1', name: 'spawn_thread', status: 'ok', content: 'spawned' }, { agent: 'd' }));
    s = reduceChat(s, ev(6, 'message.agent', { from_agent_id: 't', from_label: 'thread "A"', kind: 'completed', text: 'Done' }, { agent: 'd' }));
    s = reduceChat(s, ev(7, 'report', { headline: 'Research is in', progress: 'p', needs_you: ['Approve bun'], results: ['a.md'] }, { agent: 'd' }));
    s = reduceChat(s, ev(8, 'question.asked', { question: 'Data source first?', options: ['Yes', 'No'] }, { agent: 'd' }));
    s = reduceChat(s, ev(9, 'system.notice', { level: 'error', code: 'proxy_down', message: 'paused' }));
    s = reduceChat(s, ev(10, 'tool.call', { run_id: 'r2', tool_call_id: 'x', name: 'bash', arguments: '{}' }, { agent: 'other' }));
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'assistant', 'tools', 'agent', 'report', 'question', 'notice']);
    expect(s.items[1]).toMatchObject({ text: 'Got it. Four threads.', streaming: false, id: 'e:2' });
    expect(s.items[2]).toMatchObject({ kind: 'tools', calls: [{ id: 'c1', status: 'ok', content: 'spawned' }, { id: 'c2', status: 'running' }] });
    expect(s.items[5]).toMatchObject({ kind: 'question', answered: false, options: ['Yes', 'No'] });
    s = reduceChat(s, ev(11, 'message.user', { text: 'Yes' }, { agent: 'd' }));
    expect(s.items[5]).toMatchObject({ answered: true });
  });

  it('drops an empty streamed run and keeps a stream cut off by an error', () => {
    let s = applyChatDelta(emptyChat('d'), delta('r1', 'partial'));
    s = reduceChat(s, ev(1, 'assistant.message', { run_id: 'r1', content: null, tool_calls: [{ id: 'c', name: 'x', arguments: '{}' }] }, { agent: 'd' }));
    expect(s.items).toEqual([]);
    s = applyChatDelta(s, delta('r2', 'half a sent'));
    s = reduceChat(s, ev(2, 'run.finished', { run_id: 'r2', reason: 'error', detail: 'boom' }, { agent: 'd' }));
    expect(s.items).toEqual([expect.objectContaining({ kind: 'assistant', text: 'half a sent', streaming: false, interrupted: true })]);
    s = reduceChat(s, ev(3, 'context.compacted', { run_id: 'r3', checkpoint: 'x', up_to: 2, trigger: 'threshold' }, { agent: 'd' }));
    expect(s.items.at(-1)).toMatchObject({ kind: 'compacted' });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run packages/client/src/state`
Expected: FAIL: modules are missing.

- [ ] **Step 3: Implement**

`packages/client/src/state/project.ts`:

```ts
import { clip, summarizeToolArgs, type PlanItem, type StoredEvent } from '@desk/protocol';
import type { AgentRow, ApprovalRow, ProjectOverview, ProjectRow, SourceRow } from '../types';

/** A thread (or Desk) with live details that are not columns: current tool activity, status reason, fallback model. */
export type ThreadView = AgentRow & { activity: string | null; reason: string | null; model_override: string | null };

export type ProjectState = {
  project: ProjectRow;
  desk: ThreadView | null;
  sources: SourceRow[];
  plan: PlanItem[];
  threads: ThreadView[];
  /** Pending approvals only. */
  approvals: ApprovalRow[];
  lastSeq: number;
};

const view = (a: AgentRow): ThreadView => ({ ...a, activity: null, reason: null, model_override: null });

export function projectFromOverview(o: ProjectOverview): ProjectState {
  return {
    project: o.project,
    desk: o.desk ? view(o.desk) : null,
    sources: o.sources,
    plan: o.plan?.items ?? [],
    threads: o.threads.map(view),
    approvals: o.approvals,
    lastSeq: o.last_seq,
  };
}

const TERMINAL = new Set(['done', 'failed', 'cancelled']);

function updateAgent(s: ProjectState, id: string | null, fn: (a: ThreadView) => ThreadView): ProjectState {
  if (!id) return s;
  if (s.desk?.id === id) return { ...s, desk: fn(s.desk) };
  const i = s.threads.findIndex((t) => t.id === id);
  if (i < 0) return s;
  const threads = s.threads.slice();
  threads[i] = fn(threads[i]!);
  return { ...s, threads };
}

/** Folds one event into the project state. Events at or below `lastSeq` are ignored (replay safety). */
export function reduceProject(prev: ProjectState, e: StoredEvent): ProjectState {
  if (e.id <= prev.lastSeq || e.project_id !== prev.project.id) return prev;
  const s: ProjectState = { ...prev, lastSeq: e.id };
  const touch = (fn: (a: ThreadView) => ThreadView) => updateAgent(s, e.agent_id, (a) => ({ ...fn(a), updated_at: e.ts }));
  switch (e.type) {
    case 'project.updated': {
      const { settings, ...fields } = e.payload;
      return { ...s, project: { ...s.project, ...fields, settings: { ...s.project.settings, ...(settings ?? {}) }, updated_at: e.ts } as ProjectRow };
    }
    case 'project.archived':
      return { ...s, project: { ...s.project, archived_at: e.ts } };
    case 'source.added':
      return { ...s, sources: [...s.sources, { id: e.payload.source_id, project_id: e.project_id, path: e.payload.path, kind: e.payload.kind, label: e.payload.label, created_at: e.ts }] };
    case 'source.removed':
      return { ...s, sources: s.sources.filter((x) => x.id !== e.payload.source_id) };
    case 'agent.created': {
      if (!e.agent_id) return s;
      const row: ThreadView = view({
        id: e.agent_id,
        project_id: e.project_id,
        role: e.payload.role,
        status: 'idle',
        model: e.payload.model,
        title: e.payload.title,
        brief: e.payload.brief,
        workspace_path: e.payload.workspace_path,
        parent_id: e.payload.parent_id,
        inbox_cursor: 0,
        review_round: 0,
        result_summary: null,
        result_artifacts: null,
        active_skills: e.payload.skills ?? [],
        git_source_id: e.payload.git?.source_id ?? null,
        git_branch: e.payload.git?.branch ?? null,
        git_base: e.payload.git?.base ?? null,
        git_common_dir: e.payload.git?.common_dir ?? null,
        archived_at: null,
        created_at: e.ts,
        updated_at: e.ts,
      });
      return e.payload.role === 'desk' ? { ...s, desk: row } : { ...s, threads: [...s.threads, row] };
    }
    case 'agent.status_changed':
      return touch((a) => ({
        ...a,
        status: e.payload.status,
        reason: e.payload.reason ?? null,
        activity: TERMINAL.has(e.payload.status) ? null : a.activity,
      }));
    case 'agent.result':
      return touch((a) => ({ ...a, result_summary: e.payload.summary, result_artifacts: e.payload.artifacts }));
    case 'agent.revision':
      return touch((a) => ({ ...a, review_round: e.payload.round }));
    case 'agent.model_switched':
      return touch((a) => ({ ...a, model_override: e.payload.to }));
    case 'run.started':
      return touch((a) => ({ ...a, model_override: null }));
    case 'agent.skills_changed':
      return touch((a) => ({ ...a, active_skills: e.payload.skills }));
    case 'agent.archived':
      return touch((a) => ({ ...a, archived_at: e.ts }));
    case 'tool.call':
      return touch((a) => ({ ...a, activity: `${e.payload.name} · ${summarizeToolArgs(e.payload.arguments)}` }));
    case 'tool.result':
      return touch((a) => ({ ...a, activity: null }));
    case 'plan.updated':
      return { ...s, plan: e.payload.items };
    case 'approval.requested':
      return {
        ...s,
        approvals: [
          ...s.approvals,
          {
            id: e.payload.approval_id,
            project_id: e.project_id,
            agent_id: e.agent_id ?? '',
            run_id: e.payload.run_id,
            tool_call_id: e.payload.tool_call_id,
            tool: e.payload.tool,
            arguments: e.payload.arguments,
            reason: e.payload.reason,
            delegate_to_desk: e.payload.delegate_to_desk,
            status: 'pending',
            resolved_by: null,
            note: null,
            created_at: e.ts,
            resolved_at: null,
          },
        ],
      };
    case 'approval.resolved':
      return { ...s, approvals: s.approvals.filter((a) => a.id !== e.payload.approval_id) };
    default:
      return s;
  }
}

/** Threads to show (not archived), and a one-line label for the roster. */
export const liveThreads = (s: ProjectState) => s.threads.filter((t) => !t.archived_at);
export const threadLabel = (t: ThreadView) => clip(t.title ?? t.id, 60);
```

`packages/client/src/state/chat.ts`:

```ts
import type { AgentMessageKind, EphemeralEvent, StoredEvent, ToolResultStatus } from '@desk/protocol';

export type ToolCallView = { id: string; name: string; arguments: string; status: 'running' | ToolResultStatus; content: string | null };

export type ChatItem =
  | { kind: 'user'; id: string; ts: string; text: string }
  | { kind: 'assistant'; id: string; ts: string; runId: string; text: string; streaming: boolean; interrupted?: boolean }
  | { kind: 'tools'; id: string; ts: string; calls: ToolCallView[] }
  | { kind: 'agent'; id: string; ts: string; fromAgentId: string; fromLabel: string; messageKind: AgentMessageKind; text: string }
  | { kind: 'report'; id: string; ts: string; eventId: number; headline: string; progress: string; needsYou: string[]; results: string[] }
  | { kind: 'question'; id: string; ts: string; eventId: number; question: string; options: string[]; answered: boolean }
  | { kind: 'notice'; id: string; ts: string; level: 'info' | 'warning' | 'error'; code: string; message: string }
  | { kind: 'compacted'; id: string; ts: string };

/** The conversation of one agent (Desk): its messages, streamed replies, tool groups, reports, questions, and project notices. */
export type ChatState = { agentId: string; items: ChatItem[]; lastSeq: number };

export const emptyChat = (agentId: string): ChatState => ({ agentId, items: [], lastSeq: 0 });

const streamId = (runId: string) => `run:${runId}`;

/** Shared by chat and transcript: tool call grouping and streamed text. */
export function pushToolCall<T extends { kind: string; id: string }>(items: T[], call: ToolCallView, ts: string, eventId: number): T[] {
  const last = items.at(-1) as (T & { kind: 'tools'; calls: ToolCallView[] }) | undefined;
  if (last?.kind === 'tools') return [...items.slice(0, -1), { ...last, calls: [...last.calls, call] }];
  return [...items, { kind: 'tools', id: `tools:${eventId}`, ts, calls: [call] } as unknown as T];
}

export function resolveToolCall<T extends { kind: string }>(items: T[], id: string, status: ToolResultStatus, content: string): T[] {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i] as T & { calls?: ToolCallView[] };
    if (it.kind !== 'tools' || !it.calls?.some((c) => c.id === id)) continue;
    const next = items.slice();
    next[i] = { ...it, calls: it.calls.map((c) => (c.id === id ? { ...c, status, content } : c)) } as T;
    return next;
  }
  return items;
}

export function appendDelta<T extends { kind: string; id: string }>(items: T[], runId: string, text: string): T[] {
  const id = streamId(runId);
  const i = items.findIndex((x) => x.id === id);
  if (i < 0) return [...items, { kind: 'assistant', id, ts: '', runId, text, streaming: true } as unknown as T];
  const next = items.slice();
  const cur = next[i] as T & { text: string };
  next[i] = { ...cur, text: cur.text + text };
  return next;
}

export function finalizeRun<T extends { kind: string; id: string }>(items: T[], e: StoredEvent & { type: 'assistant.message' }): T[] {
  const id = streamId(e.payload.run_id);
  const i = items.findIndex((x) => x.id === id);
  const content = e.payload.content?.trim() ? e.payload.content : null;
  const final = content ? ({ kind: 'assistant', id: `e:${e.id}`, ts: e.ts, runId: e.payload.run_id, text: content, streaming: false } as unknown as T) : null;
  if (i < 0) return final ? [...items, final] : items;
  const next = items.slice();
  if (final) next[i] = final;
  else next.splice(i, 1);
  return next;
}

export function interruptRun<T extends { kind: string; id: string }>(items: T[], runId: string): T[] {
  const i = items.findIndex((x) => x.id === streamId(runId));
  if (i < 0) return items;
  const next = items.slice();
  next[i] = { ...(next[i] as T), streaming: false, interrupted: true } as T;
  return next;
}

export function reduceChat(prev: ChatState, e: StoredEvent): ChatState {
  if (e.id <= prev.lastSeq) return prev;
  const s: ChatState = { ...prev, lastSeq: e.id };
  const mine = e.agent_id === s.agentId;
  const id = `e:${e.id}`;
  switch (e.type) {
    case 'message.user':
      if (!mine) return s;
      return {
        ...s,
        items: [...s.items.map((it) => (it.kind === 'question' && !it.answered ? { ...it, answered: true } : it)), { kind: 'user', id, ts: e.ts, text: e.payload.text }],
      };
    case 'assistant.message':
      return mine ? { ...s, items: finalizeRun(s.items, e) } : s;
    case 'run.finished':
      return mine && e.payload.reason === 'error' ? { ...s, items: interruptRun(s.items, e.payload.run_id) } : s;
    case 'tool.call':
      return mine
        ? { ...s, items: pushToolCall(s.items, { id: e.payload.tool_call_id, name: e.payload.name, arguments: e.payload.arguments, status: 'running', content: null }, e.ts, e.id) }
        : s;
    case 'tool.result':
      return mine ? { ...s, items: resolveToolCall(s.items, e.payload.tool_call_id, e.payload.status, e.payload.content) } : s;
    case 'message.agent':
      return mine
        ? { ...s, items: [...s.items, { kind: 'agent', id, ts: e.ts, fromAgentId: e.payload.from_agent_id, fromLabel: e.payload.from_label, messageKind: e.payload.kind, text: e.payload.text }] }
        : s;
    case 'report':
      return mine
        ? { ...s, items: [...s.items, { kind: 'report', id, ts: e.ts, eventId: e.id, headline: e.payload.headline, progress: e.payload.progress, needsYou: e.payload.needs_you, results: e.payload.results }] }
        : s;
    case 'question.asked':
      return mine
        ? { ...s, items: [...s.items, { kind: 'question', id, ts: e.ts, eventId: e.id, question: e.payload.question, options: e.payload.options ?? [], answered: false }] }
        : s;
    case 'system.notice':
      return { ...s, items: [...s.items, { kind: 'notice', id, ts: e.ts, level: e.payload.level, code: e.payload.code, message: e.payload.message }] };
    case 'context.compacted':
      return mine ? { ...s, items: [...s.items, { kind: 'compacted', id, ts: e.ts }] } : s;
    default:
      return s;
  }
}

/** Applies a streamed text chunk (ephemeral `assistant.delta`) for this agent. */
export function applyChatDelta(s: ChatState, e: EphemeralEvent): ChatState {
  if (e.agent_id !== s.agentId) return s;
  return { ...s, items: appendDelta(s.items, e.payload.run_id, e.payload.text) };
}
```

`packages/client/src/index.ts`: add `export * from './state/project';` and `export * from './state/chat';`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run packages/client && pnpm typecheck`
Expected: PASS. If typecheck rejects `{ ...s.project, ...fields, … } as ProjectRow`, keep the cast, because `fields` is a partial patch of the same keys.

- [ ] **Step 5: Commit**

```bash
git add packages/client
git commit -m "feat(client): project and chat reducers with streaming, tool groups and questions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Transcript and timeline reducers

**Files:**
- Create: `packages/client/src/state/transcript.ts`, `packages/client/src/state/timeline.ts`, `packages/client/src/state/transcript.test.ts`, `packages/client/src/state/timeline.test.ts`
- Modify: `packages/client/src/index.ts`

**Interfaces:**
- Consumes: `pushToolCall`, `resolveToolCall`, `appendDelta`, `finalizeRun`, `interruptRun`, `ToolCallView` (Task 4).
- Produces:

```ts
// transcript.ts
export type TranscriptEntry = …; // see code
export type TranscriptState = { agentId: string; entries: TranscriptEntry[]; lastSeq: number };
export function emptyTranscript(agentId: string): TranscriptState;
export function reduceTranscript(s: TranscriptState, e: StoredEvent): TranscriptState;
export function applyTranscriptDelta(s: TranscriptState, e: EphemeralEvent): TranscriptState;
// timeline.ts
export type Station = { eventId: number; ts: string; kind: 'brief' | 'dispatch' | 'result_in' | 'sent_back' | 'report' | 'question'; label: string; threadIds: string[] };
export type LaneMark = { eventId: number; ts: string; kind: 'rejoin' | 'sent_back' | 'detour' | 'signal' | 'signal_cleared' | 'stalled'; label: string };
export type Lane = { threadId: string; title: string; forkedAt: string; status: AgentStatus; archived: boolean; segments: Array<{ from: string; to: string | null; status: AgentStatus }>; marks: LaneMark[] };
export type TimelineState = { deskId: string; start: string | null; stations: Station[]; lanes: Lane[]; approvals: Record<string, string>; lastSeq: number };
export function emptyTimeline(deskId: string): TimelineState;
export function reduceTimeline(s: TimelineState, e: StoredEvent): TimelineState;
```

- [ ] **Step 1: Write the failing tests**

`packages/client/src/state/transcript.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { ev } from '../testing';
import { applyTranscriptDelta, emptyTranscript, reduceTranscript } from './transcript';

describe('reduceTranscript', () => {
  it('narrates a thread: brief, text, tools, detour, compaction, result, revision, steering, approval', () => {
    const t = { agent: 't' };
    const events = [
      ev(1, 'agent.created', { role: 'thread', model: 'm', title: 'Emails', brief: 'Draft five emails', workspace_path: '/w', parent_id: 'd' }, t),
      ev(2, 'agent.status_changed', { status: 'running' }, t),
      ev(3, 'assistant.message', { run_id: 'r1', content: 'Starting from brand-voice.', tool_calls: [] }, t),
      ev(4, 'tool.call', { run_id: 'r1', tool_call_id: 'c1', name: 'read_file', arguments: '{"path":"a"}' }, t),
      ev(5, 'tool.result', { run_id: 'r1', tool_call_id: 'c1', name: 'read_file', status: 'ok', content: 'A' }, t),
      ev(6, 'agent.model_switched', { from: 'opus', to: 'fable', reason: 'rate limited', scope: 'run' }, t),
      ev(7, 'context.compacted', { run_id: 'r1', checkpoint: 'x', up_to: 5, trigger: 'threshold' }, t),
      ev(8, 'agent.result', { summary: 'Five drafts', artifacts: ['emails/01.md'] }, t),
      ev(9, 'agent.status_changed', { status: 'done' }, t),
      ev(10, 'agent.revision', { round: 1, feedback: 'Less salesy' }, t),
      ev(11, 'message.agent', { from_agent_id: 'd', from_label: 'Desk', kind: 'revision', text: 'Less salesy' }, t),
      ev(12, 'message.user', { text: 'Keep email 5 short' }, t),
      ev(13, 'message.agent', { from_agent_id: 'd', from_label: 'Desk', kind: 'note', text: 'FYI' }, t),
      ev(14, 'approval.requested', { approval_id: 'a1', run_id: 'r2', tool_call_id: 'c9', tool: 'bash', arguments: '{"command":"x"}', reason: 'rule 1', delegate_to_desk: false }, t),
      ev(15, 'approval.resolved', { approval_id: 'a1', decision: 'denied', resolved_by: 'user', note: 'no' }, t),
      ev(16, 'tool.result', { run_id: 'r2', tool_call_id: 'c9', name: 'bash', status: 'denied', content: 'Denied: no' }, t),
      ev(17, 'message.user', { text: 'ignored: another agent' }, { agent: 'x' }),
    ];
    const s = events.reduce(reduceTranscript, emptyTranscript('t'));
    expect(s.entries.map((e) => e.kind)).toEqual(['brief', 'status', 'assistant', 'tools', 'detour', 'compacted', 'result', 'status', 'revision', 'steer', 'incoming', 'approval']);
    expect(s.entries.find((e) => e.kind === 'approval')).toMatchObject({ approvalId: 'a1', state: 'denied', resolvedBy: 'user', note: 'no' });
    expect(s.entries.find((e) => e.kind === 'detour')).toMatchObject({ from: 'opus', to: 'fable' });
    expect(s.entries.find((e) => e.kind === 'tools')).toMatchObject({ calls: [{ id: 'c1', status: 'ok' }] });
  });

  it('streams text for the thread and marks interrupted tool calls', () => {
    let s = emptyTranscript('t');
    s = applyTranscriptDelta(s, { type: 'assistant.delta', project_id: 'p', agent_id: 't', payload: { run_id: 'r', text: 'Rewri' } });
    s = applyTranscriptDelta(s, { type: 'assistant.delta', project_id: 'p', agent_id: 't', payload: { run_id: 'r', text: 'ting' } });
    expect(s.entries).toEqual([expect.objectContaining({ kind: 'assistant', text: 'Rewriting', streaming: true })]);
    s = reduceTranscript(s, ev(1, 'tool.call', { run_id: 'r', tool_call_id: 'c', name: 'bash', arguments: '{}' }, { agent: 't' }));
    s = reduceTranscript(s, ev(2, 'tool.result', { run_id: 'r', tool_call_id: 'c', name: 'bash', status: 'interrupted', content: 'Interrupted by a daemon restart' }, { agent: 't' }));
    expect(s.entries.at(-1)).toMatchObject({ kind: 'tools', calls: [{ status: 'interrupted' }] });
  });
});
```

`packages/client/src/state/timeline.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { ev } from '../testing';
import { emptyTimeline, reduceTimeline } from './timeline';

describe('reduceTimeline', () => {
  it('draws Desk stations and thread lanes with forks, rejoins, a revision loop, a detour and a signal', () => {
    const d = { agent: 'd' };
    const thread = (id: number, t: string, title: string) => ev(id, 'agent.created', { role: 'thread', model: 'm', title, brief: 'b', workspace_path: '/w', parent_id: 'd' }, { agent: t });
    const events = [
      ev(1, 'message.user', { text: 'Relaunch onboarding next month' }, d),
      thread(2, 'a', 'Research'),
      thread(3, 'b', 'Emails'),
      thread(4, 'c', 'Checklist'),
      ev(5, 'agent.status_changed', { status: 'running' }, { agent: 'a' }),
      ev(6, 'agent.status_changed', { status: 'running' }, { agent: 'b' }),
      ev(7, 'agent.model_switched', { from: 'opus', to: 'fable', reason: 'rate', scope: 'run' }, { agent: 'b' }),
      ev(8, 'agent.result', { summary: 'Summary of three flows', artifacts: [] }, { agent: 'a' }),
      ev(9, 'agent.status_changed', { status: 'done' }, { agent: 'a' }),
      ev(10, 'agent.result', { summary: 'Five drafts', artifacts: [] }, { agent: 'b' }),
      ev(11, 'agent.status_changed', { status: 'done' }, { agent: 'b' }),
      ev(12, 'agent.revision', { round: 1, feedback: 'Less salesy' }, { agent: 'b' }),
      ev(13, 'agent.status_changed', { status: 'running' }, { agent: 'b' }),
      ev(14, 'approval.requested', { approval_id: 'x', run_id: 'r', tool_call_id: 'c', tool: 'bash', arguments: '{}', reason: 'r', delegate_to_desk: false }, { agent: 'c' }),
      ev(15, 'report', { headline: 'Research is in', progress: '', needs_you: [], results: [] }, d),
      ev(16, 'question.asked', { question: 'Data source first?' }, d),
    ];
    const s = events.reduce(reduceTimeline, emptyTimeline('d'));
    expect(s.start).toBe(events[0]!.ts);
    expect(s.stations.map((x) => [x.kind, x.label])).toEqual([
      ['brief', 'Relaunch onboarding next month'],
      ['dispatch', '3 threads'],
      ['result_in', 'Research'],
      ['result_in', 'Emails'],
      ['sent_back', 'Emails · round 1'],
      ['report', 'Research is in'],
      ['question', 'Data source first?'],
    ]);
    expect(s.stations[1]!.threadIds).toEqual(['a', 'b', 'c']);
    const emails = s.lanes.find((l) => l.threadId === 'b')!;
    expect(emails.marks.map((m) => m.kind)).toEqual(['detour', 'rejoin', 'sent_back']);
    expect(emails.segments.map((x) => x.status)).toEqual(['idle', 'running', 'done', 'running']);
    expect(emails.segments.at(-1)!.to).toBeNull();
    expect(s.lanes.find((l) => l.threadId === 'c')!.marks).toEqual([expect.objectContaining({ kind: 'signal', label: 'bash' })]);

    const s2 = reduceTimeline(s, ev(17, 'approval.resolved', { approval_id: 'x', decision: 'approved', resolved_by: 'user' }, { agent: 'c' }));
    expect(s2.lanes.find((l) => l.threadId === 'c')!.marks.map((m) => m.kind)).toEqual(['signal', 'signal_cleared']);
    const s3 = reduceTimeline(s2, ev(18, 'message.agent', { from_agent_id: 'c', from_label: 'l', kind: 'stalled', text: 'No activity' }, d));
    expect(s3.lanes.find((l) => l.threadId === 'c')!.marks.at(-1)!.kind).toBe('stalled');
    expect(reduceTimeline(s3, ev(18, 'agent.archived', {}, { agent: 'a' }))).toBe(s3);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run packages/client/src/state`
Expected: FAIL: modules are missing.

- [ ] **Step 3: Implement**

`packages/client/src/state/transcript.ts`:

```ts
import type { AgentMessageKind, AgentStatus, EphemeralEvent, StoredEvent } from '@desk/protocol';
import { appendDelta, finalizeRun, interruptRun, pushToolCall, resolveToolCall, type ToolCallView } from './chat';

export type TranscriptEntry =
  | { kind: 'brief'; id: string; ts: string; text: string }
  | { kind: 'status'; id: string; ts: string; status: AgentStatus; reason: string | null }
  | { kind: 'assistant'; id: string; ts: string; runId: string; text: string; streaming: boolean; interrupted?: boolean }
  | { kind: 'tools'; id: string; ts: string; calls: ToolCallView[] }
  | { kind: 'detour'; id: string; ts: string; from: string; to: string; reason: string }
  | { kind: 'compacted'; id: string; ts: string }
  | { kind: 'result'; id: string; ts: string; summary: string; artifacts: string[] }
  | { kind: 'revision'; id: string; ts: string; round: number; feedback: string }
  | { kind: 'steer'; id: string; ts: string; text: string }
  | { kind: 'incoming'; id: string; ts: string; fromAgentId: string; fromLabel: string; messageKind: AgentMessageKind; text: string }
  | { kind: 'approval'; id: string; ts: string; approvalId: string; tool: string; arguments: string; reason: string; state: 'pending' | 'approved' | 'denied'; resolvedBy: string | null; note: string | null };

/** A thread's transcript as a narrative. Every-step detail lives in the tool groups' calls. */
export type TranscriptState = { agentId: string; entries: TranscriptEntry[]; lastSeq: number };

export const emptyTranscript = (agentId: string): TranscriptState => ({ agentId, entries: [], lastSeq: 0 });

export function reduceTranscript(prev: TranscriptState, e: StoredEvent): TranscriptState {
  if (e.id <= prev.lastSeq) return prev;
  const s: TranscriptState = { ...prev, lastSeq: e.id };
  if (e.agent_id !== s.agentId) return s;
  const id = `e:${e.id}`;
  const push = (entry: TranscriptEntry) => ({ ...s, entries: [...s.entries, entry] });
  switch (e.type) {
    case 'agent.created':
      return e.payload.brief ? push({ kind: 'brief', id, ts: e.ts, text: e.payload.brief }) : s;
    case 'agent.status_changed':
      return push({ kind: 'status', id, ts: e.ts, status: e.payload.status, reason: e.payload.reason ?? null });
    case 'assistant.message':
      return { ...s, entries: finalizeRun(s.entries, e) };
    case 'run.finished':
      return e.payload.reason === 'error' ? { ...s, entries: interruptRun(s.entries, e.payload.run_id) } : s;
    case 'tool.call':
      return { ...s, entries: pushToolCall(s.entries, { id: e.payload.tool_call_id, name: e.payload.name, arguments: e.payload.arguments, status: 'running', content: null }, e.ts, e.id) };
    case 'tool.result':
      return { ...s, entries: resolveToolCall(s.entries, e.payload.tool_call_id, e.payload.status, e.payload.content) };
    case 'agent.model_switched':
      return push({ kind: 'detour', id, ts: e.ts, from: e.payload.from, to: e.payload.to, reason: e.payload.reason });
    case 'context.compacted':
      return push({ kind: 'compacted', id, ts: e.ts });
    case 'agent.result':
      return push({ kind: 'result', id, ts: e.ts, summary: e.payload.summary, artifacts: e.payload.artifacts });
    case 'agent.revision':
      return push({ kind: 'revision', id, ts: e.ts, round: e.payload.round, feedback: e.payload.feedback });
    case 'message.user':
      return push({ kind: 'steer', id, ts: e.ts, text: e.payload.text });
    case 'message.agent':
      // Revisions arrive as both agent.revision and a message; the revision entry already shows the feedback.
      return e.payload.kind === 'revision'
        ? s
        : push({ kind: 'incoming', id, ts: e.ts, fromAgentId: e.payload.from_agent_id, fromLabel: e.payload.from_label, messageKind: e.payload.kind, text: e.payload.text });
    case 'approval.requested':
      return push({ kind: 'approval', id, ts: e.ts, approvalId: e.payload.approval_id, tool: e.payload.tool, arguments: e.payload.arguments, reason: e.payload.reason, state: 'pending', resolvedBy: null, note: null });
    case 'approval.resolved':
      return {
        ...s,
        entries: s.entries.map((x) =>
          x.kind === 'approval' && x.approvalId === e.payload.approval_id ? { ...x, state: e.payload.decision, resolvedBy: e.payload.resolved_by, note: e.payload.note ?? null } : x,
        ),
      };
    default:
      return s;
  }
}

export function applyTranscriptDelta(s: TranscriptState, e: EphemeralEvent): TranscriptState {
  if (e.agent_id !== s.agentId) return s;
  return { ...s, entries: appendDelta(s.entries, e.payload.run_id, e.payload.text) };
}
```

`packages/client/src/state/timeline.ts`:

```ts
import { clip, type AgentStatus, type StoredEvent } from '@desk/protocol';

export type Station = {
  eventId: number;
  ts: string;
  kind: 'brief' | 'dispatch' | 'result_in' | 'sent_back' | 'report' | 'question';
  label: string;
  threadIds: string[];
};
export type LaneMark = { eventId: number; ts: string; kind: 'rejoin' | 'sent_back' | 'detour' | 'signal' | 'signal_cleared' | 'stalled'; label: string };
export type Lane = {
  threadId: string;
  title: string;
  forkedAt: string;
  status: AgentStatus;
  archived: boolean;
  segments: Array<{ from: string; to: string | null; status: AgentStatus }>;
  marks: LaneMark[];
};

/** Time-positioned data for the conversation's line diagram: Desk's trunk stations and one lane per thread. */
export type TimelineState = { deskId: string; start: string | null; stations: Station[]; lanes: Lane[]; approvals: Record<string, string>; lastSeq: number };

export const emptyTimeline = (deskId: string): TimelineState => ({ deskId, start: null, stations: [], lanes: [], approvals: {}, lastSeq: 0 });

/** Dispatches closer together than this merge into one station. */
const DISPATCH_WINDOW_MS = 2 * 60_000;

function withLane(s: TimelineState, threadId: string | null, fn: (l: Lane) => Lane): TimelineState {
  const i = s.lanes.findIndex((l) => l.threadId === threadId);
  if (i < 0) return s;
  const lanes = s.lanes.slice();
  lanes[i] = fn(lanes[i]!);
  return { ...s, lanes };
}

const mark = (e: StoredEvent, kind: LaneMark['kind'], label: string): LaneMark => ({ eventId: e.id, ts: e.ts, kind, label });

export function reduceTimeline(prev: TimelineState, e: StoredEvent): TimelineState {
  if (e.id <= prev.lastSeq) return prev;
  let s: TimelineState = { ...prev, lastSeq: e.id, start: prev.start ?? e.ts };
  const station = (kind: Station['kind'], label: string, threadIds: string[] = []) => ({ ...s, stations: [...s.stations, { eventId: e.id, ts: e.ts, kind, label, threadIds }] });
  const titleOf = (id: string | null) => s.lanes.find((l) => l.threadId === id)?.title ?? 'Thread';
  switch (e.type) {
    case 'message.user':
      return e.agent_id === s.deskId ? station('brief', clip(e.payload.text, 48)) : s;
    case 'agent.created': {
      if (e.payload.role !== 'thread' || e.payload.parent_id !== s.deskId || !e.agent_id) return s;
      s = { ...s, lanes: [...s.lanes, { threadId: e.agent_id, title: e.payload.title ?? 'Thread', forkedAt: e.ts, status: 'idle', archived: false, segments: [{ from: e.ts, to: null, status: 'idle' }], marks: [] }] };
      const last = s.stations.at(-1);
      if (last?.kind === 'dispatch' && Date.parse(e.ts) - Date.parse(last.ts) <= DISPATCH_WINDOW_MS) {
        const threadIds = [...last.threadIds, e.agent_id];
        return { ...s, stations: [...s.stations.slice(0, -1), { ...last, threadIds, label: `${threadIds.length} threads` }] };
      }
      return station('dispatch', '1 thread', [e.agent_id]);
    }
    case 'agent.status_changed':
      return withLane(s, e.agent_id, (l) => {
        if (l.status === e.payload.status) return l;
        const segments = l.segments.map((seg, i) => (i === l.segments.length - 1 ? { ...seg, to: e.ts } : seg));
        return { ...l, status: e.payload.status, segments: [...segments, { from: e.ts, to: null, status: e.payload.status }] };
      });
    case 'agent.result': {
      const title = titleOf(e.agent_id);
      s = withLane(s, e.agent_id, (l) => ({ ...l, marks: [...l.marks, mark(e, 'rejoin', clip(e.payload.summary, 60))] }));
      return s.lanes.some((l) => l.threadId === e.agent_id) ? station('result_in', title, [e.agent_id!]) : s;
    }
    case 'agent.revision': {
      const title = titleOf(e.agent_id);
      s = withLane(s, e.agent_id, (l) => ({ ...l, marks: [...l.marks, mark(e, 'sent_back', `round ${e.payload.round}`)] }));
      return s.lanes.some((l) => l.threadId === e.agent_id) ? station('sent_back', `${title} · round ${e.payload.round}`, [e.agent_id!]) : s;
    }
    case 'agent.model_switched':
      return withLane(s, e.agent_id, (l) => ({ ...l, marks: [...l.marks, mark(e, 'detour', e.payload.to)] }));
    case 'approval.requested':
      if (e.payload.delegate_to_desk || !s.lanes.some((l) => l.threadId === e.agent_id)) return s;
      s = { ...s, approvals: { ...s.approvals, [e.payload.approval_id]: e.agent_id! } };
      return withLane(s, e.agent_id, (l) => ({ ...l, marks: [...l.marks, mark(e, 'signal', e.payload.tool)] }));
    case 'approval.resolved': {
      const lane = s.approvals[e.payload.approval_id];
      return lane ? withLane(s, lane, (l) => ({ ...l, marks: [...l.marks, mark(e, 'signal_cleared', e.payload.decision)] })) : s;
    }
    case 'message.agent':
      return e.payload.kind === 'stalled' ? withLane(s, e.payload.from_agent_id, (l) => ({ ...l, marks: [...l.marks, mark(e, 'stalled', 'stalled')] })) : s;
    case 'agent.archived':
      return withLane(s, e.agent_id, (l) => ({ ...l, archived: true }));
    case 'report':
      return e.agent_id === s.deskId ? station('report', clip(e.payload.headline, 60)) : s;
    case 'question.asked':
      return e.agent_id === s.deskId ? station('question', clip(e.payload.question, 60)) : s;
    default:
      return s;
  }
}
```

`packages/client/src/index.ts`: add `export * from './state/transcript';` and `export * from './state/timeline';`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run packages/client && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/client
git commit -m "feat(client): transcript and line-diagram timeline reducers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: System, attention and skill-graph helpers

**Files:**
- Create: `packages/client/src/state/system.ts`, `packages/client/src/state/attention.ts`, `packages/client/src/state/skills.ts`, and the matching `*.test.ts` files
- Modify: `packages/client/src/index.ts`

**Interfaces:**
- Produces:

```ts
// system.ts
export type Notice = { eventId: number; ts: string; projectId: string; level: 'info' | 'warning' | 'error'; code: string; message: string };
export type SystemState = { proxy: 'up' | 'down' | 'unknown'; notices: Notice[]; lastSeq: number };
export function systemFromHealth(h: HealthResponse): SystemState;
export function reduceSystem(s: SystemState, e: StoredEvent): SystemState; // keeps the last 200 notices
// attention.ts
export type AttentionBays = { clearance: AttentionItem[]; queries: AttentionItem[]; handoffs: AttentionItem[]; holding: AttentionItem[] };
export function groupAttention(items: AttentionItem[]): AttentionBays;
export function affectsAttention(e: StoredEvent): boolean;
export function affectsOverview(e: StoredEvent): boolean;
// skills.ts
export type SkillNode = { key: string; name: string; scope: 'global' | 'project'; projectId: string | null; projectName: string | null; version: number; description: string; error?: string; shadowedIn: string[]; shadows: boolean; usedBy: Array<{ threadId: string; title: string | null; status: AgentStatus; projectId: string }> };
export function buildSkillGraph(input: { global: SkillSummary[]; projects: Array<{ id: string; name: string; skills: SkillSummary[] }>; threads: Array<{ id: string; title: string | null; status: AgentStatus; projectId: string; skills: string[] }> }): SkillNode[];
```

- [ ] **Step 1: Write the failing tests**

`packages/client/src/state/system.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { ev } from '../testing';
import { reduceSystem, systemFromHealth } from './system';

describe('reduceSystem', () => {
  it('tracks proxy state and keeps a bounded notice log', () => {
    let s = systemFromHealth({ version: '1', protocol_version: 1, proxy: 'up', uptime_s: 3 });
    expect(s.proxy).toBe('up');
    s = reduceSystem(s, ev(1, 'system.notice', { level: 'error', code: 'proxy_down', message: 'paused' }));
    expect(s.proxy).toBe('down');
    s = reduceSystem(s, ev(2, 'system.notice', { level: 'info', code: 'proxy_up', message: 'back' }));
    expect(s.proxy).toBe('up');
    s = reduceSystem(s, ev(3, 'message.user', { text: 'x' }));
    expect(s.notices.map((n) => n.code)).toEqual(['proxy_down', 'proxy_up']);
    for (let i = 4; i < 300; i++) s = reduceSystem(s, ev(i, 'system.notice', { level: 'info', code: 'n', message: String(i) }));
    expect(s.notices).toHaveLength(200);
    expect(s.notices.at(-1)!.message).toBe('299');
    expect(systemFromHealth({ version: '1', protocol_version: 1 }).proxy).toBe('unknown');
  });
});
```

`packages/client/src/state/attention.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import type { AttentionItem } from '@desk/protocol';
import { ev } from '../testing';
import { affectsAttention, affectsOverview, groupAttention } from './attention';

const item = (kind: AttentionItem['kind'], id: string): AttentionItem => ({ id, kind, project_id: 'p', project_name: 'P', agent_id: null, title: id, detail: '', created_at: '', ref: {} });

describe('attention helpers', () => {
  it('groups items into the four bays', () => {
    const bays = groupAttention([item('approval', 'a'), item('question', 'q'), item('needs_you', 'n'), item('stalled', 's'), item('failed', 'f')]);
    expect(Object.fromEntries(Object.entries(bays).map(([k, v]) => [k, v.map((i) => i.id)]))).toEqual({ clearance: ['a'], queries: ['q'], handoffs: ['n'], holding: ['s', 'f'] });
  });

  it('knows which events change attention and the overview', () => {
    expect(affectsAttention(ev(1, 'approval.requested', { approval_id: 'a', run_id: 'r', tool_call_id: 'c', tool: 'bash', arguments: '{}', reason: 'r', delegate_to_desk: false }))).toBe(true);
    expect(affectsAttention(ev(2, 'usage', { run_id: 'r', model: 'm', prompt_tokens: 1, completion_tokens: 1, estimated: false }))).toBe(false);
    expect(affectsOverview(ev(3, 'tool.call', { run_id: 'r', tool_call_id: 'c', name: 'bash', arguments: '{}' }))).toBe(true);
    expect(affectsOverview(ev(4, 'usage', { run_id: 'r', model: 'm', prompt_tokens: 1, completion_tokens: 1, estimated: false }))).toBe(false);
  });
});
```

`packages/client/src/state/skills.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import type { SkillSummary } from '../types';
import { buildSkillGraph } from './skills';

const sk = (name: string, scope: 'global' | 'project', version = 1): SkillSummary => ({ name, scope, description: `${name} desc`, dir: `/s/${name}`, version });

describe('buildSkillGraph', () => {
  it('links skills to projects, marks shadowing, and resolves thread usage like agents do', () => {
    const nodes = buildSkillGraph({
      global: [sk('email-sequence', 'global', 4), sk('brand-voice', 'global')],
      projects: [
        { id: 'p1', name: 'Onboarding', skills: [sk('brand-voice', 'project', 3), sk('email-sequence', 'global', 4)] },
        { id: 'p2', name: 'Tax', skills: [] },
      ],
      threads: [
        { id: 't1', title: 'Emails', status: 'running', projectId: 'p1', skills: ['brand-voice', 'email-sequence'] },
        { id: 't2', title: 'Old', status: 'done', projectId: 'p2', skills: ['brand-voice'] },
      ],
    });
    const by = (key: string) => nodes.find((n) => n.key === key)!;
    expect(nodes.map((n) => n.key).sort()).toEqual(['global:brand-voice', 'global:email-sequence', 'project:p1:brand-voice']);
    expect(by('global:brand-voice')).toMatchObject({ shadowedIn: ['p1'], usedBy: [{ threadId: 't2' }] });
    expect(by('project:p1:brand-voice')).toMatchObject({ shadows: true, projectName: 'Onboarding', version: 3, usedBy: [{ threadId: 't1', status: 'running' }] });
    expect(by('global:email-sequence').usedBy.map((u) => u.threadId)).toEqual(['t1']);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run packages/client/src/state`
Expected: FAIL: modules are missing.

- [ ] **Step 3: Implement**

`packages/client/src/state/system.ts`:

```ts
import type { HealthResponse, StoredEvent } from '@desk/protocol';

export type Notice = { eventId: number; ts: string; projectId: string; level: 'info' | 'warning' | 'error'; code: string; message: string };
export type SystemState = { proxy: 'up' | 'down' | 'unknown'; notices: Notice[]; lastSeq: number };

const MAX_NOTICES = 200;

export const systemFromHealth = (h: HealthResponse): SystemState => ({ proxy: h.proxy ?? 'unknown', notices: [], lastSeq: 0 });

export function reduceSystem(prev: SystemState, e: StoredEvent): SystemState {
  if (e.id <= prev.lastSeq) return prev;
  if (e.type !== 'system.notice') return { ...prev, lastSeq: e.id };
  const notice: Notice = { eventId: e.id, ts: e.ts, projectId: e.project_id, level: e.payload.level, code: e.payload.code, message: e.payload.message };
  const proxy = e.payload.code === 'proxy_down' ? 'down' : e.payload.code === 'proxy_up' ? 'up' : prev.proxy;
  return { proxy, notices: [...prev.notices, notice].slice(-MAX_NOTICES), lastSeq: e.id };
}
```

`packages/client/src/state/attention.ts`:

```ts
import type { AttentionItem, EventType, StoredEvent } from '@desk/protocol';

/** The four bays of the attention strip rack. */
export type AttentionBays = { clearance: AttentionItem[]; queries: AttentionItem[]; handoffs: AttentionItem[]; holding: AttentionItem[] };

export function groupAttention(items: AttentionItem[]): AttentionBays {
  const bays: AttentionBays = { clearance: [], queries: [], handoffs: [], holding: [] };
  for (const i of items) {
    if (i.kind === 'approval') bays.clearance.push(i);
    else if (i.kind === 'question') bays.queries.push(i);
    else if (i.kind === 'needs_you') bays.handoffs.push(i);
    else bays.holding.push(i);
  }
  return bays;
}

const ATTENTION_TYPES = new Set<EventType>([
  'approval.requested',
  'approval.resolved',
  'question.asked',
  'message.user',
  'report',
  'message.agent',
  'agent.status_changed',
  'agent.archived',
  'attention.dismissed',
  'project.created',
  'project.archived',
  'tool.call',
]);

const OVERVIEW_TYPES = new Set<EventType>([
  ...ATTENTION_TYPES,
  'project.updated',
  'agent.created',
  'agent.skills_changed',
  'agent.revision',
  'plan.updated',
  'tool.result',
]);

/** Whether an event can change the attention list (the broker refetches, debounced). */
export const affectsAttention = (e: StoredEvent): boolean => ATTENTION_TYPES.has(e.type);
/** Whether an event can change the cross-project overview. */
export const affectsOverview = (e: StoredEvent): boolean => OVERVIEW_TYPES.has(e.type);
```

`packages/client/src/state/skills.ts`:

```ts
import type { AgentStatus } from '@desk/protocol';
import type { SkillSummary } from '../types';

export type SkillUse = { threadId: string; title: string | null; status: AgentStatus; projectId: string };
export type SkillNode = {
  key: string;
  name: string;
  scope: 'global' | 'project';
  projectId: string | null;
  projectName: string | null;
  version: number;
  description: string;
  error?: string;
  /** For a global skill: projects that have their own skill of the same name. */
  shadowedIn: string[];
  /** For a project skill: whether a global skill of the same name exists (and is shadowed here). */
  shadows: boolean;
  usedBy: SkillUse[];
};

/**
 * Nodes for the skills map. `projects[].skills` is the project's resolved list (as returned by
 * GET /projects/:id/skills); only its project-scope entries become project nodes.
 */
export function buildSkillGraph(input: {
  global: SkillSummary[];
  projects: Array<{ id: string; name: string; skills: SkillSummary[] }>;
  threads: Array<{ id: string; title: string | null; status: AgentStatus; projectId: string; skills: string[] }>;
}): SkillNode[] {
  const globals = new Map(input.global.map((g) => [g.name, g]));
  const nodes = new Map<string, SkillNode>();
  const base = (s: SkillSummary) => ({ name: s.name, version: s.version, description: s.description, ...(s.error ? { error: s.error } : {}), usedBy: [] as SkillUse[] });
  for (const g of input.global) nodes.set(`global:${g.name}`, { key: `global:${g.name}`, scope: 'global', projectId: null, projectName: null, shadowedIn: [], shadows: false, ...base(g) });
  const projectSkills = new Map<string, Set<string>>();
  for (const p of input.projects) {
    const own = p.skills.filter((s) => s.scope === 'project');
    projectSkills.set(p.id, new Set(own.map((s) => s.name)));
    for (const s of own) {
      const key = `project:${p.id}:${s.name}`;
      nodes.set(key, { key, scope: 'project', projectId: p.id, projectName: p.name, shadowedIn: [], shadows: globals.has(s.name), ...base(s) });
      nodes.get(`global:${s.name}`)?.shadowedIn.push(p.id);
    }
  }
  for (const t of input.threads) {
    for (const name of t.skills) {
      const key = projectSkills.get(t.projectId)?.has(name) ? `project:${t.projectId}:${name}` : `global:${name}`;
      nodes.get(key)?.usedBy.push({ threadId: t.id, title: t.title, status: t.status, projectId: t.projectId });
    }
  }
  return [...nodes.values()];
}
```

`packages/client/src/index.ts`: add `export * from './state/system';`, `export * from './state/attention';` and `export * from './state/skills';`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run packages/client && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/client
git commit -m "feat(client): system reducer, attention bays and refetch triggers, skill graph

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: End-to-end fold against a real daemon, and CLI migration

**Files:**
- Create: `packages/client/src/e2e.test.ts`
- Modify: `apps/cli/src/client.ts`, `apps/cli/src/commands.ts`, `apps/cli/src/index.ts`, `apps/cli/package.json`, `CLAUDE.md`

**Interfaces:**
- Consumes: everything above.
- Produces: the CLI now uses `@desk/client` (`DeskClient`, `ApiError`) and `@desk/client/node` (`clientFromDataDir`, `readDaemonInfo`).

- [ ] **Step 1: Write the failing test** — `packages/client/src/e2e.test.ts`:

```ts
import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools } from '@desk/fake-model';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '@desk/core/testing';
import { createApp, startServer, type RunningServer } from '@desk/daemon';
import { DeskClient, DeskStream, applyChatDelta, emptyChat, emptyTimeline, projectFromOverview, reduceChat, reduceProject, reduceTimeline } from './index';

let h: Harness;
let server: RunningServer | undefined;
afterEach(async () => {
  await server?.close();
  server = undefined;
  await h?.cleanup();
});

describe('folding a real Desk turn', () => {
  it('produces the chat, roster and line diagram a UI would show', async () => {
    h = await createHarness({
      script: (req) =>
        req.model === FAKE_MODEL.id
          ? text('thread idle')
          : req.messages.some((m) => m.role === 'tool')
            ? text('Dispatched one scout.')
            : tools(call('spawn_thread', { title: 'Scout', brief: 'look around' })),
    });
    const runtime = newRuntime(h);
    server = await startServer({ app: createApp({ runtime, store: h.store, models: h.models, token: 't', version: 'v' }), store: h.store, token: 't', port: 0 });
    const client = new DeskClient({ baseUrl: `http://127.0.0.1:${server.port}`, token: 't' });
    const created = await client.projects.create({ name: 'P', goal: 'g', settings: { thread_model: FAKE_MODEL.id } });
    let project = projectFromOverview(await client.projects.get(created.project.id));
    let chat = emptyChat(project.desk!.id);
    let timeline = emptyTimeline(project.desk!.id);
    const stream = new DeskStream({
      credentials: () => client.credentials(),
      projectId: project.project.id,
      afterSeq: 0,
      onEvent: (e) => {
        project = reduceProject(project, e);
        chat = reduceChat(chat, e);
        timeline = reduceTimeline(timeline, e);
      },
      onEphemeral: (e) => (chat = applyChatDelta(chat, e)),
    });
    stream.start();
    await client.projects.send(project.project.id, 'Look around');
    const end = Date.now() + 5000;
    while (!chat.items.some((i) => i.kind === 'assistant' && i.text === 'Dispatched one scout.')) {
      if (Date.now() > end) throw new Error(`timed out; chat: ${JSON.stringify(chat.items)}`);
      await new Promise((r) => setTimeout(r, 20));
    }
    expect(chat.items[0]).toMatchObject({ kind: 'user', text: 'Look around' });
    expect(chat.items.some((i) => i.kind === 'tools' && i.calls[0]!.name === 'spawn_thread')).toBe(true);
    expect(project.threads.map((t) => t.title)).toEqual(['Scout']);
    expect(timeline.stations.map((s) => s.kind).slice(0, 2)).toEqual(['brief', 'dispatch']);
    expect(timeline.lanes.map((l) => l.title)).toEqual(['Scout']);
    stream.close();
    await runtime.shutdown();
  });
});
```

- [ ] **Step 2: Run the new test**

Run: `pnpm vitest run packages/client/src/e2e.test.ts`
Expected: PASS already, because every piece exists. If it fails, the failure is a real integration bug in Tasks 2–5: fix the reducer or the client, not the test.

- [ ] **Step 3: Migrate the CLI**

Replace `apps/cli/src/client.ts` with:

```ts
export { ApiError, DaemonNotRunning, DeskClient } from '@desk/client';
export { clientFromDataDir, readDaemonInfo, type DaemonInfo } from '@desk/client/node';
```

In `apps/cli/src/commands.ts`:
- Import `{ ApiError, clientFromDataDir, DeskClient, readDaemonInfo } from './client'`.
- Replace `DeskClient.fromDataDir(dataDir)` with `clientFromDataDir(dataDir)`.
- Replace `new DeskClient(\`http://127.0.0.1:${info.port}\`, info.token)` with `new DeskClient({ baseUrl: \`http://127.0.0.1:${info.port}\`, token: info.token })`.

In `apps/cli/src/index.ts`, keep the same exported names (`DeskClient`, `ApiError`, `readDaemonInfo`) from `./client`.

In `apps/cli/package.json`, add `"@desk/client": "workspace:*"` to `dependencies`, then run `pnpm install --offline`.

`chat.ts` only imports the `DeskClient` type and calls `get/post/stream`, which keep their signatures.

- [ ] **Step 4: Update `CLAUDE.md`**

Add this bullet to Layout after `packages/protocol`:

```
- `packages/client`: `@desk/client`, the environment-neutral client used by the CLI and the desktop app.
  - `client.ts`: typed REST (`DeskClient`); `stream.ts`: `DeskStream` (resume, dedupe, reconnect).
  - `state/*`: pure reducers (event → UI state): project, chat, transcript, timeline, system; attention and skill-graph helpers.
  - `node.ts` (`@desk/client/node`): data-dir discovery and `daemon.json`. Keep `node:*` imports out of every other file.
```

- [ ] **Step 5: Verify and commit**

Run: `pnpm test && pnpm typecheck`
Expected: PASS, with the CLI tests unchanged and green.

```bash
git add packages/client apps/cli CLAUDE.md pnpm-lock.yaml
git commit -m "feat(client): end-to-end fold test; CLI now uses @desk/client

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage:**
  - §2 layout (client, discover, stream, state): Tasks 1–6.
  - §3 data flow:
    - `hello`: Task 3.
    - Resume and dedupe: Task 3.
    - Delta buffering: Tasks 4 and 5.
    - Refetch triggers: Task 6.
  - §6 reducers table: project, chat and timeline are Tasks 4–5; transcript is Task 5; attention and skills are Task 6; system is Task 6.
  - §6 tricky cases:

    | Case | Covered by |
    |---|---|
    | Revision loop | timeline and transcript tests |
    | Compaction | chat and transcript |
    | Fallback detour | project, transcript and timeline |
    | `queued` after restart | project status handling |
    | `interrupted` tool result | transcript |
    | Replay dedupe | project and stream |
    | Delta cut off by error | chat |
    | Approval resolved (by anyone) | project and transcript |

  - §9 error handling: `ApiError` mapping, refresh on 401, `DaemonUnavailable` and close code 4401 (Tasks 2–3).
- **Types:** `ThreadView`, `ToolCallView`, `ChatItem`, `TranscriptEntry`, `Lane`, `Station`, `SkillNode` and `AttentionBays` are defined once and imported by name.
