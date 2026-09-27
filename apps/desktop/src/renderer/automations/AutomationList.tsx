import { useRef, useState } from 'react';
import { AutomationExport, type AutomationDetail, type AutomationSummary } from '@desk/protocol';
import { dayTime, href, lastRunText, listNote, whenText } from '@desk/ui-core';
import { call, DeskCallError } from '../bridge';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { toast, toastError } from '../components/Toast';
import { primeDraft } from '../conversation/draft';
import { navigate } from '../router';
import { useNow } from '../state/now';
import type { SessionState } from '../state/session';
import { useAutomationList } from './data';
import { NameDialog } from './dialogs/NameDialog';
import { RunDialog } from './dialogs/RunDialog';
import { TurnOnDialog } from './dialogs/TurnOnDialog';

/** Mockup 1: every automation with its switch, when it runs, its last run and its next one. */
export function AutomationList({ projectId, s }: { projectId: string; s: SessionState }) {
  const live = useAutomationList(projectId, s);
  const now = useNow();
  const [naming, setNaming] = useState<null | { importing?: AutomationExport }>(null);
  const [turnOn, setTurnOn] = useState<AutomationDetail | null>(null);
  const [testing, setTesting] = useState<AutomationDetail | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const list = live.value ?? [];
  const at = (automationId: string, extra: { view: 'design' } | { view: 'runs'; runId: string } = { view: 'design' }) => ({ name: 'project' as const, id: projectId, tab: 'automations' as const, automationId, ...extra });

  const describe = () => {
    primeDraft(projectId, "I'd like to automate: ");
    navigate({ name: 'project', id: projectId, tab: 'conversation' });
  };
  const doImport = async (exp: AutomationExport) => {
    try {
      const r = await call('automations.import', { projectId, exp });
      toast({ tone: 'info', message: `Imported ${r.automation.title}. It stays off until you turn it on.` });
      navigate(at(r.automation.id));
    } catch (err) {
      if (err instanceof DeskCallError && err.status === 409) setNaming({ importing: exp });
      else toastError(err);
    }
  };
  const readImport = async (file: File) => {
    let raw: unknown = null;
    try {
      raw = JSON.parse(await file.text());
    } catch {
      raw = null;
    }
    const parsed = AutomationExport.safeParse(raw);
    if (!parsed.success) {
      toast({ tone: 'error', message: "That file isn't a Desk automation export." });
      return;
    }
    await doImport(parsed.data);
  };
  const toggle = async (a: AutomationSummary) => {
    setBusy(a.id);
    try {
      if (a.enabled) {
        await call('automations.setEnabled', { id: a.id, enabled: false });
        live.reload();
      } else setTurnOn(await call('automations.get', { id: a.id }));
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="auto-list">
      <header className="auto-list-head">
        <h1 className="title">Automations</h1>
        <span className="grow" />
        <Button onClick={() => fileRef.current?.click()}>Import…</Button>
        <Button onClick={() => setNaming({})}>Blank automation</Button>
        <Button variant="primary" onClick={describe}>
          Describe one to Desk
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".json,application/json"
          hidden
          data-testid="automation-import"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file) void readImport(file);
          }}
        />
      </header>
      {live.status === 'loading' ? (
        <p className="muted">Loading…</p>
      ) : live.status === 'error' && !live.value ? (
        <EmptyState title="Couldn't load automations">{live.error}</EmptyState>
      ) : list.length ? (
        <table className="auto-table">
          <thead>
            <tr>
              <th>Automation</th>
              <th>On</th>
              <th>When</th>
              <th>Last run</th>
              <th>Next</th>
            </tr>
          </thead>
          <tbody>
            {list.map((a) => {
              const last = a.last_run ? lastRunText(a.last_run, now) : null;
              return (
                <tr key={a.id}>
                  <td>
                    <a className="auto-row-title" href={href(at(a.id))}>
                      {a.title}
                    </a>
                    <div className="muted small">{listNote(a)}</div>
                  </td>
                  <td>
                    <button type="button" role="switch" aria-checked={a.enabled} aria-label={a.enabled ? `Turn off ${a.title}` : `Turn on ${a.title}`} className="switch" disabled={busy === a.id} onClick={() => void toggle(a)}>
                      <span className="switch-knob" />
                    </button>
                  </td>
                  <td>{whenText(a.schedules)}</td>
                  <td>
                    {last ? (
                      <span className={`auto-last tone-${last.tone}`}>
                        <span className="dot" aria-hidden="true" />
                        {last.text}
                      </span>
                    ) : (
                      <span className="muted">never run</span>
                    )}
                  </td>
                  <td>{a.next_due ? dayTime(a.next_due, now) : <span className="muted">n/a</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <EmptyState title="No automations yet">Describe something you do again and again, and Desk builds, tests and proposes an automation. Or start from a blank one.</EmptyState>
      )}
      {naming ? (
        <NameDialog
          title={naming.importing ? 'Import as…' : 'New automation'}
          confirmLabel={naming.importing ? 'Import' : 'Create'}
          {...(naming.importing ? { initial: `${naming.importing.name}-2`, hint: `An automation is already called ${naming.importing.name}. Pick another name for this one.` } : {})}
          taken={list.map((a) => a.name)}
          onClose={() => setNaming(null)}
          onConfirm={(name) => {
            const importing = naming.importing;
            setNaming(null);
            if (importing) void doImport({ ...importing, name });
            else navigate({ name: 'project', id: projectId, tab: 'automations', draft: name });
          }}
        />
      ) : null}
      {turnOn ? (
        <TurnOnDialog
          detail={turnOn}
          onClose={() => setTurnOn(null)}
          onDone={() => {
            setTurnOn(null);
            live.reload();
          }}
          onTestFirst={() => {
            setTesting(turnOn);
            setTurnOn(null);
          }}
        />
      ) : null}
      {testing ? <RunDialog target={testing} test onClose={() => setTesting(null)} onStarted={(runId) => navigate(at(testing.id, { view: 'runs', runId }))} /> : null}
    </div>
  );
}
