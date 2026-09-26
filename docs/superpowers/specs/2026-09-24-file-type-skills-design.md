# Desk — File-Type Skills Design

- **Date:** 2026-09-24
- **Status:** Approved by the user's standing goal ("make the most advanced and performant full end to end handling of any file type skills, one per file type group"). Implemented by Plan 14.
- **Scope:**
  - Eleven first-party catalog skills, one per file-type group. Each is a SKILL.md plus scripts plus references. Together they read, inspect, create, edit, convert, render and verify any common file.
  - One small core capability, `view_image`, so agents can see what the scripts render.
- **Constraints from the user (2026-09-24):**
  1. It must be **skills + scripts**. File handling lives in skills, not in core tools.
  2. Desk **must run on Windows**. Nothing macOS-only (no pyobjc, Quick Look, textutil, sips, afconvert), no POSIX-only code.
  3. **No OCR.** Agents look at rendered images directly, so they understand position, style and context.

---

## 1. Decisions

| Decision | Choice | Why |
|---|---|---|
| Packaging | First-party **builtin catalog entries** in `catalog/skills/<id>/`, installed by the user from the Catalog like the other 20 | Uses the existing review, install, pinning and runtime machinery unchanged |
| Grouping | **One skill per file-type group** (§3), plus a `file-inspector` entry point for unknown files | Each group shares libraries and workflows; the skill descriptions trigger on the file extensions |
| Language | **Python 3.12**, in Desk-managed runtimes with pinned top-level packages and prebuilt wheels only | Every chosen package has wheels for macOS arm64, macOS x86_64 and Windows x64. Verified with `uv pip compile --only-binary :all: --python-platform …` on 2026-09-24 |
| Seeing files | Every visual format has a **render** script: pages, slides, sheets, frames, waveforms → PNGs sized for vision. The agent views them with the new core tool **`view_image`** | The user's direction. Vision beats OCR for layout, and a script alone cannot put an image into the model's context |
| Office rendering | **LibreOffice when installed** (exact; found on macOS, Windows and Linux). Otherwise a **built-in approximate renderer** (python-docx / python-pptx / openpyxl → Typst → PNG) | Works everywhere out of the box; exact when possible, and every render says which one it used |
| Universal converters | **pandoc** (the `pypandoc-binary` wheel ships the binary), **Typst** (the `typst` wheel embeds the compiler) and **ffmpeg** (the `imageio-ffmpeg` wheel ships a static binary) | Cross-platform binaries installed by uv; no system installs |
| Heavy engines | **pypdfium2** (PDF text and rendering), **python-calamine** (fast spreadsheet reads), **DuckDB** (SQL over data files), **PyAV** (media probing and decoding), **faster-whisper** (transcription) | Native-speed engines, chosen for performance |
| Spreadsheet formulas | **A built-in formula engine** (`_formula.py`): parser, dependency graph, about 150 functions, error values | `formulas` pulls in scipy (EUPL). `pycel` is GPL-3. `xlcalculator` has no wheels |
| Licences | Our code is MIT. Dependencies are permissive, except two binaries downloaded by uv from PyPI on the user's machine: pandoc (GPL-2) and the static ffmpeg (GPL). Desk never redistributes them | Same stance as the existing catalog: nothing third-party is shipped inside the app |
| Catalog category | New **`files`** category, the "Files & media" bay, second in order. The 11 skills live there | The Documents & data bay would otherwise hold 15 cards |

---

## 2. Seeing files: `view_image` (the only core change)

**Tool:** `view_image({ paths: string[] (1–8), purpose?: string })`, available to Desk and to threads.
- **Reading:** paths resolve like `read_file` (`readRoots`).
- **Formats:** PNG, JPEG, GIF and WebP. Dimensions are parsed in TypeScript from the file header (PNG IHDR, JPEG SOFn, GIF, WebP VP8/VP8L/VP8X).
- **Limits:** each file must be ≤ 3.75 MB and ≤ 8000 px per side, with ≤ 20 MB per call.
  - A file over a limit fails with a hint: "downscale it first: images skill `img_view.py preview`, or render at a lower DPI".
  - An SVG, HEIC or PDF fails with a hint naming the skill that renders it.
- **Result text:** one line per image (`name · W×H · format · size`). The images themselves go in the result event.

**Storage:**
- Each viewed image is copied to a content-addressed store, `<data>/attachments/<sha256>.<ext>`. History therefore never changes when the workspace file changes later, and identical images are stored once.
- `tool.result` gains an optional `images: [{ sha256, media_type, width, height, bytes, name }]`. The change is additive in `protocol/src/events.ts`, and the stored payload is plain JSON, so no migration is needed.

**Transcript (`agent/transcript.ts`):**
- After each batch of consecutive `tool.result` messages that carry images, the builder emits one **user** message. It starts with the text part `[Images from view_image]`, followed by one `image_url` part per image as a base64 data URL. Chat Completions only allows images in user messages, and this form was verified through the local proxy for claude-opus-5-5 and gpt-6-sol.
- `ChatMessage` user content becomes `string | ContentPart[]`.
- **Budget:** only the **8 most recent images** in the conversation are sent as pixels. Older ones become text placeholders (`[image no longer shown: page-3.png 1240×1754 — view it again if needed]`), which bounds tokens and keeps prompt caching stable.
- **Missing files:** a missing attachment becomes a placeholder, never an error.

**Other code paths:**
- **Compaction and usage:** images render as `[image: name W×H]`. Usage estimation counts `W×H/750` tokens per image.
- **Model capability:** `ModelInfo.vision` (default `true`). When the agent's model has `vision: false`, `view_image` fails with a clear message.

**Clients:**
- **Daemon:** `GET /v1/attachments/:sha256` streams the image. It is auth-protected, and the sha256 is validated as 64 hex characters.
- **Client:** `DeskClient.attachments.url()` and `.get()`.
- **Desktop:** IPC `attachments.get` (zod-validated) returns a data URL. The thread transcript shows thumbnails under the `view_image` result, and a click opens the image larger.
- **CLI:** prints `[image: name W×H]`.
- **Prompts:** one line in the Desk and thread prompts: "To see a document, page, slide, sheet, video frame or image, render it with its file skill and look at it with view_image."

**As built (2026-09-25), beyond the text above:**
- **Pixel window:** besides the 8 images, the images sent as pixels total at most 20 MB. A 39 MB request was refused with 413 through the local proxy, and 29 MB went through. Parallel `view_image` calls in one step share the window. An image that did not fit even when its own step was the newest was never seen, so its placeholder says "more images than the model is shown at once; view fewer or smaller images at a time" instead of "view it again".
- **Plain text without pixels:** an images message with no pixels is a plain string (header, then one line per image). This covers a model with `vision: false`, compaction, and a message whose images all left the window. Endpoints without vision may reject content parts.
- **Whole-file checks:** `view_image` refuses files both claude-opus-5-5 and gpt-6-sol refuse, with a hint to re-render or convert them (images skill `img_convert.py`). These are PNGs that are truncated, lack IEND, fail a chunk CRC, or have image data that inflates short; inflating stops once the header's size is reached. Also refused: arithmetic, lossless or 12-bit JPEGs, and truncated WebPs.
- **Refused images:** when the endpoint still refuses a request because of an image, the loop retries with some images sent as text: first the newest group, then only the older ones, then both. A group is blamed only when a retry proves it holds a refused image. Nothing is recorded until a retry goes through. Then `images.withheld { run_id, images: [{ tool_call_id, sha256, name }], reason }` marks those showings as text from then on, and `view_image` refuses an image withheld on its own. After a 413 or "too large" answer, the loop halves the image bytes instead. A lower budget that went through is kept for that model in memory until restart. Withheld images keep their place and bytes in the window, so withholding never brings an older image back.
- **`attachment:<sha256>` paths:** `view_image` also takes an image another agent of the same project viewed, as Desk's `read_thread` shows it. The digest must be in the attachment store and in one of the project's 5,000 most recent tool results.
- **Usage estimate:** `W×H/750` after scaling the long edge down to 1568 px, as providers do before counting. A raw 8000×8000 image would otherwise count 85k tokens and trigger compaction early.
- **Desktop thumbnails:** they are scaled in the sandboxed renderer (`createImageBitmap` and a canvas). IPC returns the full image's data URL, capped at 8 MB. Main never decodes an image, since viewed images may come from the web.

---

## 3. The skills

### Groups and scripts

Every script has `--help`. Output is concise Markdown or text by default and `--format json` for machine use.

| id | Group, extensions | Scripts (all `scripts/*.py`) |
|---|---|---|
| `word-documents` *(rewrite)* | Word and rich text: .docx .docm .dotx .dotm, plus .odt .rtf .doc via conversion | `docx_info` · `docx_read` (Markdown with lists, tables, footnotes, comments and tracked changes in CriticMarkup; JSON with block indexes) · `docx_create` (Markdown or JSON spec → docx: templates, TOC, headers and footers, page numbers, images, hyperlinks, footnotes, math) · `docx_edit` (formatting-preserving cross-run replace, regex, insert, delete, tables, images, comments, accept or reject tracked changes, headers and footers, page setup, properties, append a document) · `docx_template` (`{{field}}` and repeating rows or paragraphs from JSON) · `docx_compare` (diff report plus a redline .docx with real tracked changes) · `docx_render` · `docx_convert` (↔ pdf, html, md, odt, rtf, txt, epub; .doc → .docx) |
| `pdf-toolkit` *(rewrite)* | PDF | `pdf_info` · `pdf_text` (fast text, layout, words with boxes, tables, search with page and box) · `pdf_render` (pages or a region → PNG, contact sheet) · `pdf_pages` (merge, split, extract, delete, rotate, reorder, insert, crop, n-up, scale) · `pdf_form` · `pdf_create` (Markdown → PDF through pandoc and Typst, with report, letter and memo templates) · `pdf_stamp` (watermarks, page numbers, headers and footers, Bates numbering) · `pdf_redact` (true redaction by text, regex or box, verified afterwards) · `pdf_optimize` · `pdf_extract` (images, attachments) · `pdf_meta` (metadata, outline, encryption) · `pdf_compare` (text diff plus visual diff PNGs) |
| `spreadsheets` | .xlsx .xlsm .xltx .xls .xlsb .ods .csv .tsv | `sheet_info` · `sheet_read` (calamine speed; values, formulas, styles on demand) · `sheet_query` (SQL over sheets and CSVs with DuckDB) · `sheet_create` (JSON spec, CSV or Markdown → styled workbook with formulas, tables, charts, conditional formats, validation, freeze panes, widths) · `sheet_edit` (ops; inserting or deleting rows and columns rewrites formula references) · `sheet_recalc` (built-in engine; writes cached values; reports #REF!, #DIV/0!, #NAME? and cycles by cell) · `sheet_render` (grid PNG like a spreadsheet window: headers, fills, fonts, borders, number formats, merges, charts) · `sheet_convert` |
| `presentations` | .pptx .pptm .potx .ppsx, plus .odp .ppt via conversion | `pptx_info` · `pptx_read` (text by shape with positions, notes, tables, chart data, images) · `pptx_create` (Markdown or JSON outline → designed deck: themes, layouts, native charts, tables, images, notes, or a user template) · `pptx_edit` (replace text keeping formatting; notes; add, delete, duplicate, reorder and hide slides; images; table cells; chart data) · `pptx_render` (per-slide PNGs plus a contact sheet) · `pptx_lint` (overflowing text, off-slide or overlapping shapes, tiny fonts, low contrast, empty placeholders) · `pptx_convert` |
| `images` | .png .jpg .gif .webp .tiff .bmp .ico .icns .heic .avif .svg, camera RAW, .psd (composite), fonts .ttf .otf .woff .woff2 | `img_info` (EXIF, GPS, profile, frames, palette) · `img_view` (vision previews, full-resolution zoom on a region, coordinate grid overlay, folder contact sheet) · `img_convert` (batch, parallel) · `img_edit` (an ops pipeline: resize, crop, rotate, pad, adjust, annotate, watermark, composite, trim, strip metadata) · `img_compose` (grids, GIF, WebP or APNG animations, sprite sheets, icon sets) · `img_compare` (visual diff, similarity, perceptual-hash duplicates) · `img_optimize` (target size or quality, web formats) · `font_tool` (info, coverage, subset, woff2, specimen PNG) |
| `audio-video` | .mp3 .wav .m4a .aac .flac .ogg .opus .mp4 .mov .mkv .webm .avi, plus subtitles .srt .vtt .ass | `media_info` · `media_convert` (presets) · `media_edit` (trim, cut, concat, extract or replace audio, speed, volume, loudness normalise, fades, crop, scale, rotate, text overlay, subtitle tracks) · `media_frames` (frames at times, intervals or scene changes; timestamped contact sheet; GIF preview) · `media_audio` (waveform and spectrogram PNGs, silence, loudness) · `media_transcribe` (faster-whisper → txt, srt, vtt or json with word timestamps) · `subtitles` (convert, shift, merge, clean) |
| `data-files` | .csv .tsv .json .jsonl .parquet .arrow .feather .avro .xml .yaml .toml .ini .sqlite .duckdb .sav .dta .sas7bdat .xpt | `data_info` (format, dialect, encoding, schema, rows) · `data_query` (DuckDB SQL across files) · `data_profile` · `data_convert` · `data_validate` (JSON Schema, CSV rules) · `data_diff` (keyed row diff) · `data_chart` (matplotlib → PNG or SVG) · `data_tree` (explore and query nested JSON, YAML and XML) |
| `archives` | .zip .tar .tgz .tar.gz .bz2 .xz .zst .7z .rar .gz .jar and other zip-based formats | `arc_list` (tree, ratios, encryption; flags zip-slip paths, links and bombs) · `arc_extract` (safe: limits, globs, passwords) · `arc_create` (zip including AES, tar.*, 7z; excludes; reproducible) · `arc_read` (cat or grep inside, including nested archives) · `arc_test` · `arc_convert` · `arc_diff` |
| `markup-ebooks` | .md .html .rst .tex .org .adoc .typ .epub .fb2 .ipynb .txt | `mk_convert` (pandoc any → any, plus PDF through Typst; TOC, numbering, citations with .bib and CSL, templates) · `mk_render` · `html_extract` (main content → Markdown) · `md_check` (links, images, headings, TOC, front matter) · `epub_tool` (info, TOC, extract, build, check) · `nb_tool` (notebooks → md, py or html; strip outputs) |
| `email-calendar` | .eml .msg .emlx .mbox .ics .vcf | `mail_read` (headers, body as Markdown, attachments, `--to-eml` for .msg) · `mail_extract` (attachments, including nested) · `mbox_tool` (index, search, export, stats, threads) · `mail_create` (.eml drafts with HTML and text, attachments, inline images; never sends) · `ics_tool` (read, recurrence expansion, time zones, create, merge, conflicts, free slots) · `vcf_tool` (read, create, merge, dedupe, CSV) |
| `file-inspector` | anything, including unknown files | `file_identify` (magic + extension + content → type, group, skill to use, confidence; batch) · `file_survey` (inventory a tree by group, size and duplicates → a routing table) · `text_tool` (encoding detection and conversion, BOM, line endings, and head, tail, grep, split and count for huge files) · `bin_tool` (hexdump, strings, entropy, embedded-signature carving, binary compare) · `file_hash` (hashes, checksum files, duplicates) |

Every skill also ships **`scripts/selftest.py`**. It generates fixtures, runs every script end to end as an agent would, and asserts on the results. It is the catalog `smoke` command, so it runs in Desk's real sandbox under `pnpm catalog:check`. It needs no network and takes under 120 s.

### Shared modules

The shared modules are copied into each skill that uses them, so each skill stays self-contained. The source of truth is `catalog/shared/`, and `pnpm catalog:sync` copies it into the skills. A test fails when a copy drifts.
- **`_common.py`**
  - CLI plumbing: `run_main` puts `error: …` on stderr with exit 1, and UTF-8 stdout on Windows.
  - Output helpers: JSON and Markdown emitters, output capping with a paging hint, and human-readable sizes.
  - Safe output paths: never an input, and no overwrite without `--force`.
  - Page and range parsing (`1-3,7,10-`) and a cross-platform `find_tool()`.
- **`_render.py`**
  - LibreOffice discovery through `DESK_SOFFICE`, PATH and the known macOS and Windows locations. It runs with a temp profile, `--headless --norestore`, and a timeout.
  - `pdf → PNG` with pypdfium2, parallel for many pages.
  - Vision sizing, with a default long edge of 1568 px.
  - A contact-sheet helper, the Typst compile helper, and bundled-pandoc and bundled-ffmpeg resolution: the wheel's binary first, for a deterministic version.

---

## 4. Conventions (all eleven skills)

**Scripts**
- The contract is §3's: `--help`, Markdown or text by default, `--format json`. Errors go to stderr with exit 1, and usage errors exit 2.
- Scripts never modify an input. Outputs go to `--out` or a positional output. Existing files are refused without `--force`, and parent directories are created.
- Large inputs are streamed. Text output is capped (default 60 000 characters) and names the flag to page with (`--pages`, `--range`, `--offset`).
- Heavy imports are lazy, so `--help` answers in under 300 ms. Batch operations run in parallel through a `concurrent.futures` process pool when it helps.
- **Windows-safe:**
  - `pathlib`, and no `shell=True`.
  - No `os.fork`, `fcntl`, `resource`, `pwd`, `grp` or `signal.SIGKILL`.
  - No hard-coded `/tmp` or `/usr/bin`. Binaries are found with `find_tool`, and `.exe` suffixes are handled.
  - Every entry point is guarded by `if __name__ == "__main__"`.
  - Temp files go through `tempfile`.
- A render script prints each PNG path and ends with: "Look at them with view_image."

**SKILL.md**
- **Frontmatter:** `name`, and a `description` of at most 1024 characters that names the extensions and verbs, so the skill triggers on them.
- **Body:** under about 250 lines, organised around the loop: **Look** (info, read, render + view_image) → **Act** (create, edit, convert) → **Check** (read back, render + view_image, lint). Then a compact script reference and the limits. Depth goes in `references/`.
- **Language:** clear, direct prose, following the repo's writing style.

**Performance targets** (on an M-series Mac; checked in selftests where cheap)
- PDF: text of 200 pages < 3 s; 20 pages rendered < 5 s.
- Spreadsheets: a 100k-row workbook read < 5 s; recalculating 10k formulas < 5 s.
- Data: a 1 M-row CSV query < 2 s.
- Images: 50 images converted < 10 s.
- Media: a 60-minute video contact sheet < 20 s.

---

## 4b. Big files (added 2026-09-24 at the user's request)

Big XLSX, PPTX, CSV, PDF, mbox and media files are slow to parse, and hard for a model to take in whole. Every skill follows this contract.

**1. Parse once: `catalog/shared/_cache.py`**

Every `skill_run` is a new process. Expensive work is therefore cached and keyed by:
- the file's **content fingerprint**: the full SHA-256 up to 8 MB; above that, size + mtime + head, middle and tail samples, which takes milliseconds even for GB files
- the kind of work, its parameters, and a code version

As a consequence:
- Edits invalidate an entry. Copies of a file share one when the file is small (hashed in full) or when the copy keeps the modification time: for files over 8 MB the sampled fingerprint includes the mtime, so a same-size edit is never missed.
- Entries are built in a temp folder and renamed into place atomically, so racing processes each end up with one complete entry.
- The cache lives in the temp cache (`XDG_CACHE_HOME/desk-files`), which is writable in the sandbox.
- It is LRU-capped by `DESK_FILE_CACHE_MB` (default 1024).
- It never stores when free disk would drop below `DESK_FILE_CACHE_MIN_FREE_MB` (default 1536). Results are still computed, then released.
- `DESK_NO_CACHE=1` and a `--no-cache` flag bypass it.

What to cache:

| Skill | Cached |
|---|---|
| spreadsheets | Per-sheet typed **Parquet** copies (queried by DuckDB), the workbook map (sheets, used ranges, detected tables, header rows, types), recalculation results, renders |
| data-files | Parquet copies of large CSV, JSONL, JSON, XML, Avro, SPSS, Stata and SAS inputs; profiles; outlines of huge JSON |
| word-documents, presentations | The parsed model (block and shape indexes), the LibreOffice PDF (so page renders become instant), renders |
| pdf-toolkit | Per-page text, words and tables; renders |
| audio-video | Probes, frames and contact sheets, waveforms, **transcripts** |
| email-calendar | The **mbox index** (byte offsets + headers per message) for random access and fast search |
| archives | Listings of large or solid archives |
| images, markup-ebooks, file-inspector | Previews and contact sheets, Typst renders, file hashes for survey reruns |

**2. A map first, then drill down.** For any file above the "big" threshold (a few MB, or more than 5 000 rows, 50 slides or 100 pages), the default read is a **map**, never a dump:
- **Tables:** name, dimensions, header, column types, null share, and head and tail sample rows.
- **Documents:** an outline with the size of each section.
- **Decks:** per slide, the title plus word and shape counts.
- **Media:** duration, streams, chapters.

Every item carries a **stable address**: `Sheet1!A1:F200`, `table Sales`, `slide 12 / shape "Title 1"`, `block 143`, `page 37`, `rows 10001-10500`, a member path, `00:12:30`.

**3. Budgets and continuation.**
- Every text output takes `--max-chars` (default 60 000), says exactly what it omitted, and ends with the command that reads the next part.
- Long cells are truncated with a marker.
- Medium extracts default to **CSV** (far fewer tokens than Markdown tables); small ones stay Markdown.

**4. Search with addresses.**
- `--grep`/`--find` over cells, slides, pages, messages or members returns addresses with context, without reading the whole file.
- Tables beyond what can be sampled are answered with **SQL** (DuckDB over the cached Parquet).

**5. Stream, never load needlessly.**
- calamine for values, openpyxl `read_only` for formulas and styles, `lxml.iterparse` for XML parts.
- `ijson` for multi-GB JSON (a new dependency where it's needed).
- A streaming mbox index, pdfium page by page, ffmpeg streams, archive members streamed.

**6. Surgical edits.**
- Cell-level edits to a large `.xlsx` (above about 20 MB) patch only the affected sheet XML parts in the zip, without a full openpyxl load. Styles referenced by index are reused, and new styles are appended to `styles.xml`.
- Structural edits (inserting rows, moving sheets) still take the full path, which the agent is told costs time.

**7. Benchmarks.**
- Each skill's `references/performance.md` records measured timings, on this Mac, for its big fixtures, cold and cached: a 1 M-row CSV (~100 MB), a 200 k × 30 workbook, a 300-slide deck, a 1 000-page PDF and DOCX, a 1 GB mbox, a 1-hour video.
- Selftests check scaled-down versions, plus cache hits: the second call must be at least 5× faster on the cached path.

## 4c. Safety and resource limits (added 2026-09-25)

Skills run on users' laptops, often 8 GB machines, next to other agents. Building them froze the 8 GB build Mac twice: first a Python process at 11 GB, then four `bsdtar` processes holding 17.8 GB. So every skill bounds what a damaged, hostile or huge input can cost.

- **External tools.** `_common.run_tool` stops any command that runs past its timeout, or whose memory footprint passes `DESK_TOOL_MAX_MB` (default 2048; `max_mb=` per call). Code that streams from `subprocess.Popen` wraps the process in `_common.MemoryWatch`. The footprint comes from macOS phys_footprint (which counts compressed memory), Linux `/proc`, or Windows psapi. It works inside Desk's sandbox.
- **Zip containers.** Every script that opens a zip-based input (docx, xlsx, pptx, odt/ods/odp, epub, jar, zip) calls `_common.check_zip` first. It refuses extreme declared sizes, compression ratios and member counts; the `DESK_ZIP_*` variables or per-call arguments raise the limits for trusted files. `refuse_dtd=True`, for Office packages, also refuses XML parts that declare a DOCTYPE or entities (billion laughs, XXE). Verdicts are cached by file content.
- **Compressed streams and archives.** Before a 7z, xz, lzma, rar or zstd stream reaches a decoder, its declared dictionary or window size is read from the headers and refused above `DESK_ARC_MAX_DICT_MB` (default 256), because a hostile header can make a decoder allocate gigabytes up front. Python decoders get `memlimit` and `max_window_size`. Members are streamed, and extracted-bytes, entry-count and ratio limits are enforced while extracting as well as before, since declared sizes can lie. External decompressors run one at a time.
- **Parsers.** XML is parsed without entity expansion; `check_zip(refuse_dtd=True)` scans each Office part's prolog, whatever its encoding, for a DOCTYPE. YAML alias expansion and AsciiDoc include and attribute expansion are bounded. A sheet whose declared range is enormous but sparse is read cell by cell, never as a dense grid.
- **A document's references (added after the final review).** Converting or reading a stranger's document never pulls in the user's other files. Includes (AsciiDoc, reStructuredText, Org, LaTeX, Typst) and pictures resolve only inside the document's folder and the `--resource-path` folders the caller named. `_pandoc_safe.py` (shared) inlines the includes Desk understands, pandoc reads with `--sandbox`, and a Lua filter drops anything else with a warning. pandoc never fetches remote pictures itself: the skills download them first with time and size limits, or skip them offline. Cache entries that depend on included files record and check them.
- **Regular expressions.** Every regex that reads file or page content is linear: no nested or overlapping repetition that can backtrack exponentially on a crafted file. The selftests time adversarial inputs (for example `"<!---->" * 40 + "x"`).
- **Engines.** DuckDB runs in memory with a cap (1-2 GB, spilling to temp files) and never downloads extensions. Process pools are sized by `workers_for`, capped by `DESK_MAX_WORKERS`. LibreOffice runs one conversion at a time per process, and private profiles are deleted after each one. On Windows, a timeout or memory stop kills the whole process tree (`taskkill /T`); the memory cap there measures the launched process only.
- **Output.** `emit` never cuts JSON mid-way: a list is shortened to the items that fit, with a note on stderr. `cap` cuts text at a line end and says how much was left out. `_paging.py` gives listings `--max-chars`, `--offset` and `--limit`, and ends with the exact command for the next part (Windows quoting on Windows).
- **Files.** `atomic_write` gives outputs normal permissions (0666 minus the umask), and inputs are never modified.

---

## 5. Catalog changes

- **Categories:**
  - `CatalogCategory` gains `files`: protocol, the desktop `BAYS` and the CLI `CATEGORY_TITLES`.
  - The new bay reads "Files & media: Any file type: read, create, edit, convert — and see it."
  - `word-documents` and `pdf-toolkit` move to `files`.
- **Entries:** eleven builtin entries, each with:
  - pinned top-level packages
  - `smoke`: `python3 scripts/selftest.py`
  - caveats, for example:
    - "Exact rendering of Office files uses LibreOffice when installed; otherwise Desk draws a close approximation."
    - "Transcription downloads a Whisper model (~150 MB for `base`) on first use."
- **Updates:** existing installs of `word-documents` and `pdf-toolkit` show **Update**, because their digest changes.
- **Runtimes (added 2026-09-25):** `uv pip install` gets `--compile-bytecode`, since skills run with `PYTHONDONTWRITEBYTECODE=1` and would otherwise recompile their imports on every run (about 70 ms). Python environments also set `PYTHONUTF8=1`, so text I/O is UTF-8 on Windows too.
- **Other docs:** the catalog spec's §2 table is updated (30 entries with web research). `docs/api.md` documents the attachments route, and CLAUDE.md mentions `catalog/shared` and `catalog:sync`.

---

## 6. Verification

1. **Per skill:** `selftest.py` passes in a dev environment built with the exact pins, then passes as the smoke command under `pnpm catalog:check <id>` in the real sandbox.
2. **Real-world robustness:** reviewers download public sample files (in scratch space, never committed) and run every script on them. Every crash or wrong output is fixed and turned into a selftest case where practical.
3. **Adversarial review per skill**, through three lenses:
   - correctness and robustness
   - completeness and SKILL.md quality for agents
   - Windows portability, security (zip-slip, traversal, injection, overwrites) and performance
4. **Vitest:**
   - `view_image`: the tool, header parsing, limits, the attachment store, transcript injection and windowing, compaction rendering, route, client, and a desktop component
   - catalog digests
   - `catalog/shared` copies identical
   - a portability lint over first-party scripts (forbidden POSIX-only patterns)
   - every script named in each SKILL.md exists
5. **Live** (`pnpm test:live`): an agent calls `view_image` on a rendered PNG and answers a question only the pixels can answer.
6. `pnpm test` and `pnpm typecheck` green before each commit.

**Windows** can't be executed on this machine. It is covered by wheel resolution for `x86_64-pc-windows-msvc`, the portability lint, and code review.

## 7. Out of scope

- **OCR.** By design, per the user.
- **Sending email.** `mail_create` writes drafts only.
- **Executing notebooks.**
- **Formats without cross-platform readers:**
  - `.pst`, Apple `.pages`/`.numbers`/`.key`, `.dwg`
  - 3D and GIS formats: `file-inspector` identifies them and says so
- **Porting deskd itself to Windows.** The daemon still relies on `sandbox-exec`, a LaunchAgent and the Keychain. It is a separate project; the skills are ready for it.
