import { useState, type ReactNode } from 'react';
import { call } from '../bridge';
import { ConfirmDialog } from './ConfirmDialog';
import { toastError } from './Toast';

const ALLOWED = new Set(['http:', 'https:', 'mailto:']);

/** A link from agent content: web and mail links open in the system browser after a confirmation; others are inert. */
export function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  const [asking, setAsking] = useState(false);
  let url: URL | null = null;
  try {
    url = new URL(href);
  } catch {
    url = null;
  }
  if (!url || !ALLOWED.has(url.protocol)) return <span className="md-link-disabled">{children}</span>;
  const target = url.toString();
  return (
    <>
      <button type="button" className="link" title={target} onClick={() => setAsking(true)}>
        {children}
      </button>
      {asking ? (
        <ConfirmDialog
          title="Open this link?"
          confirmLabel="Open in browser"
          onCancel={() => setAsking(false)}
          onConfirm={() => {
            setAsking(false);
            void call('app.openExternal', { url: target }).catch(toastError);
          }}
        >
          <p className="subtitle">Links in agent messages can point anywhere. Check the address first.</p>
          <p className="mono" style={{ margin: 0, overflowWrap: 'anywhere' }}>
            {target}
          </p>
        </ConfirmDialog>
      ) : null}
    </>
  );
}
