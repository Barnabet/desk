/** Clips `text` to `max` characters, marking a cut with "…". */
function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/**
 * Agent or user text as one JSON-quoted line, whitespace collapsed and clipped to `max` characters: the only form such
 * text takes inside a runtime notice or a system prompt, so it can never start a line of its own.
 */
export function snippet(text: string, max: number): string {
  return JSON.stringify(clip(text.replace(WHITESPACE, ' ').trim(), max));
}

/** Whitespace, including U+0085 (next line), which `\s` leaves out. */
const WHITESPACE = /[\s\u0085]+/g;

/** Another agent's words: every line prefixed with "> ", so none can start a line of its own (design spec §1.5). */
export function quoteLines(text: string): string {
  return text
    .split(/\r\n|[\n\r\v\f\u0085\u2028\u2029]/)
    .map((line) => `> ${line}`)
    .join('\n');
}

/** A title as it may appear inside a runtime header: one line, no `]` (which would close the header), at most 60 characters. */
export function sanitizeLabel(title: string): string {
  const flat = title.replace(/]/g, '').replace(WHITESPACE, ' ').trim();
  return flat.length > 60 ? `${flat.slice(0, 59)}…` : flat;
}
