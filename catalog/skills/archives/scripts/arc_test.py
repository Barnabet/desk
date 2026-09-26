"""Test an archive's integrity: decompress every member (nothing is written), check CRCs, sizes and passwords."""

from __future__ import annotations

import sys
import time
import zlib
from typing import Any

from _common import SkillError, add_format, human_size, input_file, parser, run_main

EPILOG = """examples:
  python3 scripts/arc_test.py backup.tar.gz
  python3 scripts/arc_test.py secret.zip --password-file pw.txt     # also says whether the password is right
  python3 scripts/arc_test.py huge.zip --only 'data/**'
  python3 scripts/arc_test.py download.7z --format json

What is checked: zip and 7z members against their CRC-32; RAR members by the extractor; tar headers, member sizes and
the gzip/bzip2/xz/zstd stream checksums; single .gz/.bz2/.xz/.zst files end to end. Big zips are tested on several
cores. Bomb-like members (over --max-ratio), overlapping zip entries and streams declaring a dictionary over
DESK_ARC_MAX_DICT_MB (256) are refused, never decoded. Exit status 1 when anything is broken or refused; members
that are encrypted (no password) or need a missing tool/method make it INCOMPLETE (exit 0). Digests
computed here are cached, so arc_diff on the same archive is fast."""

VERIFIES = {"zip": "CRC-32 of every member", "7z": "CRC-32 of every member", "rar": "each member's checksum (by the extractor)",
            "tar": "tar headers, member sizes and the compressed stream's checksum", "single": "the compressed stream's checksum",
            "external": "whatever libarchive checks for this format"}


# errors that say this machine cannot decode a member (a missing tool or method), not that the member is damaged
UNTESTABLE = ("unsupported", "not supported", "needs unrar or 7-Zip", "install unrar or 7-Zip", "needs bsdtar", "needs libarchive")


def untestable(err: str) -> bool:
    return any(k in err for k in UNTESTABLE)


class CheckSink:
    """Counts and CRCs a member's bytes; complete when the backend reached its end without an error."""

    __slots__ = ("n", "crc", "error", "complete")

    def __init__(self) -> None:
        self.n = 0
        self.crc = 0
        self.error: str | None = None
        self.complete = False

    def write(self, b: bytes) -> None:
        self.n += len(b)
        self.crc = zlib.crc32(b, self.crc)

    def close(self) -> None:
        self.complete = True

    def fail(self, msg: str) -> None:
        self.error = msg


def check(arc: Any, entries: list[Any], limits: Any) -> tuple[dict[int, CheckSink], str | None]:
    import _arc

    sinks: dict[int, CheckSink] = {}

    def opener(e: Any) -> CheckSink:
        s = sinks[e.index] = CheckSink()
        return s

    stream_error = None
    try:
        arc.walk(entries, limits.wrap(opener, strict_sizes=arc.container != "single"))
    except _arc.StopWalk:
        stream_error = limits.stop
    except _arc.StreamError as err:
        stream_error = str(err)
    return sinks, stream_error


def _chunk_job(job: tuple[str, str | None, str | None, list[int], int, float]) -> dict[int, tuple[int, int, str | None, bool]]:
    import _arc
    import _guard

    _arc.EXTERNAL_ALLOWED = False  # workers decode zips in process only; external tools run one at a time
    path, password, encoding, indices, max_size, max_ratio = job
    arc = _arc.open_archive(path, password, encoding, max_ratio)
    arc.strict = True
    arc._gated = True  # the main process ran check_zip before handing out the work
    try:
        entries = arc.listing()
        want = set(indices)
        sinks, _ = check(arc, [e for e in entries if e.index in want], _guard.Limits(max_size, max_ratio))
        return {i: (s.n, s.crc, s.error, s.complete) for i, s in sinks.items()}
    finally:
        arc.close()


def run(arc: Any, entries: list[Any], limits: Any, jobs: int | None = None) -> tuple[dict[int, CheckSink], str | None]:
    """Tests the given members, in parallel processes for big zips (in-process decoding only, never external tools)."""
    from _common import pool_map, workers_for

    files = [e for e in entries if e.type == "file"]
    total = sum(e.size or 0 for e in files)
    n = workers_for(len(files), jobs)
    if arc.container == "zip" and arc.info.get("engine") == "zipfile" and total > 64 << 20 and len(files) > 8 and n > 1:
        arc.check_gate()
        chunks: list[list[int]] = [[] for _ in range(n)]
        loads = [0] * n
        for e in sorted(files, key=lambda x: -(x.size or 0)):
            k = loads.index(min(loads))
            chunks[k].append(e.index)
            loads[k] += e.size or 0
        results = pool_map(_chunk_job, [(str(arc.path), arc.password, arc.encoding, c, limits.max_size, limits.max_ratio) for c in chunks if c],
                           workers=n)
        sinks: dict[int, CheckSink] = {}
        for r in results:
            for i, (nb, crc, err, complete) in r.items():
                s = CheckSink()
                s.n, s.crc, s.error, s.complete = nb, crc, err, complete
                sinks[int(i)] = s
        return sinks, None
    return check(arc, files, limits)


def main() -> int:
    p = parser("Test archive integrity without extracting: every member is decompressed and checked (CRC, sizes, stream "
               "checksums, passwords). Works for zip, tar.*, 7z, rar, gz/bz2/xz/zst, iso, cab …", EPILOG)
    p.add_argument("archive")
    p.add_argument("--only", metavar="GLOB", action="append", help="test only matching members")
    p.add_argument("--exclude", metavar="GLOB", action="append", help="skip matching members")
    p.add_argument("--jobs", type=int, help="processes for big zips (default: cores, at most 8, and DESK_MAX_WORKERS; 1 = sequential)")
    p.add_argument("--no-cache", action="store_true", help="do not use or store the cached listing")
    from _arc import add_encoding_arg, add_password_args
    from _guard import add_limit_args

    add_limit_args(p, what="decompressed")
    add_password_args(p)
    add_encoding_arg(p)
    add_format(p)
    args = p.parse_args()

    import _arc
    import _guard
    import _safety

    t0 = time.perf_counter()
    path = input_file(args.archive)
    password = _arc.password_arg(args)
    limits = _guard.Limits.from_args(args)
    _arc.set_gate(limits.max_size)
    arc = _arc.open_archive(path, password, args.encoding, limits.max_ratio)
    arc.strict = True
    refused: list[tuple[Any, str]] = []
    try:
        entries = arc.listing(use_cache=not args.no_cache)
        info = arc.info
        sel = _safety.Selector(args.only, args.exclude, top=_safety.single_top(entries))
        chosen = [e for e in entries if e.type == _arc.FILE and (not sel or sel(e.name))]
        warning = _safety.unmatched_note(sel)
        why = limits.archive_refusal(chosen, info.get("archive_size") or 0, "test")
        if why:
            raise SkillError(why)
        overlap = {entries[i].index for i in (info.get("zip_scan") or {}).get("overlap", []) if 0 <= i < len(entries)}
        todo = []
        for e in chosen:
            r = "overlapping zip entry (a zip-bomb construction): not decoded" if e.index in overlap else limits.refused(e)
            if r:
                refused.append((e, r))
            else:
                todo.append(e)
        sinks, stream_error = run(arc, todo, limits, args.jobs)
    finally:
        arc.close()

    ok, broken, locked, unreached, cannot = [], [], [], [], []
    refused_idx = {x.index for x, _ in refused}
    for e in chosen:
        s = sinks.get(e.index)
        if e.index in refused_idx:
            continue
        if s is None:
            (locked if e.enc and not password else unreached).append(e)
        elif s.error:
            if "pass --password" in s.error and not password:
                locked.append(e)
            elif "dictionary" in s.error or "DESK_ARC_MAX_DICT_MB" in s.error or "--max-ratio" in s.error:
                refused.append((e, s.error))
            elif untestable(s.error):
                cannot.append((e, s.error))
            else:
                broken.append((e, s.error))
        elif not s.complete:
            unreached.append(e)
        elif e.size is not None and arc.container != "single" and s.n != e.size:
            broken.append((e, f"{s.n:,} bytes, header says {e.size:,}"))
        else:
            ok.append(e)
    if ok and len(ok) == len(chosen) and not sel and arc.container in ("tar", "single", "external"):
        _store_digests(path, args.encoding, entries, sinks)
    enc = [e for e in chosen if e.enc]
    pw_state = None
    wrong_pw: list[Any] = []
    if (enc or info.get("encrypted_header")) and password:
        enc_ok = [e for e in ok if e.enc]
        enc_bad = [e for e, _ in broken if e.enc]
        pw_state = "ok" if enc_ok and not enc_bad else "wrong" if enc_bad and not enc_ok else "mixed" if enc_bad else None
        if pw_state == "wrong":  # not damage: the members simply do not decrypt
            wrong_pw = enc_bad
            broken = [(e, err) for e, err in broken if not e.enc]
    scan = info.get("zip_scan") or {}
    structural = []
    if info.get("truncated"):
        structural.append(f"the zip is truncated: {human_size(info['truncated']['missing'])} are missing before its central directory, "
                          "so members stored near the end are lost")
    if scan.get("overlap"):
        structural.append(f"{len(scan['overlap'])} overlapping zip entries (a zip-bomb construction)")
    if scan.get("mismatch"):
        structural.append(f"{len(scan['mismatch'])} members whose local name differs from the central directory")
    if info.get("bomb_stop"):
        structural.append(f"the listing stopped after {info['bomb_stop'].get('members', 0):,} member(s): it decompresses to more than "
                          f"{human_size(info['bomb_stop'].get('decompressed') or 0)} (a likely decompression bomb; --max-ratio 0 lifts this)")
    if info.get("stream_error"):
        structural.append(f"the archive is damaged after member {len(entries)}: anything stored later is lost ({info['stream_error']})")
    if scan.get("badlocal"):
        structural.append(f"{len(scan['badlocal'])} members without a valid local header")
    data = {
        "archive": str(path), "format": info.get("format"), "verifies": VERIFIES.get(arc.container, ""),
        "members_tested": len(chosen), "ok": len(ok), "bytes": sum(sinks[e.index].n for e in ok if e.index in sinks),
        "broken": [{"member": e.name, "error": err} for e, err in broken], "wrong_password": [e.name for e in wrong_pw],
        "needs_password": [e.name for e in locked], "not_reached": [e.name for e in unreached],
        "refused": [{"member": e.name, "reason": r} for e, r in refused],
        "untestable": [{"member": e.name, "reason": r} for e, r in cannot],
        "stream_error": stream_error, "structural_problems": structural, "password": pw_state, "warnings": [warning] if warning else [],
        "seconds": round(time.perf_counter() - t0, 2),
    }
    passed = not broken and not unreached and not stream_error and not structural and not refused and not wrong_pw

    def render(d: dict[str, Any]) -> str:
        from _util import grouped_lines

        head = "OK" if passed and not locked and not cannot else "INCOMPLETE" if passed else "FAILED"
        lines = [f"{head}: {d['ok']:,} of {d['members_tested']:,} members passed ({human_size(d['bytes'])}) in {d['seconds']:.1f}s — "
                 f"{path.name} ({d['format']}); checked: {d['verifies']}"]
        if warning:
            lines.append(f"Warning: {warning}")
        if pw_state == "ok":
            lines.append("The password is correct.")
        elif pw_state == "wrong":
            lines.append("The password is wrong (the encrypted members do not decrypt).")
        elif pw_state == "mixed":
            lines.append("Some encrypted members decrypt and some do not: a second password, or corrupt data.")
        if structural:
            lines.append("Structure: " + "; ".join(structural))
        if stream_error:
            lines.append(f"The archive stream is broken: {stream_error}")
        if wrong_pw:
            lines.append(f"\nNot decrypted ({len(wrong_pw):,}): " + ", ".join(e.name for e in wrong_pw[:5]) + (" …" if len(wrong_pw) > 5 else ""))
        if broken:
            lines.append(f"\nBroken ({len(broken):,}):")
            lines += grouped_lines([(e.name, err) for e, err in broken], 100)
        if refused:
            lines.append(f"\nRefused, not decoded ({len(refused):,}):")
            lines += grouped_lines([(e.name, r) for e, r in refused], 50)
        if unreached:
            lines.append(f"\nNot reached ({len(unreached):,}), after the stream broke: " + ", ".join(e.name for e in unreached[:10])
                         + (" …" if len(unreached) > 10 else ""))
        if cannot:
            reasons: dict[str, list[str]] = {}
            for e, r in cannot:
                reasons.setdefault(r.replace("not readable: ", ""), []).append(e.name)
            lines.append(f"\nNot testable here ({len(cannot):,}; not known to be damaged):")
            for r, names in list(reasons.items())[:10]:
                lines.append(f"- {r}: " + ", ".join(names[:5]) + (f" … {len(names) - 5:,} more" if len(names) > 5 else ""))
        if locked:
            lines.append(f"\nEncrypted, not tested ({len(locked):,}): pass --password-file FILE (or --password). e.g. "
                         + ", ".join(e.name for e in locked[:5]))
        if wrong_pw and not (broken or unreached or stream_error or structural or refused):
            lines.append("\nNext: the data may well be fine; retry with the right password (--password-file FILE).")
        elif not passed:
            lines.append("\nNext: extract what is intact with arc_extract.py (broken members are reported and skipped).")
        return "\n".join(lines)

    from _common import emit

    emit(data, args.format, render)
    if not passed:
        n = len(broken) + len(unreached) + len(refused) + (1 if stream_error else 0) + len(structural) + len(wrong_pw)
        print(f"error: {n} problem(s) found" + (" (wrong password)" if wrong_pw else ""), file=sys.stderr)
        return 1
    return 0


def _store_digests(path: Any, encoding: str | None, entries: list[Any], sinks: dict[int, CheckSink]) -> None:
    """Keeps the CRCs computed here for arc_diff (tar and friends store none)."""
    import _cache

    if path.stat().st_size < 2 * 1024 * 1024 or not _cache.enabled():
        return
    value = {str(e.index): [sinks[e.index].n, sinks[e.index].crc] for e in entries if e.index in sinks}
    try:
        _cache.cached_json(path, "arc-crc", {"enc": encoding}, "1", lambda: value)
    except OSError:
        pass


if __name__ == "__main__":
    run_main(main)
