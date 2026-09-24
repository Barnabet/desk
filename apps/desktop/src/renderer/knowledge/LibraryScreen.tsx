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
