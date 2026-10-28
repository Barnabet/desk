import type { WebKeysStatus, WebProvider } from '@desk/protocol';

/** One provider's row on System → Web search and fetch. */
export type WebKeyRow = {
  provider: WebProvider;
  name: 'Serper' | 'Firecrawl';
  powers: string;
  statusLabel: 'Not set' | 'Set' | 'Set from environment';
  /** False for a key from deskd's environment, which the UI cannot change. */
  editable: boolean;
  /** Only a Keychain key can be removed. */
  removable: boolean;
  /** Where the user gets a key. */
  dashboardUrl: string;
};

const PROVIDERS: Array<Pick<WebKeyRow, 'provider' | 'name' | 'powers' | 'dashboardUrl'>> = [
  { provider: 'serper', name: 'Serper', powers: 'Search', dashboardUrl: 'https://serper.dev/api-key' },
  { provider: 'firecrawl', name: 'Firecrawl', powers: 'Reading pages, maps, crawls and extraction', dashboardUrl: 'https://www.firecrawl.dev/app/api-keys' },
];

/** The two providers' rows, in display order. */
export function webKeyRows(status: WebKeysStatus): WebKeyRow[] {
  return PROVIDERS.map((p) => {
    const s = status[p.provider];
    return {
      ...p,
      statusLabel: !s.set ? 'Not set' : s.source === 'env' ? 'Set from environment' : 'Set',
      editable: s.source !== 'env',
      removable: s.source === 'keychain',
    };
  });
}

/** The environment variable that sets a provider's key in deskd. */
export const WEB_KEY_ENV: Record<WebProvider, string> = { serper: 'DESK_SERPER_API_KEY', firecrawl: 'DESK_FIRECRAWL_API_KEY' };
