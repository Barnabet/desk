# Performance and caching

## What is cached

Every script that only looks at files caches its result by the file's content, so the second look is instant:

| Script | Cached | Key |
|---|---|---|
| `img_view.py` (preview, zoom, grid, map) | the rendered PNG and its notes | file content + every option that changes the render |
| `img_view.py --zoom` on 50 MP or more | a tile store: the upright image in 1024 px tiles at full and 1/4 resolution | file content + orientation/RAW options |
| `img_view.py --sheet` | each thumbnail, and each page of sheets | content of every input + layout options + the page |
| `img_info.py` | the record (EXIF, stats, palette…) | file content + `--exif`, `--stats`, `--colors` |
| `img_compare.py dupes / similar / hash` | the hashes of each file | file content |
| `font_tool.py find`, text fallback | the index of installed fonts, with their character coverage | font folders' modification times |

- Keys use the file's content, not its name or date: an edited file is read again, and a copy shares the entry.
- Keys also include a hash of the skill's own code, so an updated skill never reuses old results.
- Errors are never cached: fixing a file, or the skill, takes effect immediately.
- The cache lives in the temp folder (`desk-files`). It holds at most 1 GB by default, and nothing is stored when the
  disk is nearly full. Set `DESK_FILE_CACHE` to move it, `DESK_FILE_CACHE_MB` to resize it, and `DESK_NO_CACHE=1` (or
  `--no-cache`) to bypass it.
- A batch answers cache hits in the main process and starts worker processes only for the files it has to compute.

Scripts that write deliverables (`img_convert`, `img_edit`, `img_compose`, `img_optimize`, `font_tool` subset and
convert) always do the work.

## Measured timings

Measured on an Apple M2 (8 cores) with the scripts run as Desk runs them (sandboxed, Python 3.12). Other jobs were
using the machine at the time (load average around 10), so an idle machine is somewhat faster. "Cached" is the second
run of the same command.

| Operation | First run | Cached | Speed-up |
|---|---|---|---|
| `img_view` preview, 24 MP JPEG | 0.39 s | 0.07 s | 6× |
| `img_view` preview, 3840×2160 HEIC | 0.66 s | 0.06 s | 10× |
| `img_view` preview, 6016×6016 HEIC in Display P3 | 1.46 s | 0.06 s | 25× |
| `img_view` zoom + grid, 24 MP JPEG | 0.23 s | 0.06 s | 4× |
| `img_view --sheet`, 50 JPEGs (1600×1200) | 0.69 s | 0.09 s | 8× |
| `img_info`, 50 JPEGs (table with stats) | 0.31 s | 0.10 s | 3× |
| `img_compare dupes`, 50 JPEGs | 0.51 s | 0.11 s | 5× |

| Operation | Time |
|---|---|
| `--help` of any script (heavy libraries load lazily) | 0.06-0.1 s |
| `img_convert` 50 JPEGs (1600×1200) → WebP, in parallel | 3.6 s |
| `img_convert` 50 JPEGs → AVIF | 4.9 s |
| `img_convert` 50 small JPEGs (640×480) → WebP (selftest) | 0.7 s |
| `img_convert` 24 MP JPEG → PNG | 1.8 s |
| `img_convert` 6016×6016 HEIC → JPEG | 1.8 s |
| `img_convert` 50 JPEGs → one PDF (JPEGs embedded unchanged) | 0.7 s |
| `img_edit` 50 JPEGs: resize + text + border | 1.2 s |
| `img_edit` 24 MP JPEG: 5 annotations (boxes, arrow, highlight, callout, blur) | 0.9 s |
| `img_optimize` 24 MP JPEG for the web (`--max-edge 1920`) | 0.8 s |
| `img_optimize` 6016×6016 HEIC for the web (`--max-edge 2560`) | 3.1 s |
| `img_optimize` 24 MP JPEG `--target-kb 300` | 5.2 s |
| `img_compose grid` of 50 JPEGs | 2.7 s |
| `img_compare` diff of two 24 MP images | 1.4 s |
| `font_tool subset` of a 7.5 MB variable font → WOFF2 | 2-7 s |
| `font_tool convert` 7.5 MB font → WOFF2 (Brotli 11) | 17-33 s |
| `font_tool convert` 7.5 MB font → WOFF2 `--fast` (Brotli 9, about 10% larger) | 0.8-1.5 s |
| `font_tool find --covers 你好` over 780 installed faces | 0.1-0.3 s |

## Big images (200 megapixels)

Measured on the same machine (load average around 10), two fixtures of 20000×10000: a 45 MB photographic JPEG and a
0.7 MB PNG plan (lines and text on white). A second series under a load average of 20 (another agent's video encode on
all cores) took 3-4× longer, cached runs 0.1-0.2 s; the memory figures did not change.

| Operation | JPEG time | JPEG peak memory | PNG time | PNG peak memory |
|---|---|---|---|---|
| `img_info` (header, EXIF, stats from a reduced decode) | 0.63 s, cached 0.05 s | 146 MB | 0.81 s, cached 0.06 s | 940 MB |
| `img_view` preview | 0.60 s, cached 0.06 s | 115 MB | 0.84 s, cached 0.06 s | 950 MB |
| `img_view --map` (overview + tile table) | 0.61 s, cached 0.06 s | 135 MB | 0.86 s, cached 0.06 s | 970 MB |
| first `--zoom r2c3` (full decode + tile store) | 1.5 s | 860-970 MB | 1.3 s | 910-940 MB |
| the same zoom again | 0.07 s | 40 MB | 0.08 s | 40 MB |
| 3 new zooms in one call (read from the tile store) | 0.47 s | 121 MB | 0.26 s | 117 MB |
| 1 new small region (from the store) | 0.11 s | 51 MB | 0.1 s | 50 MB |
| `img_convert --max-edge 2000` → WebP | 0.68 s | 135 MB | 0.95 s | 950 MB |
| `img_optimize --max-edge 2560` | 1.1 s | 350 MB | 1.3 s | 1 GB |
| `img_compose tiles --tile 1568x1568` (91 tiles) | 3.2 s | 360 MB | 1.1 s | 820 MB |
| `img_edit` pixelate + labelled box, full size out | 1.4 s | 0.7-1.05 GB | 1.6 s | 900 MB |

A 20000×20000 one-bit PNG (400 MP in a 90 KB file) reads in 0.4 s with about 530 MB (`img_info`, `img_view`,
`img_convert --max-edge`), down from 2.1 GB.

Why it stays within memory:
- JPEGs decode at 1/2 to 1/8 scale through the decoder's DCT scaling whenever the output is smaller (preview, map,
  info statistics, `--max-edge`), so a 200 MP JPEG never needs its 600 MB of pixels for a look.
- PNG and TIFF cannot decode partially, so a look decodes them once; they are then converted to 8-bit and reduced in
  strips of about 16 MP, so there is no second full-size copy (a one-bit or 16-bit image never becomes a full-size
  RGB image).
- The first zoom into an image of 50 MP or more (`DESK_TILE_MIN_MP`) cuts it into the tile store (PNG tiles for
  graphics, JPEG quality 92 without chroma subsampling for photos: about 100 MB for a 200 MP photo). Every later
  zoom, from any process, opens only the tiles it covers, at full resolution or from the 1/4 level when the zoom is
  wide. Zooms of huge images run one after another in one process, never one decode per worker.
- Annotations, text, watermarks and composites are drawn in the region they cover and pasted, instead of converting the
  whole image to RGBA; filters limited to boxes work in place.
- JPEG output above 50 MP is baseline, not progressive: libjpeg's progressive encoder holds every coefficient of the
  image (about 1 GB at 200 MP).
- `img_info` reads PNG EXIF only when the file has an eXIf chunk (Pillow otherwise decodes the image to look for one).

## Big folders (2000 images)

A tree of 2000 JPEGs (58 MB): 1880 photos of 640×480 in 20 subfolders, and an extras folder with 90 planted copies
(30 identical, 30 resized, 30 recompressed at quality 40) and 30 crops, which are different pictures:

| Operation | First run | Cached |
|---|---|---|
| `img_info tree -r` (map + first page of rows, header records for all files) | 1.4 s | 0.6 s |
| `img_view tree -r --sheet` (the first 150 images: 5 sheets) | 1.4 s | 0.25 s |
| `img_compare dupes tree -r` (finds exactly the 90 groups) | 2.3-4.3 s | 0.6 s |

These were measured at a load average around 10. With another agent's video encode on all cores (load average 19)
the same runs took 3-4× longer: `img_info -r` 5.7 s cold and 1.8 s cached, `--find` over the cached records 2.3 s,
the first sheets 5.2 s and 1.0 s cached, `dupes` 7.6 s and 1.8 s cached. The main process stayed under 60 MB.

- Listings are paged: each call prints what fits in `--max-chars` (default 60000) and ends with the exact command
  for the next part. `img_info` of a big folder starts with a map (totals, formats, pixel sizes, date range, cameras,
  GPS, one row per subfolder), then rows with paths relative to the folder given.
- `--stats` on a folder computes pixel statistics only for the rows shown (at most 300 per call).
- Sheets go 150 images per call, numbered across calls (`sheet 6 of 67 (images 151-180)`).
- A batch answers cache hits in the main process and starts workers only for files it has to compute.

## Tested at smaller scale

The selftest runs scaled-down versions of these checks in about a minute: a 6400×4800 plan with `DESK_TILE_MIN_MP=20`
(map grid, tile addresses, exact pixels from the store, a cached zoom at least 5× faster than the first), a folder of 50
files paged with `--max-chars 2000` until the last part, JSON paging, `--find`, sheet paging, zTXt/svgz/WOFF bombs,
and the 12 MP cache check (a cached preview at least 5× faster).

## How the scripts stay fast

- Previews decode only what they need: JPEG draft mode decodes at 1/2 to 1/8 size, camera RAW files use their embedded
  preview, and images are shrunk before colour conversion.
- Zooming into an SVG renders only the zoomed region, at full sharpness.
- Batches run in worker processes (cores − 1, at most 8; `--workers N` changes it). Multi-threaded encoders (AVIF)
  get a share of the cores each, so a batch does not oversubscribe the machine.
- Annotations are drawn and antialiased inside each shape's own bounds, not over the whole photo.
- `img_optimize --target-kb` shrinks the image first when even a quality-60 JPEG cannot fit, then searches qualities.
  Its quality search stops early when the lowest quality is still too big.
- Font coverage is read from the raw `cmap` table and kept in the font index, so a coverage search does not decode
  780 fonts.

## Large inputs

- Images up to 600 megapixels open (`DESK_MAX_PIXELS` changes the limit). Every decoded 24 MP image takes about
  100 MB of memory in RGBA, so a batch of huge images uses one per worker.
- For very large photos, pass `--max-edge` to `img_convert` and `img_optimize`: resizing first makes encoding and the
  quality search many times faster.
- AVIF is the slowest encoder (about 1 s per 2 MP image and core). WebP and JPEG are several times faster.
- A zoom into an image under 50 MP decodes it at the smallest scale that still shows the region at full detail (a
  wide zoom of a big JPEG decodes at half size or less). Ask for several regions in one command (`--zoom` repeats):
  they render in parallel, and each is cached. Above 50 MP the tile store takes over (see above), and the zooms run in
  one process that decodes the image once.
- Limits against bombs: a .svgz inflates to at most 64 MB (`DESK_SVG_MAX_MB`), a WOFF/WOFF2 to at most 256 MB
  (`DESK_FONT_MAX_MB`), and they are refused in about 0.1 s with under 200 MB of memory. A PNG text chunk too large to
  decode is skipped with a warning; the pixels still render.
