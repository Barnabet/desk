---
name: automation-scripts
description: Write the scripts that an automation's script steps run, and test them the way Desk will. Covers how a step runs a script (argv arguments rendered from templates, never through a shell; the step folder as working directory and the only writable place; DESK_INPUTS, DESK_OUTPUT, DESK_TEST and the other variables), how it reports a route, a summary and outputs within Desk's limits, how it fails, how to make it safe to re-run (idempotent), and how to dry-run outward actions on test runs. Includes desk_step.py, a standard-library helper to copy next to your scripts, and its try command, which runs a draft's script as a step would. Use it when Desk asks you to write, test or fix the scripts of an automation.
license: MIT
---

# Automation scripts

An automation is a graph of steps that Desk runs on a schedule or on demand. A **script step** runs one file of a
skill, with no model involved:

```json
{"id": "fetch", "title": "Fetch prices", "kind": "script", "skill": "price-watch", "script": "fetch_prices.py",
 "args": ["{{inputs.url}}", "{{previous.steps.fetch.dir}}"], "routes": ["changed", "unchanged"],
 "on_error": {"retry": 2}, "idempotent": true}
```

Desk designs the automation and saves its steps. Your job is the skill those steps run: write it as a draft in
`skill-drafts/<name>/` (SKILL.md plus the scripts), try each script as a step (below), and report to Desk, for each
script: its arguments, the routes it can choose, its outputs and files, whether it acts outward, and whether it is
safe to re-run. Desk installs the draft.

## How a step runs your script

- **Command.** The file runs directly as an argument list (`python3 <file> <args…>` for a `.py` file). No shell
  parses anything: quotes, `;`, `$` and spaces in an argument reach the script as they are.
- **Python.** An installed draft gets no Python packages, and `python3` is the system's own (on a Mac it can be as
  old as 3.9). Use the standard library (`urllib.request`, `json`, `csv`, `sqlite3`, `zipfile`, `email`,
  `html.parser`…) and portable code (`pathlib`, argument lists, no `shell=True`, no POSIX-only modules). When a step
  needs more, tell Desk: a script step can run a built-in skill's script directly (for example data-files'
  `data_convert.py`), or it can be an agent step.
- **Arguments.** The step's `args` are templates, rendered one argument at a time: `{{inputs.<key>}}`,
  `{{steps.<id>.outputs.<key>}}`, `{{steps.<id>.summary}}`, `{{steps.<id>.route}}`, `{{steps.<id>.dir}}` (an earlier
  step's folder), `{{run.id}}`, `{{run.dir}}`, `{{run.date}}` (YYYY-MM-DD), `{{run.trigger}}`, `{{run.test}}`, and
  `{{previous.steps.<id>.dir}}` or `{{previous.steps.<id>.outputs.<key>}}` from the last succeeded run (an empty
  argument when there is none). An argument that is exactly `{{path}}` and holds a list becomes several arguments. At
  most 50 arguments of 4000 characters: pass big data as files, by folder. A step can also give text on standard
  input (`stdin`, a template too).
- **Environment.** `DESK_STEP_DIR` (the step folder, also the working directory), `DESK_RUN_DIR` (the run folder),
  `DESK_INPUTS` (the path of `inputs.json`: every input value by key, file and folder inputs as the paths of their
  copies), `DESK_OUTPUT` (where the result goes: `.desk/output.json` in the step folder), `DESK_TEST=1` on test runs
  only (absent otherwise), `SKILL_DIR` and `SKILL_NAME`. There are no secrets or API keys, and never put one in an
  argument or a file: arguments and output are logged and shown in the run report.
- **Files.** Write only in the step folder (temp dirs work too). The rest is read-only: the run folder
  (`inputs.json`, `inputs/`, earlier steps' folders under `steps/<id>/`), the previous run's folder, the project's
  sources and library. The network is open. Give the files you make stable names (`prices.csv`, `report.md`): later
  steps read them through `{{steps.<id>.dir}}`, and the step's `publish` globs copy them to the library.
- **Time.** A step times out after 10 minutes unless its `timeout_min` says otherwise.

## The result

- **Success** is exit code 0. Desk then reads `DESK_OUTPUT`, if the script wrote it: a JSON object with an optional
  `route`, `summary` and `outputs`.
  - `route`: one of the step's `routes` (lowercase letters, digits, `_` and `-`, starting with a letter, at most 40
    characters; `error` and `rejected` are Desk's own). Routes choose the edges that fire next: `changed` goes on to
    a report, `unchanged` ends the run quietly.
  - `summary`: at most 2000 characters, shown in the run report and the app.
  - `outputs`: keys of lowercase letters, digits and `_` (starting with a letter, at most 40 characters). Values are
    strings (at most 4000 characters), numbers, booleans, null, or flat lists of strings, numbers and booleans (at
    most 200 items, no null). At most 16 384 bytes as JSON. Later steps read them as
    `{{steps.<id>.outputs.<key>}}` and in conditions such as `steps.fetch.outputs.count > 0`. Put anything bigger
    in a file and output its path.
  - An invalid file fails the step. Without the file, the summary is the last non-blank line printed, with no outputs.
- **Failure** is any other exit code (an uncaught exception is one) or the timeout. The step's error is the last 4000
  characters of the output, so print why before exiting: `sys.exit("Could not fetch prices: HTTP 503")`. Never catch
  an error and exit 0. The whole output is logged: print short progress lines, not data dumps.
- **Retries.** A step with `on_error: {"retry": n}` runs again after 30 s, 2 min and 8 min, in the same folder, with
  whatever the failed attempt left there.

## Safe to re-run

If Desk stops while a script runs, nobody knows what the script did, so the step is re-run only if it is marked
`"idempotent": true`. Make scripts safe to re-run whenever you can, and tell Desk which ones are:
- Write files atomically (`write_file` below), and let a rerun overwrite what an earlier attempt wrote.
- Before acting outward (sending, posting, uploading, deleting, paying), check whether it was already done: a marker
  file written in the step folder right after acting, or the remote side's own record. A script that acts outward
  is safe to re-run only if the remote side ignores duplicates; say so either way.

## Test runs

Desk tests every automation before proposing it, with `DESK_TEST=1`. On a test run, do the reading, fetching and
computing for real and produce the same files, route and outputs, but only pretend to act outward, and say in the
summary what would have happened (`Test run: would have emailed 3 people`).

```python
if is_test():
    print(f"Test run: would post {len(items)} items to {url}")
elif (step_dir() / "posted.json").exists():
    print("Already posted by an earlier attempt")
else:
    post(url, items)  # the outward action
    write_file("posted.json", json.dumps({"count": len(items)}))
```

## The helper: desk_step.py

`scripts/desk_step.py` needs only the standard library. Skills cannot import from each other, so copy it into your
draft's scripts folder, where your scripts import it:

    skill_run automation-scripts desk_step.py   args: ["copy", "skill-drafts/price-watch/scripts"]

```python
from desk_step import inputs, is_test, output, run_dir, step_dir, write_file
```

- `inputs()`: the input values by key (`{}` outside a step).
- `output(route=None, summary=None, **outputs)`: checks the result against everything above (key names, value types,
  lengths, list sizes, the byte total, the route's name) and writes `DESK_OUTPUT` atomically. It raises
  `ValueError` and writes nothing when something breaks a limit. Call it once, at the end. Outside a step it prints
  the JSON instead.
- `is_test()`: true on a test run. `step_dir()` and `run_dir()`: the two folders, as `Path`s.
- `write_file(path, text_or_bytes)`: an atomic write, relative to the step folder.

## Worked example

`fetch_prices.py`, next to `desk_step.py` in `skill-drafts/price-watch/scripts/`, for the step at the top:

```python
"""Fetch a CSV of prices and compare it with the previous run's copy.

    fetch_prices.py URL PREVIOUS_DIR     (PREVIOUS_DIR is empty on the first run)
"""
import csv
import io
import sys
import urllib.request
from pathlib import Path

from desk_step import output, write_file


def prices(text):
    return {row["item"]: row["price"] for row in csv.DictReader(io.StringIO(text))}


def main():
    url, previous = sys.argv[1], sys.argv[2]
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            text = r.read().decode("utf-8")
    except OSError as e:  # URLError and timeouts: fail the step with a clear last line
        sys.exit(f"Could not fetch {url}: {e}")
    write_file("prices.csv", text)  # for later steps, and for the next run's comparison
    new = prices(text)
    old_file = Path(previous) / "prices.csv" if previous else None
    old = prices(old_file.read_text(encoding="utf-8")) if old_file and old_file.is_file() else {}
    changed = sorted(item for item, price in new.items() if old.get(item) != price)
    if changed:
        output(route="changed", summary=f"{len(changed)} of {len(new)} prices changed", count=len(changed), items=changed[:200])
    else:
        output(route="unchanged", summary=f"No price changed ({len(new)} items)", count=0)


if __name__ == "__main__":
    main()
```

It only reads, and writes its own folder, so it is safe to re-run and the same on test runs.

## Try it as a step

`try` runs a draft's script the way a step will: in a step folder in your workspace (`automation-try/steps/try/`,
kept between tries), with the `DESK_*` variables and `DESK_TEST=1` unless `--live`. Then it checks `DESK_OUTPUT`
against the limits and the step's routes, and prints the route, summary and outputs the step would record:

    skill_run automation-scripts desk_step.py   args: ["try", "skill-drafts/price-watch", "fetch_prices.py",
        "--inputs", "{\"url\": \"https://example.com/prices.csv\"}", "--routes", "changed,unchanged",
        "--", "https://example.com/prices.csv", ""]

- Arguments after `--` are passed as they are: write them as the templates would render.
- For an earlier step's folder, put its files under `automation-try/steps/<id>/` and pass that path.
- Try the first run and a later one, a failure (a bad input, a dead URL), and a second try in the same folder.
  Use `--live` only for scripts that do not act outward.
- `try` runs Desk's Python 3.12; the real step runs the system's `python3`, so keep to Python 3.9 features.

## Before you report

- Each script has a docstring with its arguments. The draft's SKILL.md lists every script with its arguments,
  routes, outputs and files.
- The script chooses only the routes you name to Desk.
- Test runs never act outward, and their summary says what would have happened.
- Failures exit non-zero with a clear last line.
- Every script passed `try`, including a failure; you say which ones are safe to re-run.
