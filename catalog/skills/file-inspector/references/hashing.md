# Hashes, checksum files, manifests and duplicates: file_hash.py

## Algorithms

`--algo` takes `md5`, `sha1`, `sha224`, `sha256` (default), `sha384`, `sha512`, `sha3_256`, `sha3_512`, `blake2b`,
`blake2s`, `crc32`, a comma-separated list, or `all` (md5, sha1, sha256, sha512, blake2b, crc32). Every algorithm
is computed in the same single read of the file. Many files are hashed in parallel threads (hashlib releases the
lock on large buffers), biggest first.

Output: a Markdown table (`--format md`), `json`, `csv`, or checksum lines on stdout (`--format sums` for GNU
`sha256sum` lines, `bsd` for `SHA256 (name) = …`). A Markdown listing longer than `--max-chars` (files, `--check`
problems, `--dupes` sets) stops at a row and ends with the same command at `--offset N`, the next part.

## Verifying

```bash
python3 scripts/file_hash.py ubuntu.iso --expect 3b1f…e9      # the algorithm follows from the hash's length
python3 scripts/file_hash.py --check SHA256SUMS               # names relative to the checksum file's folder
python3 scripts/file_hash.py --check sums.txt --base downloads/ --algo sha1
```

- Formats read by `--check`: GNU (`<hex>  name` or `<hex> *name`, and the escaped `\<hex>  name` form for names with
  a backslash or newline), BSD `--tag` (`SHA256 (name) = <hex>`) and SFV (`name CRC32`). The algorithm comes from
  the line, the hash length, or the file name (`SHA1SUMS`, `*.md5`, `*.sfv`); `--algo` forces one.
- Results: `OK`, `FAILED` (expected and actual shown), `MISSING`, `UNREADABLE`. The exit code is 1 unless every
  file is OK. If every file is missing, the names are relative to another folder: pass `--base`.
- **Verification always reads every byte**, even when a hash is cached: bit rot and careful tampering keep the
  size and modification time. (Fresh hashes still refresh the cache.)
- A 32-character hash is MD5, 40 SHA-1, 64 SHA-256 (or SHA3-256/BLAKE2s: pass `--algo`), 128 SHA-512.

## Manifests

```bash
python3 scripts/file_hash.py dataset/ -r --manifest dataset.sha256              # GNU format, relative paths
python3 scripts/file_hash.py dataset/ -r --manifest m.json --manifest-format json --algo sha256,md5
python3 scripts/file_hash.py release/*.zip --manifest SHA256SUMS --relative-to release/
```

Paths are relative to the inputs' common folder (or `--relative-to`), with forward slashes, so the manifest works
on Windows and macOS alike. The command prints the exact `--check` command to verify it later (with `--base` when
the manifest lives outside the hashed folder). JSON and CSV manifests hold every requested algorithm and the sizes.
Folders skip `.git`, `node_modules` and the like unless `--all`; `--no-hidden` skips hidden files. The manifest is
never written over one of the files it lists (compared by file identity, so a 100,000-file manifest checks in
milliseconds).

## Duplicates

`file_hash.py DIR -r --dupes [--min-size 1KB]` finds identical files in three passes, each on fewer files:

1. group by size (files of a unique size cannot have a duplicate);
2. a partial hash (BLAKE2b of the size, the first 64 KB and the last 64 KB); files up to 128 KB are fully covered
   by it, so they need no third pass;
3. a full SHA-256 of the remaining candidates.

Sets are listed by wasted space (size × extra copies), with every path. `file_survey.py` runs the same search.

## Caching

Hashes are remembered two ways, so re-hashing an unchanged tree is instant:

- by path, size and modification time, in a small SQLite index in the file cache (what `file_survey` and `--dupes`
  reruns use; 100,000 files are looked up in well under a second);
- for files over 8 MB, also by content fingerprint (size, modification time and three 256 KB samples), so a renamed
  big file (or a copy that kept its modification time) is not read again.

`--no-cache` ignores both. `DESK_NO_CACHE=1` disables caching everywhere; the cache lives in the temporary cache
folder and is size-capped (see the file-skills cache settings `DESK_FILE_CACHE_MB`).
