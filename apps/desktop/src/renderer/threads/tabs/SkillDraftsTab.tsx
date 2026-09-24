import { useState } from 'react';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { toast, toastError } from '../../components/Toast';

/** Skill drafts the thread submitted with its result. Threads can't install skills; Desk reviews and installs them. */
export function SkillDraftsTab(o: { projectId: string; threadTitle: string; drafts: string[]; onBrowse(dir: string): void }) {
  const [asking, setAsking] = useState<string | null>(null);
  if (!o.drafts.length)
    return (
      <EmptyState title="No skill drafts">
        A thread can package what it learned as a skill draft (a folder with a SKILL.md). Drafts show up here for Desk to review and install.
      </EmptyState>
    );
  const ask = async (dir: string) => {
    setAsking(dir);
    try {
      await call('projects.send', { id: o.projectId, text: `Please review the skill draft at ${dir} from the thread "${o.threadTitle}" and install it if it's good.` });
      toast({ tone: 'info', message: 'Asked Desk to review the draft.' });
    } catch (err) {
      toastError(err);
    } finally {
      setAsking(null);
    }
  };
  return (
    <ul className="tab-body drafts">
      {o.drafts.map((d) => (
        <li key={d} className="draft">
          <span className="mono grow">{d}</span>
          <Button size="sm" variant="ghost" onClick={() => o.onBrowse(d)}>
            View files
          </Button>
          <Button size="sm" variant="primary" pending={asking === d} onClick={() => void ask(d)}>
            Ask Desk to review and install
          </Button>
        </li>
      ))}
    </ul>
  );
}
