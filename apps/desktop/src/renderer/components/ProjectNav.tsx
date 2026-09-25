import { href, PROJECT_TABS, type ProjectTab } from '@desk/ui-core';

const LABEL: Record<ProjectTab, string> = { conversation: 'Conversation', threads: 'Threads', library: 'Library', memory: 'Memory', settings: 'Settings' };

export function ProjectNav({ projectId, tab }: { projectId: string; tab: ProjectTab }) {
  return (
    <nav aria-label="Project" className="subnav">
      {PROJECT_TABS.map((t) => (
        <a key={t} href={href({ name: 'project', id: projectId, tab: t })} aria-current={t === tab ? 'page' : undefined}>
          {LABEL[t]}
        </a>
      ))}
    </nav>
  );
}
