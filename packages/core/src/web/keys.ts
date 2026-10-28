import type { WebKeyStatus, WebProvider } from '@desk/protocol';
import type { Keychain } from '../model/endpoint';

export type { WebProvider } from '@desk/protocol';

export const WEB_PROVIDERS: readonly WebProvider[] = ['serper', 'firecrawl'];

/** The login Keychain item each provider's key is stored under (account `default`). */
export const WEB_KEYCHAIN_SERVICES: Record<WebProvider, string> = {
  serper: 'Desk Serper API key',
  firecrawl: 'Desk Firecrawl API key',
};

/** Development overrides, which win over the Keychain (like `DESK_OPENAI_*`). */
const ENV_VARS: Record<WebProvider, string> = {
  serper: 'DESK_SERPER_API_KEY',
  firecrawl: 'DESK_FIRECRAWL_API_KEY',
};

/** deskd's web API keys. Only deskd holds them: tools read them through `RuntimeServices.web`, never the environment. */
export interface WebKeys {
  get(p: WebProvider): string | null;
  status(p: WebProvider): WebKeyStatus;
  set(p: WebProvider, key: string): WebKeyStatus;
  remove(p: WebProvider): WebKeyStatus;
}

/** Keys from deskd's environment first, then the Keychain; read on every call, so a saved key applies at once. */
export function createWebKeys(o: { env: NodeJS.ProcessEnv; keychains: Record<WebProvider, Keychain> | null }): WebKeys {
  const fromEnv = (p: WebProvider) => o.env[ENV_VARS[p]]?.trim() || null;
  const resolve = (p: WebProvider): { key: string | null; source: WebKeyStatus['source'] } => {
    const env = fromEnv(p);
    if (env) return { key: env, source: 'env' };
    const stored = o.keychains?.[p].get() ?? null;
    return stored ? { key: stored, source: 'keychain' } : { key: null, source: null };
  };
  const status = (p: WebProvider): WebKeyStatus => {
    const { key, source } = resolve(p);
    return { set: key !== null, source };
  };
  const keychain = (p: WebProvider) => {
    if (!o.keychains) throw new Error('Storing a web key needs the macOS Keychain; set DESK_SERPER_API_KEY / DESK_FIRECRAWL_API_KEY instead');
    return o.keychains[p];
  };
  return {
    get: (p) => resolve(p).key,
    status,
    set(p, key) {
      keychain(p).set(key);
      return status(p);
    },
    remove(p) {
      keychain(p).delete();
      return status(p);
    },
  };
}
