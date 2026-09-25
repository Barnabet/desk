import { Fragment, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { agentTitle, messageById, type MessagesState, type ToolCallView, type TranscriptEntry } from '@desk/client';
import { clip } from '@desk/protocol';
import { cardTitle, clock, href, inCard, isCard, outCard, policyReason, stopText, type CardView, type NarrativeRow, type Stop } from '@desk/ui-core';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { CodeBlock } from '../components/CodeBlock';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ImageThumbs } from '../components/ImageThumbs';
import { SafeMarkdown } from '../components/SafeMarkdown';
import { toastError } from '../components/Toast';
import { ToolGroup, ToolStatus } from '../components/ToolGroup';

export type Depth = 'narrative' | 'steps';

/**
 * The box under the transcript (design spec §8 item 5): `steer` a working or stopped thread; `ask` a done, failed or
 * idle one (the user's Ask), whose secondary action reopens (`Reopen`) or resumes (`Resume`, idle) it after a confirm;
 * `off` for an archived thread.
 */
export type ComposerMode = { kind: 'steer'; hint: string } | { kind: 'ask'; reopen: 'Reopen' | 'Resume' } | { kind: 'off'; hint: string };

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

/** What message cards and tool rows need from the session (design spec §8 items 9 and 12). */
type Ctx = {
  messages: MessagesState;
  /** The messages this thread sent, by the tool call that sent them: those calls show as cards. */
  sent: ReadonlyMap<string, number>;
  /** Opens the pair sheet with a card's counterpart. */
  onPair(other: string): void;
  /** Names a thread by its id in tool rows. */
  titleOf(id: string): string | undefined;
};

/** A message between this thread and another agent, as a compact card; the counterpart's name opens the pair sheet. */
function MessageCard({ c, ctx }: { c: CardView; ctx: Ctx }) {
  const words = cardTitle(c);
  return (
    <div className={`tr-card tr-card-${c.dir}${c.auto ? ' muted' : ''}`}>
      <span className="tr-card-head">
        {words.before}
        <button
          type="button"
          className="link tr-card-who"
          onClick={(ev) => {
            ev.stopPropagation();
            ctx.onPair(c.other);
          }}
        >
          {c.name}
        </button>
        {words.after} · {clock(c.ts)}
      </span>
      <SafeMarkdown className="tr-card-text" text={c.text} />
    </div>
  );
}

/** The cards of the calls among `calls` that sent a message, in call order. */
function sentCards(calls: ToolCallView[], ctx: Ctx): CardView[] {
  return calls.flatMap((c) => {
    const id = ctx.sent.get(c.id);
    const msg = id === undefined ? undefined : messageById(ctx.messages, id);
    return msg ? [outCard(msg, ctx.messages)] : [];
  });
}

/** One entry at full detail (the Every-step depth). */
function EntryFull({ e, projectId, ctx }: { e: TranscriptEntry; projectId: string; ctx: Ctx }) {
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
          {e.calls
            .filter((c) => !ctx.sent.has(c.id))
            .map((c) => (
              <ToolCallFull key={c.id} c={c} />
            ))}
          {sentCards(e.calls, ctx).map((c) => (
            <MessageCard key={c.id} c={c} ctx={ctx} />
          ))}
        </div>
      );
    case 'compacted':
      return null;
    case 'answer':
      return (
        <span className="muted small">
          Woke to answer message #{e.question} · {clock(e.ts)}
        </span>
      );
    case 'incoming':
      return isCard(e) ? <MessageCard c={inCard(e, ctx.messages)} ctx={ctx} /> : <EntrySummary e={e} projectId={projectId} />;
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

/** A run's text and tool groups, as a work stop shows them, with its message cards in place (design spec §8 item 9). */
function RunBody({ entries, ctx }: { entries: TranscriptEntry[]; ctx: Ctx }) {
  return (
    <>
      {entries.map((e) => {
        if (e.kind === 'assistant') return e.text ? <SafeMarkdown key={e.id} className="md-voice tr-voice" text={e.text} /> : null;
        if (isCard(e)) return <MessageCard key={e.id} c={inCard(e, ctx.messages)} ctx={ctx} />;
        if (e.kind !== 'tools') return null;
        // A call that sent a message shows as its card instead of a tool row.
        const calls = e.calls.filter((c) => !ctx.sent.has(c.id));
        return (
          <Fragment key={e.id}>
            {calls.length ? <ToolGroup calls={calls} defaultOpen={calls.some((c) => c.status === 'running')} titleOf={ctx.titleOf} /> : null}
            {sentCards(e.calls, ctx).map((c) => (
              <MessageCard key={c.id} c={c} ctx={ctx} />
            ))}
          </Fragment>
        );
      })}
    </>
  );
}

/**
 * An answer run's stop (design spec §8 item 6): the question it answers, the run's text, tools and cards, and the
 * runtime's closure when the thread could not answer another agent. The user's Ask has no closure: its title says why.
 */
function AnswerBody({ s, ctx }: { s: Stop; ctx: Ctx }) {
  const e = s.entries[0];
  const q = e?.kind === 'answer' ? messageById(ctx.messages, e.question) : undefined;
  const reply = q?.answerId === undefined ? undefined : messageById(ctx.messages, q.answerId);
  return (
    <>
      {q ? (
        <p className="tr-asked">
          {q.from === 'user' ? 'You' : agentTitle(ctx.messages, q.from)} asked: “{clip(q.text, 200)}”
        </p>
      ) : null}
      <RunBody entries={s.entries} ctx={ctx} />
      {reply?.auto ? <p className="tr-text muted">{reply.text}</p> : null}
    </>
  );
}

function StopBody({ s, projectId, ctx }: { s: Stop; projectId: string; ctx: Ctx }) {
  if (s.kind === 'answer') return <AnswerBody s={s} ctx={ctx} />;
  if (s.kind !== 'work') return <EntrySummary e={s.entries[0]!} projectId={projectId} />;
  return <RunBody entries={s.entries} ctx={ctx} />;
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
  /** The messages this thread sent, by the tool call that sent them (sentCalls): those calls show as cards. */
  sent: ReadonlyMap<string, number>;
  /** Opens the pair sheet with another agent: a card's counterpart. */
  onPair(other: string): void;
  selected: number | null;
  onSelect(n: number): void;
  depth: Depth;
  onDepth(d: Depth): void;
  actions: React.ReactNode;
  /** The box under the transcript: steer a working thread, ask (or reopen) a finished one, or nothing. */
  composer: ComposerMode;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const [steer, setSteer] = useState('');
  const [sending, setSending] = useState<'ask' | 'steer' | null>(null);
  const [pending, setPending] = useState<Array<{ text: string; ask: boolean }>>([]);
  const [reopening, setReopening] = useState(false);
  const ask = o.composer.kind === 'ask';
  const ctx: Ctx = { messages: o.messages, sent: o.sent, onPair: o.onPair, titleOf: (id) => o.messages.agents[id]?.title ?? undefined };
  const stopOfEntry = new Map<string, number>();
  for (const r of o.rows) if (r.kind === 'stop') r.stop.entries.forEach((e, i) => i === 0 && stopOfEntry.set(e.id, r.stop.n));
  const inStop = new Map<string, number>();
  for (const r of o.rows) if (r.kind === 'stop') for (const e of r.stop.entries) inStop.set(e.id, r.stop.n);

  useEffect(() => {
    if (!pending.length) return;
    const steered = new Set(o.entries.flatMap((e) => (e.kind === 'steer' ? [e.text] : [])));
    setPending((p) => p.filter((t) => !steered.has(t.text)));
  }, [o.entries, pending.length]);
  useEffect(() => {
    const el = listRef.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [o.entries, o.depth]);
  useEffect(() => {
    if (o.selected === null) return;
    document.getElementById(stopDomId(o.selected))?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }, [o.selected]);

  /** Sends the box's text: an Ask (`question`), or a plain message that steers the thread or reopens a finished one. */
  const send = async (how: 'ask' | 'steer') => {
    const text = steer.trim();
    if (!text || sending || o.composer.kind === 'off') return;
    setSending(how);
    try {
      await call('threads.send', { id: o.threadId, text, ...(how === 'ask' ? { question: true } : {}) });
      setSteer('');
      setPending((p) => [...p, { text, ask: how === 'ask' }]);
    } catch (err) {
      toastError(err);
    } finally {
      setSending(null);
    }
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(ask ? 'ask' : 'steer');
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
                    <span className={`tr-title${stopText(r.stop, o.reviewRounds, o.messages).muted ? ' muted' : ''}`}>
                      {r.stop.kind === 'answer' && r.stop.live ? <span className="live-dot" aria-hidden="true" /> : null}
                      {stopText(r.stop, o.reviewRounds, o.messages).title}
                      {r.stop.kind === 'work' || r.stop.kind === 'brief' || r.stop.kind === 'steer' ? null : ` · ${clock(r.stop.from)}`}
                      {r.stop.kind === 'work' ? <span className="muted"> · {stopText(r.stop, o.reviewRounds, o.messages).sub}</span> : null}
                    </span>
                    <StopBody s={r.stop} projectId={o.projectId} ctx={ctx} />
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
                    <EntryFull e={e} projectId={o.projectId} ctx={ctx} />
                  </div>
                </div>
              ),
            )}
        {pending.map((t) => (
          <div key={t.text} className="tr-entry pending">
            <span className="tr-num tr-num-blank" aria-hidden="true" />
            <div className="tr-body">
              <span className="tr-title">{`You · ${t.ask ? 'asking' : 'steering'}…`}</span>
              <p className="tr-text">{t.text}</p>
            </div>
          </div>
        ))}
      </div>
      <div className="steer">
        <label htmlFor={`steer-${o.threadId}`}>{ask ? 'Ask this thread' : 'Steer this thread'}</label>
        <textarea
          id={`steer-${o.threadId}`}
          rows={2}
          value={steer}
          disabled={o.composer.kind === 'off'}
          onChange={(e) => setSteer(e.target.value)}
          onKeyDown={onKey}
          placeholder={ask ? 'Ask about its work. It answers from its context.' : 'Steer this thread. It reads this at its next step.'}
        />
        <div className="steer-bar">
          <span className="grow muted small">{o.composer.kind === 'ask' ? 'It answers from its context; its result stays as it is.' : o.composer.hint}</span>
          {o.composer.kind === 'ask' ? (
            <Button size="sm" pending={sending === 'steer'} disabled={!steer.trim() || sending !== null} onClick={() => setReopening(true)}>
              {o.composer.reopen} with this…
            </Button>
          ) : null}
          <Button
            variant="primary"
            size="sm"
            pending={sending === (ask ? 'ask' : 'steer')}
            disabled={o.composer.kind === 'off' || !steer.trim() || sending !== null}
            onClick={() => void send(ask ? 'ask' : 'steer')}
          >
            {ask ? 'Ask' : 'Steer'}
          </Button>
        </div>
      </div>
      {reopening && o.composer.kind === 'ask' ? (
        <ConfirmDialog
          title={`${o.composer.reopen} this thread?`}
          confirmLabel={o.composer.reopen}
          onConfirm={() => {
            setReopening(false);
            void send('steer');
          }}
          onCancel={() => setReopening(false)}
        >
          {o.composer.reopen === 'Reopen' ? 'Reopening' : 'Resuming'} lets it change its work; its result and branch may change.
        </ConfirmDialog>
      ) : null}
    </aside>
  );
}
