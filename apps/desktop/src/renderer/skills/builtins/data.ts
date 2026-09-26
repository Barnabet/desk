import { useCallback, useEffect, useState } from 'react';
import type { BuiltinSkillInfo } from '@desk/protocol';
import { call } from '../../bridge';
import { describeError } from '../../components/Toast';

/** The built-in skills' pure helpers live in `@desk/ui-core` (`builtins.ts`), shared by both UIs. */
export { builtinKey, parseBuiltinKey, runtimeLabel } from '@desk/ui-core';

/** Desk's built-in skills; refetches on focus, every 30 s, and every 2 s while one is setting up. */
export function useBuiltins(): { status: 'loading' | 'ready' | 'error'; error: string | null; items: BuiltinSkillInfo[]; refresh(): Promise<void> } {
  const [items, setItems] = useState<BuiltinSkillInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try {
      setItems(await call('builtins.list', {}));
      setError(null);
    } catch (err) {
      setError(describeError(err).message);
    }
  }, []);
  const preparing = items?.some((b) => b.runtime.state === 'preparing') ?? false;
  useEffect(() => {
    void refresh();
    const onFocus = () => void refresh();
    window.addEventListener('focus', onFocus);
    const t = setInterval(() => void refresh(), preparing ? 2_000 : 30_000);
    return () => {
      window.removeEventListener('focus', onFocus);
      clearInterval(t);
    };
  }, [refresh, preparing]);
  return { status: error && !items ? 'error' : items ? 'ready' : 'loading', error, items: items ?? [], refresh };
}
