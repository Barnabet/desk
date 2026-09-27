import { useState } from 'react';
import type { AutomationDetail } from '@desk/protocol';
import { href, versionBadge, type AutomationView } from '@desk/ui-core';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { toast, toastError } from '../components/Toast';
import { navigate } from '../router';
import { RunDialog } from './dialogs/RunDialog';
import { TurnOnDialog } from './dialogs/TurnOnDialog';

type Dialog = null | 'run' | 'test' | 'turn-on' | 'delete';

/** One automation's header (mockup 2): title, version badge, the switch, Test… and Run now…, and the sub-tabs. */
export function AutomationHeader(o: { projectId: string; detail: AutomationDetail; view: AutomationView; onChange(d: AutomationDetail): void }) {
  const d = o.detail;
  const [dialog, setDialog] = useState<Dialog>(null);
  const [switching, setSwitching] = useState(false);
  const route = (view: AutomationView, runId?: string) => ({ name: 'project' as const, id: o.projectId, tab: 'automations' as const, automationId: d.id, view, ...(runId ? { runId } : {}) });

  const toggle = async () => {
    if (!d.enabled) {
      setDialog('turn-on');
      return;
    }
    setSwitching(true);
    try {
      o.onChange(await call('automations.setEnabled', { id: d.id, enabled: false }));
    } catch (err) {
      toastError(err);
    } finally {
      setSwitching(false);
    }
  };
  const exportIt = async () => {
    try {
      const exp = await call('automations.export', { id: d.id });
      await call('app.saveFile', { name: `${d.name}.desk-automation.json`, data: new TextEncoder().encode(`${JSON.stringify(exp, null, 2)}\n`) });
    } catch (err) {
      toastError(err);
    }
  };
  const remove = async () => {
    setDialog(null);
    try {
      await call('automations.remove', { id: d.id });
      toast({ tone: 'info', message: `Deleted ${d.title}.` });
      navigate({ name: 'project', id: o.projectId, tab: 'automations' });
    } catch (err) {
      toastError(err);
    }
  };
  const tab = (view: AutomationView, label: string) => (
    <a href={href(route(view))} aria-current={o.view === view ? 'page' : undefined}>
      {label}
    </a>
  );
  const runs = d.last_run?.number ?? 0;
  return (
    <header className="automation-head">
      <div className="automation-title-row">
        <a className="muted small" href={href({ name: 'project', id: o.projectId, tab: 'automations' })}>
          Automations ›
        </a>
        <h1 className="automation-title">{d.title}</h1>
        <span className={`auto-badge${d.tested_version === d.version ? ' ok' : ''}`}>{versionBadge(d)}</span>
        {d.grants_suspended ? (
          <a className="auto-badge warn" href={href(route('grants'))}>
            grants suspended
          </a>
        ) : null}
        <span className="grow" />
        <span className="muted small">{d.enabled ? 'On' : 'Off'}</span>
        <button type="button" role="switch" aria-checked={d.enabled} aria-label={d.enabled ? `Turn off ${d.title}` : `Turn on ${d.title}`} className="switch" disabled={switching} onClick={() => void toggle()}>
          <span className="switch-knob" />
        </button>
        <Button onClick={() => setDialog('test')}>Test…</Button>
        <Button variant="primary" onClick={() => setDialog('run')}>
          Run now…
        </Button>
      </div>
      <nav className="automation-tabs" aria-label="Automation">
        {tab('design', 'Design')}
        {tab('runs', runs ? `Runs (${runs})` : 'Runs')}
        {tab('versions', 'Versions')}
        {tab('grants', d.grants.length ? `Grants (${d.grants.length})` : 'Grants')}
        <span className="grow" />
        <Button size="sm" variant="ghost" onClick={() => void exportIt()}>
          Export…
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setDialog('delete')}>
          Delete…
        </Button>
      </nav>
      {dialog === 'run' || dialog === 'test' ? (
        <RunDialog
          target={d}
          test={dialog === 'test'}
          onClose={() => setDialog(null)}
          onStarted={(runId) => {
            setDialog(null);
            navigate(route('runs', runId));
          }}
        />
      ) : null}
      {dialog === 'turn-on' ? (
        <TurnOnDialog
          detail={d}
          onClose={() => setDialog(null)}
          onDone={(next) => {
            setDialog(null);
            o.onChange(next);
          }}
          onTestFirst={() => setDialog('test')}
        />
      ) : null}
      {dialog === 'delete' ? (
        <ConfirmDialog title={`Delete ${d.title}?`} confirmLabel="Delete" danger onConfirm={() => void remove()} onCancel={() => setDialog(null)}>
          Its runs are cancelled and its schedules stop. Its history, and the files its runs put in the Library, are kept.
        </ConfirmDialog>
      ) : null}
    </header>
  );
}

/** The header of a Blank automation before its first save. */
export function DraftHeader({ projectId, name }: { projectId: string; name: string }) {
  return (
    <header className="automation-head">
      <div className="automation-title-row">
        <a className="muted small" href={href({ name: 'project', id: projectId, tab: 'automations' })}>
          Automations ›
        </a>
        <h1 className="automation-title">New automation</h1>
        <span className="auto-badge mono">{name}</span>
        <span className="muted small">Not saved yet: add a step, then Save.</span>
      </div>
      <nav className="automation-tabs" aria-label="Automation">
        <a href={href({ name: 'project', id: projectId, tab: 'automations', draft: name })} aria-current="page">
          Design
        </a>
      </nav>
    </header>
  );
}
