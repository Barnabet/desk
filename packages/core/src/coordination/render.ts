import { imageLabel, quoteLines, sanitizeLabel, snippet, type EventOf, type StoredEvent } from '@desk/protocol';
import type { AgentRow, ApprovalRow, ServiceRow } from '../state/queries';

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n)}…` : s);
/** Agent text that must stay on one line: tool names and arguments, reasons, file names. */
export const oneLine = (s: string) => s.replace(/[\s\u0085]+/g, ' ').trim();

/** One line per thread for rosters and list_threads. The title and the result are agents' words: one quoted line each. */
export function formatThreadLine(t: AgentRow): string {
  const parts = [`${t.id} ${snippet(t.title ?? 'untitled', 80)} [${t.status}]`, t.reasoning_effort ? `${t.model} (${t.reasoning_effort} effort)` : t.model];
  if (t.git_branch) parts.push(`branch ${t.git_branch}`);
  if (t.review_round) parts.push(`review round ${t.review_round}`);
  if (t.result_summary) parts.push(`result: ${snippet(t.result_summary, 160)}`);
  return `- ${parts.join('; ')}`;
}

/** Where a service runs, for service lines: a source folder or a thread's workspace (its title a snippet, as agents write it). */
export function servicePlace(s: ServiceRow, source?: { label: string; path: string } | null, threadTitle?: string | null): string {
  if (s.source_id) return `source ${s.source_id} "${source?.label ?? 'removed'}"${source ? ` (${source.path})` : ''}`;
  return `thread ${s.agent_id}${threadTitle ? ` ${snippet(threadTitle, 80)}` : ''}`;
}

/**
 * One line per project service (service_list and Desk's prompt); `place` from servicePlace. Agents choose the command
 * and the folder, so both are snippets: no service line can start another line of a system prompt (design spec §1.5).
 */
export function formatServiceLine(s: ServiceRow, place: string): string {
  const state =
    s.status === 'running'
      ? `running${s.url ? ` at ${s.url}` : ''} since ${s.started_at}`
      : s.status === 'exited'
        ? `exited (${s.exit_signal ?? `code ${s.exit_code}`}) at ${s.ended_at}`
        : `stopped (${s.stop_reason ?? 'requested'}) at ${s.ended_at}`;
  const where = `${place}${s.cwd !== '.' ? `, in ${snippet(s.cwd, 200)}` : ''}`;
  return `- ${s.name}: ${state}; ${where}; ${snippet(s.command, 200)}`;
}

/** Desk's read_thread summary. The brief, the result and the last message are quoted: they are agents' words. */
export function formatThreadSummary(t: AgentRow, approvals: ApprovalRow[], lastText: string | null): string {
  const quoted = (label: string, text: string | null, none: string) => (text ? `${label}:\n${quoteLines(text)}` : `${label}: ${none}`);
  return [
    `Thread ${t.id} ${snippet(t.title ?? 'untitled', 80)}`,
    `Status: ${t.status}${t.archived_at ? ' (archived)' : ''}`,
    `Model: ${t.model}${t.reasoning_effort ? ` (reasoning effort ${t.reasoning_effort})` : ''}`,
    t.git_branch ? `Branch: ${t.git_branch} (base ${t.git_base?.slice(0, 10)})` : `Workspace: ${t.workspace_path}`,
    `Review round: ${t.review_round}`,
    quoted('Brief', t.brief, '(none)'),
    quoted('Result', t.result_summary, '(not completed)'),
    ...(t.result_artifacts?.length ? [`Artifacts: ${t.result_artifacts.join(', ')}`] : []),
    ...(approvals.length ? [`Pending approvals: ${approvals.map((a) => `${a.id} ${a.tool}`).join(', ')}`] : []),
    quoted('Last message', lastText ? clip(lastText, 1500) : null, '(none)'),
  ].join('\n');
}

/** A message as a third party reads it in a transcript: `[message from thread "X" (id) — answer to #12]`. */
function transcriptTag(p: EventOf<'message.agent'>['payload']): string {
  const what = p.kind === 'answer' && p.reply_to !== undefined ? `answer to #${p.reply_to}${p.auto ? ', written by the runtime' : ''}` : p.kind;
  return `[message from ${senderOf(p).label} — ${what}]`;
}

/**
 * Human-readable transcript of an agent's events (Desk's read_thread full). Every free text (the user's, the agent's,
 * other agents', tool output) is quoted and the rest kept on one line, so no text poses as a transcript line (§1.5).
 */
export function renderTranscript(events: StoredEvent[]): string {
  const lines: string[] = [];
  const quoted = (head: string, text: string) => lines.push(`${head}\n${quoteLines(text)}`);
  for (const ev of events) {
    switch (ev.type) {
      case 'message.user':
        quoted(`#${ev.id} USER:`, ev.payload.text);
        break;
      case 'message.agent':
        quoted(`#${ev.id} ${transcriptTag(ev.payload)}`, ev.payload.text);
        break;
      case 'assistant.message':
        if (ev.payload.content) quoted(`#${ev.id} ASSISTANT:`, ev.payload.content);
        for (const tc of ev.payload.tool_calls) lines.push(`#${ev.id} → ${oneLine(tc.name)}(${clip(oneLine(tc.arguments), 300)})`);
        break;
      case 'tool.result':
        quoted(`#${ev.id} ← ${oneLine(ev.payload.name)} [${ev.payload.status}]:`, clip(ev.payload.content, 600));
        // Desk cannot read thread workspaces, so each image carries the reference view_image takes.
        for (const image of ev.payload.images ?? []) lines.push(`#${ev.id}   [image: ${oneLine(imageLabel(image))}, view_image attachment:${image.sha256}]`);
        break;
      case 'images.withheld':
        lines.push(`#${ev.id} (images no longer sent to the model: ${ev.payload.images.map((i) => oneLine(i.name)).join(', ')}; ${clip(oneLine(ev.payload.reason), 200)})`);
        break;
      case 'agent.status_changed':
        lines.push(`#${ev.id} (status → ${ev.payload.status}${ev.payload.reason ? `: ${clip(oneLine(ev.payload.reason), 200)}` : ''})`);
        break;
      default:
        break;
    }
  }
  return lines.join('\n') || '(no activity yet)';
}

/** A line the runtime writes itself, never another agent: `[Desk runtime — reminder] …`, `[Desk runtime — answer mode] …`. */
export function runtimeLine(what: string, text: string): string {
  return `[Desk runtime — ${what}] ${text}`;
}

/**
 * Who sent a message, from its stored label (`Desk`, or `thread "<title>" (<id>)`). The title is sanitised here too,
 * so labels stored before sanitising render like new ones.
 */
export function senderOf(p: { from_agent_id: string; from_label: string }): { desk: boolean; title: string; label: string } {
  if (p.from_label === 'Desk') return { desk: true, title: 'Desk', label: 'Desk' };
  const title = sanitizeLabel(/^thread "([\s\S]*)" \([^()]*\)$/.exec(p.from_label)?.[1] ?? p.from_label);
  return { desk: false, title, label: `thread "${title}" (${p.from_agent_id})` };
}

/**
 * The runtime's header for a message, as its recipient reads it (design spec §1.5): who sent it and its kind, how to
 * answer a tracked question, or which question an answer answers.
 */
export function messageHeader(ev: EventOf<'message.agent'>): string {
  const p = ev.payload;
  const from = senderOf(p);
  let what: string = p.kind;
  if (p.kind === 'question' && p.tracked) {
    what = from.desk
      ? 'question; Desk may be waiting on you: answer with message_desk'
      : `question; they may be waiting on you: answer with message_thread to "${from.title}"`;
  } else if (p.kind === 'answer' && p.reply_to !== undefined) {
    what = `answer to your question #${p.reply_to}${p.auto ? ', written by the runtime' : ''}`;
  }
  return `[message #${ev.id} from ${from.label} — ${what}]`;
}

/**
 * An inbox item as its recipient's model reads it: the user's text as is; another agent's message as the runtime's
 * header followed by the sender's words, every line quoted; the runtime's own reminder as a runtime line.
 */
export function renderInboxItem(ev: EventOf<'message.user'> | EventOf<'message.agent'>): string {
  if (ev.type === 'message.user') return ev.payload.text;
  if (ev.payload.kind === 'reminder') return runtimeLine('reminder', ev.payload.text);
  return `${messageHeader(ev)}\n${quoteLines(ev.payload.text)}`;
}
