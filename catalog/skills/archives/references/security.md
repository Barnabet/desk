# Security: what arc_list flags and what arc_extract does about it

Archives from the internet, email or users are untrusted input. A crafted archive can:
- write outside the target folder;
- plant a link that a later member (or a later program) writes through;
- fill the disk;
- or show one tool a different file than another.

`arc_list` reports these, and `arc_extract` neutralises them by default. `arc_convert` applies the same rules, so a
repacked archive is clean.

Each finding has a code (in `--format json`), a severity, a count and up to five examples.

## Danger

| Code | What it is | What extraction does |
|---|---|---|
| `path-escape` | A name that climbs above the root with `..` (zip-slip): `../../home/u/.bashrc` | Skipped. `--on-unsafe sanitize` keeps it inside with the `..` removed; `--on-unsafe fail` stops before writing anything |
| `absolute` | `/etc/x`, `C:\x`, `C:x`, `\\server\share\x` | The root is stripped; the path becomes relative (`--on-unsafe fail` stops instead) |
| `link-escape` | A symlink whose target is absolute or climbs out, following the other links in the archive as the OS would (`a → .` then `b → a/..`). Also a hard link to an absolute or `..` path | Never created |
| `link-through` | A member stored under a path that is a link in the archive (`d → /tmp`, then `d/evil`) | Skipped: nothing is written through a link, and links are created last |
| `device` | Character or block device members | Skipped |
| `overlap` | Zip entries whose data overlap (several central entries sharing one local header): the "better zip bomb" that reaches ratios of millions | Never decoded: skipped by arc_extract, refused by arc_test, left out by arc_convert. (For an archive the user trusts, `--on-unsafe sanitize` extracts them within the size limits.) |
| `name-mismatch` | A zip's local header names a different file than its central directory, so tools disagree on what is extracted | The central directory name is used and reported |
| `size-lie` | A zip member whose packed data is larger than its declared size allows (more than 1% + 1 KB over it): hidden data after the declared end, or a header that lies about the size | Reading stops at the declared size (and its CRC is checked there); `arc_test` fails the member when data continues past it |
| `bomb` | A member over 64 MB declaring more than 1000:1, more than 1 GB declared at more than 1000:1 in total, or a tar.* whose headers declare more than the listing may decompress | Refused before decoding (`--max-ratio`); `--max-size` and `--max-ratio` also stop the bytes actually produced |
| `huge-dict` | An LZMA, xz, zstd, PPMd or RAR stream whose header declares a dictionary or window over 256 MB (`DESK_ARC_MAX_DICT_MB`). Decoders allocate it up front: a 150-byte file can demand 4 GB | Never decoded by any script, nor handed to bsdtar, unrar or 7z |
| `nul` | A NUL byte inside a name (tools cut the name in different places) | The name is cut at the NUL |
| `nested-deep` | Archives nested more than three levels (42.zip style), seen with `--recurse` | Nothing is unpacked recursively |

## Warnings and notes

| Code | Severity | What extraction does |
|---|---|---|
| `fifo` | warn | FIFOs and sockets are skipped |
| `setuid` | warn | setuid, setgid and sticky bits are cleared; permissions are masked to 0755/0644 |
| `duplicate` | warn | The same path stored twice: the later copy wins (tar semantics); both are reported |
| `case-collision` | warn | `Readme.md` vs `README.md`, or NFC vs NFD forms of a name: on a case-insensitive disk (macOS, Windows) the later one becomes `README~2.md` |
| `file-dir` | warn | A path used as a file and as a folder: the later member is skipped |
| `dotdot-internal` | warn | `a/../b` is normalised to `b` (a name that climbs out is `path-escape` only) |
| `high-ratio` | warn | A member over 1 MB compressed more than 100:1 |
| `huge`, `many-files` | warn | Over 20 GB unpacked or 100,000 files: checked against the limits before anything is written |
| `hardlink-missing`, `link-unknown` | warn | Skipped |
| `weak-encryption` | warn | ZipCrypto can be cracked; suggest AES (`arc_convert … --encrypt`) |
| `control` | warn | Control characters are replaced by `_` |
| `unsupported` | warn | Methods like shrink or implode cannot be decompressed; those members fail and are reported |
| `encrypted` | info | Needs `--password-file` (or `--password`); with one given, `arc_test` says whether it is right |
| `nested` | info | Archives inside: `arc_list --recurse`, `arc_read outer::inner::path`. With `--recurse`, the findings of all nested archives are merged per code ("(inside 64 nested archives)"), and nested archives that are not opened are listed once per reason (bomb-like ratio, over 512 MB, unreadable) |
| `windows-names` | info | `CON`, `aux.txt`, trailing dots or spaces, `<>:"\|?*`: renamed on Windows |
| `backslash` | info | `a\b` is treated as the folder `a` and the file `b` |
| `macos-metadata` | info | `__MACOSX/`, `._*`, `.DS_Store`: skip with `--exclude '__MACOSX/' --exclude '._*'` |
| `long-paths` | info | Over 250 characters: may fail on Windows without long-path support |
| `prepended` | info | Bytes before the first zip member: a self-extractor stub, or a file that is two formats at once |
| `legacy-names` | info | Non-UTF-8 names: try `--encoding` |

## Bombs and decoder memory

A small archive can decode to terabytes, and a hostile header can make a decoder allocate gigabytes before it
reads a byte. Every script that decodes (arc_extract, arc_read, arc_test, arc_convert, arc_diff, and arc_list when a
listing needs decoding) applies the same rules:

1. **Dictionaries first.** Before any stream reaches a decoder, the dictionary or window it declares is read from
   its headers: the .xz block header, the .lzma header, the zstd frame header, the LZMA/LZMA2/PPMd coder properties
   of a 7z block (including a compressed 7z file list, which py7zr decodes as it opens the archive), the LZMA, xz,
   zstd and PPMd properties of zip members, RAR5 compression flags, and for bsdtar-read formats the outer stream,
   xar's table of contents, an RPM's payload or a zip's local headers. Anything over `DESK_ARC_MAX_DICT_MB` (256) is
   refused with its size. Python's decoders also run with a memory limit (`memlimit`, `max_window_size`) as a
   backstop for later blocks.
2. **Declared sizes next.** A member over 64 MB declaring more than `--max-ratio` (1000:1), or members adding up to
   more than `--max-size` (16 GB) or to more than 1 GB at more than 1000:1, are refused before decoding. A tar.*
   listing stops at a header that declares more than the listing may decompress (the larger of 1 GB and 1000 times
   the archive's size), and reports a `bomb`.
3. **Bytes produced last.** Every decoder call is bounded (zip, 7z Deflate/Deflate64/zstd/Brotli and bzip2 members
   too, which the libraries would otherwise inflate without limit), and the sinks stop a member that grows past its declared
   size or past `--max-ratio`, and the whole run at `--max-size`, whatever the headers claim.
4. **External tools** (bsdtar, unrar, 7z) run one at a time, never in parallel workers, under a watchdog that stops
   them after `DESK_ARC_TOOL_TIMEOUT` seconds (1800), after `DESK_ARC_TOOL_STALL` seconds without output (60), or
   past `DESK_ARC_TOOL_MAX_MB` of memory (1024). Crafted ISO and RAR files make bsdtar loop while its memory grows;
   the watchdog turns that into an error. A 7z whose file list py7zr cannot parse is checked byte by byte for its
   LZMA, LZMA2 and PPMd coders before bsdtar sees it; if even that fails (a compressed list), it is refused.

5. **The shared zip check.** Before any member of a zip (or a zip-based file) is decoded, Desk's `check_zip` (the
   gate every file skill runs) checks the declared member count, member sizes and total, with `--max-files`
   (500,000) and `--max-size` as its limits. `DESK_ZIP_MAX_MEMBERS`, `DESK_ZIP_MAX_MB` and `DESK_ZIP_MEMBER_MAX_MB`,
   when set, win. Its whole-archive ratio test is left to `--max-ratio`, which refuses bomb-like members one by one
   so the others can still be read. `arc_list` decodes nothing, so it lists such archives and reports why.

`DESK_ARC_MAX_RATIO` and `DESK_ARC_MAX_SIZE` change the defaults of `--max-ratio` and `--max-size`. Raise any of
these only for an archive you trust, and never `DESK_ARC_MAX_DICT_MB` beyond the memory the machine can spare.

## How extraction stays inside the folder

1. **Plan.**
   - Every member name is normalised: backslashes become folders, `..` is resolved, and roots and drive letters are
     removed.
   - Unsafe members are skipped (or sanitised) before anything is written.
   - Collisions are resolved on the planned paths.
2. **Folders.** Each folder on the way is created one level at a time. The walk refuses any component that is a
   link or a file, and checks that the real path stays inside the output folder. This also covers links that
   already existed on disk.
3. **Files.**
   - Each member streams into a temporary `.part` file next to its destination, then is renamed into place. An
     interrupted or aborted member leaves nothing half-written.
   - A member that grows past its declared size, past `--max-ratio` (members over 64 MB), or past `--max-size` in
     total stops the extraction. Members refused from their headers (bombs, huge dictionaries, overlapping entries)
     are never decoded and are listed as skipped.
4. **Hard links** are written as copies of members already extracted.
5. **Symlinks** are created last, only when they resolve inside. On Windows without link rights, links to files
   become copies.
6. **Existing files** are kept unless `--force`. Even with `--force`, a folder is never replaced, and an existing
   link is removed rather than written through.

Before writing, the declared total is checked against `--max-size`, against the free disk space, and the member
count against `--max-files`. When nothing at all could be written (every member refused, or a wrong password), the
folders the run made are removed again, so no empty tree is left behind.

## Passwords

- `--password-file FILE` keeps the password out of the visible command line; `--password` works too.
- AES zips (pyzipper) and 7z AES (py7zr) are decrypted in process. RAR decryption needs unrar or 7-Zip.
- `arc_test --password-file …` says whether the password is right: every encrypted member must decrypt with a
  valid checksum. A wrong password is reported as "Not decrypted", not as damage (exit 1).
- `arc_convert --new-password-file FILE` re-encrypts with another password; `--encrypt` keeps the source's.
- Listings are never cached when a password was given.
