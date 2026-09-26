# Formats in and out

Every script reads every format below. The format comes from the file's content first (magic bytes, then a look at
the text) and the extension second; `data_info` says when they disagree. Compressed inputs (`.gz`, `.zst`, `.bz2`,
`.xz`, or those bytes at the start of the file) are decompressed on the fly (see "Compressed inputs" below).

## Reading

| format | extensions | read with | becomes |
|---|---|---|---|
| CSV / TSV / PSV | .csv .tsv .tab .psv .txt .dat | DuckDB `read_csv` | one table |
| JSON | .json .geojson (JSONC too) | DuckDB `read_json`, ijson when big | the biggest array of objects (`--path`) |
| JSON Lines | .jsonl .ndjson .jsonlines | DuckDB `read_json` | one row per line |
| Parquet | .parquet .pq | DuckDB, in place | one table (a glob or folder of parts is one table) |
| Arrow IPC / Feather | .arrow .feather .ipc .arrows | pyarrow (file or stream, v1 and v2) | one table |
| Avro | .avro | fastavro (null, deflate, snappy, zstandard, bzip2, xz) | one table; records are STRUCTs |
| XML | .xml (any) | lxml iterparse (streamed) | the most repeated element (`--record`) |
| YAML / TOML / INI | .yaml .yml .toml .ini .cfg .conf | PyYAML (safe, YAML 1.2 booleans), tomllib, configparser | the biggest array of objects; INI: one row per key (section, key, value) |
| SQLite | .sqlite .sqlite3 .db .db3 | stdlib sqlite3, read-only and immutable | every table and view |
| DuckDB | .duckdb .ddb | attached read-only | every table and view, schemas kept (`sales.orders`) |
| SPSS | .sav .zsav .por | pyreadstat | one table; labels and missing codes in `data_info` |
| Stata | .dta | pyreadstat | one table |
| SAS | .sas7bdat .xpt | pyreadstat | one table |

### Text encodings

The encoding is detected from the bytes: a BOM first (UTF-8, UTF-16, UTF-32), then UTF-8, then charset-normalizer
for legacy code pages, named after the Windows code page when it decodes the same (cp1252 for Western Europe,
cp1250 Central Europe, cp1251 Cyrillic, cp1253 Greek, cp1254 Turkish, cp1255 Hebrew, cp1256 Arabic, cp1257
Baltic; also Big5, GB18030, Shift-JIS, EUC-KR, Mac encodings). A non-UTF-8 input is transcoded to a UTF-8 temporary
copy first (the original is never touched) and `data_info` names the encoding it used. When the guess is wrong
(short files with few accented letters are the hard case), pass `--encoding cp1252` or the right name.

### CSV dialects

DuckDB's sniffer reads the first 20 000 lines for the delimiter, quote and escape characters, header, column types
and date formats; if a value later breaks a type, the file is re-read with types sniffed from every row, then
leniently (short rows padded, extra fields kept), then as text. On top of it:

- **Decimal commas** (`1.234,56` with `;` separators) are recognised and read as numbers; `data_info` says so.
- **Header or not**: when every column is text, a first line shaped like the others (digits in the same places:
  dates, IDs) is data, so the columns are named `column0`, `column1`, …; `--header yes|no` decides.
- **Ragged rows or a quote left open**: the delimiter is taken from the lines themselves; rows that still cannot be
  read are skipped and listed by line number in the notes.
- **Columns aligned with runs of spaces** (`ls -l`-style reports, printed tables): read as whitespace-separated
  (`--delimiter whitespace` to force it).
- **Logs and text lists**: one column `line`, one row per line (`--delimiter lines` to force it); cut the fields
  in SQL with `regexp_extract` or `substr` (see `recipes.md`).
- **Leading zeros** (`007`, zip codes `01234`) keep a column as text. `--all-text` reads every column as text;
  `--null NA` (repeatable) adds null markers; `--skip N` skips title lines above the header.
- **Booleans**: a column becomes BOOLEAN only when it holds `true`/`false` in some letter case. `True`/`False`
  (pandas exports) is read as BOOLEAN and written back as `true`/`false` by text outputs, which the notes say;
  `--all-text` keeps the original text. `yes`/`no`, `y`/`n`, `t`/`f` and `1`/`0` stay text or numbers, so a
  CSV → CSV copy never rewrites them. Cast them on purpose with `--cast col=BOOLEAN` (it knows yes/no/y/n/oui/ja).
- **Empty strings**: `""` (quoted) is an empty string and an empty field is null, as the CSV writer writes them, so
  JSON → CSV → JSON keeps `""` apart from `null`.

### JSON and nested data

JSON with `//` or `/* */` comments and trailing commas (tsconfig.json, VS Code settings) is read as the JSON it
means; a byte order mark is skipped. In YAML only `true` and `false` are booleans (YAML 1.2): `on:` in a GitHub
Actions workflow stays the key `on`, and `yes`, `no`, `off` stay text. Several YAML documents in one file (`---`)
read as an array of documents.

Anchors and aliases (`&base`, `*base`, `<<: *base`) are resolved, but a document whose aliases expand to more
than 1,000,000 nodes plus one per character of the file (the "billion laughs" bomb: nine anchors of nine aliases
each are 387 million strings) is refused, as is an alias that contains itself; `DESK_YAML_MAX_NODES` raises the
limit for a document you trust. JSON nested more than 1,000 levels deep is refused before any reader sees it.

Nested objects are STRUCT columns and arrays are LIST columns in SQL: `SELECT user.email, tags[1],
len(tags) FROM t`, `UNNEST(tags)` for one row per element. A JSON whose shape changes from record to record is read
with the union of the keys (missing keys are null); values that do not fit one type become JSON text. Top-level
objects holding one array of records (`{"data": {"items": [...]}}`) use that array; `--path data.items` picks
another. GeoJSON reads its `features` with `properties` and `geometry` as STRUCTs.

### XML as a table

The most repeated element is a row; `--record book` or `--record catalog/book` chooses. Attributes become columns
named after the attribute (`category`), child elements with text become columns named after the child (`title`),
attributes of a child are `child.attr` (`title.lang`), a child with only attributes gives only those columns
(`<link href="…"/>` is `link.href`), deeper children are dotted paths, and a child that repeats is a JSON column (a list where it repeats: `["x","y"]`). Column types are sniffed from the text (numbers,
dates, booleans). Namespaces are dropped from column names. DTDs and external entities are never loaded (no XXE, no entity expansion).

### SPSS, Stata and SAS

Values are read as stored (codes such as 1 and 2) with `--labels` to show value labels instead. Numbers stored as
4-byte floats (Stata `float`, or float32 data saved as doubles) keep the decimals they were written with: 3.58,
not 3.5799999237060547. `data_info` lists each variable's label, format (`F8.2`, `EDATE10`, `%td`), measure,
value labels and missing codes (SPSS user-missing values and ranges such as `2000 to 3000, -1`; Stata/SAS
extended missing `.a`-`.z`). Dates and date-times stored as numbers with a date format are converted.
`.sas7bdat` files with a separate `.sas7bcat` catalog open without their value labels.

SPSS user-missing values read as null, as SPSS itself treats them, and a note lists the codes. `--user-missing`
keeps them as ordinary values (so `-1` stays `-1`). Converting `.sav` to `.sav`/`.zsav` keeps both the codes and
their missing ranges automatically. Stata and the other outputs have no user-missing ranges: the codes are null
there, or plain values with `--user-missing`, and the notes say which. DATE columns are written as dates (`%td`
in Stata, `DATE11` in SPSS) and TIMESTAMP columns as date-times (`%tc`, `DATETIME20`).

### Databases

A SQLite file is opened read-only with `immutable=1`, so nothing is written next to it (no journal, no WAL
checkpoint); tables are copied into DuckDB when a query names them (a big file is cached as Parquet table by
table). Column types follow the declared ones: `INTEGER` → BIGINT, `REAL`/`FLOAT` → DOUBLE, `DECIMAL(p,s)` or
`NUMERIC(p,s)` → DECIMAL(p,s) and `DATE` → DATE and `DATETIME`/`TIMESTAMP` → TIMESTAMP when every value fits
(checked in SQLite first; ISO text such as `2021-01-01 00:00:00`), else DOUBLE or text as stored. `data_info` lists tables, views, indexes, triggers, foreign keys and row counts. A DuckDB file is attached
read-only, with schemas: `sales.orders` works as written.

## Writing

`data_convert` and `data_query --out` write any of these; the format follows the extension (`--to` when unusual).

| format | extensions | notes |
|---|---|---|
| CSV / TSV / PSV | .csv .tsv .psv (.gz .zst) | nested values flattened to dotted columns, lists as JSON text; `--out-delimiter`, `--bom`, `--out-encoding`, `--decimal-comma`, `--quote-all`, `--null-text`, `--date-format`, `--no-header` |
| JSON / JSON Lines | .json .jsonl .ndjson (.gz .zst) | nested values kept; `--nest` turns dotted columns back into objects |
| Parquet | .parquet | zstd by default (`--codec` snappy, gzip, lz4, brotli or none), `--row-group-size` |
| Arrow IPC | .arrow .feather (.arrows for a stream) | `--codec` lz4, zstd or none |
| Avro | .avro | deflate by default (`--codec` snappy, zstd, bzip2, xz or none); every field nullable |
| SQLite | .sqlite .db | one table (`--table-name`), declared types from the data; several inputs pack into one file |
| DuckDB | .duckdb | one table, or every input/table with its schema; several inputs name a database's tables `<file>_<table>` |
| XML | .xml | `<rows><row><col>…</col></row></rows>` (`--xml-root`, `--xml-row`); invalid names made valid |
| YAML / TOML / INI | .yaml .toml .ini | records as a list (TOML: an array of tables under the output stem, nulls left out) |
| Markdown | .md | a pipe table |
| SPSS / Stata / SAS | .sav .zsav .dta .xpt | names shortened to the format's limit (64, 32, 8) and listed; variable and value labels carried over from an SPSS/Stata input |

Table outputs are written to a temporary name next to the output and renamed at the end, so an interrupted run
never leaves half a file under the real name. Spreadsheet outputs (.xlsx) are the `spreadsheets` skill's job.

### Documents

JSON, YAML, TOML, XML and INI convert structure for structure (`data_convert a.yaml a.json`, or
`data_tree convert`). XML read as a document: attributes are `@name` keys, text beside child elements is `#text`,
repeated elements become arrays, and writing XML reverses the mapping (`--root`, `--item` for arrays). TOML has no
null and needs a table at the top: `--drop-nulls` leaves nulls out, a top-level array goes under `--root`.
`data_tree format` pretty-prints (`--indent`, `--sort-keys`) or minifies, streaming a big JSON when minifying.

## Compressed inputs

gzip, zstd, bz2 and xz are recognised by their first bytes. CSV and JSON in gzip or zstd are read by DuckDB as they
are; bz2, xz and non-UTF-8 text are decoded to a temporary copy (deleted at exit), and inputs that are big after
decompression get the cached Parquet copy, so the decoding happens once. Before any decoder runs:

- **Dictionary and window**: the dictionary an xz stream declares and the window of every zstd frame are read from
  their headers (no decoding) and refused above `DESK_ARC_MAX_DICT_MB` (default 256), since the decoder would
  allocate that much at once; xz is also decoded with that memory limit.
- **Bombs**: the first 16-32 MB of output are decoded to measure the ratio (zstd sizes come from its frame
  headers). An input is refused when it would inflate past `DESK_DATA_MAX_INFLATE_MB` (default 8192) or more than
  `DESK_DATA_MAX_RATIO` (default 200) times its size once past 256 MB. Real CSV and JSON compress 3-30 times; a
  150 KB bz2 that holds 1 GB of one repeated line is refused in a fraction of a second.
- **While reading**: every stream decoded in Python counts its bytes and stops at the same limit, so a file whose
  tail differs from its head still cannot fill the disk. DuckDB's own gzip/zstd reader relies on the check above.
- A temporary copy also needs free space: the scripts refuse when the decoded copy would leave less than 512 MB.

Zip containers (.zip, .xlsx, .docx) are never opened here: the file is refused with the skill to use.

## Naming

Each input is a table named after its file name without extensions (compression included), with characters other
than letters, digits and `_` replaced: `sales-2024.csv.gz` → `sales_2024`, `v1.2.parquet` → `v1_2`, `2024.csv` →
`t_2024`. A single input is also `t`. `--as name=path` chooses the name. Quote column names with spaces, capitals
or reserved words in SQL: `"Unit price"`, `"When"`.
