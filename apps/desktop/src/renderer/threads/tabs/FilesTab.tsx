import { useEffect, useState } from 'react';
import type { WorkspaceEntry } from '@desk/protocol';
import { call, DeskCallError } from '../../bridge';
import { EmptyState } from '../../components/EmptyState';
import { FileViewer } from '../../components/FileViewer';
import { describeError, toastError } from '../../components/Toast';
import { bytes } from '../../format';

type Listing = { status: 'loading' } | { status: 'ready'; entries: WorkspaceEntry[] } | { status: 'gone' } | { status: 'error'; message: string };
type Open = { path: string; data: Uint8Array } | null;

/** Browses the thread's workspace and shows one file at a time. */
export function FilesTab({ threadId, dir, onDir, version }: { threadId: string; dir: string; onDir(path: string): void; version: string }) {
  const [list, setList] = useState<Listing>({ status: 'loading' });
  const [open, setOpen] = useState<Open>(null);
  const [loadingFile, setLoadingFile] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setList({ status: 'loading' });
    call('threads.files', { id: threadId, ...(dir ? { path: dir } : {}) })
      .then((entries) => live && setList({ status: 'ready', entries: [...entries].sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'dir' ? -1 : 1)) }))
      .catch((err) => {
        if (!live) return;
        if (err instanceof DeskCallError && err.status === 409) setList({ status: 'gone' });
        else setList({ status: 'error', message: describeError(err).message });
      });
    return () => {
      live = false;
    };
  }, [threadId, dir, version]);

  const openFile = async (path: string) => {
    setLoadingFile(path);
    try {
      const data = await call('threads.file', { id: threadId, path });
      setOpen({ path, data });
    } catch (err) {
      toastError(err);
    } finally {
      setLoadingFile(null);
    }
  };
  if (list.status === 'gone') return <EmptyState title="The workspace is gone">It was removed when this thread was archived. Published files are still in the Library.</EmptyState>;
  if (list.status === 'error') return <EmptyState title="Couldn't list the workspace">{list.message}</EmptyState>;
  const crumbs = dir ? dir.split('/') : [];
  return (
    <div className="tab-body files-tab">
      <div className="files-browser">
        <nav className="crumbs" aria-label="Folder">
          <button type="button" className="link" onClick={() => onDir('')}>
            workspace
          </button>
          {crumbs.map((c, i) => (
            <span key={i}>
              {' / '}
              <button type="button" className="link" onClick={() => onDir(crumbs.slice(0, i + 1).join('/'))}>
                {c}
              </button>
            </span>
          ))}
        </nav>
        {list.status === 'loading' ? (
          <p className="muted">Loading…</p>
        ) : list.entries.length ? (
          <ul className="files-list">
            {list.entries.map((e) => (
              <li key={e.path}>
                <button
                  type="button"
                  className={`files-entry${open?.path === e.path ? ' current' : ''}`}
                  aria-busy={loadingFile === e.path || undefined}
                  onClick={() => (e.type === 'dir' ? onDir(e.path) : void openFile(e.path))}
                >
                  <span aria-hidden="true">{e.type === 'dir' ? '▸' : '·'}</span>
                  <span className="grow mono">{e.name}</span>
                  <span className="muted small">{e.type === 'dir' ? 'folder' : bytes(e.size)}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">This folder is empty.</p>
        )}
      </div>
      <div className="files-pane">
        {open ? (
          <FileViewer path={open.path} data={open.data} />
        ) : (
          <p className="muted">Pick a file to view it.</p>
        )}
      </div>
    </div>
  );
}
