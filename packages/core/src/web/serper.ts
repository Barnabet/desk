import { errorDetail, WebApiError } from './errors';

// Serper (serper.dev): Google results over JSON. Its response fields are only documented by example, so everything
// here is read defensively: a missing field is skipped, never an error.

export const SERPER_BASE = 'https://google.serper.dev';
const SERPER_TIMEOUT_MS = 10_000;

export type SerperType = 'web' | 'news' | 'images' | 'videos' | 'places' | 'scholar' | 'patents' | 'shopping';
export const SERPER_TYPES: readonly SerperType[] = ['web', 'news', 'images', 'videos', 'places', 'scholar', 'patents', 'shopping'];

export type SerperQuery = { q: string; type: SerperType; gl?: string; hl?: string; location?: string; tbs?: string; page?: number; num?: number };

type Item = Record<string, unknown>;
/** Serper's JSON answer, read loosely. */
export type SerperResponse = Record<string, unknown> & { organic?: Item[] };

export type SearchTime = 'hour' | 'day' | 'week' | 'month' | 'year';

/** Google's `tbs` value for a time range. */
export function timeToTbs(t: SearchTime): string {
  return `qdr:${t[0]}`;
}

/** One Serper query. Throws `WebApiError` on a non-2xx answer, a timeout or a network failure; the key is never in the message. */
export async function serperSearch(key: string, query: SerperQuery, signal: AbortSignal, base: string = SERPER_BASE): Promise<SerperResponse> {
  const { type, ...body } = query;
  let res: Response;
  try {
    res = await fetch(`${base}/${type === 'web' ? 'search' : type}`, {
      method: 'POST',
      headers: { 'x-api-key': key, 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.any([signal, AbortSignal.timeout(SERPER_TIMEOUT_MS)]),
    });
  } catch (err) {
    if (signal.aborted) throw err;
    const timedOut = err instanceof Error && err.name === 'TimeoutError';
    throw new WebApiError('serper', null, timedOut ? `no answer within ${SERPER_TIMEOUT_MS / 1000}s` : `request failed: ${err instanceof Error ? err.message : String(err)}`);
  }
  if (!res.ok) throw new WebApiError('serper', res.status, (await errorDetail(res)).split(key).join('***'));
  return (await res.json()) as SerperResponse;
}

const str = (v: unknown): string => (typeof v === 'string' ? v.replace(/\s+/g, ' ').trim() : typeof v === 'number' ? String(v) : '');
const items = (v: unknown): Item[] => (Array.isArray(v) ? v.filter((x): x is Item => !!x && typeof x === 'object') : []);
const joinParts = (parts: unknown[], sep = ' · ') => parts.map(str).filter(Boolean).join(sep);

/** `n. title`, then indented lines, skipping empty ones. */
function entry(n: number, title: unknown, lines: string[]): string {
  return [`${n}. ${str(title) || '(untitled)'}`, ...lines.filter(Boolean).map((l) => `   ${l}`)].join('\n');
}

const formatters: Record<SerperType, { key: string; line: (r: Item) => string[] }> = {
  web: {
    key: 'organic',
    line: (r) => [
      joinParts([r.link, r.date]),
      str(r.snippet),
      items(r.sitelinks).length ? `Also: ${items(r.sitelinks).map((s) => `${str(s.title)} (${str(s.link)})`).join('; ')}` : '',
    ],
  },
  news: { key: 'news', line: (r) => [str(r.link), joinParts([r.source, r.date]), str(r.snippet)] },
  images: {
    key: 'images',
    line: (r) => [
      joinParts([r.imageUrl, r.imageWidth && r.imageHeight ? `${str(r.imageWidth)}×${str(r.imageHeight)}` : '']),
      r.link ? `on ${str(r.link)}` : '',
      joinParts([r.source, r.domain]),
    ],
  },
  videos: { key: 'videos', line: (r) => [str(r.link), joinParts([r.channel ?? r.source, r.duration, r.date]), str(r.snippet)] },
  places: {
    key: 'places',
    line: (r) => [
      str(r.address),
      joinParts([r.rating ? `${str(r.rating)}${r.ratingCount ? ` (${str(r.ratingCount)} reviews)` : ''}` : '', r.category ?? r.type, r.phoneNumber]),
      str(r.website),
    ],
  },
  scholar: {
    key: 'organic',
    line: (r) => [str(r.link), joinParts([r.publicationInfo, r.year, r.citedBy !== undefined ? `cited by ${str(r.citedBy)}` : '']), str(r.snippet)],
  },
  patents: {
    key: 'organic',
    line: (r) => [joinParts([r.publicationNumber, r.link]), joinParts([r.assignee, r.inventor, r.priorityDate ? `priority ${str(r.priorityDate)}` : '']), str(r.snippet), r.pdfUrl ? `PDF: ${str(r.pdfUrl)}` : ''],
  },
  shopping: { key: 'shopping', line: (r) => [str(r.link), joinParts([r.price, r.source, r.rating ? `${str(r.rating)}★${r.ratingCount ? ` (${str(r.ratingCount)})` : ''}` : '', r.delivery])] },
};

/** Compact text for one Serper answer, and how many primary results it held. */
export function formatSerper(type: SerperType, res: SerperResponse): { text: string; count: number } {
  const { key, line } = formatters[type];
  const results = items(res[key]);
  const parts: string[] = [];
  if (type === 'web') {
    const box = res.answerBox as Item | undefined;
    const answer = box ? str(box.answer) || str(box.snippet) : '';
    if (answer) parts.push(`Answer: ${answer}${box?.link ? ` (${str(box.link)})` : ''}`);
    const kg = res.knowledgeGraph as Item | undefined;
    if (kg && str(kg.title)) parts.push(`${str(kg.title)}${kg.type ? ` (${str(kg.type)})` : ''}${kg.description ? `: ${str(kg.description)}` : ''}${kg.website ? ` ${str(kg.website)}` : ''}`);
  }
  parts.push(...results.map((r, i) => entry(i + 1, r.title, line(r))));
  if (type === 'web') {
    const asked = items(res.peopleAlsoAsk).map((q) => str(q.question)).filter(Boolean);
    if (asked.length) parts.push(`People also ask: ${asked.join(' | ')}`);
    const related = items(res.relatedSearches).map((q) => str(q.query)).filter(Boolean);
    if (related.length) parts.push(`Related: ${related.join(' | ')}`);
  }
  return { text: parts.join('\n\n'), count: results.length };
}
