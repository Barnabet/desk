---
name: presentations
description: Create, read, edit, render, check and convert presentations (.pptx .pptm .potx .ppsx, plus .odp .ppt and .key through LibreOffice). Use for any slide deck task. Build a designed 16:9 deck from a Markdown outline or JSON spec, with themes or the user's own template. Read slides, notes, tables and chart data. Replace text while keeping its formatting, and add, delete, duplicate, reorder or hide slides. Swap images, update tables and charts. Render slides to PNG and look at them. Lint for overflowing text, overlaps, tiny fonts and low contrast. Convert to PDF, PNG or Markdown.
license: MIT
---

# Presentations

Scripts in `scripts/` use python-pptx (MIT), Typst and pypdfium2. Desk sets up their Python environment, so run
them with `python3`. Every script has `--help`, prints Markdown by default and JSON with `--format json`, and never
changes its input: outputs go to a new path, and an existing file is only replaced with `--force`.

Work in a loop: **look** at the deck, **act** on it, then **check** the result by rendering it and looking.

## Look

```bash
python3 scripts/pptx_info.py deck.pptx                    # size, slides, layouts, theme colours and fonts, media
python3 scripts/pptx_read.py deck.pptx                    # outline: titles, bullets, tables, chart data, notes
python3 scripts/pptx_read.py deck.pptx --slides 4 --shapes  # every shape: name, id, kind, position, size (inches)
python3 scripts/pptx_render.py deck.pptx --out-dir renders --sheet   # PNG per slide + contact sheets
```

Then look at the PNGs with view_image: text can hide layout problems that a picture shows at once. Use the contact
sheets for the whole deck (20 slides per sheet, `--per-sheet` to change) and the single slides for detail. Use
`pptx_read --format json` for the shape names and ids that edits need. The outline leaves out footers and slide
numbers, and reads two columns column by column.

Rendering uses LibreOffice when it is installed, which is exact. Otherwise it uses a built-in renderer: python-pptx
drawn with Typst, a close approximation that handles theme inheritance, text, shapes, pictures, tables, charts and
SmartArt. The output says which engine ran. Pass `--engine builtin` or `--engine libreoffice` to choose. The
built-in renderer draws EMF/WMF pictures as labelled grey boxes and says so. `references/rendering.md` has the
details (engines, fonts, what is drawn, how text is measured).

### Big decks

Above 50 slides, `pptx_read.py` prints a **map** first: one row per slide with its title, word count, shapes,
tables, charts, pictures and notes. Every place has an address like `slide 12 / shape "Title 1"`. Work in a loop:

```bash
python3 scripts/pptx_read.py big.pptx                        # 1. the map (or --map on any deck)
python3 scripts/pptx_read.py big.pptx --find "churn"         # 2. where a word is: addresses with context
python3 scripts/pptx_read.py big.pptx --grep "Q[1-4] 202[56]" --max-hits 50   # the same with a regex
python3 scripts/pptx_read.py big.pptx --slides 40-45         # 3. full detail for those slides only
python3 scripts/pptx_read.py big.pptx --format csv --slides 12   # tables and chart data as CSV
```

- **Budgets.** Output stops at `--max-chars` (60,000 by default) at a slide boundary, and ends with the exact
  command for the next part. `--full` reads every slide in parts; `--max-chars 0` removes the cap.
- **Cache.** Parsed slides, renders, LibreOffice PDFs, lint and info results are cached per file content, so a
  second call on the same deck is fast. `--no-cache` skips it. Timings: `references/performance.md`.

## Act

### Create a deck

Write an outline, then build:

```bash
python3 scripts/pptx_create.py out/review.pptx --input outline.md --theme clean --preview
python3 scripts/pptx_create.py out/pitch.pptx --spec deck.json
python3 scripts/pptx_create.py out/q3.pptx --input outline.md --template brand.potx   # the user's branding
```

A Markdown outline:

```markdown
---
title: Project Aurora
footer: Aurora · Q3 review
theme: midnight
---
# Project Aurora
Quarterly review
Louis Giraud · September 2026

## Highlights
- Shipped the **new onboarding** flow
  - Activation up from 31% to 44%
- Launched in three markets

::: notes
Lead with activation.
:::

<!-- type: kpi -->
## Numbers that matter
- 44% | Activation rate | +13 pts
- €2.4M | Annual recurring revenue | +38% YoY

<!-- chart: column -->
## Revenue by quarter
| Quarter | 2025 | 2026 |
|---|---|---|
| Q1 | 1.2 | 1.8 |
| Q2 | 1.4 | 2.0 |
```

- `# ` starts the title slide; later `# ` headings are section slides. `## ` starts a content slide, and `---`
  starts a new slide.
- Slide type is inferred from the content: images, pipe tables, `> quotes` with a `— Name` line, and code blocks each
  get their own layout.
- `:::: columns` / `::: column` gives two columns, and titles like "Agenda" or "Questions?" pick those designs.
  Text after the columns block on the same slide goes in a note under the columns, with a warning. Start a new
  slide with `---` or `## ` if it belongs elsewhere.
- `<!-- type: timeline -->`, `process` and `kpi` take `label | title | text` bullets.
- `<!-- chart: bar -->` turns the table after it into a native chart.

JSON gives full control over every slide type. The types are title, section, bullets, two-column, comparison,
image, image-text, quote, kpi, table, chart, timeline, process, agenda, closing, code and custom (free
positioning). Each slide can also take `notes`, `hidden` and `background`. See `references/spec.md`.

- **Bullets** are strings, `{"text": "…", "level": 1}`, `{"text": "…", "children": [...]}`, or a nested list
  (`["Point", ["sub-point", "sub-point"], "Next point"]`).
- **Unknown fields are errors.** A misspelled field (`"bulets"`, `"txt"`) stops the build with exit 2 and a
  "did you mean" hint, in specs and in edit ops alike. Nothing is silently dropped.
- **Warnings** report content that was moved or coerced (a KPI line without `value | label`, a non-numeric
  chart cell, a negative pie value, an unknown directive). Read them.

Themes: `clean` (default), `midnight` (dark), `paper` (editorial serif), `vivid` (bold section slides), `slate`
(corporate). Override them with `--theme '{"base": "clean", "accent": "#0A7F6F"}'`.

What the builder does for you:
- It lays out every slide on a grid, and sparse slides get larger text.
- It measures all text. Anything too long shrinks within limits, including a single word or number wider than
  its box (PowerPoint would break it mid-word). Bullet lists that still do not fit continue on "(cont.)" or
  "(2/3)" slides of at most 80 words, and long tables repeat their header on the next slide.
- Charts are native, editable PowerPoint charts in theme colours (accent1, accent2 …), so re-theming recolours
  them.

With `--template`, the template's masters, layouts and placeholders are used. Name a layout per slide with
`"layout": "Two Content"`; `pptx_info.py template.potx` lists the layouts.

### Edit a deck

Put operations in a JSON list and write to a new file:

```bash
cat > edits.json <<'JSON'
[
  {"op": "replace", "find": "FY2025", "replace": "FY2026"},
  {"op": "set_title", "slide": 3, "text": "What we shipped"},
  {"op": "add_slide", "after": 3, "type": "bullets", "title": "Risks", "bullets": ["Hiring", "Pricing"]},
  {"op": "update_chart", "slide": 6, "shape": "Chart 4", "categories": ["Q1", "Q2"], "series": [{"name": "2026", "values": [1.8, 2.0]}]},
  {"op": "replace_image", "slide": 9, "shape": "Picture 3", "image": "new.png", "fit": "cover"},
  {"op": "set_notes", "slide": 2, "text": "Pause for questions."},
  {"op": "delete_slide", "slides": "11-12"}
]
JSON
python3 scripts/pptx_edit.py deck.pptx --out deck-v2.pptx --ops edits.json
```

- **Numbering.** Slide numbers refer to the original deck, whatever ops come first. Give added slides an `"id"` to
  refer to them later.
- **Shapes.** Refer to a shape by its name, its id, or `"title"`/`"body"`.
- **replace.** It works across runs, keeps the formatting of the run where each match starts, and reports its
  count. Check that count.
- **set_title** on a slide without a title adds a real title placeholder in the free space above the content
  (read, lint and screen readers then see it). It warns when there is no room.
- **update_chart** takes `categories`, `series`, `title` (`""` removes it), `legend` (`bottom`, `right`, `top`,
  `left`, `none`), `colors` (theme roles or `#hex`, per series or per pie slice), `labels` and `number_format`.
  Added series get an accent colour of their own. Going from one series to several removes an empty automatic
  title (PowerPoint would show "Chart Title") and adds a legend.
- **Other ops:** set_body, duplicate_slide (charts and SmartArt are copied), move_slide, reorder, hide_slide,
  set_table, set_shape (text, position, size, font, colour, fill, alt text), delete_shape, add_shape,
  set_background, set_properties. See `references/edit-ops.md`.

### Convert

```bash
python3 scripts/pptx_convert.py deck.pptx deck.pdf          # LibreOffice if installed, else built-in (vector)
python3 scripts/pptx_convert.py deck.pptx deck.md           # outline with notes; .txt and .json too
python3 scripts/pptx_convert.py deck.pptx deck.spec.json    # a pptx_create spec (pictures go in deck-media/)
python3 scripts/pptx_convert.py legacy.ppt legacy.pptx      # .ppt/.odp/.key need LibreOffice
python3 scripts/pptx_convert.py brand.potx starter.pptx     # template ↔ deck ↔ slide show, built in
```

The `.md`, `.txt` and `.json` exports are for reading. To rebuild a deck, use the spec (next section). Given a
`.md` export, `pptx_create`/`pptx_convert` stop with the command to use instead.

### Rebuild or restyle a deck

```bash
python3 scripts/pptx_read.py deck.pptx --format spec --out deck.spec.json    # slides, notes, tables, charts, pictures
python3 scripts/pptx_create.py new.pptx --spec deck.spec.json --theme midnight
```

A deck made by `pptx_create` comes back slide for slide: same titles, bullets, tables, notes, chart data and
pictures. Slides from other tools are mapped to the nearest slide type, or kept as positioned shapes (`custom`);
4:3 decks are stretched to 16:9. The notes on stderr say what could not be carried over (animations, SmartArt
layout, video). Edit the spec, then build again.

## Check

After creating or editing, always:

```bash
python3 scripts/pptx_render.py out/review.pptx --out-dir renders --sheet --force
python3 scripts/pptx_lint.py out/review.pptx
```

1. Look at the renders with view_image.
2. Read the lint report. It flags text that overflows its box (measured), shapes off the slide or overlapping,
   body text under 12 pt, too many font families, contrast below WCAG AA, empty placeholders, missing titles, too
   many words, upscaled pictures, missing alt text, and leftover "Lorem ipsum" or `{{placeholders}}`. Every issue
   names the slide and shape and suggests a fix.
3. Fix the issues with `pptx_edit.py`, then render and lint again.

`pptx_read.py` on the result confirms the text. Tell the user where the output is, what changed, and anything you
could not do.

`pptx_lint.py --fail-on warning` exits 1 when warnings remain, and `--slides` narrows the check to what you
changed. The thresholds are options (`--min-size`, `--max-words`, `--projector-width`). Lint also flags a chart
showing "Chart Title" and series that share a colour.

## Common tasks

- **Fill the user's template.**
  1. `pptx_info.py brand.potx` lists its layouts; `pptx_read.py brand.potx --layouts --format json` gives their
     placeholders and positions.
  2. Build with `--template brand.potx`, naming a `layout` on any slide whose automatic choice is wrong.
  3. The template's own slides are dropped unless you pass `--keep-template-slides`.
- **Update the numbers in a deck.** `pptx_read.py deck.pptx --format json` gives each chart's categories and
  series and each table's rows, with shape names. Change them with `update_chart`, `set_table` or `set_cell`, and
  use `replace` for figures in text ("Q3 2025" to "Q3 2026"). Check the replace counts.
- **Rewrite or translate text.** Read the slides as JSON, then use `set_shape` with `text` for each shape (it keeps
  the first run's formatting), or `set_body` for bullet placeholders. Translated text is often longer, so lint
  for overflow afterwards.
- **Review a deck someone sent.**
  1. See all of it with `pptx_render.py --sheet-only` (fast, small renders; one sheet per 20 slides), then render
     and look at single slides where something looks off. For a big deck, start from the map (see Big decks).
  2. Run `pptx_lint.py`, and read the words and notes with `pptx_read.py`.
  3. Report by slide number and title, most serious first.
- **Speaker notes.** `pptx_convert.py deck.pptx notes.md` writes the outline with every slide's notes; `set_notes`
  writes them.
- **Shorten a deck.** Hide slides (`hide_slide`) when the user may want them back, and use `reorder` for a new
  order. Both keep everything else intact.
- **Share it.** Export a PDF with `pptx_convert.py`, or PNGs of chosen slides with `pptx_render.py --slides`.

## Rules

- **Inputs:** never overwrite the user's file. Write a new one and give its path.
- **Positions:** inches from the top-left corner. Edits also accept `"4cm"`, `"72pt"` and `"10%"`.
- **Look before you report:** check it visually before calling a deck done. Lint cannot judge whether a slide
  looks good.
- **Macros:** `.pptm` files keep their macros when saved as `.pptm`. Saving as `.pptx` removes them, with a
  warning.
- **Formats:** `.odp`, `.ppt` and `.key` are read by converting through LibreOffice. When it is missing, say so and
  ask for a `.pptx`.

## Limits

- **Built-in renderer:** close to PowerPoint, not exact. It lacks 3D effects, shadows, WordArt warps, animations
  and EMF/WMF pictures, and uses substitute fonts when the deck's fonts are missing. For pixel-exact output, the
  user can install LibreOffice.
- **Charts:** they are read from their cached data. The newer chart types (chartex: waterfall, treemap, sunburst)
  are listed but not drawn.
- **SmartArt:** read and drawn from its cached drawing. To change it, edit its text with `replace`.
- **Not supported:** equations are read as text, and slide transitions and animations are reported but not
  created.

For anything else (animations, custom XML, merging decks), see `references/python-pptx.md` and write a short
script with the same `python3`. The neighbouring skills are `pdf-toolkit` (PDF work after exporting), `images`
(preparing pictures), `spreadsheets` (chart data from .xlsx) and `word-documents`. They may not be installed.
