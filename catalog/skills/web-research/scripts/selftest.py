#!/usr/bin/env python3
"""Offline self test for the web-research skill: builds saved pages and a crawl folder like Desk's web tools write
them, then runs pages.py and sources.py on them. Standard library only; the smoke command Desk runs after install."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

PAGE_A = """Title: Desk threads
URL: https://docs.desk.test/threads · HTTP 200 · text/html

# Threads

Desk runs threads in parallel while the coordinator reviews their work.

## Reviews

An independent reviewer checks each submission before it is accepted.

## Limits

At most four threads run at once by default.
"""

PAGE_B = """Title: Desk memory
URL: https://docs.desk.test/memory

# Memory

Memory entries are superseded, never edited in place.
"""


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=60)


def check(cond: bool, what: str, proc: subprocess.CompletedProcess[str] | None = None) -> None:
    if not cond:
        detail = f"\nstdout: {proc.stdout}\nstderr: {proc.stderr}" if proc else ""
        raise SystemExit(f"FAIL: {what}{detail}")
    print(f"ok: {what}")


def main() -> None:
    pages = str(HERE / "pages.py")
    sources = str(HERE / "sources.py")
    with tempfile.TemporaryDirectory(prefix="web-research-selftest-") as tmp:
        root = Path(tmp)
        saved = root / "web" / "pages"
        saved.mkdir(parents=True)
        (saved / "docs-desk-test-threads-1a2b3c4d.md").write_text(PAGE_A, encoding="utf-8")
        crawl = root / "web" / "crawls" / "docs.desk.test-20261028-120000"
        crawl.mkdir(parents=True)
        (crawl / "docs-desk-test-threads-1a2b3c4d.md").write_text(PAGE_A, encoding="utf-8")
        (crawl / "docs-desk-test-memory-5e6f7a8b.md").write_text(PAGE_B, encoding="utf-8")
        index = [
            {"file": "docs-desk-test-threads-1a2b3c4d.md", "url": "https://docs.desk.test/threads", "title": "Desk threads", "chars": 300},
            {"file": "docs-desk-test-memory-5e6f7a8b.md", "url": "https://docs.desk.test/memory", "title": "Desk memory", "chars": 90},
        ]
        (crawl / "index.json").write_text(json.dumps(index), encoding="utf-8")
        page = saved / "docs-desk-test-threads-1a2b3c4d.md"

        p = run(pages, "outline", str(page), cwd=root)
        check(p.returncode == 0 and "# Threads" in p.stdout and "## Reviews" in p.stdout and "## Limits" in p.stdout, "outline lists a page's headings", p)

        p = run(pages, "section", str(page), "reviews", cwd=root)
        check(p.returncode == 0 and "independent reviewer" in p.stdout and "four threads" not in p.stdout, "section prints one section only", p)

        p = run(pages, "grep", "superseded", str(crawl), "--context", "1", cwd=root)
        check(p.returncode == 0 and "docs-desk-test-memory-5e6f7a8b.md" in p.stdout and "superseded" in p.stdout, "grep finds a phrase across a crawl folder", p)

        p = run(pages, "grep", "no such phrase", str(crawl), cwd=root)
        check(p.returncode == 1 and "No matches" in p.stdout, "grep says when nothing matches", p)

        p = run(pages, "list", str(crawl), cwd=root)
        check(p.returncode == 0 and "https://docs.desk.test/memory" in p.stdout and "Desk threads" in p.stdout, "list reads the crawl index", p)

        p = run(sources, "add", "--url", "https://docs.desk.test/threads", "--title", "Desk threads", "--site", "Desk Docs",
                "--author", "Giraud, L.", "--published", "2026-10-01", "--accessed", "2026-10-28", "--saved", str(page), cwd=root)
        check(p.returncode == 0 and "[1]" in p.stdout and (root / "sources.json").exists(), "add records a source in sources.json", p)

        p = run(sources, "check-quote", "1", "An independent  reviewer checks each submission", cwd=root)
        check(p.returncode == 0 and "found" in p.stdout.lower(), "check-quote finds a real quote, whitespace aside", p)

        p = run(sources, "check-quote", "1", "A dependent reviewer checks each submission", cwd=root)
        check(p.returncode == 1 and "not found" in p.stdout.lower(), "check-quote rejects a changed quote", p)

        p = run(sources, "cite", "--style", "apa", cwd=root)
        check(p.returncode == 0 and "Giraud, L. (2026, October 1). Desk threads. Desk Docs. https://docs.desk.test/threads" in p.stdout, "cite prints APA", p)

        p = run(sources, "cite", "--style", "mla", cwd=root)
        check('Giraud, L. "Desk threads." Desk Docs, 1 Oct. 2026, https://docs.desk.test/threads. Accessed 28 Oct. 2026.' in p.stdout, "cite prints MLA", p)

        p = run(sources, "cite", cwd=root)
        check("[1] Desk threads. Desk Docs. https://docs.desk.test/threads (accessed 2026-10-28)." in p.stdout, "cite prints numbered references", p)
    print("selftest passed")


if __name__ == "__main__":
    main()
