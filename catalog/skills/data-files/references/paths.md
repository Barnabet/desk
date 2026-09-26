# Paths in nested documents

`data_tree` works on JSON, JSON Lines, YAML, TOML, INI and XML as a tree of objects, arrays and values. Every
result is printed with its **concrete address**, which works in a later `get`:

```text
$.data.items[3].customer.email = "ann@example.com"
```

## Path syntax (`get`, `outline --path`, `--path` on the other scripts)

| path | meaning |
|---|---|
| `$` | the root (optional: `data.items` = `$.data.items`) |
| `.key` or `['key']` | an object key; brackets for keys with spaces, dots or quotes: `$['unit price']` |
| `[0]`, `[-1]` | an array element, from the end with a minus |
| `[1:5]`, `[:10]`, `[::2]` | a slice (end excluded) |
| `[*]` or `.*` | every element of an array or every value of an object |
| `..key` | `key` at any depth: `$..price` |
| `..[*]` / `..*` | every value at any depth |
| `[?(@.price > 10)]` | elements whose `price` is over 10 (`==` `!=` `<` `<=` `>` `>=`) |
| `[?(@.name == 'Ann')]` | text comparison (quote text; numbers, `true`, `false`, `null` unquoted) |
| `[?(@.tag =~ '^v\d+')]` | regular expression search (Python syntax) |
| `[?(@.email)]` | elements that have a non-null, non-false `email` |
| `[?(@.user.age >= 18)]` | a dotted path inside the filter |
| `/data/items/0/name` | JSON Pointer (RFC 6901: `~1` is `/`, `~0` is `~`) |

One condition per filter; for "and", chain filters: `items[?(@.price > 10)][?(@.stock)]`. Unions (`[a,b]`) are
not supported: run two paths. A number compared with text that looks like a number compares as a number.

```bash
python3 scripts/data_tree.py get export.json 'data.items[*].email' --as lines          # one value per line
python3 scripts/data_tree.py get export.json 'data.items[?(@.status == "open")].id' --count
python3 scripts/data_tree.py get export.json 'data.items[0:3]' --as yaml
python3 scripts/data_tree.py get export.json '$..price' --limit 100 --offset 100       # the next page
python3 scripts/data_tree.py get export.json 'data.items[*]' --out items.json          # every match to a file
```

## Outline

`outline` prints each distinct path once with its type, how often objects have the key, null counts, a few example
values and recognised formats (email, date, URL):

```text
$.data.items  array[4] of object
  $.data.items[*]  object{5}
    $.data.items[*].price  integer | string  (e.g. 5, 15, "25")
    $.data.items[*].email  string  (in 1/4; e.g. "a@x.org"; format email)
    $.data.items[*].note  null | string  (in 2/4; 1 null; e.g. null, "hi")
```

`--depth` limits nesting, `--max-keys` the keys shown per object, `--path` outlines one branch.

## Find

```bash
python3 scripts/data_tree.py find settings.yaml timeout --keys          # keys containing "timeout"
python3 scripts/data_tree.py find export.json acme --values             # values containing "acme" (case-insensitive)
python3 scripts/data_tree.py find export.json '^INV-\d+$' --regex --values --exact
```

Each hit prints its address and value. `--case-sensitive`, `--exact` (the whole key or value), `--limit`
(default 100) and `--offset` for the next page: the output ends with the exact command (a streamed file is read
again for every page, so narrow the pattern when you can).

## Compare two documents

`data_diff.py old.yaml new.yaml` (JSON objects, YAML, TOML, INI; XML with `--mode doc`) prints every changed,
added and removed value with its path, in the syntax above, so `data_tree get new.yaml '<path>'` shows it in
context. Arrays of objects are matched by an id-like key unique on both sides (`id`, `key`, `name`, …: moving an
item is then not a change, and "reordered" says the order changed); other arrays are aligned item by item, so one
inserted step is one addition. `--ignore updated_at` skips a key wherever it is, `--tolerance`, `--trim` and
`--ignore-case` loosen value comparisons, `--out changes.csv` writes them all. Formats may differ (YAML against
JSON), but values compare by type: `1` and `"1"` differ. Documents over 32 MB are compared as tables of their
record array unless `--mode doc` is given.

## Big JSON

From 32 MB (`DESK_DATA_STREAM_MB`), or with `--stream`, JSON is read with ijson instead of loaded: `outline`,
`get` and `find` make one pass with flat memory, skip subtrees a path cannot match, and stop at `--limit` matches.
`data.records[250000]` in a 100 MB file takes about a second. The outline of a big file is cached. `format --minify`
streams too; `convert` refuses a streamed document: extract a part with `get … --out`, or convert its records to a
table with `data_convert`.

## XML

As a tree (`get`, `find`, `outline`, `convert`), an element becomes an object: attributes are `@name` keys, the
text is the value (or `#text` beside attributes or children), and repeated child elements become arrays:

```xml
<book id="7" lang="en"><title>Dune</title><tag>sf</tag><tag>classic</tag></book>
```

```json
{"book": {"@id": "7", "@lang": "en", "title": "Dune", "tag": ["sf", "classic"]}}
```

Namespace prefixes are kept in keys as written (`m:price`), and declarations show as `@xmlns` keys. XML values
are text; `data_query` types them.

### XPath

`xpath` runs XPath 1.0 with lxml. The document's own prefixes are registered, and the **default namespace is
`d:`** (XPath has no default namespace), so an Atom or KML file needs `d:` on every step:

```bash
python3 scripts/data_tree.py xpath feed.xml '//d:entry/d:title/text()'
python3 scripts/data_tree.py xpath doc.kml '//d:Placemark[d:name="Depot"]/d:Point/d:coordinates/text()'
python3 scripts/data_tree.py xpath catalog.xml 'count(//book[@lang="en"])'
python3 scripts/data_tree.py xpath catalog.xml '//book[price > 30]' --xml          # matched elements as XML
python3 scripts/data_tree.py xpath data.xml '//x:item' --ns x=urn:example:items     # another prefix
```

Element results print a reusable address (`/d:kml/d:Document/d:Folder/d:Placemark[2]/d:name`), attributes and
text; attribute and text results print the element they came from. `xpath` parses the whole file (5 to 10 times
its size in memory): on a big XML, use `find`, or `data_query --record` for the repeated element as a table.
