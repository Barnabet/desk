import { mkdtemp, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { DEFAULT_POLICY } from '@desk/protocol';
import { macKeychain } from '../model/endpoint';
import { testServices, testToolContext } from '../testing/context';
import { createWebKeys, WEB_KEYCHAIN_SERVICES } from '../web/keys';
import { createWebFetchTool, webCrawlTool, webExtractTool, webMapTool, webSearchTool } from './web';

// Live: the real Serper and Firecrawl with the user's keys (env DESK_SERPER_API_KEY / DESK_FIRECRAWL_API_KEY, else
// the Keychain). Spends roughly 15 credits on each. Run with: DESK_LIVE=1 pnpm exec vitest run packages/core/src/tools/web.live.test.ts
const keys = createWebKeys({
  env: process.env,
  keychains: process.platform === 'darwin' ? { serper: macKeychain(undefined, WEB_KEYCHAIN_SERVICES.serper), firecrawl: macKeychain(undefined, WEB_KEYCHAIN_SERVICES.firecrawl) } : null,
});
const ready = !!keys.get('serper') && !!keys.get('firecrawl');

describe.skipIf(!ready)('live: web tools on Serper and Firecrawl', () => {
  let workspace: string;
  beforeAll(async () => {
    workspace = await mkdtemp(join(tmpdir(), 'desk-live-web-'));
  });
  afterAll(() => rm(workspace, { recursive: true, force: true }));
  const ctx = () => testToolContext(workspace, { services: testServices({ web: { key: (p) => keys.get(p), endpoints: {} } }), signal: AbortSignal.timeout(280_000) });
  const fetchTool = createWebFetchTool({ policy: () => DEFAULT_POLICY });

  it.each([
    ['web', 'Model Context Protocol specification'],
    ['news', 'European Central Bank interest rates'],
    ['scholar', 'attention is all you need transformer'],
  ] as const)('searches %s', async (type, query) => {
    const out = String(await webSearchTool.execute({ query, type, num: 5 }, ctx()));
    expect(out.endsWith('(via Serper)')).toBe(true);
    expect(out).toMatch(/^1\. /m);
    if (type === 'scholar') expect(out).toMatch(/cited by \d+/);
    if (type === 'news') expect(out).toMatch(/ · /);
  });

  it('reads a JavaScript-rendered page', async () => {
    const out = String(await fetchTool.execute({ url: 'https://react.dev/learn' }, ctx()));
    expect(out.endsWith('(via Firecrawl)')).toBe(true);
    expect(out.length).toBeGreaterThan(1000);
  });

  it('reads the first pages of a PDF', async () => {
    const out = String(await fetchTool.execute({ url: 'https://arxiv.org/pdf/1706.03762', pdf_max_pages: 2 }, ctx()));
    expect(out.endsWith('(via Firecrawl)')).toBe(true);
    expect(out).toMatch(/[Aa]ttention/);
  });

  it('maps a site', async () => {
    const out = String(await webMapTool.execute({ url: 'https://docs.python.org/3/tutorial/', limit: 5 }, ctx()));
    expect(out).toMatch(/\d+ URLs/);
    expect(out).toContain('https://docs.python.org/');
  });

  it('crawls three pages to files', async () => {
    const out = String(await webCrawlTool.execute({ url: 'https://docs.python.org/3/tutorial/', limit: 3, include_paths: ['^/3/tutorial/'] }, ctx()));
    const folder = /saved to (\S+?)(?: \(|\.$|\.\n)/m.exec(out)?.[1] ?? '';
    expect(folder).toContain(join('web', 'crawls'));
    const files = await readdir(folder);
    expect(files).toContain('index.json');
    expect(files.filter((f) => f.endsWith('.md')).length).toBeGreaterThan(0);
  });

  it('extracts a field as JSON', async () => {
    const out = String(await webExtractTool.execute({ url: 'https://example.com', prompt: 'The main heading of the page', schema: { type: 'object', properties: { heading: { type: 'string' } } } }, ctx()));
    expect(out).toMatch(/"heading": ".*Example/);
  });
});
