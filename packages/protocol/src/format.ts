/** Collapses whitespace and truncates with an ellipsis. */
export function clip(s: string, max: number): string {
  const flat = s.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** A short, single-line rendering of a tool call's JSON arguments: its first string value, else its first list of strings. */
export function summarizeToolArgs(args: string, max = 80): string {
  let text: string;
  try {
    const values = Object.values(JSON.parse(args) as Record<string, unknown>);
    const list = values.find((v): v is string[] => Array.isArray(v) && v.length > 0 && v.every((x) => typeof x === 'string'));
    text = String(values.find((v) => typeof v === 'string') ?? list?.join(' ') ?? '');
  } catch {
    text = args;
  }
  return clip(text, max);
}

/** How transcripts refer to an image a tool showed the model: `page-3.png 1240×1754`. */
export function imageLabel(image: { name: string; width: number; height: number }): string {
  return `${image.name} ${image.width}×${image.height}`;
}
