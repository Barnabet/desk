"""The img_edit operations: each takes a PIL image and a dict of parameters and returns a new image.

Lengths accept pixels (120) or percentages ('25%' of the image's width or height at that step of the pipeline).
Colours accept names, '#rrggbb', '#rrggbbaa', 'rgb(…)' or 'transparent'. Every operation is listed in OPS; see
references/edit-ops.md for the full parameter reference.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Callable

from _common import SkillError, UsageError
from _img import anchor_frac, flatten, has_alpha, load, normalize, parse_box, parse_color, parse_len, parse_point, pil, place, uses_alpha

Op = Callable[[Any, dict[str, Any], dict[str, Any]], Any]
OPS: dict[str, Op] = {}
ALIASES = {
    "orient": "auto_orient", "autoorient": "auto_orient", "scale": "resize", "thumbnail": "resize", "mirror": "flip", "canvas": "pad",
    "extend": "pad", "round": "round_corners", "rounded": "round_corners", "gray": "grayscale", "greyscale": "grayscale", "grey": "grayscale",
    "negate": "invert", "gaussian_blur": "blur", "autocontrast": "auto_contrast", "normalize": "auto_contrast", "annotate": "draw",
    "overlay": "composite", "paste": "composite", "remove_background": "chroma_key", "remove_bg": "chroma_key", "chromakey": "chroma_key",
    "make_transparent": "transparent", "dpi": "set_dpi", "strip": "strip_metadata", "trim_borders": "trim", "autocrop": "trim",
    "colorize": "duotone", "tint": "duotone", "background": "flatten", "drop_shadow": "shadow", "rectangle": "rect", "circle": "ellipse",
    "box": "rect", "label": "text", "caption": "text", "censor": "redact", "hide": "redact",
}
SHAPES = ("rect", "ellipse", "line", "arrow", "polygon", "highlight", "callout")


def op(name: str) -> Callable[[Op], Op]:
    def deco(fn: Op) -> Op:
        OPS[name] = fn
        return fn

    return deco


def canonical(name: str) -> str:
    key = str(name).strip().lower().replace("-", "_").replace(" ", "_")
    key = ALIASES.get(key, key)
    if key not in OPS and key not in SHAPES:
        raise UsageError(f"unknown operation '{name}'. Known: {', '.join(sorted(set(OPS) | set(SHAPES)))}")
    return key


_PLACE = ("x", "y", "at", "anchor", "margin", "position")
_LABEL = ("label", "label_position", "label_size", "label_color", "label_align", "font")
_TEXT = ("text", "size", "font_size", "font", "bold", "italic", "max_width", "fit", "stroke_width", "stroke", "stroke_color", "line_spacing", "align",
         "bg", "background", "padding", "radius", "color", "opacity", "angle")
_FILTER = ("box", "boxes")
#: The parameters each operation reads; anything else is a typo the agent must hear about (it would be ignored).
PARAMS: dict[str, tuple[str, ...]] = {
    "auto_orient": (),
    "resize": ("width", "height", "size", "scale", "value", "max_edge", "mode", "upscale", "filter", "anchor", "bg"),
    "crop": ("box", "left", "top", "right", "bottom", "aspect", "anchor", "width", "height", "trim", "auto", "fuzz", "tolerance", "color", "padding"),
    "trim": ("color", "fuzz", "tolerance", "padding"),
    "rotate": ("angle", "degrees", "expand", "bg"),
    "flip": ("direction", "axis"),
    "pad": ("all", "padding", "top", "right", "bottom", "left", "width", "height", "size", "aspect", "anchor", "bg"),
    "border": ("width", "size", "color"),
    "round_corners": ("radius", "circle"),
    "shadow": ("blur", "x", "y", "offset_x", "offset_y", "color", "opacity", "bg"),
    "adjust": ("brightness", "contrast", "saturation", "sharpness", "gamma", "exposure", "hue", "temperature"),
    "grayscale": (), "invert": (), "equalize": (), "strip_metadata": (),
    "sepia": ("strength",),
    "posterize": ("bits",),
    "solarize": ("threshold",),
    "threshold": ("value", "threshold"),
    "duotone": ("black", "dark", "white", "light", "color", "mid"),
    "levels": ("black", "low", "white", "high", "gamma", "out_black", "out_white"),
    "auto_contrast": ("cutoff", "preserve_tone"),
    "replace_color": ("from", "to", "tolerance"),
    "blur": ("radius",) + _FILTER, "box_blur": ("radius",) + _FILTER, "sharpen": ("amount",) + _FILTER,
    "unsharp": ("radius", "percent", "threshold") + _FILTER, "median": ("size",) + _FILTER, "pixelate": ("size", "block") + _FILTER,
    "filter": ("name", "filter") + _FILTER,
    "redact": ("box", "boxes", "style", "color"),
    "chroma_key": ("color", "tolerance", "feather", "edge", "connected", "despill"),
    "transparent": ("color", "tolerance"),
    "opacity": ("value", "opacity"),
    "flatten": ("color", "bg"),
    "mode": ("mode", "value", "bg", "colors"),
    "quantize": ("colors", "dither"),
    "set_dpi": ("dpi", "value"),
    "text": _TEXT + _PLACE,
    "watermark": ("text", "image", "opacity", "width", "scale", "angle", "tile", "spacing", "size", "color", "font", "bold", "stroke_width", "stroke_color", "fit", "align") + _PLACE,
    "composite": ("image", "width", "height", "scale", "opacity", "blend", "mode") + _PLACE,
    "draw": ("shapes",),
    "rect": ("box", "rect", "color", "width", "fill", "radius") + _LABEL,
    "ellipse": ("box", "rect", "color", "width", "fill") + _LABEL,
    "highlight": ("box", "rect", "color", "strength", "width") + _LABEL,
    "line": ("points", "from", "to", "color", "width") + _LABEL,
    "arrow": ("points", "from", "to", "color", "width", "head", "fill") + _LABEL,
    "polygon": ("points", "color", "width", "fill") + _LABEL,
    "callout": ("at", "point", "number", "text", "size", "color", "outline", "font"),
}
for _k in ("brightness", "contrast", "saturation", "sharpness", "gamma", "hue", "temperature", "exposure"):
    PARAMS[_k] = ("value", "amount", _k)


def validate(spec: dict[str, Any], where: str) -> str:
    """The canonical op name of `spec`; unknown parameters are a usage error naming the known ones."""
    import difflib

    name = canonical(str(spec.get("op", spec.get("type", ""))))
    known = PARAMS.get(name)
    if known is None:
        return name
    for k in spec:
        if k in ("op", "type", "comment") or str(k).startswith("_") or k in known:
            continue
        close = difflib.get_close_matches(str(k), known, n=1, cutoff=0.6)
        hint = f" (did you mean '{close[0]}'?)" if close else ""
        raise UsageError(f"{where} ({name}): unknown parameter '{k}'{hint}. It takes: {', '.join(known) or 'no parameters'}")
    if name == "draw":
        shapes = spec.get("shapes")
        if isinstance(shapes, list):
            for j, sh in enumerate(shapes, 1):
                if isinstance(sh, dict):
                    kind = canonical(str(sh.get("type", sh.get("op", ""))))
                    if kind not in SHAPES:
                        raise UsageError(f"{where} (draw) shape {j}: type is one of {', '.join(SHAPES)}")
                    validate(sh, f"{where} shape {j}")
    return name


def run_ops(img: Any, ops: list[dict[str, Any]], ctx: dict[str, Any]) -> Any:
    """Applies the operations in order; ctx collects notes, dpi and metadata decisions."""
    had_alpha = has_alpha(img)
    img = normalize(img)
    for i, spec in enumerate(ops, 1):
        name = canonical(spec.get("op", ""))
        params = {k: v for k, v in spec.items() if k != "op"}
        try:
            img = draw_shapes(img, [{"type": name, **params}], ctx) if name in SHAPES else OPS[name](img, params, ctx)
        except (UsageError, SkillError) as e:
            raise type(e)(f"operation {i} ({name}): {e}") from None
        except (ValueError, TypeError, KeyError) as e:
            raise UsageError(f"operation {i} ({name}): bad parameters ({type(e).__name__}: {e})") from None
        ctx.setdefault("log", []).append({"op": name, "size": list(img.size), "mode": img.mode})
    if not had_alpha and img.mode in ("RGBA", "LA") and not uses_alpha(img) and not ctx.get("keep_alpha"):
        img = img.convert("RGB" if img.mode == "RGBA" else "L")
    return img


# ── helpers ─────────────────────────────────────────────────────────────


def _f(p: dict[str, Any], key: str, default: float) -> float:
    v = p.get(key, default)
    if isinstance(v, str):
        s = v.strip()
        if s.endswith("%"):
            return 1 + float(s[:-1]) / 100 if key in ("brightness", "contrast", "saturation", "sharpness") else float(s[:-1]) / 100
        return float(s)
    return float(v)


def _factor(v: Any) -> float:
    """1.2, '+20%', '-10%', '120%' → a multiplier."""
    if isinstance(v, str):
        s = v.strip()
        if s.endswith("%"):
            n = float(s[:-1])
            return 1 + n / 100 if s[0] in "+-" else n / 100
        return float(s)
    return float(v)


def _len(v: Any, total: float, name: str = "length") -> float:
    return parse_len(v, total, name)


def _rgb_apply(img: Any, fn: Callable[[Any], Any]) -> Any:
    """Runs fn on the colour channels only, keeping alpha."""
    if img.mode == "RGBA":
        a = img.getchannel("A")
        out = fn(img.convert("RGB")).convert("RGB")
        out.putalpha(a)
        return out
    if img.mode == "LA":
        a = img.getchannel("A")
        out = fn(img.convert("L")).convert("L")
        out.putalpha(a)
        return out
    return fn(img)


def _rgba(img: Any) -> Any:
    return img if img.mode == "RGBA" else normalize(img).convert("RGBA")


def _restore_mode(result: Any, like: Any) -> Any:
    if like.mode in ("L", "LA") and result.mode == "RGBA" and not uses_alpha(result):
        return result.convert("L")
    return result


def _default_width(size: tuple[int, int]) -> int:
    return max(2, round(min(size) / 250))


def _boxes(p: dict[str, Any], size: tuple[int, int]) -> list[tuple[int, int, int, int]]:
    raw = p.get("boxes")
    if raw is None and p.get("box") is not None:
        raw = [p["box"]]
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [b for b in raw.split(";") if b.strip()]
    if isinstance(raw, (list, tuple)) and len(raw) == 4 and all(isinstance(v, (int, float)) for v in raw):
        raw = [raw]  # one box given as [x, y, w, h]
    if isinstance(raw, dict):
        raw = [raw]
    return [parse_box(b, size) for b in raw]


def _points(v: Any, size: tuple[int, int]) -> list[tuple[float, float]]:
    if isinstance(v, str):
        parts = [t for t in re.split(r"\s*;\s*|\s+(?=\S+,)", v.strip()) if t]
        return [parse_point(t, size) for t in parts]
    return [parse_point(t, size) for t in v]


def _overlay_image(spec: str, width: int | None = None) -> Any:
    """Loads an overlay/watermark image (any format; SVG rendered at `width`)."""
    from _img import SvgOpts

    p = Path(spec).expanduser()
    if not p.exists():
        raise SkillError(f"image {spec} does not exist")
    ld = load(p, svg=SvgOpts(width=width) if width else None)
    return normalize(ld.img).convert("RGBA")


def _with_opacity(im: Any, opacity: float) -> Any:
    if opacity >= 1:
        return im
    im = im.convert("RGBA")
    a = im.getchannel("A").point(lambda v: round(v * max(0.0, opacity)))
    im.putalpha(a)
    return im


def _contrast_text(rgb: tuple[int, ...]) -> tuple[int, int, int, int]:
    r, g, b = rgb[:3]
    return (0, 0, 0, 255) if 0.299 * r + 0.587 * g + 0.114 * b > 150 else (255, 255, 255, 255)


# ── geometry ────────────────────────────────────────────────────────────


@op("auto_orient")
def op_auto_orient(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    from _img import apply_orientation

    out, o = apply_orientation(img)
    if o != 1:
        ctx["oriented"] = True
    return out


@op("resize")
def op_resize(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    Image = pil()
    W, H = img.size
    filt = {"lanczos": Image.LANCZOS, "bicubic": Image.BICUBIC, "bilinear": Image.BILINEAR, "nearest": Image.NEAREST, "box": Image.BOX}[str(p.get("filter", "lanczos")).lower()]
    mode = str(p.get("mode", "fit")).lower()
    upscale = _bool(p.get("upscale", False))
    if p.get("value") is not None and p.get("scale") is None:
        p = {**p, "scale": p["value"]}
    if p.get("scale") is not None:
        s = _factor(p["scale"])
        if s <= 0:
            raise UsageError("scale must be positive")
        if s > 1 and not upscale and p.get("upscale") is not None:
            return img
        return img.resize((max(1, round(W * s)), max(1, round(H * s))), filt)
    if p.get("max_edge") is not None:
        m = int(_len(p["max_edge"], max(W, H)))
        if max(W, H) <= m and not upscale:
            return img
        r = m / max(W, H)
        return img.resize((max(1, round(W * r)), max(1, round(H * r))), filt)
    if p.get("size") is not None:
        from _img import parse_size

        p = {**p, **dict(zip(("width", "height"), parse_size(str(p["size"]), (W, H))))}
    w = round(_len(p["width"], W)) if p.get("width") is not None else None
    h = round(_len(p["height"], H)) if p.get("height") is not None else None
    if not w and not h:
        raise UsageError("resize needs width, height, size, scale or max_edge")
    if mode == "fit" or not (w and h):
        r = min((w / W) if w else math.inf, (h / H) if h else math.inf)
        if r > 1 and not upscale:
            return img
        return img.resize((max(1, round(W * r)), max(1, round(H * r))), filt)
    if mode == "exact":
        return img.resize((w, h), filt)
    if mode == "cover":
        r = max(w / W, h / H)
        nw, nh = max(w, round(W * r)), max(h, round(H * r))
        big = img.resize((nw, nh), filt)
        fx, fy = anchor_frac(p.get("anchor", "center"))
        x, y = round((nw - w) * fx), round((nh - h) * fy)
        return big.crop((x, y, x + w, y + h))
    if mode in ("fill", "contain", "pad"):
        r = min(w / W, h / H)
        if r > 1 and not upscale:
            r = 1.0
        small = img.resize((max(1, round(W * r)), max(1, round(H * r))), filt)
        return _place_on_canvas(small, (w, h), p.get("anchor", "center"), p.get("bg"))
    raise UsageError(f"resize mode '{mode}' (fit, cover, fill, exact)")


def _canvas_color(img: Any, bg: Any) -> tuple[int, int, int, int]:
    if bg is None:
        return (0, 0, 0, 0) if has_alpha(img) else (255, 255, 255, 255)
    return parse_color(bg)


def _place_on_canvas(img: Any, size: tuple[int, int], anchor: str, bg: Any) -> Any:
    Image = pil()
    color = _canvas_color(img, bg)
    mode = "RGBA" if color[3] < 255 or has_alpha(img) else ("L" if img.mode == "L" and color[0] == color[1] == color[2] else "RGB")
    canvas = Image.new(mode, size, color if mode != "L" else color[0])
    src = img if mode != "RGBA" else _rgba(img)
    x, y = place(anchor, size, src.size)
    if src.mode == "RGBA":
        canvas.paste(src, (x, y), src)
    else:
        canvas.paste(src.convert(mode), (x, y))
    return canvas


@op("crop")
def op_crop(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    W, H = img.size
    if _bool(p.get("trim")) or _bool(p.get("auto")):
        return op_trim(img, p, ctx)
    if p.get("box") is not None:
        return img.crop(parse_box(p["box"], (W, H), "crop box"))
    if any(k in p for k in ("left", "top", "right", "bottom")):
        l = round(_len(p.get("left", 0), W))
        t = round(_len(p.get("top", 0), H))
        r = W - round(_len(p.get("right", 0), W))
        b = H - round(_len(p.get("bottom", 0), H))
        if r <= l or b <= t:
            raise UsageError("the insets remove the whole image")
        return img.crop((l, t, r, b))
    fx, fy = anchor_frac(p.get("anchor", "center"))
    if p.get("aspect"):
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[:/x]\s*(\d+(?:\.\d+)?)\s*", str(p["aspect"]))
        if not m:
            raise UsageError("aspect is like '16:9' or '1:1'")
        ar = float(m.group(1)) / float(m.group(2))
        if W / H > ar:
            w, h = round(H * ar), H
        else:
            w, h = W, round(W / ar)
    elif p.get("width") is not None or p.get("height") is not None:
        w = min(W, round(_len(p.get("width", W), W)))
        h = min(H, round(_len(p.get("height", H), H)))
    else:
        raise UsageError("crop needs box, left/top/right/bottom, aspect, width/height, or trim")
    x, y = round((W - w) * fx), round((H - h) * fy)
    return img.crop((x, y, x + w, y + h))


@op("trim")
def op_trim(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """Crops away uniform borders (the corner colour, or transparency) within a tolerance."""
    import numpy as np

    a = np.asarray(_rgba(img)).astype(np.int16)
    fuzz = float(p.get("fuzz", p.get("tolerance", 12)))
    color = p.get("color", "auto")
    if color == "auto":
        corners = np.array([a[0, 0], a[0, -1], a[-1, 0], a[-1, -1]])
        if (corners[:, 3] < 16).sum() >= 2:
            mask = a[..., 3] > 16
        else:
            ref = np.median(corners, axis=0)
            mask = np.abs(a[..., :3] - ref[:3]).max(axis=2) > fuzz
    elif str(color).lower() == "transparent":
        mask = a[..., 3] > 16
    else:
        ref = np.array(parse_color(color)[:3])
        mask = np.abs(a[..., :3] - ref).max(axis=2) > fuzz
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        ctx.setdefault("notes", []).append("trim: the image is one uniform colour; left unchanged")
        return img
    pad = round(_len(p.get("padding", 0), min(img.size)))
    l, t = max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad)
    r, b = min(img.size[0], int(xs.max()) + 1 + pad), min(img.size[1], int(ys.max()) + 1 + pad)
    return img.crop((l, t, r, b))


@op("rotate")
def op_rotate(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """Rotates clockwise by `angle` degrees (negative = counter-clockwise); 90/180/270 are lossless."""
    Image = pil()
    angle = float(p.get("angle", p.get("degrees", 90)))
    a = angle % 360
    if a == 0:
        return img
    if a in (90, 180, 270):
        return img.transpose({90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}[int(a)])
    bg = p.get("bg")
    color = _canvas_color(img, bg) if bg is not None else ((0, 0, 0, 0) if has_alpha(img) or ctx.get("alpha_output") else (255, 255, 255, 255))
    src = _rgba(img) if color[3] < 255 else img.convert("RGB") if img.mode not in ("L", "RGB") else img
    fill = color if src.mode == "RGBA" else (color[0] if src.mode == "L" else color[:3])
    return src.rotate(-angle, resample=Image.BICUBIC, expand=_bool(p.get("expand", True)), fillcolor=fill)


@op("flip")
def op_flip(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    Image = pil()
    d = str(p.get("direction", p.get("axis", "horizontal"))).lower()
    if d in ("horizontal", "h", "x", "left-right", "lr"):
        return img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if d in ("vertical", "v", "y", "top-bottom", "tb"):
        return img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if d == "both":
        return img.transpose(Image.Transpose.ROTATE_180)
    raise UsageError("flip direction is horizontal, vertical or both")


@op("pad")
def op_pad(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    W, H = img.size
    if p.get("width") is not None or p.get("height") is not None or p.get("size") is not None:
        if p.get("size") is not None:
            from _img import parse_size

            w, h = parse_size(str(p["size"]), (W, H))
        else:
            w, h = p.get("width"), p.get("height")
        w = round(_len(w, W)) if w is not None else W
        h = round(_len(h, H)) if h is not None else H
        if p.get("aspect"):
            raise UsageError("give either width/height or aspect")
        return _place_on_canvas(img, (max(1, w), max(1, h)), p.get("anchor", "center"), p.get("bg"))
    if p.get("aspect"):
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[:/x]\s*(\d+(?:\.\d+)?)\s*", str(p["aspect"]))
        if not m:
            raise UsageError("aspect is like '1:1'")
        ar = float(m.group(1)) / float(m.group(2))
        w, h = (max(W, round(H * ar)), H) if W / H < ar else (W, max(H, round(W / ar)))
        return _place_on_canvas(img, (w, h), p.get("anchor", "center"), p.get("bg"))
    allv = p.get("all", p.get("padding", 0))
    t = round(_len(p.get("top", allv), H))
    b = round(_len(p.get("bottom", allv), H))
    l = round(_len(p.get("left", allv), W))
    r = round(_len(p.get("right", allv), W))
    Image = pil()
    color = _canvas_color(img, p.get("bg"))
    mode = "RGBA" if color[3] < 255 or has_alpha(img) else "RGB"
    canvas = Image.new(mode, (W + l + r, H + t + b), color)
    src = _rgba(img) if mode == "RGBA" else img.convert("RGB")
    canvas.paste(src, (l, t), src if mode == "RGBA" else None)
    return canvas


@op("border")
def op_border(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    w = round(_len(p.get("width", p.get("size", _default_width(img.size))), min(img.size)))
    return op_pad(img, {"all": w, "bg": p.get("color", "black")}, ctx)


@op("round_corners")
def op_round_corners(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """Rounded corners (radius px or % of the short side; '50%' makes a circle or an ellipse), antialiased."""
    Image = pil()
    from PIL import ImageDraw

    W, H = img.size
    r = _len(p.get("radius", "8%"), min(W, H))
    ss = 4
    mask = Image.new("L", (W * ss, H * ss), 0)
    d = ImageDraw.Draw(mask)
    if _bool(p.get("circle")) or r * 2 >= min(W, H) - 0.5:
        d.ellipse([0, 0, W * ss - 1, H * ss - 1], fill=255)
    else:
        d.rounded_rectangle([0, 0, W * ss - 1, H * ss - 1], radius=r * ss, fill=255)
    mask = mask.resize((W, H), Image.LANCZOS)
    out = _rgba(img)
    from PIL import ImageChops

    out.putalpha(ImageChops.multiply(out.getchannel("A"), mask))
    ctx["keep_alpha"] = True
    return out


@op("shadow")
def op_shadow(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """A soft drop shadow under the image's shape (its alpha); the canvas grows to fit."""
    Image = pil()
    from PIL import ImageFilter

    src = _rgba(img)
    W, H = src.size
    blur = _len(p.get("blur", "2%"), min(W, H))
    ox, oy = _len(p.get("x", p.get("offset_x", "1.5%")), W), _len(p.get("y", p.get("offset_y", "1.5%")), H)
    color = parse_color(p.get("color", "black"))
    opacity = float(p.get("opacity", 0.5))
    margin = int(math.ceil(blur * 3 + max(abs(ox), abs(oy))))
    size = (W + 2 * margin, H + 2 * margin)
    shadow = Image.new("RGBA", size, (*color[:3], 0))
    a = Image.new("L", size, 0)
    a.paste(src.getchannel("A").point(lambda v: round(v * opacity)), (margin + round(ox), margin + round(oy)))
    if blur > 0:
        a = a.filter(ImageFilter.GaussianBlur(blur))
    shadow.putalpha(a)
    bg = p.get("bg")
    base = Image.new("RGBA", size, parse_color(bg) if bg is not None else (0, 0, 0, 0))
    base = Image.alpha_composite(base, shadow)
    base.alpha_composite(src, (margin, margin))
    ctx["keep_alpha"] = bg is None
    return base


# ── colour and tone ─────────────────────────────────────────────────────


@op("adjust")
def op_adjust(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    from PIL import ImageEnhance

    def fn(im: Any) -> Any:
        if "exposure" in p:
            im = ImageEnhance.Brightness(im).enhance(2 ** float(p["exposure"]))
        for key, cls in (("brightness", ImageEnhance.Brightness), ("contrast", ImageEnhance.Contrast), ("saturation", ImageEnhance.Color), ("sharpness", ImageEnhance.Sharpness)):
            if key in p:
                im = cls(im).enhance(_factor(p[key]))
        if "gamma" in p:
            g = float(p["gamma"])
            if g <= 0:
                raise UsageError("gamma must be positive")
            lut = [round(255 * (i / 255) ** (1 / g)) for i in range(256)]
            im = im.point(lut * len(im.getbands()))
        if "hue" in p and im.mode == "RGB":
            shift = round(float(p["hue"]) / 360 * 256) % 256
            h, s, v = im.convert("HSV").split()
            h = h.point(lambda x: (x + shift) % 256)
            im = pil().merge("HSV", (h, s, v)).convert("RGB")
        if "temperature" in p and im.mode == "RGB":
            t = max(-100.0, min(100.0, float(p["temperature"]))) / 100
            r, g, b = im.split()
            r = r.point(lambda x: min(255, round(x * (1 + 0.18 * t))))
            b = b.point(lambda x: min(255, round(x * (1 - 0.18 * t))))
            im = pil().merge("RGB", (r, g, b))
        return im

    return _rgb_apply(img, fn)


for _k in ("brightness", "contrast", "saturation", "sharpness", "gamma", "hue", "temperature", "exposure"):

    def _single(img: Any, p: dict[str, Any], ctx: dict[str, Any], _k: str = _k) -> Any:
        v = p.get("value", p.get("amount", p.get(_k)))
        if v is None:
            raise UsageError(f"{_k} needs a value")
        return op_adjust(img, {_k: v}, ctx)

    OPS[_k] = _single


@op("grayscale")
def op_grayscale(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    if img.mode in ("L", "LA"):
        return img
    if img.mode == "RGBA":
        return img.convert("LA")
    return img.convert("L")


@op("sepia")
def op_sepia(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    strength = float(p.get("strength", 1.0))
    m = (0.393, 0.769, 0.189, 0, 0.349, 0.686, 0.168, 0, 0.272, 0.534, 0.131, 0)

    def fn(im: Any) -> Any:
        rgb = im.convert("RGB")
        sep = rgb.convert("RGB", m)
        return pil().blend(rgb, sep, max(0.0, min(1.0, strength)))

    return _rgb_apply(img if img.mode not in ("L", "LA") else img.convert("RGBA" if img.mode == "LA" else "RGB"), fn)


@op("invert")
def op_invert(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    from PIL import ImageOps

    return _rgb_apply(img, ImageOps.invert)


@op("posterize")
def op_posterize(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    from PIL import ImageOps

    bits = int(p.get("bits", 3))
    if not 1 <= bits <= 8:
        raise UsageError("bits is 1-8")
    return _rgb_apply(img, lambda im: ImageOps.posterize(im, bits))


@op("solarize")
def op_solarize(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    from PIL import ImageOps

    return _rgb_apply(img, lambda im: ImageOps.solarize(im, int(p.get("threshold", 128))))


@op("threshold")
def op_threshold(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """Black and white at a threshold (0-255, or 'auto' = Otsu), good for scans and line art."""
    import numpy as np

    g = img.convert("L")
    t = p.get("value", p.get("threshold", "auto"))
    if str(t).lower() == "auto":
        hist = np.bincount(np.asarray(g).ravel(), minlength=256).astype(np.float64)
        total = hist.sum()
        w0 = np.cumsum(hist)
        mu = np.cumsum(hist * np.arange(256))
        mt = mu[-1]
        with np.errstate(divide="ignore", invalid="ignore"):
            between = (mt * w0 / total - mu) ** 2 / (w0 * (total - w0))
        t = int(np.nanargmax(between))
        ctx.setdefault("notes", []).append(f"threshold: Otsu picked {t}")
    t = int(t)
    return g.point(lambda v: 255 if v > t else 0)


@op("duotone")
def op_duotone(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    from PIL import ImageOps

    black = parse_color(p.get("black", p.get("dark", "black")))[:3]
    white = parse_color(p.get("white", p.get("light", p.get("color", "white"))))[:3]
    mid = parse_color(p["mid"])[:3] if p.get("mid") else None
    return _rgb_apply(img, lambda im: ImageOps.colorize(im.convert("L"), black, white, mid))


@op("levels")
def op_levels(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    lo = float(p.get("black", p.get("low", 0)))
    hi = float(p.get("white", p.get("high", 255)))
    g = float(p.get("gamma", 1.0))
    olo, ohi = float(p.get("out_black", 0)), float(p.get("out_white", 255))
    if hi <= lo:
        raise UsageError("white must be above black")
    lut = []
    for i in range(256):
        x = min(1.0, max(0.0, (i - lo) / (hi - lo))) ** (1 / g)
        lut.append(round(olo + x * (ohi - olo)))
    return _rgb_apply(img, lambda im: im.point(lut * len(im.getbands())))


@op("auto_contrast")
def op_auto_contrast(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    from PIL import ImageOps

    cutoff = float(p.get("cutoff", 0.5))
    return _rgb_apply(img, lambda im: ImageOps.autocontrast(im, cutoff=cutoff, preserve_tone=_bool(p.get("preserve_tone", True))))


@op("equalize")
def op_equalize(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """Histogram equalisation of the brightness only (colours keep their hue)."""
    from PIL import ImageOps

    def fn(im: Any) -> Any:
        if im.mode == "L":
            return ImageOps.equalize(im)
        y, cb, cr = im.convert("YCbCr").split()
        return pil().merge("YCbCr", (ImageOps.equalize(y), cb, cr)).convert("RGB")

    return _rgb_apply(img, fn)


@op("replace_color")
def op_replace_color(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    import numpy as np

    src = np.array(parse_color(p["from"])[:3], dtype=np.int16)
    dst = parse_color(p["to"])
    tol = float(p.get("tolerance", 10))
    a = np.array(_rgba(img))
    d = np.abs(a[..., :3].astype(np.int16) - src).max(axis=2)
    m = d <= tol
    a[m] = dst
    ctx.setdefault("notes", []).append(f"replace_color: {int(m.sum())} pixels")
    return pil().fromarray(a, "RGBA")


# ── filters ─────────────────────────────────────────────────────────────


def _in_boxes(img: Any, p: dict[str, Any], fn: Callable[[Any], Any]) -> Any:
    boxes = _boxes(p, img.size)
    if not boxes:
        return fn(img)
    out = img  # in place: only the boxes change (no copy of a huge image)
    for b in boxes:
        out.paste(fn(img.crop(b)), b[:2])
    return out


@op("blur")
def op_blur(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    from PIL import ImageFilter

    r = _len(p.get("radius", "1%"), min(img.size))
    return _in_boxes(img, p, lambda im: im.filter(ImageFilter.GaussianBlur(r)))


@op("box_blur")
def op_box_blur(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    from PIL import ImageFilter

    r = _len(p.get("radius", 4), min(img.size))
    return _in_boxes(img, p, lambda im: im.filter(ImageFilter.BoxBlur(r)))


@op("sharpen")
def op_sharpen(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    from PIL import ImageFilter

    amount = float(p.get("amount", 1.0))
    return _in_boxes(img, p, lambda im: _rgb_apply(im, lambda x: x.filter(ImageFilter.UnsharpMask(radius=1.2, percent=round(120 * amount), threshold=2))))


@op("unsharp")
def op_unsharp(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    from PIL import ImageFilter

    f = ImageFilter.UnsharpMask(radius=float(p.get("radius", 2)), percent=int(p.get("percent", 150)), threshold=int(p.get("threshold", 3)))
    return _in_boxes(img, p, lambda im: _rgb_apply(im, lambda x: x.filter(f)))


@op("median")
def op_median(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    from PIL import ImageFilter

    size = int(p.get("size", 3))
    if size % 2 == 0:
        size += 1
    return _in_boxes(img, p, lambda im: _rgb_apply(im, lambda x: x.filter(ImageFilter.MedianFilter(size))))


@op("pixelate")
def op_pixelate(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    Image = pil()
    block = max(2, round(_len(p.get("size", p.get("block", "2%")), min(img.size))))

    def fn(im: Any) -> Any:
        w, h = im.size
        small = im.resize((max(1, w // block), max(1, h // block)), Image.BOX)
        return small.resize((w, h), Image.NEAREST)

    return _in_boxes(img, p, fn)


@op("filter")
def op_filter(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    from PIL import ImageFilter

    names = {
        "edge_enhance": ImageFilter.EDGE_ENHANCE, "edge_enhance_more": ImageFilter.EDGE_ENHANCE_MORE, "emboss": ImageFilter.EMBOSS,
        "contour": ImageFilter.CONTOUR, "find_edges": ImageFilter.FIND_EDGES, "edges": ImageFilter.FIND_EDGES, "smooth": ImageFilter.SMOOTH,
        "smooth_more": ImageFilter.SMOOTH_MORE, "detail": ImageFilter.DETAIL, "sharpen": ImageFilter.SHARPEN,
    }
    n = str(p.get("name", p.get("filter", ""))).lower()
    if n not in names:
        raise UsageError(f"filter name is one of {', '.join(names)}")
    return _in_boxes(img, p, lambda im: _rgb_apply(im, lambda x: x.filter(names[n])))


@op("redact")
def op_redact(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """Hides regions for good: solid fill (default black), heavy pixelation or heavy blur. The pixels are replaced."""
    from PIL import ImageDraw, ImageFilter

    boxes = _boxes(p, img.size)
    if not boxes:
        raise UsageError("redact needs box or boxes")
    style = str(p.get("style", "fill")).lower()
    out = img  # in place: only the boxes change
    if style in ("fill", "black", "solid", "color", "colour"):
        color = parse_color(p.get("color", "black"))
        d = ImageDraw.Draw(out)
        fill = color if out.mode in ("RGBA",) else color[:3] if out.mode == "RGB" else color[0]
        for b in boxes:
            d.rectangle([b[0], b[1], b[2] - 1, b[3] - 1], fill=fill)
    elif style == "pixelate":
        for b in boxes:
            region = img.crop(b)
            block = max(8, min(region.size) // 4)
            out.paste(op_pixelate(region, {"size": block}, ctx), b[:2])
    elif style == "blur":
        for b in boxes:
            region = img.crop(b)
            # Blur twice with a radius tied to the region so the content cannot be recovered by sharpening.
            r = max(8.0, min(region.size) / 3)
            out.paste(region.filter(ImageFilter.GaussianBlur(r)).filter(ImageFilter.BoxBlur(r)), b[:2])
    else:
        raise UsageError("redact style is fill, pixelate or blur")
    ctx.setdefault("notes", []).append(f"redact: {len(boxes)} region(s) replaced ({style}); metadata is stripped to drop embedded thumbnails")
    ctx["force_strip"] = True
    return out


# ── transparency ────────────────────────────────────────────────────────


def _flood_from_border(mask: Any) -> Any:
    """The part of a boolean mask connected (4-way) to the image border, by alternating row/column run propagation."""
    import numpy as np

    reached = np.zeros_like(mask)
    reached[0, :] = mask[0, :]
    reached[-1, :] = mask[-1, :]
    reached[:, 0] |= mask[:, 0]
    reached[:, -1] |= mask[:, -1]

    def propagate_rows(m: Any, r: Any) -> Any:
        h, w = m.shape
        flat_m = m.ravel()
        # Run ids: a new run starts where the mask turns on or a row starts.
        starts = flat_m.copy()
        starts[1:] &= ~flat_m[:-1]
        starts[::w] = flat_m[::w]
        ids = np.cumsum(starts) * flat_m
        hit = np.zeros(ids.max() + 1, dtype=bool)
        hit[ids[r.ravel() & flat_m]] = True
        hit[0] = False
        return hit[ids].reshape(h, w)

    for _ in range(200):
        before = int(reached.sum())
        reached = propagate_rows(mask, reached)
        reached = propagate_rows(mask.T.copy(), reached.T.copy()).T
        if int(reached.sum()) == before:
            break
    return reached


def _dilate(mask: Any, r: int) -> Any:
    """A boolean mask grown by r pixels (8-neighbourhood), with array shifts."""
    import numpy as np

    out = mask.copy()
    for _ in range(max(0, r)):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return np.asarray(out)


def _lab_distance(a: Any, ref: Any) -> Any:
    """Per-pixel CIELAB distance from `ref` (an sRGB colour), lightness weighted by half: a shadow on a backdrop is
    closer to it than a pale subject of another hue is. Computed in strips to keep memory low on big photos."""
    import numpy as np

    def lab(rgb: Any) -> Any:
        c = rgb.astype(np.float32) / 255.0
        c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
        m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]], dtype=np.float32)
        xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
        f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
        return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)

    r = lab(np.asarray(ref, dtype=np.float32).reshape(1, 3))[0]
    h, w = a.shape[:2]
    out = np.empty((h, w), dtype=np.float32)
    rows = max(1, 1_000_000 // max(1, w))
    for y in range(0, h, rows):
        d = lab(a[y : y + rows, :, :3]) - r
        d[..., 0] *= 0.5
        out[y : y + rows] = np.sqrt((d * d).sum(axis=-1))
    return out


@op("chroma_key")
def op_chroma_key(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """Makes a background colour transparent: colour ('auto' = the border's colour), tolerance and feather (0-100),
    `connected` (default true: only background touching the edges, so the same colour inside the subject stays).

    Pixels within the tolerance are background; partial transparency (the feather) is applied only in a thin band
    (`edge` px, default 2) around that background, so a pale subject next to a white backdrop keeps its body opaque
    and only its outline is softened.
    """
    import numpy as np

    Image = pil()
    a = np.array(_rgba(img))
    color = p.get("color", "auto")
    if str(color).lower() == "auto":
        border = np.concatenate([a[0, :, :3], a[-1, :, :3], a[:, 0, :3], a[:, -1, :3]]).astype(np.float32)
        ref = np.median(border, axis=0)
    else:
        ref = np.array(parse_color(color)[:3], dtype=np.float32)
    tol = float(p.get("tolerance", 20))
    feather = max(1.0, float(p.get("feather", 10)))
    edge = max(0, int(round(float(p.get("edge", 2)))))
    dist = _lab_distance(a, ref)
    core = dist <= tol
    if _bool(p.get("connected", True)):
        core = _flood_from_border(core)
    band = _dilate(core, edge) & ~core
    alpha = np.ones(dist.shape, dtype=np.float32)
    alpha[core] = 0.0
    alpha[band] = np.clip((dist[band] - tol) / feather, 0, 1)
    out_a = (alpha * a[..., 3]).astype(np.uint8)
    if _bool(p.get("despill", True)):
        # Pull the key colour out of semi-transparent edge pixels.
        soft = (alpha > 0) & (alpha < 1)
        if soft.any():
            k = (1 - alpha[soft])[:, None]
            px = a[..., :3][soft].astype(np.float32)
            a[..., :3][soft] = np.clip((px - ref * k) / np.maximum(1 - k, 0.2), 0, 255).astype(np.uint8)
    a[..., 3] = out_a
    ctx["keep_alpha"] = True
    removed = float((out_a == 0).mean() * 100)
    soft_pct = float(((out_a > 0) & (out_a < 255)).mean() * 100)
    ctx.setdefault("notes", []).append(f"chroma_key: removed #{''.join(f'{int(round(v)):02x}' for v in ref)} ({removed:.1f}% of pixels now transparent, {soft_pct:.1f}% softened at the edges)")
    if removed > 97:
        ctx["notes"].append("chroma_key: almost everything was removed; lower the tolerance or name the colour")
    return Image.fromarray(a, "RGBA")


@op("transparent")
def op_transparent(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """Every pixel of one colour (within `tolerance`, default 0) becomes transparent."""
    import numpy as np

    a = np.array(_rgba(img))
    ref = np.array(parse_color(p.get("color", "white"))[:3], dtype=np.int16)
    tol = float(p.get("tolerance", 0))
    m = np.abs(a[..., :3].astype(np.int16) - ref).max(axis=2) <= tol
    a[m, 3] = 0
    ctx["keep_alpha"] = True
    return pil().fromarray(a, "RGBA")


@op("opacity")
def op_opacity(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    ctx["keep_alpha"] = True
    return _with_opacity(_rgba(img), _f(p, "value", float(p.get("opacity", 1.0))))


@op("flatten")
def op_flatten(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    ctx["keep_alpha"] = False
    return flatten(img, p.get("color", p.get("bg", "white")))


@op("mode")
def op_mode(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    m = str(p.get("mode", p.get("value", "RGB"))).upper()
    if m not in ("RGB", "RGBA", "L", "LA", "1", "P", "CMYK"):
        raise UsageError("mode is RGB, RGBA, L, LA, 1, P or CMYK")
    if m in ("RGB", "L", "CMYK", "1") and has_alpha(img):
        img = flatten(img, p.get("bg", "white"))
    if m == "P":
        return img.convert("RGB").quantize(colors=int(p.get("colors", 256)))
    if m in ("RGBA", "LA"):
        ctx["keep_alpha"] = True
    return img.convert(m)


@op("quantize")
def op_quantize(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    Image = pil()
    n = max(2, min(256, int(p.get("colors", 64))))
    dither = Image.Dither.FLOYDSTEINBERG if _bool(p.get("dither", True)) else Image.Dither.NONE
    if has_alpha(img):
        return _rgba(img).quantize(colors=n, method=Image.Quantize.FASTOCTREE, dither=dither)
    return img.convert("RGB").quantize(colors=n, method=Image.Quantize.MEDIANCUT, dither=dither)


# ── metadata ────────────────────────────────────────────────────────────


@op("set_dpi")
def op_set_dpi(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    v = float(p.get("dpi", p.get("value", 300)))
    ctx["dpi"] = (v, v)
    return img


@op("strip_metadata")
def op_strip_metadata(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    ctx["force_strip"] = True
    return img


# ── text, watermarks, overlays ──────────────────────────────────────────


def _wrap(text: str, font: Any, max_w: float | None, draw: Any) -> str:
    """Wraps each paragraph at spaces so no line is wider than max_w (a single long word stays whole)."""
    if not max_w:
        return text
    out_lines = []
    for para in text.split("\n"):
        words = para.split(" ")
        line = ""
        for w in words:
            cand = w if not line else f"{line} {w}"
            if draw.textlength(cand, font=font) <= max_w or not line:
                line = cand
            else:
                out_lines.append(line)
                line = w
        out_lines.append(line)
    return "\n".join(out_lines)


def _bool(v: Any) -> bool:
    """A boolean parameter: true/false, yes/no, on/off, 1/0 (strings from --op included)."""
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "no", "off", "none")
    return bool(v)


def _text_box(probe: Any, text: str, font: Any, spacing: float, align: str, stroke: int) -> tuple[int, int, int, int]:
    """The multiline text's bounding box as whole pixels (Pillow returns floats for fractional spacing)."""
    x0, y0, x1, y1 = probe.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align=align, stroke_width=stroke)
    return math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1)


def text_layer(text: str, p: dict[str, Any], ref_size: tuple[int, int], ctx: dict[str, Any], fit_width: float | None = None, what: str = "text") -> Any:
    """An RGBA image holding styled text (optional rounded background box), not yet placed.

    With `fit_width` (the room the placement leaves) and `fit` not false, text wider than the room is first shrunk
    (down to 40% of its size, never under 12 px), then wrapped; the notes say which.
    """
    Image = pil()
    from PIL import ImageDraw

    from _fonts import pil_font

    W, H = ref_size
    # FreeType fails on fonts smaller than a few pixels; 8 px is the smallest legible size anyway.
    size = max(8.0, _len(p.get("size", p.get("font_size", "5%")), H))
    asked = size
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    max_w = _len(p["max_width"], W) if p.get("max_width") is not None else None
    align = str(p.get("align", "left"))
    fit = str(p.get("fit", "auto")).lower()
    if fit in ("false", "0", "no", "off", "none"):
        fit_width = None
    bg = p.get("bg", p.get("background"))

    def build(size: float, wrap_w: float | None) -> tuple[Any, str, Any, tuple[int, int, int, int], int, float, int]:
        font, desc, missing = pil_font(p.get("font"), size, text, _bool(p.get("bold")), _bool(p.get("italic")))
        body = _wrap(text, font, wrap_w, probe)
        stroke = round(_len(p.get("stroke_width", p.get("stroke", 0)), size)) if p.get("stroke_width", p.get("stroke")) else 0
        spacing = float(p.get("line_spacing", 0.25)) * size
        box = _text_box(probe, body, font, spacing, align, stroke)
        pad = round(_len(p.get("padding", "35%" if bg else 0), size)) if (bg or p.get("padding") is not None) else 0
        ctx["_missing"] = missing
        ctx["_desc"] = desc
        return font, body, missing, box, stroke, spacing, pad

    font, body, missing, box, stroke, spacing, pad = build(size, max_w)
    width = box[2] - box[0] + 2 * pad
    if fit_width and width > fit_width and not float(p.get("angle", 0) or 0):
        room = max(1.0, fit_width)
        floor = max(12.0, asked * 0.4)
        if fit != "wrap":
            size = max(floor, size * room / width * 0.98)
            font, body, missing, box, stroke, spacing, pad = build(size, max_w)
            width = box[2] - box[0] + 2 * pad
        if width > room:
            font, body, missing, box, stroke, spacing, pad = build(size, max(1.0, room - 2 * pad))
            width = box[2] - box[0] + 2 * pad
        lines = body.count("\n") + 1
        how = []
        if round(size) < round(asked):
            how.append(f"shrunk from {asked:.0f} to {size:.0f} px")
        if lines > text.count("\n") + 1:
            how.append(f"wrapped to {lines} lines")
        if how:
            ctx.setdefault("notes", []).append(f"{what}: {' and '.join(how)} to fit the width (fit=false keeps the size)")
        if width > room + 1:
            ctx.setdefault("notes", []).append(f"{what}: still wider than the image (a single long word?); it is cut off at the edge")
    if missing:
        ctx.setdefault("notes", []).append(f"{what}: no installed font has {''.join(missing[:20])}; those characters may show as boxes")
    ctx.setdefault("fonts", set()).add(ctx.pop("_desc", ""))
    ctx.pop("_missing", None)
    tw, th = box[2] - box[0], box[3] - box[1]
    layer = Image.new("RGBA", (max(1, tw + 2 * pad), max(1, th + 2 * pad)), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if bg:
        radius = _len(p.get("radius", "20%"), size)
        d.rounded_rectangle([0, 0, layer.size[0] - 1, layer.size[1] - 1], radius=radius, fill=parse_color(bg))
    color = parse_color(p.get("color", "white" if bg and _contrast_text(parse_color(bg))[0] == 255 else "black"))
    d.multiline_text((pad - box[0], pad - box[1]), body, font=font, fill=color, spacing=spacing, align=align, stroke_width=stroke, stroke_fill=parse_color(p.get("stroke_color", "black")), embedded_color=True)
    real = float(getattr(font, "size", size) or size)
    if abs(real - size) > 0.5:
        # A bitmap-only font (colour emoji) drew at its nearest strike: scale the layer to the size asked for.
        f = size / real
        layer = layer.resize((max(1, round(layer.size[0] * f)), max(1, round(layer.size[1] * f))), Image.LANCZOS)
    opacity = float(p.get("opacity", 1.0))
    layer = _with_opacity(layer, opacity)
    angle = float(p.get("angle", 0) or 0)
    if angle:
        layer = layer.rotate(-angle, resample=Image.BICUBIC, expand=True)
    return layer


def _position(p: dict[str, Any], canvas: tuple[int, int], item: tuple[int, int], default: str = "bottom-right") -> tuple[int, int]:
    W, H = canvas
    if p.get("x") is not None or p.get("y") is not None or p.get("at") is not None:
        if p.get("at") is not None:
            x, y = parse_point(p["at"], canvas)
        else:
            x, y = _len(p.get("x", 0), W), _len(p.get("y", 0), H)
        ax, ay = anchor_frac(p.get("anchor", "top-left"))
        return round(x - ax * item[0]), round(y - ay * item[1])
    margin = _len(p.get("margin", "3%"), min(W, H))
    return place(p.get("position", default), canvas, item, margin)


def _room(p: dict[str, Any], canvas: tuple[int, int]) -> float:
    """The width a placement leaves for text: between the margins, or from x/at towards its anchor's side."""
    W, H = canvas
    if p.get("x") is not None or p.get("at") is not None:
        x = parse_point(p["at"], canvas)[0] if p.get("at") is not None else _len(p.get("x", 0), W)
        ax, _ = anchor_frac(p.get("anchor", "top-left"))
        room = (W - x) if ax == 0 else x if ax == 1 else 2 * min(x, W - x)
        return max(1.0, room - 2)
    return max(1.0, W - 2 * _len(p.get("margin", "3%"), min(W, H)))


def _note_clipped(ctx: dict[str, Any], what: str, xy: tuple[int, int], item: tuple[int, int], canvas: tuple[int, int]) -> None:
    x, y = xy
    if x < -1 or y < -1 or x + item[0] > canvas[0] + 1 or y + item[1] > canvas[1] + 1:
        ctx.setdefault("notes", []).append(f"{what}: part of it lies outside the image and is cut off (check the position, size or max_width)")


@op("text")
def op_text(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    text = str(p.get("text", ""))
    if not text:
        raise UsageError("text needs text")
    layer = text_layer(text, p, img.size, ctx, fit_width=_room(p, img.size))
    x, y = _position(p, img.size, layer.size, "bottom-left")
    _note_clipped(ctx, "text", (x, y), layer.size, img.size)
    out = _canvas_for(img)
    _composite(out, layer, (x, y))
    return out


def _canvas_for(img: Any) -> Any:
    """The image annotations are drawn on, in place: RGB or RGBA as is (a 200 MP photo is not copied), other modes
    converted once (greyscale becomes RGB so coloured marks stay coloured)."""
    if img.mode in ("RGB", "RGBA"):
        return img
    if img.mode in ("LA", "PA") or has_alpha(img):
        return normalize(img).convert("RGBA")
    return normalize(img).convert("RGB")


def _composite(base: Any, layer: Any, xy: tuple[int, int]) -> None:
    """Composites the RGBA `layer` over `base` (RGB or RGBA) at xy, clipped to the image, touching only that region."""
    x, y = int(xy[0]), int(xy[1])
    W, H = base.size
    lw, lh = layer.size
    l, t, r, b = max(0, x), max(0, y), min(W, x + lw), min(H, y + lh)
    if r <= l or b <= t:
        return
    part = layer if (l, t, r, b) == (x, y, x + lw, y + lh) else layer.crop((l - x, t - y, r - x, b - y))
    if base.mode == "RGBA":
        base.alpha_composite(part, (l, t))
    else:
        base.paste(part, (l, t), part)  # "over" onto an opaque image


@op("watermark")
def op_watermark(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """A text or image watermark: one corner (position) or tiled diagonally over the whole image (tile: true)."""
    Image = pil()
    W, H = img.size
    opacity = float(p.get("opacity", 0.35))
    if p.get("image"):
        width = round(_len(p.get("width", p.get("scale", "20%")), W))
        mark = _overlay_image(str(p["image"]), width)
        if mark.size[0] != width:
            mark = mark.resize((width, max(1, round(mark.size[1] * width / mark.size[0]))), Image.LANCZOS)
        mark = _with_opacity(mark, opacity)
        angle = float(p.get("angle", 30 if _bool(p.get("tile")) else 0))
        if angle:
            mark = mark.rotate(-angle, resample=Image.BICUBIC, expand=True)
    elif p.get("text"):
        tile = _bool(p.get("tile"))
        tp = {"size": p.get("size", "4%" if tile else "5%"), "color": p.get("color", "white"), "font": p.get("font"), "bold": p.get("bold", True),
              "stroke_width": p.get("stroke_width", "6%"), "stroke_color": p.get("stroke_color", "#00000080"), "opacity": opacity,
              "angle": p.get("angle", 30 if tile else 0), "fit": p.get("fit", "auto"), "align": p.get("align", "center")}
        mark = text_layer(str(p["text"]), tp, img.size, ctx, fit_width=None if tile else _room(p, img.size), what="watermark")
    else:
        raise UsageError("watermark needs text or image")
    out = _canvas_for(img)
    if _bool(p.get("tile")):
        gap = _len(p.get("spacing", "8%"), min(W, H))
        layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
        sx, sy = mark.size[0] + gap, mark.size[1] + gap
        row = 0
        y = -mark.size[1] // 2
        while y < H:
            x = -mark.size[0] // 2 + (sx // 2 if row % 2 else 0)
            while x < W:
                layer.paste(mark, (round(x), round(y)), mark)
                x += sx
            y += sy
            row += 1
        _composite(out, layer, (0, 0))
    else:
        xy = _position(p, out.size, mark.size, "bottom-right")
        _note_clipped(ctx, "watermark", xy, mark.size, out.size)
        _composite(out, mark, xy)
    return out


BLENDS = ("normal", "multiply", "screen", "overlay", "darken", "lighten", "difference", "add", "subtract", "soft_light", "hard_light")


@op("composite")
def op_composite(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """Places another image (any format; SVG rendered sharp) on top: position or x/y, size, opacity, blend mode."""
    Image = pil()
    from PIL import ImageChops

    W, H = img.size
    width = round(_len(p["width"], W)) if p.get("width") is not None else None
    over = _overlay_image(str(p.get("image", "")), width)
    if width and over.size[0] != width:
        over = over.resize((width, max(1, round(over.size[1] * width / over.size[0]))), Image.LANCZOS)
    elif p.get("height") is not None:
        h = round(_len(p["height"], H))
        over = over.resize((max(1, round(over.size[0] * h / over.size[1])), h), Image.LANCZOS)
    elif p.get("scale") is not None:
        s = _factor(p["scale"])
        over = over.resize((max(1, round(over.size[0] * s)), max(1, round(over.size[1] * s))), Image.LANCZOS)
    over = _with_opacity(over, float(p.get("opacity", 1.0)))
    x, y = _position(p, img.size, over.size, "center")
    mode = str(p.get("blend", p.get("mode", "normal"))).lower()
    if mode not in BLENDS:
        raise UsageError(f"blend is one of {', '.join(BLENDS)}")
    if mode == "normal":
        base = _canvas_for(img)
        _composite(base, over, (x, y))
        return base
    base = _rgba(img)
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.paste(over, (x, y), over)
    b, l = base.convert("RGB"), layer.convert("RGB")
    fn = {"multiply": ImageChops.multiply, "screen": ImageChops.screen, "overlay": ImageChops.overlay, "darken": ImageChops.darker, "lighten": ImageChops.lighter,
          "difference": ImageChops.difference, "add": ImageChops.add, "subtract": ImageChops.subtract, "soft_light": ImageChops.soft_light, "hard_light": ImageChops.hard_light}[mode]
    blended = fn(b, l).convert("RGBA")
    blended.putalpha(base.getchannel("A"))
    out = Image.composite(blended, base, layer.getchannel("A"))
    return _restore_mode(out, img)


# ── drawing and annotation ──────────────────────────────────────────────


@op("draw")
def op_draw(img: Any, p: dict[str, Any], ctx: dict[str, Any]) -> Any:
    shapes = p.get("shapes")
    if not isinstance(shapes, list) or not shapes:
        raise UsageError("draw needs shapes: a list of {type: rect|ellipse|line|arrow|polygon|highlight|callout, …}")
    return draw_shapes(img, shapes, ctx)


def draw_shapes(img: Any, shapes: list[dict[str, Any]], ctx: dict[str, Any]) -> Any:
    """Draws annotation shapes in order, each antialiased (supersampled inside its own bounds), then their labels.

    Supersampling only each shape's bounding box keeps a 5-shape annotation of a 24 MP photo fast and small in
    memory, and compositing shape by shape lets translucent fills stack instead of replacing each other.
    """
    Image = pil()
    from PIL import ImageDraw

    base = _canvas_for(img)
    W, H = base.size
    labels: list[tuple[str, tuple[float, float, float, float], tuple[int, ...], dict[str, Any]]] = []
    dw = _default_width((W, H))

    def paint(bounds: tuple[float, float, float, float], pad: float, fn: Any) -> None:
        x0, y0 = max(0, math.floor(bounds[0] - pad)), max(0, math.floor(bounds[1] - pad))
        x1, y1 = min(W, math.ceil(bounds[2] + pad)), min(H, math.ceil(bounds[3] + pad))
        if x1 <= x0 or y1 <= y0:
            return
        bw, bh = x1 - x0, y1 - y0
        ss = max(1, min(4, int(math.sqrt(16e6 / (bw * bh)))))
        over = Image.new("RGBA", (bw * ss, bh * ss), (0, 0, 0, 0))

        def P(x: float, y: float) -> tuple[float, float]:
            return (x - x0) * ss, (y - y0) * ss

        fn(ImageDraw.Draw(over), P, ss)
        if ss > 1:
            over = over.reduce(ss)  # a box average of premultiplied pixels: exact coverage antialiasing
        _composite(base, over, (x0, y0))

    for i, sh in enumerate(shapes, 1):
        kind = ALIASES.get(str(sh.get("type", sh.get("op", ""))).lower(), str(sh.get("type", sh.get("op", ""))).lower())
        color = parse_color(sh.get("color", "#ff2d55" if kind != "highlight" else "#ffe600"))
        width = _len(sh.get("width", dw), min(W, H))
        fill = parse_color(sh["fill"]) if sh.get("fill") else None
        if kind in ("rect", "ellipse", "highlight"):
            box = parse_box(sh.get("box", sh.get("rect")), (W, H), f"shape {i} box")
            if kind == "highlight":
                from PIL import ImageChops

                region = base.crop(box)
                tint = Image.new("RGB", region.size, color[:3])
                mixed = ImageChops.multiply(region.convert("RGB"), tint).convert(region.mode)
                if region.mode == "RGBA":
                    mixed.putalpha(region.getchannel("A"))
                base.paste(Image.blend(region, mixed, max(0.0, min(1.0, float(sh.get("strength", 0.55))))), box[:2])
            else:
                radius = _len(sh.get("radius", 0), min(W, H)) if kind == "rect" else 0

                def fn(d: Any, P: Any, ss: int, box: Any = box, kind: str = kind, radius: float = radius, color: Any = color, fill: Any = fill, width: float = width) -> None:
                    (ax, ay), (bx, by) = P(box[0], box[1]), P(box[2], box[3])
                    rect = [ax, ay, bx - 1, by - 1]
                    if kind == "rect":
                        d.rounded_rectangle(rect, radius=radius * ss, outline=color, width=max(1, round(width * ss)), fill=fill)
                    else:
                        d.ellipse(rect, outline=color, width=max(1, round(width * ss)), fill=fill)

                paint(tuple(box), 2, fn)
            if sh.get("label"):
                labels.append((str(sh["label"]), tuple(box), color, sh))
        elif kind in ("line", "arrow", "polygon"):
            pts = _points(sh.get("points") or [sh.get("from"), sh.get("to")], (W, H))
            if len(pts) < 2 or any(p is None for p in pts):
                raise UsageError(f"shape {i} ({kind}) needs points (or from/to)")
            head = _len(sh.get("head", max(width * 4.5, min(W, H) / 60)), min(W, H)) if kind == "arrow" else 0.0

            def fn(d: Any, P: Any, ss: int, pts: Any = pts, kind: str = kind, color: Any = color, fill: Any = fill, width: float = width, head: float = head) -> None:
                spts = [P(x, y) for x, y in pts]
                if kind == "polygon":
                    d.polygon(spts, outline=color, fill=fill, width=max(1, round(width * ss)))
                    return
                d.line(spts, fill=color, width=max(1, round(width * ss)), joint="curve")
                if kind == "arrow":
                    (x0, y0), (x1, y1) = spts[-2], spts[-1]
                    ang = math.atan2(y1 - y0, x1 - x0)
                    hl = head * ss
                    left = (x1 - hl * math.cos(ang - 0.45), y1 - hl * math.sin(ang - 0.45))
                    right = (x1 - hl * math.cos(ang + 0.45), y1 - hl * math.sin(ang + 0.45))
                    d.polygon([(x1, y1), left, right], fill=color)

            xs, ys = [x for x, _ in pts], [y for _, y in pts]
            paint((min(xs), min(ys), max(xs), max(ys)), width + head + 2, fn)
            if sh.get("label"):
                x, y = pts[0]
                labels.append((str(sh["label"]), (x, y, x, y), color, {**sh, "label_position": sh.get("label_position", "start")}))
        elif kind == "callout":
            x, y = parse_point(sh.get("at", sh.get("point")), (W, H), f"shape {i} at")
            r = _len(sh.get("size", max(14, min(W, H) / 28)), min(W, H)) / 2
            outline = parse_color(sh.get("outline", "white"))

            def fn(d: Any, P: Any, ss: int, x: float = x, y: float = y, r: float = r, color: Any = color, outline: Any = outline) -> None:
                (ax, ay), (bx, by) = P(x - r, y - r), P(x + r, y + r)
                d.ellipse([ax, ay, bx, by], fill=color, outline=outline, width=max(1, round(max(1, r / 8) * ss)))

            paint((x - r, y - r, x + r, y + r), 2, fn)
            labels.append((str(sh.get("number", sh.get("text", i))), (x - r, y - r, x + r, y + r), color, {**sh, "label_position": "center", "size": r * 1.2}))
        else:
            raise UsageError(f"shape {i}: unknown type '{kind}' (rect, ellipse, line, arrow, polygon, highlight, callout)")
    for text, box, color, sh in labels:
        _draw_label(base, text, box, color, sh, ctx)
    return base


def _draw_label(base: Any, text: str, box: tuple[float, float, float, float], color: tuple[int, ...], sh: dict[str, Any], ctx: dict[str, Any]) -> None:
    W, H = base.size
    pos = sh.get("label_position", "above")
    size = sh.get("label_size", sh.get("size") if pos == "center" else None) or max(13, min(W, H) / 42)
    fg = _contrast_text(color)
    tp = {"size": size, "color": sh.get("label_color", "#%02x%02x%02x" % fg[:3]), "bg": None if pos == "center" else "#%02x%02x%02x" % color[:3], "font": sh.get("font"), "bold": True, "padding": "25%", "radius": "15%",
          "align": sh.get("label_align", "left")}
    layer = text_layer(text, tp, base.size, ctx, fit_width=None if pos == "center" else max(1.0, W - 4), what="label")
    lw, lh = layer.size
    x0, y0, x1, y1 = box
    if pos == "center":
        x, y = (x0 + x1) / 2 - lw / 2, (y0 + y1) / 2 - lh / 2
    elif pos == "below":
        x, y = x0, y1 + 2
    elif pos == "start":
        x, y = x0 - lw / 2, y0 - lh - 4
    else:  # above; inside the top-left corner when there is no room above
        x, y = x0, y0 - lh - 2
        if y < 0:
            x, y = x0 + 2, y0 + 2
    x = min(max(0, x), max(0, W - lw))
    y = min(max(0, y), max(0, H - lh))
    _composite(base, layer, (round(x), round(y)))
