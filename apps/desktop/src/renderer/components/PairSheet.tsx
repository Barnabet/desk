import { agentTitle, type MessagesState, type MessageView } from '@desk/client';
import { clock, duration, href, pairView, type PairEntry } from '@desk/ui-core';
import { useNow } from '../state/now';
import { Button } from './Button';
import { SafeMarkdown } from './SafeMarkdown';
import { Sheet } from './Sheet';

/** A tracked question's state: amber "open · 4m", "answered 14:05", or muted "closed" or "withdrawn". */
function QuestionState({ q, now }: { q: MessageView; now: number }) {
  if (q.state === 'open') return <span className="pair-state pair-open">open · {duration(Math.max(0, now - Date.parse(q.ts)))}</span>;
  if (q.state === 'answered') return <span className="pair-state">answered{q.stateAt ? ` ${clock(q.stateAt)}` : ''}</span>;
  return <span className="pair-state muted">{q.state}</span>;
}

/** One message: who wrote to whom, its text, a question's state, and where it shows in a transcript. */
function Entry(o: { e: PairEntry; messages: MessagesState; projectId: string; now: number; onClose(): void }) {
  const m = o.e.message;
  const from = agentTitle(o.messages, m.from);
  return (
    <div className={`pair-msg${m.auto ? ' muted' : ''}`}>
      <span className="pair-head">
        {/* A closure is the runtime's, written when the thread could not answer: never shown as an answer. */}
        {m.auto ? `${from} could not answer` : `${from} → ${agentTitle(o.messages, m.to)} · ${m.kind}`} · {clock(m.ts)}
      </span>
      <SafeMarkdown className="pair-text" text={m.text} />
      <span className="pair-foot">
        {m.state ? <QuestionState q={m} now={o.now} /> : null}
        {o.e.shownIn ? (
          // Following the link closes the sheet; the thread opens at the stop that holds the message (?at=).
          <a href={href({ name: 'project', id: o.projectId, tab: 'threads', threadId: o.e.shownIn, at: m.id })} onClick={o.onClose}>
            show in {agentTitle(o.messages, o.e.shownIn)} transcript
          </a>
        ) : null}
      </span>
    </div>
  );
}

/**
 * The messages between two agents (design spec §8 item 8), oldest first, each answer nested under its question with
 * the question's state. It lives in its opener's local state (no route) and reads the session's fold, so a question's
 * state changes in place when its answer arrives.
 */
export function PairSheet(o: { projectId: string; messages: MessagesState; a: string; b: string; onClose(): void }) {
  const now = useNow();
  const v = pairView(o.messages, o.a, o.b);
  const entry = (e: PairEntry) => <Entry e={e} messages={o.messages} projectId={o.projectId} now={now} onClose={o.onClose} />;
  return (
    <Sheet
      title={v.title}
      onClose={o.onClose}
      width={600}
      footer={
        <Button size="sm" onClick={o.onClose}>
          Close
        </Button>
      }
    >
      {v.rows.length ? (
        <ol className="pair-rows">
          {v.rows.map((r) => (
            <li key={r.message.id}>
              {entry(r)}
              {r.answers.length ? (
                <ol className="pair-answers">
                  {r.answers.map((x) => (
                    <li key={x.message.id}>{entry(x)}</li>
                  ))}
                </ol>
              ) : null}
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">They have not written to each other yet.</p>
      )}
    </Sheet>
  );
}
