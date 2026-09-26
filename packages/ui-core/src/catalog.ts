import type { CatalogCategory, CatalogEntry, CatalogInstall, CatalogItem, WritableSkillScope } from '@desk/protocol';
import { skillKey, type SkillRef } from './skill-keys';

/**
 * The skill catalog's bays, in order, each with its blurb (both UIs re-export it as `BAYS` from their catalog data;
 * Attention's `BAYS` is another thing). `files` has none: Desk's file-type skills are built in now, not catalog entries.
 */
export const CATALOG_BAYS: Array<{ category: CatalogCategory; title: string; blurb: string }> = [
  { category: 'research', title: 'Research', blurb: 'Find sources, check facts, read the web.' },
  { category: 'documents', title: 'Documents & data', blurb: 'Conversion to Markdown, data analysis, Excel automation, HTML slides.' },
  { category: 'writing', title: 'Writing & diagrams', blurb: 'Clearer prose, rendered diagrams.' },
  { category: 'planning', title: 'Planning', blurb: 'Meetings, risks and decisions.' },
  { category: 'code', title: 'Code', blurb: 'Debugging, review and testing.' },
];

/** The skill a catalog install became (installs are global or in a project, never built in). */
export const installRef = (id: string, i: { scope: WritableSkillScope; project_id: string | null }): SkillRef =>
  i.scope === 'global' ? { scope: 'global', name: id } : { scope: 'project', projectId: i.project_id!, name: id };

/** Installs that really came from the catalog (not another skill that happens to share the name). */
export const fromCatalog = (i: CatalogInstall) => i.state !== 'name_taken';

/** Installed catalog skills by skill key, for the chips on the map, the list and the detail panel. */
export function catalogIndex(items: CatalogItem[]): Map<string, { item: CatalogItem; install: CatalogInstall }> {
  const out = new Map<string, { item: CatalogItem; install: CatalogInstall }>();
  for (const item of items) for (const install of item.installs) if (fromCatalog(install)) out.set(skillKey(installRef(item.id, install)), { item, install });
  return out;
}

/** What Desk sets up for an entry, in plain words. */
export function runtimeWords(e: Pick<CatalogEntry, 'runtime'>): string {
  const parts: string[] = [];
  if (e.runtime.python) parts.push(`Python ${e.runtime.python.version}`);
  if (e.runtime.node) parts.push('Node');
  if (e.runtime.extras?.includes('playwright-chromium')) parts.push('Chromium');
  if (e.runtime.extras?.includes('browser')) parts.push('browser');
  return parts.length ? `${parts.join(' + ')} · set up by Desk` : 'Nothing to set up';
}

/** The packages Desk installs for an entry: pinned Python packages and the top-level Node packages. */
export function runtimePackages(e: Pick<CatalogEntry, 'runtime'>): string[] {
  const out = [...(e.runtime.python?.packages ?? [])];
  for (const l of e.runtime.node?.lock ?? []) if (l.path === `node_modules/${l.name}`) out.push(`${l.name}@${l.version}`);
  return out;
}

export const sourceLabel = (e: Pick<CatalogEntry, 'source'>) => (e.source.type === 'github' ? e.source.repo : 'Desk');

/** The action a card offers for one scope. */
export function actionFor(install: CatalogInstall | undefined): { label: string; kind: 'install' | 'update' | 'installed' | 'modified' | 'taken' } {
  if (!install) return { label: 'Install', kind: 'install' };
  switch (install.state) {
    case 'installed':
      return { label: 'Installed', kind: 'installed' };
    case 'update_available':
      return { label: 'Update', kind: 'update' };
    case 'modified':
      return { label: 'Modified', kind: 'modified' };
    case 'name_taken':
      return { label: 'Name taken', kind: 'taken' };
    default:
      return { label: 'Install', kind: 'install' };
  }
}
