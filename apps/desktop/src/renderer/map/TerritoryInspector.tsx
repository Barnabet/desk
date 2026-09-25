import { useEffect, useState } from 'react';
import type { AttentionItem, PlanItem, ProjectSummary } from '@desk/protocol';
import { call } from '../bridge';
import { ago, clock } from '../format';
import { href } from '../router';
import { projectSummaryLine, projectTone } from './OrbitMap';

const BADGE = { running: 'chip-run', waiting: 'chip-wait', idle: 'chip-idle' } as const;

function waypointTone(item: PlanItem, p: ProjectSummary): string {
  if (item.status === 'done') return 'done';
  if (item.status === 'dropped') return 'dropped';
  if (item.status === 'todo') return 'todo';
  return p.threads.some((t) => item.thread_ids.includes(t.id) && t.status === 'waiting') ? 'wait' : 'run';
}

function threadLine(t: ProjectSummary['threads'][number], waitingOnYou: boolean, now: number): string {
  switch (t.status) {
    case 'running':
      return t.review_round ? `revision ${t.review_round} · ${ago(t.created_at, now)}` : `running · ${ago(t.created_at, now)}`;
    case 'waiting':
      return waitingOnYou ? 'waiting on you' : 'waiting';
    case 'queued':
      return /restart/i.test(t.reason ?? '') ? 'will resume' : 'queued';
    default:
      return t.status;
  }
}

/** The TERRITORY card: latest report, plan waypoints, threads, needs-you, and the ways in. */
export function TerritoryInspector({ p, items, now }: { p: ProjectSummary; items: AttentionItem[]; now: number }) {
  const [plan, setPlan] = useState<PlanItem[] | null>(null);
  useEffect(() => {
    let live = true;
    setPlan(null);
    call('projects.plan', { id: p.project.id })
      .then((r) => live && setPlan(r?.items ?? []))
      .catch(() => live && setPlan([]));
    return () => {
      live = false;
    };
  }, [p.project.id, p.plan_progress.done, p.plan_progress.total]);
  const tone = projectTone(p);
  const done = plan?.filter((i) => i.status === 'done').length ?? p.plan_progress.done;
  const total = plan?.length ?? p.plan_progress.total;
  return (
    <article className="card territory" aria-label="Territory" role="region" aria-live="polite">
      <div className="territory-head">
        <span className="eyebrow">Territory</span>
        <span className={`chip ${BADGE[tone]}`}>{projectSummaryLine(p, items)}</span>
      </div>
      <h2 className="territory-name">{p.project.name}</h2>
      {p.latest_report ? (
        <div className="territory-quote">
          <p>“{p.latest_report.headline}”</p>
          <span className="muted">Desk · report {clock(p.latest_report.ts)}</span>
        </div>
      ) : (
        <p className="muted">{p.project.goal || 'No report yet.'}</p>
      )}
      <div className="territory-block">
        <div className="territory-row">
          <strong>Plan</strong>
          <span className="muted">{total ? (done === total ? `all ${total} done` : `${done} of ${total} done`) : 'no plan yet'}</span>
        </div>
        {plan && plan.length ? (
          <div className="waypoints" aria-hidden="true">
            <span className="waypoints-rule" />
            {plan.map((i) => (
              <span key={i.id} className={`waypoint waypoint-${waypointTone(i, p)}`} title={i.title} />
            ))}
          </div>
        ) : null}
      </div>
      {p.threads.length ? (
        <div className="territory-block">
          <strong>Threads</strong>
          {p.threads.map((t) => (
            <a key={t.id} className="territory-thread" href={href({ name: 'project', id: p.project.id, tab: 'threads', threadId: t.id })}>
              <span className={`status-dot status-dot-${t.status}`} aria-hidden="true" />
              <span className="grow">{t.title ?? 'Thread'}</span>
              <span className={`territory-thread-status status-text-${t.status}`}>{threadLine(t, items.some((i) => i.ref.thread_id === t.id), now)}</span>
            </a>
          ))}
        </div>
      ) : null}
      {items.length ? (
        <div className="needs-box">
          <strong>Needs you · {items.length}</strong>
          {items.map((i) => (
            <a key={i.id} href={href({ name: 'attention', item: i.id })}>
              {i.title}
            </a>
          ))}
        </div>
      ) : null}
      <div className="actions">
        <a className="btn btn-primary grow" href={href({ name: 'project', id: p.project.id, tab: 'conversation' })}>
          Open conversation
        </a>
        <a className="btn btn-secondary grow" href={href(items[0] ? { name: 'attention', item: items[0].id } : { name: 'attention' })}>
          Attention
        </a>
      </div>
    </article>
  );
}
