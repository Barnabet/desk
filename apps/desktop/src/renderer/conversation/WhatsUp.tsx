import type { ProjectState } from '@desk/client';
import { SafeMarkdown } from '../components/SafeMarkdown';
import { ago, plural } from '../format';

/** Desk's What's up at the top of the conversation's left column; until Desk has written one, the thread counts. */
export function WhatsUp({ project, now }: { project: ProjectState; now: number }) {
  const w = project.whatsUp;
  const when = w ? ago(w.ts, now) : null;
  const live = project.threads.filter((t) => !t.archived_at);
  const count = (st: string) => live.filter((t) => t.status === st).length;
  return (
    <section className="whats-up" aria-label="What's up">
      <span className="eyebrow">What's up{when ? ` · ${when === 'now' ? 'just now' : `${when} ago`}` : ''}</span>
      {w ? (
        <SafeMarkdown className="md-voice whats-up-text" text={w.text} />
      ) : (
        <p className="muted">
          Desk and {plural(live.length, 'thread')}. {count('running')} running, {count('waiting')} waiting, {count('done')} done.
        </p>
      )}
    </section>
  );
}
