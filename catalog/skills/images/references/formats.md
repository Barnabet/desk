# Formats

## What each format keeps

| Format | Read | Write | Alpha | Frames | Metadata written | Notes |
|---|---|---|---|---|---|---|
| PNG | ✓ | ✓ (`png`, `apng`) | ✓ | APNG | EXIF, XMP, ICC, DPI | lossless; 16-bit greyscale kept by convert; a text chunk too big to decode (a zTXt bomb) is skipped with a warning |
| JPEG | ✓ | ✓ | – (flattened on `--bg`) | – | EXIF, XMP, ICC, DPI | quality 88 by default; 4:4:4 chroma at quality ≥ 90; progressive and optimised up to 50 MP, baseline above (a progressive encoder holds the whole image's coefficients: about 1 GB at 200 MP) |
| WebP | ✓ | ✓ | ✓ | ✓ | EXIF, XMP, ICC | lossy (default 85) or `--lossless` (exact); frame timings are read from the file's ANMF chunks |
| AVIF | ✓ | ✓ | ✓ | ✓ | EXIF, XMP, ICC | small files; quality 65 by default; slower to encode; `--lossless` is only near-lossless (4:4:4 at quality 100, differences up to about 3 levels): use WebP or PNG for exact pixels |
| HEIC/HEIF | ✓ | ✓ | ✓ | first image | EXIF, XMP, ICC | iPhone photos; pillow-heif (libheif, x265 encoder); `--lossless` is exact (4:4:4, RGB matrix; files are large) |
| GIF | ✓ | ✓ | 1-bit | ✓ | – | 256 colours: a shared palette from all frames avoids flicker |
| TIFF | ✓ | ✓ | ✓ | pages | basic tags (camera, date, artist, copyright), XMP, ICC, DPI | LZW by default; `--tiff-compression deflate|jpeg|none`; macOS multi-resolution TIFFs (the same picture at 1× and 2×) open at the largest size |
| BMP | ✓ | ✓ | – | – | DPI | |
| ICO | ✓ (largest) | ✓ multi-size | ✓ | sizes | – | `--sizes 16,32,48,256`; non-square images are padded |
| ICNS | ✓ (largest) | ✓ | ✓ | sizes | – | all ten macOS sizes, 16×16 to 512×512@2x (16 and 32 px also as classic RGB + mask entries), each rendered from the source (SVG stays sharp); opens in Finder and iconutil |
| PDF | – (pdf-toolkit renders PDFs) | ✓ | – | pages | – | one image per page; JPEGs embedded unchanged; graphics lossless (Flate) |
| JPEG 2000, TGA, QOI, PPM | ✓ | ✓ | varies | – | – | |
| PSD | composite only | – | ✓ | – | – | layers are listed by `img_info.py` but not rendered separately |
| Camera RAW | ✓ (LibRaw) | – | – | – | EXIF carried to outputs | CR2, CR3, NEF, NRW, ARW, SRF, SR2, DNG, ORF, RW2, RAF, PEF, SRW, 3FR, IIQ, … |
| SVG / SVGZ | ✓ (resvg) | – | ✓ | – | – | rendered at any size; text uses installed fonts; UTF-8, UTF-16 and Latin-1 files; a .svgz that inflates past 64 MB is refused as a possible gzip bomb |
| MPO, DDS, PCX, SGI, XBM, BLP | ✓ | – | | | | read through Pillow |

A multi-frame source written to a single-frame format gives the first frame, and the output says so. Pick another
frame with `--page N`, or write every frame with `--split`. Only animations (GIF, APNG, WebP, AVIF) stay animated:
the pages of a TIFF are kept in TIFF and PDF outputs, never turned into an animated GIF or WebP.

## Orientation

Cameras store pixels as the sensor saw them and record the rotation in the EXIF `Orientation` tag (1-8). Every script
applies it first: `img_view` shows the upright image, `img_edit` coordinates refer to it, and outputs are written upright
with `Orientation = 1`, so no viewer rotates them twice. `--no-orient` keeps the stored pixels. `img_info` reports the
tag and, when it swaps width and height, the stored size.

## Colour

- **ICC profiles** are kept by default. `--srgb` converts pixels to sRGB, and `--strip` does too, so colours stay right
  once the profile is gone. Formats without profiles (GIF, BMP, ICO, ICNS, PDF pages, TGA, QOI) always get sRGB pixels.
- **CMYK** JPEGs and TIFFs are converted through their embedded profile when present. Adobe's inverted CMYK is handled.
- **16-bit** and float images (PNG, TIFF, scientific data) are scaled to 8-bit for viewing and editing.
  `img_view --stretch` stretches data that uses only part of the range (depth maps, 12-bit data).
- **Wide gamut** (Display P3 from iPhones, Adobe RGB) previews are converted to sRGB, so view_image shows true colours.

## Transparency

`img_view` shows transparency as a grey checkerboard, so you can see what is transparent. Use `--bg white` to hide it.
Formats without alpha (JPEG, BMP, PDF) flatten onto `--bg` (default white). GIF keeps 1-bit transparency: pixels under
50% opacity become transparent.

## Metadata and privacy

- **EXIF** holds the camera, lens, exposure, date and time zone, the orientation and often **GPS coordinates**.
  `img_info` decodes GPS to latitude and longitude (plus altitude and direction).
- **XMP** holds titles, keywords, ratings and editing history. `img_info` shows the useful fields; `--exif all` shows
  everything.
- **IPTC** (JPEG and TIFF) holds captions, keywords, bylines and copyright.
- To remove all of them: `img_convert.py … --strip`, `img_optimize.py` (strips by default) or the `strip_metadata` op.
  `redact` always strips, because an EXIF thumbnail can keep an un-redacted copy.

## HEIC and AVIF

HEIC decoding and encoding go through libheif (pillow-heif). AVIF uses Pillow's built-in libavif. Both keep EXIF, XMP and
ICC. HEIC depth maps, auxiliary images and burst sequences are not read: only the primary image is. Live Photo video is
a separate .mov file.

## Camera RAW

`img_view` shows the camera's embedded JPEG preview when it is big enough: it is instant and has the camera's own
colours. `--raw-develop` renders from sensor data instead. `img_convert` always develops with LibRaw: demosaicing, camera
white balance (`--raw-wb auto|daylight`), auto brightness (`--raw-bright 1.3` for brighter output), 8-bit sRGB output.
`--raw-half` develops at half size, 4× faster.
`img_info` reads EXIF from the TIFF structure (CR2, NEF, ARW, DNG, ORF, RW2, PEF), from the CMT boxes of CR3, or from
the embedded preview.

## SVG

resvg renders SVG 1.1 and most of SVG 2 (gradients, filters, masks, clip paths, text with system fonts, embedded
images). Scripts, animations and external web resources are ignored. Relative image links resolve next to the SVG.
`img_info` lists the SVG's text, fonts, external references and scripts, so you can check an SVG without rendering it.

## Fonts

`font_tool.py info` decodes `fsType`, the embedding permissions a font's licence sets:
- **installable**: no restriction.
- **restricted licence**: must not be embedded in documents, and subsetting or converting such a font may breach its licence.
- **preview & print**: may be embedded read-only.
- **editable**: may be embedded in editable documents.
- **no subsetting**: the whole font must be embedded.

Tell the user when a font is restricted before you subset, convert or embed it.

WOFF and WOFF2 fonts are checked before fontTools parses them: the sizes their tables declare, then a streaming
inflation that stops at the declared size (at most 256 MB). A font that would inflate further is refused as a possible
bomb, and a damaged font gives `<name>: damaged or unsupported font (…)`.

WOFF2 is the web format (Brotli, typically 30% smaller than WOFF). Subsetting to the characters a page uses often
shrinks a font by 90% or more. Keep `--features '*'` so kerning and ligatures survive.

## Limits you can raise

| Variable | Default | What it limits |
|---|---|---|
| `DESK_MAX_PIXELS` | 600000000 | the largest image opened (Pillow's decompression-bomb guard) |
| `DESK_SVG_MAX_MB` | 64 | the markup of an SVG, or what a .svgz inflates to |
| `DESK_FONT_MAX_MB` | 256 | what a WOFF or WOFF2 font inflates to |
| `DESK_TILE_MIN_MP` | 50 | zooms into images of at least this many megapixels build the cached tile store |
| `DESK_FONT_DIRS` | – | extra font folders (separated by the OS path separator) for `font=` and `font_tool.py find` |

Raise a limit only for a file you trust. Files other skills handle get a pointer in the error: a PDF names pdf-toolkit,
a video audio-video, a zip archives, an Office file its skill, and anything else file-inspector.
