import { useEffect, useState, type FormEvent } from 'react';
import type { WebKeysStatus, WebKeyTestResult, WebProvider } from '@desk/protocol';
import { WEB_KEY_ENV, webKeyRows, type WebKeyRow } from '@desk/ui-core';
import { call, DeskCallError } from '../bridge';
import { Button } from './Button';
import { ConfirmDialog } from './ConfirmDialog';
import { ExternalLink } from './ExternalLink';
import { Field } from './Field';
import { describeError } from './Toast';

/** One provider: its status, a key field (unless the key comes from deskd's environment), Test, Remove, and where to get a key. */
function KeyRow({ row, onStatus }: { row: WebKeyRow; onStatus(p: WebProvider, s: WebKeysStatus[WebProvider]): void }) {
  const [key, setKey] = useState('');
  const [pending, setPending] = useState<'save' | 'test' | 'remove' | null>(null);
  const [result, setResult] = useState<WebKeyTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const run = async (what: 'save' | 'test' | 'remove', fn: () => Promise<void>) => {
    setPending(what);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setPending(null);
    }
  };
  const save = (e: FormEvent) => {
    e.preventDefault();
    void run('save', async () => {
      const s = await call('config.saveWebKey', { provider: row.provider, key });
      setKey('');
      setResult(null);
      onStatus(row.provider, s);
    });
  };
  const test = () => void run('test', async () => setResult(await call('config.testWebKey', { provider: row.provider })));
  const remove = () => {
    setConfirming(false);
    void run('remove', async () => {
      onStatus(row.provider, await call('config.removeWebKey', { provider: row.provider }));
      setResult(null);
    });
  };
  const id = `web-key-${row.provider}`;
  const set = row.statusLabel !== 'Not set';
  return (
    <div className="web-key" role="group" aria-labelledby={id}>
      <p className="status-line">
        <span className={set ? 'dot ok' : 'dot'} aria-hidden="true" />
        <strong id={id}>{row.name}</strong>
        <span className="muted">{row.powers}</span>
        <span>{row.statusLabel}</span>
      </p>
      {row.editable ? (
        <form className="sheet-body" onSubmit={save} noValidate>
          <Field id={`${id}-input`} label={`${row.name} API key`} hint="Stored in the Keychain; Desk never displays it.">
            <input id={`${id}-input`} className="input mono" type="password" autoComplete="off" spellCheck={false} value={key} onChange={(e) => setKey(e.target.value)} />
          </Field>
          <div className="actions">
            <Button type="submit" variant="primary" size="sm" pending={pending === 'save'} disabled={!key.trim()}>
              Save
            </Button>
            {set ? (
              <Button size="sm" pending={pending === 'test'} onClick={test}>
                Test
              </Button>
            ) : null}
            {row.removable ? (
              <Button size="sm" variant="ghost" pending={pending === 'remove'} onClick={() => setConfirming(true)}>
                Remove
              </Button>
            ) : null}
            <ExternalLink href={row.dashboardUrl}>Get a key</ExternalLink>
          </div>
        </form>
      ) : (
        <>
          <p className="field-hint">
            Set by <span className="mono">{WEB_KEY_ENV[row.provider]}</span> in deskd’s environment.
          </p>
          <div className="actions">
            <Button size="sm" pending={pending === 'test'} onClick={test}>
              Test
            </Button>
          </div>
        </>
      )}
      {result?.ok ? (
        <p className="status-line">
          <span className="dot ok" aria-hidden="true" />
          Key works.
        </p>
      ) : null}
      {result && !result.ok ? (
        <p className="field-error" role="alert">
          Key test failed: {result.error ?? 'unknown error'}
        </p>
      ) : null}
      {error ? (
        <p className="field-error" role="alert">
          {error}
        </p>
      ) : null}
      {confirming ? (
        <ConfirmDialog title={`Remove the ${row.name} key?`} confirmLabel="Remove" danger onCancel={() => setConfirming(false)} onConfirm={remove}>
          <p className="subtitle">Agents fall back to the keyless engines for what {row.name} did until you add a key again.</p>
        </ConfirmDialog>
      ) : null}
    </div>
  );
}

/** System → Web search and fetch: the Serper and Firecrawl keys, stored by deskd in the Keychain and never shown. */
export function WebKeysPanel() {
  const [status, setStatus] = useState<WebKeysStatus | 'unsupported' | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    call('config.webKeys', {})
      .then(setStatus)
      .catch((err) => {
        if (err instanceof DeskCallError && (err.code === 'unsupported' || err.status === 501)) setStatus('unsupported');
        else setError(describeError(err).message);
      });
  }, []);
  const onStatus = (p: WebProvider, s: WebKeysStatus[WebProvider]) => setStatus((prev) => (prev && prev !== 'unsupported' ? { ...prev, [p]: s } : prev));
  return (
    <div className="web-keys">
      <p className="field-hint">Agents search with Serper and read pages with Firecrawl. Without a key, they fall back to keyless search engines and plain fetches.</p>
      {status === 'unsupported' ? <p className="status-line">This deskd takes its web keys from its environment.</p> : null}
      {status && status !== 'unsupported' ? webKeyRows(status).map((row) => <KeyRow key={row.provider} row={row} onStatus={onStatus} />) : null}
      {error ? (
        <p className="field-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
