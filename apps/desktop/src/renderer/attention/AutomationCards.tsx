import { useEffect, useState } from 'react';
import type { AttentionItem, AutomationDetail, RunDetail } from '@desk/protocol';
import { askDeskText, runFailure } from '@desk/ui-core';
import { AskAnswer, GateAnswer } from '../automations/runs/StepPanel';
import { TurnOnDialog } from '../automations/dialogs/TurnOnDialog';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { toast, toastError } from '../components/Toast';
import { navigate } from '../router';
import type { InspectorActions } from './Inspector';

export type AutomationItemData = { detail: AutomationDetail | null; run: RunDetail | null; missing: boolean };

/** An item's automation and run, when it names them (both null until loaded; `missing` once either is gone). */
export function useAutomationItem(i: AttentionItem): AutomationItemData {
  const [detail, setDetail] = useState<AutomationDetail | null>(null);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const automationId = i.ref.automation_id;
  const runId = i.ref.run_id;
  useEffect(() => {
    let live = true;
    if (automationId) {
      call('automations.get', { id: automationId })
        .then((d) => live && setDetail(d))
        .catch(() => live && setMissing(true));
    }
    if (runId) {
      call('automations.getRun', { runId })
        .then((r) => live && setRun(r))
        .catch(() => live && setMissing(true));
    }
    return () => {
      live = false;
    };
  }, [automationId, runId]);
  return { detail, run, missing };
}

/** The inspector's body for the four automation kinds (spec §8.4). */
export function AutomationCard({ item: i, a, auto }: { item: AttentionItem; a: InspectorActions; auto: AutomationItemData }) {
  const { detail, run } = auto;
  const [turningOn, setTurningOn] = useState(false);
  const [asking, setAsking] = useState(false);
  const row = run && i.ref.step_id ? run.steps.find((s) => s.step_id === i.ref.step_id) : undefined;
  const stepTitle = row ? (run?.definition.steps.find((s) => s.id === row.step_id)?.title ?? row.step_id) : null;
  const gone = auto.missing ? <p className="muted">It could not be loaded: the automation or its run may have been deleted.</p> : null;

  const askFix = async () => {
    if (!run) return;
    setAsking(true);
    try {
      const f = runFailure(run);
      await call('projects.send', { id: i.project_id, text: askDeskText(run, f.stepTitle, f.error) });
      toast({ tone: 'info', message: 'Sent to Desk.', action: { label: 'Open conversation', run: () => navigate({ name: 'project', id: i.project_id, tab: 'conversation' }) } });
    } catch (err) {
      toastError(err);
    } finally {
      setAsking(false);
    }
  };

  switch (i.kind) {
    case 'automation_ask':
      return (
        <>
          <h2 className="inspector-title">{run && stepTitle ? `${run.automation_title} · ${stepTitle}` : i.title}</h2>
          {gone ??
            (!run || !row ? (
              <p className="muted">Loading the step…</p>
            ) : row.status !== 'waiting' ? (
              <p className="muted">It has been answered.</p>
            ) : row.question ? (
              <AskAnswer run={run} row={row} />
            ) : row.gate ? (
              <GateAnswer run={run} row={row} grantsSuspended={detail?.grants_suspended ?? true} />
            ) : null)}
          <div className="inspector-actions">
            <Button variant="ghost" onClick={a.open}>
              Open run <kbd>E</kbd>
            </Button>
          </div>
        </>
      );
    case 'automation_failed':
      return (
        <>
          <h2 className="inspector-title">{i.title}</h2>
          {i.detail ? <pre className="auto-error">{i.detail}</pre> : null}
          {gone}
          <div className="inspector-actions">
            <Button variant="primary" onClick={a.open}>
              Open run <kbd>E</kbd>
            </Button>
            <Button pending={asking} disabled={!run} onClick={() => void askFix()}>
              Ask Desk to fix
            </Button>
            <Button pending={a.busy === 'dismiss'} disabled={a.busy !== null} onClick={a.dismiss}>
              Dismiss
            </Button>
          </div>
          <p className="muted small">Dismissing hides this here. The automation is not changed, and its next run starts on time.</p>
        </>
      );
    case 'automation_enable_request':
      return (
        <>
          <h2 className="inspector-title">{i.title}</h2>
          {i.detail ? <p>{i.detail}</p> : null}
          {gone}
          <div className="inspector-actions">
            <Button variant="primary" disabled={!detail} onClick={() => setTurningOn(true)}>
              Turn on…
            </Button>
            <Button variant="ghost" onClick={a.open}>
              Open automation <kbd>E</kbd>
            </Button>
            <Button pending={a.busy === 'dismiss'} disabled={a.busy !== null} onClick={a.dismiss}>
              Dismiss
            </Button>
          </div>
          <p className="muted small">Only you turn automations on. Dismissing leaves it off; Desk is not told.</p>
          {turningOn && detail ? (
            <TurnOnDialog
              detail={detail}
              onClose={() => setTurningOn(false)}
              onDone={() => setTurningOn(false)}
              onTestFirst={() => {
                setTurningOn(false);
                a.open();
              }}
            />
          ) : null}
        </>
      );
    default:
      return (
        <>
          <h2 className="inspector-title">{i.title}</h2>
          {i.detail ? <p>{i.detail}</p> : null}
          <div className="inspector-actions">
            <Button variant="primary" onClick={a.open}>
              Review changes <kbd>E</kbd>
            </Button>
            <Button pending={a.busy === 'dismiss'} disabled={a.busy !== null} onClick={a.dismiss}>
              Dismiss
            </Button>
          </div>
          <p className="muted small">Until you keep them on its Grants tab, its runs ask you for everything.</p>
        </>
      );
  }
}
