# Performance

Measured on 2026-09-25 on this Mac (Apple M-series, 8 GB RAM), in Desk's sandbox with the skill's runtime and
`DESK_MAX_WORKERS=2` (two helper processes; on a machine with more cores, the cold times of the parallel paths
drop roughly in proportion). "Cold" is the first call on a file with an empty cache; "cached" is the next call.
Per the build's resource rules, the 2 GB log of the plan was scaled down to 290 MB.

## A 290 MB log (4,871,183 lines, ISO timestamps, five levels, hex request ids, 3 FATAL lines)

| command | cold | cached |
|---|---|---|
| `file_identify.py` | 0.10 s | |
| `text_tool.py info` (encoding, line endings, stats, line index) | 2.0 s | 0.07 s |
| `text_tool.py count` | (index built by info) | 0.08 s |
| `text_tool.py map` (20 equal sections with their first lines) | 0.24 s | 0.24 s |
| `text_tool.py slice --lines 2170000-2170100` | 0.09 s | 0.09 s |
| `text_tool.py head` / `tail -n 50` / `sample -n 20` | 0.08 s | |
| `text_tool.py grep -F -e FATAL --count` | 0.33 s | not cached (a search) |
| `text_tool.py grep -e ERROR --max-chars 3000` (ends with the `--from-line` command) | 0.43 s | |
| `text_tool.py grep -e 'FATAL\|OutOfMemory' -C 2 --max 5` (matches spread to line 3,040,870) | 1.55 s | |
| `text_tool.py log` (levels, time buckets, templates; the 3 FATAL lines listed first) | 6.6-6.9 s | 0.10-0.18 s (also with `--chart` or `--level FATAL`) |
| `text_tool.py split --parts 4` (writes 290 MB, exactly 4 parts) | 3.4 s | |
| `bin_tool.py entropy` | 0.12 s | 0.05 s |
| `bin_tool.py map` (profile + signature scan + PNG) | 2.8 s | 0.18 s |
| `bin_tool.py carve` (after the map: same cached scan) | | 0.07 s |
| `bin_tool.py strings --offset 150MB --max 5` | 0.30 s | |
| `file_hash.py --algo sha256,md5` | 0.66 s | 0.07 s |

Earlier in the build the cold `info` took 5.3 s (line-anchored regexes tried at every byte) and `log` 13.0 s
(per-line date arithmetic and templating every other line); `bin_tool.py map` did not reuse the carve scan.
Keeping the top templates of every level (so rare FATAL lines survive) and masking every hex id cost nothing
measurable.

## A 120 MB SQL dump (mysqldump style, 4 tables, 69,530 multi-row INSERTs)

| command | cold | cached |
|---|---|---|
| `text_tool.py map shop.sql` (16 items: DROP, CREATE, one INSERT run and ALTER per table) | 0.6 s | 0.05-0.07 s |

## A tree of 99,513 files in 796 folders (231 MB)

Four levels of folders (year/area/kind/month); 25% small PNGs (5% truncated), 15% cp1252 CSVs, 5% copies of a real
.docx, 7% HTML pages named .pdf, .env files with secrets, 20% random 2 KB blobs, text notes and logs, and 7% copies
of one blob.

| command | cold | cached |
|---|---|---|
| `file_survey.py tree` (content identification, zip-bomb checks, duplicates, the full map) | 29.8 s (walk 1.2, identify 27.2, duplicates 1.1) | 1.7 s |
| `file_survey.py tree --folder 2023/finance` | | 0.28 s |
| `file_survey.py tree --problems --list` (24,106 rows, CSV pages of 2,000) | | 0.87 s |
| `file_hash.py tree -r --manifest SUMS` | 11.0 s | 2.45 s |
| `file_hash.py tree -r --format sums` | | 1.7 s |
| `file_hash.py --check SUMS --base tree` (always re-reads every byte) | 10.1 s | 10.1 s |
| `file_hash.py tree -r --dupes` | | 1.25 s |
| `file_identify.py tree -r --problems --fast` (no remembered identifications) | 27.0 s | |

- Identification runs at about 1,850 files per second per process on this mix (16 KB read and sniffed per file):
  a PNG costs 0.2 ms, plain text 0.55 ms, a random blob 0.5 ms, a small .docx 0.8 ms (the zip-bomb check included),
  a legacy-encoded CSV 1.2 ms (charset-normalizer). The first run of this build took 41.1 s cold on this tree: the
  encoding plausibility check now visits only non-ASCII characters, and prose skips the code-language statistics.
- Small ZIP-based files get the zip-bomb verdict directly (0.25 ms); files over 8 MB go through `check_zip`'s
  content-keyed cache, so a big package is checked once.
- The duplicate search reuses the identification pass: a file of at most 16 KB is already in memory, so its
  partial hash is stored with its type.
- Cached results are keyed by absolute path, size and modification time in one SQLite index. The manifest's
  "never overwrite an input" check compares file identities instead of resolving 100,000 paths: the cached
  manifest went from 4.5 s (on a 60,000-file tree) to 2.45 s on this one.

## Smaller measurements

- `bin_tool.py compare --align` on two 40 MB files with an insertion, a deletion and a changed byte: 0.07 s
  (galloping comparisons); the plain streaming compare: 0.34 s.
- `--help` of every script: 50-70 ms (heavy imports are lazy).
- Encoding detection on 1,200 generated French cp1252 CSVs of 15-25 rows: 1.3 s in all, every one right; on 600
  of 2-4 rows, 599 right.
- The selftest (about 330 checks, including scaled-down big-file, cache and regression checks): about 12 s.

## Reproducing

Fixtures were generated in scratch space (deleted afterwards): a log writer producing timestamped lines with five
levels, hex ids and three FATAL lines, a mysqldump-style writer, and a tree writer spreading generated PNGs, CSVs,
copies of a public sample .docx, HTML pages, .env files and random blobs over 800 folders. Run each command twice
with `DESK_FILE_CACHE` set to an empty folder to see cold and cached times; `--no-cache` forces the cold path.
