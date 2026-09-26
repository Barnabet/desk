# Recipes: pandoc, Typst and your own scripts

The scripts cover the usual jobs. For anything else, pass pandoc options through `mk_convert.py`, call pandoc or
Typst yourself, or write a short Python script that imports this skill's helpers. Run every command with the same
`python3`: it has pandoc, Typst, lxml, pypdfium2, Pillow and PyYAML.

## pandoc options through mk_convert.py

`--extra=ARG` passes a raw pandoc argument (repeat it for several). Desk's templates, filters, remote-image
handling, caching and warnings still apply.

```bash
python3 scripts/mk_convert.py thesis.md thesis.tex --extra=--top-level-division=chapter    # # = \chapter
python3 scripts/mk_convert.py notes.md notes-refs.md --to gfm --extra=--reference-links   # [text][1] links
python3 scripts/mk_convert.py talk.md talk.pptx --extra=--slide-level=2      # ## headings start slides
python3 scripts/mk_convert.py notes.md notes.html --extra=--strip-comments    # drop <!-- comments -->
python3 scripts/mk_convert.py ch2.md ch2.html --extra=--id-prefix=ch2- --fragment   # ids that do not clash
python3 scripts/mk_convert.py part2.md part2.html -N --extra=--number-offset=4   # numbering starts at 5
python3 scripts/mk_convert.py doc.md doc-wrapped.md --to markdown --wrap auto --extra=--columns=100
python3 scripts/mk_convert.py doc.md doc.pdf --extra=--lua-filter=my-filter.lua
```

A Lua filter changes the document between reading and writing. This one replaces every picture by its
description (an image alone in a paragraph is a Figure):

```lua
-- no-images.lua
function Figure(el) return pandoc.Para(pandoc.utils.blocks_to_inlines(el.caption.long)) end
function Image(el) return el.caption end
```

`--metadata-file meta.yaml` holds metadata for many conversions (title, author, lang, and for EPUB
`identifier`, `publisher`, `rights`, `belongs-to-collection`).

## Calling pandoc yourself

```python
import pathlib, subprocess, sys
import pypandoc

pandoc = pathlib.Path(pypandoc.__file__).parent / "files" / ("pandoc.exe" if sys.platform == "win32" else "pandoc")
out = subprocess.run([str(pandoc), "in.org", "-f", "org", "-t", "gfm", "--wrap=none"], capture_output=True, check=True)
print(out.stdout.decode())
```

- Always pass a list of arguments (never a shell string) and name outputs that do not exist yet.
- `pandoc -t json` gives the document tree: walk it in Python to count or collect elements precisely.
- pandoc downloads remote images for some outputs and waits on each for as long as the server takes, and it
  follows includes and embeds pictures from anywhere on disk. `mk_convert.py` downloads images first with a time
  limit and keeps includes and pictures inside the document's folder: prefer it for documents you did not write,
  and run pandoc yourself with `--sandbox` on those.
- A single run needs about 200 MB of memory per MB of Markdown. Convert long documents by section
  (`mk_convert.py --section 1.3`) or to PDF (converted in parts).

## Typst

```python
import typst

typst.compile("report.typ", output="report.pdf")                  # the folder of the file is the root
pages = typst.compile("report.typ", format="png", ppi=144)        # one PNG (bytes) per page
headings = typst.query("report.typ", "heading", field="body")     # JSON text of every heading
```

Typst snippets that are useful in the generated source (`--typ-out`) or in raw blocks in Markdown
(`` ```{=typst} `` … `` ``` ``):

```typst
#pagebreak()                                            // new page
#set page(flipped: true)                                // landscape from here (again with false)
#set text(size: 9pt)                                    // smaller text from here
#columns(2)[ … ]                                        // two columns for a passage
#block(breakable: false)[ … ]                           // keep a passage on one page
#figure(image("plot.png", width: 70%), caption: [Sales by month]) <fig-sales>
See @fig-sales.                                         // a numbered reference to it
#table(columns: (auto, 1fr), [*Key*], [*Value*], [a], [1])
#outline(target: figure.where(kind: table))             // a list of tables
```

A Typst error names the file, line and column: fix it in the `.typ` source, then compile again with
`mk_convert.py doc.typ doc.pdf --force`. When the Markdown makes Typst fail, Desk typesets it again with math and raw
Typst as plain text and says so in a warning: look at the pages with formulas before handing the PDF over.

## Your own scripts with the skill's helpers

Put the skill's `scripts/` folder on `sys.path` to reuse the parsers:

```python
import sys; sys.path.insert(0, "scripts")
from pathlib import Path
from _mk import outline                     # Markdown sections: address, level, title, line, end_line, words
from _html import extract_article           # a saved page → {title, byline, date, markdown, …}
from _epub import cached_book               # an EPUB → {meta, chapters: [{n, title, markdown, words}], sections}
import _nb                                  # notebooks: load, to_markdown, output_images, strip, dump

for f in sorted(Path("docs").glob("*.md")):
    for s in outline(f.read_text(encoding="utf-8")):
        if s["level"] == 1:
            print(f.name, s["address"], s["title"], s["total_words"])
```

More examples:

- **Code blocks from Markdown into files.** Take the fenced blocks with
  `re.findall(r"^```(\w+)\n(.*?)^```", text, re.S | re.M)` and write each one to its own file.
- **The same article from many saved pages.** Loop over `extract_article(path)` and write one Markdown file per
  page. Keep the title and byline in YAML front matter so `mk_convert.py` puts them in the title block.
- **An EPUB metadata fix.** Read the OPF file (`META-INF/container.xml` names it) with `zipfile`, change
  `dc:title` with lxml and write a new zip. The `mimetype` member goes first and uncompressed (`zipfile.ZIP_STORED`), then every other member is
  copied. Check the result with `epub_tool.py check new.epub`.
- **Clearing the outputs of some notebook cells only.** Use `nb = _nb.load(path)`, set
  `nb["cells"][i]["outputs"] = []` for those cells, then write `_nb.dump(nb)` to a new `.ipynb`.

Write results to new files and print where they are. Never overwrite the input.
