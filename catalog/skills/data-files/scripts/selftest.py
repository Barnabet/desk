#!/usr/bin/env python3
"""Self-test for the data-files skill: builds fixtures in a temp folder, runs every script the way an agent does
(python3 scripts/<name>.py …) and checks the real outputs: values, counts, round trips, rendered image sizes,
the Parquet cache for big inputs and the streamed paths for big JSON. No network. Prints "ok: N checks in S s".

The skill never uses LibreOffice; every child runs with DESK_SOFFICE=none all the same.
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
import os
import random
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
T0 = time.time()
CHECKS = 0
FAILURES: list[str] = []
TIMES: list[tuple[float, str]] = []
if __name__ == "__main__" and any(a in ("-h", "--help") for a in sys.argv[1:]):
    print(__doc__.strip() + "\n\nExamples:\n  python3 scripts/selftest.py            # exit 0 when every check passes\n  DESK_DEBUG=1 python3 scripts/selftest.py   # also lists the slowest script runs")
    sys.exit(0)
TMP = Path(tempfile.mkdtemp(prefix="desk-data-selftest-"))
ENV = {**os.environ, "DESK_SOFFICE": "none", "DESK_FILE_CACHE": str(TMP / "cache"), "PYTHONIOENCODING": "utf-8"}
ENV.pop("DESK_NO_CACHE", None)
ENV.pop("DESK_DATA_BIG_MB", None)
ENV.pop("DESK_DATA_STREAM_MB", None)


def run(script: str, *args: Any, rc: int | None = 0, env: dict[str, str] | None = None) -> tuple[str, str]:
    cmd = [sys.executable, str(HERE / f"{script}.py"), *[str(a) for a in args]]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, cwd=str(TMP), env={**ENV, **(env or {})}, timeout=300)
    TIMES.append((time.time() - t0, f"{script} {' '.join(map(str, args))[:100]}"))
    out, err = p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    if rc is not None and p.returncode != rc:
        fail(f"{script} {' '.join(map(str, args))[:160]}: exit {p.returncode} (wanted {rc}): {(err or out).strip()[-600:]}")
    return out, err


def run_json(script: str, *args: Any, rc: int | None = 0, env: dict[str, str] | None = None) -> Any:
    out, err = run(script, *args, "--format", "json", rc=rc, env=env)
    try:
        return json.loads(out)
    except ValueError:
        fail(f"{script} {' '.join(map(str, args))[:120]}: not JSON: {out[:300]!r} {err[:300]!r}")
        return {}


def ok(cond: Any, what: str) -> bool:
    global CHECKS
    CHECKS += 1
    if not cond:
        fail(what)
    return bool(cond)


def fail(what: str) -> None:
    FAILURES.append(what)
    print(f"FAIL: {what}", file=sys.stderr)


def png_size(p: Path) -> tuple[int, int]:
    data = p.read_bytes()[:32]
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def timed(fn: Any) -> float:
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t


# ── fixtures ────────────────────────────────────────────────────────────


def fixtures() -> None:
    rnd = random.Random(7)
    regions = ["North", "South", "East", "West"]
    rows = []
    for i in range(1, 3001):
        rows.append({
            "id": i,
            "day": (dt.date(2024, 1, 1) + dt.timedelta(days=i % 120)).isoformat(),
            "region": regions[i % 4],
            "product": "ABC"[i % 3],
            "qty": 1 + i % 20,
            "amount": round(10 + (i * 37 % 1000) / 4, 2),
            "email": f"user{i}@example.com",
        })
    write_csv(TMP / "sales.csv", rows)
    with gzip.open(TMP / "sales.csv.gz", "wt", encoding="utf-8", newline="") as f:
        f.write((TMP / "sales.csv").read_text(encoding="utf-8"))
    # v2: rows 3 and 4 removed, 3 amounts and 1 region changed, 2 rows added, a new column
    v2 = []
    for r in rows:
        if r["id"] in (3, 4):
            continue
        r = {**r, "channel": "web" if r["id"] % 2 else "shop"}
        if r["id"] in (10, 11, 12):
            r["amount"] = round(r["amount"] + 5, 2)
        if r["id"] == 20:
            r["region"] = "Central"
        v2.append(r)
    v2 += [{**rows[0], "id": 5001, "channel": "web"}, {**rows[1], "id": 5002, "channel": "shop"}]
    write_csv(TMP / "sales_v2.csv", v2)
    (TMP / "parts").mkdir()
    for k in range(3):
        write_csv(TMP / "parts" / f"part-{k + 1}.csv", rows[k * 100 : (k + 1) * 100])
    # European CSV: cp1252, semicolons, decimal comma with thousands dots
    with open(TMP / "euro.csv", "w", encoding="cp1252", newline="") as f:
        f.write("Nom;Montant;Ville\n")
        for i in range(40):
            f.write(f"Café {i};{fmt_eu(1000 + i * 12.5)};Zürich\n")
    (TMP / "wide.tsv").write_text("a\tb\tc\n" + "".join(f"{i}\t{i * 2}\tx{i}\n" for i in range(12)), encoding="utf-16")
    # Messy table for the profiler
    rnd2 = random.Random(3)
    messy = []
    for i in range(1, 501):
        messy.append({
            "id": i,
            "signup": (dt.date(2023, 1, 1) + dt.timedelta(days=i)).strftime("%Y-%m-%d" if i % 10 else "%d/%m/%Y"),
            "amount": "N/A" if i % 25 == 0 else f"{rnd2.uniform(10, 100):.2f}",
            "city": ["Paris", "Lyon", "paris", " Lyon", "Nice"][i % 5],
            "email": f"p{i}@mail.org" if i != 77 else "not-an-email",
            "score": 50 + (i % 7) if i not in (100, 200) else 5000,
            "code": f"{i:05d}",
        })
    messy += [dict(messy[5]), dict(messy[6]), dict(messy[6])]
    write_csv(TMP / "messy.csv", messy)
    nested = {"meta": {"version": 3, "generated": "2024-05-01T10:00:00Z"}, "data": {"items": [
        {"id": i, "name": f"item {i}", "price": i * 1.5, "tags": ["x", "y"][: i % 3], "dims": {"w": i, "h": i * 2}} for i in range(1, 21)]}}
    (TMP / "nested.json").write_text(json.dumps(nested, indent=2), encoding="utf-8")
    (TMP / "events.jsonl").write_text("".join(json.dumps({"ts": f"2024-03-{1 + i % 28:02d}T10:00:00Z", "user": f"u{i % 7}", "n": i}) + "\n" for i in range(60)), encoding="utf-8")
    bad = [json.dumps({"id": i, "email": f"a{i}@x.org", "age": 20 + i}) for i in range(10)]
    bad[3] = json.dumps({"id": 3, "email": "a3@x.org", "age": "old"})
    bad[6] = "{not json"
    (TMP / "people.jsonl").write_text("\n".join(bad) + "\n", encoding="utf-8")
    (TMP / "person.schema.json").write_text(json.dumps({
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "required": ["id", "email", "age"],
        "properties": {"id": {"type": "integer"}, "email": {"type": "string", "format": "email"}, "age": {"type": "integer", "minimum": 0}}}), encoding="utf-8")
    (TMP / "config.yaml").write_text("server:\n  host: example.org\n  port: 8080\n  tls: true\ndatabases:\n  - name: main\n    size: 10\n  - name: logs\n    size: 3\nfeatures: [a, b]\n", encoding="utf-8")
    (TMP / "config.toml").write_text('title = "demo"\n[owner]\nname = "Tom"\ndob = 1979-05-27T07:32:00-08:00\n[[servers]]\nname = "alpha"\nip = "10.0.0.1"\n[[servers]]\nname = "beta"\nip = "10.0.0.2"\n', encoding="utf-8")
    (TMP / "app.ini").write_text("[DEFAULT]\nuser = admin\n\n[db]\nhost = localhost\nport = 5432\n\n[cache]\nttl = 60\n", encoding="utf-8")
    (TMP / "feed.xml").write_text("""<?xml version="1.0" encoding="UTF-8"?>
<catalog xmlns="urn:example:books" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <book id="bk101" lang="en"><dc:creator>Gambardella, Matthew</dc:creator><title>XML Developer's Guide</title><price>44.95</price><published>2000-10-01</published></book>
  <book id="bk102" lang="en"><dc:creator>Ralls, Kim</dc:creator><title>Midnight Rain</title><price>5.95</price><published>2000-12-16</published></book>
  <book id="bk103" lang="fr"><dc:creator>Corets, Eva</dc:creator><title>Maeve Ascendant</title><price>5.95</price><published>2000-11-17</published></book>
</catalog>
""", encoding="utf-8")
    db = sqlite3.connect(TMP / "shop.sqlite")
    db.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, country TEXT)")
    db.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), amount REAL, day DATE)")
    db.execute("CREATE INDEX ix_orders_customer ON orders(customer_id)")
    db.execute("CREATE VIEW big_orders AS SELECT * FROM orders WHERE amount > 100")
    db.executemany("INSERT INTO customers VALUES (?,?,?)", [(i, f"Cust {i}", ["FR", "DE", "US"][i % 3]) for i in range(1, 11)])
    db.executemany("INSERT INTO orders VALUES (?,?,?,?)", [(i, 1 + i % 10, float(i * 7 % 300), "2024-02-01") for i in range(1, 51)])
    db.commit()
    db.close()
    import fastavro

    schema = fastavro.parse_schema({"type": "record", "name": "Person", "fields": [
        {"name": "id", "type": "long"}, {"name": "name", "type": ["null", "string"]},
        {"name": "born", "type": {"type": "int", "logicalType": "date"}},
        {"name": "address", "type": {"type": "record", "name": "Address", "fields": [{"name": "city", "type": "string"}, {"name": "zip", "type": "string"}]}},
        {"name": "tags", "type": {"type": "array", "items": "string"}}]})
    with open(TMP / "people.avro", "wb") as f:
        fastavro.writer(f, schema, [{"id": i, "name": None if i == 2 else f"P{i}", "born": dt.date(1990, 1, 1) + dt.timedelta(days=i), "address": {"city": ["Oslo", "Rome"][i % 2], "zip": f"{i:04d}"}, "tags": ["a"] * (i % 3)} for i in range(1, 31)])
    import pandas as pd
    import pyreadstat

    df = pd.DataFrame({"id": [1, 2, 3, 4], "sex": [1.0, 2.0, None, 1.0], "age": [34.5, 51.0, 29.0, None], "name": ["Ann", "Bob", "", "Dee"]})
    pyreadstat.write_sav(df, str(TMP / "survey.sav"), column_labels={"sex": "Sex of respondent", "age": "Age in years"}, variable_value_labels={"sex": {1: "male", 2: "female"}}, file_label="Test survey")
    pyreadstat.write_por(df, str(TMP / "survey.por"))
    # Big-file stand-ins: DESK_DATA_BIG_MB / DESK_DATA_STREAM_MB scale the thresholds down in the checks below.
    cities = ["Paris", "Lyon", "Nice", "Lille", "Nantes", "Brest", "Metz"]
    with open(TMP / "big.csv", "w", encoding="utf-8", newline="") as f:
        f.write("id,day,region,qty,amount,note,email,city,code\n")
        f.writelines(f"{i},2024-{1 + i % 12:02d}-{1 + i % 28:02d},{regions[i % 4]},{i % 50},{(i * 7919 % 100000) / 100:.2f},note {i % 997},u{i % 5003}@mail.org,{cities[i % 7]},C{i % 211:04d}\n" for i in range(1, 300_001))
    with open(TMP / "big.json", "w", encoding="utf-8") as f:
        f.write('{"meta": {"source": "selftest"}, "data": {"records": [')
        for i in range(30_000):
            f.write(("," if i else "") + json.dumps({"id": i, "user": {"name": f"user{i % 997}", "age": 18 + i % 60}, "amount": round(i * 1.25 % 900, 2), "tags": ["a", "b"][: i % 3]}))
        f.write("]}}")
    (TMP / "aligned.txt").write_text("x    y     z\n1    2     3\n4   16    64\n5   25   125\n", encoding="utf-8")
    (TMP / "app.log").write_text("".join(f"[2024-01-0{1 + i % 9} 10:00:{i:02d}] [{'error' if i % 5 == 0 else 'info'}] worker {i}, pid {1000 + i}\n" for i in range(40)), encoding="utf-8")
    (TMP / "ragged.csv").write_text("a,b,c\n1,2,3\n4,5\n6,7,8,9\n10,11,12\n", encoding="utf-8")
    (TMP / "prices.csv").write_text("Date,Close\n" + "".join(f"{d}-{m}-03,{20 + i * 0.5:.2f}\n" for i, (d, m) in enumerate([(1, "Jul"), (15, "Jul"), (1, "Aug"), (15, "Aug"), (1, "Sep"), (19, "Sep")])), encoding="utf-8")
    (TMP / "v1.2.parquet").write_bytes(b"")  # replaced below by a real Parquet file (a dotted file name)
    import duckdb

    con = duckdb.connect(str(TMP / "warehouse.duckdb"))
    con.execute("CREATE SCHEMA sales")
    con.execute("CREATE TABLE sales.orders AS SELECT range AS id, range % 7 AS store, (range * 13 % 1000) / 10.0 AS amount FROM range(500)")
    con.execute("CREATE TABLE stores AS SELECT range AS id, 'Store ' || range AS name FROM range(7)")
    con.execute("CREATE VIEW store_totals AS SELECT s.name, sum(o.amount) AS total FROM sales.orders o JOIN stores s ON s.id = o.store GROUP BY 1")
    con.close()
    con = duckdb.connect()
    con.execute(f"COPY (SELECT range AS k FROM range(5)) TO '{(TMP / 'v1.2.parquet').as_posix()}' (FORMAT parquet)")
    con.close()
    (TMP / "empty.csv").write_bytes(b"")
    (TMP / "binary.dat").write_bytes(bytes(range(256)) * 40)


def fmt_eu(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


# ── checks per script ───────────────────────────────────────────────────


def check_info() -> None:
    r = run_json("data_info", "sales.csv")
    ok(r.get("rows") == 3000 and r.get("columns") == 7, f"data_info sales.csv: 3000 × 7 ({r.get('rows')} × {r.get('columns')})")
    types = {c["name"]: c["type"] for c in r.get("schema", [])}
    ok(types.get("id") == "BIGINT" and types.get("day") == "DATE" and types.get("amount") == "DOUBLE", f"data_info infers types: {types}")
    ok(r.get("dialect", {}).get("delimiter") == "," and r["dialect"].get("header") is True, "data_info dialect")
    r = run_json("data_info", "euro.csv")
    types = {c["name"]: c["type"] for c in r.get("schema", [])}
    ok(r.get("encoding", "").startswith("cp1252"), f"euro.csv encoding cp1252 ({r.get('encoding')})")
    ok(r.get("dialect", {}).get("delimiter") == ";" and r["dialect"].get("decimal_comma"), "euro.csv: semicolon and decimal comma")
    ok(types.get("Montant") == "DOUBLE" and types.get("Nom") == "VARCHAR", f"euro.csv: 1.234,56 read as numbers ({types})")
    ok(any("Café 0" == str(c.get("example")) for c in r.get("schema", [])), "euro.csv: accents decoded")
    r = run_json("data_info", "wide.tsv")
    ok(r.get("rows") == 12 and "utf-16" in r.get("encoding", ""), f"UTF-16 TSV ({r.get('rows')}, {r.get('encoding')})")
    r = run_json("data_info", "sales.csv.gz")
    ok(r.get("rows") == 3000 and r.get("compression") == "gzip", "gzip CSV")
    r = run_json("data_info", "nested.json")
    ok(r.get("rows") == 20 and r.get("record_path") == "$.data.items", f"nested.json records at $.data.items ({r.get('record_path')})")
    ok(any("$.data.items[*].dims.w" in ln for ln in r.get("structure", {}).get("outline", [])), "nested.json outline has addresses")
    r = run_json("data_info", "feed.xml")
    ok(r.get("rows") == 3 and r.get("xml_record") == "catalog/book", f"XML rows are <book> ({r.get('xml_record')})")
    types = {c["name"]: c["type"] for c in r.get("schema", [])}
    ok(types.get("price") == "DOUBLE" and types.get("published") == "DATE", f"XML values typed ({types})")
    ok(r.get("structure", {}).get("namespaces", {}).get("dc") == "http://purl.org/dc/elements/1.1/", "XML namespaces listed")
    r = run_json("data_info", "shop.sqlite")
    objs = {o["name"]: o for o in r.get("database", {}).get("objects", [])}
    ok(objs.get("orders", {}).get("rows") == 50 and objs.get("big_orders", {}).get("type") == "view" and objs.get("ix_orders_customer", {}).get("type") == "index", "SQLite tables, views, indexes, counts")
    ok(objs.get("orders", {}).get("foreign_keys", [{}])[0].get("table") == "customers", "SQLite foreign keys")
    r = run_json("data_info", "people.avro")
    types = {c["name"]: c["type"] for c in r.get("schema", [])}
    ok(r.get("rows") == 30 and types.get("born") == "DATE" and types.get("address", "").startswith("STRUCT"), f"Avro schema ({types})")
    r = run_json("data_info", "survey.sav")
    lab = {c["name"]: c.get("label") for c in r.get("schema", [])}
    ok(r.get("rows") == 4 and lab.get("sex") == "Sex of respondent", f"SPSS labels ({lab})")
    ok(r.get("stat_meta", {}).get("value_labels", {}).get("sex", {}).get("1") == "male", "SPSS value labels")
    for f in ("config.yaml", "config.toml", "app.ini"):
        r = run_json("data_info", f)
        ok(r.get("format") == f.split(".")[1] and r.get("structure", {}).get("outline"), f"data_info {f}")
    out, _ = run("data_info", "sales.csv", "euro.csv")
    ok("## sales.csv" in out and "## euro.csv" in out and "--rows 6-105" in out, "data_info: several files, Markdown with next commands")
    _, err = run("data_info", "empty.csv", rc=1)
    ok("empty" in err and err.startswith("error:"), "empty file: one error line")
    _, err = run("data_info", "binary.dat", rc=1)
    ok("binary" in err, "binary file refused")
    _, err = run("data_info", "missing.csv", rc=1)
    ok("does not exist" in err, "missing file")


def check_query() -> None:
    r = run_json("data_query", "sales.csv", "--sql", "SELECT region, count(*) AS n, round(sum(amount), 2) AS total FROM sales GROUP BY 1 ORDER BY 1")
    got = {row["region"]: row["n"] for row in r.get("rows", [])}
    ok(got == {"East": 750, "North": 750, "South": 750, "West": 750}, f"GROUP BY ({got})")
    r = run_json("data_query", "sales.csv", "nested.json", "--sql", "SELECT count(*) AS n FROM sales s JOIN nested n ON s.id = n.id")
    ok(r.get("rows", [{}])[0].get("n") == 20, "join across CSV and JSON")
    r = run_json("data_query", str(TMP / "parts" / "*.csv"), "--sql", "SELECT count(*) AS n, count(DISTINCT _file) AS files FROM parts")
    ok(r.get("rows", [{}])[0] == {"n": 300, "files": 3}, f"glob as one table ({r.get('rows')})")
    r = run_json("data_query", "--as", "a=sales.csv", "--as", "b=sales_v2.csv", "--sql", "SELECT count(*) AS n FROM a ANTI JOIN b USING (id)")
    ok(r.get("rows", [{}])[0].get("n") == 2, "--as names")
    r = run_json("data_query", "shop.sqlite", "--sql", "SELECT c.country, count(*) AS n FROM orders o JOIN customers c ON c.id = o.customer_id GROUP BY 1 ORDER BY 1")
    ok(sum(x["n"] for x in r.get("rows", [])) == 50, "SQLite tables unqualified")
    out, _ = run("data_query", "shop.sqlite", "--tables")
    ok("customers" in out and "big_orders" in out and "(50 rows)" in out, "--tables")
    out, _ = run("data_query", "sales.csv", "--explain", "--sql", "SELECT * FROM sales WHERE amount > 100")
    ok("FILTER" in out.upper() or "SCAN" in out.upper(), "--explain")
    out, _ = run("data_query", "sales.csv", "--limit", "10")
    ok("rows 1-10 of 3000" in out and "--offset 10" in out, "paging footer with the next command")
    out, _ = run("data_query", "sales.csv", "--rows", "2991-3000", "--format", "csv")
    lines = out.strip().splitlines()
    ok(lines[0].startswith("id,day") and lines[1].startswith("2991,") and lines[10].startswith("3000,"), "--rows as CSV")
    r = run_json("data_query", "sales.csv", "--find", "USER2999@")
    ok(r.get("total") == 1 and r["rows"][0]["_row"] == 2999 and r["rows"][0]["_match"] == "email", f"--find gives row numbers ({r.get('rows')})")
    r = run_json("data_query", "sales.csv", "--find", "^user1[0-9]@", "--regex", "--in", "email")
    ok(r.get("total") == 10, f"--find --regex --in ({r.get('total')})")
    r = run_json("data_query", "sales.csv", "--sql", "SELECT * FROM sales WHERE region = $r AND qty > $q", "--param", "r=West", "--param", "q=15")
    ok(r.get("total", 0) > 0 and all(x["region"] == "West" and x["qty"] > 15 for x in r.get("rows", [])), "--param binding")
    out, _ = run("data_query", "sales.csv", "--sql", "SELECT * FROM sales WHERE region = 'East'", "--out", "east.parquet")
    ok("Wrote 750 rows" in out, "--out parquet")
    r = run_json("data_query", "east.parquet", "--sql", "SELECT count(*) AS n, min(region) AS r FROM east")
    ok(r.get("rows", [{}])[0] == {"n": 750, "r": "East"}, "parquet written and read back")
    _, err = run("data_query", "sales.csv", "--sql", "SELECT nope FROM sales", rc=1)
    ok("SQL error" in err and "sales(id, day" in err, "SQL errors list the columns")
    _, err = run("data_query", "sales.csv", "--sql", "SELECT * FROM sales", "--out", "sales.csv", rc=1)
    ok("refusing to overwrite the input" in err, "never overwrites an input")
    before = (TMP / "sales.csv").read_bytes()
    _, err = run("data_query", "sales.csv", "--sql", "COPY (SELECT 1 AS x) TO 'sales.csv' (HEADER)", rc=2)
    ok("--out" in err and (TMP / "sales.csv").read_bytes() == before, "COPY … TO in SQL is refused (inputs stay intact)")
    _, err = run("data_query", "sales.csv", "--sql", "INSTALL httpfs", rc=2)
    ok("INSTALL" in err, "extensions are never installed from SQL")
    _, err = run("data_query", "sales.csv", "--sql", "SET autoinstall_known_extensions = true", rc=1)
    ok("locked" in err, "settings are locked")
    r = run_json("data_query", "people.avro", "--sql", "SELECT address.city AS city, count(*) AS n FROM people GROUP BY 1 ORDER BY 1")
    ok([x["n"] for x in r.get("rows", [])] == [15, 15], "Avro nested fields in SQL")
    r = run_json("data_query", "survey.sav", "--labels", "--sql", "SELECT sex FROM survey ORDER BY id")
    ok([x["sex"] for x in r.get("rows", [])] == ["male", "female", None, "male"], "SPSS --labels")
    r = run_json("data_query", "config.toml", "--sql", "SELECT name FROM config ORDER BY 1")
    ok([x["name"] for x in r.get("rows", [])] == ["alpha", "beta"], "TOML array of tables as rows")
    r = run_json("data_query", "euro.csv", "--sql", "SELECT round(sum(Montant), 2) AS s FROM euro")
    ok(abs((r.get("rows", [{}])[0].get("s") or 0) - sum(1000 + i * 12.5 for i in range(40))) < 0.01, "decimal-comma numbers sum correctly")


def check_profile() -> None:
    r = run_json("data_profile", "messy.csv")
    issues = " | ".join(i["issue"] for i in r.get("issues", []))
    ok("mixed date formats" in issues, f"profile: mixed date formats ({issues[:300]})")
    ok("placeholder" in issues, "profile: placeholder nulls")
    ok("differ only by case or spaces" in issues, "profile: case/space variants")
    ok("duplicate rows" in issues and r.get("duplicates", {}).get("rows") == 3, "profile: duplicate rows")
    ok("outliers" in issues, "profile: outliers")
    ok("not email" in issues, "profile: pattern violations")
    ok("numbers stored as text" in issues or "mixed types" in issues, "profile: numbers stored as text")
    code = next((p for p in r.get("profiles", []) if p["name"] == "code"), {})
    ok(code.get("pattern") == "zero-padded id" and "code" not in issues.split("numbers stored as text")[0][-40:], "zero-padded codes are ids, not numbers")
    s = run_json("data_profile", "sales.csv")
    ok("id" in (s.get("candidate_keys") or []), f"candidate key id ({s.get('candidate_keys')})")
    amt = next((p for p in s.get("profiles", []) if p["name"] == "amount"), {})
    ok(amt.get("median") is not None and amt.get("q25") is not None and amt.get("std") is not None, "numeric stats")
    corr = {(c["a"], c["b"]): c["r"] for c in s.get("correlations", [])}
    ok(corr and all(-1 <= v <= 1 for v in corr.values()), "correlations")
    out, _ = run("data_profile", "messy.csv", "--out", "profile.md")
    ok((TMP / "profile.md").read_text(encoding="utf-8").startswith("# Profile") and "## Issues" in out, "profile Markdown report and --out")


def check_convert() -> None:
    out, _ = run("data_convert", "sales.csv", "sales.parquet")
    ok("3,000 rows" in out, "csv → parquet")
    run("data_convert", "sales.parquet", "back.csv")
    ok((TMP / "back.csv").read_text(encoding="utf-8").splitlines() == (TMP / "sales.csv").read_text(encoding="utf-8").splitlines(), "csv → parquet → csv round trip is exact")
    run("data_convert", "nested.json", "flat.csv")
    head = (TMP / "flat.csv").read_text(encoding="utf-8").splitlines()[0]
    ok(head == "id,name,price,tags,dims.w,dims.h", f"nested JSON flattened to dotted columns ({head})")
    run("data_convert", "flat.csv", "renested.json", "--nest")
    back = json.loads((TMP / "renested.json").read_text(encoding="utf-8"))
    ok(back[0].get("dims") == {"w": 1, "h": 2} and back[0]["tags"] in (["x"], '["x"]'), f"--nest rebuilds objects ({back[0] if back else None})")
    run("data_convert", "euro.csv", "euro-utf8.csv")
    t = (TMP / "euro-utf8.csv").read_text(encoding="utf-8").splitlines()
    ok(t[1] == "Café 0,1000.0,Zürich", f"cp1252 decimal-comma CSV → UTF-8 CSV ({t[1] if len(t) > 1 else t})")
    run("data_convert", "sales.csv", "excel.csv", "--out-delimiter", ";", "--decimal-comma", "--bom", "--out-encoding", "utf-8", "--select", "id,amount", "--limit", "3")
    raw = (TMP / "excel.csv").read_bytes()
    ok(raw.startswith(b"\xef\xbb\xbf") and b"1;19,25" in raw.replace(b"\r", b""), f"BOM, semicolons, decimal comma ({raw[:60]!r})")
    run("data_convert", "sales.csv", "cp.csv", "--out-encoding", "cp1252", "--limit", "2")
    ok((TMP / "cp.csv").read_bytes().decode("cp1252").startswith("id,day"), "--out-encoding cp1252")
    out, _ = run("data_convert", "sales.csv", "west.jsonl.zst", "--where", "region = 'West'", "--select", "id,amount,day", "--sort", "amount DESC", "--cast", "amount=DECIMAL(10,2)", "--rename", "amount=total")
    r = run_json("data_query", "west.jsonl.zst", "--sql", "SELECT count(*) AS n, max(total) AS mx FROM west")
    ok(r.get("rows", [{}])[0].get("n") == 750, "where/select/sort/cast/rename → zstd JSONL, read back")
    run("data_convert", "messy.csv", "dedup.csv", "--dedupe")
    ok(len((TMP / "dedup.csv").read_text(encoding="utf-8").splitlines()) == 501, "--dedupe drops exact duplicates")
    run("data_convert", "messy.csv", "dates.parquet", "--cast", "signup=DATE:%Y-%m-%d|%d/%m/%Y")
    r = run_json("data_query", "dates.parquet", "--sql", "SELECT typeof(signup) AS t, count(signup) AS n FROM dates GROUP BY 1")
    ok(r.get("rows", [{}])[0] == {"t": "DATE", "n": 503}, f"--cast with two date formats ({r.get('rows')})")
    out, _ = run("data_convert", "sales.csv", str(TMP / "split" / "sales.csv"), "--split-by", "region")
    ok(sorted(p.name for p in (TMP / "split").iterdir()) == ["sales-East.csv", "sales-North.csv", "sales-South.csv", "sales-West.csv"], "--split-by")
    run("data_convert", "sales.csv", str(TMP / "chunks" / "sales.parquet"), "--split-rows", "1000")
    ok(len(list((TMP / "chunks").iterdir())) == 3, "--split-rows")
    for ext in ("arrow", "avro", "sqlite", "duckdb", "xml", "yaml", "tsv", "sav", "dta", "xpt"):
        out, _ = run("data_convert", "sales.csv", f"conv.{ext}", "--limit", "50")
        r = run_json("data_query", f"conv.{ext}", "--sql", "SELECT count(*) AS n, sum(qty) AS q FROM conv" if ext not in ("sqlite", "duckdb") else "SELECT count(*) AS n, sum(qty) AS q FROM conv.conv")
        row = (r.get("rows") or [{}])[0]
        ok(row.get("n") == 50 and row.get("q") == sum(1 + i % 20 for i in range(1, 51)), f"csv → {ext} → read back ({row})")
    for codec in ("snappy", "zstd"):
        run("data_convert", "sales.csv", f"sales-{codec}.avro", "--codec", codec)
        r = run_json("data_info", f"sales-{codec}.avro")
        ok(r.get("rows") == 3000 and (r.get("avro") or {}).get("codec") == {"zstd": "zstandard"}.get(codec, codec), f"Avro {codec} written and read ({r.get('rows')}, {r.get('avro')})")
    out, _ = run("data_convert", "survey.sav", "survey2.dta")
    r = run_json("data_query", "survey2.dta", "--labels", "--sql", "SELECT sex FROM survey2 ORDER BY id")
    ok("value labels kept" in out and [x["sex"] for x in r.get("rows", [])] == ["male", "female", None, "male"], f"SPSS → Stata keeps value labels ({r.get('rows')})")
    run("data_convert", "config.yaml", "config.json")
    ok(json.loads((TMP / "config.json").read_text(encoding="utf-8"))["server"]["port"] == 8080, "YAML → JSON document")
    run("data_convert", "config.json", "config2.toml")
    ok('port = 8080' in (TMP / "config2.toml").read_text(encoding="utf-8"), "JSON → TOML document")
    out, _ = run("data_convert", "sales.csv", "euro.csv", "--to", "parquet", "--out-dir", "batch")
    ok(sorted(p.name for p in (TMP / "batch").iterdir()) == ["euro.parquet", "sales.parquet"], "batch --out-dir")
    r = run_json("data_convert", "shop.sqlite", "shop.duckdb")
    src = run_json("data_query", "shop.sqlite", "--sql", "SELECT count(*) AS n FROM shop.orders")
    dst = run_json("data_query", "shop.duckdb", "--sql", "SELECT count(*) AS n FROM shop.orders")
    ok(r.get("mode") == "pack" and {"customers", "orders"} <= set(r.get("tables", {})) and src.get("rows") == dst.get("rows"), f"SQLite → DuckDB copies every table ({r.get('tables')})")
    r = run_json("data_convert", "sales.csv", "people.avro", "survey.sav", "pack.sqlite")
    q = run_json("data_query", "pack.sqlite", "--sql", "SELECT (SELECT count(*) FROM pack.sales) AS s, (SELECT count(*) FROM pack.people) AS p")
    ok(sorted(r.get("tables", {})) == ["people", "sales", "survey"] and (q.get("rows") or [{}])[0].get("s") == 3000, f"several inputs packed into one SQLite file ({r.get('tables')})")
    _, err = run("data_convert", "sales.csv", "euro.csv", "pack.parquet", rc=2)
    ok("--out-dir" in err, "several inputs into a flat format point to --out-dir")
    _, err = run("data_convert", "sales.csv", "sales.parquet", rc=1)
    ok("already exists" in err, "refuses to replace an output without --force")
    _, err = run("data_convert", "sales.csv", "x.xlsx", rc=2)
    ok("spreadsheets" in err, "points to the spreadsheets skill for .xlsx")


def check_validate() -> None:
    out, _ = run("data_validate", "people.jsonl", "--schema", "person.schema.json", rc=1)
    ok("INVALID" in out and "line 4" in out and "line 7" in out and "invalid JSON" in out, f"JSONL schema errors by line ({out[:300]})")
    r = run_json("data_validate", "people.jsonl", "--schema", "person.schema.json", rc=1)
    ok(r.get("records") == 10 and r.get("invalid_records") == 2, "counts of invalid records")
    run("data_validate", "nested.json", "--infer", "--out", "nested.schema.json")
    out, _ = run("data_validate", "nested.json", "--schema", "nested.schema.json")
    ok("VALID" in out and "INVALID" not in out, "inferred schema validates its data")
    out, _ = run("data_validate", "config.yaml", "--schema", '{"type": "object", "properties": {"server": {"type": "object", "properties": {"port": {"type": "string"}}}}}', rc=1)
    ok("$.server.port" in out, f"YAML error path ({out[:200]})")
    rules = {"columns": {"id": {"type": "integer", "unique": True, "not_null": True}, "email": {"regex": r"^[^@\s]+@[^@\s]+\.[a-z]+$"},
                         "score": {"type": "integer", "min": 0, "max": 100}, "city": {"allowed": ["Paris", "Lyon", "Nice"]}, "missing_col": {"required": True}},
             "unique": [["id", "email"]]}
    (TMP / "rules.json").write_text(json.dumps(rules), encoding="utf-8")
    r = run_json("data_validate", "messy.csv", "--rules", "rules.json", rc=1)
    res = {(x["column"], x["rule"].split(" ")[0]): x for x in r.get("results", [])}
    ok(res.get(("email", "regex"), {}).get("rows") == [77], f"regex violation row numbers ({res.get(('email', 'regex'))})")
    ok(res.get(("score", "max"), {}).get("rows") == [100, 200], "range violation rows")
    ok(res.get(("id", "unique"), {}).get("violations") == 2, "unique violations")
    ok(res.get(("city", "allowed"), {}).get("violations") == 202, f"allowed values: case and spaces count ({res.get(('city', 'allowed'), {}).get('violations')})")
    ok(res.get(("missing_col", "required"), {}).get("violations") == 1, "required column missing")
    out, _ = run("data_validate", "sales.csv", "--rules", '{"columns": {"id": {"type": "integer", "unique": true}}, "row_count": {"min": 1}}')
    ok("VALID" in out, "valid table")


def check_diff() -> None:
    r = run_json("data_diff", "sales.csv", "sales_v2.csv", "--key", "id")
    ok((r.get("added"), r.get("removed"), r.get("changed")) == (2, 2, 4), f"keyed diff counts ({r.get('added')}, {r.get('removed')}, {r.get('changed')})")
    ok(r.get("per_column") == {"amount": 3, "region": 1}, f"changes per column ({r.get('per_column')})")
    ok(any(c["column"] == "region" and c["old"] == "North" and c["new"] == "Central" for row in r.get("changed_rows", []) for c in row["changes"]), "old → new values")
    ok(any("channel" in s for s in r.get("schema", {}).get("only_new", [])), "schema difference (added column)")
    r = run_json("data_diff", "sales.csv", "sales_v2.csv")
    ok(r.get("key") == ["id"], "key guessed")
    run("data_diff", "sales.csv", "sales_v2.csv", "--key", "id", "--out", "changes.csv")
    lines = (TMP / "changes.csv").read_text(encoding="utf-8").splitlines()
    ok(len(lines) == 1 + 4 + 2 + 2 and lines[0].startswith("change,id,column,old,new"), f"--out one line per change ({len(lines)})")
    r = run_json("data_diff", "sales.csv", "sales_v2.csv", "--key", "id", "--tolerance", "10")
    ok(r.get("changed") == 1, "--tolerance ignores small number changes")
    r = run_json("data_diff", "sales.parquet", "sales_v2.csv", "--no-key", "--ignore", "channel")
    ok((r.get("added"), r.get("removed")) == (6, 6), f"whole-row diff across formats ({r.get('added')}, {r.get('removed')})")


def check_chart() -> None:
    jobs = [
        ("bar.png", ["--kind", "bar", "--x", "region", "--y", "amount"]),
        ("line.png", ["--kind", "line", "--x", "day", "--y", "amount", "--series", "region", "--date-unit", "month"]),
        ("scatter.png", ["--kind", "scatter", "--x", "qty", "--y", "amount", "--trend"]),
        ("hist.png", ["--kind", "hist", "--y", "amount"]),
        ("heat.png", ["--kind", "heatmap"]),
        ("donut.svg", ["--kind", "donut", "--x", "product", "--y", "qty"]),
    ]
    for name, args in jobs:
        out, _ = run("data_chart", "sales.csv", *args, "--out", name)
        p = TMP / name
        ok(p.exists() and str(p.name) in out and "view_image" in out, f"data_chart {name} written and announced")
        if name.endswith(".png") and p.exists():
            w, h = png_size(p)
            ok(0 < max(w, h) <= 1568 and min(w, h) >= 400, f"{name} sized for vision ({w}×{h})")
        if name.endswith(".svg") and p.exists():
            ok(p.read_text(encoding="utf-8").lstrip().startswith(("<?xml", "<svg")), "SVG output")
    out, _ = run("data_chart", "sales.csv", "--kind", "bar", "--x", "region", "--y", "amount", "--out", "bar2.png")
    ok("North" in out and "East" in out, "the plotted numbers are printed")
    out, _ = run("data_chart", "shop.sqlite", "--sql", "SELECT country, sum(amount) AS total FROM orders o JOIN customers c ON c.id = o.customer_id GROUP BY 1", "--kind", "barh", "--x", "country", "--y", "total", "--out", "sq.png")
    ok((TMP / "sq.png").exists(), "chart from SQL over SQLite")
    r = run_json("data_chart", "prices.csv", "--kind", "line", "--x", "Date", "--y", "Close", "--out", "prices-json.png")
    ok((TMP / r.get("path", "-")).exists() and r.get("kind") == "line" and "view_image" in r.get("hint", ""), f"--format json report ({ {k: r.get(k) for k in ('path', 'kind', 'notes')} })")
    _, err = run("data_chart", "sales.csv", "--kind", "bar", "--x", "region", "--y", "amount", "--out", "tiny.png", "--width", "50", rc=2)
    ok("--width" in err, "size limits are checked")


def check_tree() -> None:
    out, _ = run("data_tree", "nested.json")
    ok("$.data.items[*].dims.h" in out and "array[20]" in out, "outline with addresses and counts")
    out, _ = run("data_tree", "get", "nested.json", "data.items[2].name", "$..h", "--limit", "3")
    ok('$.data.items[2].name = "item 3"' in out and "$.data.items[0].dims.h = 2" in out, f"get by path and recursive descent ({out[:200]})")
    r = run_json("data_tree", "get", "nested.json", "data.items[?(@.price > 27)].id")
    ok([x["value"] for x in r] == [19, 20], f"filter expression ({r})")
    out, _ = run("data_tree", "get", "config.toml", "/servers/1/ip")
    ok('"10.0.0.2"' in out, "JSON Pointer on TOML")
    out, _ = run("data_tree", "find", "config.yaml", "logs")
    ok("$.databases[1].name" in out, "find values with addresses")
    out, _ = run("data_tree", "xpath", "feed.xml", "//d:book[@lang='fr']/d:title/text()")
    ok("Maeve Ascendant" in out, "XPath with the default namespace as d:")
    out, _ = run("data_tree", "xpath", "feed.xml", "count(//dc:creator)")
    ok(out.strip().startswith("3"), "XPath with a prefixed namespace")
    run("data_tree", "format", "nested.json", "min.json", "--minify", "--sort-keys")
    t = (TMP / "min.json").read_text(encoding="utf-8")
    ok("\n" not in t.strip() and t.index('"data"') < t.index('"meta"'), "minify and sort keys")
    run("data_tree", "convert", "feed.xml", "feed.yaml")
    y = (TMP / "feed.yaml").read_text(encoding="utf-8")
    ok("'@id': bk101" in y and "dc:creator" in y, "XML → YAML keeps attributes and prefixes")
    run("data_tree", "convert", "feed.yaml", "feed2.xml")
    out, _ = run("data_tree", "xpath", "feed2.xml", "//d:book[3]/@id")
    ok("bk103" in out, f"XML → YAML → XML round trip ({out[:200]})")
    run("data_tree", "convert", "config.yaml", "config.xml")
    run("data_tree", "convert", "app.ini", "app.json")
    ok(json.loads((TMP / "app.json").read_text(encoding="utf-8"))["db"]["port"] == "5432", "INI → JSON")
    run("data_tree", "convert", "nested.json", "nested.toml")
    ok("[[data.items]]" in (TMP / "nested.toml").read_text(encoding="utf-8"), "JSON → TOML (arrays of tables)")
    (TMP / "nulls.json").write_text('{"a": 1, "b": null, "c": {"d": null}}', encoding="utf-8")
    _, err = run("data_tree", "convert", "nulls.json", "nulls.toml", rc=1)
    ok("--drop-nulls" in err and "$.b" in err, f"TOML has no null: says where and how ({err.strip()[:200]})")
    run("data_tree", "convert", "nulls.json", "nulls.toml", "--drop-nulls")
    ok((TMP / "nulls.toml").read_text(encoding="utf-8").strip().startswith("a = 1"), "--drop-nulls")


def check_shapes() -> None:
    """Layouts found in real files: aligned columns, logs, ragged rows, text dates, schemas, dotted names."""
    r = run_json("data_info", "aligned.txt")
    ok(r.get("columns") == 3 and r.get("rows") == 3 and [c["type"] for c in r.get("schema", [])] == ["BIGINT"] * 3, f"columns aligned with spaces ({r.get('columns')} × {r.get('rows')})")
    r = run_json("data_query", "aligned.txt", "--delimiter", "lines", "--header", "yes", "--sql", "SELECT CAST(trim(substr(line, 5, 5)) AS INTEGER) AS y FROM aligned")
    ok([x["y"] for x in r.get("rows", [])] == [2, 16, 25], f"--delimiter lines: fixed-width fields cut with substr ({r.get('rows')})")
    r = run_json("data_query", "app.log", "--find", "[error]")
    ok(r.get("total") == 8 and [x["_row"] for x in r.get("rows", [])][:2] == [1, 6], f"a log reads as lines; --find gives line numbers ({[x.get('_row') for x in r.get('rows', [])][:3]})")
    r = run_json("data_query", "ragged.csv", "--sql", "SELECT a, c FROM ragged ORDER BY a")
    ok([(x["a"], x["c"]) for x in r.get("rows", [])] == [(1, 3), (4, None), (6, 8), (10, 12)], f"ragged rows padded with nulls ({r.get('rows')})")
    r = run_json("data_profile", "prices.csv", "--no-cache")
    issues = " | ".join(i["issue"] for i in r.get("issues", []))
    ok("--cast 'Date=DATE:%d-%b-%y'" in issues, f"text dates: the exact cast is suggested ({issues[:200]})")
    date = next((p for p in r.get("profiles", []) if p["name"] == "Date"), {})
    ok((date.get("min"), date.get("max")) == ("2003-07-01", "2003-09-19"), f"text dates: min/max as dates ({date.get('min')}, {date.get('max')})")
    out, _ = run("data_chart", "prices.csv", "--kind", "line", "--x", "Date", "--y", "Close", "--out", "prices.png")
    ok("2003-07-01" in out and "Close by Date" in out, f"chart reads text dates on the x axis ({out[:200]})")
    r = run_json("data_info", "warehouse.duckdb")
    names = {o["name"]: o for o in r.get("database", {}).get("objects", [])}
    ok(names.get("sales.orders", {}).get("rows") == 500 and names.get("store_totals", {}).get("type") == "view", "DuckDB file: schemas, tables, views")
    r = run_json("data_query", "warehouse.duckdb", "--sql", "SELECT count(*) AS n FROM sales.orders")
    ok(r.get("rows", [{}])[0].get("n") == 500, "DuckDB file: schema-qualified tables")
    r = run_json("data_query", "v1.2.parquet", "--tables")
    ok([t["table"] for t in r.get("tables", [])] == ["v1_2"], f"table named after the whole file stem ({r.get('tables')})")
    r = run_json("data_query", "survey.por", "--sql", "SELECT count(*) AS n FROM survey")
    ok(r.get("rows", [{}])[0].get("n") == 4, "SPSS portable (.por)")
    out, _ = run("data_tree", "xpath", "feed.xml", "//d:book[@id='bk103']/d:title")
    addr = out.strip().splitlines()[-1].split("  ")[0] if out.strip() else ""
    out2, _ = run("data_tree", "xpath", "feed.xml", f"{addr}/text()")
    ok(addr == "/d:catalog/d:book[3]/d:title" and "Maeve Ascendant" in out2, f"XPath results carry reusable addresses ({addr})")


def check_big() -> None:
    small = {"DESK_DATA_BIG_MB": "2", "DESK_DATA_STREAM_MB": "1"}
    cold = timed(lambda: run("data_info", "big.csv", env=small))
    out, _ = run("data_info", "big.csv", env=small)
    ok("300,000 rows" in out and "cached Parquet copy" in out and "Rows 1-5 and 299998-300000" in out, f"big CSV: map from the cached Parquet copy, first and last rows ({out[:400]})")
    ok("--rows 6-105" in out, "big CSV: next-part command")
    t_cold = timed(lambda: run("data_profile", "big.csv", "--no-cache", env=small))
    ok(run_json("data_profile", "big.csv", env=small).get("cached") is False, "the first profile is computed")
    ok(run_json("data_profile", "big.csv", env=small).get("cached") is True, "the second profile comes from the cache")
    t_hot = min(timed(lambda: run("data_profile", "big.csv", env=small)) for _ in range(3))  # a busy machine only adds time
    # Mostly process start-up once cached, so the ratio is modest when the cold profile is quick.
    ok(t_hot * 3 <= t_cold, f"cached profile at least 3× faster ({t_cold:.2f}s → {t_hot:.2f}s)")
    r = run_json("data_query", "big.csv", "--sql", "SELECT count(*) AS n, count(DISTINCT region) AS r FROM big", env=small)
    ok(r.get("rows", [{}])[0] == {"n": 300000, "r": 4} and any("cached Parquet" in n for n in r.get("notes", [])), "query uses the cached copy")
    r = run_json("data_query", "big.csv", "--sql", "SELECT count(*) FILTER (WHERE r != id) AS bad FROM (SELECT row_number() OVER () AS r, id FROM big)", env=small)
    ok(r.get("rows", [{}])[0].get("bad") == 0, "row numbers follow the file order")
    r = run_json("data_query", "big.csv", "--no-cache", "--sql", "SELECT sum(qty) AS q FROM big", env=small)
    ok(r.get("rows", [{}])[0].get("q") == sum(i % 50 for i in range(1, 300_001)), "--no-cache gives the same answer")
    t1 = timed(lambda: run("data_tree", "outline", "big.json", env=small))
    out, _ = run("data_tree", "outline", "big.json", env=small)
    t2 = timed(lambda: run("data_tree", "outline", "big.json", env=small))
    ok("streamed" in out and "$.data.records[*].user.age" in out and "array[30000]" in out, f"big JSON outline streamed ({out[:300]})")
    ok(t2 < t1, f"outline cached ({t1:.2f}s → {t2:.2f}s)")
    out, _ = run("data_tree", "get", "big.json", "data.records[29999].user.name", env=small)
    ok('"user29999"' not in out and '= "user' in out and "$.data.records[29999].user.name" in out, "streamed get by index")
    r = run_json("data_tree", "get", "big.json", "data.records[*].id", "--limit", "5", "--offset", "10", env=small)
    ok([x["value"] for x in r] == [10, 11, 12, 13, 14], "streamed get with paging")
    out, _ = run("data_tree", "find", "big.json", "user996", "--values", "--limit", "3", env=small)
    ok("$.data.records[996].user.name" in out, "streamed find with addresses")
    r = run_json("data_query", "big.json", "--sql", "SELECT count(*) AS n, max(user.age) AS a FROM big", env=small)
    ok(r.get("rows", [{}])[0] == {"n": 30000, "a": 77}, "records inside a big JSON document streamed into SQL")


def check_quirks() -> None:
    """Things real files do: YAML 1.1 booleans, byte order marks, JSON with comments, Arrow inputs everywhere, Stata
    4-byte floats, XML elements that are only attributes, JSON values inside nested lists, big JSONL checked in
    parallel, schemas inferred from a sample of a big table."""
    (TMP / "workflow.yml").write_text("name: CI\non:\n  push:\n    branches: [main]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    continue-on-error: yes\n    enabled: true\n", encoding="utf-8")
    r = run_json("data_tree", "get", "workflow.yml", "on.push.branches[0]")
    ok([x.get("value") for x in r] == ["main"], f"YAML: `on:` stays a key, not true ({r})")
    r = run_json("data_tree", "get", "workflow.yml", "jobs.test")
    val = r[0].get("value") if r else {}
    ok(val.get("continue-on-error") == "yes" and val.get("enabled") is True, f"YAML 1.2 booleans: yes stays text, true is a boolean ({val})")
    (TMP / "bom.json").write_bytes(b"\xef\xbb\xbf" + json.dumps([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]).encode())
    (TMP / "bom.jsonl").write_bytes(b"\xef\xbb\xbf" + b'{"a": 1}\n{"a": 2}\n{"a": 3}\n')
    r = run_json("data_query", "bom.json", "--sql", "SELECT sum(a) AS s, count(*) AS n FROM bom")
    ok(r.get("rows") == [{"s": 3, "n": 2}], f"JSON array with a byte order mark ({r.get('rows')})")
    r = run_json("data_query", "bom.jsonl", "--sql", "SELECT sum(a) AS s FROM bom")
    ok(r.get("rows") == [{"s": 6}], f"JSONL with a byte order mark ({r.get('rows')})")
    out, _ = run("data_tree", "outline", "bom.json", "--stream")
    ok("$[*].b  string" in out, f"streamed outline skips a byte order mark ({out[:200]})")
    (TMP / "tsconfig.json").write_text('{\n  // compiler\n  "compilerOptions": {"target": "ES2022", /* c */ "paths": {"@/*": ["src/*"]},},\n  "include": ["src/**/*.ts",],\n}\n', encoding="utf-8")
    r = run_json("data_tree", "get", "tsconfig.json", "compilerOptions.paths['@/*'][0]")
    ok([x.get("value") for x in r] == ["src/*"], f"JSON with comments and trailing commas (JSONC) ({r})")
    run("data_convert", "tsconfig.json", "tsconfig.yaml")
    ok("target: ES2022" in (TMP / "tsconfig.yaml").read_text(encoding="utf-8"), "JSONC converts to YAML")
    import pyarrow as pa
    import pyarrow.feather as feather

    feather.write_feather(pa.table({"id": [1, 2, 3], "name": ["a", "b", None], "when": pa.array([dt.datetime(2024, 1, 1, 12, tzinfo=dt.timezone.utc)] * 3, type=pa.timestamp("us", tz="UTC"))}), str(TMP / "frame.feather"))
    out, _ = run("data_validate", "frame.feather", "--infer")
    ok('"id"' in out and '"required"' in out, f"schema inferred from an Arrow file ({out[:200]})")
    run("data_convert", "frame.feather", "frame.sqlite")
    con = sqlite3.connect(str(TMP / "frame.sqlite"))
    ok(con.execute("SELECT count(*), max(id) FROM frame").fetchone() == (3, 3), "Arrow file → SQLite")
    con.close()
    run("data_convert", "frame.feather", "frame.xml")
    ok((TMP / "frame.xml").read_text(encoding="utf-8").count("<row>") == 3, "Arrow file → XML")
    r = run_json("data_query", "frame.feather", "--sql", 'SELECT "when" AS w FROM frame LIMIT 1')
    ok(str((r.get("rows") or [{}])[0].get("w", "")).startswith("2024-01-01"), f"time zone timestamps print ({r.get('rows')})")
    import pandas as pd
    import pyreadstat

    pyreadstat.write_dta(pd.DataFrame({"make": ["A", "B"], "ratio": pd.Series([3.58, 2.53], dtype="float32")}), str(TMP / "cars.dta"))
    r = run_json("data_query", "cars.dta", "--sql", "SELECT ratio FROM cars ORDER BY make")
    ok([x["ratio"] for x in r.get("rows", [])] == [3.58, 2.53], f"Stata float keeps its decimals ({r.get('rows')})")
    (TMP / "feed.atom").write_text('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">' + "".join(f'<entry><title>t{i}</title><link rel="alternate" href="https://x.org/{i}"/></entry>' for i in range(3)) + "</feed>", encoding="utf-8")
    r = run_json("data_info", "feed.atom")
    ok([c["name"] for c in r.get("schema", [])] == ["title", "link.rel", "link.href"], f"XML: an element with only attributes adds no empty column ({[c['name'] for c in r.get('schema', [])]})")
    (TMP / "shapes.json").write_text(json.dumps({"features": [{"id": 1, "geometry": {"type": "Point", "coordinates": [[2.35, 48.85], [4.83, 45.76, 170]]}}]}), encoding="utf-8")
    out, _ = run("data_query", "shapes.json")
    ok('"coordinates":[[2.35,48.85],[4.83,45.76,170' in out.replace(" ", ""), f"JSON values inside nested lists print as numbers ({out[:300]})")
    r = run_json("data_profile", "sales.csv", "--no-cache", "--columns", "qty")
    # qty cycles 1..20: an IQR, no outliers; a column that is mostly one value (IQR 0) gets none either
    (TMP / "counts.csv").write_text("n\n" + "0\n" * 90 + "1\n2\n3\n1\n5\n0\n0\n0\n0\n7\n", encoding="utf-8")
    r = run_json("data_profile", "counts.csv", "--no-cache")
    prof = (r.get("profiles") or [{}])[0]
    ok(prof.get("outliers") == 0 and not any("outlier" in i["issue"] for i in r.get("issues", [])), f"IQR 0: no outliers reported ({prof.get('outliers')})")
    # Big JSONL checked in parallel (threshold scaled down), identical to the serial check, errors grouped by kind.
    rows = [json.dumps({"id": i, "amount": ("x" if i % 50 == 7 else i * 1.5), "tag": "t"}) for i in range(12000)]
    rows[9000] = "{broken"
    (TMP / "many.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (TMP / "many.schema.json").write_text(json.dumps({"type": "object", "properties": {"id": {"type": "integer"}, "amount": {"type": "number"}}, "required": ["id", "amount"]}), encoding="utf-8")
    par = run_json("data_validate", "many.jsonl", "--schema", "many.schema.json", rc=1, env={"DESK_DATA_BIG_MB": "0.5"})
    ser = run_json("data_validate", "many.jsonl", "--schema", "many.schema.json", rc=1)
    ok(par.get("workers", 0) >= 1 and "workers" not in ser, f"big JSONL validated in parallel ranges ({par.get('workers')})")
    same = (par.get("errors_total"), par.get("invalid_records"), [e["where"] for e in par.get("errors", [])][:6]) == (ser.get("errors_total"), ser.get("invalid_records"), [e["where"] for e in ser.get("errors", [])][:6])
    ok(same and par.get("errors_total") == 241, f"parallel = serial: counts and line numbers ({par.get('errors_total')}, {[e['where'] for e in par.get('errors', [])][:3]})")
    kinds = {(k["path"], k["keyword"]): k for k in par.get("error_kinds", [])}
    ok(kinds.get(("$.amount", "type"), {}).get("count") == 240 and kinds.get(("$", "json"), {}).get("first") == ["line 9001"], f"errors grouped by kind with first lines ({list(kinds)[:3]})")
    out, _ = run("data_validate", "many.jsonl", "--schema", "many.schema.json", rc=1)
    ok("Errors by kind" in out and "| $.amount | type | 240 |" in out, f"Markdown report groups errors ({out[:300]})")
    run("data_validate", "many.jsonl", "--infer", "--strict", "--out", "many.inferred.json", env={"DESK_DATA_BIG_MB": "0.5"})
    sch = json.loads((TMP / "many.inferred.json").read_text(encoding="utf-8"))
    ok(sch.get("properties", {}).get("id", {}).get("maximum") == 11999, f"parallel JSONL inference sees every line ({sch.get('properties', {}).get('id')})")
    out, _ = run("data_validate", "big.csv", "--infer", "--strict", env={"DESK_DATA_BIG_MB": "2"})
    sch = json.loads(out[out.index("{"):])
    ok("random sample of 50,000" in sch.get("title", "") and sch["properties"]["id"].get("maximum") == 300000 and "id" in sch.get("required", []), f"table schema from a sample, ranges from every row ({sch.get('title')}, {sch['properties'].get('id')})")
    # Streamed find: a raw byte scan answers "absent" only when the file cannot spell the text another way.
    (TMP / "esc.json").write_text('{"items": [{"name": "x\\u0041y", "n": 1e5, "t": "plain"}]}', encoding="utf-8")
    hits = run_json("data_tree", "find", "esc.json", "xAy", "--stream")
    ok([h["path"] for h in hits] == ["$.items[0].name"], f"streamed find sees text written with \\u escapes ({hits})")
    hits = run_json("data_tree", "find", "esc.json", "100000", "--stream", "--values")
    ok([h["path"] for h in hits] == ["$.items[0].n"], f"streamed find matches numbers as printed ({hits})")
    out, _ = run("data_tree", "find", "big.json", "nosuchvalue42", env={"DESK_DATA_STREAM_MB": "1"})
    ok("no match for 'nosuchvalue42'" in out, f"streamed find: absent text ({out[:200]})")
    for i in (1, 2):
        (TMP / f"part_{i}.csv").write_text(f"k,v\n{i},{i * 10}\n", encoding="utf-8")
    r = run_json("data_info", "part_*.csv")
    ok([x.get("rows") for x in (r if isinstance(r, list) else [r])] == [1, 1], f"data_info expands a quoted glob ({str(r)[:200]})")
    run("data_info", "nothing_*.csv", rc=1)
    (TMP / "halves.csv").write_text("n\n1\n7.5\n3\n", encoding="utf-8")
    out, _ = run("data_convert", "halves.csv", "halves.parquet", "--cast", "n=INTEGER")
    ok("1 value of n had decimals and was rounded" in out and "7.5" in out, f"integer cast says it rounded ({out[:300]})")
    (TMP / "keys.json").write_text(json.dumps({"a b": 1, "ok": 2, "1st": 3}), encoding="utf-8")
    out, _ = run("data_convert", "keys.json", "keys.xml")
    ok("'a b'" in out and "'1st'" in out and "'ok'" not in out, f"XML note lists only the adjusted keys ({out[:300]})")
    (TMP / "cities.csv").write_text("city,sales\n東京,120\n大阪,95\nParis,50\n", encoding="utf-8")
    out, err = run("data_chart", "cities.csv", "--kind", "bar", "--x", "city", "--y", "sales", "--out", "cities.png")
    ok("missing from font" not in err and png_size(TMP / "cities.png") == (1568, 980), f"CJK labels: no missing-glyph warnings ({err[:200]})")


def check_hostile() -> None:
    """Inputs built to hurt: YAML alias bombs and cycles, JSON nested thousands deep, decompression bombs, xz/zstd
    headers declaring huge dictionaries. Each is refused quickly with one clear line, never decoded."""
    lines = ['a: &a ["lol","lol","lol","lol","lol","lol","lol","lol","lol"]']
    prev = "a"
    for c in "bcdefghi":
        lines.append(f"{c}: &{c} [" + ",".join(["*" + prev] * 9) + "]")
        prev = c
    (TMP / "laughs.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for args in (("data_info", "laughs.yaml"), ("data_tree", "outline", "laughs.yaml"), ("data_convert", "laughs.yaml", "laughs.json")):
        t = time.time()
        _, err = run(*args, rc=1)
        ok("alias bomb" in err and time.time() - t < 10, f"{args[0]}: YAML billion laughs refused ({err.strip()[-160:]})")
    ok(not (TMP / "laughs.json").exists(), "no output from a refused YAML bomb")
    (TMP / "cycle.yaml").write_text("a: &x\n  b: *x\n", encoding="utf-8")
    _, err = run("data_info", "cycle.yaml", rc=1)
    ok("refers to the node that contains it" in err, f"recursive YAML alias refused ({err.strip()[-160:]})")
    (TMP / "anchors.yaml").write_text("base: &b {x: 1, y: 2}\nitems:\n  - *b\n  - *b\n  - {<<: *b, z: 3}\n", encoding="utf-8")
    run("data_convert", "anchors.yaml", "anchors.json")
    doc = json.loads((TMP / "anchors.json").read_text(encoding="utf-8"))
    ok(doc["items"][2] == {"x": 1, "y": 2, "z": 3} and doc["items"][0] == {"x": 1, "y": 2}, f"ordinary anchors, aliases and merge keys still read ({doc})")
    (TMP / "deep.json").write_text("[" * 20000 + "]" * 20000, encoding="utf-8")
    for script in ("data_info", "data_query"):
        _, err = run(script, "deep.json", rc=1)
        ok("nested" in err and "Error" not in err, f"{script}: JSON nested 20,000 deep refused cleanly, no crash ({err.strip()[-160:]})")
    # Compressed bombs: 6 MB of one repeated line; the ratio limit applies from 1 MB here instead of 256 MB.
    import bz2
    import lzma

    import pyarrow as pa

    body = b"id,text,day\n" + b"12345,hello world,2024-01-01\n" * (6 * 1024 * 1024 // 29)
    (TMP / "bomb.csv.gz").write_bytes(gzip.compress(body, 6))
    (TMP / "bomb.csv.bz2").write_bytes(bz2.compress(body, 1))
    (TMP / "bomb.csv.xz").write_bytes(lzma.compress(body, preset=0))
    with pa.CompressedOutputStream(str(TMP / "bomb.csv.zst"), "zstd") as w:
        w.write(body)
    low = {"DESK_DATA_RATIO_FLOOR_MB": "1"}
    for name in ("bomb.csv.gz", "bomb.csv.bz2", "bomb.csv.xz", "bomb.csv.zst"):
        t = time.time()
        _, err = run("data_query", name, "--sql", "SELECT count(*) FROM bomb", rc=1, env=low)
        ok("decompression bomb" in err and "DESK_DATA_MAX_RATIO" in err and time.time() - t < 5, f"{name}: refused before decoding ({err.strip()[-200:]})")
    r = run_json("data_query", "bomb.csv.bz2", "--sql", "SELECT count(*) AS n FROM bomb", env={**low, "DESK_DATA_MAX_RATIO": "100000"})
    ok(r.get("rows") == [{"n": 6 * 1024 * 1024 // 29}], f"a raised DESK_DATA_MAX_RATIO reads the same file ({r.get('rows')})")
    small = b"id,name\n" + b"".join(b"%d,name%d\n" % (i, i) for i in range(1000))
    (TMP / "fine.csv.xz").write_bytes(lzma.compress(small))
    with pa.CompressedOutputStream(str(TMP / "fine.csv.zst"), "zstd") as w:
        w.write(small)
    (TMP / "fine.csv.bz2").write_bytes(bz2.compress(small))
    for name in ("fine.csv.xz", "fine.csv.zst", "fine.csv.bz2"):
        r = run_json("data_query", name, "--sql", "SELECT count(*) AS n, sum(id) AS s FROM fine")
        ok(r.get("rows") == [{"n": 1000, "s": 499500}], f"{name}: an ordinary compressed CSV reads ({r.get('rows')})")
    # An xz block header declaring a 3 GiB dictionary, a zstd frame declaring a 2 GiB window.
    import binascii
    import struct

    x = bytearray(lzma.compress(small, format=lzma.FORMAT_XZ, filters=[{"id": lzma.FILTER_LZMA2, "dict_size": 1 << 20}]))
    hs = (x[12] + 1) * 4
    hdr = x[12 : 12 + hs - 4]
    hdr[hdr.find(b"\x21\x01") + 2] = 39
    x[12 : 12 + hs - 4] = hdr
    x[12 + hs - 4 : 12 + hs] = struct.pack("<I", binascii.crc32(bytes(hdr)) & 0xFFFFFFFF)
    (TMP / "hugedict.csv.xz").write_bytes(bytes(x))
    raw = small[:1000]
    (TMP / "hugewindow.csv.zst").write_bytes(b"\x28\xb5\x2f\xfd" + bytes([0x00, (31 - 10) << 3]) + ((len(raw) << 3) | 1).to_bytes(3, "little") + raw)
    _, err = run("data_info", "hugedict.csv.xz", rc=1)
    ok("declares a 3.0 GB dictionary" in err and "DESK_ARC_MAX_DICT_MB" in err, f"xz with a 3 GiB dictionary refused ({err.strip()[-160:]})")
    _, err = run("data_info", "hugewindow.csv.zst", rc=1)
    ok("declares a 2.0 GB window" in err, f"zstd with a 2 GiB window refused ({err.strip()[-160:]})")
    # The byte counter stops a stream whose real size is past the limit even when the size check was fooled.
    sys.path.insert(0, str(HERE))
    os.environ["DESK_FILE_CACHE"] = str(TMP / "cache")
    import _formats
    from _common import SkillError

    _formats.allow_pandas()  # importing _formats defers pandas; this process still needs it
    _formats.RATIO_FLOOR = 1024 * 1024
    os.environ["DESK_DATA_MAX_RATIO"] = "2"
    st = (TMP / "bomb.csv.bz2").stat()
    _formats._CHECKED[(str(TMP / "bomb.csv.bz2"), st.st_size, st.st_mtime_ns)] = {"size": 1, "exact": True}
    stopped = False
    try:
        with _formats.open_decompressed(TMP / "bomb.csv.bz2", "bz2") as f:
            while f.read(1 << 20):
                pass
    except SkillError as e:
        stopped = "inflates past 1.0 MB" in str(e)
    finally:
        os.environ.pop("DESK_DATA_MAX_RATIO", None)
    ok(stopped, "a stream that lies about its size is stopped while it is read")
    # Big means big after decompression: a small .gz holding a larger CSV gets the cached Parquet copy.
    with gzip.open(TMP / "roomy.csv.gz", "wt", encoding="utf-8", newline="") as f:
        f.write("id,v\n" + "".join(f"{i},{i % 7}\n" for i in range(150000)))
    r = run_json("data_info", "roomy.csv.gz", env={"DESK_DATA_BIG_MB": "1"})
    ok(r.get("rows") == 150000 and any("Parquet" in n for n in r.get("notes", [])), f"compressed input: big by its decompressed size ({r.get('notes')})")
    # Format sniffing matches each line of the sample: one long line of spaces once took hours (cubic INI regex).
    kinds = (_formats._text_format("[db]\nhost = x\nport = 5\n", ".conf"), _formats._text_format("name: x\nitems:\n  - a\n", ""))
    t = time.time()
    for text in ("[a]\n" + " " * 60000 + "x\n", " " * 60000 + "x\n" + "k: v\n", "[a]\n" + "a " * 30000 + "!\n"):
        _formats._text_format(text, "")
    ok(kinds == ("ini", "yaml") and time.time() - t < 2, f"format sniffing is linear on a long blank line ({time.time() - t:.2f}s, {kinds})")


def check_fixes() -> None:
    """Regressions from acceptance testing: CSV booleans and empty strings, SPSS missing codes and dates, SQLite
    types, document diffs, chart layout, paging of finds, and messages."""
    (TMP / "flags.csv").write_text('id,alive,adult,t,note\n1,yes,True,t,""\n2,no,False,f,\n3,yes,True,t,x\n', encoding="utf-8")
    r = run_json("data_info", "flags.csv")
    types = {c["name"]: c["type"] for c in r.get("schema", [])}
    ok(types.get("alive") == "VARCHAR" and types.get("t") == "VARCHAR" and types.get("adult") == "BOOLEAN", f"yes/no and t/f stay text, True/False is BOOLEAN ({types})")
    ok(any("adult (True/False)" in n for n in r.get("notes", [])), f"a note says True/False is written back lower case ({r.get('notes')})")
    run("data_convert", "flags.csv", "flags_copy.csv")
    ok((TMP / "flags_copy.csv").read_text(encoding="utf-8").splitlines()[1] == '1,yes,true,t,""', f"CSV → CSV keeps yes/no and \"\" ({(TMP / 'flags_copy.csv').read_text(encoding='utf-8')[:120]!r})")
    r = run_json("data_query", "flags.csv", "--sql", "SELECT note IS NULL AS n FROM flags ORDER BY id")
    ok([x["n"] for x in r.get("rows", [])] == [False, True, False], f'quoted "" is an empty string, an empty field is null ({r.get("rows")})')
    import pandas as pd
    import pyreadstat

    df = pd.DataFrame({"score": [1.5, -1.0, 2500.0, 3.0], "grp": [1.0, 2.0, -1.0, 1.0], "day": [dt.date(2024, 1, 2), dt.date(2024, 2, 3), None, dt.date(2024, 3, 4)]})
    pyreadstat.write_sav(df, str(TMP / "miss.sav"), missing_ranges={"score": [-1.0, {"lo": 2000.0, "hi": 3000.0}], "grp": [-1.0]}, variable_value_labels={"grp": {1: "a", 2: "b", -1: "refused"}})
    r = run_json("data_info", "miss.sav")
    sch = {c["name"]: c for c in r.get("schema", [])}
    ok(set(sch.get("score", {}).get("missing codes", "").split(", ")) == {"-1", "2000 to 3000"} and sch.get("day", {}).get("format", "").startswith(("DATE", "EDATE", "ADATE")), f"SPSS missing codes and formats in the map ({sch.get('score')}, {sch.get('day')})")
    ok(sch.get("score", {}).get("nulls") == 2 and any("user-missing codes read as null" in n for n in r.get("notes", [])), f"user-missing values read as null, with a note ({r.get('notes')})")
    r = run_json("data_query", "miss.sav", "--user-missing", "--sql", "SELECT score FROM miss ORDER BY score")
    ok([x["score"] for x in r.get("rows", [])] == [-1.0, 1.5, 3.0, 2500.0], f"--user-missing keeps the codes ({r.get('rows')})")
    run("data_convert", "miss.sav", "miss2.sav")
    _, m = pyreadstat.read_sav(str(TMP / "miss2.sav"), metadataonly=True, user_missing=True)
    ok(sorted((x["lo"], x["hi"]) for x in m.missing_ranges.get("score", [])) == [(-1.0, -1.0), (2000.0, 3000.0)], f".sav → .sav keeps the missing ranges ({m.missing_ranges})")
    out, _ = run("data_convert", "miss.sav", "miss.dta")
    _, m = pyreadstat.read_dta(str(TMP / "miss.dta"), metadataonly=True)
    ok(m.original_variable_types.get("day") == "%td" and "read as null" in out, f".dta: a DATE stays a date (%td), and the note says codes became null ({m.original_variable_types}, {out[:200]})")
    con = sqlite3.connect(str(TMP / "money.sqlite"))
    con.execute("CREATE TABLE inv (id INTEGER PRIMARY KEY, total NUMERIC(10,2), ts DATETIME, day DATE, odd DATETIME)")
    con.executemany("INSERT INTO inv VALUES (?,?,?,?,?)", [(1, 1.98, "2021-01-01 00:00:00", "2021-01-01", "yesterday"), (2, 13.86, "2021-01-02 10:30:00", "2021-01-02", None)])
    con.commit()
    con.close()
    r = run_json("data_query", "money.sqlite", "--sql", "SELECT typeof(total) AS a, typeof(ts) AS b, typeof(day) AS c, typeof(odd) AS d, sum(total) OVER () AS s FROM inv LIMIT 1")
    row = (r.get("rows") or [{}])[0]
    ok((row.get("a"), row.get("b"), row.get("c"), row.get("d")) == ("DECIMAL(10,2)", "TIMESTAMP", "DATE", "VARCHAR") and str(row.get("s")) == "15.84", f"SQLite declared DECIMAL/DATETIME/DATE kept; odd values stay text ({row})")
    # Documents compare structurally, whatever part of them changed.
    base = "name: Python package\non:\n  push:\n    branches: [main]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - name: Test\n        run: pytest\n"
    (TMP / "wf.yml").write_text(base, encoding="utf-8")
    (TMP / "wf2.yml").write_text(base.replace("Python package", "Python CHANGED").replace("ubuntu-latest", "windows-latest").replace("      - name: Test", "      - name: Lint\n        run: ruff .\n      - name: Test") + "env:\n  X: 1\n", encoding="utf-8")
    r = run_json("data_diff", "wf.yml", "wf2.yml")
    got = {(c["change"], c["path"]) for c in r.get("changes", [])}
    want = {("changed", "$.name"), ("changed", "$.jobs.build.runs-on"), ("added", "$.jobs.build.steps[1]"), ("added", "$.env")}
    ok(r.get("mode") == "doc" and got == want, f"YAML documents: every changed path, an inserted step is one addition ({sorted(got)})")
    r = run_json("data_diff", "wf.yml", "wf.yml")
    ok(r.get("total") == 0, "identical documents: no changes")
    out, _ = run("data_diff", "wf.yml", "wf2.yml", "--mode", "table")
    ok("only the records at $.jobs.build.steps were compared" in out and "--mode doc" in out, f"table mode says what it left out ({out[:300]})")
    (TMP / "cfg.toml").write_text('[server]\nport = 8080\nhosts = ["a", "b"]\n[[users]]\nid = 1\nrole = "admin"\n[[users]]\nid = 2\nrole = "dev"\n', encoding="utf-8")
    (TMP / "cfg2.toml").write_text('[server]\nport = 9090\nhosts = ["a", "b"]\n[[users]]\nid = 2\nrole = "ops"\n[[users]]\nid = 1\nrole = "admin"\n', encoding="utf-8")
    r = run_json("data_diff", "cfg.toml", "cfg2.toml")
    got = {(c["change"], c["path"], c.get("old"), c.get("new")) for c in r.get("changes", []) if c["change"] != "reordered"}
    ok(got == {("changed", "$.server.port", 8080, 9090), ("changed", "$.users[0].role", "dev", "ops")}, f"TOML: arrays of tables matched by id, not position ({sorted(map(str, got))})")
    # Charts: a legend of two rows sits under the subtitle, reads row by row, and one extra series is not folded.
    rows = [{"year": y, "country": c, "gdp": (i + 1) * 100 + y - 2000} for i, c in enumerate(["US", "CN", "JP", "DE", "IN", "GB", "FR", "IT", "CA"]) for y in range(2000, 2010)]
    write_csv(TMP / "gdp.csv", rows)
    r = run_json("data_chart", "gdp.csv", "--kind", "line", "--x", "year", "--y", "gdp", "--series", "country", "--where", "country NOT IN ('CN', 'IN')", "--title", "G7", "--subtitle", "Current US dollars", "--out", "g7.png")
    ok(r.get("layout_overlaps") == [] and png_size(TMP / "g7.png") == (1568, 980), f"two-row legend and subtitle do not overlap ({r.get('layout_overlaps')})")
    r = run_json("data_chart", "gdp.csv", "--kind", "line", "--x", "year", "--y", "gdp", "--series", "country", "--out", "g9.png")
    ok(not any("Other" in n for n in r.get("notes", [])) and "CA=" in r.get("summary", ""), f"a ninth series is drawn, not folded into Other ({r.get('notes')})")
    write_csv(TMP / "daily.csv", [{"day": (dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat(), "n": 5} for i in range(63)])
    r = run_json("data_chart", "daily.csv", "--kind", "area", "--x", "day", "--y", "n", "--date-unit", "month", "--out", "daily.png")
    ok(any("the last month (2024-03) is partial: the data ends 2024-03-03" in n for n in r.get("notes", [])), f"a partial last month is flagged ({r.get('notes')})")
    # Paging a find in a document; a --find with no hit says how much was searched.
    (TMP / "hits.json").write_text(json.dumps({"items": [{"tag": f"hit{i}"} for i in range(12)], "meta": {"tag": "hit-meta"}}), encoding="utf-8")
    out, _ = run("data_tree", "find", "hits.json", "hit", "--values", "--limit", "5")
    ok("matches 1-5 (more follow)" in out and "--offset 5" in out, f"find ends with the next page's command ({out[-200:]})")
    out, _ = run("data_tree", "find", "hits.json", "hit", "--values", "--limit", "5", "--offset", "10")
    ok("$.items[10].tag" in out and "$.meta.tag" in out and "Next:" not in out, f"find --offset reads the last page ({out[-300:]})")
    out, _ = run("data_query", "hits.json", "--find", "hit-meta")
    ok("no row contains 'hit-meta' (searched 12 rows" in out and "data_tree.py find" in out, f"--find with no hit: rows searched and where else to look ({out[-400:]})")
    (TMP / "photo.csv").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8)
    _, err = run("data_info", "photo.csv", rc=1)
    ok("is a PNG image" in err and "images skill" in err, f"a PNG named .csv is refused with the skill to use ({err.strip()})")
    (TMP / "id3.csv").write_text("ID3,name\n1,a\n", encoding="utf-8")
    r = run_json("data_query", "id3.csv")
    ok(r.get("columns") == ["ID3", "name"], f"a CSV that starts like a magic number still reads ({r.get('columns')})")
    _, err = run("data_query", "flags.csv", "--sql", "COPY flags TO 'x.csv'", rc=2)
    ok("COPY statements are refused" in err, f"refusal message ({err.strip()})")
    # data_info: a column with one value in thousands is not '100%' null; a cut map ends with the command for all.
    with open(TMP / "sparse.csv", "w", encoding="utf-8", newline="") as f:
        f.write("id,note\n" + "".join(f"{i},{'x' if i == 4000 else ''}\n" for i in range(6000)))
    out, _ = run("data_info", "sparse.csv")
    ok("| note | VARCHAR | all but 1 |" in out, f"null share of a nearly empty column ({[ln for ln in out.splitlines() if 'note' in ln][:1]})")
    out, _ = run("data_info", "sparse.csv", "--max-chars", "400")
    ok("Everything: python3 scripts/data_info.py" in out and "--max-chars" in out.split("Everything:")[-1], f"a truncated map gives the command for the rest ({out[-200:]})")
    (TMP / "rc.yaml").write_text("row_count: {min: 10}\ncolumns:\n  id: {type: integer}\n", encoding="utf-8")
    out, _ = run("data_validate", "flags.csv", "--rules", "rc.yaml", rc=1)
    ok("row count at least 10" in out and "{'min'" not in out, f"row_count rule in words ({[ln for ln in out.splitlines() if 'row count' in ln][:1]})")


def main() -> int:
    fixtures()
    for fn in (check_info, check_query, check_profile, check_convert, check_validate, check_diff, check_chart, check_tree, check_shapes, check_big, check_quirks, check_hostile, check_fixes):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — a crash in one group must not hide the others
            fail(f"{fn.__name__} crashed: {type(e).__name__}: {e}")
    secs = time.time() - T0
    if os.environ.get("DESK_DEBUG"):
        for t, what in sorted(TIMES, reverse=True)[:25]:
            print(f"{t:6.2f}s  {what}", file=sys.stderr)
        print(f"{len(TIMES)} runs, {sum(t for t, _ in TIMES):.1f}s in scripts", file=sys.stderr)
    import shutil

    shutil.rmtree(TMP, ignore_errors=True)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} of {CHECKS} checks failed in {secs:.1f}s", file=sys.stderr)
        return 1
    print(f"ok: {CHECKS} checks in {secs:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
