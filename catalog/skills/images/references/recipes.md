# Recipes: custom scripts with the same Python

When no script does what you need, write a small one and run it with the same `python3`. It has Pillow, pillow-heif,
rawpy, resvg-py, fontTools (with Brotli), NumPy and pyoxipng. Put it in the workspace, not in the skill folder. You can
reuse the skill's helpers: `SKILL_DIR` points to the skill during `skill_run`.

```python
import os, sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.environ["SKILL_DIR"], "scripts"))
from _img import load, to_view, save_image, SaveOpts, expand_inputs, parse_color
ld = load(Path("IMG_0042.HEIC"))          # any format, upright; ld.img is a PIL image, ld.info has exif/icc
```

Always write to new files, and look at visual results with `img_view.py` and view_image.

## Open anything with Pillow

```python
from PIL import Image, ImageOps
import pillow_heif
pillow_heif.register_heif_opener()          # adds .heic/.heif to Image.open
im = ImageOps.exif_transpose(Image.open("photo.heic"))   # upright
```

- RAW: `rawpy.imread(path).postprocess(use_camera_wb=True)` returns an RGB NumPy array; `Image.fromarray(...)`.
- SVG: `resvg_py.svg_to_bytes(svg_path="a.svg", width=1024)` returns PNG bytes.

## Rename photos by the date they were taken

```python
from pathlib import Path
from PIL import Image
for p in sorted(Path("photos").glob("*.jpg")):
    taken = Image.open(p).getexif().get_ifd(0x8769).get(0x9003)   # "2026:07:14 18:30:05"
    if taken:
        print(p.name, "→", taken.replace(":", "-", 2).replace(" ", "_").replace(":", "") + p.suffix)
```

Print the plan first, show it to the user, then copy the files to new names; never rename in place without asking.

## Export GPS positions to CSV (for a map)

```bash
python3 scripts/img_info.py photos/ -r --no-stats --format json > info.json
```

```python
import csv, json
rows = [(r["file"], r["gps"]["lat"], r["gps"]["lon"], r.get("exif", {}).get("taken", "")) for r in json.load(open("info.json")) if r.get("gps")]
csv.writer(open("positions.csv", "w", newline="")).writerows([("file", "lat", "lon", "taken"), *rows])
```

## Pick the sharpest photo of a burst

`img_info.py burst/ --stats --format json` reports a `sharpness` score for each photo (the variance of the Laplacian at
1024 px). A higher score means sharper. Compare scores within a burst, not across different scenes. Then look at the top
two with `img_view.py`.

## Crop many screenshots to the same region

```bash
python3 scripts/img_view.py shots/first.png --grid          # find the box once
python3 scripts/img_edit.py shots/ --out-dir cropped/ --op 'crop box=0,64,1440,836'
```

## Side-by-side of every "before/after" pair

```bash
for f in before/*.png; do n=$(basename "$f"); python3 scripts/img_compose.py compare "$f" "after/$n" --out "pairs/$n"; done
```

## Visual regression check

```bash
python3 scripts/img_compare.py expected.png actual.png --threshold 0 --format json   # identical, changed_pct, regions
```

`--threshold 0` counts any pixel difference. The default (10) ignores JPEG and antialiasing noise. SSIM above 0.99 is
visually identical, 0.95-0.99 is a slight difference, and below 0.9 is clearly different.

## Social media sizes

| Target | Size | Command |
|---|---|---|
| Instagram square | 1080×1080 | `--op 'resize width=1080 height=1080 mode=cover'` |
| Instagram portrait | 1080×1350 | `--op 'resize width=1080 height=1350 mode=cover'` |
| Story / Reel | 1080×1920 | `--op 'resize width=1080 height=1920 mode=cover'` |
| Open Graph / link preview | 1200×630 | `--op 'resize width=1200 height=630 mode=cover'` |
| YouTube thumbnail | 1280×720 | `--op 'resize width=1280 height=720 mode=cover'` |

Use `anchor=top` (or another position) on `cover` to keep a face or a headline in frame, then check the result with
img_view.

## Work on pixels with NumPy

```python
import numpy as np
from PIL import Image
a = np.asarray(Image.open("in.png").convert("RGB")).astype(np.int16)
mask = (a[..., 0] > 200) & (a[..., 1] < 80)            # strongly red pixels
print("red pixels:", int(mask.sum()))
a[mask] = (0, 120, 255)
Image.fromarray(a.clip(0, 255).astype(np.uint8)).save("out.png")
```

## Fonts with fontTools

```python
from fontTools.ttLib import TTFont
f = TTFont("Brand.otf")
print(f["name"].getDebugName(1), f["OS/2"].usWeightClass, len(f.getBestCmap()))
print(sorted({fr.FeatureTag for fr in f["GSUB"].table.FeatureList.FeatureRecord}) if "GSUB" in f else [])
```

Subsetting with options the script does not expose: `from fontTools import subset` and `subset.main([...])`, with the same
arguments as the `pyftsubset` command (for example `--layout-features+=ss01 --no-hinting`).
