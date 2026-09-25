import { imageLabel, type StoredEvent } from '@desk/protocol';
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

/** Where a service runs, for service lines: a source folder or a thread's workspace. */
export function servicePlace(s: ServiceRow, source?: { label: string; path: string } | null, threadTitle?: string | null): string {
  if (s.source_id) return `source ${s.source_id} "${source?.label ?? 'removed'}"${source ? ` (${source.path})` : ''}`;
  return `thread ${s.agent_id}${threadTitle ? ` "${threadTitle}"` : ''}`;
}

/** One line per project service (service_list and Desk's prompt); `place` from servicePlace. */
export function formatServiceLine(s: ServiceRow, place: string): string {
  const state =
    s.status === 'running'
      ? `running${s.url ? ` at ${s.url}` : ''} since ${s.started_at}`
      : s.status === 'exited'
        ? `exited (${s.exit_signal ?? `code ${s.exit_code}`}) at ${s.ended_at}`
        : `stopped (${s.stop_reason ?? 'requested'}) at ${s.ended_at}`;
  const where = `${place}${s.cwd !== '.' ? `, in ${s.cwd}` : ''}`;
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
        // Desk cannot read thread workspaces, so each image carries the reference view_image takes.
        for (const image of ev.payload.images ?? []) lines.push(`#${ev.id}   [image: ${imageLabel(image)}, view_image attachment:${image.sha256}]`);
        break;
      case 'images.withheld':
        lines.push(`#${ev.id} (images no longer sent to the model: ${ev.payload.images.map((i) => i.name).join(', ')}; ${clip(ev.payload.reason, 200)})`);
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
