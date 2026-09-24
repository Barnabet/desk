# Plan 10 · Core screens — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The four core screens of design C, wired to deskd: the orbit **Map**, the **Conversation** (a transit line diagram above the chat, with the plan route), **Threads** (roster, a route with numbered stops synced to the transcript, and the Result/Diff/Files/Skill drafts/Usage tabs), and **Attention** (a flight-strip rack plus an inspector). Plan 10 also adds the project switcher (⌘P), unread markers and the menu-bar popover (a mini line diagram, plus strips with approve and deny in place).

**Architecture:**
- The renderer gets a per-project **session** (`state/session.ts`). It loads `projects.get`, asks the broker to watch the project from seq 0 (which backfills the whole log, then goes live), and folds every event through the `@desk/client` reducers: project, chat and timeline, plus transcripts on demand. Events are applied in micro-batches, and `assistant.delta` updates a per-agent stream overlay.
- Screens are React components over that session and the global store.
- Pure geometry lives in plain modules that are tested on their own: `map/layout.ts`, `conversation/lineGeometry.ts`, `threads/route.ts` and `tray/miniLine.ts`.
- Visual source: canvas row C (`OrbitHome`, `OrbitMain`, `OrbitThread`, `OrbitAttention`, `OrbitMenuBar`).

**Tech Stack:** React 19, the Plan 9 renderer foundation, `@desk/client` reducers, Vitest with jsdom and Testing Library, and Playwright for Electron.

**Spec:** `docs/superpowers/specs/2026-09-24-desk-desktop-app-design.md` §5, §6, §7 (Shell, items 1–4 and 11, Resilience, Safety), §9, §10.

## Global Constraints

- `pnpm test` and `pnpm typecheck` must pass before every commit. `pnpm test:e2e` must pass at the end of the plan.
- **No optimistic agent state.** A write shows a pending control and the event stream confirms it. A local "sending…" ghost for your own message is allowed; it is removed when the `message.user` event arrives.
- Store selectors must return stable references, such as fields or the store's own arrays. Derive filtered lists with `useMemo`; a selector that returns a new array on every call loops forever in `useSyncExternalStore`.
- Agent text is rendered only through `SafeMarkdown` (or as plain text). Links go through `ExternalLink`.
- Every map has a List alternative. Every control is a real button, link or input, with a label on icon-only buttons.
- Design C tokens as in Plan 9. The screens scale from 1100×700; the Conversation's plan panel becomes a toggle below 1280px.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Work on the `desktop-app` branch.

## File map

| File | Responsibility |
|---|---|
| `renderer/format.ts` | `clock`, `duration`, `ago`, `bytes`, `plural` |
| `renderer/state/now.ts` | `useNow()`, a shared 15-second clock |
| `renderer/state/unread.ts` | last-seen per project; `useUnread`, `markSeen` |
| `renderer/state/session.ts` | project session: load, watch, fold, stream overlay; `useSession`, `useTranscript` |
| `renderer/map/*` | `layout.ts` (pure), `MapCanvas` (pan and zoom), `OrbitMap`, `TerritoryInspector`, `ProjectList`, `MapScreen` |
| `renderer/conversation/*` | `lineGeometry.ts` (pure), `LineDiagram`, `ChatItems`, `Composer`, `PlanPanel`, `ConversationScreen` |
| `renderer/threads/*` | `route.ts` (pure), `RouteView`, `Transcript`, `tabs/*`, `ThreadRoster`, `ThreadDetail`, `ThreadsScreen` |
| `renderer/attention/*` | `FlightStrip`, `StripRack`, `Inspector`, `AttentionScreen` |
| `renderer/components/ProjectSwitcher.tsx` | the ⌘P switcher in the title bar |
| `renderer/tray/*` | `miniLine.ts` (pure), `TrayPopover` |
| `main/popover.ts` | the menu-bar popover window |
| `e2e/flows.e2e.test.ts` | the end-to-end flows from spec §10 for these screens |

All renderer paths are under `apps/desktop/src/`.

---

### Task 1: Renderer data layer (format, clock, unread, project session)

**Files:**
- Create in `apps/desktop/src/renderer/`: `format.ts`, `format.test.ts`, `state/now.ts`, `state/unread.ts`, `state/unread.test.ts`, `state/session.ts`, `state/session.test.tsx`
- Modify: `apps/desktop/src/renderer/App.tsx`, which starts the session routing

**Interfaces:**
- Consumes: `call` and `onPush` from `bridge.ts`; `createStore` and `useStore`; the `@desk/client` reducers.
- Produces:

```ts
// format.ts
export function clock(ts: string | number | Date): string;      // "10:42" local
export function duration(ms: number): string;                    // "42s" | "14m" | "1h 5m" | "3d"
export function ago(ts: string, now: number): string;            // "now" | duration
export function bytes(n: number): string;                        // "812 B" | "1.9 KB" | "3.2 MB"
export function plural(n: number, one: string, many?: string): string;
// state/now.ts
export function useNow(): number;
// state/unread.ts
export function markSeen(projectId: string, at?: string): void;
export function lastActivity(p: ProjectSummary): string;
export function unreadIn(p: ProjectSummary, seen: Record<string, string>): boolean;
export function useUnread(p: ProjectSummary): boolean;
export function resetSeen(): void;
// state/session.ts
export type SessionState = { status: 'loading' | 'ready' | 'missing' | 'error'; error: string | null; project: ProjectState | null; chat: ChatState; timeline: TimelineState; events: StoredEvent[]; streams: Record<string, { runId: string; text: string }> };
export function startSessionRouting(): () => void;
export function useSession(projectId: string): SessionState;
export function useTranscript(s: SessionState, projectId: string, agentId: string): TranscriptState;
export function setReleaseDelay(ms: number): void;   // tests
export function resetSessions(): void;               // tests
```

- [ ] **Step 1: Write the failing tests**

`apps/desktop/src/renderer/format.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { ago, bytes, clock, duration, plural } from './format';

describe('format', () => {
  it('formats durations, ages, sizes and counts', () => {
    expect(duration(42_000)).toBe('42s');
    expect(duration(14 * 60_000)).toBe('14m');
    expect(duration(65 * 60_000)).toBe('1h 5m');
    expect(duration(120 * 60_000)).toBe('2h');
    expect(duration(3 * 86_400_000)).toBe('3d');
    const now = Date.parse('2026-09-24T11:27:00Z');
    expect(ago('2026-09-24T11:26:30Z', now)).toBe('now');
    expect(ago('2026-09-24T11:23:00Z', now)).toBe('4m');
    expect(bytes(812)).toBe('812 B');
    expect(bytes(1946)).toBe('1.9 KB');
    expect(bytes(3_355_443)).toBe('3.2 MB');
    expect(plural(1, 'thread')).toBe('1 thread');
    expect(plural(3, 'thread')).toBe('3 threads');
    expect(plural(2, 'reply', 'replies')).toBe('2 replies');
  });

  it('shows local wall-clock time', () => {
    const d = new Date(2026, 8, 24, 9, 5);
    expect(clock(d)).toBe('09:05');
    expect(clock(d.toISOString())).toBe('09:05');
  });
});
```

`apps/desktop/src/renderer/state/unread.test.ts`:

```ts
// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest';
import type { ProjectSummary } from '@desk/protocol';
import { lastActivity, markSeen, resetSeen, unreadIn } from './unread';

const summary = (updated: string[], report?: string): ProjectSummary => ({
  project: { id: 'p', name: 'P', goal: '', updated_at: '2026-09-24T09:00:00.000Z' },
  desk_status: 'idle',
  threads: updated.map((u, i) => ({ id: `t${i}`, title: null, status: 'running', reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: u, updated_at: u })),
  latest_report: report ? { headline: 'h', ts: report } : null,
  plan_progress: { done: 0, total: 0 },
  attention_count: 0,
});

beforeEach(() => {
  localStorage.clear();
  resetSeen();
});

describe('unread', () => {
  it('compares the last activity with the last visit', () => {
    const p = summary(['2026-09-24T10:00:00.000Z'], '2026-09-24T10:30:00.000Z');
    expect(lastActivity(p)).toBe('2026-09-24T10:30:00.000Z');
    expect(unreadIn(p, {})).toBe(true);
    expect(unreadIn(p, { p: '2026-09-24T11:00:00.000Z' })).toBe(false);
    expect(unreadIn(p, { p: '2026-09-24T10:15:00.000Z' })).toBe(true);
    expect(unreadIn(summary([]), {})).toBe(false);
  });

  it('persists visits', () => {
    markSeen('p', '2026-09-24T11:00:00.000Z');
    expect(JSON.parse(localStorage.getItem('desk.seen')!)).toEqual({ p: '2026-09-24T11:00:00.000Z' });
  });
});
```

`apps/desktop/src/renderer/state/session.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ev } from '@desk/client/testing';
import type { ProjectOverview } from '@desk/client';
import { installBridge } from '../test/bridge';
import { resetSessions, setReleaseDelay, startSessionRouting, useSession, useTranscript } from './session';

afterEach(cleanup);
beforeEach(() => {
  resetSessions();
  setReleaseDelay(0);
});

export const overview = (): ProjectOverview => ({
  project: { id: 'p', name: 'Launch', goal: 'g', instructions: '', settings: { desk_model: 'm', thread_model: 'm', fallback_model: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, policy: [] }, created_at: 't', updated_at: 't', archived_at: null },
  desk: { id: 'd', project_id: 'p', role: 'desk', status: 'idle', model: 'm', title: 'Desk', brief: null, workspace_path: '/w', parent_id: null, inbox_cursor: 0, review_round: 0, result_summary: null, result_artifacts: null, active_skills: [], git_source_id: null, git_branch: null, git_base: null, git_common_dir: null, archived_at: null, created_at: 't', updated_at: 't' },
  sources: [],
  plan: null,
  threads: [],
  approvals: [],
  last_seq: 2,
});

function Probe({ id, agent }: { id: string; agent: string }) {
  const s = useSession(id);
  const t = useTranscript(s, id, agent);
  return (
    <p data-testid="probe">
      {[s.status, s.chat.items.map((i) => i.kind).join(','), s.timeline.lanes.length, s.project?.threads.length ?? '-', t.entries.map((e) => e.kind).join(','), s.streams.d?.text ?? ''].join('|')}
    </p>
  );
}

describe('project session', () => {
  it('loads, watches from zero, folds events and streams, then unwatches', async () => {
    const events = [
      ev(1, 'project.created', { name: 'Launch', goal: 'g', instructions: '' }),
      ev(2, 'agent.created', { role: 'desk', model: 'm', title: 'Desk', brief: null, workspace_path: '/w', parent_id: null }, { agent: 'd' }),
      ev(3, 'message.user', { text: 'Kick off' }, { agent: 'd' }),
      ev(4, 'agent.created', { role: 'thread', model: 'm', title: 'Checklist', brief: 'Build it', workspace_path: '/w/t', parent_id: 'd' }, { agent: 't' }),
      ev(5, 'agent.status_changed', { status: 'running' }, { agent: 't' }),
    ];
    const bridge = installBridge({
      'projects.get': () => overview(),
      'broker.watch': () => {
        for (const e of events) bridge.emit('desk:event', e);
        return { ok: true };
      },
      'broker.unwatch': () => ({ ok: true }),
    });
    startSessionRouting();
    const view = render(<Probe id="p" agent="t" />);
    await waitFor(() => expect(screen.getByTestId('probe').textContent).toBe('ready|user|1|1|brief,status|'));
    expect(bridge.calls.find((c) => c.channel === 'broker.watch')?.input).toEqual({ projectId: 'p', afterSeq: 0 });

    bridge.emit('desk:ephemeral', { type: 'assistant.delta', project_id: 'p', agent_id: 'd', payload: { run_id: 'r', text: 'Hel' } });
    bridge.emit('desk:ephemeral', { type: 'assistant.delta', project_id: 'p', agent_id: 'd', payload: { run_id: 'r', text: 'lo' } });
    await waitFor(() => expect(screen.getByTestId('probe').textContent).toBe('ready|user,assistant|1|1|brief,status|Hello'));
    bridge.emit('desk:event', ev(6, 'assistant.message', { run_id: 'r', content: 'Hello there', tool_calls: [] }, { agent: 'd' }));
    bridge.emit('desk:event', ev(6, 'assistant.message', { run_id: 'r', content: 'Hello there', tool_calls: [] }, { agent: 'd' }));
    await waitFor(() => expect(screen.getByTestId('probe').textContent).toBe('ready|user,assistant|1|1|brief,status|'));

    view.unmount();
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'broker.unwatch')).toBe(true));
  });

  it('reports a missing project', async () => {
    installBridge({ 'projects.get': () => Promise.reject({ code: 'not_found', message: 'Unknown project: x', status: 404 }) });
    startSessionRouting();
    render(<Probe id="x" agent="t" />);
    await waitFor(() => expect(screen.getByTestId('probe').textContent?.startsWith('missing|')).toBe(true));
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop/src/renderer/format.test.ts apps/desktop/src/renderer/state`
Expected: FAIL: the modules are missing.

- [ ] **Step 3: Implement**

`apps/desktop/src/renderer/format.ts`:

```ts
const pad = (n: number) => String(n).padStart(2, '0');

/** Local wall-clock time, e.g. "10:42". */
export function clock(ts: string | number | Date): string {
  const d = new Date(ts);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** A short duration: "42s", "14m", "1h 5m", "3d". */
export function duration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

/** Time since `ts`: "now" under a minute, else a short duration. */
export function ago(ts: string, now: number): string {
  const ms = now - Date.parse(ts);
  return ms < 60_000 ? 'now' : duration(ms);
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;
```

`apps/desktop/src/renderer/state/now.ts`:

```ts
import { useSyncExternalStore } from 'react';

const STEP_MS = 15_000;
let now = Date.now();
const subs = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | undefined;

function subscribe(fn: () => void): () => void {
  subs.add(fn);
  timer ??= setInterval(() => {
    now = Date.now();
    for (const s of subs) s();
  }, STEP_MS);
  return () => {
    subs.delete(fn);
    if (!subs.size && timer) {
      clearInterval(timer);
      timer = undefined;
    }
  };
}

function snapshot(): number {
  if (Date.now() - now > STEP_MS) now = Date.now();
  return now;
}

/** The current time, shared and refreshed every 15 s (elapsed times, the "now" line). */
export function useNow(): number {
  return useSyncExternalStore(subscribe, snapshot);
}
```

`apps/desktop/src/renderer/state/unread.ts`:

```ts
import type { ProjectSummary } from '@desk/protocol';
import { createStore, useStore } from '../store';

const KEY = 'desk.seen';

function load(): Record<string, string> {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? '{}') as unknown;
    return v && typeof v === 'object' ? (v as Record<string, string>) : {};
  } catch {
    return {};
  }
}

const seenStore = createStore<Record<string, string>>(load());

/** Records that the user has seen a project (its conversation is open). Local to this viewer. */
export function markSeen(projectId: string, at: string = new Date().toISOString()): void {
  seenStore.set((all) => ({ ...all, [projectId]: at }));
  try {
    localStorage.setItem(KEY, JSON.stringify(seenStore.get()));
  } catch {
    // A convenience only.
  }
}

/** The latest thing that happened in a project (a report or any thread change). */
export function lastActivity(p: ProjectSummary): string {
  return [p.latest_report?.ts ?? '', ...p.threads.map((t) => t.updated_at)].reduce((a, b) => (b > a ? b : a), '');
}

/** Something happened since the last visit; never-visited projects count once they have activity. */
export function unreadIn(p: ProjectSummary, seen: Record<string, string>): boolean {
  const last = lastActivity(p);
  if (!last) return false;
  const at = seen[p.project.id];
  return !at || last > at;
}

export function useUnread(p: ProjectSummary): boolean {
  return useStore(seenStore, (s) => unreadIn(p, s));
}

export function resetSeen(): void {
  seenStore.set({});
}
```

`apps/desktop/src/renderer/state/session.ts`:

```ts
import { useEffect, useMemo } from 'react';
import {
  applyChatDelta,
  applyTranscriptDelta,
  emptyChat,
  emptyTimeline,
  emptyTranscript,
  projectFromOverview,
  reduceChat,
  reduceProject,
  reduceTimeline,
  reduceTranscript,
  type ChatState,
  type ProjectState,
  type TimelineState,
  type TranscriptState,
} from '@desk/client';
import type { EphemeralEvent, StoredEvent } from '@desk/protocol';
import { call, DeskCallError, onPush } from '../bridge';
import { createStore, useStore, type Store } from '../store';

export type SessionState = {
  status: 'loading' | 'ready' | 'missing' | 'error';
  error: string | null;
  project: ProjectState | null;
  chat: ChatState;
  timeline: TimelineState;
  /** The project's full event log, in id order. */
  events: StoredEvent[];
  /** Live streamed text per agent (ephemeral), until its assistant.message lands. */
  streams: Record<string, { runId: string; text: string }>;
};

const initial = (): SessionState => ({ status: 'loading', error: null, project: null, chat: emptyChat(''), timeline: emptyTimeline(''), events: [], streams: {} });

let releaseDelayMs = 30_000;
/** How long a session outlives its last viewer (switching tabs keeps it warm). */
export const setReleaseDelay = (ms: number): void => {
  releaseDelayMs = ms;
};

class ProjectSession {
  readonly store: Store<SessionState> = createStore(initial());
  private refs = 0;
  private releaseTimer: ReturnType<typeof setTimeout> | undefined;
  private queue: StoredEvent[] = [];
  private scheduled = false;
  private started = false;

  constructor(
    readonly projectId: string,
    private readonly onDispose: (s: ProjectSession) => void,
  ) {}

  acquire(): void {
    this.refs++;
    clearTimeout(this.releaseTimer);
    if (!this.started) void this.load();
  }

  release(): void {
    this.refs = Math.max(0, this.refs - 1);
    if (this.refs) return;
    clearTimeout(this.releaseTimer);
    this.releaseTimer = setTimeout(() => {
      void call('broker.unwatch', { projectId: this.projectId }).catch(() => {});
      this.onDispose(this);
    }, releaseDelayMs);
  }

  private async load(): Promise<void> {
    this.started = true;
    try {
      const overview = await call('projects.get', { id: this.projectId });
      const deskId = overview.desk?.id ?? '';
      this.store.set((s) => ({ ...s, status: 'ready', error: null, project: projectFromOverview(overview), chat: emptyChat(deskId), timeline: emptyTimeline(deskId) }));
      this.flush();
      await call('broker.watch', { projectId: this.projectId, afterSeq: 0 });
    } catch (err) {
      this.started = false;
      const missing = err instanceof DeskCallError && err.status === 404;
      this.store.set((s) => ({ ...s, status: missing ? 'missing' : 'error', error: err instanceof Error ? err.message : String(err) }));
    }
  }

  enqueue(e: StoredEvent): void {
    this.queue.push(e);
    if (this.scheduled) return;
    this.scheduled = true;
    queueMicrotask(() => {
      this.scheduled = false;
      this.flush();
    });
  }

  /** Applies queued events in one store update (a backfill arrives as one burst). */
  private flush(): void {
    if (this.store.get().status !== 'ready' || !this.queue.length) return;
    const batch = this.queue;
    this.queue = [];
    this.store.set((s) => {
      let { project, chat, timeline, streams } = s;
      const fresh: StoredEvent[] = [];
      let last = s.events.at(-1)?.id ?? 0;
      for (const e of batch) {
        if (e.id <= last) continue;
        last = e.id;
        fresh.push(e);
        if (project) project = reduceProject(project, e);
        chat = reduceChat(chat, e);
        timeline = reduceTimeline(timeline, e);
        if ((e.type === 'assistant.message' || e.type === 'run.finished') && e.agent_id && streams[e.agent_id]?.runId === e.payload.run_id) {
          const { [e.agent_id]: _done, ...rest } = streams;
          streams = rest;
        }
      }
      return fresh.length ? { ...s, project, chat, timeline, streams, events: s.events.concat(fresh) } : s;
    });
  }

  onDelta(e: EphemeralEvent): void {
    if (this.store.get().status !== 'ready') return;
    this.store.set((s) => {
      const prev = s.streams[e.agent_id];
      const text = prev?.runId === e.payload.run_id ? prev.text + e.payload.text : e.payload.text;
      return { ...s, chat: applyChatDelta(s.chat, e), streams: { ...s.streams, [e.agent_id]: { runId: e.payload.run_id, text } } };
    });
  }
}

const sessions = new Map<string, ProjectSession>();
let stopRouting: (() => void) | null = null;

/** Routes pushed events to their project's session. Call once at startup. */
export function startSessionRouting(): () => void {
  if (stopRouting) return stopRouting;
  const offEvent = onPush<StoredEvent>('desk:event', (e) => sessions.get(e.project_id)?.enqueue(e));
  const offDelta = onPush<EphemeralEvent>('desk:ephemeral', (e) => sessions.get(e.project_id)?.onDelta(e));
  stopRouting = () => {
    offEvent();
    offDelta();
    stopRouting = null;
  };
  return stopRouting;
}

function sessionFor(projectId: string): ProjectSession {
  let s = sessions.get(projectId);
  if (!s) {
    s = new ProjectSession(projectId, (done) => {
      if (sessions.get(projectId) === done) sessions.delete(projectId);
    });
    sessions.set(projectId, s);
  }
  return s;
}

export function useSession(projectId: string): SessionState {
  const session = useMemo(() => sessionFor(projectId), [projectId]);
  useEffect(() => {
    session.acquire();
    return () => session.release();
  }, [session]);
  return useStore(session.store, (s) => s);
}

/** One agent's transcript, folded from the session log plus its live stream. */
export function useTranscript(s: SessionState, projectId: string, agentId: string): TranscriptState {
  const stream = s.streams[agentId];
  return useMemo(() => {
    let t = emptyTranscript(agentId);
    for (const e of s.events) if (e.agent_id === agentId) t = reduceTranscript(t, e);
    return stream ? applyTranscriptDelta(t, { type: 'assistant.delta', project_id: projectId, agent_id: agentId, payload: { run_id: stream.runId, text: stream.text } }) : t;
  }, [s.events, stream, agentId, projectId]);
}

export function resetSessions(): void {
  sessions.clear();
  stopRouting?.();
}
```

In `apps/desktop/src/renderer/App.tsx`:
- Add `import { startSessionRouting } from './state/session';`.
- Add `useEffect(() => startSessionRouting(), []);` next to the `startGlobalSync` effect.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop
git commit -m "feat(desktop): renderer data layer — project sessions over the broker, clock, unread, formatting

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Map — orbit layout, canvas, inspector, list view

**Files:**
- Create in `apps/desktop/src/renderer/map/`: `layout.ts`, `layout.test.ts`, `MapCanvas.tsx`, `OrbitMap.tsx`, `TerritoryInspector.tsx`, `ProjectList.tsx`, `map.css`, `MapScreen.test.tsx`
- Replace: `apps/desktop/src/renderer/screens/MapScreen.tsx`, which moves to `map/MapScreen.tsx`. Update the import in `App.tsx`.

**Interfaces:**
- Consumes: `useGlobal`, `useNow`, `useUnread`, `call('projects.plan')`, `ProjectForm`, `Sheet`, `href`, `navigate`.
- Produces:

```ts
// map/layout.ts
export type MapProject = { id: string; activity: number; threads: Array<{ id: string; status: AgentStatus }> };
export type Territory = { id: string; x: number; y: number; r: number; desk: number; orbit: number; threads: Array<{ id: string; x: number; y: number }> };
export type MapLayout = { sun: { x: number; y: number }; rings: number[]; territories: Territory[] };
export function activityOf(p: ProjectSummary): number;
export function layoutMap(projects: MapProject[], width: number, height: number): MapLayout;
// map/OrbitMap.tsx
export type Tone = 'running' | 'waiting' | 'idle';
export function projectTone(p: ProjectSummary): Tone;
export function projectSummaryLine(p: ProjectSummary): string;
export function OrbitMap(props): JSX.Element;
// map/MapScreen.tsx
export function MapScreen(props: { newProject: boolean }): JSX.Element;
```

- [ ] **Step 1: Write the failing tests**

`apps/desktop/src/renderer/map/layout.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { layoutMap, type MapProject } from './layout';

const projects = (n: number): MapProject[] =>
  Array.from({ length: n }, (_, i) => ({ id: `p${i}`, activity: (i * 7) % 11 + 1, threads: Array.from({ length: i % 4 }, (_, j) => ({ id: `t${i}-${j}`, status: 'running' as const })) }));

describe('layoutMap', () => {
  it('is deterministic, in bounds, clear of the sun, and never overlaps', () => {
    for (const n of [1, 3, 5, 8]) {
      const a = layoutMap(projects(n), 1000, 700);
      expect(layoutMap(projects(n), 1000, 700)).toEqual(a);
      for (const t of a.territories) {
        expect(t.x - t.r).toBeGreaterThanOrEqual(0);
        expect(t.y - t.r).toBeGreaterThanOrEqual(0);
        expect(t.x + t.r).toBeLessThanOrEqual(1000);
        expect(t.y + t.r).toBeLessThanOrEqual(700);
        expect(Math.hypot(t.x - a.sun.x, t.y - a.sun.y)).toBeGreaterThanOrEqual(t.r + 40);
      }
      for (let i = 0; i < a.territories.length; i++)
        for (let j = i + 1; j < a.territories.length; j++) {
          const p = a.territories[i]!;
          const q = a.territories[j]!;
          expect(Math.hypot(p.x - q.x, p.y - q.y)).toBeGreaterThanOrEqual(p.r + q.r);
        }
    }
  });

  it('makes busier projects larger and puts threads on their orbit', () => {
    const [quiet, busy] = layoutMap(
      [
        { id: 'quiet', activity: 1, threads: [] },
        { id: 'busy', activity: 20, threads: [{ id: 't1', status: 'running' }, { id: 't2', status: 'waiting' }] },
      ],
      1200,
      800,
    ).territories.sort((x, y) => x.id.localeCompare(y.id)).reverse();
    expect(busy!.r).toBeGreaterThan(quiet!.r);
    for (const t of busy!.threads) expect(Math.hypot(t.x - busy!.x, t.y - busy!.y)).toBeCloseTo(busy!.orbit, 5);
  });
});
```

`apps/desktop/src/renderer/map/MapScreen.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { AttentionItem, ProjectSummary } from '@desk/protocol';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { installBridge } from '../test/bridge';
import { MapScreen } from './MapScreen';

afterEach(cleanup);
beforeEach(() => localStorage.clear());

const thread = (id: string, title: string, status: 'running' | 'waiting' | 'done') => ({ id, title, status, reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: '2026-09-24T10:00:00.000Z', updated_at: '2026-09-24T10:00:00.000Z' });
const project = (id: string, name: string, threads: ProjectSummary['threads'], attention = 0): ProjectSummary => ({
  project: { id, name, goal: `${name} goal`, updated_at: '2026-09-24T10:00:00.000Z' },
  desk_status: 'idle',
  threads,
  latest_report: { headline: `${name}: research is in`, ts: '2026-09-24T11:24:00.000Z' },
  plan_progress: { done: 1, total: 3 },
  attention_count: attention,
});
const approval: AttentionItem = { id: 'approval:a1', kind: 'approval', project_id: 'p1', project_name: 'Onboarding revamp', agent_id: 't2', title: 'Signup checklist wants to run bash', detail: 'rule 1', created_at: '2026-09-24T11:21:00.000Z', ref: { approval_id: 'a1', thread_id: 't2' } };

function seed() {
  globalStore.set({
    ...initialGlobalState(),
    connection: { status: 'live' },
    health: { version: '1.0.0', protocol_version: 1, proxy: 'up', uptime_s: 60 },
    overview: [project('p1', 'Onboarding revamp', [thread('t1', 'Funnel analysis', 'running'), thread('t2', 'Signup checklist', 'waiting')], 1), project('p2', 'Tax paperwork', [thread('t3', 'Receipts', 'done')])],
    attention: [approval],
  });
}

describe('MapScreen', () => {
  it('draws a territory per project and fills the inspector for the selected one', async () => {
    seed();
    installBridge({ 'projects.plan': ({ id }: { id: string }) => ({ project_id: id, items: [{ id: '1', title: 'Map competitors', status: 'done', thread_ids: [], notes: '' }, { id: '2', title: 'Funnel', status: 'in_progress', thread_ids: ['t1'], notes: '' }, { id: '3', title: 'Launch', status: 'todo', thread_ids: [], notes: '' }], updated_at: '' }) });
    render(<MapScreen newProject={false} />);
    expect(screen.getByRole('button', { name: 'Onboarding revamp: show details' })).toBeTruthy();
    for (const a of screen.getAllByRole('link', { name: /Funnel analysis/ })) expect(a.getAttribute('href')).toBe('#/p/p1/threads/t1');
    const inspector = screen.getByRole('region', { name: 'Territory' });
    expect(inspector.textContent).toContain('Onboarding revamp');
    expect(inspector.textContent).toContain('Onboarding revamp: research is in');
    await waitFor(() => expect(inspector.textContent).toContain('1 of 3 done'));
    expect(screen.getAllByRole('link', { name: 'Signup checklist wants to run bash' })[0]!.getAttribute('href')).toBe('#/attention?item=approval%3Aa1');
    fireEvent.click(screen.getByRole('button', { name: 'Tax paperwork: show details' }));
    expect(screen.getByRole('region', { name: 'Territory' }).textContent).toContain('Tax paperwork');
  });

  it('switches to the list view and remembers it', () => {
    seed();
    installBridge({ 'projects.plan': () => null });
    render(<MapScreen newProject={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    expect(screen.getByRole('link', { name: /Tax paperwork/ }).getAttribute('href')).toBe('#/p/p2/conversation');
    expect(localStorage.getItem('desk.mapView')).toBe('list');
  });

  it('opens the new project sheet', () => {
    seed();
    installBridge({ 'projects.plan': () => null });
    render(<MapScreen newProject={false} />);
    fireEvent.click(screen.getByRole('button', { name: /New project/ }));
    expect(screen.getByRole('dialog', { name: 'New project' })).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop/src/renderer/map`
Expected: FAIL: the modules are missing.

- [ ] **Step 3: Implement**

`apps/desktop/src/renderer/map/layout.ts`:

```ts
import type { AgentStatus, ProjectSummary } from '@desk/protocol';

export type MapProject = { id: string; activity: number; threads: Array<{ id: string; status: AgentStatus }> };
export type Territory = { id: string; x: number; y: number; r: number; desk: number; orbit: number; threads: Array<{ id: string; x: number; y: number }> };
export type MapLayout = { sun: { x: number; y: number }; rings: number[]; territories: Territory[] };

const TAU = Math.PI * 2;

/** How much is going on in a project: sizes its territory and orders it toward the centre. */
export function activityOf(p: ProjectSummary): number {
  const count = (...s: AgentStatus[]) => p.threads.filter((t) => s.includes(t.status)).length;
  return 1 + count('running') * 3 + count('waiting', 'queued') * 2 + p.threads.length + p.attention_count * 2 + (p.desk_status === 'running' ? 2 : 0);
}

/**
 * Deterministic orbit layout: deskd in the middle, the busiest projects on the inner ring and largest,
 * then a relaxation pass so territories never overlap, stay in bounds and keep clear of the sun.
 */
export function layoutMap(projects: MapProject[], width: number, height: number): MapLayout {
  const sun = { x: width / 2, y: height / 2 + 20 };
  const span = Math.min(width, height);
  const rings = [0.26, 0.42, 0.58].map((f) => f * span);
  const maxR = span * 0.19;
  const minR = span * 0.08;
  const sorted = [...projects].sort((a, b) => b.activity - a.activity || a.id.localeCompare(b.id));
  const slots = [3, 6, Math.max(0, sorted.length - 9)];
  const nodes = sorted.map((p, i) => {
    const ring = i < 3 ? 0 : i < 9 ? 1 : 2;
    const k = ring === 0 ? i : ring === 1 ? i - 3 : i - 9;
    const inRing = Math.max(1, Math.min(slots[ring]!, sorted.length - (ring === 0 ? 0 : ring === 1 ? 3 : 9)));
    const angle = -Math.PI / 2 + ring * 0.6 + (k / inRing) * TAU;
    const r = Math.min(maxR, minR + Math.sqrt(p.activity) * span * 0.022);
    return { p, r, x: sun.x + Math.cos(angle) * rings[ring]!, y: sun.y + Math.sin(angle) * rings[ring]! * 0.8 };
  });
  for (let iter = 0; iter < 120; iter++) {
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i]!;
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j]!;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const d = Math.hypot(dx, dy) || 0.01;
        const min = a.r + b.r + 24;
        if (d < min) {
          const push = (min - d) / 2;
          a.x -= (dx / d) * push;
          a.y -= (dy / d) * push;
          b.x += (dx / d) * push;
          b.y += (dy / d) * push;
        }
      }
      const ds = Math.hypot(a.x - sun.x, a.y - sun.y) || 0.01;
      const clear = a.r + 64;
      if (ds < clear) {
        a.x = sun.x + ((a.x - sun.x) / ds) * clear;
        a.y = sun.y + ((a.y - sun.y) / ds) * clear;
      }
      a.x = Math.min(width - a.r - 8, Math.max(a.r + 8, a.x));
      a.y = Math.min(height - a.r - 8, Math.max(a.r + 8, a.y));
    }
  }
  const territories = nodes.map(({ p, x, y, r }) => {
    const orbit = r * 0.62;
    const n = p.threads.length;
    return {
      id: p.id,
      x,
      y,
      r,
      orbit,
      desk: Math.round(40 + (r / maxR) * 24),
      threads: p.threads.map((t, i) => {
        const a = -Math.PI / 3 + (i / Math.max(1, n)) * TAU;
        return { id: t.id, x: x + Math.cos(a) * orbit, y: y + Math.sin(a) * orbit };
      }),
    };
  });
  return { sun, rings, territories };
}
```

When many projects crowd a small window, the relaxation can leave small overlaps. The layout test covers up to 8 projects at 1000×700. The canvas zooms and pans, and the List view is always available.

`apps/desktop/src/renderer/map/MapCanvas.tsx`:

```tsx
import { useLayoutEffect, useRef, useState, type PointerEvent, type ReactNode, type WheelEvent } from 'react';

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** A pannable, zoomable surface (drag the background, scroll to pan, pinch or ⌘-scroll to zoom). */
export function MapCanvas({ children, label }: { children(size: { width: number; height: number }): ReactNode; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 1000, height: 700 });
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ px: number; py: number; x: number; y: number } | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setSize({ width: el.clientWidth || 1000, height: el.clientHeight || 700 });
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const zoomAt = (px: number, py: number, factor: number) =>
    setView((v) => {
      const k = clamp(v.k * factor, 0.5, 2.5);
      return { k, x: px - ((px - v.x) * k) / v.k, y: py - ((py - v.y) * k) / v.k };
    });

  const onWheel = (e: WheelEvent) => {
    if (e.ctrlKey || e.metaKey) {
      const rect = ref.current!.getBoundingClientRect();
      zoomAt(e.clientX - rect.left, e.clientY - rect.top, Math.exp(-e.deltaY * 0.01));
    } else setView((v) => ({ ...v, x: v.x - e.deltaX, y: v.y - e.deltaY }));
  };
  const onPointerDown = (e: PointerEvent) => {
    if ((e.target as Element).closest('a, button')) return;
    drag.current = { px: e.clientX, py: e.clientY, x: view.x, y: view.y };
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: PointerEvent) => {
    const d = drag.current;
    if (d) setView((v) => ({ ...v, x: d.x + e.clientX - d.px, y: d.y + e.clientY - d.py }));
  };
  const endDrag = () => {
    drag.current = null;
  };
  const moved = view.x !== 0 || view.y !== 0 || view.k !== 1;

  return (
    <div className="map-canvas" ref={ref} aria-label={label} role="group" onWheel={onWheel} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={endDrag} onPointerCancel={endDrag}>
      <div className="map-layer" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}>
        {children(size)}
      </div>
      <div className="map-zoom" role="group" aria-label="Zoom">
        <button type="button" aria-label="Zoom in" onClick={() => zoomAt(size.width / 2, size.height / 2, 1.2)}>
          +
        </button>
        <button type="button" aria-label="Zoom out" onClick={() => zoomAt(size.width / 2, size.height / 2, 1 / 1.2)}>
          −
        </button>
        {moved ? (
          <button type="button" onClick={() => setView({ x: 0, y: 0, k: 1 })}>
            Reset
          </button>
        ) : null}
      </div>
    </div>
  );
}
```

`apps/desktop/src/renderer/map/OrbitMap.tsx`:

```tsx
import type { AgentStatus, AttentionItem, ProjectSummary } from '@desk/protocol';
import { ago } from '../format';
import { href } from '../router';
import { useUnread } from '../state/unread';
import type { MapLayout, Territory } from './layout';

export type Tone = 'running' | 'waiting' | 'idle';

/** Blue while anything runs, amber when it only waits on you, grey when idle (design C territories). */
export function projectTone(p: ProjectSummary): Tone {
  if (p.desk_status === 'running' || p.threads.some((t) => t.status === 'running' || t.status === 'queued')) return 'running';
  if (p.attention_count > 0 || p.desk_status === 'waiting' || p.threads.some((t) => t.status === 'waiting')) return 'waiting';
  return 'idle';
}

export function projectSummaryLine(p: ProjectSummary): string {
  const c = (...s: AgentStatus[]) => p.threads.filter((t) => s.includes(t.status)).length;
  const parts = [
    c('running') ? `${c('running')} running` : '',
    c('waiting', 'queued') ? `${c('waiting', 'queued')} waiting` : '',
    c('done') ? `${c('done')} done` : '',
    c('failed') ? `${c('failed')} failed` : '',
  ].filter(Boolean);
  if (parts.length) return parts.join(' · ');
  if (p.desk_status === 'waiting') return 'Desk is waiting on you';
  if (p.desk_status === 'running') return 'Desk is working';
  return p.plan_progress.total && p.plan_progress.done === p.plan_progress.total ? 'Idle · all plan items done' : 'Idle';
}

const TONE = {
  running: { fill: '#E3E8F5', stroke: '#C9D3EC', orbit: '#B7C4E6', text: '#1F45A8' },
  waiting: { fill: '#F3E6CF', stroke: '#E6D3AF', orbit: '#E0C99E', text: '#7A4500' },
  idle: { fill: '#E6E1D7', stroke: '#D6CFC1', orbit: '#D6CFC1', text: '#4A4740' },
} as const;

const SPOKE: Partial<Record<AgentStatus, { stroke: string; width: number; dash?: string }>> = {
  running: { stroke: '#2F5BD3', width: 2.5 },
  waiting: { stroke: '#A15C00', width: 2, dash: '4 4' },
  queued: { stroke: '#A15C00', width: 2, dash: '4 4' },
  failed: { stroke: '#C4441C', width: 2, dash: '4 4' },
};

const CALLOUT: Record<AttentionItem['kind'], { label: string; glyph: string }> = {
  approval: { label: 'Approval', glyph: '!' },
  question: { label: 'Question', glyph: '?' },
  needs_you: { label: 'From a report', glyph: '' },
  stalled: { label: 'Stalled', glyph: '' },
  failed: { label: 'Failed', glyph: '!' },
};

function ProjectLabel({ p, t }: { p: ProjectSummary; t: Territory }) {
  const unread = useUnread(p);
  const tone = TONE[projectTone(p)];
  return (
    <div className="orbit-label" style={{ left: t.x, top: t.y - t.r + 14, color: tone.text }}>
      <span className="orbit-label-name">
        {p.project.name}
        {unread ? <span className="unread-dot" aria-label="unread" /> : null}
      </span>
      <span className="orbit-label-sub">{projectSummaryLine(p)}</span>
    </div>
  );
}

export function OrbitMap(o: {
  layout: MapLayout;
  projects: ProjectSummary[];
  attention: AttentionItem[];
  selected: string | null;
  onSelect(id: string): void;
  width: number;
  height: number;
  sunLabel: [string, string];
  sunAria: string;
  now: number;
}) {
  const byId = new Map(o.projects.map((p) => [p.project.id, p]));
  return (
    <>
      <svg className="orbit-svg" width={o.width} height={o.height} aria-hidden="true">
        {o.layout.rings.map((r) => (
          <circle key={r} cx={o.layout.sun.x} cy={o.layout.sun.y} r={r} fill="none" stroke="#D3CCBE" strokeDasharray="3 6" />
        ))}
        {o.layout.territories.map((t) => {
          const p = byId.get(t.id)!;
          const tone = TONE[projectTone(p)];
          return (
            <g key={t.id}>
              <circle cx={t.x} cy={t.y} r={t.r} fill={tone.fill} stroke={tone.stroke} />
              <circle cx={t.x} cy={t.y} r={t.orbit} fill="none" stroke={tone.orbit} strokeDasharray="3 6" />
              {t.threads.map((pos) => {
                const th = p.threads.find((x) => x.id === pos.id);
                const s = (th && SPOKE[th.status]) ?? { stroke: '#B7C4E6', width: 1.5, dash: '2 4' };
                return <path key={pos.id} d={`M${t.x} ${t.y} L ${pos.x} ${pos.y}`} stroke={s.stroke} strokeWidth={s.width} strokeDasharray={s.dash} />;
              })}
            </g>
          );
        })}
      </svg>

      <button type="button" className="orbit-sun" style={{ left: o.layout.sun.x, top: o.layout.sun.y }} aria-label={o.sunAria} onClick={() => (window.location.hash = '/system')}>
        deskd
      </button>
      <div className="orbit-sun-label" style={{ left: o.layout.sun.x, top: o.layout.sun.y + 40 }}>
        <span>{o.sunLabel[0]}</span>
        <span>{o.sunLabel[1]}</span>
      </div>

      {o.layout.territories.map((t) => {
        const p = byId.get(t.id)!;
        const tone = projectTone(p);
        const items = o.attention.filter((i) => i.project_id === t.id);
        const first = items[0];
        return (
          <div key={t.id}>
            <ProjectLabel p={p} t={t} />
            <button
              type="button"
              className={`orbit-desk orbit-desk-${tone}`}
              style={{ left: t.x, top: t.y, width: t.desk, height: t.desk, boxShadow: o.selected === t.id ? `0 0 0 5px ${TONE[tone].fill}, 0 0 0 8px var(--accent)` : 'none' }}
              aria-label={`${p.project.name}: show details`}
              aria-pressed={o.selected === t.id}
              onClick={() => o.onSelect(t.id)}
            >
              Desk
            </button>
            {t.threads.map((pos) => {
              const th = p.threads.find((x) => x.id === pos.id)!;
              const left = pos.x < t.x;
              return (
                <a
                  key={pos.id}
                  className={`orbit-thread orbit-thread-${th.status}${left ? ' left' : ''}`}
                  style={{ left: pos.x, top: pos.y }}
                  href={href({ name: 'project', id: t.id, tab: 'threads', threadId: th.id })}
                >
                  <span className="orbit-thread-dot" aria-hidden="true" />
                  {th.title ?? 'Thread'}
                  {th.status === 'done' ? ' · done' : ''}
                </a>
              );
            })}
            {first ? (
              <a className="orbit-callout" style={{ left: t.x, top: t.y - t.desk / 2 - 18 }} href={href({ name: 'attention', item: first.id })} aria-label={first.title}>
                <span className="orbit-pin" aria-hidden="true">
                  {items.length > 1 ? items.length : CALLOUT[first.kind].glyph || '1'}
                </span>
                <span className="orbit-callout-card">
                  <span className="orbit-callout-kind">
                    {CALLOUT[first.kind].label} · {ago(first.created_at, o.now)}
                  </span>
                  <span>{first.title}</span>
                </span>
              </a>
            ) : null}
          </div>
        );
      })}
    </>
  );
}
```

`apps/desktop/src/renderer/map/TerritoryInspector.tsx`:

```tsx
import { useEffect, useState } from 'react';
import type { AttentionItem, PlanItem, ProjectSummary } from '@desk/protocol';
import { call } from '../bridge';
import { ago, clock } from '../format';
import { href } from '../router';
import { projectSummaryLine, projectTone } from './OrbitMap';

const BADGE = { running: 'chip-run', waiting: 'chip-wait', idle: 'chip-idle' } as const;

function waypointTone(item: PlanItem, p: ProjectSummary): string {
  if (item.status === 'done') return 'done';
  if (item.status === 'dropped') return 'dropped';
  if (item.status === 'todo') return 'todo';
  return p.threads.some((t) => item.thread_ids.includes(t.id) && t.status === 'waiting') ? 'wait' : 'run';
}

function threadLine(t: ProjectSummary['threads'][number], waitingOnYou: boolean, now: number): string {
  switch (t.status) {
    case 'running':
      return t.review_round ? `revision ${t.review_round} · ${ago(t.created_at, now)}` : `running · ${ago(t.created_at, now)}`;
    case 'waiting':
      return waitingOnYou ? 'waiting on you' : 'waiting';
    case 'queued':
      return /restart/i.test(t.reason ?? '') ? 'will resume' : 'queued';
    default:
      return t.status;
  }
}

/** The TERRITORY card: latest report, plan waypoints, threads, needs-you, and the ways in. */
export function TerritoryInspector({ p, items, now }: { p: ProjectSummary; items: AttentionItem[]; now: number }) {
  const [plan, setPlan] = useState<PlanItem[] | null>(null);
  useEffect(() => {
    let live = true;
    setPlan(null);
    call('projects.plan', { id: p.project.id })
      .then((r) => live && setPlan(r?.items ?? []))
      .catch(() => live && setPlan([]));
    return () => {
      live = false;
    };
  }, [p.project.id, p.plan_progress.done, p.plan_progress.total]);
  const tone = projectTone(p);
  const done = plan?.filter((i) => i.status === 'done').length ?? p.plan_progress.done;
  const total = plan?.length ?? p.plan_progress.total;
  return (
    <article className="card territory" aria-label="Territory" role="region" aria-live="polite">
      <div className="territory-head">
        <span className="eyebrow">Territory</span>
        <span className={`chip ${BADGE[tone]}`}>{projectSummaryLine(p)}</span>
      </div>
      <h2 className="territory-name">{p.project.name}</h2>
      {p.latest_report ? (
        <div className="territory-quote">
          <p>“{p.latest_report.headline}”</p>
          <span className="muted">Desk · report {clock(p.latest_report.ts)}</span>
        </div>
      ) : (
        <p className="muted">{p.project.goal || 'No report yet.'}</p>
      )}
      <div className="territory-block">
        <div className="territory-row">
          <strong>Plan</strong>
          <span className="muted">{total ? (done === total ? `all ${total} done` : `${done} of ${total} done`) : 'no plan yet'}</span>
        </div>
        {plan && plan.length ? (
          <div className="waypoints" aria-hidden="true">
            <span className="waypoints-rule" />
            {plan.map((i) => (
              <span key={i.id} className={`waypoint waypoint-${waypointTone(i, p)}`} title={i.title} />
            ))}
          </div>
        ) : null}
      </div>
      {p.threads.length ? (
        <div className="territory-block">
          <strong>Threads</strong>
          {p.threads.map((t) => (
            <a key={t.id} className="territory-thread" href={href({ name: 'project', id: p.project.id, tab: 'threads', threadId: t.id })}>
              <span className={`status-dot status-dot-${t.status}`} aria-hidden="true" />
              <span className="grow">{t.title ?? 'Thread'}</span>
              <span className={`territory-thread-status status-text-${t.status}`}>{threadLine(t, items.some((i) => i.ref.thread_id === t.id), now)}</span>
            </a>
          ))}
        </div>
      ) : null}
      {items.length ? (
        <div className="needs-box">
          <strong>Needs you · {items.length}</strong>
          {items.map((i) => (
            <a key={i.id} href={href({ name: 'attention', item: i.id })}>
              {i.title}
            </a>
          ))}
        </div>
      ) : null}
      <div className="actions">
        <a className="btn btn-primary grow" href={href({ name: 'project', id: p.project.id, tab: 'conversation' })}>
          Open conversation
        </a>
        <a className="btn btn-secondary grow" href={href(items[0] ? { name: 'attention', item: items[0].id } : { name: 'attention' })}>
          Attention
        </a>
      </div>
    </article>
  );
}
```

`apps/desktop/src/renderer/map/ProjectList.tsx`:

```tsx
import type { ProjectSummary } from '@desk/protocol';
import { href } from '../router';
import { useUnread } from '../state/unread';
import { projectSummaryLine } from './OrbitMap';

function ProjectCard({ p }: { p: ProjectSummary }) {
  const unread = useUnread(p);
  return (
    <a className="card project-card" href={href({ name: 'project', id: p.project.id, tab: 'conversation' })}>
      <h2>
        {p.project.name}
        {unread ? <span className="unread-dot" aria-label="unread" /> : null}
      </h2>
      {p.project.goal ? <p className="subtitle">{p.project.goal}</p> : null}
      <span className="muted">{projectSummaryLine(p)}</span>
      {p.latest_report ? <span>{p.latest_report.headline}</span> : null}
      {p.attention_count ? <span className="needs-count">{p.attention_count} need you</span> : null}
    </a>
  );
}

/** The map's list alternative. */
export function ProjectList({ projects }: { projects: ProjectSummary[] }) {
  return (
    <div className="project-list">
      {projects.map((p) => (
        <ProjectCard key={p.project.id} p={p} />
      ))}
    </div>
  );
}
```

`apps/desktop/src/renderer/map/MapScreen.tsx`:

```tsx
import { useMemo, useState } from 'react';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { Sheet } from '../components/Sheet';
import { plural } from '../format';
import { navigate } from '../router';
import { ProjectForm } from '../screens/ProjectForm';
import { useGlobal } from '../state/global';
import { useNow } from '../state/now';
import { activityOf, layoutMap } from './layout';
import { MapCanvas } from './MapCanvas';
import { OrbitMap } from './OrbitMap';
import { ProjectList } from './ProjectList';
import { TerritoryInspector } from './TerritoryInspector';
import './map.css';

const VIEW_KEY = 'desk.mapView';
const readView = (): 'map' | 'list' => {
  try {
    return localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'map';
  } catch {
    return 'map';
  }
};

function Legend() {
  return (
    <div className="map-legend">
      <span><span className="legend-disc legend-running" />Running</span>
      <span><span className="legend-disc legend-waiting" />Waiting on you</span>
      <span><span className="legend-disc legend-idle" />Idle</span>
      <span><span className="legend-dot legend-dot-running" />Thread running</span>
      <span><span className="legend-dot legend-dot-waiting" />Waiting</span>
      <span><span className="legend-dot legend-dot-needs" />Needs you</span>
      <span className="muted">Disc size = activity</span>
    </div>
  );
}

export function MapScreen({ newProject }: { newProject: boolean }) {
  const overview = useGlobal((s) => s.overview);
  const attention = useGlobal((s) => s.attention);
  const health = useGlobal((s) => s.health);
  const status = useGlobal((s) => s.connection.status);
  const proxy = useGlobal((s) => s.system.proxy);
  const now = useNow();
  const [view, setView] = useState(readView);
  const [picked, setPicked] = useState<string | null>(null);
  const [creating, setCreating] = useState(newProject);

  const running = overview.reduce((n, p) => n + p.threads.filter((t) => t.status === 'running').length, 0);
  const busy = overview.filter((p) => p.threads.some((t) => t.status === 'running')).length;
  const selected = overview.find((p) => p.project.id === picked) ?? overview.find((p) => p.attention_count > 0) ?? overview[0];
  const selectedItems = useMemo(() => attention.filter((i) => i.project_id === selected?.project.id), [attention, selected?.project.id]);
  const mapProjects = useMemo(() => overview.map((p) => ({ id: p.project.id, activity: activityOf(p), threads: p.threads })), [overview]);

  const choose = (v: 'map' | 'list') => {
    setView(v);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      // A convenience only.
    }
  };
  const close = () => {
    setCreating(false);
    if (newProject) navigate({ name: 'map' });
  };

  return (
    <div className="map-screen">
      <div className="map-head">
        <h1 className="title">Projects</h1>
        <p className="subtitle">
          {plural(running, 'thread')} running in {plural(busy, 'project')}. {attention.length === 1 ? '1 thing is' : `${attention.length} things are`} waiting on you.
        </p>
        <div className="segmented" role="group" aria-label="View">
          <button type="button" aria-pressed={view === 'map'} onClick={() => choose('map')}>
            Map
          </button>
          <button type="button" aria-pressed={view === 'list'} onClick={() => choose('list')}>
            List
          </button>
        </div>
      </div>

      {!overview.length ? (
        <div className="page">
          <EmptyState title="No projects yet" action={<Button onClick={() => setCreating(true)}>Create a project</Button>}>
            A project is a goal Desk works toward with its own threads, library and memory.
          </EmptyState>
        </div>
      ) : view === 'map' ? (
        <>
          <MapCanvas label="Projects map">
            {(size) => {
              const width = size.width > 900 ? size.width - 380 : size.width;
              const layout = layoutMap(mapProjects, width, size.height);
              return (
                <OrbitMap
                  layout={layout}
                  projects={overview}
                  attention={attention}
                  selected={selected?.project.id ?? null}
                  onSelect={setPicked}
                  width={width}
                  height={size.height}
                  now={now}
                  sunLabel={[`${health?.version ?? ''} · ${status === 'live' ? 'running' : status}`, `proxy ${proxy} · ${plural(running, 'thread')} running`]}
                  sunAria={`deskd, ${status === 'live' ? 'running' : status}, model proxy ${proxy}`}
                />
              );
            }}
          </MapCanvas>
          <Legend />
          {selected ? <TerritoryInspector p={selected} items={selectedItems} now={now} /> : null}
        </>
      ) : (
        <div className="page">
          <ProjectList projects={overview} />
        </div>
      )}

      <button type="button" className="new-project-btn" onClick={() => setCreating(true)}>
        <span aria-hidden="true">+</span> New project <span className="muted">· brief Desk in a sentence</span>
      </button>

      {creating ? (
        <Sheet title="New project" onClose={close}>
          <ProjectForm
            onCancel={close}
            onCreated={(id) => {
              setCreating(false);
              navigate({ name: 'project', id, tab: 'conversation' });
            }}
          />
        </Sheet>
      ) : null}
    </div>
  );
}
```

`apps/desktop/src/renderer/map/map.css`:

```css
.map-screen {
  position: relative;
  height: 100%;
  overflow: hidden;
}
.map-head {
  position: absolute;
  left: 32px;
  top: 24px;
  z-index: 5;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 320px;
  pointer-events: none;
}
.map-head > * {
  pointer-events: auto;
}
.map-screen > .page {
  padding-top: 150px;
}
.segmented {
  display: inline-flex;
  width: max-content;
  padding: 2px;
  border-radius: 8px;
  background: var(--nav);
}
.segmented button {
  height: 26px;
  padding: 0 12px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-min);
  font-size: 12.5px;
  cursor: pointer;
}
.segmented button[aria-pressed='true'] {
  background: #fff;
  color: var(--ink);
  font-weight: 500;
}
.map-canvas {
  position: absolute;
  inset: 0;
  overflow: hidden;
  cursor: grab;
  touch-action: none;
}
.map-layer {
  position: absolute;
  inset: 0;
  transform-origin: 0 0;
}
.map-zoom {
  position: absolute;
  right: 396px;
  top: 20px;
  display: flex;
  gap: 4px;
}
.map-zoom button {
  min-width: 28px;
  height: 28px;
  padding: 0 8px;
  border: 1px solid var(--rule);
  border-radius: 7px;
  background: #fffc;
  cursor: pointer;
}
.orbit-svg {
  position: absolute;
  left: 0;
  top: 0;
}
.orbit-sun {
  position: absolute;
  width: 60px;
  height: 60px;
  transform: translate(-50%, -50%);
  border: 0;
  border-radius: 30px;
  background: var(--ink);
  color: #f7f5f0;
  font-family: var(--font-mono);
  font-size: 11px;
  cursor: pointer;
}
.orbit-sun-label {
  position: absolute;
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  font-size: 11.5px;
  color: var(--text-min);
  white-space: nowrap;
}
.orbit-label {
  position: absolute;
  transform: translate(-50%, -100%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  white-space: nowrap;
  pointer-events: none;
}
.orbit-label-name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
}
.orbit-label-sub {
  font-size: 11.5px;
}
.orbit-desk {
  position: absolute;
  transform: translate(-50%, -50%);
  border: 0;
  border-radius: 50%;
  background: var(--ink);
  color: #f7f5f0;
  font-family: var(--font-serif);
  font-size: 15px;
  cursor: pointer;
}
.orbit-desk-idle {
  background: #f7f5f0;
  color: var(--text-min);
  border: 1.5px solid var(--muted);
}
.orbit-thread {
  position: absolute;
  display: flex;
  align-items: center;
  gap: 6px;
  transform: translate(-7px, -50%);
  white-space: nowrap;
  text-decoration: none;
  font-size: 12px;
  font-weight: 500;
  color: var(--run-text);
}
.orbit-thread.left {
  flex-direction: row-reverse;
  transform: translate(calc(-100% + 7px), -50%);
}
.orbit-thread-dot {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  border-radius: 7px;
  background: var(--run);
  box-shadow: 0 0 0 4px rgba(47, 91, 211, 0.2);
}
.orbit-thread-waiting,
.orbit-thread-queued {
  color: var(--wait-text);
}
.orbit-thread-waiting .orbit-thread-dot,
.orbit-thread-queued .orbit-thread-dot {
  background: #fff;
  border: 2.5px solid var(--wait);
  box-shadow: none;
}
.orbit-thread-done,
.orbit-thread-cancelled,
.orbit-thread-idle {
  color: var(--text-min);
  font-size: 11.5px;
  font-weight: 400;
}
.orbit-thread-done .orbit-thread-dot,
.orbit-thread-cancelled .orbit-thread-dot,
.orbit-thread-idle .orbit-thread-dot {
  width: 10px;
  height: 10px;
  background: var(--muted-blue);
  box-shadow: none;
}
.orbit-thread-failed {
  color: var(--accent);
}
.orbit-thread-failed .orbit-thread-dot {
  background: #fff;
  border: 2.5px solid var(--accent);
  box-shadow: none;
}
.orbit-callout {
  position: absolute;
  display: flex;
  align-items: center;
  gap: 8px;
  transform: translate(-11px, -100%);
  text-decoration: none;
  color: var(--ink);
  z-index: 2;
}
.orbit-pin {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  border-radius: 11px;
  background: var(--accent);
  color: #fff;
  box-shadow: 0 0 0 4px var(--accent-tint);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11.5px;
  font-weight: 600;
}
.orbit-callout-card {
  width: 200px;
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 6px 10px;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 4px 14px rgba(28, 27, 24, 0.1);
  font-size: 12px;
  line-height: 1.35;
}
.orbit-callout-kind {
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
}
.unread-dot {
  width: 7px;
  height: 7px;
  border-radius: 4px;
  background: var(--accent);
  display: inline-block;
}
.map-legend {
  position: absolute;
  left: 32px;
  bottom: 24px;
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  align-items: center;
  max-width: calc(100% - 700px);
  min-width: 360px;
  padding: 10px 14px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.72);
  font-size: 12px;
  color: var(--text);
}
.map-legend > span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.legend-disc {
  width: 14px;
  height: 14px;
  border-radius: 7px;
  border: 1px solid;
}
.legend-running {
  background: #e3e8f5;
  border-color: #c9d3ec;
}
.legend-waiting {
  background: #f3e6cf;
  border-color: #e6d3af;
}
.legend-idle {
  background: #e6e1d7;
  border-color: #d6cfc1;
}
.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 5px;
}
.legend-dot-running {
  background: var(--run);
}
.legend-dot-waiting {
  background: #fff;
  border: 2px solid var(--wait);
}
.legend-dot-needs {
  background: var(--accent);
}
.new-project-btn {
  position: absolute;
  right: 28px;
  bottom: 24px;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 42px;
  padding: 0 18px;
  border: 1.5px dashed var(--muted);
  border-radius: 21px;
  background: #f7f5f0;
  font-size: 13px;
  font-weight: 500;
  box-shadow: 0 8px 20px rgba(28, 27, 24, 0.1);
  cursor: pointer;
}
.territory {
  position: absolute;
  right: 28px;
  top: 24px;
  z-index: 5;
  width: 336px;
  max-height: calc(100% - 110px);
  overflow: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.territory-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.territory-name {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 26px;
  font-weight: 500;
  line-height: 1.15;
}
.territory-quote {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.territory-quote p {
  margin: 0;
  font-family: var(--font-serif);
  font-style: italic;
  font-size: 16px;
  line-height: 1.4;
  color: var(--text);
}
.territory-quote span {
  font-size: 12px;
}
.territory-block {
  display: flex;
  flex-direction: column;
  gap: 7px;
  font-size: 12px;
}
.territory-row {
  display: flex;
  justify-content: space-between;
}
.waypoints {
  position: relative;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.waypoints-rule {
  position: absolute;
  left: 6px;
  right: 6px;
  top: 7px;
  border-top: 1.5px dashed var(--rule);
}
.waypoint {
  position: relative;
  width: 12px;
  height: 12px;
  border-radius: 6px;
  background: #fff;
  border: 2px solid #b9b3a7;
}
.waypoint-done {
  background: var(--ink);
  border-color: var(--ink);
}
.waypoint-run {
  border-color: var(--run);
}
.waypoint-wait {
  border-color: var(--wait);
}
.waypoint-dropped {
  border-style: dashed;
}
.territory-thread {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  text-decoration: none;
}
.grow {
  flex: 1;
}
.status-dot {
  width: 9px;
  height: 9px;
  flex-shrink: 0;
  border-radius: 5px;
  background: var(--muted-blue);
}
.status-dot-running {
  background: var(--run);
}
.status-dot-waiting,
.status-dot-queued {
  background: #fff;
  border: 2px solid var(--wait);
}
.status-dot-failed {
  background: var(--accent);
}
.territory-thread-status {
  font-size: 12px;
  color: var(--text-min);
}
.status-text-running {
  color: var(--run-text);
}
.status-text-waiting,
.status-text-queued {
  color: var(--wait-text);
}
.needs-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--accent-tint);
  font-size: 13px;
}
.needs-count {
  color: var(--accent);
}
a.btn {
  text-decoration: none;
}
```

In `App.tsx`, change the Map import to `import { MapScreen } from './map/MapScreen';` and delete `screens/MapScreen.tsx`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop
git commit -m "feat(desktop): orbit map — territories sized by activity, threads in orbit, needs-you pins, inspector and list view

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 3: Conversation — line diagram, chat, composer, plan route

**Files:**
- Create in `apps/desktop/src/renderer/`:
  - `conversation/lineGeometry.ts`, `conversation/lineGeometry.test.ts`
  - `conversation/LineDiagram.tsx`, `conversation/ChatItems.tsx`, `conversation/Composer.tsx`, `conversation/PlanPanel.tsx`, `conversation/ConversationScreen.tsx`, `conversation/conversation.css`
  - `conversation/ConversationScreen.test.tsx`
  - `components/ToolGroup.tsx`
- Modify: `router.ts`, which gains `file` on the project route; `router.test.ts`; `App.tsx`, which routes `conversation` here.

**Interfaces:**
- Consumes: `useSession`, `useNow`, `useGlobal`, `markSeen`, `call`, `SafeMarkdown`, `StatusChip`, `Button`, `toastError`, and `summarizeToolArgs` from `@desk/protocol`.
- Produces:

```ts
// router.ts: project route becomes { name: 'project'; id: string; tab: ProjectTab; threadId?: string; file?: string }  (file only for tab 'library': #/p/<id>/library?file=<path>)
// conversation/lineGeometry.ts
export const LANE_COLOR: Record<AgentStatus, string>;
export type LaneGeometry = { lane: Lane; thread: ThreadView | undefined; y: number; color: string; fork: string; segments: Array<{ d: string; color: string; dashed: boolean }>; rejoins: string[]; marks: Array<LaneMark & { x: number }>; signal: (LaneMark & { x: number }) | null; trainX: number | null };
export type LineGeometry = { width: number; height: number; x0: number; x1: number; trunkY: number; nowX: number; ticks: Array<{ x: number; t: number }>; stations: Array<Station & { x: number; showLabel: boolean }>; lanes: LaneGeometry[] };
export function lineGeometry(o: { timeline: TimelineState; threads: ThreadView[]; now: number; width: number; showArchived?: boolean }): LineGeometry;
// components/ToolGroup.tsx
export function toolNames(calls: ToolCallView[]): string;   // "read_file ×2 · skill_run"
export function ToolStatus(props: { status: ToolCallView['status'] }): JSX.Element;
export function ToolGroup(props: { calls: ToolCallView[]; title?: string; defaultOpen?: boolean }): JSX.Element;
// conversation/ConversationScreen.tsx
export function ConversationScreen(props: { projectId: string }): JSX.Element;
```

- [ ] **Step 1: Write the failing tests**

In `apps/desktop/src/renderer/router.test.ts`, add this to the `routes` array:

```ts
      { name: 'project', id: 'p1', tab: 'library', file: 'emails/01 welcome.md' },
```

`apps/desktop/src/renderer/conversation/lineGeometry.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { emptyTimeline, reduceTimeline, type ThreadView } from '@desk/client';
import { ev } from '@desk/client/testing';
import { lineGeometry } from './lineGeometry';

const at = (min: number) => new Date(Date.UTC(2026, 8, 24, 10, min)).toISOString();
const thread = (id: string, status: ThreadView['status'], activity: string | null = null) => ({ id, status, activity }) as ThreadView;

function timeline() {
  const d = { agent: 'd' };
  const created = (id: number, t: string, title: string, min: number) =>
    ev(id, 'agent.created', { role: 'thread', model: 'm', title, brief: 'b', workspace_path: '/w', parent_id: 'd' }, { agent: t, ts: at(min) });
  return [
    ev(1, 'message.user', { text: 'Relaunch onboarding' }, { ...d, ts: at(0) }),
    created(2, 'a', 'Research', 1),
    created(3, 'b', 'Emails', 1),
    ev(4, 'agent.status_changed', { status: 'running' }, { agent: 'a', ts: at(2) }),
    ev(5, 'agent.status_changed', { status: 'running' }, { agent: 'b', ts: at(2) }),
    ev(6, 'agent.model_switched', { from: 'opus', to: 'fable', reason: 'rate', scope: 'run' }, { agent: 'b', ts: at(10) }),
    ev(7, 'agent.result', { summary: 'Done', artifacts: [] }, { agent: 'a', ts: at(30) }),
    ev(8, 'agent.status_changed', { status: 'done' }, { agent: 'a', ts: at(30) }),
    ev(9, 'approval.requested', { approval_id: 'x', run_id: 'r', tool_call_id: 'c', tool: 'bash', arguments: '{}', reason: 'r', delegate_to_desk: false }, { agent: 'b', ts: at(40) }),
    ev(10, 'report', { headline: 'Research is in', progress: '', needs_you: [], results: [] }, { ...d, ts: at(31) }),
  ].reduce(reduceTimeline, emptyTimeline('d'));
}

describe('lineGeometry', () => {
  it('lays out time, stations and lanes', () => {
    const now = Date.parse(at(60));
    const g = lineGeometry({ timeline: timeline(), threads: [thread('a', 'done'), thread('b', 'running', 'bash · ls')], now, width: 1440 });
    expect(g.nowX).toBeCloseTo(g.x1);
    expect(g.ticks.length).toBeGreaterThan(2);
    expect(g.ticks.length).toBeLessThanOrEqual(9);
    for (let i = 1; i < g.ticks.length; i++) expect(g.ticks[i]!.x).toBeGreaterThan(g.ticks[i - 1]!.x);
    expect(g.stations.map((s) => s.kind)).toEqual(['brief', 'dispatch', 'result_in', 'report']);
    expect(g.stations[0]!.showLabel).toBe(true);
    const [a, b] = g.lanes;
    expect(b!.y).toBeGreaterThan(a!.y);
    expect(a!.rejoins).toHaveLength(1);
    expect(a!.trainX).toBeNull();
    expect(b!.trainX).toBe(g.nowX);
    expect(b!.marks.map((m) => m.kind)).toEqual(['detour', 'signal']);
    expect(b!.signal?.label).toBe('bash');
    expect(a!.signal).toBeNull();
    expect(g.height).toBeGreaterThan(b!.y);
  });

  it('draws a still-empty project around now', () => {
    const now = Date.parse(at(5));
    const g = lineGeometry({ timeline: emptyTimeline('d'), threads: [], now, width: 1200 });
    expect(g.lanes).toEqual([]);
    expect(g.nowX).toBeGreaterThan(g.x0);
  });
});
```

`apps/desktop/src/renderer/conversation/ConversationScreen.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import { ev } from '@desk/client/testing';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { resetSessions, setReleaseDelay, startSessionRouting } from '../state/session';
import { installBridge } from '../test/bridge';
import { ConversationScreen } from './ConversationScreen';

afterEach(cleanup);
beforeEach(() => {
  resetSessions();
  setReleaseDelay(0);
  localStorage.clear();
});

const overview = (): ProjectOverview => ({
  project: { id: 'p', name: 'Onboarding revamp', goal: 'g', instructions: '', settings: { desk_model: 'claude-opus-5-5', thread_model: 'm', fallback_model: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, policy: [] }, created_at: 't', updated_at: 't', archived_at: null },
  desk: { id: 'd', project_id: 'p', role: 'desk', status: 'idle', model: 'claude-opus-5-5', title: 'Desk', brief: null, workspace_path: '/w', parent_id: null, inbox_cursor: 0, review_round: 0, result_summary: null, result_artifacts: null, active_skills: [], git_source_id: null, git_branch: null, git_base: null, git_common_dir: null, archived_at: null, created_at: 't', updated_at: 't' },
  sources: [],
  plan: { project_id: 'p', items: [{ id: '1', title: 'Add a setup checklist', status: 'in_progress', thread_ids: ['t'], notes: '' }], updated_at: 't' },
  threads: [],
  approvals: [],
  last_seq: 1,
});

const events = [
  ev(1, 'project.created', { name: 'Onboarding revamp', goal: 'g', instructions: '' }),
  ev(2, 'message.user', { text: 'Relaunch onboarding next month' }, { agent: 'd' }),
  ev(3, 'agent.created', { role: 'thread', model: 'm', title: 'Signup checklist', brief: 'Build it', workspace_path: '/w/t', parent_id: 'd' }, { agent: 't' }),
  ev(4, 'agent.status_changed', { status: 'running' }, { agent: 't' }),
  ev(5, 'report', { headline: 'Research is in', progress: 'All three competitors lead with one first win.', needs_you: ['Approve installing bun'], results: ['competitor-onboarding.md'] }, { agent: 'd' }),
  ev(6, 'question.asked', { question: 'Data source or teammate invite first?', options: ['Connect a data source', 'Invite a teammate'] }, { agent: 'd' }),
];

function setup(extra: Record<string, (input: any) => unknown> = {}) {
  globalStore.set({ ...initialGlobalState(), connection: { status: 'live' }, attention: [{ id: 'report:5:0', kind: 'needs_you', project_id: 'p', project_name: 'Onboarding revamp', agent_id: 'd', title: 'Approve installing bun', detail: '', created_at: '', ref: { event_id: 5 } }] });
  const bridge = installBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    'projects.send': () => ({ ok: true }),
    ...extra,
  });
  startSessionRouting();
  render(<ConversationScreen projectId="p" />);
  return bridge;
}

describe('ConversationScreen', () => {
  it('shows the line diagram, the chat, the report and the plan', async () => {
    setup();
    await screen.findByRole('heading', { name: 'Research is in' });
    expect(screen.getAllByRole('link', { name: /Signup checklist/ })[0]!.getAttribute('href')).toBe('#/p/p/threads/t');
    expect(screen.getByRole('button', { name: /Your brief|Relaunch onboarding/ })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'competitor-onboarding.md' }).getAttribute('href')).toBe('#/p/p/library?file=competitor-onboarding.md');
    expect(screen.getByRole('link', { name: 'Approve installing bun' }).getAttribute('href')).toBe('#/attention?item=report%3A5%3A0');
    const plan = screen.getByRole('complementary', { name: 'Plan and Desk' });
    expect(plan.textContent).toContain('Add a setup checklist');
    expect(plan.textContent).toContain('claude-opus-5-5');
  });

  it('answers a question with an option and shows the message as sending', async () => {
    const bridge = setup();
    fireEvent.click(await screen.findByRole('button', { name: 'Connect a data source' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.send')?.input).toEqual({ id: 'p', text: 'Connect a data source' }));
    expect(await screen.findByText('You · sending…')).toBeTruthy();
    bridge.emit('desk:event', ev(7, 'message.user', { text: 'Connect a data source' }, { agent: 'd' }));
    await waitFor(() => expect(screen.queryByText('You · sending…')).toBeNull());
    expect(screen.queryByRole('button', { name: 'Connect a data source' })).toBeNull();
  });

  it('sends with Enter and attaches files through the Library', async () => {
    const bridge = setup({ 'library.upload': ({ file }: { file: { name: string } }) => ({ id: 'a1', path: `uploads/${file.name}` }) });
    const box = (await screen.findByLabelText('Message Desk')) as HTMLTextAreaElement;
    fireEvent.change(screen.getByTestId('attach-input'), { target: { files: [new File(['hi'], 'notes.md')] } });
    await waitFor(() => expect(box.value).toContain('Attached: uploads/notes.md'));
    expect(bridge.calls.find((c) => c.channel === 'library.upload')?.input).toEqual({ projectId: 'p', file: { name: 'notes.md', content_base64: 'aGk=' } });
    fireEvent.change(box, { target: { value: 'Use these notes' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(bridge.calls.filter((c) => c.channel === 'projects.send').map((c) => c.input)).toEqual([{ id: 'p', text: 'Use these notes' }]));
    await waitFor(() => expect(box.value).toBe(''));
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop/src/renderer/conversation apps/desktop/src/renderer/router.test.ts`
Expected: FAIL: the modules are missing and `file` is not parsed.

- [ ] **Step 3: Implement**

In `apps/desktop/src/renderer/router.ts`:
- Change the project route type to `{ name: 'project'; id: string; tab: ProjectTab; threadId?: string; file?: string }`.
- In `parseRoute`, in the `'p'` case, after computing `tab`:

```ts
      if (tab === 'threads' && parts[3]) return { name: 'project', id, tab, threadId: parts[3] };
      const file = q.get('file');
      return tab === 'library' && file ? { name: 'project', id, tab, file } : { name: 'project', id, tab };
```

- In `href`, make the project case:

```ts
    case 'project':
      return `#/p/${enc(r.id)}/${r.tab}${r.threadId ? `/${enc(r.threadId)}` : ''}${r.file ? `?file=${enc(r.file)}` : ''}`;
```

`apps/desktop/src/renderer/components/ToolGroup.tsx`:

```tsx
import type { ToolCallView } from '@desk/client';
import { summarizeToolArgs } from '@desk/protocol';
import { plural } from '../format';

/** "read_file ×2 · skill_run", in first-use order. */
export function toolNames(calls: ToolCallView[]): string {
  const counts = new Map<string, number>();
  for (const c of calls) counts.set(c.name, (counts.get(c.name) ?? 0) + 1);
  return [...counts].map(([n, k]) => (k > 1 ? `${n} ×${k}` : n)).join(' · ');
}

const LABEL: Record<ToolCallView['status'], string> = { running: 'running', ok: 'ok', error: 'error', denied: 'denied', interrupted: 'interrupted' };

export function ToolStatus({ status }: { status: ToolCallView['status'] }) {
  return (
    <span className={`tool-status tool-status-${status}`}>
      {status === 'running' ? <span className="live-dot" aria-hidden="true" /> : null}
      {LABEL[status]}
    </span>
  );
}

/** A collapsible group of tool calls (the Narrative depth). */
export function ToolGroup({ calls, title, defaultOpen = false }: { calls: ToolCallView[]; title?: string; defaultOpen?: boolean }) {
  const live = calls.some((c) => c.status === 'running');
  return (
    <details className="toolgroup" open={defaultOpen || undefined}>
      <summary>
        <span className="toolgroup-title">{title ?? `${live ? 'Using' : 'Used'} ${plural(calls.length, 'tool')}`}</span>
        <span className="toolgroup-names">{toolNames(calls)}</span>
      </summary>
      <ul>
        {calls.map((c) => (
          <li key={c.id}>
            <span className="mono grow">
              {c.name} <span className="muted">{summarizeToolArgs(c.arguments, 70)}</span>
            </span>
            <ToolStatus status={c.status} />
          </li>
        ))}
      </ul>
    </details>
  );
}
```

`apps/desktop/src/renderer/conversation/lineGeometry.ts`:

```ts
import type { Lane, LaneMark, Station, ThreadView, TimelineState } from '@desk/client';
import type { AgentStatus } from '@desk/protocol';

export const LANE_COLOR: Record<AgentStatus, string> = {
  running: '#2F5BD3',
  waiting: '#A15C00',
  queued: '#A15C00',
  idle: '#B9B3A7',
  done: '#8A857B',
  cancelled: '#8A857B',
  failed: '#C4441C',
};

const TICK_MINUTES = [1, 2, 5, 10, 15, 30, 60, 120, 240, 480, 720, 1440, 2880, 10080];
export const LABEL_W = 200;
const LEGEND_W = 120;
const TRUNK_Y = 62;
const FIRST_LANE = 44;
const LANE_GAP = 36;
const CURVE = 30;

export type LaneGeometry = {
  lane: Lane;
  thread: ThreadView | undefined;
  y: number;
  color: string;
  fork: string;
  segments: Array<{ d: string; color: string; dashed: boolean }>;
  rejoins: string[];
  marks: Array<LaneMark & { x: number }>;
  signal: (LaneMark & { x: number }) | null;
  trainX: number | null;
};

export type LineGeometry = {
  width: number;
  height: number;
  x0: number;
  x1: number;
  trunkY: number;
  nowX: number;
  ticks: Array<{ x: number; t: number }>;
  stations: Array<Station & { x: number; showLabel: boolean }>;
  lanes: LaneGeometry[];
};

/** Positions for the conversation's transit diagram: a time axis, Desk's trunk with stations, one lane per thread. */
export function lineGeometry(o: { timeline: TimelineState; threads: ThreadView[]; now: number; width: number; showArchived?: boolean }): LineGeometry {
  const x0 = LABEL_W + 10;
  const x1 = Math.max(x0 + 240, o.width - LEGEND_W - 30);
  const start = o.timeline.start ? Date.parse(o.timeline.start) : o.now - 30 * 60_000;
  const end = Math.max(o.now, start + 10 * 60_000);
  const x = (t: string | number) => x0 + (((typeof t === 'number' ? t : Date.parse(t)) - start) / (end - start)) * (x1 - x0);
  const step = (TICK_MINUTES.find((m) => (end - start) / 60_000 / m <= 8) ?? 10080) * 60_000;
  const ticks: Array<{ x: number; t: number }> = [];
  for (let t = Math.ceil(start / step) * step; t <= end; t += step) ticks.push({ x: x(t), t });
  const nowX = x(o.now);

  const byId = new Map(o.threads.map((t) => [t.id, t]));
  const visible = o.timeline.lanes.filter((l) => o.showArchived || !l.archived);
  const lanes = visible.map((lane, i): LaneGeometry => {
    const y = TRUNK_Y + FIRST_LANE + i * LANE_GAP;
    const xf = x(lane.forkedAt);
    const laneStart = xf + CURVE * 2;
    const terminal = lane.status === 'done' || lane.status === 'cancelled' || lane.status === 'failed';
    const segments = lane.segments
      .map((seg, idx) => {
        const last = idx === lane.segments.length - 1;
        const a = Math.max(laneStart, x(seg.from));
        const b = Math.max(a, last ? (terminal ? a : nowX) : x(seg.to ?? o.now));
        return { a, b, status: seg.status };
      })
      .filter((s) => s.b - s.a > 0.5)
      .map((s) => ({ d: `M${s.a} ${y} H${s.b}`, color: LANE_COLOR[s.status], dashed: s.status === 'idle' || s.status === 'queued' }));
    const marks = lane.marks.map((m) => ({ ...m, x: Math.max(laneStart, x(m.ts)) }));
    const rejoins = marks
      .filter((m) => m.kind === 'rejoin')
      .map((m) => `M${m.x} ${y} C${m.x + CURVE} ${y} ${m.x + CURVE} ${TRUNK_Y} ${m.x + CURVE * 2} ${TRUNK_Y}`);
    let signal: (LaneMark & { x: number }) | null = null;
    for (const m of marks) {
      if (m.kind === 'signal') signal = m;
      if (m.kind === 'signal_cleared') signal = null;
    }
    return {
      lane,
      thread: byId.get(lane.threadId),
      y,
      color: LANE_COLOR[lane.status],
      fork: `M${xf} ${TRUNK_Y} C${xf + CURVE} ${TRUNK_Y} ${xf + CURVE} ${y} ${laneStart} ${y}`,
      segments,
      rejoins,
      marks,
      signal,
      trainX: byId.get(lane.threadId)?.status === 'running' ? nowX : null,
    };
  });

  let lastLabel = -Infinity;
  const stations = o.timeline.stations.map((s) => {
    const sx = x(s.ts);
    const showLabel = sx - lastLabel >= 120;
    if (showLabel) lastLabel = sx;
    return { ...s, x: sx, showLabel };
  });

  const height = Math.max(120, TRUNK_Y + FIRST_LANE + Math.max(0, lanes.length - 1) * LANE_GAP + 34);
  return { width: o.width, height, x0, x1, trunkY: TRUNK_Y, nowX, ticks, stations, lanes };
}
```

`apps/desktop/src/renderer/conversation/LineDiagram.tsx`:

```tsx
import type { ProjectState, ThreadView } from '@desk/client';
import type { AgentStatus } from '@desk/protocol';
import { ago, clock, duration } from '../format';
import { href } from '../router';
import type { LaneGeometry, LineGeometry } from './lineGeometry';

type StationG = LineGeometry['stations'][number];

function laneStatus(l: LaneGeometry, reviewRounds: number, waitingOnYou: boolean, now: number): { text: string; tone: AgentStatus } {
  const t = l.thread;
  const status = t?.status ?? l.lane.status;
  const since = ago(l.lane.forkedAt, now);
  switch (status) {
    case 'running':
      return { text: t?.review_round ? `revision ${t.review_round} of ${reviewRounds} · ${since}` : `running · ${since}`, tone: status };
    case 'waiting':
      return { text: waitingOnYou ? `waiting on you · ${since}` : `waiting · ${since}`, tone: status };
    case 'queued':
      return { text: /restart/i.test(t?.reason ?? '') ? 'will resume' : 'queued', tone: status };
    case 'done': {
      const last = l.lane.segments.at(-1);
      return { text: `done · ${duration(Date.parse(last?.from ?? l.lane.forkedAt) - Date.parse(l.lane.forkedAt))}`, tone: status };
    }
    default:
      return { text: status, tone: status };
  }
}

const shortModel = (m: string) => m.replace(/^claude-/, '');

/** The transit diagram: Desk's trunk with stations, thread lanes forking and rejoining, trains at "now". */
export function LineDiagram(o: {
  g: LineGeometry;
  project: ProjectState;
  now: number;
  onStation(s: StationG): void;
}) {
  const { g, project } = o;
  const desk = project.desk;
  const pendingApproval = (threadId: string) => project.approvals.find((a) => a.agent_id === threadId && !a.delegate_to_desk);
  return (
    <section className="line-diagram" aria-label="Line diagram: Desk and its threads since the brief" style={{ height: g.height }}>
      <svg width={g.width} height={g.height} aria-hidden="true" className="line-svg">
        {g.ticks.map((t) => (
          <path key={t.t} d={`M${t.x} 24 V${g.height}`} stroke="#E3DFD6" strokeDasharray="2 4" />
        ))}
        <path d={`M${g.nowX} 22 V${g.height}`} stroke="#4A4740" strokeDasharray="3 3" />
        {g.lanes.map((l) => (
          <g key={l.lane.threadId} opacity={l.lane.archived ? 0.45 : 1}>
            <path d={l.fork} fill="none" stroke={l.segments[0]?.color ?? l.color} strokeWidth={4} strokeLinecap="round" />
            {l.segments.map((s, i) => (
              <path key={i} d={s.d} fill="none" stroke={s.color} strokeWidth={4} strokeLinecap="round" strokeDasharray={s.dashed ? '3 5' : undefined} />
            ))}
            {l.rejoins.map((d, i) => (
              <path key={`r${i}`} d={d} fill="none" stroke="#8A857B" strokeWidth={4} strokeLinecap="round" />
            ))}
            {l.marks
              .filter((m) => m.kind === 'detour')
              .map((m) => (
                <g key={`d${m.eventId}`}>
                  <path d={`M${m.x - 16} ${l.y} C${m.x - 8} ${l.y} ${m.x - 8} ${l.y - 12} ${m.x} ${l.y - 12} C${m.x + 8} ${l.y - 12} ${m.x + 8} ${l.y} ${m.x + 16} ${l.y}`} fill="none" stroke="#EFEAE0" strokeWidth={9} />
                  <path d={`M${m.x - 16} ${l.y} C${m.x - 8} ${l.y} ${m.x - 8} ${l.y - 12} ${m.x} ${l.y - 12} C${m.x + 8} ${l.y - 12} ${m.x + 8} ${l.y} ${m.x + 16} ${l.y}`} fill="none" stroke={l.color} strokeWidth={4} strokeLinecap="round" />
                </g>
              ))}
          </g>
        ))}
        <path d={`M${g.x0 - 6} ${g.trunkY} H${g.nowX}`} stroke="#1C1B18" strokeWidth={6} strokeLinecap="round" />
      </svg>

      {g.ticks.map((t) => (
        <span key={t.t} className="line-tick" style={{ left: t.x }}>
          {clock(t.t)}
        </span>
      ))}
      <span className="line-now" style={{ left: g.nowX }}>
        now {clock(o.now)}
      </span>

      <div className="line-label" style={{ top: g.trunkY - 15 }}>
        <span className="line-label-title">
          <span className="line-swatch line-swatch-desk" />
          Desk
        </span>
        <span className={`line-label-sub status-text-${desk?.status ?? 'idle'}`}>
          {desk ? `${desk.status === 'running' ? 'writing' : desk.status} · ${shortModel(desk.model_override ?? desk.model)}` : ''}
        </span>
      </div>
      {g.lanes.map((l) => {
        const approval = pendingApproval(l.lane.threadId);
        const s = laneStatus(l, project.project.settings.review_rounds, !!approval, o.now);
        return (
          <a key={l.lane.threadId} className="line-label" style={{ top: l.y - 15 }} href={href({ name: 'project', id: project.project.id, tab: 'threads', threadId: l.lane.threadId })}>
            <span className="line-label-title">
              <span className="line-swatch" style={{ background: l.color }} />
              {l.lane.title}
            </span>
            <span className={`line-label-sub status-text-${s.tone}`}>{s.text}</span>
          </a>
        );
      })}

      {g.stations.map((st) => (
        <div key={st.eventId}>
          <button
            type="button"
            className={`line-station line-station-${st.kind}`}
            style={{ left: st.x, top: g.trunkY }}
            aria-label={`${clock(st.ts)}, ${st.label}`}
            title={`${clock(st.ts)} · ${st.label}`}
            onClick={() => o.onStation(st)}
          />
          {st.showLabel ? (
            <span className="line-station-label" style={{ left: st.x, top: g.trunkY - 31 }}>
              <span className="mono muted">{clock(st.ts)}</span> {st.kind === 'brief' ? `Your brief · ${st.label}` : st.label}
            </span>
          ) : null}
        </div>
      ))}

      {g.lanes.flatMap((l) =>
        l.marks
          .filter((m) => m.kind === 'detour' || m.kind === 'sent_back' || m.kind === 'stalled')
          .map((m) => (
            <span key={`${l.lane.threadId}-${m.eventId}`} className={`line-mark line-mark-${m.kind}`} style={{ left: m.x, top: m.kind === 'detour' ? l.y + 8 : l.y - 24 }}>
              {m.kind === 'detour' ? (
                <>
                  <span className="mono">
                    {clock(m.ts)} {shortModel(m.label)}
                  </span>
                  <span className="sr-only">: rate limited, continued on the fallback model</span>
                </>
              ) : m.kind === 'sent_back' ? (
                `sent back · ${m.label}`
              ) : (
                'stalled'
              )}
            </span>
          )),
      )}

      {g.lanes.map((l) => {
        const approval = l.signal ? pendingApproval(l.lane.threadId) : undefined;
        return l.signal ? (
          <a key={`sig-${l.lane.threadId}`} className="line-signal" style={{ left: l.signal.x, top: l.y }} href={href({ name: 'attention', ...(approval ? { item: `approval:${approval.id}` } : {}) })}>
            <span className="line-signal-chip">
              <strong>Waiting for your approval</strong> · <span className="mono">{l.signal.label}</span> · <span className="mono muted">{clock(l.signal.ts)}</span>
            </span>
            <span className="line-signal-dot" aria-hidden="true" />
          </a>
        ) : null;
      })}

      {g.lanes.map((l) =>
        l.trainX !== null ? (
          <div key={`train-${l.lane.threadId}`}>
            {l.thread?.activity ? (
              <span className="line-activity" style={{ left: l.trainX - 18, top: l.y - 26 }}>
                {l.thread.activity}
              </span>
            ) : null}
            <a
              className="line-train"
              style={{ left: l.trainX, top: l.y }}
              href={href({ name: 'project', id: project.project.id, tab: 'threads', threadId: l.lane.threadId })}
              aria-label={`${l.lane.title}, running now`}
            >
              <span aria-hidden="true" />
            </a>
          </div>
        ) : null,
      )}

      {desk?.status === 'running' ? (
        <span className="line-desk-writing" style={{ left: g.nowX - 10, top: g.trunkY }}>
          <span className="live-dot" aria-hidden="true" />
          Desk · writing
        </span>
      ) : null}

      <div className="line-legend" aria-hidden="true">
        <span><span className="line-swatch line-swatch-desk" />Desk</span>
        <span><span className="line-swatch" style={{ background: '#2F5BD3' }} />running</span>
        <span><span className="line-swatch" style={{ background: '#8A857B' }} />done</span>
        <span><span className="line-swatch" style={{ background: '#A15C00' }} />waiting</span>
        <span><span className="line-legend-dot" />needs you</span>
        <span>
          <svg width="18" height="10" viewBox="0 0 18 10">
            <path d="M1 8 H4 C6 8 6 2 9 2 C12 2 12 8 14 8 H17" fill="none" stroke="#2F5BD3" strokeWidth="2" strokeLinecap="round" />
          </svg>
          fallback
        </span>
      </div>
    </section>
  );
}

export type { ThreadView };
```

`apps/desktop/src/renderer/conversation/ChatItems.tsx`:

```tsx
import type { AttentionItem } from '@desk/protocol';
import type { ChatItem } from '@desk/client';
import { Button } from '../components/Button';
import { SafeMarkdown } from '../components/SafeMarkdown';
import { ToolGroup } from '../components/ToolGroup';
import { clock } from '../format';
import { href } from '../router';

const FEED: Record<string, string> = {
  note: 'Note',
  update: 'Update',
  question: 'Question',
  blocker: 'Blocked',
  completed: 'Result',
  failed: 'Failed',
  cancelled: 'Stopped',
  approval: 'Approval',
  stalled: 'Stalled',
  revision: 'Sent back',
};

export const chatDomId = (id: string) => `chat-${id.replace(/[^a-zA-Z0-9_-]/g, '-')}`;

/** The event id a chat item came from (streaming runs sort last). */
export function chatEventId(item: ChatItem): number {
  const n = Number(item.id.split(':')[1]);
  return Number.isFinite(n) ? n : Number.POSITIVE_INFINITY;
}

export function ChatItemView(o: {
  item: ChatItem;
  projectId: string;
  attentionIds: Set<string>;
  answering: string | null;
  onAnswer(text: string): void;
  onOwnWords(): void;
}) {
  const { item } = o;
  switch (item.kind) {
    case 'user':
      return (
        <div className="chat-user">
          <span className="chat-meta">You · {clock(item.ts)}</span>
          <p>{item.text}</p>
        </div>
      );
    case 'assistant':
      return (
        <div className="chat-desk" aria-live={item.streaming ? 'polite' : undefined}>
          <span className="chat-meta">
            {item.streaming ? (
              <>
                <span className="live-dot" aria-hidden="true" />
                Desk · writing
              </>
            ) : (
              `Desk · ${clock(item.ts)}`
            )}
          </span>
          <SafeMarkdown className="md-voice" text={item.text} />
          {item.interrupted ? <span className="muted small">Cut off by an error; Desk will pick up again.</span> : null}
        </div>
      );
    case 'tools':
      return <ToolGroup calls={item.calls} title={`Desk ${item.calls.some((c) => c.status === 'running') ? 'is using' : 'used'} ${item.calls.length} tool${item.calls.length === 1 ? '' : 's'}`} />;
    case 'agent':
      return (
        <div className={`chat-feed feed-${item.messageKind}`}>
          <span className="feed-chip">{FEED[item.messageKind] ?? item.messageKind}</span>
          <a className="feed-from" href={href({ name: 'project', id: o.projectId, tab: 'threads', threadId: item.fromAgentId })}>
            {item.fromLabel}
          </a>
          <span className="muted small">{clock(item.ts)}</span>
          <SafeMarkdown className="feed-text" text={item.text} />
        </div>
      );
    case 'report':
      return (
        <article className="report-card" aria-labelledby={`report-${item.eventId}`}>
          <div className="report-main">
            <span className="eyebrow">Report · {clock(item.ts)}</span>
            <h2 id={`report-${item.eventId}`}>{item.headline}</h2>
            {item.progress ? <SafeMarkdown className="report-progress" text={item.progress} /> : null}
            {item.results.length ? (
              <div className="report-results">
                <strong>Results</strong>
                {item.results.map((r) => (
                  <a key={r} className="file-chip" href={href({ name: 'project', id: o.projectId, tab: 'library', file: r })}>
                    {r}
                  </a>
                ))}
              </div>
            ) : null}
          </div>
          {item.needsYou.length ? (
            <div className="needs-box report-needs">
              <strong>Needs you · {item.needsYou.length}</strong>
              {item.needsYou.map((text, i) => {
                const id = `report:${item.eventId}:${i}`;
                return (
                  <div key={id} className="needs-row">
                    <span className="needs-num" aria-hidden="true">
                      {i + 1}
                    </span>
                    {o.attentionIds.has(id) ? <a href={href({ name: 'attention', item: id })}>{text}</a> : <span className="needs-done">{text}</span>}
                  </div>
                );
              })}
            </div>
          ) : null}
        </article>
      );
    case 'question':
      return (
        <section className={`question-card${item.answered ? ' answered' : ''}`} aria-labelledby={`question-${item.eventId}`}>
          <span className="eyebrow">
            <span className="accent-dot" aria-hidden="true" />
            Desk asks · {clock(item.ts)}
          </span>
          <p id={`question-${item.eventId}`} className="question-text">
            {item.question}
          </p>
          {item.answered ? (
            <span className="muted small">Answered</span>
          ) : (
            <div className="actions">
              {item.options.map((opt, i) => (
                <Button key={opt} size="sm" variant={i === 0 ? 'primary' : 'secondary'} pending={o.answering === opt} disabled={o.answering !== null} onClick={() => o.onAnswer(opt)}>
                  {opt}
                </Button>
              ))}
              <button type="button" className="link small" onClick={o.onOwnWords}>
                Answer in your own words…
              </button>
            </div>
          )}
        </section>
      );
    case 'notice':
      return (
        <div className={`chat-notice notice-${item.level}`} role="status">
          <span className={`dot ${item.level === 'error' ? 'warn' : item.level === 'warning' ? 'warn' : 'ok'}`} aria-hidden="true" />
          {item.code === 'proxy_down' ? 'Paused, will resume: the model proxy is unreachable.' : item.message}
        </div>
      );
    case 'compacted':
      return (
        <div className="chat-divider" role="separator">
          Earlier conversation summarised
        </div>
      );
  }
}

export type { AttentionItem };
```

`apps/desktop/src/renderer/conversation/Composer.tsx`:

```tsx
import { useRef, useState, type KeyboardEvent, type RefObject } from 'react';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { toastError } from '../components/Toast';

const MAX_UPLOAD = 25 * 1024 * 1024;

export async function fileToBase64(file: Blob): Promise<string> {
  const buf = new Uint8Array(await file.arrayBuffer());
  let s = '';
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return btoa(s);
}

/** Message Desk: ⏎ sends, ⇧⏎ adds a line; attachments go to the Library and are referenced in the message. */
export function Composer(o: { projectId: string; draft: string; setDraft(v: string | ((d: string) => string)): void; textareaRef: RefObject<HTMLTextAreaElement | null>; onSent(text: string): void }) {
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);
  const id = `composer-${o.projectId}`;

  const send = async () => {
    const text = o.draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      await call('projects.send', { id: o.projectId, text });
      o.setDraft('');
      o.onSent(text);
    } catch (err) {
      toastError(err);
    } finally {
      setSending(false);
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    }
  };

  const attach = async (files: FileList | null) => {
    for (const f of Array.from(files ?? [])) {
      if (f.size > MAX_UPLOAD) {
        toastError(new Error(`${f.name} is larger than 25 MB.`));
        continue;
      }
      setUploading((n) => n + 1);
      try {
        const a = await call('library.upload', { projectId: o.projectId, file: { name: f.name, content_base64: await fileToBase64(f) } });
        o.setDraft((d) => `${d}${d && !d.endsWith('\n') ? '\n' : ''}Attached: ${a.path}\n`);
      } catch (err) {
        toastError(err);
      } finally {
        setUploading((n) => n - 1);
      }
    }
    if (fileRef.current) fileRef.current.value = '';
  };

  return (
    <div className="composer">
      <label htmlFor={id}>Message Desk</label>
      <textarea
        id={id}
        ref={o.textareaRef}
        rows={2}
        value={o.draft}
        onChange={(e) => o.setDraft(e.target.value)}
        onKeyDown={onKey}
        placeholder="Brief a new piece of work, answer a question, or change direction"
      />
      <div className="composer-bar">
        <button type="button" className="icon-btn" aria-label="Attach a file" disabled={uploading > 0} onClick={() => fileRef.current?.click()}>
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            <path d="M11.5 6.5L7 11a3 3 0 0 1-4.2-4.2l4.6-4.6a2 2 0 0 1 2.8 2.8L5.6 9.6a1 1 0 0 1-1.4-1.4L8.4 4" fill="none" stroke="#3D3A34" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <input ref={fileRef} type="file" multiple hidden data-testid="attach-input" onChange={(e) => void attach(e.target.files)} />
        <button
          type="button"
          className="link small"
          onClick={() => {
            o.setDraft((d) => d || 'Turn what we just did into a reusable skill: ');
            o.textareaRef.current?.focus();
          }}
        >
          Turn this into a skill
        </button>
        <span className="grow" />
        <span className="muted small">{uploading ? 'Uploading…' : '⏎ send · ⇧⏎ new line'}</span>
        <Button variant="primary" size="sm" pending={sending} disabled={!o.draft.trim()} onClick={() => void send()}>
          Send
        </Button>
      </div>
    </div>
  );
}
```

`apps/desktop/src/renderer/conversation/PlanPanel.tsx`:

```tsx
import type { ProjectState } from '@desk/client';
import type { PlanItem } from '@desk/protocol';
import { StatusChip } from '../components/StatusChip';
import { href } from '../router';

const STATUS: Record<PlanItem['status'], string> = { todo: 'To do', in_progress: 'In progress', done: 'Done', dropped: 'Dropped' };

/** The plan as a route of waypoints, the merge note, and Desk's card. */
export function PlanPanel({ project, proxyDown }: { project: ProjectState; proxyDown: boolean }) {
  const threads = new Map(project.threads.map((t) => [t.id, t]));
  const items = project.plan;
  const done = items.filter((i) => i.status === 'done').length;
  const branches = project.threads.filter((t) => t.git_branch && !t.archived_at).map((t) => t.git_branch!);
  const desk = project.desk;
  const s = project.project.settings;
  return (
    <aside className="card plan-panel" aria-label="Plan and Desk">
      <div className="plan-head">
        <h2>Plan</h2>
        <span className="chip chip-idle">{items.length ? `${done} of ${items.length} done` : 'No plan yet'}</span>
      </div>
      {items.length ? (
        <ol className="plan-route">
          {items.map((i) => {
            const linked = i.thread_ids.map((id) => threads.get(id)).filter((t) => t !== undefined);
            const waiting = linked.some((t) => t.status === 'waiting');
            const tone = i.status === 'in_progress' ? (waiting ? 'wait' : 'run') : i.status;
            return (
              <li key={i.id} className={`plan-stop plan-${tone}`}>
                <span className="plan-dot" aria-hidden="true" />
                <div className="plan-text">
                  <span className="plan-title">{i.title}</span>
                  <span className={`plan-sub plan-sub-${tone}`}>
                    {STATUS[i.status]}
                    {linked.map((t) => (
                      <span key={t.id}>
                        {' · '}
                        <a href={href({ name: 'project', id: project.project.id, tab: 'threads', threadId: t.id })}>{t.title ?? 'Thread'}</a>
                        {t.review_round && t.status !== 'done' ? ` · revision ${t.review_round}/${s.review_rounds}` : ''}
                      </span>
                    ))}
                    {waiting ? ' · waiting on you' : ''}
                    {i.status === 'todo' && !linked.length ? ' · Desk, once the threads report' : ''}
                  </span>
                  {i.notes ? <span className="muted small">{i.notes}</span> : null}
                </div>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="muted">Desk writes the plan once it understands the brief.</p>
      )}
      {branches.length ? (
        <div className="merge-note">
          <span>Desk never merges; you'll get a merge order.</span>
          {branches.map((b) => (
            <span key={b} className="mono small">
              {b}
            </span>
          ))}
        </div>
      ) : null}
      {desk ? (
        <div className="desk-card">
          <div className="desk-card-head">
            <span className="desk-avatar" aria-hidden="true">
              Desk
            </span>
            <span className="grow">
              <strong>Desk</strong>
              <span className="muted small"> coordinator</span>
            </span>
            <StatusChip status={desk.status} reason={desk.reason} proxyDown={proxyDown} />
          </div>
          <dl>
            <div>
              <dt>Model</dt>
              <dd className="mono">{desk.model_override ?? desk.model}</dd>
            </div>
            <div>
              <dt>Check-ins</dt>
              <dd>{s.check_in}</dd>
            </div>
            <div>
              <dt>Autonomy</dt>
              <dd>{s.autonomy === 'dispatch-freely' ? 'dispatches freely' : 'asks before dispatching'}</dd>
            </div>
          </dl>
        </div>
      ) : null}
    </aside>
  );
}
```

`apps/desktop/src/renderer/conversation/ConversationScreen.tsx`:

```tsx
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { call } from '../bridge';
import { EmptyState } from '../components/EmptyState';
import { toastError } from '../components/Toast';
import { plural } from '../format';
import { useGlobal } from '../state/global';
import { useNow } from '../state/now';
import { useSession } from '../state/session';
import { markSeen } from '../state/unread';
import { ChatItemView, chatDomId, chatEventId } from './ChatItems';
import { Composer } from './Composer';
import { LineDiagram } from './LineDiagram';
import { lineGeometry } from './lineGeometry';
import { PlanPanel } from './PlanPanel';
import './conversation.css';

function useDraft(projectId: string): [string, (v: string | ((d: string) => string)) => void] {
  const key = `desk.draft.${projectId}`;
  const [draft, setDraftState] = useState(() => {
    try {
      return localStorage.getItem(key) ?? '';
    } catch {
      return '';
    }
  });
  useEffect(() => {
    try {
      if (draft) localStorage.setItem(key, draft);
      else localStorage.removeItem(key);
    } catch {
      // Drafts are a convenience.
    }
  }, [key, draft]);
  return [draft, setDraftState];
}

function useWidth(ref: React.RefObject<HTMLElement | null>, fallback = 1200): number {
  const [w, setW] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setW(el.clientWidth || fallback);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, fallback]);
  return w;
}

export function ConversationScreen({ projectId }: { projectId: string }) {
  const s = useSession(projectId);
  const attention = useGlobal((g) => g.attention);
  const proxy = useGlobal((g) => g.system.proxy);
  const now = useNow();
  const [draft, setDraft] = useDraft(projectId);
  const [pending, setPending] = useState<string[]>([]);
  const [answering, setAnswering] = useState<string | null>(null);
  const [planOpen, setPlanOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const pinned = useRef(true);
  const width = useWidth(rootRef);

  const attentionIds = useMemo(() => new Set(attention.filter((i) => i.project_id === projectId).map((i) => i.id)), [attention, projectId]);
  const threads = s.project?.threads;
  const geometry = useMemo(() => lineGeometry({ timeline: s.timeline, threads: threads ?? [], now, width }), [s.timeline, threads, now, width]);

  useEffect(() => markSeen(projectId), [projectId, s.events.length]);
  useEffect(() => {
    if (!pending.length) return;
    const sent = new Set(s.chat.items.filter((i) => i.kind === 'user').slice(-20).map((i) => (i.kind === 'user' ? i.text : '')));
    setPending((p) => p.filter((t) => !sent.has(t)));
  }, [s.chat.items, pending.length]);
  useLayoutEffect(() => {
    const el = listRef.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [s.chat.items, pending]);

  if (s.status === 'loading') return <div className="page muted">Loading…</div>;
  if (s.status === 'missing')
    return (
      <div className="page">
        <EmptyState title="This project isn't here" action={<a href="#/map">Back to the map</a>}>
          It may have been archived.
        </EmptyState>
      </div>
    );
  if (s.status === 'error' || !s.project)
    return (
      <div className="page">
        <EmptyState title="Couldn't load this project">{s.error}</EmptyState>
      </div>
    );

  const project = s.project;
  const live = project.threads.filter((t) => !t.archived_at);
  const count = (st: string) => live.filter((t) => t.status === st).length;
  const answer = async (text: string) => {
    setAnswering(text);
    try {
      await call('projects.send', { id: projectId, text });
      setPending((p) => [...p, text]);
    } catch (err) {
      toastError(err);
    } finally {
      setAnswering(null);
    }
  };
  const onStation = (st: { eventId: number }) => {
    const target = s.chat.items.find((i) => chatEventId(i) >= st.eventId);
    const el = target ? document.getElementById(chatDomId(target.id)) : null;
    el?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    el?.classList.add('flash');
    setTimeout(() => el?.classList.remove('flash'), 1200);
  };

  return (
    <div className="conversation" ref={rootRef}>
      <LineDiagram g={geometry} project={project} now={now} onStation={onStation} />
      <div className={`conv-body${planOpen ? ' plan-open' : ''}`}>
        <div className="conv-intro">
          <h1>{project.project.name}</h1>
          <p className="muted">
            Desk and {plural(live.length, 'thread')}. {count('running')} running, {count('waiting')} waiting, {count('done')} done.
          </p>
          <button type="button" className="btn btn-secondary btn-sm plan-toggle" aria-expanded={planOpen} onClick={() => setPlanOpen((v) => !v)}>
            Plan
          </button>
        </div>
        <section className="conv-chat" aria-label="Conversation with Desk">
          <div
            className="chat-list"
            ref={listRef}
            onScroll={(e) => {
              const el = e.currentTarget;
              pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
            }}
          >
            {s.chat.items.length === 0 && !pending.length ? (
              <EmptyState title="Brief Desk">Say what you want done. Desk plans it, splits it into threads, and reports back.</EmptyState>
            ) : null}
            {s.chat.items.map((item) => (
              <div key={item.id} id={chatDomId(item.id)} className="chat-item">
                <ChatItemView
                  item={item}
                  projectId={projectId}
                  attentionIds={attentionIds}
                  answering={answering}
                  onAnswer={(t) => void answer(t)}
                  onOwnWords={() => textareaRef.current?.focus()}
                />
              </div>
            ))}
            {pending.map((t) => (
              <div key={t} className="chat-item">
                <div className="chat-user pending">
                  <span className="chat-meta">You · sending…</span>
                  <p>{t}</p>
                </div>
              </div>
            ))}
          </div>
          <Composer projectId={projectId} draft={draft} setDraft={setDraft} textareaRef={textareaRef} onSent={(t) => setPending((p) => [...p, t])} />
        </section>
        <PlanPanel project={project} proxyDown={proxy === 'down'} />
      </div>
    </div>
  );
}
```

`apps/desktop/src/renderer/conversation/conversation.css`:

```css
.conversation {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.line-diagram {
  position: relative;
  flex-shrink: 0;
  max-height: 320px;
  overflow-x: hidden;
  overflow-y: auto;
  border-bottom: 1px solid var(--rule);
}
.line-svg {
  position: absolute;
  left: 0;
  top: 0;
}
.line-tick {
  position: absolute;
  top: 5px;
  transform: translateX(-50%);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 16px;
  color: var(--text-min);
}
.line-now {
  position: absolute;
  top: 3px;
  transform: translateX(-50%);
  height: 18px;
  padding: 0 7px;
  border-radius: 9px;
  background: var(--ink);
  color: #f7f5f0;
  font-family: var(--font-mono);
  font-size: 10.5px;
  line-height: 18px;
  white-space: nowrap;
}
.line-label {
  position: absolute;
  left: 24px;
  width: 176px;
  display: flex;
  flex-direction: column;
  text-decoration: none;
  color: var(--ink);
}
.line-label-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  line-height: 16px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.line-label-sub {
  padding-left: 18px;
  font-size: 11.5px;
  line-height: 14px;
  color: var(--text-min);
}
.line-swatch {
  width: 12px;
  height: 4px;
  flex-shrink: 0;
  border-radius: 2px;
  display: inline-block;
}
.line-swatch-desk {
  height: 5px;
  background: var(--ink);
}
.line-station {
  position: absolute;
  width: 16px;
  height: 16px;
  transform: translate(-50%, -50%);
  padding: 0;
  border: 3px solid var(--ink);
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
}
.line-station-question,
.line-station-report {
  border-color: var(--accent);
  box-shadow: 0 0 0 2.5px var(--ground), 0 0 0 4px var(--accent);
}
.line-station-dispatch {
  width: 28px;
}
.line-station-label {
  position: absolute;
  transform: translateX(-50%);
  padding: 0 4px;
  background: var(--ground);
  font-size: 11.5px;
  font-weight: 500;
  line-height: 16px;
  white-space: nowrap;
}
.line-mark {
  position: absolute;
  transform: translateX(-50%);
  padding: 0 4px;
  background: var(--ground);
  font-size: 11px;
  line-height: 14px;
  white-space: nowrap;
}
.line-mark-detour {
  color: var(--text-min);
  font-size: 10.5px;
}
.line-mark-sent_back {
  color: var(--run-text);
}
.line-mark-stalled {
  color: var(--wait-text);
  font-weight: 600;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
.line-signal {
  position: absolute;
  display: flex;
  align-items: center;
  gap: 8px;
  transform: translate(calc(-100% + 10px), -50%);
  text-decoration: none;
  color: var(--ink);
}
.line-signal-chip {
  height: 20px;
  display: flex;
  align-items: center;
  padding: 0 9px;
  border: 1px solid var(--accent);
  border-radius: 10px;
  background: #fff;
  font-size: 11.5px;
  white-space: nowrap;
}
.line-signal-chip strong {
  color: var(--wait-text);
  font-weight: 600;
}
.line-signal-dot {
  width: 20px;
  height: 20px;
  border-radius: 10px;
  background: var(--accent);
  box-shadow: 0 0 0 2.5px var(--ground), 0 0 0 4px var(--accent);
}
.line-train {
  position: absolute;
  width: 26px;
  height: 14px;
  transform: translate(-50%, -50%);
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 7px;
  background: var(--run);
  box-shadow: 0 0 0 4px rgba(47, 91, 211, 0.22), 0 0 0 7px rgba(47, 91, 211, 0.09);
}
.line-train span {
  width: 12px;
  height: 3px;
  border-radius: 2px;
  background: #fff;
}
.line-activity {
  position: absolute;
  transform: translateX(-100%);
  height: 20px;
  display: flex;
  align-items: center;
  padding: 0 9px;
  border: 1px solid var(--rule);
  border-radius: 10px;
  background: #fff;
  font-family: var(--font-mono);
  font-size: 11px;
  white-space: nowrap;
  max-width: 420px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.line-desk-writing {
  position: absolute;
  transform: translate(-100%, -50%);
  height: 24px;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0 10px 0 6px;
  border-radius: 12px;
  background: var(--ink);
  color: #f7f5f0;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}
.live-dot {
  width: 8px;
  height: 8px;
  flex-shrink: 0;
  border-radius: 4px;
  background: var(--run);
  box-shadow: 0 0 0 3px rgba(47, 91, 211, 0.3);
  display: inline-block;
}
.line-legend {
  position: absolute;
  right: 16px;
  top: 40px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 11px;
  line-height: 16px;
  color: var(--text);
}
.line-legend span {
  display: flex;
  align-items: center;
  gap: 6px;
}
.line-legend .line-swatch {
  width: 18px;
}
.line-legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 5px;
  background: var(--accent);
  margin: 0 4px;
}

.conv-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 190px minmax(0, 1fr) 420px;
  gap: 20px;
  padding: 0 24px 16px;
}
.conv-intro {
  padding-top: 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.conv-intro h1 {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 24px;
  font-weight: 500;
  line-height: 1.15;
}
.conv-intro p {
  margin: 0;
  font-size: 12.5px;
}
.plan-toggle {
  display: none;
  width: max-content;
}
.conv-chat {
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 16px;
}
.chat-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-right: 4px;
}
.chat-item {
  flex-shrink: 0;
  border-radius: 14px;
  transition: background 0.4s;
}
.chat-item.flash {
  background: #eef2fb;
}
.chat-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  line-height: 16px;
}
.chat-user {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 14px;
  border-radius: 12px;
  background: #f4f1ea;
}
.chat-user p {
  margin: 0;
  white-space: pre-wrap;
}
.chat-user.pending {
  opacity: 0.6;
}
.chat-desk {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.md-voice {
  font-family: var(--font-serif);
  font-size: 17px;
  line-height: 1.38;
}
.small {
  font-size: 12px;
}
.toolgroup {
  border: 1px solid var(--rule-soft);
  border-radius: 10px;
  background: #fff;
  padding: 8px 12px;
}
.toolgroup summary {
  display: flex;
  align-items: baseline;
  gap: 10px;
  cursor: pointer;
  font-size: 12.5px;
}
.toolgroup-title {
  font-weight: 600;
}
.toolgroup-names {
  color: var(--text);
  font-size: 12px;
}
.toolgroup ul {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.toolgroup li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
}
.tool-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-min);
}
.tool-status-running {
  color: var(--run-text);
}
.tool-status-error,
.tool-status-denied {
  color: var(--accent);
}
.tool-status-interrupted {
  color: var(--wait-text);
}
.chat-feed {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 8px;
  padding: 8px 12px;
  border-left: 3px solid var(--rule);
  font-size: 12.5px;
}
.feed-chip {
  height: 18px;
  display: inline-flex;
  align-items: center;
  padding: 0 6px;
  border-radius: 4px;
  background: #e6e1d7;
  font-size: 11px;
  font-weight: 600;
}
.feed-completed {
  border-left-color: var(--muted);
}
.feed-revision {
  border-left-color: var(--run);
}
.feed-blocker,
.feed-failed {
  border-left-color: var(--accent);
}
.feed-blocker .feed-chip,
.feed-failed .feed-chip {
  background: var(--accent-tint);
  color: var(--accent);
}
.feed-stalled {
  border-left-color: var(--wait);
}
.feed-stalled .feed-chip {
  background: var(--wait-pastel);
  color: var(--wait-text);
}
.feed-from {
  font-weight: 600;
  text-decoration: none;
}
.feed-text {
  flex-basis: 100%;
  font-size: 13px;
}
.report-card {
  display: flex;
  gap: 16px;
  padding: 12px 16px;
  border: 1px solid var(--rule);
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 4px 14px rgba(28, 27, 24, 0.06);
}
.report-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.report-main h2 {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 19px;
  font-weight: 500;
  line-height: 1.3;
}
.report-progress {
  font-size: 12.5px;
  color: var(--text);
}
.report-results {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
.file-chip {
  height: 24px;
  display: inline-flex;
  align-items: center;
  padding: 0 8px;
  border-radius: 6px;
  border: 1px solid var(--rule);
  background: #fff;
  font-family: var(--font-mono);
  font-size: 11px;
  text-decoration: none;
}
.report-needs {
  width: 236px;
  flex-shrink: 0;
}
.needs-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 12.5px;
  line-height: 1.4;
}
.needs-num {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  margin-top: 1px;
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 600;
}
.needs-done {
  color: var(--text-min);
  text-decoration: line-through;
}
.question-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px;
  border: 1.5px solid var(--accent);
  border-radius: 14px;
  background: #fff;
}
.question-card.answered {
  border-color: var(--rule);
  opacity: 0.8;
}
.question-card .eyebrow {
  display: flex;
  align-items: center;
  gap: 6px;
}
.accent-dot {
  width: 8px;
  height: 8px;
  border-radius: 4px;
  background: var(--accent);
  display: inline-block;
}
.question-text {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 18px;
  line-height: 1.3;
}
.chat-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 10px;
  background: #f4f1ea;
  color: var(--text);
  font-size: 12.5px;
}
.chat-divider {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-min);
}
.chat-divider::before,
.chat-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: var(--rule);
}
.composer {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 14px 12px;
  border: 1px solid var(--rule);
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 4px 14px rgba(28, 27, 24, 0.06);
}
.composer label {
  font-size: 12px;
  font-weight: 600;
}
.composer textarea {
  min-height: 40px;
  max-height: 180px;
  resize: vertical;
  padding: 0;
  border: 0;
  background: transparent;
  font-size: 14px;
  line-height: 1.4;
  outline: none;
}
.composer-bar {
  display: flex;
  align-items: center;
  gap: 10px;
}
.icon-btn {
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid var(--rule);
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
}
.plan-panel {
  margin-top: 16px;
  margin-bottom: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 22px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.plan-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.plan-head h2 {
  flex: 1;
  margin: 0;
  font-family: var(--font-serif);
  font-size: 24px;
  font-weight: 500;
}
.plan-route {
  position: relative;
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.plan-route::before {
  content: '';
  position: absolute;
  left: 9px;
  top: 10px;
  bottom: 10px;
  border-left: 1.5px dashed var(--rule);
}
.plan-stop {
  position: relative;
  display: flex;
  gap: 14px;
}
.plan-dot {
  position: relative;
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  border-radius: 10px;
  background: #fff;
  border: 2px solid var(--muted);
}
.plan-done .plan-dot {
  background: var(--ink);
  border-color: var(--ink);
}
.plan-run .plan-dot {
  border-color: var(--run);
}
.plan-wait .plan-dot {
  border-color: var(--wait);
}
.plan-dropped .plan-title {
  text-decoration: line-through;
  color: var(--text-min);
}
.plan-text {
  display: flex;
  flex-direction: column;
}
.plan-title {
  font-size: 13.5px;
  font-weight: 500;
  line-height: 20px;
}
.plan-sub {
  font-size: 12px;
  line-height: 17px;
  color: var(--text-min);
}
.plan-sub a {
  color: inherit;
}
.plan-sub-run {
  color: var(--run-text);
}
.plan-sub-wait {
  color: var(--wait-text);
}
.merge-note {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 12px;
  border-radius: 10px;
  background: #f4f1ea;
  font-size: 12.5px;
}
.desk-card {
  margin-top: auto;
  padding-top: 14px;
  border-top: 1px solid var(--rule-soft);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.desk-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.desk-avatar {
  width: 30px;
  height: 30px;
  border-radius: 15px;
  background: var(--ink);
  color: #f7f5f0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-serif);
  font-size: 12.5px;
}
.desk-card dl {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.desk-card dl div {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.desk-card dt {
  width: 72px;
  font-size: 12px;
  color: var(--text-min);
}
.desk-card dd {
  margin: 0;
  font-size: 12.5px;
}

@media (max-width: 1279px) {
  .conv-body {
    grid-template-columns: minmax(0, 1fr);
    position: relative;
  }
  .conv-intro {
    flex-direction: row;
    align-items: center;
    padding-top: 10px;
  }
  .conv-intro h1 {
    font-size: 20px;
  }
  .plan-toggle {
    display: inline-flex;
  }
  .plan-panel {
    display: none;
  }
  .plan-open .plan-panel {
    display: flex;
    position: absolute;
    right: 24px;
    top: 0;
    bottom: 16px;
    width: min(420px, 90%);
    z-index: 10;
  }
}
```

In `App.tsx`, route `{ name: 'project', tab: 'conversation' }` to `<ConversationScreen projectId={route.id} />`, importing it from `./conversation/ConversationScreen`. The other tabs stay `Pending` until later tasks and Plan 11.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop
git commit -m "feat(desktop): conversation — line diagram with forks, rejoins, detours, signals and trains; chat, composer and plan route

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
