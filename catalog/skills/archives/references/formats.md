# Formats, engines and methods

What each format can do here, and which engine does it. Detection uses the file's magic bytes, not its
extension. A `.zip` that is really a tar is read as a tar. Self-extracting `.exe` files are found by their embedded
zip, 7z or RAR signature.

| Format | List | Read, test, extract | Create | Encryption | Engine |
|---|---|---|---|---|---|
| zip, and zip-based: .jar .war .ear .apk .aab .whl .egg .nupkg .vsix .xpi .ipa .cbz .epub .docx .xlsx .pptx .odt … | yes | yes | yes | read ZipCrypto and WinZip AES; write AES-256 | zipfile, pyzipper |
| .tar | yes | yes | yes | none (tar has none) | tarfile |
| .tar.gz .tgz, .tar.bz2 .tbz2, .tar.xz .txz, .tar.zst .tzst, .tar.lzma | yes | yes | yes (not .lzma) | none | tarfile + gzip/bz2/lzma/zstandard |
| .gz .bz2 .xz .zst .lzma (one compressed file) | yes | yes | yes (not .lzma) | none | stdlib, zstandard |
| .7z | yes | yes | yes, LZMA2 | read and write AES-256, including encrypted file names | py7zr, bsdtar fallback |
| .rar (RAR 1.5-5) | yes | bsdtar, unrar, unar or 7-Zip | no (proprietary) | with unrar or 7-Zip | rarfile + external tool |
| .iso .cab .cpio .xar .ar/.deb .rpm .lha/.lzh .warc, .Z, compressed cpio and warc | yes | yes | no | no | bsdtar (libarchive) |

Files split into byte slices (`name.001`, `name.002` …, as 7-Zip's "split to volumes" and HJSplit write them) are
refused with the one-line Python command that joins them; open the joined file. Spanned zips (`.z01` … `.zip`,
from `zip -s` or WinZip) count offsets per part, so they cannot be concatenated: they are refused with the Info-ZIP
command that rejoins them (`zip -s 0 name.zip --out joined.zip`).

## zip details

- **Methods read:** store, deflate, deflate64 (Windows Explorer uses it for big files), bzip2, lzma, zstd (93), xz
  (95), PPMd (98). Shrink, implode and others are reported as unsupported.
- **Encryption:** ZipCrypto (weak; flagged) and WinZip AES-128/192/256 with deflate, bzip2 or lzma inside. PKWARE
  "strong encryption" and AES around xz/zstd/PPMd are not supported.
- **Written zips:**
  - Zip64 is used automatically when needed.
  - Each member carries a Unix-time extra field, so dates are timezone-exact.
  - Unix permissions and symlinks are stored as Info-ZIP does.
  - Already-compressed files are stored, not deflated: known extensions, or a sample that does not shrink.
- **Names:**
  - UTF-8 when flagged, or when the bytes are valid UTF-8.
  - The Info-ZIP Unicode path field when present.
  - Otherwise CP437, the zip default. Pass `--encoding cp932` (Japanese), `cp866` (Russian) or `gbk` (Chinese) for
    old Windows zips with garbled names.
- **Structure checks:** every local header is compared with the central directory. Overlapping entries (the
  "better zip bomb"), name mismatches, missing local headers and data before the first member (self-extractor
  stubs, polyglots) are reported.
- **Damaged zips:** when bytes are missing before the central directory (a cut download), zipfile would shift every
  offset by the missing amount. The scripts compare both readings against the local headers and keep the one that
  finds them, so the members before the damage stay readable; the listing says how much is missing and `arc_test`
  names the lost members. If zipfile cannot open a zip at all (no central directory: a truncated download or a
  streamed zip), bsdtar reads it from its local headers when available (links and Unix modes, which live in the
  central directory, are then unknown), and a listing that breaks off keeps the members read so far.
- **Lying sizes:** `arc_test` reads zip members with the skill's own decoder and fails a member whose stream goes on
  after its declared size, or leaves packed bytes unread; reading and extraction stop at the declared size.

## tar details

- ustar, GNU (long names, sparse files) and pax (UTF-8 names, big sizes, sub-second times) are read. Written tars
  use pax.
- Names that are not UTF-8 are read as Latin-1 (`--encoding` picks another).
- Compressed tars are streamed, never loaded whole. A truncated or corrupt stream still lists and extracts every
  member before the damage, and says where it stops.
- A tar whose first header tarfile rejects (odd pax or GNU extensions) is read through bsdtar, after the same
  dictionary check of its outer stream.
- `.tar.gz` output is one standard gzip stream, compressed on several cores (up to 8, capped by `DESK_MAX_WORKERS`)
  in 1 MB blocks (each primed with the previous 32 KB, as pigz does). `.tar.zst` uses zstd's own threads. `.tar.xz` and `.tar.bz2` are single-threaded.
- Reproducible tars (`--reproducible`): members sorted, mtime fixed, owner 0/0 with empty names, modes 644/755, and a
  gzip header without name or time.

## 7z details

- **Methods (py7zr):** LZMA, LZMA2, Deflate, Deflate64, BZip2, PPMd, zstd, Brotli, Copy, BCJ (x86, ARM, ARMT, PPC,
  SPARC, IA64), Delta, and AES-256.
- **Fallback:** when py7zr cannot read a method (BCJ2, ARM64, RISC-V, some zstd variants), the members are read
  through bsdtar if libarchive supports it. Otherwise the member reports the unsupported method.
- **Solid archives:** members share compressed blocks. Reading one member decompresses its block from the start.
  `arc_list` shows the number of blocks. A block's packed size is shared out over its members by unpacked size (7z
  stores one size per block), marked `packed_estimated` in JSON, so folder maps and ratios stay meaningful.
- **Encrypted file names** (`-mhe` in 7-Zip): the listing needs `--password`. A wrong password is reported as such.
- **Written 7z:** one solid LZMA2 block (`--level` 0-9, default 5). Dates, Unix permissions and symlinks are kept.

## Written archives

- Every output is written to a temporary file next to the target and renamed into place: complete or not at all.
  It gets the permissions a new file normally gets (0666 minus the umask: 0644 usually), so it can be shared.
- `arc_convert` keeps the source's member order: folders, links and files come out interleaved as stored, so a jar
  keeps `META-INF/MANIFEST.MF` first (Java's `JarInputStream` needs it there). A member replaced with `--add` keeps
  its place; new members go at the end. An `.epub` output always gets `mimetype` first, stored, without extra fields.
  `--reproducible` fixes dates, owners and permissions but keeps that order, so the same source always gives the same
  bytes. For output sorted by name, extract and use `arc_create --reproducible`.
- **Self-extracting 7z** (.exe with a 7z inside) is read at its embedded offset.

## RAR details

- File lists come from rarfile (pure Python): names, sizes, CRCs, dates, Unix modes, RAR5 symlinks and hard links,
  and the solid, multi-volume, comment and encryption flags.
- **Data:**
  - Plain archives stream through bsdtar in one pass. Stored members are read directly.
  - Encrypted members, multi-volume sets, RAR 1.5-2.x archives and solid RAR 2.x-4.x need `unrar` (or `unar`, or
    7-Zip `7z`/`7zz`). These tools are found on PATH and in the usual Windows folders. bsdtar also stops at an
    archive that contains any encrypted member, so the plain members of such an archive need them too.
  - Without them, the scripts say which tool is missing, per member; `arc_test` reports those members as "not
    testable here" (INCOMPLETE), not as damaged. Encrypted members without `--password` are "encrypted, not tested".
- Header-encrypted RAR (`-hp`) needs both the password and unrar or 7-Zip.
- For multi-volume sets, open the first part (`.part1.rar`). A lone file whose header claims to be a later volume is
  read through bsdtar.

## Single compressed files

- The member name comes from the gzip header (FNAME) when present, otherwise from the file name without its suffix.
- Sizes are exact for .xz (from the index) and .zst (from the frame headers when they declare it), and for any file
  up to 64 MB (counted). Otherwise the gzip trailer gives the size, which is exact below 4 GB.
- Concatenated (multi-member) .gz, .bz2 and .xz streams are read whole.
- Counting a size decompresses the file, so it stops at 2 GB (or earlier for a likely bomb) and says so.

## Decoders and memory

- Every decompress call is bounded: zip members are read by the skill's own bounded readers (deflate64, bzip2,
  LZMA, xz, zstd, PPMd), and py7zr's Deflate, Deflate64, zstd and Brotli decoders are replaced by bounded ones.
- xz and .lzma streams decode with a memory limit, zstd with a window limit, and every declared dictionary is checked
  first (`references/security.md`, "Bombs and decoder memory").

## Timestamps and permissions

- **Kept everywhere:** modification times, permission bits, symlinks.
- **Zip without the Unix-time field:** times are local "DOS" times with 2-second resolution.
- **On extraction:**
  - Permissions are masked to 0755/0644 plus the owner bits, and setuid, setgid and sticky are cleared.
  - Owners are never applied.
  - `--no-permissions` and `--no-times` skip permissions and dates.

## External tools

| Tool | Where | Used for |
|---|---|---|
| bsdtar (libarchive) | macOS `/usr/bin/bsdtar` and `tar`; Windows 10+ `tar.exe`; Linux: `libarchive-tools` | RAR data, iso, cab, cpio, xar, ar, rpm, lha, warc; fallback for zip, 7z and tar dialects tarfile rejects |
| unrar / unar / 7z / 7zz | installed by the user | encrypted, multi-volume, RAR 1.5-2.x and old solid RAR |

GNU tar is not used: it cannot read zip, 7z, RAR or iso. `DESK_BSDTAR=/path/to/bsdtar` points at a specific build,
and `DESK_BSDTAR=none` disables it. External tools run one at a time, from the main process only (never from the
parallel workers that test or search big zips), only after the dictionary checks above, and under a watchdog:
`DESK_ARC_TOOL_TIMEOUT` (1800 s), `DESK_ARC_TOOL_STALL` (60 s without output), `DESK_ARC_TOOL_MAX_MB` (1024 MB).
