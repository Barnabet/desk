---
name: archives
description: List, inspect, safely extract, create, read, search, test, convert and compare archives and compressed files. Handles .zip and zip-based files (.jar .war .apk .whl .epub .docx …), .tar, .tar.gz/.tgz, .tar.bz2, .tar.xz, .tar.zst, .gz, .bz2, .xz, .zst, .7z and .rar, and reads .iso .cab .cpio .xar .deb .rpm .warc through the system's libarchive. Use it whenever a task involves an archive or a compressed file. See what is inside without unpacking. Check a download or upload for zip-slip paths, escaping links, devices, zip bombs and hostile compression headers before extracting. Extract some files or all of them, with passwords and limits. Read or grep members in place, including nested archives. Zip up a folder with AES-256 encryption, .gitignore excludes or byte-reproducible output. Repack between formats, adding or removing members. Verify integrity (CRCs, passwords), or diff two releases or an archive against a folder. Huge archives get a map-first listing and cached indexes.
license: MIT
---

# Archives

Scripts in `scripts/` use Python's zipfile and tarfile with py7zr (7z), pyzipper (AES zip), zstandard and rarfile.
Desk sets up their Python environment, so run them with `python3`. Every script has `--help`, prints Markdown by
default and JSON with `--format json`, and never changes its input: outputs go to new paths, and an existing file
is only replaced with `--force`. RAR data, .iso, .cab, .cpio, .xar and .warc are read through libarchive's `bsdtar`.
It is built into macOS and Windows 10+ as `tar`. Encrypted, multi-volume, RAR 1.5-2.x and RAR 2-4 solid data also
need `unrar` or 7-Zip installed.

Work in a loop: **look** inside the archive, **act** (extract, create, repack), then **check** the result.

## Look

```bash
python3 scripts/arc_list.py download.zip                    # summary, security verdict, members
python3 scripts/arc_list.py upload.zip --check              # only the verdict and findings: run this before extracting
python3 scripts/arc_list.py app.jar --find '*.class' --sort size
python3 scripts/arc_list.py bundle.zip --recurse            # also inside nested archives: a.zip::b/c.txt
```

- The listing gives sizes, packed sizes, dates, methods and encryption, and the archive's comment, engine, and
  whether it is solid. A **security** section comes first, with a verdict (`UNSAFE`, `Caution`, or no problems) and
  each finding with a count and examples:
  - **danger**: `..` paths (zip-slip), absolute paths, links pointing outside, members stored under a link, device
    files, overlapping zip entries, local/central name mismatches, packed data bigger than the declared size allows,
    bomb-like ratios, compression dictionaries too big to decode safely, NUL bytes in names.
  - **warn**: duplicates, names differing only in case or Unicode form, a path used both as a file and a folder,
    setuid bits, very high ratios, huge sizes or file counts, ZipCrypto, unsupported methods.
  - **info**: encrypted members, nested archives, names Windows cannot create, backslashes, macOS metadata.
  With `--recurse`, findings inside nested archives are merged per kind. Details: `references/security.md`.
- Each member's path is its **address**: other scripts take it as is (`docs/a.md`), and `outer.zip::inner/x.txt` reaches
  into nested archives.

```bash
python3 scripts/arc_read.py release.zip README.md                     # text, encoding detected (UTF-8/16, cp1252 …)
python3 scripts/arc_read.py logs.tar.gz app/server.log --lines 5000-5200
python3 scripts/arc_read.py access.log.gz --tail 50                   # the last 50 lines (same as --lines -50)
python3 scripts/arc_read.py fw.zip boot.bin --hex --bytes 256         # binary: hexdump and file type
python3 scripts/arc_read.py photos.zip img/cover.jpg --save cover.jpg # copy one member out, then view_image it
```

- `arc_read` never extracts to disk. `--lines A-B` is a range, `--lines A-` runs to the end, and `--lines -N` (or
  `--tail N`) is the **last** N lines; `--head N` the first N.
- A binary member shows its type and size (pixel size for images); unknown binaries also get a short hexdump, and
  `--hex` shows the bytes of any member.
- To **see** an image member, save it with `--save` and look at it with view_image (PNG, JPEG, GIF, WebP up to
  8000 px and 3.75 MB; other or bigger images go through the `images` skill first). Documents inside archives
  (.docx, .pdf …): save them, then use their own skill.

### Big files

Big archives never print whole. Work map → find → drill down:

```bash
python3 scripts/arc_list.py dump.tar.gz                             # the map: folders with file counts, sizes, dates; largest files
python3 scripts/arc_list.py dump.tar.gz --path data/logs/           # one folder (addresses stay full member paths)
python3 scripts/arc_list.py dump.tar.gz --find '**/2024-06-*.log'   # members matching a glob, anywhere
python3 scripts/arc_read.py dump.tar.gz --grep 'OutOfMemory' -C 2   # member:line: text, streamed, nothing extracted
python3 scripts/arc_read.py dump.tar.gz data/logs/app-17.log --lines 880-960
```

- Above 300 members `arc_list` shows the map. When everything sits in one top folder (`dump/`, `project-1.2/`),
  the map starts inside it. `--depth 2` shows two folder levels. `--flat` lists members: up to 100 as a table,
  beyond that as CSV rows, as many as fit in `--max-chars` (60,000). A cut output ends with the exact command for
  the next part (`--offset N`); run it rather than raising the cap.
- Listings of big, solid or many-member archives are **cached** by content (`--no-cache` skips it), and so is each
  printed view: the same command again is instant. tar.gz, tar.xz and solid 7z have no index, so the first listing
  of a 2 GB tar.gz decompresses it all (seconds).
- Reading one member of a tar.* or solid archive decompresses everything before it. Ask for several members in one
  `arc_read` call, and grep once rather than reading member by member. Grep prints nearby matches' context once, as
  grep does, stops at `--max-matches` (200) and says so, and skips binary members (by content, signature or
  extension) unless `--binary`.
- Big zips are tested and searched on several cores. Measured timings: `references/performance.md`.

## Act

### Extract safely

```bash
python3 scripts/arc_extract.py release.zip                  # → ./release/, or ./<top folder>/ when it has exactly one
python3 scripts/arc_extract.py data.tar.gz --out work/data
python3 scripts/arc_extract.py site.zip --only 'docs/**' --only '*.md' --strip-components 1
python3 scripts/arc_extract.py photos.7z --only '*.jpg' --flatten --out pics
python3 scripts/arc_extract.py secret.zip --password-file pw.txt
python3 scripts/arc_extract.py upload.zip --dry-run         # the plan: every member → path, and what would be skipped
```

- Extraction always goes into a **new folder**. If `./release/` exists it uses `./release-2/`. Existing files are
  never overwritten without `--force`.
- It **neutralises** dangerous members instead of trusting them:
  - `..` paths are skipped (`--on-unsafe sanitize` keeps them inside, `--on-unsafe fail` stops before writing).
  - Absolute paths become relative.
  - Links that point outside (followed through other links, as the OS would) and device files are never created.
  - Nothing is ever written through a link.
  - Hard links become copies. setuid bits are cleared. Case collisions are renamed `name~2.ext`.
  - On Windows, names Windows forbids are renamed.
- **Bombs are refused before anything is decoded**: members over 64 MB that declare more than `--max-ratio`
  (1000:1), archives over 1 GB that do so in total, overlapping zip entries, and streams whose headers declare a
  compression dictionary over 256 MB (`DESK_ARC_MAX_DICT_MB`: decoders allocate it up front). While writing, the
  limits hold even when headers lie: `--max-size` (16GB), `--max-files` (500000), `--max-ratio`. Raise them only
  for archives you trust.
- The report lists everything skipped, refused, renamed or failed, and why (identical reasons on one line). Exit
  status: 0 when every chosen member was written or deliberately skipped (neutralised members are skipped, not
  failures); 1 when a member failed (corrupt, wrong password …): the others are still extracted. If nothing at all
  could be written, the folders it made are removed again.
- **Globs** (`--only`, `--exclude`, `--find`, in every script) work like .gitignore: a pattern without a `/` (`*.py`,
  `build/`) matches a name at any depth, and `**` crosses folders. A pattern with a `/` inside (`docs/**`,
  `src/app/`) is matched from the archive root; when every member sits in one top folder it may be left out
  (`docs/**` finds `project-1.2/docs/…`), and with `--strip-components` it may be written as the stripped path.
  Write `**/docs/` to match a folder at any depth. A pattern that matches nothing is reported as a warning, and
  `arc_list --path` may leave out the single top folder too.

### Create

```bash
python3 scripts/arc_create.py release.zip dist/ README.md            # dist/… and README.md
python3 scripts/arc_create.py site.zip --base public                 # the contents of public/ at the root
python3 scripts/arc_create.py src.tar.gz . --gitignore --exclude-vcs # honour .gitignore; drop .git, .DS_Store …
python3 scripts/arc_create.py data.tar.zst data/ --level 19
python3 scripts/arc_create.py secret.zip report.pdf --password-file pw.txt           # AES-256
python3 scripts/arc_create.py vault.7z docs/ --password-file pw.txt --encrypt-names
python3 scripts/arc_create.py build.zip out/ --reproducible          # identical bytes for identical inputs
```

- The output name picks the format: `.zip` `.jar` `.whl` `.epub` … `.tar` `.tar.gz`/`.tgz` `.tar.bz2` `.tar.xz`
  `.tar.zst` `.7z`, or one compressed file: `.gz` `.bz2` `.xz` `.zst`. Use `--to` when the name does not say.
  Archives are written complete or not at all, with normal permissions (0644 under the usual umask).
- Zip stores already-compressed files (jpg, mp4, zip …) instead of deflating them again. EPUBs get `mimetype`
  first and stored.
- gzip and zstd compress on several cores. Symlinks are stored as links (`--follow-links` stores their targets).
- `--exclude`, `--include`, `--exclude-from FILE` (.gitignore syntax) and `--prefix DIR/` shape the contents.
- Passwords encrypt zip and 7z with AES-256. Windows Explorer cannot open AES zips: tell the user 7-Zip can.
  tar and .gz cannot be encrypted.

### Repack, add, remove

```bash
python3 scripts/arc_convert.py legacy.rar legacy.zip
python3 scripts/arc_convert.py data.tar.gz --to 7z                      # → ./data.7z
python3 scripts/arc_convert.py old.zip clean.zip --exclude '__MACOSX/' --exclude '._*'
python3 scripts/arc_convert.py app.jar app2.jar --add MANIFEST.MF=META-INF/MANIFEST.MF   # replace or add members
python3 scripts/arc_convert.py export.7z export.zip --password-file pw.txt --encrypt
```

Members stream across without being extracted, in the source's order (a jar keeps its manifest first; an .epub
output gets `mimetype` first and stored). `--add PATH=NAME` replaces a member in its place, or appends a new one.
Dates, permissions and symlinks are kept; hard links stay links in tar and become copies elsewhere. Unsafe and
bomb-like members are left out, as in extraction, and listed. `--new-password-file FILE` re-encrypts with another
password; tar and .gz cannot be encrypted.

## Check

```bash
python3 scripts/arc_test.py backup.tar.gz                  # every member decompressed and checked; exit 1 if broken
python3 scripts/arc_test.py secret.zip --password-file pw.txt   # also says whether the password is right
python3 scripts/arc_diff.py release-1.2.zip release-1.3.zip --content
python3 scripts/arc_diff.py backup.tar.gz ~/project        # what changed on disk since the backup
```

1. `arc_test` says `OK` (exit 0), `FAILED` (exit 1: broken, refused or unreadable members, a wrong password, or a
   damaged structure, each listed), or `INCOMPLETE` (exit 0) when some members could not be checked here: encrypted
   without a password, or a method or tool this machine lacks. Say which, and do not call them damaged. A wrong
   password is reported as such ("Not decrypted"), not as damage.
2. After creating or repacking, run `arc_test` on the result, and `arc_diff` against the source (folder or archive).
   The diff should say `IDENTICAL`, or list exactly the changes you meant.
3. After extracting, read the report. Tell the user what was skipped, refused or renamed and why, and compare with
   the `arc_diff` command the report suggests (it repeats your `--only`/`--exclude`) when completeness matters.
4. `arc_diff` matches members by path and sets aside a single top folder automatically (`project-1.2/`). It compares
   by size and CRC-32, or `--hash sha256`. `--content` adds unified diffs of changed text members.

## Rules

- Never modify the user's archive. Write new archives and folders, and say where they are.
- Run `arc_list --check` before extracting anything from an untrusted source. Never extract with other tools
  (`unzip`, `tar -x`, `7z x`) that skip these protections, and never raise `DESK_ARC_MAX_DICT_MB` or `--max-ratio`
  for an archive you have not verified.
- Report what could not be done: skipped or refused members, wrong passwords, unsupported methods, missing tools. Do
  not guess at contents you could not read.
- Keep passwords out of command lines the user will see: prefer `--password-file`.

## Damaged archives

- A zip cut short or damaged before its central directory is still listed: `arc_list` says how much is missing,
  `arc_test` names the members that are lost, and `arc_extract` writes the intact ones (exit 1 for the lost). A zip
  whose central directory is gone is read from its local headers through bsdtar; links and permissions stored only
  in the central directory are lost then.
- A truncated tar.* lists and extracts every member before the damage and says where it stops.
- When members are lost, say which, and ask the user for a fresh copy rather than guessing at their contents.

## Limits

- RAR cannot be created (the format is proprietary). Reading RAR data needs `bsdtar` for plain archives, and
  `unrar` or 7-Zip for encrypted, multi-volume, RAR 1.5-2.x or RAR 2-4 solid ones. File lists are always read.
- Files split into `name.001`, `name.002` … are refused with a one-line command that joins them into the workspace;
  run it, then open the joined file (multi-volume 7z is such a split). Spanned zips (`.z01` … `.zip`) are refused
  with the Info-ZIP `zip -s 0` command that rejoins them.
- .iso, .cab, .cpio, .xar, .ar/.deb, .rpm, .lha and .warc are read-only through libarchive's bsdtar. External tools
  run one at a time and are stopped after 1800 s, after 60 s without output, or past 1 GB of memory (crafted
  archives make bsdtar loop): `DESK_ARC_TOOL_TIMEOUT`, `DESK_ARC_TOOL_STALL`, `DESK_ARC_TOOL_MAX_MB`.
- Zips also pass Desk's shared zip check before anything is decoded, with this skill's `--max-size` and
  `--max-files` as its limits (`DESK_ZIP_*` variables, when set, win).
- Not supported: zip PKWARE strong encryption, AES-encrypted zip members compressed with xz, zstd or PPMd, 7z BCJ2
  and ARM64 filters when bsdtar cannot read them either, and 7z LZ4/Brotli (7-Zip-zstd builds). The scripts say so
  per member.
- Deflate64, zstd, xz and PPMd zip members, zip64, self-extracting .exe archives, and legacy name encodings
  (`--encoding cp932`, `cp866`, `gbk` …) are supported.
- Symlinks need Developer Mode on Windows. Otherwise links that stay inside are stored as copies of their target.

## Beyond the scripts

For anything the scripts do not cover, write a small Python script with the same `python3`. zipfile, tarfile,
py7zr, pyzipper, zstandard and rarfile are installed. This skill's helpers (`scripts/_arc.py` streams any
format's members with the same memory guards; `scripts/_safety.py` checks names) can be imported: see
`references/recipes.md`.

Neighbouring skills (they may not be installed): `file-inspector` for unknown files, `word-documents` /
`spreadsheets` / `presentations` for the Office files themselves, `markup-ebooks` for EPUB content, `images` for
pictures found inside archives, `data-files` for CSV, JSON and Parquet members, `email-calendar` for .eml and
.mbox members.

References: `references/formats.md` (formats, engines, methods, encryption), `references/security.md` (findings,
bombs, dictionaries and extraction safety), `references/recipes.md` (custom scripts), `references/performance.md`
(timings on big archives).
