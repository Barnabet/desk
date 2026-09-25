import { sanitizeLabel } from '@desk/protocol';

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
