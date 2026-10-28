# web_search (Serper)

`web_search` sends the query to Serper (google.serper.dev), which returns Google's results as JSON. deskd holds the
key; the answer ends with `(via Serper)`, or `(via keyless fallback: …)` when Serper had no key or failed.

## Inputs

| Input | Meaning |
|---|---|
| `query` | The search, with Google operators |
| `type` | `web` (default), `news`, `images`, `videos`, `places`, `scholar`, `patents`, `shopping` |
| `country` | Two-letter country for local results (Google's `gl`), e.g. `de` |
| `language` | Interface language (`hl`), e.g. `en`, `fr` |
| `location` | A place to search from, e.g. "Austin, Texas, United States" |
| `time` | `hour`, `day`, `week`, `month` or `year` (Google's `tbs=qdr:*`) |
| `page` | 1 to 10 |
| `num` | Results per page, up to 20 |

## Operators

| Operator | Example |
|---|---|
| Exact phrase | `"carbon border adjustment"` |
| One site or domain | `site:europa.eu`, `site:*.gov` |
| File type | `filetype:pdf`, `filetype:xlsx` |
| Word in the title | `intitle:benchmark` |
| Exclude | `jaguar -car` |
| Either | `(sqlite OR duckdb) benchmark` |
| Before / after a date | `after:2026-01-01`, `before:2025-06-30` |

## What each type returns

- `web`: an answer box when Google has one, a knowledge-graph line, then results (title, URL, date, snippet, sitelinks),
  people-also-ask questions and related searches.
- `news`: title, URL, source and how long ago.
- `scholar`: title, URL, publication info (authors, venue), year and citation count.
- `places`: name, address, rating and review count, category, phone, website.
- `images`: image URL and size, and the page it is on.
- `videos`: URL, channel, duration, date.
- `patents`: publication number, assignee, inventor, priority date, PDF link.
- `shopping`: price, seller, rating, delivery.

## Cost and limits

About one credit per query. Serper's free tier starts with 2,500 queries. Only successful answers are charged.
