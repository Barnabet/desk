import { lookup as dnsLookup } from 'node:dns/promises';
import { BlockList, isIP } from 'node:net';
import { Readability } from '@mozilla/readability';
import { DOMParser, parseHTML } from 'linkedom';
import TurndownService from 'turndown';
import { z } from 'zod';
import type { PolicyRule } from '@desk/protocol';
import { evaluatePolicy } from '../policy/evaluate';
import { getProject } from '../state/queries';
import { defineTool, type Tool, type ToolContext } from './types';

const MAX_FETCH_CHARS = 100_000;
const FETCH_TIMEOUT_MS = 30_000;
/** Per search provider, so a stuck engine leaves time for the next one. */
const SEARCH_TIMEOUT_MS = 10_000;
const ARCHIVE_LOOKUP_TIMEOUT_MS = 15_000;
const USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Desk/1.0';
/** Public APIs (Marginalia, Wayback) ask clients to identify themselves. */
const API_USER_AGENT = 'Desk/1.0 (+https://github.com/Barnabet/desk)';

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

/** A non-2xx answer, with its status for fallback decisions. */
class HttpError extends Error {
  /** `HTTP 404 Not Found`, without the URL. */
  readonly summary: string;
  constructor(
    readonly status: number,
    statusText: string,
    url: string,
  ) {
    const summary = `HTTP ${status}${statusText ? ` ${statusText}` : ''}`;
    super(`${summary} for ${url}`);
    this.summary = summary;
  }
}

async function httpGet(url: string, signal: AbortSignal, headers: Record<string, string> = {}, timeoutMs = FETCH_TIMEOUT_MS): Promise<Response> {
  const res = await fetch(url, {
    headers: { 'user-agent': USER_AGENT, ...headers },
    redirect: 'follow',
    signal: AbortSignal.any([signal, AbortSignal.timeout(timeoutMs)]),
  });
  if (!res.ok) throw new HttpError(res.status, res.statusText, url);
  return res;
}

/** The page as Markdown (HTML) or its raw body, ending with a source line. */
async function render(res: Response, url: string, source = url): Promise<string> {
  const type = res.headers.get('content-type') ?? '';
  const body = await res.text();
  if (!/html/i.test(type)) return body;
  const { title, markdown } = htmlToMarkdown(body, url);
  return `${title ? `# ${title}\n\n` : ''}${markdown}\n\nSource: ${source}`;
}

/** Answers that mean the page is gone or blocked, where an archived copy is the best remaining source. */
const ARCHIVE_STATUSES = new Set([403, 404, 410, 451]);
/** Connection errors that mean the host cannot be reached at all. */
const UNREACHABLE_CODES = new Set(['ENOTFOUND', 'EAI_AGAIN', 'ECONNREFUSED', 'EHOSTUNREACH', 'ENETUNREACH', 'EHOSTDOWN', 'ETIMEDOUT', 'UND_ERR_CONNECT_TIMEOUT']);

/** The system error code behind a failed fetch (`TypeError: fetch failed` wraps it in `cause`, sometimes in an AggregateError). */
function networkCode(err: unknown): string | undefined {
  for (let e: unknown = err, depth = 0; e && typeof e === 'object' && depth < 5; depth++) {
    const { code, cause, errors } = e as { code?: unknown; cause?: unknown; errors?: unknown };
    if (typeof code === 'string') return code;
    e = cause ?? (Array.isArray(errors) ? errors[0] : undefined);
  }
  return undefined;
}

/**
 * Why the live page is unavailable, when an archived copy may stand in for it (403/404/410/451 or an unreachable host):
 * `reason` for the archive label, `message` for the error when there is no copy. Undefined for every other failure.
 */
function unavailable(err: unknown, host: string): { reason: string; message: string } | undefined {
  if (err instanceof HttpError) return ARCHIVE_STATUSES.has(err.status) ? { reason: err.summary, message: err.message } : undefined;
  const code = networkCode(err);
  return code && UNREACHABLE_CODES.has(code) ? { reason: `host unreachable: ${code}`, message: `Could not reach ${host} (${code})` } : undefined;
}

/** Loopback, private, shared, link-local, benchmarking, documentation, multicast and reserved ranges; IPv4-mapped IPv6 is checked against the IPv4 rules. */
const PRIVATE_NETS = new BlockList();
for (const [net, prefix] of [
  ['0.0.0.0', 8], ['10.0.0.0', 8], ['100.64.0.0', 10], ['127.0.0.0', 8], ['169.254.0.0', 16], ['172.16.0.0', 12], ['192.0.0.0', 24], ['192.0.2.0', 24],
  ['192.168.0.0', 16], ['198.18.0.0', 15], ['198.51.100.0', 24], ['203.0.113.0', 24], ['224.0.0.0', 4], ['240.0.0.0', 4],
] as const)
  PRIVATE_NETS.addSubnet(net, prefix, 'ipv4');
// ::/96 also covers IPv4-compatible addresses; 64:ff9b::/96 and 64:ff9b:1::/48 are NAT64, which reaches IPv4 hosts through a local gateway.
for (const [net, prefix] of [['::', 96], ['64:ff9b::', 96], ['64:ff9b:1::', 48], ['100::', 64], ['2001:db8::', 32], ['fc00::', 7], ['fe80::', 10], ['ff00::', 8]] as const)
  PRIVATE_NETS.addSubnet(net, prefix, 'ipv6');

/** Special-use and customary private top-level names (RFC 6761, 6762, 8375, ICANN's `.internal`, common intranet suffixes). */
const PRIVATE_SUFFIX = /(^|\.)(localhost|local|localdomain|internal|intranet|private|lan|home|corp|test|example|invalid|onion|home\.arpa)$/;

/** Whether an IP address is on the public internet. */
function isPublicAddress(ip: string): boolean {
  const family = isIP(ip);
  return family !== 0 && !PRIVATE_NETS.check(ip, family === 6 ? 'ipv6' : 'ipv4');
}

/** The URL's host, lowercased, without IPv6 brackets or the trailing dot of a fully qualified name. */
const hostOf = (url: URL) => url.hostname.replace(/^\[|\]$/g, '').replace(/\.+$/, '').toLowerCase();

/**
 * Whether a URL's host looks public by its name alone: a public IP, or a dotted name outside the special-use and intranet
 * suffixes. It does not resolve the name; `isArchivable` does.
 */
export function isPublicHost(url: URL): boolean {
  const host = hostOf(url);
  if (isIP(host)) return isPublicAddress(host);
  return host.includes('.') && !PRIVATE_SUFFIX.test(host);
}

/** Resolves a host name to its addresses: [] when the name does not exist; throws when DNS cannot answer. */
export type HostLookup = (host: string) => Promise<string[]>;

const DNS_TIMEOUT_MS = 3_000;
const NO_SUCH_NAME = new Set(['ENOTFOUND', 'ENODATA']);

const systemLookup: HostLookup = async (host) => {
  try {
    const found = await Promise.race([
      dnsLookup(host, { all: true, verbatim: true }),
      new Promise<never>((_, reject) => setTimeout(reject, DNS_TIMEOUT_MS, new Error(`DNS lookup of ${host} timed out`)).unref()),
    ]);
    return found.map((a) => a.address);
  } catch (err) {
    if (NO_SUCH_NAME.has(networkCode(err) ?? '')) return [];
    throw err;
  }
};

/**
 * Whether the Wayback Machine may be asked about `url`, so nothing local leaves the machine: the host must be public by
 * name and resolve only to public addresses. A name that does not resolve counts only when no parent domain resolves
 * either (an expired domain): an unresolvable subdomain of a live domain is usually an intranet host seen off VPN.
 * Any DNS failure other than "no such name" says no. An intranet name whose public DNS points to public addresses
 * cannot be told apart from a public site.
 */
export async function isArchivable(url: URL, lookup: HostLookup = systemLookup): Promise<boolean> {
  if (!isPublicHost(url)) return false;
  const host = hostOf(url);
  if (isIP(host)) return true;
  try {
    const addresses = await lookup(host);
    if (addresses.length) return addresses.every(isPublicAddress);
    const labels = host.split('.');
    const parents = labels.slice(1, -1).map((_, i) => labels.slice(i + 1).join('.'));
    return (await Promise.all(parents.map(lookup))).every((found) => !found.length);
  } catch {
    return false;
  }
}

/** Words in query parameter names that carry credentials: signed-URL signatures, tokens, keys, sessions, one-time codes. */
const SECRET_WORDS = new Set([
  'key', 'apikey', 'accesskey', 'secretkey', 'privatekey', 'resourcekey', 'token', 'sig', 'signature', 'secret', 'password', 'passwd', 'pwd',
  'auth', 'authorization', 'credential', 'credentials', 'session', 'sessionid', 'sid', 'jwt', 'code', 'otp', 'ticket', 'nonce', 'hmac', 'expires', 'policy',
]);
const secretName = (name: string) =>
  name
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .some((w) => SECRET_WORDS.has(w) || /(token|secret|password|signature)$/.test(w));
/** Long opaque values: hex digests, or base64-like strings mixing cases and digits (a slug or a sentence does not). */
const secretValue = (v: string) =>
  v.length >= 32 && (/^[0-9a-f]+$/i.test(v) || (/^[\w.~+/=-]+$/.test(v) && /[a-z]/.test(v) && /[A-Z]/.test(v) && /\d/.test(v)));

/**
 * The URL to look up in the Wayback Machine: without its #fragment (never part of a capture). Undefined when the URL
 * carries credentials (user info, or query parameters that look like tokens, keys or signatures), which must not reach a
 * third party and would not be archived anyway.
 */
export function archiveTarget(url: URL): string | undefined {
  if (url.username || url.password) return undefined;
  for (const [name, value] of url.searchParams) if (secretName(name) || secretValue(value)) return undefined;
  const target = new URL(url.href);
  target.hash = '';
  return target.href;
}

/** `20240501123000` → `2024-05-01`. */
const snapshotDate = (timestamp: string) => timestamp.replace(/^(\d{4})(\d{2})(\d{2}).*$/, '$1-$2-$3');

/**
 * A Wayback snapshot URL as the archive's availability API gives it (`http://web.archive.org/web/<ts>/<url>`): `view`, over
 * https, for the source line; `read`, its `id_` form, which serves the original bytes without the toolbar and rewritten links.
 */
export function snapshotUrls(closest: string): { view: string; read: string } {
  const snapshot = new URL(closest);
  if (snapshot.hostname === 'web.archive.org') snapshot.protocol = 'https:';
  return { view: snapshot.href, read: snapshot.href.replace(/^(https?:\/\/[^/]+\/web\/\d+)\//, '$1id_/') };
}

const errorText = (err: unknown) => {
  const text = err instanceof Error ? err.message : String(err);
  const code = networkCode(err);
  return code && !text.includes(code) ? `${text} (${code})` : text;
};

type ArchiveRequest = {
  /** What the live fetch was asked for, for the source line. */
  url: string;
  /** What the archive is asked about (`archiveTarget`). */
  target: string;
  gone: { reason: string; message: string };
  api: string;
  /** Why policy keeps web_fetch from reaching a URL without asking, or undefined when it may. */
  blocked: (href: string) => string | undefined;
};

/**
 * The closest Wayback Machine snapshot of `target`, labelled as archived. Throws `gone.message`, plus what went wrong,
 * when there is no usable copy, when the archive cannot be reached or read, or when policy keeps web_fetch from it.
 */
async function fetchArchived({ url, target, gone, api, blocked }: ArchiveRequest, signal: AbortSignal): Promise<string> {
  const lookupUrl = `${api}?url=${encodeURIComponent(target)}`;
  const lookupBlocked = blocked(lookupUrl);
  if (lookupBlocked) throw new Error(`${gone.message}; the Wayback Machine was not asked (${lookupBlocked})`);
  let closest: { available?: boolean; url?: string; timestamp?: string; status?: string } | undefined;
  try {
    const lookup = await httpGet(lookupUrl, signal, { 'user-agent': API_USER_AGENT, accept: 'application/json' }, ARCHIVE_LOOKUP_TIMEOUT_MS);
    closest = ((await lookup.json()) as { archived_snapshots?: { closest?: typeof closest } }).archived_snapshots?.closest;
  } catch (err) {
    if (signal.aborted) throw err;
    throw new Error(`${gone.message}; the Wayback Machine lookup failed too (${errorText(err)})`);
  }
  if (!closest?.available || !closest.url || !closest.timestamp) throw new Error(`${gone.message}; no archived copy in the Wayback Machine`);
  if (closest.status && !/^[23]/.test(closest.status))
    throw new Error(`${gone.message}; the closest Wayback Machine capture is an HTTP ${closest.status} page, not the page itself`);
  let snapshot: { view: string; read: string };
  try {
    snapshot = snapshotUrls(closest.url);
  } catch {
    throw new Error(`${gone.message}; the Wayback Machine gave an invalid snapshot URL`);
  }
  const snapshotBlocked = blocked(snapshot.read);
  if (snapshotBlocked) throw new Error(`${gone.message}; the archived copy at ${snapshot.view} was not fetched (${snapshotBlocked})`);
  let body: string;
  try {
    body = await render(await httpGet(snapshot.read, signal), url, `${snapshot.view} (archived copy of ${url})`);
  } catch (err) {
    if (signal.aborted) throw err;
    throw new Error(`${gone.message}; the archived copy at ${snapshot.view} could not be read (${errorText(err)})`);
  }
  return `Archived copy from ${snapshotDate(closest.timestamp)}: the live page was unavailable (${gone.reason}).\n\n${body}`;
}

export type WebFetchOptions = {
  /** The Wayback availability API; null turns the archive fallback off. */
  waybackApi?: string | null;
  /** Whether the archive may be asked about a URL. Default `isArchivable`: public hosts only, by name and by DNS. */
  archivable?: (url: URL) => boolean | Promise<boolean>;
  /** The project's policy rules (default: its settings). The archive is used only where they let web_fetch go without asking. */
  policy?: (ctx: ToolContext) => PolicyRule[];
};

const projectPolicy = (ctx: ToolContext): PolicyRule[] => getProject(ctx.services.store.db, ctx.projectId)?.settings.policy ?? [];

/** `web_fetch`: one page as Markdown, falling back to the Wayback Machine when the live page is gone, blocked or unreachable. */
export function createWebFetchTool({ waybackApi = 'https://archive.org/wayback/available', archivable = isArchivable, policy = projectPolicy }: WebFetchOptions = {}) {
  const tool: Tool<{ url: string }> = defineTool({
    name: 'web_fetch',
    description:
      'Fetch a web page and return its main content as Markdown (or the raw body for non-HTML). Max 100k characters. ' +
      'When a public page is gone or blocked (403, 404, 410, 451) or its host is unreachable, returns the Wayback Machine copy instead, labelled as archived.',
    input: z.object({ url: z.string().url() }),
    gate: { subject: (i) => ({ domain: new URL(i.url).hostname }), unmatched: 'ask' },
    async execute({ url }, ctx) {
      const parsed = new URL(url);
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') throw new Error('Only http(s) URLs can be fetched');
      let res: Response;
      try {
        res = await httpGet(url, ctx.signal);
      } catch (err) {
        const gone = ctx.signal.aborted ? undefined : unavailable(err, parsed.host);
        if (!gone) throw err;
        const target = waybackApi ? archiveTarget(parsed) : undefined;
        if (!waybackApi || !target || !(await archivable(parsed))) throw new Error(gone.message);
        const rules = policy(ctx);
        const blocked = (href: string) => {
          const { action } = evaluatePolicy(tool, { url: href }, rules, { sandboxAvailable: true });
          return action === 'allow' || action === 'auto' ? undefined : `web_fetch policy for ${new URL(href).hostname}: ${action}`;
        };
        return cap(await fetchArchived({ url, target, gone, api: waybackApi, blocked }, ctx.signal));
      }
      return cap(await render(res, url));
    },
  });
  return tool;
}

export const webFetchTool = createWebFetchTool();

/** `provider` names the engine that found the result (set by `fallbackSearchProvider`). */
export type SearchResult = { title: string; url: string; snippet: string; provider?: string };
export type SearchProvider = ((query: string, signal: AbortSignal) => Promise<SearchResult[]>) & { readonly name: string };

function named(name: string, fn: (query: string, signal: AbortSignal) => Promise<SearchResult[]>): SearchProvider {
  return Object.defineProperty(fn, 'name', { value: name }) as SearchProvider;
}

const clean = (s: string | null | undefined) => (s ?? '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();

/** GET for keyless engines, which answer 202 (DuckDuckGo's "slow down" page) instead of results when they throttle. */
async function searchGet(url: string, signal: AbortSignal, headers: Record<string, string> = {}): Promise<Response> {
  const res = await httpGet(url, signal, headers, SEARCH_TIMEOUT_MS);
  if (res.status === 202) throw new Error(`HTTP 202 (throttled) for ${url}`);
  return res;
}

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
    const res = await searchGet(`${baseUrl}?q=${encodeURIComponent(query)}`, signal);
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

const fold = (s: string) => s.toLowerCase().normalize('NFKD').replace(/\p{M}/gu, '');
const COMMON_WORDS = new Set(['the', 'and', 'for', 'with', 'how', 'what', 'why', 'when', 'who', 'are', 'from', 'that', 'this', 'does', 'www', 'com', 'http', 'https']);

/**
 * Results that mention enough of the query's significant words: all of them for one or two, three in five for more (a
 * plural also matches its singular). Bing's keyless RSS sometimes answers unrelated or loosely related pages.
 */
function onTopic(results: SearchResult[], query: string): SearchResult[] {
  const terms = fold(query.replace(/\S+:\S+/g, ' '))
    .split(/[^\p{L}\p{N}]+/u)
    .filter((t) => t.length >= 3 && !COMMON_WORDS.has(t))
    .map((t) => (t.length > 4 && t.endsWith('s') ? t.slice(0, -1) : t));
  if (!terms.length) return results;
  const needed = terms.length <= 2 ? terms.length : Math.ceil(terms.length * 0.6);
  return results.filter((r) => {
    const text = fold(`${r.title} ${r.snippet} ${r.url}`);
    return terms.filter((t) => text.includes(t)).length >= needed;
  });
}

export function bingProvider(baseUrl = 'https://www.bing.com/search'): SearchProvider {
  return named('bing', async (query, signal) => {
    const res = await searchGet(`${baseUrl}?q=${encodeURIComponent(query)}&format=rss`, signal);
    const doc = new DOMParser().parseFromString(await res.text(), 'text/xml');
    const results = [...doc.querySelectorAll('item')]
      .map((item) => ({ title: clean(item.querySelector('title')?.textContent), url: clean(item.querySelector('link')?.textContent), snippet: clean(item.querySelector('description')?.textContent) }))
      .filter((r) => r.title && /^https?:\/\//.test(r.url));
    return onTopic(results, query).slice(0, 10);
  });
}

export function marginaliaProvider(baseUrl = 'https://api.marginalia.nu/public/search/'): SearchProvider {
  return named('marginalia', async (query, signal) => {
    const res = await searchGet(`${baseUrl}${encodeURIComponent(query)}?count=10`, signal, { 'user-agent': API_USER_AGENT, accept: 'application/json' });
    const data = (await res.json()) as { results?: Array<{ url?: string; title?: string; description?: string }> };
    return (data.results ?? [])
      .map((r) => ({ title: clean(r.title), url: r.url ?? '', snippet: clean(r.description) }))
      .filter((r) => r.title && r.url)
      .slice(0, 10);
  });
}

export function braveProvider(apiKey: string, baseUrl = 'https://api.search.brave.com/res/v1/web/search'): SearchProvider {
  return named('brave', async (query, signal) => {
    const res = await searchGet(`${baseUrl}?q=${encodeURIComponent(query)}&count=10`, signal, {
      accept: 'application/json',
      'x-subscription-token': apiKey,
    });
    const data = (await res.json()) as { web?: { results?: Array<{ title?: string; url?: string; description?: string }> } };
    return (data.web?.results ?? []).map((r) => ({ title: clean(r.title), url: r.url ?? '', snippet: clean(r.description) })).filter((r) => r.url);
  });
}

/** How long a provider that just failed is tried after the others rather than first. */
const SEARCH_COOLDOWN_MS = 5 * 60_000;

export type FallbackSearchOptions = {
  /** Per provider (default 10s), so a stuck engine leaves time for the next one. */
  timeoutMs?: number;
  /** How long a provider that failed (error, 202/429 throttling, timeout) goes last in the order (default 5 minutes). */
  cooldownMs?: number;
  now?: () => number;
};

/** `promise`, or the signal's reason once it aborts, for providers that do not stop on their own. */
function untilAborted<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(signal.reason);
    if (signal.aborted) return onAbort();
    signal.addEventListener('abort', onAbort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', onAbort));
  });
}

/**
 * Tries providers in order and returns the first non-empty answer, each result tagged with the provider that found it.
 * A provider that errors (including 202/429 throttling), times out or finds nothing passes to the next; a cancelled call
 * stops. A provider that failed is tried after the others for a while, so a throttled engine does not cost every call its
 * timeout. Throws with every provider's outcome when none answered and one of them failed; [] when all found nothing.
 */
export function fallbackSearchProvider(
  providers: SearchProvider[],
  { timeoutMs = SEARCH_TIMEOUT_MS, cooldownMs = SEARCH_COOLDOWN_MS, now = Date.now }: FallbackSearchOptions = {},
): SearchProvider {
  const failedAt = new Map<SearchProvider, number>();
  return named(providers.map((p) => p.name).join(' > '), async (query, signal) => {
    const cooling = (p: SearchProvider) => now() - (failedAt.get(p) ?? -Infinity) < cooldownMs;
    const order = [...providers.filter((p) => !cooling(p)), ...providers.filter(cooling)];
    const outcomes: string[] = [];
    let failed = false;
    for (const provider of order) {
      signal.throwIfAborted();
      const limited = AbortSignal.any([signal, AbortSignal.timeout(timeoutMs)]);
      try {
        const results = await untilAborted(provider(query, limited), limited);
        failedAt.delete(provider);
        if (results.length) return results.map((r) => ({ ...r, provider: r.provider ?? provider.name }));
        outcomes.push(`${provider.name}: no results`);
      } catch (err) {
        if (signal.aborted) throw err;
        failed = true;
        failedAt.set(provider, now());
        outcomes.push(`${provider.name}: ${limited.aborted ? `no answer within ${timeoutMs / 1000}s` : errorText(err)}`);
      }
    }
    if (failed) throw new Error(`No search provider answered (${outcomes.join('; ')})`);
    return [];
  });
}

/** Where the default engines are reached (tests point them at fake servers). */
export type SearchEndpoints = Partial<Record<'brave' | 'duckduckgo' | 'bing' | 'marginalia', string>>;

/** Brave when `BRAVE_API_KEY` is set, then the keyless engines: DuckDuckGo, Bing RSS, Marginalia. */
export function defaultSearchProvider(env: NodeJS.ProcessEnv = process.env, endpoints: SearchEndpoints = {}, options?: FallbackSearchOptions): SearchProvider {
  return fallbackSearchProvider(
    [
      ...(env.BRAVE_API_KEY ? [braveProvider(env.BRAVE_API_KEY, endpoints.brave)] : []),
      duckDuckGoProvider(endpoints.duckduckgo),
      bingProvider(endpoints.bing),
      marginaliaProvider(endpoints.marginalia),
    ],
    options,
  );
}

export function createWebSearchTool(provider: SearchProvider): Tool {
  return defineTool({
    name: 'web_search',
    description: 'Search the web. Returns up to 10 results with title, URL and snippet, and names the search engine that answered. Use web_fetch to read a result.',
    input: z.object({ query: z.string().min(1) }),
    gate: { subject: () => ({}), unmatched: 'ask' },
    async execute({ query }, ctx) {
      const results = await provider(query, ctx.signal);
      if (!results.length) return 'No results';
      const list = results.map((r, i) => `${i + 1}. ${r.title}\n   ${r.url}\n   ${r.snippet}`).join('\n\n');
      return `Results from ${results[0]?.provider ?? provider.name}:\n\n${list}`;
    },
  });
}

export const webSearchTool = createWebSearchTool(defaultSearchProvider());
export const webTools: Tool[] = [webFetchTool, webSearchTool];
