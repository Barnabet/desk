---
name: images
description: Read, view, convert, edit, compose, compare and shrink images, and inspect or convert fonts. Use for any task with .png, .jpg/.jpeg, .gif, .webp, .tif/.tiff, .bmp, .ico, .icns, .heic/.heif, .avif, .psd, camera RAW (.cr2 .cr3 .nef .arw .dng .orf .rw2 .raf), .svg, or fonts (.ttf .otf .woff .woff2). It reads EXIF, GPS and colour profiles; looks at an image, zooms into a region or overlays a coordinate grid; makes contact sheets and animation frame sheets; converts formats (HEIC to JPEG, SVG to PNG, RAW to TIFF, images to PDF, favicons); resizes, crops, rotates, annotates screenshots with boxes, arrows and labels, adds text or watermarks, redacts, removes a solid background; builds collages, GIF/WebP animations, sprite sheets and icon sets; diffs two images or finds duplicates; optimises images for the web or email; and inspects, subsets or converts fonts.
license: MIT
---

# Images

Scripts in `scripts/` use Pillow, pillow-heif, rawpy (LibRaw), resvg, fontTools, NumPy and oxipng. Desk sets up their
Python environment, so run them with `python3`. Every script has `--help` with examples, prints Markdown by default and
JSON with `--format json`, and works on folders and globs (`-r` recurses) in parallel.

You cannot see a file by reading its bytes, and this skill has no OCR. To know what an image shows, render it with
`img_view.py` and look at the PNG with **view_image**. That is also how you check your own work.

## Look

```bash
python3 scripts/img_info.py photo.heic                  # format, size, EXIF (camera, date, GPS), profile, palette
python3 scripts/img_info.py photos/ -r                  # a table of a whole folder
python3 scripts/img_view.py photo.heic scan.tif logo.svg   # PNGs sized for vision in ./renders
```

`img_view.py` turns photos upright (EXIF orientation), converts wide-gamut colour to sRGB, scales 16-bit data, and shows
transparency as a grey checkerboard. It prints the PNG paths and the scale between the render and the original. Then
call view_image on those paths.

To see detail, zoom. The crop is taken from the full-resolution image, so small text stays readable:

```bash
python3 scripts/img_view.py screenshot.png --grid                        # rulers and grid lines in image pixels
python3 scripts/img_view.py screenshot.png --zoom 60%,0,40%,25% --grid   # x,y,w,h in pixels or %, or WxH+X+Y
python3 scripts/img_view.py photos/ --sheet -r                           # numbered contact sheet of a folder
python3 scripts/img_view.py loading.gif --frames                         # every frame with its timing
```

Use `--grid` before editing: its labels are the pixel coordinates that `img_edit.py` boxes, crops and annotations take.
A grid on a zoom is labelled in the original image's coordinates. A folder of more than 12 images becomes a contact
sheet automatically. Renders, sheets, info and hashes are cached by file content, so looking again is instant
(`--no-cache` redoes the work).

## Big files and big folders

Do not try to see a 200-megapixel scan or 2000 photos in one go. Map first, find, then drill down:

```bash
python3 scripts/img_info.py photos/ -r                           # map: totals, formats, sizes, dates, cameras, subfolders
python3 scripts/img_info.py photos/ -r --find "Canon|2024:07"     # matching files, with the field and context
python3 scripts/img_view.py photos/2024-07/ --sheet               # sheets of the part that matters
python3 scripts/img_view.py scan.tif --map                        # overview with rulers and named tiles r1c1 …
python3 scripts/img_view.py scan.tif --zoom r2c3 --zoom r4c1:r4c2  # tiles (or a block) at full resolution
```

- Every listing stops at `--max-chars` (default 60000) on a whole row and ends with the exact command that reads the
  next part (`--offset N`; `--limit` caps rows). JSON pages the same way, with that command on stderr. Rows show paths
  relative to the folder given, so you can pass them back. Sheets come 150 images (5 sheets) per call.
- Each map tile is about one render at actual pixels (at most 100 tiles; `--tile 800` for a finer grid). The first zoom
  into an image of 50 MP or more decodes it once into a cached tile store (about 1.5 s for 200 MP), and later zooms
  read only the tiles they need (under 0.2 s). Previews and maps of a 200 MP image take about a second.
- Results are cached per file: listing or sheeting 2000 files again takes a fraction of the first run.
- `img_compose.py tiles scan.tif --out-dir tiles/` writes the map's tiles as files when you need them elsewhere.

## Act

**Convert** any readable format to png, jpg, webp, avif, heic, tif, gif, bmp, ico, icns, pdf, apng, jp2, tga or qoi:

```bash
python3 scripts/img_convert.py IMG_0042.HEIC --to jpg --out IMG_0042.jpg
python3 scripts/img_convert.py photos/ -r --to webp --quality 80 --max-edge 2000 --strip --out-dir web/
python3 scripts/img_convert.py scan1.jpg scan2.png --out scans.pdf --pdf-page a4
```

Animations stay animated in gif, webp, apng and avif; `--split` writes each frame or TIFF page as its own file. SVG is
re-rendered at the requested `--width` or `--scale`, never upscaled from pixels. RAW files are developed with LibRaw
(`--raw-wb camera|auto|daylight`). JPEGs go into PDFs unchanged, and graphics stay lossless. `--lossless` is exact for
WebP and HEIC (PNG, TIFF, BMP and QOI always are); AVIF gets only near-lossless (differences of a few levels).

**Edit** with a pipeline of operations, as JSON (`--ops`) or one at a time (`--op 'name key=value …'`):

```bash
python3 scripts/img_edit.py photo.jpg --out avatar.png --op 'crop aspect=1:1' --op 'resize width=512' --op 'round_corners radius=50%'
python3 scripts/img_edit.py shot.png --out shot-annotated.png --preview --ops '[
  {"op": "rect", "box": "120,80,400,60", "label": "Save button"},
  {"op": "arrow", "from": "70%,70%", "to": "52%,40%"},
  {"op": "redact", "box": "10,10,300,40"},
  {"op": "text", "text": "Figure 1", "position": "bottom", "bg": "#000000aa"}]'
python3 scripts/img_edit.py product.jpg --out product.png --op 'chroma_key color=auto tolerance=15' --op trim --op shadow
```

- Geometry: `resize` (fit, cover, fill, exact, scale, max_edge; never enlarges unless `upscale=true`), `crop` (box,
  insets, aspect, size), `trim`, `rotate` (clockwise degrees), `flip`, `pad`, `border`, `round_corners`, `shadow`.
- Tone: `adjust` (brightness, contrast, saturation, sharpness, gamma, exposure, hue, temperature), `levels`,
  `auto_contrast`, `equalize`, `grayscale`, `sepia`, `invert`, `posterize`, `threshold`, `duotone`, `replace_color`.
- Filters: `blur`, `sharpen`, `unsharp`, `median`, `pixelate`, `filter`. Add `box` or `boxes` to limit one to regions.
- Annotation: `rect`, `ellipse`, `line`, `arrow`, `polygon`, `highlight`, `callout` (numbered badge), each with an
  optional `label`; `text`; `watermark` (text or image, `tile=true`); `composite` (another image, blend modes).
- Alpha and output: `chroma_key`, `transparent`, `opacity`, `flatten`, `redact`, `set_dpi`, `strip_metadata`, `mode`,
  `quantize`.

Lengths are pixels or percentages of the image at that step. `references/edit-ops.md` lists every parameter.
A misspelled operation or parameter is an error that lists what the operation takes. In `--op`, quote values with
spaces and give lists as JSON: `--op 'text text="Figure 1: sales" size=4%'`,
`--op 'blur radius=20 boxes=["0,0,300,40","0,900,300,40"]'` (or `--op "blur boxes=['0,0,300,40']"`).

- `text` and `watermark` are 5% of the image height unless `size=` says otherwise (px or %; labels take `label_size`).
  Text or a label wider than the space left is shrunk (to 40% of its size at most), then wrapped, and the output
  says which. `fit=false` keeps the size, `max_width=80%` wraps at a width, and `\n` breaks lines.
- `redact` replaces pixels for good, and it also strips metadata so no embedded thumbnail keeps the original.
- `chroma_key` removes only background connected to the edges unless `connected=false`. `tolerance` is a colour
  distance (0-100, default 20): raise it for a noisy backdrop, lower it when pale parts of the subject go
  translucent (the output says how much it removed and softened). Only a thin band (`edge`, 2 px) is feathered. It is a
  colour key, not AI segmentation: it works on plain or green-screen backgrounds. Check the edges on black:
  `img_view.py out.png --bg black --zoom …`.
- Animated GIF/WebP/APNG inputs are edited frame by frame.

**Compose** several images:

```bash
python3 scripts/img_compose.py grid shots/*.png --out overview.png --cols 3 --title "Onboarding"
python3 scripts/img_compose.py compare before.jpg after.jpg --out before-after.jpg
python3 scripts/img_compose.py animate frames/ --out demo.gif --fps 12 --max-edge 640 --boomerang
python3 scripts/img_compose.py icons logo.svg --out-dir icons/ --preset all   # favicon.ico, PNGs, manifest, .icns, .ico
```

`icons` names the app in site.webmanifest after the file unless you pass `--name "Acme Notes"` (and `--short-name`,
`--theme-color`). `sprite` packs images and writes a JSON map (and CSS with `--css`). `tiles` splits a large image into
tiles with a JSON map of their boxes (JPEG for photos, PNG for graphics; `--to png` forces lossless).

**Optimise** for the web or email. The script tries formats and qualities and keeps the smallest result that meets
your target. It reports the SSIM against the original (1.0 = identical):

```bash
python3 scripts/img_optimize.py hero.png --out hero.webp --max-edge 1920 --min-ssim 0.97
python3 scripts/img_optimize.py photos/ -r --to email --target-kb 300 --max-edge 1600 --out-dir for-email/
```

**Fonts**:

```bash
python3 scripts/font_tool.py info Brand.woff2                 # names, licence, embedding rights, coverage, axes
python3 scripts/font_tool.py coverage Brand.otf --text "Crème brûlée — 50 €" --system
python3 scripts/font_tool.py subset Brand.ttf --text-file page.html --out brand-subset.woff2
python3 scripts/font_tool.py specimen Brand.otf --text "Quarterly report"   # a PNG to look at
```

`convert` switches between TTF, OTF, WOFF and WOFF2, TrueType and CFF outlines included (`--fast` for big fonts:
WOFF2 in seconds instead of a minute, about 10% larger). `find` searches installed fonts
by name, or with `--covers TEXT` finds fonts that have every character. `instance` makes a static font from a
variable font. Text drawn by `img_edit.py` uses `font=` (a family name, `sans`, `serif`, `mono`, or a font file). If
that font lacks some characters (CJK, symbols), an installed font that covers them is used and the output says so.

## Check

- Look at every visual result: `img_view.py out.png` (or `--preview` on img_edit and img_compose), then view_image.
  Zoom into details you changed, such as label placement, redactions and edges after `chroma_key`.
- For a change that should be invisible, or for regressions, compare: `img_compare.py before.png after.png` gives
  the changed regions as boxes, SSIM and PSNR, and a diff PNG to look at.
- Read metadata back with `img_info.py` (for example, check that GPS is gone after `--strip`).
- Find duplicates before cleaning a folder: `img_compare.py dupes photos/ -r --sheet`. Each group lists the copy to
  keep (the largest) first; every other member is byte-identical or within `--threshold` (pHash distance, default 8
  of 64) of it. Near copies can still be different shots (bursts, crops, edits), so look at every group's sheet and
  let the user decide what to delete. `--threshold 4` is stricter; `--exact-only` lists byte-identical files only.

## Rules

- Never modify the user's files. Write new files, say where they are, and never overwrite without `--force`. The scripts
  refuse to write over an input.
- Renders for looking go to `./renders/` and may be replaced. Deliverables go wherever `--out` or `--out-dir` says
  (defaults: `converted/`, `edited/`, `optimized/`).
- Photos often carry GPS coordinates and camera serial numbers. Strip metadata (`--strip`) before sharing images
  outside, unless the user wants it kept.
- Say what you could not do: a PSD's layers, a RAW file LibRaw does not know, a font without some characters, or a
  size target that was missed (the scripts report these).

## Limits

- PSD: only the flattened composite is read. Files saved without "maximize compatibility" have no composite.
- RAW: developed to 8-bit with LibRaw's defaults. Fine RAW processing needs a dedicated editor.
- Edits run in 8-bit: 16-bit and HDR sources lose precision when edited.
- SVG: rendered with resvg and the installed fonts; scripts and animations in SVG are ignored.
- TIFF output keeps only basic EXIF tags. JPEG XL, EPS and camera-specific HEIC depth maps are not supported.
- Background removal is colour-based (`chroma_key`). There is no subject segmentation and no vectorising (raster → SVG).
- Text drawn on images uses simple layout: right-to-left and complex scripts (Arabic, Hebrew, Indic) are not shaped.
- Safety caps: a .svgz that inflates past 64 MB and a WOFF/WOFF2 font that inflates past 256 MB are refused as possible
  bombs (`DESK_SVG_MAX_MB`, `DESK_FONT_MAX_MB` raise them). A PNG text chunk too big to decode is skipped with a warning.
- Neighbouring skills: PDF pages are `pdf-toolkit` (render, then come back here), video frames are `audio-video`,
  Office documents are `word-documents`, `presentations` and `spreadsheets`, unknown files are `file-inspector`.
  They may not be installed.

More: `references/edit-ops.md` (every operation), `references/formats.md` (what each format keeps: alpha, animation,
metadata, colour), `references/recipes.md` (custom scripts with the same `python3` and libraries),
`references/performance.md` (timings, caching, big files).
