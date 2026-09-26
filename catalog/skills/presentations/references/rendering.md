# Rendering, fonts and fidelity

## Engines

`pptx_render.py` and `pptx_convert.py … .pdf` pick an engine:

| engine | when | fidelity |
|---|---|---|
| LibreOffice | installed (found on PATH, in /Applications or Program Files, or via `DESK_SOFFICE`) | what PowerPoint shows, give or take fonts; hidden slides included |
| built-in | no LibreOffice, or `--engine builtin` | close approximation, drawn with Typst: fast and needs nothing installed |

`DESK_SOFFICE=none` hides LibreOffice; `DESK_SOFFICE=/path/to/soffice` points at one. Every render says which engine
ran, and a built-in render made while LibreOffice is installed says `--engine libreoffice` would be exact. PNGs are
1568 px on the long edge by default (`--width` 64–8000), which suits view_image.

## Contact sheets

`--sheet` (with the PNGs) or `--sheet-only` (just the sheets) writes one sheet per 20 slides: `deck-sheet.png`, or
`deck-sheet-1.png`, `deck-sheet-2.png` … for bigger decks, each titled with its slide range. Thumbnails are 360 px
wide in a 4-column grid (1500 px sheets), readable with view_image; `--per-sheet` (1–200) trades size for count.
Each thumbnail is labelled with its number and title, and hidden slides are marked. `--sheet-only` renders the
slides at thumbnail size, so it is the fastest way to see a whole deck.

## Cache

Renders are cached per file content (a fingerprint of the bytes), engine, size and installed fonts: rendering the
same deck again, or other slides of it, only draws what is missing, and the result says how many slides came from
the cache. The PDF LibreOffice makes of a deck is cached too, and reused by `pptx_convert … .pdf` and by later
renders. `pptx_read`, `pptx_info`, `pptx_lint` and `pptx_convert` (md, txt, json, pdf) cache their parsed results
the same way. The cache lives in Desk's file cache (`DESK_FILE_CACHE`), is capped in size (`DESK_FILE_CACHE_MB`,
default 1024 MB), and is skipped with `--no-cache` or `DESK_NO_CACHE=1`. An edited deck is a new file with its own
entries.

## What the built-in renderer draws

- **Inheritance:** the slide's placeholders take their position, size, text styles, bullets, fonts and colours
  from the layout, the master, the master's title/body/other styles and the presentation defaults. It also
  applies the theme colour map (dark masters), the theme fonts (+mj-lt/+mn-lt) and the theme style matrix
  (fillRef, lnRef, fontRef).
- **Backgrounds:** solid, linear and radial gradients (with focus), picture (stretched or tiled) and pattern (as
  its blend colour). Shapes on the master and layout are drawn unless the slide or layout hides them.
- **Shapes:**
  - Exact: rect, rounded, ellipse, triangles, diamond, parallelogram, trapezoid, pentagon to dodecagon, chevron
    and home plate, the four arrows and the double arrow, stars, plus, snipped corners, frame, can, sun and moon.
  - Approximate: callouts, flowchart shapes, wave and document; other presets fall back to a rectangle.
  - Custom geometry (move, line, cubic, quadratic, arc).
  - Rotation and flips, fills (solid, gradient, picture, pattern), and outlines with width, dash and arrowheads.
- **Text:** fonts and sizes, bold, italic, underline, strike, caps, super/subscript, highlight, colours and
  hyperlink colour (also inside table cells). A word wider than its box is broken mid-word, as PowerPoint does,
  so an overflowing KPI value looks as broken as it will in PowerPoint. Also alignment, justification, indents, hanging bullets (characters, Wingdings mapped to
  Unicode, auto-numbering), line and paragraph spacing, insets, vertical anchoring, vertical text, no-wrap text,
  normAutofit font scale and slide-number fields.
- **Pictures:** crop (including negative crop), SVG versions when present, and rounded or ellipse frames.
  Pictures in other preset shapes are drawn clipped to the shape. BMP, TIFF and WebP are converted; EMF and WMF
  become grey boxes.
- **Tables:**
  - Cell fills and borders, merged cells, cell margins and anchoring.
  - The deck's own table styles, and the built-in Medium Style 2 (all accents), Light Style 1 and No Style grid.
    Other style GUIDs are drawn like Medium Style 2 Accent 1.
  - Rows grow to fit their text.
- **Charts:**
  - Types: clustered, stacked and 100% column and bar (in PowerPoint's category and legend order), line, area,
    pie, doughnut, scatter and radar.
  - Details: series and point colours, titles (including the automatic series-name title), legends, gridlines,
    value axes with rounded ticks, and data labels.
- **SmartArt:** drawn from the cached drawing PowerPoint stores with it, text included.
- **Groups:** nested groups with their child coordinate spaces.
- **Hidden shapes:** skipped.

Not drawn:
- Shadows, glow, reflection, soft edges and 3D.
- WordArt warps, picture recolouring and transparency, and the newer chartex charts.
- Ink, video frames (the poster picture is shown when there is one), and OLE objects without a preview picture.

## Fonts

Decks name fonts the machine may not have (Calibri, Aptos, Segoe UI…). The built-in renderer and the text
measurer use the same substitution:

- **Stack.** The named font if installed, else a metric-compatible or look-alike font (Calibri → Carlito →
  Helvetica Neue, Arial; Cambria → Caladea; Segoe UI/Aptos → Helvetica Neue, Arial …), else a sans, serif or
  mono stack.
- **Weights.** Family names with a weight ("Arial Black", "Segoe UI Semibold") become the base family at that
  weight.
- **Width.** When a substitute is wider or narrower, a small letter-spacing correction keeps line breaks close to
  PowerPoint's.
- **Fallback for missing characters.** CJK, symbol and emoji fonts come after the main font, so those characters
  still draw.
- **Line pitch.** It follows each font's "single" spacing in PowerPoint (Calibri 1.22 em, Arial 1.15 em, …).

The font list is cached in the temp folder, keyed by the state of the font folders. Fonts installed later are
picked up.

## Text measurement (pptx_create, pptx_lint)

Text is laid out with the same Typst engine the renderer uses: one compile for the whole deck, using the fonts
above.

- **pptx_create** shrinks a text box in steps down to its minimum: 14 pt for body text and 22 pt for titles by
  default. It grows sparse bullet slides by up to 30%. Bullet lists that still overflow are split at top-level
  items into continuation slides ("(cont.)", or "(2/3)" and so on) of at most 80 words each. It also sizes table
  fonts and column widths.
- **Words wider than their box.** PowerPoint breaks such a word mid-word (`$10.4` / `M`). pptx_create shrinks the
  text until the widest word fits; pptx_lint reports it as an overflow error naming the word.
- **pptx_lint** compares each text frame's measured height with its box, and reports the height needed and the
  height available.

Measurement matches PowerPoint within a line or so when the deck's fonts are installed. With substitute fonts, a
line can break differently, so leave some slack and always look at a render.

## Performance

Built-in renders of 12 slides or more are split across processes (one per core minus one, at most 8, capped by
`DESK_MAX_WORKERS`), one Typst compile per chunk. A 300-slide deck renders in about 2.3 s cold and 0.2 s from the
cache; LibreOffice adds its start-up (about 7 s for 300 PNGs). Single slides (`--slides 3`) render in about 0.3 s.
Full timings for every script, cold and cached: `performance.md`.
