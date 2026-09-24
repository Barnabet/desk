import type { ProjectState } from '@desk/client';
import type { PlanItem } from '@desk/protocol';
import { StatusChip } from '../components/StatusChip';
import { href } from '../router';

const STATUS: Record<PlanItem['status'], string> = { todo: 'To do', in_progress: 'In progress', done: 'Done', dropped: 'Dropped' };

/** The plan as a route of waypoints, the merge note, and Desk's card. */
export function PlanPanel({ project, proxyDown }: { project: ProjectState; proxyDown: boolean }) {
  const threads = new Map(project.threads.map((t) => [t.id, t]));
  const items = project.plan;
  const done = items.filter((i) => i.status === 'done').length;
  const branches = project.threads.filter((t) => t.git_branch && !t.archived_at).map((t) => t.git_branch!);
  const desk = project.desk;
  const s = project.project.settings;
  return (
    <aside className="card plan-panel" aria-label="Plan and Desk">
      <div className="plan-head">
        <h2>Plan</h2>
        <span className="chip chip-idle">{items.length ? `${done} of ${items.length} done` : 'No plan yet'}</span>
      </div>
      {items.length ? (
        <ol className="plan-route">
          {items.map((i) => {
            const linked = i.thread_ids.map((id) => threads.get(id)).filter((t) => t !== undefined);
            const waiting = linked.some((t) => t.status === 'waiting');
            const tone = i.status === 'in_progress' ? (waiting ? 'wait' : 'run') : i.status;
            return (
              <li key={i.id} className={`plan-stop plan-${tone}`}>
                <span className="plan-dot" aria-hidden="true" />
                <div className="plan-text">
                  <span className="plan-title">{i.title}</span>
                  <span className={`plan-sub plan-sub-${tone}`}>
                    {STATUS[i.status]}
                    {linked.map((t) => (
                      <span key={t.id}>
                        {' · '}
                        <a href={href({ name: 'project', id: project.project.id, tab: 'threads', threadId: t.id })}>{t.title ?? 'Thread'}</a>
                        {t.review_round && t.status !== 'done' ? ` · revision ${t.review_round}/${s.review_rounds}` : ''}
                      </span>
                    ))}
                    {waiting ? ' · waiting on you' : ''}
                    {i.status === 'todo' && !linked.length ? ' · Desk, once the threads report' : ''}
                  </span>
                  {i.notes ? <span className="muted small">{i.notes}</span> : null}
                </div>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="muted">Desk writes the plan once it understands the brief.</p>
      )}
      {branches.length ? (
        <div className="merge-note">
          <span>Desk never merges; you'll get a merge order.</span>
          {branches.map((b) => (
            <span key={b} className="mono small">
              {b}
            </span>
          ))}
        </div>
      ) : null}
      {desk ? (
        <div className="desk-card">
          <div className="desk-card-head">
            <span className="desk-avatar" aria-hidden="true">
              Desk
            </span>
            <span className="grow">
              <strong>Desk</strong>
              <span className="muted small"> coordinator</span>
            </span>
            <StatusChip status={desk.status} reason={desk.reason} proxyDown={proxyDown} />
          </div>
          <dl>
            <div>
              <dt>Model</dt>
              <dd className="mono">{desk.model_override ?? desk.model}</dd>
            </div>
            <div>
              <dt>Check-ins</dt>
              <dd>{s.check_in}</dd>
            </div>
            <div>
              <dt>Autonomy</dt>
              <dd>{s.autonomy === 'dispatch-freely' ? 'dispatches freely' : 'asks before dispatching'}</dd>
            </div>
          </dl>
        </div>
      ) : null}
    </aside>
  );
}
