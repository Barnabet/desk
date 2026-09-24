import { useEffect, useState, type FormEvent } from 'react';
import type { ModelEndpointStatus, ModelEndpointTestResult } from '@desk/protocol';
import { call, DeskCallError } from '../bridge';
import { Button } from '../components/Button';
import { Field } from '../components/Field';
import { describeError } from '../components/Toast';
import { navigate } from '../router';
import { useGlobal } from '../state/global';
import { ProjectForm } from './ProjectForm';

const FLAG = 'desk.onboarded';

export function isOnboarded(): boolean {
  try {
    return localStorage.getItem(FLAG) === '1';
  } catch {
    return false;
  }
}

export function markOnboarded(): void {
  try {
    localStorage.setItem(FLAG, '1');
  } catch {
    // Onboarding shows again next launch; harmless.
  }
}

type Step = 'daemon' | 'endpoint' | 'project';
const STEPS: Array<{ id: Step; label: string }> = [
  { id: 'daemon', label: 'Daemon' },
  { id: 'endpoint', label: 'Model endpoint' },
  { id: 'project', label: 'First project' },
];

function DaemonStep({ onNext }: { onNext(): void }) {
  const status = useGlobal((s) => s.connection.status);
  const detail = useGlobal((s) => s.connection.detail);
  const version = useGlobal((s) => s.health?.version);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const start = async () => {
    setPending(true);
    setError(null);
    try {
      await call('daemon.start', {});
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setPending(false);
    }
  };
  const live = status === 'live' || status === 'connecting';
  return (
    <section className="sheet-body" aria-labelledby="step-title">
      <h1 id="step-title" className="title">
        Start the Desk daemon
      </h1>
      <p className="subtitle">Desk runs in the background as deskd, so your projects keep working when this window is closed.</p>
      <p className="status-line">
        <span className={`dot ${live ? 'ok' : status === 'mismatch' ? 'bad' : ''}`} aria-hidden="true" />
        {live ? `deskd ${version ?? ''} is running.`.replace('  ', ' ') : status === 'mismatch' ? (detail ?? 'deskd needs an update.') : status === 'offline' ? 'deskd is not running yet.' : 'Looking for deskd…'}
      </p>
      {error ? (
        <p className="field-error" role="alert">
          {error}{' '}
          <button type="button" className="link" onClick={() => void call('app.revealLogs', {}).catch(() => {})}>
            Reveal logs
          </button>
        </p>
      ) : null}
      <div className="actions">
        {live ? (
          <Button variant="primary" onClick={onNext}>
            Continue
          </Button>
        ) : (
          <Button variant="primary" pending={pending} disabled={status === 'starting'} onClick={() => void start()}>
            Start Desk
          </Button>
        )}
      </div>
    </section>
  );
}

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
      {`Connected. ${result.models?.length ?? 0} models available.`}
    </p>
  ) : (
    <p className="field-error" role="alert">
      Couldn’t connect: {result.error ?? 'unknown error'}
    </p>
  );
}

function EndpointStep({ onNext }: { onNext(): void }) {
  const [status, setStatus] = useState<ModelEndpointStatus | 'unsupported' | null>(null);
  const [editing, setEditing] = useState(false);
  const [baseUrl, setBaseUrl] = useState('http://127.0.0.1:8317/v1');
  const [apiKey, setApiKey] = useState('');
  const [result, setResult] = useState<ModelEndpointTestResult | null>(null);
  const [pending, setPending] = useState<'test' | 'save' | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    call('config.endpoint', {})
      .then((s) => setStatus(s))
      .catch((err) => {
        if (err instanceof DeskCallError && (err.code === 'unsupported' || err.status === 501)) setStatus('unsupported');
        else setError(describeError(err).message);
      });
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
    <section className="sheet-body" aria-labelledby="step-title">
      <h1 id="step-title" className="title">
        Connect a model endpoint
      </h1>
      <p className="subtitle">Desk talks to an OpenAI-compatible endpoint, such as a local proxy. The key goes to your macOS Keychain and is never shown again.</p>
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
            {status.source === 'keychain' ? (
              <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
                Change key
              </Button>
            ) : null}
          </div>
        </>
      ) : null}
      {status !== null && status !== 'unsupported' && (!configured || editing) ? (
        <form className="sheet-body" onSubmit={save} noValidate>
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
          </div>
        </form>
      ) : null}
      <TestResult result={result} />
      {error ? (
        <p className="field-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="actions">
        <Button variant={configured || status === 'unsupported' ? 'primary' : 'ghost'} onClick={onNext} disabled={status === null && !error}>
          {configured || status === 'unsupported' ? 'Continue' : 'Skip for now'}
        </Button>
      </div>
    </section>
  );
}

function ProjectStep({ onDone }: { onDone(): void }) {
  const count = useGlobal((s) => s.overview.length);
  return (
    <section className="sheet-body" aria-labelledby="step-title">
      <h1 id="step-title" className="title">
        Create your first project
      </h1>
      <p className="subtitle">A project is a goal Desk works toward, with its own threads, library and memory.</p>
      {count > 0 ? (
        <p className="status-line">
          You already have {count} project{count === 1 ? '' : 's'}.{' '}
          <Button variant="ghost" size="sm" onClick={onDone}>
            Skip to the map
          </Button>
        </p>
      ) : null}
      <ProjectForm onCreated={() => onDone()} />
    </section>
  );
}

export function Onboarding() {
  const [step, setStep] = useState<Step>('daemon');
  const index = STEPS.findIndex((s) => s.id === step);
  const finish = () => {
    markOnboarded();
    navigate({ name: 'map' });
  };
  return (
    <div className="onboarding">
      <div className="drag-strip" />
      <div className="card onboarding-card">
        <p className="eyebrow">Welcome to Desk</p>
        <ol className="steps" aria-label="Setup steps">
          {STEPS.map((s, i) => (
            <li key={s.id} className={i < index ? 'done' : undefined} aria-current={i === index ? 'step' : undefined}>
              {s.label}
            </li>
          ))}
        </ol>
        {step === 'daemon' ? <DaemonStep onNext={() => setStep('endpoint')} /> : null}
        {step === 'endpoint' ? <EndpointStep onNext={() => setStep('project')} /> : null}
        {step === 'project' ? <ProjectStep onDone={finish} /> : null}
      </div>
    </div>
  );
}
