import { DestroyRef, computed, effect, inject, signal, untracked, type Signal } from '@angular/core';
import type { BuiltinSkillInfo } from '@desk/protocol';
import { describeError } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { singleFlight } from '../../core/refresh';

/** The built-in skills' pure helpers live in `@desk/ui-core` (`builtins.ts`), shared by both UIs. */
export { builtinKey, parseBuiltinKey, runtimeLabel } from '@desk/ui-core';

/** What `injectBuiltins` gives a screen (the React `useBuiltins` result). */
export type BuiltinsState = {
  status: Signal<'loading' | 'ready' | 'error'>;
  error: Signal<string | null>;
  items: Signal<BuiltinSkillInfo[]>;
  refresh(): Promise<void>;
};

/**
 * Desk's built-in skills (renderer/skills/builtins/data.ts `useBuiltins`): listed on start, on window focus, every 30 s,
 * and every 2 s while one is being set up. Call it in a field initializer.
 */
export function injectBuiltins(): BuiltinsState {
  const bridge = inject(DeskBridge);
  const items = signal<BuiltinSkillInfo[] | null>(null);
  const error = signal<string | null>(null);
  // One list at a time (focus, the timers), as in injectSkills and injectCatalog: an ask while one runs lists once more
  // when it ends, so a list slower than the 2 s poll neither piles up calls nor lets an older answer land last.
  const flight = singleFlight(async () => {
    try {
      items.set(await bridge.call('builtins.list', {}));
      error.set(null);
    } catch (err) {
      error.set(describeError(err).message);
    }
  });
  const refresh = flight.run;
  inject(DestroyRef).onDestroy(flight.stop);

  const preparing = computed(() => (items() ?? []).some((b) => b.runtime.state === 'preparing'));
  // React's effect on [refresh, preparing]: it lists at start and again whenever the poll changes speed.
  effect((onCleanup) => {
    const fast = preparing();
    untracked(() => void refresh());
    const onFocus = () => void refresh();
    window.addEventListener('focus', onFocus);
    const timer = setInterval(() => void refresh(), fast ? 2_000 : 30_000);
    onCleanup(() => {
      window.removeEventListener('focus', onFocus);
      clearInterval(timer);
    });
  });

  const status = computed<'loading' | 'ready' | 'error'>(() => (error() && !items() ? 'error' : items() ? 'ready' : 'loading'));
  return { status, error: error.asReadonly(), items: computed(() => items() ?? []), refresh };
}
