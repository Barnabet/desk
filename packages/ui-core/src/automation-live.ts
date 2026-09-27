import { affectsAutomations, affectsRun, emptyAutomations, reduceAutomations, withRunDetail } from '@desk/client';
import type { AutomationDetail, AutomationSummary, RunDetail, RunListEntry, StoredEvent } from '@desk/protocol';

// How both UIs keep automation snapshots current: events after a snapshot fold in through the Plan 19 reducer, and the
// events these rules pick reload it, so fields no reducer can derive (next_due, at_step…) catch up.

/** The events after `after`, from a log in id order. */
export function eventsAfter(events: readonly StoredEvent[], after: number): StoredEvent[] {
  let lo = 0;
  let hi = events.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (events[mid]!.id <= after) lo = mid + 1;
    else hi = mid;
  }
  return events.slice(lo);
}

/** Folds one event into a project's automation list. */
export const reduceAutomationList = (list: AutomationSummary[], e: StoredEvent): AutomationSummary[] => reduceAutomations({ projectId: e.project_id, list, runs: {} }, e).list;

/** Any automation event but a layout save reloads the list. */
export const automationListRefetch = (e: StoredEvent): boolean => affectsAutomations(e) && e.type !== 'automation.layout_saved';

/** The automation an automation event is about, when its payload says. */
const automationOf = (e: StoredEvent): string | undefined => (e.type.startsWith('automation.') ? (e.payload as { automation_id?: string }).automation_id : undefined);

/** A detail reloads when the automation changes (not its layout: the editor saved it), and when its last run ends. */
export const automationDetailRefetch =
  (id: string) =>
  (e: StoredEvent, d: AutomationDetail): boolean =>
    (automationOf(e) === id && e.type !== 'automation.layout_saved') || (e.type === 'automation.run_finished' && e.payload.run_id === d.last_run?.id);

/** A run list reloads when one of the automation's runs starts or a schedule time is skipped, and when a listed run changes. */
export const automationRunsRefetch =
  (id: string) =>
  (e: StoredEvent, list: RunListEntry[]): boolean => {
    if (automationOf(e) === id && (e.type === 'automation.run_started' || e.type === 'automation.trigger_skipped')) return true;
    if (e.type !== 'automation.step_changed' && e.type !== 'automation.run_finished') return false;
    const runId = e.payload.run_id;
    return list.some((x) => x.kind === 'run' && x.run.id === runId);
  };

/** Folds one event into a loaded run (its step changes and its end). */
export const reduceRunDetail = (run: RunDetail, e: StoredEvent): RunDetail => reduceAutomations(withRunDetail(emptyAutomations(run.project_id), run), e).runs[run.id] ?? run;

/** A run reloads for its agents' approvals, a child run's end and its own end. */
export const runDetailRefetch =
  (runId: string) =>
  (e: StoredEvent, run: RunDetail): boolean =>
    affectsRun(e, run) || (e.type === 'automation.run_finished' && e.payload.run_id === runId);
