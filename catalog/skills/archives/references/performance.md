# Performance on big archives

These are measured timings. Use them to estimate how long an operation will take before you start it, and to pick
the cheap way to get an answer.

## Setup

- Machine: Apple M2 (8 cores, 8 GB of memory), macOS, the skill's Python 3.12 environment, `DESK_MAX_WORKERS=2`
  (the default when several agents share a machine). The runs were made on 2026-09-25 while other agents were
  working.
  - The table below comes from a run at load average 4-5.
  - A run at load average 13-22 took 1.5 to 4 times as long for everything that decodes. Cache hits went from
    about 0.08 s to 0.2 s.
- Four archives of **50,000 members** each:
  - log files (lines of random words), plus one random binary file in every 50;
  - sizes spread exponentially, about 20 KB on average;
  - laid out as 5 top folders × 50 subfolders under `data/`;
  - written by `arc_create`'s own writers.
- Two archives shaped like real dumps:
  - `dataset.zip`: 120,002 members (60,000 stored JPEG-like files, 60,000 small JSON files, a 35 MB CSV and a nested
    zip), all under `dataset/`;
  - `dump.tar.gz`: 49,503 members (daily logs, CSV metrics, source files and two 40 MB logs), all under `dump/`.
- Test files are capped at 300 MB on disk. That is why three of the archives hold 1 GB and the tar.zst holds 2 GB.
  - Anything that decompresses every member grows linearly with the unpacked size: double those figures for 2 GB.
    This covers cold listings of tar.*, tests, grep, and reading from a solid 7z.
  - Cached listings and cached views depend only on the member count.
- Peak memory is that of the largest single process.
- The fixtures were deleted after the runs.

| Archive | Members | Unpacked | On disk |
|---|---|---|---|
| big.tar.gz | 50,000 | 1.0 GB | 264 MB |
| big.tar.zst | 50,000 | 2.0 GB | 127 MB |
| big.zip (deflate) | 50,000 | 1.0 GB | 291 MB |
| big.7z (solid LZMA2, one block) | 50,000 | 1.0 GB | 158 MB |
| dataset.zip | 120,002 | 133 MB | 124 MB |
| dump.tar.gz | 49,503 | 376 MB | 70 MB |

## Three speeds of `arc_list`

1. **Cold**: the first command on an archive. It reads the whole index (zip, 7z), or decompresses the whole stream
   (tar.*), and stores the listing in the content cache.
2. **A new view from the cached listing**, such as another `--path`, `--find` or `--offset`. Nothing is decoded; the
   time goes into loading the listing and sorting it into folders.
3. **The same command again.** Each printed view is cached too, so repeating a command costs the same as starting
   Python. This includes asking again after losing the context, or a command another agent already ran.

| | tar.gz 1 GB | tar.zst 2 GB | zip 1 GB | 7z 1 GB solid | dataset.zip (120k) | dump.tar.gz |
|---|---|---|---|---|---|---|
| `arc_list` (the map), cold | 2.9 s | 2.8 s | 1.7 s | 1.3 s | 1.95 s | 1.55 s |
| the same command again | 0.08 s | 0.09 s | 0.08 s | 0.09 s | 0.09 s | 0.07 s |
| `--path <folder>`, first time (cached listing) | 0.24 s | 0.26 s | 0.24 s | 0.39 s | 0.64 s | 0.20 s |
| `--find '*-04999*'` (cached listing) | 0.36 s | 0.32 s | 0.32 s | 0.34 s | | |
| `--flat --offset 25000` (one page, cached listing) | 0.33 s | 0.29 s | 0.32 s | 0.35 s | | |

- The same command again is 14 to 36 times faster than the cold listing, and a first view from the cached listing
  is 3 to 12 times faster.
- `--no-cache` (nothing read from or stored in the cache) took 2.2 s on dataset.zip and 2.0 s on dump.tar.gz.
- A cache hit peaks at 40 MB of memory. A cached listing of 50,000 members peaks at about 90 MB, and one of 120,000
  at 190 MB.

## Decoding operations

| Operation | tar.gz 1 GB | tar.zst 2 GB | zip 1 GB | 7z 1 GB solid |
|---|---|---|---|---|
| `arc_read` one member near the end | 1.4 s | 0.8 s | 0.5 s | 19 s |
| `arc_read --grep` over every member | 4.4 s | 4.3 s | 3.8 s | 25 s |
| `arc_test` (every member) | 1.4 s | 1.1 s | 2.0 s | 19 s |
| `arc_extract --only 'data/db/**'` (10,000 members) | 4.7 s | 4.5 s | 5.4 s | 20 s |
| `arc_convert` to .tar.zst (every member) | 4.3 s | | | |

- `arc_test` took 3.2 s on dataset.zip (120,002 members, 2 workers, 252 MB peak) and 0.8 s on dump.tar.gz.
- Peak memory stayed between 95 and 265 MB for every operation that decodes:
  - 100-150 MB while decoding;
  - 150-210 MB for the solid 7z;
  - about 230 MB for `arc_convert`, with zstd compressing on 2 threads.
- zip is the only format with random access: one member costs the same wherever it is. Its test and grep run on
  `DESK_MAX_WORKERS` processes.
- A solid 7z decodes from the start of its block to reach a member, at about 50-60 MB of output per second here
  (py7zr's LZMA2). Members near the start are quick.
- tar.gz and tar.zst decode at 0.7-1.8 GB per second. Their first listing decodes everything once; after that, the
  cached listing makes every map, folder view and `--find` fast.

## What this means for your work

1. Run `arc_list` once, early. The cold listing is the only full pass a map needs. After it, browse with `--path`,
   `--find` and `--flat --offset` from the cache, in a fraction of a second each. Repeating a command is free, so
   there is no need to copy big outputs into notes.
2. Search with one `arc_read --grep` rather than reading members one by one. Each read of a tar.* or solid-7z
   member decodes everything before it again, which is 19 s each in a 1 GB solid 7z.
3. Ask for several members in one `arc_read` call. They come out in a single pass.
4. Extract only what you need (`--only`). Extraction is limited by writing thousands of small files, not by
   decompression.
5. If you will read a solid 7z often, repack it once (`arc_convert big.7z big.zip`) to get random access, and say
   so.
6. You rarely need `--no-cache`. The cache is keyed by the file's content (a fingerprint of samples, size and date),
   so a changed archive is listed again anyway. Views are also keyed by the exact command, time zone and skill
   version.
