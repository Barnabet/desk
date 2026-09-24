import { useEffect, useState } from 'react';
import type { ThreadDiff } from '@desk/protocol';
import { call, DeskCallError } from '../../bridge';
import { EmptyState } from '../../components/EmptyState';
import { describeError } from '../../components/Toast';

type State = { status: 'loading' } | { status: 'ready'; diff: ThreadDiff } | { status: 'none'; message: string } | { status: 'error'; message: string };

const FILE_STATUS: Record<string, string> = { added: 'A', modified: 'M', deleted: 'D', renamed: 'R', copied: 'C' };

/** The thread's branch against its base, with a colored patch. Scratch threads have no diff (409). */
export function DiffTab({ threadId, version }: { threadId: string; version: string }) {
  const [s, setS] = useState<State>({ status: 'loading' });
  useEffect(() => {
    let live = true;
    call('threads.diff', { id: threadId })
      .then((diff) => live && setS({ status: 'ready', diff }))
      .catch((err) => {
        if (!live) return;
        if (err instanceof DeskCallError && err.status === 409) setS({ status: 'none', message: err.message });
        else setS({ status: 'error', message: describeError(err).message });
      });
    return () => {
      live = false;
    };
  }, [threadId, version]);

  if (s.status === 'loading') return <p className="muted tab-body">Loading the diff…</p>;
  if (s.status === 'none') return <EmptyState title="No diff for this thread">This thread works in a scratch workspace, not on a git branch. See Files instead.</EmptyState>;
  if (s.status === 'error') return <EmptyState title="Couldn't load the diff">{s.message}</EmptyState>;
  const d = s.diff;
  return (
    <div className="tab-body diff-tab">
      <p className="small">
        <span className="mono">{d.branch}</span> against <span className="mono">{d.base}</span> · {d.files.length} file{d.files.length === 1 ? '' : 's'} changed.
        Desk never merges; merge the branch yourself when you're happy.
      </p>
      {d.files.length ? (
        <table className="diff-files">
          <tbody>
            {d.files.map((f) => (
              <tr key={f.path}>
                <td className="mono diff-status" title={f.status}>
                  {FILE_STATUS[f.status] ?? f.status}
                </td>
                <td className="mono grow">{f.path}</td>
                <td className="mono diff-add">{f.additions === null ? 'bin' : `+${f.additions}`}</td>
                <td className="mono diff-del">{f.deletions === null ? '' : `−${f.deletions}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">No changes yet.</p>
      )}
      {d.patch ? (
        <pre className="diff-patch" aria-label="Patch">
          {d.patch.split('\n').map((line, i) => (
            <span
              key={i}
              className={
                line.startsWith('+++') || line.startsWith('---') ? 'diff-meta' : line.startsWith('+') ? 'diff-line-add' : line.startsWith('-') ? 'diff-line-del' : line.startsWith('@@') ? 'diff-hunk' : line.startsWith('diff ') ? 'diff-meta' : undefined
              }
            >
              {line}
              {'\n'}
            </span>
          ))}
        </pre>
      ) : null}
    </div>
  );
}
