import { useState } from 'react';
import type { ProjectState, ThreadView } from '@desk/client';
import { EmptyState } from '../components/EmptyState';
import { SkillBadge } from '../components/SkillBadge';
import { StatusChip } from '../components/StatusChip';
import { ago } from '../format';
import { href } from '../router';
import { useGlobal } from '../state/global';

const ORDER: Record<string, number> = { waiting: 0, running: 1, queued: 2, idle: 3, failed: 4, done: 5, cancelled: 6 };

export function ThreadCard({ projectId, t, reviewRounds, now, proxyDown }: { projectId: string; t: ThreadView; reviewRounds: number; now: number; proxyDown: boolean }) {
  return (
    <a className={`card thread-card${t.archived_at ? ' archived' : ''}`} href={href({ name: 'project', id: projectId, tab: 'threads', threadId: t.id })}>
      <div className="thread-card-head">
        <h2>{t.title ?? 'Untitled thread'}</h2>
        <StatusChip status={t.status} reason={t.reason} proxyDown={proxyDown} />
      </div>
      {t.reason && t.status !== 'running' ? <span className="small muted">{t.reason}</span> : null}
      {t.activity ? <span className="mono small thread-activity">{t.activity}</span> : null}
      <dl className="thread-facts">
        <div>
          <dt>Elapsed</dt>
          <dd>{ago(t.created_at, now)}</dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd className="mono">{t.model_override ?? t.model}</dd>
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
}

/** Every thread in the project as cards, busiest first. */
export function ThreadRoster({ project, now }: { project: ProjectState; now: number }) {
  const [showArchived, setShowArchived] = useState(false);
  const proxyDown = useGlobal((g) => g.system.proxy) === 'down';
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
          {list.map((t) => (
            <ThreadCard key={t.id} projectId={project.project.id} t={t} reviewRounds={project.project.settings.review_rounds} now={now} proxyDown={proxyDown} />
          ))}
        </div>
      ) : (
        <EmptyState title="No threads yet" action={<a href={href({ name: 'project', id: project.project.id, tab: 'conversation' })}>Brief Desk</a>}>
          Desk splits your brief into threads that work in parallel.
        </EmptyState>
      )}
    </div>
  );
}
