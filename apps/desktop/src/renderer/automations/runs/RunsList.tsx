import { useState } from 'react';
import type { AutomationDetail } from '@desk/protocol';
import { dayTime, href, runStatusText, runTook, skippedText, triggerText } from '@desk/ui-core';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { useNow } from '../../state/now';
import type { SessionState } from '../../state/session';
import { useRuns } from '../data';

/** The Runs tab (spec §8.3): newest first, with skipped schedule times as lines of their own. */
export function RunsList(o: { projectId: string; s: SessionState; detail: AutomationDetail }) {
  const [limit, setLimit] = useState(50);
  const live = useRuns(o.s, o.detail.id, limit);
  const now = useNow();
  const entries = live.value ?? [];
  if (live.status === 'loading' && !live.value) return <p className="muted auto-loading">Loading…</p>;
  if (!entries.length) return <EmptyState title="No runs yet">Run now or Test starts one; schedules start them while it is on.</EmptyState>;
  return (
    <div className="auto-runs">
      <table className="auto-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Started</th>
            <th>Trigger</th>
            <th>Status</th>
            <th>Took</th>
            <th>Summary</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => {
            if (e.kind === 'skipped') {
              return (
                <tr key={`skip-${e.due_at}-${e.trigger_index}`} className="auto-skipped">
                  <td />
                  <td colSpan={5} className="muted small">{`${dayTime(e.due_at, now)} · ${skippedText(e)}`}</td>
                </tr>
              );
            }
            const r = e.run;
            const st = runStatusText(r);
            return (
              <tr key={r.id}>
                <td>
                  <a className="mono" href={href({ name: 'project', id: o.projectId, tab: 'automations', automationId: o.detail.id, view: 'runs', runId: r.id })}>{`#${r.number}`}</a>
                </td>
                <td>{dayTime(r.started_at, now)}</td>
                <td>{triggerText(r)}</td>
                <td>
                  <span className={`auto-last tone-${st.tone}`}>
                    <span className="dot" aria-hidden="true" />
                    {st.text}
                  </span>
                </td>
                <td>{runTook(r, now)}</td>
                <td className="auto-summary">{r.summary ?? r.reason ?? ''}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {entries.length >= limit ? (
        <div>
          <Button size="sm" onClick={() => setLimit((l) => l + 50)}>
            Show older runs
          </Button>
        </div>
      ) : null}
    </div>
  );
}
