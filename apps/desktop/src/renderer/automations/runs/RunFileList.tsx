import { useState } from 'react';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { FileViewer } from '../../components/FileViewer';
import { toastError } from '../../components/Toast';

/** Files of a run folder as buttons; one opens in the app's file viewer below them. */
export function RunFileList({ runId, files }: { runId: string; files: Array<{ path: string; label: string }> }) {
  const [open, setOpen] = useState<{ path: string; data: Uint8Array } | null>(null);
  const show = async (path: string) => {
    try {
      setOpen({ path, data: await call('automations.file', { runId, path }) });
    } catch (err) {
      toastError(err);
    }
  };
  return (
    <div className="auto-files">
      <ul className="auto-plain">
        {files.map((f) => (
          <li key={f.path}>
            <button type="button" className="link mono" onClick={() => void show(f.path)}>
              {f.label}
            </button>
          </li>
        ))}
      </ul>
      {open ? (
        <FileViewer
          path={open.path}
          data={open.data}
          actions={
            <Button size="sm" variant="ghost" onClick={() => setOpen(null)}>
              Close
            </Button>
          }
        />
      ) : null}
    </div>
  );
}
