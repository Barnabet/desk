import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_POLICY, type PolicyRule } from '@desk/protocol';
import { evaluatePolicy } from '../policy/evaluate';
import { testServices, testToolContext } from '../testing/context';
import { createHarness, FAKE_MODEL, newRuntime } from '../testing/harness';
import {
  archiveTarget,
  bingProvider,
  braveProvider,
  createWebFetchTool,
  createWebSearchTool,
  defaultSearchProvider,
  duckDuckGoProvider,
  fallbackSearchProvider,
  isArchivable,
  isPublicHost,
  marginaliaProvider,
  snapshotUrls,
  webFetchTool,
  type HostLookup,
  type SearchProvider,
  type SearchResult,
} from './web';

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
const rss = (items: Array<[title: string, link: string, description: string]>) =>
  `<?xml version="1.0" encoding="utf-8" ?><rss version="2.0"><channel><title>Bing: q</title><link>http://www.bing.com:80/search?q=q</link>` +
  `<description>Search results</description>` +
  items.map(([t, l, d]) => `<item><title>${t}</title><link>${l}</link><description>${d}</description><pubDate>Mon, 21 Sep 2026 03:12:00 GMT</pubDate></item>`).join('') +
  `</channel></rss>`;
const NETFLIX: [string, string, string] = ['Netflix - Watch TV Shows Online', 'https://www.netflix.com/', 'Watch anywhere. Cancel anytime.'];
const SNAPSHOT = '20240501123000';

/** Paths the fake server was asked for, in order. */
const hits: string[] = [];
/** The `url` parameter of each Wayback availability lookup. */
const lookups: string[] = [];
let server: Server;
let base: string;
beforeAll(async () => {
  server = createServer((req, res) => {
    const url = new URL(req.url ?? '/', 'http://x');
    hits.push(url.pathname);
    const status = /^\/status\/(\d{3})/.exec(url.pathname);
    if (status) return res.writeHead(Number(status[1]), { 'content-type': 'text/html' }).end('<html><body>blocked</body></html>');
    if (url.pathname === '/hang' || url.pathname === '/wayback-hang/available') return; // never answers
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
    if (url.pathname === '/bing') {
      if (url.searchParams.get('format') !== 'rss') return res.writeHead(400).end();
      const q = url.searchParams.get('q');
      const items: Array<[string, string, string]> =
        q === 'desk'
          ? [['Desk &amp; threads', 'https://desk.dev/docs?a=1&amp;b=2', 'How &lt;b&gt;Desk&lt;/b&gt; works'], NETFLIX]
          : q === 'junk results'
            ? [NETFLIX]
            : q === 'typescript zod'
              ? [['TypeScript — Wikipédia', 'https://fr.wikipedia.org/wiki/TypeScript', 'TypeScript est un langage'], ['Zod: TypeScript-first schema validation', 'https://zod.dev/', 'Schemas']]
              : q === 'vitest fake timers'
                ? [['Timer Mocks | Vitest', 'https://vitest.dev/guide/mocking/timers', 'Use fake timers'], ['Vitest', 'https://vitest.dev/', 'Next generation testing framework']]
                : [];
      return res.writeHead(200, { 'content-type': 'application/rss+xml; charset=utf-8' }).end(rss(items));
    }
    if (url.pathname.startsWith('/marginalia/')) {
      const q = decodeURIComponent(url.pathname.slice('/marginalia/'.length));
      const results = q === 'nothing' ? [] : [{ url: 'https://small.dev/a', title: 'Small <b>web</b>', description: `A page about ${q}`, quality: 3.1 }];
      return res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ license: 'CC-BY-NC-SA 4.0', query: q, count: url.searchParams.get('count'), results }));
    }
    if (url.pathname === '/wayback/available') {
      const target = url.searchParams.get('url') ?? '';
      lookups.push(target);
      const snapshot = (host: string, status = '200') => ({ closest: { status, available: true, url: `${host}/web/${SNAPSHOT}/${target}`, timestamp: SNAPSHOT } });
      const snapshots = target.includes('never-archived')
        ? {}
        : target.includes('captured-404')
          ? snapshot(base, '404')
          : target.includes('broken-snapshot')
            ? snapshot(`${base}/status/503`)
            : target.includes('other-host')
              ? snapshot(base.replace('127.0.0.1', 'localhost'))
              : snapshot(base);
      return res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ url: target, archived_snapshots: snapshots }));
    }
    if (url.pathname.startsWith(`/web/${SNAPSHOT}id_/`)) return res.writeHead(200, { 'content-type': 'text/html' }).end(ARTICLE);
    if (url.pathname.startsWith(`/web/${SNAPSHOT}/`)) return res.writeHead(200, { 'content-type': 'text/html' }).end(`<p>WAYBACK TOOLBAR</p>${ARTICLE}`);
    res.writeHead(404).end('nope');
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
afterAll(() => {
  server.closeAllConnections();
  return new Promise<void>((r) => server.close(() => r()));
});
beforeEach(() => {
  hits.length = 0;
  lookups.length = 0;
});

const ctx = () => testToolContext('/tmp');
const signal = () => new AbortController().signal;

/** A loopback port nothing listens on. */
async function closedPort(): Promise<number> {
  const s = createServer();
  await new Promise<void>((r) => s.listen(0, '127.0.0.1', r));
  const { port } = s.address() as AddressInfo;
  await new Promise<void>((r) => s.close(() => r()));
  return port;
}

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

describe('web_fetch Wayback fallback', () => {
  const archiving = (opts: { policy?: PolicyRule[]; waybackApi?: string } = {}) =>
    createWebFetchTool({ waybackApi: opts.waybackApi ?? `${base}/wayback/available`, archivable: () => true, policy: () => opts.policy ?? DEFAULT_POLICY });

  it.each([403, 404, 410, 451])('returns the archived copy, labelled, when the live page answers %i', async (code) => {
    const url = `${base}/status/${code}/page`;
    const out = String(await archiving().execute({ url }, ctx()));
    expect(out).toMatch(new RegExp(`^Archived copy from 2024-05-01: the live page was unavailable \\(HTTP ${code}\\b[^)]*\\)\\.\n`));
    expect(out).toContain('How Desk Works');
    expect(out).not.toContain('WAYBACK TOOLBAR');
    expect(out).toContain(`Source: ${base}/web/${SNAPSHOT}/${url} (archived copy of ${url})`);
    expect(hits).toEqual([`/status/${code}/page`, '/wayback/available', `/web/${SNAPSHOT}id_/${url}`]);
  });

  it('returns the archived copy when the host is unreachable', async () => {
    const url = `http://127.0.0.1:${await closedPort()}/gone`;
    const out = String(await archiving().execute({ url }, ctx()));
    expect(out).toMatch(/^Archived copy from 2024-05-01: the live page was unavailable \(host unreachable: ECONNREFUSED\)\.\n/);
    expect(out).toContain('How Desk Works');
  });

  it('treats a failed DNS lookup as an unreachable host', async () => {
    const dnsFailure = new TypeError('fetch failed', { cause: Object.assign(new Error('getaddrinfo ENOTFOUND gone.example'), { code: 'ENOTFOUND' }) });
    const spy = vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(dnsFailure);
    try {
      const out = String(await archiving().execute({ url: 'https://gone.example/docs' }, ctx()));
      expect(out).toMatch(/^Archived copy from 2024-05-01: the live page was unavailable \(host unreachable: ENOTFOUND\)\.\n/);
      expect(lookups).toEqual(['https://gone.example/docs']);
    } finally {
      spy.mockRestore();
    }
  });

  it('says so when there is no archived copy', async () => {
    await expect(archiving().execute({ url: `${base}/status/404/never-archived` }, ctx())).rejects.toThrow(/HTTP 404.*no archived copy/);
    const url = `http://127.0.0.1:${await closedPort()}/never-archived`;
    await expect(archiving().execute({ url }, ctx())).rejects.toThrow(/Could not reach 127\.0\.0\.1:\d+ \(ECONNREFUSED\).*no archived copy/);
  });

  it('does not serve a capture of an error page', async () => {
    const url = `${base}/status/404/captured-404`;
    await expect(archiving().execute({ url }, ctx())).rejects.toThrow(`HTTP 404 Not Found for ${url}; the closest Wayback Machine capture is an HTTP 404 page`);
    expect(hits).not.toContain(`/web/${SNAPSHOT}id_/${url}`);
  });

  it('says so when the archive lookup fails', async () => {
    const tool = archiving({ waybackApi: `${base}/status/503` });
    await expect(tool.execute({ url: `${base}/status/404/page` }, ctx())).rejects.toThrow(/HTTP 404.*Wayback Machine lookup failed.*503/);
  });

  it('keeps the live failure in the error when the snapshot cannot be read', async () => {
    const url = `${base}/status/404/broken-snapshot`;
    await expect(archiving().execute({ url }, ctx())).rejects.toThrow(
      new RegExp(`^HTTP 404 Not Found for ${url.replace(/[.?]/g, '\\$&')}; the archived copy at ${base}/status/503/web/${SNAPSHOT}/.* could not be read \\(HTTP 503`),
    );
  });

  it('does not use the archive for other failures', async () => {
    await expect(archiving().execute({ url: `${base}/status/500/page` }, ctx())).rejects.toThrow(/HTTP 500/);
    await expect(archiving().execute({ url: `${base}/status/429/page` }, ctx())).rejects.toThrow(/HTTP 429/);
    expect(hits).not.toContain('/wayback/available');
  });

  it('never sends local URLs to the archive by default', async () => {
    const tool = createWebFetchTool({ waybackApi: `${base}/wayback/available` });
    await expect(tool.execute({ url: `${base}/status/404/page` }, ctx())).rejects.toThrow(/HTTP 404/);
    await expect(tool.execute({ url: `http://127.0.0.1:${await closedPort()}/x` }, ctx())).rejects.toThrow(/^Could not reach 127\.0\.0\.1:\d+ \(ECONNREFUSED\)$/);
    await expect(tool.execute({ url: `http://localhost.:${await closedPort()}/admin` }, ctx())).rejects.toThrow(/^Could not reach localhost\.:\d+/);
    expect(hits).toEqual(['/status/404/page']);
  });

  it('asks the archive about the URL without its fragment', async () => {
    const url = `${base}/status/404/page?lang=en#install`;
    const out = String(await archiving().execute({ url }, ctx()));
    expect(out).toContain('How Desk Works');
    expect(lookups).toEqual([`${base}/status/404/page?lang=en`]);
  });

  it('never sends URLs that carry credentials to the archive', async () => {
    for (const query of ['X-Amz-Signature=abc&X-Amz-Credential=AKIA', 'token=abc', 'accessToken=abc', 'sig=abc', 'resourcekey=0-abc', `t=${'eyJhbGciOiJIUzI1NiJ9'.repeat(2)}`]) {
      const url = `${base}/status/403/f.pdf?${query}#page=2`;
      await expect(archiving().execute({ url }, ctx())).rejects.toThrow(new RegExp(`^HTTP 403 Forbidden for .*f\\.pdf\\?[^;]*$`));
    }
    expect(lookups).toEqual([]);
  });

  it('follows the project policy for the archive', async () => {
    const url = `${base}/status/404/page`;
    const deny: PolicyRule[] = [{ tool: 'web_fetch', match: { domain: '127.0.0.1' }, action: 'deny' }, ...DEFAULT_POLICY];
    await expect(archiving({ policy: deny }).execute({ url }, ctx())).rejects.toThrow(/HTTP 404.*the Wayback Machine was not asked \(web_fetch policy for 127\.0\.0\.1: deny\)/);
    await expect(archiving({ policy: [] }).execute({ url }, ctx())).rejects.toThrow(/not asked \(web_fetch policy for 127\.0\.0\.1: ask\)/);
    expect(lookups).toEqual([]);
    const denySnapshot: PolicyRule[] = [{ tool: 'web_fetch', match: { domain: 'localhost' }, action: 'deny' }, ...DEFAULT_POLICY];
    await expect(archiving({ policy: denySnapshot }).execute({ url: `${base}/status/404/other-host` }, ctx())).rejects.toThrow(
      /the archived copy at http:\/\/localhost:\d+\/web\/.* was not fetched \(web_fetch policy for localhost: deny\)/,
    );
    expect(hits).toEqual(['/status/404/page', '/status/404/page', '/status/404/other-host', '/wayback/available']);
  });

  it('reads the policy from the project settings by default', async () => {
    const h = await createHarness();
    try {
      const rt = newRuntime(h);
      const rules: PolicyRule[] = [{ tool: 'web_fetch', match: { domain: '127.0.0.1' }, action: 'deny' }, ...DEFAULT_POLICY];
      const projectId = rt.createProject({ name: 'P', goal: 'g', settings: { desk_model: FAKE_MODEL.id, policy: rules } });
      const tool = createWebFetchTool({ waybackApi: `${base}/wayback/available`, archivable: () => true });
      const inProject = testToolContext('/tmp', { projectId, services: testServices({ store: h.store }) });
      await expect(tool.execute({ url: `${base}/status/404/page` }, inProject)).rejects.toThrow(/not asked \(web_fetch policy for 127\.0\.0\.1: deny\)/);
      expect(lookups).toEqual([]);
    } finally {
      await h.cleanup();
    }
  });

  it('does not fall back once the call is cancelled', async () => {
    const ac = new AbortController();
    ac.abort();
    await expect(archiving().execute({ url: `${base}/status/404/page` }, { ...ctx(), signal: ac.signal })).rejects.toThrow();
    expect(hits).not.toContain('/wayback/available');
  });

  it('stops when the call is cancelled during the archive lookup', async () => {
    const ac = new AbortController();
    const pending = archiving({ waybackApi: `${base}/wayback-hang/available` }).execute({ url: `${base}/status/404/page` }, { ...ctx(), signal: ac.signal });
    await vi.waitFor(() => expect(hits).toContain('/wayback-hang/available'));
    ac.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('reads web.archive.org snapshots over https through the id_ form', () => {
    expect(snapshotUrls('http://web.archive.org/web/20240501123000/https://example.com/a?b=1')).toEqual({
      view: 'https://web.archive.org/web/20240501123000/https://example.com/a?b=1',
      read: 'https://web.archive.org/web/20240501123000id_/https://example.com/a?b=1',
    });
    expect(snapshotUrls('http://mirror.test/web/20240501123000/http://x.dev/web/1/y').read).toBe('http://mirror.test/web/20240501123000id_/http://x.dev/web/1/y');
  });

  it('builds the archive lookup target', () => {
    expect(archiveTarget(new URL('https://docs.x/page?v=2#section'))).toBe('https://docs.x/page?v=2');
    expect(archiveTarget(new URL('https://docs.x/search?keyword=monkey&q=a%20long%20search%20about%20many%20different%20things'))).toBe(
      'https://docs.x/search?keyword=monkey&q=a%20long%20search%20about%20many%20different%20things',
    );
    expect(archiveTarget(new URL('https://blog.x/?p=my-very-long-article-title-2024-edition-final'))).toBeDefined();
    for (const url of [
      'https://user:pw@docs.x/p',
      'https://b.s3.amazonaws.com/f.pdf?X-Amz-Signature=abc',
      'https://x.dev/cb?code=abc&state=1',
      'https://x.dev/f?Key-Pair-Id=K&Policy=P',
      'https://x.dev/api?apiKey=abc',
      `https://x.dev/f?h=${'0123456789abcdef'.repeat(2)}`,
    ])
      expect(archiveTarget(new URL(url)), url).toBeUndefined();
  });

  it('tells public hosts from local ones by name', () => {
    for (const host of ['example.com', 'docs.python.org', '8.8.8.8', '[2606:4700::1111]', '[::ffff:8.8.8.8]', 'example.com.']) expect(isPublicHost(new URL(`https://${host}/x`)), host).toBe(true);
    for (const host of [
      'localhost', 'localhost.', 'app.localhost', 'intranet', 'intranet.', 'printer.local', 'db.internal', 'foo.localdomain', 'nas.home', 'wiki.corp', 'x.test',
      'router.lan', 'nas.home.arpa', '127.0.0.1', '10.1.2.3', '172.20.0.1', '192.168.1.10', '169.254.1.1', '198.18.0.1', '224.0.0.1', '[::1]', '[fd00::1]',
      '[fe80::1]', '[::ffff:127.0.0.1]', '[64:ff9b::a00:1]', '[::a00:1]',
    ])
      expect(isPublicHost(new URL(`http://${host}/x`)), host).toBe(false);
  });

  it('checks what a host name resolves to before calling it archivable', async () => {
    const dns =
      (table: Record<string, string[]>, failing: string[] = []): HostLookup =>
      async (host) => {
        if (failing.includes(host)) throw new Error('EAI_AGAIN');
        return table[host] ?? [];
      };
    const archivable = (url: string, lookup: HostLookup) => isArchivable(new URL(url), lookup);
    expect(await archivable('https://docs.dev/x', dns({ 'docs.dev': ['93.184.216.34', '2606:2800:220:1::1'] }))).toBe(true);
    // An expired domain: neither the name nor its parents resolve.
    expect(await archivable('https://docs.dead-project.io/x', dns({}))).toBe(true);
    // An intranet name seen off VPN: it does not resolve, but its parent domain does.
    expect(await archivable('https://jira.mycompany.com/browse/X-1', dns({ 'mycompany.com': ['93.184.216.34'] }))).toBe(false);
    // Public DNS that points to private addresses.
    expect(await archivable('https://wiki.acme.com/x', dns({ 'wiki.acme.com': ['10.0.0.5'] }))).toBe(false);
    expect(await archivable('https://mixed.acme.com/x', dns({ 'mixed.acme.com': ['93.184.216.34', '192.168.1.2'] }))).toBe(false);
    // DNS that cannot answer says no.
    expect(await archivable('https://docs.dev/x', dns({}, ['docs.dev']))).toBe(false);
    const never: HostLookup = async () => {
      throw new Error('looked up');
    };
    expect(await archivable('https://8.8.8.8/x', never)).toBe(true);
    expect(await archivable('http://localhost./x', never)).toBe(false);
  });
});

describe('web_search providers', () => {
  it('parses DuckDuckGo HTML results and decodes redirect links', async () => {
    const results = await duckDuckGoProvider(`${base}/ddg`)('desk', signal());
    expect(results).toEqual([
      { title: 'Example A', url: 'https://example.com/a?x=1', snippet: 'Snippet about A' },
      { title: 'Example B', url: 'https://example.org/b', snippet: 'Snippet about B' },
    ]);
  });

  it('treats a DuckDuckGo 202 as throttled', async () => {
    await expect(duckDuckGoProvider(`${base}/status/202`)('desk', signal())).rejects.toThrow(/HTTP 202/);
  });

  it('parses Brave API results', async () => {
    const results = await braveProvider('key', `${base}/brave`)('desk', signal());
    expect(results).toEqual([{ title: 'Brave A', url: 'https://a.dev', snippet: 'About A' }]);
  });

  it('parses Bing RSS results and drops off-topic ones', async () => {
    expect(await bingProvider(`${base}/bing`)('desk', signal())).toEqual([{ title: 'Desk & threads', url: 'https://desk.dev/docs?a=1&b=2', snippet: 'How Desk works' }]);
    expect(await bingProvider(`${base}/bing`)('junk results', signal())).toEqual([]);
    expect(await bingProvider(`${base}/bing`)('other', signal())).toEqual([]);
  });

  it('keeps Bing results that mention enough of the query', async () => {
    expect((await bingProvider(`${base}/bing`)('typescript zod', signal())).map((r) => r.url)).toEqual(['https://zod.dev/']);
    // Two of three words, and "timers" matches "Timer".
    expect((await bingProvider(`${base}/bing`)('vitest fake timers', signal())).map((r) => r.url)).toEqual(['https://vitest.dev/guide/mocking/timers']);
  });

  it('parses Marginalia API results', async () => {
    expect(await marginaliaProvider(`${base}/marginalia/`)('small web', signal())).toEqual([{ title: 'Small web', url: 'https://small.dev/a', snippet: 'A page about small web' }]);
    expect(await marginaliaProvider(`${base}/marginalia/`)('nothing', signal())).toEqual([]);
  });
});

/** A scripted provider: each call runs the next behaviour (the last one repeats). */
function scripted(name: string, ...behaviours: Array<(signal: AbortSignal) => Promise<SearchResult[]>>): SearchProvider & { calls: number } {
  const fn = async (_query: string, signal: AbortSignal) => behaviours[Math.min(fn.calls++, behaviours.length - 1)]!(signal);
  fn.calls = 0;
  return Object.defineProperty(fn, 'name', { value: name }) as unknown as SearchProvider & { calls: number };
}
const found = (title: string) => async () => [{ title, url: `https://${title}.dev`, snippet: '' }];
const fails = (message: string) => async (): Promise<SearchResult[]> => {
  throw new Error(message);
};
const nothing = async () => [];

describe('web_search', () => {
  const chain = (ddg: string, bing: string, marginalia: string) =>
    fallbackSearchProvider([duckDuckGoProvider(`${base}${ddg}`), bingProvider(`${base}${bing}`), marginaliaProvider(`${base}${marginalia}`)]);

  it('formats results as a numbered list naming the provider', async () => {
    const tool = createWebSearchTool(duckDuckGoProvider(`${base}/ddg`));
    const out = String(await tool.execute({ query: 'desk' }, ctx()));
    expect(out).toBe('Results from duckduckgo:\n\n1. Example A\n   https://example.com/a?x=1\n   Snippet about A\n\n2. Example B\n   https://example.org/b\n   Snippet about B');
    expect(String(await tool.execute({ query: 'none' }, ctx()))).toBe('No results');
  });

  it('uses the first provider that answers', async () => {
    const out = String(await createWebSearchTool(chain('/ddg', '/bing', '/marginalia/')).execute({ query: 'desk' }, ctx()));
    expect(out).toMatch(/^Results from duckduckgo:\n\n1. Example A/);
    expect(hits).toEqual(['/ddg']);
  });

  it('moves on to Bing when DuckDuckGo is throttled', async () => {
    const out = String(await createWebSearchTool(chain('/status/202', '/bing', '/marginalia/')).execute({ query: 'desk' }, ctx()));
    expect(out).toBe('Results from bing:\n\n1. Desk & threads\n   https://desk.dev/docs?a=1&b=2\n   How Desk works');
    expect(hits).toEqual(['/status/202', '/bing']);
  });

  it('moves on to Marginalia when the others error or return nothing', async () => {
    const out = String(await createWebSearchTool(chain('/status/429', '/bing', '/marginalia/')).execute({ query: 'other' }, ctx()));
    expect(out).toBe('Results from marginalia:\n\n1. Small web\n   https://small.dev/a\n   A page about other');
    expect(hits).toEqual(['/status/429', '/bing', '/marginalia/other']);
    const results = await chain('/ddg', '/status/500', '/marginalia/')('other', signal());
    expect(results.map((r) => r.provider)).toEqual(['marginalia']);
  });

  it('reports every provider when none answers', async () => {
    const tool = createWebSearchTool(chain('/status/202', '/status/500', '/status/429'));
    await expect(tool.execute({ query: 'desk' }, ctx())).rejects.toThrow(/duckduckgo: HTTP 202.*; bing: HTTP 500.*; marginalia: HTTP 429/);
    await expect(tool.execute({ query: 'nothing' }, ctx())).rejects.toThrow(/HTTP 202/);
    await expect(chain('/ddg', '/status/500', '/marginalia/')('nothing', signal())).rejects.toThrow(/duckduckgo: no results; bing: HTTP 500.*; marginalia: no results/);
  });

  it('returns No results when every provider comes back empty', async () => {
    expect(String(await createWebSearchTool(chain('/ddg', '/bing', '/marginalia/')).execute({ query: 'nothing' }, ctx()))).toBe('No results');
  });

  it('stops when the call is cancelled', async () => {
    const ac = new AbortController();
    ac.abort();
    await expect(chain('/ddg', '/bing', '/marginalia/')('desk', ac.signal)).rejects.toThrow();
    expect(hits).toEqual([]);
  });

  it('moves on when a provider does not answer in time', async () => {
    const hung = fallbackSearchProvider([duckDuckGoProvider(`${base}/hang`), bingProvider(`${base}/bing`)], { timeoutMs: 100 });
    expect((await hung('desk', signal())).map((r) => r.provider)).toEqual(['bing']);
    // Providers that ignore the signal are cut off too.
    const deaf = scripted('deaf', () => new Promise<SearchResult[]>(() => {}));
    const results = await fallbackSearchProvider([deaf, scripted('next', found('next'))], { timeoutMs: 50 })('q', signal());
    expect(results.map((r) => r.provider)).toEqual(['next']);
    await expect(fallbackSearchProvider([deaf], { timeoutMs: 50 })('q', signal())).rejects.toThrow('No search provider answered (deaf: no answer within 0.05s)');
  });

  it('tries a provider that just failed after the others for a while', async () => {
    let clock = 0;
    const ddg = scripted('ddg', fails('HTTP 202 (throttled)'), found('ddg'));
    const bing = scripted('bing', found('bing'), nothing);
    const search = fallbackSearchProvider([ddg, bing], { cooldownMs: 60_000, now: () => clock });
    expect((await search('q', signal()))[0]?.provider).toBe('bing');
    clock = 30_000;
    // ddg is cooling down, so bing goes first; bing finds nothing, and ddg is still tried last.
    expect((await search('q', signal()))[0]?.provider).toBe('ddg');
    expect([ddg.calls, bing.calls]).toEqual([2, 2]);
    // ddg answered, so it is first again.
    expect((await search('q', signal()))[0]?.provider).toBe('ddg');
    expect([ddg.calls, bing.calls]).toEqual([3, 2]);
  });

  it('returns to the normal order once the cooldown is over', async () => {
    let clock = 0;
    const ddg = scripted('ddg', fails('HTTP 429'), found('ddg'));
    const bing = scripted('bing', found('bing'));
    const search = fallbackSearchProvider([ddg, bing], { cooldownMs: 60_000, now: () => clock });
    await search('q', signal());
    clock = 59_000;
    expect((await search('q', signal()))[0]?.provider).toBe('bing');
    expect(ddg.calls).toBe(1);
    clock = 61_000;
    expect((await search('q', signal()))[0]?.provider).toBe('ddg');
  });

  it('puts Brave first when BRAVE_API_KEY is set', () => {
    expect(defaultSearchProvider({ BRAVE_API_KEY: 'k' }).name).toBe('brave > duckduckgo > bing > marginalia');
    expect(defaultSearchProvider({}).name).toBe('duckduckgo > bing > marginalia');
  });

  it('falls through to the keyless engines when Brave fails, without leaking the key', async () => {
    const endpoints = { brave: `${base}/brave`, duckduckgo: `${base}/ddg`, bing: `${base}/bing`, marginalia: `${base}/marginalia/` };
    expect((await defaultSearchProvider({ BRAVE_API_KEY: 'key' }, endpoints)('desk', signal()))[0]?.provider).toBe('brave');
    hits.length = 0;
    expect((await defaultSearchProvider({ BRAVE_API_KEY: 'wrong-secret' }, endpoints)('desk', signal()))[0]?.provider).toBe('duckduckgo');
    expect(hits).toEqual(['/brave', '/ddg']);
    const failing = { brave: `${base}/brave`, duckduckgo: `${base}/status/202`, bing: `${base}/status/500`, marginalia: `${base}/status/429` };
    const err = await defaultSearchProvider({ BRAVE_API_KEY: 'wrong-secret' }, failing)('desk', signal()).catch((e: Error) => e);
    expect(String(err)).toMatch(/brave: HTTP 401.*; duckduckgo: HTTP 202/);
    expect(String(err)).not.toContain('wrong-secret');
  });
});
