"""Nested documents (JSON, JSONL, YAML, TOML, INI, XML) for the data-files skill.

Loading and dumping, a path language with stable addresses ($.a.b[0], wildcards, recursive descent, filters, JSON
Pointer), outlines (types, counts, key presence), search, the XML <-> object mapping, and streaming (ijson)
versions of outline / select / search for JSON files too big to load.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from _common import SkillError, UsageError

# ── loading and dumping ─────────────────────────────────────────────────


def read_text(path: Path, encoding: str | None = None) -> str:
    from _formats import detect_encoding, open_decompressed, split_ext, magic_compression

    with open(path, "rb") as f:
        head = f.read(8)
    comp = magic_compression(head) or split_ext(path)[1]
    with open_decompressed(path, comp) as f:
        data = f.read()
    enc = encoding or detect_encoding(data[: 1024 * 1024], complete=len(data) <= 1024 * 1024)["encoding"]
    text = data.decode(enc, errors="replace")
    return text[1:] if text.startswith("\ufeff") else text


def load_doc(path: Path, fmt: str, encoding: str | None = None) -> Any:
    """Parses a whole document into Python objects (XML via the @attr/#text mapping)."""
    if fmt == "xml":
        from lxml import etree

        try:
            tree = etree.parse(str(path), _xml_parser())
        except etree.XMLSyntaxError as e:
            raise SkillError(f"{path.name}: invalid XML: {e}") from None
        root = tree.getroot()
        return {qname(root): xml_to_obj(root, {})}
    text = read_text(path, encoding)
    return parse_text(text, fmt, path.name)


def strip_jsonc(text: str) -> str:
    """JSON with comments (tsconfig.json, VS Code settings): // and /* */ comments and trailing commas removed,
    strings left alone. Line breaks inside comments are kept, so error line numbers still match the file."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("\n" * text.count("\n", i, j))
            i = j
        elif ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1  # a trailing comma
            else:
                out.append(ch)
                i += 1
        else:
            j = i
            while j < n and text[j] not in '"/,':
                j += 1
            if j == i:  # a lone slash
                j += 1
            out.append(text[i:j])
            i = j
    return "".join(out)


def parse_text(text: str, fmt: str, name: str = "input") -> Any:
    try:
        if fmt == "json":
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                if "//" not in text and "/*" not in text and not re.search(r",\s*[}\]]", text):
                    raise
                try:
                    return json.loads(strip_jsonc(text))  # JSON with comments or trailing commas (JSONC)
                except json.JSONDecodeError:
                    pass
                raise
        if fmt == "jsonl":
            out = []
            for i, line in enumerate(text.splitlines(), 1):
                if line.strip():
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        raise SkillError(f"{name}: line {i}: invalid JSON: {e.msg}") from None
            return out
        if fmt == "yaml":
            import yaml

            docs = list(yaml.load_all(text, Loader=yaml_loader()))
            if "*" in text:  # aliases share one object per anchor: count what they would expand to
                check_yaml_aliases(docs, len(text), name)
            return docs[0] if len(docs) == 1 else docs
        if fmt == "toml":
            import tomllib

            return tomllib.loads(text)
        if fmt == "ini":
            import configparser

            cp = configparser.ConfigParser(interpolation=None, strict=False, allow_no_value=True)
            cp.optionxform = str  # type: ignore[assignment,method-assign]
            try:
                cp.read_string(text)
            except configparser.MissingSectionHeaderError:
                cp.read_string("[DEFAULT]\n" + text)
            out: dict[str, Any] = {}
            if cp.defaults():
                out["DEFAULT"] = dict(cp.defaults())
            for s in cp.sections():
                out[s] = {k: v for k, v in cp.items(s, raw=True) if k not in cp.defaults() or cp.get(s, k, raw=True) != cp.defaults().get(k)}
            return out
    except json.JSONDecodeError as e:
        raise SkillError(f"{name}: invalid JSON at line {e.lineno} column {e.colno}: {e.msg}") from None
    except SkillError:
        raise
    except RecursionError:
        raise SkillError(f"{name}: nested too deeply to parse (more than about {_depth_limit():,} levels of arrays or objects)") from None
    except Exception as e:  # yaml.YAMLError, tomllib.TOMLDecodeError, configparser.Error
        raise SkillError(f"{name}: invalid {fmt.upper()}: {str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__}") from None
    raise SkillError(f"{fmt} is not a nested-document format")


def _depth_limit() -> int:
    import sys

    return sys.getrecursionlimit()


def yaml_max_nodes(text_len: int) -> int:
    """How many nodes a YAML document may expand to through aliases: DESK_YAML_MAX_NODES (default 1,000,000) plus one
    per character of the document, so big alias-free documents always pass."""
    import os

    try:
        base = int(os.environ.get("DESK_YAML_MAX_NODES") or 1_000_000)
    except ValueError:
        base = 1_000_000
    return base + text_len


def check_yaml_aliases(docs: list[Any], text_len: int, name: str = "input") -> None:
    """Refuses YAML whose aliases expand into more nodes than the limit ("billion laughs": nine anchors of nine
    aliases each are 387 million strings once written out) or refer to themselves (a cycle no output can hold)."""
    limit = yaml_max_nodes(text_len)
    memo: dict[int, int] = {}
    for root in docs:
        if not isinstance(root, (dict, list)):
            continue
        stack: list[tuple[Any, bool]] = [(root, False)]
        path: set[int] = set()
        while stack:
            obj, done = stack.pop()
            i = id(obj)
            items = list(obj.values()) if isinstance(obj, dict) else obj
            if done:
                path.discard(i)
                total = 1 + sum(memo.get(id(c), 1) if isinstance(c, (dict, list)) else 1 for c in items)
                memo[i] = total
                if total > limit:
                    raise SkillError(f"{name}: YAML aliases expand to more than {limit:,} nodes (an alias bomb, \"billion laughs\"): refusing it. Raise DESK_YAML_MAX_NODES for a document you trust")
                continue
            if i in memo:
                continue
            if i in path:
                raise SkillError(f"{name}: a YAML alias refers to the node that contains it (a recursive document), which JSON, TOML and tables cannot hold")
            path.add(i)
            stack.append((obj, True))
            for c in items:
                if isinstance(c, (dict, list)) and id(c) not in memo:
                    stack.append((c, False))


_YAML_LOADER: list[Any] = []


def yaml_loader() -> Any:
    """PyYAML's safe loader with YAML 1.2 booleans: only true/false are booleans, so `on:` (GitHub Actions), `yes`,
    `no`, `off` and `y` stay text instead of turning into True/False."""
    if _YAML_LOADER:
        return _YAML_LOADER[0]
    import yaml

    base = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

    class Loader(base):  # type: ignore[misc,valid-type]
        pass

    bool_tag = "tag:yaml.org,2002:bool"
    Loader.yaml_implicit_resolvers = {ch: [r for r in rs if r[0] != bool_tag] for ch, rs in base.yaml_implicit_resolvers.items()}
    Loader.add_implicit_resolver(bool_tag, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))
    _YAML_LOADER.append(Loader)
    return Loader


def _xml_parser() -> Any:
    from lxml import etree

    # No network, no entity expansion (billion laughs), huge trees allowed.
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, remove_comments=True, remove_pis=True)


def plain(obj: Any) -> Any:
    """JSON-safe copy: dates → ISO strings, Decimal → number, bytes → hex, tuples → lists, NaN → None."""
    if isinstance(obj, dict):
        return {str(k): plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() and abs(obj) < 2**63 else float(obj)
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    return obj


def drop_nulls(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: drop_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [drop_nulls(v) for v in obj if v is not None]
    return obj


def sort_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: sort_keys(obj[k]) for k in sorted(obj, key=str)}
    if isinstance(obj, list):
        return [sort_keys(v) for v in obj]
    return obj


def null_paths(obj: Any, path: tuple = ()) -> Iterator[tuple]:
    if obj is None:
        yield path
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from null_paths(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from null_paths(v, path + (i,))


def dump_doc(obj: Any, fmt: str, indent: int | None = 2, sort: bool = False, xml_root: str | None = None, xml_item: str = "item", nulls: str = "error") -> str:
    """Serialises a document; `nulls` ('error' or 'drop') matters for TOML, which has no null."""
    if sort:
        obj = sort_keys(obj)
    if fmt == "json":
        if indent is None:
            return json.dumps(plain(obj), ensure_ascii=False, separators=(",", ":"))
        return json.dumps(plain(obj), ensure_ascii=False, indent=indent)
    if fmt == "jsonl":
        items = obj if isinstance(obj, list) else [obj]
        return "".join(json.dumps(plain(x), ensure_ascii=False, separators=(",", ":")) + "\n" for x in items)
    if fmt == "yaml":
        import yaml

        dumper = getattr(yaml, "CSafeDumper", yaml.SafeDumper)
        return yaml.dump(_yaml_safe(obj), Dumper=dumper, sort_keys=False, allow_unicode=True, default_flow_style=False, width=120, indent=indent or 2)
    if fmt == "toml":
        import tomli_w

        data = obj
        if not isinstance(data, dict):
            data = {xml_root or "items": data}
        if nulls == "drop":
            data = drop_nulls(data)
        else:
            bad = list(null_paths(data))
            if bad:
                raise SkillError(f"TOML has no null; {len(bad)} null value(s), first at {fmt_path(bad[0])}. Pass --drop-nulls to leave them out")
        try:
            return tomli_w.dumps(_toml_safe(data), multiline_strings=True, indent=indent or 4)
        except (TypeError, ValueError) as e:
            raise SkillError(f"cannot write TOML: {e}") from None
    if fmt == "ini":
        return dump_ini(obj)
    if fmt == "xml":
        from lxml import etree

        root = obj_to_xml(obj, xml_root, xml_item)
        return etree.tostring(root, pretty_print=indent is not None, xml_declaration=True, encoding="UTF-8").decode("utf-8")
    raise UsageError(f"cannot write {fmt} as a document")


def _yaml_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {(k if isinstance(k, (str, int, float, bool)) or k is None else str(k)): _yaml_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_yaml_safe(v) for v in obj]
    if isinstance(obj, Decimal):
        return plain(obj)
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    return obj


def _toml_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _toml_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_toml_safe(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return float("nan")
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    return obj


def dump_ini(obj: Any) -> str:
    import configparser
    import io

    if not isinstance(obj, dict):
        raise SkillError("INI needs an object of sections ({section: {key: value}})")
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str  # type: ignore[assignment,method-assign]
    loose = {k: v for k, v in obj.items() if not isinstance(v, dict)}
    for section, values in obj.items():
        if not isinstance(values, dict):
            continue
        target = cp.defaults() if section == "DEFAULT" else None
        if target is None:
            cp.add_section(str(section))
        for k, v in values.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(plain(v), ensure_ascii=False)
            val = "" if v is None else ("true" if v is True else "false" if v is False else str(plain(v)))
            if section == "DEFAULT":
                cp.defaults()[str(k)] = val
            else:
                cp.set(str(section), str(k), val)
    if loose:
        if not cp.has_section("DEFAULT") and "DEFAULT" not in obj:
            for k, v in loose.items():
                cp.defaults()[str(k)] = json.dumps(plain(v), ensure_ascii=False) if isinstance(v, (dict, list)) else str(plain(v))
    buf = io.StringIO()
    cp.write(buf)
    return buf.getvalue()


# ── XML <-> objects ─────────────────────────────────────────────────────


_QNAMES: dict[tuple[str, str | None], str] = {}


def qname(el: Any) -> str:
    """prefix:local for namespaced tags (using the document's prefixes), local otherwise."""
    tag = el.tag
    if not isinstance(tag, str):
        return "#comment"
    if tag.startswith("{"):
        prefix = el.prefix
        name = _QNAMES.get((tag, prefix))
        if name is None:
            local = tag[1:].split("}", 1)[1]
            name = _QNAMES[(tag, prefix)] = f"{prefix}:{local}" if prefix else local
        return name
    return tag


def _attr_name(el: Any, key: str) -> str:
    if key.startswith("{"):
        uri, local = key[1:].split("}", 1)
        for p, u in (el.nsmap or {}).items():
            if u == uri and p:
                return f"{p}:{local}"
        if uri == "http://www.w3.org/XML/1998/namespace":
            return f"xml:{local}"
        return local
    return key


def xml_to_obj(el: Any, parent_ns: dict[str | None, str]) -> Any:
    """Element → object: attributes '@name', text '#text', repeated children → lists (xmltodict convention)."""
    out: dict[str, Any] = {}
    nsmap = dict(el.nsmap or {})
    for p, u in nsmap.items():
        if parent_ns.get(p) != u:
            out["@xmlns" + (f":{p}" if p else "")] = u
    for k, v in el.attrib.items():
        out["@" + _attr_name(el, k)] = v
    children = [c for c in el if isinstance(c.tag, str)]
    text = (el.text or "").strip()
    tails = " ".join(t for t in ((c.tail or "").strip() for c in children) if t)
    for c in children:
        key = qname(c)
        val = xml_to_obj(c, nsmap)
        if key in out:
            if not isinstance(out[key], list) or key not in _LISTS.get(id(out), set()):
                out[key] = [out[key]]
                _LISTS.setdefault(id(out), set()).add(key)
            out[key].append(val)
        else:
            out[key] = val
    _LISTS.pop(id(out), None)
    full_text = " ".join(t for t in (text, tails) if t)
    if not out:
        return full_text if full_text else None
    if full_text:
        out["#text"] = full_text
    return out


_LISTS: dict[int, set[str]] = {}

_XML_NAME = re.compile(r"^[A-Za-z_][\w.\-]*(:[A-Za-z_][\w.\-]*)?$")


def xml_name(key: str) -> str:
    k = str(key)
    if _XML_NAME.match(k) and not k.lower().startswith("xml"):
        return k
    k = re.sub(r"[^\w.\-]", "_", k)
    if not k or not (k[0].isalpha() or k[0] == "_") or k.lower().startswith("xml"):
        k = "_" + k
    return k


def obj_to_xml(obj: Any, root_name: str | None = None, item_name: str = "item") -> Any:
    from lxml import etree

    if root_name is None and isinstance(obj, dict) and len(obj) == 1 and not str(next(iter(obj))).startswith(("@", "#")):
        (root_name, obj), = obj.items()
    root_name = root_name or "root"
    nsmap = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) == "@xmlns":
                nsmap[None] = v
            elif str(k).startswith("@xmlns:"):
                nsmap[str(k)[7:]] = v
    root = etree.Element(_clark(root_name, nsmap), nsmap=nsmap or None)
    _fill(root, obj, item_name, nsmap)
    return root


def _clark(name: str, nsmap: dict[Any, str]) -> str:
    name = str(name)
    if ":" in name:
        p, local = name.split(":", 1)
        if p in nsmap:
            return f"{{{nsmap[p]}}}{xml_name(local)}"
        return xml_name(name.replace(":", "_"))
    if None in nsmap:
        return f"{{{nsmap[None]}}}{xml_name(name)}"
    return xml_name(name)


def _scalar_text(v: Any) -> str:
    v = plain(v)
    if v is None:
        return ""
    if v is True:
        return "true"
    if v is False:
        return "false"
    return str(v)


def _fill(el: Any, obj: Any, item_name: str, nsmap: dict[Any, str]) -> None:
    from lxml import etree

    if isinstance(obj, dict):
        for k, v in obj.items():
            ks = str(k)
            if ks == "@xmlns" or ks.startswith("@xmlns:"):
                continue
            if ks.startswith("@"):
                name = ks[1:]
                if ":" in name and name.split(":", 1)[0] in nsmap:
                    p, local = name.split(":", 1)
                    el.set(f"{{{nsmap[p]}}}{local}", _scalar_text(v))
                elif name.startswith("xml:"):
                    el.set(f"{{http://www.w3.org/XML/1998/namespace}}{name[4:]}", _scalar_text(v))
                else:
                    el.set(xml_name(name), _scalar_text(v))
            elif ks == "#text":
                el.text = _scalar_text(v)
            else:
                values = v if isinstance(v, list) else [v]
                for item in values:
                    child_ns = dict(nsmap)
                    if isinstance(item, dict):
                        for kk, vv in item.items():
                            if str(kk) == "@xmlns":
                                child_ns[None] = vv
                            elif str(kk).startswith("@xmlns:"):
                                child_ns[str(kk)[7:]] = vv
                    new = {p: u for p, u in child_ns.items() if nsmap.get(p) != u}
                    child = etree.SubElement(el, _clark(ks, child_ns), nsmap=new or None)
                    if isinstance(item, list):
                        _fill(child, {item_name: item}, item_name, child_ns)
                    else:
                        _fill(child, item, item_name, child_ns)
    elif isinstance(obj, list):
        for item in obj:
            child = etree.SubElement(el, _clark(item_name, nsmap))
            _fill(child, item, item_name, nsmap)
    else:
        el.text = _scalar_text(obj)


# ── paths ───────────────────────────────────────────────────────────────
#
# Steps: ("key", name) ("index", i) ("slice", a, b, step) ("wild",) ("recurse",) ("filter", expr)

_KEY_CHARS = re.compile(r"[A-Za-z_$@\-\w][\w\-$@:]*")


def parse_path(expr: str) -> list[tuple]:
    """Parses $.a.b[0], a.b[*].c, ..name, ['odd key'], [1:5], [?(@.price > 10)] and JSON Pointer /a/b/0."""
    s = expr.strip()
    if s in ("", "$", ".", "/"):
        return []
    if s.startswith("/"):
        return [("ptr", p.replace("~1", "/").replace("~0", "~")) for p in s[1:].split("/")]
    steps: list[tuple] = []
    i = 0
    if s.startswith("$"):
        i = 1
    n = len(s)
    while i < n:
        c = s[i]
        if s.startswith("..", i):
            steps.append(("recurse",))
            i += 2
            if i < n and s[i] == "[":
                continue
            m = re.compile(r"\*|[^.\[\]]+").match(s, i)
            if not m:
                raise UsageError(f"bad path {expr!r} at {i}")
            tok = m.group(0)
            steps.append(("wild",) if tok == "*" else ("key", tok))
            i = m.end()
        elif c == ".":
            i += 1
            m = re.compile(r"\*|[^.\[\]]+").match(s, i)
            if not m:
                raise UsageError(f"bad path {expr!r}: nothing after '.' at {i}")
            tok = m.group(0)
            steps.append(("wild",) if tok == "*" else ("key", tok))
            i = m.end()
        elif c == "[":
            j = _match_bracket(s, i, expr)
            inner = s[i + 1 : j].strip()
            steps.append(_bracket_step(inner, expr))
            i = j + 1
        else:
            m = re.compile(r"\*|[^.\[\]]+").match(s, i)
            if not m:
                raise UsageError(f"bad path {expr!r} at {i}")
            tok = m.group(0)
            steps.append(("wild",) if tok == "*" else ("key", tok))
            i = m.end()
    return steps


def _match_bracket(s: str, i: int, expr: str) -> int:
    depth = 0
    quote = None
    for j in range(i, len(s)):
        ch = s[j]
        if quote:
            if ch == "\\":
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return j
    raise UsageError(f"bad path {expr!r}: unclosed [")


def _bracket_step(inner: str, expr: str) -> tuple:
    if inner == "*":
        return ("wild",)
    if inner[:1] in "'\"" and inner[-1:] == inner[:1]:
        return ("key", inner[1:-1].replace("\\" + inner[0], inner[0]))
    if re.fullmatch(r"-?\d+", inner):
        return ("index", int(inner))
    m = re.fullmatch(r"(-?\d*)\s*:\s*(-?\d*)(?:\s*:\s*(-?\d+))?", inner)
    if m:
        a, b, st = m.groups()
        return ("slice", int(a) if a else None, int(b) if b else None, int(st) if st else None)
    if inner.startswith("?"):
        body = inner[1:].strip()
        if body.startswith("(") and body.endswith(")"):
            body = body[1:-1].strip()
        return ("filter", _parse_filter(body, expr))
    if "," in inner:
        raise UsageError(f"bad path {expr!r}: unions like [a,b] are not supported; run two paths")
    return ("key", inner)


_FILTER = re.compile(r"^(?P<lhs>@?[\w.\-\[\]'\"$*]*?)\s*(?:(?P<op>==|!=|<=|>=|=~|<|>|=)\s*(?P<rhs>.+))?$")


def _parse_filter(body: str, expr: str) -> tuple:
    m = _FILTER.match(body)
    if not m:
        raise UsageError(f"bad filter in {expr!r}: use [?(@.field > 10)], [?(@.name == 'x')], [?(@.tag =~ 'regex')] or [?(@.field)]")
    lhs = m.group("lhs").strip()
    lhs = lhs[1:] if lhs.startswith("@") else lhs
    lhs = lhs[1:] if lhs.startswith(".") else lhs
    sub = parse_path(lhs) if lhs else []
    op = m.group("op")
    if op == "=":
        op = "=="
    rhs: Any = None
    if op:
        raw = m.group("rhs").strip()
        if raw[:1] in "'\"" and raw[-1:] == raw[:1]:
            rhs = raw[1:-1]
        else:
            try:
                rhs = json.loads(raw)
            except ValueError:
                rhs = raw
        if op == "=~":
            try:
                rhs = re.compile(str(rhs))
            except re.error as e:
                raise UsageError(f"bad regex in filter: {e}") from None
    return (sub, op, rhs)


def _filter_ok(value: Any, flt: tuple) -> bool:
    sub, op, rhs = flt
    got = [v for _, v in select(value, sub)] if sub else [value]
    if not got:
        return False
    v = got[0]
    if op is None:
        return v is not None and v is not False
    try:
        if op == "==":
            return v == rhs or (isinstance(v, str) and not isinstance(rhs, str) and v == str(rhs))
        if op == "!=":
            return v != rhs
        if op == "=~":
            return isinstance(v, str) and bool(rhs.search(v))
        if isinstance(v, str) and isinstance(rhs, (int, float)):
            v = float(v)
        return {"<": v < rhs, "<=": v <= rhs, ">": v > rhs, ">=": v >= rhs}[op]
    except (TypeError, ValueError):
        return False


def select(obj: Any, steps: list[tuple], base: tuple = ()) -> Iterator[tuple[tuple, Any]]:
    """All (concrete path, value) matches of a parsed path."""
    if not steps:
        yield base, obj
        return
    step, rest = steps[0], steps[1:]
    kind = step[0]
    if kind == "key":
        if isinstance(obj, dict) and step[1] in obj:
            yield from select(obj[step[1]], rest, base + (step[1],))
        elif isinstance(obj, list) and re.fullmatch(r"-?\d+", step[1]):
            yield from select(obj, [("index", int(step[1]))] + rest, base)
    elif kind == "ptr":
        key = step[1]
        if isinstance(obj, dict) and key in obj:
            yield from select(obj[key], rest, base + (key,))
        elif isinstance(obj, list) and key.isdigit() and int(key) < len(obj):
            yield from select(obj[int(key)], rest, base + (int(key),))
    elif kind == "index":
        if isinstance(obj, list):
            i = step[1] + len(obj) if step[1] < 0 else step[1]
            if 0 <= i < len(obj):
                yield from select(obj[i], rest, base + (i,))
    elif kind == "slice":
        if isinstance(obj, list):
            for i in range(*slice(step[1], step[2], step[3]).indices(len(obj))):
                yield from select(obj[i], rest, base + (i,))
    elif kind == "wild":
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield from select(v, rest, base + (k,))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                yield from select(v, rest, base + (i,))
    elif kind == "recurse":
        yield from select(obj, rest, base)
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield from select(v, steps, base + (k,))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                yield from select(v, steps, base + (i,))
    elif kind == "filter":
        if isinstance(obj, list):
            for i, v in enumerate(obj):
                if _filter_ok(v, step[1]):
                    yield from select(v, rest, base + (i,))
        elif isinstance(obj, dict):
            if _filter_ok(obj, step[1]):
                yield from select(obj, rest, base)
    elif kind == "self_filter":
        if _filter_ok(obj, step[1]):
            yield from select(obj, rest, base)


_SIMPLE_KEY = re.compile(r"^[A-Za-z_][\w\-]*$")


def fmt_path(path: tuple | list) -> str:
    out = "$"
    for p in path:
        if isinstance(p, int):
            out += f"[{p}]"
        elif isinstance(p, str) and _SIMPLE_KEY.match(p):
            out += f".{p}"
        else:
            out += "[" + json.dumps(str(p), ensure_ascii=False) + "]"
    return out


def fmt_pattern(steps: list[tuple]) -> str:
    out = "$"
    for s in steps:
        if s[0] == "key":
            out += f".{s[1]}" if _SIMPLE_KEY.match(s[1]) else "[" + json.dumps(s[1]) + "]"
        elif s[0] == "wild":
            out += "[*]"
        elif s[0] == "index":
            out += f"[{s[1]}]"
        elif s[0] == "recurse":
            out += ".."
        else:
            out += "[…]"
    return out


def type_name(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, (float, Decimal)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, dict):
        return "object"
    if isinstance(v, (list, tuple)):
        return "array"
    if isinstance(v, _dt.datetime):
        return "datetime"
    if isinstance(v, _dt.date):
        return "date"
    if isinstance(v, _dt.time):
        return "time"
    return type(v).__name__


def preview(v: Any, width: int = 80) -> str:
    if isinstance(v, str):
        s = json.dumps(v, ensure_ascii=False)
    else:
        s = json.dumps(plain(v), ensure_ascii=False, separators=(",", ":"), default=str)
    return s if len(s) <= width else s[: width - 1] + "…"


# ── shapes (outline and schema inference) ───────────────────────────────

MAP_KEYS = 300  # an object with more distinct keys than this is summarised as a map

_FORMATS = [
    # The same rules data_validate checks formats with (RFC 3339: a date-time has seconds and an offset).
    ("date-time", re.compile(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")),
    ("date", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("time", re.compile(r"^\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})?$")),
    ("email", re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")),
    ("uri", re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://\S+$")),
    ("uuid", re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")),
    ("ipv4", re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")),
]


def string_format(s: str) -> str | None:
    if len(s) > 200:
        return None
    for name, rx in _FORMATS:
        if rx.match(s):
            if name in ("date", "date-time", "ipv4") and not _valid_format(name, s):
                return None
            return name
    return None


def _valid_format(name: str, s: str) -> bool:
    """Shape-matching strings that are not real values (2024-02-30, 999.1.1.1) get no format."""
    try:
        if name == "date":
            _dt.date.fromisoformat(s)
        elif name == "date-time":
            _dt.datetime.fromisoformat(s.replace("z", "Z").replace("Z", "+00:00"))
        else:
            import ipaddress

            ipaddress.IPv4Address(s)
        return True
    except ValueError:
        return False


class Shape:
    """Merged statistics of every value seen at one path pattern."""

    __slots__ = ("n", "types", "keys", "items", "examples", "arrays", "items_total", "len_min", "len_max", "is_map", "map_keys", "formats", "num_min", "num_max", "str_max", "enum", "exact")

    def __init__(self) -> None:
        self.n = 0
        self.types: Counter[str] = Counter()
        self.keys: dict[str, Shape] = {}
        self.items: Shape | None = None
        self.examples: list[Any] = []
        self.arrays = 0
        self.items_total = 0
        self.len_min: int | None = None
        self.len_max: int | None = None
        self.is_map = False
        self.map_keys: list[str] = []
        self.formats: Counter[str] = Counter()
        self.num_min: float | None = None
        self.num_max: float | None = None
        self.str_max = 0
        self.enum: Counter[Any] | None = Counter()
        self.exact = True

    def add(self, v: Any) -> None:
        self.n += 1
        t = type_name(v)
        self.types[t] += 1
        if t == "object":
            if self.is_map:
                star = self.keys.setdefault("*", Shape())
                for k, x in v.items():
                    star.add(x)
                    k = k if isinstance(k, str) else str(k)
                    if len(self.map_keys) < 5 and k not in self.map_keys:
                        self.map_keys.append(k)
            else:
                for k, x in v.items():
                    k = k if isinstance(k, str) else str(k)  # YAML keys can be numbers or dates
                    sh = self.keys.get(k)
                    if sh is None:
                        sh = self.keys[k] = Shape()
                    sh.add(x)
                if len(self.keys) > MAP_KEYS:
                    self._to_map()
        elif t == "array":
            self.arrays += 1
            ln = len(v)
            self.items_total += ln
            self.len_min = ln if self.len_min is None else min(self.len_min, ln)
            self.len_max = ln if self.len_max is None else max(self.len_max, ln)
            if ln:
                if self.items is None:
                    self.items = Shape()
                for x in v:
                    self.items.add(x)
        else:
            if len(self.examples) < 3 and v not in self.examples:
                self.examples.append(v)
            if t in ("integer", "number"):
                f = float(v)
                if not math.isnan(f):
                    self.num_min = f if self.num_min is None else min(self.num_min, f)
                    self.num_max = f if self.num_max is None else max(self.num_max, f)
            elif t == "string":
                self.str_max = max(self.str_max, len(v))
                fm = string_format(v)
                if fm:
                    self.formats[fm] += 1
            if self.enum is not None and t in ("string", "integer", "boolean"):
                self.enum[v] += 1
                if len(self.enum) > 12:
                    self.enum = None

    def _to_map(self) -> None:
        star = Shape()
        self.map_keys = list(self.keys)[:5]
        for sh in self.keys.values():
            star.merge(sh)
        self.keys = {"*": star}
        self.is_map = True

    def merge(self, o: "Shape") -> None:
        self.n += o.n
        self.types.update(o.types)
        for k, sh in o.keys.items():
            if k in self.keys:
                self.keys[k].merge(sh)
            else:
                self.keys[k] = sh
        if o.items is not None:
            if self.items is None:
                self.items = o.items
            else:
                self.items.merge(o.items)
        for e in o.examples:
            if len(self.examples) < 3 and e not in self.examples:
                self.examples.append(e)
        self.arrays += o.arrays
        self.items_total += o.items_total
        for attr, fn in (("len_min", min), ("len_max", max), ("num_min", min), ("num_max", max)):
            a, b = getattr(self, attr), getattr(o, attr)
            setattr(self, attr, b if a is None else a if b is None else fn(a, b))
        self.formats.update(o.formats)
        self.str_max = max(self.str_max, o.str_max)
        if self.enum is None or o.enum is None:
            self.enum = None
        else:
            self.enum.update(o.enum)
            if len(self.enum) > 12:
                self.enum = None
        self.exact = self.exact and o.exact


def shape_of(obj: Any) -> Shape:
    sh = Shape()
    sh.add(obj)
    return sh


def type_label(sh: Shape) -> str:
    parts = []
    for t, c in sh.types.most_common():
        if t == "array":
            inner = ""
            if sh.items is not None:
                it = [x for x, _ in sh.items.types.most_common()]
                inner = " of " + "|".join(it)
            size = f"{sh.items_total}" if sh.arrays == 1 else f"{sh.arrays}×, {sh.items_total} items"
            parts.append(f"array[{size}]{inner}")
        elif t == "object":
            if sh.is_map:
                parts.append(f"map{{{'…' if not sh.map_keys else ', '.join(sh.map_keys[:3])}…}}")
            else:
                parts.append(f"object{{{len(sh.keys)}}}")
        else:
            parts.append(t)
    return " | ".join(parts)


def outline_lines(sh: Shape, name: str = "$", depth: int = 8, max_keys: int = 60, _level: int = 0, parent_n: int | None = None) -> Iterator[str]:
    """One line per path pattern: address, types, presence, examples."""
    label = type_label(sh)
    extra = []
    if parent_n and sh.n < parent_n:
        extra.append(f"in {sh.n}/{parent_n}")
    nulls = sh.types.get("null", 0)
    if nulls and len(sh.types) > 1:
        extra.append(f"{nulls} null")
    if sh.examples:
        extra.append("e.g. " + ", ".join(preview(e, 40) for e in sh.examples[:3]))
    if sh.formats and sh.types.get("string"):
        fm, c = sh.formats.most_common(1)[0]
        if c >= sh.types["string"] * 0.9:
            extra.append(f"format {fm}")
    if sh.len_min is not None and sh.arrays > 1:
        extra.append(f"len {sh.len_min}-{sh.len_max}")
    yield "  " * _level + f"{name}  {label}" + (f"  ({'; '.join(extra)})" if extra else "")
    if _level >= depth:
        if sh.keys or sh.items is not None:
            yield "  " * (_level + 1) + "… (deeper levels hidden; raise --depth or outline a sub-path)"
        return
    keys = list(sh.keys.items())
    obj_n = sh.types.get("object", 0)
    for k, child in keys[:max_keys]:
        key_txt = "*" if sh.is_map and k == "*" else (f".{k}" if _SIMPLE_KEY.match(k) else "[" + json.dumps(k, ensure_ascii=False) + "]")
        if sh.is_map:
            key_txt = ".*"
        yield from outline_lines(child, name + key_txt, depth, max_keys, _level + 1, obj_n)
    if len(keys) > max_keys:
        yield "  " * (_level + 1) + f"… {len(keys) - max_keys} more keys (raise --max-keys)"
    if sh.items is not None:
        yield from outline_lines(sh.items, name + "[*]", depth, max_keys, _level + 1, None)


def shape_json(sh: Shape, depth: int = 12) -> dict[str, Any]:
    out: dict[str, Any] = {"count": sh.n, "types": dict(sh.types)}
    if sh.examples:
        out["examples"] = [plain(e) for e in sh.examples]
    if sh.formats:
        out["formats"] = dict(sh.formats)
    if sh.arrays:
        out["arrays"] = sh.arrays
        out["items"] = sh.items_total
    if sh.is_map:
        out["map"] = True
        out["map_key_examples"] = sh.map_keys
    if depth > 0:
        if sh.keys:
            out["keys"] = {k: shape_json(v, depth - 1) for k, v in sh.keys.items()}
        if sh.items is not None:
            out["element"] = shape_json(sh.items, depth - 1)
    return out


def infer_schema(sh: Shape, top: bool = True) -> dict[str, Any]:
    """A JSON Schema (draft 2020-12) describing every value merged into `sh`."""
    types = [t for t in sh.types if t != "null"]
    schemas: list[dict[str, Any]] = []
    for t in types:
        if t == "object":
            s: dict[str, Any] = {"type": "object"}
            obj_n = sh.types["object"]
            if sh.is_map and "*" in sh.keys:
                s["additionalProperties"] = infer_schema(sh.keys["*"], False)
            else:
                s["properties"] = {k: infer_schema(v, False) for k, v in sh.keys.items()}
                req = [k for k, v in sh.keys.items() if v.n - v.types.get("null", 0) * 0 >= obj_n and v.n >= obj_n]
                if req:
                    s["required"] = req
            schemas.append(s)
        elif t == "array":
            s = {"type": "array"}
            if sh.items is not None:
                s["items"] = infer_schema(sh.items, False)
            schemas.append(s)
        elif t in ("integer", "number", "boolean"):
            s = {"type": t}
            schemas.append(s)
        elif t == "string":
            s = {"type": "string"}
            if sh.formats:
                fm, c = sh.formats.most_common(1)[0]
                if c == sh.types["string"] and fm in ("date-time", "date", "time", "email", "uri", "uuid", "ipv4"):
                    s["format"] = fm
            schemas.append(s)
        elif t in ("date", "datetime", "time"):
            schemas.append({"type": "string", "format": {"date": "date", "datetime": "date-time", "time": "time"}[t]})
        else:
            schemas.append({})
    if "integer" in types and "number" in types:
        schemas = [s for s in schemas if s.get("type") != "integer"]
    if sh.types.get("null"):
        schemas.append({"type": "null"})
    if not schemas:
        out: dict[str, Any] = {}
    elif len(schemas) == 1:
        out = schemas[0]
    elif all(set(s) == {"type"} for s in schemas):
        out = {"type": [s["type"] for s in schemas]}
    else:
        out = {"anyOf": schemas}
    if top:
        out = {"$schema": "https://json-schema.org/draft/2020-12/schema", **out}
    return out


# ── search ──────────────────────────────────────────────────────────────


def walk(obj: Any, path: tuple = ()) -> Iterator[tuple[tuple, Any]]:
    yield path, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, path + (i,))


def matcher(pattern: str, regex: bool, ignore_case: bool, exact: bool = False) -> Any:
    flags = re.IGNORECASE if ignore_case else 0
    if regex:
        try:
            rx = re.compile(pattern, flags)
        except re.error as e:
            raise UsageError(f"bad regex: {e}") from None
    else:
        rx = re.compile(("^" + re.escape(pattern) + "$") if exact else re.escape(pattern), flags)
    return rx


def search(obj: Any, rx: Any, keys: bool = True, values: bool = True) -> Iterator[dict[str, Any]]:
    for path, v in walk(obj):
        if keys and path and isinstance(path[-1], str) and rx.search(path[-1]):
            yield {"path": fmt_path(path), "on": "key", "value": v}
        elif values and not isinstance(v, (dict, list)) and v is not None and rx.search(_scalar_text(v)):
            yield {"path": fmt_path(path), "on": "value", "value": v}


# ── records (the table inside a document) ───────────────────────────────


def record_paths(obj: Any, max_depth: int = 4) -> list[tuple[tuple, int]]:
    """Arrays of objects inside a document, biggest first: [(path, length)]."""
    found: list[tuple[tuple, int]] = []

    def visit(o: Any, path: tuple, d: int) -> None:
        if isinstance(o, list):
            if o and sum(1 for x in o[:50] if isinstance(x, dict)) >= min(len(o), 50) * 0.8:
                found.append((path, len(o)))
            return
        if isinstance(o, dict) and d < max_depth:
            for k, v in o.items():
                visit(v, path + (k,), d + 1)

    visit(obj, (), 0)
    found.sort(key=lambda x: -x[1])
    return found


def records_at(obj: Any, path_expr: str | None) -> tuple[list[Any], str]:
    """The record list for tabular use, and the address it came from."""
    if path_expr:
        steps = parse_path(path_expr)
        matches = [v for _, v in select(obj, steps)]
        if not matches:
            raise SkillError(f"nothing at {path_expr}")
        if len(matches) == 1 and isinstance(matches[0], list):
            return matches[0], fmt_pattern(steps)
        if len(matches) == 1 and isinstance(matches[0], dict):
            return [matches[0]], fmt_pattern(steps)
        out: list[Any] = []
        for m in matches:
            out.extend(m if isinstance(m, list) else [m])
        return out, fmt_pattern(steps)
    if isinstance(obj, list):
        return obj, "$"
    paths = record_paths(obj)
    if paths:
        p, _ = paths[0]
        return [v for _, v in select(obj, [("key", k) for k in p])][0], fmt_path(p)
    return [obj], "$"


def flatten_record(rec: Any, sep: str = ".", max_depth: int = 8) -> dict[str, Any]:
    """{'a': {'b': 1}, 'c': [1, 2]} → {'a.b': 1, 'c': [1, 2]} (lists stay whole)."""
    out: dict[str, Any] = {}

    def go(o: Any, prefix: str, d: int) -> None:
        if isinstance(o, dict) and o and d < max_depth:
            for k, v in o.items():
                go(v, f"{prefix}{sep}{k}" if prefix else str(k), d + 1)
        else:
            out[prefix or "value"] = o

    if not isinstance(rec, dict):
        return {"value": rec}
    go(rec, "", 0)
    return out


# ── streaming (ijson) for huge JSON ─────────────────────────────────────


class _SkipBOM:
    """A binary stream without its UTF-8 byte order mark (ijson's parser rejects one)."""

    def __init__(self, f: Any) -> None:
        self.f = f
        head = f.read(3)
        self.head = b"" if head == b"\xef\xbb\xbf" else head

    def read(self, n: int = -1) -> bytes:
        if not self.head:
            return self.f.read(n)
        if n is None or n < 0:
            out, self.head = self.head + self.f.read(), b""
            return out
        out, self.head = self.head[:n], self.head[n:]
        return out + self.f.read(n - len(out)) if len(out) < n else out


class json_open:  # noqa: N801 — used like open()
    """`with json_open(path, compression) as f`: the decompressed JSON bytes, without a byte order mark."""

    def __init__(self, path: Path, compression: str | None) -> None:
        from _formats import open_decompressed

        self.raw = open_decompressed(path, compression)

    def __enter__(self) -> _SkipBOM:
        return _SkipBOM(self.raw)

    def __exit__(self, *exc: Any) -> None:
        self.raw.close()


def _ijson() -> Any:
    try:
        import ijson

        return ijson
    except ImportError as e:  # pragma: no cover
        raise SkillError("ijson is not installed in this runtime") from e


def stream_shape(path: Path, compression: str | None = None) -> dict[str, Any]:
    """An outline of a huge JSON document in one streaming pass (C-speed counting per path pattern).

    Returns {"patterns": {prefix: {event: count}}, "examples": {prefix: [values]}, "bytes": n}. ijson prefixes use
    'item' for array elements.
    """
    from itertools import islice
    from operator import itemgetter

    from _formats import open_decompressed

    ij = _ijson()
    counts: Counter[tuple[str, str]] = Counter()
    collapsed = False
    with json_open(path, compression) as f:
        events = ij.parse(f, use_float=True, buf_size=1 << 20)
        pairs = map(itemgetter(0, 1), events)
        while True:
            chunk = islice(pairs, 2_000_000)
            before = sum(counts.values())
            counts.update(chunk)
            if sum(counts.values()) == before:
                break
            if len(counts) > 20000:
                collapsed = True
                counts = _collapse_counts(counts)
    examples: dict[str, list[Any]] = {}
    kinds: dict[str, Counter[str]] = {}
    with json_open(path, compression) as f:
        for i, (prefix, event, value) in enumerate(ij.parse(f, use_float=True)):
            if i > 400_000:
                break
            if event in ("string", "number", "boolean"):
                ex = examples.setdefault(prefix, [])
                if len(ex) < 3 and value not in ex:
                    ex.append(value)
                if event == "number":
                    kinds.setdefault(prefix, Counter())["integer" if isinstance(value, int) else "number"] += 1
    return {"counts": [[p, e, c] for (p, e), c in counts.items()], "examples": examples, "numkinds": {k: dict(v) for k, v in kinds.items()}, "collapsed": collapsed}


def _collapse_counts(counts: Counter[tuple[str, str]]) -> Counter[tuple[str, str]]:
    # Too many distinct prefixes: objects used as maps (id → record). Collapse components that vary a lot.
    comp_freq: Counter[tuple[int, str]] = Counter()
    for (p, _), _c in counts.items():
        for i, part in enumerate(p.split(".")):
            comp_freq[(i, part)] += 1
    parents: dict[tuple[int, str], set[str]] = {}
    for (p, _), _c in counts.items():
        parts = p.split(".")
        for i in range(1, len(parts)):
            parents.setdefault((i, ".".join(parts[:i])), set()).add(parts[i])
    wide = {key for key, kids in parents.items() if len(kids) > MAP_KEYS}
    out: Counter[tuple[str, str]] = Counter()
    for (p, e), c in counts.items():
        parts = p.split(".")
        for i in range(1, len(parts)):
            if (i, ".".join(parts[:i])) in wide:
                parts[i] = "*"
        out[(".".join(parts), e)] += c
    return out


def stream_outline_lines(res: dict[str, Any], depth: int = 8, max_keys: int = 60) -> list[str]:
    """Renders stream_shape() output like outline_lines()."""
    by_prefix: dict[str, Counter[str]] = {}
    for p, e, c in res["counts"]:
        by_prefix.setdefault(p, Counter())[e] += c
    children: dict[str, list[str]] = {}
    for p in by_prefix:
        if p == "":
            continue
        parent = p.rsplit(".", 1)[0] if "." in p else ""
        children.setdefault(parent, []).append(p)
    lines: list[str] = []
    numkinds = res.get("numkinds", {})

    def value_count(ev: Counter[str]) -> int:
        return sum(c for e, c in ev.items() if e in ("start_map", "start_array", "string", "number", "boolean", "null"))

    def render(p: str, level: int, parent_objs: int | None) -> None:
        ev = by_prefix.get(p, Counter())
        n = value_count(ev)
        parts = []
        for e, t in (("start_map", "object"), ("start_array", "array"), ("string", "string"), ("number", "number"), ("boolean", "boolean"), ("null", "null")):
            if ev.get(e):
                if t == "array":
                    items = value_count(by_prefix.get(p + ".item" if p else "item", Counter()))
                    kids = by_prefix.get(p + ".item" if p else "item", Counter())
                    it = [tt for ee, tt in (("start_map", "object"), ("start_array", "array"), ("string", "string"), ("number", "number"), ("boolean", "boolean"), ("null", "null")) if kids.get(ee)]
                    size = f"{items}" if ev[e] == 1 else f"{ev[e]}×, {items} items"
                    parts.append(f"array[{size}]" + (" of " + "|".join(it) if it else ""))
                elif t == "object":
                    nk = len([c for c in children.get(p, []) if not c.endswith(".item") and c != "item"])
                    parts.append(f"object{{{nk}}}")
                elif t == "number":
                    k = numkinds.get(p, {})
                    parts.append("integer" if k and set(k) == {"integer"} else "number")
                else:
                    parts.append(t)
        extra = []
        if parent_objs and n < parent_objs:
            extra.append(f"in {n}/{parent_objs}")
        ex = res["examples"].get(p)
        if ex:
            extra.append("e.g. " + ", ".join(preview(x, 40) for x in ex[:3]))
        name = _prefix_to_path(p)
        lines.append("  " * level + f"{name}  {' | '.join(parts)}" + (f"  ({'; '.join(extra)})" if extra else ""))
        kids = children.get(p, [])
        if level >= depth:
            if kids:
                lines.append("  " * (level + 1) + "… (deeper levels hidden; raise --depth)")
            return
        objs = ev.get("start_map", 0)
        keyed = [c for c in kids if not (c == "item" or c.endswith(".item"))]
        for c in keyed[:max_keys]:
            render(c, level + 1, objs)
        if len(keyed) > max_keys:
            lines.append("  " * (level + 1) + f"… {len(keyed) - max_keys} more keys (raise --max-keys)")
        item = (p + ".item") if p else "item"
        if item in by_prefix:
            render(item, level + 1, None)

    render("", 0, None)
    return lines


def _prefix_to_path(p: str) -> str:
    if not p:
        return "$"
    out = "$"
    for part in p.split("."):
        if part == "item":
            out += "[*]"
        elif part == "*":
            out += ".*"
        elif _SIMPLE_KEY.match(part):
            out += "." + part
        else:
            out += "[" + json.dumps(part, ensure_ascii=False) + "]"
    return out


def steps_to_prefix(steps: list[tuple]) -> str | None:
    """An ijson prefix for paths made only of keys and [*] (fast C item streaming), else None."""
    parts = []
    for s in steps:
        if s[0] == "key" and "." not in s[1] and s[1] != "item":
            parts.append(s[1])
        elif s[0] == "wild":
            return None
        else:
            return None
    return ".".join(parts)


def stream_select(path: Path, steps: list[tuple], limit: int | None, compression: str | None = None) -> Iterator[tuple[tuple, Any]]:
    """Matches of a path in a huge JSON file without loading it, stopping after `limit` matches.

    Tracks the concrete path of every value; subtrees that cannot match are skipped; matched values (and array
    elements a filter needs) are built with ijson's ObjectBuilder.
    """
    from _formats import open_decompressed

    ij = _ijson()
    for s in steps:
        if s[0] == "index" and s[1] < 0 or s[0] == "slice" and any(x is not None and x < 0 for x in s[1:3]):
            raise UsageError("negative indexes need the whole array in memory; on a streamed file use positive ones like [0:10]")

    steps = [("key", s[1]) if s[0] == "ptr" else s for s in steps]
    most = max_matches(steps)
    if most is not None:
        limit = min(limit, most) if limit else most
    found = 0
    fast = _fast_prefix(steps)
    if fast is not None:
        prefix, star = fast
        with json_open(path, compression) as f:
            for i, value in enumerate(ij.items(f, prefix, use_float=True, buf_size=1 << 20)):
                concrete: list[Any] = []
                for st in steps:
                    concrete.append(i if st[0] == "wild" else st[1])
                yield tuple(concrete), value
                found += 1
                if limit and found >= limit:
                    return
        if found or not star:
            return
    with json_open(path, compression) as f:
        events = ij.basic_parse(f, use_float=True, buf_size=1 << 20)
        for match_path, value in _stream_walk(events, steps, ij):
            yield match_path, value
            found += 1
            if limit and found >= limit:
                return


def max_matches(steps: list[tuple]) -> int | None:
    """How many matches a path can have at most (None: unbounded), so streams can stop early."""
    total = 1
    for st in steps:
        if st[0] in ("key", "index", "ptr"):
            continue
        if st[0] == "slice" and st[1] is not None and st[2] is not None:
            total *= max(0, len(range(*slice(st[1], st[2], st[3]).indices(st[2]))))
            continue
        return None
    return total


def _fast_prefix(steps: list[tuple]) -> tuple[str, bool] | None:
    """An ijson prefix for paths of plain keys with at most one [*] (C-speed matching), else None."""
    parts = []
    stars = 0
    for st in steps:
        if st[0] == "key" and "." not in st[1] and st[1] != "item" and not st[1].lstrip("-").isdigit():
            parts.append(st[1])
        elif st[0] == "wild":
            stars += 1
            parts.append("item")
        else:
            return None
    if stars > 1 or not parts:
        return None
    return ".".join(parts), stars == 1


def _advance(states: frozenset[int], steps: list[tuple], comp: Any) -> tuple[frozenset[int], list[int]]:
    """NFA step over one path component: (next states, states whose step is a filter needing the value)."""
    nxt: set[int] = set()
    need_value: list[int] = []
    for s in states:
        if s >= len(steps):
            continue
        st = steps[s]
        k = st[0]
        if k == "recurse":
            nxt.add(s)  # stay: '..' consumes any number of components
            # and also try the following step on this component
            sub, nv = _advance(frozenset([s + 1]), steps, comp)
            nxt |= sub
            need_value += nv
        elif k == "key":
            if comp == st[1] or (isinstance(comp, int) and st[1].lstrip("-").isdigit() and int(st[1]) == comp):
                nxt.add(s + 1)
        elif k == "wild":
            nxt.add(s + 1)
        elif k == "index":
            if isinstance(comp, int) and comp == st[1]:
                nxt.add(s + 1)
        elif k == "slice":
            if isinstance(comp, int):
                a, b, stp = st[1] or 0, st[2], st[3] or 1
                if comp >= a and (b is None or comp < b) and (comp - a) % stp == 0:
                    nxt.add(s + 1)
        elif k == "filter":
            if isinstance(comp, int):
                need_value.append(s)
    return frozenset(nxt), need_value


def _closure(states: frozenset[int], steps: list[tuple]) -> frozenset[int]:
    out = set(states)
    for s in states:
        j = s
        while j < len(steps) and steps[j][0] == "recurse":
            j += 1
            out.add(j)
    return frozenset(out)


def _stream_walk(events: Iterator[tuple[str, Any]], steps: list[tuple], ij: Any) -> Iterator[tuple[tuple, Any]]:
    n = len(steps)
    events = iter(events)

    def build(first_event: str, first_value: Any) -> Any:
        b = ij.ObjectBuilder()
        b.event(first_event, first_value)
        if first_event not in ("start_map", "start_array"):
            return b.value
        depth = 1
        for ev, val in events:
            b.event(ev, val)
            if ev in ("start_map", "start_array"):
                depth += 1
            elif ev in ("end_map", "end_array"):
                depth -= 1
                if depth == 0:
                    break
        return b.value

    def skip(first_event: str) -> None:
        if first_event not in ("start_map", "start_array"):
            return
        depth = 1
        for ev, _ in events:
            if ev in ("start_map", "start_array"):
                depth += 1
            elif ev in ("end_map", "end_array"):
                depth -= 1
                if depth == 0:
                    return

    def deeper(value: Any, states: Any, base: tuple) -> Iterator[tuple[tuple, Any]]:
        # Remaining steps evaluated in memory on a value we had to build; only strictly deeper matches.
        seen: set[tuple] = set()
        for s in states:
            if s < n:
                for sub_path, sub_val in select(value, steps[s:], ()):
                    if sub_path and sub_path not in seen:
                        seen.add(sub_path)
                        yield base + sub_path, sub_val

    path: list[Any] = []
    frames: list[list[Any]] = []  # [kind, states, next index, current key]

    def start_value(ev: str, val: Any, states: frozenset[int], comp: Any, has_comp: bool) -> Iterator[tuple[tuple, Any]]:
        here = tuple(path) + ((comp,) if has_comp else ())
        if n in states:
            value = build(ev, val)
            yield here, value
            yield from deeper(value, states, here)
            return
        live = [s for s in states if s < n]
        if not live or ev not in ("start_map", "start_array"):
            skip(ev)
            return
        if has_comp:
            path.append(comp)
        frames.append(["map" if ev == "start_map" else "array", states, 0, None])

    first = next(events, None)
    if first is None:
        return
    yield from start_value(first[0], first[1], _closure(frozenset([0]), steps), None, False)
    for ev, val in events:
        if not frames:
            break
        top = frames[-1]
        if ev == "map_key":
            top[3] = val
            continue
        if ev in ("end_map", "end_array"):
            frames.pop()
            if frames:
                path.pop()
            continue
        if top[0] == "map":
            comp = top[3]
        else:
            comp = top[2]
            top[2] += 1
        nxt, needv = _advance(top[1], steps, comp)
        nxt = _closure(nxt, steps)
        if needv:
            value = build(ev, val)
            here = tuple(path) + (comp,)
            for s in needv:
                if _filter_ok(value, steps[s][1]):
                    for sub_path, sub_val in select(value, steps[s + 1 :], ()):
                        yield here + sub_path, sub_val
            if n in nxt:
                yield here, value
            yield from deeper(value, nxt, here)
            continue
        yield from start_value(ev, val, nxt, comp, True)


def stream_search(path: Path, rx: Any, keys: bool, values: bool, limit: int, compression: str | None = None) -> Iterator[dict[str, Any]]:
    """Search keys and scalar values of a huge JSON file, with concrete paths."""
    from _formats import open_decompressed

    ij = _ijson()
    found = 0
    with json_open(path, compression) as f:
        pathl: list[Any] = []
        kinds: list[str] = []
        idx: list[int] = []
        key: Any = None
        for ev, val in ij.basic_parse(f, use_float=True, buf_size=1 << 20):
            if ev == "map_key":
                key = val
                if keys and rx.search(val):
                    yield {"path": fmt_path(tuple(pathl) + (val,)), "on": "key", "value": None}
                    found += 1
                    if found >= limit:
                        return
                continue
            if ev in ("end_map", "end_array"):
                kinds.pop()
                idx.pop()
                if pathl:
                    pathl.pop()
                continue
            comp = None
            if kinds:
                if kinds[-1] == "map":
                    comp = key
                else:
                    comp = idx[-1]
                    idx[-1] += 1
            if ev in ("start_map", "start_array"):
                if kinds:
                    pathl.append(comp)
                kinds.append("map" if ev == "start_map" else "array")
                idx.append(0)
                continue
            if values and val is not None and rx.search(_scalar_text(val)):
                p = tuple(pathl) + ((comp,) if kinds else ())
                yield {"path": fmt_path(p), "on": "value", "value": val}
                found += 1
                if found >= limit:
                    return


def _records_prefix(steps: list[tuple]) -> str | None:
    """ijson prefix for keys and [*] steps (records need no addresses, so any number of [*] is fine)."""
    parts = []
    for st in steps:
        if st[0] == "key" and "." not in st[1] and st[1] != "item":
            parts.append(st[1])
        elif st[0] == "wild":
            parts.append("item")
        else:
            return None
    return ".".join(parts)


def stream_records(path: Path, steps: list[tuple], compression: str | None = None) -> Iterator[Any]:
    """Items of the record array at a path (fast ijson.items when the path is simple)."""
    from _formats import open_decompressed

    ij = _ijson()
    prefix = _records_prefix(steps)
    if prefix is not None:
        item_prefix = (prefix + ".item") if prefix else "item"
        with json_open(path, compression) as f:
            yielded = False
            for it in ij.items(f, item_prefix, use_float=True, buf_size=1 << 20):
                yielded = True
                yield it
            if yielded:
                return
        with json_open(path, compression) as f:
            for it in ij.items(f, prefix, use_float=True, buf_size=1 << 20):
                if isinstance(it, list):
                    yield from it
                else:
                    yield it
        return
    for _, v in stream_select(path, steps, None, compression):
        if isinstance(v, list):
            yield from v
        else:
            yield v


def stream_record_path(path: Path, compression: str | None = None, max_events: int = 2_000_000) -> tuple[list[tuple], str]:
    """Guesses the main record array of a huge JSON document from its first events: (steps, address)."""
    from _formats import open_decompressed

    ij = _ijson()
    starts: Counter[str] = Counter()
    with json_open(path, compression) as f:
        for i, (prefix, event, _) in enumerate(ij.parse(f, use_float=True, buf_size=1 << 20)):
            if event == "start_map" and (prefix == "item" or prefix.endswith(".item")):
                starts[prefix] += 1
            if i >= max_events:
                break
    if not starts:
        return [], "$"
    # Like record_paths(): arrays of objects reached through object keys only (not nested in other records),
    # the one with the most items first.
    top = {p: c for p, c in starts.items() if "item" not in p.split(".")[:-1]}
    pool = top or starts
    chosen = max(pool.items(), key=lambda kv: (kv[1], -kv[0].count(".")))[0]
    parts = chosen.split(".")[:-1]
    steps: list[tuple] = []
    for p in parts:
        steps.append(("wild",) if p == "item" else ("key", p))
    return steps, _prefix_to_path(".".join(parts)) if parts else "$"


# ── XML outline (streaming) ─────────────────────────────────────────────


def xml_outline(path: Path, max_paths: int = 2000) -> dict[str, Any]:
    """Element paths with counts, attributes, text presence and examples, in one streaming pass."""
    from lxml import etree

    from _formats import magic_compression, open_decompressed, split_ext

    with open(path, "rb") as f:
        comp = magic_compression(f.read(8)) or split_ext(path)[1]
    stats: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    stack: list[str] = []
    nsmap: dict[str, str] = {}
    root_tag = None
    with open_decompressed(path, comp) as f:
        ctx = etree.iterparse(f, events=("start", "end", "start-ns"), resolve_entities=False, no_network=True, huge_tree=True, remove_comments=True)
        try:
            for ev, el in ctx:
                if ev == "start-ns":
                    p, uri = el
                    nsmap.setdefault(p or "", uri)
                    continue
                if ev == "start":
                    stack.append(qname(el))
                    key = "/" + "/".join(stack)
                    st = stats.get(key)
                    if st is None:
                        if len(stats) >= max_paths:
                            key = "/" + "/".join(stack[:-1]) + "/*"
                            st = stats.get(key)
                        if st is None:
                            st = stats[key] = {"count": 0, "attrs": Counter(), "text": 0, "examples": []}
                            order.append(key)
                    st["count"] += 1
                    for a in el.attrib:
                        st["attrs"][_attr_name(el, a)] += 1
                    if root_tag is None:
                        root_tag = stack[0]
                    continue
                key = "/" + "/".join(stack)
                st = stats.get(key) or stats.get("/" + "/".join(stack[:-1]) + "/*")
                txt = (el.text or "").strip()
                if txt and st is not None and not len(el):
                    st["text"] += 1
                    if len(st["examples"]) < 3 and txt[:60] not in st["examples"]:
                        st["examples"].append(txt[:60])
                stack.pop()
                if stack:  # done with it: drop it and its finished siblings (the root would keep a million rows)
                    el.clear(keep_tail=True)
                    parent = el.getparent()
                    if parent is not None:
                        while el.getprevious() is not None:
                            del parent[0]
        except etree.XMLSyntaxError as e:
            raise SkillError(f"{path.name}: invalid XML: {e}") from None
    return {"root": root_tag, "namespaces": nsmap, "paths": [{"path": k, "count": stats[k]["count"], "attrs": dict(stats[k]["attrs"]), "text": stats[k]["text"], "examples": stats[k]["examples"]} for k in order]}


def xml_outline_lines(res: dict[str, Any], depth: int = 8, max_keys: int = 60) -> list[str]:
    lines = []
    parent_counts: dict[str, int] = {}
    kids: Counter[str] = Counter()
    for item in res["paths"]:
        p = item["path"]
        level = p.count("/") - 1
        parent = p.rsplit("/", 1)[0]
        kids[parent] += 1
        if level > depth:
            continue
        if kids[parent] > max_keys:
            if kids[parent] == max_keys + 1:
                lines.append("  " * level + f"… more child elements under {parent} (raise --max-keys)")
            continue
        parent_counts[p] = item["count"]
        extra = []
        pc = parent_counts.get(parent)
        if pc and item["count"] != pc:
            per = item["count"] / pc
            extra.append((f"{per:,.0f}" if per >= 100 or per.is_integer() else f"{per:.3g}") + " per parent" if item["count"] > pc else f"in {item['count']:,}/{pc:,}")
        if item["attrs"]:
            extra.append("attrs " + ", ".join("@" + a for a in list(item["attrs"])[:8]))
        if item["text"]:
            extra.append("text e.g. " + ", ".join(json.dumps(x, ensure_ascii=False) for x in item["examples"][:3]))
        lines.append("  " * level + f"{p}  ×{item['count']}" + (f"  ({'; '.join(extra)})" if extra else ""))
    return lines


def doc_outline(path: Path, fmt: str, size: int, compression: str | None, depth: int = 8, max_keys: int = 60, big: int | None = None) -> dict[str, Any]:
    """Outline lines for a nested document; big JSON and XML are streamed once and the result cached."""
    import _cache
    from _formats import outline_bytes

    streamed = size >= (outline_bytes() if big is None else big)
    if fmt == "xml":
        res = _cache.cached_json(path, "xml-outline", {}, "1", lambda: xml_outline(path)) if streamed else xml_outline(path)
        return {"lines": xml_outline_lines(res, depth, max_keys), "streamed": streamed, "root": res["root"], "namespaces": res["namespaces"], "raw": res}
    if fmt == "json" and streamed:
        res = _cache.cached_json(path, "json-outline", {}, "1", lambda: stream_shape(path, compression))
        return {"lines": stream_outline_lines(res, depth, max_keys), "streamed": True, "raw": res}
    if fmt == "jsonl" and streamed:
        sh, n, sampled = jsonl_shape(path, compression)
        lines = list(outline_lines(sh, "$", depth, max_keys))
        return {"lines": lines, "streamed": True, "records": n, "sampled": sampled, "shape": sh}
    doc = load_doc(path, fmt)
    sh = shape_of(doc)
    return {"lines": list(outline_lines(sh, "$", depth, max_keys)), "streamed": False, "shape": sh, "doc": doc}


def jsonl_shape(path: Path, compression: str | None, sample: int = 20000) -> tuple[Shape, int, bool]:
    """Shape of a big JSONL file from its first `sample` records, plus the exact line count."""
    from _formats import open_decompressed

    sh = Shape()
    items = Shape()
    n = 0
    with open_decompressed(path, compression) as f:
        for raw in f:
            if not raw.strip():
                continue
            if n < sample:
                try:
                    items.add(json.loads(raw[3:] if raw[:3] == b"\xef\xbb\xbf" else raw))
                except ValueError:
                    items.add("<invalid JSON line>")
            n += 1
    sh.n = 1
    sh.types["array"] = 1
    sh.arrays = 1
    sh.items_total = n
    sh.items = items
    return sh, n, n > sample
