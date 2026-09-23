import type { ChatMessage } from '../model/types';
import type { TaggedMessage } from './transcript';

/** Fraction of the model's context window at which the conversation is compacted. */
export const COMPACTION_THRESHOLD = 0.7;
/** Conversation messages kept verbatim after the checkpoint. */
export const DEFAULT_KEEP_MESSAGES = 6;

const MAX_MESSAGE_CHARS = 4_000;
/** Rough characters-per-token used to budget the compaction request itself. */
const CHARS_PER_TOKEN = 3;

export function shouldCompact(promptTokens: number, contextWindow: number): boolean {
  return promptTokens >= contextWindow * COMPACTION_THRESHOLD;
}

/**
 * Where to cut: keep (at least) the last `keep` messages, moving the cut back so the kept part never starts
 * with tool results separated from the assistant message that requested them. `upTo` is the id of the last
 * event summarised. Null when there is nothing before the cut.
 */
export function chooseSplit(messages: TaggedMessage[], keep: number): { index: number; upTo: number } | null {
  let index = messages.length - keep;
  while (index > 0 && messages[index]?.message.role === 'tool') index--;
  if (index <= 0) return null;
  return { index, upTo: messages[index - 1]!.eventId };
}

function clip(text: string, max = MAX_MESSAGE_CHARS): string {
  return text.length <= max ? text : `${text.slice(0, max)}\n[… ${text.length - max} characters omitted]`;
}

function renderMessage(m: ChatMessage): string {
  switch (m.role) {
    case 'system':
      return `SYSTEM:\n${clip(m.content)}`;
    case 'user':
      return `USER:\n${clip(m.content)}`;
    case 'assistant': {
      const calls = (m.tool_calls ?? []).map((tc) => `→ called ${tc.function.name}(${clip(tc.function.arguments, 1_000)}) [${tc.id}]`);
      return ['ASSISTANT:', ...(m.content ? [clip(m.content)] : []), ...calls].join('\n');
    }
    case 'tool':
      return `TOOL RESULT [${m.tool_call_id}]:\n${clip(m.content)}`;
  }
}

/** Renders the part being summarised as plain text (no tool messages: no tool schemas needed, and it fits any model). */
export function renderForCompaction(messages: ChatMessage[], contextWindow: number): string {
  const budget = Math.floor(contextWindow * 0.5 * CHARS_PER_TOKEN);
  const parts = messages.map(renderMessage);
  let total = parts.reduce((n, p) => n + p.length + 2, 0);
  // Over budget: keep the first message (usually the previous checkpoint) and the newest ones.
  let dropped = 0;
  while (total > budget && parts.length > 2) {
    total -= parts[1]!.length + 2;
    parts.splice(1, 1);
    dropped++;
  }
  if (dropped) parts.splice(1, 0, `[… ${dropped} earlier messages omitted]`);
  return parts.join('\n\n');
}

const CHECKPOINT_INSTRUCTIONS = [
  'You write checkpoints for an autonomous agent whose conversation has grown too long.',
  'The earlier part of its conversation will be replaced by your checkpoint; the most recent messages are kept verbatim after it.',
  'The agent must be able to continue its work from the checkpoint alone, so be specific: exact file paths, names, ids, numbers, commands, decisions and their reasons.',
  'Include the content of any previous checkpoint that is still relevant.',
].join(' ');

export function compactionPrompt(covered: ChatMessage[], contextWindow: number): ChatMessage[] {
  return [
    { role: 'system', content: CHECKPOINT_INSTRUCTIONS },
    {
      role: 'user',
      content: [
        '<conversation>',
        renderForCompaction(covered, contextWindow),
        '</conversation>',
        '',
        'Write the checkpoint now, in Markdown, with exactly these sections:',
        '## Goal — the assignment and what done means',
        '## Decisions — decisions made and why, constraints learned',
        '## Current state — what has been done and verified, what is in progress',
        '## Files touched — paths created or changed, and what they contain',
        '## Open questions — unresolved questions, pending replies or approvals',
        '## Next steps — what to do next, in order',
      ].join('\n'),
    },
  ];
}
