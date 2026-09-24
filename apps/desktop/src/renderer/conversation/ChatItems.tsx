import type { AttentionItem } from '@desk/protocol';
import type { ChatItem } from '@desk/client';
import { Button } from '../components/Button';
import { SafeMarkdown } from '../components/SafeMarkdown';
import { ToolGroup } from '../components/ToolGroup';
import { clock } from '../format';
import { href } from '../router';

const FEED: Record<string, string> = {
  note: 'Note',
  update: 'Update',
  question: 'Question',
  blocker: 'Blocked',
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

export function ChatItemView(o: {
  item: ChatItem;
  projectId: string;
  attentionIds: Set<string>;
  answering: string | null;
  onAnswer(text: string): void;
  onOwnWords(): void;
}) {
  const { item } = o;
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
    case 'tools':
      return <ToolGroup calls={item.calls} title={`Desk ${item.calls.some((c) => c.status === 'running') ? 'is using' : 'used'} ${item.calls.length} tool${item.calls.length === 1 ? '' : 's'}`} />;
    case 'agent':
      return (
        <div className={`chat-feed feed-${item.messageKind}`}>
          <span className="feed-chip">{FEED[item.messageKind] ?? item.messageKind}</span>
          <a className="feed-from" href={href({ name: 'project', id: o.projectId, tab: 'threads', threadId: item.fromAgentId })}>
            {agentLabel(item.fromLabel)}
          </a>
          <span className="muted small">{clock(item.ts)}</span>
          <SafeMarkdown className="feed-text" text={item.text} />
        </div>
      );
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
