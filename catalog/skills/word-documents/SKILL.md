---
name: word-documents
description: Create, read and edit Word documents (.docx). Use for any .docx task, such as extracting the text and structure of a document, building a report or memo from Markdown, finding and replacing across the body, tables, headers and footers, inserting or deleting paragraphs, or setting document properties.
license: MIT
---

# Word documents

Scripts in `scripts/` use python-docx (MIT). Desk sets up their Python environment, so run them with `python3`.
Each script prints JSON or Markdown to stdout, and `--help` shows its options.

## Read

```bash
python3 scripts/docx_read.py input.docx                 # Markdown: headings, lists, tables, bold and italic
python3 scripts/docx_read.py input.docx --format json   # structure, with the paragraph indexes edits use
```

Read before editing. Use the JSON form when you need paragraph indexes, styles, headers, footers or comments.

## Create

Write Markdown-lite (see `references/markdown.md`), then:

```bash
python3 scripts/docx_create.py out/report.docx --input report.md --title "Quarterly report" --author "Desk"
python3 scripts/docx_create.py out/letter.docx --input letter.md --template letterhead.docx   # reuse styles, headers, footers
```

Headings, bullet and numbered lists (nested two spaces per level), quotes, code blocks, pipe tables, `\pagebreak`, and
inline **bold**, *italic* and `code` are supported.

## Edit

Put operations in a JSON list and write to a new file:

```bash
cat > edits.json <<'JSON'
[
  {"op": "replace", "find": "ACME Ltd", "replace": "Acme Limited"},
  {"op": "insert_after", "index": 4, "markdown": "The parties agree to review terms annually."},
  {"op": "delete", "index": 9},
  {"op": "append", "markdown": "## Signatures\n\n| Name | Date |\n|---|---|\n| | |"},
  {"op": "properties", "title": "Services agreement v2"}
]
JSON
python3 scripts/docx_edit.py contract.docx --out contract-v2.docx --ops edits.json
```

- `replace` covers the body, tables, headers and footers, and keeps the formatting of the run where each match starts.
  Use `"first": true` to replace only the first match. The output reports how many were replaced; check that count.
- `index` values refer to `docx_read.py --format json` on the original file, whatever other operations come first.
- Never overwrite the user's original. Write a new file and say where it is.

## Check your work

Read the output back with `docx_read.py` and compare it with what was asked. Report what changed, and anything you
could not do: tracked changes, live fields and embedded objects need Word itself.

For anything the scripts do not cover (images, section layout, styles, comments), see `references/python-docx.md` and
write a small script using the same `python3`.
