import { useState } from 'react';
import type { AttentionItem } from '@desk/protocol';
import type { ChatItem, QuestionView } from '@desk/client';
import { Button } from '../components/Button';
import { SafeMarkdown } from '../components/SafeMarkdown';
import { ToolGroup } from '../components/ToolGroup';
import { clock, duration, plural } from '../format';
import { href } from '../router';
import type { AnswerQuote, RowView } from './rowViews';

const FEED: Record<string, string> = {
  note: 'Note',
  update: 'Update',
  question: 'Question',
  blocker: 'Blocked',
  answer: 'Answer',
  completed: 'Result',
  failed: 'Failed',
  cancelled: 'Stopped',
  approval: 'Approval',
  stalled: 'Stalled',
  revision: 'Sent back',
};

/** The daemon labels threads as `thread "Title" (id)`; people only need the title. */
export const agentLabel = (label: string) => /^thread "(.*)" \([\w-]+\)$/.exec(label)?.[1] ?? label;

export const chatDomId = (id: string) => `chat-${id.replace(/[^a-zA-Z0-9_-]/g, '-')}`;

/** The event id a chat item came from (streaming runs sort last). */
export function chatEventId(item: ChatItem): number {
  const n = Number(item.id.split(':')[1]);
  return Number.isFinite(n) ? n : Number.POSITIVE_INFINITY;
}

/** Time since `iso` on the shared clock: "42s", "4m". */
const age = (iso: string, now: number) => duration(Math.max(0, now - Date.parse(iso)));

/** A runtime closure's text without its parentheses: "(Frontend was stopped before answering.)" → "Frontend was …". */
const unwrap = (text: string) => text.replace(/^\(([\s\S]*)\)$/, '$1');

/** A message's text, at most three lines until "more". Agent text always goes through SafeMarkdown. */
function Clamped({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 280 || text.split('\n').length > 3;
  return (
    <>
      <SafeMarkdown className={`feed-text${long && !open ? ' clamp-3' : ''}`} text={text} />
      {long ? (
        <button type="button" className="link small feed-more" onClick={() => setOpen((v) => !v)}>
          {open ? 'less' : 'more'}
        </button>
      ) : null}
    </>
  );
}

/** A tracked question's state line: amber "waiting for an answer · 4m", "answered 14:05 ↓" (jumps to the answer), or muted. */
function QuestionState({ q, now, onJump }: { q: QuestionView; now: number | undefined; onJump(itemId: string): void }) {
  if (q.state === 'open') return <span className="msg-state msg-open">waiting for an answer · {age(q.since, now ?? Date.now())}</span>;
  if (q.state === 'answered' && q.answerId !== undefined && q.answeredAt) {
    const answer = `e:${q.answerId}`;
    return (
      <button type="button" className="link small msg-state" onClick={() => onJump(answer)}>
        answered {clock(q.answeredAt)} ↓
      </button>
    );
  }
  return <span className="msg-state muted">{q.state}</span>;
}

/** An answer's one-line quote of its question; it jumps to the question and flashes it. */
function Quote({ a, onJump }: { a: AnswerQuote; onJump(itemId: string): void }) {
  return (
    <button type="button" className="msg-quote" onClick={() => onJump(`e:${a.question}`)}>
      {`↩ ${a.asker}'s question: “${a.text}”`}
    </button>
  );
}

/** What threads said to each other during one Desk turn: counts, then one line per pair (design spec §8 item 2). */
function Digest({ view, projectId, now }: { view: NonNullable<RowView['digest']>; projectId: string; now: number | undefined }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="chat-digest">
      <button type="button" className="digest-toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {`Between threads · ${plural(view.messages, 'message')}${view.open ? ` · ${plural(view.open, 'open question')}` : ''}`}
      </button>
      {open ? (
        <ul className="digest-pairs">
          {view.pairs.map((p) => (
            <li key={p.key}>
              <a href={href({ name: 'project', id: projectId, tab: 'threads', threadId: p.to, at: p.at })}>
                {`${p.label} · ${p.count}${p.waiting ? ` · ${p.waiting.who} waiting ${age(p.waiting.since, now ?? Date.now())}` : ''}`}
              </a>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function ChatItemView(o: {
  item: ChatItem;
  projectId: string;
  attentionIds: Set<string>;
  answering: string | null;
  onAnswer(text: string): void;
  onOwnWords(): void;
  /** What the row shows from the message fold (rowViews): a question's state, an answer's quote, a digest's counts. */
  view?: RowView | undefined;
  /** The shared clock, passed only to rows whose view ticks; the others never re-render for it. */
  now?: number | undefined;
  /** Scrolls the chat to an item and flashes it. */
  onJump(itemId: string): void;
}) {
  const { item, view } = o;
  const thread = (id: string) => href({ name: 'project', id: o.projectId, tab: 'threads', threadId: id });
  switch (item.kind) {
    case 'user':
      return (
        <div className="chat-user">
          <span className="chat-meta">You · {clock(item.ts)}</span>
          <p>{item.text}</p>
        </div>
      );
    case 'assistant':
      return (
        <div className="chat-desk" aria-live={item.streaming ? 'polite' : undefined}>
          <span className="chat-meta">
            {item.streaming ? (
              <>
                <span className="live-dot" aria-hidden="true" />
                Desk · writing
              </>
            ) : (
              `Desk · ${clock(item.ts)}`
            )}
          </span>
          <SafeMarkdown className="md-voice" text={item.text} />
          {item.interrupted ? <span className="muted small">Cut off by an error; Desk will pick up again.</span> : null}
        </div>
      );
    case 'tools': {
      // Desk's sends to threads show as message rows; failed, denied or unfinished ones stay here (design spec §8 item 1).
      const calls = item.calls.filter((c) => !(c.name === 'message_thread' && c.status === 'ok'));
      if (!calls.length) return null;
      return <ToolGroup calls={calls} title={`Desk ${calls.some((c) => c.status === 'running') ? 'is using' : 'used'} ${calls.length} tool${calls.length === 1 ? '' : 's'}`} />;
    }
    case 'agent':
      if (item.auto)
        // The runtime closed Desk's question for a thread that could not answer: never shown as an answer.
        return (
          <div className="chat-feed feed-closed">
            <span className="feed-closed-text">{`${agentLabel(item.fromLabel)} could not answer: ${unwrap(item.text)}`}</span>
            <span className="muted small">{clock(item.ts)}</span>
            {view?.answers ? <Quote a={view.answers} onJump={o.onJump} /> : null}
          </div>
        );
      return (
        <div className={`chat-feed feed-${item.messageKind}`}>
          <span className="feed-chip">{FEED[item.messageKind] ?? item.messageKind}</span>
          <a className="feed-from" href={thread(item.fromAgentId)}>
            {agentLabel(item.fromLabel)}
          </a>
          <span className="muted small">{clock(item.ts)}</span>
          {view?.answers ? <Quote a={view.answers} onJump={o.onJump} /> : null}
          <SafeMarkdown className="feed-text" text={item.text} />
          {view?.question ? <QuestionState q={view.question} now={o.now} onJump={o.onJump} /> : null}
        </div>
      );
    case 'message':
      return (
        <div className={`chat-msg msg-${item.messageKind}`}>
          <span className="msg-head">
            Desk → <a href={thread(item.to)}>{view?.toTitle ?? 'a thread'}</a> · {item.messageKind} · {clock(item.ts)}
          </span>
          {view?.answers ? <Quote a={view.answers} onJump={o.onJump} /> : null}
          <Clamped text={item.text} />
          {view?.question ? <QuestionState q={view.question} now={o.now} onJump={o.onJump} /> : null}
        </div>
      );
    case 'steer':
      return (
        <div className="chat-msg msg-steer">
          <span className="msg-head">
            {item.question ? 'You asked ' : 'You → '}
            <a href={thread(item.to)}>{view?.toTitle ?? 'a thread'}</a> · {clock(item.ts)}
          </span>
          <Clamped text={item.text} />
        </div>
      );
    case 'digest':
      return view?.digest ? <Digest view={view.digest} projectId={o.projectId} now={o.now} /> : null;
    case 'report':
      return (
        <article className="report-card" aria-labelledby={`report-${item.eventId}`}>
          <div className="report-main">
            <span className="eyebrow">Report · {clock(item.ts)}</span>
            <h2 id={`report-${item.eventId}`}>{item.headline}</h2>
            {item.progress ? <SafeMarkdown className="report-progress" text={item.progress} /> : null}
            {item.results.length ? (
              <div className="report-results">
                <strong>Results</strong>
                {item.results.map((r) => (
                  <a key={r} className="file-chip" href={href({ name: 'project', id: o.projectId, tab: 'library', file: r })}>
                    {r}
                  </a>
                ))}
              </div>
            ) : null}
          </div>
          {item.needsYou.length ? (
            <div className="needs-box report-needs">
              <strong>Needs you · {item.needsYou.length}</strong>
              {item.needsYou.map((text, i) => {
                const id = `report:${item.eventId}:${i}`;
                return (
                  <div key={id} className="needs-row">
                    <span className="needs-num" aria-hidden="true">
                      {i + 1}
                    </span>
                    {o.attentionIds.has(id) ? <a href={href({ name: 'attention', item: id })}>{text}</a> : <span className="needs-done">{text}</span>}
                  </div>
                );
              })}
            </div>
          ) : null}
        </article>
      );
    case 'question':
      return (
        <section className={`question-card${item.answered ? ' answered' : ''}`} aria-labelledby={`question-${item.eventId}`}>
          <span className="eyebrow">
            <span className="accent-dot" aria-hidden="true" />
            Desk asks · {clock(item.ts)}
          </span>
          <p id={`question-${item.eventId}`} className="question-text">
            {item.question}
          </p>
          {item.answered ? (
            <span className="muted small">Answered</span>
          ) : (
            <div className="choice-options">
              {item.options.map((opt, i) => (
                <Button key={opt} size="sm" variant={i === 0 ? 'primary' : 'secondary'} pending={o.answering === opt} disabled={o.answering !== null} onClick={() => o.onAnswer(opt)}>
                  {opt}
                </Button>
              ))}
              <button type="button" className="link small" onClick={o.onOwnWords}>
                Answer in your own words…
              </button>
            </div>
          )}
        </section>
      );
    case 'notice':
      return (
        <div className={`chat-notice notice-${item.level}`} role="status">
          <span className={`dot ${item.level === 'error' ? 'warn' : item.level === 'warning' ? 'warn' : 'ok'}`} aria-hidden="true" />
          {item.code === 'proxy_down' ? 'Paused, will resume: the model proxy is unreachable.' : item.message}
        </div>
      );
    case 'compacted':
      return (
        <div className="chat-divider" role="separator">
          Earlier conversation summarised
        </div>
      );
  }
}

export type { AttentionItem };
