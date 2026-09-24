import { useState } from 'react';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { Sheet } from '../components/Sheet';
import { href, navigate } from '../router';
import { useGlobal } from '../state/global';
import { ProjectForm } from './ProjectForm';

function summary(threads: Array<{ status: string }>): string {
  const count = (s: string) => threads.filter((t) => t.status === s).length;
  const parts = [
    count('running') && `${count('running')} running`,
    count('waiting') && `${count('waiting')} waiting`,
    count('done') && `${count('done')} done`,
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : 'No threads yet';
}

export function MapScreen({ newProject }: { newProject: boolean }) {
  const overview = useGlobal((s) => s.overview);
  const attention = useGlobal((s) => s.attention.length);
  const [creating, setCreating] = useState(newProject);
  const running = overview.reduce((n, p) => n + p.threads.filter((t) => t.status === 'running').length, 0);
  const close = () => {
    setCreating(false);
    if (newProject) navigate({ name: 'map' });
  };
  return (
    <div className="page">
      <div className="actions" style={{ justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div>
          <h1 className="title">Projects</h1>
          <p className="subtitle">
            {running} thread{running === 1 ? '' : 's'} running in {overview.length} project{overview.length === 1 ? '' : 's'}. {attention} thing
            {attention === 1 ? ' is' : 's are'} waiting on you.
          </p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)}>
          New project
        </Button>
      </div>
      {overview.length ? (
        <div className="project-list">
          {overview.map((p) => (
            <a key={p.project.id} className="card project-card" href={href({ name: 'project', id: p.project.id, tab: 'conversation' })}>
              <h2>{p.project.name}</h2>
              {p.project.goal ? <p className="subtitle">{p.project.goal}</p> : null}
              <span className="muted">{summary(p.threads)}</span>
              {p.latest_report ? <span>{p.latest_report.headline}</span> : null}
              {p.attention_count ? <span style={{ color: 'var(--accent)' }}>{p.attention_count} need you</span> : null}
            </a>
          ))}
        </div>
      ) : (
        <EmptyState title="No projects yet" action={<Button onClick={() => setCreating(true)}>Create a project</Button>}>
          A project is a goal Desk works toward with its own threads, library and memory.
        </EmptyState>
      )}
      {creating ? (
        <Sheet title="New project" onClose={close}>
          <ProjectForm
            onCancel={close}
            onCreated={(id) => {
              setCreating(false);
              navigate({ name: 'project', id, tab: 'conversation' });
            }}
          />
        </Sheet>
      ) : null}
    </div>
  );
}
