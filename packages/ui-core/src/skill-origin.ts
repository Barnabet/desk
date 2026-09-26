/**
 * Who made a skill version, from its origin: `user` → you, `agent:<id>` → the agent's title when known (else Desk),
 * `builtin:<name>` → Desk itself (a copy of a built-in skill), `catalog:<id>@<commit>` → the catalog with its commit.
 */
export function whoLabel(origin: string | null, titles: Map<string, string>): string {
  if (!origin) return 'Unknown';
  if (origin === 'user') return 'You';
  if (origin.startsWith('agent:')) return titles.get(origin.slice(6)) ?? 'Desk';
  if (origin.startsWith('builtin:')) return 'Built into Desk';
  if (origin.startsWith('catalog:')) {
    const marker = origin.slice(origin.lastIndexOf('@') + 1);
    return /^[0-9a-f]{40}$/.test(marker) ? `Catalog · ${marker.slice(0, 7)}` : 'Catalog';
  }
  return origin;
}
