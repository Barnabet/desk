import { useMemo, useState } from 'react';
import type { AttentionItem } from '@desk/protocol';
import { Button } from '../components/Button';
import { CodeBlock } from '../components/CodeBlock';
import { SafeMarkdown } from '../components/SafeMarkdown';
import { clock } from '../format';
import { policyReason } from '../policyReason';
import { href } from '../router';
import { useSession, useTranscript } from '../state/session';
import { KIND_NAME, STRIP_CODE, waited } from './strips';

const SHELL = new Set(['bash', 'bash_background', 'bash_readonly']);

/** A shell command reads as `$ command`; anything else as pretty JSON. */
export function describeArgs(tool: string, args: string): { command: string | null; pretty: string } {
  try {
    const v = JSON.parse(args) as Record<string, unknown>;
    const pretty = JSON.stringify(v, null, 2);
    if (SHELL.has(tool) && typeof v.command === 'string') return { command: v.command, pretty };
    if (tool === 'skill_run' && typeof v.script === 'string') return { command: [v.skill, v.script, ...(Array.isArray(v.args) ? v.args : [])].filter(Boolean).join(' '), pretty };
    return { command: null, pretty };
  } catch {
    return { command: null, pretty: args };
  }
}

export type InspectorActions = {
  note: string;
  setNote(v: string): void;
  busy: string | null;
  answered: boolean;
  resolve(decision: 'approved' | 'denied'): void;
  answer(text: string): void;
  dismiss(): void;
  open(): void;
};

function Fact({ label, children, mono }: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div className="fact">
      <span className="fact-label">{label}</span>
      <span className={mono ? 'mono fact-value' : 'fact-value'}>{children}</span>
    </div>
  );
}

/** The selected strip in full, with the controls to act on it. */
export function Inspector(o: { item: AttentionItem; index: number; total: number; now: number; a: InspectorActions }) {
  const { item: i, a } = o;
  const s = useSession(i.project_id);
  const agentId = i.agent_id ?? '';
  const transcript = useTranscript(s, i.project_id, agentId);
  const [raw, setRaw] = useState(false);
  const [free, setFree] = useState('');
  const approval = i.ref.approval_id ? s.project?.approvals.find((x) => x.id === i.ref.approval_id) : undefined;
  const agent = s.project ? (s.project.desk?.id === agentId ? s.project.desk : s.project.threads.find((t) => t.id === agentId)) : undefined;
  const lastWords = useMemo(() => {
    for (let k = transcript.entries.length - 1; k >= 0; k--) {
      const e = transcript.entries[k]!;
      if (e.kind === 'assistant' && e.text.trim()) return e.text.trim();
    }
    return null;
  }, [transcript.entries]);
  const threadHref = i.ref.thread_id ? href({ name: 'project', id: i.project_id, tab: 'threads', threadId: i.ref.thread_id }) : null;
  const args = approval ? describeArgs(approval.tool, approval.arguments) : null;
  const why = policyReason(i.detail || approval?.reason || '');

  return (
    <article className="card inspector" aria-label={`Selected: ${KIND_NAME[i.kind].toLowerCase()}`}>
      <div className="inspector-eyebrow">
        <span className={`strip-code-badge code-${i.kind}`}>{STRIP_CODE[i.kind]}</span>
        <span className="eyebrow">{KIND_NAME[i.kind]}</span>
        <span className="muted">
          · {i.project_name} · {i.kind === 'approval' ? `paused since ${clock(i.created_at)}` : `waiting ${waited(i.created_at, o.now)}`}
        </span>
        <span className="grow" />
        <span className="muted">
          {o.index + 1} of {o.total}
        </span>
      </div>

      {i.kind === 'approval' ? (
        <>
          <h2 className="inspector-title">{i.title}</h2>
          {args ? (
            <>
              {args.command && !raw ? (
                <pre className="command" aria-label="Command">
                  <span className="command-prompt">$ </span>
                  {args.command}
                </pre>
              ) : (
                <CodeBlock code={raw ? approval!.arguments : args.pretty} language={raw ? 'raw arguments' : 'arguments'} />
              )}
              <button type="button" className="link small inspector-raw" onClick={() => setRaw((r) => !r)}>
                {raw ? 'Show readable' : 'Show raw arguments'}
              </button>
            </>
          ) : (
            <p className="muted">{s.status === 'loading' ? 'Loading the request…' : 'The request details are not available.'}</p>
          )}
          <div className="facts">
            <Fact label="TOOL" mono>
              {approval?.tool ?? '—'}
            </Fact>
            <Fact label={agent?.role === 'desk' ? 'ASKED BY' : 'THREAD'}>{threadHref ? <a href={threadHref}>{agent?.title ?? 'Thread'}</a> : 'Desk'}</Fact>
            <Fact label="BRANCH" mono>
              {agent?.git_branch ?? 'scratch workspace'}
            </Fact>
            <Fact label="PROJECT">
              <a href={href({ name: 'project', id: i.project_id, tab: 'conversation' })}>{i.project_name}</a>
            </Fact>
            <Fact label="WORKDIR" mono>
              <span title={agent?.workspace_path ?? ''}>{agent?.workspace_path?.split('/').slice(-2).join('/') ?? '—'}</span>
            </Fact>
            <Fact label="REQUESTED">{clock(i.created_at)}</Fact>
          </div>
          <div className="why">
            <div className="why-row">
              <span className="why-label">Why it's asking</span>
              <div className="why-body">
                <p>{why.text}</p>
                {why.chip ? <span className="rule-chip mono">{why.chip}</span> : null}
              </div>
            </div>
            <div className="why-row">
              <span className="why-label">What the thread said</span>
              {lastWords ? <SafeMarkdown className="why-quote" text={lastWords} /> : <p className="muted">Nothing yet.</p>}
            </div>
          </div>
          <div className="field">
            <label htmlFor="attention-note">Note to the thread (optional)</label>
            <input id="attention-note" type="text" value={a.note} onChange={(e) => a.setNote(e.target.value)} placeholder="e.g. Use npm test instead" maxLength={2000} />
          </div>
          <div className="inspector-actions">
            <Button variant="primary" pending={a.busy === 'approved'} disabled={a.busy !== null} onClick={() => a.resolve('approved')}>
              Approve once <kbd>⌘⏎</kbd>
            </Button>
            <Button pending={a.busy === 'denied'} disabled={a.busy !== null} onClick={() => a.resolve('denied')}>
              Deny <kbd>⌘⌫</kbd>
            </Button>
            <span className="grow" />
            <a className="small" href={href({ name: 'project', id: i.project_id, tab: 'settings' })}>
              Edit policy rules
            </a>
          </div>
          <p className="muted small">The thread resumes as soon as you decide. If you deny, it's told why and tries another way.</p>
        </>
      ) : i.kind === 'question' ? (
        <>
          <h2 className="inspector-title">{i.title}</h2>
          {a.answered ? (
            <p className="muted">Sent. Desk picks it up at its next step.</p>
          ) : (
            <>
              {i.ref.options?.length ? (
                <div className="choice-options">
                  {i.ref.options.map((opt, k) => (
                    <Button key={opt} variant={k === 0 ? 'primary' : 'secondary'} pending={a.busy === opt} disabled={a.busy !== null} onClick={() => a.answer(opt)}>
                      {opt}
                    </Button>
                  ))}
                </div>
              ) : null}
              <form
                className="free-answer"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (free.trim()) a.answer(free.trim());
                }}
              >
                <label htmlFor="attention-answer">Answer in your own words</label>
                <div className="free-answer-row">
                  <input id="attention-answer" type="text" value={free} onChange={(e) => setFree(e.target.value)} />
                  <Button type="submit" disabled={!free.trim() || a.busy !== null}>
                    Send
                  </Button>
                </div>
              </form>
            </>
          )}
          <div className="inspector-actions">
            <Button variant="ghost" onClick={a.open}>
              Open conversation <kbd>E</kbd>
            </Button>
          </div>
        </>
      ) : i.kind === 'paused' ? (
        <>
          <h2 className="inspector-title">{i.title}</h2>
          {i.detail ? <p>{i.detail}</p> : null}
          <div className="inspector-actions">
            <Button variant="primary" onClick={a.open}>
              Open conversation <kbd>E</kbd>
            </Button>
            <Button pending={a.busy === 'dismiss'} disabled={a.busy !== null} onClick={a.dismiss}>
              Resume
            </Button>
          </div>
          <p className="muted small">Resuming lets agents wake each other again.</p>
        </>
      ) : (
        <>
          <h2 className="inspector-title">{i.title}</h2>
          {i.detail ? (
            <div className="why">
              <div className="why-row">
                <span className="why-label">{i.kind === 'needs_you' ? 'From the report' : i.kind === 'failed' ? 'Reason' : 'What the thread said'}</span>
                {i.kind === 'stalled' ? <SafeMarkdown className="why-quote" text={i.detail} /> : <p>{i.detail}</p>}
              </div>
            </div>
          ) : null}
          <div className="inspector-actions">
            <Button variant="primary" onClick={a.open}>
              {i.kind === 'needs_you' ? 'Open conversation' : 'Open thread'} <kbd>E</kbd>
            </Button>
            <Button pending={a.busy === 'dismiss'} onClick={a.dismiss}>
              Dismiss
            </Button>
          </div>
          <p className="muted small">
            {i.kind === 'needs_you' ? 'Dismiss once it is done. Desk is not told; tell it in the conversation if it needs to know.' : 'Dismissing hides this here. The thread is not changed.'}
          </p>
        </>
      )}
    </article>
  );
}
