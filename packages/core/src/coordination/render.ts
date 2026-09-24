import type { StoredEvent } from '@desk/protocol';
import type { AgentRow, ApprovalRow, ServiceRow } from '../state/queries';

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n)}…` : s);

/** One line per thread for rosters and list_threads. */
export function formatThreadLine(t: AgentRow): string {
  const parts = [`${t.id} "${t.title ?? 'untitled'}" [${t.status}]`, t.reasoning_effort ? `${t.model} (${t.reasoning_effort} effort)` : t.model];
  if (t.git_branch) parts.push(`branch ${t.git_branch}`);
  if (t.review_round) parts.push(`review round ${t.review_round}`);
  if (t.result_summary) parts.push(`result: ${clip(t.result_summary.replace(/\s+/g, ' '), 160)}`);
  return `- ${parts.join('; ')}`;
}

/** One line per project service (service_list and Desk's prompt). */
export function formatServiceLine(s: ServiceRow, threadTitle?: string | null): string {
  const state =
    s.status === 'running'
      ? `running${s.url ? ` at ${s.url}` : ''} since ${s.started_at}`
      : s.status === 'exited'
        ? `exited (${s.exit_signal ?? `code ${s.exit_code}`}) at ${s.ended_at}`
        : `stopped (${s.stop_reason ?? 'requested'}) at ${s.ended_at}`;
  const where = `thread ${s.agent_id}${threadTitle ? ` "${threadTitle}"` : ''}${s.cwd !== '.' ? `, in ${s.cwd}` : ''}`;
  return `- ${s.name}: ${state}; ${where}; \`${clip(s.command, 200)}\``;
}

export function formatThreadSummary(t: AgentRow, approvals: ApprovalRow[], lastText: string | null): string {
  return [
    `Thread ${t.id} "${t.title ?? 'untitled'}"`,
    `Status: ${t.status}${t.archived_at ? ' (archived)' : ''}`,
    `Model: ${t.model}${t.reasoning_effort ? ` (reasoning effort ${t.reasoning_effort})` : ''}`,
    t.git_branch ? `Branch: ${t.git_branch} (base ${t.git_base?.slice(0, 10)})` : `Workspace: ${t.workspace_path}`,
    `Review round: ${t.review_round}`,
    `Brief: ${t.brief ?? ''}`,
    `Result: ${t.result_summary ?? '(not completed)'}`,
    ...(t.result_artifacts?.length ? [`Artifacts: ${t.result_artifacts.join(', ')}`] : []),
    ...(approvals.length ? [`Pending approvals: ${approvals.map((a) => `${a.id} ${a.tool}`).join(', ')}`] : []),
    `Last message: ${lastText ? clip(lastText, 1500) : '(none)'}`,
  ].join('\n');
}

/** Human-readable transcript of an agent's events. */
export function renderTranscript(events: StoredEvent[]): string {
  const lines: string[] = [];
  for (const ev of events) {
    switch (ev.type) {
      case 'message.user':
        lines.push(`#${ev.id} USER: ${ev.payload.text}`);
        break;
      case 'message.agent':
        lines.push(`#${ev.id} [from ${ev.payload.from_label} — ${ev.payload.kind}] ${ev.payload.text}`);
        break;
      case 'assistant.message':
        if (ev.payload.content) lines.push(`#${ev.id} ASSISTANT: ${ev.payload.content}`);
        for (const tc of ev.payload.tool_calls) lines.push(`#${ev.id} → ${tc.name}(${clip(tc.arguments, 300)})`);
        break;
      case 'tool.result':
        lines.push(`#${ev.id} ← ${ev.payload.name} [${ev.payload.status}]: ${clip(ev.payload.content, 600)}`);
        break;
      case 'agent.status_changed':
        lines.push(`#${ev.id} (status → ${ev.payload.status}${ev.payload.reason ? `: ${clip(ev.payload.reason, 200)}` : ''})`);
        break;
      default:
        break;
    }
  }
  return lines.join('\n') || '(no activity yet)';
}
