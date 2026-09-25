import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import type { MessagesState, ToolCallView, TranscriptEntry } from '@desk/client';
import { clip } from '@desk/protocol';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { CodeBlock } from '../components/CodeBlock';
import { ImageThumbs } from '../components/ImageThumbs';
import { SafeMarkdown } from '../components/SafeMarkdown';
import { toastError } from '../components/Toast';
import { ToolGroup, ToolStatus } from '../components/ToolGroup';
import { clock } from '../format';
import { policyReason } from '../policyReason';
import { href } from '../router';
import { stopText, type NarrativeRow, type Stop } from './route';

export type Depth = 'narrative' | 'steps';

const MAX_OUTPUT = 6000;

function pretty(args: string): string {
  try {
    return JSON.stringify(JSON.parse(args), null, 2);
  } catch {
    return args;
  }
}

function ToolCallFull({ c }: { c: ToolCallView }) {
  const [all, setAll] = useState(false);
  const out = c.content ?? '';
  return (
    <div className="tr-call">
      <div className="tr-call-head">
        <span className="mono grow">{c.name}</span>
        <ToolStatus status={c.status} />
      </div>
      <CodeBlock code={pretty(c.arguments)} language="arguments" />
      {out ? (
        <>
          <CodeBlock code={all || out.length <= MAX_OUTPUT ? out : `${out.slice(0, MAX_OUTPUT)}\n…`} language={c.status === 'ok' ? 'result' : c.status} />
          {out.length > MAX_OUTPUT && !all ? (
            <button type="button" className="link small" onClick={() => setAll(true)}>
              Show all {out.length.toLocaleString()} characters
            </button>
          ) : null}
        </>
      ) : null}
      {c.images?.length ? <ImageThumbs images={c.images} /> : null}
    </div>
  );
}

/** One entry at full detail (the Every-step depth). */
function EntryFull({ e, projectId }: { e: TranscriptEntry; projectId: string }) {
  switch (e.kind) {
    case 'brief':
      return (
        <>
          <span className="eyebrow">Brief from Desk · {clock(e.ts)}</span>
          <SafeMarkdown text={e.text} />
        </>
      );
    case 'status':
      return (
        <span className="muted small">
          Status → {e.status}
          {e.reason ? ` · ${e.reason}` : ''} · {clock(e.ts)}
        </span>
      );
    case 'assistant':
      return (
        <>
          <SafeMarkdown className="md-voice tr-voice" text={e.text} />
          {e.interrupted ? <span className="muted small">Cut off by an error.</span> : null}
        </>
      );
    case 'tools':
      return (
        <div className="tr-calls">
          {e.calls.map((c) => (
            <ToolCallFull key={c.id} c={c} />
          ))}
        </div>
      );
    case 'compacted':
      return null;
    default:
      return <EntrySummary e={e} projectId={projectId} />;
  }
}

/** The body shared by both depths for entries that are their own stop. */
function EntrySummary({ e, projectId }: { e: TranscriptEntry; projectId: string }) {
  switch (e.kind) {
    case 'brief':
      return <SafeMarkdown text={e.text} />;
    case 'detour':
      return (
        <p className="tr-text">
          Continued on <span className="mono">{e.to}</span> for the rest of the run. Nothing was lost.
        </p>
      );
    case 'result':
      return (
        <>
          <SafeMarkdown text={e.summary} />
          {e.artifacts.length ? (
            <div className="report-results">
              {e.artifacts.map((a) => (
                <a key={a} className="file-chip" href={href({ name: 'project', id: projectId, tab: 'library', file: a })}>
                  {a}
                </a>
              ))}
            </div>
          ) : null}
        </>
      );
    case 'revision':
      return <SafeMarkdown text={e.feedback} />;
    case 'steer':
      return <p className="tr-text">{e.text}</p>;
    case 'incoming':
      return <SafeMarkdown text={e.text} />;
    case 'approval':
      return (
        <div className="tr-approval">
          <span className="mono small">
            {e.tool} {clip(e.arguments, 120)}
          </span>
          <span className="small">{policyReason(e.reason).text}</span>
          {e.state === 'pending' ? (
            <a href={href({ name: 'attention', item: `approval:${e.approvalId}` })}>Review in Attention</a>
          ) : (
            <span className="small muted">
              {e.state === 'approved' ? 'Approved' : 'Denied'}
              {e.resolvedBy ? ` by ${e.resolvedBy === 'user' ? 'you' : e.resolvedBy}` : ''}
              {e.note ? ` · “${e.note}”` : ''}
            </span>
          )}
        </div>
      );
    default:
      return null;
  }
}

function StopBody({ s, projectId }: { s: Stop; projectId: string }) {
  if (s.kind !== 'work') return <EntrySummary e={s.entries[0]!} projectId={projectId} />;
  return (
    <>
      {s.entries.map((e) =>
        e.kind === 'assistant' && e.text ? (
          <SafeMarkdown key={e.id} className="md-voice tr-voice" text={e.text} />
        ) : e.kind === 'tools' ? (
          <ToolGroup key={e.id} calls={e.calls} defaultOpen={e.calls.some((c) => c.status === 'running')} />
        ) : null,
      )}
    </>
  );
}

export const stopDomId = (n: number) => `tr-stop-${n}`;

/** The transcript aside: Narrative (numbered stops) or Every step, plus the steering composer. */
export function Transcript(o: {
  projectId: string;
  threadId: string;
  rows: NarrativeRow[];
  entries: TranscriptEntry[];
  reviewRounds: number;
  /** The session's message fold: names senders and titles answer runs. */
  messages: MessagesState;
  selected: number | null;
  onSelect(n: number): void;
  depth: Depth;
  onDepth(d: Depth): void;
  actions: React.ReactNode;
  canSteer: boolean;
  steerHint: string;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const [steer, setSteer] = useState('');
  const [sending, setSending] = useState(false);
  const [pending, setPending] = useState<string[]>([]);
  const stopOfEntry = new Map<string, number>();
  for (const r of o.rows) if (r.kind === 'stop') r.stop.entries.forEach((e, i) => i === 0 && stopOfEntry.set(e.id, r.stop.n));
  const inStop = new Map<string, number>();
  for (const r of o.rows) if (r.kind === 'stop') for (const e of r.stop.entries) inStop.set(e.id, r.stop.n);

  useEffect(() => {
    if (!pending.length) return;
    const steered = new Set(o.entries.flatMap((e) => (e.kind === 'steer' ? [e.text] : [])));
    setPending((p) => p.filter((t) => !steered.has(t)));
  }, [o.entries, pending.length]);
  useEffect(() => {
    const el = listRef.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [o.entries, o.depth]);
  useEffect(() => {
    if (o.selected === null) return;
    document.getElementById(stopDomId(o.selected))?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }, [o.selected]);

  const send = async () => {
    const text = steer.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      await call('threads.send', { id: o.threadId, text });
      setSteer('');
      setPending((p) => [...p, text]);
    } catch (err) {
      toastError(err);
    } finally {
      setSending(false);
    }
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    }
  };

  const numBadge = (n: number) => (
    <span className="tr-num" aria-label={`Stop ${n}`}>
      {n}
    </span>
  );

  return (
    <aside className="card transcript" aria-label="Transcript">
      <div className="transcript-head">
        <h2>Transcript</h2>
        <div className="segmented" role="group" aria-label="Depth">
          <button type="button" aria-pressed={o.depth === 'narrative'} onClick={() => o.onDepth('narrative')}>
            Narrative
          </button>
          <button type="button" aria-pressed={o.depth === 'steps'} onClick={() => o.onDepth('steps')}>
            Every step
          </button>
        </div>
        {o.actions}
      </div>
      <div
        className="transcript-list"
        ref={listRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
      >
        {o.depth === 'narrative'
          ? o.rows.map((r) =>
              r.kind === 'compacted' ? (
                <div key={r.id} className="chat-divider" role="separator">
                  Earlier conversation summarised
                </div>
              ) : (
                <div
                  key={r.stop.n}
                  id={stopDomId(r.stop.n)}
                  className={`tr-entry${o.selected === r.stop.n ? ' selected' : ''}`}
                  onClick={() => o.onSelect(r.stop.n)}
                >
                  {numBadge(r.stop.n)}
                  <div className="tr-body">
                    <span className="tr-title">
                      {stopText(r.stop, o.reviewRounds, o.messages).title}
                      {r.stop.kind === 'work' || r.stop.kind === 'brief' || r.stop.kind === 'steer' ? null : ` · ${clock(r.stop.from)}`}
                      {r.stop.kind === 'work' ? <span className="muted"> · {stopText(r.stop, o.reviewRounds, o.messages).sub}</span> : null}
                    </span>
                    <StopBody s={r.stop} projectId={o.projectId} />
                  </div>
                </div>
              ),
            )
          : o.entries.map((e) =>
              e.kind === 'compacted' ? (
                <div key={e.id} className="chat-divider" role="separator">
                  Earlier conversation summarised
                </div>
              ) : (
                <div
                  key={e.id}
                  id={stopOfEntry.has(e.id) ? stopDomId(stopOfEntry.get(e.id)!) : undefined}
                  className={`tr-entry${inStop.get(e.id) !== undefined && inStop.get(e.id) === o.selected ? ' selected' : ''}`}
                  onClick={() => inStop.has(e.id) && o.onSelect(inStop.get(e.id)!)}
                >
                  {stopOfEntry.has(e.id) ? numBadge(stopOfEntry.get(e.id)!) : <span className="tr-num tr-num-blank" aria-hidden="true" />}
                  <div className="tr-body">
                    <EntryFull e={e} projectId={o.projectId} />
                  </div>
                </div>
              ),
            )}
        {pending.map((t) => (
          <div key={t} className="tr-entry pending">
            <span className="tr-num tr-num-blank" aria-hidden="true" />
            <div className="tr-body">
              <span className="tr-title">You · steering…</span>
              <p className="tr-text">{t}</p>
            </div>
          </div>
        ))}
      </div>
      <div className="steer">
        <label htmlFor={`steer-${o.threadId}`}>Steer this thread</label>
        <textarea
          id={`steer-${o.threadId}`}
          rows={2}
          value={steer}
          disabled={!o.canSteer}
          onChange={(e) => setSteer(e.target.value)}
          onKeyDown={onKey}
          placeholder="Steer this thread. It reads this at its next step."
        />
        <div className="steer-bar">
          <span className="grow muted small">{o.steerHint}</span>
          <Button variant="primary" size="sm" pending={sending} disabled={!o.canSteer || !steer.trim()} onClick={() => void send()}>
            Steer
          </Button>
        </div>
      </div>
    </aside>
  );
}
