import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { WebApiError } from './errors';
import { firecrawlCrawl, firecrawlMap, firecrawlScrape } from './firecrawl';

type Seen = { method: string; path: string; auth: string | undefined; body: Record<string, unknown> };
const seen: Seen[] = [];
/** Per-test hooks: called on every request before the default routes. */
let hook: ((s: Seen) => { status: number; body: unknown; headers?: Record<string, string> } | undefined) | undefined;
/** How many status polls a crawl stays "scraping". */
let scrapingPolls = 1;
let polls = 0;
let server: Server;
let base: string;

const page = (n: number) => ({ markdown: `# Page ${n}\n\nBody ${n}`, metadata: { title: `Page ${n}`, sourceURL: `https://site.test/p${n}`, statusCode: 200 } });

beforeAll(async () => {
  server = createServer((req, res) => {
    let raw = '';
    req.on('data', (c) => (raw += c));
    req.on('end', () => {
      const s: Seen = { method: req.method ?? '', path: req.url ?? '', auth: req.headers.authorization, body: raw ? JSON.parse(raw) : {} };
      seen.push(s);
      const send = (status: number, body: unknown, headers: Record<string, string> = {}) =>
        res.writeHead(status, { 'content-type': 'application/json', ...headers }).end(JSON.stringify(body));
      const custom = hook?.(s);
      if (custom) return send(custom.status, custom.body, custom.headers);
      if (s.method === 'POST' && s.path === '/scrape')
        return send(200, { success: true, data: { markdown: '# Hi', metadata: { title: 'Hi', url: String(s.body.url), statusCode: 200, contentType: 'text/html' } } });
      if (s.method === 'POST' && s.path === '/map') return send(200, { success: true, links: [{ url: 'https://site.test/a', title: 'A' }, { url: 'https://site.test/b' }] });
      if (s.method === 'POST' && s.path === '/crawl') return send(200, { success: true, id: 'c1', url: `${base}/crawl/c1` });
      if (s.method === 'GET' && s.path === '/crawl/c1') {
        polls++;
        const done = polls > scrapingPolls;
        return send(200, { status: done ? 'completed' : 'scraping', total: 3, completed: done ? 3 : 1, creditsUsed: 3, data: [page(1), page(2)], next: `${base}/crawl/c1?skip=2` });
      }
      if (s.method === 'GET' && s.path === '/crawl/c1?skip=2') return send(200, { status: 'completed', data: [page(3)] });
      if (s.method === 'GET' && s.path === '/crawl/c1/errors') return send(200, { errors: [], robotsBlocked: ['https://site.test/private'] });
      if (s.method === 'DELETE' && s.path === '/crawl/c1') return send(200, { status: 'cancelled' });
      send(404, { success: false, error: 'no route' });
    });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
afterAll(() => server.close());
beforeEach(() => {
  seen.length = 0;
  hook = undefined;
  scrapingPolls = 1;
  polls = 0;
});

const signal = new AbortController().signal;

describe('firecrawlScrape', () => {
  it('sends Markdown-only main content with the 20-page PDF cap and the 45 s timeout by default', async () => {
    const r = await firecrawlScrape('K', { url: 'https://site.test/', mainOnly: true, pdfMaxPages: 20 }, signal, base);
    expect(seen[0]?.auth).toBe('Bearer K');
    expect(seen[0]?.body).toEqual({
      url: 'https://site.test/',
      formats: ['markdown'],
      onlyMainContent: true,
      parsers: [{ type: 'pdf', maxPages: 20 }],
      timeout: 45000,
    });
    expect(r).toMatchObject({ markdown: '# Hi', title: 'Hi', url: 'https://site.test/', statusCode: 200, contentType: 'text/html' });
  });

  it('asks for links, a screenshot, a fresh copy, a wait and JSON only when told to', async () => {
    await firecrawlScrape(
      'K',
      { url: 'https://site.test/', mainOnly: false, waitMs: 2000, fresh: true, screenshot: true, links: true, json: { prompt: 'price', schema: { type: 'object' } } },
      signal,
      base,
    );
    const body = seen[0]?.body ?? {};
    expect(body.formats).toEqual(['markdown', 'links', 'screenshot', { type: 'json', prompt: 'price', schema: { type: 'object' } }]);
    expect(body).toMatchObject({ onlyMainContent: false, waitFor: 2000, maxAge: 0 });
    expect(body).not.toHaveProperty('skipTlsVerification');
    expect(body).not.toHaveProperty('proxy');
  });

  it('retries a 429 once when Retry-After is short', async () => {
    let n = 0;
    hook = (s) => (s.path === '/scrape' && n++ === 0 ? { status: 429, body: { success: false, error: 'Rate limit' }, headers: { 'retry-after': '0' } } : undefined);
    const r = await firecrawlScrape('K', { url: 'https://site.test/', mainOnly: true }, signal, base);
    expect(r.markdown).toBe('# Hi');
    expect(seen.filter((s) => s.path === '/scrape')).toHaveLength(2);
  });

  it('does not wait out a long Retry-After', async () => {
    hook = (s) => (s.path === '/scrape' ? { status: 429, body: { success: false, error: 'Rate limit' }, headers: { 'retry-after': '30' } } : undefined);
    const err = (await firecrawlScrape('K', { url: 'https://site.test/', mainOnly: true }, signal, base).catch((e: unknown) => e)) as WebApiError;
    expect(err).toBeInstanceOf(WebApiError);
    expect(err.status).toBe(429);
    expect(seen).toHaveLength(1);
  });

  it('reports insufficient credits as a key problem, without the key', async () => {
    hook = () => ({ status: 402, body: { success: false, error: 'Insufficient credits' } });
    const err = (await firecrawlScrape('fc-secret', { url: 'https://site.test/', mainOnly: true }, signal, base).catch((e: unknown) => e)) as WebApiError;
    expect(err.keyProblem).toBe(true);
    expect(err.provider).toBe('firecrawl');
    expect(err.message).toContain('Insufficient credits');
    expect(err.message).not.toContain('fc-secret');
  });

  it('treats success: false in a 200 answer as a failure', async () => {
    hook = () => ({ status: 200, body: { success: false, error: 'Could not scrape' } });
    await expect(firecrawlScrape('K', { url: 'https://site.test/', mainOnly: true }, signal, base)).rejects.toThrow(/Could not scrape/);
  });
});

describe('firecrawlMap', () => {
  it('lists URLs with titles, without subdomains by default', async () => {
    const links = await firecrawlMap('K', { url: 'https://site.test/', limit: 100, includeSubdomains: false }, signal, base);
    expect(seen[0]?.body).toEqual({ url: 'https://site.test/', limit: 100, includeSubdomains: false });
    expect(links).toEqual([{ url: 'https://site.test/a', title: 'A' }, { url: 'https://site.test/b' }]);
  });

  it('accepts plain string links too', async () => {
    hook = (s) => (s.path === '/map' ? { status: 200, body: { success: true, links: ['https://site.test/x'] } } : undefined);
    expect(await firecrawlMap('K', { url: 'https://site.test/', search: 'docs', limit: 5, includeSubdomains: true }, signal, base)).toEqual([{ url: 'https://site.test/x' }]);
    expect(seen[0]?.body).toMatchObject({ search: 'docs' });
  });
});

describe('firecrawlCrawl', () => {
  const req = { url: 'https://site.test/', limit: 10, mainOnly: true };

  it('stays on the start host, waits for completion and collects every chunk', async () => {
    const out = await firecrawlCrawl('K', { ...req, maxDepth: 2, includePaths: ['^/docs'], excludePaths: ['^/blog'] }, signal, { base, pollMs: 5 });
    expect(seen[0]?.body).toMatchObject({
      url: 'https://site.test/',
      limit: 10,
      maxDiscoveryDepth: 2,
      includePaths: ['^/docs'],
      excludePaths: ['^/blog'],
      allowSubdomains: false,
      allowExternalLinks: false,
      crawlEntireDomain: false,
      sitemap: 'include',
      scrapeOptions: { formats: ['markdown'], onlyMainContent: true },
    });
    expect(out.status).toBe('completed');
    expect(out.pages.map((p) => p.url)).toEqual(['https://site.test/p1', 'https://site.test/p2', 'https://site.test/p3']);
    expect(out.pages[0]).toEqual({ url: 'https://site.test/p1', title: 'Page 1', markdown: '# Page 1\n\nBody 1' });
    expect(out.creditsUsed).toBe(3);
    expect(out.robotsBlocked).toEqual(['https://site.test/private']);
    expect(seen.some((s) => s.method === 'DELETE')).toBe(false);
  });

  it('cancels the crawl when the call is aborted, keeping the pages it has', async () => {
    scrapingPolls = 1000;
    const ac = new AbortController();
    hook = (s) => {
      if (s.method === 'GET' && s.path === '/crawl/c1') queueMicrotask(() => ac.abort());
      return undefined;
    };
    const out = await firecrawlCrawl('K', req, ac.signal, { base, pollMs: 5 });
    expect(out.status).toBe('cancelled');
    expect(seen.some((s) => s.method === 'DELETE' && s.path === '/crawl/c1')).toBe(true);
    expect(out.pages.length).toBeGreaterThan(0);
  });

  it('cancels the crawl at the deadline', async () => {
    scrapingPolls = 1000;
    const out = await firecrawlCrawl('K', req, signal, { base, pollMs: 5, deadlineMs: 50 });
    expect(out.status).toBe('timeout');
    expect(seen.some((s) => s.method === 'DELETE' && s.path === '/crawl/c1')).toBe(true);
    expect(out.pages.map((p) => p.url)).toContain('https://site.test/p1');
  });

  it('reports a failed crawl with what it got', async () => {
    hook = (s) => (s.method === 'GET' && s.path === '/crawl/c1' ? { status: 200, body: { status: 'failed', data: [page(1)] } } : undefined);
    const out = await firecrawlCrawl('K', req, signal, { base, pollMs: 5 });
    expect(out.status).toBe('failed');
    expect(out.pages).toHaveLength(1);
  });
});
