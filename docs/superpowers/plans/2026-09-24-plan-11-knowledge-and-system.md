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

### Task 3: Project settings, policy editor, and More options when creating a project

**Files:**
- Create: `apps/desktop/src/renderer/settings/SettingsScreen.test.tsx`, `apps/desktop/src/renderer/screens/ProjectForm.test.tsx`, `apps/desktop/src/renderer/settings/useModels.ts`, `apps/desktop/src/renderer/settings/SettingsFields.tsx`, `apps/desktop/src/renderer/settings/PolicyEditor.tsx`, `apps/desktop/src/renderer/settings/SettingsScreen.tsx`, `apps/desktop/src/renderer/settings/settings.css`
- Modify: `apps/desktop/src/renderer/screens/ProjectForm.tsx`, `apps/desktop/src/renderer/conversation/conversation.css`, `apps/desktop/src/renderer/theme/tokens.css`, `apps/desktop/src/renderer/App.tsx`

**Interfaces:**

- Consumes: `useSession(projectId).project` (project, settings and sources, kept live by `project.updated` and `source.*`); the `projects.update`, `projects.addSource`, `projects.removeSource`, `projects.archive`, `models.list` and `app.pickFolder` channels; `DEFAULT_POLICY` and `RISKY_COMMAND_PATTERN` from `@desk/protocol`.
- Produces:

```ts
// settings/useModels.ts
export function useModels(): ModelInfo[] | null;
// settings/SettingsFields.tsx
export type WorkingStyle = Pick<ProjectSettings, 'desk_model' | 'thread_model' | 'fallback_model' | 'max_concurrent_threads' | 'check_in' | 'autonomy' | 'review_rounds'>;
export const workingStyleOf: (s: ProjectSettings) => WorkingStyle;
export const DEFAULT_STYLE: WorkingStyle;
export function SettingsFields(props: { value: WorkingStyle; models: ModelInfo[] | null; onChange(patch: Partial<WorkingStyle>): void; idPrefix?: string }): JSX.Element;
// settings/PolicyEditor.tsx — first match wins; the built-in risky pattern shows as a named chip
export const sameRules: (a: PolicyRule[], b: PolicyRule[]) => boolean;
export function PolicyEditor(props: { rules: PolicyRule[]; onChange(rules: PolicyRule[]): void }): JSX.Element;
// settings/SettingsScreen.tsx
export function SettingsScreen(props: { projectId: string }): JSX.Element;
```

- [ ] **Step 1: Write the failing tests**

`apps/desktop/src/renderer/settings/SettingsScreen.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import { DEFAULT_POLICY, RISKY_COMMAND_PATTERN, type StoredEvent } from '@desk/protocol';
import { ev } from '@desk/client/testing';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { resetSessions, setReleaseDelay, startSessionRouting } from '../state/session';
import { installBridge } from '../test/bridge';
import { SettingsScreen } from './SettingsScreen';

afterEach(cleanup);
beforeEach(() => {
  resetSessions();
  setReleaseDelay(0);
  window.location.hash = '#/p/p/settings';
  globalStore.set({ ...initialGlobalState(), connection: { status: 'live' } });
});

const settings = { desk_model: 'claude-opus-5-5', thread_model: 'claude-opus-5-5', fallback_model: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, policy: DEFAULT_POLICY };
const overview = () =>
  ({
    project: { id: 'p', name: 'Tax 2026', goal: 'File on time', instructions: '', settings, created_at: 't', updated_at: 't', archived_at: null },
    desk: null,
    sources: [{ id: 's1', project_id: 'p', path: '/Users/me/tax', kind: 'folder', label: 'tax', created_at: 't' }],
    plan: null,
    threads: [],
    approvals: [],
    last_seq: 0,
  }) as unknown as ProjectOverview;
const models = ['claude-opus-5-5', 'claude-fable-5-1', 'gpt-6-sol'].map((id) => ({ id, family: 'claude', context_window: 1, max_output_tokens: 1, supports_reasoning_effort: false, concurrency: 1 }));

function setup(extra: Record<string, (input: any) => unknown> = {}, events: StoredEvent[] = []) {
  const bridge = installBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of events) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
    'models.list': () => models,
    'projects.update': () => ({}),
    ...extra,
  });
  startSessionRouting();
  render(<SettingsScreen projectId="p" />);
  return bridge;
}

const updates = (bridge: ReturnType<typeof installBridge>) => bridge.calls.filter((c) => c.channel === 'projects.update').map((c) => c.input);

describe('SettingsScreen', () => {
  it('saves the name and goal, and how Desk works', async () => {
    const bridge = setup();
    fireEvent.change(await screen.findByLabelText('Goal'), { target: { value: 'File by April' } });
    const about = screen.getByRole('region', { name: 'About this project' });
    fireEvent.click(within(about).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(updates(bridge)[0]).toEqual({ id: 'p', patch: { name: 'Tax 2026', goal: 'File by April', instructions: '' } }));

    const style = screen.getByRole('region', { name: 'How Desk works' });
    fireEvent.click(within(style).getByLabelText(/Detailed/));
    fireEvent.click(within(style).getByLabelText(/Ask before dispatching/));
    await waitFor(() => expect((within(style).getByLabelText("Threads' model") as HTMLSelectElement).options.length).toBe(3));
    fireEvent.change(within(style).getByLabelText('Fallback when rate limited'), { target: { value: 'claude-fable-5-1' } });
    fireEvent.click(within(style).getByRole('button', { name: '6 threads at once' }));
    fireEvent.click(within(style).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(updates(bridge)[1]).toEqual({
        id: 'p',
        patch: { settings: { desk_model: 'claude-opus-5-5', thread_model: 'claude-opus-5-5', fallback_model: 'claude-fable-5-1', max_concurrent_threads: 6, check_in: 'detailed', autonomy: 'ask-before-dispatch', review_rounds: 2 } },
      }),
    );
  });

  it('edits the policy in order, resets to the default, and shows the risky pattern by name', async () => {
    const bridge = setup();
    const policy = await screen.findByRole('region', { name: 'Policy' });
    expect(within(policy).getAllByText('risky commands (built-in)')).toHaveLength(4);
    expect(within(policy).getByText('This is the default policy.')).toBeTruthy();
    fireEvent.click(within(policy).getByRole('button', { name: 'Add rule' }));
    fireEvent.change(within(policy).getByLabelText('Rule 10 tool'), { target: { value: 'web_fetch' } });
    fireEvent.change(within(policy).getByLabelText('Rule 10 match'), { target: { value: 'domain' } });
    fireEvent.change(within(policy).getByLabelText('Rule 10 pattern'), { target: { value: '*.internal' } });
    fireEvent.change(within(policy).getByLabelText('Rule 10 action'), { target: { value: 'deny' } });
    for (let i = 10; i > 1; i--) fireEvent.click(within(policy).getByRole('button', { name: `Move rule ${i} up` }));
    fireEvent.click(within(policy).getByRole('button', { name: 'Save policy' }));
    await waitFor(() => expect(updates(bridge)).toHaveLength(1));
    const saved = (updates(bridge)[0] as { patch: { settings: { policy: unknown[] } } }).patch.settings.policy;
    expect(saved[0]).toEqual({ tool: 'web_fetch', match: { domain: '*.internal' }, action: 'deny' });
    expect(saved[1]).toEqual({ tool: 'bash', match: { command: RISKY_COMMAND_PATTERN }, action: 'ask' });
    fireEvent.click(within(policy).getByRole('button', { name: 'Reset to the default policy' }));
    expect(within(policy).getByText('This is the default policy.')).toBeTruthy();
  });

  it('adds and removes sources, and archives the project after confirming', async () => {
    const bridge = setup({ 'app.pickFolder': () => '/Users/me/repo', 'projects.addSource': () => ({}), 'projects.removeSource': () => ({ ok: true }), 'projects.archive': () => ({ ok: true }) });
    const sources = await screen.findByRole('region', { name: 'Sources' });
    fireEvent.click(within(sources).getByRole('button', { name: 'Add folder…' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.addSource')?.input).toEqual({ id: 'p', source: { path: '/Users/me/repo' } }));
    fireEvent.click(within(sources).getByRole('button', { name: 'Remove tax' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.removeSource')?.input).toEqual({ id: 'p', sourceId: 's1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Archive project…' }));
    fireEvent.click(screen.getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(window.location.hash).toBe('#/map'));
  });

  it('follows the live project after a save', async () => {
    setup({}, [ev(9, 'project.updated', { goal: 'File by April' })]);
    await waitFor(() => expect((screen.getByLabelText('Goal') as HTMLTextAreaElement).value).toBe('File by April'));
  });
});
```

`apps/desktop/src/renderer/screens/ProjectForm.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { installBridge } from '../test/bridge';
import { ProjectForm } from './ProjectForm';

afterEach(cleanup);

describe('ProjectForm', () => {
  it('sends settings only when More options changed them', async () => {
    const bridge = installBridge({
      'models.list': () => [{ id: 'claude-opus-5-5' }, { id: 'claude-fable-5-1' }],
      'projects.create': () => ({ project: { id: 'new' } }),
    });
    let created = '';
    render(<ProjectForm onCreated={(id) => (created = id)} />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Launch' } });
    const more = screen.getByText('More options').closest('details')!;
    more.open = true;
    fireEvent(more, new Event('toggle'));
    fireEvent.click(await screen.findByLabelText(/Minimal/));
    fireEvent.click(screen.getByRole('button', { name: 'Create project' }));
    await waitFor(() => expect(created).toBe('new'));
    expect(bridge.calls.find((c) => c.channel === 'projects.create')?.input).toMatchObject({ name: 'Launch', settings: { check_in: 'minimal', review_rounds: 2 } });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop/src/renderer/settings apps/desktop/src/renderer/screens`
Expected: FAIL, because the modules don't exist yet.

- [ ] **Step 3: Implement**

`apps/desktop/src/renderer/settings/useModels.ts`:

```ts
import { useEffect, useState } from 'react';
import type { ModelInfo } from '@desk/protocol';
import { call } from '../bridge';

/** The model registry, fetched once per mount; null while loading or unavailable. */
export function useModels(): ModelInfo[] | null {
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  useEffect(() => {
    let live = true;
    call('models.list', {})
      .then((m) => live && setModels(m))
      .catch(() => live && setModels(null));
    return () => {
      live = false;
    };
  }, []);
  return models;
}
```

`apps/desktop/src/renderer/settings/SettingsFields.tsx`:

```tsx
import type { ModelInfo, ProjectSettings } from '@desk/protocol';

export type WorkingStyle = Pick<ProjectSettings, 'desk_model' | 'thread_model' | 'fallback_model' | 'max_concurrent_threads' | 'check_in' | 'autonomy' | 'review_rounds'>;

export const workingStyleOf = (s: ProjectSettings): WorkingStyle => ({
  desk_model: s.desk_model,
  thread_model: s.thread_model,
  fallback_model: s.fallback_model,
  max_concurrent_threads: s.max_concurrent_threads,
  check_in: s.check_in,
  autonomy: s.autonomy,
  review_rounds: s.review_rounds,
});

export const DEFAULT_STYLE: WorkingStyle = { desk_model: 'claude-opus-5-5', thread_model: 'claude-opus-5-5', fallback_model: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2 };

const CHECK_IN: Array<[WorkingStyle['check_in'], string, string]> = [
  ['minimal', 'Minimal', 'Reports only when done or blocked'],
  ['normal', 'Normal', 'Reports at milestones'],
  ['detailed', 'Detailed', 'Reports every step of the plan'],
];
const AUTONOMY: Array<[WorkingStyle['autonomy'], string, string]> = [
  ['dispatch-freely', 'Dispatch freely', 'Desk starts threads as it sees fit'],
  ['ask-before-dispatch', 'Ask before dispatching', 'Desk proposes threads and waits for your go'],
];
const SLOTS = 12;

function ModelSelect(o: { id: string; label: string; value: string | null; models: ModelInfo[] | null; allowNone?: boolean; onChange(v: string | null): void }) {
  const ids = o.models?.map((m) => m.id) ?? [];
  const options = o.value && !ids.includes(o.value) ? [o.value, ...ids] : ids;
  return (
    <div className="field">
      <label htmlFor={o.id}>{o.label}</label>
      <select id={o.id} className="select" value={o.value ?? ''} onChange={(e) => o.onChange(e.target.value || null)} disabled={!o.models}>
        {o.allowNone ? <option value="">None</option> : null}
        {options.map((m) => (
          <option key={m} value={m}>
            {m}
            {o.models && !ids.includes(m) ? ' (not in the registry)' : ''}
          </option>
        ))}
      </select>
    </div>
  );
}

/** How Desk works on a project: check-ins, autonomy, review rounds, models and thread slots. */
export function SettingsFields({ value, models, onChange, idPrefix = 'settings' }: { value: WorkingStyle; models: ModelInfo[] | null; onChange(patch: Partial<WorkingStyle>): void; idPrefix?: string }) {
  return (
    <div className="settings-fields">
      <fieldset className="choice">
        <legend>Check-ins</legend>
        {CHECK_IN.map(([v, label, hint]) => (
          <label key={v} className={value.check_in === v ? 'on' : undefined}>
            <input type="radio" name={`${idPrefix}-check-in`} value={v} checked={value.check_in === v} onChange={() => onChange({ check_in: v })} />
            <span>
              <strong>{label}</strong>
              <span className="muted small">{hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <fieldset className="choice">
        <legend>Autonomy</legend>
        {AUTONOMY.map(([v, label, hint]) => (
          <label key={v} className={value.autonomy === v ? 'on' : undefined}>
            <input type="radio" name={`${idPrefix}-autonomy`} value={v} checked={value.autonomy === v} onChange={() => onChange({ autonomy: v })} />
            <span>
              <strong>{label}</strong>
              <span className="muted small">{hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <div className="field">
        <label htmlFor={`${idPrefix}-rounds`}>Review rounds</label>
        <input id={`${idPrefix}-rounds`} className="input narrow" type="number" min={0} max={10} value={value.review_rounds} onChange={(e) => onChange({ review_rounds: Math.max(0, Math.min(10, Number(e.target.value) || 0)) })} />
        <p className="field-hint">How many times Desk may send a thread's work back before accepting or escalating it.</p>
      </div>
      <div className="settings-models">
        <ModelSelect id={`${idPrefix}-desk-model`} label="Desk's model" value={value.desk_model} models={models} onChange={(v) => v && onChange({ desk_model: v })} />
        <ModelSelect id={`${idPrefix}-thread-model`} label="Threads' model" value={value.thread_model} models={models} onChange={(v) => v && onChange({ thread_model: v })} />
        <ModelSelect id={`${idPrefix}-fallback-model`} label="Fallback when rate limited" value={value.fallback_model} models={models} allowNone onChange={(v) => onChange({ fallback_model: v })} />
      </div>
      <div className="field">
        <span className="label" id={`${idPrefix}-slots-label`}>
          Threads at once · {value.max_concurrent_threads}
        </span>
        <div className="slots" role="group" aria-labelledby={`${idPrefix}-slots-label`}>
          {Array.from({ length: SLOTS }, (_, i) => i + 1).map((n) => (
            <button key={n} type="button" className={n <= value.max_concurrent_threads ? 'slot on' : 'slot'} aria-label={`${n} thread${n === 1 ? '' : 's'} at once`} aria-pressed={n === value.max_concurrent_threads} onClick={() => onChange({ max_concurrent_threads: n })} />
          ))}
          <input
            className="input narrow"
            type="number"
            min={1}
            max={32}
            aria-label="Threads at once"
            value={value.max_concurrent_threads}
            onChange={(e) => onChange({ max_concurrent_threads: Math.max(1, Math.min(32, Number(e.target.value) || 1)) })}
          />
        </div>
      </div>
    </div>
  );
}
```

`apps/desktop/src/renderer/settings/PolicyEditor.tsx`:

```tsx
import { DEFAULT_POLICY, RISKY_COMMAND_PATTERN, type PolicyRule } from '@desk/protocol';
import { Button } from '../components/Button';

type MatchKey = 'branch' | 'command' | 'domain';
const TOOLS = ['bash', 'bash_background', 'bash_readonly', 'skill_run', 'git_push', 'open_pr', 'web_fetch', 'web_search'];
const MATCH_HINT: Record<MatchKey, string> = { branch: 'a glob such as desk/*', command: 'a regular expression', domain: 'a glob such as *.github.com' };

export const sameRules = (a: PolicyRule[], b: PolicyRule[]) => JSON.stringify(a) === JSON.stringify(b);

function matchOf(r: PolicyRule): [MatchKey | 'none', string] {
  const m = r.match ?? {};
  for (const k of ['branch', 'command', 'domain'] as const) if (m[k] !== undefined) return [k, m[k]!];
  return ['none', ''];
}

function withMatch(r: PolicyRule, key: MatchKey | 'none', pattern: string): PolicyRule {
  const { match: _m, ...rest } = r;
  return key === 'none' ? rest : { ...rest, match: { [key]: pattern } };
}

/** The ordered policy: the first rule that matches a tool call decides whether it runs, asks or is refused. */
export function PolicyEditor({ rules, onChange }: { rules: PolicyRule[]; onChange(rules: PolicyRule[]): void }) {
  const set = (i: number, r: PolicyRule) => onChange(rules.map((x, k) => (k === i ? r : x)));
  const move = (i: number, d: -1 | 1) => {
    const next = [...rules];
    const [r] = next.splice(i, 1);
    next.splice(i + d, 0, r!);
    onChange(next);
  };
  return (
    <div className="policy">
      <datalist id="policy-tools">
        {TOOLS.map((t) => (
          <option key={t} value={t} />
        ))}
      </datalist>
      <p className="field-hint">Rules are checked top to bottom; the first match decides. Tools no rule matches use their built-in default. Shell tools always run in the sandbox.</p>
      <ol className="policy-rules">
        {rules.map((r, i) => {
          const [key, pattern] = matchOf(r);
          const risky = key === 'command' && pattern === RISKY_COMMAND_PATTERN;
          return (
            <li key={i} className="policy-rule" aria-label={`Rule ${i + 1}`}>
              <span className="policy-num" aria-hidden="true">
                {i + 1}
              </span>
              <input className="input" list="policy-tools" aria-label={`Rule ${i + 1} tool`} value={r.tool} onChange={(e) => set(i, { ...r, tool: e.target.value })} />
              <select className="select" aria-label={`Rule ${i + 1} match`} value={key} onChange={(e) => set(i, withMatch(r, e.target.value as MatchKey | 'none', pattern))}>
                <option value="none">any call</option>
                <option value="branch">branch</option>
                <option value="command">command</option>
                <option value="domain">domain</option>
              </select>
              {key === 'none' ? (
                <span />
              ) : risky ? (
                <span className="policy-risky">
                  <span className="chip chip-idle">risky commands (built-in)</span>
                  <button type="button" className="link small" onClick={() => set(i, withMatch(r, key, ''))}>
                    Replace
                  </button>
                </span>
              ) : (
                <input className="input mono" aria-label={`Rule ${i + 1} pattern`} placeholder={MATCH_HINT[key]} value={pattern} onChange={(e) => set(i, withMatch(r, key, e.target.value))} />
              )}
              <select className="select" aria-label={`Rule ${i + 1} action`} value={r.action} onChange={(e) => set(i, { ...r, action: e.target.value as PolicyRule['action'] })}>
                <option value="allow">allow</option>
                <option value="ask">ask</option>
                <option value="deny">deny</option>
              </select>
              <label className={`policy-delegate small${r.action === 'ask' ? '' : ' hidden'}`}>
                <input
                  type="checkbox"
                  checked={r.delegate_to_desk ?? false}
                  disabled={r.action !== 'ask'}
                  onChange={(e) => {
                    const { delegate_to_desk: _d, ...rest } = r;
                    set(i, e.target.checked ? { ...rest, delegate_to_desk: true } : rest);
                  }}
                />{' '}
                Desk decides
              </label>
              <span className="policy-row-actions">
                <button type="button" className="icon-btn" aria-label={`Move rule ${i + 1} up`} disabled={i === 0} onClick={() => move(i, -1)}>
                  ↑
                </button>
                <button type="button" className="icon-btn" aria-label={`Move rule ${i + 1} down`} disabled={i === rules.length - 1} onClick={() => move(i, 1)}>
                  ↓
                </button>
                <button type="button" className="icon-btn" aria-label={`Remove rule ${i + 1}`} onClick={() => onChange(rules.filter((_, k) => k !== i))}>
                  ✕
                </button>
              </span>
            </li>
          );
        })}
      </ol>
      <div className="actions">
        <Button size="sm" onClick={() => onChange([...rules, { tool: 'bash', action: 'ask' }])}>
          Add rule
        </Button>
        <Button size="sm" variant="ghost" disabled={sameRules(rules, DEFAULT_POLICY)} onClick={() => onChange(DEFAULT_POLICY.map((r) => ({ ...r, ...(r.match ? { match: { ...r.match } } : {}) })))}>
          Reset to the default policy
        </Button>
        {sameRules(rules, DEFAULT_POLICY) ? <span className="muted small">This is the default policy.</span> : null}
      </div>
    </div>
  );
}
```

`apps/desktop/src/renderer/settings/SettingsScreen.tsx`:

```tsx
import { useEffect, useState } from 'react';
import type { PolicyRule } from '@desk/protocol';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EmptyState } from '../components/EmptyState';
import { Field } from '../components/Field';
import { toast, toastError } from '../components/Toast';
import { navigate } from '../router';
import { useSession } from '../state/session';
import { PolicyEditor, sameRules } from './PolicyEditor';
import { SettingsFields, workingStyleOf, type WorkingStyle } from './SettingsFields';
import { useModels } from './useModels';
import './settings.css';

function useSaver() {
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (what: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(what);
    try {
      await fn();
      if (done) toast({ tone: 'info', message: done });
      return true;
    } catch (err) {
      toastError(err);
      return false;
    } finally {
      setBusy(null);
    }
  };
  return { busy, run };
}

/** A project's settings: what it is, where its sources are, how Desk works, the policy, and archiving. */
export function SettingsScreen({ projectId }: { projectId: string }) {
  const s = useSession(projectId);
  const models = useModels();
  const project = s.project?.project;
  const { busy, run } = useSaver();
  const [about, setAbout] = useState<{ name: string; goal: string; instructions: string } | null>(null);
  const [style, setStyle] = useState<WorkingStyle | null>(null);
  const [policy, setPolicy] = useState<PolicyRule[] | null>(null);
  const [confirmArchive, setConfirmArchive] = useState(false);

  // Drafts start from the live project and reset when it changes underneath (after a save, or another client).
  const settingsKey = JSON.stringify(project?.settings ?? null);
  useEffect(() => {
    if (!project) return;
    setAbout({ name: project.name, goal: project.goal, instructions: project.instructions });
  }, [project?.name, project?.goal, project?.instructions]);
  useEffect(() => {
    if (!project) return;
    setStyle(workingStyleOf(project.settings));
    setPolicy(project.settings.policy);
  }, [settingsKey]);

  if (s.status === 'loading' || !about || !style || !policy) return <div className="page muted">Loading…</div>;
  if (s.status !== 'ready' || !project || !s.project)
    return (
      <div className="page">
        <EmptyState title="Couldn't load this project">{s.error}</EmptyState>
      </div>
    );

  const aboutDirty = about.name !== project.name || about.goal !== project.goal || about.instructions !== project.instructions;
  const styleDirty = JSON.stringify(style) !== JSON.stringify(workingStyleOf(project.settings));
  const policyDirty = !sameRules(policy, project.settings.policy);

  const addSource = async () => {
    const path = await call('app.pickFolder', { purpose: 'source' }).catch((err) => (toastError(err), null));
    if (path) await run('source', () => call('projects.addSource', { id: projectId, source: { path } }));
  };

  return (
    <div className="page settings">
      <h1 className="title">Settings</h1>

      <section className="card settings-section" aria-labelledby="set-about">
        <h2 id="set-about">About this project</h2>
        <Field id="set-name" label="Name">
          <input id="set-name" className="input" value={about.name} onChange={(e) => setAbout({ ...about, name: e.target.value })} />
        </Field>
        <Field id="set-goal" label="Goal">
          <textarea id="set-goal" className="textarea" value={about.goal} onChange={(e) => setAbout({ ...about, goal: e.target.value })} />
        </Field>
        <Field id="set-instructions" label="Standing instructions" hint="Desk and every thread read these before they start.">
          <textarea id="set-instructions" className="textarea" rows={4} value={about.instructions} onChange={(e) => setAbout({ ...about, instructions: e.target.value })} />
        </Field>
        <div className="actions">
          <Button
            variant="primary"
            pending={busy === 'about'}
            disabled={!aboutDirty || !about.name.trim()}
            onClick={() => void run('about', () => call('projects.update', { id: projectId, patch: { name: about.name.trim(), goal: about.goal, instructions: about.instructions } }), 'Saved.')}
          >
            Save
          </Button>
        </div>
      </section>

      <section className="card settings-section" aria-labelledby="set-sources">
        <h2 id="set-sources">Sources</h2>
        <p className="field-hint">Folders Desk and its threads can read. A git repository gets its own branch per thread; Desk never merges.</p>
        {s.project.sources.length ? (
          <ul className="sources">
            {s.project.sources.map((src) => (
              <li key={src.id}>
                <span className={`chip ${src.kind === 'git' ? 'chip-run' : 'chip-idle'}`}>{src.kind}</span>
                <span className="grow">
                  <strong>{src.label}</strong> <span className="mono small muted">{src.path}</span>
                </span>
                <Button size="sm" variant="ghost" aria-label={`Remove ${src.label}`} pending={busy === `rm-${src.id}`} onClick={() => void run(`rm-${src.id}`, () => call('projects.removeSource', { id: projectId, sourceId: src.id }))}>
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No sources yet.</p>
        )}
        <div>
          <Button size="sm" pending={busy === 'source'} onClick={() => void addSource()}>
            Add folder…
          </Button>
        </div>
      </section>

      <section className="card settings-section" aria-labelledby="set-style">
        <h2 id="set-style">How Desk works</h2>
        <SettingsFields value={style} models={models} onChange={(p) => setStyle({ ...style, ...p })} />
        <div className="actions">
          <Button variant="primary" pending={busy === 'style'} disabled={!styleDirty} onClick={() => void run('style', () => call('projects.update', { id: projectId, patch: { settings: style } }), 'Saved.')}>
            Save
          </Button>
          {styleDirty ? (
            <Button variant="ghost" onClick={() => setStyle(workingStyleOf(project.settings))}>
              Discard changes
            </Button>
          ) : null}
        </div>
      </section>

      <section className="card settings-section" aria-labelledby="set-policy">
        <h2 id="set-policy">Policy</h2>
        <PolicyEditor rules={policy} onChange={setPolicy} />
        <div className="actions">
          <Button
            variant="primary"
            pending={busy === 'policy'}
            disabled={!policyDirty || policy.some((r) => !r.tool.trim())}
            onClick={() => void run('policy', () => call('projects.update', { id: projectId, patch: { settings: { policy } } }), 'Policy saved.')}
          >
            Save policy
          </Button>
          {policyDirty ? (
            <Button variant="ghost" onClick={() => setPolicy(project.settings.policy)}>
              Discard changes
            </Button>
          ) : null}
        </div>
      </section>

      <section className="card settings-section danger" aria-labelledby="set-archive">
        <h2 id="set-archive">Archive</h2>
        <p className="small">Archiving stops the project's threads and hides it from the map. Its library, memory and branches are kept.</p>
        <div>
          <Button variant="danger" pending={busy === 'archive'} onClick={() => setConfirmArchive(true)}>
            Archive project…
          </Button>
        </div>
      </section>
      {confirmArchive ? (
        <ConfirmDialog
          title={`Archive ${project.name}?`}
          confirmLabel="Archive"
          danger
          onCancel={() => setConfirmArchive(false)}
          onConfirm={() => {
            setConfirmArchive(false);
            void run('archive', () => call('projects.archive', { id: projectId })).then((ok) => ok && navigate({ name: 'map' }));
          }}
        >
          Running threads are stopped. Nothing is deleted.
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
```

`apps/desktop/src/renderer/settings/settings.css`:

```css
.settings {
  height: 100%;
  overflow-y: auto;
  box-sizing: border-box;
  max-width: 920px;
}
.settings-section {
  margin: 0;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.settings-section h2 {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 22px;
  font-weight: 500;
}
.settings-section.danger {
  border: 1px solid var(--accent-tint);
}
.settings-fields {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.choice {
  margin: 0;
  padding: 0;
  border: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 8px;
}
.choice legend {
  margin-bottom: 6px;
  font-size: 13px;
  font-weight: 600;
}
.choice label {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--rule);
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
}
.choice label.on {
  border-color: var(--ink);
  box-shadow: 0 0 0 1px var(--ink);
}
.choice label > span {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 13px;
}
.input.narrow {
  width: 80px;
}
.settings-models {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}
.label {
  font-size: 13px;
  font-weight: 600;
}
.slots {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}
.slot {
  width: 22px;
  height: 30px;
  padding: 0;
  border: 1.5px dashed var(--muted);
  border-radius: 6px;
  background: transparent;
  cursor: pointer;
}
.slot.on {
  border: 1.5px solid var(--run);
  background: var(--run-pastel);
}
.slots .input {
  margin-left: 10px;
}
.policy-rules {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.policy-rule {
  display: grid;
  grid-template-columns: 22px minmax(120px, 1fr) 110px minmax(160px, 1.6fr) 90px 110px auto;
  gap: 8px;
  align-items: center;
  padding: 6px 8px;
  border-radius: 8px;
  background: #fbfaf7;
  border: 1px solid var(--rule-soft);
}
.policy-num {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-min);
}
.policy-risky {
  display: flex;
  align-items: center;
  gap: 8px;
}
.policy-delegate.hidden {
  visibility: hidden;
}
.policy-row-actions {
  display: flex;
  gap: 4px;
}
.policy-row-actions .icon-btn {
  width: 26px;
  height: 26px;
}
.new-project-more {
  border-top: 1px solid var(--rule-soft);
  padding-top: 10px;
}
.new-project-more summary {
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
}
```

Other changes:
- `ProjectForm.tsx` gains a `<details>` "More options" with `<SettingsFields idPrefix="new-project">`. It sends `settings` only when the style differs from `DEFAULT_STYLE`.
- `.icon-btn` moves from `conversation.css` to `tokens.css`.
- `App.tsx` routes `tab === 'settings'` to `<SettingsScreen key={route.id} projectId={route.id} />`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(desktop): project settings — about, sources, how Desk works (check-ins, autonomy, rounds, models, slots), ordered policy editor with reset, archive; More options on new projects

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Skills — map and list, detail with files and history (compare, restore), editor, import, delete, Ask Desk

**Files:**
- Create: `apps/desktop/src/renderer/skills/diff.test.ts`, `apps/desktop/src/renderer/skills/skillsMap.test.ts`, `apps/desktop/src/renderer/skills/SkillsScreen.test.tsx`, `apps/desktop/src/renderer/skills/diff.ts`, `apps/desktop/src/renderer/skills/skillsMap.ts`, `apps/desktop/src/renderer/skills/data.ts`, `apps/desktop/src/renderer/skills/SkillsMapView.tsx`, `apps/desktop/src/renderer/skills/SkillList.tsx`, `apps/desktop/src/renderer/skills/SkillPanel.tsx`, `apps/desktop/src/renderer/skills/SkillEditor.tsx`, `apps/desktop/src/renderer/skills/AskDesk.tsx`, `apps/desktop/src/renderer/skills/SkillsScreen.tsx`, `apps/desktop/src/renderer/skills/skills.css`
- Modify: `packages/core/src/runtime/runtime.ts`, `packages/client/src/types.ts`, `apps/daemon/src/skills.test.ts`, `docs/api.md`, `apps/desktop/src/renderer/threads/threads.css`, `apps/desktop/src/renderer/theme/tokens.css`, `apps/desktop/src/renderer/App.tsx`

**Interfaces:**

- Consumes: the `skills.*`, `app.pickFolder` (`skill-import`) and `projects.send` channels; `buildSkillGraph` from `@desk/client`; the overview (projects, and thread skills and status); `MapCanvas`, `projectTone`, `FileViewer` and `replaceRoute`.
- Backend change, committed separately before the UI:
  - `Runtime.skillHistory` joins the `skill.saved` log, so each entry is `{ version, description, current, change_note, origin, ts }`.
  - A global skill is looked up across the whole log, because an agent saves it under its own project.
  - `SkillHistoryEntry` in `@desk/client` gains those fields.
  - `docs/api.md` documents the new history shape.
- Produces:

```ts
// skills/diff.ts
export function diffLines(before: string, after: string): DiffLine[];
export function withContext(lines: DiffLine[], context?: number): Array<DiffLine | null>;
export function diffFiles(before: Array<{ path; size }>, after: Array<{ path; size }>): FileChange[];
// skills/skillsMap.ts
export function layoutSkillsMap(o: { nodes: SkillNode[]; projects: Array<{ id; name; tone }>; width; height }): SkillsMapLayout;
// skills/data.ts
export type SkillRef = { scope: 'global' | 'project'; projectId?: string; name: string };
export const skillKey: (r: SkillRef) => string;              // 'global:name' | 'project:<pid>:name' (the #/skills/<key> route)
export function parseSkillKey(key: string): SkillRef | null;
export const scopeArg: (r: SkillRef) => { projectId?: string };
export function useSkills(): { status; error; nodes: SkillNode[]; refresh(): Promise<void> };
export function whoLabel(origin: string | null, titles: Map<string, string>): string;
// skills/SkillsScreen.tsx
export function SkillsScreen(props: { skill?: string }): JSX.Element;
```

- [ ] **Step 1: Write the failing tests**

`apps/desktop/src/renderer/skills/diff.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { diffFiles, diffLines, withContext } from './diff';

describe('diffLines', () => {
  it('finds added and removed lines', () => {
    expect(diffLines('a\nb\nc', 'a\nc\nd')).toEqual([
      { kind: 'same', text: 'a' },
      { kind: 'del', text: 'b' },
      { kind: 'same', text: 'c' },
      { kind: 'add', text: 'd' },
    ]);
  });

  it('trims unchanged runs to context', () => {
    const before = Array.from({ length: 20 }, (_, i) => `l${i}`).join('\n');
    const after = before.replace('l10', 'L10');
    const shown = withContext(diffLines(before, after), 1);
    expect(shown.map((l) => (l ? `${l.kind}:${l.text}` : '…'))).toEqual(['same:l9', 'del:l10', 'add:L10', 'same:l11']);
  });
});

describe('diffFiles', () => {
  it('reports added, removed and resized files', () => {
    expect(diffFiles([{ path: 'SKILL.md', size: 10 }, { path: 'a.py', size: 3 }], [{ path: 'SKILL.md', size: 12 }, { path: 'b.py', size: 4 }])).toEqual([
      { path: 'SKILL.md', change: 'changed', before: 10, after: 12 },
      { path: 'a.py', change: 'removed', before: 3 },
      { path: 'b.py', change: 'added', after: 4 },
    ]);
  });
});
```

`apps/desktop/src/renderer/skills/skillsMap.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import type { SkillNode } from '@desk/client';
import { layoutSkillsMap } from './skillsMap';

const node = (key: string, extra: Partial<SkillNode> = {}): SkillNode => {
  const [scope, a, b] = key.split(':');
  return { key, name: b ?? a!, scope: scope as 'global' | 'project', projectId: b ? a! : null, projectName: null, version: 1, description: '', shadowedIn: [], shadows: false, usedBy: [], ...extra };
};

describe('layoutSkillsMap', () => {
  it('rings globals, fills territories, links live threads and shadows', () => {
    const nodes = [
      node('global:email-sequence', { usedBy: [{ threadId: 't1', title: 'Welcome emails', status: 'running', projectId: 'p1' }] }),
      node('global:brand-voice', { shadowedIn: ['p1'] }),
      node('global:csv-analysis', { usedBy: [{ threadId: 't9', title: 'Old', status: 'done', projectId: 'p2' }] }),
      node('project:p1:brand-voice', { shadows: true, usedBy: [{ threadId: 't1', title: 'Welcome emails', status: 'running', projectId: 'p1' }] }),
      node('project:p2:receipts'),
    ];
    const l = layoutSkillsMap({ nodes, projects: [{ id: 'p1', name: 'Onboarding', tone: 'running' }, { id: 'p2', name: 'Tax', tone: 'waiting' }, { id: 'p3', name: 'Notes', tone: 'idle' }], width: 1100, height: 800 });
    expect(l.skills).toHaveLength(5);
    const g = l.skills.filter((s) => s.key.startsWith('global:'));
    for (const s of g) expect(Math.hypot(s.x - l.center.x, s.y - l.center.y)).toBeCloseTo(l.globalRadius, 5);
    expect(l.skills.find((s) => s.key === 'global:email-sequence')!.r).toBeGreaterThan(l.skills.find((s) => s.key === 'global:brand-voice')!.r);
    const p1 = l.territories.find((t) => t.projectId === 'p1')!;
    const own = l.skills.find((s) => s.key === 'project:p1:brand-voice')!;
    expect(Math.hypot(own.x - p1.x, own.y - p1.y)).toBeLessThan(p1.r);
    expect(l.territories.find((t) => t.projectId === 'p3')!.count).toBe(0);
    expect(l.markers).toHaveLength(1);
    expect(l.markers[0]).toMatchObject({ threadId: 't1', status: 'running' });
    expect(l.markers[0]!.to).toHaveLength(2);
    expect(l.shadows).toEqual([expect.objectContaining({ fromKey: 'global:brand-voice', toKey: 'project:p1:brand-voice' })]);
    for (const t of l.territories) {
      expect(t.x - t.r).toBeGreaterThanOrEqual(0);
      expect(t.x + t.r).toBeLessThanOrEqual(1100);
      expect(t.y + t.r).toBeLessThanOrEqual(800);
    }
  });
});
```

`apps/desktop/src/renderer/skills/SkillsScreen.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectSummary } from '@desk/protocol';
import { initialGlobalState } from '../../shared/state';
import { useRoute } from '../router';
import { globalStore } from '../state/global';
import { installBridge } from '../test/bridge';
import { SkillsScreen } from './SkillsScreen';

afterEach(cleanup);
beforeEach(() => {
  window.location.hash = '#/skills';
  localStorage.clear();
  const summary = (id: string, name: string, threads: unknown[]) =>
    ({ project: { id, name, goal: '', updated_at: 't' }, desk_status: 'idle', threads, latest_report: null, plan_progress: { done: 0, total: 0 }, attention_count: 0 }) as unknown as ProjectSummary;
  globalStore.set({
    ...initialGlobalState(),
    connection: { status: 'live' },
    overview: [
      summary('p1', 'Onboarding', [{ id: 't1', title: 'Welcome emails', status: 'running', reason: null, activity: null, model: 'm', git_branch: null, skills: ['email-sequence', 'brand-voice'], review_round: 0, created_at: 't', updated_at: 't' }]),
      summary('p2', 'Tax', []),
    ],
  });
});

const sk = (name: string, scope: 'global' | 'project', version = 1, description = `${name} does things`) => ({ name, scope, description, dir: `/s/${name}`, version });
const lists: Record<string, unknown[]> = {
  global: [sk('email-sequence', 'global', 2), sk('brand-voice', 'global')],
  p1: [sk('brand-voice', 'project', 3, 'House tone'), sk('email-sequence', 'global', 2)],
  p2: [sk('email-sequence', 'global', 2), sk('brand-voice', 'global')],
};
const detail = (v: number, instructions: string) => ({ ...sk('email-sequence', 'global', v), instructions, frontmatter: {}, files: [{ path: 'SKILL.md', size: 40 }, { path: 'scripts/count.py', size: 12 }] });

function Routed() {
  const r = useRoute();
  return <SkillsScreen {...(r.name === 'skills' && r.skill ? { skill: r.skill } : {})} />;
}

function setup(extra: Record<string, (input: any) => unknown> = {}) {
  const bridge = installBridge({
    'skills.list': ({ projectId }: { projectId?: string }) => lists[projectId ?? 'global'],
    'skills.get': () => detail(2, 'Plan the sequence.\nCheck the voice.'),
    'skills.history': () => [
      { version: 1, description: 'd', current: false, change_note: 'First draft', origin: 'user', ts: new Date().toISOString() },
      { version: 2, description: 'd', current: true, change_note: 'Added a voice check', origin: 'agent:t1', ts: new Date().toISOString() },
    ],
    'skills.version': ({ version }: { version: number }) => (version === 1 ? detail(1, 'Plan the sequence.') : detail(2, 'Plan the sequence.\nCheck the voice.')),
    'skills.file': () => new TextEncoder().encode('print(1)'),
    ...extra,
  });
  render(<Routed />);
  return bridge;
}

describe('SkillsScreen', () => {
  it('maps global, project and shadowed skills with live usage, and lists them too', async () => {
    setup();
    const map = await screen.findByRole('group', { name: 'Skill map' });
    expect(within(map).getByRole('button', { name: /^email-sequence, global, version 2, used by 1$/ })).toBeTruthy();
    expect(within(map).getByRole('button', { name: /^brand-voice, global, version 1, shadowed/ })).toBeTruthy();
    expect(within(map).getByRole('button', { name: /^brand-voice, Onboarding project skill, version 3/ })).toBeTruthy();
    expect(within(map).getByRole('link', { name: 'Welcome emails' }).getAttribute('href')).toBe('#/p/p1/threads/t1');
    expect(screen.getByRole('button', { name: 'In use now · 2' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    const onboarding = screen.getByRole('region', { name: 'Onboarding skills' });
    expect(onboarding.textContent).toContain('shadows global');
  });

  it('opens a skill with its change notes, compares versions and restores one', async () => {
    const bridge = setup({ 'skills.restore': () => ({ version: 3 }) });
    fireEvent.click(await screen.findByRole('button', { name: /^email-sequence, global/ }));
    await waitFor(() => expect(window.location.hash).toBe('#/skills/global%3Aemail-sequence'));
    const panel = await screen.findByRole('article', { name: 'Skill email-sequence' });
    expect(await within(panel).findByText('Added a voice check')).toBeTruthy();
    expect(panel.textContent).toContain('Welcome emails');
    fireEvent.click(within(panel).getByRole('tab', { name: /^History/ }));
    const changes = await within(panel).findByLabelText('Changes from v1 to v2');
    expect(changes.textContent).toContain('+ Check the voice.');
    fireEvent.click(within(panel).getByRole('button', { name: 'Restore' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Restore' }).at(-1)!);
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'skills.restore')?.input).toEqual({ name: 'email-sequence', version: 1 }));
    fireEvent.click(within(panel).getByRole('tab', { name: /^Files/ }));
    fireEvent.click(within(panel).getByRole('button', { name: /scripts\/count\.py/ }));
    expect(await within(panel).findByText('print(1)')).toBeTruthy();
  });

  it('edits by hand, deletes, and asks Desk to refine', async () => {
    const bridge = setup({ 'skills.save': () => ({ version: 3 }), 'skills.remove': () => ({ ok: true }), 'projects.send': () => ({ ok: true }) });
    window.location.hash = '#/skills/global%3Aemail-sequence';
    const panel = await screen.findByRole('article', { name: 'Skill email-sequence' });
    fireEvent.click(await within(panel).findByRole('button', { name: 'Edit' }));
    const sheet = screen.getByRole('dialog', { name: 'Edit email-sequence' });
    fireEvent.change(within(sheet).getByLabelText('Instructions (SKILL.md)'), { target: { value: 'Plan it.' } });
    fireEvent.click(within(sheet).getByRole('checkbox'));
    fireEvent.change(within(sheet).getByLabelText('Change note (optional)'), { target: { value: 'Shorter' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save new version' }));
    await waitFor(() =>
      expect(bridge.calls.find((c) => c.channel === 'skills.save')?.input).toEqual({ name: 'email-sequence', skill: { instructions: 'Plan it.', files: [], remove_files: ['scripts/count.py'], change_note: 'Shorter' } }),
    );
    fireEvent.click(within(panel).getByRole('button', { name: 'Refine with Desk' }));
    const ask = screen.getByRole('dialog', { name: 'Refine email-sequence with Desk' });
    expect((within(ask).getByLabelText("Which project's Desk") as HTMLSelectElement).value).toBe('p1');
    fireEvent.click(within(ask).getByRole('button', { name: 'Send to Desk' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.send')?.input).toEqual({ id: 'p1', text: 'Refine the skill "email-sequence":' }));
    expect(window.location.hash).toBe('#/p/p1/conversation');
    window.location.hash = '#/skills/global%3Aemail-sequence';
    const again = await screen.findByRole('article', { name: 'Skill email-sequence' });
    fireEvent.click(await within(again).findByRole('button', { name: 'Delete' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete' }).at(-1)!);
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'skills.remove')?.input).toEqual({ name: 'email-sequence' }));
  });

  it('creates a project skill with an uploaded script, and imports a folder', async () => {
    const bridge = setup({ 'skills.save': () => ({ version: 1 }), 'app.pickFolder': () => '/Users/me/.claude/skills/pdf', 'skills.import': () => ({ version: 1, dir: '/data/skills/pdf', created: true, description: '' }) });
    fireEvent.click(await screen.findByRole('button', { name: 'New skill' }));
    const sheet = screen.getByRole('dialog', { name: 'New skill' });
    fireEvent.change(within(sheet).getByLabelText('Name'), { target: { value: 'Bad Name' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Create skill' }));
    expect(await within(sheet).findByText(/lowercase letters/)).toBeTruthy();
    fireEvent.change(within(sheet).getByLabelText('Name'), { target: { value: 'receipt-sorting' } });
    fireEvent.change(within(sheet).getByLabelText('Scope'), { target: { value: 'p2' } });
    fireEvent.change(within(sheet).getByLabelText('Description'), { target: { value: 'Sort receipts' } });
    fireEvent.change(within(sheet).getByLabelText('Instructions (SKILL.md)'), { target: { value: 'Sort them.' } });
    fireEvent.change(within(sheet).getByTestId('skill-upload'), { target: { files: [new File(['hi'], 'sort.py')] } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Create skill' }));
    await waitFor(() =>
      expect(bridge.calls.find((c) => c.channel === 'skills.save')?.input).toEqual({
        projectId: 'p2',
        name: 'receipt-sorting',
        skill: { description: 'Sort receipts', instructions: 'Sort them.', files: [{ path: 'scripts/sort.py', content_base64: 'aGk=' }], remove_files: [] },
      }),
    );
    await waitFor(() => expect(window.location.hash).toBe('#/skills/project%3Ap2%3Areceipt-sorting'));

    fireEvent.click(screen.getByRole('button', { name: 'Import from ~/.claude/skills' }));
    const imp = await screen.findByRole('dialog', { name: 'Import a skill' });
    fireEvent.click(within(imp).getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'skills.import')?.input).toEqual({ path: '/Users/me/.claude/skills/pdf' }));
    await waitFor(() => expect(window.location.hash).toBe('#/skills/global%3Apdf'));
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop/src/renderer/skills`
Expected: FAIL, because the modules don't exist yet.

- [ ] **Step 3: Implement**

`apps/desktop/src/renderer/skills/diff.ts`:

```ts
export type DiffLine = { kind: 'same' | 'add' | 'del'; text: string };

const MAX = 4000;

/** A line diff (longest common subsequence). Very long inputs fall back to "all removed, all added". */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split('\n');
  const b = after.split('\n');
  if (a.length * b.length > MAX * MAX / 4) return [...a.map((text) => ({ kind: 'del' as const, text })), ...b.map((text) => ({ kind: 'add' as const, text }))];
  const n = a.length;
  const m = b.length;
  const lcs: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) lcs[i]![j] = a[i] === b[j] ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!);
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i]! });
      i++;
      j++;
    } else if (lcs[i + 1]![j]! >= lcs[i]![j + 1]!) out.push({ kind: 'del', text: a[i++]! });
    else out.push({ kind: 'add', text: b[j++]! });
  }
  while (i < n) out.push({ kind: 'del', text: a[i++]! });
  while (j < m) out.push({ kind: 'add', text: b[j++]! });
  return out;
}

/** Keeps changed lines with `context` unchanged lines around them; gaps become a single `…` marker (null). */
export function withContext(lines: DiffLine[], context = 3): Array<DiffLine | null> {
  const keep = new Set<number>();
  lines.forEach((l, i) => {
    if (l.kind !== 'same') for (let k = i - context; k <= i + context; k++) keep.add(k);
  });
  const out: Array<DiffLine | null> = [];
  lines.forEach((l, i) => {
    if (keep.has(i)) out.push(l);
    else if (out.at(-1) !== null) out.push(null);
  });
  if (out[0] === null) out.shift();
  if (out.at(-1) === null) out.pop();
  return out;
}

export type FileChange = { path: string; change: 'added' | 'removed' | 'changed' | 'same'; before?: number; after?: number };

/** Compares two file lists by path and size. */
export function diffFiles(before: Array<{ path: string; size: number }>, after: Array<{ path: string; size: number }>): FileChange[] {
  const a = new Map(before.map((f) => [f.path, f.size]));
  const b = new Map(after.map((f) => [f.path, f.size]));
  const paths = [...new Set([...a.keys(), ...b.keys()])].sort();
  return paths.map((path) => {
    const x = a.get(path);
    const y = b.get(path);
    if (x === undefined) return { path, change: 'added', after: y! };
    if (y === undefined) return { path, change: 'removed', before: x };
    return { path, change: x === y ? 'same' : 'changed', before: x, after: y };
  });
}
```

`apps/desktop/src/renderer/skills/skillsMap.ts`:

```ts
import type { SkillNode } from '@desk/client';
import type { AgentStatus } from '@desk/protocol';

export type MapTone = 'running' | 'waiting' | 'idle';
export type PlacedSkill = { key: string; x: number; y: number; r: number };
export type Territory = { projectId: string; name: string; tone: MapTone; x: number; y: number; r: number; count: number };
export type UsageMarker = { threadId: string; projectId: string; title: string; status: AgentStatus; x: number; y: number; to: Array<{ x: number; y: number }> };
export type ShadowLink = { fromKey: string; toKey: string; d: string; mx: number; my: number };
export type SkillsMapLayout = {
  width: number;
  height: number;
  center: { x: number; y: number };
  globalRadius: number;
  skills: PlacedSkill[];
  territories: Territory[];
  markers: UsageMarker[];
  shadows: ShadowLink[];
};

const LIVE = new Set<AgentStatus>(['running', 'waiting', 'queued']);
const skillR = (n: SkillNode, base: number) => base + Math.min(24, 6 * n.usedBy.length);
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/**
 * The skills map: global skills on a ring in the middle; each project a territory on an outer
 * ellipse holding its own skills; markers for live threads linked to the skills they use; and a
 * dashed link from a global skill to each project skill that shadows it.
 */
export function layoutSkillsMap(o: { nodes: SkillNode[]; projects: Array<{ id: string; name: string; tone: MapTone }>; width: number; height: number }): SkillsMapLayout {
  const { width: w, height: h } = o;
  const center = { x: w * 0.44, y: h * 0.52 };
  const globals = o.nodes.filter((n) => n.scope === 'global');
  const globalRadius = globals.length <= 1 ? 0 : Math.min(Math.min(w, h) * 0.2, 60 + 16 * globals.length);
  const skills = new Map<string, PlacedSkill>();
  globals.forEach((n, i) => {
    const a = (i / Math.max(1, globals.length)) * Math.PI * 2 - Math.PI / 2;
    skills.set(n.key, { key: n.key, x: center.x + Math.cos(a) * globalRadius, y: center.y + Math.sin(a) * globalRadius, r: skillR(n, 30) });
  });

  const territories: Territory[] = o.projects.map((p, i) => {
    const own = o.nodes.filter((n) => n.scope === 'project' && n.projectId === p.id);
    const r = 56 + 24 * Math.sqrt(own.length);
    const a = (i / Math.max(1, o.projects.length)) * Math.PI * 2 - Math.PI / 3;
    const x = clamp(center.x + Math.cos(a) * w * 0.36, r + 12, w - r - 12);
    const y = clamp(center.y + Math.sin(a) * h * 0.36, r + 40, h - r - 12);
    own.forEach((n, k) => {
      const ring = own.length === 1 ? 0 : r * 0.45;
      const b = (k / own.length) * Math.PI * 2 - Math.PI / 2;
      skills.set(n.key, { key: n.key, x: x + Math.cos(b) * ring, y: y + Math.sin(b) * ring, r: skillR(n, 22) });
    });
    return { projectId: p.id, name: p.name, tone: p.tone, x, y, r, count: own.length };
  });

  const byThread = new Map<string, { title: string; status: AgentStatus; projectId: string; keys: string[] }>();
  for (const n of o.nodes)
    for (const u of n.usedBy) {
      if (!LIVE.has(u.status)) continue;
      const t = byThread.get(u.threadId) ?? { title: u.title ?? 'Thread', status: u.status, projectId: u.projectId, keys: [] };
      t.keys.push(n.key);
      byThread.set(u.threadId, t);
    }
  const markers: UsageMarker[] = [];
  for (const [threadId, t] of byThread) {
    const to = t.keys.map((k) => skills.get(k)).filter((s): s is PlacedSkill => !!s);
    if (!to.length) continue;
    const mx = to.reduce((a, s) => a + s.x, 0) / to.length;
    const my = to.reduce((a, s) => a + s.y, 0) / to.length;
    const home = territories.find((x) => x.projectId === t.projectId);
    // Sit between the skills and the thread's project, a little off the skills themselves.
    const tx = home ? mx + (home.x - mx) * 0.35 : mx + 60;
    const ty = home ? my + (home.y - my) * 0.35 : my + 40;
    const k = markers.filter((m) => Math.hypot(m.x - tx, m.y - ty) < 30).length;
    markers.push({ threadId, projectId: t.projectId, title: t.title, status: t.status, x: tx, y: ty + k * 24, to: to.map((s) => ({ x: s.x, y: s.y })) });
  }

  const shadows: ShadowLink[] = [];
  for (const n of o.nodes) {
    if (n.scope !== 'project' || !n.shadows) continue;
    const from = skills.get(`global:${n.name}`);
    const to = skills.get(n.key);
    if (!from || !to) continue;
    const mx = (from.x + to.x) / 2;
    const my = (from.y + to.y) / 2 - 60;
    shadows.push({ fromKey: from.key, toKey: to.key, d: `M${from.x} ${from.y} Q ${mx} ${my} ${to.x} ${to.y}`, mx, my: my + 30 });
  }
  return { width: w, height: h, center, globalRadius, skills: [...skills.values()], territories, markers, shadows };
}
```

`apps/desktop/src/renderer/skills/data.ts`:

```ts
import { useCallback, useEffect, useMemo, useState } from 'react';
import { buildSkillGraph, type SkillNode, type SkillSummary } from '@desk/client';
import { call } from '../bridge';
import { describeError } from '../components/Toast';
import { useGlobal } from '../state/global';

export type SkillRef = { scope: 'global' | 'project'; projectId?: string; name: string };

export const skillKey = (r: SkillRef) => (r.scope === 'global' ? `global:${r.name}` : `project:${r.projectId}:${r.name}`);

export function parseSkillKey(key: string): SkillRef | null {
  const parts = key.split(':');
  if (parts[0] === 'global' && parts.length === 2 && parts[1]) return { scope: 'global', name: parts[1] };
  if (parts[0] === 'project' && parts.length === 3 && parts[1] && parts[2]) return { scope: 'project', projectId: parts[1], name: parts[2] };
  return null;
}

/** The IPC scope argument: project skills name their project; global ones don't. */
export const scopeArg = (r: SkillRef): { projectId?: string } => (r.scope === 'project' && r.projectId ? { projectId: r.projectId } : {});

type Lists = { global: SkillSummary[]; projects: Record<string, SkillSummary[]> };

/** Global and per-project skills, joined with live thread usage from the overview. Refetches on focus and every 30 s. */
export function useSkills(): { status: 'loading' | 'ready' | 'error'; error: string | null; nodes: SkillNode[]; refresh(): Promise<void> } {
  const overview = useGlobal((g) => g.overview);
  const [lists, setLists] = useState<Lists | null>(null);
  const [error, setError] = useState<string | null>(null);
  const projectIds = overview.map((p) => p.project.id).join(',');

  const refresh = useCallback(async () => {
    try {
      const ids = projectIds ? projectIds.split(',') : [];
      const [global, ...perProject] = await Promise.all([call('skills.list', {}), ...ids.map((id) => call('skills.list', { projectId: id }).catch(() => [] as SkillSummary[]))]);
      setLists({ global, projects: Object.fromEntries(ids.map((id, i) => [id, perProject[i] ?? []])) });
      setError(null);
    } catch (err) {
      setError(describeError(err).message);
    }
  }, [projectIds]);

  useEffect(() => {
    void refresh();
    const onFocus = () => void refresh();
    window.addEventListener('focus', onFocus);
    const t = setInterval(() => void refresh(), 30_000);
    return () => {
      window.removeEventListener('focus', onFocus);
      clearInterval(t);
    };
  }, [refresh]);

  const nodes = useMemo(
    () =>
      lists
        ? buildSkillGraph({
            global: lists.global,
            projects: overview.map((p) => ({ id: p.project.id, name: p.project.name, skills: lists.projects[p.project.id] ?? [] })),
            threads: overview.flatMap((p) => p.threads.map((t) => ({ id: t.id, title: t.title, status: t.status, projectId: p.project.id, skills: t.skills }))),
          })
        : [],
    [lists, overview],
  );
  return { status: lists ? 'ready' : error ? 'error' : 'loading', error, nodes, refresh };
}

/** `user` → you, `agent:<id>` → the agent's title when known. */
export function whoLabel(origin: string | null, titles: Map<string, string>): string {
  if (!origin) return 'Unknown';
  if (origin === 'user') return 'You';
  if (origin.startsWith('agent:')) return titles.get(origin.slice(6)) ?? 'Desk';
  return origin;
}
```

`apps/desktop/src/renderer/skills/SkillsMapView.tsx`:

```tsx
import type { SkillNode } from '@desk/client';
import type { AgentStatus } from '@desk/protocol';
import { MapCanvas } from '../map/MapCanvas';
import { href } from '../router';
import { layoutSkillsMap, type MapTone } from './skillsMap';
import '../map/map.css';

const TERRITORY: Record<MapTone, { fill: string; stroke: string; text: string }> = {
  running: { fill: '#E3E8F5', stroke: '#C9D3EC', text: '#1F45A8' },
  waiting: { fill: '#F3E6CF', stroke: '#E6D3AF', text: '#7A4500' },
  idle: { fill: '#E6E1D7', stroke: '#D6CFC1', text: '#4A4740' },
};
const LINE: Partial<Record<AgentStatus, { stroke: string; dash?: string; width: number }>> = {
  running: { stroke: '#2F5BD3', width: 2.5 },
  waiting: { stroke: '#A15C00', width: 2, dash: '4 4' },
  queued: { stroke: '#A15C00', width: 2, dash: '4 4' },
};

/** Global skills in the middle, project skills inside their project, live threads linked to the skills they use. */
export function SkillsMapView(o: { nodes: SkillNode[]; projects: Array<{ id: string; name: string; tone: MapTone }>; selected: string | null; onSelect(key: string): void }) {
  return (
    <MapCanvas label="Skill map">
      {({ width, height }) => {
        const l = layoutSkillsMap({ nodes: o.nodes, projects: o.projects, width, height });
        const byKey = new Map(o.nodes.map((n) => [n.key, n]));
        return (
          <>
            <svg width={width} height={height} aria-hidden="true" className="skills-svg">
              {l.globalRadius ? <circle cx={l.center.x} cy={l.center.y} r={l.globalRadius} fill="none" stroke="#D3CCBE" strokeDasharray="3 6" /> : null}
              {l.territories.map((t) => (
                <circle key={t.projectId} cx={t.x} cy={t.y} r={t.r} fill={TERRITORY[t.tone].fill} stroke={TERRITORY[t.tone].stroke} />
              ))}
              {l.shadows.map((s) => (
                <path key={s.toKey} d={s.d} fill="none" stroke="#A15C00" strokeWidth="1.5" strokeDasharray="5 5" />
              ))}
              {l.markers.flatMap((m) =>
                m.to.map((p, i) => {
                  const line = LINE[m.status] ?? { stroke: '#8A857B', width: 2 };
                  return <path key={`${m.threadId}-${i}`} d={`M${m.x} ${m.y} L ${p.x} ${p.y}`} stroke={line.stroke} strokeWidth={line.width} strokeDasharray={line.dash} />;
                }),
              )}
            </svg>
            {l.territories.map((t) => (
              <span key={t.projectId} className="skills-territory-label" style={{ left: t.x, top: t.y - t.r - 22, color: TERRITORY[t.tone].text }}>
                {t.name}
                {t.count ? null : <span className="muted small"> · no project skills</span>}
              </span>
            ))}
            {l.globalRadius || o.nodes.some((n) => n.scope === 'global') ? (
              <span className="skills-global-label" style={{ left: l.center.x, top: l.center.y + l.globalRadius + 48 }}>
                GLOBAL
              </span>
            ) : null}
            {l.shadows.slice(0, 1).map((s) => (
              <span key={s.toKey} className="skills-shadow-label" style={{ left: s.mx, top: s.my - 40 }}>
                shadowed by
              </span>
            ))}
            {l.skills.map((s) => {
              const n = byKey.get(s.key)!;
              const shadowed = n.scope === 'global' && n.shadowedIn.length > 0;
              return (
                <button
                  key={s.key}
                  type="button"
                  className={`skill-node ${n.scope}${shadowed ? ' shadowed' : ''}${n.error ? ' broken' : ''}${o.selected === s.key ? ' selected' : ''}`}
                  style={{ left: s.x, top: s.y, width: s.r * 2, height: s.r * 2 }}
                  aria-label={`${n.name}, ${n.scope === 'global' ? 'global' : `${n.projectName ?? 'project'} project skill`}, version ${n.version}${shadowed ? ', shadowed' : ''}${n.usedBy.length ? `, used by ${n.usedBy.length}` : ''}`}
                  aria-pressed={o.selected === s.key}
                  onClick={() => o.onSelect(s.key)}
                >
                  <span className="skill-node-name">{n.name}</span>
                  <span className="skill-node-v">v{n.version}</span>
                </button>
              );
            })}
            {l.markers.map((m) => (
              <a key={m.threadId} className={`skill-marker status-${m.status}`} style={{ left: m.x, top: m.y }} href={href({ name: 'project', id: m.projectId, tab: 'threads', threadId: m.threadId })}>
                <span className="skill-marker-dot" aria-hidden="true" />
                {m.title}
              </a>
            ))}
          </>
        );
      }}
    </MapCanvas>
  );
}
```

`apps/desktop/src/renderer/skills/SkillList.tsx`:

```tsx
import type { SkillNode } from '@desk/client';

/** The list alternative to the map: global skills, then each project's own. */
export function SkillList(o: { nodes: SkillNode[]; projectNames: Map<string, string>; selected: string | null; onSelect(key: string): void }) {
  const groups: Array<{ title: string; nodes: SkillNode[] }> = [{ title: 'Global', nodes: o.nodes.filter((n) => n.scope === 'global') }];
  for (const [id, name] of o.projectNames) {
    const own = o.nodes.filter((n) => n.scope === 'project' && n.projectId === id);
    if (own.length) groups.push({ title: name, nodes: own });
  }
  return (
    <div className="skill-list">
      {groups.map((g) => (
        <section key={g.title} aria-label={`${g.title} skills`}>
          <h2 className="skill-list-title">{g.title}</h2>
          {g.nodes.length ? (
            <ul>
              {g.nodes
                .slice()
                .sort((a, b) => a.name.localeCompare(b.name))
                .map((n) => (
                  <li key={n.key}>
                    <button type="button" className={`skill-row${o.selected === n.key ? ' current' : ''}`} aria-pressed={o.selected === n.key} onClick={() => o.onSelect(n.key)}>
                      <span className="mono skill-row-name">{n.name}</span>
                      <span className="muted small grow">{n.error ? `Broken: ${n.error}` : n.description}</span>
                      {n.shadows ? <span className="chip chip-wait">shadows global</span> : null}
                      {n.shadowedIn.length ? <span className="chip chip-idle">shadowed in {n.shadowedIn.length}</span> : null}
                      {n.usedBy.length ? <span className="chip chip-run">in use · {n.usedBy.length}</span> : null}
                      <span className="mono small muted">v{n.version}</span>
                    </button>
                  </li>
                ))}
            </ul>
          ) : (
            <p className="muted small">No global skills yet.</p>
          )}
        </section>
      ))}
    </div>
  );
}
```

`apps/desktop/src/renderer/skills/SkillPanel.tsx`:

```tsx
import { useEffect, useMemo, useState } from 'react';
import type { SkillDetail, SkillHistoryEntry, SkillNode } from '@desk/client';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EmptyState } from '../components/EmptyState';
import { FileViewer } from '../components/FileViewer';
import { SafeMarkdown } from '../components/SafeMarkdown';
import { describeError, toast, toastError } from '../components/Toast';
import { ago, bytes } from '../format';
import { href } from '../router';
import { useNow } from '../state/now';
import { scopeArg, whoLabel, type SkillRef } from './data';
import { diffFiles, diffLines, withContext } from './diff';

type Tab = 'overview' | 'instructions' | 'files' | 'history';

function Compare({ skill, history }: { skill: SkillRef; history: SkillHistoryEntry[] }) {
  const versions = history.map((h) => h.version);
  const [from, setFrom] = useState(versions.at(-2) ?? versions[0] ?? 1);
  const [to, setTo] = useState(versions.at(-1) ?? 1);
  const [pair, setPair] = useState<[SkillDetail, SkillDetail] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setPair(null);
    Promise.all([call('skills.version', { ...scopeArg(skill), name: skill.name, version: from }), call('skills.version', { ...scopeArg(skill), name: skill.name, version: to })])
      .then(([a, b]) => live && (setPair([a, b]), setError(null)))
      .catch((err) => live && setError(describeError(err).message));
    return () => {
      live = false;
    };
  }, [skill.scope, skill.projectId, skill.name, from, to]);
  const lines = useMemo(() => (pair ? withContext(diffLines(`${pair[0].description}\n\n${pair[0].instructions}`, `${pair[1].description}\n\n${pair[1].instructions}`)) : []), [pair]);
  const files = useMemo(() => (pair ? diffFiles(pair[0].files, pair[1].files).filter((f) => f.change !== 'same') : []), [pair]);
  return (
    <div className="skill-compare">
      <div className="skill-compare-bar">
        <label>
          Compare{' '}
          <select className="select" aria-label="From version" value={from} onChange={(e) => setFrom(Number(e.target.value))}>
            {versions.map((v) => (
              <option key={v} value={v}>
                v{v}
              </option>
            ))}
          </select>
        </label>
        <label>
          with{' '}
          <select className="select" aria-label="To version" value={to} onChange={(e) => setTo(Number(e.target.value))}>
            {versions.map((v) => (
              <option key={v} value={v}>
                v{v}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error ? <p className="field-error">{error}</p> : null}
      {pair ? (
        <>
          {lines.length ? (
            <pre className="diff-patch" aria-label={`Changes from v${from} to v${to}`}>
              {lines.map((l, i) =>
                l === null ? (
                  <span key={i} className="diff-hunk">
                    {'…\n'}
                  </span>
                ) : (
                  <span key={i} className={l.kind === 'add' ? 'diff-line-add' : l.kind === 'del' ? 'diff-line-del' : undefined}>
                    {l.kind === 'add' ? '+ ' : l.kind === 'del' ? '- ' : '  '}
                    {l.text}
                    {'\n'}
                  </span>
                ),
              )}
            </pre>
          ) : (
            <p className="muted small">The instructions are the same.</p>
          )}
          {files.length ? (
            <ul className="skill-file-changes">
              {files.map((f) => (
                <li key={f.path}>
                  <span className={`chip ${f.change === 'added' ? 'chip-run' : f.change === 'removed' ? 'chip-fail' : 'chip-idle'}`}>{f.change}</span> <span className="mono">{f.path}</span>
                  {f.change === 'changed' ? <span className="muted small"> {bytes(f.before!)} → {bytes(f.after!)}</span> : null}
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : error ? null : (
        <p className="muted small">Loading…</p>
      )}
    </div>
  );
}

/** One skill in full: overview, instructions, files and history (compare any two versions, restore), with edit, delete and Desk. */
export function SkillPanel(o: {
  skill: SkillRef;
  node: SkillNode | undefined;
  projectNames: Map<string, string>;
  threadTitles: Map<string, string>;
  version: number;
  onEdit(detail: SkillDetail): void;
  onAskDesk(): void;
  onChanged(): void;
  onClose(): void;
}) {
  const { skill } = o;
  const now = useNow();
  const [tab, setTab] = useState<Tab>('overview');
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [history, setHistory] = useState<SkillHistoryEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<{ path: string; data: Uint8Array } | null>(null);
  const [confirm, setConfirm] = useState<{ kind: 'delete' } | { kind: 'restore'; version: number } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let live = true;
    setError(null);
    Promise.all([call('skills.get', { ...scopeArg(skill), name: skill.name }), call('skills.history', { ...scopeArg(skill), name: skill.name })])
      .then(([d, h]) => live && (setDetail(d), setHistory(h)))
      .catch((err) => live && setError(describeError(err).message));
    return () => {
      live = false;
    };
  }, [skill.scope, skill.projectId, skill.name, o.version, reload]);
  useEffect(() => {
    setTab('overview');
    setFile(null);
  }, [skill.scope, skill.projectId, skill.name]);

  const openFile = async (path: string) => {
    try {
      setFile({ path, data: await call('skills.file', { ...scopeArg(skill), name: skill.name, path }) });
    } catch (err) {
      toastError(err);
    }
  };
  const act = async (what: string, fn: () => Promise<unknown>, done: string) => {
    setConfirm(null);
    setBusy(what);
    try {
      await fn();
      toast({ tone: 'info', message: done });
      o.onChanged();
      setReload((r) => r + 1);
      return true;
    } catch (err) {
      toastError(err);
      return false;
    } finally {
      setBusy(null);
    }
  };

  const node = o.node;
  const current = history.find((h) => h.current);
  const scopeLabel = skill.scope === 'global' ? 'Global' : (o.projectNames.get(skill.projectId!) ?? 'Project');
  return (
    <article className="card skill-panel" aria-label={`Skill ${skill.name}`}>
      <div className="skill-panel-head">
        <h2 className="mono">{skill.name}</h2>
        <span className={`skill-scope ${skill.scope}`}>{scopeLabel}</span>
        <button type="button" className="icon-btn" aria-label="Close" onClick={o.onClose}>
          ✕
        </button>
      </div>
      {error ? (
        <EmptyState title="Couldn't load this skill">{error}</EmptyState>
      ) : !detail ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <p className="skill-desc">{detail.description}</p>
          {detail.error ? (
            <p className="field-error" role="alert">
              SKILL.md has a problem, so agents can't use this skill until it's fixed: {detail.error}
            </p>
          ) : null}
          <div className="tabs" role="tablist" aria-label="Skill">
            {(['overview', 'instructions', 'files', 'history'] as Tab[]).map((t) => (
              <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
                {t === 'overview' ? 'Overview' : t === 'instructions' ? 'Instructions' : t === 'files' ? `Files · ${detail.files.length}` : `History · ${history.length}`}
              </button>
            ))}
          </div>
          <div className="skill-tab" role="tabpanel">
            {tab === 'overview' ? (
              <>
                {node?.shadows ? (
                  <p className="shadow-note">
                    {scopeLabel} has its own {skill.name}, so agents there use this one instead of the global skill.
                  </p>
                ) : null}
                {node?.shadowedIn.length ? (
                  <p className="shadow-note">
                    {node.shadowedIn.map((id) => o.projectNames.get(id) ?? id).join(', ')} {node.shadowedIn.length === 1 ? 'has' : 'have'} its own {skill.name}, so agents there use that one. Everywhere
                    else they use this.
                  </p>
                ) : null}
                {current ? (
                  <div className="skill-change">
                    <strong className="small">
                      {whoLabel(current.origin, o.threadTitles)} · v{current.version}
                      {current.ts ? ` · ${ago(current.ts, now)} ago` : ''}
                    </strong>
                    <span className="small">{current.change_note || 'No change note.'}</span>
                  </div>
                ) : null}
                <div className="skill-versions" role="group" aria-label="Versions">
                  {history
                    .slice()
                    .reverse()
                    .map((h) => (
                      <button key={h.version} type="button" className={h.current ? 'on' : undefined} aria-pressed={h.current} onClick={() => setTab('history')}>
                        v{h.version}
                      </button>
                    ))}
                </div>
                <div>
                  <span className="label">Used by</span>
                  {node?.usedBy.length ? (
                    <ul className="skill-used">
                      {node.usedBy.map((u) => (
                        <li key={u.threadId}>
                          <span className={`status-dot status-dot-${u.status}`} aria-hidden="true" />
                          <a href={href({ name: 'project', id: u.projectId, tab: 'threads', threadId: u.threadId })}>{u.title ?? 'Thread'}</a>
                          <span className="muted small"> · {u.status} · {o.projectNames.get(u.projectId) ?? ''}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="muted small">Not in use right now.</p>
                  )}
                </div>
              </>
            ) : tab === 'instructions' ? (
              <SafeMarkdown text={detail.instructions} />
            ) : tab === 'files' ? (
              <div className="skill-files">
                <ul className="files-list">
                  {detail.files.map((f) => (
                    <li key={f.path}>
                      <button type="button" className={`files-entry${file?.path === f.path ? ' current' : ''}`} onClick={() => void openFile(f.path)}>
                        <span className="grow mono">{f.path}</span>
                        <span className="muted small">{bytes(f.size)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
                {file ? <FileViewer path={file.path} data={file.data} /> : <p className="muted small">Pick a file to view it.</p>}
              </div>
            ) : (
              <>
                <ol className="skill-history" reversed>
                  {history
                    .slice()
                    .reverse()
                    .map((h) => (
                      <li key={h.version}>
                        <span className="mono">v{h.version}</span>
                        <span className="grow">
                          <span className="small">
                            <strong>{whoLabel(h.origin, o.threadTitles)}</strong>
                            {h.ts ? ` · ${ago(h.ts, now)} ago` : ''}
                          </span>
                          <span className="small muted">{h.change_note || h.description}</span>
                        </span>
                        {h.current ? (
                          <span className="chip chip-done">current</span>
                        ) : (
                          <Button size="sm" pending={busy === `restore-${h.version}`} onClick={() => setConfirm({ kind: 'restore', version: h.version })}>
                            Restore
                          </Button>
                        )}
                      </li>
                    ))}
                </ol>
                {history.length > 1 ? <Compare skill={skill} history={history} /> : null}
              </>
            )}
          </div>
          <div className="skill-actions">
            <Button variant="primary" onClick={() => o.onEdit(detail)}>
              Edit
            </Button>
            <Button onClick={o.onAskDesk}>Refine with Desk</Button>
            <span className="grow" />
            <Button variant="ghost" pending={busy === 'delete'} onClick={() => setConfirm({ kind: 'delete' })}>
              Delete
            </Button>
          </div>
        </>
      )}
      {confirm?.kind === 'restore' ? (
        <ConfirmDialog
          title={`Restore v${confirm.version}?`}
          confirmLabel="Restore"
          onCancel={() => setConfirm(null)}
          onConfirm={() => void act(`restore-${confirm.version}`, () => call('skills.restore', { ...scopeArg(skill), name: skill.name, version: confirm.version }), `Restored v${confirm.version} as a new version.`)}
        >
          It comes back as the newest version. Nothing in the history is lost.
        </ConfirmDialog>
      ) : null}
      {confirm?.kind === 'delete' ? (
        <ConfirmDialog
          title={`Delete ${skill.name}?`}
          confirmLabel="Delete"
          danger
          onCancel={() => setConfirm(null)}
          onConfirm={() =>
            void act('delete', () => call('skills.remove', { ...scopeArg(skill), name: skill.name }), `Deleted ${skill.name}.`).then((ok) => ok && o.onClose())
          }
        >
          Agents stop using it. Its past versions stay in the skill's history on disk.
        </ConfirmDialog>
      ) : null}
    </article>
  );
}
```

`apps/desktop/src/renderer/skills/SkillEditor.tsx`:

```tsx
import { useRef, useState } from 'react';
import type { SkillDetail } from '@desk/client';
import { SkillName } from '@desk/protocol';
import { call, DeskCallError } from '../bridge';
import { Button } from '../components/Button';
import { Field } from '../components/Field';
import { Sheet } from '../components/Sheet';
import { toastError } from '../components/Toast';
import { fileToBase64, MAX_UPLOAD, textToBase64 } from '../files';
import { bytes } from '../format';
import { scopeArg, type SkillRef } from './data';

type NewFile = { path: string; content: string | File };

/** Create a skill, or refine one by hand: description, instructions (SKILL.md), files to add or remove, and a change note. */
export function SkillEditor(o: { skill?: { ref: SkillRef; detail: SkillDetail }; projects: Array<{ id: string; name: string }>; defaultProjectId?: string; onSaved(ref: SkillRef): void; onClose(): void }) {
  const editing = o.skill;
  const [name, setName] = useState(editing?.ref.name ?? '');
  const [scope, setScope] = useState<string>(editing ? (editing.ref.scope === 'global' ? 'global' : editing.ref.projectId!) : (o.defaultProjectId ?? 'global'));
  const [description, setDescription] = useState(editing?.detail.description ?? '');
  const [instructions, setInstructions] = useState(editing?.detail.instructions ?? '');
  const [remove, setRemove] = useState<Set<string>>(new Set());
  const [added, setAdded] = useState<NewFile[]>([]);
  const [folder, setFolder] = useState('scripts/');
  const [textPath, setTextPath] = useState('');
  const [textBody, setTextBody] = useState('');
  const [note, setNote] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const pickRef = useRef<HTMLInputElement>(null);
  const existing = (editing?.detail.files ?? []).filter((f) => f.path !== 'SKILL.md');

  const addUploads = (files: FileList | null) => {
    const prefix = folder.trim().replace(/^\/+/, '');
    const next: NewFile[] = [];
    for (const f of Array.from(files ?? [])) {
      if (f.size > MAX_UPLOAD) {
        toastError(new Error(`${f.name} is larger than 25 MB.`));
        continue;
      }
      next.push({ path: `${prefix && !prefix.endsWith('/') ? `${prefix}/` : prefix}${f.name}`, content: f });
    }
    setAdded((a) => [...a.filter((x) => !next.some((n) => n.path === x.path)), ...next]);
    if (pickRef.current) pickRef.current.value = '';
  };

  const save = async () => {
    const e: Record<string, string> = {};
    const parsedName = SkillName.safeParse(name.trim());
    if (!editing && !parsedName.success) e.name = parsedName.error.issues[0]?.message ?? 'Invalid name';
    if (!description.trim()) e.description = 'Say in one line what the skill is for; Desk uses it to pick skills.';
    if (!instructions.trim()) e.instructions = 'Write the instructions agents follow.';
    setErrors(e);
    if (Object.keys(e).length) return;
    const ref: SkillRef = editing ? editing.ref : scope === 'global' ? { scope: 'global', name: name.trim() } : { scope: 'project', projectId: scope, name: name.trim() };
    setPending(true);
    try {
      const files = await Promise.all(added.map(async (f) => ({ path: f.path, content_base64: typeof f.content === 'string' ? await textToBase64(f.content) : await fileToBase64(f.content) })));
      await call('skills.save', {
        ...scopeArg(ref),
        name: ref.name,
        skill: {
          ...(!editing || description.trim() !== editing.detail.description ? { description: description.trim() } : {}),
          ...(!editing || instructions !== editing.detail.instructions ? { instructions } : {}),
          files,
          remove_files: [...remove],
          ...(note.trim() ? { change_note: note.trim() } : {}),
        },
      });
      o.onSaved(ref);
    } catch (err) {
      if (err instanceof DeskCallError && err.status === 400) setErrors({ form: err.message });
      else toastError(err);
    } finally {
      setPending(false);
    }
  };

  return (
    <Sheet
      title={editing ? `Edit ${editing.ref.name}` : 'New skill'}
      width={720}
      onClose={o.onClose}
      footer={
        <>
          <Button onClick={o.onClose}>Cancel</Button>
          <Button variant="primary" pending={pending} onClick={() => void save()}>
            {editing ? 'Save new version' : 'Create skill'}
          </Button>
        </>
      }
    >
      {editing ? null : (
        <div className="skill-editor-row">
          <Field id="skill-name" label="Name" error={errors.name ?? null} hint="Lowercase words joined by hyphens, like weekly-report.">
            <input id="skill-name" className="input mono" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field id="skill-scope" label="Scope">
            <select id="skill-scope" className="select" value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="global">Global (every project)</option>
              {o.projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} only
                </option>
              ))}
            </select>
          </Field>
        </div>
      )}
      <Field id="skill-description" label="Description" error={errors.description ?? null}>
        <input id="skill-description" className="input" maxLength={1024} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <Field id="skill-instructions" label="Instructions (SKILL.md)" error={errors.instructions ?? null} hint="Markdown. Scripts in the skill run with skill_run, in the sandbox.">
        <textarea id="skill-instructions" className="textarea mono" rows={12} value={instructions} onChange={(e) => setInstructions(e.target.value)} />
      </Field>
      <div className="field">
        <span className="label">Files</span>
        {existing.length || added.length ? (
          <ul className="skill-files-edit">
            {existing.map((f) => (
              <li key={f.path} className={remove.has(f.path) ? 'removing' : undefined}>
                <span className="mono grow">{f.path}</span>
                <span className="muted small">{bytes(f.size)}</span>
                <label className="small">
                  <input
                    type="checkbox"
                    checked={remove.has(f.path)}
                    onChange={(e) =>
                      setRemove((r) => {
                        const n = new Set(r);
                        if (e.target.checked) n.add(f.path);
                        else n.delete(f.path);
                        return n;
                      })
                    }
                  />{' '}
                  Remove
                </label>
              </li>
            ))}
            {added.map((f) => (
              <li key={f.path} className="adding">
                <span className="mono grow">{f.path}</span>
                <span className="muted small">{typeof f.content === 'string' ? 'new text file' : bytes(f.content.size)}</span>
                <Button size="sm" variant="ghost" aria-label={`Don't add ${f.path}`} onClick={() => setAdded((a) => a.filter((x) => x.path !== f.path))}>
                  ✕
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="field-hint">No files besides SKILL.md.</p>
        )}
        <div className="skill-add-files">
          <input className="input mono" aria-label="Folder for uploads" value={folder} onChange={(e) => setFolder(e.target.value)} />
          <Button size="sm" onClick={() => pickRef.current?.click()}>
            Upload files…
          </Button>
          <input ref={pickRef} type="file" multiple hidden data-testid="skill-upload" onChange={(e) => addUploads(e.target.files)} />
        </div>
        <details className="skill-new-text">
          <summary>New text file</summary>
          <div className="skill-add-files">
            <input className="input mono" aria-label="New file path" placeholder="scripts/check.py" value={textPath} onChange={(e) => setTextPath(e.target.value)} />
            <Button
              size="sm"
              disabled={!textPath.trim()}
              onClick={() => {
                const path = textPath.trim().replace(/^\/+/, '');
                setAdded((a) => [...a.filter((x) => x.path !== path), { path, content: textBody }]);
                setTextPath('');
                setTextBody('');
              }}
            >
              Add file
            </Button>
          </div>
          <textarea className="textarea mono" aria-label="New file content" rows={6} value={textBody} onChange={(e) => setTextBody(e.target.value)} />
        </details>
      </div>
      <Field id="skill-note" label="Change note (optional)" hint="Shown in the history next to this version.">
        <input id="skill-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
      {errors.form ? (
        <p className="field-error" role="alert">
          {errors.form}
        </p>
      ) : null}
    </Sheet>
  );
}
```

`apps/desktop/src/renderer/skills/AskDesk.tsx`:

```tsx
import { useState } from 'react';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { Field } from '../components/Field';
import { Sheet } from '../components/Sheet';
import { toast, toastError } from '../components/Toast';
import { navigate } from '../router';

/** Sends Desk a message about a skill: build a new one, or refine a named one. Global skills need a project whose Desk does the work. */
export function AskDesk(o: { skillName?: string; projects: Array<{ id: string; name: string }>; defaultProjectId?: string; onClose(): void }) {
  const [projectId, setProjectId] = useState(o.defaultProjectId ?? o.projects[0]?.id ?? '');
  const [text, setText] = useState(o.skillName ? `Refine the skill "${o.skillName}": ` : 'Build a new skill that ');
  const [pending, setPending] = useState(false);
  const send = async () => {
    setPending(true);
    try {
      await call('projects.send', { id: projectId, text: text.trim() });
      toast({ tone: 'info', message: 'Sent to Desk.' });
      o.onClose();
      navigate({ name: 'project', id: projectId, tab: 'conversation' });
    } catch (err) {
      toastError(err);
    } finally {
      setPending(false);
    }
  };
  return (
    <Sheet
      title={o.skillName ? `Refine ${o.skillName} with Desk` : 'Ask Desk for a new skill'}
      onClose={o.onClose}
      footer={
        <>
          <Button onClick={o.onClose}>Cancel</Button>
          <Button variant="primary" pending={pending} disabled={!projectId || text.trim().length < 12} onClick={() => void send()}>
            Send to Desk
          </Button>
        </>
      }
    >
      {o.projects.length ? (
        <>
          <Field id="ask-project" label="Which project's Desk" hint="Desk has a thread draft and test it, then installs it.">
            <select id="ask-project" className="select" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
              {o.projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
          <Field id="ask-text" label="Message">
            <textarea id="ask-text" className="textarea" rows={4} value={text} onChange={(e) => setText(e.target.value)} />
          </Field>
        </>
      ) : (
        <p>Create a project first; Desk works on skills from inside a project.</p>
      )}
    </Sheet>
  );
}
```

`apps/desktop/src/renderer/skills/SkillsScreen.tsx`:

```tsx
import { useMemo, useState } from 'react';
import type { SkillDetail } from '@desk/client';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { Field } from '../components/Field';
import { Sheet } from '../components/Sheet';
import { toast, toastError } from '../components/Toast';
import { projectTone } from '../map/OrbitMap';
import { replaceRoute } from '../router';
import { useGlobal } from '../state/global';
import { AskDesk } from './AskDesk';
import { parseSkillKey, skillKey, useSkills, type SkillRef } from './data';
import { SkillEditor } from './SkillEditor';
import { SkillList } from './SkillList';
import { SkillPanel } from './SkillPanel';
import { SkillsMapView } from './SkillsMapView';
import './skills.css';

type View = 'map' | 'list';
type Filter = 'all' | 'used' | 'shadowed';

function useView(): [View, (v: View) => void] {
  const [v, setV] = useState<View>(() => {
    try {
      return localStorage.getItem('desk.skillsView') === 'list' ? 'list' : 'map';
    } catch {
      return 'map';
    }
  });
  return [
    v,
    (next) => {
      setV(next);
      try {
        localStorage.setItem('desk.skillsView', next);
      } catch {
        // A convenience only.
      }
    },
  ];
}

function ImportSheet(o: { path: string; projects: Array<{ id: string; name: string }>; onDone(ref: SkillRef): void; onClose(): void }) {
  const [scope, setScope] = useState('global');
  const [name, setName] = useState('');
  const [pending, setPending] = useState(false);
  const run = async () => {
    setPending(true);
    try {
      const projectId = scope === 'global' ? undefined : scope;
      const r = await call('skills.import', { ...(projectId ? { projectId } : {}), path: o.path, ...(name.trim() ? { name: name.trim() } : {}) });
      const imported = r.dir.split('/').filter(Boolean).pop() ?? name.trim();
      toast({ tone: 'info', message: `Imported ${imported}.` });
      o.onDone(projectId ? { scope: 'project', projectId, name: imported } : { scope: 'global', name: imported });
    } catch (err) {
      toastError(err);
    } finally {
      setPending(false);
    }
  };
  return (
    <Sheet
      title="Import a skill"
      onClose={o.onClose}
      footer={
        <>
          <Button onClick={o.onClose}>Cancel</Button>
          <Button variant="primary" pending={pending} onClick={() => void run()}>
            Import
          </Button>
        </>
      }
    >
      <p className="small">
        From <span className="mono">{o.path}</span>. The folder needs a SKILL.md. It's copied in; the original stays where it is.
      </p>
      <Field id="import-scope" label="Scope">
        <select id="import-scope" className="select" value={scope} onChange={(e) => setScope(e.target.value)}>
          <option value="global">Global (every project)</option>
          {o.projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} only
            </option>
          ))}
        </select>
      </Field>
      <Field id="import-name" label="Name (optional)" hint="Defaults to the folder's name.">
        <input id="import-name" className="input mono" value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
    </Sheet>
  );
}

/** Every skill Desk and its threads can use: a map (or list) with global, project and shadowed skills, and what's in use now. */
export function SkillsScreen({ skill }: { skill?: string }) {
  const overview = useGlobal((g) => g.overview);
  const data = useSkills();
  const [view, setView] = useView();
  const [filter, setFilter] = useState<Filter>('all');
  const [editor, setEditor] = useState<{ skill?: { ref: SkillRef; detail: SkillDetail } } | null>(null);
  const [ask, setAsk] = useState<{ name?: string; projectId?: string } | null>(null);
  const [importPath, setImportPath] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  const projects = useMemo(() => overview.map((p) => ({ id: p.project.id, name: p.project.name, tone: projectTone(p) })), [overview]);
  const projectNames = useMemo(() => new Map(projects.map((p) => [p.id, p.name])), [projects]);
  const threadTitles = useMemo(() => new Map(overview.flatMap((p) => p.threads.map((t) => [t.id, t.title ?? 'Thread'] as const))), [overview]);
  const counts = { all: data.nodes.length, used: data.nodes.filter((n) => n.usedBy.length).length, shadowed: data.nodes.filter((n) => n.shadows || n.shadowedIn.length).length };
  const shown = useMemo(
    () => data.nodes.filter((n) => filter === 'all' || (filter === 'used' ? n.usedBy.length > 0 : n.shadows || n.shadowedIn.length > 0)),
    [data.nodes, filter],
  );
  const ref = skill ? parseSkillKey(skill) : null;
  const node = skill ? data.nodes.find((n) => n.key === skill) : undefined;
  const select = (key: string | null) => replaceRoute({ name: 'skills', ...(key ? { skill: key } : {}) });
  const changed = () => {
    setVersion((v) => v + 1);
    void data.refresh();
  };
  const startImport = async () => {
    try {
      const path = await call('app.pickFolder', { purpose: 'skill-import' });
      if (path) setImportPath(path);
    } catch (err) {
      toastError(err);
    }
  };

  return (
    <div className={`skills${ref ? ' with-panel' : ''}`}>
      <div className="skills-main">
        <div className="skills-head">
          <h1 className="title">Skill map</h1>
          <p className="muted">Global skills sit in the middle; project skills live inside their project. Lines show the threads using a skill right now.</p>
          <div className="skills-controls">
            <div className="segmented" role="group" aria-label="View">
              <button type="button" aria-pressed={view === 'map'} onClick={() => setView('map')}>
                Map
              </button>
              <button type="button" aria-pressed={view === 'list'} onClick={() => setView('list')}>
                List
              </button>
            </div>
            <div className="chips" role="group" aria-label="Show">
              {(
                [
                  ['all', 'All'],
                  ['used', 'In use now'],
                  ['shadowed', 'Shadowed'],
                ] as Array<[Filter, string]>
              ).map(([f, label]) => (
                <button key={f} type="button" className="filter-chip" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                  {label} · {counts[f]}
                </button>
              ))}
            </div>
          </div>
        </div>
        {data.status === 'loading' ? (
          <p className="muted skills-body">Loading…</p>
        ) : data.status === 'error' ? (
          <EmptyState title="Couldn't load skills">{data.error}</EmptyState>
        ) : !data.nodes.length ? (
          <div className="skills-body">
            <EmptyState title="No skills yet">Skills are instructions and scripts Desk and its threads reuse. Import one, write one, or ask Desk to build one.</EmptyState>
          </div>
        ) : view === 'map' ? (
          <div className="skills-body map">
            <SkillsMapView nodes={shown} projects={projects} selected={skill ?? null} onSelect={(k) => select(k === skill ? null : k)} />
          </div>
        ) : (
          <div className="skills-body">
            <SkillList nodes={shown} projectNames={projectNames} selected={skill ?? null} onSelect={(k) => select(k === skill ? null : k)} />
          </div>
        )}
        <div className="skills-foot">
          {view === 'map' ? (
            <div className="skills-legend" aria-hidden="true">
              <span>
                <span className="lg-dot global" />
                Global skill
              </span>
              <span>
                <span className="lg-dot project" />
                Project skill
              </span>
              <span>
                <span className="lg-dot shadowed" />
                Shadowed
              </span>
              <span>
                <span className="lg-line run" />
                Used by a running thread
              </span>
              <span>
                <span className="lg-line wait" />
                Waiting
              </span>
              <span>Size = how often it's used</span>
            </div>
          ) : (
            <span />
          )}
          <span className="grow" />
          <Button onClick={() => void startImport()}>Import from ~/.claude/skills</Button>
          <Button onClick={() => setEditor({})}>New skill</Button>
          <Button variant="primary" onClick={() => setAsk({})}>
            + Ask Desk for a new skill
          </Button>
        </div>
      </div>
      {ref ? (
        <SkillPanel
          skill={ref}
          node={node}
          projectNames={projectNames}
          threadTitles={threadTitles}
          version={version + (node?.version ?? 0)}
          onEdit={(detail) => setEditor({ skill: { ref, detail } })}
          onAskDesk={() => setAsk({ name: ref.name, ...(ref.projectId ? { projectId: ref.projectId } : node?.usedBy[0] ? { projectId: node.usedBy[0].projectId } : {}) })}
          onChanged={changed}
          onClose={() => select(null)}
        />
      ) : null}
      {editor ? (
        <SkillEditor
          {...(editor.skill ? { skill: editor.skill } : {})}
          projects={projects}
          onClose={() => setEditor(null)}
          onSaved={(saved) => {
            setEditor(null);
            toast({ tone: 'info', message: `Saved ${saved.name}.` });
            changed();
            select(skillKey(saved));
          }}
        />
      ) : null}
      {ask ? <AskDesk {...(ask.name ? { skillName: ask.name } : {})} projects={projects} {...(ask.projectId ? { defaultProjectId: ask.projectId } : {})} onClose={() => setAsk(null)} /> : null}
      {importPath ? (
        <ImportSheet
          path={importPath}
          projects={projects}
          onClose={() => setImportPath(null)}
          onDone={(r) => {
            setImportPath(null);
            changed();
            select(skillKey(r));
          }}
        />
      ) : null}
    </div>
  );
}
```

`apps/desktop/src/renderer/skills/skills.css`:

```css
.skills {
  height: 100%;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
  padding: 0 16px 0 0;
}
.skills.with-panel {
  grid-template-columns: minmax(0, 1fr) minmax(400px, 460px);
}
.skills-main {
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
}
.skills-head {
  padding: 22px 32px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.skills-head .title {
  font-size: 36px;
}
.skills-head p {
  margin: 0;
  max-width: 420px;
  font-size: 13.5px;
  line-height: 1.45;
}
.skills-controls {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.chips {
  display: flex;
  gap: 6px;
}
.filter-chip {
  height: 26px;
  padding: 0 10px;
  border: 1px solid #cfc9bd;
  border-radius: 13px;
  background: #f7f5f0;
  color: var(--text);
  font-size: 12px;
  cursor: pointer;
}
.filter-chip[aria-pressed='true'] {
  border-color: var(--ink);
  background: var(--ink);
  color: #fff;
}
.skills-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 0 32px;
}
.skills-body.map {
  position: relative;
  padding: 0;
  overflow: hidden;
}
.skills-svg {
  position: absolute;
  left: 0;
  top: 0;
}
.skills-foot {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 12px 24px 18px 32px;
}
.skills-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  align-items: center;
  padding: 9px 14px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.72);
  font-size: 12px;
  color: var(--text);
}
.skills-legend > span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.lg-dot {
  width: 12px;
  height: 12px;
  box-sizing: border-box;
  border-radius: 6px;
}
.lg-dot.global {
  background: var(--ink);
}
.lg-dot.project {
  border: 2px solid var(--run-text);
  background: #fff;
}
.lg-dot.shadowed {
  border: 1.5px dashed var(--muted);
}
.lg-line {
  width: 20px;
  height: 2.5px;
  background: var(--run);
}
.lg-line.wait {
  height: 0;
  border-top: 2px dashed var(--wait);
  background: none;
}
.skills-territory-label {
  position: absolute;
  transform: translateX(-50%);
  font-size: 12.5px;
  font-weight: 600;
  white-space: nowrap;
}
.skills-global-label {
  position: absolute;
  transform: translateX(-50%);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.14em;
  color: #6e6a62;
}
.skills-shadow-label {
  position: absolute;
  transform: translateX(-50%);
  font-family: var(--font-serif);
  font-style: italic;
  font-size: 12px;
  color: var(--wait-text);
}
.skill-node {
  position: absolute;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  padding: 4px;
  border: 0;
  border-radius: 50%;
  background: var(--ink);
  color: #f7f5f0;
  cursor: pointer;
  overflow: hidden;
}
.skill-node.project {
  border: 2px solid var(--run-text);
  background: #fff;
  color: var(--ink);
}
.skill-node.shadowed {
  border: 1.5px dashed var(--muted);
  background: var(--ground);
  color: var(--text);
}
.skill-node.broken {
  border: 2px solid var(--accent);
}
.skill-node.selected {
  box-shadow: 0 0 0 5px var(--ground), 0 0 0 8px var(--accent);
}
.skill-node-name {
  max-width: 100%;
  font-family: var(--font-mono);
  font-size: 11.5px;
  font-weight: 500;
  line-height: 1.2;
  text-align: center;
  overflow-wrap: anywhere;
}
.skill-node-v {
  font-size: 10.5px;
  opacity: 0.7;
}
.skill-marker {
  position: absolute;
  transform: translate(-7px, -7px);
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  text-decoration: none;
  font-size: 12px;
  font-weight: 500;
  color: var(--run-text);
}
.skill-marker-dot {
  width: 14px;
  height: 14px;
  box-sizing: border-box;
  border-radius: 7px;
  background: var(--run);
  box-shadow: 0 0 0 4px rgba(47, 91, 211, 0.2);
}
.skill-marker.status-waiting,
.skill-marker.status-queued {
  color: var(--wait-text);
}
.skill-marker.status-waiting .skill-marker-dot,
.skill-marker.status-queued .skill-marker-dot {
  border: 2.5px solid var(--wait);
  background: #fff;
  box-shadow: none;
}
.skill-list section {
  margin-bottom: 18px;
}
.skill-list ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.skill-list-title {
  margin: 0 0 8px;
  font-family: var(--font-serif);
  font-size: 20px;
  font-weight: 500;
}
.skill-row {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border: 1px solid var(--rule);
  border-radius: 10px;
  background: #fff;
  text-align: left;
  font: inherit;
  cursor: pointer;
}
.skill-row.current {
  border-color: var(--ink);
}
.skill-row-name {
  min-width: 160px;
  font-size: 13px;
}
.skill-panel {
  margin: 16px 0;
  min-height: 0;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.skill-panel-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.skill-panel-head h2 {
  flex: 1;
  margin: 0;
  font-size: 18px;
  font-weight: 500;
}
.skill-scope {
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  white-space: nowrap;
}
.skill-scope.global {
  background: var(--ink);
  color: #fff;
}
.skill-scope.project {
  border: 1.5px solid var(--run-text);
  background: var(--run-pastel);
  color: var(--run-text);
}
.skill-desc {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.5;
  color: var(--text);
}
.skill-panel .tabs {
  padding: 0;
}
.skill-tab {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 120px;
}
.shadow-note {
  margin: 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--wait-pastel);
  font-size: 12.5px;
  line-height: 1.45;
  color: #5c3400;
}
.skill-change {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  border-radius: 10px;
  background: #f4f1ea;
}
.skill-versions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.skill-versions button {
  width: 40px;
  height: 30px;
  border: 1px solid var(--rule);
  border-radius: 6px;
  background: #fff;
  font-size: 12.5px;
  cursor: pointer;
}
.skill-versions button.on {
  border-color: var(--ink);
  background: var(--ink);
  color: #fff;
}
.skill-used {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.skill-used li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.skill-files {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.skill-history {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.skill-history li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--rule-soft);
  border-radius: 8px;
}
.skill-history li .grow {
  display: flex;
  flex-direction: column;
}
.skill-compare {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.skill-compare-bar {
  display: flex;
  gap: 12px;
  font-size: 12.5px;
}
.skill-file-changes {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
}
.skill-actions {
  display: flex;
  gap: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--rule-soft);
}
.skill-editor-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.skill-files-edit {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.skill-files-edit li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: 6px;
  background: #fbfaf7;
  font-size: 12.5px;
}
.skill-files-edit li.removing .mono {
  text-decoration: line-through;
  color: var(--text-min);
}
.skill-files-edit li.adding {
  background: #e6f2eb;
}
.skill-add-files {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 6px;
}
.skill-add-files .input {
  flex: 1;
}
.skill-new-text summary {
  margin-top: 8px;
  cursor: pointer;
  font-size: 12.5px;
}
```

Other changes:
- The `.diff-*` rules move from `threads.css` to a "Diffs" block in `tokens.css`; they are shared with the skill compare view.
- `App.tsx` routes `skills` to `<SkillsScreen skill={route.skill} />`.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(desktop): skills — skill map and list with shadows and live usage, detail (instructions, files, history with change notes, compare, restore), editor with uploads, import, delete, Ask Desk

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: System — deskd controls and repair, endpoint, model registry, usage, notices, notifications, data

**Files:**
- Create: `apps/desktop/src/renderer/system/SystemScreen.test.tsx`, `apps/desktop/src/main/daemon.test.ts`, `apps/desktop/src/renderer/components/EndpointPanel.tsx`, `apps/desktop/src/renderer/system/ModelsEditor.tsx`, `apps/desktop/src/renderer/system/SystemScreen.tsx`, `apps/desktop/src/renderer/system/system.css`
- Modify: `apps/desktop/src/main/daemon.ts`, `apps/desktop/src/shared/ipc.ts`, `apps/desktop/src/main/handlers.ts`, `apps/desktop/src/main/handlers.test.ts`, `apps/desktop/src/main/index.ts`, `apps/desktop/src/renderer/screens/Onboarding.tsx`, `apps/desktop/src/renderer/threads/threads.css`, `apps/desktop/src/renderer/theme/tokens.css`, `apps/desktop/src/renderer/App.tsx`

**Interfaces:**

- Consumes: the `daemon.*`, `config.*`, `models.*`, `usage`, `app.info`, `app.settings`, `app.updateSettings` and `app.revealLogs` channels; the global `system.notices` and `overview`; `tokens` from the thread Usage tab.
- Produces:

```ts
// main/daemon.ts
async repair(): Promise<DaemonStatus>;   // packaged macOS: installAgent() then waitHealthy(previous pid); elsewhere restart()
// shared/ipc.ts
'daemon.repair': none;                   // HandlerContext.daemon gains repair(); index.ts reconnects the broker afterwards
// components/EndpointPanel.tsx (extracted from Onboarding; also in System)
export type EndpointState = ModelEndpointStatus | 'unsupported' | null;
export function EndpointPanel(props: { onStatus?(s: EndpointState): void }): JSX.Element;
// system/ModelsEditor.tsx
export function modelProblems(list: ModelInfo[]): string[];
export function ModelsEditor(): JSX.Element;
// system/SystemScreen.tsx
export function SystemScreen(): JSX.Element;
```

- [ ] **Step 1: Write the failing tests**

`apps/desktop/src/renderer/system/SystemScreen.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectSummary } from '@desk/protocol';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { installBridge } from '../test/bridge';
import { modelProblems } from './ModelsEditor';
import { SystemScreen } from './SystemScreen';

afterEach(cleanup);
beforeEach(() => {
  globalStore.set({
    ...initialGlobalState(),
    connection: { status: 'live' },
    overview: [{ project: { id: 'p1', name: 'Onboarding', goal: '', updated_at: 't' }, desk_status: 'idle', threads: [], latest_report: null, plan_progress: { done: 0, total: 0 }, attention_count: 0 } as unknown as ProjectSummary],
    system: { proxy: 'down', lastSeq: 3, notices: [{ eventId: 3, ts: new Date().toISOString(), projectId: 'p1', level: 'warning', code: 'proxy_down', message: 'The model proxy is unreachable.' }] },
  });
});

const status = { running: true, version: '1.0.0', pid: 42, uptime_s: 3700, proxy: 'down', mode: 'packaged', bundledVersion: '1.0.0', agent: 'installed' };
const model = (id: string) => ({ id, family: 'claude' as const, context_window: 200000, max_output_tokens: 32000, supports_reasoning_effort: true, concurrency: 4 });

function setup(extra: Record<string, (input: any) => unknown> = {}) {
  const bridge = installBridge({
    'daemon.status': () => status,
    'daemon.restart': () => ({ ...status, pid: 43 }),
    'daemon.stop': () => ({ ...status, running: false }),
    'daemon.repair': () => status,
    'config.endpoint': () => ({ configured: true, source: 'keychain', base_url: 'http://127.0.0.1:8317/v1' }),
    'config.get': () => ({ notifications: 'auto' }),
    'config.patch': (p: { notifications: string }) => p,
    'app.settings': () => ({ notifications: true }),
    'app.updateSettings': (p: { notifications: boolean }) => p,
    'app.info': () => ({ version: '1.0.0', platform: 'darwin', packaged: true, dataDir: '/Users/me/Library/Application Support/Desk' }),
    'app.revealLogs': () => undefined,
    'models.list': () => [model('claude-opus-5-5'), model('claude-fable-5-1')],
    'models.replace': ({ models }: { models: unknown[] }) => models,
    usage: () => ({ rows: [{ project_id: 'p1', model: 'claude-opus-5-5', prompt_tokens: 12000, completion_tokens: 3000 }], totals: { prompt_tokens: 12000, completion_tokens: 3000 } }),
    ...extra,
  });
  render(<SystemScreen />);
  return bridge;
}

describe('modelProblems', () => {
  it('explains what the daemon would reject', () => {
    expect(modelProblems([])).toEqual(['Keep at least one model.']);
    expect(modelProblems([model('a'), model('a')])).toEqual(['a is listed twice.']);
    expect(modelProblems([{ ...model(''), concurrency: 0 }])).toEqual(['Every model needs an id.', 'Token limits must be positive and concurrency at least 1.']);
  });
});

describe('SystemScreen', () => {
  it('shows deskd and controls it', async () => {
    const bridge = setup();
    const d = screen.getByRole('region', { name: 'deskd' });
    expect(await within(d).findByText(/v1\.0\.0 · pid 42 · up 1h/)).toBeTruthy();
    expect(d.textContent).toContain('Unreachable. Threads pause');
    fireEvent.click(within(d).getByRole('button', { name: 'Restart' }));
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'daemon.restart')).toBe(true));
    fireEvent.click(await within(d).findByRole('button', { name: 'Repair LaunchAgent' }));
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'daemon.repair')).toBe(true));
    fireEvent.click(await within(d).findByRole('button', { name: 'Stop' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Stop' }).at(-1)!);
    expect(await within(d).findByRole('button', { name: 'Start' })).toBeTruthy();
  });

  it('shows the endpoint without the key, and both notification switches', async () => {
    const bridge = setup();
    const ep = screen.getByRole('region', { name: 'Model endpoint' });
    expect(await within(ep).findByText('http://127.0.0.1:8317/v1')).toBeTruthy();
    fireEvent.click(within(ep).getByRole('button', { name: 'Change key' }));
    expect((within(ep).getByLabelText('API key') as HTMLInputElement).type).toBe('password');
    const n = screen.getByRole('region', { name: 'Notifications' });
    await waitFor(() => expect((within(n).getByLabelText(/From deskd/) as HTMLInputElement).checked).toBe(true));
    fireEvent.click(within(n).getByLabelText(/From the app/));
    fireEvent.click(within(n).getByLabelText(/From deskd/));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'app.updateSettings')?.input).toEqual({ notifications: false }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'config.patch')?.input).toEqual({ notifications: 'off' }));
  });

  it('edits the model registry', async () => {
    const bridge = setup();
    const reg = screen.getByRole('region', { name: 'Model registry' });
    fireEvent.click(await within(reg).findByRole('button', { name: 'Add model' }));
    fireEvent.change(within(reg).getByLabelText('Model 3 id'), { target: { value: 'claude-opus-5-5' } });
    expect(within(reg).getByRole('alert').textContent).toContain('claude-opus-5-5 is listed twice.');
    expect((within(reg).getByRole('button', { name: 'Save registry' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(reg).getByLabelText('Model 3 id'), { target: { value: 'gpt-6-sol' } });
    fireEvent.change(within(reg).getByLabelText('Model 3 family'), { target: { value: 'gpt' } });
    fireEvent.click(within(reg).getByRole('button', { name: 'Remove model 2' }));
    fireEvent.click(within(reg).getByRole('button', { name: 'Save registry' }));
    await waitFor(() => expect((bridge.calls.find((c) => c.channel === 'models.replace')?.input as { models: Array<{ id: string }> }).models.map((m) => m.id)).toEqual(['claude-opus-5-5', 'gpt-6-sol']));
  });

  it('shows usage by model and project, notices, and the data directory', async () => {
    const bridge = setup();
    const u = screen.getByRole('region', { name: 'Usage' });
    expect(await within(u).findByRole('link', { name: 'Onboarding' })).toBeTruthy();
    expect(within(u).getByText('claude-opus-5-5')).toBeTruthy();
    expect((bridge.calls.find((c) => c.channel === 'usage')?.input as { since?: string }).since).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    fireEvent.click(within(u).getByRole('button', { name: 'All time' }));
    await waitFor(() => expect(bridge.calls.filter((c) => c.channel === 'usage').at(-1)?.input).toEqual({}));
    expect(screen.getByRole('region', { name: 'System notices' }).textContent).toContain('The model proxy is unreachable.');
    const data = screen.getByRole('region', { name: 'Data' });
    expect(await within(data).findByText('/Users/me/Library/Application Support/Desk')).toBeTruthy();
    fireEvent.click(within(data).getByRole('button', { name: 'Reveal logs' }));
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'app.revealLogs')).toBe(true));
  });
});
```

`apps/desktop/src/main/daemon.test.ts`:

```ts
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { compareVersions, DaemonManager, type DaemonManagerOptions } from './daemon';
import { launchdPlist, plistPath } from './launchd';

let dir: string;
beforeEach(() => void (dir = mkdtempSync(join(tmpdir(), 'desk-dm-'))));
afterEach(() => rmSync(dir, { recursive: true, force: true }));

function manager(o: Partial<DaemonManagerOptions> = {}) {
  const calls: string[][] = [];
  const dataDir = join(dir, 'data');
  mkdirSync(dataDir, { recursive: true });
  const up = (pid = 42) => writeFileSync(join(dataDir, 'daemon.json'), JSON.stringify({ port: 1234, token: 't', pid, version: '1.0.0' }));
  const down = () => rmSync(join(dataDir, 'daemon.json'), { force: true });
  let version = '1.0.0';
  const m = new DaemonManager({
    dataDir,
    mode: 'dev',
    platform: 'darwin',
    home: join(dir, 'home'),
    uid: 501,
    bundledVersion: '1.0.0',
    execPath: '/Applications/Desk.app/Contents/MacOS/Desk',
    bundlePath: '/Applications/Desk.app/Contents/Resources/deskd/deskd.mjs',
    repoRoot: '/repo',
    nodePath: 'node',
    exec: async (file, args) => {
      calls.push([file, ...args]);
      if (args[0] === 'bootstrap' || args[0] === 'kickstart') {
        up(args[0] === 'kickstart' ? 43 : 42);
        version = '1.0.0';
      }
      if (args[0] === 'bootout') down();
      return { code: 0, stdout: '', stderr: '' };
    },
    spawnDetached: (file, args) => {
      calls.push(['spawn', file, ...args]);
      up();
    },
    kill: (pid) => {
      calls.push(['kill', String(pid)]);
      down();
    },
    fetchHealth: async () => ({ version, protocol_version: 1, proxy: 'up', uptime_s: 5 }),
    sleep: async () => {},
    startTimeoutMs: 200,
    ...o,
  });
  return { m, calls, up, down, dataDir, setVersion: (v: string) => void (version = v) };
}

describe('launchd plist', () => {
  it('runs the bundle with Electron as Node and escapes paths', () => {
    const xml = launchdPlist({ programArguments: ['/A & B/Desk', 'x.mjs'], env: { ELECTRON_RUN_AS_NODE: '1' }, workingDirectory: '/w', logFile: '/l/deskd.log' });
    expect(xml).toContain('<string>dev.desk.deskd</string>');
    expect(xml).toContain('<string>/A &amp; B/Desk</string>');
    expect(xml).toContain('<key>ELECTRON_RUN_AS_NODE</key>');
    expect(xml).toContain('<key>KeepAlive</key>');
  });
});

describe('DaemonManager', () => {
  it('reports status from daemon.json and health', async () => {
    const { m, up } = manager();
    expect(await m.status()).toMatchObject({ running: false, agent: 'unsupported', mode: 'dev' });
    up();
    expect(await m.status()).toMatchObject({ running: true, version: '1.0.0', pid: 42, proxy: 'up' });
  });

  it('starts the repo daemon through tsx in dev', async () => {
    const { m, calls } = manager();
    expect((await m.start()).running).toBe(true);
    expect(calls[0]).toEqual(['spawn', 'node', '--import', '/repo/node_modules/tsx/dist/loader.mjs', '/repo/apps/daemon/src/main.ts', '--data-dir', join(dir, 'data')]);
  });

  it('installs and bootstraps the LaunchAgent when packaged on macOS', async () => {
    const { m, calls } = manager({ mode: 'packaged' });
    expect((await m.start()).agent).toBe('installed');
    const file = plistPath(join(dir, 'home'));
    expect(readFileSync(file, 'utf8')).toContain('<string>/Applications/Desk.app/Contents/Resources/deskd/deskd.mjs</string>');
    expect(calls).toEqual([['launchctl', 'bootstrap', 'gui/501', file]]);
  });

  it('refreshes an older daemon under an installed agent, and leaves others alone', async () => {
    const { m, calls, up, setVersion } = manager({ mode: 'packaged' });
    up();
    setVersion('0.9.0');
    expect(await m.ensureCurrent()).toBe(false); // no agent installed: a daemon started by hand is left alone
    await m.installAgent(); // bootstrap brings up the bundled 1.0.0
    setVersion('0.9.0');
    calls.length = 0;
    expect(await m.ensureCurrent()).toBe(true);
    expect(calls.map((c) => c[1])).toEqual(['bootout', 'bootstrap']);
    calls.length = 0;
    expect(await m.ensureCurrent()).toBe(false);
    expect(calls).toEqual([]);
    expect(await manager().m.ensureCurrent()).toBe(false);
  });

  it('restarts with kickstart and stops with bootout', async () => {
    const { m, calls } = manager({ mode: 'packaged' });
    await m.start();
    calls.length = 0;
    expect((await m.restart()).pid).toBe(43);
    expect(calls[0]).toEqual(['launchctl', 'kickstart', '-k', 'gui/501/dev.desk.deskd']);
    expect((await m.stop()).running).toBe(false);
    expect(calls.at(-1)).toEqual(['launchctl', 'bootout', 'gui/501/dev.desk.deskd']);
  });

  it('repairs by reinstalling the LaunchAgent, or restarts in dev', async () => {
    const { m, calls, up } = manager({ mode: 'packaged' });
    await m.start();
    up(7); // the agent's daemon has since been replaced by another process; repair must bring up a fresh one
    calls.length = 0;
    const s = await m.repair();
    expect(s).toMatchObject({ running: true, agent: 'installed' });
    expect(calls.map((c) => c[1])).toEqual(['bootout', 'bootstrap']);
    const dev = manager();
    dev.up();
    expect((await dev.m.repair()).running).toBe(true);
    expect(dev.calls.map((c) => c[0])).toEqual(['kill', 'spawn']);
  });

  it('stops a dev daemon by pid and times out when a start never comes up', async () => {
    const { m, calls, up } = manager();
    up();
    expect((await m.stop()).running).toBe(false);
    expect(calls).toEqual([['kill', '42']]);
    const stuck = manager({ spawnDetached: () => {} }).m;
    await expect(stuck.start()).rejects.toMatchObject({ code: 'daemon_start_timeout' });
  });

  it('refuses to start automatically where there is no LaunchAgent', async () => {
    const { m } = manager({ mode: 'packaged', platform: 'win32' });
    await expect(m.start()).rejects.toMatchObject({ code: 'unsupported' });
    expect(existsSync(plistPath(join(dir, 'home')))).toBe(false);
  });

  it('compares versions numerically', () => {
    expect(compareVersions('0.9.0', '1.0.0')).toBeLessThan(0);
    expect(compareVersions('1.10.0', '1.9.3')).toBeGreaterThan(0);
    expect(compareVersions('1.0', '1.0.0')).toBe(0);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm vitest run apps/desktop/src/renderer/system apps/desktop/src/main`
Expected: FAIL, because the modules don't exist yet.

- [ ] **Step 3: Implement**

`apps/desktop/src/renderer/components/EndpointPanel.tsx`:

```tsx
import { useEffect, useState, type FormEvent } from 'react';
import type { ModelEndpointStatus, ModelEndpointTestResult } from '@desk/protocol';
import { call, DeskCallError } from '../bridge';
import { Button } from './Button';
import { Field } from './Field';
import { describeError } from './Toast';

export type EndpointState = ModelEndpointStatus | 'unsupported' | null;

const SOURCE_LABEL: Record<NonNullable<ModelEndpointStatus['source']>, string> = {
  env: 'the DESK_OPENAI_* environment variables',
  file: '~/.config/cliproxyapi.env',
  keychain: 'your Keychain',
};

function TestResult({ result }: { result: ModelEndpointTestResult | null }) {
  if (!result) return null;
  return result.ok ? (
    <p className="status-line">
      <span className="dot ok" aria-hidden="true" />
      {`Connected. ${result.models?.length ?? 0} model${result.models?.length === 1 ? '' : 's'} available.`}
    </p>
  ) : (
    <p className="field-error" role="alert">
      Couldn’t connect: {result.error ?? 'unknown error'}
    </p>
  );
}

/**
 * The model endpoint: where it comes from, a connection test, and a form that writes a new base URL and key
 * (the key goes to the Keychain and is never shown). Used by onboarding and System.
 */
export function EndpointPanel({ onStatus }: { onStatus?(s: EndpointState): void }) {
  const [status, setStatusState] = useState<EndpointState>(null);
  const [editing, setEditing] = useState(false);
  const [baseUrl, setBaseUrl] = useState('http://127.0.0.1:8317/v1');
  const [apiKey, setApiKey] = useState('');
  const [result, setResult] = useState<ModelEndpointTestResult | null>(null);
  const [pending, setPending] = useState<'test' | 'save' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const setStatus = (s: EndpointState) => {
    setStatusState(s);
    onStatus?.(s);
  };

  useEffect(() => {
    call('config.endpoint', {})
      .then((s) => {
        setStatus(s);
        if (s.base_url) setBaseUrl(s.base_url);
      })
      .catch((err) => {
        if (err instanceof DeskCallError && (err.code === 'unsupported' || err.status === 501)) setStatus('unsupported');
        else setError(describeError(err).message);
      });
    // Loaded once; onStatus is a notification, not an input.
  }, []);

  const test = async () => {
    setPending('test');
    setError(null);
    try {
      setResult(await call('config.testEndpoint', {}));
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setPending(null);
    }
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setPending('save');
    setError(null);
    try {
      const saved = await call('config.saveEndpoint', { base_url: baseUrl.trim(), api_key: apiKey });
      setApiKey('');
      setStatus(saved);
      setEditing(false);
      setResult(await call('config.testEndpoint', {}));
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setPending(null);
    }
  };

  const configured = status !== null && status !== 'unsupported' && status.configured;
  return (
    <div className="endpoint">
      {status === 'unsupported' ? <p className="status-line">This deskd manages its model endpoint itself.</p> : null}
      {configured && !editing ? (
        <>
          <p className="status-line">
            <span className="dot ok" aria-hidden="true" />
            <span>
              Using <span className="mono">{status.base_url}</span> from {status.source ? SOURCE_LABEL[status.source] : 'the daemon'}.
            </span>
          </p>
          <div className="actions">
            <Button size="sm" pending={pending === 'test'} onClick={() => void test()}>
              Test connection
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
              {status.source === 'keychain' ? 'Change key' : 'Use a different endpoint'}
            </Button>
          </div>
        </>
      ) : null}
      {status !== null && status !== 'unsupported' && (!configured || editing) ? (
        <form className="sheet-body" onSubmit={save} noValidate>
          {configured && status.source !== 'keychain' ? (
            <p className="field-hint">Saving stores this endpoint in your Keychain. Environment variables, if set, still take precedence when deskd starts.</p>
          ) : null}
          <Field id="endpoint-url" label="Base URL">
            <input id="endpoint-url" className="input mono" type="url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
          </Field>
          <Field id="endpoint-key" label="API key" hint="Stored in the Keychain; Desk never displays it.">
            <input id="endpoint-key" className="input mono" type="password" autoComplete="off" spellCheck={false} value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
          </Field>
          <div className="actions">
            <Button type="submit" variant="primary" size="sm" pending={pending === 'save'} disabled={!apiKey || !baseUrl}>
              Save and test
            </Button>
            {editing ? (
              <Button size="sm" variant="ghost" onClick={() => (setEditing(false), setApiKey(''))}>
                Cancel
              </Button>
            ) : null}
          </div>
        </form>
      ) : null}
      <TestResult result={result} />
      {error ? (
        <p className="field-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
```

`apps/desktop/src/renderer/system/ModelsEditor.tsx`:

```tsx
import { useEffect, useState } from 'react';
import type { ModelInfo } from '@desk/protocol';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { describeError, toast, toastError } from '../components/Toast';

const blank = (): ModelInfo => ({ id: '', family: 'claude', context_window: 200_000, max_output_tokens: 32_000, supports_reasoning_effort: false, concurrency: 4 });

/** Problems that would make PUT /models fail, in words. */
export function modelProblems(list: ModelInfo[]): string[] {
  const out: string[] = [];
  if (!list.length) out.push('Keep at least one model.');
  const ids = list.map((m) => m.id.trim());
  if (ids.some((id) => !id)) out.push('Every model needs an id.');
  const dup = ids.find((id, i) => id && ids.indexOf(id) !== i);
  if (dup) out.push(`${dup} is listed twice.`);
  if (list.some((m) => !(m.context_window > 0) || !(m.max_output_tokens > 0) || !(m.concurrency >= 1))) out.push('Token limits must be positive and concurrency at least 1.');
  return out;
}

/** The model registry (PUT /models): the models Desk can pick, their limits and how many calls may run at once. */
export function ModelsEditor() {
  const [saved, setSaved] = useState<ModelInfo[] | null>(null);
  const [draft, setDraft] = useState<ModelInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    call('models.list', {})
      .then((m) => (setSaved(m), setDraft(m)))
      .catch((err) => setError(describeError(err).message));
  }, []);
  if (error) return <p className="field-error">{error}</p>;
  if (!saved) return <p className="muted">Loading…</p>;
  const set = (i: number, patch: Partial<ModelInfo>) => setDraft((d) => d.map((m, k) => (k === i ? { ...m, ...patch } : m)));
  const problems = modelProblems(draft);
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const save = async () => {
    setPending(true);
    try {
      const next = await call('models.replace', { models: draft.map((m) => ({ ...m, id: m.id.trim() })) });
      setSaved(next);
      setDraft(next);
      toast({ tone: 'info', message: 'Model registry saved.' });
    } catch (err) {
      toastError(err);
    } finally {
      setPending(false);
    }
  };
  const num = (v: string) => Math.max(0, Math.floor(Number(v) || 0));
  return (
    <div className="models-editor">
      <table className="models-table">
        <thead>
          <tr>
            <th scope="col">Model id</th>
            <th scope="col">Family</th>
            <th scope="col">Context</th>
            <th scope="col">Max output</th>
            <th scope="col">Reasoning effort</th>
            <th scope="col">At once</th>
            <th scope="col">
              <span className="sr-only">Remove</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {draft.map((m, i) => (
            <tr key={i}>
              <td>
                <input className="input mono" aria-label={`Model ${i + 1} id`} value={m.id} onChange={(e) => set(i, { id: e.target.value })} />
              </td>
              <td>
                <select className="select" aria-label={`Model ${i + 1} family`} value={m.family} onChange={(e) => set(i, { family: e.target.value as ModelInfo['family'] })}>
                  <option value="claude">claude</option>
                  <option value="gpt">gpt</option>
                </select>
              </td>
              <td>
                <input className="input" type="number" aria-label={`Model ${i + 1} context window`} value={m.context_window} onChange={(e) => set(i, { context_window: num(e.target.value) })} />
              </td>
              <td>
                <input className="input" type="number" aria-label={`Model ${i + 1} max output tokens`} value={m.max_output_tokens} onChange={(e) => set(i, { max_output_tokens: num(e.target.value) })} />
              </td>
              <td>
                <input type="checkbox" aria-label={`Model ${i + 1} supports reasoning effort`} checked={m.supports_reasoning_effort} onChange={(e) => set(i, { supports_reasoning_effort: e.target.checked })} />
              </td>
              <td>
                <input className="input narrow" type="number" aria-label={`Model ${i + 1} concurrency`} value={m.concurrency} onChange={(e) => set(i, { concurrency: num(e.target.value) })} />
              </td>
              <td>
                <button type="button" className="icon-btn" aria-label={`Remove model ${i + 1}`} onClick={() => setDraft((d) => d.filter((_, k) => k !== i))}>
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {problems.length ? (
        <ul className="field-error" role="alert">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      ) : null}
      <div className="actions">
        <Button size="sm" onClick={() => setDraft((d) => [...d, blank()])}>
          Add model
        </Button>
        <Button size="sm" variant="primary" pending={pending} disabled={!dirty || problems.length > 0} onClick={() => void save()}>
          Save registry
        </Button>
        {dirty ? (
          <Button size="sm" variant="ghost" onClick={() => setDraft(saved)}>
            Discard changes
          </Button>
        ) : null}
      </div>
    </div>
  );
}
```

`apps/desktop/src/renderer/system/SystemScreen.tsx`:

```tsx
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { UsageResponse } from '@desk/protocol';
import type { ChannelOutput } from '../../main/handlers';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EndpointPanel } from '../components/EndpointPanel';
import { describeError, toastError } from '../components/Toast';
import { clock, duration } from '../format';
import { href } from '../router';
import { useGlobal } from '../state/global';
import { tokens } from '../threads/tabs/UsageTab';
import { ModelsEditor } from './ModelsEditor';
import './system.css';

type DaemonStatusView = ChannelOutput<'daemon.status'>;
type AppInfo = ChannelOutput<'app.info'>;

type Period = 'all' | '30d' | '7d';
const PERIOD_DAYS: Record<Exclude<Period, 'all'>, number> = { '30d': 30, '7d': 7 };

function DaemonSection() {
  const [s, setS] = useState<DaemonStatusView | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmStop, setConfirmStop] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    call('daemon.status', {})
      .then((v) => (setS(v), setError(null)))
      .catch((err) => setError(describeError(err).message));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 10_000);
    return () => clearInterval(t);
  }, [load]);
  const act = async (what: 'start' | 'restart' | 'stop' | 'repair') => {
    setConfirmStop(false);
    setBusy(what);
    try {
      setS(await call(`daemon.${what}`, {}));
    } catch (err) {
      toastError(err);
      load();
    } finally {
      setBusy(null);
    }
  };
  return (
    <section className="card sys-section" aria-labelledby="sys-daemon">
      <h2 id="sys-daemon">deskd</h2>
      {error ? <p className="field-error">{error}</p> : null}
      {s ? (
        <>
          <p className="status-line">
            <span className={`dot ${s.running ? (s.proxy === 'down' ? 'warn' : 'ok') : 'bad'}`} aria-hidden="true" />
            <strong>{s.running ? 'Running' : 'Not running'}</strong>
            {s.running ? (
              <span className="muted">
                · v{s.version} · pid {s.pid}
                {s.uptime_s !== null ? ` · up ${duration(s.uptime_s * 1000)}` : ''}
              </span>
            ) : null}
          </p>
          <dl className="sys-facts">
            <div>
              <dt>Model proxy</dt>
              <dd>{s.proxy === 'up' ? 'Reachable' : s.proxy === 'down' ? 'Unreachable. Threads pause and resume when it is back.' : 'Unknown'}</dd>
            </div>
            <div>
              <dt>Mode</dt>
              <dd>{s.mode === 'packaged' ? `Bundled deskd ${s.bundledVersion}` : 'Development (runs from this repository)'}</dd>
            </div>
            <div>
              <dt>Starts at login</dt>
              <dd>{s.agent === 'installed' ? 'Yes, as a LaunchAgent' : s.agent === 'missing' ? 'No. Install the LaunchAgent to keep Desk running.' : 'Not on this platform'}</dd>
            </div>
          </dl>
          {s.running && s.version && s.mode === 'packaged' && s.version !== s.bundledVersion ? (
            <p className="field-hint">This deskd is v{s.version}; the app bundles v{s.bundledVersion}. Repair installs the bundled one.</p>
          ) : null}
          <div className="actions">
            {s.running ? (
              <>
                <Button size="sm" pending={busy === 'restart'} disabled={busy !== null} onClick={() => void act('restart')}>
                  Restart
                </Button>
                <Button size="sm" variant="ghost" pending={busy === 'stop'} disabled={busy !== null} onClick={() => setConfirmStop(true)}>
                  Stop
                </Button>
              </>
            ) : (
              <Button size="sm" variant="primary" pending={busy === 'start'} disabled={busy !== null} onClick={() => void act('start')}>
                Start
              </Button>
            )}
            {s.agent !== 'unsupported' ? (
              <Button size="sm" variant="ghost" pending={busy === 'repair'} disabled={busy !== null} onClick={() => void act('repair')}>
                {s.agent === 'installed' ? 'Repair LaunchAgent' : 'Install LaunchAgent'}
              </Button>
            ) : null}
          </div>
        </>
      ) : error ? null : (
        <p className="muted">Checking…</p>
      )}
      {confirmStop ? (
        <ConfirmDialog title="Stop deskd?" confirmLabel="Stop" danger onCancel={() => setConfirmStop(false)} onConfirm={() => void act('stop')}>
          Running threads pause. They resume where they were when deskd starts again.
        </ConfirmDialog>
      ) : null}
    </section>
  );
}

function UsageSection() {
  const overview = useGlobal((g) => g.overview);
  const [period, setPeriod] = useState<Period>('30d');
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const since = period === 'all' ? undefined : new Date(Date.now() - PERIOD_DAYS[period] * 86_400_000).toISOString().slice(0, 10);
    call('usage', since ? { since } : {})
      .then((u) => live && (setUsage(u), setError(null)))
      .catch((err) => live && setError(describeError(err).message));
    return () => {
      live = false;
    };
  }, [period]);
  const names = useMemo(() => new Map(overview.map((p) => [p.project.id, p.project.name])), [overview]);
  const sum = (key: 'model' | 'project_id') => {
    const by = new Map<string, { prompt: number; completion: number }>();
    for (const r of usage?.rows ?? []) {
      const k = r[key];
      const v = by.get(k) ?? { prompt: 0, completion: 0 };
      v.prompt += r.prompt_tokens;
      v.completion += r.completion_tokens;
      by.set(k, v);
    }
    return [...by].sort((a, b) => b[1].prompt + b[1].completion - (a[1].prompt + a[1].completion));
  };
  const table = (title: string, rows: Array<[string, { prompt: number; completion: number }]>, label: (k: string) => React.ReactNode) => (
    <table className="usage-table">
      <caption>{title}</caption>
      <thead>
        <tr>
          <th scope="col">{title === 'By model' ? 'Model' : 'Project'}</th>
          <th scope="col">Prompt</th>
          <th scope="col">Completion</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td>{label(k)}</td>
            <td title={v.prompt.toLocaleString()}>{tokens(v.prompt)}</td>
            <td title={v.completion.toLocaleString()}>{tokens(v.completion)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
  return (
    <section className="card sys-section" aria-labelledby="sys-usage">
      <div className="sys-head">
        <h2 id="sys-usage">Usage</h2>
        <div className="segmented" role="group" aria-label="Period">
          {(['7d', '30d', 'all'] as Period[]).map((p) => (
            <button key={p} type="button" aria-pressed={period === p} onClick={() => setPeriod(p)}>
              {p === 'all' ? 'All time' : p === '7d' ? '7 days' : '30 days'}
            </button>
          ))}
        </div>
      </div>
      {error ? <p className="field-error">{error}</p> : null}
      {usage ? (
        usage.rows.length ? (
          <>
            <p className="small">
              {tokens(usage.totals.prompt_tokens)} prompt and {tokens(usage.totals.completion_tokens)} completion tokens.
            </p>
            <div className="usage-grid">
              {table('By model', sum('model'), (k) => <span className="mono">{k}</span>)}
              {table('By project', sum('project_id'), (k) => (names.has(k) ? <a href={href({ name: 'project', id: k, tab: 'conversation' })}>{names.get(k)}</a> : <span className="muted">{k === '_global' ? 'Outside projects' : 'Archived project'}</span>))}
            </div>
          </>
        ) : (
          <p className="muted">No model calls in this period.</p>
        )
      ) : error ? null : (
        <p className="muted">Loading…</p>
      )}
    </section>
  );
}

function NoticesSection() {
  const notices = useGlobal((g) => g.system.notices);
  const overview = useGlobal((g) => g.overview);
  const names = useMemo(() => new Map(overview.map((p) => [p.project.id, p.project.name])), [overview]);
  return (
    <section className="card sys-section" aria-labelledby="sys-notices">
      <h2 id="sys-notices">System notices</h2>
      {notices.length ? (
        <ol className="notices" reversed>
          {notices
            .slice()
            .reverse()
            .slice(0, 50)
            .map((n) => (
              <li key={n.eventId} className={`notice notice-${n.level}`}>
                <span className="mono small muted">{clock(n.ts)}</span>
                <span className="grow">{n.message}</span>
                <span className="small muted">{names.get(n.projectId) ?? ''}</span>
              </li>
            ))}
        </ol>
      ) : (
        <p className="muted">Nothing to report since the app started. Proxy outages, restarts and recoveries show up here.</p>
      )}
    </section>
  );
}

function NotificationsSection() {
  const [appOn, setAppOn] = useState<boolean | null>(null);
  const [daemon, setDaemon] = useState<'auto' | 'off' | null>(null);
  useEffect(() => {
    call('app.settings', {})
      .then((s) => setAppOn(s.notifications))
      .catch(() => setAppOn(null));
    call('config.get', {})
      .then((c) => setDaemon(c.notifications))
      .catch(() => setDaemon(null));
  }, []);
  const setApp = async (v: boolean) => {
    try {
      setAppOn((await call('app.updateSettings', { notifications: v })).notifications);
    } catch (err) {
      toastError(err);
    }
  };
  const setD = async (v: 'auto' | 'off') => {
    try {
      setDaemon((await call('config.patch', { notifications: v })).notifications);
    } catch (err) {
      toastError(err);
    }
  };
  return (
    <section className="card sys-section" aria-labelledby="sys-notify">
      <h2 id="sys-notify">Notifications</h2>
      <label className="toggle">
        <input type="checkbox" checked={appOn ?? false} disabled={appOn === null} onChange={(e) => void setApp(e.target.checked)} />
        <span>
          <strong>From the app</strong>
          <span className="muted small">Approvals, questions, hand-offs and stuck threads, while Desk is open or in the menu bar. Silent while a Desk window is focused.</span>
        </span>
      </label>
      <label className="toggle">
        <input type="checkbox" checked={daemon === 'auto'} disabled={daemon === null} onChange={(e) => void setD(e.target.checked ? 'auto' : 'off')} />
        <span>
          <strong>From deskd when the app is closed</strong>
          <span className="muted small">deskd stays quiet while the app is running, so you never get both.</span>
        </span>
      </label>
    </section>
  );
}

function AboutSection() {
  const [info, setInfo] = useState<AppInfo | null>(null);
  useEffect(() => {
    call('app.info', {})
      .then(setInfo)
      .catch(() => setInfo(null));
  }, []);
  return (
    <section className="card sys-section" aria-labelledby="sys-about">
      <h2 id="sys-about">Data</h2>
      <dl className="sys-facts">
        <div>
          <dt>Data directory</dt>
          <dd className="mono">{info?.dataDir ?? '…'}</dd>
        </div>
        <div>
          <dt>App</dt>
          <dd>
            Desk {info?.version ?? ''} {info && !info.packaged ? '(development)' : ''}
          </dd>
        </div>
      </dl>
      <div className="actions">
        <Button size="sm" onClick={() => void call('app.revealLogs', {}).catch(toastError)}>
          Reveal logs
        </Button>
      </div>
    </section>
  );
}

/** The machine room: deskd, the model endpoint and registry, usage, notices, notifications and data. */
export function SystemScreen() {
  return (
    <div className="page system">
      <h1 className="title">System</h1>
      <div className="sys-grid">
        <DaemonSection />
        <section className="card sys-section" aria-labelledby="sys-endpoint">
          <h2 id="sys-endpoint">Model endpoint</h2>
          <EndpointPanel />
        </section>
        <NotificationsSection />
        <AboutSection />
      </div>
      <section className="card sys-section" aria-labelledby="sys-models">
        <h2 id="sys-models">Model registry</h2>
        <p className="field-hint">The models projects can choose. Concurrency caps how many calls to a model run at once across all projects.</p>
        <ModelsEditor />
      </section>
      <UsageSection />
      <NoticesSection />
    </div>
  );
}
```

`apps/desktop/src/renderer/system/system.css`:

```css
.system {
  height: 100%;
  overflow-y: auto;
  box-sizing: border-box;
  max-width: 1180px;
}
.sys-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 16px;
}
.sys-section {
  margin: 0;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.sys-section h2 {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 22px;
  font-weight: 500;
}
.sys-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.sys-head h2 {
  flex: 1;
}
.sys-facts {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.sys-facts div {
  display: flex;
  gap: 12px;
}
.sys-facts dt {
  width: 120px;
  flex-shrink: 0;
  font-size: 12.5px;
  color: var(--text-min);
}
.sys-facts dd {
  margin: 0;
  font-size: 13px;
  overflow-wrap: anywhere;
}
.toggle {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  cursor: pointer;
}
.toggle > span {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 13px;
}
.models-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.models-table th {
  padding: 4px 6px;
  text-align: left;
  font-weight: 600;
  color: var(--text-min);
}
.models-table td {
  padding: 4px 6px;
}
.models-table .input {
  width: 100%;
  box-sizing: border-box;
}
.usage-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 16px;
}
.usage-table caption {
  text-align: left;
  font-size: 13px;
  font-weight: 600;
  padding-bottom: 6px;
}
.notices {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.notice {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 6px 10px;
  border-radius: 8px;
  background: #f4f1ea;
  font-size: 13px;
}
.notice-warning {
  background: var(--wait-pastel);
}
.notice-error {
  background: var(--accent-tint);
}
```

Other changes:
- `daemon.test.ts` gains "repairs by reinstalling the LaunchAgent, or restarts in dev". It sets pid 7 before repair so the fake bootstrap's pid 42 counts as a fresh daemon.
- `Onboarding.tsx`: `EndpointStep` becomes a heading plus `<EndpointPanel onStatus>` plus Continue/Skip. `SOURCE_LABEL` and `TestResult` move into `EndpointPanel`. A non-Keychain endpoint can now be replaced ("Use a different endpoint"), with a note that environment variables still take precedence.
- `.usage-table` moves from `threads.css` to `tokens.css`.
- `App.tsx` routes `system` to `<SystemScreen />`. `screens/Pending.tsx` is deleted, since every route now has its screen.

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run apps/desktop && pnpm typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A apps/desktop
git commit -m "feat(desktop): system — deskd start/restart/stop/repair, proxy, endpoint panel (shared with onboarding), model registry editor, usage by model and project, notices, notification switches, data dir and logs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

