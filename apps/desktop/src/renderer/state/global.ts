import { initialGlobalState, type GlobalState } from '@desk/bff/contract';
import { createStore } from '@desk/ui-core';
import { call, onPush } from '../bridge';
import { useStore } from '../store';

export const globalStore = createStore<GlobalState>(initialGlobalState());

/** Seeds from the broker's snapshot, then follows its pushes. */
export function startGlobalSync(): () => void {
  let pushed = false;
  const off = onPush<GlobalState>('desk:global', (s) => {
    pushed = true;
    globalStore.set(s);
  });
  void call('broker.snapshot', {})
    .then((s) => {
      if (!pushed) globalStore.set(s);
    })
    .catch(() => {});
  return off;
}

export const useGlobal = <S,>(select: (s: GlobalState) => S): S => useStore(globalStore, select);
