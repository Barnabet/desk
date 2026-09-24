import type { ProjectSummary } from '@desk/protocol';
import { href } from '../router';
import { useUnread } from '../state/unread';
import { projectSummaryLine } from './OrbitMap';

function ProjectCard({ p }: { p: ProjectSummary }) {
  const unread = useUnread(p);
  return (
    <a className="card project-card" href={href({ name: 'project', id: p.project.id, tab: 'conversation' })}>
      <h2>
        {p.project.name}
        {unread ? <span className="unread-dot" aria-label="unread" /> : null}
      </h2>
      {p.project.goal ? <p className="subtitle">{p.project.goal}</p> : null}
      <span className="muted">{projectSummaryLine(p)}</span>
      {p.latest_report ? <span>{p.latest_report.headline}</span> : null}
      {p.attention_count ? <span className="needs-count">{p.attention_count} need you</span> : null}
    </a>
  );
}

/** The map's list alternative. */
export function ProjectList({ projects }: { projects: ProjectSummary[] }) {
  return (
    <div className="project-list">
      {projects.map((p) => (
        <ProjectCard key={p.project.id} p={p} />
      ))}
    </div>
  );
}
