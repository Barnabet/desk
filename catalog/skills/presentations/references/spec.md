# Deck spec reference (pptx_create)

`pptx_create.py` takes a Markdown outline (`--input deck.md`) or a JSON spec (`--spec deck.json`, or `--input deck.json`).
Markdown is turned into the same JSON internally, so everything below applies to both.

## Deck

```json
{
  "title": "Project Aurora",          // document properties (defaults to the first slide title)
  "author": "Desk",
  "subject": "Q3 review",
  "keywords": ["review", "q3"],
  "theme": "clean",                    // or {"base": "paper", "accent": "#0A7F6F", ...}, ignored with --template
  "footer": "Aurora · Q3 review",      // on content slides (not title, section, closing)
  "slide_numbers": true,               // default true on content slides
  "slides": [ ... ]
}
```

A bare list `[ {slide}, ... ]` works too. Image paths are relative to the spec or outline file.

Fields are checked: an unknown or misspelled field in the deck, a slide, a column, a chart, a series, a KPI,
timeline or process item, a bullet object or a custom element stops the build (exit 2) with the accepted fields
and a "did you mean" hint. `pptx_read.py deck.pptx --format spec` writes a spec of an existing deck in this
format.

### Themes

| name | look | heading / body font |
|---|---|---|
| `clean` | white, near-black text, blue accent, accent bar under titles | Arial / Arial |
| `midnight` | dark navy, light text, sky and violet accents | Trebuchet MS / Arial |
| `paper` | warm off-white, serif headings, terracotta and teal, hairline rules | Georgia / Arial |
| `vivid` | white, left accent band, violet section slides | Trebuchet MS / Arial |
| `slate` | cool grey, teal accent, dark section slides | Arial / Arial |

Theme overrides (JSON object): `base`, `accent` (accent1), `accents` (list of up to 6), `bg`, `surface`, `text`,
`muted`, `positive`, `negative`, `dark` (true for a light-on-dark palette), `fonts` (`heading`, `body`, `mono`),
`heading_bold`, `decor` (`bar` | `rule` | `band`), `section_bg` (`surface` | `accent` | `text`), and `sizes`
(`cover`, `section`, `title`, `subtitle`, `body`, `body2`, `body3`, `small`, `caption`, `footer`, `kpi`, `quote`,
`min_body`, `min_title`, in points).

Colours are written into the theme part, and shapes refer to them by role, so a user can re-theme the deck in
PowerPoint. Pick fonts every machine has (Arial, Georgia, Trebuchet MS, Verdana, Times New Roman, Courier New);
others are substituted on computers that lack them.

## Common slide fields

| field | meaning |
|---|---|
| `type` | one of the types below (default `bullets`; the first slide defaults to `title`) |
| `title` | slide title (inline Markdown allowed) |
| `notes` | speaker notes (string or list of lines) |
| `hidden` | true hides the slide in the show |
| `background` | a colour (`"#0F172A"` or a role like `"accent1"`) or `{"image": "bg.jpg"}` |
| `layout` | a layout name to use (template mode, or to override the choice). An unknown name falls back to the automatic choice, with a warning. On a layout without a title (Blank), the title is still a real title placeholder |

Text fields accept inline Markdown: `**bold**`, `*italic*`, `` `code` ``, `~~strike~~`, `[link](https://…)`.
Colour fields accept `#RRGGBB` or a role: `text`, `muted`, `bg`, `surface`, `accent1`…`accent6`, `positive`,
`negative`.

Bullets are strings, `{"text": "…", "children": [...]}` objects, `{"text", "level"}` items, or nested lists
(`["Point", ["sub-point a", "sub-point b"], "Next"]`: a list holds the sub-points of the item before it). Items
can also take `"bullet": false` (a plain paragraph), `"numbered": true`, `"bold"`, `"color"` and `"size"`.

## Slide types

| type | fields | notes |
|---|---|---|
| `title` | `title`, `subtitle`, `meta` (or `author` + `date`), `image` | an image fills the right 42% |
| `section` | `title`, `subtitle`, `number` (default "01", "02", …; `false` for none) | coloured background per theme |
| `bullets` | `title`, `bullets`, `columns` (1 or 2) | ≥10 short items flow into two columns; long lists continue on "(cont.)" slides, or "(1/3)", "(2/3)"… for three or more, each under 80 words |
| `two-column` | `title`, `left`, `right`, `split` (e.g. `[2, 1]`), `note` | each side: `heading` plus `bullets`, `text`, `image`, `table`, `chart` or `code`; headings are a step larger than the bodies, and both bodies share one size; `note` is a line under the columns |
| `comparison` | same as two-column | headings are drawn as labelled columns |
| `image` | `image` (or `images`, up to 4), `title`, `caption`, `fit` (`fit`/`cover`), `full_bleed` | without a title the picture fills the slide |
| `image-text` | `image`, `title`, `bullets` or `text`, `image_side` (`left`/`right`), `image_width` (0.45) | full-height picture beside the text |
| `quote` | `quote`, `attribution` | large quote mark, text fitted by measurement |
| `kpi` | `title`, `items`: `[{"value", "label", "delta", "color", "good"}]`, `colorful` | up to 4 per row, 2 rows; deltas starting with + or − turn green or red (`good: false` flips) |
| `table` | `title`, `columns`, `rows`, `align`, `widths`, `font_size`, `total` (last row bold), `highlight` (row indexes), `note` | numbers right-aligned; column widths and font size are fitted; long tables continue with the header repeated |
| `chart` | `title`, `chart` (below), `note` | a note puts a takeaway panel beside the chart |
| `timeline` | `title`, `items`: `[{"label", "title", "text", "color"}]` | 3–7 items read best |
| `process` | `title`, `items`: `[{"label", "title", "text", "color"}]` | chevrons with numbered steps |
| `agenda` | `title` (default "Agenda"), `items` | numbered; two columns above 5 items |
| `closing` | `title` (default "Thank you"), `subtitle`, `contact` | like a section slide |
| `code` | `title`, `code`, `language`, `note` | monospace, sized to fit the longest line |
| `custom` | `title`, `elements` (below) | free positioning |

### Charts

```json
{"type": "column", "categories": ["Q1", "Q2", "Q3"],
 "series": [{"name": "2025", "values": [1.2, 1.4, 1.5]}, {"name": "2026", "values": [1.8, 2.0, 2.4]}],
 "number_format": "0.0", "labels": true, "legend": "bottom", "title": "Revenue (€M)", "colors": ["accent1", "#999999"]}
```

Types: `column`, `column-stacked`, `column-stacked-100`, `bar`, `bar-stacked`, `bar-stacked-100`, `line`,
`line-plain`, `area`, `area-stacked`, `pie`, `doughnut`, `scatter`, `scatter-lines`, `radar`. Scatter series take
`"points": [[x, y], ...]`. Pie and doughnut show percentages (`"labels": "value"` for values). Horizontal bars
list the first category on top. `legend`: `bottom`, `right`, `top`, `left` or `none`. `colors` takes theme roles
or `#hex` per series (per slice for pie and doughnut); by default series use accent1, accent2 … from the theme,
so re-theming recolours them. A negative value in a pie is reported (pies cannot show it).

### Custom elements

```json
{"type": "custom", "title": "Architecture", "elements": [
  {"kind": "card", "x": 0.8, "y": 1.8, "w": 3.6, "h": 1.4, "text": "**API**\nPython, 3 services", "fill": "surface"},
  {"kind": "line", "x": 4.4, "y": 2.5, "w": 1.0, "h": 0, "color": "muted", "width": 2},
  {"kind": "text", "x": 0.8, "y": 5.8, "w": 11.7, "h": 0.6, "text": "All traffic stays in the EU", "size": 16, "color": "muted"},
  {"kind": "image", "x": 8.5, "y": 1.8, "w": 4, "h": 3, "image": "diagram.png", "fit": "fit"}
]}
```

Kinds: `text` (size, color, bold, align, anchor, bullets, font), `rect` / `card` (rounded) / `oval` (fill, line,
radius, text, size, color), `line` (color, width), `image` (fit), `table` (columns, rows; its text shrinks to fit
the box instead of continuing on another slide), `chart` (a chart object). Positions and sizes are inches, or strings with units: `"4cm"`, `"72pt"`, `"50%"` (of the slide width or
height). The slide is 13.333 × 7.5 in. Content usually sits between x 0.75 and 12.58, and between y 1.62 and 6.7.

## Markdown outline

```markdown
---
title: Deck title
theme: paper
footer: Company · Confidential
---
# Deck title                 ← title slide: following lines are subtitle, then author/date
Subtitle
Author · Date

# Section name               ← later '# ' headings: section slides
One-line description

## Slide title               ← content slide
- bullet
  - sub-bullet (2 or 4 spaces, or a tab)
1. numbered item
Plain paragraph lines become text without bullets.

---                          ← new slide without a title (e.g. a picture or quote)
![Caption](photo.jpg)        ← alone: image slide; with bullets: image-text slide

> A quote.                   ← alone: quote slide
> — Name, Role

::: notes                    ← speaker notes (also a line "Notes:" to the end of the slide)
Say this.
:::

:::: columns                 ← two columns; '### Heading' labels a column
::: column
### Before
- …
:::
::: column
### After
- …
:::
::::
```

- **Directives.** Put `<!-- key: value -->` inside the slide or just before its heading.
  - `type:` takes any slide type.
  - `chart: column` needs a pipe table next: its first column is the categories, the other columns are series, and
    `%` values become a percent axis.
  - The other keys are `layout:`, `background:`, `hidden: true`, `image_side: right` and `fit: cover`.
- **Special bullets.** On `kpi` slides, bullets are `value | label | delta`. On `timeline` and `process` slides,
  they are `label | title | text`. A KPI line without `|` becomes a value only when it starts with a number;
  otherwise it is a label, with a warning.
- **Columns.** Content after the `::::` that closes a columns block, on the same slide, is placed in a note under
  the columns, with a warning. A third column is dropped, with a warning.
- **Warnings.** Unknown directives, directives that do not apply to the slide's type, unknown front-matter keys
  and non-numeric chart cells are reported in the result's warnings, never silently used or dropped.
- **Inferred types.** A slide titled "Agenda", "Contents" or "Outline" with bullets becomes an agenda. One titled
  "Thank you", "Questions?" or "Q&A" with only text becomes a closing slide.
- **Other blocks.** Code blocks become a code slide, and a lone pipe table becomes a table slide.
