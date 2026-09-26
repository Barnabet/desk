# docx_template.py: filling Word templates

A template is an ordinary .docx or .dotx that the user designed in Word, with `{{placeholders}}` typed where data
goes. Everything else (fonts, letterhead, tables, headers, footers) stays exactly as designed.

```bash
python3 scripts/docx_template.py template.docx --fields                       # what the template expects
python3 scripts/docx_template.py template.docx out/letter.docx --data data.json
python3 scripts/docx_template.py template.docx out/letter.docx --data '{"name": "Ada"}' --strict
```

Start with `--fields`: it lists the placeholders, loops and conditions (with the data keys they use), so you can
build the JSON to match. After filling, the report lists every placeholder left unfilled. Fix the data rather than
passing `--blank-missing` unless blanks are really wanted.

## Placeholders

| Template | Data | Result |
|---|---|---|
| `{{name}}` | `{"name": "Ada"}` | Ada |
| `{{client.address.city}}` | nested objects | dotted path |
| `{{items.0.price}}` or `{{items[0].price}}` | lists | by position |
| `{{name \| upper}}` | | filters, chained left to right |

A placeholder can sit anywhere: body, tables, text boxes, headers, footers, footnotes. Word often splits typed text
into several runs (spell-check, edits, formatting); placeholders are matched across runs and the result takes the
formatting of the placeholder's first character. Values containing `\n` become line breaks.

### Filters

| Filter | Example | Result |
|---|---|---|
| `default:"x"` | `{{note \| default:"none"}}` | used when missing, null or empty |
| `upper`, `lower`, `title`, `capitalize`, `trim` | `{{name \| upper}}` | ADA |
| `date:FORMAT` | `{{due \| date:long}}` | 24 September 2026. Presets `long`, `us` (September 24, 2026), `short` (24/09/2026), `us-short`, `iso`, `month`, `datetime`, or strftime like `"%d %B %Y"` (`%-d` for no leading zero). Input: ISO dates, `24/09/2026`, `today` |
| `number:DECIMALS:THOUSANDS:POINT` | `{{total \| number:2}}`, `{{total \| number:2:" ":","}}` | 1,234.50 / 1 234,50 |
| `currency:SYMBOL:DECIMALS:after` | `{{total \| currency:"€":2:after}}` | 1,234.50 € (default `$1,234.50`) |
| `percent:DECIMALS` | `{{rate \| percent:1}}` (0.125) | 12.5% |
| `round:N`, `int`, `abs` | `{{x \| round:1}}` | |
| `join:SEP` | `{{tags \| join:", "}}` | a, b, c |
| `count`, `first`, `last`, `sum` | `{{items \| count}}` | on lists |
| `yesno:"Yes,No"` | `{{paid \| yesno:"Paid,Due"}}` | |
| `truncate:N`, `replace:OLD:NEW` | | |
| `image:WIDTH:HEIGHT` | `{{logo \| image:4cm}}` | the file at that path (relative to the data file or the current folder) as an inline picture |

## Loops

```text
{{#each items}}            ← alone in its paragraph
{{@number}}. {{name}} — {{price | currency:"€"}}
{{/each}}                  ← alone in its paragraph
```

The content between the two tags repeats once per item: paragraphs, tables, pictures, whole sections of text.

- **Table rows**: put `{{#each items}}` in the first cell of a row and `{{/each}}` in the last cell of the same row
  (or of a later row). Those rows repeat; the header and total rows stay. This is the usual invoice table.
- **Inline**: `{{#each tags}}{{this}}{{#unless @last}}, {{/unless}}{{/each}}` inside one paragraph repeats that part.
- Inside a loop: `{{this}}` (the item itself), `{{name}}` (a field of the item; names not found on the item fall
  back to the outer data), `{{../title}}` (outer data explicitly), `{{@index}}` (0-based), `{{@number}}`
  (1-based), `{{@first}}`, `{{@last}}`. Loops nest.

## Conditions

`{{#if paid}} … {{else}} … {{/if}}` and `{{#unless paid}} … {{/unless}}`, with the same three placements as
loops (own paragraphs, table rows, inline). Conditions: a value (false, 0, "", null, [] and missing are false),
`not x`, `status == "late"`, `status != "late"`, `total > 1000` (also `>=`, `<`, `<=`), `tags contains "vip"`, and
`and` / `or`.

## Word's own fields

- **Merge fields** (`MERGEFIELD Name`, from Word's Mail Merge) are filled from the data key of the same name and
  turned into plain text.
- **Content controls** whose tag or title matches a data key get the value as their text.
- **Document properties** (title, subject, keywords, comments, category) that contain `{{placeholders}}` are
  filled too.
- `{{! a note }}` is a template comment: removed from the output.

## Making a template with docx_create.py

A template can be written in Markdown and built with `docx_create.py` (or with a JSON spec for exact control).
`{{…}}` tags are kept exactly as typed, so they work in Markdown too:

```markdown
| # | Item | Price |
|---|---|--:|
| {{#each items}}{{@number}} | {{description}} | {{price | currency:"€"}}{{/each}} |

Total: **{{total | currency:"€"}}**{{#if paid}} (paid){{/if}}
```

- A `|` inside `{{…}}` in a table row does not split the cell (it is escaped for you); anywhere else in a table
  cell, write `\|` for a literal bar.
- `{{#each items}}{{@number}}` is not turned into an e-mail link, and `_`, `*`, `$` and quotes inside tags stay
  literal.
- Run `docx_template.py template.docx --fields` on the result to check that every tag was seen.

## Tips

- Keep the template untouched; always write the filled copy to a new path.
- Render the result (`docx_render.py`) and look at it: long values can wrap or push a table onto the next page.
- For many documents from one template (mail merge), run the script once per record; a small shell loop or a
  Python loop over `subprocess.run([...])` works, and each run takes well under a second.
