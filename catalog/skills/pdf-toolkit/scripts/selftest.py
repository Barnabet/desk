#!/usr/bin/env python3
"""End-to-end self test for the pdf-toolkit skill: builds fixture PDFs in a temp folder, runs every script the way
an agent does (python3 scripts/<name>.py …), and checks the real outputs. No network. Prints "ok: N checks in Xs".

  python3 scripts/selftest.py              # run everything
  python3 scripts/selftest.py --keep       # keep the temp folder and print its path
  python3 scripts/selftest.py --fixtures DIR   # only write the fixture PDFs into DIR
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
# Scripts run from the selftest's temp dir unless a check names another, so default outputs never land in the
# skill's own folder (read-only when the skill is built into Desk).
WORK: Path | None = None

SECRET_EMAIL = "jane.doe@example.com"
SECRET_SSN = "123-45-6789"
SECRET_NAME = "Jane Doe"


# ── fixtures ────────────────────────────────────────────────────────────


def _png(path: Path, size=(480, 320), text="Chart", color=(40, 110, 200)) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    for i in range(0, size[0], 40):
        d.rectangle([i, size[1] - (i * 7 % size[1]) - 20, i + 30, size[1] - 10], fill=color)
    try:
        font = ImageFont.load_default(size=36)
    except TypeError:
        font = ImageFont.load_default()
    d.text((20, 20), text, fill="black", font=font)
    img.save(path)
    return path


def make_report(d: Path, name: str = "report", west_q2: str = "150") -> Path:
    """A 5-page Typst report: headings (outline), a ruled table, an image, a link, personal data to redact."""
    import typst

    if not (d / "chart.png").exists():
        _png(d / "chart.png")
    src = f"""
#set document(title: "Quarterly Report", author: "Desk Test")
#set page(paper: "a4", margin: 2cm, numbering: "1")
#set text(font: "Libertinus Serif", size: 11pt)
#set heading(numbering: "1.")
= Introduction
This quarterly report covers revenue, costs and hiring. Contact {SECRET_NAME} at #"{SECRET_EMAIL}" or call +1 415 555 0134.
Her social security number is {SECRET_SSN}. The unique marker word is ZEPHYRINE.

#link("https://example.org/report")[Full report online]

= Revenue
#table(
  columns: 3,
  stroke: 0.5pt,
  [*Region*], [*Q1*], [*Q2*],
  [North], [120], [135],
  [South], [98], [101],
  [West], [143], [{west_q2}],
)

#figure(image("chart.png", width: 60%), caption: [Revenue by month])
#pagebreak()
= Costs
Costs rose by 4 percent. {"Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 12}
#pagebreak()
#set page(flipped: true)
= Wide appendix
This landscape page holds a wide table.
#table(columns: 6, stroke: 0.5pt, [A], [B], [C], [D], [E], [F], [1], [2], [3], [4], [5], [6])
#pagebreak()
#set page(flipped: false)
= Hiring
We hired 12 engineers. The unique marker word is QUOKKA.
#pagebreak()
= Summary
All targets were met. Contact #"{SECRET_EMAIL}" for details.
"""
    (d / f"{name}.typ").write_text(src, encoding="utf-8")
    data = typst.compile(str(d / f"{name}.typ"), root=str(d), format="pdf", ignore_system_fonts=True)
    out = d / f"{name}.pdf"
    out.write_bytes(data)
    return out


def make_big(d: Path, pages: int = 220) -> Path:
    """A long Typst document: a chapter every 50 pages, a section every 10, a ruled table every 10 pages and a
    unique marker "P0001X" … on every page, for the map, the cache and the timing checks."""
    import typst

    parts = ['#set page(paper: "a4", margin: 2cm, numbering: "1")', "#set text(size: 10pt)", '#set heading(numbering: "1.1")']
    for p in range(1, pages + 1):
        if p % 50 == 1:
            parts.append(f"= Chapter {p // 50 + 1}")
        if p % 10 == 1:
            parts.append(f"== Section from page {p}")
        parts.append(f"Page marker P{p:04d}X. #lorem(330)")
        if p % 10 == 5:
            rows = ", ".join(f"[Item {p}-{r}], [{r * 17 % 97}], [{r * 31 % 89}.5]" for r in range(1, 9))
            parts.append(f"#table(columns: 3, stroke: 0.5pt, [*Item*], [*Qty*], [*Price*], {rows})")
        parts.append("#pagebreak(weak: true)")
    out = d / "big.pdf"
    out.write_bytes(typst.compile("\n\n".join(parts).encode(), ignore_system_fonts=True))
    return out


def make_photo(d: Path) -> Path:
    """A one-page PDF holding a 2400×1800 photo-like image drawn 3 inches wide (far above screen resolution)."""
    import typst
    from PIL import Image, ImageFilter

    img = Image.effect_noise((2400, 1800), 60).convert("RGB").filter(ImageFilter.GaussianBlur(2))
    img.save(d / "photo.png")
    (d / "photo.typ").write_text('#set page(paper: "a5")\nA photo:\n#image("photo.png", width: 3in)\n', encoding="utf-8")
    out = d / "photo.pdf"
    out.write_bytes(typst.compile(str(d / "photo.typ"), root=str(d), format="pdf", ignore_system_fonts=True))
    return out


def make_scan(d: Path) -> Path:
    """Page 1 is text (Typst), page 2 is only an image of text (a 'scan')."""
    import typst
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("L", (1240, 1754), 255)
    dr = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=48)
    except TypeError:
        font = ImageFont.load_default()
    for i, line in enumerate(["SCANNED INVOICE 4711", "Total due: 99.00 EUR", "Thank you"]):
        dr.text((120, 200 + i * 90), line, fill=0, font=font)
    img.save(d / "scan.png")
    src = '#set page(paper: "a4")\nA normal text page.\n#pagebreak()\n#set page(margin: 0pt)\n#image("scan.png", width: 100%, height: 100%)\n'
    (d / "scan.typ").write_text(src, encoding="utf-8")
    out = d / "scan.pdf"
    out.write_bytes(typst.compile(str(d / "scan.typ"), root=str(d), format="pdf", ignore_system_fonts=True))
    return out


def _stream(w, data: bytes, extra: dict | None = None):
    from pypdf.generic import DecodedStreamObject, NameObject

    s = DecodedStreamObject()
    s.set_data(data)
    for k, v in (extra or {}).items():
        s[NameObject(k)] = v
    return w._add_object(s)


def make_simple(d: Path) -> Path:
    """Standard-14 Helvetica text without /Widths, a TJ array with kerning, text inside a form XObject,
    a shifted media box on page 2 and a /Rotate 90 page 3."""
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject

    w = PdfWriter()
    helv = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    fonts = DictionaryObject({NameObject("/F1"): helv})
    form = _stream(w, b"BT /F1 12 Tf 0 0 Td (Inside form: secret-token-42 stays hidden) Tj ET", {
        "/Type": NameObject("/XObject"), "/Subtype": NameObject("/Form"),
        "/BBox": ArrayObject([FloatObject(v) for v in (0, -5, 400, 20)]),
        "/Resources": DictionaryObject({NameObject("/Font"): fonts}),
    })
    pages = [
        ((0, 0, 612, 792), 0, b"BT /F1 14 Tf 72 700 Td (Account holder: John Smith, card 4111 1111 1111 1111.) Tj ET\n"
         b"BT /F1 14 Tf 72 670 Td [(Kerned) -250 (TJ) 120 (array: Alice) -300 (Wonder)] TJ ET\n"
         b"BT /F1 12 Tf 72 640 Td (Line one) Tj T* 0 -16 Td (Line two keeps going) Tj ET\n"
         b"q 1 0 0 1 72 600 cm /Fx1 Do Q\n"
         b"q 0.2 0.4 0.8 rg 72 500 200 60 re f Q\n"),
        ((100, 200, 712, 992), 0, b"BT /F1 16 Tf 150 900 Td (Shifted box page with John Smith inside.) Tj ET\n"),
        ((0, 0, 612, 792), 90, b"BT /F1 16 Tf 72 700 Td (Rotated page mentions John Smith too.) Tj ET\n"),
    ]
    for box, rot, content in pages:
        p = w.add_blank_page(612, 792)
        p[NameObject("/MediaBox")] = ArrayObject([FloatObject(v) for v in box])
        if rot:
            p[NameObject("/Rotate")] = NumberObject(rot)
        p[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts, NameObject("/XObject"): DictionaryObject({NameObject("/Fx1"): form})})
        p[NameObject("/Contents")] = _stream(w, content)
    w.add_metadata({"/Title": "Simple fonts", "/Author": "John Smith"})
    out = d / "simple.pdf"
    with open(out, "wb") as f:
        w.write(f)
    return out


def make_form(d: Path) -> Path:
    """An AcroForm with a text field, a checkbox, a radio group, a combo box and a list box."""
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, BooleanObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject

    w = PdfWriter()
    page = w.add_blank_page(612, 792)
    helv = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    zadb = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/ZapfDingbats")}))
    dr = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): helv, NameObject("/ZaDb"): zadb})})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): helv})})
    page[NameObject("/Contents")] = _stream(w, b"BT /Helv 12 Tf 72 720 Td (Name:) Tj 0 -40 Td (Subscribe:) Tj 0 -40 Td (Plan:) Tj 0 -40 Td (Country:) Tj 0 -40 Td (Colors:) Tj ET")

    def rect(*v):
        return ArrayObject([FloatObject(x) for x in v])

    def box_ap(on_name: str, size=14):
        res = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/ZaDb"): zadb})})
        bbox = rect(0, 0, size, size)
        on = _stream(w, b"q 0 g BT /ZaDb 12 Tf 2 3 Td (4) Tj ET Q", {"/Type": NameObject("/XObject"), "/Subtype": NameObject("/Form"), "/BBox": bbox, "/Resources": res})
        off = _stream(w, b"q 0.5 G 0.5 0.5 13 13 re S Q", {"/Type": NameObject("/XObject"), "/Subtype": NameObject("/Form"), "/BBox": bbox})
        return DictionaryObject({NameObject("/N"): DictionaryObject({NameObject(on_name): on, NameObject("/Off"): off})})

    fields = ArrayObject()
    annots = ArrayObject()

    def widget(extra: dict):
        o = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"), NameObject("/F"): NumberObject(4), NameObject("/P"): page.indirect_reference})
        o.update({NameObject(k): v for k, v in extra.items()})
        ref = w._add_object(o)
        annots.append(ref)
        return ref

    fields.append(widget({"/FT": NameObject("/Tx"), "/T": TextStringObject("name"), "/Rect": rect(160, 712, 400, 732), "/DA": TextStringObject("/Helv 11 Tf 0 g"), "/TU": TextStringObject("Full name")}))
    fields.append(widget({"/FT": NameObject("/Btn"), "/T": TextStringObject("subscribe"), "/Rect": rect(160, 674, 174, 688), "/V": NameObject("/Off"), "/AS": NameObject("/Off"), "/AP": box_ap("/Yes"), "/MK": DictionaryObject({NameObject("/CA"): TextStringObject("4")})}))
    radio = DictionaryObject({NameObject("/FT"): NameObject("/Btn"), NameObject("/T"): TextStringObject("plan"), NameObject("/Ff"): NumberObject(1 << 15), NameObject("/V"): NameObject("/Off"), NameObject("/Kids"): ArrayObject()})
    radio_ref = w._add_object(radio)
    for i, opt in enumerate(["/Basic", "/Pro"]):
        kid = widget({"/Parent": radio_ref, "/Rect": rect(160 + i * 80, 634, 174 + i * 80, 648), "/AS": NameObject("/Off"), "/AP": box_ap(opt)})
        radio["/Kids"].append(kid)
    fields.append(radio_ref)
    opts = ArrayObject([TextStringObject(x) for x in ("France", "Germany", "Japan")])
    fields.append(widget({"/FT": NameObject("/Ch"), "/T": TextStringObject("country"), "/Ff": NumberObject(1 << 17), "/Opt": opts, "/Rect": rect(160, 592, 300, 612), "/DA": TextStringObject("/Helv 11 Tf 0 g")}))
    opts2 = ArrayObject([TextStringObject(x) for x in ("Red", "Green", "Blue")])
    fields.append(widget({"/FT": NameObject("/Ch"), "/T": TextStringObject("colors"), "/Ff": NumberObject(1 << 21), "/Opt": opts2, "/Rect": rect(160, 520, 300, 572), "/DA": TextStringObject("/Helv 10 Tf 0 g")}))
    page[NameObject("/Annots")] = annots
    w._root_object[NameObject("/AcroForm")] = w._add_object(DictionaryObject({NameObject("/Fields"): fields, NameObject("/DR"): dr, NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g"), NameObject("/NeedAppearances"): BooleanObject(False)}))
    out = d / "form.pdf"
    with open(out, "wb") as f:
        w.write(f)
    return out


def make_extras(d: Path, report: Path) -> tuple[Path, Path]:
    """encrypted.pdf (user password 'secret', printing only) and extras.pdf (attachment + document JavaScript)."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.constants import UserAccessPermissions as P

    w = PdfWriter(clone_from=PdfReader(report))
    w.encrypt("secret", "owner-pw", permissions_flag=P.PRINT, algorithm="AES-256")
    enc = d / "encrypted.pdf"
    with open(enc, "wb") as f:
        w.write(f)
    w = PdfWriter(clone_from=PdfReader(report))
    w.add_attachment("data.csv", b"region,q1\nNorth,120\n")
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        w.add_js("app.alert('hello');")
    ext = d / "extras.pdf"
    with open(ext, "wb") as f:
        w.write(f)
    return enc, ext


def raw_pdf(objs: list[bytes], trailer: bytes) -> bytes:
    """A PDF written by hand (objects 1…n, a correct xref), for damaged or unusual files."""
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    return bytes(out + b"trailer\n" + trailer + f"\nstartxref\n{x}\n%%EOF\n".encode())


def _stream_obj(dict_body: bytes, data: bytes) -> bytes:
    return b"<< " + dict_body + b" /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"


def make_broken(d: Path) -> dict[str, Path]:
    """noroot.pdf (no /Root in the trailer: pdfium refuses it, pypdf reads it), infopages.pdf (/Info points at the
    page tree: pdfminer sees no page, pypdf cannot write it back), nopages.pdf (the catalog points at a missing page
    tree: only pdfminer reads it), all with the text "Damaged but readable text"."""
    content = b"BT /F1 18 Tf 72 700 Td (Damaged but readable text) Tj ET"
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    good = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", page, _stream_obj(b"", content), font]
    bad = [b"<< /Type /Catalog /Pages 7 0 R >>", *good[1:]]
    out = {}
    for name, objs, trailer in (("noroot", good, b"<< /Size 6 >>"), ("infopages", good, b"<< /Size 6 /Root 1 0 R /Info 2 0 R >>"), ("nopages", bad, b"<< /Size 6 /Root 1 0 R >>")):
        out[name] = d / f"{name}.pdf"
        out[name].write_bytes(raw_pdf(objs, trailer))
    return out


def make_widgets(d: Path) -> Path:
    """Widgets without an /AcroForm: a list box whose /Opt holds [export, display] pairs, and a "Submit" push button
    whose appearance shows "Submit" over the page's own "Submit" label."""
    content = b"BT /F1 12 Tf 72 700 Td (Pick a size:) Tj ET BT /F1 12 Tf 76 604 Td (Submit) Tj ET"
    ap = _stream_obj(b"/Type /XObject /Subtype /Form /BBox [0 0 60 20] /Resources << /Font << /F1 5 0 R >> >>", b"BT /F1 10 Tf 4 6 Td (Submit) Tj ET")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> /Annots [6 0 R 7 0 R] >>",
        _stream_obj(b"", content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Annot /Subtype /Widget /FT /Ch /T (size) /Rect [180 640 300 720] /Opt [[(s) (Small)] [(m) (Medium)] [(l) (Large)] [(xl) (Extra large)] [(xxl) (Huge)]] /DA (/Helv 10 Tf 0 g) /P 3 0 R >>",
        b"<< /Type /Annot /Subtype /Widget /FT /Btn /Ff 65536 /T (go) /Rect [70 596 130 616] /AP << /N 8 0 R >> /P 3 0 R >>",
        ap,
    ]
    out = d / "widgets.pdf"
    out.write_bytes(raw_pdf(objs, b"<< /Size 9 /Root 1 0 R >>"))
    return out


def make_indexed(d: Path) -> Path:
    """A red 40×20 image in an /Indexed colour space (palette: black, red), drawn 200×100 pt."""
    data = bytes([1]) * (40 * 20)
    img = _stream_obj(b"/Type /XObject /Subtype /Image /Width 40 /Height 20 /BitsPerComponent 8 /ColorSpace [/Indexed /DeviceRGB 1 <000000FF0000>]", data)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Contents 4 0 R /Resources << /XObject << /Im1 5 0 R >> >> >>",
        _stream_obj(b"", b"q 200 0 0 100 100 100 cm /Im1 Do Q"),
        img,
    ]
    out = d / "indexed.pdf"
    out.write_bytes(raw_pdf(objs, b"<< /Size 6 /Root 1 0 R >>"))
    return out


def build_fixtures(d: Path) -> dict[str, Path]:
    d.mkdir(parents=True, exist_ok=True)
    report = make_report(d)
    enc, ext = make_extras(d, report)
    return {
        "report": report, "report2": make_report(d, "report2", "175"), "scan": make_scan(d), "simple": make_simple(d),
        "form": make_form(d), "encrypted": enc, "extras": ext, "chart": d / "chart.png", "big": make_big(d), "photo": make_photo(d),
        "widgets": make_widgets(d), "indexed": make_indexed(d), **make_broken(d),
    }


# ── test runner ─────────────────────────────────────────────────────────


SOLO = {"test_big"}  # tests with timing targets run before the others, on their own
NO_LO = {"DESK_SOFFICE": "none"}  # hides LibreOffice: the built-in path


class Checks:
    def __init__(self) -> None:
        import threading

        self.n = 0
        self.failures: list[str] = []
        self.lock = threading.Lock()

    def ok(self, cond: bool, what: str) -> None:
        with self.lock:
            self.n += 1
            if not cond:
                self.failures.append(what)
                print(f"FAIL: {what}", file=sys.stderr)


def run(args: list[str], env: dict[str, str] | None = None, expect: int = 0, stdin: str | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [PY, str(HERE / args[0]), *[str(a) for a in args[1:]]]
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=e, input=stdin, cwd=cwd or WORK, timeout=180)
    if r.returncode != expect:
        raise AssertionError(f"{' '.join(str(x) for x in args[:3])} … exited {r.returncode} (expected {expect}):\n{r.stderr[-1500:]}\n{r.stdout[-500:]}")
    return r


def jrun(args: list[str], **kw) -> object:
    r = run([*args, "--format", "json"], **kw)
    return json.loads(r.stdout)


def timed(args: list[str], **kw) -> tuple[float, subprocess.CompletedProcess[str]]:
    t = time.perf_counter()
    r = run(args, **kw)
    return time.perf_counter() - t, r


def text_of(path: Path, password: str | None = None) -> list[str]:
    """Each page's text, read independently of the scripts (pypdfium2)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path), password=password)
    try:
        out = []
        for page in doc:
            tp = page.get_textpage()
            out.append(tp.get_text_range())
            tp.close()
            page.close()
        return out
    finally:
        doc.close()


def png_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


def dark_share(png: Path, box: tuple[int, int, int, int] | None = None) -> float:
    """Share of dark pixels in the image (or a pixel box of it)."""
    from PIL import Image

    with Image.open(png) as im:
        g = im.convert("L")
        if box:
            g = g.crop(box)
        hist = g.histogram()
    return sum(hist[:100]) / max(1, sum(hist))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--fixtures", help="only write fixtures into this folder")
    ap.add_argument("--only", help="run only test functions whose name contains this")
    ap.add_argument("--times", action="store_true", help="print how long each test took")
    a = ap.parse_args()
    if a.fixtures:
        fx = build_fixtures(Path(a.fixtures))
        print("\n".join(str(p) for p in fx.values()))
        return 0
    t0 = time.time()
    global WORK
    tmp = Path(tempfile.mkdtemp(prefix="pdf-toolkit-selftest-"))
    WORK = tmp
    os.environ["DESK_FILE_CACHE"] = str(tmp / "cache")  # a private, initially empty cache for the cache checks
    os.environ.pop("DESK_NO_CACHE", None)
    c = Checks()
    try:
        fx = build_fixtures(tmp / "fx")
        if a.times:
            print(f"fixtures: {time.time() - t0:.1f}s", file=sys.stderr)
        tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_") and callable(fn) and (not a.only or a.only in name)]

        def one(item: tuple[str, object]) -> None:
            name, fn = item
            work = tmp / name
            work.mkdir()
            t1 = time.time()
            try:
                fn(c, fx, work)  # type: ignore[operator]
            except AssertionError as e:
                c.ok(False, f"{name}: {e}")
            except Exception as e:  # noqa: BLE001
                c.ok(False, f"{name}: {type(e).__name__}: {e}")
            if a.times:
                print(f"{name}: {time.time() - t1:.1f}s", file=sys.stderr)

        # The timing checks run alone; the rest share two threads (each test runs the scripts as subprocesses).
        for item in [t for t in tests if t[0] in SOLO]:
            one(item)
        from concurrent.futures import ThreadPoolExecutor

        try:
            workers = max(1, min(2, int(os.environ.get("DESK_MAX_WORKERS", "2"))))
        except ValueError:
            workers = 2
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, [t for t in tests if t[0] not in SOLO]))
    finally:
        if a.keep:
            print(f"kept {tmp}", file=sys.stderr)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    dt = time.time() - t0
    if c.failures:
        print(f"FAILED: {len(c.failures)} of {c.n} checks failed in {dt:.1f}s", file=sys.stderr)
        return 1
    print(f"ok: {c.n} checks in {dt:.1f}s")
    return 0


# ── tests ───────────────────────────────────────────────────────────────


def test_info(c: Checks, fx: dict[str, Path], w: Path) -> None:
    info = jrun(["pdf_info.py", fx["report"]])
    c.ok(info["pages"] == 5, "info: report has 5 pages")
    c.ok(info["metadata"].get("Title") == "Quarterly Report", "info: title from metadata")
    c.ok(info["outline"]["entries"] >= 6, "info: outline from headings")
    c.ok(any(s["paper"] == "A4 landscape" for s in info["page_sizes"]), "info: mixed sizes include A4 landscape")
    c.ok(info["text"]["pages_with_text"] == 5, "info: all report pages have text")
    c.ok(info["images"]["objects"] >= 1, "info: image counted")
    c.ok(all(f["embedded"] for f in info["fonts"]), "info: Typst fonts embedded")
    c.ok(info["links"]["external"] >= 1, "info: external link counted")
    md = run(["pdf_info.py", fx["report"]]).stdout
    c.ok("# report.pdf" in md and "Text layer: 5 of 5" in md, "info: Markdown summary")
    scan = jrun(["pdf_info.py", fx["scan"]])
    c.ok(scan["text"]["scanned_pages"] == [2], "info: scanned page detected")
    md = run(["pdf_info.py", fx["scan"]]).stdout
    c.ok("view_image" in md and "no OCR" in md, "info: tells the agent to look at scanned pages")
    enc = jrun(["pdf_info.py", fx["encrypted"], "--password", "secret"])
    c.ok(enc["encryption"]["algorithm"] == "AES-256" and enc["encryption"]["permissions"] == ["print"], "info: AES-256 and permissions")
    r = run(["pdf_info.py", fx["encrypted"]], expect=1)
    c.ok("--password" in r.stderr, "info: asks for a password")
    ext = jrun(["pdf_info.py", fx["extras"]])
    c.ok(ext["attachments"] and ext["attachments"][0]["name"] == "data.csv", "info: attachment listed")
    c.ok(ext["actions"]["risky"].get("JavaScript", 0) >= 1, "info: document JavaScript flagged")
    form = jrun(["pdf_info.py", fx["form"]])
    c.ok(form["forms"]["fields"] == 5, "info: 5 form fields")
    simple = jrun(["pdf_info.py", fx["simple"]])
    c.ok(any(not f["embedded"] and f["name"] == "Helvetica" for f in simple["fonts"]), "info: non-embedded Helvetica")
    c.ok(simple["rotated_pages"] == [3], "info: rotated page")


def test_text(c: Checks, fx: dict[str, Path], w: Path) -> None:
    out = run(["pdf_text.py", fx["report"], "--all"]).stdout
    c.ok("--- page 1 ---" in out and "ZEPHYRINE" in out and "QUOKKA" in out, "text: all pages with markers")
    out = run(["pdf_text.py", fx["report"], "--pages", "4"]).stdout
    c.ok("QUOKKA" in out and "ZEPHYRINE" not in out, "text: --pages selects pages")
    out = run(["pdf_text.py", fx["report"], "--pages", "4", "--no-markers"]).stdout
    c.ok(out.lstrip().startswith("5. Hiring"), "text: --no-markers")
    d = jrun(["pdf_text.py", fx["report"], "--pages", "1-2"])
    c.ok([r["page"] for r in d["results"]] == [1, 2] and "Revenue" in d["results"][0]["text"], "text: JSON per page")
    out = run(["pdf_text.py", fx["scan"], "--all"]).stdout
    c.ok("no text layer" in out and "pdf_render.py --pages 2" in out, "text: scanned page points to rendering")
    out = run(["pdf_text.py", fx["simple"], "--all"]).stdout
    c.ok("secret-token-42" in out and "Shifted box page" in out and "Rotated page mentions" in out, "text: form XObject, shifted and rotated pages")
    out = run(["pdf_text.py", fx["report"], "--layout", "--pages", "1"]).stdout
    c.ok(re.search(r"North\s+120\s+135", out) is not None, "text: --layout keeps table columns")
    r = run(["pdf_text.py", fx["encrypted"]], expect=1)
    c.ok("--password" in r.stderr, "text: encrypted file asks for --password")
    out = run(["pdf_text.py", fx["encrypted"], "--password", "secret", "--pages", "1"]).stdout
    c.ok("ZEPHYRINE" in out, "text: --password opens it")
    out = run(["pdf_text.py", fx["report"], "--map"]).stdout
    c.ok("a map" in out and "Revenue" in out and "Read a part:" in out, "text: --map lists sections")


def test_words_search(c: Checks, fx: dict[str, Path], w: Path) -> None:
    d = jrun(["pdf_text.py", fx["report"], "--words", "--pages", "1"])
    word = next((x for x in d["words"] if x["text"].startswith("ZEPHYRINE")), None)
    c.ok(word is not None, "words: word found")
    s = jrun(["pdf_text.py", fx["report"], "--search", "ZEPHYRINE"])
    c.ok(s["count"] == 1 and s["matches"][0]["page"] == 1, "search: one match on page 1")
    box = s["matches"][0]["box"]
    c.ok(word is not None and abs(word["box"][0] - box[0]) < 1 and abs(word["box"][1] - box[1]) < 2, "search and words agree on the box")
    c.ok(0 < box[0] < box[2] < 595 and 0 < box[1] < box[3] < 842, "search: box inside the page, top-left origin")
    s = jrun(["pdf_text.py", fx["report"], "--search", "jane doe", "--ignore-case", "--literal"])
    c.ok(s["count"] == 1 and s["matches"][0]["context"], "search: literal, ignore case, context")
    s = jrun(["pdf_text.py", fx["simple"], "--search", "John Smith"])
    pages = {m["page"]: m["box"] for m in s["matches"]}
    c.ok(set(pages) == {1, 2, 3}, "search: finds matches on shifted and rotated pages")
    b3 = pages.get(3, [0, 0, 0, 0])
    c.ok(b3[3] - b3[1] > b3[2] - b3[0] and b3[2] < 792, "search: rotated page box is vertical in view space")
    s = jrun(["pdf_text.py", fx["simple"], "--search", "Shifted", "--pages", "2"])
    b2 = s["matches"][0]["box"] if s["matches"] else [0, 0, 0, 0]
    c.ok(abs(b2[0] - 50) < 3 and 70 < b2[1] < 95, f"search: shifted media box maps to view coordinates {b2}")
    r = run(["pdf_text.py", fx["report"], "--search", "NOT-THERE-XYZ", "--fail-if-none"], expect=1)
    c.ok("0 matches" in r.stdout, "search: --fail-if-none exits 1")
    run(["pdf_text.py", fx["report"], "--search", "(unclosed"], expect=2)
    c.ok(True, "search: bad regex is a usage error")
    # The search box lines up with the render: the marked region holds dark text pixels.
    img = jrun(["pdf_render.py", fx["report"], "--pages", "1", "--dpi", "72", "--max-edge", "0", "--out", w / "r"])["images"][0]["path"]
    c.ok(dark_share(Path(img), (int(box[0]), int(box[1]), int(box[2]) + 1, int(box[3]) + 1)) > 0.08, "search box covers the word in the rendered page")


def test_tables(c: Checks, fx: dict[str, Path], w: Path) -> None:
    out = run(["pdf_text.py", fx["report"], "--tables"]).stdout
    c.ok("| Region | Q1 | Q2 |" in out and "| West | 143 | 150 |" in out, "tables: Markdown with header")
    d = jrun(["pdf_text.py", fx["report"], "--tables"])
    t = d["tables"][0]
    c.ok(t["header"] == ["Region", "Q1", "Q2"] and len(t["rows"]) == 3 and t["page"] == 1, "tables: JSON header and rows")
    c.ok(any(x["page"] == 3 and x["columns"] == 6 for x in d["tables"]), "tables: landscape page table")
    out = run(["pdf_text.py", fx["report"], "--tables", "--format", "csv", "--pages", "1"]).stdout
    c.ok("Region,Q1,Q2\nNorth,120,135" in out, "tables: CSV")
    out = run(["pdf_text.py", fx["report"], "--tables", "--out", w / "csv"]).stdout
    files = sorted((w / "csv").glob("*.csv"))
    c.ok(len(files) == 2 and files[0].read_text(encoding="utf-8").startswith("Region,Q1,Q2"), "tables: --out writes one CSV per table")
    run(["pdf_text.py", fx["report"], "--tables", "--out", w / "csv"], expect=1)
    c.ok(True, "tables: existing CSV files are not replaced without --force")
    run(["pdf_text.py", fx["report"], "--format", "csv"], expect=2)
    c.ok(True, "tables: --format csv without --tables is a usage error")


def test_big(c: Checks, fx: dict[str, Path], w: Path) -> None:
    big = fx["big"]
    out = run(["pdf_text.py", big]).stdout
    c.ok("220 pages" in out and "a map" in out and "Chapter 3" in out and "pdf_text.py" in out, "big: default read is a map with sections")
    dt, r = timed(["pdf_text.py", big, "--pages", "1-200", "--max-chars", "0"], env={"DESK_NO_CACHE": "1"})
    c.ok(r.stdout.count("--- page ") == 200 and "P0200X" in r.stdout, "big: 200 pages of text")
    c.ok(dt < 3.0, f"big: 200 pages of text in {dt:.2f}s (target < 3 s)")
    out = run(["pdf_text.py", big, "--pages", "1-220", "--max-chars", "20000"]).stdout
    m = re.search(r"Next: python3 scripts/pdf_text\.py (\S+) --pages ([\d,-]+)", out)
    c.ok(m is not None and "stopped after page" in out, "big: --max-chars ends with the next command")
    if m:
        nxt = run(["pdf_text.py", m.group(1), "--pages", m.group(2), "--max-chars", "20000"]).stdout
        first = int(m.group(2).split("-")[0].split(",")[0])
        c.ok(f"--- page {first} ---" in nxt and f"P{first:04d}X" in nxt, "big: the continuation command reads on")
    s = jrun(["pdf_text.py", big, "--search", r"P0187X"])
    c.ok(s["count"] == 1 and s["matches"][0]["page"] == 187 and s["matches"][0]["box"], "big: search returns the page and box")
    # Cache: the second read of slow modes comes from the cache.
    t1, r1 = timed(["pdf_text.py", big, "--tables", "--format", "json", "--max-chars", "0"])
    t2, r2 = timed(["pdf_text.py", big, "--tables", "--format", "json", "--max-chars", "0"])
    n1 = json.loads(r1.stdout)["count"]
    c.ok(n1 == 22 and r1.stdout == r2.stdout, f"big: 22 tables, identical when cached ({n1})")
    c.ok(t1 >= 5 * t2, f"big: cached tables {t1 / max(t2, 1e-6):.1f}× faster ({t1:.2f}s → {t2:.2f}s; need 5×)")
    t1, r1 = timed(["pdf_text.py", big, "--layout", "--pages", "1-60", "--max-chars", "0"])
    t2, r2 = timed(["pdf_text.py", big, "--layout", "--pages", "1-60", "--max-chars", "0"])
    c.ok(r1.stdout == r2.stdout and "P0060X" in r2.stdout, "big: layout identical when cached")
    c.ok(t1 >= 5 * t2, f"big: cached layout {t1 / max(t2, 1e-6):.1f}× faster ({t1:.2f}s → {t2:.2f}s; need 5×)")
    out = run(["pdf_text.py", big, "--tables", "--max-chars", "6000"]).stdout
    c.ok("Item,Qty,Price" in out and "Next: python3 scripts/pdf_text.py" in out, "big: many table cells default to CSV, with a next command")
    dt, r = timed(["pdf_render.py", big, "--pages", "1-20", "--out", w / "r20", "--format", "json"], env={"DESK_NO_CACHE": "1"})
    imgs = json.loads(r.stdout)["images"]
    c.ok(len(imgs) == 20 and all(max(i["width"], i["height"]) <= 1568 for i in imgs), "big: 20 pages rendered at vision size")
    c.ok(dt < 5.0, f"big: 20 pages rendered in {dt:.2f}s (target < 5 s)")
    info = jrun(["pdf_info.py", big])
    c.ok(info["pages"] == 220 and info["outline"]["entries"] >= 25, "big: info with outline")


def test_render(c: Checks, fx: dict[str, Path], w: Path) -> None:
    r = run(["pdf_render.py", fx["report"], "--out", w / "all"])
    pngs = sorted((w / "all").glob("*.png"))
    c.ok(len(pngs) == 5 and r.stdout.rstrip().endswith("Look at them with view_image."), "render: 5 PNGs and the view_image line")
    sizes = [png_size(p) for p in pngs]
    c.ok(all(max(s) == 1568 for s in sizes) and sizes[2][0] > sizes[2][1], "render: vision size, landscape page 3")
    d = jrun(["pdf_render.py", fx["report"], "--pages", "1", "--region", "50,60,300,240", "--grid", "--out", w / "reg"])
    im = d["images"][0]
    c.ok(abs(im["width"] - 694) <= 2 and abs(im["height"] - 500) <= 2, "render: --region at 200 dpi")
    d = jrun(["pdf_render.py", fx["big"], "--sheet", "--out", w / "sheet"])
    c.ok(len(d["sheets"]) == 19 and all(Path(p["path"]).exists() for p in d["sheets"]), "render: contact sheets for 220 pages")
    run(["pdf_render.py", fx["report"], "--pages", "1", "--out", w / "all"], expect=1)
    c.ok(True, "render: refuses to replace images without --force")
    run(["pdf_render.py", fx["report"], "--pages", "1", "--jpeg", "--out", w / "jpg"])
    c.ok(len(list((w / "jpg").glob("*.jpg"))) == 1, "render: --jpeg")
    run(["pdf_render.py", fx["simple"], "--pages", "3", "--out", w / "rot"])
    s = png_size(next((w / "rot").glob("*.png")))
    c.ok(s[0] > s[1], "render: /Rotate 90 page renders in landscape")


def test_pages(c: Checks, fx: dict[str, Path], w: Path) -> None:
    from pypdf import PdfReader

    rep, simple = fx["report"], fx["simple"]
    d = jrun(["pdf_pages.py", "merge", w / "m.pdf", rep, f"{simple}:1-2"])
    rd = PdfReader(w / "m.pdf")
    c.ok(d["pages"] == 7 and len(rd.pages) == 7, "pages merge: 5 + 2 pages")
    c.ok(len(rd.outline) >= 2, "pages merge: outline entry per source")
    d = jrun(["pdf_pages.py", "split", rep, w / "split", "--every", "2"])
    c.ok(len(d["outputs"]) == 3, "pages split --every 2: 3 files")
    d = jrun(["pdf_pages.py", "split", rep, w / "split-at", "--at", "3"])
    c.ok(len(d["outputs"]) == 2, "pages split --at 3: 2 files")
    d = jrun(["pdf_pages.py", "split", rep, w / "split-ol", "--by-outline"])
    c.ok(len(d["outputs"]) >= 4, "pages split --by-outline")
    jrun(["pdf_pages.py", "extract", rep, w / "x.pdf", "--pages", "2-3"])
    c.ok(len(PdfReader(w / "x.pdf").pages) == 2, "pages extract")
    jrun(["pdf_pages.py", "delete", rep, w / "del.pdf", "--pages", "1"])
    t = text_of(w / "del.pdf")
    c.ok(len(t) == 4 and "Costs" in t[0], "pages delete")
    jrun(["pdf_pages.py", "rotate", rep, w / "rot.pdf", "--degrees", "90", "--pages", "1"])
    c.ok(PdfReader(w / "rot.pdf").pages[0].rotation == 90, "pages rotate")
    jrun(["pdf_pages.py", "reorder", rep, w / "ro.pdf", "--order", "5,1,1"])
    t = text_of(w / "ro.pdf")
    c.ok(len(t) == 3 and "Summary" in t[0] and "Introduction" in t[2], "pages reorder with repeats")
    jrun(["pdf_pages.py", "reverse", rep, w / "rev.pdf"])
    c.ok("Summary" in text_of(w / "rev.pdf")[0], "pages reverse")
    jrun(["pdf_pages.py", "insert", rep, w / "ins.pdf", "--blank", "1", "--after", "0"])
    t = text_of(w / "ins.pdf")
    c.ok(len(t) == 6 and not t[0].strip(), "pages insert a blank first page")
    jrun(["pdf_pages.py", "insert", rep, w / "ins2.pdf", "--from", simple, "--from-pages", "1", "--after", "end"])
    c.ok("Account holder" in text_of(w / "ins2.pdf")[-1], "pages insert from another PDF")
    jrun(["pdf_pages.py", "crop", rep, w / "crop.pdf", "--margins", "36"])
    b = PdfReader(w / "crop.pdf").pages[0].cropbox
    c.ok(abs(float(b.width) - (595.28 - 72)) < 1, "pages crop --margins")
    run(["pdf_pages.py", "insert", rep, w / "bad.pdf", "--after", "two", "--blank", "1"], expect=2)
    c.ok(not (w / "bad.pdf").exists(), "pages insert: a bad --after is a usage error")
    limit = int(rep.stat().st_size * 0.7)
    d = jrun(["pdf_pages.py", "split", rep, w / "by-size", "--max-size", f"{limit // 1024}KB"])
    outs = d["outputs"]
    c.ok(len(outs) >= 2 and sum(o["pages"] for o in outs) == 5 and all(o["bytes"] <= (limit // 1024) * 1024 or o.get("over_limit") for o in outs), f"pages split --max-size: {[(o['pages'], o['bytes']) for o in outs]}")
    jrun(["pdf_pages.py", "crop", rep, w / "box.pdf", "--box", "0,0,0.5,0.5", "--pages", "1"])
    b = PdfReader(w / "box.pdf").pages[0].cropbox
    c.ok(abs(float(b.width) - 297.64) < 1 and abs(float(b.height) - 420.95) < 1, "pages crop --box with fractions")
    d = jrun(["pdf_pages.py", "nup", rep, w / "2up.pdf", "--n", "2", "--paper", "A4"])
    p0 = PdfReader(w / "2up.pdf").pages[0]
    c.ok(d["pages"] == 3 and float(p0.mediabox.width) > float(p0.mediabox.height), "pages nup 2: 3 landscape A4 sheets")
    jrun(["pdf_pages.py", "crop", rep, w / "auto.pdf", "--auto"])
    b = PdfReader(w / "auto.pdf").pages[0].cropbox
    c.ok(float(b.width) < 595 - 60, f"pages crop --auto trims white margins ({float(b.width):.0f} pt wide)")
    jrun(["pdf_pages.py", "nup", rep, w / "nup.pdf", "--n", "4"])
    t = text_of(w / "nup.pdf")
    c.ok(len(t) == 2 and "ZEPHYRINE" in t[0] and "QUOKKA" in t[0], "pages nup 4: 2 sheets")
    jrun(["pdf_pages.py", "scale", rep, w / "letter.pdf", "--paper", "letter"])
    p0 = PdfReader(w / "letter.pdf").pages[0]
    c.ok(abs(float(p0.mediabox.width) - 612) < 1 and abs(float(p0.mediabox.height) - 792) < 1, "pages scale to Letter")
    jrun(["pdf_pages.py", "interleave", w / "split" / next(p.name for p in sorted((w / "split").iterdir())), rep, w / "il.pdf"])
    c.ok(len(PdfReader(w / "il.pdf").pages) == 7, "pages interleave")
    run(["pdf_pages.py", "rotate", rep, rep, "--degrees", "90", "--force"], expect=1)
    c.ok(True, "pages: refuses to write over the input")
    run(["pdf_pages.py", "reverse", rep, w / "rev.pdf"], expect=1)
    c.ok(True, "pages: refuses to replace an existing output without --force")
    # Old command names still work.
    run(["pdf_pages.py", "encrypt", rep, w / "old-enc.pdf", "--password", "pw"])
    run(["pdf_pages.py", "decrypt", w / "old-enc.pdf", w / "old-dec.pdf", "--password", "pw"])
    run(["pdf_pages.py", "metadata", rep, w / "old-meta.pdf", "--title", "Old API"])
    c.ok(not PdfReader(w / "old-dec.pdf").is_encrypted and PdfReader(w / "old-meta.pdf").metadata.title == "Old API", "pages: old encrypt/decrypt/metadata names")


def test_meta(c: Checks, fx: dict[str, Path], w: Path) -> None:
    from pypdf import PdfReader

    rep = fx["report"]
    run(["pdf_meta.py", "set", rep, w / "set.pdf", "--title", "New title", "--author", "Ada", "--keywords", "a, b"])
    d = jrun(["pdf_meta.py", "show", w / "set.pdf"])
    c.ok(d["info"].get("Title") == "New title" and d["info"].get("Author") == "Ada", "meta set: Info")
    c.ok(d["xmp"] and d["xmp"].get("dc:title") == "New title", "meta set: XMP kept in sync")
    run(["pdf_meta.py", "set", fx["simple"], w / "xmp.pdf", "--title", "Fresh", "--author", "Doe, Jane"])
    d = jrun(["pdf_meta.py", "show", w / "xmp.pdf"])
    c.ok(bool(d["xmp"]) and d["xmp"].get("dc:title") == "Fresh" and d["xmp"].get("dc:creator") == "Doe, Jane", "meta set: creates XMP when the file had none")
    run(["pdf_meta.py", "set", rep, w / "clear.pdf", "--clear"])
    d = jrun(["pdf_meta.py", "show", w / "clear.pdf"])
    c.ok(not d["info"].get("Title") and not d["xmp"], "meta set --clear")
    tree = jrun(["pdf_meta.py", "outline", rep])
    c.ok(tree[0]["title"] == "1. Introduction" and tree[0]["page"] == 1, "meta outline tree")
    (w / "ol.json").write_text(json.dumps([{"title": "Start", "page": 1, "children": [{"title": "Money", "page": 2}]}, {"title": "End", "page": 5}]), encoding="utf-8")
    run(["pdf_meta.py", "set-outline", rep, w / "ol.pdf", "--outline", w / "ol.json"])
    tree = jrun(["pdf_meta.py", "outline", w / "ol.pdf"])
    c.ok([t["title"] for t in tree] == ["Start", "End"] and tree[0]["children"][0]["page"] == 2, "meta set-outline")
    run(["pdf_meta.py", "encrypt", rep, w / "enc.pdf", "--password", "pw1", "--allow", "print"])
    r = PdfReader(w / "enc.pdf")
    c.ok(r.is_encrypted and r.decrypt("pw1") != 0, "meta encrypt")
    info = jrun(["pdf_info.py", w / "enc.pdf", "--password", "pw1"])
    c.ok(info["encryption"]["algorithm"] == "AES-256" and "print" in info["encryption"]["permissions"], "meta encrypt: AES-256, print allowed")
    run(["pdf_meta.py", "decrypt", w / "enc.pdf", w / "dec.pdf", "--password", "pw1"])
    c.ok(not PdfReader(w / "dec.pdf").is_encrypted, "meta decrypt")
    run(["pdf_meta.py", "decrypt", w / "enc.pdf", w / "dec2.pdf", "--password", "wrong"], expect=1)
    c.ok(True, "meta decrypt: wrong password fails")
    run(["pdf_meta.py", "set-labels", rep, w / "lab.pdf", "--labels", "1:roman,3:decimal"])
    d = jrun(["pdf_meta.py", "labels", w / "lab.pdf"])
    c.ok(d["labels"] == ["i", "ii", "1", "2", "3"], "meta set-labels")
    out = run(["pdf_text.py", w / "lab.pdf", "--pages", "1"]).stdout
    c.ok("--- page 1 (i) ---" in out, "text shows page labels")
    run(["pdf_meta.py", "viewer", rep, w / "view.pdf", "--page-mode", "outlines", "--open-page", "2"])
    d = jrun(["pdf_meta.py", "show", w / "view.pdf"])
    c.ok("outlines" in json.dumps(d["viewer"]).lower(), "meta viewer settings")


def test_form(c: Checks, fx: dict[str, Path], w: Path) -> None:
    form = fx["form"]
    d = jrun(["pdf_form.py", "list", form])
    types = {f["name"]: f["type"] for f in d["fields"]}
    c.ok(types == {"name": "text", "subscribe": "checkbox", "plan": "radio", "country": "combo", "colors": "list"}, f"form list: types {types}")
    vals = {"name": "Ada Lovelace", "subscribe": True, "plan": "Pro", "country": "Japan", "colors": ["Red", "Blue"]}
    d = jrun(["pdf_form.py", "fill", form, w / "filled.pdf", "--values", json.dumps(vals), "--render", w / "png"])
    got = {f["name"]: f["value"] for f in jrun(["pdf_form.py", "list", w / "filled.pdf"])["fields"]}
    c.ok(got["name"] == "Ada Lovelace" and got["plan"] == "Pro" and got["country"] == "Japan" and got["subscribe"] not in (None, "Off", False), f"form fill: values read back {got}")
    c.ok(sorted(got["colors"] or []) == ["Blue", "Red"], "form fill: multi-select list")
    pngs = list((w / "png").glob("*.png"))
    c.ok(len(pngs) == 1, "form fill --render")
    if pngs:
        sx = png_size(pngs[0])[0] / 612
        c.ok(dark_share(pngs[0], (int(165 * sx), int(62 * sx), int(395 * sx), int(78 * sx))) > 0.02, "form fill: value visible in the render")
    r = run(["pdf_form.py", "fill", form, w / "bad.pdf", "--values", '{"nmae": "x"}'], expect=1)
    c.ok("name" in r.stderr and not (w / "bad.pdf").exists(), "form fill: unknown field suggests the right name")
    r = run(["pdf_form.py", "fill", form, w / "bad2.pdf", "--values", '{"plan": "Gold"}'], expect=1)
    c.ok("Basic" in r.stderr, "form fill: bad option lists the valid ones")
    d = jrun(["pdf_form.py", "fill", form, w / "flat.pdf", "--values", json.dumps(vals), "--flatten"])
    after = jrun(["pdf_form.py", "list", w / "flat.pdf"])
    c.ok(after["count"] == 0 and "Ada Lovelace" in "".join(text_of(w / "flat.pdf")), "form fill --flatten: no fields, value in page text")
    d = jrun(["pdf_form.py", "flatten", w / "filled.pdf", w / "flat2.pdf"])
    c.ok(d["flattened_widgets"] >= 5, "form flatten")


def test_create(c: Checks, fx: dict[str, Path], w: Path) -> None:
    md = w / "doc.md"
    md.write_text("---\ntitle: Field Report\nauthor: Ada\n---\n\n# Findings\n\nWe measured *three* things.[^1]\n\n| Item | Value |\n|---|---|\n| alpha | 1 |\n| beta | 2 |\n\n## Details\n\nSee @sec-x and `code`.\n\n```python\nprint('hi')\n```\n\n[^1]: A footnote.\n", encoding="utf-8")
    d = jrun(["pdf_create.py", md, "--toc", "--render", w / "png"], cwd=w)  # no --out: the PDF lands in the working directory
    out = w / d["output"]
    t = "\n".join(text_of(out))
    c.ok(out.exists() and d["template"] == "report" and "Field Report" in t and "Findings" in t and "A footnote." in t, "create: Markdown → report PDF")
    c.ok("alpha" in t and "Contents" in t, "create: table and table of contents")
    c.ok(len(list((w / "png").glob("*.png"))) >= 1, "create --render")
    info = jrun(["pdf_info.py", out])
    c.ok(info["metadata"].get("Title") == "Field Report" and all(f["embedded"] for f in info["fonts"]), "create: PDF title and embedded fonts")
    for tpl, marker in (("memo", "Memorandum"), ("letter", "Dear"), ("plain", "Findings")):
        body = w / f"{tpl}.md"
        body.write_text("Dear team,\n\n# Findings\n\nAll good.\n", encoding="utf-8")
        d = jrun(["pdf_create.py", body, "--template", tpl, "--title", "Update", "--to", "Team", "--from-name", "Desk", "--out", w / f"{tpl}.pdf"])
        c.ok(marker in "".join(text_of(Path(d["output"]))), f"create --template {tpl}")
    html = w / "page.html"
    html.write_text("<h1>Web page</h1><p>Hello <b>HTML</b>.</p><ul><li>one</li><li>two</li></ul>", encoding="utf-8")
    d = jrun(["pdf_create.py", html, "--out", w / "html.pdf"])
    c.ok("Web page" in "".join(text_of(Path(d["output"]))), "create: HTML input")
    typ = w / "direct.typ"
    typ.write_text('#set page(paper: "a6")\n= Typst direct\nHello.\n', encoding="utf-8")
    d = jrun(["pdf_create.py", typ, "--out", w / "typ.pdf"])
    c.ok("Typst direct" in "".join(text_of(Path(d["output"]))), "create: .typ input")
    run(["pdf_create.py", md, "--out", w / "t2.pdf", "--typ-out", w / "typ-src"])
    c.ok((w / "typ-src" / "desk.typ").exists() and (w / "typ-src" / "doc.typ").exists(), "create --typ-out keeps the Typst source")
    d = jrun(["pdf_create.py", w / "typ-src" / "doc.typ", "--out", w / "t3.pdf"])
    c.ok("Findings" in "".join(text_of(Path(d["output"]))), "create: the kept Typst source compiles")
    run(["pdf_create.py", w / "old.pdf", "--input", md])
    c.ok((w / "old.pdf").exists(), "create: old 'OUT --input IN' form")
    run(["pdf_create.py", md, "--out", w / "old.pdf"], expect=1)
    c.ok(True, "create: refuses to replace an existing PDF without --force")


def test_create_jail(c: Checks, fx: dict[str, Path], w: Path) -> None:
    """A stranger's document never pulls files from outside its folder into the PDF (includes, pictures, file: URIs,
    symlinks); includes and pictures inside the folder still work, and pandoc never fetches remote pictures itself."""
    from PIL import Image

    doc, far = w / "doc", w / "far"
    (doc / "sub").mkdir(parents=True)
    far.mkdir()
    marker = "SECRETMARK-9Z"
    secret = far / "secret.txt"
    secret.write_text(marker + "\n", encoding="utf-8")
    Image.new("RGB", (60, 40), (200, 0, 0)).save(far / "outside.png")
    Image.new("RGB", (60, 40), (0, 0, 200)).save(doc / "pic.png")
    link = ""
    try:
        (doc / "link.png").symlink_to(far / "outside.png")
        link = "\n\n![l](link.png)"
    except OSError:
        pass
    files = {
        "m.md": f"# M\n\nMDPART\n\n![x]({secret})\n\n![h](/etc/hosts)\n\n![y](../far/outside.png)\n\n![f](file://{secret.as_posix()})\n\n![ok](pic.png){link}\n",
        "c.rst": f"Title\n=====\n\n.. include:: {secret}\n\n.. include:: /etc/hosts\n\n.. include:: part.rst\n\n.. raw:: typst\n   :file: ../far/secret.txt\n",
        "part.rst": "RSTPART\n\n.. include:: ../far/secret.txt\n",
        "o.org": f'* H\n#+INCLUDE: "{secret}"\n#+INCLUDE: "sub/p.org"\n',
        "sub/p.org": 'ORGPART\n#+INCLUDE: "../../far/secret.txt"\n',
        "l.tex": "\\documentclass{article}\n\\begin{document}\n\\include{" + secret.with_suffix("").as_posix() + "}\n\\input{sub/ch}\n\\def\\p{../far/secret.txt}\\input{\\p}\n\\end{document}\n",
        "sub/ch.tex": "TEXPART\n",
        "h.html": f'<h1>HTMLPART</h1><img src="file://{secret.as_posix()}"><img src="../far/outside.png"><img src="pic.png">',
    }
    for name, text in files.items():
        (doc / name).write_text(text, encoding="utf-8")
    for name, part in (("m.md", "MDPART"), ("c.rst", "RSTPART"), ("o.org", "ORGPART"), ("l.tex", "TEXPART"), ("h.html", "HTMLPART")):
        out = w / (Path(name).stem + "-" + Path(name).suffix[1:] + ".pdf")
        d = jrun(["pdf_create.py", doc / name, "--out", out])
        t = "".join(text_of(out))
        c.ok(marker not in t and marker not in json.dumps(d) and part in t, f"create {name}: nothing from outside its folder, its own include kept")
        if name in ("m.md", "h.html"):
            imgs = jrun(["pdf_extract.py", "images", out, "--out", w / f"img-{Path(name).stem}"])
            c.ok(imgs["saved"] == 1, f"create {name}: only the picture inside the folder is embedded ({imgs['saved']})")
    d = jrun(["pdf_create.py", doc / "m.md", "--out", w / "m2.pdf"])
    ws = " ".join(d.get("warnings", []))
    c.ok("left out (outside the document's folder)" in ws and "left out (a URL pandoc must not open)" in ws, "create: pictures from outside the folder are reported")
    remote = w / "remote.md"
    remote.write_text("# R\n\n![far away](https://example.invalid/pic.png)\n\nREMOTEWORD\n", encoding="utf-8")
    d = jrun(["pdf_create.py", remote, "--out", w / "remote.pdf"], env={"DESK_OFFLINE": "1"})
    c.ok("REMOTEWORD" in "".join(text_of(w / "remote.pdf")) and "remote image not downloaded" in " ".join(d.get("warnings", [])), "create: remote picture offline becomes a link, with a warning")
    from _pdfkit import safe_filename

    names = {"con.tar.gz": "_con.tar.gz", "nul.report.pdf": "_nul.report.pdf", "Aux": "_Aux", "report.pdf": "report.pdf", "C:x.png": "C_x.png", "a.png:ads": "a.png_ads"}
    c.ok(all(safe_filename(k) == v for k, v in names.items()), "safe_filename: reserved names before the first dot, drive letters, streams")
    long = safe_filename("a" * 179 + ".x")
    c.ok(len(long) <= 180 and not long.endswith((".", " ")), "safe_filename: no trailing dot after cutting to 180 characters")


def test_office(c: Checks, fx: dict[str, Path], w: Path) -> None:
    """Office input: LibreOffice when installed (exact), pandoc + Typst otherwise; both paths when possible."""
    from _render import find_soffice, pandoc_path

    md = w / "note.md"
    md.write_text("# Office note\n\nHello from Word, ÉTÉ.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")
    docx = w / "note.docx"
    subprocess.run([pandoc_path(), str(md), "-o", str(docx)], check=True, capture_output=True, timeout=120)
    d = jrun(["pdf_create.py", docx, "--out", w / "pandoc.pdf"], env=NO_LO)
    t = "".join(text_of(w / "pandoc.pdf"))
    c.ok(d["engine"] == "pandoc+typst" and bool(d.get("notes")) and "Hello from Word" in t, "create .docx without LibreOffice: pandoc and Typst, with a note")
    r = run(["pdf_create.py", docx, "--out", w / "x.pdf", "--engine", "office"], env=NO_LO, expect=1)
    c.ok("LibreOffice is not installed" in r.stderr and not (w / "x.pdf").exists(), "create --engine office without LibreOffice: a clear error")
    shutil.copy(docx, w / "deck.pptx")
    r = run(["pdf_create.py", w / "deck.pptx", "--out", w / "deck.pdf"], env=NO_LO, expect=1)
    c.ok("presentations skill" in r.stderr, "create .pptx without LibreOffice: points to the presentations skill")
    if find_soffice():
        d = jrun(["pdf_create.py", docx, "--out", w / "lo.pdf", "--toc"])
        t = "".join(text_of(w / "lo.pdf"))
        c.ok(d["engine"] == "libreoffice" and "Hello from Word" in t and "ÉTÉ" in t, "create .docx with LibreOffice: exact export")
        c.ok(any("--toc" in n for n in d.get("notes", [])), "create with LibreOffice: says which flags do not apply")


def test_stamp(c: Checks, fx: dict[str, Path], w: Path) -> None:
    rep = fx["report"]
    d = jrun(["pdf_stamp.py", rep, w / "wm.pdf", "--watermark", "DRAFT", "--page-numbers", "{n} / {total}", "--header", "ACME confidential"])
    t = text_of(w / "wm.pdf")
    c.ok(d["stamped"] == 5 and all("DRAFT" in p for p in t), "stamp: watermark on every page")
    c.ok("1 / 5" in t[0] and "5 / 5" in t[4] and "ACME confidential" in t[2], "stamp: page numbers and header")
    c.ok("ZEPHYRINE" in t[0], "stamp: original text kept")
    c.ok("page numbers" in d.get("overlaps_existing_text", {}) and "header" not in d.get("overlaps_existing_text", {}), "stamp: notices page numbers over the document's own numbers")
    d = jrun(["pdf_stamp.py", rep, w / "bates.pdf", "--bates", "ABC", "--bates-start", "7"])
    t = text_of(w / "bates.pdf")
    c.ok("ABC000007" in t[0] and "ABC000011" in t[4] and d["bates_next_start"] == 12, "stamp: Bates numbers and next start")
    s = jrun(["pdf_text.py", w / "bates.pdf", "--search", "ABC000009"])
    b = s["matches"][0]["box"] if s["matches"] else [0, 0, 0, 0]
    c.ok(b[3] > 500, "stamp: Bates number near the bottom of the page")
    jrun(["pdf_stamp.py", fx["simple"], w / "rot.pdf", "--watermark", "COPY", "--pages", "3"])
    s = jrun(["pdf_text.py", w / "rot.pdf", "--search", "COPY"])
    b = s["matches"][0]["box"] if s["matches"] else [0, 0, 0, 0]
    c.ok(s["count"] == 1 and s["matches"][0]["page"] == 3 and 150 < (b[0] + b[2]) / 2 < 650 and 100 < (b[1] + b[3]) / 2 < 520, "stamp: centred on a rotated page")
    jrun(["pdf_stamp.py", rep, w / "img.pdf", "--image", fx["chart"], "--opacity", "0.3", "--position", "top-right", "--pages", "1"])
    c.ok(jrun(["pdf_info.py", w / "img.pdf"])["images"]["placed"] >= 2, "stamp: image stamp")


def test_redact(c: Checks, fx: dict[str, Path], w: Path) -> None:
    rep = fx["report"]
    d = jrun(["pdf_redact.py", rep, w / "red.pdf", "--find", SECRET_NAME, "--preset", "email", "--preset", "ssn", "--preset", "phone"])
    t = "\n".join(text_of(w / "red.pdf"))
    c.ok(d["verified"] and d["matches"] >= 5, "redact: verified")
    c.ok(SECRET_NAME not in t and SECRET_EMAIL not in t and SECRET_SSN not in t and "555 0134" not in t, "redact: text gone")
    c.ok("ZEPHYRINE" in t and "Contact" in t and "Her social security number is" in t, "redact: surrounding text intact")
    raw = (w / "red.pdf").read_bytes()
    c.ok(b"jane.doe" not in raw and b"Jane Doe" not in raw, "redact: not in the raw file")
    img = jrun(["pdf_render.py", w / "red.pdf", "--pages", "1", "--dpi", "72", "--max-edge", "0", "--out", w / "r"])["images"][0]["path"]
    s = jrun(["pdf_text.py", rep, "--search", SECRET_SSN])["matches"][0]["box"]
    c.ok(dark_share(Path(img), (int(s[0]) + 1, int(s[1]) + 1, int(s[2]) - 1, int(s[3]) - 1)) > 0.9, "redact: black box drawn over the SSN")
    d = jrun(["pdf_redact.py", fx["simple"], w / "simple.pdf", "--find", "secret-token-42", "--preset", "card"])
    t = "\n".join(text_of(w / "simple.pdf"))
    c.ok(d["verified"] and "secret-token-42" not in t and "4111" not in t and "Inside form:" in t and "stays hidden" in t, "redact: inside a form XObject, standard font")
    c.ok(b"secret-token-42" not in (w / "simple.pdf").read_bytes(), "redact: shared form content rewritten")
    d = jrun(["pdf_redact.py", rep, w / "dry.pdf", "--find", "QUOKKA", "--dry-run"])
    c.ok(not (w / "dry.pdf").exists() and d.get("matches") == 1, "redact --dry-run writes nothing")
    d = jrun(["pdf_redact.py", fx["scan"], w / "scan.pdf", "--box", "2:0,0.1,1,0.2"])
    c.ok(d["verified"], "redact: box over a scanned image")
    img = jrun(["pdf_render.py", w / "scan.pdf", "--pages", "2", "--dpi", "36", "--max-edge", "0", "--out", w / "r2"])["images"][0]["path"]
    wd, ht = png_size(Path(img))
    c.ok(dark_share(Path(img), (5, int(ht * 0.11), wd - 5, int(ht * 0.19))) > 0.95, "redact: image pixels painted over")
    d = jrun(["pdf_redact.py", rep, w / "raster.pdf", "--find", "QUOKKA", "--raster", "--label", "REDACTED"])
    t = text_of(w / "raster.pdf")
    c.ok(d["verified"] and "QUOKKA" not in t[3] and "ZEPHYRINE" in t[0], "redact --raster: page rasterised, other pages keep text")
    run(["pdf_redact.py", rep, w / "none.pdf", "--find", "NOT-IN-THERE"], expect=1)
    c.ok(not (w / "none.pdf").exists(), "redact: nothing found exits 1")


def test_optimize(c: Checks, fx: dict[str, Path], w: Path) -> None:
    photo = fx["photo"]
    d = jrun(["pdf_optimize.py", photo, w / "small.pdf", "--preset", "screen"])
    before, after = photo.stat().st_size, (w / "small.pdf").stat().st_size
    c.ok(after < before * 0.4 and d["images_recompressed"] >= 1, f"optimize screen: {before} → {after} bytes")
    c.ok("A photo" in text_of(w / "small.pdf")[0], "optimize: text kept")
    d = jrun(["pdf_optimize.py", fx["report"], w / "lossless.pdf", "--preset", "lossless"])
    c.ok((w / "lossless.pdf").stat().st_size <= fx["report"].stat().st_size, "optimize lossless: never larger")
    cmp = jrun(["pdf_compare.py", fx["report"], w / "lossless.pdf"])
    c.ok(cmp["identical"], "optimize lossless: looks identical")


def test_extract(c: Checks, fx: dict[str, Path], w: Path) -> None:
    d = jrun(["pdf_extract.py", "images", fx["report"], "--out", w / "img"])
    c.ok(d["saved"] == 1 and d["images"][0]["width"] == 480 and Path(d["images"][0]["file"]).exists(), "extract images")
    d = jrun(["pdf_extract.py", "images", fx["photo"], "--out", w / "img-png", "--png"])
    c.ok(d["saved"] == 1 and d["images"][0]["file"].endswith(".png") and png_size(Path(d["images"][0]["file"])) == (2400, 1800), "extract images --png at full resolution")
    d = jrun(["pdf_extract.py", "attachments", fx["extras"], "--out", w / "att"])
    f = w / "att" / "data.csv"
    c.ok(f.exists() and f.read_bytes() == b"region,q1\nNorth,120\n", "extract attachments")
    d = jrun(["pdf_extract.py", "fonts", fx["report"], "--extract", w / "fonts"])
    c.ok(len(d["fonts"]) >= 2 and any((w / "fonts").iterdir()), "extract fonts")
    d = jrun(["pdf_extract.py", "links", fx["report"]])
    c.ok(any(x.get("url") == "https://example.org/report" for x in d["links"]), "extract links")


def test_compare(c: Checks, fx: dict[str, Path], w: Path) -> None:
    d = jrun(["pdf_compare.py", fx["report"], fx["report2"], "--out", w / "cmp"])
    changed = [p for p in d["pages"] if p["status"] != "same text"]
    c.ok(not d["identical"] and len(changed) == 1 and changed[0]["old_page"] == 1, "compare: one changed page")
    md = run(["pdf_compare.py", fx["report"], fx["report2"], "--out", w / "cmp2"]).stdout
    c.ok("175" in md and "150" in md and "view_image" in md, "compare: text diff and images")
    pngs = list((w / "cmp").glob("*.png"))
    c.ok(len(pngs) == 1, "compare: one side-by-side diff image")
    run(["pdf_compare.py", fx["report"], fx["report2"], "--out", w / "cmp"], expect=1)
    run(["pdf_compare.py", fx["report"], fx["report2"], "--out", w / "cmp", "--force"])
    c.ok(True, "compare: replacing diff images needs --force")
    d = jrun(["pdf_compare.py", fx["report"], fx["report"], "--text-only"])
    c.ok(d["identical"], "compare: same file is identical")


def test_damaged(c: Checks, fx: dict[str, Path], w: Path) -> None:
    """Files pdfium, pypdf or pdfminer cannot read: every script falls back to another engine or a repaired copy."""
    r = run(["pdf_text.py", fx["noroot"]])
    c.ok("Damaged but readable text" in r.stdout and "repaired by pypdf" in r.stderr, "damaged: pdfium refuses a file, text read from a copy pypdf repaired")
    d = jrun(["pdf_render.py", fx["noroot"], "--out", w / "r"])
    c.ok(len(d["images"]) == 1 and Path(d["images"][0]["path"]).exists(), "damaged: renders through the repaired copy")
    c.ok(jrun(["pdf_info.py", fx["noroot"], "--no-cache"])["pages"] == 1, "damaged: pdf_info")
    d = jrun(["pdf_redact.py", fx["noroot"], w / "red.pdf", "--find", "readable"])
    c.ok(d["verified"] and "readable" not in "".join(text_of(w / "red.pdf")), "damaged: redaction verified")
    r = run(["pdf_text.py", fx["infopages"], "--layout", "--no-cache"])
    c.ok("Damaged but readable text" in r.stdout and "plain text shown instead" in r.stderr, "damaged: layout falls back to plain text where pdfplumber sees no page")
    jrun(["pdf_optimize.py", fx["infopages"], w / "opt.pdf", "--strip-metadata"])
    jrun(["pdf_stamp.py", fx["infopages"], w / "st.pdf", "--page-numbers"])
    c.ok("Damaged" in text_of(w / "opt.pdf")[0] and "1 / 1" in text_of(w / "st.pdf")[0], "damaged: /Info pointing at the page tree does not break writing")
    r = run(["pdf_text.py", fx["nopages"]])
    c.ok("Damaged but readable text" in r.stdout and "pdfminer" in r.stderr, "damaged: only pdfminer reads it: text still comes out")
    d = jrun(["pdf_text.py", fx["nopages"], "--search", "READABLE", "-i"])
    c.ok(len(d["results"]) == 1 and d["results"][0]["page"] == 1 and len(d["results"][0]["box"]) == 4, "damaged: search with pdfminer gives page and box")
    r = run(["pdf_render.py", fx["nopages"], "--out", w / "r2"], expect=1)
    c.ok("pdf_text.py can still try" in r.stderr, "damaged: render says the text can still be read")
    shutil.copy(fx["chart"], w / "picture.pdf")
    r = run(["pdf_info.py", w / "picture.pdf"], expect=1)
    c.ok("is a PNG image, not a PDF" in r.stderr, "not a PDF: says what the file is")
    from pypdf import PdfWriter

    PdfWriter().write(w / "empty.pdf")
    r = run(["pdf_text.py", w / "empty.pdf"], expect=1)
    r2 = run(["pdf_pages.py", "merge", w / "m.pdf", w / "empty.pdf", fx["report"]], expect=1)
    c.ok("has no pages" in r.stderr and "has no pages" in r2.stderr, "a PDF without pages: a clear error")
    (w / "cut.pdf").write_bytes(fx["report"].read_bytes()[:3000])
    r = run(["pdf_text.py", w / "cut.pdf"], expect=1)
    c.ok("all fail" in r.stderr and r.stderr.count("\n") == 1, "a truncated PDF: one clear line")


def test_form_edge(c: Checks, fx: dict[str, Path], w: Path) -> None:
    """Widgets without an /AcroForm, [export, display] option pairs, a push button's appearance in redaction."""
    d = jrun(["pdf_form.py", "list", fx["widgets"]])
    size = next((f for f in d["fields"] if f["name"] == "size"), {})
    c.ok(size.get("options") == ["s", "m", "l", "xl", "xxl"] and size.get("option_labels", [None])[0] == "Small", "form: export values and display labels listed")
    d = jrun(["pdf_form.py", "fill", fx["widgets"], w / "f.pdf", "--values", '{"size": "Huge"}', "--render", w / "png"])
    got = {f["name"]: f["value"] for f in jrun(["pdf_form.py", "list", w / "f.pdf"])["fields"]}
    c.ok(got.get("size") == "xxl" and "AcroForm" in d.get("note", ""), f"form: a display label fills the export value, the missing AcroForm is added ({got})")
    from pypdf import PdfReader

    fld = next(a.get_object() for a in PdfReader(w / "f.pdf").pages[0]["/Annots"] if a.get_object().get("/T") == "size")
    c.ok(list(fld.get("/I", [])) == [4] and int(fld.get("/TI", -1)) == 3, "form: list selection index set so renders show it")
    d = jrun(["pdf_redact.py", fx["widgets"], w / "red.pdf", "--find", "Submit"])
    r = PdfReader(w / "red.pdf")
    leftovers = [n for n in sorted({n for e in r.xref.values() for n in e}) if hasattr(r.get_object(n), "get_data") and b"Submit" in r.get_object(n).get_data()]
    c.ok(d["verified"] and not leftovers, f"redact: the button's appearance leaves the file with its widget ({leftovers})")
    d = jrun(["pdf_redact.py", fx["indexed"], w / "idx.pdf", "--box", "1:100,0,200,300"])
    img = jrun(["pdf_render.py", w / "idx.pdf", "--dpi", "72", "--max-edge", "0", "--out", w / "idx"])["images"][0]["path"]
    from PIL import Image

    with Image.open(img) as im:
        rgb = im.convert("RGB")
        left, right = rgb.getpixel((150, 150)), rgb.getpixel((250, 150))
    cs = next(v.get_object() for v in PdfReader(w / "idx.pdf").pages[0]["/Resources"]["/XObject"].values() if v.get_object().get("/Subtype") == "/Image")["/ColorSpace"]
    c.ok(left == (0, 0, 0) and right == (255, 0, 0) and str(cs[0]) == "/Indexed", f"redact: palette image painted in place, colours kept ({left}, {right}, {cs[0]})")


# ── regressions from the acceptance review ──────────────────────────────


def _raw_text_pdf(pages: list[list[tuple[float, float, str]]], size: tuple[int, int] = (612, 792)) -> bytes:
    """A PDF with Helvetica text lines at given positions: one list of (x, y, text) per page."""
    n = len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode()]
    font_no = 3 + 2 * n
    for lines in pages:
        body = b" ".join(b"BT /F1 12 Tf %.1f %.1f Td (%s) Tj ET" % (x, y, t.encode("latin-1").replace(b"(", b"\\(").replace(b")", b"\\)")) for x, y, t in lines)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {size[0]} {size[1]}] /Contents {len(objs) + 2} 0 R /Resources << /Font << /F1 {font_no} 0 R >> >> >>".encode())
        objs.append(_stream_obj(b"", body))
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    return raw_pdf(objs, f"<< /Size {len(objs) + 1} /Root 1 0 R >>".encode())


def _joined(text: str) -> str:
    """Page text as a reader sees it: line breaks as spaces, hyphenated line ends joined."""
    t = re.sub("[\ufffe\x02]\\s*", "", text)
    return re.sub(r"\s+", " ", t)


def test_regressions(c: Checks, fx: dict[str, Path], w: Path) -> None:
    """Defects found by the acceptance review: wrapped and hyphenated targets, page breaks, zebra tables, budgets,
    scanned-only files, zip bombs, broken page trees, short text layers, grouped e-mails, captions, headers."""
    # 1. Targets wrapped over lines or hyphenated at a line end (pdf_create's own justified text).
    lines = ["# Contacts", ""]
    for i in range(40):
        lines.append(("word " * (i % 13)) + f"Please write to legal{i}@northwind-example.com about the termination notice period today.\n")
    lines.append("Also the termination notice period legal99@northwind-example.com is ninety days, as agreed.\n")
    (w / "wrap.md").write_text("\n".join(lines), encoding="utf-8")
    run(["pdf_create.py", w / "wrap.md", "--out", w / "wrap.pdf", "--template", "plain"])
    raw = "\n".join(text_of(w / "wrap.pdf"))
    c.ok(re.search("legal\\d+@northwind[\ufffe\x02]", raw) is not None, "regress: the fixture hyphenates an e-mail at a line end")
    s = jrun(["pdf_text.py", w / "wrap.pdf", "--search", "termination notice period"])
    c.ok(s["count"] == 41, f"regress: search matches a phrase over line breaks and hyphenation ({s['count']} of 41)")
    s = jrun(["pdf_text.py", w / "wrap.pdf", "--search", r"legal\d+@northwind-example\.com"])
    c.ok(s["count"] == 41, f"regress: search matches e-mails hyphenated at a line end ({s['count']} of 41)")
    d = jrun(["pdf_redact.py", w / "wrap.pdf", w / "wrap-red.pdf", "--preset", "email", "--find", "termination notice period"])
    after = _joined("\n".join(text_of(w / "wrap-red.pdf")))
    c.ok(d["verified"] and not re.search(r"legal\d+|northwind|termination", after), f"regress: redaction leaves no wrapped or hyphenated target ({re.findall(r'legal[0-9]+|termination', after)[:4]})")
    c.ok(all(v["method"] == "removed" for v in d["pages"].values()) and "Please write to" in after, "regress: removed exactly, no page rasterised, surrounding text kept")
    d = jrun(["pdf_redact.py", w / "wrap.pdf", w / "wrap-red2.pdf", "--preset", "email", "--find", "termination notice period is ninety days"])
    after = _joined("\n".join(text_of(w / "wrap-red2.pdf")))
    c.ok(d["verified"] and "ninety" not in after and "termination notice period today" in after and all(v["method"] == "removed" for v in d["pages"].values()), "regress: a phrase that reads whole once an address inside it is removed is redacted too")
    s = jrun(["pdf_text.py", w / "wrap-red.pdf", "--search", "legal|termination|northwind", "-i"])
    c.ok(s["count"] == 0, "regress: the agent's own search check agrees with the verification")

    # 2. Targets split by a page break (running header and "n / 3" footer in between).
    pages = []
    bodies = [["First page text.", "Please write to legal7@northwind-"], ["example.com about the notice.", "Remember that the termination notice"], ["period is ninety days.", "End."]]
    for k, body in enumerate(bodies, 1):
        pages.append([(72, 760, "ACME Handbook"), *[(72, 700 - 20 * i, t) for i, t in enumerate(body)], (290, 40, f"{k} / 3")])
    (w / "breaks.pdf").write_bytes(_raw_text_pdf(pages))
    s = jrun(["pdf_text.py", w / "breaks.pdf", "--search", r"legal7@northwind-example\.com|termination notice period"])
    cont = sorted((m["page"], m.get("continues_on", {}).get("page")) for m in s["matches"])
    c.ok(cont == [(1, 2), (2, 3)], f"regress: search finds matches over page breaks ({cont})")
    d = jrun(["pdf_redact.py", w / "breaks.pdf", w / "breaks-red.pdf", "--preset", "email", "--find", "termination notice period is ninety days"])
    t = text_of(w / "breaks-red.pdf")
    c.ok(d["verified"] and "legal7" not in t[0] and "example.com" not in t[1] and "termination" not in t[1] and "ninety" not in t[2], "regress: redaction removes both parts of a target split by a page break")
    c.ok("ACME Handbook" in t[1] and "Remember that the" in t[1] and "End." in t[2], "regress: headers and the rest of the text kept")

    # 3. Zebra-striped tables (pdf_create's style): every row comes back, not only the shaded ones.
    (w / "zebra.md").write_text("# T\n\n| Item | Price |\n|---|---:|\n| SKU-5711 | 1.00 |\n| SKU-5712 | 2.00 |\n| SKU-5713 | 3.00 |\n| SKU-5714 | 4.00 |\n", encoding="utf-8")
    run(["pdf_create.py", w / "zebra.md", "--out", w / "zebra.pdf"])
    d = jrun(["pdf_text.py", w / "zebra.pdf", "--tables", "--no-cache"])
    rows = [r[0] for t in d["tables"] for r in t["rows"]]
    c.ok(d["count"] == 1 and d["tables"][0]["header"] == ["Item", "Price"] and rows == ["SKU-5711", "SKU-5712", "SKU-5713", "SKU-5714"], f"regress: zebra table read whole ({d['count']} tables, rows {rows})")

    # 4. Budgets: never over --max-chars (bar the continuation line), and the next command covers the rest.
    big = fx["big"]
    out = run(["pdf_text.py", big, "--pages", "100-140", "--max-chars", "5000"]).stdout
    m = re.search(r"Next: .* --pages (\d+)-140 --max-chars 5000", out)
    c.ok(len(out) <= 5000 + 400 and m is not None, f"regress: text within the budget ({len(out)} chars), next command to page 140")
    out = run(["pdf_text.py", big, "--words", "--pages", "1-220", "--max-chars", "20000"]).stdout
    c.ok(len(out) <= 20000 + 400 and "Next:" in out and re.search(r"--pages \d+-220", out) is not None, f"regress: --words within the budget ({len(out)} chars)")
    out = run(["pdf_text.py", big, "--search", r"P\d{4}X", "--max-chars", "3000"]).stdout
    m = re.search(r"Next: python3 scripts/pdf_text\.py \S+ --search \S+ --pages ([\d,-]+)", out)
    c.ok(len(out) <= 3000 + 600 and m is not None and "more on" in out, f"regress: search output stops on a page with a next command ({len(out)} chars)")
    if m:
        s = jrun(["pdf_text.py", big, "--search", r"P\d{4}X", "--pages", m.group(1), "--max-chars", "0"])
        first = int(m.group(1).split("-")[0])
        c.ok(s["count"] == 220 - first + 1 and s["matches"][0]["page"] == first, "regress: the search continuation reads on to the end")

    allw = jrun(["pdf_text.py", fx["report"], "--words", "--pages", "1"])["words"]
    top = jrun(["pdf_text.py", fx["report"], "--words", "--pages", "1", "--region", "0,0,1,0.2"])["words"]
    c.ok(0 < len(top) < len(allw) and all((x["box"][1] + x["box"][3]) / 2 <= 0.2 * 842 for x in top), f"regress: --words --region keeps the words of an area ({len(top)} of {len(allw)})")

    # 5. Scanned-only pages: say there is no text instead of "0 matches" or an empty map.
    run(["pdf_pages.py", "extract", fx["scan"], w / "scanonly.pdf", "--pages", "2"])
    r = run(["pdf_text.py", w / "scanonly.pdf", "--search", "INVOICE"])
    c.ok("Nothing to search" in r.stdout and "pdf_render.py" in r.stdout and "0 matches" not in r.stdout, "regress: search on a scan says there is no text layer")
    d = jrun(["pdf_text.py", fx["scan"], "--search", "INVOICE"])
    c.ok(d.get("pages_without_text") == [2] and "cannot be found" in d.get("note", ""), "regress: search notes the pages without text")
    out = run(["pdf_text.py", w / "scanonly.pdf"]).stdout
    c.ok("no text" in out and "pdf_render.py" in out and "| 1" not in out, "regress: default read of a scan points to rendering, not a map")

    # 6. A zip bomb given to pdf_create is refused before LibreOffice or pandoc sees it.
    import zipfile

    with zipfile.ZipFile(w / "bomb.docx", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        with z.open("word/document.xml", "w", force_zip64=True) as f:
            chunk = b" " * (1 << 20)
            for _ in range(20):
                f.write(chunk)
    r = run(["pdf_create.py", w / "bomb.docx", "--out", w / "bomb.pdf"], expect=1)
    c.ok("zip bomb" in r.stderr and not (w / "bomb.pdf").exists(), "regress: pdf_create refuses a zip bomb")

    # 7. A cyclic page tree: text and render work on the pages that can be reached.
    cyc = raw_pdf([b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R 2 0 R] /Count 2 >>",
                   b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
                   _stream_obj(b"", b"BT /F1 24 Tf 72 700 Td (Cycle page) Tj ET"), b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"], b"<< /Size 6 /Root 1 0 R >>")
    (w / "cycle.pdf").write_bytes(cyc)
    r = run(["pdf_text.py", w / "cycle.pdf"])
    c.ok("Cycle page" in r.stdout and "broken page tree" in r.stderr, "regress: cyclic page tree read through a rebuilt copy")
    d = jrun(["pdf_render.py", w / "cycle.pdf", "--out", w / "cyc"])
    c.ok(len(d["images"]) == 1, "regress: cyclic page tree renders")

    # 8. A short text layer is text; grouped addresses match the email preset.
    (w / "short.pdf").write_bytes(_raw_text_pdf([[(72, 700, "Invoice 42")]]))
    c.ok(jrun(["pdf_info.py", w / "short.pdf"])["text"]["pages_with_text"] == 1, "regress: a page with 9 characters has a text layer")
    (w / "group.pdf").write_bytes(_raw_text_pdf([[(72, 700, "Authors {ann,bob.lee}@example.org and x@y.io")]]))
    d = jrun(["pdf_redact.py", w / "group.pdf", "--preset", "email", "--dry-run"])
    c.ok(sorted(i["text"] for i in d["items"]) == ["x@y.io", "{ann,bob.lee}@example.org"], f"regress: grouped e-mail addresses ({[i['text'] for i in d['items']]})")

    # 9. Honest outputs: version header kept (1.7 with AES-256), normal file permissions, capped diff images noted.
    run(["pdf_pages.py", "extract", fx["report"], w / "x.pdf", "--pages", "1"])
    head = fx["report"].read_bytes()[:8]
    c.ok((w / "x.pdf").read_bytes()[:8] == head, f"regress: the PDF version header is kept ({head!r})")
    run(["pdf_meta.py", "encrypt", w / "short.pdf", w / "locked.pdf", "--password", "pw"])
    c.ok((w / "locked.pdf").read_bytes()[:8] == b"%PDF-1.7", "regress: AES-256 output says PDF 1.7")
    if os.name != "nt":
        mask = os.umask(0)
        os.umask(mask)
        c.ok((w / "x.pdf").stat().st_mode & 0o777 == 0o666 & ~mask, "regress: outputs get normal file permissions")
    run(["pdf_stamp.py", big, w / "big-st.pdf", "--header", "CHANGED COPY", "--pages", "1-3"])
    d = jrun(["pdf_compare.py", big, w / "big-st.pdf", "--pages", "1-3", "--max-images", "1", "--out", w / "cmp"])
    c.ok(d.get("images_skipped_pages") == [2, 3] and "got none" in d.get("images_note", ""), "regress: compare says which changed pages got no image")
    out = run(["pdf_compare.py", big, w / "big-st.pdf", "--text-only", "--max-chars", "300"]).stdout
    c.ok(re.search(r"Next: python3 scripts/pdf_compare\.py \S+ \S+ --pages [23]- --text-only --max-chars 300", out) is not None, "regress: compare output stops within the budget with a next command")

    # 9b. Big outlines and link lists: JSON always parses (never cut), Markdown stops on a line with a way on.
    tree = [{"title": f"Chapter {i} " + "x" * 40, "page": 1 + i % 5, "children": [{"title": f"Section {i}.{j} " + "y" * 30, "page": 1 + (i + j) % 5} for j in range(4)]} for i in range(400)]
    (w / "outline.json").write_text(json.dumps(tree), encoding="utf-8")
    run(["pdf_meta.py", "set-outline", fx["report"], w / "ol.pdf", "--outline", w / "outline.json"])
    js = json.loads(run(["pdf_meta.py", "outline", w / "ol.pdf", "--format", "json"]).stdout)
    c.ok(len(js) == 400 and len(js[-1]["children"]) == 4, "regress: a 2000-entry outline comes out as whole JSON")
    out = run(["pdf_meta.py", "outline", w / "ol.pdf"]).stdout
    c.ok(len(out) <= 61000 and "--depth" in out and "--format json > outline.json" in out, "regress: a long outline stops on a line and says how to see the rest")
    c.ok("4 entries below" in run(["pdf_meta.py", "outline", w / "ol.pdf", "--depth", "1"]).stdout, "regress: outline --depth")

    # 10. {title} without a Title: dropped with its separator and a note; --title fills it.
    r = run(["pdf_stamp.py", w / "short.pdf", w / "t1.pdf", "--header", "Review copy · {title}"])
    t = text_of(w / "t1.pdf")[0]
    c.ok("Review copy" in t and "short" not in t and "·" not in t and "no Title" in r.stdout, "regress: empty {title} is dropped with a note")
    run(["pdf_stamp.py", w / "short.pdf", w / "t2.pdf", "--header", "Review copy · {title}", "--title", "Q4 plan"])
    c.ok("Review copy · Q4 plan" in text_of(w / "t2.pdf")[0], "regress: --title fills {title}")

    # 11. Radio buttons are listed and filled by the words next to them; long text shrinks to fit its field.
    rf = make_labelled_form(w)
    d = jrun(["pdf_form.py", "list", rf])
    g = next(f for f in d["fields"] if f["name"] == "gender")
    c.ok([(x["value"], x["label"]) for x in g.get("choices", [])] == [("1", "Female"), ("2", "Male")], f"regress: radio choices with labels ({g.get('choices')})")
    d = jrun(["pdf_form.py", "fill", rf, w / "rf.pdf", "--values", '{"gender": "Male", "last": "Montgomery-Wellington the Third"}'])
    got = {f["name"]: f["value"] for f in jrun(["pdf_form.py", "list", w / "rf.pdf"])["fields"]}
    c.ok(got.get("gender") == "2" and "last" in d.get("font_sizes", {}), f"regress: radio filled by label, long text sized to fit ({got}, {d.get('font_sizes')})")
    d = jrun(["pdf_form.py", "fill", rf, w / "rf9.pdf", "--values", '{"last": "Short"}', "--font-size", "9"])
    c.ok(d.get("font_sizes", {}).get("last", "").endswith("9 pt"), "regress: --font-size sets the text size")


def make_labelled_form(d: Path) -> Path:
    """A radio group whose export values (1, 2) say nothing, with "Female" and "Male" printed next to the buttons,
    and a narrow text field."""
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject

    w = PdfWriter()
    page = w.add_blank_page(612, 792)
    helv = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding")}))
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): helv})})
    page[NameObject("/Contents")] = _stream(w, b"BT /Helv 12 Tf 72 700 Td (Gender:) Tj ET BT /Helv 12 Tf 182 700 Td (Female) Tj ET BT /Helv 12 Tf 282 700 Td (Male) Tj ET BT /Helv 12 Tf 72 650 Td (Last name:) Tj ET")

    def rect(*v):
        return ArrayObject([FloatObject(x) for x in v])

    def ap(state: str):
        bbox = rect(0, 0, 12, 12)
        on = _stream(w, b"q 0 g 3 3 6 6 re f Q", {"/Type": NameObject("/XObject"), "/Subtype": NameObject("/Form"), "/BBox": bbox})
        off = _stream(w, b"q 0 G 0.5 0.5 11 11 re S Q", {"/Type": NameObject("/XObject"), "/Subtype": NameObject("/Form"), "/BBox": bbox})
        return DictionaryObject({NameObject("/N"): DictionaryObject({NameObject(state): on, NameObject("/Off"): off})})

    annots = ArrayObject()
    radio = DictionaryObject({NameObject("/FT"): NameObject("/Btn"), NameObject("/T"): TextStringObject("gender"), NameObject("/Ff"): NumberObject(1 << 15), NameObject("/V"): NameObject("/Off"), NameObject("/Kids"): ArrayObject()})
    radio_ref = w._add_object(radio)
    for i, state in enumerate(["/1", "/2"]):
        kid = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"), NameObject("/Parent"): radio_ref, NameObject("/Rect"): rect(166 + i * 100, 698, 178 + i * 100, 710), NameObject("/AS"): NameObject("/Off"), NameObject("/AP"): ap(state), NameObject("/P"): page.indirect_reference}))
        radio["/Kids"].append(kid)
        annots.append(kid)
    text = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"), NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject("last"), NameObject("/Rect"): rect(150, 645, 230, 663), NameObject("/DA"): TextStringObject("/Helv 12 Tf 0 g"), NameObject("/P"): page.indirect_reference}))
    annots.append(text)
    page[NameObject("/Annots")] = annots
    w._root_object[NameObject("/AcroForm")] = w._add_object(DictionaryObject({NameObject("/Fields"): ArrayObject([radio_ref, text]), NameObject("/DR"): DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): helv})}), NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g")}))
    out = d / "labelled-form.pdf"
    with open(out, "wb") as f:
        w.write(f)
    return out


def test_help(c: Checks, fx: dict[str, Path], w: Path) -> None:
    scripts = sorted(p.name for p in HERE.glob("pdf_*.py"))
    c.ok(len(scripts) == 12, f"12 scripts ({len(scripts)})")
    slow = []
    for name in scripts:
        dt, r = timed([name, "--help"])
        if "python3 scripts/" not in r.stdout:
            c.ok(False, f"{name} --help shows examples")
        if dt > 1.0:
            slow.append(f"{name} {dt:.2f}s")
    c.ok(not slow, f"--help answers fast: {slow}")
    r = run(["pdf_text.py", w / "missing.pdf"], expect=1)
    c.ok(r.stderr.startswith("error:"), "missing input: error on stderr, exit 1")


if __name__ == "__main__":
    sys.exit(main())
