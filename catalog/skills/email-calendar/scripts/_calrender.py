"""Calendar pictures for the agent to look at: a week view (time grid, all-day strip, side-by-side overlapping
events, conflicts outlined in red) and a month view, drawn with Typst and saved as PNG sized for vision.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from _mailrender import esc

PALETTE = ["#4f7fd9", "#2e9d6a", "#d9822b", "#9b59b6", "#c0392b", "#16a085", "#7f8c8d", "#b7950b"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _lanes(evs: list[dict[str, Any]]) -> None:
    """Assigns lane / lanes to overlapping timed events of one day (interval partitioning per cluster)."""
    evs.sort(key=lambda e: (e["s"], -(e["e"] - e["s"])))
    cluster: list[dict[str, Any]] = []
    cluster_end = -1.0
    lane_ends: list[float] = []

    def close() -> None:
        n = max((e["lane"] for e in cluster), default=0) + 1
        for e in cluster:
            e["lanes"] = n

    for e in evs:
        if cluster and e["s"] >= cluster_end:
            close()
            cluster, lane_ends = [], []
        for i, end in enumerate(lane_ends):
            if end <= e["s"]:
                e["lane"] = i
                lane_ends[i] = e["e"]
                break
        else:
            e["lane"] = len(lane_ends)
            lane_ends.append(e["e"])
        cluster.append(e)
        cluster_end = max(cluster_end, e["e"])
    if cluster:
        close()


def _color(src: str, sources: list[str]) -> str:
    return PALETTE[sources.index(src) % len(PALETTE)] if src in sources else PALETTE[0]


def week_typst(week_start: date, occs: list[dict[str, Any]], tzname: str, sources: list[str], conflicts: set[int], title: str | None = None) -> str:
    W, H = 1000.0, 680.0
    gutter, head_h = 38.0, 34.0
    days = [week_start + timedelta(days=i) for i in range(7)]
    timed: dict[int, list[dict[str, Any]]] = {i: [] for i in range(7)}
    allday: list[tuple[int, int, dict[str, Any]]] = []
    h0, h1 = 8.0, 18.0
    for k, o in enumerate(occs):
        s, e = o["start_local"], o["end_local"]
        if o["all_day"]:
            sd = s.date() if isinstance(s, datetime) else s
            ed = (e.date() if isinstance(e, datetime) else e) - timedelta(days=1)
            a = max(0, (sd - week_start).days)
            b = min(6, (max(ed, sd) - week_start).days)
            if b >= 0 and a <= 6:
                allday.append((a, b, o))
            continue
        # Split timed events across midnight.
        cur = s
        while cur < e:
            day_end = datetime.combine(cur.date() + timedelta(days=1), time(0), tzinfo=cur.tzinfo)
            seg_end = min(e, day_end)
            di = (cur.date() - week_start).days
            if 0 <= di <= 6:
                sh = cur.hour + cur.minute / 60
                eh = (seg_end - datetime.combine(cur.date(), time(0), tzinfo=cur.tzinfo)).total_seconds() / 3600
                timed[di].append({"s": sh, "e": max(eh, sh + 0.25), "o": o, "k": k})
                h0 = min(h0, float(int(sh)))
                h1 = max(h1, min(24.0, float(int(eh + 0.999))))
            cur = seg_end
    for di in timed:
        _lanes(timed[di])
    rows_ad = 0
    ad_rows: list[list[tuple[int, int]]] = []
    placed_ad = []
    for a, b, o in sorted(allday, key=lambda x: (x[0], -(x[1] - x[0]))):
        for r, used in enumerate(ad_rows):
            if all(b < x or a > y for x, y in used):
                used.append((a, b))
                placed_ad.append((r, a, b, o))
                break
        else:
            ad_rows.append([(a, b)])
            placed_ad.append((len(ad_rows) - 1, a, b, o))
    rows_ad = min(len(ad_rows), 4)
    ad_h = 13.0
    grid_top = head_h + rows_ad * ad_h + (4 if rows_ad else 0)
    col_w = (W - gutter) / 7
    hour_h = (H - grid_top - 4) / max(1.0, h1 - h0)
    parts: list[str] = []
    for i, d in enumerate(days):
        x = gutter + i * col_w
        fill = "luma(248)" if i >= 5 else "white"
        parts.append(f"#place(top + left, dx: {x:.1f}pt, dy: {grid_top:.1f}pt)[#rect(width: {col_w:.1f}pt, height: {H - grid_top:.1f}pt, fill: {fill}, stroke: 0.4pt + luma(215))]")
        parts.append(f"#place(top + left, dx: {x:.1f}pt, dy: 2pt)[#box(width: {col_w:.1f}pt)[#align(center)[#text(size: 9pt, weight: \"bold\")[{DAYS[i]}] #text(size: 9pt)[{d.strftime('%d %b')}]]]]")
    hh = int(h0)
    while hh <= h1:
        y = grid_top + (hh - h0) * hour_h
        parts.append(f"#place(top + left, dx: {gutter:.1f}pt, dy: {y:.1f}pt)[#line(length: {W - gutter:.1f}pt, stroke: 0.3pt + luma(225))]")
        if hh < 24 and hh < h1:
            parts.append(f"#place(top + left, dx: 2pt, dy: {y - 4:.1f}pt)[#text(size: 7pt, fill: luma(120))[{hh:02d}:00]]")
        hh += 1
    for r, a, b, o in placed_ad:
        if r >= rows_ad:
            continue
        x = gutter + a * col_w + 1
        w = (b - a + 1) * col_w - 2
        y = head_h + r * ad_h
        c = _color(o["source"], sources)
        parts.append(f'#place(top + left, dx: {x:.1f}pt, dy: {y:.1f}pt)[#box(width: {w:.1f}pt, height: {ad_h - 2:.1f}pt, fill: rgb("{c}").lighten(55%), stroke: 0.5pt + rgb("{c}"), inset: (x: 3pt, y: 1.5pt), clip: true)[#text(size: 7pt)[{esc(o["summary"])}]]]')
    if len(ad_rows) > rows_ad:
        parts.append(f"#place(top + left, dx: 2pt, dy: {head_h + (rows_ad - 1) * ad_h:.1f}pt)[#text(size: 6pt, fill: luma(100))[+{sum(1 for p in placed_ad if p[0] >= rows_ad)}]]")
    for di, evs in timed.items():
        for ev in evs:
            o = ev["o"]
            lanes = ev.get("lanes", 1)
            lw = (col_w - 4) / lanes
            x = gutter + di * col_w + 2 + ev["lane"] * lw
            y = grid_top + (ev["s"] - h0) * hour_h + 0.5
            h = max(9.0, (ev["e"] - ev["s"]) * hour_h - 1)
            c = _color(o["source"], sources)
            stroke = '1.4pt + rgb("#d11a1a")' if ev["k"] in conflicts else f'0.5pt + rgb("{c}")'
            label = f"{o['start_local'].strftime('%H:%M')} {o['summary']}"
            loc = f" #linebreak() #text(fill: luma(80))[{esc(o['location'][:60])}]" if o.get("location") and h > 26 else ""
            if o.get("status") == "TENTATIVE" and ev["k"] not in conflicts:
                stroke = f'(paint: rgb("{c}"), thickness: 0.8pt, dash: "dashed")'
            parts.append(f'#place(top + left, dx: {x:.1f}pt, dy: {y:.1f}pt)[#box(width: {lw - 1:.1f}pt, height: {h:.1f}pt, fill: rgb("{c}").lighten(70%), stroke: {stroke}, radius: 1.5pt, inset: 2pt, clip: true)[#text(size: 6.8pt)[{esc(label)}{loc}]]]')
    heading = title or f"Week of {days[0].isoformat()} – {days[-1].isoformat()} ({tzname})"
    legend = "  ".join(f'#box(width: 7pt, height: 7pt, fill: rgb("{_color(s, sources)}")) {esc(s)}' for s in sources[:6])
    red = "  #box(width: 7pt, height: 7pt, stroke: 1.2pt + rgb(\"#d11a1a\")) conflict" if conflicts else ""
    return (
        f"#set page(width: {W + 40}pt, height: {H + 70}pt, margin: 20pt, fill: white)\n"
        '#set text(font: ("Helvetica Neue", "Helvetica", "Arial", "Segoe UI", "Liberation Sans", "DejaVu Sans", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Libertinus Serif"), size: 8pt, fallback: true)\n'
        f"#text(size: 12pt, weight: \"bold\")[{esc(heading)}] #h(1fr) #text(size: 7.5pt)[{legend}{red}]\n#v(4pt)\n"
        f"#block(width: {W}pt, height: {H}pt)[\n" + "\n".join(parts) + "\n]\n"
    )


def month_typst(month_start: date, occs: list[dict[str, Any]], tzname: str, sources: list[str], conflicts: set[int]) -> str:
    W, H = 1000.0, 700.0
    first = month_start - timedelta(days=month_start.weekday())
    nxt = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    weeks = ((nxt - first).days + 6) // 7
    cell_w, head_h = W / 7, 18.0
    cell_h = (H - head_h) / weeks
    by_day: dict[date, list[tuple[int, dict[str, Any]]]] = {}
    for k, o in enumerate(occs):
        s, e = o["start_local"], o["end_local"]
        sd = s.date() if isinstance(s, datetime) else s
        if o["all_day"]:
            ed = (e.date() if isinstance(e, datetime) else e) - timedelta(days=1)
        else:
            ed = (e - timedelta(seconds=1)).date() if isinstance(e, datetime) else sd
        d = sd
        while d <= max(ed, sd):
            by_day.setdefault(d, []).append((k, o))
            d += timedelta(days=1)
    parts = []
    for i, name in enumerate(DAYS):
        parts.append(f"#place(top + left, dx: {i * cell_w:.1f}pt, dy: 0pt)[#box(width: {cell_w:.1f}pt)[#align(center)[#text(size: 9pt, weight: \"bold\")[{name}]]]]")
    max_lines = max(2, int((cell_h - 18) / 10.2))
    for w in range(weeks):
        for i in range(7):
            d = first + timedelta(days=w * 7 + i)
            x, y = i * cell_w, head_h + w * cell_h
            inside = d.month == month_start.month
            fill = "white" if inside and i < 5 else ("luma(248)" if inside else "luma(238)")
            items = sorted(by_day.get(d, []), key=lambda t: (not t[1]["all_day"], t[1]["start_local"].timestamp() if isinstance(t[1]["start_local"], datetime) else 0))
            lines = []
            shown = items[:max_lines] if len(items) <= max_lines else items[: max_lines - 1]  # keep a line for "+N more"
            for k, o in shown:
                c = _color(o["source"], sources)
                t = "" if o["all_day"] else o["start_local"].strftime("%H:%M") + " "
                mark = '#text(fill: rgb("#d11a1a"))[● ]' if k in conflicts else ""
                lines.append(f'#box(width: 5pt, height: 5pt, fill: rgb("{c}")) {mark}{esc(t + o["summary"])}')
            if len(items) > len(shown):
                lines.append(f"#text(fill: luma(90), weight: \"bold\")[+{len(items) - len(shown)} more]")
            body = " #linebreak() ".join(lines)
            num_color = "black" if inside else "luma(150)"
            parts.append(
                f"#place(top + left, dx: {x:.1f}pt, dy: {y:.1f}pt)[#rect(width: {cell_w:.1f}pt, height: {cell_h:.1f}pt, fill: {fill}, stroke: 0.4pt + luma(210), inset: 3pt)[#text(size: 8pt, weight: \"bold\", fill: {num_color})[{d.day}] #linebreak() #block(height: {cell_h - 16:.1f}pt, clip: true)[#text(size: 6.8pt)[{body}]]]]"
            )
    heading = f"{month_start.strftime('%B %Y')} ({tzname})"
    legend = "  ".join(f'#box(width: 7pt, height: 7pt, fill: rgb("{_color(s, sources)}")) {esc(s)}' for s in sources[:6])
    return (
        f"#set page(width: {W + 40}pt, height: {H + 70}pt, margin: 20pt, fill: white)\n"
        '#set text(font: ("Helvetica Neue", "Helvetica", "Arial", "Segoe UI", "Liberation Sans", "DejaVu Sans", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Libertinus Serif"), size: 8pt, fallback: true)\n'
        f"#text(size: 12pt, weight: \"bold\")[{esc(heading)}] #h(1fr) #text(size: 7.5pt)[{legend}]\n#v(4pt)\n"
        f"#block(width: {W}pt, height: {H}pt)[\n" + "\n".join(parts) + "\n]\n"
    )


def render_views(occs: list[dict[str, Any]], view: str, start: date, end: date, tzname: str, outdir: Path, conflicts: set[int], force: bool = False, max_images: int = 8) -> list[Path]:
    from _common import SkillError
    from _render import VISION_EDGE, typst_compile

    sources = []
    for o in occs:
        if o["source"] not in sources:
            sources.append(o["source"])
    outs: list[Path] = []
    if view == "week":
        cur = start - timedelta(days=start.weekday())
        while cur < end and len(outs) < max_images:
            wk = [o for o in occs if _overlaps(o, cur, cur + timedelta(days=7))]
            src = week_typst(cur, wk, tzname, sources, conflicts)
            png = typst_compile(src, fmt="png", ppi=VISION_EDGE * 72.0 / 1040.0)[0]
            p = outdir / f"week-{cur.isoformat()}.png"
            if p.exists() and not force:
                raise SkillError(f"{p} already exists; pass --force or choose another folder")
            p.write_bytes(png)
            outs.append(p)
            cur += timedelta(days=7)
    else:
        cur = start.replace(day=1)
        while cur < end and len(outs) < max_images:
            nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
            first = cur - timedelta(days=cur.weekday())
            mo = [o for o in occs if _overlaps(o, first, nxt + timedelta(days=7))]
            src = month_typst(cur, mo, tzname, sources, conflicts)
            png = typst_compile(src, fmt="png", ppi=VISION_EDGE * 72.0 / 1040.0)[0]
            p = outdir / f"month-{cur.strftime('%Y-%m')}.png"
            if p.exists() and not force:
                raise SkillError(f"{p} already exists; pass --force or choose another folder")
            p.write_bytes(png)
            outs.append(p)
            cur = nxt
    return outs


def _overlaps(o: dict[str, Any], a: date, b: date) -> bool:
    s, e = o["start_local"], o["end_local"]
    sd = s.date() if isinstance(s, datetime) else s
    ed = e.date() if isinstance(e, datetime) else e
    return sd < b and ed >= a
