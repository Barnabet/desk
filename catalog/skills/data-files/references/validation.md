# Validation

`data_validate` checks data two ways: a **JSON Schema** (documents, records, table rows) or **table rules** (a short
YAML/JSON file of column checks for any table). It exits 0 when the data is valid, 1 when it is not (the report
is on stdout either way) and 2 for bad arguments, so it can gate a pipeline.

## JSON Schema

```bash
python3 scripts/data_validate.py order.json --schema order.schema.json
python3 scripts/data_validate.py orders.json --schema order.schema.json --path data.orders --each   # each element
python3 scripts/data_validate.py events.jsonl --schema event.schema.json      # every line (automatic for JSONL)
python3 scripts/data_validate.py people.csv --schema person.schema.json       # every row, as an object
python3 scripts/data_validate.py config.yaml --schema '{"type": "object", "required": ["server"]}'
```

- The draft comes from the schema's `$schema` (2020-12, 2019-09, 7, 6, 4, 3); without one, 2020-12. An invalid
  schema is reported with the place where it breaks before any data is checked.
- Formats are checked, not just annotated: `email`, `idn-email`, `date`, `date-time` and `time` (RFC 3339),
  `uri`, `uri-reference`, `ipv4`, `ipv6`, `uuid`, `regex`. Other formats are accepted as annotations.
- Each error lists where (`$.items[3].price`, `line 4`, `row 17`), the failing keyword and the message. All errors
  are counted and grouped by kind (the path with indexes as `[*]`, and the rule), with a count and the first five
  places, so 450,000 `$.id maximum` errors read as one line; `--max-errors` (default 50) limits the one-by-one
  list, and `--out report.json|report.csv` writes every error.
- JSONL lines that are not JSON are reported as errors on their line, not a crash. Table rows leave out null cells,
  so `required` means "not null".
- A big JSON validated with `--each` is streamed (400,000 records in about 4 s, under 60 MB). A JSONL file from
  8 MB is checked in parallel byte ranges (`DESK_MAX_WORKERS` processes), with the same line numbers and counts as
  a serial run: 500,000 lines in about 10 s with two workers.

### Drafting a schema from data

```bash
python3 scripts/data_validate.py orders.json --infer --out order.schema.json            # types, required keys, formats
python3 scripts/data_validate.py orders.json --infer --strict --out order.schema.json   # plus enums, ranges, no extra keys
```

The inferred schema is a starting point: a key present in every record becomes `required`, strings that all look
like dates, emails or URIs get a `format` (the same rules the checker applies, so a `date-time` needs seconds and a
time zone: `2024-01-01 10:00` gets none). JSONL is read in full (in parallel when big); `--sample N` uses the
first N lines. A big table's shape comes from a reproducible 50,000-row sample (`--sample`), while its required
columns, number ranges and enums come from every row. `--strict` adds `enum` for small value sets, `minimum`/`maximum` and
`additionalProperties: false`. Read it, loosen what is accidental (an enum of the three countries seen so far), and
check that the data validates against it.

## Table rules

For CSV and every other table format (Parquet, a SQLite table with `--table`, JSONL records…):

```yaml
columns:
  id:       {type: integer, required: true, not_null: true, unique: true}
  email:    {regex: '^[^@\s]+@[^@\s]+\.[a-z]+$', not_null: true}
  age:      {type: integer, min: 0, max: 120}
  country:  {allowed: [FR, DE, US]}
  name:     {min_length: 1, max_length: 80}
  day:      {type: date, format: '%d/%m/%Y'}
  signed:   {type: datetime, min: '2024-01-01'}
unique: [[order_id, line]]          # composite keys
row_count: {min: 1, max: 1000000}
no_extra_columns: true              # a column not listed above is a violation
empty_is_null: true                 # default: '' and '   ' count as missing
```

| rule | checks |
|---|---|
| `required` | the column exists (default true for listed columns; `required: false` makes a missing column fine) |
| `not_null` | no null or empty cell |
| `type` | every non-empty value parses as `integer`, `number` (decimal comma accepted), `boolean` (true/false, 1/0, yes/no, t/f, y/n), `date`, `datetime` or `string`; `format` gives the strptime pattern for dates |
| `unique` | no value repeats (empty cells ignored) |
| `min`, `max` | numbers compare as numbers, dates and datetimes as dates, other text alphabetically |
| `regex` (or `pattern`) | the whole value matches (RE2 syntax, as in DuckDB) |
| `allowed` | the value is one of the list (compared as text, case and spaces included) |
| `min_length`, `max_length` | text length in characters |

Column names match case-insensitively. Columns can also be given as a list: `columns: [{name: id, type: integer}]`.

The report lists each rule with its violation count, the first row numbers and example values:

```text
**INVALID**: people.csv (3 rows) against the rules · 3 violation(s) · 0.17s

- FAIL `id` unique: 1 — rows 2, 3 (1 repeated values)
- FAIL `email` regex ^[^@]+@[^@]+$: 1 — rows 2 — e.g. 'bob@@x.org'
- FAIL `age` max 120: 1 — rows 2 — e.g. '200'
```

Row numbers count data rows from 1 (in a CSV, the file line is the row number + 1 for the header). `--out
violations.csv` writes one line per rule with every violating row number. To look at the rows:

```bash
python3 scripts/data_query.py people.csv --rows 2-3
python3 scripts/data_query.py people.csv --sql "SELECT * FROM people WHERE id IN (SELECT id FROM people GROUP BY id HAVING count(*) > 1)"
```

## Choosing

- A JSON document, config or API payload: JSON Schema (it can express nesting, conditionals, `oneOf`).
- A table whose columns must meet business rules: table rules (shorter, and they report row numbers).
- Both work on JSONL and on tables; for a table meant to be loaded into a database, run the rules, then
  `data_profile` for what rules do not catch (outliers, near-duplicates, mixed date formats).
