import { useState } from 'react';
import { waitingOn, type MessagesState, type ProjectState, type ThreadView } from '@desk/client';
import { AnsweringBadge } from '../components/AnsweringBadge';
import { HopLink } from '../components/HopLink';
import { PairSheet } from '../components/PairSheet';
import { EmptyState } from '../components/EmptyState';
import { SkillBadge } from '../components/SkillBadge';
import { StatusChip } from '../components/StatusChip';
import { ago } from '../format';
import { href } from '../router';
import { useGlobal } from '../state/global';
import { answeringLabel, waitHop, waitLabel, waitsOnYou, type WaitHop } from '../waits';

const ORDER: Record<string, number> = { waiting: 0, running: 1, queued: 2, idle: 3, failed: 4, done: 5, cancelled: 6 };

export function ThreadCard({
  projectId,
  t,
  reviewRounds,
  now,
  proxyDown,
  answering,
  wait,
  hop,
  onWait,
}: {
  projectId: string;
  t: ThreadView;
  reviewRounds: number;
  now: number;
  proxyDown: boolean;
  /** "answering Desk" while the thread's answer run is in progress; its status chip does not change. */
  answering: string | null;
  /** What a waiting thread waits on (waitLabel), shown as its own line (design spec §8 item 13). */
  wait: string | null;
  /** One hop further, to what needs the user. */
  hop: WaitHop | null;
  /** Opens the pair sheet with what it waits on; null for "waiting on you", which is not a pair. */
  onWait: (() => void) | null;
}) {
  const card = (
    <a className={`card thread-card${t.archived_at ? ' archived' : ''}${wait ? ' has-wait' : ''}`} href={href({ name: 'project', id: projectId, tab: 'threads', threadId: t.id })}>
      <div className="thread-card-head">
        <h2>{t.title ?? 'Untitled thread'}</h2>
        <StatusChip status={t.status} reason={t.reason} proxyDown={proxyDown} />
      </div>
      {answering ? <AnsweringBadge label={answering} /> : null}
      {/* A waiting thread's wait comes from the message fold, never from the status reason (design spec §7). */}
      {t.reason && t.status !== 'running' && t.status !== 'waiting' ? <span className="small muted">{t.reason}</span> : null}
      {t.activity ? <span className="mono small thread-activity">{t.activity}</span> : null}
      <dl className="thread-facts">
        <div>
          <dt>Elapsed</dt>
          <dd>{ago(t.created_at, now)}</dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd className="mono">
            {t.model_override ?? t.model}
            {t.effort ? <span className="muted"> · {t.effort}</span> : null}
          </dd>
        </div>
        <div>
          <dt>Workspace</dt>
          <dd className="mono">{t.git_branch ?? 'scratch'}</dd>
        </div>
        {t.review_round ? (
          <div>
            <dt>Revision</dt>
            <dd>
              {t.review_round} of {reviewRounds}
            </dd>
          </div>
        ) : null}
      </dl>
      {t.active_skills.length ? (
        <div className="thread-skills">
          {t.active_skills.map((s) => (
            <SkillBadge key={s} name={s} />
          ))}
        </div>
      ) : null}
      {t.archived_at ? <span className="small muted">Archived</span> : null}
    </a>
  );
  if (!wait) return card;
  // A button and the hop's link cannot sit inside the card's link: the wait is a line in a slot under it.
  return (
    <div className="thread-card-slot">
      {card}
      <div className="thread-card-wait">
        {onWait ? (
          <button type="button" className="link wait-line" onClick={onWait}>
            {wait}
          </button>
        ) : (
          <span className="wait-line">{wait}</span>
        )}
        {hop ? <HopLink hop={hop} /> : null}
      </div>
    </div>
  );
}

/** Every thread in the project as cards, busiest first. */
export function ThreadRoster({ project, messages, now }: { project: ProjectState; messages: MessagesState; now: number }) {
  const [showArchived, setShowArchived] = useState(false);
  const proxyDown = useGlobal((g) => g.system.proxy) === 'down';
  const attention = useGlobal((g) => g.attention);
  /** The pair sheet's two agents (design spec §8 item 8): local state, no route. */
  const [pairOf, setPairOf] = useState<readonly [string, string] | null>(null);
  const all = project.threads;
  const archived = all.filter((t) => t.archived_at).length;
  const list = all
    .filter((t) => showArchived || !t.archived_at)
    .sort((a, b) => (ORDER[a.status] ?? 9) - (ORDER[b.status] ?? 9) || b.created_at.localeCompare(a.created_at));
  return (
    <div className="page roster">
      <div className="roster-head">
        <h1 className="title">Threads</h1>
        <span className="muted">Desk dispatches threads; message Desk in the Conversation to start new work.</span>
        <span className="grow" />
        {archived ? (
          <label className="small">
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> Show {archived} archived
          </label>
        ) : null}
      </div>
      {list.length ? (
        <div className="roster-grid">
          {list.map((t) => {
            const wait = t.status === 'waiting' ? waitLabel(messages, t.id, attention, now) : null;
            // Its first wait target, for the pair sheet; a thread with an item of its own that waits on the user waits on you,
            // which is no pair.
            const target = wait && !attention.some((i) => i.agent_id === t.id && waitsOnYou(i)) ? waitingOn(messages, t.id, attention)[0]?.agentId : undefined;
            return (
              <ThreadCard
                key={t.id}
                projectId={project.project.id}
                t={t}
                reviewRounds={project.project.settings.review_rounds}
                now={now}
                proxyDown={proxyDown}
                answering={answeringLabel(messages, t.id)}
                wait={wait}
                hop={wait ? waitHop(messages, t.id, attention) : null}
                onWait={target ? () => setPairOf([t.id, target]) : null}
              />
            );
          })}
        </div>
      ) : (
        <EmptyState title="No threads yet" action={<a href={href({ name: 'project', id: project.project.id, tab: 'conversation' })}>Brief Desk</a>}>
          Desk splits your brief into threads that work in parallel.
        </EmptyState>
      )}
      {pairOf ? <PairSheet projectId={project.project.id} messages={messages} a={pairOf[0]} b={pairOf[1]} onClose={() => setPairOf(null)} /> : null}
    </div>
  );
}
