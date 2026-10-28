import { errorDetail, WebApiError } from './errors';

// Firecrawl v2 (docs.firecrawl.dev): pages as Markdown with JavaScript rendering, site maps and crawl jobs.

export const FIRECRAWL_BASE = 'https://api.firecrawl.dev/v2';
/** Firecrawl's own page timeout, and how long deskd waits for a scrape answer overall. */
const SCRAPE_TIMEOUT_MS = 45_000;
const SCRAPE_DEADLINE_MS = 60_000;
const REQUEST_TIMEOUT_MS = 30_000;
/** The longest `Retry-After` worth waiting for before one retry of a 429. */
const MAX_RETRY_AFTER_S = 10;
const CRAWL_DEADLINE_MS = 10 * 60_000;
const CRAWL_POLL_MS = 2_000;

export type ScrapeRequest = {
  url: string;
  mainOnly: boolean;
  waitMs?: number;
  /** Skip Firecrawl's cache (`maxAge: 0`). */
  fresh?: boolean;
  screenshot?: boolean;
  links?: boolean;
  /** Firecrawl charges per PDF page, so its PDF parser stops here. */
  pdfMaxPages?: number;
  /** Structured extraction (the `json` format). */
  json?: { prompt: string; schema?: unknown };
};

export type ScrapeResult = {
  markdown: string;
  links?: string[];
  /** Valid for 24 hours. */
  screenshotUrl?: string;
  json?: unknown;
  title?: string;
  /** The final URL after redirects. */
  url: string;
  statusCode?: number;
  contentType?: string;
  numPages?: number;
  totalPages?: number;
};

export type CrawlRequest = { url: string; limit: number; maxDepth?: number; includePaths?: string[]; excludePaths?: string[]; mainOnly: boolean };
export type CrawlPage = { url: string; title?: string; markdown: string };
export type CrawlOutcome = {
  pages: CrawlPage[];
  status: 'completed' | 'failed' | 'cancelled' | 'timeout';
  creditsUsed?: number;
  robotsBlocked: string[];
};

type Json = Record<string, unknown>;

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => (clearTimeout(t), resolve()), { once: true });
  });

/**
 * One Firecrawl call. A 429 is retried once when `Retry-After` is at most 10 s. Non-2xx answers, `success: false`,
 * timeouts and network failures become `WebApiError`; an aborted call rethrows the abort. The key never reaches a message.
 */
async function firecrawlRequest(key: string, method: string, url: string, body: unknown, signal: AbortSignal, timeoutMs = REQUEST_TIMEOUT_MS): Promise<Json> {
  for (let attempt = 0; ; attempt++) {
    let res: Response;
    try {
      res = await fetch(url, {
        method,
        headers: { authorization: `Bearer ${key}`, ...(body === undefined ? {} : { 'content-type': 'application/json' }) },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.any([signal, AbortSignal.timeout(timeoutMs)]),
      });
    } catch (err) {
      if (signal.aborted) throw err;
      const timedOut = err instanceof Error && err.name === 'TimeoutError';
      throw new WebApiError('firecrawl', null, timedOut ? `no answer within ${timeoutMs / 1000}s` : `request failed: ${err instanceof Error ? err.message : String(err)}`);
    }
    if (res.status === 429 && attempt === 0) {
      const wait = Number(res.headers.get('retry-after'));
      if (Number.isFinite(wait) && wait >= 0 && wait <= MAX_RETRY_AFTER_S) {
        await res.body?.cancel();
        await sleep(wait * 1000, signal);
        signal.throwIfAborted();
        continue;
      }
    }
    if (!res.ok) throw new WebApiError('firecrawl', res.status, (await errorDetail(res)).split(key).join('***'));
    const json = (await res.json()) as Json;
    if (json.success === false) throw new WebApiError('firecrawl', res.status, String(json.error ?? 'Firecrawl reported a failure').split(key).join('***'));
    return json;
  }
}

const num = (v: unknown) => (typeof v === 'number' ? v : undefined);
const text = (v: unknown) => (typeof v === 'string' && v ? v : undefined);

/** One page through Firecrawl. */
export async function firecrawlScrape(key: string, req: ScrapeRequest, signal: AbortSignal, base: string = FIRECRAWL_BASE): Promise<ScrapeResult> {
  const formats: unknown[] = ['markdown'];
  if (req.links) formats.push('links');
  if (req.screenshot) formats.push('screenshot');
  if (req.json) formats.push({ type: 'json', prompt: req.json.prompt, ...(req.json.schema === undefined ? {} : { schema: req.json.schema }) });
  const body = {
    url: req.url,
    formats,
    onlyMainContent: req.mainOnly,
    ...(req.waitMs ? { waitFor: req.waitMs } : {}),
    ...(req.fresh ? { maxAge: 0 } : {}),
    ...(req.pdfMaxPages ? { parsers: [{ type: 'pdf', maxPages: req.pdfMaxPages }] } : {}),
    timeout: SCRAPE_TIMEOUT_MS,
  };
  const json = await firecrawlRequest(key, 'POST', `${base}/scrape`, body, signal, SCRAPE_DEADLINE_MS);
  const data = (json.data ?? {}) as Json;
  const meta = (data.metadata ?? {}) as Json;
  return {
    markdown: typeof data.markdown === 'string' ? data.markdown : '',
    ...(Array.isArray(data.links) ? { links: data.links.filter((l): l is string => typeof l === 'string') } : {}),
    ...(text(data.screenshot) ? { screenshotUrl: text(data.screenshot) } : {}),
    ...(data.json !== undefined ? { json: data.json } : {}),
    ...(text(meta.title) ? { title: text(meta.title) } : {}),
    url: text(meta.url) ?? text(meta.sourceURL) ?? req.url,
    ...(num(meta.statusCode) !== undefined ? { statusCode: num(meta.statusCode) } : {}),
    ...(text(meta.contentType) ? { contentType: text(meta.contentType) } : {}),
    ...(num(meta.numPages) !== undefined ? { numPages: num(meta.numPages) } : {}),
    ...(num(meta.totalPages) !== undefined ? { totalPages: num(meta.totalPages) } : {}),
  };
}

/** A site's URLs, from its sitemap and Firecrawl's index (1 credit). */
export async function firecrawlMap(
  key: string,
  req: { url: string; search?: string; limit: number; includeSubdomains: boolean },
  signal: AbortSignal,
  base: string = FIRECRAWL_BASE,
): Promise<Array<{ url: string; title?: string }>> {
  const json = await firecrawlRequest(key, 'POST', `${base}/map`, { url: req.url, ...(req.search ? { search: req.search } : {}), limit: req.limit, includeSubdomains: req.includeSubdomains }, signal);
  const links = Array.isArray(json.links) ? json.links : [];
  return links.flatMap((l): Array<{ url: string; title?: string }> => {
    if (typeof l === 'string') return [{ url: l }];
    if (!l || typeof l !== 'object') return [];
    const { url, title } = l as Json;
    return typeof url === 'string' ? [{ url, ...(text(title) ? { title: text(title) } : {}) }] : [];
  });
}

function crawlPages(data: unknown): CrawlPage[] {
  if (!Array.isArray(data)) return [];
  return data.flatMap((d): CrawlPage[] => {
    if (!d || typeof d !== 'object') return [];
    const meta = ((d as Json).metadata ?? {}) as Json;
    const url = text(meta.sourceURL) ?? text(meta.url);
    if (!url) return [];
    return [{ url, ...(text(meta.title) ? { title: text(meta.title) } : {}), markdown: typeof (d as Json).markdown === 'string' ? ((d as Json).markdown as string) : '' }];
  });
}

/**
 * A crawl of one site: starts the job on the start URL's host only, polls until it ends, and collects every page. On
 * abort or at the deadline it cancels the job and returns what Firecrawl had so far. `next` links are followed only on
 * Firecrawl's own origin, so the key never goes anywhere else.
 */
export async function firecrawlCrawl(
  key: string,
  req: CrawlRequest,
  signal: AbortSignal,
  o: { base?: string; pollMs?: number; deadlineMs?: number } = {},
): Promise<CrawlOutcome> {
  const base = o.base ?? FIRECRAWL_BASE;
  const origin = new URL(base).origin;
  const start = await firecrawlRequest(
    key,
    'POST',
    `${base}/crawl`,
    {
      url: req.url,
      limit: req.limit,
      ...(req.maxDepth !== undefined ? { maxDiscoveryDepth: req.maxDepth } : {}),
      ...(req.includePaths?.length ? { includePaths: req.includePaths } : {}),
      ...(req.excludePaths?.length ? { excludePaths: req.excludePaths } : {}),
      allowSubdomains: false,
      allowExternalLinks: false,
      crawlEntireDomain: false,
      sitemap: 'include',
      scrapeOptions: { formats: ['markdown'], onlyMainContent: req.mainOnly },
    },
    signal,
  );
  const id = text(start.id);
  if (!id) throw new WebApiError('firecrawl', null, 'Firecrawl did not return a crawl id');
  const statusUrl = `${base}/crawl/${encodeURIComponent(id)}`;

  /** Every page of a status answer, following `next` on Firecrawl's origin. */
  const collect = async (first: Json, s: AbortSignal) => {
    const pages = crawlPages(first.data);
    let next = text(first.next);
    for (let hops = 0; next && hops < 100; hops++) {
      if (new URL(next).origin !== origin) break;
      const chunk = await firecrawlRequest(key, 'GET', next, undefined, s);
      pages.push(...crawlPages(chunk.data));
      next = text(chunk.next);
    }
    const seen = new Set<string>();
    return pages.filter((p) => !seen.has(p.url) && seen.add(p.url));
  };
  const robotsBlocked = async (s: AbortSignal) => {
    try {
      const errors = await firecrawlRequest(key, 'GET', `${statusUrl}/errors`, undefined, s);
      return Array.isArray(errors.robotsBlocked) ? errors.robotsBlocked.filter((u): u is string => typeof u === 'string') : [];
    } catch {
      return [];
    }
  };
  /** Cancels the job (with its own short deadline: the call's signal may be aborted) and keeps what it had. */
  const stop = async (status: 'cancelled' | 'timeout'): Promise<CrawlOutcome> => {
    const cleanup = AbortSignal.timeout(15_000);
    await firecrawlRequest(key, 'DELETE', statusUrl, undefined, cleanup).catch(() => undefined);
    let pages: CrawlPage[] = [];
    let creditsUsed: number | undefined;
    try {
      const last = await firecrawlRequest(key, 'GET', statusUrl, undefined, cleanup);
      creditsUsed = num(last.creditsUsed);
      pages = await collect(last, cleanup);
    } catch {
      // Nothing more to keep.
    }
    return { pages, status, ...(creditsUsed !== undefined ? { creditsUsed } : {}), robotsBlocked: await robotsBlocked(cleanup) };
  };

  const deadline = Date.now() + (o.deadlineMs ?? CRAWL_DEADLINE_MS);
  for (;;) {
    if (signal.aborted) return stop('cancelled');
    if (Date.now() >= deadline) return stop('timeout');
    let status: Json;
    try {
      status = await firecrawlRequest(key, 'GET', statusUrl, undefined, signal);
    } catch (err) {
      if (signal.aborted) return stop('cancelled');
      await firecrawlRequest(key, 'DELETE', statusUrl, undefined, AbortSignal.timeout(15_000)).catch(() => undefined);
      throw err;
    }
    const state = text(status.status);
    if (state === 'completed' || state === 'failed' || state === 'cancelled') {
      const pages = await collect(status, signal);
      const creditsUsed = num(status.creditsUsed);
      return { pages, status: state, ...(creditsUsed !== undefined ? { creditsUsed } : {}), robotsBlocked: await robotsBlocked(signal) };
    }
    await sleep(Math.min(o.pollMs ?? CRAWL_POLL_MS, Math.max(0, deadline - Date.now())), signal);
  }
}
