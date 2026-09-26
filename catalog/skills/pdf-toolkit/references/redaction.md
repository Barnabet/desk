# How pdf_redact.py works

A black rectangle drawn over text hides nothing: the text is still in the file and can be copied out. Real
redaction removes what is under the box. `pdf_redact.py` does that, then checks its own work.

## 1. Find the targets

- `--find TEXT` (repeatable) is literal. `--ignore-case`, `--whole-word`.
- `--regex PATTERN` is a Python regular expression over each page's text.
- `--preset` adds tested patterns: `email` (also the grouped form of papers, `{ann,bob}@example.org`), `phone`,
  `ssn` (US), `card` (13–19 digits, Luhn-checked), `iban` (checksum-validated), `ip`, `url`, `date` (numeric dates
  such as 12/03/2024, 12.3.24 and 2024-03-12). When `@` appears on a page where no address matched, the output
  says so: look at it with `pdf_text.py --search '@'`.

Text is matched the way it reads, not the way the PDF stores it (the same rules as `pdf_text.py --search`):
- a space in a target matches any whitespace, line breaks included ("notice period" over two lines);
- a word hyphenated at a line end matches whole ("termi-/nation" is "termination"), and so does a real hyphen at a
  line end ("legal@northwind-/example.com");
- a target that runs over a page break matches too: running headers, footers and page numbers between the pages
  are skipped, and both parts are redacted;
- text that only reads as a target once other targets are removed ("notice period <e-mail> is ninety days"
  becomes "notice period is ninety days") is redacted as well, since a reader copying the text would see it.
- `--box PAGES:x0,top,x1,bottom` redacts an area in view coordinates (see `coordinates.md`), for example a
  signature, a photo or a scanned line. `PAGES` is a range or `all`; fractions of the page work
  (`all:0,0,1,0.08` is the top 8 % of every page).
- `--pages` limits text targets to some pages. `--padding` (points, default 1) grows each box slightly.

Always preview first: `--dry-run` lists every match with its page and box without writing anything, and
`--dry-run --render DIR` draws the boxes on page renders to look at with view_image.

## 2. Remove what is under the boxes

For each affected page, the content stream is parsed and rewritten:

- **Text**: every glyph whose centre falls in a box is removed. Its advance is kept (a spacing adjustment
  replaces it), so the remaining text stays exactly where it was. This works for simple and composite (CID) fonts,
  kerned `TJ` arrays, character and word spacing, horizontal scaling, and text inside form XObjects (a shared form
  is copied first, so other pages that use it are untouched).
- **Images**: the pixels under the box are painted over in the image itself (soft masks too), so the picture is
  kept elsewhere. Palette (indexed) images keep their palette and colours; the covered pixels take its darkest
  colour. Inline images that overlap a box are dropped.
- **Vector graphics**: paths entirely inside a box are dropped.
- **Annotations** that overlap a box (comments, links, form widgets) are removed; form fields whose widgets are all
  removed leave the form.

Then the box is drawn (`--fill black` by default; `--label REDACTED` writes a label in it) and the document is
cleaned:

- the same patterns are replaced (by `--replacement`, default `[REDACTED]`) in metadata (Info and XMP), bookmark
  titles, annotation contents and form values;
- annotation appearances that still show a target (a stamp, a sticky note, a button caption) are removed;
- objects the document no longer reaches (the original of a copied form, an image replaced by its painted copy,
  the appearance streams of a removed widget) are dropped, so the old content does not stay in the file;
- `--strip-metadata` removes all document metadata; `--remove-attachments` removes embedded files (otherwise the
  output warns when there are any, since they are not searched).

## 3. Fall back to an image when needed

Some content cannot be edited glyph by glyph: Type3 fonts with rotated or skewed glyphs, vertical writing, JBIG2
images, or a content stream the parser does not understand. Such a page is **rasterised**: rendered at `--dpi`
(default 200) with the boxes burnt in and stored as an image. Its text is no longer selectable. `--raster` rasterises every affected page on
purpose; `--no-fallback` stops with an error instead. The report says which pages were rasterised and why.

## 4. Verify

The output is read again with pdfium, independently of the code that wrote it:

1. no target (text, pattern, preset) is extractable on **any searched page** (all pages, or `--pages`), with the
   same line-break, hyphenation and page-break rules as the search, not only on the pages that had matches;
2. no character remains inside any box;
3. no character outside the boxes disappeared (the text around a redaction is intact).

A redacted page that fails is rasterised and checked again. A target left on a page that was not redacted stops
the run (nothing is written): redact it with `--box`, or rasterise with `--raster`. Finally the raw file (every object, decompressed) is scanned
for the literal `--find` strings. The report says `verified` when everything passed. If a check still fails,
nothing is written: the script exits 1 and names the page and the problem. The raw scan reads text only (strings
and the text content streams show), so a PDF keyword such as `Contents` is never mistaken for a leak.

## Check it yourself

```bash
python3 scripts/pdf_text.py redacted.pdf --search "Jane Doe|jane\.doe@" -i --fail-if-none   # expect exit 1: nothing found
python3 scripts/pdf_render.py redacted.pdf --pages 1-3                                      # look at the boxes
python3 scripts/pdf_compare.py original.pdf redacted.pdf --text-only                        # only the targets changed
```

## Limits

- Verification can only see text: a target inside an image, drawn as outlines, or split in an order the PDF does
  not store as reading order (two columns read across) is not seen by the search either. Look at the renders.
- Text drawn as vector outlines or inside images is not text: find it by looking at renders and redact it with
  `--box`.
- Hidden text (white on white, off the page, under an image) is found by `--find` and `--regex` like any other
  text, since extraction ignores colour; check the dry-run list.
- Digital signatures become invalid, as with any change to a signed file.
- Redaction keeps the rest of the page as it was, including fonts: a subset font still holds the glyph shapes of
  removed letters (not their order or text), as in every PDF redaction tool.
