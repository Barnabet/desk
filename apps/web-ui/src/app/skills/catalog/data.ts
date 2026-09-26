import { DestroyRef, computed, effect, inject, signal, untracked, type Signal } from '@angular/core';
import type { CatalogCategory, CatalogEntry, CatalogInstall, CatalogItem } from '@desk/protocol';
import { skillKey, type SkillRef } from '@desk/ui-core';
import { describeError } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { GlobalStore } from '../../core/global.store';

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

/** What `injectCatalog` gives a screen (the React `useCatalog` result). */
export type CatalogState = {
  status: Signal<'loading' | 'ready' | 'error'>;
  error: Signal<string | null>;
  items: Signal<CatalogItem[]>;
  refresh(): Promise<void>;
};

/**
 * The catalog with where each entry is installed (renderer/skills/catalog/data.ts `useCatalog`). Lists again when a skill
 * runtime changes state (the broker bumps `runtimes.seq`), on focus, and every 3 s while a runtime is being set up.
 * Call it in a field initializer.
 */
export function injectCatalog(): CatalogState {
  const bridge = inject(DeskBridge);
  const global = inject(GlobalStore);
  const items = signal<CatalogItem[] | null>(null);
  const error = signal<string | null>(null);

  const refresh = async (): Promise<void> => {
    try {
      items.set(await bridge.call('catalog.list', {}));
      error.set(null);
    } catch (err) {
      error.set(describeError(err).message);
    }
  };

  // Its own computed, so a desk:global push that leaves the sequence alone lists nothing.
  const seq = computed(() => global.state().runtimes.seq);
  effect(() => {
    seq();
    untracked(() => void refresh());
  });

  const onFocus = () => void refresh();
  window.addEventListener('focus', onFocus);
  inject(DestroyRef).onDestroy(() => window.removeEventListener('focus', onFocus));

  const preparing = computed(() => (items() ?? []).some((i) => i.installs.some((x) => x.runtime === 'preparing')));
  effect((onCleanup) => {
    if (!preparing()) return;
    const timer = setInterval(() => void refresh(), 3000);
    onCleanup(() => clearInterval(timer));
  });

  const status = computed<'loading' | 'ready' | 'error'>(() => (items() ? 'ready' : error() ? 'error' : 'loading'));
  return { status, error: error.asReadonly(), items: computed(() => items() ?? []), refresh };
}
