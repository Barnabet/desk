import { useEffect, useRef, useState } from 'react';
import type { ProjectState, ServiceRow } from '@desk/client';
import { ago, duration } from '@desk/ui-core';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { Sheet } from '../components/Sheet';
import { toastError } from '../components/Toast';
import { useNow } from '../state/now';

const STOP_REASON: Record<string, string> = {
  requested: 'stopped',
  restart: 'restarting',
  thread_archived: 'stopped · thread archived',
  project_archived: 'stopped · project archived',
  daemon_shutdown: 'stopped · deskd restarted',
  daemon_restart: 'stopped · deskd restarted',
};

/** Only loopback http(s) URLs are ever offered to open (they come from a process's own output). */
export function openableUrl(url: string | null): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return (u.protocol === 'http:' || u.protocol === 'https:') && ['localhost', '127.0.0.1', '[::1]'].includes(u.hostname) ? u.toString() : null;
  } catch {
    return null;
  }
}

export function serviceState(s: ServiceRow, now: number): { tone: 'run' | 'fail' | 'off'; text: string } {
  if (s.status === 'running') return { tone: 'run', text: `running · ${duration(now - Date.parse(s.started_at))}` };
  const when = s.ended_at ? ` · ${ago(s.ended_at, now) === 'now' ? 'just now' : `${ago(s.ended_at, now)} ago`}` : '';
  if (s.status === 'exited') {
    const failed = s.exit_signal !== null || s.exit_code !== 0;
    return { tone: failed ? 'fail' : 'off', text: `exited (${s.exit_signal ?? s.exit_code})${when}` };
  }
  return { tone: 'off', text: `${STOP_REASON[s.stop_reason ?? 'requested'] ?? 'stopped'}${when}` };
}

const port = (url: string | null) => {
  if (!url) return null;
  try {
    const u = new URL(url);
    return `:${u.port || (u.protocol === 'https:' ? '443' : '80')}`;
  } catch {
    return null;
  }
};

/** The project's services under the plan: status, URL, where they run, and Open / Logs / Restart / Stop / Start. */
export function ServicesCard({ project }: { project: ProjectState }) {
  const now = useNow();
  const [busy, setBusy] = useState<string | null>(null);
  const [logsFor, setLogsFor] = useState<ServiceRow | null>(null);
  const services = project.services;
  if (!services.length) return null;
  const titles = new Map(project.threads.map((t) => [t.id, t.title ?? 'thread']));
  const sourceLabels = new Map(project.sources.map((x) => [x.id, x.label]));
  const act = async (s: ServiceRow, what: 'start' | 'stop' | 'restart') => {
    setBusy(`${s.id}:${what}`);
    try {
      await (what === 'start' ? call('services.start', { id: s.id }) : what === 'stop' ? call('services.stop', { id: s.id }) : call('services.restart', { id: s.id }));
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(null);
    }
  };
  const running = services.filter((s) => s.status === 'running').length;
  return (
    <section className="card services-card" aria-label="Services">
      <div className="services-head">
        <h2>Services</h2>
        <span className="chip chip-idle">{running ? `${running} running` : 'none running'}</span>
      </div>
      <ul className="services-list">
        {services.map((s) => {
          const state = serviceState(s, now);
          const open = s.status === 'running' ? openableUrl(s.url) : null;
          return (
            <li key={s.id} className={`service service-${state.tone}`} aria-label={`${s.name}, ${state.text}`}>
              <span className="service-dot" aria-hidden="true" />
              <div className="service-text">
                <span className="service-name">
                  {s.name}
                  {s.status === 'running' && port(s.url) ? <span className="service-port">{port(s.url)}</span> : null}
                </span>
                <span className="service-sub" title={`${state.text} · ${s.command}`}>
                  {state.text} · from {s.source_id ? (sourceLabels.get(s.source_id) ?? 'a removed folder') : (titles.get(s.agent_id) ?? 'an archived thread')}
                </span>
              </div>
              <div className="service-actions">
                {open ? (
                  <Button size="sm" variant="ghost" onClick={() => void call('app.openExternal', { url: open }).catch(toastError)} title={open}>
                    Open
                  </Button>
                ) : null}
                <Button size="sm" variant="ghost" onClick={() => setLogsFor(s)}>
                  Logs
                </Button>
                {s.status === 'running' ? (
                  <>
                    <Button size="sm" variant="ghost" pending={busy === `${s.id}:restart`} disabled={busy !== null} onClick={() => void act(s, 'restart')}>
                      Restart
                    </Button>
                    <Button size="sm" variant="ghost" pending={busy === `${s.id}:stop`} disabled={busy !== null} onClick={() => void act(s, 'stop')} aria-label={`Stop ${s.name}`}>
                      Stop
                    </Button>
                  </>
                ) : (
                  <Button size="sm" pending={busy === `${s.id}:start`} disabled={busy !== null} onClick={() => void act(s, 'start')} aria-label={`Start ${s.name}`}>
                    Start
                  </Button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {logsFor ? <LogsSheet service={services.find((s) => s.id === logsFor.id) ?? logsFor} onClose={() => setLogsFor(null)} /> : null}
    </section>
  );
}

/** A service's log tail, refreshed every second while open. Plain text: it is the process's own output. */
function LogsSheet({ service, onClose }: { service: ServiceRow; onClose(): void }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pre = useRef<HTMLPreElement>(null);
  const pinned = useRef(true);
  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const r = await call('services.logs', { id: service.id, lines: 500 });
        if (!stop) {
          setText(r.text);
          setError(null);
        }
      } catch (err) {
        if (!stop) setError(err instanceof Error ? err.message : String(err));
      }
    };
    void load();
    const timer = setInterval(() => void load(), 1000);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [service.id]);
  useEffect(() => {
    const el = pre.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [text]);
  return (
    <Sheet title={`${service.name} · logs`} onClose={onClose} width={860} footer={<Button onClick={onClose}>Close</Button>}>
      <p className="small muted service-command">
        <code>{service.command}</code>
        {service.cwd !== '.' ? ` in ${service.cwd}` : ''}
      </p>
      {error ? <p className="field-error">{error}</p> : null}
      <pre
        className="service-log"
        ref={pre}
        aria-label={`${service.name} log`}
        onScroll={(e) => {
          const el = e.currentTarget;
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
      >
        {text === null ? 'Loading…' : text || '(no output yet)'}
      </pre>
    </Sheet>
  );
}
