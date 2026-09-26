# Binary files: bin_tool.py

Commands: `hex`, `strings`, `entropy`, `map`, `carve`, `compare`, `search`. All stream the file, so any size works.
Offsets and lengths take decimal (`4096`), hex (`0x1000`), sizes (`4KB`, `1.5MB`) or a negative offset counted
from the end (`-512`).

## hex

`hex FILE --offset 0x200 --length 256 [--width 16]` prints offset, hex pairs and ASCII. The output ends with the
command for the next range. `--format json` gives the bytes as hex.

## strings

```bash
python3 scripts/bin_tool.py strings app.exe -n 8                      # ASCII + UTF-16LE (Windows) strings, 8+ chars
python3 scripts/bin_tool.py strings app.exe --interesting             # only the telling ones, tagged
python3 scripts/bin_tool.py strings dump.bin --enc all --grep "(?i)pass(word)?" --unique
python3 scripts/bin_tool.py strings big.img --offset 1GB --length 64MB --max 100
```

- Encodings: `default` (ASCII + UTF-16LE), `ascii`, `utf8`, `utf16le`, `utf16be`, `all`.
- `--interesting` keeps URLs, e-mail addresses, IPv4 addresses (not version numbers such as `1.0.0.0` or
  `version="6.0.0.0"`), Windows and Unix paths, registry keys, GUIDs, key material (PEM headers, SSH keys, AWS access
  key ids, GitHub, Slack and OpenAI-style tokens), secrets in settings (`password=…`, `api_key: …`, `token=…`),
  version strings and file names. The header counts the kinds of the strings listed (of all of them with
  `--count-all`).
- `--max` (default 500) stops the scan early, and `--max-chars` cuts the listing at a string; either way the output
  ends with the `--offset` command that continues exactly there. `--count-all` keeps scanning for the total.
  `--max-len` truncates long strings.
- Secrets found this way are real: report that they exist and where (offset), not their values.

## entropy and map

`entropy FILE` profiles the file in 512 blocks (`--blocks N`). Blocks of big files are sampled (their first 64 KB),
so a multi-GB file takes a second; the profile is cached. Each block gets its Shannon entropy (0-8 bits per byte)
and its byte classes (zero, printable text, control, bytes ≥ 0x80, 0xFF). Adjacent blocks of one kind merge into
regions:

| region | rule | usually |
|---|---|---|
| padding (zeros) / padding (0xFF) | > 90% one byte | alignment, erased flash, sparse files |
| text | > 85% printable and entropy < 6 | strings tables, scripts, config, logs |
| sparse data | entropy < 4 | tables, headers, uncompressed structures |
| code or structured data | 4 to 7.5 | machine code, uncompressed images and databases |
| compressed or encrypted | > 7.5 | deflate/LZMA/JPEG data, ciphertext, random keys |

`map FILE --out map.png` (or `entropy FILE --chart map.png`) draws it: the entropy curve on top (the pink band is
7.5-8, compressed or encrypted), a strip of byte-class colours (black zeros, blue text, green control bytes, red
high bytes, white 0xFF; mixed blocks blend, and random bytes blend to the purple the legend calls "random mix"),
and orange lines labelled with the embedded signatures `carve` found, a ZIP by what it holds (`docx`, `xlsx`,
`epub`, `jar`…) (`--no-signatures` skips that scan). The x axis has round hex offsets with their size under them.
The PNG is 1500 px wide: look at it with view_image. A packed executable shows
a short code region and a long flat plateau; a firmware image shows compressed partitions between padding; an
encrypted container is flat from start to end.

## carve

```bash
python3 scripts/bin_tool.py carve firmware.bin                        # list: offset, kind, size, how the end was found
python3 scripts/bin_tool.py carve setup.exe --out-dir carved/         # extract what was listed
python3 scripts/bin_tool.py carve disk.img --types jpg,png,pdf --min-size 10KB --out-dir recovered/
python3 scripts/bin_tool.py carve bundle.bin --nested                 # also files inside files (images stored in a ZIP)
python3 scripts/bin_tool.py carve disk.img --from 0x5000000           # hits at or after an offset (paging)
```

Signatures searched: PNG, JPEG, GIF, BMP, WebP, PDF, ZIP (and every zip-based format), gzip, bzip2, xz, 7z, RAR,
CAB, TAR, PE (Windows executables), ELF, DEX, WAV, AVI, Ogg, FLAC, MP3 (ID3), MP4/MOV/HEIF (`ftyp`), SQLite,
OLE2 (doc/xls/ppt/msg/msi), WOFF, WOFF2, PEM blocks, DER certificates. `--types` takes those extensions and
aliases (jpeg, docx, dll, heic, …).

Every hit is validated (a 2-byte `MZ` or `BM` alone proves nothing) and its end is found from the format itself
when possible:

| end found by | formats |
|---|---|
| a length in the header | RIFF (WAV, AVI, WebP), BMP, CAB, WOFF, DEX, SQLite (page size × pages) |
| walking the structure | PNG (IEND), JPEG (EOI after the scan), GIF (trailer), ZIP (end of central directory), PE (section table), ELF (section and program headers), MP4 (boxes), TAR (headers), Ogg (last page), PEM (END line), DER (length) |
| decompressing to the stream's end | gzip, bzip2, xz, 7z (by its header's next-header offset) |
| unknown | RAR, FLAC, MP3, OLE2: carved up to the next signature (at most 64 MB), marked `?` |

A ZIP's own local file headers are its structure, not more files: they are skipped (read from its central directory,
or by walking the headers of a truncated ZIP), and a ZIP hit is named by its members (`Word document (docx)`,
`EPUB e-book (epub)`, `Java archive (jar)`…). `--nested` then shows real files inside files: members *stored*
uncompressed (a thumbnail JPEG in a .pptx, a PNG in a ZIP), images inside a PDF or an executable. Compressed
members have no visible signature: list and extract them with the archives skill instead.

`rejected_signatures` counts the signature-like byte runs that failed validation. The listing stops at `--max`
(default 200) hits or at `--max-chars`, and ends with the `--from` command for the next part. Extraction writes
`<name>-0x<offset>.<ext>` into `--out-dir`, never overwrites (`--force`), cuts at `--max-size` (default 1 GB),
identifies each file again and reports whether it is valid. Carving recovers files whose bytes are contiguous;
fragmented files (on a used disk) come out partly wrong, which the re-identification usually shows.

## compare

```bash
python3 scripts/bin_tool.py compare v1.bin v2.bin                     # byte-by-byte: differing ranges, first difference
python3 scripts/bin_tool.py compare v1.bin v2.bin --align             # insertions, deletions, changes (≤ 256 MB each)
```

The plain compare streams both files and merges differences closer than `--gap` bytes (default 8) into ranges, with
the first 16 bytes of each side. When most bytes differ after some point, data was probably inserted or removed:
the output says to use `--align`, which resynchronizes on 32-byte anchors and reports `insert`, `delete`, `change`
and `replace` operations with offsets in both files.

## search

```bash
python3 scripts/bin_tool.py search dump.bin --hex "4D 5A ?? ?? 03 00"          # ?? matches any byte
python3 scripts/bin_tool.py search dump.bin --text "Copyright" --utf16 -i
python3 scripts/bin_tool.py search dump.bin --regex "[A-Z]{4}\x00\x01" --count
```

Each match shows its offset and a hex dump with `--context` bytes around it. `--max` (default 50) stops early and
prints the `--offset` command that continues; `--count` scans everything for the total.
