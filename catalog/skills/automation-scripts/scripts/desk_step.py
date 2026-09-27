"""The helper for automation script steps: inputs, the step's result (route, summary, outputs) and test runs.

Copy this file next to your script (it needs only the standard library), then import it there:

    from desk_step import inputs, is_test, output, step_dir, write_file

Run it directly for its two commands (see USAGE below):

    python3 desk_step.py copy DIR                               # put a copy of this helper in DIR
    python3 desk_step.py try DRAFT SCRIPT [options] [-- ARG ...]  # run a draft's script the way a step runs it
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Desk's limits for a step's result (the same numbers deskd checks).
OUTPUTS_MAX_BYTES = 16384
OUTPUT_STRING_MAX = 4000
OUTPUT_LIST_MAX = 200
SUMMARY_MAX = 2000
KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")
ROUTE_RE = re.compile(r"[a-z][a-z0-9_-]{0,39}")
RESERVED_ROUTES = ("error", "rejected")

USAGE = """desk_step.py: the helper for automation script steps (standard library only).

In a script, with this file copied next to it:
    from desk_step import inputs, is_test, output, run_dir, step_dir, write_file

Commands:
  python3 desk_step.py copy DIR
      Copy this helper into DIR (your draft's scripts folder).
  python3 desk_step.py try DRAFT SCRIPT [--inputs JSON] [--routes a,b] [--live] [--stdin TEXT]
                       [--step ID] [--run DIR] [--timeout-min N] [-- ARG ...]
      Run SCRIPT of the skill folder DRAFT as a script step would: in DIR/steps/ID (default
      automation-try/steps/try), with the DESK_* variables set and DESK_TEST=1 unless --live.
      Then check its DESK_OUTPUT against the limits and --routes, and print what the step
      would record. Exits 0 when the step would succeed, 1 when it would fail."""


# ── the step's environment ──────────────────────────────────────────────


def inputs() -> Dict[str, Any]:
    """The run's input values by key, from $DESK_INPUTS; file and folder inputs are paths of copies. {} outside a step."""
    path = os.environ.get("DESK_INPUTS")
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    return data


def is_test() -> bool:
    """True on a test run ($DESK_TEST is 1): read and compute for real, but only pretend to act outward."""
    return os.environ.get("DESK_TEST") == "1"


def step_dir() -> Path:
    """The step folder ($DESK_STEP_DIR, also the working directory): the only place the script may write."""
    return Path(os.environ.get("DESK_STEP_DIR") or os.getcwd())


def run_dir() -> Path:
    """The run folder ($DESK_RUN_DIR): inputs.json, inputs/ and every step's folder under steps/. Read-only."""
    return Path(os.environ.get("DESK_RUN_DIR") or os.getcwd())


def write_file(path: Union[str, "os.PathLike[str]"], content: Union[str, bytes]) -> Path:
    """Writes a file atomically (temp file, then rename), relative to the step folder; text is UTF-8."""
    target = Path(path)
    if not target.is_absolute():
        target = step_dir() / target
    _write_atomic(target, content.encode("utf-8") if isinstance(content, str) else content)
    return target


# ── the step's result ───────────────────────────────────────────────────


def output(route: Optional[str] = None, summary: Optional[str] = None, **outputs: Any) -> Dict[str, Any]:
    """Checks the step's result against Desk's limits and writes it to $DESK_OUTPUT atomically.

    route: one of the step's routes; summary: a line for the run report; outputs: keyword values that later steps
    read as {{steps.<id>.outputs.<key>}}. Raises ValueError, writing nothing, when something breaks a limit.
    Outside a step ($DESK_OUTPUT unset) it prints the JSON instead. Returns what it wrote.
    """
    doc: Dict[str, Any] = {}
    if route is not None:
        doc["route"] = check_route(route)
    if summary is not None:
        doc["summary"] = check_summary(summary)
    doc["outputs"] = check_outputs(outputs)
    data = _encode(json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False))
    target = os.environ.get("DESK_OUTPUT")
    if not target:
        print("DESK_OUTPUT is not set (this is not a step run), so here is what the step would report:")
        print(data.decode("utf-8"))
        return doc
    _write_atomic(Path(target), data)
    return doc


def check_route(route: object) -> str:
    """A route name as Desk accepts it. Whether the step declares it is checked by deskd (or by `try --routes`)."""
    if not isinstance(route, str):
        raise ValueError(f"route must be a string, not {type(route).__name__}")
    if route in RESERVED_ROUTES:
        raise ValueError(f'route "{route}" is reserved: Desk sets error and rejected itself')
    if not ROUTE_RE.fullmatch(route):
        raise ValueError(f'route "{route}": use lowercase letters, digits, _ and -, starting with a letter (at most 40 characters)')
    return route


def check_summary(summary: object) -> str:
    if not isinstance(summary, str):
        raise ValueError(f"summary must be a string, not {type(summary).__name__}")
    n = len(summary)
    if n > SUMMARY_MAX:
        raise ValueError(f"summary has {n} characters; at most {SUMMARY_MAX}")
    return summary


def check_outputs(outputs: object) -> Dict[str, Any]:
    """Outputs as Desk accepts them: snake_case keys; strings, numbers, booleans, null, or flat lists; small."""
    if not isinstance(outputs, dict):
        raise ValueError(f"outputs must be an object, not {type(outputs).__name__}")
    clean: Dict[str, Any] = {}
    for key, value in outputs.items():
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise ValueError(f"output key {key!r}: use lowercase letters, digits and _, starting with a letter (at most 40 characters)")
        clean[key] = _value(f"outputs.{key}", value)
    size = len(_encode(json.dumps(clean, ensure_ascii=False, separators=(",", ":"), allow_nan=False)))
    if size > OUTPUTS_MAX_BYTES:
        raise ValueError(
            f"outputs take {size} bytes as JSON; at most {OUTPUTS_MAX_BYTES}. Write the rest to a file in the step folder and output its path"
        )
    return clean


def _value(where: str, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if len(value) > OUTPUT_LIST_MAX:
            raise ValueError(f"{where} has {len(value)} items; lists hold at most {OUTPUT_LIST_MAX}")
        items = []
        for i, item in enumerate(value):
            if item is None:
                raise ValueError(f"{where}[{i}] is null; lists hold only strings, numbers and booleans")
            items.append(_scalar(f"{where}[{i}]", item))
        return items
    return _scalar(where, value)


def _scalar(where: str, value: Any) -> Any:
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    if isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{where} is {value}; JSON has no NaN or infinity")
        return value
    if isinstance(value, str):
        n = len(value)
        if n > OUTPUT_STRING_MAX:
            raise ValueError(
                f"{where} has {n} characters; strings hold at most {OUTPUT_STRING_MAX}. Write long text to a file in the step folder and output its path"
            )
        return value
    if isinstance(value, (dict, list, tuple)):
        raise ValueError(f"{where} is a {type(value).__name__}: outputs cannot nest. Use several keys, or write a JSON file in the step folder")
    raise ValueError(f"{where} is a {type(value).__name__}; outputs hold strings, numbers, booleans, null and lists of those (without null)")


def _encode(text: str) -> bytes:
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("the result holds text that is not valid Unicode (a lone surrogate); decode such bytes with errors='replace'") from None


def _write_atomic(path: Path, data: bytes) -> None:
    """Writes data to path through a temp file in the same folder and a rename, so readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── commands ────────────────────────────────────────────────────────────


def _copy(argv: List[str]) -> int:
    if len(argv) != 1:
        print("usage: python3 desk_step.py copy DIR", file=sys.stderr)
        return 2
    folder = Path(argv[0])
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / "desk_step.py"
    src = Path(__file__).resolve()
    if dest.resolve() != src:
        shutil.copyfile(str(src), str(dest))
    print(f"Copied desk_step.py to {dest}. Scripts in that folder can now: from desk_step import inputs, output, is_test")
    return 0


def _try(argv: List[str]) -> int:
    import argparse

    script_args: List[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, script_args = argv[:cut], argv[cut + 1 :]
    p = argparse.ArgumentParser(prog="desk_step.py try", description="Run a draft's script the way an automation script step runs it.")
    p.add_argument("draft", help="the skill folder, e.g. skill-drafts/price-watch")
    p.add_argument("script", help="the step's script: a file of the skill, or of its scripts folder")
    p.add_argument("--inputs", default="{}", help="the run's inputs as a JSON object (written to inputs.json)")
    p.add_argument("--routes", default="", help="the step's routes, comma-separated")
    p.add_argument("--live", action="store_true", help="run as a real run: no DESK_TEST")
    p.add_argument("--stdin", help="text for the script's standard input")
    p.add_argument("--step", default="try", help="the step id (default try)")
    p.add_argument("--run", default="automation-try", help="the run folder (default automation-try)")
    p.add_argument("--timeout-min", type=float, default=10.0, help="minutes before the step times out (default 10)")
    a = p.parse_args(argv)

    draft = Path(a.draft).resolve()
    if not draft.is_dir():
        print(f"{a.draft} is not a folder", file=sys.stderr)
        return 2
    file = draft / a.script
    if not file.is_file() and "/" not in a.script and "\\" not in a.script:
        file = draft / "scripts" / a.script
    if not file.is_file():
        print(f"{a.script} is neither in {draft} nor in its scripts folder", file=sys.stderr)
        return 2
    try:
        routes = [check_route(r.strip()) for r in a.routes.split(",") if r.strip()]
        values = json.loads(a.inputs)
        if not isinstance(values, dict):
            raise ValueError("--inputs must be a JSON object")
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    if not ROUTE_RE.fullmatch(a.step):
        print(f'step id "{a.step}": use lowercase letters, digits, _ and -, starting with a letter', file=sys.stderr)
        return 2

    run = Path(a.run).resolve()
    step = run / "steps" / a.step
    (run / "inputs").mkdir(parents=True, exist_ok=True)
    step.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(str(step / ".desk"), ignore_errors=True)  # deskd starts every attempt with an empty .desk folder
    (step / ".desk").mkdir()
    (run / "inputs.json").write_text(json.dumps(values, indent=2), encoding="utf-8")
    result = step / ".desk" / "output.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith("DESK_")}
    env.update(
        SKILL_DIR=str(draft),
        SKILL_NAME=draft.name,
        DESK_RUN_DIR=str(run),
        DESK_STEP_DIR=str(step),
        DESK_INPUTS=str(run / "inputs.json"),
        DESK_OUTPUT=str(result),
        PYTHONDONTWRITEBYTECODE="1",
    )
    if not a.live:
        env["DESK_TEST"] = "1"
    cmd = ([sys.executable] if file.suffix.lower() == ".py" else []) + [str(file), *script_args]
    stdin: Dict[str, Any] = {"input": a.stdin} if a.stdin is not None else {"stdin": subprocess.DEVNULL}
    print(f"Ran {shlex.join([file.name, *script_args])} in {step}{'' if a.live else ' with DESK_TEST=1'}")
    t0 = time.monotonic()
    timed_out = False
    try:
        r = subprocess.run(cmd, cwd=str(step), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace", timeout=a.timeout_min * 60, **stdin)
        out, code = r.stdout or "", r.returncode
    except subprocess.TimeoutExpired as e:
        raw = e.stdout or b""
        out, code, timed_out = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw, None, True
    secs = time.monotonic() - t0
    if out.strip():
        print(out if len(out) <= 4000 else f"[… the first {len(out) - 4000} characters are cut]\n{out[-4000:]}")
    ok, lines = _judge(code, timed_out, out, result, routes, a.timeout_min)
    print("---")
    print(f"Step: {'succeeded' if ok else 'failed'} in {secs:.1f} s" + ("" if ok else f": {lines.pop(0)}"))
    for line in lines:
        print(line)
    files = sorted(x.name for x in step.iterdir() if x.name != ".desk")
    print(f"Folder: {step}" + (f" ({', '.join(files[:20])}{', …' if len(files) > 20 else ''})" if files else " (empty)"))
    print("Mode: real run (--live)" if a.live else "Mode: test run (DESK_TEST=1); add --live to run it as a real run would")
    return 0 if ok else 1


def _judge(code: Optional[int], timed_out: bool, out: str, result: Path, routes: List[str], minutes: float) -> Tuple[bool, List[str]]:
    """What deskd would record: (succeeded, lines); on failure the first line is the error."""
    if timed_out:
        return False, [f"timed out after {minutes:g} minutes"]
    if code != 0:
        return False, [f"exit code {code}; the step's error is the last 4000 characters of its output"]
    last = next((line.strip() for line in reversed(out.splitlines()) if line.strip()), "")[:SUMMARY_MAX] or "Done"
    try:
        st = os.lstat(str(result))
    except FileNotFoundError:
        return True, ["Route: none", f"Summary: {last} (the last line printed: there is no DESK_OUTPUT)", "Outputs: none"]
    if not stat.S_ISREG(st.st_mode):
        return False, ["DESK_OUTPUT must be a regular file (not a link or a folder)"]
    raw = result.read_bytes().decode("utf-8", "replace")

    def no_constant(name: str) -> Any:
        raise ValueError(f"{name} is not JSON")

    try:
        doc = json.loads(raw, parse_constant=no_constant)
    except ValueError as e:
        return False, [f"DESK_OUTPUT is not valid JSON: {e}"]
    if not isinstance(doc, dict):
        return False, ["DESK_OUTPUT must hold a JSON object"]
    try:
        route = doc.get("route")
        if "route" in doc and not isinstance(route, str):
            raise ValueError(f"route must be a string, not {'null' if route is None else type(route).__name__}")
        if route is not None and route not in routes:
            raise ValueError(f'route "{route}" is not one of this step\'s routes ({", ".join(routes) or "none declared: pass --routes"})')
        summary = check_summary(doc["summary"]) if "summary" in doc else None
        outputs = check_outputs(doc["outputs"]) if "outputs" in doc else {}
    except ValueError as e:
        return False, [f"DESK_OUTPUT: {e}"]
    return True, [
        f"Route: {route or 'none'}",
        f"Summary: {summary if summary is not None else last}",
        f"Outputs: {json.dumps(outputs, ensure_ascii=False) if outputs else 'none'}",
    ]


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[0] == "copy":
        return _copy(argv[1:])
    if argv[0] == "try":
        return _try(argv[1:])
    print(f"Unknown command: {argv[0]}\n\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
