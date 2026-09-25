import { useMemo, useSyncExternalStore } from 'react';

export type ProjectTab = 'conversation' | 'threads' | 'library' | 'memory' | 'settings';
export const PROJECT_TABS: ProjectTab[] = ['conversation', 'threads', 'library', 'memory', 'settings'];

export type Route =
  | { name: 'onboarding' }
  | { name: 'tray' }
  | { name: 'map'; newProject?: boolean }
  | { name: 'attention'; item?: string }
  | { name: 'skills'; skill?: string }
  /** The skill catalog, optionally with one entry's review open. */
  | { name: 'catalog'; review?: string }
  | { name: 'system' }
  /**
   * `at`: on a thread, the event id of a message to open the route at (the digest's pair lines); on the conversation,
   * the event id of a Desk stop to scroll the chat to (a stop clicked on the Threads tab's timeline).
   */
  | { name: 'project'; id: string; tab: ProjectTab; threadId?: string; at?: number; file?: string; q?: string };

const enc = encodeURIComponent;

export function parseRoute(hash: string): Route {
  const [path = '', query = ''] = hash.replace(/^#/, '').split('?');
  const q = new URLSearchParams(query);
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  switch (parts[0]) {
    case 'onboarding':
      return { name: 'onboarding' };
    case 'tray':
      return { name: 'tray' };
    case 'attention': {
      const item = q.get('item');
      return item ? { name: 'attention', item } : { name: 'attention' };
    }
    case 'skills':
      if (parts[1] === 'catalog') return parts[2] ? { name: 'catalog', review: parts[2] } : { name: 'catalog' };
      return parts[1] ? { name: 'skills', skill: parts[1] } : { name: 'skills' };
    case 'system':
      return { name: 'system' };
    case 'p': {
      const id = parts[1];
      if (!id) return { name: 'map' };
      const tab = PROJECT_TABS.includes(parts[2] as ProjectTab) ? (parts[2] as ProjectTab) : 'conversation';
      const at = Number(q.get('at'));
      const atOk = Number.isSafeInteger(at) && at > 0;
      if (tab === 'threads' && parts[3]) return atOk ? { name: 'project', id, tab, threadId: parts[3], at } : { name: 'project', id, tab, threadId: parts[3] };
      if (tab === 'conversation' && atOk) return { name: 'project', id, tab, at };
      const file = q.get('file');
      if (tab === 'library' && file) return { name: 'project', id, tab, file };
      const search = q.get('q');
      return tab === 'memory' && search ? { name: 'project', id, tab, q: search } : { name: 'project', id, tab };
    }
    default:
      return q.get('new') === '1' ? { name: 'map', newProject: true } : { name: 'map' };
  }
}

export function href(r: Route): string {
  switch (r.name) {
    case 'onboarding':
      return '#/onboarding';
    case 'tray':
      return '#/tray';
    case 'map':
      return r.newProject ? '#/map?new=1' : '#/map';
    case 'attention':
      return r.item ? `#/attention?item=${enc(r.item)}` : '#/attention';
    case 'skills':
      return r.skill ? `#/skills/${enc(r.skill)}` : '#/skills';
    case 'catalog':
      return r.review ? `#/skills/catalog/${enc(r.review)}` : '#/skills/catalog';
    case 'system':
      return '#/system';
    case 'project':
      return `#/p/${enc(r.id)}/${r.tab}${r.threadId ? `/${enc(r.threadId)}` : ''}${r.file ? `?file=${enc(r.file)}` : r.q ? `?q=${enc(r.q)}` : r.at !== undefined ? `?at=${r.at}` : ''}`;
  }
}

export function navigate(to: Route | string): void {
  const target = typeof to === 'string' ? to : href(to);
  window.location.hash = target.replace(/^#/, '');
}

/** Changes the route without adding a history entry (selection within a screen). */
export function replaceRoute(to: Route | string): void {
  const target = typeof to === 'string' ? to : href(to);
  if (window.location.hash === target) return;
  history.replaceState(null, '', target);
  window.dispatchEvent(new HashChangeEvent('hashchange'));
}

const subscribeHash = (fn: () => void) => {
  window.addEventListener('hashchange', fn);
  return () => window.removeEventListener('hashchange', fn);
};

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribeHash, () => window.location.hash);
  return useMemo(() => parseRoute(hash), [hash]);
}
