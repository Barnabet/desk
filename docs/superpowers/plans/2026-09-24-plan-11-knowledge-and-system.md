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

### Task 1: Library — grid, safe preview, upload by drop or picker, download

**Files:**
- Create: `apps/desktop/src/renderer/knowledge/LibraryScreen.test.tsx`, `apps/desktop/src/renderer/files.ts`, `apps/desktop/src/renderer/components/FileViewer.tsx`, `apps/desktop/src/renderer/knowledge/library.ts`, `apps/desktop/src/renderer/knowledge/LibraryScreen.tsx`, `apps/desktop/src/renderer/knowledge/knowledge.css`
- Modify: `apps/desktop/src/renderer/threads/tabs/FilesTab.tsx`, `apps/desktop/src/renderer/threads/threads.css`, `apps/desktop/src/renderer/conversation/Composer.tsx`, `apps/desktop/src/renderer/theme/tokens.css`, `apps/desktop/src/renderer/App.tsx`

**Interfaces:**

- Consumes: `useSession(projectId).events`; the `library.upload`, `library.file` and `app.saveFile` channels; `replaceRoute`; the `file` route param from Plan 10.
- Produces:

```ts
// renderer/files.ts
export function asText(data: Uint8Array): string | null;
export function fileToBase64(file: Blob): Promise<string>;
export const textToBase64: (text: string) => Promise<string>;
export const extOf: (path: string) => string;
export const imageMime: (path: string) => string | null;
export const isMarkdown: (path: string) => boolean;
export const MAX_UPLOAD: number;   // 25 MB
// components/FileViewer.tsx — images via blob URLs from daemon bytes, Markdown safe with a Raw toggle, text as code, else save
export function FileViewer(props: { path: string; data: Uint8Array; actions?: ReactNode }): JSX.Element;
// knowledge/library.ts
export type LibraryItem = { id; path; title; kind: ArtifactKind; origin; description; ts; eventId };
export function libraryFromEvents(events: StoredEvent[]): LibraryItem[];
export const originAgent: (origin: string) => string | null;
export function filterLibrary(items: LibraryItem[], kind: ArtifactKind | 'all', query: string): LibraryItem[];
// knowledge/LibraryScreen.tsx
export function LibraryScreen(props: { projectId: string; file?: string }): JSX.Element;
```

- [ ] **Step 1: Write the failing tests**

`apps/desktop/src/renderer/knowledge/LibraryScreen.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import type { StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { initialGlobalState } from '../../shared/state';
import { useRoute } from '../router';
import { globalStore } from '../state/global';
import { resetSessions, setReleaseDelay, startSessionRouting } from '../state/session';
import { installBridge } from '../test/bridge';
import { libraryFromEvents } from './library';
import { LibraryScreen } from './LibraryScreen';

afterEach(cleanup);
beforeEach(() => {
  resetSessions();
  setReleaseDelay(0);
  window.location.hash = '#/p/p/library';
  globalStore.set({ ...initialGlobalState(), connection: { status: 'live' } });
});

const overview = () =>
  ({
    project: { id: 'p', name: 'P', goal: '', instructions: '', settings: {}, created_at: 't', updated_at: 't', archived_at: null },
    desk: null,
    sources: [],
    plan: null,
    threads: [],
    approvals: [],
    last_seq: 0,
  }) as unknown as ProjectOverview;

const events: StoredEvent[] = [
  ev(1, 'agent.created', { role: 'thread', model: 'm', title: 'Research', brief: 'b', workspace_path: '/w', parent_id: 'd' }, { agent: 't' }),
  ev(2, 'artifact.published', { artifact_id: 'a1', path: 'competitors.md', title: 'Competitor onboarding', kind: 'report', origin: 'agent:t', description: 'Three competitors' }, { agent: 't' }),
  ev(3, 'artifact.published', { artifact_id: 'a2', path: 'uploads/notes.txt', title: 'notes.txt', kind: 'file', origin: 'user', description: '' }),
  ev(4, 'artifact.published', { artifact_id: 'a3', path: 'competitors.md', title: 'Competitor onboarding v2', kind: 'report', origin: 'agent:t', description: '' }, { agent: 't' }),
];

function Routed() {
  const r = useRoute();
  return <LibraryScreen projectId="p" {...(r.name === 'project' && r.file ? { file: r.file } : {})} />;
}

function setup(extra: Record<string, (input: any) => unknown> = {}) {
  const bridge = installBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    ...extra,
  });
  startSessionRouting();
  render(<Routed />);
  return bridge;
}

describe('libraryFromEvents', () => {
  it('keeps the latest publication of each path, newest first', () => {
    expect(libraryFromEvents(events).map((i) => [i.path, i.title])).toEqual([
      ['competitors.md', 'Competitor onboarding v2'],
      ['uploads/notes.txt', 'notes.txt'],
    ]);
  });
});

describe('LibraryScreen', () => {
  it('lists files with their origin, filters by kind, and previews Markdown safely', async () => {
    setup({ 'library.file': () => new TextEncoder().encode('# Findings\n\n<img src="https://x/y.png">') });
    const card = await screen.findByRole('button', { name: /Competitor onboarding v2/ });
    expect(card.textContent).toContain('Research');
    fireEvent.click(screen.getByRole('button', { name: 'File' }));
    expect(screen.queryByRole('button', { name: /Competitor onboarding v2/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'All' }));
    fireEvent.click(screen.getByRole('button', { name: /Competitor onboarding v2/ }));
    await waitFor(() => expect(window.location.hash).toBe('#/p/p/library?file=competitors.md'));
    const preview = await screen.findByRole('complementary', { name: 'Preview' });
    expect(await within(preview).findByRole('heading', { name: 'Findings' })).toBeTruthy();
    expect(preview.querySelector('img')).toBeNull();
    expect(within(preview).getByRole('link', { name: 'Research' }).getAttribute('href')).toBe('#/p/p/threads/t');
  });

  it('uploads through the picker and opens the new file', async () => {
    const bridge = setup({ 'library.upload': ({ file }: { file: { name: string } }) => ({ path: `uploads/${file.name}` }), 'library.file': () => new Uint8Array([1, 0, 2]) });
    await screen.findByRole('button', { name: /Competitor onboarding v2/ });
    fireEvent.change(screen.getByTestId('library-input'), { target: { files: [new File(['hi'], 'brief.pdf')] } });
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'library.upload')?.input).toEqual({ projectId: 'p', file: { name: 'brief.pdf', content_base64: 'aGk=' } }));
    await waitFor(() => expect(window.location.hash).toBe('#/p/p/library?file=uploads%2Fbrief.pdf'));
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop/src/renderer/knowledge`
Expected: FAIL, because the modules don't exist yet.

- [ ] **Step 3: Implement**

`apps/desktop/src/renderer/files.ts`:

```ts
const MAX_TEXT = 1024 * 1024;

/** Decodes bytes as UTF-8 text, or returns null for binary (or very large) content. */
export function asText(data: Uint8Array): string | null {
  if (data.length > MAX_TEXT) return null;
  if (data.subarray(0, 8000).includes(0)) return null;
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(data);
  } catch {
    return null;
  }
}

/** Base64 of a Blob, for upload bodies. */
export async function fileToBase64(file: Blob): Promise<string> {
  const buf = new Uint8Array(await file.arrayBuffer());
  let s = '';
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return btoa(s);
}

export const textToBase64 = (text: string) => fileToBase64(new Blob([text]));

const IMAGE: Record<string, string> = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp', svg: 'image/svg+xml' };

export const extOf = (path: string) => (/\.([^./]+)$/.exec(path)?.[1] ?? '').toLowerCase();
export const imageMime = (path: string): string | null => IMAGE[extOf(path)] ?? null;
export const isMarkdown = (path: string) => /^(md|markdown)$/.test(extOf(path));
export const MAX_UPLOAD = 25 * 1024 * 1024;
```

`apps/desktop/src/renderer/components/FileViewer.tsx`:

```tsx
import { useEffect, useMemo, useState } from 'react';
import { call } from '../bridge';
import { asText, extOf, imageMime, isMarkdown } from '../files';
import { bytes } from '../format';
import { Button } from './Button';
import { CodeBlock } from './CodeBlock';
import { SafeMarkdown } from './SafeMarkdown';
import { toastError } from './Toast';

/**
 * Shows one file's bytes: images through a blob URL (never a remote one), Markdown rendered safely
 * (with a Raw toggle), other text as code, and anything else as a download.
 */
export function FileViewer({ path, data, actions }: { path: string; data: Uint8Array; actions?: React.ReactNode }) {
  const [raw, setRaw] = useState(false);
  const text = useMemo(() => asText(data), [data]);
  const mime = imageMime(path);
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!mime) return;
    const u = URL.createObjectURL(new Blob([new Uint8Array(data)], { type: mime }));
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [data, mime]);
  useEffect(() => setRaw(false), [path]);
  const save = async () => {
    try {
      await call('app.saveFile', { name: path.split('/').pop() ?? 'file', data: new Uint8Array(data) });
    } catch (err) {
      toastError(err);
    }
  };
  const md = text !== null && isMarkdown(path);
  return (
    <div className="file-viewer">
      <div className="file-viewer-bar">
        <span className="mono grow">{path}</span>
        <span className="muted small">{bytes(data.length)}</span>
        {md ? (
          <Button size="sm" variant="ghost" onClick={() => setRaw((r) => !r)}>
            {raw ? 'Rendered' : 'Raw'}
          </Button>
        ) : null}
        {actions}
        <Button size="sm" onClick={() => void save()}>
          Save a copy…
        </Button>
      </div>
      {mime ? (
        url ? <img className="file-image" src={url} alt={path.split('/').pop() ?? ''} /> : null
      ) : text === null ? (
        <p className="muted">This file isn't text, so it can't be shown here. Save a copy to open it.</p>
      ) : md && !raw ? (
        <SafeMarkdown text={text} />
      ) : (
        <CodeBlock code={text} language={extOf(path)} />
      )}
    </div>
  );
}
```

`apps/desktop/src/renderer/knowledge/library.ts`:

```ts
import type { ArtifactKind, StoredEvent } from '@desk/protocol';

export type LibraryItem = { id: string; path: string; title: string; kind: ArtifactKind; origin: string; description: string; ts: string; eventId: number };

/** Library items from the project log, newest first; a republished path replaces its older entry. */
export function libraryFromEvents(events: StoredEvent[]): LibraryItem[] {
  const byPath = new Map<string, LibraryItem>();
  for (const e of events) {
    if (e.type !== 'artifact.published') continue;
    const p = e.payload;
    byPath.set(p.path, { id: p.artifact_id, path: p.path, title: p.title, kind: p.kind, origin: p.origin, description: p.description, ts: e.ts, eventId: e.id });
  }
  return [...byPath.values()].sort((a, b) => b.eventId - a.eventId);
}

/** `user` or `agent:<id>` → who published it. */
export const originAgent = (origin: string): string | null => (origin.startsWith('agent:') ? origin.slice(6) : null);

export function filterLibrary(items: LibraryItem[], kind: ArtifactKind | 'all', query: string): LibraryItem[] {
  const q = query.trim().toLowerCase();
  return items.filter((i) => (kind === 'all' || i.kind === kind) && (!q || i.title.toLowerCase().includes(q) || i.path.toLowerCase().includes(q) || i.description.toLowerCase().includes(q)));
}
```

`apps/desktop/src/renderer/knowledge/LibraryScreen.tsx`:

```tsx
import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import type { ArtifactKind } from '@desk/protocol';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { FileViewer } from '../components/FileViewer';
import { toast, toastError } from '../components/Toast';
import { extOf, fileToBase64, imageMime, MAX_UPLOAD } from '../files';
import { clock, plural } from '../format';
import { href, replaceRoute } from '../router';
import { useSession } from '../state/session';
import { filterLibrary, libraryFromEvents, originAgent, type LibraryItem } from './library';
import './knowledge.css';

const KINDS: Array<ArtifactKind | 'all'> = ['all', 'report', 'code', 'data', 'file', 'other'];

function glyph(i: LibraryItem): string {
  if (imageMime(i.path)) return 'IMG';
  const ext = extOf(i.path);
  return ext ? ext.slice(0, 4).toUpperCase() : i.kind.slice(0, 3).toUpperCase();
}

type Preview = { status: 'loading' } | { status: 'ready'; data: Uint8Array } | { status: 'error'; message: string };

/** The project's library: a grid of published files with a preview, upload by drop or picker, and download. */
export function LibraryScreen({ projectId, file }: { projectId: string; file?: string }) {
  const s = useSession(projectId);
  const items = useMemo(() => libraryFromEvents(s.events), [s.events]);
  const [kind, setKind] = useState<ArtifactKind | 'all'>('all');
  const [query, setQuery] = useState('');
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(0);
  const [preview, setPreview] = useState<Preview | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const shown = useMemo(() => filterLibrary(items, kind, query), [items, kind, query]);
  const selected = file ? items.find((i) => i.path === file) : undefined;
  const version = selected?.eventId;
  const threads = s.project?.threads;
  const threadTitle = (id: string) => threads?.find((t) => t.id === id)?.title ?? (s.project?.desk?.id === id ? 'Desk' : 'a thread');

  useEffect(() => {
    if (!file) {
      setPreview(null);
      return;
    }
    let live = true;
    setPreview({ status: 'loading' });
    call('library.file', { projectId, path: file })
      .then((data) => live && setPreview({ status: 'ready', data }))
      .catch((err) => live && setPreview({ status: 'error', message: err instanceof Error ? err.message : String(err) }));
    return () => {
      live = false;
    };
  }, [projectId, file, version]);

  const select = (path: string | null) => replaceRoute({ name: 'project', id: projectId, tab: 'library', ...(path ? { file: path } : {}) });

  const upload = async (files: FileList | File[] | null) => {
    const list = Array.from(files ?? []);
    for (const f of list) {
      if (f.size > MAX_UPLOAD) {
        toastError(new Error(`${f.name} is larger than 25 MB.`));
        continue;
      }
      setUploading((n) => n + 1);
      try {
        const a = await call('library.upload', { projectId, file: { name: f.name, content_base64: await fileToBase64(f) } });
        if (list.length === 1) select(a.path);
      } catch (err) {
        toastError(err);
      } finally {
        setUploading((n) => n - 1);
      }
    }
    if (list.length > 1) toast({ tone: 'info', message: `Uploaded ${plural(list.length, 'file')}.` });
    if (inputRef.current) inputRef.current.value = '';
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    void upload(e.dataTransfer.files);
  };

  if (s.status === 'loading') return <div className="page muted">Loading…</div>;
  if (s.status !== 'ready')
    return (
      <div className="page">
        <EmptyState title="Couldn't load this project">{s.error}</EmptyState>
      </div>
    );

  return (
    <div
      className={`library${dragging ? ' dragging' : ''}${file ? ' with-preview' : ''}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => e.currentTarget === e.target && setDragging(false)}
      onDrop={onDrop}
    >
      <div className="library-main">
        <div className="knowledge-head">
          <h1 className="title">Library</h1>
          <span className="muted">{plural(items.length, 'file')}. Threads publish here; so can you.</span>
          <span className="grow" />
          <Button variant="primary" pending={uploading > 0} onClick={() => inputRef.current?.click()}>
            Upload…
          </Button>
          <input ref={inputRef} type="file" multiple hidden data-testid="library-input" onChange={(e) => void upload(e.target.files)} />
        </div>
        <div className="knowledge-filters">
          <div className="segmented" role="group" aria-label="Kind">
            {KINDS.map((k) => (
              <button key={k} type="button" aria-pressed={kind === k} onClick={() => setKind(k)}>
                {k === 'all' ? 'All' : k[0]!.toUpperCase() + k.slice(1)}
              </button>
            ))}
          </div>
          <input className="knowledge-search" type="search" aria-label="Filter the library" placeholder="Filter by title or path" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        {shown.length ? (
          <ul className="library-grid">
            {shown.map((i) => {
              const agent = originAgent(i.origin);
              return (
                <li key={i.path}>
                  <button type="button" className={`card library-card${i.path === file ? ' current' : ''}`} aria-pressed={i.path === file} onClick={() => select(i.path === file ? null : i.path)}>
                    <span className={`library-glyph kind-${i.kind}`} aria-hidden="true">
                      {glyph(i)}
                    </span>
                    <span className="library-title">{i.title}</span>
                    <span className="mono small library-path">{i.path}</span>
                    <span className="small muted">
                      {i.kind} · {agent ? threadTitle(agent) : 'you'} · {clock(i.ts)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : items.length ? (
          <p className="muted">Nothing matches.</p>
        ) : (
          <EmptyState title="Nothing here yet">Drop files anywhere on this page, or upload them. Desk and its threads can read everything in the Library.</EmptyState>
        )}
        <p className="drop-hint muted small">{dragging ? 'Drop to upload' : 'Drop files anywhere to upload them.'}</p>
      </div>
      {file ? (
        <aside className="library-preview" aria-label="Preview">
          {selected ? (
            <div className="library-meta">
              <h2>{selected.title}</h2>
              {selected.description ? <p className="small">{selected.description}</p> : null}
              <p className="small muted">
                {selected.kind} · published by{' '}
                {originAgent(selected.origin) ? (
                  <a href={href({ name: 'project', id: projectId, tab: 'threads', threadId: originAgent(selected.origin)! })}>{threadTitle(originAgent(selected.origin)!)}</a>
                ) : (
                  'you'
                )}{' '}
                at {clock(selected.ts)}
              </p>
            </div>
          ) : null}
          {preview?.status === 'ready' ? (
            <FileViewer
              path={file}
              data={preview.data}
              actions={
                <Button size="sm" variant="ghost" aria-label="Close preview" onClick={() => select(null)}>
                  ✕
                </Button>
              }
            />
          ) : preview?.status === 'error' ? (
            <EmptyState title="Couldn't open this file">{preview.message}</EmptyState>
          ) : (
            <p className="muted">Loading…</p>
          )}
        </aside>
      ) : null}
    </div>
  );
}
```

`apps/desktop/src/renderer/knowledge/knowledge.css`:

```css
.knowledge-head {
  display: flex;
  align-items: baseline;
  gap: 14px;
  flex-wrap: wrap;
}
.knowledge-filters {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.knowledge-search {
  height: 30px;
  min-width: 240px;
  padding: 0 10px;
  border: 1px solid var(--rule);
  border-radius: 8px;
  background: #fff;
  font-size: 13px;
}
.library {
  height: 100%;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 20px;
  padding: 24px 32px;
  box-sizing: border-box;
}
.library.with-preview {
  grid-template-columns: minmax(0, 1fr) minmax(420px, 44%);
}
.library.dragging {
  outline: 2px dashed var(--run);
  outline-offset: -10px;
  background: var(--run-pastel);
}
.library-main {
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.library-grid {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}
.library-card {
  width: 100%;
  margin: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 14px;
  border: 1px solid transparent;
  text-align: left;
  font: inherit;
  cursor: pointer;
  box-shadow: 0 4px 14px rgba(28, 27, 24, 0.06);
}
.library-card.current {
  border-color: var(--ink);
}
.library-glyph {
  height: 22px;
  display: inline-flex;
  align-items: center;
  padding: 0 6px;
  margin-bottom: 6px;
  border-radius: 4px;
  background: var(--ink);
  color: #fff;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
}
.library-glyph.kind-report {
  background: var(--accent);
}
.library-glyph.kind-data {
  background: var(--run);
}
.library-glyph.kind-code {
  background: var(--ok);
}
.library-title {
  font-family: var(--font-serif);
  font-size: 16px;
  line-height: 1.25;
}
.library-path {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}
.drop-hint {
  margin: 0;
}
.library-preview {
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.library-meta h2 {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 22px;
  font-weight: 500;
}
.library-meta p {
  margin: 4px 0 0;
}
```

Other changes:
- `FilesTab` drops its own `asText` and viewer and renders `<FileViewer>` inside `.files-pane`. The `.files-viewer` CSS is replaced by `.files-pane`.
- `Composer` imports `fileToBase64` and `MAX_UPLOAD` from `../files`.
- `tokens.css` gains the "File viewer" block: `.file-viewer`, `.file-viewer-bar` and `.file-image`, with a checkerboard behind images.
- `App.tsx` routes `tab === 'library'` to `<LibraryScreen key={route.id} projectId={route.id} file={route.file} />`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(desktop): library — live grid from the event log, kind filter, safe preview with images from bytes, upload by drop or picker, download; shared FileViewer

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Memory — grouped by kind, search, add, correct with the chain, delete

**Files:**
- Create: `apps/desktop/src/renderer/knowledge/MemoryScreen.test.tsx`, `apps/desktop/src/renderer/knowledge/memory.ts`, `apps/desktop/src/renderer/knowledge/MemoryScreen.tsx`
- Modify: `apps/desktop/src/renderer/knowledge/knowledge.css`, `apps/desktop/src/renderer/router.ts`, `apps/desktop/src/renderer/router.test.ts`, `apps/desktop/src/renderer/App.tsx`

**Interfaces:**

- Consumes: `useSession(projectId).events` (`memory.written`, `memory.deleted`); the `memory.add`, `memory.correct`, `memory.remove` and `memory.list` (`?q=`) channels; `originAgent` (Task 1).
- Produces:

```ts
// router.ts — project route gains q (memory tab only): #/p/<id>/memory?q=<search>
// knowledge/memory.ts
export type MemoryEntry = { id; kind: MemoryKind; content; source; ts; supersedes: string | null; supersededBy: string | null; deleted: boolean };
export const MEMORY_KINDS: Array<{ kind: MemoryKind; title: string }>;
export function memoryFromEvents(events: StoredEvent[]): Map<string, MemoryEntry>;
export const activeEntries: (all: Map<string, MemoryEntry>) => MemoryEntry[];
export function chainOf(all: Map<string, MemoryEntry>, id: string): MemoryEntry[];
// knowledge/MemoryScreen.tsx
export function MemoryScreen(props: { projectId: string; q?: string }): JSX.Element;
```

- [ ] **Step 1: Write the failing tests**

`apps/desktop/src/renderer/knowledge/MemoryScreen.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import type { StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { resetSessions, setReleaseDelay, startSessionRouting } from '../state/session';
import { installBridge } from '../test/bridge';
import { activeEntries, chainOf, memoryFromEvents } from './memory';
import { MemoryScreen } from './MemoryScreen';

afterEach(cleanup);
beforeEach(() => {
  resetSessions();
  setReleaseDelay(0);
  globalStore.set({ ...initialGlobalState(), connection: { status: 'live' } });
});

const overview = () =>
  ({ project: { id: 'p', name: 'P', goal: '', instructions: '', settings: {}, created_at: 't', updated_at: 't', archived_at: null }, desk: null, sources: [], plan: null, threads: [], approvals: [], last_seq: 0 }) as unknown as ProjectOverview;

const events: StoredEvent[] = [
  ev(1, 'agent.created', { role: 'thread', model: 'm', title: 'Pricing research', brief: 'b', workspace_path: '/w', parent_id: 'd' }, { agent: 't' }),
  ev(2, 'memory.written', { memory_id: 'm1', kind: 'decision', content: 'Launch on 10 October', source: 'user' }),
  ev(3, 'memory.written', { memory_id: 'm2', kind: 'decision', content: 'Launch on 17 October', source: 'agent:t', supersedes: 'm1' }, { agent: 't' }),
  ev(4, 'memory.written', { memory_id: 'm3', kind: 'fact', content: 'Churn is 4% monthly', source: 'agent:t' }, { agent: 't' }),
  ev(5, 'memory.written', { memory_id: 'm4', kind: 'note', content: 'Scratch', source: 'user' }),
  ev(6, 'memory.deleted', { memory_id: 'm4' }),
];

function setup(extra: Record<string, (input: any) => unknown> = {}) {
  const bridge = installBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    ...extra,
  });
  startSessionRouting();
  render(<MemoryScreen projectId="p" />);
  return bridge;
}

describe('memory fold', () => {
  it('tracks supersession and deletion', () => {
    const all = memoryFromEvents(events);
    expect(activeEntries(all).map((m) => m.id)).toEqual(['m3', 'm2']);
    expect(chainOf(all, 'm2').map((m) => m.content)).toEqual(['Launch on 10 October']);
  });
});

describe('MemoryScreen', () => {
  it('groups entries by kind with sources and the correction chain', async () => {
    setup();
    const decisions = await screen.findByRole('region', { name: /Decisions/ });
    const entry = within(decisions).getByRole('listitem', { name: /Launch on 17 October/ });
    expect(within(entry).getByRole('link', { name: 'Pricing research' }).getAttribute('href')).toBe('#/p/p/threads/t');
    fireEvent.click(within(entry).getByRole('button', { name: 'Corrected 1 time' }));
    expect(within(entry).getByRole('list', { name: 'Earlier versions' }).textContent).toContain('Launch on 10 October');
    expect(screen.getByRole('region', { name: /Facts/ })).toBeTruthy();
    expect(screen.queryByText('Scratch')).toBeNull();
  });

  it('adds, corrects, deletes and searches', async () => {
    const bridge = setup({
      'memory.add': () => ({}),
      'memory.correct': () => ({}),
      'memory.remove': () => ({ ok: true }),
      'memory.list': () => [{ id: 'm3' }],
    });
    await screen.findByRole('region', { name: /Facts/ });
    fireEvent.change(screen.getByLabelText('Remember something'), { target: { value: 'Prefer email over calls' } });
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'preference' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'memory.add')?.input).toEqual({ projectId: 'p', entry: { kind: 'preference', content: 'Prefer email over calls' } }));

    const fact = screen.getByRole('listitem', { name: /Churn is 4%/ });
    fireEvent.click(within(fact).getByRole('button', { name: 'Correct' }));
    fireEvent.change(within(fact).getByLabelText('Correct this entry'), { target: { value: 'Churn is 3.5% monthly' } });
    fireEvent.click(within(fact).getByRole('button', { name: 'Save correction' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'memory.correct')?.input).toEqual({ projectId: 'p', memoryId: 'm3', update: { content: 'Churn is 3.5% monthly' } }));

    fireEvent.click(within(screen.getByRole('listitem', { name: /Launch on 17/ })).getByRole('button', { name: 'Delete' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete' }).at(-1)!);
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'memory.remove')?.input).toEqual({ projectId: 'p', memoryId: 'm2' }));

    fireEvent.change(screen.getByLabelText('Search memory'), { target: { value: 'churn' } });
    const results = await screen.findByRole('list', { name: 'Search results' });
    expect(within(results).getAllByRole('listitem')).toHaveLength(1);
    expect(bridge.calls.find((c) => c.channel === 'memory.list')?.input).toEqual({ projectId: 'p', q: 'churn' });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop/src/renderer/knowledge`
Expected: FAIL, because the modules don't exist yet.

- [ ] **Step 3: Implement**

`apps/desktop/src/renderer/knowledge/memory.ts`:

```ts
import type { MemoryKind, StoredEvent } from '@desk/protocol';

export type MemoryEntry = { id: string; kind: MemoryKind; content: string; source: string; ts: string; supersedes: string | null; supersededBy: string | null; deleted: boolean };

export const MEMORY_KINDS: Array<{ kind: MemoryKind; title: string }> = [
  { kind: 'decision', title: 'Decisions' },
  { kind: 'fact', title: 'Facts' },
  { kind: 'preference', title: 'Preferences' },
  { kind: 'contact', title: 'Contacts' },
  { kind: 'note', title: 'Notes' },
];

/** Every memory entry ever written in the project, with supersession links, by id. */
export function memoryFromEvents(events: StoredEvent[]): Map<string, MemoryEntry> {
  const all = new Map<string, MemoryEntry>();
  for (const e of events) {
    if (e.type === 'memory.written') {
      const p = e.payload;
      all.set(p.memory_id, { id: p.memory_id, kind: p.kind, content: p.content, source: p.source, ts: e.ts, supersedes: p.supersedes ?? null, supersededBy: null, deleted: false });
      const old = p.supersedes ? all.get(p.supersedes) : undefined;
      if (old) all.set(old.id, { ...old, supersededBy: p.memory_id });
    } else if (e.type === 'memory.deleted') {
      const m = all.get(e.payload.memory_id);
      if (m) all.set(m.id, { ...m, deleted: true });
    }
  }
  return all;
}

/** Entries in use (not superseded, not deleted), newest first. */
export const activeEntries = (all: Map<string, MemoryEntry>): MemoryEntry[] =>
  [...all.values()].filter((m) => !m.supersededBy && !m.deleted).sort((a, b) => b.ts.localeCompare(a.ts) || b.id.localeCompare(a.id));

/** The versions an entry replaced, newest first. */
export function chainOf(all: Map<string, MemoryEntry>, id: string): MemoryEntry[] {
  const out: MemoryEntry[] = [];
  const seen = new Set<string>([id]);
  let next = all.get(id)?.supersedes;
  while (next && !seen.has(next)) {
    seen.add(next);
    const m = all.get(next);
    if (!m) break;
    out.push(m);
    next = m.supersedes;
  }
  return out;
}
```

`apps/desktop/src/renderer/knowledge/MemoryScreen.tsx`:

```tsx
import { useEffect, useMemo, useState } from 'react';
import type { MemoryKind } from '@desk/protocol';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EmptyState } from '../components/EmptyState';
import { SafeMarkdown } from '../components/SafeMarkdown';
import { toastError } from '../components/Toast';
import { clock, plural } from '../format';
import { href, replaceRoute } from '../router';
import { useSession } from '../state/session';
import { originAgent } from './library';
import { activeEntries, chainOf, MEMORY_KINDS, memoryFromEvents, type MemoryEntry } from './memory';
import './knowledge.css';

function Source({ projectId, source, label }: { projectId: string; source: string; label(id: string): string }) {
  const agent = originAgent(source);
  return agent ? <a href={href({ name: 'project', id: projectId, tab: 'threads', threadId: agent })}>{label(agent)}</a> : <>you</>;
}

function Entry(o: { projectId: string; m: MemoryEntry; chain: MemoryEntry[]; label(id: string): string }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(o.m.content);
  const [busy, setBusy] = useState<string | null>(null);
  const [showChain, setShowChain] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const save = async () => {
    if (!draft.trim() || draft.trim() === o.m.content) return setEditing(false);
    setBusy('save');
    try {
      await call('memory.correct', { projectId: o.projectId, memoryId: o.m.id, update: { content: draft.trim() } });
      setEditing(false);
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(null);
    }
  };
  const remove = async () => {
    setConfirm(false);
    setBusy('delete');
    try {
      await call('memory.remove', { projectId: o.projectId, memoryId: o.m.id });
    } catch (err) {
      toastError(err);
      setBusy(null);
    }
  };
  return (
    <li className="memory-entry card" aria-label={`${o.m.kind}: ${o.m.content.slice(0, 80)}`}>
      {editing ? (
        <div className="field">
          <label htmlFor={`mem-${o.m.id}`}>Correct this entry</label>
          <textarea id={`mem-${o.m.id}`} className="textarea" rows={3} value={draft} onChange={(e) => setDraft(e.target.value)} />
          <div className="actions">
            <Button size="sm" variant="primary" pending={busy === 'save'} onClick={() => void save()}>
              Save correction
            </Button>
            <Button size="sm" variant="ghost" onClick={() => (setEditing(false), setDraft(o.m.content))}>
              Cancel
            </Button>
            <span className="muted small">The old version is kept in the history.</span>
          </div>
        </div>
      ) : (
        <SafeMarkdown className="memory-content" text={o.m.content} />
      )}
      <div className="memory-meta small muted">
        <span>
          <Source projectId={o.projectId} source={o.m.source} label={o.label} /> · {clock(o.m.ts)}
        </span>
        {o.chain.length ? (
          <button type="button" className="link small" aria-expanded={showChain} onClick={() => setShowChain((v) => !v)}>
            Corrected {plural(o.chain.length, 'time')}
          </button>
        ) : null}
        <span className="grow" />
        {editing ? null : (
          <>
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              Correct
            </Button>
            <Button size="sm" variant="ghost" pending={busy === 'delete'} onClick={() => setConfirm(true)}>
              Delete
            </Button>
          </>
        )}
      </div>
      {showChain ? (
        <ol className="memory-chain" aria-label="Earlier versions">
          {o.chain.map((c) => (
            <li key={c.id}>
              <span className="memory-old">{c.content}</span>
              <span className="muted small">
                {' '}
                · <Source projectId={o.projectId} source={c.source} label={o.label} /> · {clock(c.ts)}
              </span>
            </li>
          ))}
        </ol>
      ) : null}
      {confirm ? (
        <ConfirmDialog title="Delete this memory?" confirmLabel="Delete" danger onConfirm={() => void remove()} onCancel={() => setConfirm(false)}>
          Desk and its threads stop seeing it. To change it instead, use Correct.
        </ConfirmDialog>
      ) : null}
    </li>
  );
}

/** What Desk remembers for this project: grouped by kind, searchable, and correctable by you. */
export function MemoryScreen({ projectId, q }: { projectId: string; q?: string }) {
  const s = useSession(projectId);
  const all = useMemo(() => memoryFromEvents(s.events), [s.events]);
  const active = useMemo(() => activeEntries(all), [all]);
  const [query, setQuery] = useState(q ?? '');
  const [hits, setHits] = useState<string[] | null>(null);
  const [kind, setKind] = useState<MemoryKind>('fact');
  const [content, setContent] = useState('');
  const [adding, setAdding] = useState(false);
  const label = (id: string) => s.project?.threads.find((t) => t.id === id)?.title ?? (s.project?.desk?.id === id ? 'Desk' : 'a thread');

  useEffect(() => setQuery(q ?? ''), [q]);
  useEffect(() => {
    const term = query.trim();
    if (!term) {
      setHits(null);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      call('memory.list', { projectId, q: term })
        .then((rows) => live && setHits(rows.map((r) => r.id)))
        .catch((err) => live && (toastError(err), setHits([])));
    }, 250);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [projectId, query, active.length]);

  const add = async () => {
    if (!content.trim()) return;
    setAdding(true);
    try {
      await call('memory.add', { projectId, entry: { kind, content: content.trim() } });
      setContent('');
    } catch (err) {
      toastError(err);
    } finally {
      setAdding(false);
    }
  };

  if (s.status === 'loading') return <div className="page muted">Loading…</div>;
  if (s.status !== 'ready')
    return (
      <div className="page">
        <EmptyState title="Couldn't load this project">{s.error}</EmptyState>
      </div>
    );

  const byId = new Map(active.map((m) => [m.id, m]));
  const shown = hits ? hits.map((id) => byId.get(id)).filter((m): m is MemoryEntry => !!m) : active;
  return (
    <div className="page memory">
      <div className="knowledge-head">
        <h1 className="title">Memory</h1>
        <span className="muted">{plural(active.length, 'entry', 'entries')}. Desk and its threads read these before they work.</span>
      </div>
      <form
        className="card memory-add"
        onSubmit={(e) => {
          e.preventDefault();
          void add();
        }}
      >
        <label htmlFor="memory-new">Remember something</label>
        <div className="memory-add-row">
          <select aria-label="Kind" className="select" value={kind} onChange={(e) => setKind(e.target.value as MemoryKind)}>
            {MEMORY_KINDS.map((k) => (
              <option key={k.kind} value={k.kind}>
                {k.kind}
              </option>
            ))}
          </select>
          <input id="memory-new" type="text" value={content} onChange={(e) => setContent(e.target.value)} placeholder="e.g. We never discount annual plans" />
          <Button type="submit" variant="primary" pending={adding} disabled={!content.trim()}>
            Add
          </Button>
        </div>
      </form>
      <input
        className="knowledge-search"
        type="search"
        aria-label="Search memory"
        placeholder="Search memory"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          replaceRoute({ name: 'project', id: projectId, tab: 'memory', ...(e.target.value.trim() ? { q: e.target.value } : {}) });
        }}
      />
      {hits && !shown.length ? <p className="muted">No entries match “{query.trim()}”.</p> : null}
      {!active.length ? <EmptyState title="Nothing remembered yet">Desk writes down decisions, facts and preferences as it works. You can add and correct them here.</EmptyState> : null}
      {hits ? (
        <ul className="memory-list" aria-label="Search results">
          {shown.map((m) => (
            <Entry key={m.id} projectId={projectId} m={m} chain={chainOf(all, m.id)} label={label} />
          ))}
        </ul>
      ) : (
        MEMORY_KINDS.map((k) => {
          const list = shown.filter((m) => m.kind === k.kind);
          return list.length ? (
            <section key={k.kind} aria-labelledby={`mem-group-${k.kind}`}>
              <h2 id={`mem-group-${k.kind}`} className="memory-group">
                {k.title} <span className="muted">{list.length}</span>
              </h2>
              <ul className="memory-list">
                {list.map((m) => (
                  <Entry key={m.id} projectId={projectId} m={m} chain={chainOf(all, m.id)} label={label} />
                ))}
              </ul>
            </section>
          ) : null;
        })
      )}
    </div>
  );
}
```

Other changes:
- `router.ts`:
  - The project route type gains `q?: string`.
  - `parseRoute` reads `?q=` for the memory tab.
  - `href` writes `?file=` for library and `?q=` for memory.
- `router.test.ts` round-trips `{ tab: 'memory', q: 'pricing & plans' }`.
- `knowledge.css` gains the `.memory*` rules: the add form, groups, entry cards, meta and the struck-through chain.
- `App.tsx` routes `tab === 'memory'` to `<MemoryScreen key={route.id} projectId={route.id} q={route.q} />`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(desktop): memory — live entries by kind with sources, server search, add, correct with supersession chain, delete

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

