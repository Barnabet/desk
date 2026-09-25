import type { AgentStatus, AttentionItem, ProjectSummary } from '@desk/protocol';

export type Tone = 'running' | 'waiting' | 'idle';

/** Blue while anything runs, amber when it only waits on you, grey when idle (design C territories). */
export function projectTone(p: ProjectSummary): Tone {
  if (p.desk_status === 'running' || p.threads.some((t) => t.status === 'running' || t.status === 'queued')) return 'running';
  if (p.attention_count > 0 || p.desk_status === 'waiting' || p.threads.some((t) => t.status === 'waiting')) return 'waiting';
  return 'idle';
}

/** Whether Desk itself needs the user in this project: an item of Desk's (its question, a report's hand-off, its approval). */
const deskNeedsYou = (p: ProjectSummary, attention: readonly AttentionItem[]) =>
  attention.some((i) => i.project_id === p.project.id && i.agent_id !== null && !i.ref.thread_id);

/** One line about a project. It says "Desk is waiting on you" only when Desk has an attention item. */
export function projectSummaryLine(p: ProjectSummary, attention: readonly AttentionItem[] = []): string {
  const c = (...s: AgentStatus[]) => p.threads.filter((t) => s.includes(t.status)).length;
  const parts = [
    c('running') ? `${c('running')} running` : '',
    c('waiting', 'queued') ? `${c('waiting', 'queued')} waiting` : '',
    c('done') ? `${c('done')} done` : '',
    c('failed') ? `${c('failed')} failed` : '',
  ].filter(Boolean);
  if (parts.length) return parts.join(' · ');
  if (p.desk_status === 'waiting') return deskNeedsYou(p, attention) ? 'Desk is waiting on you' : 'Desk is waiting';
  if (p.desk_status === 'running') return 'Desk is working';
  return p.plan_progress.total && p.plan_progress.done === p.plan_progress.total ? 'Idle · all plan items done' : 'Idle';
}
