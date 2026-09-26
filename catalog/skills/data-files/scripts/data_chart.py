#!/usr/bin/env python3
"""Charts from data files or SQL: bar, barh, stacked, line, area, scatter (with trend), histogram, box, heatmap
(correlation or pivot), pie and donut, as PNG (sized for vision) or SVG. Aggregation happens in DuckDB, so big
tables chart quickly; the numbers plotted are printed too."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SkillError, UsageError, output_path, parser, run_main  # noqa: E402

EPILOG = """examples:
  python3 scripts/data_chart.py sales.csv --kind bar --x region --y amount --agg sum --out region.png
  python3 scripts/data_chart.py sales.csv --kind line --x day --y amount --agg sum --date-unit month
  python3 scripts/data_chart.py sales.csv --kind line --x day --y amount --series region --date-unit week
  python3 scripts/data_chart.py sales.csv --kind stacked --x region --series product --y qty
  python3 scripts/data_chart.py people.csv --kind scatter --x age --y income --trend
  python3 scripts/data_chart.py people.csv --kind hist --y age --bins 30
  python3 scripts/data_chart.py people.csv --kind box --x country --y income
  python3 scripts/data_chart.py metrics.parquet --kind heatmap                       # correlation matrix
  python3 scripts/data_chart.py sales.csv --kind heatmap --x month --y region --value amount
  python3 scripts/data_chart.py sales.csv --kind donut --x channel --y amount
  python3 scripts/data_chart.py a.csv --sql "SELECT year, count(*) AS n FROM a GROUP BY 1" --kind bar --x year --y n

Without --y, bar/pie count rows per --x. With repeated --x values, --agg (default sum) combines them. Bars and
pies keep the top --top categories (default 20 bars, 8 slices) and fold the rest into "Other". Scatter plots of
more than 20,000 points draw a random sample (said on the chart). The default output is <input>-<kind>.png; .svg
works too. Look at the PNG with view_image before sharing it.
"""

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948", "#0e9aa7", "#8c5a2b"]
OTHER = "#a3a29c"
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e4e3df"
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
DIV = ["#1c5cab", "#5598e7", "#b7d3f6", "#f0efec", "#f3b7b6", "#e66767", "#b8302f"]
KINDS = ("bar", "barh", "stacked", "line", "area", "scatter", "hist", "box", "heatmap", "pie", "donut")
AGGS = ("sum", "mean", "avg", "count", "min", "max", "median", "count_distinct")


def main() -> int:
    from _duck import add_read_args

    p = parser("Draw a chart (PNG sized for vision, or SVG) from a data file or a SQL query.", EPILOG)
    p.add_argument("files", nargs="+", help="data file(s)")
    p.add_argument("--kind", choices=KINDS, default="bar")
    p.add_argument("--x", help="category / time / x column")
    p.add_argument("--y", help="value column(s), comma-separated for several series")
    p.add_argument("--series", help="column whose values become separate series (long data)")
    p.add_argument("--value", help="heatmap pivot: the value column (with --x and --y)")
    p.add_argument("--agg", choices=AGGS, help="how repeated x values combine (default sum; count without --y)")
    p.add_argument("--sql", help="chart the result of this query (inputs are tables named after their files)")
    p.add_argument("--where", help="filter rows first (SQL condition)")
    p.add_argument("--date-unit", choices=["day", "week", "month", "quarter", "year", "hour"], help="bucket a date/time x column")
    p.add_argument("--top", type=int, help="categories kept before 'Other' (default 20 bars, 8 slices, 8 series)")
    p.add_argument("--sort", choices=["value", "label", "none"], help="bar order (default: value for categories, label for numbers/dates)")
    p.add_argument("--bins", type=int, help="histogram bins (default automatic)")
    p.add_argument("--trend", action="store_true", help="scatter: add a least-squares line with r²")
    p.add_argument("--log", action="store_true", help="logarithmic value axis")
    p.add_argument("--value-labels", choices=["auto", "on", "off"], default="auto", help="value labels on bars and slices (auto: when few)")
    p.add_argument("--title", help="chart title (default: generated)")
    p.add_argument("--subtitle", help="line under the title")
    p.add_argument("--xlabel")
    p.add_argument("--ylabel")
    p.add_argument("--out", "-o", help="output .png or .svg (default <input>-<kind>.png)")
    p.add_argument("--width", type=int, default=1568, help="pixels (default 1568, the vision size)")
    p.add_argument("--height", type=int, default=980, help="pixels (default 980)")
    p.add_argument("--font", help="a .ttf/.otf file for labels in scripts DejaVu Sans lacks (Chinese, Japanese, Arabic …)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--format", choices=["md", "json"], default="md", help="report format (the chart itself is --out)")
    add_read_args(p)
    a = p.parse_args()
    if not (200 <= a.width <= 8000 and 200 <= a.height <= 8000):
        raise UsageError("--width and --height are 200 to 8000 pixels")
    t0 = time.time()
    res = chart(a)
    res["seconds"] = round(time.time() - t0, 2)
    if a.format == "json":
        import json

        print(json.dumps({**res, "hint": "Look at it with view_image."}, ensure_ascii=False, indent=1, default=str))
        return 0
    from _render import announce

    print(res["summary"])
    for n in res.get("notes") or []:
        print(f"- {n}")
    announce([res["path"]], f"{res['kind']} chart, {res['width']}×{res['height']} px, {res['seconds']}s")
    return 0


# ── data ────────────────────────────────────────────────────────────────


def _cols(s: str | None) -> list[str]:
    return [c.strip() for c in (s or "").split(",") if c.strip()]


def _agg_sql(agg: str, col: str | None) -> str:
    from _duck import ident

    if agg == "count" or col is None:
        return "count(*)" if col is None else f"count({ident(col)})"
    if agg == "count_distinct":
        return f"count(DISTINCT {ident(col)})"
    fn = {"avg": "avg", "mean": "avg"}.get(agg, agg)
    return f"{fn}({ident(col)})"


def chart(a: Any) -> dict[str, Any]:
    from _duck import columns_of, connect, guard_sql, ident, load_all, parse_inputs, read_opts

    opts = read_opts(a)
    con = connect()
    if a.sql:
        guard_sql(con, a.sql, "data_query.py --out")
    specs = parse_inputs(a.files)
    loaded = load_all(con, specs, opts, a.sql)
    if a.sql:
        try:
            con.execute(f"CREATE TEMP TABLE __c AS {a.sql}")
        except Exception as e:  # noqa: BLE001
            raise SkillError(f"SQL error: {str(e).splitlines()[0]}") from None
        src = "__c"
    elif len(loaded) == 1 and loaded[0].kind == "database":
        from _duck import one_table

        src = one_table(con, loaded[0], opts)
    else:
        tables = [ld for ld in loaded if ld.kind != "database"]
        if len(tables) != 1 or len(loaded) != 1:
            raise UsageError("chart one table (a database needs --table), or combine several inputs with --sql")
        src = ident(tables[0].name)
    if a.where:
        src = f"(SELECT * FROM {src} WHERE {a.where})"
    cols = dict(columns_of(con, src))
    lower = {c.lower(): c for c in cols}

    def col(name: str | None, what: str) -> str | None:
        if name is None:
            return None
        c = lower.get(name.lower())
        if c is None:
            raise UsageError(f"{what}: no column {name!r}; columns: {', '.join(list(cols)[:40])}")
        return c

    x = col(a.x, "--x")
    notes: list[str] = []
    if x and cols[x] == "VARCHAR" and a.kind not in ("pie", "donut", "box", "hist"):
        from _duck import date_cast_sql, guess_date_formats

        g = guess_date_formats(con, src, x)
        if g:
            src = f"(SELECT * REPLACE ({date_cast_sql(x, g)} AS {ident(x)}) FROM {src})"
            cols[x] = g["kind"]
            notes.append(f"{x} read as dates ({' | '.join(g['formats'])})")
    ys = [col(y, "--y") for y in _cols(a.y)]
    series = col(a.series, "--series")
    value = col(a.value, "--value")
    stem = Path(a.files[0]).name.split(".")[0] if a.files else "chart"
    out = Path(a.out) if a.out else Path(f"{stem}-{a.kind}.png")
    if out.suffix.lower() not in (".png", ".svg"):
        raise UsageError("--out must end in .png or .svg")
    out = output_path(out, [Path(f) for f in a.files if Path(f).exists()], a.force)
    ctx = {"a": a, "con": con, "src": src, "cols": cols, "x": x, "ys": ys, "series": series, "value": value, "notes": notes, "source": ", ".join(Path(f).name for f in a.files)}
    fig, summary = DRAW[a.kind](ctx)
    _save(fig, out, a, ctx)
    return {"path": str(out), "kind": a.kind, "summary": summary, "width": a.width, "height": a.height, "notes": ctx["notes"], "layout_overlaps": ctx.get("overlaps", [])}


def _is_time(t: str) -> bool:
    t = t.upper()
    return t.startswith("DATE") or t.startswith("TIMESTAMP") or t == "TIME"


def _is_num(t: str) -> bool:
    from data_profile import kind_of

    return kind_of(t) == "number"


def _xexpr(ctx: dict[str, Any]) -> str:
    from _duck import ident

    a, x = ctx["a"], ctx["x"]
    if a.date_unit:
        if not _is_time(ctx["cols"][x]):
            return f"date_trunc('{a.date_unit}', TRY_CAST({ident(x)} AS TIMESTAMP))"
        return f"date_trunc('{a.date_unit}', {ident(x)})"
    return ident(x)


def _partial_buckets(ctx: dict[str, Any]) -> None:
    """Notes a first or last --date-unit bucket the data covers only in part: a sum or count there looks like a drop."""
    from _duck import ident

    a, con, src, x = ctx["a"], ctx["con"], ctx["src"], ctx["x"]
    raw = ident(x) if _is_time(ctx["cols"][x]) else f"TRY_CAST({ident(x)} AS TIMESTAMP)"
    unit = a.date_unit
    try:
        row = con.sql(f"SELECT min(v), max(v), date_trunc('{unit}', min(v)), date_trunc('{unit}', max(v)), date_trunc('{unit}', max(v)) + INTERVAL 1 {unit} FROM (SELECT CAST({raw} AS TIMESTAMP) AS v FROM {src}) WHERE v IS NOT NULL").fetchone()
    except Exception:  # noqa: BLE001 — a note, never an error
        return
    lo, hi, first, last, end = row
    if lo is None or first == last:
        return
    fmt = {"year": "%Y", "quarter": "%Y-%m", "month": "%Y-%m", "hour": "%Y-%m-%d %H:00"}.get(unit, "%Y-%m-%d")
    span = (end - last).total_seconds()
    step = {"hour": 3600, "day": 86400}.get(unit, 86400)
    if span and (hi - last).total_seconds() + step < 0.9 * span:
        ctx["notes"].append(f"the last {unit} ({last:{fmt}}) is partial: the data ends {hi:%Y-%m-%d}")
        ctx["partial"] = ctx.get("partial", []) + ["last"]
    first_end = con.sql(f"SELECT TIMESTAMP '{first}' + INTERVAL 1 {unit}").fetchone()[0]
    fspan = (first_end - first).total_seconds()
    if fspan and (lo - first).total_seconds() > 0.1 * fspan:
        ctx["notes"].append(f"the first {unit} ({first:{fmt}}) is partial: the data starts {lo:%Y-%m-%d}")
        ctx["partial"] = ctx.get("partial", []) + ["first"]


def grouped(ctx: dict[str, Any], need_x: bool = True) -> tuple[list[Any], dict[str, list[Any]], str]:
    """Aggregated data: (x values, {series name: values}, value label). Handles --y lists and --series."""
    from _duck import ident

    a, con, src, x, ys, series = ctx["a"], ctx["con"], ctx["src"], ctx["x"], ctx["ys"], ctx["series"]
    if need_x and not x:
        raise UsageError(f"--kind {a.kind} needs --x")
    agg = a.agg or ("sum" if ys else "count")
    if a.date_unit and x and agg in ("sum", "count", "count_distinct"):
        _partial_buckets(ctx)
    xe = _xexpr(ctx)
    if series:
        if len(ys) > 1:
            raise UsageError("use either several --y columns or --series, not both")
        y = ys[0] if ys else None
        rows = con.sql(f"SELECT {xe} AS x, CAST({ident(series)} AS VARCHAR) AS s, {_agg_sql(agg, y)} AS v FROM {src} WHERE {xe} IS NOT NULL GROUP BY 1, 2").fetchall()
        totals: dict[str, float] = {}
        for _, s, v in rows:
            totals[str(s)] = totals.get(str(s), 0) + (v or 0)
        keep_n = a.top or 8
        order = sorted(totals, key=lambda s: -abs(totals[s]))
        if len(order) == keep_n + 1:
            keep_n += 1  # folding one series into "Other" would only hide its name
        keep = set(order[:keep_n])
        xs = sorted({r[0] for r in rows}, key=_sort_key)
        idx = {v: i for i, v in enumerate(xs)}
        # None where a series has no row for an x: bars draw nothing there, lines skip the point (not a fake zero).
        data: dict[str, list[Any]] = {s: [None] * len(xs) for s in order[:keep_n]}
        additive = agg in ("sum", "count", "count_distinct")
        if len(order) > keep_n and additive:
            data["Other"] = [None] * len(xs)
            ctx["notes"].append(f"{len(order) - keep_n} smaller series folded into Other")
        elif len(order) > keep_n:
            ctx["notes"].append(f"showing the {keep_n} largest of {len(order)} series")
        for xv, s, v in rows:
            key = str(s) if str(s) in keep else "Other"
            if key in data and v is not None:
                i = idx[xv]
                data[key][i] = (data[key][i] or 0) + v if additive else v
        once = y is not None and not a.agg and con.sql(f"SELECT coalesce(max(c), 0) <= 1 FROM (SELECT count(*) AS c FROM {src} WHERE {xe} IS NOT NULL GROUP BY {xe}, {ident(series)})").fetchone()[0]
        label = y if once else (f"{agg} of {y}" if y else "rows")
        return xs, data, label
    if not ys:
        rows = con.sql(f"SELECT {xe} AS x, count(*) FROM {src} WHERE {xe} IS NOT NULL GROUP BY 1").fetchall()
        return [r[0] for r in rows], {"count": [r[1] for r in rows]}, "rows"
    sel = ", ".join(f"{_agg_sql(agg, y)}" for y in ys)
    rows = con.sql(f"SELECT {xe} AS x, {sel} FROM {src} WHERE {xe} IS NOT NULL GROUP BY 1").fetchall()
    xs = [r[0] for r in rows]
    data = {y: [r[i + 1] for r in rows] for i, y in enumerate(ys)}
    dups = con.sql(f"SELECT count(*) > count(DISTINCT {xe}) FROM {src} WHERE {xe} IS NOT NULL").fetchone()[0]
    if dups or a.agg:
        label = f"{agg} of {ys[0]}" if len(ys) == 1 else f"{agg} of {', '.join(ys)}"
    else:
        label = ", ".join(ys)  # one row per x: the values themselves
    if dups and not a.agg:
        ctx["notes"].append(f"repeated {x} values combined with {agg} (--agg to change)")
    return xs, data, label


def _sort_key(v: Any) -> Any:
    if v is None:
        return (2, "")
    if isinstance(v, (int, float)):
        return (0, v)
    try:
        import datetime as dt

        if isinstance(v, (dt.date, dt.datetime)):
            return (0, v.toordinal() if not isinstance(v, dt.datetime) else v.timestamp() / 86400)
    except Exception:  # noqa: BLE001
        pass
    return (1, str(v))


# ── matplotlib setup ────────────────────────────────────────────────────


def _plt(font: str | None = None) -> Any:
    os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "desk-matplotlib-bundled-fonts"))
    # Scanning system fonts takes from seconds to minutes on a first run (macOS asks system_profiler); the bundled
    # DejaVu fonts cover Latin, Greek and Cyrillic. --font adds a font file for other scripts.
    set_here = "MPL_IGNORE_SYSTEM_FONTS" not in os.environ
    if set_here:
        os.environ["MPL_IGNORE_SYSTEM_FONTS"] = "1"
    import warnings

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if set_here:
        # The font list is built (bundled fonts only); from now on the variable would also hide every font added
        # with addfont (--font, the Unicode fallback) from findfont, which then only searches matplotlib's folder.
        os.environ.pop("MPL_IGNORE_SYSTEM_FONTS", None)
    warnings.filterwarnings("ignore", message=r"Glyph \d+ .* missing from font")
    import logging

    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)  # "no bold face, using regular" notices

    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "font.size": 13, "axes.titlesize": 18, "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlepad": 14,
        "axes.labelsize": 13, "axes.labelcolor": INK2, "axes.edgecolor": GRID, "axes.linewidth": 1.0,
        "xtick.color": INK2, "ytick.color": INK2, "xtick.labelsize": 12, "ytick.labelsize": 12,
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 1.0,
        "axes.axisbelow": True, "legend.frameon": False, "legend.fontsize": 12, "text.color": INK,
        "axes.prop_cycle": matplotlib.cycler(color=PALETTE), "lines.linewidth": 2.0, "lines.solid_capstyle": "round",
        "svg.fonttype": "none",
    })
    if font:
        from matplotlib import font_manager

        fp = Path(font).expanduser()
        if not fp.is_file():
            raise UsageError(f"--font: {font} is not a font file")
        font_manager.fontManager.addfont(str(fp))
        name = font_manager.FontProperties(fname=str(fp)).get_name()
        plt.rcParams["font.family"] = [name, "DejaVu Sans"]
    return plt


def _fig(ctx: dict[str, Any]) -> tuple[Any, Any]:
    a = ctx["a"]
    plt = _plt(a.font)
    fig, ax = plt.subplots(figsize=(a.width / 100, a.height / 100), dpi=100)
    return fig, ax


def _cap(s: str) -> str:
    """First letter upper case, the rest untouched (str.capitalize() would lower-case column names)."""
    return s[:1].upper() + s[1:]


def _compact(v: float, scale: float | None = None) -> str:
    """Short number text for ticks and labels: 1.2M, 45.3K, 1,234, 29.96, 0.2425. `scale` (an axis's largest
    value) picks one unit for every tick of the axis, so 5K sits under 10K instead of 5,000."""
    av = abs(v)
    unit = abs(scale) if scale is not None else av
    if av < 1e-12:
        return "0"
    if unit >= 1e15:
        return f"{v:.3g}"
    if unit >= 1e12:
        return f"{v / 1e12:.3g}T"
    if unit >= 1e9:
        return f"{v / 1e9:.3g}B"
    if unit >= 1e6:
        return f"{v / 1e6:.3g}M"
    if unit >= 1e4:
        return f"{v / 1e3:.3g}K"
    if float(v).is_integer():
        return f"{v:,.0f}"
    if av >= 1000:
        return f"{v:,.0f}"
    if av >= 100:
        return f"{v:,.1f}"
    return f"{v:.4g}"


def _num_axis(axis: Any, values: Any = None) -> None:
    """Compact number ticks (1.2M, 45K); years (whole numbers 1000-2999) as plain 1990, never 1,990 or 1990.5."""
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    if values is not None and _years(values):
        axis.set_major_locator(MaxNLocator(integer=True, nbins=10, steps=[1, 2, 5, 10]))
        axis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}"))
        return

    def fmt(v: float, _: Any) -> str:
        lo, hi = axis.get_view_interval()
        return _compact(v, max(abs(lo), abs(hi)))

    axis.set_major_formatter(FuncFormatter(fmt))


def _years(values: Any) -> bool:
    vals = [v for v in values if v is not None]
    try:
        return bool(vals) and all(float(v).is_integer() and 1000 <= float(v) <= 2999 for v in vals)
    except (TypeError, ValueError):
        return False


def _date_axis(ax: Any, axis: str = "x") -> None:
    import matplotlib.dates as mdates

    loc = mdates.AutoDateLocator(minticks=4, maxticks=10)
    target = ax.xaxis if axis == "x" else ax.yaxis
    target.set_major_locator(loc)
    target.set_major_formatter(mdates.ConciseDateFormatter(loc))


def _legend(ax: Any, handles: Any = None, labels: Any = None) -> None:
    """A legend between the title (and subtitle) and the plot, so it never covers data: up to 6 entries per row,
    rows balanced (7 entries → 4 + 3) and read left to right, top to bottom. _finish makes room above it."""
    if handles is None:
        handles, labels = ax.get_legend_handles_labels()
    if not handles:
        return
    n = len(handles)
    rows = -(-n // 6)
    ncols = -(-n // rows)
    # matplotlib fills a multi-row legend column by column: reorder so that it reads row by row.
    order = [i * ncols + j for j in range(ncols) for i in range(rows) if i * ncols + j < n]
    handles = [handles[i] for i in order]
    labels = [labels[i] for i in order]
    leg = ax.legend(handles, labels, loc="lower left", bbox_to_anchor=(0, 1.0), ncols=ncols, borderaxespad=0.3, handlelength=1.4, columnspacing=1.6)
    for h in getattr(leg, "legend_handles", []) or []:  # faint scatter points still get a solid legend key
        try:
            h.set_alpha(1)
        except AttributeError:
            pass
    ax._desk_legend = True  # noqa: SLF001


def _finish(fig: Any, ax: Any, ctx: dict[str, Any], title: str, xlabel: str | None, ylabel: str | None) -> None:
    a = ctx["a"]
    title = a.title or (title[:1].upper() + title[1:])
    # Top to bottom: title, subtitle, legend, plot. Heights are measured (in points, which do not depend on the
    # final axes size), so a legend of several rows never runs into the subtitle.
    renderer = fig.canvas.get_renderer()
    to_pt = 72.0 / fig.dpi
    leg = ax.get_legend() if getattr(ax, "_desk_legend", False) else None
    leg_h = leg.get_window_extent(renderer).height * to_pt + 2 if leg is not None else 0.0
    sub = a.subtitle or ctx.get("subtitle")
    sub_h = 0.0
    if sub:
        t = ax.annotate(sub, xy=(0, 1), xycoords="axes fraction", xytext=(0, leg_h + 4), textcoords="offset points", color=INK2, fontsize=12, ha="left", va="bottom", annotation_clip=False)
        sub_h = t.get_window_extent(renderer).height * to_pt + 4
        ax._desk_subtitle = t  # noqa: SLF001
    ax.set_title(title, color=INK, pad=leg_h + sub_h + 10)
    if xlabel is not None or a.xlabel:
        ax.set_xlabel(a.xlabel or xlabel or "")
    if ylabel is not None or a.ylabel:
        ax.set_ylabel(a.ylabel or ylabel or "")
    foot = "Source: " + ctx["source"] + ("  ·  " + "; ".join(ctx["notes"]) if ctx["notes"] else "")
    fig.text(0.01, 0.01, foot[:220], color=INK2, fontsize=10, ha="left", va="bottom")
    if not a.font:
        note = _font_fallback(fig)  # before the layout, which measures the labels in their final font
        if note:
            ctx["notes"].append(note)
    fig.tight_layout(rect=(0, 0.035, 1, 1))


def _save(fig: Any, out: Path, a: Any, ctx: dict[str, Any] | None = None) -> None:
    import matplotlib.pyplot as plt

    fig.savefig(out, format=out.suffix.lower()[1:], dpi=100)
    if ctx is not None:
        ctx["overlaps"] = _overlaps(fig)
        if ctx["overlaps"]:
            ctx["notes"].append("layout: " + ", ".join(ctx["overlaps"]) + " (a shorter --title/--subtitle or fewer series helps)")
    plt.close(fig)


def _overlaps(fig: Any) -> list[str]:
    """Header elements drawn on top of each other (title, subtitle, legend, plot area), as 'legend overlaps subtitle'.
    Measured after saving, when every artist has its final position."""
    renderer = fig.canvas.get_renderer()
    out = []
    for ax in fig.axes[:1]:
        items = [("title", ax.title if ax.get_title() else None), ("subtitle", getattr(ax, "_desk_subtitle", None)), ("legend", ax.get_legend() if getattr(ax, "_desk_legend", False) else None), ("plot", ax.patch)]
        boxes = [(n, art.get_window_extent(renderer)) for n, art in items if art is not None]
        for i, (n1, b1) in enumerate(boxes):
            for n2, b2 in boxes[i + 1 :]:
                w = min(b1.x1, b2.x1) - max(b1.x0, b2.x0)
                h = min(b1.y1, b2.y1) - max(b1.y0, b2.y0)
                if w > 1 and h > 1:
                    out.append(f"{n2} overlaps {n1}")
    return out


def _font_fallback(fig: Any) -> str | None:
    """Labels in scripts DejaVu Sans lacks (Chinese, Japanese, Korean, Thai …) get a system font with wide coverage
    as a fallback (Arial Unicode, PingFang, Microsoft YaHei, Noto …), so they never print as empty boxes."""
    import matplotlib.text as mtext
    from matplotlib import font_manager

    texts = [t for t in fig.findobj(mtext.Text) if t.get_text()]
    chars = {ch for t in texts for ch in t.get_text() if ord(ch) > 0x24F}
    if not chars:
        return None
    try:
        dejavu = font_manager.get_font(font_manager.findfont("DejaVu Sans"))
        missing = sorted(ch for ch in chars if dejavu.get_char_index(ord(ch)) == 0)
    except Exception:  # noqa: BLE001 — a font problem must not stop the chart
        return None
    if not missing:
        return None
    from _render import _unicode_font_file

    f = _unicode_font_file()
    if f is None:
        return f"no installed font has these characters ({''.join(missing[:12])}); pass --font with a font file that does"
    try:
        font_manager.fontManager.addfont(str(f))
        name = font_manager.FontProperties(fname=str(f)).get_name()
    except Exception:  # noqa: BLE001
        return None
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = ["DejaVu Sans", name]
    for t in texts:
        t.set_fontfamily(["DejaVu Sans", name])
    return f"labels use {f.name} for characters DejaVu Sans lacks"


def _label(v: Any, n: int = 22) -> str:
    s = "(blank)" if v is None or v == "" else str(v)
    if hasattr(v, "isoformat") and not isinstance(v, str):
        s = v.isoformat()[:10] if getattr(v, "hour", 0) == 0 and getattr(v, "minute", 0) == 0 else v.isoformat(sep=" ")[:16]
    return s if len(s) <= n else s[: n - 1] + "…"


def _summary(title: str, xs: list[Any], data: dict[str, list[Any]], limit: int = 12) -> str:
    lines = [f"{title}:"]
    names = list(data)
    for i, xv in enumerate(xs[:limit]):
        vals = ", ".join((f"{n}={_compact(data[n][i])}" if len(names) > 1 else _compact(data[n][i])) for n in names if data[n][i] is not None)
        lines.append(f"  {_label(xv, 40)}: {vals}")
    if len(xs) > limit:
        lines.append(f"  … {len(xs) - limit} more points")
    return "\n".join(lines)


# ── chart kinds ─────────────────────────────────────────────────────────


def _bars(ctx: dict[str, Any]) -> tuple[Any, str]:
    import numpy as np

    a = ctx["a"]
    kind = a.kind
    xs, data, vlabel = grouped(ctx)
    if not xs:
        raise SkillError("nothing to plot (no rows with an x value)")
    xtype = ctx["cols"][ctx["x"]]
    categorical = not (_is_num(xtype) or _is_time(xtype) or a.date_unit)
    names = list(data)
    totals = [sum((data[n][i] or 0) for n in names) for i in range(len(xs))]
    order = list(range(len(xs)))
    sort = a.sort or ("value" if categorical else "label")
    if sort == "value":
        order.sort(key=lambda i: -totals[i])
    elif sort == "label":
        order.sort(key=lambda i: _sort_key(xs[i]))
    top = a.top or (20 if categorical else 400)
    agg = a.agg or ("sum" if ctx["ys"] else "count")
    if len(order) == top + 1:
        top += 1  # an "Other" bar standing for a single category only hides its name
    if len(order) > top:
        rest = order[top:]
        order = order[:top]
        if categorical and agg in ("sum", "count", "count_distinct"):
            xs = xs + ["Other"]
            for n in names:
                data[n] = data[n] + [sum(data[n][i] or 0 for i in rest)]
            order.append(len(xs) - 1)
            ctx["notes"].append(f"{len(rest)} smaller categories folded into Other")
        else:
            ctx["notes"].append(f"showing {top} of {top + len(rest)} categories")
    xs = [xs[i] for i in order]
    data = {n: [data[n][i] or 0 for i in order] for n in names}
    fig, ax = _fig(ctx)
    horizontal = kind == "barh"
    pos = np.arange(len(xs))
    stacked = kind == "stacked" or (len(names) > 1 and kind == "stacked")
    n_series = len(names)
    px_per_slot = (a.height if horizontal else a.width) * 0.85 / max(1, len(xs))
    band = min(0.8, 110 / px_per_slot) if n_series > 1 and not stacked else min(0.8, 100 / px_per_slot)
    width = band if stacked or n_series == 1 else band / n_series
    base = np.zeros(len(xs))
    colors = [OTHER if n == "Other" else PALETTE[i % len(PALETTE)] for i, n in enumerate(names)]
    one_color = n_series == 1 and "Other" in xs
    for i, n in enumerate(names):
        vals = np.array(data[n], dtype=float)
        if one_color:  # a single series: the folded "Other" bar is grey
            colors_i: Any = [OTHER if x == "Other" else colors[i] for x in xs]
        else:
            colors_i = colors[i]
        offs = pos if stacked or n_series == 1 else pos - band / 2 + width * (i + 0.5)
        if horizontal:
            ax.barh(offs, vals, height=width * 0.92, left=base if stacked else None, color=colors_i, label=str(n), edgecolor=SURFACE, linewidth=2 if stacked else 0)
        else:
            ax.bar(offs, vals, width=width * 0.92, bottom=base if stacked else None, color=colors_i, label=str(n), edgecolor=SURFACE, linewidth=2 if stacked else 0)
        if stacked:
            base = base + vals
    labels = [_label(x) for x in xs]
    show_vals = a.value_labels == "on" or (a.value_labels == "auto" and len(xs) <= 25 and (n_series == 1 or stacked))
    tops = base if stacked else np.array(data[names[0]], dtype=float)
    if horizontal:
        ax.set_yticks(pos, labels)
        ax.invert_yaxis()
        ax.grid(axis="y", visible=False)
        _num_axis(ax.xaxis)
        if show_vals:
            for p_, v in zip(pos, tops):
                ax.text(v, p_, " " + _compact(v), va="center", ha="left", color=INK2, fontsize=11)
            ax.margins(x=0.08)
    else:
        rot = 0 if max(len(s) for s in labels) * len(labels) < 120 else 35
        ax.set_xticks(pos, labels, rotation=rot, ha="right" if rot else "center")
        ax.grid(axis="x", visible=False)
        _num_axis(ax.yaxis)
        if show_vals:
            for p_, v in zip(pos, tops):
                ax.text(p_, v, _compact(v), va="bottom", ha="center", color=INK2, fontsize=11)
            ax.margins(y=0.08)
    if a.log:
        (ax.set_xscale if horizontal else ax.set_yscale)("log")
    if n_series > 1:
        _legend(ax)
    title = f"{_cap(vlabel)} by {ctx['x']}" + (f" and {ctx['series']}" if ctx["series"] else "")
    _finish(fig, ax, ctx, title, None if horizontal else ctx["x"], vlabel if not horizontal else None)
    if horizontal:
        ax.set_xlabel(a.xlabel or vlabel)
    return fig, _summary(title, xs, data)


def _lines(ctx: dict[str, Any]) -> tuple[Any, str]:
    import numpy as np

    a = ctx["a"]
    xs, data, vlabel = grouped(ctx)
    if not xs:
        raise SkillError("nothing to plot")
    order = sorted(range(len(xs)), key=lambda i: _sort_key(xs[i]))
    xs = [xs[i] for i in order]
    data = {n: [data[n][i] for i in order] for n in data}
    xtype = ctx["cols"][ctx["x"]]
    is_time = _is_time(xtype) or bool(a.date_unit)
    categorical = not is_time and not _is_num(xtype)
    fig, ax = _fig(ctx)
    xv = np.arange(len(xs)) if categorical else xs
    names = list(data)
    colors = [OTHER if n == "Other" else PALETTE[i % len(PALETTE)] for i, n in enumerate(names)]
    if a.kind == "area":
        vals = [np.array([v or 0 for v in data[n]], dtype=float) for n in names]
        if len(names) > 1:
            ax.stackplot(xv, *vals, labels=[str(n) for n in names], colors=colors, alpha=0.85, edgecolor=SURFACE, linewidth=2)
        else:
            ax.fill_between(xv, vals[0], color=colors[0], alpha=0.15, linewidth=0)
            ax.plot(xv, vals[0], color=colors[0])
    else:
        ends = []
        for i, n in enumerate(names):
            # Missing points are skipped, so a series with gaps stays one connected line.
            pts = [(xv[k], v) for k, v in enumerate(data[n]) if v is not None]
            if not pts:
                continue
            px, py = [p[0] for p in pts], [p[1] for p in pts]
            ax.plot(px, py, color=colors[i], label=str(n), marker="o" if len(pts) <= 30 else None, markersize=8, markeredgecolor=SURFACE, markeredgewidth=2)
            ends.append([float(py[-1]), px[-1], _compact(py[-1]), colors[i]])
        if 0 < len(ends) <= 6 and not a.log:
            _end_labels(ax, ends)
    if is_time and len(xs) >= 3:
        # A partial first or last bucket (a month with two days of data) is shaded and labelled, so its drop is not
        # read as a real one.
        for side in ctx.get("partial", []):
            lo_x, hi_x = (xs[-2], xs[-1]) if side == "last" else (xs[0], xs[1])
            ax.axvspan(lo_x, hi_x, color=GRID, alpha=0.6, zorder=0, linewidth=0)
            ax.text(hi_x if side == "last" else lo_x, 0.99, f"partial {a.date_unit} ", transform=ax.get_xaxis_transform(), ha="right" if side == "last" else "left", va="top", color=INK2, fontsize=11)
    if categorical:
        labels = [_label(x) for x in xs]
        step = max(1, len(labels) // 20)
        ax.set_xticks(xv[::step], labels[::step], rotation=35 if len(labels) > 8 else 0, ha="right" if len(labels) > 8 else "center")
    elif is_time:
        _date_axis(ax)
    else:
        _num_axis(ax.xaxis, xs)
    _num_axis(ax.yaxis)
    if a.log:
        ax.set_yscale("log")
    ax.grid(axis="x", visible=False)
    if len(names) > 1:
        _legend(ax)
    title = f"{_cap(vlabel)} by {ctx['x']}" + (f" per {a.date_unit}" if a.date_unit else "") + (f", by {ctx['series']}" if ctx["series"] else "")
    _finish(fig, ax, ctx, title, ctx["x"], None if len(names) > 1 and vlabel == ", ".join(ctx["ys"]) else vlabel)
    return fig, _summary(title, xs, data)


def _end_labels(ax: Any, ends: list[list[Any]]) -> None:
    """Value labels at the right end of each line, nudged apart vertically so they never overlap."""
    ax.autoscale_view()
    lo, hi = ax.get_ylim()
    gap = (hi - lo) * 0.035
    ends.sort(key=lambda e: e[0])
    placed: list[float] = []
    for e in ends:
        y = e[0] if not placed else max(e[0], placed[-1] + gap)
        placed.append(y)
    over = placed[-1] - hi if placed else 0
    if over > 0:  # pushed past the top: shift the stack down
        placed = [p - over for p in placed]
    for (_, x_end, text, color), y in zip(ends, placed):
        ax.annotate(text, xy=(x_end, y), xytext=(8, 0), textcoords="offset points", va="center", color=color, fontsize=11, fontweight="bold", annotation_clip=False)


def _scatter(ctx: dict[str, Any]) -> tuple[Any, str]:
    import numpy as np

    from _duck import ident

    a, con, src, x, ys, series = ctx["a"], ctx["con"], ctx["src"], ctx["x"], ctx["ys"], ctx["series"]
    if not x or len(ys) != 1:
        raise UsageError("--kind scatter needs --x and one --y")
    y = ys[0]
    n = con.sql(f"SELECT count(*) FROM {src} WHERE {ident(x)} IS NOT NULL AND {ident(y)} IS NOT NULL").fetchone()[0]
    samp = ""
    if n > 20000:
        samp = " USING SAMPLE reservoir(20000 ROWS) REPEATABLE (1)"
        ctx["notes"].append(f"random sample of 20,000 of {n:,} points")
    scol = f", CAST({ident(series)} AS VARCHAR)" if series else ""
    rows = con.sql(f"SELECT {ident(x)}, {ident(y)}{scol} FROM (SELECT * FROM {src} WHERE {ident(x)} IS NOT NULL AND {ident(y)} IS NOT NULL){samp}").fetchall()
    fig, ax = _fig(ctx)
    alpha = 0.9 if len(rows) < 500 else 0.5 if len(rows) < 5000 else 0.25
    size = 64 if len(rows) < 500 else 30 if len(rows) < 5000 else 12
    groups: dict[str, list[Any]] = {}
    if series:
        counts: dict[str, int] = {}
        for r in rows:
            counts[r[2]] = counts.get(r[2], 0) + 1
        keep_n = a.top or 6
        if len(counts) == keep_n + 1:
            keep_n += 1
        keep = sorted(counts, key=lambda s: -counts[s])[:keep_n]
        for r in rows:
            groups.setdefault(r[2] if r[2] in keep else "Other", []).append(r)
        if len(counts) > keep_n:
            ctx["notes"].append(f"{len(counts) - keep_n} smaller series shown as Other (--top to change)")
    else:
        groups[y] = rows
    xt = ctx["cols"][x]
    for i, (name, rs) in enumerate(groups.items()):
        xv = [r[0] for r in rs]
        yv = [r[1] for r in rs]
        ax.scatter(xv, yv, s=size, alpha=alpha, color=OTHER if name == "Other" else PALETTE[i], edgecolors=SURFACE, linewidths=1 if size > 20 else 0, label=str(name))
    summary = f"{len(rows):,} points of {y} against {x}"
    if a.trend and not _is_time(xt):
        xv = np.array([float(r[0]) for r in rows])
        yv = np.array([float(r[1]) for r in rows])
        if len(xv) > 2 and np.ptp(xv) > 0:
            k, b = np.polyfit(xv, yv, 1)
            r = np.corrcoef(xv, yv)[0, 1]
            grid = np.linspace(xv.min(), xv.max(), 100)
            sign = "−" if b < 0 else "+"
            ax.plot(grid, k * grid + b, color=INK, linewidth=2, label=f"trend: y = {_compact(k)}·x {sign} {_compact(abs(b))}  (r² = {r * r:.2f})")
            summary += f"; trend y = {k:.4g}·x {sign} {abs(b):.4g}, r = {r:.3f}, r² = {r * r:.3f}"
    if _is_time(xt):
        _date_axis(ax)
    else:
        _num_axis(ax.xaxis, [r[0] for r in rows])
    _num_axis(ax.yaxis)
    if a.log:
        ax.set_yscale("log")
    if len(groups) > 1 or a.trend:
        _legend(ax)
    title = f"{y} vs {x}"
    _finish(fig, ax, ctx, title, x, y)
    return fig, summary


def _hist(ctx: dict[str, Any]) -> tuple[Any, str]:
    import numpy as np

    from _duck import ident

    a, con, src = ctx["a"], ctx["con"], ctx["src"]
    col = (ctx["ys"] or [ctx["x"]])[0] if (ctx["ys"] or ctx["x"]) else None
    if not col:
        raise UsageError("--kind hist needs --y (or --x): the numeric column")
    n = con.sql(f"SELECT count({ident(col)}) FROM {src}").fetchone()[0]
    samp = ""
    if n > 500_000:
        samp = " USING SAMPLE reservoir(500000 ROWS) REPEATABLE (1)"
        ctx["notes"].append(f"random sample of 500,000 of {n:,} values")
    vals = np.array([r[0] for r in con.sql(f"SELECT CAST({ident(col)} AS DOUBLE) FROM (SELECT {ident(col)} FROM {src} WHERE {ident(col)} IS NOT NULL){samp}").fetchall()], dtype=float)
    if not len(vals):
        raise SkillError(f"{col} has no numeric values")
    fig, ax = _fig(ctx)
    bins = a.bins or "auto"
    counts, edges, _ = ax.hist(vals, bins=bins if isinstance(bins, int) else min(80, len(np.histogram_bin_edges(vals, "auto")) - 1) or 10, color=PALETTE[0], edgecolor=SURFACE, linewidth=1.5)
    med, mean = float(np.median(vals)), float(np.mean(vals))
    ax.axvline(med, color=INK, linewidth=2, label=f"median {_compact(med)}")
    ax.axvline(mean, color=INK2, linewidth=2, linestyle=(0, (4, 3)), label=f"mean {_compact(mean)}")
    _legend(ax)
    _num_axis(ax.xaxis)
    _num_axis(ax.yaxis)
    if a.log:
        ax.set_yscale("log")
    ax.grid(axis="x", visible=False)
    title = f"Distribution of {col}"
    _finish(fig, ax, ctx, title, col, "rows")
    q = np.percentile(vals, [0, 25, 50, 75, 100])
    return fig, f"{title}: n={len(vals):,}, min {_compact(q[0])}, q25 {_compact(q[1])}, median {_compact(q[2])}, q75 {_compact(q[3])}, max {_compact(q[4])}, mean {_compact(mean)}, {len(counts)} bins"


def _box(ctx: dict[str, Any]) -> tuple[Any, str]:
    from _duck import ident

    a, con, src, x, ys = ctx["a"], ctx["con"], ctx["src"], ctx["x"], ctx["ys"]
    if len(ys) < 1:
        raise UsageError("--kind box needs --y (and optionally --x to group)")
    fig, ax = _fig(ctx)
    lines = []
    if x:
        y = ys[0]
        n_groups = con.sql(f"SELECT count(DISTINCT CAST({ident(x)} AS VARCHAR)) FROM {src} WHERE {ident(y)} IS NOT NULL").fetchone()[0]
        groups = [r[0] for r in con.sql(f"SELECT CAST({ident(x)} AS VARCHAR) g FROM {src} WHERE {ident(y)} IS NOT NULL GROUP BY 1 ORDER BY count(*) DESC LIMIT {a.top or 12}").fetchall()]
        if n_groups > len(groups):
            ctx["notes"].append(f"the {len(groups)} largest of {n_groups} groups (--top to change)")
        data = []
        for g in groups:
            cond = f"CAST({ident(x)} AS VARCHAR) IS NULL" if g is None else f"CAST({ident(x)} AS VARCHAR) = '{str(g).replace(chr(39), chr(39) * 2)}'"
            vals = [r[0] for r in con.sql(f"SELECT CAST({ident(y)} AS DOUBLE) FROM (SELECT {ident(y)} FROM {src} WHERE {ident(y)} IS NOT NULL AND {cond}) USING SAMPLE reservoir(100000 ROWS) REPEATABLE (1)").fetchall()]
            data.append(vals)
        labels = [f"{_label(g)}\n(n={len(d):,})" for g, d in zip(groups, data)]
        title = f"{y} by {x}"
    else:
        data = [[r[0] for r in con.sql(f"SELECT CAST({ident(y)} AS DOUBLE) FROM (SELECT {ident(y)} FROM {src} WHERE {ident(y)} IS NOT NULL) USING SAMPLE reservoir(100000 ROWS) REPEATABLE (1)").fetchall()] for y in ys]
        labels = [f"{y}\n(n={len(d):,})" for y, d in zip(ys, data)]
        title = "Distribution of " + ", ".join(ys)
    bp = ax.boxplot(data, patch_artist=True, widths=0.5, medianprops={"color": INK, "linewidth": 2}, flierprops={"marker": "o", "markersize": 4, "markerfacecolor": PALETTE[0], "markeredgecolor": SURFACE, "alpha": 0.5}, whiskerprops={"color": INK2}, capprops={"color": INK2}, boxprops={"edgecolor": PALETTE[0]})
    for patch in bp["boxes"]:
        patch.set_facecolor("#cde2fb")
    ax.set_xticks(range(1, len(labels) + 1), labels)
    _num_axis(ax.yaxis)
    if a.log:
        ax.set_yscale("log")
    ax.grid(axis="x", visible=False)
    import numpy as np

    for lab, d in zip(labels, data):
        if d:
            q = np.percentile(d, [25, 50, 75])
            lines.append(f"  {lab.splitlines()[0]}: median {_compact(q[1])}, IQR {_compact(q[0])}–{_compact(q[2])}, n={len(d):,}")
    _finish(fig, ax, ctx, title, x, ys[0] if x else None)
    return fig, title + ":\n" + "\n".join(lines)


def _heatmap(ctx: dict[str, Any]) -> tuple[Any, str]:
    import numpy as np

    from _duck import ident

    a, con, src, x, ys, value = ctx["a"], ctx["con"], ctx["src"], ctx["x"], ctx["ys"], ctx["value"]
    fig, ax = _fig(ctx)
    if x and ys:
        y = ys[0]
        agg = a.agg or ("sum" if value else "count")
        xe = _xexpr(ctx)
        rows = con.sql(f"SELECT {xe}, {ident(y)}, {_agg_sql(agg, value)} FROM {src} WHERE {xe} IS NOT NULL GROUP BY 1, 2").fetchall()
        xs = sorted({r[0] for r in rows}, key=_sort_key)
        yv = sorted({r[1] for r in rows}, key=_sort_key)
        if len(xs) > 60 or len(yv) > 60:
            raise SkillError(f"{len(xs)} × {len(yv)} cells is too many for a heatmap; bucket dates with --date-unit or filter with --where")
        m = np.full((len(yv), len(xs)), np.nan)
        xi = {v: i for i, v in enumerate(xs)}
        yi = {v: i for i, v in enumerate(yv)}
        for xx, yy, vv in rows:
            m[yi[yy], xi[xx]] = vv
        from matplotlib.colors import LinearSegmentedColormap

        cmap = LinearSegmentedColormap.from_list("seq", SEQ)
        im = ax.imshow(m, cmap=cmap, aspect="auto")
        ax.set_xticks(range(len(xs)), [_label(v, 14) for v in xs], rotation=45 if len(xs) > 8 else 0, ha="right" if len(xs) > 8 else "center")
        ax.set_yticks(range(len(yv)), [_label(v, 20) for v in yv])
        title = f"{agg} of {value or 'rows'} by {x} and {y}"
        xl, yl = x, y
        summary = f"{title}: {len(yv)} × {len(xs)} cells, range {_compact(np.nanmin(m))} … {_compact(np.nanmax(m))}"
    else:
        from data_profile import kind_of

        nums = [c for c, t in ctx["cols"].items() if kind_of(t) == "number"][:30]
        if ys:
            nums = ys
        if len(nums) < 2:
            raise UsageError("a correlation heatmap needs at least two numeric columns (or give --x, --y and --value for a pivot)")
        k = len(nums)
        aggs = ", ".join(f"corr(CAST({ident(p)} AS DOUBLE), CAST({ident(q)} AS DOUBLE))" for p in nums for q in nums)
        row = con.sql(f"SELECT {aggs} FROM {src}").fetchone()
        m = np.array([np.nan if v is None else v for v in row], dtype=float).reshape(k, k)
        from matplotlib.colors import LinearSegmentedColormap

        cmap = LinearSegmentedColormap.from_list("div", DIV)
        im = ax.imshow(m, cmap=cmap, vmin=-1, vmax=1, aspect="auto")
        ax.set_xticks(range(k), [_label(c, 14) for c in nums], rotation=45 if k > 6 else 0, ha="right" if k > 6 else "center")
        ax.set_yticks(range(k), [_label(c, 20) for c in nums])
        title = "Correlation between numeric columns"
        xl = yl = None
        pairs = sorted(((abs(m[i, j]), nums[i], nums[j], m[i, j]) for i in range(k) for j in range(i + 1, k) if not np.isnan(m[i, j])), reverse=True)[:8]
        summary = title + ":\n" + "\n".join(f"  {p} ~ {q}: r = {r:.3f}" for _, p, q, r in pairs)
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    rows_n, cols_n = m.shape
    if rows_n * cols_n <= 400:
        norm = im.norm
        for i in range(rows_n):
            for j in range(cols_n):
                v = m[i, j]
                if np.isnan(v):
                    continue
                shade = norm(v)
                dark = shade > 0.62 or (title.startswith("Correlation") and abs(v) > 0.55)
                ax.text(j, i, (f"{v:.2f}" if abs(v) >= 0.005 else "0.00") if title.startswith("Correlation") else _compact(v), ha="center", va="center", fontsize=10 if rows_n * cols_n > 100 else 12, color="white" if dark else INK)
    cb = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cb.outline.set_visible(False)
    _finish(fig, ax, ctx, title, xl, yl)
    return fig, summary


def _pie(ctx: dict[str, Any]) -> tuple[Any, str]:
    a = ctx["a"]
    if ctx["series"] or len(ctx["ys"]) > 1:
        raise UsageError("pie and donut show one --y by --x")
    xs, data, vlabel = grouped(ctx)
    vals = data[next(iter(data))]
    pairs = sorted(((v or 0, x) for x, v in zip(xs, vals)), key=lambda t: -t[0])
    if any(v < 0 for v, _ in pairs):
        raise SkillError("pie charts need non-negative values; use a bar chart")
    top = a.top or 7
    if len(pairs) > top + 1:
        rest = sum(v for v, _ in pairs[top:])
        ctx["notes"].append(f"{len(pairs) - top} smaller categories folded into Other")
        pairs = pairs[:top] + [(rest, "Other")]
    total = sum(v for v, _ in pairs) or 1
    other_share = sum(v for v, x in pairs if x == "Other") / total
    fig, ax = _fig(ctx)
    colors = [OTHER if x == "Other" else PALETTE[i % len(PALETTE)] for i, (_, x) in enumerate(pairs)]
    donut = a.kind == "donut"
    wedges, _t = ax.pie([v for v, _ in pairs], colors=colors, startangle=90, counterclock=False, wedgeprops={"edgecolor": SURFACE, "linewidth": 2, **({"width": 0.38} if donut else {})})
    import numpy as np

    for w, (v, x) in zip(wedges, pairs):
        share = v / total
        if share < 0.03 and a.value_labels != "on":
            continue
        ang = np.deg2rad((w.theta2 + w.theta1) / 2)
        r = 1.12
        ax.text(r * np.cos(ang), r * np.sin(ang), f"{_label(x, 18)}\n{share:.0%}", ha="left" if np.cos(ang) >= 0 else "right", va="center", fontsize=12, color=INK)
    if donut:
        ax.text(0, 0, f"{_compact(total)}\n{vlabel}", ha="center", va="center", fontsize=18, fontweight="bold", color=INK)
    ax.set_aspect("equal")
    ax.grid(False)
    ax.legend(wedges, [f"{_label(x, 24)} ({_compact(v)})" for v, x in pairs], loc="center left", bbox_to_anchor=(1.15, 0.5))
    title = f"{_cap(vlabel)} by {ctx['x']}"
    _finish(fig, ax, ctx, title, None, None)
    summary = _summary(title + f" (total {_compact(total)})", [x for _, x in pairs], {"value": [v for v, _ in pairs]})
    if other_share > 0.4:
        summary += f"\nnote: 'Other' is {other_share:.0%} of the total; a bar chart (--kind barh) of the top categories reads better"
    return fig, summary


DRAW = {"bar": _bars, "barh": _bars, "stacked": _bars, "line": _lines, "area": _lines, "scatter": _scatter, "hist": _hist, "box": _box, "heatmap": _heatmap, "pie": _pie, "donut": _pie}


if __name__ == "__main__":
    run_main(main)
