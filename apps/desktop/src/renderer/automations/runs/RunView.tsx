import { useMemo, useState } from 'react';
import type { AutomationDetail, RunDetail, StepRunInfo } from '@desk/protocol';
import { agentActivity, dayTime, focusStep, href, runGraph, runStatusText, runTook, stepLook, triggerText, type GraphSelection } from '@desk/ui-core';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { EmptyState } from '../../components/EmptyState';
import { toastError } from '../../components/Toast';
import { useNow } from '../../state/now';
import type { SessionState } from '../../state/session';
import { useRun } from '../data';
import { GraphCanvas } from '../design/GraphCanvas';

/** One run (spec §8.3, view A): the graph lit with each step's state, the header's Open folder and Cancel run, and a side panel. */
export function RunView(o: { projectId: string; s: SessionState; detail: AutomationDetail; runId: string }) {
  const live = useRun(o.s, o.runId);
  const now = useNow();
  const [picked, setPicked] = useState<GraphSelection | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const run = live.value;
  const activity = useMemo(() => {
    const out: Record<string, string | null> = {};
    for (const st of run?.steps ?? []) if (st.agent_id && st.status === 'running') out[st.step_id] = agentActivity(o.s.events, st.agent_id);
    return out;
  }, [run?.steps, o.s.events]);
  const graph = useMemo(() => (run ? runGraph(run, now, activity) : undefined), [run, now, activity]);
  const back = href({ name: 'project', id: o.projectId, tab: 'automations', automationId: o.detail.id, view: 'runs' });
  if (live.status === 'missing') {
    return (
      <EmptyState title="This run is gone" action={<a href={back}>All runs</a>}>
        Desk keeps each automation's last 20 runs or 30 days of runs.
      </EmptyState>
    );
  }
  if (!run || !graph) return <p className="muted auto-loading">Loading…</p>;
  const focus = focusStep(run);
  const sel: GraphSelection = picked ?? (focus ? { kind: 'step', id: focus } : { kind: 'start' });
  const st = runStatusText(run);
  const going = run.status === 'running' || run.status === 'waiting';
  const reveal = () => void call('app.revealPath', { runId: run.id }).catch(toastError);
  const cancel = async () => {
    setCancelling(false);
    try {
      await call('automations.cancelRun', { runId: run.id });
    } catch (err) {
      toastError(err);
    }
  };
  return (
    <div className="auto-run">
      <div className="auto-run-main">
        <header className="auto-run-head">
          <a className="muted small" href={back}>
            Runs ›
          </a>
          <h2>{`Run #${run.number}`}</h2>
          <span className="muted small">{`${triggerText(run)} · ${dayTime(run.started_at, now)} · ${runTook(run, now)}`}</span>
          <span className={`auto-last tone-${st.tone}`}>
            <span className="dot" aria-hidden="true" />
            {st.text}
          </span>
          {run.version !== o.detail.version ? <span className="auto-badge">{`v${run.version}`}</span> : null}
          <span className="grow" />
          <Button size="sm" onClick={reveal}>
            Open folder
          </Button>
          {going ? (
            <Button size="sm" variant="danger" onClick={() => setCancelling(true)}>
              Cancel run…
            </Button>
          ) : null}
        </header>
        <GraphCanvas def={run.definition} layout={o.detail.layout} startLabel={triggerText(run)} selection={sel} onSelect={(x) => setPicked(x.kind === 'none' ? { kind: 'start' } : x)} run={graph} editable={false} />
      </div>
      {sel.kind === 'step' ? <StepSide run={run} stepId={sel.id} now={now} /> : <RunSide run={run} />}
      {cancelling ? (
        <ConfirmDialog title={`Cancel run #${run.number}?`} confirmLabel="Cancel run" danger onConfirm={() => void cancel()} onCancel={() => setCancelling(false)}>
          Its running steps stop: agents are stopped and scripts are killed. Steps that finished keep their results.
        </ConfirmDialog>
      ) : null}
    </div>
  );
}

/** The run itself: its inputs, summary or reason. */
function RunSide({ run }: { run: RunDetail }) {
  return (
    <aside className="auto-panel" aria-label={`Run #${run.number}`}>
      <p className="eyebrow">Run</p>
      <h2>{run.automation_title}</h2>
      {run.summary ? <p>{run.summary}</p> : null}
      {run.reason ? <p className="auto-issues">{run.reason}</p> : null}
      <h3 className="auto-sub">Inputs</h3>
      {Object.keys(run.inputs).length ? (
        <dl className="auto-facts">
          {Object.entries(run.inputs).map(([k, v]) => (
            <div key={k}>
              <dt className="mono">{k}</dt>
              <dd>{String(v)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="muted small">None.</p>
      )}
    </aside>
  );
}

/** A step, briefly (Task 15 replaces this with the full step panel). */
function StepSide({ run, stepId, now }: { run: RunDetail; stepId: string; now: number }) {
  const step = run.definition.steps.find((s) => s.id === stepId);
  const row: StepRunInfo | undefined = run.steps.find((s) => s.step_id === stepId);
  const look = stepLook(row, now);
  return (
    <aside className="auto-panel" aria-label={step?.title ?? stepId}>
      <p className="eyebrow">{look.badge}</p>
      <h2>{step?.title ?? stepId}</h2>
      {row?.summary ? <p>{row.summary}</p> : null}
      {row?.error ? <p className="auto-issues">{row.error}</p> : null}
    </aside>
  );
}
