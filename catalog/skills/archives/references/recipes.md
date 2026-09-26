# Custom scripts

When the scripts do not cover a task, write a short Python script and run it with the same `python3`. The skill's
libraries are installed: zipfile, tarfile, gzip, bz2 and lzma (stdlib), plus py7zr, pyzipper, zstandard, rarfile,
inflate64 and pyppmd. The skill's own modules can be imported: they give one API for every format, with the same
safety checks as the scripts.

```python
import sys
sys.path.insert(0, "/path/to/skill/scripts")   # the folder holding arc_list.py
import _arc, _safety
```

## Iterate over members of any archive (streaming)

`Archive.walk` streams chosen members to "sinks" in archive order: one pass, nothing on disk. A sink has
`write(bytes)`, `close()` (the member ended and its checksum was verified) and `fail(message)`.

```python
import hashlib, _arc

arc = _arc.open_archive("release.tar.zst")            # zip, tar.*, 7z, rar, gz, iso …; password=, encoding=
entries = arc.listing()                               # [Entry(name, type, size, csize, mtime, mode, link, method, enc, crc)]
digests = {}

class Sha:
    def __init__(self, e): self.e, self.h = e, hashlib.sha256()
    def write(self, b): self.h.update(b)
    def close(self): digests[self.e.name] = self.h.hexdigest()
    def fail(self, msg): print("broken:", self.e.name, msg)

arc.walk([e for e in entries if e.type == "file"], Sha)
arc.close()
```

- `raise _arc.StopMember` in `write` to stop reading a member; `raise _arc.StopWalk` to stop the whole pass.
- `arc.read_member(entry, limit=None)` returns the bytes of one member.
- `arc.info` holds the format, engine, solid flag and zip scan. `arc.cached` tells whether the listing came from the
  cache.

## Check names yourself

```python
parts, issues = _safety.member_parts("../../etc/passwd")   # (['etc', 'passwd'], {'path-escape'})
findings = _safety.analyze(entries, arc.info)               # what arc_list prints, as dicts
sel = _safety.Selector(only=["docs/**"], top=_safety.single_top(entries))   # the scripts' --only/--exclude rules
wanted = [e for e in entries if sel(e.name)]
print(sel.unmatched())                                      # patterns that matched nothing
```

## Plain library recipes

**Replace one file in a zip (zip cannot be edited in place; write a new one):**
```python
import zipfile
with zipfile.ZipFile("in.zip") as src, zipfile.ZipFile("out.zip", "w", zipfile.ZIP_DEFLATED) as dst:
    for zi in src.infolist():
        data = b"new content" if zi.filename == "conf/app.json" else src.read(zi)
        dst.writestr(zi, data)            # keeps name, date, permissions and method
```
`arc_convert in.zip out.zip --add new.json=conf/app.json` does the same for any format, keeping the member in its
place.

**Split a big archive into parts (and join them):**
```python
size = 1_000_000_000
with open("big.tar.zst", "rb") as f:
    i = 0
    while chunk := f.read(size):
        open(f"big.tar.zst.{i:03d}", "wb").write(chunk); i += 1
# join: concatenate the parts in order into one file, then use it normally
```

**Write an AES-256 zip with pyzipper:**
```python
import pyzipper
with pyzipper.AESZipFile("secret.zip", "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as z:
    z.setpassword(b"pass"); z.write("report.pdf")
```

**Read a 7z member into memory with py7zr (1.x: through a writer factory):**
```python
import py7zr
from py7zr.io import BytesIOFactory
with py7zr.SevenZipFile("a.7z") as z:
    f = BytesIOFactory(64 << 20)
    z.extract(targets=["docs/readme.md"], factory=f)
    data = f.get("docs/readme.md").read()
```
`_arc` does this for you, and works around a py7zr bug with members interleaved with empty folders.

**Decompress zstd with multiple threads, or a zstd dictionary:**
```python
import zstandard
with open("x.zst", "rb") as f, open("x", "wb") as out:
    zstandard.ZstdDecompressor().copy_stream(f, out)
```

**Recompress a .gz as .zst without a temp file:**
```python
import gzip, zstandard
with gzip.open("log.gz") as src, open("log.zst", "wb") as dst:
    zstandard.ZstdCompressor(level=10, threads=-1).copy_stream(src, dst)
```

## Limits in your own scripts

`_arc` applies the same guards as the scripts: dictionaries are checked before decoding, every decoder call is
bounded, and bsdtar runs under a time and memory watchdog. Budgets are opt-in per walk:

```python
import _arc, _guard

arc = _arc.open_archive("upload.zip")                    # max_ratio= bounds how far a tar.* listing decompresses
entries = arc.listing()
lim = _guard.Limits(max_size=2 << 30, max_ratio=1000)
todo = [e for e in entries if e.type == "file" and not lim.refused(e)]   # refused: bomb-like declared sizes
try:
    arc.walk(todo, lim.wrap(Sha))                        # sinks fail past the declared size or ratio
except _arc.StopWalk:
    print(lim.stop)                                      # --max-size reached
```

## Notes

- Never call `ZipFile.extractall` or `TarFile.extractall` on untrusted archives. Use `arc_extract.py`, which applies
  the checks in `security.md`. If you must use tarfile, pass `filter="data"`.
- Plain zipfile, py7zr and lzma calls have no such guards: `ZipFile.read` of a bzip2 member, or py7zr on a Deflate
  or zstd block, can inflate gigabytes in one call, and an xz or 7z header can declare a 4 GB dictionary. Go
  through `_arc` for anything untrusted.
- Write outputs to new files. `_common.output_path(path, inputs, force)` refuses to overwrite inputs or existing
  files.
- Heavy work on big archives should go through `Archive.listing()`: it uses the content cache.
