# Performance and big files

Measured on 2026-09-25 on an Apple M2 (8 cores, 8 GB of RAM) inside Desk's sandbox, with `DESK_MAX_WORKERS=2`
(two worker processes where a script works in parallel). "Cold" means an empty file cache; "cached" is the next
call on the same file. Every time includes starting Python and printing the result.

## How big files are handled

- **One parse, then Parquet.** The first script that opens a big CSV/TSV (32 MB), JSON or JSONL (16 MB), SQLite
  (16 MB), or XML, Avro, SPSS, Stata or SAS file (4 MB), sizes counted after decompression (a 5 MB .csv.gz that
  holds 60 MB of text is big), converts it once to a typed
  Parquet copy in Desk's file cache (`XDG_CACHE_HOME/desk-files`, keyed by the file's content, so an edit makes a
  new copy). Every later `data_info`, `data_query`, `data_profile`, `data_diff`, `data_chart` or `data_convert`
  call reads that copy: a few MB instead of hundreds. Parquet, Arrow and DuckDB files are read in place.
  `DESK_DATA_BIG_MB` sets all the thresholds at once; `--no-cache` (or `DESK_NO_CACHE=1`) reads the original.
- **Maps, not dumps.** `data_info` on a big table prints the schema, null shares and the first and last rows
  with their row numbers; `data_query` prints 50 rows in Markdown or 1 000 as CSV and ends with the exact command
  for the next page (`--offset`, `--rows A-B`), or `--out` writes everything.
- **Search with addresses.** `data_query --find` returns `_row` (the row or record number, as `--rows` takes it:
  not the file line) and `_match` (the columns that matched); `data_tree find` returns JSON paths such as
  `$.data.records[1999998].user.name`, paged with `--offset`.
- **Streaming.** JSON documents over 32 MB (`DESK_DATA_STREAM_MB`) are never loaded: `data_tree outline`, `get`,
  `find` and `format --minify` stream them with ijson (the C backend) in flat memory, and the outline is cached.
  XML is read with `lxml.iterparse`, clearing each record once it is used. JSONL is validated and its schema
  inferred in parallel byte ranges.
- **Cached results.** Profiles (`data_profile`) and outlines (`data_tree`, `data_info`) are cached per file
  content as well; asking again is instant.
- DuckDB is limited to 2 GB of memory (`DESK_DATA_MEMORY`) and spills to temp files beyond that, and never
  downloads extensions.

## CSV: 1,000,000 rows × 10 columns, 115 MB

| call | cold | cached |
|---|---|---|
| `data_info orders.csv` (builds the 7 MB Parquet copy) | 0.54 s | 0.13 s |
| `data_query --sql "SELECT region, count(*), sum(amount) … GROUP BY 1"` | 0.47 s (`--no-cache`) | 0.11 s |
| `data_query` filter + sort, top 20 | | 0.14 s |
| `data_query --rows 500001-500100` | | 0.12 s |
| `data_query --find user4242@` (every column) | | 0.53 s |
| `data_query --find user4242@ --in email` | | 0.22 s |
| `data_query --out all.parquet` (every row) | | 0.23 s |
| `data_profile` | 1.19 s (`--no-cache`) | 0.07 s |
| `data_convert orders.csv orders.parquet` | | 0.24 s |
| `data_convert … nice.jsonl.gz --where … --select …` | | 0.41 s |
| `data_diff orders.csv orders_v2.parquet --key id` (1M × 1M, 2,500 changes) | | 0.58 s |
| `data_diff` with the key guessed | | 0.59 s |
| `data_chart --kind line --x day --date-unit month --series region` | | 1.07 s |
| `data_chart --kind hist` | | 1.21 s |
| `data_validate --rules rules.json` (types, unique, regex, range, allowed) | | 0.52 s |
| `data_validate --infer --strict` (shape from a 50,000-row sample, ranges from all rows) | | 1.10 s |

The spec's target, a 1M-row CSV query under 2 s, holds cold (0.47 s) and cached (0.11 s).

Re-measured after the acceptance fixes (1,000,000 rows × 12 columns, 94 MB, with a `true`/`false` column read as
BOOLEAN and a `yes`/`no` column kept as text, which costs one extra read of the first 20,480 rows):

| call | cold | cached |
|---|---|---|
| `data_info orders.csv` | 0.56 s | 0.15 s |
| `data_query … GROUP BY` | 0.57 s (`--no-cache`) | 0.14 s |
| `data_query --find ACME` (every column) | | 0.47 s |
| `data_profile` | 1.26 s (`--no-cache`) | |
| `data_info orders.csv.gz` (15 MB gzip: ratio probe, then the Parquet copy) | 1.12 s | 0.15 s |
| `data_chart --kind line --x ts --date-unit month --series region` (partial last month shaded) | | 1.14 s |

## JSON: one document of 276 MB, 2,000,000 nested records under `$.data.records`

| call | cold | cached |
|---|---|---|
| `data_tree outline events.json` (streamed) | 8.5 s | 0.06 s |
| `data_tree get events.json 'data.records[1500000].user.name'` | 5.3 s | |
| `data_tree get … 'data.records[*].id' --limit 5 --offset 100000` | 0.23 s | |
| `data_tree find events.json user1999998` (a match near the end) | 15 to 19 s | |
| `data_tree find events.json nosuchvalue42` (absent: a raw byte scan says so) | 0.35 s | |
| `data_info events.json` (outline cached; records → 5 MB Parquet) | 9.2 s | 0.16 s |
| `data_query --sql "SELECT user.country, count(*), avg(amount) … GROUP BY 1"` | | 0.11 s |
| `data_query events.json --find user1999998` (row N = record N-1) | | 0.87 s |

For records, `data_query --find` on the cached copy is 20 times faster than a streamed `data_tree find`; use
`data_tree find` for keys and for values outside the record array. Streaming runs at about 30 s per GB with ijson,
in flat memory: 64 MB for the outline and 130 MB for `find` on this file. Building the Parquet copy peaks at
850 MB (DuckDB, capped at 2 GB).

A JSON array or JSON Lines file read by DuckDB is first checked for nesting depth (1,000 levels at most): 3 ms per
MB (0.12 s for 40 MB), paid once, on the call that builds the Parquet copy. A 38 MB top-level array of 500,000
records: `data_info` 2.0 s cold, 0.15 s cached.

## JSON Lines: 500,000 lines, 67 MB

| call | cold | cached |
|---|---|---|
| `data_info events.jsonl` (DuckDB reads it natively) | 0.35 s | 0.12 s |
| `data_query … GROUP BY` | | 0.10 s |
| `data_validate --infer --strict` (every line, 2 parallel ranges) | 3.0 s | |
| `data_validate --schema event.schema.json` (every line, 2 parallel ranges) | 10.4 s | |

Schema validation is jsonschema in Python, about 20 µs per line per worker; it scales with `DESK_MAX_WORKERS`
(up to one worker per core, 8 at most). Error line numbers are exact.

## XML: 128 MB, 600,000 `<order>` records in a namespace

| call | cold | cached |
|---|---|---|
| `data_tree outline orders.xml` (streamed, 42 MB of memory) | 8.8 s | 0.05 s |
| `data_info orders.xml` (outline cached; records → 1.4 MB Parquet) | 12.8 s | |
| `data_query --sql "SELECT status, count(*), sum(total) … GROUP BY 1"` | | 0.13 s |

## Avro, SPSS, Stata: 1,000,000 rows

| file | `data_info` cold (builds the Parquet copy) | `data_info` cached | query cached |
|---|---|---|---|
| orders.avro (30 MB, deflate) | 8.3 s | 0.15 s | 0.11 s |
| orders.sav (88 MB) | 3.1 s | 0.29 s | 0.28 s |
| orders.dta (77 MB) | 2.4 s | 0.28 s | 0.28 s |

## Compressed and hostile inputs

| input | what happens | time |
|---|---|---|
| 745 KB .csv.gz holding 300 MB of one repeated line (412×) | refused by the ratio probe (32 MB decoded) | 0.10 s (0.06 s once the probe is cached) |
| 44 KB .csv.bz2 holding 300 MB (6,500×) | refused by the probe (16 MB decoded) | 0.20 s |
| 45 KB .csv.xz, 28 KB .csv.zst, 300 MB each | refused (xz probe; zstd sizes from its frame headers, nothing decoded) | 0.24 s, 0.06 s |
| .xz declaring a 3 GiB dictionary, .zst declaring a 2 GiB window | refused from the headers | 0.06 s |
| the same .bz2 with `DESK_DATA_MAX_RATIO=100000` | read: 10.8 million rows, cached as Parquet | 3.3 s |
| 342-byte YAML "billion laughs" (387 million strings) | refused after counting the alias graph (9 lists) | 0.10 s |
| JSON nested 100,000 levels | refused before DuckDB or Python parse it | 0.07 s |

Before these limits, the bz2 file was decoded to a 1 GB temporary copy on every call and the YAML file ran until
it was killed at 1.7 GB of memory.

## Writing

`data_convert orders.csv orders.avro` (1M rows) takes 13 s (fastavro, record by record); `.sav` 9 s and `.dta`
7.5 s (through pandas). Parquet, CSV, JSON and DuckDB outputs are written by DuckDB itself (under a second for 1M
rows).

## Selftest

`scripts/selftest.py` runs every script (240 checks) in about 50 s, including scaled-down big-file checks: a
300,000-row CSV mapped through its Parquet copy, a cached profile at least 5 times faster than a cold one, a
streamed outline, `get` and `find` on a JSON document, and parallel JSONL validation that must match the serial
result exactly; and hostile inputs (alias bombs, deep JSON, gzip/bz2/xz/zstd bombs with the ratio floor lowered to
1 MB, huge xz dictionaries and zstd windows), each of which must be refused within seconds.

The fixtures above were generated in scratch space for these measurements and deleted afterwards; the spec's
300 MB JSON was scaled to 276 MB and the XML to 128 MB to respect the build machine's limits.
