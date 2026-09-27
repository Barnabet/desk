import { useCallback, useMemo } from 'react';
import { emptyAutomations, withAutomationSummaries } from '@desk/client';
import type { AutomationDetail, AutomationSummary, RunDetail, RunListEntry } from '@desk/protocol';
import { automationDetailRefetch, automationListRefetch, automationRunsRefetch, reduceAutomationList, reduceRunDetail, runDetailRefetch } from '@desk/ui-core';
import { call } from '../bridge';
import type { SessionState } from '../state/session';
import { useLive, type Live } from './live';

/** The project's automations, by name, kept current. */
export function useAutomationList(projectId: string, s: SessionState): Live<AutomationSummary[]> {
  const load = useCallback(() => call('automations.list', { projectId }).then((list) => withAutomationSummaries(emptyAutomations(projectId), list).list), [projectId]);
  return useLive({ key: projectId, events: s.events, ready: s.status === 'ready', load, reduce: reduceAutomationList, refetch: automationListRefetch });
}

/** One automation's detail, reloaded when it changes and when its last run ends. */
export function useAutomation(s: SessionState, id: string): Live<AutomationDetail> {
  const load = useCallback(() => call('automations.get', { id }), [id]);
  const refetch = useMemo(() => automationDetailRefetch(id), [id]);
  return useLive({ key: id, events: s.events, ready: s.status === 'ready', load, refetch });
}

/** An automation's runs and skipped schedule times, newest first. */
export function useRuns(s: SessionState, id: string, limit: number): Live<RunListEntry[]> {
  const load = useCallback(() => call('automations.runs', { id, limit }), [id, limit]);
  const refetch = useMemo(() => automationRunsRefetch(id), [id]);
  return useLive({ key: `${id}:${limit}`, events: s.events, ready: s.status === 'ready', load, refetch });
}

/** One run with live steps: step changes fold in at once; its agents' approvals, a child's end and its own end reload it. */
export function useRun(s: SessionState, runId: string): Live<RunDetail> {
  const load = useCallback(() => call('automations.getRun', { runId }), [runId]);
  const refetch = useMemo(() => runDetailRefetch(runId), [runId]);
  return useLive({ key: runId, events: s.events, ready: s.status === 'ready', load, reduce: reduceRunDetail, refetch });
}
