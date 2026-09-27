import { inject } from '@angular/core';
import { emptyAutomations, withAutomationSummaries } from '@desk/client';
import type { AutomationDetail, AutomationSummary, RunDetail, RunListEntry } from '@desk/protocol';
import { automationDetailRefetch, automationListRefetch, automationRunsRefetch, reduceAutomationList, reduceRunDetail, runDetailRefetch } from '@desk/ui-core';
import { DeskBridge } from '../core/desk-bridge';
import type { SessionState } from '../core/session.service';
import { injectLive, type Live } from './live';

/** The project's automations, by name, kept current. */
export function injectAutomationList(projectId: () => string, s: () => SessionState): Live<AutomationSummary[]> {
  const bridge = inject(DeskBridge);
  return injectLive(() => {
    const id = projectId();
    const st = s();
    return {
      key: id,
      events: st.events,
      ready: st.status === 'ready',
      load: () => bridge.call('automations.list', { projectId: id }).then((list) => withAutomationSummaries(emptyAutomations(id), list).list),
      reduce: reduceAutomationList,
      refetch: automationListRefetch,
    };
  });
}

/** One automation's detail, reloaded when it changes and when its last run ends. */
export function injectAutomation(s: () => SessionState, id: () => string): Live<AutomationDetail> {
  const bridge = inject(DeskBridge);
  return injectLive(() => {
    const a = id();
    const st = s();
    return { key: a, events: st.events, ready: st.status === 'ready', load: () => bridge.call('automations.get', { id: a }), refetch: automationDetailRefetch(a) };
  });
}

/** An automation's runs and skipped schedule times, newest first. */
export function injectRuns(s: () => SessionState, id: () => string, limit: () => number): Live<RunListEntry[]> {
  const bridge = inject(DeskBridge);
  return injectLive(() => {
    const a = id();
    const n = limit();
    const st = s();
    return { key: `${a}:${n}`, events: st.events, ready: st.status === 'ready', load: () => bridge.call('automations.runs', { id: a, limit: n }), refetch: automationRunsRefetch(a) };
  });
}

/** One run with live steps: step changes fold in at once; its agents' approvals, a child's end and its own end reload it. */
export function injectRun(s: () => SessionState, runId: () => string): Live<RunDetail> {
  const bridge = inject(DeskBridge);
  return injectLive(() => {
    const r = runId();
    const st = s();
    return { key: r, events: st.events, ready: st.status === 'ready', load: () => bridge.call('automations.getRun', { runId: r }), reduce: reduceRunDetail, refetch: runDetailRefetch(r) };
  });
}
