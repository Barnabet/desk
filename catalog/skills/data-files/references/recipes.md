# Recipes

DuckDB SQL covers most data work in one `data_query` call. Every input is a table named after its file (see
`formats.md`); put long queries in a file and pass `--sql-file query.sql`. Write big results with `--out` rather
than printing them.

## Cleaning

```sql
-- the latest row per key
SELECT * FROM customers QUALIFY row_number() OVER (PARTITION BY id ORDER BY updated_at DESC) = 1

-- dates in several formats (NULL where none fits); the profile suggests the formats
SELECT try_strptime(signup, ['%d/%m/%Y', '%Y-%m-%d', '%d %b %Y']) AS signup FROM raw

-- numbers stored as text with currency signs, spaces or thousands separators
SELECT TRY_CAST(replace(regexp_replace(price, '[^0-9,.-]', '', 'g'), ',', '') AS DECIMAL(12, 2)) AS price FROM raw

-- trim, case, blanks to NULL
SELECT trim(name) AS name, upper(country) AS country, nullif(trim(city), '') AS city FROM raw

-- values that differ only by case or spaces
SELECT lower(trim(city)) AS k, list(DISTINCT city) AS spellings FROM raw GROUP BY 1 HAVING count(DISTINCT city) > 1
```

To keep the result: `data_convert raw.csv clean.csv --sql "…"`, or `data_query raw.csv --sql "…" --out clean.parquet`.

## Reshaping

```sql
PIVOT sales ON region USING sum(amount) GROUP BY product                 -- long → wide
UNPIVOT wide ON COLUMNS(* EXCLUDE (id)) INTO NAME measure VALUE value    -- wide → long
SELECT id, unnest(tags) AS tag FROM items                                -- one row per list element
SELECT id, unnest(dims) FROM items                                       -- a struct's fields as columns
SELECT id, dims.w, dims.h, tags[1] AS first_tag, len(tags) AS n FROM items
SELECT email, split_part(email, '@', 2) AS domain FROM people
```

`data_convert --explode tags`, `--flatten` and `--nest` do the same without SQL.

## Analysis

```sql
SELECT date_trunc('month', day) AS month, sum(amount) AS total FROM sales GROUP BY 1 ORDER BY 1
SELECT day, sum(amount) OVER (ORDER BY day) AS running FROM sales
SELECT region, quantile_cont(amount, [0.5, 0.9, 0.99]) AS p50_p90_p99 FROM sales GROUP BY 1
SELECT * FROM sales QUALIFY row_number() OVER (PARTITION BY region ORDER BY amount DESC) <= 3   -- top 3 per group
SELECT floor(amount / 100) * 100 AS bucket, count(*) AS n FROM sales GROUP BY 1 ORDER BY 1      -- a histogram
SELECT corr(qty, amount), regr_slope(amount, qty) FROM sales
SELECT * FROM sales USING SAMPLE 1% (reservoir, 42)                       -- a reproducible sample
SUMMARIZE sales                                                            -- min, max, nulls, quartiles per column
```

## Several files

```bash
python3 scripts/data_query.py orders.parquet customers.csv --sql "SELECT c.country, sum(o.amount) FROM orders o JOIN customers c USING (customer_id) GROUP BY 1"
python3 scripts/data_query.py --as old=v1.csv --as new=v2.csv --sql "SELECT id FROM new ANTI JOIN old USING (id)"   # ids only in new
python3 scripts/data_query.py 'exports/*.csv' --sql "SELECT _file, count(*) FROM exports GROUP BY 1"                 # one table, a _file column
python3 scripts/data_query.py a.csv --sql "SELECT * FROM a UNION ALL BY NAME SELECT * FROM 'b.parquet'"           # a file read inline
```

`data_diff` is the better tool for "what changed between two versions".

## Logs and fixed-width text

A log reads as one `line` column (row numbers are line numbers). Cut it with a regular expression into a struct:

```sql
SELECT p.level, count(*) AS n
FROM (SELECT regexp_extract(line, '^\[([^\]]+)\] \[(\w+)\] (.*)$', ['ts', 'level', 'msg']) AS p FROM apache)
GROUP BY 1 ORDER BY 2 DESC
```

`strptime(p.ts, '%a %b %d %H:%M:%S %Y')` turns the text time into a timestamp. A fixed-width file without
separators: read it with `--delimiter lines` (add `--header yes` if the first line is a header) and cut by
position:

```sql
SELECT trim(substr(line, 1, 10)) AS code, TRY_CAST(trim(substr(line, 11, 8)) AS INTEGER) AS qty FROM stock
```

## JSON inside cells

```sql
SELECT payload->>'$.user.id' AS user_id, json_extract(payload, '$.items[0].sku') AS first_sku FROM events
SELECT json_keys(payload) FROM events LIMIT 5                              -- what the objects hold
SELECT unnest(from_json(payload->'$.items', '[{"sku": "VARCHAR", "qty": "INTEGER"}]'), recursive := true) FROM events
```

## Custom Python

When SQL is not enough, write a short script and run it with the same `python3`. The skill's helpers load any
format into DuckDB the way the scripts do (encoding, dialect, Parquet cache) and write any output format:

```python
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.environ.get("SKILL_DIR", "."), "scripts"))   # this skill's scripts folder
from _common import output_path
from _duck import connect, load_all, parse_inputs, read_opts
from _out import out_format, write_table

con = connect()                                                     # in memory, temp files only, no downloads
opts = read_opts(SimpleNamespace(encoding=None, delimiter=None))    # same fields as --encoding, --delimiter, --path …
loaded = load_all(con, parse_inputs(["sales.csv", "export.json"]), opts)
for ld in loaded:
    print(ld.name, ld.fmt, ld.notes)                                # table names and what was assumed

for region, total in con.sql("SELECT region, sum(amount) FROM sales GROUP BY 1").fetchall():
    print(f"{region:<10} {total:>12,.2f}")

out = output_path("by_region.parquet", [p for ld in loaded for p in ld.paths])   # refuses to replace an input
fmt, comp = out_format(out)
write_table(con, "SELECT region, product, sum(qty) AS qty FROM sales GROUP BY ALL", out, fmt, comp)
```

- `con.sql(q).fetchall()` gives Python rows and `con.sql(q).to_arrow_table()` a pyarrow table; for big results,
  `cur = con.execute(q)` then `cur.fetchmany(10000)` in a loop keeps memory flat.
- `_tree.load_doc(path, fmt)`, `_tree.select(doc, _tree.parse_path("data.items[*].id"))` and
  `_tree.stream_select(path, steps, limit)` walk nested documents as `data_tree` does.
- pandas is installed, but the helpers keep it from loading (pyarrow and DuckDB would import it on every call just to
  check types, which costs up to a second). To use it in your script, import it before the helpers, or call
  `from _formats import allow_pandas; allow_pandas()` first.
