# The honest client: cache, pacing and settings

## Rules the scripts follow

- **Identity.** Pages get a normal browser User-Agent. APIs and engines' JSON endpoints get
  `Desk/1.0 (+https://github.com/Barnabet/desk)`, as their policies ask. `crawl.py` identifies itself as
  `Mozilla/5.0 (compatible; Desk/1.0; +https://github.com/Barnabet/desk)` and obeys robots.txt rules for `desk`.
  The browser keeps its own User-Agent.
- **No evasion.** No CAPTCHA solving, no TLS fingerprint spoofing, no stealth plugins, no cookies carried between
  runs, no logins, no paywall bypasses. A challenge page or a 403/429 is reported as a block; the scripts then
  use the Wayback Machine or other sources, and say so.
- **TLS.** The operating system's trust store (truststore), else certifi's bundle.

## The shared store

One SQLite file, `$XDG_CACHE_HOME/desk-web/web.sqlite` (the temp cache Desk gives every thread), shared by every
process and thread:

- **Response cache:** search results for 1 hour, pages for 24 hours, archive lookups for 6 hours to 7 days.
  Expired pages are revalidated with ETag/Last-Modified (a 304 costs almost nothing). Bodies are compressed and the
  cache is capped (200 MB by default), least recently used first.
- **Extracted documents and browser renders** are cached next to the responses (24 h and 1 h).
- **Pacing:** at most 2 requests in flight and one request start per second per site; engines one request per
  2 s each. A slot is taken only when it is due, and the HTTP client is ready before, so requests leave when their
  slot is taken and arrive spaced even on a loaded machine. Stricter where a service asks: arXiv 3 s, GitHub search 6 s, Nominatim 1.1 s, crates.io 1 s, Wayback
  1.5 s. Crawls use at least robots.txt's Crawl-delay.
- **Retries:** 429 and 503 are retried twice, honouring Retry-After up to 20 s; a longer wait is reported instead.
  Connection drops and timeouts are retried once. Anti-bot challenges are never retried.
- **Engine health:** an engine that answers 429, 202 (DuckDuckGo's anomaly page) or a challenge rests for 10 to
  30 minutes; timeouts rest 5 minutes. Searches skip resting engines and say so. `search.py --engines-status`
  checks every engine live and clears the rest periods of those that answer.

## Environment variables

| Variable | Effect |
|---|---|
| `DESK_BROWSER` | the Chrome/Edge executable for `browse.py` (set by the skill's browser runtime) |
| `PLAYWRIGHT_BROWSERS_PATH` | where Playwright's own Chromium is, when Desk downloaded it |
| `DESK_WEB_CACHE` | the store folder (default `$XDG_CACHE_HOME/desk-web`) |
| `DESK_WEB_CACHE_MB` | the cache cap (default 200) |
| `DESK_NO_CACHE=1` | no response cache (pacing still applies) |
| `DESK_WORKSPACE` | where `research/sources.json` and `research/feeds.json` live (default: the current folder) |
| `DESK_DEBUG=1` | full tracebacks instead of one-line errors |
| `DESK_WEB_RATE_SCALE`, `DESK_WEB_SERVICE_BASE` | self-test only: faster pacing and a local fixture server |

## Troubleshooting

- **"timed out"**: the site or engine is slow; try again later or use another source. Engines rest automatically.
- **"blocked by an anti-bot challenge"**: don't retry in a loop. Read the archived copy, find the same fact
  elsewhere, or ask the user to open the page.
- **"the host name does not resolve"**: a dead or mistyped host; the archive fallback runs automatically.
- **Garbled text**: the page declared the wrong encoding; `--raw` shows the source.
- **Thin extraction** or a `Note:` that headings, prices or code may be missing: try `--full`, then
  `browse.py render`.
- **"the Wayback Machine could not be reached"**: its CDX index is often overloaded (503); the lookup already falls
  back to the availability API and the Wayback page for the date. Try again in a few minutes.
- **Empty screenshot tiles**: the page draws parts only while they are on screen; the capture already retries
  screen by screen. Use `--element SELECTOR` for the part you need, or `browse.py render --grep` for its text.
