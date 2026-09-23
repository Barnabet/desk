import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { DEFAULT_POLICY } from '@desk/protocol';
import { evaluatePolicy } from '../policy/evaluate';
import { testToolContext } from '../testing/context';
import { braveProvider, createWebSearchTool, defaultSearchProvider, duckDuckGoProvider, webFetchTool } from './web';

const para = 'Desk coordinates parallel threads that do the work while the coordinator reviews and assembles the results. '.repeat(6);
const ARTICLE = `<!doctype html><html><head><title>Desk Article</title><script>window.evil = 1; console.log("tracking")</script></head>
<body><nav>Home | About</nav><article><h1>How Desk Works</h1><p>${para}</p><p>${para}</p><h2>Threads</h2><p>${para}</p></article>
<footer>Copyright</footer></body></html>`;
const DDG = `<html><body>
<div class="result"><h2 class="result__title"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fx%3D1&amp;rut=abc">Example <b>A</b></a></h2>
<a class="result__snippet" href="#">Snippet about A</a></div>
<div class="result"><h2 class="result__title"><a class="result__a" href="https://example.org/b">Example B</a></h2>
<a class="result__snippet" href="#">Snippet about B</a></div>
</body></html>`;

let server: Server;
let base: string;
beforeAll(async () => {
  server = createServer((req, res) => {
    const url = new URL(req.url ?? '/', 'http://x');
    if (url.pathname === '/article') return res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }).end(ARTICLE);
    if (url.pathname === '/data.json') return res.writeHead(200, { 'content-type': 'application/json' }).end('{"ok":true}');
    if (url.pathname === '/big') return res.writeHead(200, { 'content-type': 'text/plain' }).end('x'.repeat(150_000));
    if (url.pathname === '/ddg') return res.writeHead(200, { 'content-type': 'text/html' }).end(url.searchParams.get('q') === 'desk' ? DDG : '<html></html>');
    if (url.pathname === '/brave') {
      if (req.headers['x-subscription-token'] !== 'key') return res.writeHead(401).end();
      return res
        .writeHead(200, { 'content-type': 'application/json' })
        .end(JSON.stringify({ web: { results: [{ title: 'Brave A', url: 'https://a.dev', description: 'About <strong>A</strong>' }] } }));
    }
    res.writeHead(404).end('nope');
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
afterAll(() => new Promise<void>((r) => server.close(() => r())));

const ctx = () => testToolContext('/tmp');

describe('web_fetch', () => {
  it('converts an HTML article to markdown without scripts', async () => {
    const out = String(await webFetchTool.execute({ url: `${base}/article` }, ctx()));
    expect(out).toContain('How Desk Works');
    expect(out).toContain('Desk coordinates parallel threads');
    expect(out).toMatch(/Threads/);
    expect(out).not.toContain('tracking');
    expect(out).toContain(`Source: ${base}/article`);
  });

  it('returns non-HTML bodies raw', async () => {
    expect(String(await webFetchTool.execute({ url: `${base}/data.json` }, ctx()))).toContain('{"ok":true}');
  });

  it('caps output at 100k characters', async () => {
    const out = String(await webFetchTool.execute({ url: `${base}/big` }, ctx()));
    expect(out.length).toBeLessThan(100_300);
    expect(out).toContain('[truncated at 100000 characters]');
  });

  it('throws on HTTP errors and non-http URLs', async () => {
    await expect(webFetchTool.execute({ url: `${base}/missing` }, ctx())).rejects.toThrow(/404/);
    await expect(webFetchTool.execute({ url: 'file:///etc/passwd' }, ctx())).rejects.toThrow(/http/);
  });

  it('is gated by domain', () => {
    expect(evaluatePolicy(webFetchTool, { url: 'https://docs.dev/x' }, DEFAULT_POLICY, { sandboxAvailable: true }).action).toBe('allow');
    expect(evaluatePolicy(webFetchTool, { url: 'https://docs.dev/x' }, [], { sandboxAvailable: true }).action).toBe('ask');
    expect(
      evaluatePolicy(webFetchTool, { url: 'https://evil.dev/x' }, [{ tool: 'web_fetch', match: { domain: 'evil.dev' }, action: 'deny' }], { sandboxAvailable: true }).action,
    ).toBe('deny');
  });
});

describe('web_search', () => {
  it('parses DuckDuckGo HTML results and decodes redirect links', async () => {
    const results = await duckDuckGoProvider(`${base}/ddg`)('desk', new AbortController().signal);
    expect(results).toEqual([
      { title: 'Example A', url: 'https://example.com/a?x=1', snippet: 'Snippet about A' },
      { title: 'Example B', url: 'https://example.org/b', snippet: 'Snippet about B' },
    ]);
  });

  it('parses Brave API results', async () => {
    const results = await braveProvider('key', `${base}/brave`)('desk', new AbortController().signal);
    expect(results).toEqual([{ title: 'Brave A', url: 'https://a.dev', snippet: 'About A' }]);
  });

  it('formats results as a numbered list', async () => {
    const tool = createWebSearchTool(duckDuckGoProvider(`${base}/ddg`));
    const out = String(await tool.execute({ query: 'desk' }, ctx()));
    expect(out).toBe('1. Example A\n   https://example.com/a?x=1\n   Snippet about A\n\n2. Example B\n   https://example.org/b\n   Snippet about B');
    expect(String(await tool.execute({ query: 'none' }, ctx()))).toBe('No results');
  });

  it('picks Brave when BRAVE_API_KEY is set', () => {
    expect(defaultSearchProvider({ BRAVE_API_KEY: 'k' }).name).toBe('brave');
    expect(defaultSearchProvider({}).name).toBe('duckduckgo');
  });
});
