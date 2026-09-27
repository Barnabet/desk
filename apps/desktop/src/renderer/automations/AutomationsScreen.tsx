import type { AutomationDetail } from '@desk/protocol';
import { href, type AutomationView } from '@desk/ui-core';
import { EmptyState } from '../components/EmptyState';
import { useSession, type SessionState } from '../state/session';
import { AutomationHeader, DraftHeader } from './AutomationHeader';
import { AutomationList } from './AutomationList';
import { useAutomation } from './data';
import { DesignView } from './design/DesignView';
import { VersionsView } from './versions/VersionsView';

/** The Automations tab (spec §8.1): the list, one automation (header, then Design, Runs, Versions or Grants), or a new draft. */
export function AutomationsScreen(o: { projectId: string; automationId?: string; view?: AutomationView; runId?: string; draft?: string }) {
  const s = useSession(o.projectId);
  if (s.status === 'missing') return <EmptyState title="This project is gone" />;
  if (o.draft) return <DraftAutomation projectId={o.projectId} s={s} name={o.draft} />;
  if (!o.automationId) return <AutomationList projectId={o.projectId} s={s} />;
  return <OneAutomation projectId={o.projectId} s={s} id={o.automationId} view={o.view ?? 'design'} {...(o.runId ? { runId: o.runId } : {})} />;
}

function OneAutomation(o: { projectId: string; s: SessionState; id: string; view: AutomationView; runId?: string }) {
  const live = useAutomation(o.s, o.id);
  const all = href({ name: 'project', id: o.projectId, tab: 'automations' });
  if (live.status === 'missing') {
    return (
      <EmptyState title="This automation is gone" action={<a href={all}>All automations</a>}>
        It was deleted.
      </EmptyState>
    );
  }
  if (!live.value) {
    return live.status === 'error' ? (
      <EmptyState title="Couldn't load this automation" action={<a href={all}>All automations</a>}>
        {live.error}
      </EmptyState>
    ) : (
      <p className="muted auto-loading">Loading…</p>
    );
  }
  return (
    <div className="automation">
      <AutomationHeader projectId={o.projectId} detail={live.value} view={o.view} onChange={live.replace} />
      <div className="automation-body">
        <AutomationBody projectId={o.projectId} s={o.s} view={o.view} {...(o.runId ? { runId: o.runId } : {})} detail={live.value} onChange={live.replace} />
      </div>
    </div>
  );
}

/** One automation's current view. Tasks 12–16 replace these branches with the real views. */
function AutomationBody(o: { projectId: string; s: SessionState; view: AutomationView; runId?: string; detail: AutomationDetail; onChange(d: AutomationDetail): void }) {
  switch (o.view) {
    case 'runs':
      return <EmptyState title="Runs">Its runs show here.</EmptyState>;
    case 'versions':
      return <VersionsView detail={o.detail} onChange={o.onChange} />;
    case 'grants':
      return <EmptyState title="Grants">What its runs may do without asking shows here.</EmptyState>;
    default:
      return <DesignView key={o.detail.id} projectId={o.projectId} sources={gitSources(o.s)} detail={o.detail} onChange={o.onChange} />;
  }
}

/** The project's git sources, for an agent step's worktree. */
const gitSources = (s: SessionState) => (s.project?.sources ?? []).filter((x) => x.kind === 'git').map((x) => ({ id: x.id, label: x.label }));

/** A Blank automation before its first save (spec §8.1): only Design, on a local draft. */
function DraftAutomation(o: { projectId: string; s: SessionState; name: string }) {
  return (
    <div className="automation">
      <DraftHeader projectId={o.projectId} name={o.name} />
      <div className="automation-body">
        <DesignView key={o.name} projectId={o.projectId} sources={gitSources(o.s)} draftName={o.name} />
      </div>
    </div>
  );
}
