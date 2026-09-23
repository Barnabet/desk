import { Readability } from '@mozilla/readability';
import { parseHTML } from 'linkedom';
import TurndownService from 'turndown';
import { z } from 'zod';
import { defineTool, type Tool } from './types';

const MAX_FETCH_CHARS = 100_000;
const FETCH_TIMEOUT_MS = 30_000;
const USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Desk/1.0';

function htmlToMarkdown(html: string, url: string): { title: string; markdown: string } {
  const turndown = new TurndownService({ headingStyle: 'atx', codeBlockStyle: 'fenced' });
  turndown.remove(['script', 'style', 'noscript', 'iframe']);
  const { document } = parseHTML(html);
  let article: { title?: string | null; content?: string | null } | null = null;
  try {
    article = new Readability(document as unknown as ConstructorParameters<typeof Readability>[0], { charThreshold: 200 }).parse();
  } catch {
    article = null;
  }
  if (article?.content) return { title: article.title ?? '', markdown: turndown.turndown(article.content) };
  const { document: fresh } = parseHTML(html);
  for (const el of fresh.querySelectorAll('script, style, noscript')) el.remove();
  return { title: fresh.title ?? '', markdown: turndown.turndown(fresh.body?.innerHTML ?? html) || url };
}

function cap(text: string): string {
  return text.length > MAX_FETCH_CHARS ? `${text.slice(0, MAX_FETCH_CHARS)}\n\n[truncated at ${MAX_FETCH_CHARS} characters]` : text;
}

async function httpGet(url: string, signal: AbortSignal, headers: Record<string, string> = {}): Promise<Response> {
  const res = await fetch(url, {
    headers: { 'user-agent': USER_AGENT, ...headers },
    redirect: 'follow',
    signal: AbortSignal.any([signal, AbortSignal.timeout(FETCH_TIMEOUT_MS)]),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText} for ${url}`);
  return res;
}

export const webFetchTool = defineTool({
  name: 'web_fetch',
  description: 'Fetch a web page and return its main content as Markdown (or the raw body for non-HTML). Max 100k characters.',
  input: z.object({ url: z.string().url() }),
  gate: { subject: (i) => ({ domain: new URL(i.url).hostname }), unmatched: 'ask' },
  async execute({ url }, ctx) {
    const parsed = new URL(url);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') throw new Error('Only http(s) URLs can be fetched');
    const res = await httpGet(url, ctx.signal);
    const type = res.headers.get('content-type') ?? '';
    const body = await res.text();
    if (!/html/i.test(type)) return cap(body);
    const { title, markdown } = htmlToMarkdown(body, url);
    return cap(`${title ? `# ${title}\n\n` : ''}${markdown}\n\nSource: ${url}`);
  },
});

export type SearchResult = { title: string; url: string; snippet: string };
export type SearchProvider = ((query: string, signal: AbortSignal) => Promise<SearchResult[]>) & { readonly name: string };

function named(name: string, fn: (query: string, signal: AbortSignal) => Promise<SearchResult[]>): SearchProvider {
  return Object.defineProperty(fn, 'name', { value: name }) as SearchProvider;
}

const clean = (s: string | null | undefined) => (s ?? '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();

function decodeDdgHref(href: string): string {
  const absolute = href.startsWith('//') ? `https:${href}` : href;
  try {
    const u = new URL(absolute);
    return u.searchParams.get('uddg') ?? absolute;
  } catch {
    return absolute;
  }
}

export function duckDuckGoProvider(baseUrl = 'https://html.duckduckgo.com/html/'): SearchProvider {
  return named('duckduckgo', async (query, signal) => {
    const res = await httpGet(`${baseUrl}?q=${encodeURIComponent(query)}`, signal);
    const { document } = parseHTML(await res.text());
    return [...document.querySelectorAll('.result')]
      .map((el) => {
        const a = el.querySelector('.result__a');
        return { title: clean(a?.textContent), url: decodeDdgHref(a?.getAttribute('href') ?? ''), snippet: clean(el.querySelector('.result__snippet')?.textContent) };
      })
      .filter((r) => r.title && r.url)
      .slice(0, 10);
  });
}

export function braveProvider(apiKey: string, baseUrl = 'https://api.search.brave.com/res/v1/web/search'): SearchProvider {
  return named('brave', async (query, signal) => {
    const res = await httpGet(`${baseUrl}?q=${encodeURIComponent(query)}&count=10`, signal, {
      accept: 'application/json',
      'x-subscription-token': apiKey,
    });
    const data = (await res.json()) as { web?: { results?: Array<{ title?: string; url?: string; description?: string }> } };
    return (data.web?.results ?? []).map((r) => ({ title: clean(r.title), url: r.url ?? '', snippet: clean(r.description) })).filter((r) => r.url);
  });
}

export function defaultSearchProvider(env: NodeJS.ProcessEnv = process.env): SearchProvider {
  return env.BRAVE_API_KEY ? braveProvider(env.BRAVE_API_KEY) : duckDuckGoProvider();
}

export function createWebSearchTool(provider: SearchProvider): Tool {
  return defineTool({
    name: 'web_search',
    description: 'Search the web. Returns up to 10 results with title, URL and snippet. Use web_fetch to read a result.',
    input: z.object({ query: z.string().min(1) }),
    gate: { subject: () => ({}), unmatched: 'ask' },
    async execute({ query }, ctx) {
      const results = await provider(query, ctx.signal);
      if (!results.length) return 'No results';
      return results.map((r, i) => `${i + 1}. ${r.title}\n   ${r.url}\n   ${r.snippet}`).join('\n\n');
    },
  });
}

export const webSearchTool = createWebSearchTool(defaultSearchProvider());
export const webTools: Tool[] = [webFetchTool, webSearchTool];
