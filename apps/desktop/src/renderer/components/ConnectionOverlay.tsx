import { useState } from 'react';
import { call } from '../bridge';
import { useGlobal } from '../state/global';
import { Button } from './Button';
import { describeError } from './Toast';

/** Daemon lost: an overlay with Start. Reconnecting: a calm banner. Protocol mismatch: blocks the app. */
export function ConnectionOverlay() {
  const connection = useGlobal((s) => s.connection);
  const [pending, setPending] = useState<'start' | 'restart' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = (op: 'start' | 'restart') => {
    setPending(op);
    setError(null);
    void call(op === 'start' ? 'daemon.start' : 'daemon.restart', {})
      .catch((err) => setError(describeError(err).message))
      .finally(() => setPending(null));
  };
  if (connection.status === 'reconnecting') {
    return (
      <div className="banner" role="status">
        <span className="dot warn" aria-hidden="true" />
        Reconnecting to deskd… Your threads keep running.
      </div>
    );
  }
  if (connection.status !== 'offline' && connection.status !== 'mismatch') return null;
  const mismatch = connection.status === 'mismatch';
  return (
    <div className="overlay" role="alertdialog" aria-modal="true" aria-labelledby="overlay-title">
      <div className="card">
        <h2 id="overlay-title" className="sheet-title">
          {mismatch ? 'Desk and deskd are out of step' : 'Desk isn’t running'}
        </h2>
        <p className="subtitle">
          {mismatch
            ? `${connection.detail ?? 'The daemon speaks a different protocol.'} Restart the daemon from this install, or update Desk.`
            : 'Your projects and threads are safe. Start the daemon to pick up where they left off.'}
        </p>
        {error ? (
          <p className="field-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="actions">
          <Button variant="primary" pending={pending !== null} onClick={() => run(mismatch ? 'restart' : 'start')}>
            {mismatch ? 'Restart deskd' : 'Start Desk'}
          </Button>
          <Button variant="ghost" onClick={() => void call('app.revealLogs', {}).catch(() => {})}>
            Reveal logs
          </Button>
        </div>
      </div>
    </div>
  );
}
