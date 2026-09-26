# Search engines

`search.py` queries every engine of the chosen groups in parallel, then fuses each group's lists by reciprocal
rank: a result's score is the sum over engines of `weight / (10 + rank)`. With a handful of engines, k = 10 (not
the usual 60) lets rank count as much as weight, so the engines' top results interleave (ddg 1, ddg 2, ddg 3,
bing 1, ddg 4 …) instead of one engine's whole list coming first. Duplicates are merged by canonical URL (scheme,
`www.`, trailing slashes, index pages and tracking parameters ignored) or, for papers, by DOI, and a site gets at
most two of the first `--max` places unless `--site` asks for one site. Papers are also scored by how many of the
query's words their title and abstract contain, so a famous but off-topic work does not take a slot. Each engine's
parser is separate, so a markup change breaks one engine, not search.

## Engines

| Engine | Group | Default | What it returns | Notes |
|---|---|---|---|---|
| `bing` | web | yes | Bing's RSS results; its HTML page when RSS fails | Anonymous Bing sometimes ignores rare words; weight 0.8 |
| `bing-html` | web | fallback | Bing's HTML results page | Result links are unwrapped from Bing's redirects |
| `ddg` | web | yes | DuckDuckGo's HTML page, its lite page when throttled | Weight 1.0; dates when DuckDuckGo shows them |
| `ddg-html`, `ddg-lite` | web | fallback | the two DuckDuckGo pages separately | A throttled HTML page (202) cools down; lite takes over |
| `marginalia` | web | yes | Marginalia's independent index of the small web | Weight 0.5; slow some days (6 s timeout) |
| `mwmbl` | web | yes | Mwmbl's community index | Weight 0.4; titles only, no snippets |
| `google-news` | news | yes | Google News RSS: headline, outlet, date | Links redirect through Google (a consent page in some regions); merged with the same story's direct link from Bing News |
| `bing-news` | news | yes | Bing News RSS: headline, outlet, date, summary | Direct article links |
| `openalex` | papers | yes | Works with DOI, authors, venue, year, citations, abstract, open-access link | The broadest scholarly index |
| `arxiv` | papers | yes | Preprints with authors, category, abstract, PDF link | One request per 3 s (arXiv's policy) |
| `crossref` | papers | yes | DOI metadata: journals, books, proceedings | |
| `pubmed` | papers | yes | Biomedical articles: authors, journal, date, PMID, DOI | Two requests (search, then summaries) |
| `github` | code | yes | Repositories: stars, language, licence, last push | 10 searches a minute without a key: paced 6 s apart |
| `stackoverflow` | code | yes | Questions: score, answers, accepted, tags | |
| `hackernews` | code | yes | Stories: points, comments, discussion link | |
| `reddit` | forums | yes | Reddit posts: subreddit, author, date, the post's opening text | Search RSS; the comments are on the post page |
| `pypi` | packages | yes | Project pages for names in the query | PyPI has no keyword search API (its search page is behind a bot check) |
| `npm` | packages | yes | Packages: version, monthly downloads, repository | |
| `crates` | packages | yes | Rust crates: version, downloads, repository | |
| `wikipedia` | wiki | yes | Articles with their opening sentences | `--lang` picks the Wikipedia |
| `wikidata` | wiki | yes | Entities (Q-ids) with descriptions | |
| `openlibrary` | books | yes | Books: authors, first publication, editions, ISBN | |
| `nominatim` | places | yes | OpenStreetMap places: address, type, coordinates | One request a second (its policy) |
| `youtube` | video | yes | Videos: channel, duration, views, age | `video.py` reads transcripts |

`search.py --list-engines` prints this list; `--engines a,b` runs exactly those engines.

## Status on 2026-09-25 (from this Mac)

`search.py --engines-status` at 09:45: 24 of 25 up, every group answering (0.2 to 2 s per engine). Marginalia is
the flaky one: it timed out after 6 s, as it does most days (it then rests for 5 minutes and searches carry on
without it; its weight is low, so it rarely changes the first results). An earlier smoke the same morning saw one
transient TLS failure on the Wikipedia API; the client now retries such a failure once against certifi's bundle.
Blocked or throttled for keyless clients, and therefore not used: Google (CAPTCHA), Brave (429), Mojeek,
Startpage, Yahoo, Jina (now needs a key), Reddit JSON (403), Semantic Scholar (shared-pool 429), GDELT (429).

## Operators and filters

| Flag | Bing | DuckDuckGo | News | Papers | Code | Others |
|---|---|---|---|---|---|---|
| `--site` | `site:` | `site:` | `site:` | post-filter | post-filter | post-filter |
| `--filetype` | `filetype:` | `filetype:` | — | — | — | post-filter (URL extension) |
| `--since` | `ez1/ez2/ez3` or a day range | `df=d/w/m/y` or a range | `when:`/`after:` (Google), `interval` (Bing) | OpenAlex, Crossref, arXiv, PubMed date filters | GitHub `pushed:>=`, Stack Exchange `fromdate`, HN `created_at_i` | YouTube upload-date filter |
| `--lang`, `--region` | `setlang`, `cc`, `mkt` | `kl` | `hl`, `gl`, `ceid` | — | — | Wikipedia language, Nominatim `accept-language`/`countrycodes` |

Without `--lang` and `--region`, an English-looking query (ASCII, no common French, German, Spanish, Italian,
Portuguese, Dutch or Swedish words) asks Bing and Bing News for US English (`mkt=en-US`): anonymous Bing otherwise
picks the market from the IP address, and an English query from France got French shops and fr.wikipedia.

Results with a known date older than `--since` are dropped; undated results are kept.

Quotes (`"exact phrase"`), `-word` and `OR` pass through to the web engines that support them.

## --auto

`--auto` adds groups from the query's wording, and the output says which words triggered them, or that none did:
- news: latest, today, this week, announced, 2025/2026 …
- papers: paper, study, arXiv, DOI, peer-reviewed, meta-analysis, clinical trial …
- code: error, exception, API, library, a programming language, `func()`, nginx, config, "how to enable/install/
  configure/deploy …" …
- forums: Reddit, opinions, experiences, "worth it", recommend, reviews, "vs" …
- packages: package, library for, pip install, alternatives to …
- wiki: a question starting with "who", "what is", history of, born, capital of, designed by …
- books: book, novel, author of, ISBN …
- places: where is, located, near me, address of …
- video: video, YouTube, talk, lecture, webinar …

When the wording is ambiguous, add the flags yourself.

## When results are thin

1. Rephrase: shorter, the words a source would use, the official name, the other language.
2. Check `--engines-status`. A cooling engine says how long it rests; don't hammer it.
3. Add the specialised group that owns the question (`--papers`, `--code`, `--news`).
4. Go to the source: `crawl.py map` on the official site, `feeds.py` for its news, `archive.py` for old versions.
