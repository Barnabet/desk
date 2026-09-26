# Reading, browsing, crawling and archives

## fetch.py: how a page becomes Markdown

1. The response is fetched politely (cached 24 h; `--refresh` revalidates with ETag/Last-Modified).
2. The type is sniffed: HTML, PDF, RSS/Atom/JSON Feed, JSON, text, image or other binary.
3. HTML: trafilatura extracts the main content, and a structural converter renders the page's `<main>`/`<article>`.
   The structural version is used when trafilatura found too little, dropped code blocks (a lone one, or code
   nested in lists), content headings (price cards, docs sections) or prices shown outside links. Headings in
   comments, related-story teasers and sidebars don't count. When neither version has everything, a `Note:` says
   what may be missing; `--full` keeps the whole page, navigation included. Cloudflare's e-mail protection is
   decoded (`git@github.com`, not `[email protected]`); Sphinx `¶` anchors, Wikipedia's citation markers and
   `[edit]` links, and blockquotes that only wrap an example are cleaned up.
4. Metadata comes from meta tags, JSON-LD and trafilatura: title (without the site-name suffix), author,
   published and updated dates, site, language, canonical URL, DOI. A date found only by heuristics is shown as
   "date: … (guessed from the page)". Bylines that are headings or sentences are dropped, job titles after a name
   too ("Joe Doe, Senior Editor" → "Joe Doe"); a CDN host is never the site name; a wiki page is dated by its
   last revision.
5. Extracted documents are cached next to the responses, so `--outline`, then `--section`, then `--grep` cost one
   request and little time.

Fallbacks when the live page can't be read (404, 410, 403, 451, 429 after retries, an anti-bot challenge, including
interstitials that answer 200 such as Akamai's `bm-verify` page or "Checking your browser", a dead host), in order:
1. **PubMed and PMC pages**: the same record from NCBI's E-utilities API (the abstract, or PMC's full text).
2. **A DOI link** (doi.org, or a publisher's `/doi/10.…` page): the open-access copy OpenAlex lists (its PDF or
   landing page in a repository or on the publisher's free version). Copies that turn out to be block pages are
   skipped. The header says where it came from; the canonical URL stays the DOI.
3. **The Wayback Machine** (`--no-archive` disables this): for a DOI, first the publisher page it resolves to
   (doi.org links are archived as redirects); then the URL itself. The capture is found through the availability
   API, else the CDX index (it lists captures the availability API misses, often those of long-dead pages), else,
   when the CDX index is overloaded, the Wayback Machine's own page for the date, which redirects to the nearest
   capture. A capture that is itself a block page ("Cookies must be enabled") is skipped for the next nearest.

Flags on the header:
- **needs JavaScript**: an empty app shell. Use `browse.py render URL`.
- **paywalled**: the page says so in its structured data. Only the free part is shown; look for another source.
- **Archived copy from DATE**: the live page was gone, forbidden, blocked or dead, so the nearest Wayback snapshot
  was read. Cite the original URL and the snapshot.

## Paging long pages

- Sections are numbered in document order (`§0` is text before the first heading). `--section 3` includes its
  subsections; `--section 3-5`, `--section 2,7` and `--section "install"` (title words) work too.
- `--grep REGEX` is case-insensitive (`--case-sensitive` otherwise), shows `--context N` lines and each match's
  section, so you can jump to it. An invalid regex is searched literally.
- `--max-chars` (default 30 000) is the budget. The footer names the next `--offset`.
- `--meta` prints only the header: cheap for checking dates and authors of many pages.

## Batches

`fetch.py --urls FILE` (or `-` for stdin) fetches in parallel (`--workers 6`) while each site stays paced. With
`--save DIR` it writes each page's raw response and Markdown (with a metadata header) and prints one line per URL;
without it, the budget is shared between the pages. Pipe search results straight in:

```bash
python3 scripts/search.py "topic" --urls-only | python3 scripts/fetch.py --urls - --save pages/
grep -rln "keyword" pages/ | head
```

## Images and tables

- `--images DIR` downloads the page's pictures (main content first, icons and tracking pixels skipped), converts
  formats view_image can't show and downsizes anything over 1568 px or 3.7 MB. SVG is skipped.
- `--tables` prints data tables (layout tables are skipped, colspans repeated). `--tables csv` prints CSV;
  with `--save DIR` each table is also written as a CSV file.

## browse.py

- Browser order: `DESK_BROWSER` (set by the skill's browser runtime), installed Google Chrome, installed Microsoft
  Edge, then Playwright's Chromium (`PLAYWRIGHT_BROWSERS_PATH`). `browse.py check` shows which one runs.
- `--wait-for` takes `load` (default, plus a short network-idle wait), `networkidle`, `domcontentloaded`, or a CSS
  selector. `--wait 2` adds seconds.
- `--click TEXT` clicks the first visible button or link whose text matches (a regex), `--click-times` times,
  waiting for the network after each. It refuses buttons that would submit a POST form.
- `--scroll N` scrolls to the bottom N times and stops when the page stops growing.
- `--viewport desktop|tablet|mobile|WxH`, `--no-images` for faster text renders.
- Renders are cached for an hour (`--refresh` renders again).
- Screenshots: the viewport by default, `--full-page` split into tiles of at most 1568 px (with a small overlap,
  `--max-tiles`), or `--element SELECTOR`. Then view_image. Before a full-page capture the page is scrolled
  through once, so lazy images and sections drawn on sight are there. When tiles still come out empty (pages that
  paint only what is on screen), the page is captured again screen by screen while scrolling, with fixed headers
  and banners hidden after the first screen. Tiles that stay almost empty are marked: skip them. `--no-reveal`
  captures the page as it is.
- The browser is honest: its own User-Agent, no stealth tricks. If a page shows a challenge, it is reported, not
  solved.

## crawl.py

- Scope: `prefix` (default: the start URL's folder), `host`, or `domain` (subdomains too); `--include` and
  `--exclude` regexes refine it. Images, media, archives, scripts and styles are never fetched.
- robots.txt (RFC 9309): Desk's own group if one names `desk`, else `*`; the longest matching rule wins; an
  unreachable robots.txt (5xx or no answer) means no crawling; a missing one (4xx) allows everything.
  Crawl-delay is honoured, and requests are never closer than one second per site.
- Pages with `noindex` (meta or X-Robots-Tag) are not saved; `nofollow` pages are not followed.
- Output: `pages/*.md` (each with its URL, title, fetch time and depth, and the untrusted-content line),
  `index.json` (every URL with status, file, title, size and in-scope links) and `index.md`.
- `--resume` continues from `index.json`, even with a larger `--depth` or `--max-pages`.
- `map` lists URLs from sitemaps (indexes and `.xml.gz` included): robots.txt's, the site root's, then, when none
  lists anything in scope, the start folder's and its parents' `sitemap.xml` (documentation generators put one at
  the docs root, e.g. `docs.astral.sh/uv/sitemap.xml`). With no sitemap, or `--bfs`, it walks links (`--max-fetch`
  pages at most). `crawl --sitemap` seeds from the same sitemaps.

## archive.py

- `nearest` and `read` find the capture with the availability API, then the CDX index, then the Wayback page for
  the date (as fetch.py does). `list` uses the CDX index, which can be slow or overloaded: it times out after
  `--timeout` seconds and says so; `--collapse` drops identical captures, `--match prefix` lists everything under
  a path.
- `read` returns the capture as it was archived (no Wayback toolbar), labelled with its date.
- `--source commoncrawl` searches Common Crawl's newest index (`--crawls N` for more) and reads the page straight
  from its WARC file with a byte-range request.
- `save --yes` asks the Wayback Machine to capture a page. It publishes the page: only when the user asked.

## feeds.py and video.py

- `feeds.py find` reads `<link rel="alternate">` feeds, knows GitHub (`releases.atom`, `tags.atom`,
  `commits.atom`) and subreddit feeds, and probes common locations (`/feed`, `/rss.xml`, `/atom.xml` …) when none
  are linked. `watch` keeps its state in `research/feeds.json` in the workspace; the first run records everything.
- `video.py captions` prefers captions written by people in `--lang`, then automatic ones (`--no-auto` refuses
  them), and falls back to the language there is (the header says which). `--as text` groups cues into ~30 s
  paragraphs (`--every`), `--grep` searches cue by cue with timestamps, `--as srt|vtt|json --out FILE` saves.
