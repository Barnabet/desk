import { sanitizeLabel } from '@desk/protocol';
import type { PolicyDecision } from '../policy/evaluate';
import type { AgentRow } from '../state/queries';
import type { RunDeps } from './run';

/** Steps an answer run may take (design spec §4.2); most answers take one. */
export const ANSWER_MAX_STEPS = 4;

/** An answer is a message: at most this many characters (design spec §5.3). */
export const MAX_ANSWER_CHARS = 4000;
const CLIPPED = '[… clipped; ask again or use read_thread]';

/**
 * Why the runtime answered a question for its recipient: the end of the closure `(<title> <why>.)` (design spec §4.6,
 * §3.5).
 */
export const WHY = {
  noAnswer: 'did not answer',
  unfinished: 'did not finish answering. Ask again or use read_thread',
  stopped: 'was stopped before answering',
  archived: 'was archived before answering',
  restart: 'could not answer: Desk was restarting. Ask again if you still need to know',
  noWorkspace: 'could not answer: its workspace is missing',
  error: (detail: string) => `could not answer: ${detail.trim().replace(/\.+$/, '')}`,
};

/** An answer run's text reply as the answer: trimmed, and clipped to 4000 characters. Empty when the model said nothing. */
export function answerText(text: string): string {
  const t = text.trim();
  return t.length > MAX_ANSWER_CHARS ? `${t.slice(0, MAX_ANSWER_CHARS - CLIPPED.length - 1)}\n${CLIPPED}` : t;
}

/** The runtime's closure of a question to a thread, on one line: `(Frontend was stopped before answering.)`. */
export function closureText(title: string | null, why: string): string {
  return `(${sanitizeLabel(title ?? 'untitled')} ${why}.)`;
}

/** Tools an answer run may call: they only read (design spec §4.4). */
export const ANSWER_TOOLS: ReadonlySet<string> = new Set([
  'read_file',
  'list_dir',
  'glob',
  'grep',
  'view_image',
  'git_status',
  'git_diff',
  'memory_search',
  'library_list',
  'library_read',
  'list_threads',
  'read_thread',
]);

/** What an answer run gets for a call it may not make. */
export const ANSWER_DENIAL = 'Denied: not available while answering a question. You can only read; answer in plain text.';
const APPROVAL_DENIAL = 'Denied: this call needs approval, which is not available while answering a question. You can only read; answer in plain text.';

/**
 * Whether a message tool call sends the answer to the asker: message_thread to it (its id, its exact title, or the
 * sanitised title the question's header names it by), or message_desk when Desk asked, of any kind but a question or
 * a blocker. send() records it as the answer (S3).
 */
function answersAsker(toolName: string, input: unknown, asker: AgentRow): boolean {
  const i = (input ?? {}) as { thread_id?: unknown; kind?: unknown };
  if (i.kind === 'question' || i.kind === 'blocker') return false;
  if (toolName === 'message_desk') return asker.role === 'desk';
  if (toolName !== 'message_thread' || asker.role !== 'thread') return false;
  return i.thread_id === asker.id || i.thread_id === asker.title || i.thread_id === sanitizeLabel(asker.title ?? 'untitled');
}

/**
 * The policy gate of an answer run (design spec §4.4). Read tools, and the answer sent to the asker as a message, go
 * to the policy as usual, and an `ask` becomes a denial: an answer run never creates an approval. Everything else is
 * denied without asking the policy, so an answer run changes nothing, runs no command, messages no one but its
 * asker, and never yields (complete and wait_for_reply are denied).
 */
export function answerGate(gate: RunDeps['gate'], asker: AgentRow | 'user'): RunDeps['gate'] {
  return (tool, input, project, agent) => {
    if (!ANSWER_TOOLS.has(tool.name) && (asker === 'user' || !answersAsker(tool.name, input, asker))) {
      return { action: 'deny', delegateToDesk: false, reason: 'Not available while answering a question', denial: ANSWER_DENIAL };
    }
    const d: PolicyDecision = gate(tool, input, project, agent);
    return d.action === 'ask' ? { ...d, action: 'deny', denial: APPROVAL_DENIAL } : d;
  };
}
