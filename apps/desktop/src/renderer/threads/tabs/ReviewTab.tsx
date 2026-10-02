import { useEffect, useState } from 'react';
import type { ThreadReview, ThreadView } from '@desk/client';
import { clock, criteriaDraft, href, linesOf, reviewView, shortCommit, since, type FindingView, type ReviewView } from '@desk/ui-core';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { describeError, toast, toastError } from '../../components/Toast';

type State = { status: 'loading' } | { status: 'ready'; review: ThreadReview } | { status: 'error'; message: string };
type Mode = 'review' | 'accept' | 'limits' | 'changes';

const TITLES: Record<Mode, string> = {
  review: 'Request a review',
  accept: 'Accept this submission',
  limits: 'Accept with limitations',
  changes: 'Request changes',
};

function Finding({ f, onWaive, busy }: { f: FindingView; onWaive: ((reason: string) => void) | null; busy: boolean }) {
  const [waiving, setWaiving] = useState(false);
  const [reason, setReason] = useState('');
  return (
    <li className="review-item">
      <div className="review-item-head">
        <strong>{f.title}</strong>
        {f.blocking ? <span className="chip chip-fail">Blocking</span> : null}
        {f.outcome ? <span className="small muted">{f.outcome}</span> : null}
        {onWaive && !waiving ? (
          <Button size="sm" variant="ghost" onClick={() => setWaiving(true)}>
            Waive
          </Button>
        ) : null}
      </div>
      {f.detail ? <p className="review-text">{f.detail}</p> : null}
      {f.reproducer ? <pre className="review-repro" aria-label="Reproducer">{f.reproducer}</pre> : null}
      {f.raisedOn ? <span className="small muted">Raised on submission {f.raisedOn}</span> : null}
      {onWaive && waiving ? (
        <div className="actions">
          <input className="input" aria-label="Why waive it" placeholder="Why it can stay (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <Button size="sm" pending={busy} onClick={() => onWaive(reason)}>
            Waive finding
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setWaiving(false)}>
            Cancel
          </Button>
        </div>
      ) : null}
    </li>
  );
}

/**
 * A thread's submissions, reviews and findings, and the user's decisions about them (reviews and acceptance spec §7):
 * request a review, accept (with limitations) or request changes, and waive a finding.
 */
export function ReviewTab({ thread, threads, version, now }: { thread: ThreadView; threads: ThreadView[]; version: string; now: number }) {
  const [s, setS] = useState<State>({ status: 'loading' });
  const [mode, setMode] = useState<Mode | null>(null);
  const [text, setText] = useState('');
  const [focus, setFocus] = useState('');
  const [same, setSame] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    call('threads.review', { id: thread.id })
      .then((review) => live && setS({ status: 'ready', review }))
      .catch((err) => live && setS({ status: 'error', message: describeError(err).message }));
    return () => {
      live = false;
    };
  }, [thread.id, version, tick]);
  useEffect(() => setMode(null), [thread.id]);

  if (s.status === 'loading') return <p className="muted tab-body">Loading the review…</p>;
  if (s.status === 'error') return <EmptyState title="Couldn't load the review">{s.message}</EmptyState>;
  const v: ReviewView = reviewView(s.review);
  const name = (id: string) => threads.find((t) => t.id === id)?.title ?? 'a thread';
  const link = (id: string) => href({ name: 'project', id: thread.project_id, tab: 'threads', threadId: id });

  if (v.reviewing) {
    return (
      <div className="tab-body review-tab">
        <p>
          This thread reviews {v.reviewing.submissionSeq ? `submission ${v.reviewing.submissionSeq} of ` : ''}
          <a href={link(v.reviewing.builderId)}>{name(v.reviewing.builderId)}</a>
          {v.reviewing.filed ? ', and has filed its review.' : '.'} Its findings and verdict are on that thread's Review tab.
        </p>
      </div>
    );
  }
  if (!v.current) {
    return <EmptyState title="Nothing submitted yet">A thread submits its work when it finishes: its commit, the files it published and what it says it met.</EmptyState>;
  }
  const current = v.current;
  const archived = !!thread.archived_at;

  const open = (m: Mode) => {
    setMode(m);
    setError(null);
    setFocus('');
    setSame(true);
    setText(m === 'review' ? criteriaDraft(v) : '');
  };
  const done = (message: string) => {
    setMode(null);
    setTick((n) => n + 1);
    toast({ tone: 'info', message });
  };
  const submit = async () => {
    if (!mode) return;
    setBusy(mode);
    setError(null);
    try {
      if (mode === 'review') {
        const criteria = linesOf(text);
        if (!criteria.length) throw new Error('Write at least one acceptance criterion.');
        const out = await call('threads.requestReview', {
          id: thread.id,
          req: { criteria, ...(focus.trim() ? { focus: focus.trim() } : {}), ...(v.reReviewer && same ? { reviewer_id: v.reReviewer } : {}) },
        });
        done(out.reopened ? 'Asked the same reviewer to look again.' : 'A reviewer thread has started.');
      } else {
        const limitations = mode === 'limits' ? linesOf(text) : [];
        if (mode === 'limits' && !limitations.length) throw new Error('Write at least one limitation.');
        const decision = mode === 'accept' ? 'accepted' : mode === 'limits' ? 'accepted_with_limitations' : 'changes_requested';
        await call('threads.accept', { id: thread.id, req: { decision, ...(limitations.length ? { limitations } : {}), ...(mode !== 'limits' && text.trim() ? { note: text.trim() } : {}) } });
        done(mode === 'changes' ? 'Sent the changes back to the thread.' : `Accepted submission ${current.seq}.`);
      }
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setBusy(null);
    }
  };
  const waive = async (f: FindingView, reason: string) => {
    setBusy(f.id);
    try {
      await call('threads.waiveFinding', { findingId: f.id, req: reason.trim() ? { reason: reason.trim() } : {} });
      done('Finding waived.');
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(null);
    }
  };
  const blocked = v.blocking > 0 && (mode === 'accept' || mode === 'limits');

  return (
    <div className="tab-body review-tab">
      <p className="review-state">
        {v.chip ? <span className={`chip chip-${v.chip.tone}`}>{v.chip.label}</span> : <span className="chip chip-idle">Not reviewed</span>}
        <span>
          Submission {current.seq}
          {current.commit ? (
            <>
              {' '}
              · commit <span className="mono">{shortCommit(current.commit)}</span>
            </>
          ) : null}
          {current.artifacts.length ? ` · ${current.artifacts.length} file${current.artifacts.length === 1 ? '' : 's'}` : ''} · {since(current.createdAt, now)}
        </span>
      </p>
      {!archived && !mode ? (
        <div className="actions">
          <Button size="sm" onClick={() => open('review')}>
            {v.reReviewer ? 'Request a re-review' : 'Request a review'}
          </Button>
          <Button size="sm" onClick={() => open('accept')}>
            Accept
          </Button>
          <Button size="sm" onClick={() => open('limits')}>
            Accept with limitations
          </Button>
          <Button size="sm" onClick={() => open('changes')}>
            Request changes
          </Button>
        </div>
      ) : null}
      {mode ? (
        <div className="review-form" role="group" aria-label={TITLES[mode]}>
          <strong>{TITLES[mode]}</strong>
          {mode === 'review' ? (
            <>
              <div className="field">
                <label htmlFor="review-criteria">Acceptance criteria, one per line</label>
                <textarea id="review-criteria" className="textarea" value={text} onChange={(e) => setText(e.target.value)} />
                <p className="field-hint">A reviewer thread checks the submitted work against these without seeing the thread's own report first.</p>
              </div>
              <div className="field">
                <label htmlFor="review-focus">Focus (optional)</label>
                <input id="review-focus" className="input" value={focus} onChange={(e) => setFocus(e.target.value)} />
              </div>
              {v.reReviewer ? (
                <label className="small">
                  <input type="checkbox" checked={same} onChange={(e) => setSame(e.target.checked)} /> Ask {name(v.reReviewer)} again
                </label>
              ) : null}
            </>
          ) : mode === 'limits' ? (
            <div className="field">
              <label htmlFor="review-limits">Limitations, one per line</label>
              <textarea id="review-limits" className="textarea" value={text} onChange={(e) => setText(e.target.value)} />
            </div>
          ) : (
            <div className="field">
              <label htmlFor="review-note">{mode === 'changes' ? 'What needs to change' : 'Note (optional)'}</label>
              <textarea id="review-note" className="textarea" value={text} onChange={(e) => setText(e.target.value)} />
              {mode === 'changes' && v.open.length ? <p className="field-hint">The open findings go to the thread with your note.</p> : null}
            </div>
          )}
          {blocked ? <p className="field-error">Waive the {v.blocking} open blocking finding{v.blocking === 1 ? '' : 's'} below first, or request changes.</p> : null}
          {error ? <p className="field-error">{error}</p> : null}
          <div className="actions">
            <Button size="sm" variant="primary" pending={busy === mode} disabled={blocked} onClick={() => void submit()}>
              {TITLES[mode]}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setMode(null)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      <h3>Open findings ({v.open.length})</h3>
      {v.open.length ? (
        <ul className="review-list">
          {v.open.map((f) => (
            <Finding key={f.id} f={f} busy={busy === f.id} onWaive={archived ? null : (reason) => void waive(f, reason)} />
          ))}
        </ul>
      ) : (
        <p className="muted">None.</p>
      )}

      <h3>Reviews</h3>
      {v.reviews.length ? (
        <ul className="review-list">
          {v.reviews.map((r) => (
            <li key={r.id} className="review-item">
              <div className="review-item-head">
                <strong>
                  <a href={link(r.reviewerId)}>{name(r.reviewerId)}</a>
                  {r.submissionSeq ? ` · submission ${r.submissionSeq}` : ''}
                </strong>
                <span className={`chip chip-${r.chip.tone}`}>{r.chip.label}</span>
                <span className="small muted">Requested by {r.requestedBy === 'user' ? 'you' : 'Desk'}</span>
              </div>
              {r.requirements.length ? (
                <ul className="review-req">
                  {r.requirements.map((q, i) => (
                    <li key={i}>
                      <span className={q.ok === true ? 'met-yes' : q.ok === false ? 'met-no' : 'muted'}>{q.met}</span>: {q.criterion}
                      {q.note ? <span className="muted"> · {q.note}</span> : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <ul className="review-req">
                  {r.criteria.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              )}
              {r.notChecked.length ? <span className="small muted">Not checked: {r.notChecked.join('; ')}</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No review yet.</p>
      )}

      <h3>Submissions</h3>
      <ul className="review-list">
        {[current, ...v.earlier].map((sub) => (
          <li key={sub.id} className="review-item">
            <div className="review-item-head">
              <strong>
                Submission {sub.seq}
                {sub.commit ? (
                  <>
                    {' '}
                    · <span className="mono">{shortCommit(sub.commit)}</span>
                  </>
                ) : null}
              </strong>
              {sub.tag ? <span className={`chip ${sub.tag === 'Superseded' ? 'chip-idle' : 'chip-done'}`}>{sub.tag}</span> : null}
              <span className="small muted">{clock(sub.createdAt)}</span>
            </div>
            {sub.claims.length ? (
              <>
                <span className="small">It says it met:</span>
                <ul className="review-req">
                  {sub.claims.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              </>
            ) : null}
            {sub.limitations.length ? (
              <>
                <span className="small">Limitations it reported:</span>
                <ul className="review-req">
                  {sub.limitations.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              </>
            ) : null}
            {sub.evidence ? <p className="review-text small">How it checked: {sub.evidence}</p> : null}
            {sub.artifacts.length ? <span className="small mono">{sub.artifacts.map((a) => a.path).join(' · ')}</span> : null}
          </li>
        ))}
      </ul>

      {v.resolved.length ? (
        <>
          <h3>Resolved findings ({v.resolved.length})</h3>
          <ul className="review-list">
            {v.resolved.map((f) => (
              <Finding key={f.id} f={f} busy={false} onWaive={null} />
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}
