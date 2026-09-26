# Custom scripts with this skill's helpers

When the scripts don't cover a case, write a short Python script and run it with the same `python3` (it has
puremagic, charset-normalizer and Pillow). This skill's modules do the hard parts; add its `scripts` folder to the
import path first:

```python
import os, sys
sys.path.insert(0, os.path.join(os.environ.get("SKILL_DIR", "."), "scripts"))
```

## Identify files in your own loop

```python
from _sniff import identify

for path in ["a.bin", "b.dat"]:
    r = identify(path)                      # never raises; an unreadable file has r["error"]
    print(r["type"], r["desc"], r["confidence"], r.get("skill"), r.get("command"), r.get("warnings"))
```

`identify(path, deep=False)` reads only 16 KB (what `file_survey` uses). The fields are listed in `formats.md`.
`_types.TYPES[type_id]` gives the description, MIME, usual extensions and group; `_types.route(type_id)` gives
`(skill, script, subcommand)`.

## Stream a huge text file

```python
from _textops import chunks, line_index, seek_line

for offset, buf in chunks("huge.log"):      # newline-aligned bytes, 8 MB at a time
    n = buf.count(b"ERROR")

idx = line_index("huge.log")                # {'lines', 'final_newline', 'marks'}; cached for files over 32 MB
off = seek_line("huge.log", 2_000_000, idx) # byte offset of line 2,000,000: one seek
with open("huge.log", "rb") as f:
    f.seek(off)
    print(f.readline().decode("utf-8", "replace"))
```

Encodings: `from _textenc import detect; enc = detect(open(p, "rb").read(65536))` gives `enc.name`,
`enc.confidence`, `enc.bom` and `enc.alternatives`.

## Hash, find duplicates

```python
from _hashing import hash_file, find_duplicates

print(hash_file("a.iso", ["sha256", "md5"]))            # one read, every algorithm
items = [(p, os.path.getsize(p), os.stat(p).st_mtime_ns) for p in paths]
sets, stats = find_duplicates(items)                    # [{'size', 'count', 'wasted', 'files', 'hash'}]
```

## Carve and profile binaries

```python
from _carve import scan, carve_span
from _binmap import profile, regions

found, rejected = scan("firmware.bin", kinds=("zip", "png"))
for f in found:
    start, end, exact = carve_span(f)                   # exact False: the format does not record its end
    print(hex(start), f.kind, end - start, f.how)
print(regions(profile("firmware.bin"))[:5])            # [{'start', 'end', 'kind', 'entropy', 'size'}]
```

## Formats the other skills don't read

```python
import plistlib                                          # binary or XML property lists (.plist, .webloc)
with open("Info.plist", "rb") as f:
    print(plistlib.load(f))

import sqlite3                                           # a quick look at an unknown SQLite file (read only)
con = sqlite3.connect("file:app.db?mode=ro", uri=True)
print(con.execute("select name, type from sqlite_master").fetchall())

import tomllib, json, csv                                # TOML, JSON, CSV: the standard library reads them
```

For a PEM or DER certificate, `file_identify.py` already prints subject, issuer, validity, key and names; the
parser is `_der.pem_summary(text)` and `_der.classify_der(bytes)`.

## Folders

```python
from _walk import walk, WalkStats

stats = WalkStats()
for e in walk("project", stats=stats):              # skips .git, node_modules… (ignores=() to include them)
    print(e.rel, e.size, e.mtime_ns, e.hidden)
print(stats.dirs, stats.skipped, stats.errors[:3])
```

Keep the skill's rules in custom scripts: never write over an input (`_common.output_path(out, [inputs])` refuses
it), write new files, and print what you did.

## Check a zip-based file before opening it yourself

```python
from pathlib import Path
from _sniff import zip_safety

for w in zip_safety(Path("attachment.docx"), office=True):   # [] when it is safe to open
    print(w)                                                # zip bomb, XML bomb, false sizes, overlapping members
```

`zip_safety` applies `_common.check_zip`, the check every Desk skill runs before opening a docx, xlsx, pptx, epub
or zip, and adds the crafted-archive checks. With Python's `zipfile`, read members through `ZipFile.open` (it
never inflates past a member's declared size) and never `extractall` an archive you have not checked.
