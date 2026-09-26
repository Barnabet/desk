# img_edit operations

Operations run in order. Each is an object with `"op"` plus parameters, given as a JSON list (`--ops`, inline, a
`.json` file, or `-` for stdin) or one per `--op 'name key=value …'`.

In `--op`:
- Quote values with spaces: `--op 'text text="Hello world" size=4%'`. Inside double quotes, `\n` is a line break and
  `\"` a quote.
- Numbers and `true`/`false` are read as such (`width=800`, `tile=true`); everything else is a string.
- Lists and objects are JSON, and single quotes work inside them:
  `--op 'blur radius=20 boxes=["0,0,300,40","0,900,300,40"]'` or `--op "redact boxes=['10,10,300,40']"`.
- A single box can also be `box=[10,10,300,40]`.

Every operation's parameters are checked before anything runs: an unknown operation or a misspelled parameter
(`colour=`, `lable=`) is an error naming the closest match and listing what the operation takes.

**Conventions**
- **Lengths** are pixels (`120`) or percentages (`"25%"`) of the image at that step: of the width for x and widths,
  of the height for y and heights, of the short side for radii and line widths.
- **Boxes** are `"x,y,w,h"`, `"WxH+X+Y"`, `[x, y, w, h]` or `{"x":…, "y":…, "w":…, "h":…}`, in pixels or %.
- **Points** are `"x,y"` or `[x, y]`.
- **Colours** are names, `#rrggbb`, `#rrggbbaa`, `rgb(r,g,b)` or `transparent`.
- **Positions** (`position`, `anchor`): `top-left`, `top`, `top-right`, `left`, `center`, `right`, `bottom-left`,
  `bottom`, `bottom-right`, with `margin` (default 3% of the short side).
- **Coordinates** refer to the upright image, since the EXIF orientation is applied before the first operation.
  `img_view.py --grid` labels the same pixels.

## Geometry

| op | parameters |
|---|---|
| `resize` | `width`, `height` (px or %), or `size` (`"800x600"`, `"50%"`), or `scale` (`0.5`, `"50%"`), or `max_edge`; `mode`: `fit` (inside the box, default), `cover` (fill the box, crop at `anchor`), `fill` (fit, then pad to the box with `bg`), `exact` (stretch); `upscale` (default false for fit and max_edge); `filter`: lanczos, bicubic, bilinear, nearest, box |
| `crop` | `box`; or insets `left`/`top`/`right`/`bottom`; or `aspect` (`"16:9"`) with `anchor`; or `width`/`height` with `anchor`; or `trim: true` |
| `trim` | crop uniform borders: `color` (`auto` = the corners' colour or transparency, or a colour), `fuzz` (0-255, default 12), `padding` |
| `rotate` | `angle` in degrees **clockwise** (negative = counter-clockwise; 90/180/270 are lossless), `expand` (default true), `bg` (default transparent when the output has alpha, else white) |
| `flip` | `direction`: `horizontal` (mirror), `vertical` or `both` |
| `pad` (`canvas`) | `all` or `top`/`right`/`bottom`/`left`; or `width`/`height` (or `size`) with `anchor` to place the image on a canvas; or `aspect` to pad to a ratio; `bg` |
| `border` | `width`, `color` |
| `round_corners` | `radius` (default 8%; 50% makes a circle or an ellipse), `circle: true` |
| `shadow` | drop shadow: `blur` (2%), `x`/`y` offset (1.5%), `color`, `opacity` (0.5), `bg` (default transparent) |
| `auto_orient` | applies the EXIF orientation (done automatically unless `--no-orient`) |

## Tone and colour

| op | parameters |
|---|---|
| `adjust` | any of `brightness`, `contrast`, `saturation`, `sharpness` (factors: 1 = unchanged, `1.2` or `"+20%"`), `gamma` (>1 brightens midtones), `exposure` (stops), `hue` (degrees), `temperature` (-100 cool … 100 warm). The single ops `brightness`, `contrast`, … take `value` |
| `levels` | `black` (0-255), `white`, `gamma`, `out_black`, `out_white` |
| `auto_contrast` | `cutoff` (% clipped at each end, default 0.5), `preserve_tone` (default true) |
| `equalize` | histogram equalisation of brightness only (hues kept) |
| `grayscale` | to L (or LA with alpha) |
| `sepia` | `strength` 0-1 |
| `invert` | colours only; alpha is kept |
| `posterize` | `bits` 1-8 |
| `solarize` | `threshold` |
| `threshold` | `value` 0-255 or `auto` (Otsu): pure black and white, for scans and line art |
| `duotone` | `black`, `white`, optional `mid` colours |
| `replace_color` | `from`, `to`, `tolerance` (0-255 per channel) |

## Filters

Every filter takes `box` or `boxes` to apply only inside regions, for example to blur faces or number plates.

| op | parameters |
|---|---|
| `blur` | `radius` (default 1% of the short side) |
| `box_blur` | `radius` |
| `sharpen` | `amount` (1 = moderate) |
| `unsharp` | `radius` (2), `percent` (150), `threshold` (3) |
| `median` | `size` (odd, default 3): removes speckle noise |
| `pixelate` | `size` (block size, default 2%) |
| `filter` | `name`: edge_enhance, edge_enhance_more, emboss, contour, find_edges, smooth, smooth_more, detail, sharpen |
| `redact` | `box`/`boxes`, `style`: `fill` (default, `color` black), `pixelate` or `blur` (both heavy). Pixels are replaced, and metadata is stripped from the output so no EXIF thumbnail keeps the original |

## Annotation

Shapes can be separate ops (`{"op": "rect", …}`) or listed in `{"op": "draw", "shapes": [{"type": "rect", …}, …]}`.
They are antialiased. The default colour is a strong pink-red (`#ff2d55`), and the default line width scales with the
image.

| shape | parameters |
|---|---|
| `rect` | `box`, `color`, `width`, `fill` (a colour, may be translucent `#ff000040`), `radius`, `label` |
| `ellipse` | `box`, `color`, `width`, `fill`, `label` |
| `line` | `points` (≥2) or `from`/`to`, `color`, `width`, `label` |
| `arrow` | `from`/`to` (or `points`, arrowhead at the last), `color`, `width`, `head` (length), `label` (at the start) |
| `polygon` | `points`, `color`, `width`, `fill` |
| `highlight` | `box`, `color` (default yellow), `strength` (0.55): a marker-pen tint that keeps text readable |
| `callout` | `at` (point), `number` (or `text`), `size`, `color`: a numbered badge for step-by-step screenshots |

Label options on shapes: `label`, `label_position` (`above` (default; inside the corner when there is no room), `below`,
`center`), `label_size` (default about 2.4% of the short side), `label_color`, `label_align`, `font`. A label wider
than the image is shrunk, then wrapped, like text.

| op | parameters |
|---|---|
| `text` | `text` (`\n` for new lines), `size` (px or % of the height, default 5%), `fit` (`auto` default: shrink to fit the width, down to 40% of `size` and never under 12 px, then wrap; `wrap`: wrap only; `false`: keep the size even if it is cut off), `font` (a family name, `sans`, `serif`, `mono`, or a file), `bold`, `italic`, `color`, `stroke_width`, `stroke_color`, `bg` (a box colour, may be translucent), `padding`, `radius`, `align` (left, center, right), `max_width` (wraps at that width, px or %), `line_spacing`, `opacity`, `angle`; placement by `position` + `margin`, or `x`/`y` (or `at`) + `anchor` |
| `watermark` | `text` or `image` (any format; SVG renders sharp), `opacity` (0.35), `position` or `tile: true` (diagonal repeat; `angle` 30, `spacing`), for images `width` (default 20%), for text `size` (5%, 4% tiled), `color`, `font`, `fit`, `align` |
| `composite` | `image` (a path), `width`/`height`/`scale`, `opacity`, placement as for text (default center), `blend`: normal, multiply, screen, overlay, darken, lighten, difference, add, subtract, soft_light, hard_light |

## Transparency and output

| op | parameters |
|---|---|
| `chroma_key` | `color` (`auto` = the median border colour, or a colour), `tolerance` (0-100, default 20: a colour distance in Lab, where lightness counts half, so a pale subject on white is told apart better than by RGB), `feather` (0-100, default 10: how far past the tolerance the soft edge reaches), `edge` (px, default 2: only this band around the removed background gets partial transparency; the subject's body stays opaque), `connected` (default true: only background connected to the edges, so the same colour inside the subject stays), `despill` (default true) |
| `transparent` | `color`, `tolerance` (default 0): every pixel of that colour becomes transparent |
| `opacity` | `value` 0-1 |
| `flatten` | `color` (default white): composite transparency onto a colour |
| `mode` | `mode`: RGB, RGBA, L, LA, 1, P, CMYK |
| `quantize` | `colors` (2-256), `dither` (default true) |
| `set_dpi` | `dpi`: written to JPEG, PNG, TIFF, WebP and BMP |
| `strip_metadata` | drops EXIF (and GPS), XMP and comments from the output |

Text placement reports: when text, a label or a watermark is shrunk or wrapped to fit, the output says so (for
example `text: shrunk from 78 to 37 px to fit the width`). `chroma_key` reports how much it made transparent and how
much it softened, and warns when almost everything went (a tolerance too high, or a subject touching the edges).
Tuning it: lower `tolerance` (10-15) when pale parts of the subject turn translucent, raise it (25-35) for a noisy or
unevenly lit backdrop, set `feather` 0-5 for hard edges. Check the result on black: `img_view.py out.png --bg black`,
then `--zoom` on the edges.

## Output rules

- The output format comes from `--out`'s extension, or `--to` for batches (default: each input's format; SVG, PSD and
  RAW inputs become PNG, or JPEG for RAW).
- Transparency is kept where the format allows it. JPEG, BMP and PDF flatten it onto white.
- An image with no alpha comes back without alpha unless an operation needs it (`round_corners`, `chroma_key`,
  `opacity`, `transparent`, `shadow`).
- Metadata is kept (EXIF with the orientation reset to 1, XMP and the ICC profile) unless `--strip`, `strip_metadata` or
  `redact` is used.
- Animated inputs are edited frame by frame and keep their timings. `--first-frame` edits only the first frame.

## Recipes

```json
[{"op": "resize", "width": 1080, "height": 1080, "mode": "cover"},
 {"op": "text", "text": "SUMMER SALE", "position": "top", "size": "9%", "bold": true, "color": "white", "stroke_width": "8%"}]
```

```json
[{"op": "callout", "at": "12%,20%", "number": 1}, {"op": "callout", "at": "55%,62%", "number": 2},
 {"op": "rect", "box": "40%,55%,30%,14%", "color": "#1e88e5", "label": "Then press Save"}]
```

```json
[{"op": "blur", "radius": 25, "boxes": ["310,120,90,90", "620,140,85,95"]}, {"op": "strip_metadata"}]
```

```json
[{"op": "chroma_key", "color": "auto", "tolerance": 12}, {"op": "trim", "padding": "4%"}, {"op": "pad", "aspect": "1:1"}, {"op": "shadow"}]
```
