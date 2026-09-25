import type { ToolCallView } from '@desk/client';

/** "read_file ×2 · skill_run", in first-use order. */
export function toolNames(calls: ToolCallView[]): string {
  const counts = new Map<string, number>();
  for (const c of calls) counts.set(c.name, (counts.get(c.name) ?? 0) + 1);
  return [...counts].map(([n, k]) => (k > 1 ? `${n} ×${k}` : n)).join(' · ');
}
