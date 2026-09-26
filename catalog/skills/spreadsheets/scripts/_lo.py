"""LibreOffice (optional) for exact recalculation, legacy-format writing and print-layout rendering.

Uses its own user profile in the temp area, preset to recalculate every formula when a file loads (LibreOffice's
default keeps Excel's cached results), so a round trip through soffice is a full recalculation.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

from _common import SkillError, run_tool
from _render import find_soffice

_XCU = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="FirstRun" oor:op="fuse"><value>false</value></prop></item>
</oor:items>
"""

_LOCKS: list[Path] = []


def _profile() -> tuple[Path, bool]:
    """(profile dir, shared?) — the shared one is reused when free (fast starts), else a private one."""
    base = Path(tempfile.gettempdir()) / "desk-sheets-lo-profile"
    lock = base.with_suffix(".lock")
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        _LOCKS.append(lock)
        shared = True
        prof = base
    except FileExistsError:
        try:
            if time.time() - lock.stat().st_mtime > 600:
                lock.unlink(missing_ok=True)
                return _profile()
        except OSError:
            pass
        prof = Path(tempfile.mkdtemp(prefix="desk-sheets-lo-"))
        shared = False
    xcu = prof / "user" / "registrymodifications.xcu"
    if not xcu.exists():
        xcu.parent.mkdir(parents=True, exist_ok=True)
        xcu.write_text(_XCU, encoding="utf-8")
    elif "OOXMLRecalcMode" not in xcu.read_text(encoding="utf-8", errors="replace"):
        text = xcu.read_text(encoding="utf-8", errors="replace")
        inject = _XCU.split("\n", 2)[2].rsplit("</oor:items>", 1)[0]
        xcu.write_text(text.replace("</oor:items>", inject + "</oor:items>"), encoding="utf-8")
    return prof, shared


def _release() -> None:
    for lock in _LOCKS:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass
    _LOCKS.clear()


def available() -> bool:
    return find_soffice() is not None


def convert(src: str | os.PathLike[str], fmt: str, timeout: float = 300) -> Path:
    """Converts with headless LibreOffice (recalculating on load) into a fresh temp folder; returns the file.

    fmt is a --convert-to target such as 'xlsx', 'ods', 'xls:"MS Excel 97"', 'pdf', 'csv'.
    """
    exe = find_soffice()
    if not exe:
        raise SkillError("LibreOffice is not installed (install it, or set DESK_SOFFICE to its soffice path)")
    src = Path(src).resolve()
    outdir = Path(tempfile.mkdtemp(prefix="desk-sheets-out-"))
    prof, shared = _profile()
    try:
        args = [
            exe, f"-env:UserInstallation={prof.as_uri()}", "--headless", "--invisible", "--norestore", "--nologo",
            "--nodefault", "--nolockcheck", "--convert-to", fmt, "--outdir", str(outdir), str(src),
        ]
        run_tool(args, timeout=timeout)
    except BaseException:
        shutil.rmtree(outdir, ignore_errors=True)
        raise
    finally:
        _release()
        if not shared:
            shutil.rmtree(prof, ignore_errors=True)
    ext = fmt.split(":", 1)[0]
    produced = sorted(outdir.glob(f"*.{ext}"))
    if not produced:
        shutil.rmtree(outdir, ignore_errors=True)
        raise SkillError(f"LibreOffice produced no .{ext} from {src.name} (the file may be damaged or password-protected)")
    return produced[0]
