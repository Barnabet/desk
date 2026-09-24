import type { ThreadView } from '@desk/client';
import { EmptyState } from '../../components/EmptyState';
import { SafeMarkdown } from '../../components/SafeMarkdown';
import { href } from '../../router';

export function ResultTab({ projectId, thread }: { projectId: string; thread: ThreadView }) {
  if (!thread.result_summary)
    return <EmptyState title="No result yet">The thread reports here when it finishes. Desk reviews it and may send it back.</EmptyState>;
  return (
    <div className="tab-body result-tab">
      <SafeMarkdown text={thread.result_summary} />
      {thread.result_artifacts?.length ? (
        <>
          <h3>Artifacts</h3>
          <div className="report-results">
            {thread.result_artifacts.map((a) => (
              <a key={a} className="file-chip" href={href({ name: 'project', id: projectId, tab: 'library', file: a })}>
                {a}
              </a>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}
