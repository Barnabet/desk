"""Self-test for the automation-scripts skill: desk_step.py's inputs, results and limits, and its copy and try commands.

    python3 scripts/selftest.py            # prints "ok: N checks in S s" or the failures, exit 1 on any failure
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator, List, Optional

sys.dont_write_bytecode = True  # never leave __pycache__ in the skill folder (it is read-only once installed)
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import desk_step  # noqa: E402

PY = sys.executable
CHECKS = 0
FAILS: List[str] = []
T0 = time.perf_counter()
STEP_VARS = ("DESK_INPUTS", "DESK_OUTPUT", "DESK_TEST", "DESK_STEP_DIR", "DESK_RUN_DIR")


def check(cond: object, what: str, detail: str = "") -> bool:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILS.append(f"{what}{': ' + detail[:600] if detail else ''}")
    return bool(cond)


@contextlib.contextmanager
def step_env(**values: Optional[str]) -> Iterator[None]:
    """Sets (or, with None, removes) the step variables for the block; the others are removed."""
    saved = {k: os.environ.get(k) for k in STEP_VARS}
    try:
        for k in STEP_VARS:
            os.environ.pop(k, None)
        for k, v in values.items():
            if v is not None:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def refused(what: str, fn: Callable[[], Any], words: str) -> None:
    try:
        fn()
    except ValueError as e:
        check(words in str(e), f"{what}: the message says {words!r}", str(e))
        return
    check(False, f"{what} is refused")


def run(*args: str, cwd: Path, expect: int = 0) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in STEP_VARS}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([PY, str(HERE / "desk_step.py"), *args], cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=120)
    check(r.returncode == expect, f"desk_step.py {' '.join(args)[:120]} exit {r.returncode} (want {expect})", r.stdout[-600:] + r.stderr[-600:])
    return r


# ── library ─────────────────────────────────────────────────────────────


def test_environment(tmp: Path) -> None:
    with step_env():
        check(desk_step.inputs() == {}, "inputs() is {} outside a step")
        check(desk_step.is_test() is False, "is_test() is False without DESK_TEST")
        check(desk_step.step_dir() == Path.cwd() and desk_step.run_dir() == Path.cwd(), "the folders default to the working directory")
    values = {"url": "https://example.com/prices.csv", "threshold": 5, "draft": True, "file": str(tmp / "inputs" / "a.csv")}
    (tmp / "inputs.json").write_text(json.dumps(values), encoding="utf-8")
    (tmp / "list.json").write_text("[1, 2]", encoding="utf-8")
    with step_env(DESK_INPUTS=str(tmp / "inputs.json"), DESK_TEST="1", DESK_STEP_DIR=str(tmp / "steps" / "a"), DESK_RUN_DIR=str(tmp)):
        check(desk_step.inputs() == values, "inputs() reads DESK_INPUTS", repr(desk_step.inputs()))
        check(desk_step.is_test() is True, "is_test() is True with DESK_TEST=1")
        check(desk_step.step_dir() == tmp / "steps" / "a", "step_dir() is DESK_STEP_DIR")
        check(desk_step.run_dir() == tmp, "run_dir() is DESK_RUN_DIR")
        written = desk_step.write_file("notes/today.md", "café ✓\n")
        check(written == tmp / "steps" / "a" / "notes" / "today.md", "write_file() resolves against the step folder", str(written))
        check(written.read_text(encoding="utf-8") == "café ✓\n", "write_file() writes UTF-8 text")
        check(sorted(p.name for p in written.parent.iterdir()) == ["today.md"], "write_file() leaves no temp file")
    for value in ("0", "true", ""):
        with step_env(DESK_TEST=value):
            check(desk_step.is_test() is False, f"is_test() is False with DESK_TEST={value!r}")
    with step_env(DESK_INPUTS=str(tmp / "list.json")):
        refused("inputs.json holding a list", desk_step.inputs, "JSON object")


def test_output_written(tmp: Path) -> None:
    target = tmp / "step" / ".desk" / "output.json"  # .desk does not exist yet: output() creates it
    with step_env(DESK_OUTPUT=str(target)):
        doc = desk_step.output(route="changed", summary="2 of 5 prices changed", count=2, ratio=0.4, ok=True, note=None,
                               items=["tea", "milk"], mixed=[1, 2.5, False, "x"], report=tmp / "step" / "report.md", text="café ✓")
        want = {"route": "changed", "summary": "2 of 5 prices changed",
                "outputs": {"count": 2, "ratio": 0.4, "ok": True, "note": None, "items": ["tea", "milk"], "mixed": [1, 2.5, False, "x"],
                            "report": str(tmp / "step" / "report.md"), "text": "café ✓"}}
        check(doc == want, "output() returns what it wrote", repr(doc))
        check(json.loads(target.read_text(encoding="utf-8")) == want, "output() writes DESK_OUTPUT", target.read_text(encoding="utf-8")[:300])
        check(sorted(p.name for p in target.parent.iterdir()) == ["output.json"], "output() leaves no temp file")
        desk_step.output()
        check(json.loads(target.read_text(encoding="utf-8")) == {"outputs": {}}, "a later call replaces the file; no null route or summary")
        edge = desk_step.output(summary="s" * 2000, s="x" * 4000, many=list(range(200)))
        check(json.loads(target.read_text(encoding="utf-8")) == edge, "values exactly at the limits are accepted")
        emoji = desk_step.output(e="\U0001F600" * 4000)  # characters are code points, as deskd counts them; 16 000 bytes
        check(json.loads(target.read_text(encoding="utf-8")) == emoji, "4000 emoji are 4000 characters")
        check(desk_step.check_route("nothing_new-2") == "nothing_new-2", "a route with _ and - is accepted")


def test_output_refused(tmp: Path) -> None:
    target = tmp / ".desk" / "output.json"
    with step_env(DESK_OUTPUT=str(target)):
        desk_step.output(route="kept", n=1)
        before = target.read_bytes()
        refused("a string over 4000 characters", lambda: desk_step.output(s="x" * 4001), "at most 4000")
        refused("4001 emoji", lambda: desk_step.output(s="\U0001F600" * 4001), "4001 characters")
        for key in ("Bad", "1x", "_x", "a-b", "x" * 41):
            refused(f"output key {key[:10]!r}", lambda key=key: desk_step.output(**{key: 1}), "output key")
        refused("a route in capitals", lambda: desk_step.output(route="Changed"), "lowercase")
        refused("a route over 40 characters", lambda: desk_step.output(route="r" * 41), "at most 40")
        refused("an empty route", lambda: desk_step.output(route=""), "lowercase")
        refused("the reserved route error", lambda: desk_step.output(route="error"), "reserved")
        refused("the reserved route rejected", lambda: desk_step.output(route="rejected"), "reserved")
        refused("a route that is not a string", lambda: desk_step.output(route=3), "must be a string")  # type: ignore[arg-type]
        refused("201 list items", lambda: desk_step.output(items=list(range(201))), "at most 200")
        refused("a nested object", lambda: desk_step.output(meta={"a": 1}), "cannot nest")
        refused("an object in a list", lambda: desk_step.output(items=[{"a": 1}]), "cannot nest")
        refused("a list in a list", lambda: desk_step.output(items=[[1]]), "cannot nest")
        refused("null in a list", lambda: desk_step.output(items=["a", None]), "is null")
        refused("NaN", lambda: desk_step.output(x=float("nan")), "NaN")
        refused("infinity", lambda: desk_step.output(x=float("inf")), "NaN")
        refused("a set", lambda: desk_step.output(x={1, 2}), "is a set")
        refused("a summary over 2000 characters", lambda: desk_step.output(summary="s" * 2001), "at most 2000")
        refused("a summary that is not a string", lambda: desk_step.output(summary=5), "must be a string")  # type: ignore[arg-type]
        refused("outputs over 16384 bytes", lambda: desk_step.output(**{f"k{i}": "x" * 4000 for i in range(5)}), "at most 16384")
        refused("multi-byte text over 16384 bytes", lambda: desk_step.output(**{f"k{i}": "é" * 3000 for i in range(3)}), "bytes as JSON")
        refused("a lone surrogate", lambda: desk_step.output(name="bad \udcff"), "lone surrogate")
        check(target.read_bytes() == before, "a refused result leaves the earlier DESK_OUTPUT as it was")
        check(sorted(p.name for p in target.parent.iterdir()) == ["output.json"], "a refused result leaves no temp file")


def test_output_outside_a_step(tmp: Path) -> None:
    buf = io.StringIO()
    with step_env(), contextlib.redirect_stdout(buf):
        doc = desk_step.output(summary="by hand", n=1)
    check(doc == {"summary": "by hand", "outputs": {"n": 1}}, "output() works without DESK_OUTPUT", repr(doc))
    check("DESK_OUTPUT is not set" in buf.getvalue() and '"n": 1' in buf.getvalue(), "output() prints the JSON without DESK_OUTPUT", buf.getvalue())


# ── commands ────────────────────────────────────────────────────────────

GOOD = '''import sys
from desk_step import inputs, is_test, output, write_file
v = inputs()
write_file("seen.txt", "|".join([v["name"], str(is_test()), *sys.argv[1:]]))
print("working")
output(route="changed", summary="Hello " + v["name"], count=len(sys.argv) - 1)
'''
PLAIN = 'print("working")\nprint("all done")\nprint("")\n'
FAIL = 'print("about to fail")\nraise SystemExit(3)\n'
BADJSON = 'import os\nopen(os.environ["DESK_OUTPUT"], "w").write("not json")\n'
NULLROUTE = 'import os\nopen(os.environ["DESK_OUTPUT"], "w").write(\'{"route": null}\')\n'
BIGLIST = 'import json, os\nopen(os.environ["DESK_OUTPUT"], "w").write(json.dumps({"outputs": {"items": list(range(201))}}))\n'
STDIN = 'import sys\nprint("got " + sys.stdin.read().strip())\n'


def test_commands(tmp: Path) -> None:
    r = run(cwd=tmp)
    check("copy DIR" in r.stdout and "try DRAFT SCRIPT" in r.stdout, "no command prints the usage", r.stdout)
    run("nope", cwd=tmp, expect=2)

    draft = tmp / "skill-drafts" / "hello"
    (draft / "scripts").mkdir(parents=True)
    (draft / "SKILL.md").write_text("---\nname: hello\ndescription: Says hello for the selftest.\n---\n", encoding="utf-8")
    for name, code in {"good.py": GOOD, "plain.py": PLAIN, "fail.py": FAIL, "badjson.py": BADJSON, "nullroute.py": NULLROUTE,
                       "biglist.py": BIGLIST, "stdin.py": STDIN}.items():
        (draft / "scripts" / name).write_text(code, encoding="utf-8")
    r = run("copy", "skill-drafts/hello/scripts", cwd=tmp)
    copied = draft / "scripts" / "desk_step.py"
    check(copied.is_file() and copied.read_bytes() == (HERE / "desk_step.py").read_bytes(), "copy puts an identical helper in the folder", r.stdout)

    seen = tmp / "automation-try" / "steps" / "try" / "seen.txt"
    r = run("try", "skill-drafts/hello", "good.py", "--inputs", '{"name": "Ada"}', "--routes", "changed,unchanged", "--",
            "a b", "; rm -rf ~", cwd=tmp)
    check("Step: succeeded" in r.stdout and "Route: changed" in r.stdout and "Summary: Hello Ada" in r.stdout, "try reports the result", r.stdout)
    check('Outputs: {"count": 2}' in r.stdout and "working" in r.stdout, "try shows the outputs and the script's output", r.stdout)
    check(seen.is_file() and seen.read_text(encoding="utf-8") == "Ada|True|a b|; rm -rf ~", "try passes argv as is, inputs and DESK_TEST",
          seen.read_text(encoding="utf-8") if seen.is_file() else "no seen.txt")
    check(json.loads((tmp / "automation-try" / "inputs.json").read_text(encoding="utf-8")) == {"name": "Ada"}, "try writes inputs.json")

    run("try", "skill-drafts/hello", "good.py", "--inputs", '{"name": "Bo"}', "--routes", "changed", "--live", cwd=tmp)
    check(seen.read_text(encoding="utf-8") == "Bo|False", "try --live runs without DESK_TEST", seen.read_text(encoding="utf-8"))

    r = run("try", "skill-drafts/hello", "good.py", "--inputs", '{"name": "Cy"}', cwd=tmp, expect=1)
    check("not one of this step's routes" in r.stdout, "try refuses a route the step does not declare", r.stdout)
    r = run("try", "skill-drafts/hello", "plain.py", cwd=tmp)
    check("Summary: all done" in r.stdout and "Outputs: none" in r.stdout, "without DESK_OUTPUT the summary is the last line", r.stdout)
    r = run("try", "skill-drafts/hello", "fail.py", cwd=tmp, expect=1)
    check("exit code 3" in r.stdout and "about to fail" in r.stdout, "try reports a non-zero exit", r.stdout)
    r = run("try", "skill-drafts/hello", "badjson.py", cwd=tmp, expect=1)
    check("not valid JSON" in r.stdout, "try refuses a DESK_OUTPUT that is not JSON", r.stdout)
    r = run("try", "skill-drafts/hello", "nullroute.py", cwd=tmp, expect=1)
    check("route must be a string, not null" in r.stdout, "try refuses a null route", r.stdout)
    r = run("try", "skill-drafts/hello", "biglist.py", cwd=tmp, expect=1)
    check("at most 200" in r.stdout, "try checks the outputs' limits", r.stdout)
    r = run("try", "skill-drafts/hello", "stdin.py", "--stdin", "some text", cwd=tmp)
    check("Summary: got some text" in r.stdout, "try passes --stdin", r.stdout)
    r = run("try", "skill-drafts/hello", "missing.py", cwd=tmp, expect=2)
    check("neither in" in r.stderr, "try names a missing script", r.stderr)
    run("try", "skill-drafts/hello", "good.py", "--routes", "error", cwd=tmp, expect=2)
    run("try", "skill-drafts/hello", "good.py", "--inputs", "[1]", cwd=tmp, expect=2)


def main() -> int:
    tests = [test_environment, test_output_written, test_output_refused, test_output_outside_a_step, test_commands]
    root = Path(tempfile.mkdtemp(prefix="desk-step-selftest-"))
    try:
        for fn in tests:
            w = root / fn.__name__
            w.mkdir()
            try:
                fn(w)
            except Exception as e:  # noqa: BLE001 — report and go on with the other tests
                import traceback

                check(False, f"{fn.__name__} crashed", "".join(traceback.format_exception(type(e), e, e.__traceback__))[-1500:])
    finally:
        shutil.rmtree(str(root), ignore_errors=True)
    secs = time.perf_counter() - T0
    if FAILS:
        for f in FAILS:
            print(f"FAIL {f}")
        print(f"failed: {len(FAILS)} of {CHECKS} checks in {secs:.1f}s")
        return 1
    print(f"ok: {CHECKS} checks in {secs:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
