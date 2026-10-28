# web_fetch, web_map, web_crawl and web_extract (Firecrawl)

Firecrawl (api.firecrawl.dev) renders pages in a browser and returns Markdown. deskd holds the key. Private, intranet
and credential-carrying URLs are never sent there.

## web_fetch

| Input | Default | Meaning |
|---|---|---|
| `url` | | The page |
| `main_only` | true | Drop navigation, headers and footers |
| `wait_ms` | 0 | Wait for JavaScript (up to 10000) |
| `fresh` | false | Skip Firecrawl's cached copy (up to two days old) |
| `screenshot` | false | Save a PNG under `web/shots/` for `view_image` |
| `links` | false | List the page's links |
| `pdf_max_pages` | 20 | Read at most this many PDF pages (up to 200) |
| `raw` | false | Fetch directly and return non-HTML bodies raw |
| `save` | false | Save the whole page under `web/pages/` and return its path, outline and a preview |

URLs ending in `.json`, `.csv`, `.tsv`, `.txt`, `.md`, `.xml`, `.rss`, `.atom`, `.yaml` or `.yml`, and
`raw.githubusercontent.com`, are fetched directly and come back raw. Without 100k characters of room an answer is cut;
use `save: true` for long pages. A page that is gone (403, 404, 410, 451) or a host that is down falls back to the
Wayback Machine's copy, labelled as archived.

Firecrawl reads HTML, PDF (with OCR), DOCX and similar office files, and images. It does not read SVG or HEIC.

## web_map

`url`, `search` (rank URLs by relevance), `limit` (default 100, up to 1000), `include_subdomains` (default false).
Returns URLs with titles where known. 1 credit.

## web_crawl

`url`, `limit` (default 10, up to 50 pages), `max_depth`, `include_paths` and `exclude_paths` (regexes on the path),
`main_only`. The crawl stays on the start URL's host, follows the sitemap, respects robots.txt, and stops after 10
minutes. Pages land in `web/crawls/<host>-<time>/` as `.md` files with an `index.json` (file, url, title, chars).

## web_extract

`url`, `prompt` (what to extract), optional `schema` (JSON Schema). Returns JSON. Good schemas name every field and
its type, e.g. `{"type": "object", "properties": {"price": {"type": "number"}, "currency": {"type": "string"}}}`.

## Credits

| Call | Credits |
|---|---|
| A page (`web_fetch`, each crawled page) | 1 |
| Each PDF page read | +1 |
| `web_extract` | 5 |
| `web_map` | 1 |

A page Firecrawl answers with a 403 or 404 still costs 1. Out of credits, the tools fall back to plain fetches and the
user gets a notice.
