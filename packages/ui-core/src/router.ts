export type ProjectTab = 'conversation' | 'threads' | 'automations' | 'library' | 'memory' | 'settings';
export const PROJECT_TABS: ProjectTab[] = ['conversation', 'threads', 'automations', 'library', 'memory', 'settings'];

/** An automation's sub-tabs. */
export type AutomationView = 'design' | 'runs' | 'versions' | 'grants';

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
   * Automations: `automationId` + `view` (and `runId` on runs) open one; `draft` is a new one not saved yet.
   */
  | {
      name: 'project';
      id: string;
      tab: ProjectTab;
      threadId?: string;
      at?: number;
      file?: string;
      q?: string;
      automationId?: string;
      view?: AutomationView;
      runId?: string;
      draft?: string;
    };

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
      if (tab === 'automations') {
        if (parts[3] === 'new') {
          const name = q.get('name');
          return name ? { name: 'project', id, tab, draft: name } : { name: 'project', id, tab };
        }
        const automationId = parts[3];
        if (!automationId) return { name: 'project', id, tab };
        if (parts[4] === 'runs') return parts[5] ? { name: 'project', id, tab, automationId, view: 'runs', runId: parts[5] } : { name: 'project', id, tab, automationId, view: 'runs' };
        if (parts[4] === 'versions' || parts[4] === 'grants') return { name: 'project', id, tab, automationId, view: parts[4] };
        return { name: 'project', id, tab, automationId, view: 'design' };
      }
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

/** The part of an automations route after `/automations`. */
function automationPath(r: Extract<Route, { name: 'project' }>): string {
  if (r.draft) return `/new?name=${enc(r.draft)}`;
  if (!r.automationId) return '';
  const base = `/${enc(r.automationId)}`;
  if (r.view === 'runs') return `${base}/runs${r.runId ? `/${enc(r.runId)}` : ''}`;
  if (r.view === 'versions' || r.view === 'grants') return `${base}/${r.view}`;
  return base;
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
      if (r.tab === 'automations') return `#/p/${enc(r.id)}/automations${automationPath(r)}`;
      return `#/p/${enc(r.id)}/${r.tab}${r.threadId ? `/${enc(r.threadId)}` : ''}${r.file ? `?file=${enc(r.file)}` : r.q ? `?q=${enc(r.q)}` : r.at !== undefined ? `?at=${r.at}` : ''}`;
  }
}
