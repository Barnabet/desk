---
name: file-inspector
description: Identify any file by its content, not its name, and name the skill and first command that handle it. Use it first for unknown, misnamed or extensionless files (.bin .dat .tmp, downloads, attachments, "what is this?"), for whole folders (inventory, duplicates, routing), and for text and binary data no other skill owns. Detects 300+ formats (Office by their inner files, PDF, images, media, archives, fonts, PE/ELF/Mach-O executables, SQLite, certificates, private keys, encrypted data) and flags extension mismatches, secrets, zip bombs and truncated files. Text of any size and encoding (.txt .log .csv .sql .md, code) - detect and convert encodings, BOMs, line endings, invisible and bidi characters; head, tail, grep, slice, count, split, sample, outline and summarize multi-GB logs without loading them. Binary files - hex dump, strings, entropy, byte-map PNG, carve embedded files, compare, byte search. Hashes (md5, sha1, sha256, sha512, blake2b, crc32), checksum-file verification, manifests, duplicates.
license: MIT
---

# File inspector

The entry point for files nobody has named yet. Scripts in `scripts/` use puremagic (MIT), charset-normalizer (MIT)
and Pillow, plus the standard library. Desk sets up their Python environment, so run them with `python3`. Every
script has `--help`, prints Markdown by default and JSON with `--format json`, and never changes its input: outputs go
to a new path, and an existing file is only replaced with `--force`. Nothing here needs LibreOffice or the network.

Work in a loop: **look** (what is it, where is what), **act** (hand it to the right skill, or convert, cut, carve
or hash it here), then **check** the result.

## Look

### What is this file?

```bash
python3 scripts/file_identify.py download.bin                     # type, MIME, confidence, details, warnings, skill
python3 scripts/file_identify.py inbox/ "attachments/*"            # several files, folders, globs (expanded for you)
python3 scripts/file_identify.py project/ -r --problems            # mismatches, secrets, damage, zip bombs, encryption
python3 scripts/file_identify.py big-folder/ -r --format csv       # one row per file
```

- Detection reads the content: magic numbers, then the container's inside (a .docx is a ZIP holding
  `word/document.xml`; an .xls is an OLE2 file holding `Workbook`; JAR, APK, IPA, EPUB, ODF, wheels, XPI and more
  are told apart the same way), then text structure (JSON, CSV, XML, YAML, TOML, logs, Markdown, source code by
  language), then a long-tail signature table, then entropy.
- Each result names the **skill** that handles the type and the **exact first command**, e.g.
  word-documents with `docx_info.py report.docx`. Run it with that skill (it may not be installed: then say so and
  use what this skill can show).
- Details: pages, dimensions, duration, tracks, sheets, slides, tables of a SQLite file, certificate subject,
  issuer and expiry, text encoding and line count, executable architecture, and whether an installer carries an
  archive.
- Warnings to act on: **extension mismatch** (a PNG named .jpg, an HTML login page saved as .pdf), **private keys**
  (never print, upload or share them), **password-protected** or encrypted files (ask the user), **macros**,
  **truncated or damaged** files (no PNG IEND, no JPEG end marker, a docx without its central directory or its main
  part), **unsafe to open** (a zip or XML bomb, false or overlapping ZIP entries: other skills refuse such files, so
  do not extract or open it), **data appended after the end** (a ZIP hidden after a PDF's `%%EOF`: a polyglot),
  text **not in UTF-8**, **bidi control characters**.
- "Is this attachment safe to open?" is answered by `file_identify.py FILE`: its type by content, and any of the
  warnings above. Never open or run it to find out.
- Confidence is `high`, `medium` or `low`. A low guess for an unknown blob is honest: look closer with `bin_tool`.

### What is in this folder?

```bash
python3 scripts/file_survey.py ~/Downloads                         # the map: groups, types, folders, largest, dupes, problems, routing
python3 scripts/file_survey.py archive/ --folder 2023/invoices     # drill into one folder
python3 scripts/file_survey.py archive/ --group spreadsheet --list # the files of one group (CSV when long)
python3 scripts/file_survey.py archive/ --find "*report*" --list   # by name, with their types
python3 scripts/file_survey.py archive/ --problems --list          # zip bombs, damaged, mismatches, secrets, not UTF-8
python3 scripts/file_survey.py archive/ --skill none --list        # files no skill reads (keys, encrypted, unknown)
```

The survey ends with a **routing table**: for each skill, how many files it gets, the first command, and the
`--skill X --list` command that lists them. Problems cover unsafe archives, damaged or truncated files, data
appended after a file's end, mismatches, secrets, encryption, empty files and non-UTF-8 text. The survey reads 16 KB
per file; `file_identify.py DIR -r --problems` reads 64 KB and finds deeper damage. Version-control, dependency and
cache folders (.git, node_modules, .venv…) are skipped unless `--all`. `--by ext` classifies by extension only
(instant on any tree, but trusts the names).

### Text files of any size

```bash
python3 scripts/text_tool.py info notes.txt           # encoding (+ confidence), BOM, line endings, lines, invisibles
python3 scripts/text_tool.py map dump.sql             # outline with line numbers: tables, headings, definitions, chapters
python3 scripts/text_tool.py head app.log -n 50       # also: tail (instant on any size), slice --lines 2000-2100
python3 scripts/text_tool.py grep app.log -e "ERROR|FATAL" -C 2      # line numbers; -F fixed, -i, -w, -v, --count
python3 scripts/text_tool.py log app.log --chart app-log.png         # time span, levels, busiest periods, top messages
python3 scripts/text_tool.py invisible contract.txt   # zero-width, bidi (Trojan Source), control chars, mixed scripts
```

- `log` knows ISO timestamps, syslog (with `error:`/`fatal:` prefixes, as sshd writes them), Apache access and
  error logs, nginx, logcat, glog, HDFS, BGL/Thunderbird, `20171223-22:15:29` and `[10.30 16:49:06]` stamps, JSON
  lines and epoch times (the full list: `references/text.md`). Messages are grouped into templates (numbers, hex
  ids and quoted text masked), so "user 42 timed out" and "user 7 timed out" count as one. Top errors list the most
  severe level first, so three FATAL lines among millions are shown; `--level FATAL` lists one level. The `Levels:`
  line counts every line: grep the rare ones to see them all. `--chart` writes a timeline PNG with a marker on each
  period holding FATAL or CRITICAL lines: look at it with view_image.
- `encoding` checks many files at once and shows **words to check** (the accented words as decoded). If one reads
  wrong (`Cafť`, `Crčme`, `CafÃ©`), convert with `--from` and the other candidate that reads right (for Western
  European text, `--from cp1252`).
- `grep` matches bytes (fast). With `-i` or `-w` and a pattern holding accented letters it switches to decoded
  text by itself, so `-i -e "élan"` also finds `ÉLAN`; `--text` forces that mode.
- `map` finds headings, SQL tables, definitions and chapter lines in several languages (`Chapter`, `Chapitre`,
  `Capítulo`, `Kapitel`, `Глава`…); `--pattern '^KAPITOLA'` for any other marker.

### Binary files

```bash
python3 scripts/bin_tool.py map firmware.bin --out firmware-map.png   # entropy curve + byte classes + signatures (PNG)
python3 scripts/bin_tool.py carve firmware.bin                        # embedded files: offset, kind, exact size, what a ZIP holds
python3 scripts/bin_tool.py strings app.exe --interesting             # URLs, paths, registry keys, IPs, GUIDs, secrets
python3 scripts/bin_tool.py hex dump.bin --offset 0x1F400 --length 256
python3 scripts/bin_tool.py entropy blob.dat                          # regions: text, code, padding, compressed/encrypted
```

Look at the map PNG with view_image: flat high entropy is compressed or encrypted data, dips are headers, tables
or text, and orange lines mark embedded file signatures. Then `carve` or `hex` the interesting offsets.
`strings` reads ASCII and UTF-16LE by default (`--enc all` adds UTF-8 and UTF-16BE); `search` finds hex with
`??` wildcards (`--hex "4D 5A ?? ?? 03 00"`), text (`--text password --utf16`) or a bytes regex.

### Hashes

```bash
python3 scripts/file_hash.py installer.dmg --expect 9f86d081884c7d65…     # does it match the published hash?
python3 scripts/file_hash.py --check SHA256SUMS                           # GNU, BSD (--tag) and .sfv formats
python3 scripts/file_hash.py release/ -r --algo sha256,md5                # one read for every algorithm
python3 scripts/file_hash.py photos/ -r --dupes                           # duplicate sets and wasted space
```

## Big files and big folders

Never dump a big file. Work **map → find → drill down**, with addresses the next command reads directly:

```bash
python3 scripts/text_tool.py map huge.log             # 20 equal sections: where each starts (its timestamp), line and byte
python3 scripts/text_tool.py map dump.sql             # CREATE TABLE / INSERT INTO runs by table, with line ranges
python3 scripts/text_tool.py grep huge.log -e "OutOfMemory" --max 20      # matches with line numbers
python3 scripts/text_tool.py slice huge.log --lines 2170000-2170100       # read around one of them
python3 scripts/file_survey.py /data                  # a tree of 100k files: group rollups, then --folder / --group --list
```

- Line numbers and byte offsets are stable addresses: `slice --lines A-B`, `slice --bytes 0x1F400:8KB`,
  `bin_tool.py hex --offset`, `grep --from-line N` (paging) all take them.
- Output is capped by `--max-chars` (default 60,000). A cut output says what it left out and ends with the exact
  command for the next part; run it rather than raising the cap. Long listings switch to CSV.
- Expensive results are cached by the file's content: line indexes, log summaries, maps, text statistics, entropy
  profiles and hashes. A second call on a 300 MB log takes a fraction of a second. Folder surveys remember each
  file's type and partial hash by path, size and modification time, so a rerun on 100,000 files takes about a
  second. `--no-cache` skips the cache. Timings: `references/performance.md`.
- Everything streams: files of any size work in constant memory, and big scans are split across processes.

## Act

**Route.** Most files belong to another skill: run the command `file_identify` printed, with that skill. A file with
the wrong extension may confuse other tools: copy it to a new name with the suggested extension first (keep the
original). Neighbouring skills: `word-documents`, `pdf-toolkit`, `spreadsheets`, `presentations`, `images` (also
fonts and SVG), `audio-video` (also subtitles), `data-files` (CSV, JSON, XML, Parquet, SQLite), `archives`
(ZIP, 7z, RAR, TAR, JAR, APK, disk images), `markup-ebooks` (HTML, Markdown, EPUB, notebooks), `email-calendar`
(.eml, .msg, mbox, .ics, .vcf).

**Fix text.** Convert to UTF-8 and normalize, into a new file:

```bash
python3 scripts/text_tool.py convert legacy.csv legacy-utf8.csv --to utf-8 --eol lf
python3 scripts/text_tool.py convert data.csv for-excel.csv --bom add --eol crlf      # opens right in Excel
python3 scripts/text_tool.py convert pasted.txt clean.txt --strip-invisible --normalize-spaces --strip-trailing
python3 scripts/text_tool.py split huge.csv parts/ --size 100MB --header             # also --lines N, --parts K
```

`--strip-invisible` removes zero-width characters, bidi controls, stray BOMs and soft hyphens (`--keep-bidi` for
Arabic or Hebrew text). `--errors replace` accepts characters the target encoding lacks (the default fails).

**Carve.** `bin_tool.py carve FILE --out-dir carved/` extracts what `carve` listed (`--types png,zip`,
`--min-size 10KB`, `--from 0x5000000` to page). A ZIP's own member headers are never listed as more ZIPs;
`--nested` shows files stored uncompressed inside others (a thumbnail in a .pptx, images in a PDF), while compressed
members belong to the archives skill. Each extracted file is identified again, so the output says whether it is
valid.

**Record.** `file_hash.py DIR -r --manifest DIR.sha256` writes a checksum file with relative paths
(`--manifest-format bsd|json|csv`), and prints the command that verifies it later.

## Check

1. Identify what you produced: `file_identify.py out.csv` (right type, UTF-8, no warnings), or `text_tool.py info`
   for line endings, BOM and invisibles after a conversion (a leading BOM you added is reported as a signature,
   not as an issue: keep it).
2. For carved or split files: identify them, and `file_hash.py` or `bin_tool.py compare` them against expectations
   (split parts joined back are byte-identical).
3. Look at every PNG you made (`bin_tool.py map`, `text_tool.py log --chart`) with view_image before describing it.
4. Tell the user where the outputs are, what each file is and which skill to use next, and anything left unknown.

## Rules

- Never modify, rename or delete the user's files. Write new files and say where they are.
- Private keys, passwords and tokens: report that they exist and where, never print their values. `strings
  --interesting` and `grep` can reveal secrets: quote only what the user needs.
- Do not run what you inspect: executables, installers, scripts, macros, pickles and .reg files are read only.
- An extension is a claim, not a fact. Trust the content, and say when the two disagree.
- Say how sure you are: quote the confidence, and when a type is a guess, say what would confirm it.

## Limits

- Identification is by content signatures and structure: a few formats have none (raw headerless audio, some
  proprietary blobs) and come out as "unknown binary data" with their entropy. Encrypted files are recognized but
  never decrypted.
- Encoding detection on a few dozen bytes of legacy text is a guess (the output says so). Close code pages
  (ISO-8859-15 vs cp1252, cp1250 vs cp1252 on a few accents, cp850 vs cp852) can swap: read the words to check,
  and force `--from` when they look wrong.
- `map`, `log` and `split` work on bytes and need an ASCII-compatible encoding (UTF-8, Latin-1, cp125x…): convert
  UTF-16 and UTF-32 text to UTF-8 first. The other text commands read UTF-16/32 directly (more slowly).
- No OCR and no rendering of documents here: those belong to the file's own skill. This skill draws only its
  byte maps and log charts.
- `compare --align` (insertions and deletions) reads both files into memory, up to 256 MB each; the plain compare
  streams any size.

## Beyond the scripts

For anything the scripts do not cover, write a small Python script with the same `python3` and import this
skill's helpers (identification, streaming text, carving, hashing): see `references/recipes.md`.

References: `references/formats.md` (what is detected and where it is routed), `references/text.md` (encodings,
conversion, invisibles, grep, map and log formats), `references/binary.md` (strings, entropy, map legend,
carving, compare, search), `references/hashing.md` (algorithms, checksum formats, manifests, duplicates, caching),
`references/recipes.md` (custom scripts), `references/performance.md` (timings on big inputs).
