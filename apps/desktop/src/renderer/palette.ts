export type PaletteGroup = 'Go to' | 'Projects' | 'Threads' | 'Skills' | 'Library' | 'Memory';
export type PaletteItem = { id: string; group: PaletteGroup; title: string; detail?: string; keywords?: string; route: string };

export const GROUP_ORDER: PaletteGroup[] = ['Go to', 'Projects', 'Threads', 'Skills', 'Library', 'Memory'];
const PER_GROUP = 6;

/** 3 for a prefix of the title, 2 for a word start in the title, 1 anywhere in title/detail/keywords, 0 for no match. */
export function score(item: PaletteItem, query: string): number {
  const q = query.trim().toLowerCase();
  if (!q) return 1;
  const title = item.title.toLowerCase();
  if (title.startsWith(q)) return 3;
  if (new RegExp(`(^|[\\s\\-_/.·])${q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`).test(title)) return 2;
  return `${title} ${item.detail ?? ''} ${item.keywords ?? ''}`.toLowerCase().includes(q) ? 1 : 0;
}

/**
 * Matches in group order, best first within a group, at most six per group. Memory results come from the
 * server's search, so they are kept as given. With no query only "Go to" and projects show.
 */
export function rankPalette(items: PaletteItem[], query: string): PaletteItem[] {
  const q = query.trim();
  const out: PaletteItem[] = [];
  for (const g of GROUP_ORDER) {
    if (!q && g !== 'Go to' && g !== 'Projects') continue;
    const inGroup = items.filter((i) => i.group === g);
    const ranked = g === 'Memory' ? inGroup : inGroup.map((i) => ({ i, s: score(i, q) })).filter((x) => x.s > 0).sort((a, b) => b.s - a.s || a.i.title.localeCompare(b.i.title)).map((x) => x.i);
    out.push(...ranked.slice(0, PER_GROUP));
  }
  return out;
}
