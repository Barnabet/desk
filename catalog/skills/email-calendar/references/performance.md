# Performance

Measured on 2026-09-25 on the build Mac (Apple M-series, 8 GB RAM), inside Desk's sandbox with the skill's own
Python 3.12 runtime and `DESK_MAX_WORKERS=2`. "Cold" is the first call on a file; "cached" is any later call on
the same content (`_cache.py`, keyed by the file's content fingerprint). Peak memory is the resident set of the
script's main process. The fixtures were generated in scratch space and deleted afterwards.

## Fixtures

- **Mailbox A (attachment-heavy):** a 300 MB mbox of 22,830 messages. The build rules cap fixtures at 300 MB, so
  this stands in for the 1 GB benchmark: every step below is linear in bytes or messages, so multiply by about
  3.4 for 1 GB. Messages look like real mail: Received chains, DKIM signatures, Authentication-Results,
  multipart/alternative text and HTML in quoted-printable, RFC 2047 subjects, reply threads with References, list
  headers and Gmail labels. 8% carry a base64 attachment of 20-160 KB.
- **Mailbox B (text-heavy):** a 260 MB mbox of 33,552 plain and alternative messages with long bodies (84 MB of
  decoded text, 2,728 PDF attachments), the acceptance review's fixture. Body search costs grow with the decoded
  text, so this is the harder case for it.
- **Real mailbox:** the Apache Kafka dev list for January 2024 (7.5 MB, 770 messages).
- **Calendar:** 22,501 items (5.6 MB): 22,000 events in five zones (two of them Windows names), plus 500
  recurring series started in 2005 with EXDATEs.
- **Contacts:** 20,400 vCard 3.0 cards (4.4 MB), 400 of them duplicates by email and every phone shared by four
  different people (households).

## Mailbox A (300 MB, 22,830 messages)

| Operation | Cold | Cached | Peak memory |
|---|---|---|---|
| `mbox_tool.py index` (scan, parse headers and MIME structure, cache) | 2.9 s | 0.11 s | 92 MB |
| `list --range 20001-20200` | | 0.06 s | 36 MB |
| `search --from garcia --count` (regex over the index) | | 0.10 s | 38 MB |
| `search --subject budget --since 2020-01-01 --has-attachment` | | 0.08 s | 38 MB |
| `search --attachment "*.pdf"` | | 0.08 s | 39 MB |
| `search --body RE` (first call builds the text store) | 2.5 s | 0.29 s | 77 MB |
| `search --any RE` (headers, attachment names and body; a literal in every message) | | 0.70 s | 65 MB |
| `show last`, `mail_read.py --message 11000` (random access by offset) | | 0.08 s | 39 MB |
| `threads` (References / In-Reply-To / subject, 14,785 conversations) | | 0.27 s | 71 MB |
| `stats` | | 0.32 s | 62 MB |
| `export --messages 1-500 --as mbox` | | 0.21 s | 38 MB |
| `index --no-cache` (built in a temp folder, then deleted) | 2.8 s | | 84 MB |

## Mailbox B (260 MB, 33,552 text-heavy messages)

| Operation | Cold | Cached | Peak memory |
|---|---|---|---|
| `index` | 2.2 s | 0.10 s | 113 MB |
| `search --body 'invoice\s+#?88231'` (first call builds the text store) | 2.2 s | 0.37 s | 66 MB |
| `search --body 'budget review' --count` (6,235 hits) | | 0.44 s | 74 MB |
| `search --body '\bthe\b' --count` (every message matches) | | 0.61 s | 117 MB |
| `search --from billing --body ZEBRA` (header filter first) | | 0.09 s | 37 MB |
| `threads --sort bytes` | | 0.34 s | 77 MB |
| `stats` | | 0.37 s | 75 MB |
| `search --body … --no-cache` (index and text store built, then deleted) | 4.3 s | | 120 MB |

- The cached `index` is 22-27× faster than the cold one; the cached body search is 6-8× faster.
- Body search reads the text store in two threads (SQLite and zlib release the GIL) in 4 MB batches, and first
  looks for a literal every match must contain with a plain substring search, so the regular expression runs
  only on the messages that have it. Before these changes the cached search on mailbox B took 1.0 s (only 3×
  faster than cold): Python's regex with IGNORECASE over 84 MB of text was most of it.
- The separator scan streams the file in 8 MB blocks, and each worker maps only its own chunk of 2,000
  messages, so memory does not grow with the mailbox size. An earlier version mapped the whole file and peaked at
  322 MB on mailbox A.
- Cache entries: mailbox A's index is 9 MB (SQLite with byte offsets and parsed headers) and its text store 12 MB
  (zlib-compressed decoded bodies); mailbox B's are 13 MB and 34 MB.
- The cache key is the file's fingerprint: a full SHA-256 up to 8 MB, and for bigger files the size, head,
  middle and tail samples and the modification time. A plain `cp` of a big mailbox (new modification time) is
  therefore indexed again (2.3 s for mailbox B); `cp -p` keeps the time, and the copy answers from the cache in
  0.12 s.
- On the real Kafka archive: `index` takes 0.26 s cold and 0.06 s cached; a body search takes 0.31 s, then
  0.09 s.

## Calendar (22,501 items) and contacts (20,400 cards)

| Operation | Cold | Cached | Peak memory |
|---|---|---|---|
| `ics_tool.py read` (parse, then the map) | 3.2 s | 0.12 s | 158 MB |
| `agenda` for October 2026 (5,484 occurrences, recurrences expanded) | | 0.24 s | 92 MB |
| the same as JSON (ISO start and end with offsets) | | 0.27 s | 103 MB |
| `conflicts` for October (28,713 overlapping pairs, output budgeted) | | 0.51 s | 98 MB |
| `free` for one week | | 0.19 s | 92 MB |
| `render --view month --from 2026-10-01` (the whole month grid, 6,198 occurrences) | | 0.81 s | 127 MB |
| `render --view week` (Typst, one PNG, about 1,300 events) | | 0.51 s | 156 MB |
| `merge` of two copies of a 21,000-item calendar (42,000 items in, dedupe by UID, verified) | 12 s | | 346 MB |
| `vcf_tool.py dedupe` (400 groups, 5,000 shared phones checked against names) | 0.8 s | | 150 MB |
| `vcf_tool.py convert … --csv-style google` | 0.9 s | | 150 MB |
| `vcf_tool.py convert` that CSV back to vCard | 1.1 s | | 150 MB |

- The cached parse is 27× faster than the cold one.
- Recurrences are expanded only inside `--from`/`--to`, and a series that started long ago is not walked from its
  first occurrence: DTSTART is moved forward by whole intervals to just before the window (rules with COUNT
  excepted, since COUNT counts from the start). 500 series started in 2005 (daily, hourly, weekly, monthly,
  yearly) give one month of 2026 (16,959 occurrences) in 0.28 s; walking them from 2005 took 9.2 s. 11,000
  random rules were checked to expand identically both ways, and the acceptance review checked all 500 series
  of the fixture against dateutil across the DST change.
- `merge` and `edit` parse with icalendar, write, and parse the result again to prove it reads. The written file's
  parse goes into the cache, so reading the merged calendar afterwards costs 0.15 s. icalendar's parser is the
  whole cost: about 2.5 s per 20,000 events.
- vCards are not cached: `read` of the 20,400 cards (parse and map) takes 0.7 s.
- A hostile calendar (an endless `FREQ=SECONDLY` series, an event ending past the year 9999, a 3 MB summary)
  reads in 0.5 s; its one-week agenda stops the endless series at 20,000 occurrences with a warning and pages a
  1.2 MB Markdown output (summaries cut at 200 characters) instead of 28 MB.

## Single messages

- Reading a message parses it whole with the standard library, and attachments stay in memory while the script
  runs. A 30 MB message with three 7 MB attachments reads in 0.7 s (peak 254 MB) and `mail_extract.py` saves them
  in 0.8 s (280 MB). Memory is about 9 times the message size, which is fine for anything a mail server accepts
  (usually 25-50 MB).
- `--render` compiles Typst once per message: 0.2 s for a text message, 0.3-0.5 s for HTML with inline images.
- `--help` of every script answers in about 40 ms (heavy libraries are imported lazily).
