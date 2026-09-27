import { useEffect, useMemo, useState } from 'react';
import type { ApprovalRow } from '@desk/client';
import type { RunDetail, Step, StepRunInfo, WorkspaceEntry } from '@desk/protocol';
import { agentActivity, agentTokens, askDeskText, canStopStep, clock, outputText, policyReason, relRunPath, STEP_KIND_LABEL, stepLook, tokens } from '@desk/ui-core';
import { describeArgs } from '../../attention/Inspector';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { CodeBlock } from '../../components/CodeBlock';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { Field } from '../../components/Field';
import { SafeMarkdown } from '../../components/SafeMarkdown';
import { toast, toastError } from '../../components/Toast';
import { navigate } from '../../router';
import { useNow } from '../../state/now';
import { useTranscript, type SessionState } from '../../state/session';
import { RunFileList } from './RunFileList';
import { StepTranscript } from './StepTranscript';

type Props = { projectId: string; s: SessionState; run: RunDetail; stepId: string; grantsSuspended: boolean };

/** The selected step of a run (spec §8.3): what it is doing, what it needs from you, or what it produced. */
export function StepPanel(o: Props) {
  const now = useNow();
  const step = o.run.definition.steps.find((x) => x.id === o.stepId);
  const row = o.run.steps.find((x) => x.step_id === o.stepId);
  const activity = row?.agent_id && row.status === 'running' ? agentActivity(o.s.events, row.agent_id) : null;
  if (!step) return null;
  const look = stepLook(row, now, activity);
  const done = row?.status === 'succeeded' || row?.status === 'rejected';
  return (
    <aside className="auto-panel" aria-label={step.title}>
      <p className="eyebrow">{`${STEP_KIND_LABEL[step.kind]} · ${look.badge}`}</p>
      <h2>{step.title}</h2>
      {row && row.attempt > 1 ? <p className="muted small">{`Attempt ${row.attempt}`}</p> : null}
      {row?.status === 'waiting' && row.question ? <AskAnswer run={o.run} row={row} /> : null}
      {row?.status === 'waiting' && row.gate ? <GateAnswer run={o.run} row={row} grantsSuspended={o.grantsSuspended} /> : null}
      {row?.agent_id ? <AgentPart {...o} step={step} row={row} /> : null}
      {row?.status === 'waiting' && row.resume_at ? <p>{`Waits until ${clock(row.resume_at)}.`}</p> : null}
      {row?.child_run_id ? <p className="muted small">{`Runs another automation (run ${row.child_run_id}).`}</p> : null}
      {row?.status === 'failed' ? <Failed {...o} step={step} row={row} /> : null}
      {done && row ? <Results row={row} /> : null}
      {step.kind === 'script' && row && row.attempt > 0 ? <ScriptLog runId={o.run.id} stepId={step.id} live={row.status === 'running'} /> : null}
      {row && row.attempt > 0 && row.status !== 'pending' ? <StepFiles runId={o.run.id} stepId={step.id} status={row.status} /> : null}
      {!row || row.attempt === 0 ? <p className="muted">Not reached yet.</p> : null}
      {row?.status === 'skipped' ? <p className="muted">Skipped: none of the edges into it fired.</p> : null}
    </aside>
  );
}

export function AskAnswer({ run, row }: { run: RunDetail; row: StepRunInfo }) {
  const q = row.question!;
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const answer = async (decision: 'approve' | 'reject') => {
    setBusy(decision);
    try {
      await call('automations.answer', { runId: run.id, stepId: row.step_id, req: { decision, ...(note.trim() ? { note: note.trim() } : {}) } });
    } catch (err) {
      toastError(err);
      setBusy(null);
    }
  };
  const files = q.files.flatMap((f) => {
    const path = relRunPath(f, run.id);
    return path ? [{ path, label: path.split('/').at(-1)! }] : [];
  });
  return (
    <section className="auto-card" aria-label="Your answer">
      <SafeMarkdown text={q.text} />
      {files.length ? <RunFileList runId={run.id} files={files} /> : null}
      <Field id="answer-note" label="Note (optional)">
        <textarea id="answer-note" className="textarea" rows={2} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
      <div className="auto-inline">
        <Button variant="primary" pending={busy === 'approve'} disabled={busy !== null} onClick={() => void answer('approve')}>
          {q.approve_label ?? 'Approve'}
        </Button>
        <Button pending={busy === 'reject'} disabled={busy !== null} onClick={() => void answer('reject')}>
          {q.reject_label ?? 'Reject'}
        </Button>
      </div>
      <p className="muted small">Rejecting takes the route rejected. Later steps can read your note.</p>
    </section>
  );
}

export function GateAnswer({ run, row, grantsSuspended }: { run: RunDetail; row: StepRunInfo; grantsSuspended: boolean }) {
  const gate = row.gate!;
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const answer = async (decision: 'approve' | 'reject', remember = false) => {
    setBusy(remember ? 'remember' : decision);
    try {
      await call('automations.answer', { runId: run.id, stepId: row.step_id, req: { decision, ...(note.trim() ? { note: note.trim() } : {}), ...(remember ? { remember: true } : {}) } });
    } catch (err) {
      toastError(err);
      setBusy(null);
    }
  };
  const why = policyReason(gate.reason);
  return (
    <section className="auto-card" aria-label="Approval">
      <p>{`Its script wants to run ${gate.tool}:`}</p>
      <pre className="command">
        <span className="command-prompt">$ </span>
        {gate.subject}
      </pre>
      <p className="muted small">{why.text}</p>
      <Field id="gate-note" label="Note (optional)">
        <input id="gate-note" className="input" maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
      <div className="auto-inline">
        <Button variant="primary" pending={busy === 'approve'} disabled={busy !== null} onClick={() => void answer('approve')}>
          Approve
        </Button>
        {grantsSuspended ? null : (
          <Button pending={busy === 'remember'} disabled={busy !== null} onClick={() => void answer('approve', true)}>
            Approve and remember
          </Button>
        )}
        <Button pending={busy === 'reject'} disabled={busy !== null} onClick={() => void answer('reject')}>
          Reject
        </Button>
      </div>
      <p className="muted small">
        {grantsSuspended ? 'Its grants are suspended until you keep them, so an approval is for this run only.' : 'Remember adds a grant, so later runs run this script without asking.'}
      </p>
    </section>
  );
}

function AgentApproval({ approval, grantsSuspended }: { approval: ApprovalRow; grantsSuspended: boolean }) {
  const [busy, setBusy] = useState<string | null>(null);
  const args = describeArgs(approval.tool, approval.arguments);
  const resolve = async (decision: 'approved' | 'denied', remember = false) => {
    setBusy(remember ? 'remember' : decision);
    try {
      await call('approvals.resolve', { id: approval.id, decision, ...(remember ? { remember: true } : {}) });
    } catch (err) {
      toastError(err);
      setBusy(null);
    }
  };
  return (
    <section className="auto-card" aria-label={`Approval: ${approval.tool}`}>
      {args.command ? (
        <pre className="command">
          <span className="command-prompt">$ </span>
          {args.command}
        </pre>
      ) : (
        <CodeBlock code={args.pretty} language={approval.tool} />
      )}
      <div className="auto-inline">
        <Button variant="primary" size="sm" pending={busy === 'approved'} disabled={busy !== null} onClick={() => void resolve('approved')}>
          Approve
        </Button>
        {grantsSuspended ? null : (
          <Button size="sm" pending={busy === 'remember'} disabled={busy !== null} onClick={() => void resolve('approved', true)}>
            Approve and remember
          </Button>
        )}
        <Button size="sm" pending={busy === 'denied'} disabled={busy !== null} onClick={() => void resolve('denied')}>
          Deny
        </Button>
      </div>
    </section>
  );
}

function AgentPart(o: Props & { step: Step; row: StepRunInfo }) {
  const agentId = o.row.agent_id!;
  const transcript = useTranscript(o.s, o.projectId, agentId);
  const last = useMemo(() => {
    for (let i = transcript.entries.length - 1; i >= 0; i--) {
      const e = transcript.entries[i]!;
      if (e.kind === 'assistant' && e.text.trim()) return e.text.trim();
    }
    return null;
  }, [transcript.entries]);
  const used = useMemo(() => agentTokens(o.s.events, agentId), [o.s.events, agentId]);
  const approvals = (o.s.project?.approvals ?? []).filter((a) => a.agent_id === agentId);
  const [open, setOpen] = useState(false);
  const [stopping, setStopping] = useState(false);
  const going = o.row.status === 'running' || o.row.status === 'waiting';
  const stop = async () => {
    setStopping(false);
    try {
      await call('automations.stopStep', { runId: o.run.id, stepId: o.row.step_id });
    } catch (err) {
      toastError(err);
    }
  };
  return (
    <>
      {approvals.map((a) => (
        <AgentApproval key={a.id} approval={a} grantsSuspended={o.grantsSuspended} />
      ))}
      {going ? (
        <section aria-label="Live output" className="auto-live">
          {last ? <SafeMarkdown text={last} /> : <p className="muted small">Starting…</p>}
        </section>
      ) : null}
      <p className="muted small">{`${tokens(used)} tokens`}</p>
      <div className="auto-inline">
        <Button size="sm" onClick={() => setOpen(true)}>
          Open transcript
        </Button>
        {canStopStep(o.run, o.row) ? (
          <Button size="sm" variant="danger" onClick={() => setStopping(true)}>
            Stop step…
          </Button>
        ) : null}
      </div>
      {open ? <StepTranscript projectId={o.projectId} s={o.s} agentId={agentId} title={o.step.title} onClose={() => setOpen(false)} /> : null}
      {stopping ? (
        <ConfirmDialog title="Stop this step?" confirmLabel="Stop step" danger onConfirm={() => void stop()} onCancel={() => setStopping(false)}>
          Its agent stops and the step fails. The run then does what the step's "If it fails" says.
        </ConfirmDialog>
      ) : null}
    </>
  );
}

function Failed(o: Props & { step: Step; row: StepRunInfo }) {
  const [sending, setSending] = useState(false);
  const error = o.row.error ?? o.run.reason ?? 'It failed.';
  const ask = async () => {
    setSending(true);
    try {
      await call('projects.send', { id: o.projectId, text: askDeskText(o.run, o.step.title, error) });
      toast({ tone: 'info', message: 'Sent to Desk.', action: { label: 'Open conversation', run: () => navigate({ name: 'project', id: o.projectId, tab: 'conversation' }) } });
    } catch (err) {
      toastError(err);
    } finally {
      setSending(false);
    }
  };
  return (
    <section className="auto-card" aria-label="Error">
      <pre className="auto-error">{error}</pre>
      <div>
        <Button variant="primary" size="sm" pending={sending} onClick={() => void ask()}>
          Ask Desk to fix
        </Button>
      </div>
    </section>
  );
}

function Results({ row }: { row: StepRunInfo }) {
  const outputs = Object.entries(row.outputs);
  return (
    <section aria-label="Results" className="auto-results">
      {row.summary ? <SafeMarkdown text={row.summary} /> : null}
      <dl className="auto-facts">
        {row.route ? (
          <div>
            <dt>route</dt>
            <dd className="mono">{row.route}</dd>
          </div>
        ) : null}
        {row.note ? (
          <div>
            <dt>your note</dt>
            <dd>{row.note}</dd>
          </div>
        ) : null}
        {outputs.map(([k, v]) => (
          <div key={k}>
            <dt className="mono">{k}</dt>
            <dd className="auto-output">{outputText(v)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function ScriptLog({ runId, stepId, live }: { runId: string; stepId: string; live: boolean }) {
  const [log, setLog] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () =>
      call('automations.log', { runId, stepId })
        .then((t) => alive && setLog(t))
        .catch(() => alive && setLog(''));
    void load();
    const timer = live ? setInterval(() => void load(), 2000) : undefined;
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [runId, stepId, live]);
  if (log === null) return null;
  return (
    <details className="auto-log" open={live || undefined}>
      <summary>Log</summary>
      {log ? <CodeBlock code={log.split('\n').slice(-400).join('\n')} language="log" /> : <p className="muted small">Nothing logged.</p>}
    </details>
  );
}

function StepFiles({ runId, stepId, status }: { runId: string; stepId: string; status: string }) {
  const [entries, setEntries] = useState<WorkspaceEntry[] | null>(null);
  useEffect(() => {
    let alive = true;
    call('automations.files', { runId, path: `steps/${stepId}` })
      .then((list) => alive && setEntries(list))
      .catch(() => alive && setEntries([]));
    return () => {
      alive = false;
    };
  }, [runId, stepId, status]);
  const files = (entries ?? []).filter((e) => e.type === 'file');
  const reveal = () => void call('app.revealPath', { runId, stepId }).catch(toastError);
  return (
    <section aria-label="Files" className="auto-results">
      <div className="auto-inline">
        <h3 className="auto-sub">Files</h3>
        <span className="grow" />
        <Button size="sm" variant="ghost" onClick={reveal}>
          Open folder
        </Button>
      </div>
      {files.length ? <RunFileList runId={runId} files={files.map((f) => ({ path: f.path, label: f.path.replace(`steps/${stepId}/`, '') }))} /> : entries ? <p className="muted small">No files.</p> : null}
    </section>
  );
}
