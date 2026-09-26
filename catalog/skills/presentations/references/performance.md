# Performance on big decks

Measured on an Apple M2 (8 cores, 8 GB) in Desk's sandbox with `DESK_MAX_WORKERS=2`, on a generated 300-slide
deck (840 KB: bullets with notes, line charts, 7-row tables, KPI cards, timelines and section slides). "Cold" is
the first call on the file with an empty cache; "cached" is the same call again. Times are wall-clock and include
Python start-up (about 0.05 s).

| command | cold | cached |
|---|---|---|
| `pptx_create.py big.pptx --input big.md` (300 slides, every text box measured) | 4.0 s | (not cached) |
| `pptx_info.py big.pptx` | 0.31 s | 0.06 s |
| `pptx_read.py big.pptx` (the map: over 50 slides) | 0.14 s | 0.06 s |
| `pptx_read.py big.pptx --find "R3"` | 0.14 s | 0.06 s |
| `pptx_read.py big.pptx --slides 150-160` | 0.28 s | 0.07 s |
| `pptx_read.py big.pptx --full --max-chars 0` (48,000 characters) | 1.03 s | 0.11 s |
| `pptx_read.py big.pptx --format csv --max-chars 0` (every table and chart) | 1.00 s | 0.10 s |
| `pptx_read.py big.pptx --format spec --out big.spec.json` | 0.76 s | 0.76 s (writes pictures) |
| `pptx_lint.py big.pptx` | 0.94 s | 0.06 s |
| `pptx_render.py big.pptx --sheet-only` (15 sheets, built-in) | 1.87 s | 0.66 s |
| `pptx_render.py big.pptx` (300 PNGs at 1568 px, built-in) | 2.31 s | 0.17 s |
| `pptx_render.py big.pptx --slides 150` (built-in) | 0.34 s | 0.09 s |
| `pptx_convert.py big.pptx big.pdf` (built-in, vector) | 1.24 s | 0.06 s |
| `pptx_convert.py big.pptx big.md` | 1.02 s | 0.11 s |
| `pptx_render.py big.pptx --engine libreoffice` (300 PNGs) | 7.19 s | 0.17 s |
| `pptx_render.py big.pptx --engine libreoffice --sheet-only` | 5.97 s | 1.17 s |
| `pptx_render.py big.pptx --engine libreoffice --slides 150` | 3.97 s | 0.08 s |
| `pptx_convert.py big.pptx big.pdf` (LibreOffice) | 3.87 s | 0.06 s |
| `pptx_edit.py big.pptx --out v2.pptx --ops '[{"op": "replace", …}]'` | 3.07 s | (not cached) |
| `pptx_create.py v2.pptx --spec big.spec.json` (rebuild from the spec) | 3.83 s | (not cached) |

What the numbers mean in practice:

- **Start with the map.** On a 300-slide deck the map, `--find` and `--slides` answer in well under a second, and
  the second call is almost free. Read the whole deck (`--full`) only when you need all of it.
- **What is shared.** `pptx_read` and `pptx_convert` (md, txt, json) share the parsed slides; the map (also used
  by `--find` and the render labels), `pptx_info` and `pptx_lint` keep their own results. One slide's render is
  reused by any later render of that slide at the same size. LibreOffice's PDF of the deck is reused by renders
  and PDF conversions, so only the first LibreOffice call on a file pays for its start-up.
- **Edits and builds are not cached.** They write a new file, which starts cold: render and lint it once, then
  iterate on the slides you change (`--slides`).
- **Scaling is linear.** Create, render and lint times grow with the number of slides and the amount of text,
  not faster. A 100-slide deck takes about a third of the times above.
- **Memory.** Peak for the largest process (often the Typst measurer or renderer) on the 300-slide deck: create
  510 MB, render 270 MB, lint 260 MB, read 90 MB. The built-in renderer draws at most `DESK_MAX_WORKERS` chunks at
  once.

`--no-cache` (or `DESK_NO_CACHE=1`) always gives the cold time. The cache is capped at 1 GB by default
(`DESK_FILE_CACHE_MB`), and stops storing when the disk is nearly full.
