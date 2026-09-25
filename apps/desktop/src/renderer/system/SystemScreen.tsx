import { useCallback, useEffect, useMemo, useState } from 'react';
import type { RuntimesReport, UsageResponse } from '@desk/protocol';
import type { ChannelOutput } from '@desk/bff/contract';
import { bytes, clock, duration, href, plural } from '@desk/ui-core';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EndpointPanel } from '../components/EndpointPanel';
import { describeError, toast, toastError } from '../components/Toast';
import { useGlobal } from '../state/global';
import { tokens } from '../threads/tabs/UsageTab';
import { ModelsEditor } from './ModelsEditor';
import './system.css';

type DaemonStatusView = ChannelOutput<'daemon.status'>;
type AppInfo = ChannelOutput<'app.info'>;

type Period = 'all' | '30d' | '7d';
const PERIOD_DAYS: Record<Exclude<Period, 'all'>, number> = { '30d': 30, '7d': 7 };

function DaemonSection() {
  const [s, setS] = useState<DaemonStatusView | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmStop, setConfirmStop] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    call('daemon.status', {})
      .then((v) => (setS(v), setError(null)))
      .catch((err) => setError(describeError(err).message));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 10_000);
    return () => clearInterval(t);
  }, [load]);
  const act = async (what: 'start' | 'restart' | 'stop' | 'repair') => {
    setConfirmStop(false);
    setBusy(what);
    try {
      setS(await call(`daemon.${what}`, {}));
    } catch (err) {
      toastError(err);
      load();
    } finally {
      setBusy(null);
    }
  };
  return (
    <section className="card sys-section" aria-labelledby="sys-daemon">
      <h2 id="sys-daemon">deskd</h2>
      {error ? <p className="field-error">{error}</p> : null}
      {s ? (
        <>
          <p className="status-line">
            <span className={`dot ${s.running ? (s.proxy === 'down' ? 'warn' : 'ok') : 'bad'}`} aria-hidden="true" />
            <strong>{s.running ? 'Running' : 'Not running'}</strong>
            {s.running ? (
              <span className="muted">
                · v{s.version} · pid {s.pid}
                {s.uptime_s !== null ? ` · up ${duration(s.uptime_s * 1000)}` : ''}
              </span>
            ) : null}
          </p>
          <dl className="sys-facts">
            <div>
              <dt>Model proxy</dt>
              <dd>{s.proxy === 'up' ? 'Reachable' : s.proxy === 'down' ? 'Unreachable. Threads pause and resume when it is back.' : 'Unknown'}</dd>
            </div>
            <div>
              <dt>Mode</dt>
              <dd>{s.mode === 'packaged' ? `Bundled deskd ${s.bundledVersion}` : 'Development (runs from this repository)'}</dd>
            </div>
            <div>
              <dt>Starts at login</dt>
              <dd>{s.agent === 'installed' ? 'Yes, as a LaunchAgent' : s.agent === 'missing' ? 'No. Install the LaunchAgent to keep Desk running.' : 'Not on this platform'}</dd>
            </div>
          </dl>
          {s.running && s.version && s.mode === 'packaged' && s.version !== s.bundledVersion ? (
            <p className="field-hint">This deskd is v{s.version}; the app bundles v{s.bundledVersion}. Repair installs the bundled one.</p>
          ) : s.running && s.mode === 'packaged' && s.bundledBuild && s.build !== null && s.build !== s.bundledBuild ? (
            <p className="field-hint">This deskd is from a different build than the app. Repair installs the bundled one.</p>
          ) : null}
          <div className="actions">
            {s.running ? (
              <>
                <Button size="sm" pending={busy === 'restart'} disabled={busy !== null} onClick={() => void act('restart')}>
                  Restart
                </Button>
                <Button size="sm" variant="ghost" pending={busy === 'stop'} disabled={busy !== null} onClick={() => setConfirmStop(true)}>
                  Stop
                </Button>
              </>
            ) : (
              <Button size="sm" variant="primary" pending={busy === 'start'} disabled={busy !== null} onClick={() => void act('start')}>
                Start
              </Button>
            )}
            {s.agent !== 'unsupported' ? (
              <Button size="sm" variant="ghost" pending={busy === 'repair'} disabled={busy !== null} onClick={() => void act('repair')}>
                {s.agent === 'installed' ? 'Repair LaunchAgent' : 'Install LaunchAgent'}
              </Button>
            ) : null}
          </div>
        </>
      ) : error ? null : (
        <p className="muted">Checking…</p>
      )}
      {confirmStop ? (
        <ConfirmDialog title="Stop deskd?" confirmLabel="Stop" danger onCancel={() => setConfirmStop(false)} onConfirm={() => void act('stop')}>
          Running threads pause. They resume where they were when deskd starts again.
        </ConfirmDialog>
      ) : null}
    </section>
  );
}

function UsageSection() {
  const overview = useGlobal((g) => g.overview);
  const [period, setPeriod] = useState<Period>('30d');
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const since = period === 'all' ? undefined : new Date(Date.now() - PERIOD_DAYS[period] * 86_400_000).toISOString().slice(0, 10);
    call('usage', since ? { since } : {})
      .then((u) => live && (setUsage(u), setError(null)))
      .catch((err) => live && setError(describeError(err).message));
    return () => {
      live = false;
    };
  }, [period]);
  const names = useMemo(() => new Map(overview.map((p) => [p.project.id, p.project.name])), [overview]);
  const sum = (key: 'model' | 'project_id') => {
    const by = new Map<string, { prompt: number; completion: number }>();
    for (const r of usage?.rows ?? []) {
      const k = r[key];
      const v = by.get(k) ?? { prompt: 0, completion: 0 };
      v.prompt += r.prompt_tokens;
      v.completion += r.completion_tokens;
      by.set(k, v);
    }
    return [...by].sort((a, b) => b[1].prompt + b[1].completion - (a[1].prompt + a[1].completion));
  };
  const table = (title: string, rows: Array<[string, { prompt: number; completion: number }]>, label: (k: string) => React.ReactNode) => (
    <table className="usage-table">
      <caption>{title}</caption>
      <thead>
        <tr>
          <th scope="col">{title === 'By model' ? 'Model' : 'Project'}</th>
          <th scope="col">Prompt</th>
          <th scope="col">Completion</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td>{label(k)}</td>
            <td title={v.prompt.toLocaleString()}>{tokens(v.prompt)}</td>
            <td title={v.completion.toLocaleString()}>{tokens(v.completion)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
  return (
    <section className="card sys-section" aria-labelledby="sys-usage">
      <div className="sys-head">
        <h2 id="sys-usage">Usage</h2>
        <div className="segmented" role="group" aria-label="Period">
          {(['7d', '30d', 'all'] as Period[]).map((p) => (
            <button key={p} type="button" aria-pressed={period === p} onClick={() => setPeriod(p)}>
              {p === 'all' ? 'All time' : p === '7d' ? '7 days' : '30 days'}
            </button>
          ))}
        </div>
      </div>
      {error ? <p className="field-error">{error}</p> : null}
      {usage ? (
        usage.rows.length ? (
          <>
            <p className="small">
              {tokens(usage.totals.prompt_tokens)} prompt and {tokens(usage.totals.completion_tokens)} completion tokens.
            </p>
            <div className="usage-grid">
              {table('By model', sum('model'), (k) => <span className="mono">{k}</span>)}
              {table('By project', sum('project_id'), (k) => (names.has(k) ? <a href={href({ name: 'project', id: k, tab: 'conversation' })}>{names.get(k)}</a> : <span className="muted">{k === '_global' ? 'Outside projects' : 'Archived project'}</span>))}
            </div>
          </>
        ) : (
          <p className="muted">No model calls in this period.</p>
        )
      ) : error ? null : (
        <p className="muted">Loading…</p>
      )}
    </section>
  );
}

function NoticesSection() {
  const notices = useGlobal((g) => g.system.notices);
  const overview = useGlobal((g) => g.overview);
  const names = useMemo(() => new Map(overview.map((p) => [p.project.id, p.project.name])), [overview]);
  return (
    <section className="card sys-section" aria-labelledby="sys-notices">
      <h2 id="sys-notices">System notices</h2>
      {notices.length ? (
        <ol className="notices" reversed>
          {notices
            .slice()
            .reverse()
            .slice(0, 50)
            .map((n) => (
              <li key={n.eventId} className={`notice notice-${n.level}`}>
                <span className="mono small muted">{clock(n.ts)}</span>
                <span className="grow">{n.message}</span>
                <span className="small muted">{names.get(n.projectId) ?? ''}</span>
              </li>
            ))}
        </ol>
      ) : (
        <p className="muted">Nothing to report since the app started. Proxy outages, restarts and recoveries show up here.</p>
      )}
    </section>
  );
}

function NotificationsSection() {
  const [appOn, setAppOn] = useState<boolean | null>(null);
  const [daemon, setDaemon] = useState<'auto' | 'off' | null>(null);
  useEffect(() => {
    call('app.settings', {})
      .then((s) => setAppOn(s.notifications))
      .catch(() => setAppOn(null));
    call('config.get', {})
      .then((c) => setDaemon(c.notifications))
      .catch(() => setDaemon(null));
  }, []);
  const setApp = async (v: boolean) => {
    try {
      setAppOn((await call('app.updateSettings', { notifications: v })).notifications);
    } catch (err) {
      toastError(err);
    }
  };
  const setD = async (v: 'auto' | 'off') => {
    try {
      setDaemon((await call('config.patch', { notifications: v })).notifications);
    } catch (err) {
      toastError(err);
    }
  };
  return (
    <section className="card sys-section" aria-labelledby="sys-notify">
      <h2 id="sys-notify">Notifications</h2>
      <label className="toggle">
        <input type="checkbox" checked={appOn ?? false} disabled={appOn === null} onChange={(e) => void setApp(e.target.checked)} />
        <span>
          <strong>From the app</strong>
          <span className="muted small">Approvals, questions, hand-offs and stuck threads, while Desk is open or in the menu bar. Silent while a Desk window is focused.</span>
        </span>
      </label>
      <label className="toggle">
        <input type="checkbox" checked={daemon === 'auto'} disabled={daemon === null} onChange={(e) => void setD(e.target.checked ? 'auto' : 'off')} />
        <span>
          <strong>From deskd when the app is closed</strong>
          <span className="muted small">deskd stays quiet while the app is running, so you never get both.</span>
        </span>
      </label>
    </section>
  );
}

/** Skill environments Desk set up for catalog skills: their total size, and removing the ones no skill uses. */
function RuntimesFacts() {
  const [report, setReport] = useState<RuntimesReport | null>(null);
  const [pending, setPending] = useState(false);
  const load = () =>
    call('system.runtimes', {})
      .then(setReport)
      .catch(() => setReport(null));
  useEffect(() => void load(), []);
  if (!report) return null;
  const orphans = report.envs.filter((e) => e.orphan);
  const orphanBytes = orphans.reduce((n, e) => n + e.bytes, 0);
  const cleanup = async () => {
    setPending(true);
    try {
      const r = await call('system.runtimesCleanup', {});
      toast({ tone: 'info', message: `Removed ${plural(r.removed, 'unused environment')} (${bytes(r.bytes)}).` });
      await load();
    } catch (err) {
      toastError(err);
    } finally {
      setPending(false);
    }
  };
  return (
    <div>
      <dt>Skill environments</dt>
      <dd>
        {report.envs.length ? `${bytes(report.bytes)} for ${plural(report.envs.length, 'skill')}` : 'None yet'}
        {orphans.length ? (
          <>
            {' · '}
            <Button size="sm" pending={pending} onClick={() => void cleanup()}>
              Clean up unused ({bytes(orphanBytes)})
            </Button>
          </>
        ) : null}
      </dd>
    </div>
  );
}

function AboutSection() {
  const [info, setInfo] = useState<AppInfo | null>(null);
  useEffect(() => {
    call('app.info', {})
      .then(setInfo)
      .catch(() => setInfo(null));
  }, []);
  return (
    <section className="card sys-section" aria-labelledby="sys-about">
      <h2 id="sys-about">Data</h2>
      <dl className="sys-facts">
        <div>
          <dt>Data directory</dt>
          <dd className="mono">{info?.dataDir ?? '…'}</dd>
        </div>
        <RuntimesFacts />
        <div>
          <dt>App</dt>
          <dd>
            Desk {info?.version ?? ''} {info && !info.packaged ? '(development)' : ''}
          </dd>
        </div>
      </dl>
      <div className="actions">
        <Button size="sm" onClick={() => void call('app.revealLogs', {}).catch(toastError)}>
          Reveal logs
        </Button>
      </div>
    </section>
  );
}

/** The machine room: deskd, the model endpoint and registry, usage, notices, notifications and data. */
export function SystemScreen() {
  return (
    <div className="page system">
      <h1 className="title">System</h1>
      <div className="sys-grid">
        <DaemonSection />
        <section className="card sys-section" aria-labelledby="sys-endpoint">
          <h2 id="sys-endpoint">Model endpoint</h2>
          <EndpointPanel />
        </section>
        <NotificationsSection />
        <AboutSection />
      </div>
      <section className="card sys-section" aria-labelledby="sys-models">
        <h2 id="sys-models">Model registry</h2>
        <p className="field-hint">The models projects can choose. Concurrency caps how many calls to a model run at once across all projects.</p>
        <ModelsEditor />
      </section>
      <UsageSection />
      <NoticesSection />
    </div>
  );
}
