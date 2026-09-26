import { useCallback, useEffect, useMemo, useState } from 'react';
import type { CatalogItem } from '@desk/protocol';
import { call } from '../../bridge';
import { describeError } from '../../components/Toast';
import { useGlobal } from '../../state/global';

/** The catalog's pure helpers live in `@desk/ui-core`, shared by both UIs; its bays keep their name here. */
export { CATALOG_BAYS as BAYS, actionFor, catalogIndex, fromCatalog, installRef, runtimePackages, runtimeWords, sourceLabel } from '@desk/ui-core';

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
