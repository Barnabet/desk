/** Clips `text` to `max` characters, marking a cut with "…". */
function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/**
 * Agent or user text as one JSON-quoted line, whitespace collapsed and clipped to `max` characters: the only form such
 * text takes inside a runtime notice or a system prompt, so it can never start a line of its own.
 */
export function snippet(text: string, max: number): string {
  return JSON.stringify(clip(text.replace(/\s+/g, ' ').trim(), max));
}
