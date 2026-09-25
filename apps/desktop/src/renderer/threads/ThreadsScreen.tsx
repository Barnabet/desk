import { EmptyState } from '../components/EmptyState';
import { href } from '../router';
import { useNow } from '../state/now';
import { useSession } from '../state/session';
import { ThreadDetail } from './ThreadDetail';
import { ThreadRoster } from './ThreadRoster';
import './threads.css';

/** The roster, or one thread; `at` opens the thread at the stop that holds that event (a message's id). */
export function ThreadsScreen({ projectId, threadId, at }: { projectId: string; threadId?: string; at?: number }) {
  const s = useSession(projectId);
  const now = useNow();
  if (s.status === 'loading') return <div className="page muted">Loading…</div>;
  if (s.status !== 'ready' || !s.project)
    return (
      <div className="page">
        <EmptyState title={s.status === 'missing' ? "This project isn't here" : "Couldn't load this project"} action={<a href="#/map">Back to the map</a>}>
          {s.error}
        </EmptyState>
      </div>
    );
  if (!threadId) return <ThreadRoster project={s.project} messages={s.messages} now={now} />;
  const thread = s.project.threads.find((t) => t.id === threadId);
  if (!thread)
    return (
      <div className="page">
        <EmptyState title="This thread isn't here" action={<a href={href({ name: 'project', id: projectId, tab: 'threads' })}>All threads</a>}>
          It may belong to another project.
        </EmptyState>
      </div>
    );
  return <ThreadDetail s={s} thread={thread} {...(at !== undefined ? { at } : {})} />;
}
