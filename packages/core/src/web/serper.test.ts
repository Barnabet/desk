import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { WebApiError } from './errors';
import { formatSerper, serperSearch, timeToTbs, type SerperResponse } from './serper';

type Seen = { path: string; key: string | undefined; body: Record<string, unknown> };
const seen: Seen[] = [];
let reply: { status: number; body: unknown } = { status: 200, body: {} };
let server: Server;
let base: string;
beforeAll(async () => {
  server = createServer((req, res) => {
    let raw = '';
    req.on('data', (c) => (raw += c));
    req.on('end', () => {
      seen.push({ path: req.url ?? '', key: req.headers['x-api-key'] as string | undefined, body: raw ? JSON.parse(raw) : {} });
      res.writeHead(reply.status, { 'content-type': 'application/json' }).end(JSON.stringify(reply.body));
    });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
afterAll(() => server.close());
beforeEach(() => {
  seen.length = 0;
  reply = { status: 200, body: {} };
});

const signal = new AbortController().signal;

const WEB: SerperResponse = {
  answerBox: { answer: 'Paris' },
  knowledgeGraph: { title: 'Paris', type: 'Capital of France', description: 'Paris is the capital of France.', website: 'https://paris.fr' },
  organic: [
    { title: 'Paris - Wikipedia', link: 'https://en.wikipedia.org/wiki/Paris', snippet: 'Paris is the capital...', date: 'Oct 1, 2026', sitelinks: [{ title: 'History', link: 'https://en.wikipedia.org/wiki/Paris#History' }] },
    { title: 'Visit Paris', link: 'https://parisjetaime.com', snippet: 'Official site' },
  ],
  peopleAlsoAsk: [{ question: 'Is Paris the capital of France?', snippet: 'Yes', link: 'https://x.org' }],
  relatedSearches: [{ query: 'paris population' }, { query: 'paris map' }],
};

describe('serperSearch', () => {
  it('posts a web search to /search with the key header and no type field', async () => {
    reply.body = WEB;
    const res = await serperSearch('K', { q: 'capital of france', type: 'web', gl: 'fr', hl: 'en', num: 5 }, signal, base);
    expect(seen[0]).toEqual({ path: '/search', key: 'K', body: { q: 'capital of france', gl: 'fr', hl: 'en', num: 5 } });
    expect(res.organic).toHaveLength(2);
  });

  it('posts other types to their own path', async () => {
    await serperSearch('K', { q: 'desk', type: 'news', tbs: 'qdr:w', page: 2 }, signal, base);
    expect(seen[0]).toMatchObject({ path: '/news', body: { q: 'desk', tbs: 'qdr:w', page: 2 } });
  });

  it('flags a refused key as a key problem, without the key in the message', async () => {
    reply = { status: 403, body: { message: 'Unauthorized.', statusCode: 403 } };
    const err = await serperSearch('secret-key-123', { q: 'x', type: 'web' }, signal, base).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(WebApiError);
    expect((err as WebApiError).keyProblem).toBe(true);
    expect((err as WebApiError).status).toBe(403);
    expect((err as Error).message).toContain('Unauthorized');
    expect((err as Error).message).not.toContain('secret-key-123');
  });

  it('does not treat a server error as a key problem', async () => {
    reply = { status: 500, body: { message: 'boom' } };
    const err = (await serperSearch('K', { q: 'x', type: 'web' }, signal, base).catch((e: unknown) => e)) as WebApiError;
    expect(err.keyProblem).toBe(false);
    expect(err.provider).toBe('serper');
  });
});

describe('formatSerper', () => {
  it('puts the answer and knowledge graph first, then results, questions and related searches', () => {
    const { text, count } = formatSerper('web', WEB);
    expect(count).toBe(2);
    const order = ['Answer: Paris', 'Paris (Capital of France)', '1. Paris - Wikipedia', 'Oct 1, 2026', 'History', '2. Visit Paris', 'People also ask:', 'Related: paris population | paris map'];
    const at = order.map((s) => text.indexOf(s));
    expect(at.every((i) => i >= 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
  });

  it('shows the source and date of news', () => {
    const { text, count } = formatSerper('news', { news: [{ title: 'Desk ships', link: 'https://n.example/a', snippet: 'It shipped', source: 'The Verge', date: '2 hours ago' }] });
    expect(count).toBe(1);
    expect(text).toContain('1. Desk ships');
    expect(text).toContain('The Verge · 2 hours ago');
  });

  it('shows the address, rating and phone of places', () => {
    const { text } = formatSerper('places', { places: [{ title: 'Café', address: '1 Rue X, Paris', rating: 4.5, ratingCount: 120, phoneNumber: '+33 1 23', website: 'https://cafe.fr' }] });
    expect(text).toContain('1 Rue X, Paris');
    expect(text).toContain('4.5 (120 reviews)');
    expect(text).toContain('+33 1 23');
    expect(text).toContain('https://cafe.fr');
  });

  it('shows publication info, year and citations for scholar results', () => {
    const { text } = formatSerper('scholar', { organic: [{ title: 'Attention', link: 'https://arxiv.org/x', publicationInfo: 'A Vaswani - NeurIPS', year: 2017, citedBy: 100000 }] });
    expect(text).toContain('A Vaswani - NeurIPS');
    expect(text).toContain('2017');
    expect(text).toContain('cited by 100000');
  });

  it('lists the image URL and the page it is on', () => {
    const { text } = formatSerper('images', { images: [{ title: 'Logo', imageUrl: 'https://i.example/l.png', imageWidth: 800, imageHeight: 600, link: 'https://example.com/about' }] });
    expect(text).toContain('https://i.example/l.png');
    expect(text).toContain('800×600');
    expect(text).toContain('https://example.com/about');
  });

  it('counts zero when there are no results', () => {
    expect(formatSerper('web', { organic: [] }).count).toBe(0);
    expect(formatSerper('videos', {}).count).toBe(0);
  });
});

describe('timeToTbs', () => {
  it('maps each range to its qdr value', () => {
    expect(['hour', 'day', 'week', 'month', 'year'].map((t) => timeToTbs(t as Parameters<typeof timeToTbs>[0]))).toEqual(['qdr:h', 'qdr:d', 'qdr:w', 'qdr:m', 'qdr:y']);
  });
});
