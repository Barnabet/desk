import { execFileSync } from 'node:child_process';
import { mkdtemp, readdir, readFile, realpath, rm } from 'node:fs/promises';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { DEFAULT_POLICY } from '@desk/protocol';
import { deriveGrant } from '../automations/grants';
import { evaluatePolicy } from '../policy/evaluate';
import { testServices, testToolContext } from '../testing/context';
import type { Tool } from './types';
import { createWebCrawlTool, createWebExtractTool, createWebMapTool, webCrawlTool, webExtractTool, webMapTool, webTools } from './web';

const seen: Array<{ method: string; path: string; body: Record<string, unknown> }> = [];
let server: Server;
let base: string;
const SECRET_SENTENCE = 'Only this page says the moon is made of basalt.';

beforeAll(async () => {
  server = createServer((req, res) => {
    let raw = '';
    req.on('data', (c) => (raw += c));
    req.on('end', () => {
      const s = { method: req.method ?? '', path: req.url ?? '', body: raw ? (JSON.parse(raw) as Record<string, unknown>) : {} };
      seen.push(s);
      const send = (body: unknown) => res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify(body));
      if (s.path === '/map') return send({ success: true, links: [{ url: 'https://docs.site.org/a', title: 'Guide A' }, { url: 'https://docs.site.org/b' }] });
      if (s.path === '/crawl') return send({ success: true, id: 'job1' });
      if (s.path === '/crawl/job1')
        return send({
          status: 'completed',
          creditsUsed: 2,
          data: [
            { markdown: `# Intro\n\n${SECRET_SENTENCE}`, metadata: { title: 'Intro', sourceURL: 'https://docs.site.org/intro' } },
            { markdown: '# Setup\n\nInstall it.', metadata: { title: 'Setup', sourceURL: 'https://docs.site.org/setup' } },
          ],
        });
      if (s.path === '/crawl/job1/errors') return send({ errors: [], robotsBlocked: ['https://docs.site.org/admin'] });
      if (s.path === '/scrape') return send({ success: true, data: { markdown: '# P', json: { price: 12, currency: 'EUR' }, metadata: { url: 'https://shop.site.org/item', statusCode: 200 } } });
      res.writeHead(404).end();
    });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
afterAll(() => server.close());

let workspace: string;
beforeEach(async () => {
  seen.length = 0;
  workspace = await mkdtemp(join(tmpdir(), 'desk-site-'));
});
afterEach(() => rm(workspace, { recursive: true, force: true }));

const ctx = (key: string | null = 'FK') =>
  testToolContext(workspace, { services: testServices({ web: { key: (p) => (p === 'firecrawl' ? key : null), endpoints: { firecrawl: base } } }) });
const open = { archivable: () => true };

describe('Firecrawl-only tools without a key or for private URLs', () => {
  const tools = () => [
    [createWebMapTool(open), { url: 'https://docs.site.org/' }],
    [createWebCrawlTool(open), { url: 'https://docs.site.org/' }],
    [createWebExtractTool(open), { url: 'https://docs.site.org/', prompt: 'the price' }],
  ] as const;

  it('names the missing key and the keyless skill', async () => {
    for (const [tool, input] of tools()) await expect((tool as Tool).execute(input, ctx(null))).rejects.toThrow(/needs the Firecrawl key.*web-research-keyless/);
    expect(seen).toEqual([]);
  });

  it('refuses private and credential URLs without calling Firecrawl', async () => {
    await expect(createWebMapTool({ archivable: () => false }).execute({ url: 'https://intranet.corp.example/' }, ctx())).rejects.toThrow(/local or private/);
    await expect(createWebCrawlTool(open).execute({ url: 'https://docs.site.org/?token=abcdef0123456789abcdef0123456789' }, ctx())).rejects.toThrow(/credentials/);
    expect(seen).toEqual([]);
  });
});

describe('web_map', () => {
  it('lists the URLs with their titles and a count', async () => {
    const out = String(await createWebMapTool(open).execute({ url: 'https://docs.site.org/', search: 'guide', limit: 50 }, ctx()));
    expect(seen[0]?.body).toEqual({ url: 'https://docs.site.org/', search: 'guide', limit: 50, includeSubdomains: false });
    expect(out).toContain('2 URLs');
    expect(out).toContain('https://docs.site.org/a — Guide A');
    expect(out).toContain('https://docs.site.org/b\n');
    expect(out.endsWith('(via Firecrawl)')).toBe(true);
  });
});

describe('web_crawl', () => {
  it('saves every page to files and returns only an index', async () => {
    const out = String(await createWebCrawlTool(open).execute({ url: 'https://docs.site.org/', limit: 5 }, ctx()));
    expect(seen[0]).toMatchObject({ path: '/crawl', body: { limit: 5, allowSubdomains: false, allowExternalLinks: false } });
    expect(out).not.toContain(SECRET_SENTENCE);
    const folder = /saved to (\S+)/.exec(out)?.[1] ?? '';
    expect(folder.startsWith(join(await realpath(workspace), 'web', 'crawls', 'docs.site.org-'))).toBe(true);
    const files = (await readdir(folder)).sort();
    expect(files).toContain('index.json');
    const pages = files.filter((f) => f.endsWith('.md'));
    expect(pages).toHaveLength(2);
    const texts = await Promise.all(pages.map((f) => readFile(join(folder, f), 'utf8')));
    expect(texts.some((t) => t.includes(SECRET_SENTENCE) && t.includes('URL: https://docs.site.org/intro'))).toBe(true);
    expect(out).toContain('https://docs.site.org/setup');
    expect(out).toContain('Setup');
    expect(out).toMatch(/credits used: 2/);
    expect(out).toContain('Blocked by robots.txt: https://docs.site.org/admin');
    const index = JSON.parse(await readFile(join(folder, 'index.json'), 'utf8')) as Array<{ file: string; url: string; title?: string }>;
    expect(index.map((p) => p.url)).toEqual(['https://docs.site.org/intro', 'https://docs.site.org/setup']);
  });

  it('keeps saved pages out of a git workspace', async () => {
    execFileSync('git', ['init', '-q'], { cwd: workspace });
    await createWebCrawlTool(open).execute({ url: 'https://docs.site.org/', limit: 5 }, ctx());
    expect(await readFile(join(workspace, 'web', '.gitignore'), 'utf8')).toBe('*\n');
    expect(execFileSync('git', ['status', '--porcelain', '--untracked-files=all'], { cwd: workspace, encoding: 'utf8' })).toBe('');
  });

  it('caps a crawl at 50 pages', () => {
    expect(webCrawlTool.input.safeParse({ url: 'https://docs.site.org/', limit: 51 }).success).toBe(false);
    expect(webCrawlTool.input.safeParse({ url: 'https://docs.site.org/', limit: 50 }).success).toBe(true);
  });
});

describe('web_extract', () => {
  it('asks Firecrawl for JSON with the prompt and schema and prints it', async () => {
    const schema = { type: 'object', properties: { price: { type: 'number' } } };
    const out = String(await createWebExtractTool(open).execute({ url: 'https://shop.site.org/item', prompt: 'the price', schema }, ctx()));
    expect(seen[0]?.body).toMatchObject({ url: 'https://shop.site.org/item', formats: ['markdown', { type: 'json', prompt: 'the price', schema }] });
    expect(out).toContain('"price": 12');
    expect(out.endsWith('(via Firecrawl)')).toBe(true);
  });
});

describe('policy and grants for the site tools', () => {
  const opts = { sandboxAvailable: true };

  it('are allowed by the default policy and follow web_fetch rules', () => {
    for (const tool of [webMapTool, webCrawlTool, webExtractTool]) {
      expect(evaluatePolicy(tool, { url: 'https://docs.site.org/' }, DEFAULT_POLICY, opts).action).toBe('allow');
      expect(evaluatePolicy(tool, { url: 'https://x.org/' }, [{ tool: 'web_fetch', match: { domain: 'x.org' }, action: 'deny' }], opts).action).toBe('deny');
    }
  });

  it('every agent with the web tools gets all five', () => {
    expect(webTools.map((t) => t.name)).toEqual(['web_fetch', 'web_search', 'web_map', 'web_crawl', 'web_extract']);
  });

  it('grants a domain to an automation, like web_fetch', () => {
    expect(deriveGrant('web_crawl', { domain: 'docs.site.org' }, 'n')).toEqual({ tool: 'web_crawl', match: { domain: 'docs.site.org' }, action: 'allow' });
    expect(deriveGrant('web_extract', { domain: 'a.b' }, 'n')).toEqual({ tool: 'web_extract', match: { domain: 'a.b' }, action: 'allow' });
  });
});

describe('policy rules written for the site tools', () => {
  it('win over the web_fetch rule in the default policy', () => {
    const opts = { sandboxAvailable: true };
    const policy = DEFAULT_POLICY.map((r) => (r.tool === 'web_crawl' ? { ...r, action: 'ask' as const } : r));
    expect(evaluatePolicy(webCrawlTool, { url: 'https://docs.site.org/' }, policy, opts).action).toBe('ask');
    const denyMap = DEFAULT_POLICY.map((r) => (r.tool === 'web_map' ? { ...r, action: 'deny' as const } : r));
    expect(evaluatePolicy(webMapTool, { url: 'https://docs.site.org/' }, denyMap, opts).action).toBe('deny');
  });
});
