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
