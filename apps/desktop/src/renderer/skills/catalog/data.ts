import { useCallback, useEffect, useMemo, useState } from 'react';
import type { CatalogCategory, CatalogEntry, CatalogInstall, CatalogItem } from '@desk/protocol';
import { call } from '../../bridge';
import { describeError } from '../../components/Toast';
import { useGlobal } from '../../state/global';
import { skillKey, type SkillRef } from '../data';

export const BAYS: Array<{ category: CatalogCategory; title: string; blurb: string }> = [
  { category: 'research', title: 'Research', blurb: 'Find sources, check facts, read the web.' },
  { category: 'documents', title: 'Documents & data', blurb: 'Word, PDF, Excel, slides and datasets.' },
  { category: 'writing', title: 'Writing & diagrams', blurb: 'Clearer prose, rendered diagrams.' },
  { category: 'planning', title: 'Planning', blurb: 'Meetings, risks and decisions.' },
  { category: 'code', title: 'Code', blurb: 'Debugging, review and testing.' },
];

/** The skill a catalog install became. */
export const installRef = (id: string, i: Pick<CatalogInstall, 'scope' | 'project_id'>): SkillRef =>
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

/**
 * The catalog with where each entry is installed. Refetches when a skill runtime changes state (pushed by main),
 * on focus, and every 3 s while a runtime is being set up.
 */
export function useCatalog(): { status: 'loading' | 'ready' | 'error'; error: string | null; items: CatalogItem[]; refresh(): Promise<void> } {
  const seq = useGlobal((g) => g.runtimes.seq);
  const [items, setItems] = useState<CatalogItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try {
      setItems(await call('catalog.list', {}));
      setError(null);
    } catch (err) {
      setError(describeError(err).message);
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh, seq]);
  useEffect(() => {
    const onFocus = () => void refresh();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [refresh]);
  const preparing = useMemo(() => (items ?? []).some((i) => i.installs.some((x) => x.runtime === 'preparing')), [items]);
  useEffect(() => {
    if (!preparing) return;
    const t = setInterval(() => void refresh(), 3000);
    return () => clearInterval(t);
  }, [preparing, refresh]);
  return { status: items ? 'ready' : error ? 'error' : 'loading', error, items: items ?? [], refresh };
}
