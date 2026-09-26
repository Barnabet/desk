"""Hash files (md5, sha1, sha256, sha512, blake2b, crc32…), verify checksum files, write manifests, find duplicates."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import DEFAULT_MAX_CHARS, SkillError, UsageError, cap, emit, human_size, md_table, output_path, parser, run_main  # noqa: E402

EXAMPLES = """examples:
  python3 scripts/file_hash.py installer.dmg                              # sha256
  python3 scripts/file_hash.py installer.dmg --expect 9f86d081884c7d65…   # does it match the published hash?
  python3 scripts/file_hash.py release/ -r --algo sha256,md5              # a folder, several algorithms in one pass
  python3 scripts/file_hash.py --check SHA256SUMS                         # verify (GNU, BSD --tag and .sfv formats)
  python3 scripts/file_hash.py dataset/ -r --manifest dataset.sha256      # write a checksum file (relative paths)
  python3 scripts/file_hash.py photos/ -r --dupes                         # duplicate files and wasted space

Files are read once whatever the number of algorithms, hashed in parallel, and cached by path, size and
modification time (and, for files over 8 MB, by content), so re-hashing an unchanged tree is instant.
--check and --expect never use the cache: verifying reads every byte again.
"""


def main() -> int:
    p = parser("Hash files, verify checksum files, write manifests and find duplicates; streaming, parallel and cached.", EXAMPLES)
    p.add_argument("inputs", nargs="*", help="files, folders or globs")
    p.add_argument("-a", "--algo", default="sha256", help="md5, sha1, sha224, sha256, sha384, sha512, sha3_256, sha3_512, blake2b, blake2s, crc32; comma-separated, or 'all'")
    p.add_argument("-r", "--recursive", action="store_true", help="walk folders (skips .git, node_modules… unless --all)")
    p.add_argument("--all", action="store_true", help="with -r, include version-control and dependency folders")
    p.add_argument("--no-hidden", action="store_true", help="skip hidden files and folders")
    p.add_argument("--expect", help="the expected hash of a single file (algorithm inferred from its length)")
    p.add_argument("--check", metavar="SUMS", help="verify a checksum file (sha256sum/md5sum, shasum --tag, or .sfv)")
    p.add_argument("--base", help="with --check: folder the listed names are relative to (default: the checksum file's folder)")
    p.add_argument("--manifest", metavar="OUT", help="write a checksum file of the inputs")
    p.add_argument("--manifest-format", choices=["gnu", "bsd", "json", "csv"], default="gnu", help="gnu (sha256sum), bsd (shasum --tag), json or csv")
    p.add_argument("--relative-to", help="paths in the manifest relative to this folder (default: the common folder)")
    p.add_argument("--dupes", action="store_true", help="find duplicate files (size, then partial hash, then full hash)")
    p.add_argument("--min-size", default="1", help="with --dupes: ignore smaller files (e.g. 1KB; default 1 byte)")
    p.add_argument("--workers", type=int, help="parallel threads (default: CPU count, at most 8)")
    p.add_argument("--no-cache", action="store_true", help="hash again even when a cached hash exists")
    p.add_argument("--force", action="store_true", help="overwrite the manifest file")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="text budget (default 60000); a longer listing ends with the --offset command for the next part")
    p.add_argument("--offset", type=int, default=0, help="skip the first N rows of the listing (files, problems or duplicate sets): paging")
    p.add_argument("--format", choices=["md", "json", "csv", "sums", "bsd"], default="md", help="md, json, csv, or sums/bsd lines on stdout")
    a = p.parse_args()

    from _hashing import parse_algos
    from _store import Store

    algos = parse_algos(a.algo)
    store = Store(enabled=not a.no_cache)
    try:
        if a.check:
            return check(a, store)
        if not a.inputs:
            raise UsageError("give files or folders to hash (or --check SUMS)")
        from _walk import expand_inputs

        files, problems = expand_inputs(a.inputs, recursive=a.recursive, all_files=a.all, hidden=not a.no_hidden)
        for pr in problems:
            print(f"warning: {pr}", file=sys.stderr)
        if not files:
            raise SkillError("; ".join(problems) or "no files")
        if a.dupes:
            return dupes(a, files, store)
        if a.expect:
            from _hashing import algo_from_len

            if len(files) != 1:
                raise UsageError("--expect takes exactly one file")
            exp = a.expect.strip().lower().split()[0]
            algo = algo_from_len(len(exp), algos[0] if a.algo != "sha256" else None)
            if not algo:
                raise UsageError(f"cannot tell the algorithm of a {len(exp)}-character hash; pass --algo")
            algos = [algo]
        return hash_files(a, files, algos, store)
    finally:
        store.close()


def _items(files: list[tuple[str, str]]) -> list[tuple[str, int, int]]:
    import os

    out = []
    for p, _ in files:
        st = os.stat(p)
        out.append((p, st.st_size, st.st_mtime_ns))
    return out


def hash_files(a, files: list[tuple[str, str]], algos: list[str], store) -> int:
    import os

    from _hashing import common_base, format_line, hash_many, rel_display

    t0 = time.time()
    items = _items(files)
    # --expect is a verification: always read the bytes (a remembered hash cannot see corruption)
    res, errors, cached = hash_many(items, algos, store, a.workers, use_cache=not a.no_cache, reuse=not a.expect)
    elapsed = time.time() - t0
    total_bytes = sum(s for _, s, _ in items)
    shown = {p: d for p, d in files}
    rows = []
    for p, s, _m in items:
        rows.append({"file": shown[p], "size": s, **(res.get(p) or {}), **({"error": errors[p]} if p in errors else {})})
    if a.expect:
        algo = algos[0]
        exp = a.expect.strip().lower().split()[0]
        got = rows[0].get(algo)
        ok = got == exp
        data = {"file": rows[0]["file"], "algorithm": algo, "expected": exp, "actual": got, "match": ok}
        emit(data, "json" if a.format == "json" else "md", lambda d: f"{'OK' if d['match'] else 'MISMATCH'}: {d['file']} {d['algorithm']} {d['actual']}" + ("" if d["match"] else f"\n  expected {d['expected']}\nThe file is not the one published: re-download it or ask where it came from."), a.max_chars)
        return 0 if ok else 1
    if a.manifest:
        base = Path(a.relative_to).resolve() if a.relative_to else common_base([p for p, _ in files])
        # the output can only be an input if it already exists and is the same file: compare inodes, instead of
        # resolving every input path (seconds on 100k files)
        target = Path(a.manifest).expanduser()
        clash: list[str] = []
        if target.exists():
            st = target.stat()
            for p, _ in files:
                try:
                    ps = os.stat(p)
                except OSError:
                    continue
                if (ps.st_dev, ps.st_ino) == (st.st_dev, st.st_ino):
                    clash.append(p)
        out = output_path(target, clash, a.force)
        lines = []
        algo = algos[0]
        if a.manifest_format in ("gnu", "bsd"):
            for p, s, _m in items:
                if p in res:
                    lines.append(format_line(a.manifest_format, algo, res[p][algo], rel_display(p, base)))
            out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        elif a.manifest_format == "json":
            import json

            out.write_text(json.dumps({"base": str(base) if base else None, "algorithms": algos, "files": [{"path": rel_display(p, base), "size": s, **res.get(p, {})} for p, s, _m in items]}, indent=1), encoding="utf-8")
        else:
            import csv

            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["path", "size", *algos])
                for p, s, _m in items:
                    w.writerow([rel_display(p, base), s, *[res.get(p, {}).get(x, "") for x in algos]])
        print(f"wrote {out} ({len(res)} files, {algos[0] if a.manifest_format in ('gnu', 'bsd') else ', '.join(algos)}, paths relative to {base or '.'})")
        if a.manifest_format in ("gnu", "bsd"):
            from _types import quote_arg

            same = base is not None and os.path.normcase(os.path.abspath(out.parent)) == os.path.normcase(str(base))
            print(f"verify later with: python3 scripts/file_hash.py --check {quote_arg(str(out))}" + ("" if same or base is None else f" --base {quote_arg(str(base))}"))
        if errors:
            print(f"warning: {len(errors)} files could not be read", file=sys.stderr)
        return 0
    if a.format in ("sums", "bsd"):
        for r in rows:
            if "error" in r:
                print(f"error: {r['file']}: {r['error']}", file=sys.stderr)
                continue
            for algo in algos:
                print(format_line("gnu" if a.format == "sums" else "bsd", algo, r[algo], Path(r["file"]).as_posix()))
        return 0
    if a.format == "csv":
        import csv
        import io

        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["file", "size", *algos])
        for r in rows:
            w.writerow([r["file"], r["size"], *[r.get(x, r.get("error", "")) for x in algos]])
        print(buf.getvalue(), end="")
        return 0
    data = {"files": rows, "algorithms": algos, "count": len(rows), "bytes": total_bytes, "cached": cached, "seconds": round(elapsed, 2), "errors": len(errors)}
    if a.format == "json":
        emit(data, "json", max_chars=a.max_chars)
        return 1 if errors and len(errors) == len(rows) else 0
    head = f"{len(rows)} files, {human_size(total_bytes)}, {round(elapsed, 2)}s" + (f" ({cached} from cache)" if cached else "")
    if len(rows) == 1 and len(algos) > 1:
        r = rows[0]
        print(f"{r['file']} ({human_size(r['size'])})\n" + "\n".join(f"{x:9} {r.get(x, r.get('error'))}" for x in algos))
    else:
        table = md_table(["file", "size", *algos], [[r["file"], human_size(r["size"]), *[r.get(x, r.get("error", "")) for x in algos]] for r in rows[a.offset :]]).split("\n")
        _paged(a, head + "\n\n" + "\n".join(table[:2]), table[2:], "files")
    return 1 if errors and len(errors) == len(rows) else 0


def _paged(a, head: str, lines: list[str], what: str, starts: list[int] | None = None, foot: str = "") -> None:
    """`head`, then the rows (`lines`; `starts` marks which line starts each row, when rows span lines) that fit in
    --max-chars, then `foot`; a cut ends with this command at --offset N, N being the first row left out."""
    from _budget import fit_rows

    starts = list(range(len(lines))) if starts is None else starts
    first_line = {ln: i for i, ln in enumerate(starts)}
    rows = [(t, a.offset + first_line[i] if i in first_line else None) for i, t in enumerate(lines)]
    print(fit_rows(rows, a.max_chars, what, lambda n: _next_cmd(n), head=head, foot=foot))


def _next_cmd(offset: int) -> str:
    from _types import quote_arg

    args: list[str] = []
    skip = False
    for x in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if x == "--offset":
            skip = True
            continue
        if x.startswith("--offset="):
            continue
        args.append(x)
    return "python3 scripts/file_hash.py " + " ".join(quote_arg(x) for x in args + ["--offset", str(offset)])


def check(a, store) -> int:
    import os

    from _hashing import hash_many, parse_checksums

    sums = Path(a.check).expanduser()
    if not sums.is_file():
        raise SkillError(f"{sums} does not exist")
    text = sums.read_bytes().decode("utf-8", "replace")
    forced = None
    if a.algo and a.algo != "sha256":
        forced = a.algo.split(",")[0].strip().lower()
    entries, bad = parse_checksums(text, sums.name, forced)
    if not entries:
        raise SkillError(f"no checksum lines found in {sums.name}" + (f" (first unparsed line: {bad[0]!r})" if bad else ""))
    base = Path(a.base).expanduser() if a.base else sums.parent
    t0 = time.time()
    by_algo: dict[str, list] = {}
    results = []
    for algo, exp, name in entries:
        p = base / name
        if not p.is_file():
            results.append({"file": name, "status": "MISSING", "algorithm": algo})
            continue
        st = os.stat(p)
        by_algo.setdefault(algo, []).append((str(p), st.st_size, st.st_mtime_ns, exp, name))
    for algo, lst in by_algo.items():
        # verification always reads every byte: bit rot and careful tampering keep the size and modification time
        res, errs, _cached = hash_many([(p, s, m) for p, s, m, _e, _n in lst], [algo], store, a.workers, reuse=False)
        for p, _s, _m, exp, name in lst:
            if p in errs:
                results.append({"file": name, "status": "UNREADABLE", "algorithm": algo, "error": errs[p]})
            else:
                got = res[p][algo]
                results.append({"file": name, "status": "OK" if got == exp else "FAILED", "algorithm": algo, **({"expected": exp, "actual": got} if got != exp else {})})
    order = {n: i for i, (_a, _e, n) in enumerate(entries)}
    results.sort(key=lambda r: order.get(r["file"], 0))
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    data = {"checksum_file": str(sums), "base": str(base), "counts": counts, "results": results, "unparsed_lines": bad[:10], "seconds": round(time.time() - t0, 2)}

    ok = all(r["status"] == "OK" for r in results)
    if a.format == "json":
        emit(data, "json", max_chars=a.max_chars)
        return 0 if ok else 1
    c = data["counts"]
    head = f"{sums.name}: " + ", ".join(f"{v} {k}" for k, v in sorted(c.items())) + f" ({data['seconds']}s)"
    problems = [r for r in results if r["status"] != "OK"]
    foot = []
    if data["unparsed_lines"]:
        foot.append(f"\n{len(data['unparsed_lines'])} lines not understood, e.g. {data['unparsed_lines'][0]!r}")
    if c.get("MISSING") == len(results):  # said first: a cut listing would hide it
        head += f"\nEvery file is missing under {data['base']}: the names are relative to another folder; pass --base FOLDER (the folder that was hashed)."
    if not problems:
        print("\n".join([head, "All files match."] + foot))
        return 0 if ok else 1
    table = md_table(["file", "status", "algorithm", "detail"], [[r["file"], r["status"], r["algorithm"], r.get("error") or (f"expected {r['expected'][:16]}…, got {r['actual'][:16]}…" if r.get("actual") else "")] for r in problems[a.offset :]]).split("\n")
    _paged(a, head + "\n\n" + "\n".join(table[:2]), table[2:], "problems", foot="\n".join(foot))
    return 0 if ok else 1


def dupes(a, files: list[tuple[str, str]], store) -> int:
    from _hashing import find_duplicates, parse_size

    t0 = time.time()
    items = _items(files)
    min_size = parse_size(a.min_size)
    sets, stats = find_duplicates(items, store, min_size=min_size, workers=a.workers, use_cache=not a.no_cache)
    shown = {p: d for p, d in files}
    for s in sets:
        s["files"] = [shown.get(f, f) for f in s["files"]]
    wasted = sum(s["wasted"] for s in sets)
    data = {"files": len(items), "bytes": sum(s for _, s, _ in items), "duplicate_sets": len(sets), "duplicate_files": sum(s["count"] for s in sets), "reclaimable": wasted, "sets": sets, "stats": stats, "seconds": round(time.time() - t0, 2)}

    if a.format == "json":
        emit(data, "json", max_chars=a.max_chars)
        return 0
    head = f"{data['files']:,} files ({human_size(data['bytes'])}): {data['duplicate_sets']:,} sets of duplicates, {data['duplicate_files']:,} files, {human_size(data['reclaimable'])} reclaimable ({data['seconds']}s)"
    if not sets:
        print(head + "\nNo duplicates.")
        return 0
    lines: list[str] = []
    starts: list[int] = []
    for i, st in enumerate(sets[a.offset :], a.offset + 1):
        starts.append(len(lines))
        lines.append(f"\n{i}. {st['count']} × {human_size(st['size'])}" + (f"  sha256 {st['hash'][:16]}…" if st.get("hash") else ""))
        lines.extend(f"   - {f}" for f in st["files"][:20])
        if len(st["files"]) > 20:
            lines.append(f"   - … {len(st['files']) - 20} more (all of them: --format json)")
    _paged(a, head, lines, "duplicate sets", starts)
    return 0


if __name__ == "__main__":
    run_main(main)
