# Sources, quotes and citations

## The ledger

`scripts/sources.py` keeps `sources.json` in the current folder (`--ledger FILE` for another, before the command).
Each source has a number and records `url`, `title`, `site`, `author`, `published`, `accessed` (default today) and
`saved`, the page file `web_fetch` wrote with `save: true` (or a crawled page). Adding the same URL again updates its
entry and keeps its number, so an answer that already cites `[3]` stays right.

## Checking quotes

`sources.py check-quote <id> "<quote>"` looks for the quote in the saved copy. Whitespace, curly quotes, dashes,
non-breaking spaces and Markdown emphasis (`*`, `_`, backticks) are ignored on both sides; words and case are not.
It prints the line where the quote is, or "Not found" with the closest line and exit code 1. Quote only what passes;
otherwise paraphrase and cite, or fetch the page again (`fresh: true`) if it may have changed.

## Formats

| Style | Example |
|---|---|
| numbered (default) | `[1] Annual report. Example. https://example.org/report (accessed 2026-10-28).` |
| apa | `Doe, J. (2026, March 2). Annual report. Example. https://example.org/report` |
| mla | `Doe, J. "Annual report." Example, 2 Mar. 2026, https://example.org/report. Accessed 28 Oct. 2026.` |

Without an author, APA starts with the title and MLA leaves the author out. Without a date, APA writes `(n.d.)`.
Dates are `YYYY`, `YYYY-MM` or `YYYY-MM-DD`. Authors are written as you give them: `Last, F.` for a person, the
name for an organisation.

## Good sources

- Primary first: the statute, the filing, the paper, the vendor's own docs, the dataset.
- Two independent sources for a claim that matters; a press release and the articles that repeat it are one source.
- Note the publication date, and prefer the most recent authoritative one for anything that changes.
