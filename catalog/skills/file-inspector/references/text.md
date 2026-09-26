# Text files: encodings, cleanup, search and big files

`text_tool.py` works on text of any size. Commands: `info`, `encoding`, `convert`, `invisible`, `head`, `tail`,
`slice`, `count`, `grep`, `split`, `sample`, `log`, `map`. Each has `--help` with examples. Common options: `--encoding`
(when detection is wrong), `--max-chars` (output budget), `--no-cache`, `--workers`, `--format md|json`.

## Encodings

Detection order: a BOM (UTF-8, UTF-16 LE/BE, UTF-32 LE/BE) → the NUL-byte pattern of BOM-less UTF-16 → pure ASCII →
strict UTF-8 → charset-normalizer on the first 64 KB. Its candidates, plus the common Windows code pages
(cp1250-cp1257, KOI8-R) that decode the file, are ranked by how plausible their decoding is: letters of one
coherent alphabet (Western, Central European, Baltic or Turkish letters, Cyrillic, Greek, Arabic, Hebrew, CJK), no
capital inside a word ("CafŽ"), no Cyrillic or Greek letter glued to Latin letters ("reЗu"), no lone CJK character
between ASCII letters (a Western accent read as Shift-JIS), Greek words with their accents. Then the code pages real
files use win (cp1252 before cp1257, cp1251 before KOI8, never a Mac or EBCDIC page on a tie). When
charset-normalizer finds nothing, the most plausible of those code pages is given with a low confidence.

The result has a confidence, **words to check** (up to six words holding non-ASCII letters, as decoded) and **other
candidates**, common code pages first and each with a lower confidence than the pick. Only candidates that decode
the file differently are listed.

```bash
python3 scripts/text_tool.py encoding legacy/ -r                # many files; --sample 1048576 reads more of each
python3 scripts/text_tool.py info legacy.csv --format json      # encoding, confidence, method, alternatives, language
```

- `method`: `BOM`, `NUL pattern`, `ascii check`, `utf-8 check` (certain) or `charset-normalizer` (a judgement).
- `info` also checks the whole file for UTF-8: "mostly UTF-8, but invalid bytes at offset N" means a mixed file
  (often two files concatenated, or a UTF-8 file edited by a legacy tool). `slice --bytes N:64` shows the spot.
- Short texts (a few words) and close code pages are ambiguous: ISO-8859-15 and cp1252 differ in 8 characters
  (€ Š š Ž ž Œ œ Ÿ); cp1250 turns cp1252's "è" into "č" ("Crème" → "Crčme"); cp850/cp852/cp437 are DOS-era. Look at
  the words to check: when one reads wrong (`Cafť`, `Crčme`, `CafÃ©`), convert again with `--from` and the
  candidate that reads right (for Western European text, usually `--from cp1252`). Measured on generated
  two-to-twenty-row French CSVs in cp1252: 1,199 of 1,200 detected right, and cp1252 listed first in the other
  candidates of the last one.
- `info` treats a leading BOM as what it is, a signature (Excel needs it on UTF-8 CSV), not an invisible character;
  `invisible` ignores it too, and `convert --strip-invisible` keeps it. Only `--bom remove` drops it.

## convert

```bash
python3 scripts/text_tool.py convert in.txt out.txt                          # to UTF-8 (the default)
python3 scripts/text_tool.py convert in.txt out.txt --from cp1251 --to utf-8 # force the source encoding
python3 scripts/text_tool.py convert data.csv excel.csv --bom add --eol crlf # what Excel on Windows expects
python3 scripts/text_tool.py convert src.py clean.py --expand-tabs 4 --strip-trailing --final-newline add
python3 scripts/text_tool.py convert page.txt ascii.txt --to ascii --errors xmlcharref
```

| option | effect |
|---|---|
| `--from ENC` / `--to ENC` | source (default: detected) and target (default utf-8) encodings; any Python codec name |
| `--bom keep\|add\|remove` | byte order mark in the output (default keep: kept when the input has one and the target is UTF-8/16/32) |
| `--eol keep\|lf\|crlf\|cr` | line endings (mixed endings are unified) |
| `--expand-tabs N` / `--tabs N` | tabs to spaces with stops every N, or N leading spaces to a tab |
| `--strip-trailing` | spaces and tabs at line ends |
| `--final-newline keep\|add\|remove` | the newline at the end of the file |
| `--strip-invisible` (`--keep-bidi`) | zero-width characters, bidi controls, stray BOMs, soft hyphens (bidi kept for RTL text) |
| `--normalize-spaces` | non-breaking, thin, ideographic and other odd spaces to plain spaces |
| `--strip-control` | control characters other than tab and newlines |
| `--normalize nfc\|nfd\|nfkc\|nfkd` | Unicode normalization (NFC is what most tools expect) |
| `--errors strict\|replace\|ignore\|xmlcharref` | characters the target cannot hold (default: fail and say where) |

The output is a new file (`--force` to replace an existing output, never the input). The summary gives the source
and target encodings, line endings and what was changed.

## invisible

Lists, with line:column, character code and name: bidi controls (U+202A-202E, U+2066-2069, LRM/RLM/ALM: the
"Trojan Source" trick that makes code or contracts read differently from how they run), zero-width characters,
BOMs inside the text, control characters, odd spaces, soft hyphens, replacement characters (a lossy conversion
happened earlier), private-use and tag characters, and **mixed-script words** (a Latin word with a Cyrillic or
Greek homoglyph, as in phishing domains). Fix with `convert --strip-invisible --normalize-spaces --strip-control`.
A long list stops at `--max` findings or at `--max-chars`, and ends with the command for the next part
(`--from-line N`, the line of the first finding left out).

## head, tail, slice, sample, count

- `head -n N` and `tail -n N` (tail seeks from the end, so it is instant on any size). `--plain` drops line numbers.
- `slice --lines A-B` (also `A-`, `-B`, `N`, `last-50`) and `slice --bytes OFFSET:LENGTH` (`0x1F400:8KB`, `-4KB:4KB`).
  On big files a cached line index (a mark every 8 MB) makes any line one seek away. `--limit` caps the lines.
- `sample -n N`: random lines; on big files by seeking (`--method seek`, fast, byte offsets), otherwise an exact
  reservoir sample with line numbers; `--seed` for repeatability, `--skip-header` for CSVs.
- `count FILES…` (`-r` for folders): lines and bytes, `--words`, `--chars`. Cached and parallel on big files.

## grep

```bash
python3 scripts/text_tool.py grep app.log -e "ERROR|FATAL" -C 2              # regex, context lines
python3 scripts/text_tool.py grep app.log -F -e "user=42" -e "user=43"      # fixed strings, OR
python3 scripts/text_tool.py grep src/ -r -w -i -e "todo" --max 50          # folders, whole words, any case
python3 scripts/text_tool.py grep huge.log -e Exception --count             # count only (scans everything)
python3 scripts/text_tool.py grep huge.log -e Exception --from-line 1200001 # page on
```

Patterns are Python regular expressions matched on bytes, which runs at disk speed and is split across processes
on big files. On bytes, `-i` folds ASCII letters only and `-w` does not see accented letters as word characters, so
a pattern with non-ASCII letters and `-i` or `-w` switches to `--text` by itself (a note on stderr says so): then
`-i -e misérables` also matches `MISÉRABLES`. `--text` matches decoded text (Unicode-aware `\w`, full case
folding); UTF-16 files always use it. `-v` inverts, `-A`/`-B`/`-C` add context, `--max` caps the matches,
`--max-line` cuts long lines around the match.

A cut output (by `--max` or by `--max-chars`) ends with the exact command for the next part: the same grep with
`--from-line` set to the first match left out; with several files, a second command covers the files after it.
The same holds for `head`, `slice` (a `slice --lines` command), `tail` (a smaller `tail -n`), `map`, `invisible`
and `bin_tool.py strings`, `search` and `carve`. Run it rather than raising `--max-chars`.

## split

`split FILE OUT_DIR` with exactly one of `--lines N`, `--size 100MB` or `--parts K`. Parts end at line boundaries
(LF; a stray CR stays inside its line), `--parts K` writes exactly K parts of about equal size, `--header` repeats
the first line (CSV header) in every part, `--prefix` names them. Joined back, the parts are byte-identical to the
input. UTF-16/32 files are refused: convert them to UTF-8 first.

## log

Formats recognized from the first lines (a sample line of each):

| format | looks like |
|---|---|
| Apache/nginx access log | `10.0.0.1 - - [01/May/2024:10:00:00 +0000] "GET /x HTTP/1.1" 200 …` (5xx is an error, 4xx a warning) |
| Apache error log | `[Sun Dec 04 04:47:44 2005] [error] …` |
| syslog | `Jun 14 15:16:01 host prog[pid]: …` (no year: a jump back of over 30 days starts the next year, shown `(+1y)`) |
| ISO timestamps | `2024-03-01 10:00:00,123 ERROR …` (log4j, Python logging, most apps) |
| slash dates, US dates | `2024/03/01 10:00:00` (nginx error log), `3/1/2024 10:00:00 AM` |
| glog | `I0101 12:00:00.123 …` |
| Android logcat | `03-17 16:13:38.811  1702  2395 D Tag: …` |
| compact dates | `081109 203615 …` (Hadoop HDFS) |
| compact dash dates | `20171223-22:15:29:606|…` (HealthApp and similar) |
| bracketed month.day | `[10.30 16:49:06] chrome.exe - …` (Proxifier) |
| BGL/Thunderbird | `- 1117838570 2005.06.03 R02-M1-N0 …` (epoch seconds, then a dotted date) |
| epoch seconds | `1700000000.123 …` (and epoch milliseconds) |
| time of day | `10:00:00 …` (no date) |
| JSON lines | `{"ts": …, "level": "error", "msg": …}` (`level`/`severity`/`levelname` and `msg`/`message`/`event` keys) |
| level first | `ERROR something failed` |

Levels are found as words (`ERROR`, `WARN`, `FATAL`, `CRITICAL`…), bracketed (`[error]`, `[core:warn]`,
`[alert]`), logfmt (`level=info`), logcat letters, or a lower-case prefix after the program name
(`sshd[24200]: error: Received disconnect`, `fatal: …`). The bare words ALERT and EMERG are message text, not levels.
Other text is summarized as "plain lines" (templates still work, times do not).

The time span is the earliest and latest timestamp; when the lines are not in time order (merged logs), the
summary says so and gives where the file starts and ends as well.

The summary: time span, level counts, busiest periods (the bucket size gives about 48 buckets; ordered by lines,
with a second table of the periods with the most errors when those differ), top error templates (the most severe
level first: a FATAL seen three times comes before an ERROR seen 3,000 times), top warnings and the most frequent
messages, each with the line of its first occurrence. Templates mask numbers, hex ids (`d3223499`, `deadbeef`,
`0x1F`), UUIDs, quoted strings and the timestamp prefix, and are counted exactly for errors and warnings; on logs of
millions of lines other messages are sampled (their counts are marked `~`). The cached summary keeps the top 300
templates of every level, so a rare level is never crowded out; `--level FATAL --top 30` lists one level. To be
sure nothing is missed, grep the level names the `Levels:` line shows (`grep FILE -e FATAL`).

`--chart out.png` draws lines, warnings and errors per period, with a dark marker above each period holding FATAL or
CRITICAL lines, on a round axis (look at it with view_image). The summary is cached, so asking again (or for a
chart) is instant.

## map

The map of a big text file, with addresses for `slice`:

| kind | chosen for | markers |
|---|---|---|
| `markdown` | .md and Markdown-looking text | `#` headings (not `#` lines inside fenced code) |
| `org`, `asciidoc`, `latex` | .org, .adoc, .tex | headings; `\part`, `\chapter`, `\section`… |
| `sql` | .sql, or CREATE TABLE / INSERT INTO / COPY in the first 64 KB | CREATE, INSERT, COPY, ALTER, DROP, UPDATE, DELETE, USE; consecutive INSERTs into one table become one run |
| `code` | source code extensions | top-level `def`/`class`, `function`, `func`, `fn`/`struct`/`impl`, `interface`/`type`/`enum`, public methods, `module`/`package`/`namespace` |
| `chapters` | plain text (.txt or no extension) with chapter lines | Chapter, Part, Book, Section, Act, Letter, Volume and their French (Chapitre, Livre, Tome), Spanish, Italian, Portuguese, German, Dutch, Polish and Russian forms, followed by a number, a Roman numeral, a capitalised word or an ordinal ("Chapitre premier"); UTF-8 or the legacy code page |
| `sections` | anything else (logs, CSV, JSON lines) | N equal line ranges (`--sections`, default 20) with each one's first line: for a log, its timestamp |

`--pattern REGEX` (with `-i`) makes the outline any lines you choose (`--pattern '^class '`,
`--pattern 'BEGIN TRANSACTION'`, `--pattern '^KAPITOLA'` for a language not listed). Each item has its line or line
range, byte offset and label; `--max` and `--from-line` page through long outlines, and an outline cut by
`--max-chars` ends with the `--from-line` command for the rest. Maps of files over 8 MB are cached.
