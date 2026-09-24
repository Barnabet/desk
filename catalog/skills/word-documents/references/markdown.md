# Markdown-lite for docx_create.py and docx_edit.py

| Write | Becomes |
|---|---|
| `# Title` … `###### Minor` | Heading 1–6 |
| A paragraph of text; consecutive lines join | Normal paragraph |
| `- item` / `* item` / `+ item`, indented two spaces per level | List Bullet, List Bullet 2, List Bullet 3 |
| `1. item` (any number), indented two spaces per level | List Number, List Number 2, List Number 3 |
| `> quoted` | Quote style |
| Fenced code block (three backticks) | Monospaced lines (Menlo 9.5pt) |
| `\| a \| b \|` followed by `\|---\|---\|` and more rows | A table with a bold header row (Table Grid) |
| A line with exactly `\pagebreak` | Page break |
| `**bold**`, `*italic*` or `_italic_`, `` `code` `` | Inline formatting |
| `[text](https://…)` | "text (https://…)" (plain text, not a live hyperlink) |

Anything else (images, footnotes, nested tables) needs python-docx directly: see `python-docx.md`.
With `--template`, styles come from the template; a style it lacks falls back to Normal.
