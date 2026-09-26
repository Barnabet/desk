# pptx_edit operations

```bash
python3 scripts/pptx_edit.py deck.pptx --out deck-v2.pptx --ops edits.json     # or --ops '[...]' or --ops -
```

Ops run in order. The output lists what each op did and the final slide order ("was 4", "new").

Every op's fields are checked before anything runs: an unknown op, an unknown or misspelled field (`"txt"`,
`"with"`, `"colour"`) or a missing required field (`replace` without `"replace"`) stops the edit with exit 2, a
"did you mean" hint and the op's accepted fields. No output is written. Deleting every slide is refused too.

## Addressing

- **Slides.** 1-based numbers from the **original** deck, as `pptx_read.py` shows them. Earlier deletes, moves and
  inserts do not shift them. Where a set of slides is allowed, use ranges like `"2-4,7"` or `"last"`, a list
  `[2, 5]`, or `"all"`. Slides created by `add_slide` or `duplicate_slide` can be given `"id": "risks"` and
  referenced as `"slide": "risks"` afterwards.
- **Shapes.** A name as `pptx_read --shapes` prints it (`"Title 1"`, case-insensitive), a shape id (`7` or
  `"#7"`), or `"title"` / `"body"` for the placeholders.
- **Lengths.** Inches (`1.5`), or `"4cm"`, `"40mm"`, `"72pt"`, `"120px"` (96 dpi), `"10%"` (of the slide width
  for x/w, height for y/h).
- **Colours.** `#RRGGBB`, or theme roles: `text`, `muted`, `bg`, `surface`, `accent1`…`accent6`, `positive`,
  `negative`. Roles follow the deck's theme, so they suit the user's template.

## Text

| op | fields | what it does |
|---|---|---|
| `replace` | `find`, `replace`, `slides`, `regex`, `match_case` (true), `whole_word`, `notes` (also in speaker notes), `max` | Replaces across runs in every text frame, table cell and group. The replacement takes the formatting of the run where the match starts. With `regex`, `\1` or `\g<name>` refer to groups. Returns the count and the slides touched |
| `set_title` | `slide`, `text` | Replaces the title text, keeping its formatting. On a slide without one, it adds a real title placeholder (read, lint and screen readers see it) in the free band above the content, at the layout's title position when there is room; it warns when there is not |
| `set_body` | `slide`, `bullets` or `text`, `shape` (default the body placeholder) | Rewrites the body with bullet levels (same forms as pptx_create); text is fitted by measuring |
| `set_notes` | `slide`, `text`, `append` | Speaker notes |

## Slides

| op | fields | what it does |
|---|---|---|
| `add_slide` | a pptx_create slide spec (`type`, `title`, `bullets`, `chart` …, or nested in `spec`), `after` (slide number, `0` for first) or `position`, `id`, `layout` | Builds the slide with the deck's own layouts and theme colours (a deck made by pptx_create keeps its design). Footer and slide number are copied from a neighbouring slide |
| `delete_slide` | `slides` | Removes slides |
| `duplicate_slide` | `slide`, `after`, `id` | Deep copy placed right after the source by default. Charts (with their data), SmartArt and embedded objects are cloned, and pictures and media are shared |
| `move_slide` | `slide`, `after` (0 = first) or `position` | |
| `reorder` | `order` (original numbers; unlisted slides keep their order at the end) | |
| `hide_slide` / `unhide_slide` | `slides`, `hidden` | Hidden slides stay in the file and are skipped in the show |
| `set_background` | `slides` (default all), `color` or `image` | Per-slide background |

## Shapes

| op | fields | what it does |
|---|---|---|
| `set_shape` | `slide`, `shape`, and any of `text` (\n = new paragraph, keeps the first run's formatting), `x`, `y`, `w`, `h`, `rotation`, `font_size`, `bold`, `italic`, `font`, `color`, `align`, `fill` (`"none"` to clear), `line`, `line_width`, `alt_text`, `name` | Changes one shape |
| `delete_shape` | `slide`, `shape` | Also drops its image or chart relationship |
| `add_shape` | `slide`, `kind` (`text`, `rect`, `card`, `oval`, `line`, `image`, `table`, `chart`), `x`, `y`, `w`, `h`, plus the fields of pptx_create custom elements | Adds a shape; `add_text` and `add_image` are shortcuts |
| `replace_image` | `slide`, `shape`, `image`, `fit` (`cover` crops to keep the frame, `fit` shrinks the frame to the image, `stretch`), `alt_text` | Keeps position and size; SVG files are rasterised |
| `set_table` | `slide`, `shape`, `rows` (list of rows from `start_row`, default 0, the header), `exact` (true: the table ends up with exactly that many rows), `cells` (`[{"row", "col", "text"}]`, from 0) | Keeps cell formatting; added rows copy the last row's style |
| `set_cell` | `slide`, `shape`, `row`, `col`, `text` | One cell |
| `update_chart` | `slide`, `shape`, and any of `categories` (defaults to the current ones), `series` (`[{"name", "values"}]`, or `"points"` for XY charts), `number_format`, `title` (text, or `""` to remove), `legend` (`bottom`, `right`, `top`, `left`, `none`), `colors` (theme roles or `#hex`, one per series, or per slice for pie/doughnut), `labels` (true, false, or `"value"` on pies) | Replaces the chart data and its embedded workbook, keeping the chart's formatting. Added series get an accent colour no other series uses. Going from one series to several removes an empty automatic title (PowerPoint would show "Chart Title"), with a warning, and adds a legend |

## Document

| op | fields |
|---|---|
| `set_properties` | `title`, `author`, `subject`, `keywords`, `comments`, `category`, `last_modified_by` |

## Checking an edit

1. Render and look: `pptx_render.py deck-v2.pptx --sheet` then view_image.
2. Lint: `pptx_lint.py deck-v2.pptx` flags text that no longer fits after a replace, overlaps from moved shapes,
   and contrast problems from new colours.
3. Read back: `pptx_read.py deck-v2.pptx --slides N` to confirm the words.
