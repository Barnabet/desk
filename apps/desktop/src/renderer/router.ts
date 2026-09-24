import { useMemo, useSyncExternalStore } from 'react';

export type ProjectTab = 'conversation' | 'threads' | 'library' | 'memory' | 'settings';
export const PROJECT_TABS: ProjectTab[] = ['conversation', 'threads', 'library', 'memory', 'settings'];

export type Route =
  | { name: 'onboarding' }
  | { name: 'map'; newProject?: boolean }
  | { name: 'attention'; item?: string }
  | { name: 'skills'; skill?: string }
  | { name: 'system' }
  | { name: 'project'; id: string; tab: ProjectTab; threadId?: string };

const enc = encodeURIComponent;

export function parseRoute(hash: string): Route {
  const [path = '', query = ''] = hash.replace(/^#/, '').split('?');
  const q = new URLSearchParams(query);
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  switch (parts[0]) {
    case 'onboarding':
      return { name: 'onboarding' };
    case 'attention': {
      const item = q.get('item');
      return item ? { name: 'attention', item } : { name: 'attention' };
    }
    case 'skills':
      return parts[1] ? { name: 'skills', skill: parts[1] } : { name: 'skills' };
    case 'system':
      return { name: 'system' };
    case 'p': {
      const id = parts[1];
      if (!id) return { name: 'map' };
      const tab = PROJECT_TABS.includes(parts[2] as ProjectTab) ? (parts[2] as ProjectTab) : 'conversation';
      return tab === 'threads' && parts[3] ? { name: 'project', id, tab, threadId: parts[3] } : { name: 'project', id, tab };
    }
    default:
      return q.get('new') === '1' ? { name: 'map', newProject: true } : { name: 'map' };
  }
}

export function href(r: Route): string {
  switch (r.name) {
    case 'onboarding':
      return '#/onboarding';
    case 'map':
      return r.newProject ? '#/map?new=1' : '#/map';
    case 'attention':
      return r.item ? `#/attention?item=${enc(r.item)}` : '#/attention';
    case 'skills':
      return r.skill ? `#/skills/${enc(r.skill)}` : '#/skills';
    case 'system':
      return '#/system';
    case 'project':
      return `#/p/${enc(r.id)}/${r.tab}${r.threadId ? `/${enc(r.threadId)}` : ''}`;
  }
}

export function navigate(to: Route | string): void {
  const target = typeof to === 'string' ? to : href(to);
  window.location.hash = target.replace(/^#/, '');
}

const subscribeHash = (fn: () => void) => {
  window.addEventListener('hashchange', fn);
  return () => window.removeEventListener('hashchange', fn);
};

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribeHash, () => window.location.hash);
  return useMemo(() => parseRoute(hash), [hash]);
}
