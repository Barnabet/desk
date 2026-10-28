#!/usr/bin/env python3
"""A real browser for pages that need JavaScript: rendered Markdown, screenshots sized for vision, and PDFs.

The browser is, in order: DESK_BROWSER (a Chrome/Edge executable, set by the skill's browser runtime), an installed
Google Chrome, an installed Microsoft Edge, then Playwright's own Chromium (PLAYWRIGHT_BROWSERS_PATH). Each run
starts a fresh private profile: no cookies, no logins, and cookie banners are left as they are (the screenshot shows
them). It never solves CAPTCHAs or hides that it is automated; --click refuses to submit POST forms.

  render      the rendered page as Markdown, read like fetch.py (--outline, --section, --grep, --offset …)
  screenshot  PNGs of the viewport, the full page (split into vision-sized tiles) or one element; then view_image
  pdf         the page printed to PDF
  check       which browser would be used, and whether it starts

Examples:
  python3 scripts/browse.py render https://example.com/app --wait-for networkidle
  python3 scripts/browse.py render https://example.com/feed --scroll 5 --outline
  python3 scripts/browse.py render https://example.com/list --click "Load more" --click-times 3
  python3 scripts/browse.py screenshot https://example.com --full-page --out shots/example.png
  python3 scripts/browse.py screenshot https://example.com/pricing --element "table" --viewport mobile
  python3 scripts/browse.py pdf https://example.com/report out/report.pdf
  python3 scripts/browse.py check
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from _common import SkillError, UsageError, add_format, output_path, parser, run_main

VIEWPORTS = {"desktop": (1280, 800, False), "mobile": (390, 844, True), "tablet": (820, 1180, True)}
TILE = 1568


def build_parser():
    import _doc

    p = parser(__doc__.split("\n\n")[0], epilog=__doc__[__doc__.index("  render") :])
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, url=True):
        if url:
            sp.add_argument("url")
        sp.add_argument("--wait-for", default="load", help="load (default), networkidle, domcontentloaded, or a CSS selector to wait for")
        sp.add_argument("--wait", type=float, default=0, help="extra seconds to wait after loading")
        sp.add_argument("--scroll", type=int, default=0, help="scroll to the bottom N times (infinite lists)")
        sp.add_argument("--click", metavar="TEXT", help="click the button or link with this text (regex), e.g. 'Load more'")
        sp.add_argument("--click-times", type=int, default=1, help="how many times to click it (default 1)")
        sp.add_argument("--viewport", default="desktop", help="desktop (1280×800), tablet, mobile (390×844) or WxH")
        sp.add_argument("--no-images", action="store_true", help="don't load images (faster text renders)")
        sp.add_argument("--timeout", type=float, default=30, help="navigation timeout in seconds (default 30)")

    r = sub.add_parser("render", help="rendered page → Markdown")
    common(r)
    _doc.add_read_args(r)
    r.add_argument("--links", action="store_true", help="list the rendered page's links")
    r.add_argument("--save", metavar="DIR", help="save the rendered HTML and Markdown into DIR")
    r.add_argument("--refresh", action="store_true", help="render again instead of using the 1 h render cache")
    add_format(r)
    s = sub.add_parser("screenshot", help="PNG screenshots sized for view_image")
    common(s)
    s.add_argument("--out", help="output PNG (default screenshots/<page>.png)")
    s.add_argument("--full-page", action="store_true", help="the whole scrollable page, split into tiles of at most 1568 px")
    s.add_argument("--element", metavar="SELECTOR", help="only this element (CSS selector; the first match)")
    s.add_argument("--max-tiles", type=int, default=8, help="with --full-page: at most this many tiles (default 8)")
    s.add_argument("--no-reveal", action="store_true", help="with --full-page: don't scroll through the page first (it loads content drawn on sight)")
    s.add_argument("--force", action="store_true", help="overwrite existing files")
    add_format(s)
    d = sub.add_parser("pdf", help="print the page to PDF")
    common(d)
    d.add_argument("out", help="output .pdf")
    d.add_argument("--paper", default="A4", help="A4 (default), Letter, Legal, A3")
    d.add_argument("--landscape", action="store_true")
    d.add_argument("--force", action="store_true", help="overwrite an existing file")
    c = sub.add_parser("check", help="which browser would be used, and does it start")
    add_format(c)
    return p


# ── the browser ─────────────────────────────────────────────────────────


NESTED_NOTE = "macOS cannot start it inside another sandbox; Desk's sandbox still contains the page"


def inside_macos_sandbox() -> bool:
    """True when this process already runs under a macOS sandbox, such as Desk's for skill_run. macOS does not nest
    sandboxes: Chrome's own sandbox then fails in every helper process, and Playwright's driver crashes and hangs."""
    if sys.platform != "darwin":
        return False
    try:
        import ctypes

        check = ctypes.CDLL(None).sandbox_check
        check.restype = ctypes.c_int
        check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        return check(os.getpid(), None, 0) == 1  # no operation, SANDBOX_FILTER_NONE: "is this process sandboxed?"
    except (OSError, AttributeError):
        return False


def sandbox_modes(windows: bool | None = None, nested: bool | None = None) -> list[bool]:
    """The chromium_sandbox values to try, in order."""
    windows = os.name == "nt" if windows is None else windows
    if windows:
        return [True]
    if inside_macos_sandbox() if nested is None else nested:
        return [False]
    return [True, False]


def launch(pw):
    """(browser, label, sandboxed): DESK_BROWSER, then installed Chrome, Edge, then Playwright's Chromium.

    Chrome's own sandbox is on (Playwright turns it off unless asked), since pages run untrusted JavaScript. It is off
    only where it cannot start, and the label then says so: inside another macOS sandbox (Desk's), or when a launch
    with it fails on macOS or Linux. On Windows it is never turned off: nothing else contains the page there."""
    tried: list[str] = []
    args = ["--no-first-run", "--no-default-browser-check", "--disable-dev-shm-usage"]
    modes = sandbox_modes()

    def attempt(name: str, label: str, **kw):
        failed = ""
        for sandbox in modes:
            try:
                browser = pw.chromium.launch(headless=True, args=args, chromium_sandbox=sandbox, **kw)
            except Exception as e:  # noqa: BLE001
                failed = _first_line(e)
                tried.append(f"{name}{'' if sandbox else ' without its sandbox'}: {failed}")
                if "is not found" in failed or "doesn't exist" in failed:
                    return None  # not installed: no point retrying without the sandbox
                continue
            if sandbox:
                return browser, label, True
            why = NESTED_NOTE if len(modes) == 1 else f"it failed to start with it: {failed[:120]}"
            return browser, f"{label}, without Chrome's own sandbox ({why})", False
        return None

    exe = os.environ.get("DESK_BROWSER", "").strip()
    if exe:
        if Path(exe).exists():
            got = attempt("DESK_BROWSER", f"{Path(exe).stem} ({exe})", executable_path=exe)
            if got:
                return got
        else:
            tried.append(f"DESK_BROWSER={exe} does not exist")
    for channel, name in (("chrome", "Google Chrome"), ("msedge", "Microsoft Edge")):
        got = attempt(name, f"{name} (installed)", channel=channel)
        if got:
            return got
    got = attempt("Playwright's Chromium", "Playwright's Chromium")
    if got:
        return got
    raise SkillError(
        "no browser available for browse.py. Install Google Chrome or Microsoft Edge, or install the skill's browser "
        "runtime (Desk downloads Chromium once), or set DESK_BROWSER to a Chrome/Edge executable. Meanwhile fetch.py "
        "reads pages without JavaScript. Tried: " + " | ".join(tried)
    )


def _first_line(e: Exception) -> str:
    s = str(e).strip().splitlines()
    return (s[0] if s else type(e).__name__)[:200]


def _viewport(spec: str) -> tuple[int, int, bool]:
    if spec in VIEWPORTS:
        return VIEWPORTS[spec]
    m = re.fullmatch(r"(\d{3,4})\s*[x×]\s*(\d{3,5})", spec)
    if not m:
        raise UsageError("--viewport takes desktop, tablet, mobile or WxH (like 1440x900)")
    return int(m.group(1)), int(m.group(2)), False


class Session:
    """A browser, a fresh context and one page, opened politely on a URL with the requested waits and actions."""

    def __init__(self, args):
        self.args = args
        self.notes: list[str] = []
        self.status: int | None = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise SkillError("playwright is not installed in this environment") from e
        self._pw_cm = sync_playwright()
        self.pw = self._pw_cm.__enter__()
        try:
            self.browser, self.label, self.sandboxed = launch(self.pw)
            w, h, mobile = _viewport(getattr(self.args, "viewport", "desktop"))
            self.context = self.browser.new_context(viewport={"width": w, "height": h}, is_mobile=mobile, has_touch=mobile, device_scale_factor=1, locale="en-US", accept_downloads=False, service_workers="block")
            self.page = self.context.new_page()
            self.page.on("dialog", lambda d: d.dismiss())
            if getattr(self.args, "no_images", False):
                blocked = {"image", "font", "media"}
                self.page.route("**/*", lambda route: route.abort() if route.request.resource_type in blocked else route.continue_())
        except BaseException:
            self._pw_cm.__exit__(*sys.exc_info())
            raise
        return self

    def __exit__(self, *exc):
        try:
            self.browser.close()
        except Exception:  # noqa: BLE001
            pass
        self._pw_cm.__exit__(*exc)

    def open(self, url: str) -> None:
        import _net

        a = self.args
        slot = _net.store().acquire(_net.host(url), _net.DEFAULT_INTERVAL)
        try:
            wait_until = a.wait_for if a.wait_for in ("load", "domcontentloaded", "networkidle", "commit") else "load"
            try:
                resp = self.page.goto(url, wait_until=wait_until, timeout=a.timeout * 1000)
            except Exception as e:  # noqa: BLE001
                msg = _first_line(e)
                if "Download is starting" in msg:
                    raise SkillError(f"{url} is a file download, not a page: use fetch.py (or fetch.py --save DIR)") from None
                if "Timeout" in msg and wait_until == "networkidle":
                    self.notes.append("The page never went network-idle; this is what had rendered by the timeout.")
                    resp = None
                else:
                    raise SkillError(f"{url}: {msg}") from None
        finally:
            _net.store().release(slot)
        self.status = resp.status if resp is not None else None
        if self.status and self.status >= 400:
            self.notes.append(f"The server answered HTTP {self.status}.")
        if a.wait_for not in ("load", "domcontentloaded", "networkidle", "commit"):
            try:
                self.page.wait_for_selector(a.wait_for, timeout=a.timeout * 1000)
            except Exception:  # noqa: BLE001
                self.notes.append(f"'{a.wait_for}' never appeared; this is what had rendered by the timeout.")
        elif wait_until == "load":
            try:
                self.page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:  # noqa: BLE001 — best effort only
                pass
        if a.wait:
            self.page.wait_for_timeout(a.wait * 1000)
        if a.click:
            self.click(a.click, a.click_times)
        if a.scroll:
            self.scroll(a.scroll)

    def click(self, text: str, times: int) -> None:
        rx = re.compile(text, re.I)
        clicked = 0
        for _ in range(max(1, times)):
            loc = self.page.get_by_role("button", name=rx).or_(self.page.get_by_role("link", name=rx)).or_(self.page.get_by_text(rx))
            loc = loc.first
            try:
                if loc.count() == 0 or not loc.is_visible():
                    break
                if loc.evaluate("e => { const f = e.form || (e.closest && e.closest('form')); return !!(f && (f.method || 'get').toLowerCase() === 'post' && (e.type === 'submit' || e.tagName === 'BUTTON')); }"):
                    self.notes.append(f"Not clicking '{text}': it would submit a form (POST).")
                    break
                loc.scroll_into_view_if_needed(timeout=3000)
                loc.click(timeout=5000)
                clicked += 1
            except Exception as e:  # noqa: BLE001
                self.notes.append(f"Clicking '{text}' stopped: {_first_line(e)}")
                break
            try:
                self.page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:  # noqa: BLE001
                pass
            self.page.wait_for_timeout(300)
        self.notes.append(f"Clicked '{text}' {clicked} time(s)." if clicked else f"Found nothing to click matching '{text}'.")

    def scroll(self, n: int) -> None:
        last = -1
        same = 0
        for _ in range(n):
            h = self.page.evaluate("() => { window.scrollTo(0, document.documentElement.scrollHeight); return document.documentElement.scrollHeight; }")
            try:
                self.page.wait_for_load_state("networkidle", timeout=2500)
            except Exception:  # noqa: BLE001
                pass
            self.page.wait_for_timeout(400)
            if h == last:
                same += 1
                if same >= 2:
                    break
            last = h
        self.page.evaluate("() => window.scrollTo(0, 0)")

    def reveal(self, max_px: int, budget: float = 8.0) -> int:
        """Scrolls down the page one screen at a time (then back to the top) so content that appears only when it
        scrolls into view (lazy images, fade-in sections, charts drawn on sight) is there for a full-page
        screenshot. Returns how many screens were scrolled."""
        t0 = time.time()
        step = max(200, int((self.page.viewport_size or {"height": 800})["height"] * 0.85))
        y, n = 0, 0
        while time.time() - t0 < budget:
            height = self.page.evaluate("() => document.documentElement.scrollHeight")
            if y >= min(height, max_px):
                break
            y += step
            n += 1
            self.page.evaluate(f"() => window.scrollTo(0, {y})")
            self.page.wait_for_timeout(250)
        try:
            self.page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:  # noqa: BLE001 — best effort
            pass
        self.page.evaluate("() => window.scrollTo(0, 0)")
        self.page.wait_for_timeout(300)
        return n

    def stitched(self, max_px: int) -> bytes:
        """A full-page PNG captured one screen at a time while scrolling, for pages that paint content only while it
        is on screen (a normal full-page capture shows those parts empty). Fixed and sticky elements (headers,
        banners) are hidden after the first screen so they don't cover every part."""
        import io

        from PIL import Image

        vp = self.page.viewport_size or {"width": 1280, "height": 800}
        vw, vh = vp["width"], vp["height"]
        total = min(int(self.page.evaluate("() => document.documentElement.scrollHeight")), max_px)
        canvas = Image.new("RGB", (vw, max(total, vh)), "white")
        y, first = 0, True
        while True:
            self.page.evaluate(f"() => window.scrollTo(0, {y})")
            self.page.wait_for_timeout(400)
            top = int(self.page.evaluate("() => window.scrollY"))
            shot = Image.open(io.BytesIO(self.page.screenshot()))
            canvas.paste(shot.convert("RGB"), (0, top))
            if first:
                self.page.evaluate("() => { for (const e of document.querySelectorAll('body *')) { const p = getComputedStyle(e).position; if (p === 'fixed' || p === 'sticky') e.style.setProperty('opacity', '0', 'important'); } }")
                first = False
            if top + vh >= total or top + vh <= y:
                break
            y = top + vh
        buf = io.BytesIO()
        canvas.crop((0, 0, vw, min(canvas.height, max(total, top + vh)))).save(buf, "PNG")
        return buf.getvalue()


# ── commands ────────────────────────────────────────────────────────────


def cmd_render(args) -> int:
    import _doc
    import _net

    url = _net.normalize_input_url(args.url)
    key = ["render", _doc.CODE_VERSION, url, args.wait_for, args.wait, args.scroll, args.click, args.click_times, args.viewport, args.no_images]
    hit = None if args.refresh else _net.memo_get("render", key)
    if hit:
        html, final, notes, label, fetched = hit["html"], hit["final"], hit["notes"], hit["label"], hit["fetched"]
    else:
        with Session(args) as s:
            s.open(url)
            html, final, notes, label = s.page.content(), s.page.url, s.notes, s.label
        fetched = _doc.iso()
        _net.memo_put("render", key, {"html": html, "final": final, "notes": notes, "label": label, "fetched": fetched}, 3600)
    body = html.encode("utf-8")
    doc = _doc.extract(body, "text/html; charset=utf-8", url, final, full=args.full, inline_links=args.inline_links)
    doc.fetched = fetched
    doc.notes = [n for n in doc.notes if "need JavaScript" not in n] + notes + [f"Rendered with {label}."]
    if len(html) < 60_000 and (_net.CHALLENGE_TITLE.search(html[:20_000]) or _net.CHALLENGE_200.search(html[:20_000])):
        doc.notes.append("The page shows an anti-bot challenge. Don't try to get around it: use archive.py read URL or another source.")
    out = []
    if args.save:
        from _common import output_dir

        files = _doc.save(doc, body, output_dir(args.save))
        out.append("Saved: " + ", ".join(str(f) for f in files))
    if args.links:
        out.append(_doc.render_links(final, body, "text/html", args.format))
    if not args.links or args.outline or args.section or args.grep:
        out.append(_doc.render(doc, args, args.format))
    print("\n\n".join(out))
    return 0


def _tiles(png: bytes, stem: Path, max_tiles: int, force: bool) -> list[dict]:
    import io

    from PIL import Image

    im = Image.open(io.BytesIO(png))
    im.load()
    if im.width > TILE:
        im = im.resize((TILE, round(im.height * TILE / im.width)))
    tiles = []
    step = TILE - 60  # a small overlap so no line is cut in two without context
    top = 0
    n = 0
    while top < im.height and n < max_tiles:
        n += 1
        box = (0, top, im.width, min(im.height, top + TILE))
        path = stem.with_name(f"{stem.stem}-{n}.png") if (im.height > TILE) else stem.with_suffix(".png")
        path = output_path(path, force=force)
        tile = im.crop(box)
        tile.save(path, "PNG", optimize=True)
        tiles.append({"path": str(path), "width": box[2], "height": box[3] - box[1], "top": top, "blank": _blank(tile)})
        top += step
    left = max(0, im.height - top) if top < im.height else 0
    return tiles if not left else tiles + [{"omitted_px": left}]


def _blank_parts(png: bytes, max_tiles: int) -> list[int]:
    """Indexes of the tile-sized parts of a full-page PNG that are (almost) empty."""
    import io

    from PIL import Image

    im = Image.open(io.BytesIO(png))
    scale = TILE / im.width if im.width > TILE else 1.0
    step = int((TILE - 60) / scale)
    return [i for i, top in enumerate(range(0, im.height, step)) if i < max_tiles and _blank(im.crop((0, top, im.width, min(im.height, top + int(TILE / scale)))))]


def _blank(im) -> bool:
    """True for an (almost) single-colour image: under 0.5 % of its pixels differ visibly from the dominant colour."""
    small = im.convert("L").resize((max(1, im.width // 8), max(1, im.height // 8)))
    hist = small.histogram()
    peak = max(range(256), key=lambda v: hist[v])
    near = sum(hist[max(0, peak - 12) : peak + 13])
    return near >= 0.995 * small.width * small.height


def cmd_screenshot(args) -> int:
    import _doc
    import _net

    url = _net.normalize_input_url(args.url)
    out = Path(args.out) if args.out else Path("screenshots") / f"{_doc.slug(url, 60)}.png"
    if out.suffix.lower() != ".png":
        out = out.with_suffix(".png")
    out.parent.mkdir(parents=True, exist_ok=True)
    with Session(args) as s:
        s.open(url)
        if args.element:
            loc = s.page.locator(args.element).first
            if loc.count() == 0:
                raise SkillError(f"no element matches {args.element!r} on {s.page.url}")
            png = loc.screenshot(timeout=args.timeout * 1000)
        else:
            if args.full_page and not args.no_reveal:
                s.reveal(args.max_tiles * TILE * 2)
            png = s.page.screenshot(full_page=args.full_page, timeout=args.timeout * 1000)
            if args.full_page and not args.no_reveal and _blank_parts(png, args.max_tiles):
                alt = s.stitched(args.max_tiles * TILE)
                if len(_blank_parts(alt, args.max_tiles)) < len(_blank_parts(png, args.max_tiles)):
                    png = alt
                    s.notes.append("Captured screen by screen while scrolling: this page paints some parts only while they are on screen.")
        final, notes, label, title = s.page.url, s.notes, s.label, s.page.title()
    tiles = _tiles(png, out, args.max_tiles, args.force)
    omitted = next((t["omitted_px"] for t in tiles if "omitted_px" in t), 0)
    tiles = [t for t in tiles if "path" in t]
    blank = [Path(t["path"]).name for t in tiles if t.get("blank")]
    if blank:
        notes.append(f"{', '.join(blank)} {'is' if len(blank) == 1 else 'are'} almost empty (content the page draws only while it is on screen, or a long blank area): skip {'it' if len(blank) == 1 else 'them'}; for a section, use --element SELECTOR or browse.py render --grep.")
    if args.format == "json":
        print(json.dumps({"url": url, "final_url": final, "title": title, "browser": label, "images": tiles, "omitted_px": omitted, "notes": notes}, ensure_ascii=False, indent=2))
        return 0
    lines = [f"{len(tiles)} screenshot(s) of {final} ({title[:80]}) with {label}:"]
    lines += [f"- {t['path']} · {t['width']}×{t['height']}" + (f" · from y={t['top']}" if len(tiles) > 1 else "") + (" · almost empty" if t.get("blank") else "") for t in tiles]
    if omitted:
        lines.append(f"({omitted} px further down were not captured: raise --max-tiles or use --element)")
    lines += [f"Note: {n}" for n in notes]
    lines.append(_doc.VIEW_HINT)
    print("\n".join(lines))
    return 0


def cmd_pdf(args) -> int:
    import _net

    url = _net.normalize_input_url(args.url)
    out = output_path(args.out, force=args.force)
    with Session(args) as s:
        s.open(url)
        try:
            s.page.pdf(path=str(out), format=args.paper, landscape=args.landscape, print_background=True, margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"})
        except Exception as e:  # noqa: BLE001
            raise SkillError(f"printing to PDF failed: {_first_line(e)}") from None
        label = s.label
    print(f"Saved {out} ({out.stat().st_size:,} bytes) from {url} with {label}. Read it with fetch.py {out} or the pdf-toolkit skill.")
    return 0


def cmd_check(args) -> int:
    t0 = time.time()
    with Session(argparse_ns(viewport="desktop")) as s:
        s.page.set_content("<p id=x>ok</p><script>document.getElementById('x').textContent='javascript ok'</script>")
        text = s.page.inner_text("#x")
        version = s.browser.version
        label, sandboxed = s.label, s.sandboxed
    ok = text == "javascript ok"
    info = {"browser": label, "version": version, "javascript": ok, "chrome_sandbox": sandboxed, "seconds": round(time.time() - t0, 2)}
    if args.format == "json":
        print(json.dumps(info, indent=2))
    else:
        print(f"browser: {label} · version {version} · JavaScript {'works' if ok else 'FAILED'} · started in {info['seconds']}s")
    return 0 if ok else 1


def argparse_ns(**kw):
    import argparse

    return argparse.Namespace(**kw)


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == "check":
        return cmd_check(args)
    _viewport(args.viewport)
    return {"render": cmd_render, "screenshot": cmd_screenshot, "pdf": cmd_pdf}[args.cmd](args)


if __name__ == "__main__":
    run_main(main)
