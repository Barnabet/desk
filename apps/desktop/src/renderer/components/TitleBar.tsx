import { href, type Route } from '../router';
import { useGlobal } from '../state/global';
import { ProjectSwitcher } from './ProjectSwitcher';

function daemonLabel(status: string, proxy: string): { label: string; tone: '' | 'ok' | 'warn' | 'bad' } {
  switch (status) {
    case 'live':
      return { label: `deskd · proxy ${proxy}`, tone: proxy === 'up' ? 'ok' : 'warn' };
    case 'reconnecting':
      return { label: 'reconnecting…', tone: 'warn' };
    case 'offline':
      return { label: 'deskd not running', tone: 'bad' };
    case 'mismatch':
      return { label: 'deskd needs an update', tone: 'bad' };
    default:
      return { label: 'connecting…', tone: '' };
  }
}

/** Places nav (Map · project · ⌘P switcher · Skills · System), daemon status, and the attention pill. */
export function TitleBar({ route }: { route: Route }) {
  const count = useGlobal((s) => s.attention.length);
  const status = useGlobal((s) => s.connection.status);
  const proxy = useGlobal((s) => s.system.proxy);
  const overview = useGlobal((s) => s.overview);
  const projectId = route.name === 'project' ? route.id : null;
  const project = projectId ? overview.find((p) => p.project.id === projectId) : undefined;
  const d = daemonLabel(status, proxy);
  const mac = window.desk?.platform === 'darwin';
  return (
    <header className={`titlebar${mac ? ' mac' : ''}`}>
      <nav aria-label="Places" className="places">
        <a href={href({ name: 'map' })} aria-current={route.name === 'map' ? 'page' : undefined}>
          Map
        </a>
        {project ? (
          <a href={href({ name: 'project', id: project.project.id, tab: 'conversation' })} aria-current="page">
            {project.project.name}
          </a>
        ) : null}
        <ProjectSwitcher currentId={project ? project.project.id : null} />
        <a href={href({ name: 'skills' })} aria-current={route.name === 'skills' ? 'page' : undefined}>
          Skills
        </a>
        <a href={href({ name: 'system' })} aria-current={route.name === 'system' ? 'page' : undefined}>
          System
        </a>
      </nav>
      <span className="spacer" />
      <span className="daemon">
        <span className={`dot ${d.tone}`} aria-hidden="true" />
        {d.label}
      </span>
      <a href={href({ name: 'attention' })} className={`pill ${count ? 'needs' : 'clear'}`} aria-current={route.name === 'attention' ? 'page' : undefined}>
        {count ? `${count} need you` : 'All clear'}
      </a>
    </header>
  );
}
