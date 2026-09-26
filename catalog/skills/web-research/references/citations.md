# Sources, quotes and citations

## The ledger

`sources.py` keeps `research/sources.json` in the workspace (`--ledger FILE` for another, before or after the
command). Each source has an id (`S1`, `S2` …) and records:

| Field | From |
|---|---|
| `url`, `final_url` | the URL given (the canonical URL when the page declares one) and where it redirected |
| `title`, `author`, `published`, `site` | the page's metadata; `--title`, `--author`, `--date`, `--site` override |
| `venue`, `volume`, `issue`, `pages` | for a paper with a DOI: from Crossref (else OpenAlex), with the authors in order and the title |
| `author_org` | the author is an organisation (the page's JSON-LD says so, or `set --org`) |
| `date_guessed` | true when the date came from heuristics or a PDF's creation date: cited as n.d. |
| `accessed` | the day the page was read |
| `archived_url` | the latest Wayback Machine capture (looked up, never created) |
| `read_from_archive` | true when the live page was unavailable and the archive was read |
| `claims` | `S1.1`, `S1.2` …: the claim, the quote, its status, the page's matching text and the check date |

Adding the same URL again adds a claim to the existing source. Ids are never reused: with claims S2.1 to S2.3, after `remove
S2.3` the next claim is S2.4, so an answer that already cites an id keeps pointing at the same claim.

`sources.py set S3 --date 2025-06-01` (or `--author "Doe, Jane; Smith, John"`, `--title`, `--site`, `--venue`,
`--org`, `--person`) fixes a source by hand without fetching the page again.

## How quotes are checked

- Both sides are normalised: Unicode compatibility forms, case, whitespace, curly quotes, dashes, non-breaking
  and zero-width characters, and Markdown markup (bold, italics, code marks) are ignored. Punctuation at word
  edges is ignored, but underscores inside words are kept (`ngx_http_v3_module`, `__init__`) and so is a minus
  sign before a number (`-0.33` is not `0.33`). A dash joining two words counts as a space: "$0.01/GB—no surprise
  fees" contains the quote "$0.01/GB".
- `…`, `...` or `[…]` split the quote into fragments that must appear in that order.
- The main content is searched first, then the whole page (navigation, captions, footers).
- Statuses:
  - `verified`: the words are on the page.
  - `close`: a passage at least 85 % similar exists (typically a changed number or word); the page's actual
    wording is shown. Quote that wording, or explain the difference.
  - `unverified`: recorded with `--force` although it is not on the page. Say so in the answer.
  - `missing`: `verify` no longer finds it (the page changed or vanished). Check the archived copy with
    `archive.py read URL --date …` and cite that instead.
  - `unreachable`: the page could not be read during `verify`.
- Pick quotes that are short, distinctive and carry the claim (a number, a name, a verb). Avoid quoting headings
  or boilerplate.

## Citing in an answer

```bash
python3 scripts/sources.py refs --ids S4,S1,S2      # numbered in the order the answer cites them
```

Write `[1]`, `[2]` … in the text, then paste the reference list. Each line ends with an HTML comment naming the
ledger id, and archived URLs are included. Unverified quotes are listed after the references.

## Styles

- **APA 7:** `Doe, J. Q., & Smith, J. (2026, September 1). *Title*. Site. URL`. Without an author the title
  leads; without a date: `(n.d.)` and "Retrieved Month D, YYYY, from URL".
- **MLA 9:** `Doe, Jane Q., and John Smith. "Title." *Site*, 1 Sept. 2026, url. Accessed 24 Sept. 2026.`
  Three or more authors become "et al."
- **Chicago (notes-bibliography, bibliography entry):** `Doe, Jane Q., and John Smith. "Title." Site. September 1,
  2026. URL.` An undated page adds "Accessed …".
- **BibTeX:** `@online{doe2026title, author = {Doe, Jane Q. and Smith, John}, title = …, url = …, urldate = …,
  organization = …, note = {Archived at …}}`.

Organisations as authors are never inverted: the page's JSON-LD says so, the name has an organisation word (News,
Records, Institute, Team, University, Contributors …), it is a single word, or `set --org` says so. Job titles and
outlets after a byline are dropped ("Joe Doe, XBOX Wire Editor-in-Chief" → Doe, J.). When a source is a paper with
a DOI, it is cited as a journal article from the DOI registry, with the DOI link as its URL:
`Liu, S., Lu, P., & Liu, D. (2009). Title. *Journal*, *2*(1), 80–87. https://doi.org/…` (BibTeX: `@article`).

Dates guessed from the page are cited as `(n.d.)` (MLA and Chicago add "Accessed …"), and `refs` ends with the list
of them. Metadata on the web is often incomplete: check the author and date on the page (`fetch.py URL --meta`,
or read the byline), and fix them with `sources.py set ID --date/--author` before citing.
