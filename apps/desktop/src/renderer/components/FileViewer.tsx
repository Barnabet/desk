import { useEffect, useMemo, useState } from 'react';
import { asText, bytes, extOf, imageMime, isMarkdown } from '@desk/ui-core';
import { call } from '../bridge';
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
