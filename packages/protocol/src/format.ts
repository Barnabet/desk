/** Collapses whitespace and truncates with an ellipsis. */
export function clip(s: string, max: number): string {
  const flat = s.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** A short, single-line rendering of a tool call's JSON arguments: its first string value. */
export function summarizeToolArgs(args: string, max = 80): string {
  let text: string;
  try {
    const parsed = JSON.parse(args) as Record<string, unknown>;
    text = String(Object.values(parsed).find((v) => typeof v === 'string') ?? '');
  } catch {
    text = args;
  }
  return clip(text, max);
}
