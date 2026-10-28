import type { WebKeyTestResult } from '@desk/protocol';
import type { WebAccess } from '../tools/types';
import { firecrawlScrape } from './firecrawl';
import type { WebProvider } from './keys';
import { serperSearch } from './serper';

/**
 * Checks a key with one cheap call (1 credit each): a one-result Serper search, or a Firecrawl scrape of example.com.
 * The error never contains the key.
 */
export async function testWebKey(p: WebProvider, key: string, o: { endpoints?: WebAccess['endpoints']; signal?: AbortSignal } = {}): Promise<WebKeyTestResult> {
  const signal = o.signal ?? AbortSignal.timeout(60_000);
  try {
    if (p === 'serper') await serperSearch(key, { q: 'desk', type: 'web', num: 1 }, signal, o.endpoints?.serper);
    else await firecrawlScrape(key, { url: 'https://example.com', mainOnly: true }, signal, o.endpoints?.firecrawl);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: (err instanceof Error ? err.message : String(err)).split(key).join('***') };
  }
}
