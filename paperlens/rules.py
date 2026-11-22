"""Rules: deterministic checks a workflow declares in its own words, without code.

A check step with ``check: rules`` carries ``params.rules``, a list of rules over the preset's
fields. Each rule names a ``code``, a ``severity`` and the ``unit`` it reads — an entry (the
default), the paper, the rows of an entry's child table (``"<child>"``) or of a paper table
(``"paper.<table>"``) — and says either what must hold (``require``) or what is a finding when it
holds (``when``); ``where`` narrows the units it applies to. A condition is the review grammar's
``where`` form, extended: ``{<term>: value}`` is equality, ``{<term>: {op: arg, …}}`` combines
operators (``eq ne in not_in null not_null gt gte lt lte between matches``, ``tol`` beside a
numeric ``eq``/``ne``), several terms are AND, ``$all`` / ``$any`` / ``$not`` nest. A term is a
field of the unit, ``paper.<field>``, ``entry.<field>`` from a child row, or an aggregate over a
collection — ``sum(cells.value)``, ``count(cells)``, ``min``, ``max``, ``mean``, ``len(<field>)``.
An argument may refer to another term: ``{"lte": {"field": "ci_high"}}``.

A value that is missing makes a comparison undecided, never a finding: the ``schema`` check
already says what is required. Findings name the record and the cell, so they reach the review
card like every other check's.
"""
from __future__ import annotations

import math
import re
from typing import Any

OPS = ("eq", "ne", "in", "not_in", "null", "not_null", "gt", "gte", "lt", "lte", "between", "matches")
_MODIFIERS = ("tol",)
SEVERITIES = ("error", "warn", "info")
AGGS = ("sum", "count", "min", "max", "mean", "len")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_AGG = re.compile(r"^(sum|count|min|max|mean|len)\(([^()]+)\)$")
IDENTITY = ("title", "doi", "year", "journal", "authors", "abstract")


# ── the preset's shape, as the rules see it ───────────────────────────────────

def _entries(pspec: dict) -> dict:
    return pspec.get("entries") or {}


def entries_key(pspec: dict) -> str:
    return _entries(pspec).get("key") or "records"


def _fields(defs) -> dict[str, dict]:
    return {f["name"]: f for f in defs or [] if isinstance(f, dict) and f.get("name")}


def children(pspec: dict) -> dict[str, dict[str, dict]]:
    """child key → its columns (an entry field of type table counts as a child too)."""
    out = {c["key"]: _fields(c.get("fields")) for c in _entries(pspec).get("children") or [] if isinstance(c, dict) and c.get("key")}
    for f in _entries(pspec).get("fields") or []:
        if isinstance(f, dict) and f.get("type") == "table" and f.get("name") and f["name"] not in out:
            out[f["name"]] = _fields(f.get("columns"))
    return out


def paper_tables(pspec: dict) -> dict[str, dict[str, dict]]:
    return {f["name"]: _fields(f.get("columns")) for f in (pspec.get("paper") or {}).get("fields") or [] if isinstance(f, dict) and f.get("type") == "table" and f.get("name")}


def paper_fields(pspec: dict) -> dict[str, dict]:
    out = {n: {"name": n, "type": "list" if n == "authors" else "integer" if n == "year" else "string"} for n in IDENTITY}
    out.update({n: f for n, f in _fields((pspec.get("paper") or {}).get("fields")).items() if f.get("type") != "table"})
    return out


def entry_fields(pspec: dict) -> dict[str, dict]:
    return {n: f for n, f in _fields(_entries(pspec).get("fields")).items() if f.get("type") != "table"}


def units_of(pspec: dict) -> dict[str, dict[str, dict]]:
    """unit name → the fields a bare term may name there."""
    out = {"entry": entry_fields(pspec), "paper": paper_fields(pspec)}
    out.update({k: cols for k, cols in children(pspec).items()})
    out.update({f"paper.{t}": cols for t, cols in paper_tables(pspec).items()})
    return out


# ── validation ────────────────────────────────────────────────────────────────

def _term_ok(term: str, unit: str, pspec: dict) -> str | None:
    """None when the term resolves for the unit, else why not."""
    units = units_of(pspec)
    kids, tables, ekey = children(pspec), paper_tables(pspec), entries_key(pspec)
    m = _AGG.match(term)
    if m:
        agg, inner = m.group(1), m.group(2).strip()
        if agg == "len":
            return None if _term_ok(inner, unit, pspec) is None else f"len({inner}): {_term_ok(inner, unit, pspec)}"
        if inner.startswith("paper."):                                 # paper.<table>[.<column>]
            t, _, col = inner[len("paper."):].partition(".")
            coll = f"paper.{t}"
        else:
            coll, _, col = inner.partition(".")
        colls: dict[str, dict] = {}
        if unit == "entry":
            colls = dict(kids); colls.update({f"paper.{t}": c for t, c in tables.items()})
        elif unit == "paper":
            colls = {ekey: entry_fields(pspec)}; colls.update(tables); colls.update({f"paper.{t}": c for t, c in tables.items()})
        if coll not in colls:
            return f"{term}: {coll!r} is not a table the {unit} unit can sum over"
        if agg == "count":
            return None if not col or col in colls[coll] else f"{term}: {col!r} is not a column of {coll}"
        if not col:
            return f"{term}: {agg} needs a column ({coll}.<column>)"
        return None if col in colls[coll] else f"{term}: {col!r} is not a column of {coll}"
    if term.startswith("paper."):
        name = term[len("paper."):]
        if unit == "paper" and name in units["paper"]:
            return None
        return None if name in paper_fields(pspec) else f"{term}: {name!r} is not a paper field"
    if term.startswith("entry."):
        if unit in ("entry", "paper") or unit.startswith("paper."):
            return f"{term}: only a child row reads its entry"
        return None if term[len("entry."):] in entry_fields(pspec) else f"{term}: {term[6:]!r} is not an entry field"
    fields = units.get(unit) or {}
    return None if term in fields else f"{term!r} is not a field of the {unit} unit"


def _validate_cond(cond: Any, unit: str, pspec: dict, at: str, errs: list[str]) -> None:
    if not isinstance(cond, dict) or not cond:
        errs.append(f"{at}: a condition is an object {{<term>: value | {{op: arg}}}} or $all / $any / $not"); return
    for key, arg in cond.items():
        if key in ("$all", "$any"):
            if not isinstance(arg, list) or not arg:
                errs.append(f"{at}.{key} must list conditions"); continue
            for i, c in enumerate(arg):
                _validate_cond(c, unit, pspec, f"{at}.{key}[{i}]", errs)
            continue
        if key == "$not":
            _validate_cond(arg, unit, pspec, f"{at}.$not", errs); continue
        if not isinstance(key, str):
            errs.append(f"{at}: keys must be terms"); continue
        why = _term_ok(key, unit, pspec)
        if why:
            errs.append(f"{at}: {why}")
        if not isinstance(arg, dict):
            continue
        for op, val in arg.items():
            if op in _MODIFIERS:
                if not isinstance(val, (int, float)) or isinstance(val, bool) or val < 0:
                    errs.append(f"{at}.{key}.{op} must be a number ≥ 0")
                continue
            if op not in OPS:
                errs.append(f"{at}.{key}: unknown operator {op!r} (one of {', '.join(OPS)})"); continue
            if isinstance(val, dict) and "field" in val:
                if op in ("in", "not_in", "between", "matches", "null", "not_null"):
                    errs.append(f"{at}.{key}.{op}: a reference to another term fits eq, ne, gt, gte, lt, lte")
                elif not isinstance(val["field"], str) or _term_ok(val["field"], unit, pspec):
                    errs.append(f"{at}.{key}.{op}.field: {_term_ok(str(val.get('field')), unit, pspec) or 'must name a term'}")
            elif op in ("in", "not_in") and not isinstance(val, list):
                errs.append(f"{at}.{key}.{op} must be a list")
            elif op == "between" and not (isinstance(val, list) and len(val) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in val)):
                errs.append(f"{at}.{key}.between must be [low, high]")
            elif op == "matches":
                if not isinstance(val, str):
                    errs.append(f"{at}.{key}.matches must be a regular expression")
                else:
                    try:
                        re.compile(val)
                    except re.error as exc:
                        errs.append(f"{at}.{key}.matches: {exc}")
            elif op in ("null", "not_null") and not isinstance(val, bool):
                errs.append(f"{at}.{key}.{op} must be true or false")


def validate(rules: Any, pspec: dict | None) -> list[str]:
    """Every rule's shape and, with the preset at hand, every term against its unit's fields."""
    errs: list[str] = []
    if not isinstance(rules, list):
        return ["must be a list of rules"]
    seen: set[str] = set()
    for i, r in enumerate(rules):
        at = f"[{i}]"
        if not isinstance(r, dict):
            errs.append(f"{at} must be an object"); continue
        code = r.get("code")
        if not isinstance(code, str) or not _CODE.match(code):
            errs.append(f"{at}.code must be a short slug (a-z, 0-9, _)")
        elif code in seen:
            errs.append(f"{at}.code {code!r} is used twice")
        else:
            seen.add(code)
        if r.get("severity", "error") not in SEVERITIES:
            errs.append(f"{at}.severity must be one of {', '.join(SEVERITIES)}")
        if r.get("message") is not None and not isinstance(r["message"], str):
            errs.append(f"{at}.message must be a string")
        unit = r.get("unit", "entry")
        if not isinstance(unit, str):
            errs.append(f"{at}.unit must be a string"); unit = "entry"
        elif pspec is not None and unit not in units_of(pspec):
            errs.append(f"{at}.unit {unit!r} is not 'entry', 'paper', a child table or 'paper.<table>' of the preset")
        for k in r:
            if k not in ("code", "severity", "message", "unit", "where", "require", "when"):
                errs.append(f"{at}: unknown key {k!r}")
        has_req, has_when = r.get("require") is not None, r.get("when") is not None
        if has_req == has_when:
            errs.append(f"{at}: exactly one of `require` (what must hold) or `when` (what is a finding)")
        if pspec is None:
            continue
        if unit in units_of(pspec):
            for k in ("where", "require", "when"):
                if r.get(k) is not None:
                    _validate_cond(r[k], unit, pspec, f"{at}.{k}", errs)
    return errs


# ── evaluation ────────────────────────────────────────────────────────────────

def _num(x) -> float | None:
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x) if math.isfinite(float(x)) else None
    if isinstance(x, str):
        s = x.strip().replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _missing(v) -> bool:
    return v is None or v == "" or v == []


def _aggregate(agg: str, values: list) -> Any:
    if agg == "count":
        return len(values)
    if agg == "len":
        return None
    nums = [n for n in (_num(v) for v in values) if n is not None]
    if not nums:
        return None
    return {"sum": sum(nums), "min": min(nums), "max": max(nums), "mean": sum(nums) / len(nums)}[agg]


def _resolve(term: str, ctx: dict) -> Any:
    """The value of a term in a context: ``unit`` (the row), ``entry``, ``paper``, ``collections``
    (name → list of rows, for the aggregates)."""
    m = _AGG.match(term)
    if m:
        agg, inner = m.group(1), m.group(2).strip()
        if agg == "len":
            v = _resolve(inner, ctx)
            return len(v) if isinstance(v, (list, str, dict)) else None
        coll, _, col = inner.partition(".")
        if coll == "paper" and col:                                   # paper.<table>[.<column>]
            t, _, col = col.partition(".")
            coll = f"paper.{t}"
        rows = (ctx.get("collections") or {}).get(coll)
        if rows is None:
            return None
        if agg == "count" and not col:
            return len(rows)
        return _aggregate(agg, [r.get(col) if isinstance(r, dict) else None for r in rows])
    if term.startswith("paper."):
        return (ctx.get("paper") or {}).get(term[len("paper."):])
    if term.startswith("entry."):
        return (ctx.get("entry") or {}).get(term[len("entry."):])
    return (ctx.get("unit") or {}).get(term)


def _arg(val: Any, ctx: dict) -> Any:
    return _resolve(val["field"], ctx) if isinstance(val, dict) and "field" in val else val


def _compare(op: str, v: Any, arg: Any, tol: float) -> bool | None:
    """True, False, or None when the comparison cannot be decided (a value missing or not a number)."""
    if op == "null":
        return _missing(v) == bool(arg)
    if op == "not_null":
        return (not _missing(v)) == bool(arg)
    if _missing(v):
        return None
    if op in ("eq", "ne"):
        x, y = _num(v), _num(arg)
        same = (abs(x - y) <= tol) if x is not None and y is not None else (v == arg)
        return same if op == "eq" else not same
    if op == "in":
        return v in (arg or [])
    if op == "not_in":
        return v not in (arg or [])
    if op == "matches":
        return bool(re.search(arg, str(v))) if isinstance(arg, str) else None
    x = _num(v)
    if x is None:
        return None
    if op == "between":
        lo, hi = _num(arg[0]) if isinstance(arg, list) and len(arg) == 2 else None, _num(arg[1]) if isinstance(arg, list) and len(arg) == 2 else None
        return None if lo is None or hi is None else lo <= x <= hi
    y = _num(arg)
    if y is None:
        return None
    return {"gt": x > y, "gte": x >= y, "lt": x < y, "lte": x <= y}[op]


def holds(cond: dict, ctx: dict) -> bool | None:
    """Three-valued: True when every part holds, False when one fails, None when nothing decides."""
    results: list[bool | None] = []
    for key, arg in cond.items():
        if key == "$all":
            results.append(_combine([holds(c, ctx) for c in arg], all_=True))
        elif key == "$any":
            results.append(_combine([holds(c, ctx) for c in arg], all_=False))
        elif key == "$not":
            r = holds(arg, ctx); results.append(None if r is None else not r)
        else:
            v = _resolve(key, ctx)
            if not isinstance(arg, dict):
                results.append(None if _missing(v) and arg is not None else (v == arg))
                continue
            tol = float(arg.get("tol") or 0)
            for op, val in arg.items():
                if op in _MODIFIERS:
                    continue
                results.append(_compare(op, v, _arg(val, ctx), tol))
    return _combine(results, all_=True)


def _combine(rs: list[bool | None], *, all_: bool) -> bool | None:
    if all_:
        if any(r is False for r in rs):
            return False
        return None if any(r is None for r in rs) or not rs else True
    if any(r is True for r in rs):
        return True
    return None if any(r is None for r in rs) or not rs else False


def _describe(cond: dict, ctx: dict) -> str:
    parts = []
    for key, arg in cond.items():
        if key.startswith("$"):
            parts.append(key[1:] + "(…)"); continue
        v = _resolve(key, ctx)
        if not isinstance(arg, dict):
            parts.append(f"{key} = {v!r}, not {arg!r}"); continue
        for op, val in arg.items():
            if op in _MODIFIERS:
                continue
            ref = _arg(val, ctx)
            shown = f"{val['field']} ({ref!r})" if isinstance(val, dict) and "field" in val else repr(ref)
            parts.append(f"{key} = {v!r} {op} {shown}")
    return "; ".join(parts)


def _first_term(cond: dict) -> str | None:
    for key in cond:
        if not key.startswith("$") and not _AGG.match(key):
            return key
    return None


def _units(rule: dict, rec: dict | None, doc: dict, pspec: dict, recs: list[dict]) -> list[tuple[dict, str | None]]:
    """The contexts a rule reads in one record (or, for the paper, one document) with the path
    a finding names."""
    unit = rule.get("unit", "entry")
    paper = doc.get("paper") or {}
    tables = {f"paper.{t}": list(paper.get(t) or []) for t in paper_tables(pspec)}
    if unit == "paper":
        colls = {entries_key(pspec): [r["field_values"] for r in recs], **tables, **{t: rows for t, rows in ((k[len("paper."):], v) for k, v in tables.items())}}
        return [({"unit": paper, "paper": paper, "collections": colls}, None)]
    if unit.startswith("paper."):
        return [({"unit": row, "paper": paper, "collections": tables}, f"{unit}[{i}]") for i, row in enumerate(tables.get(unit) or []) if isinstance(row, dict)]
    fv = (rec or {}).get("field_values") or {}
    kids = {k: [r for r in (fv.get(k) or []) if isinstance(r, dict)] for k in children(pspec)}
    if unit == "entry":
        return [({"unit": fv, "entry": fv, "paper": paper, "collections": {**kids, **tables}}, None)]
    return [({"unit": row, "entry": fv, "paper": paper, "collections": tables}, f"{unit}[{i}]") for i, row in enumerate(kids.get(unit) or [])]


def check_rules(inputs: list[dict], params: dict) -> list[dict]:
    """The registry's entry point: every rule over every unit of every record (or document)."""
    src = inputs[0]
    pspec = src.get("spec") or {}
    rules = [r for r in (params.get("rules") or []) if isinstance(r, dict) and (r.get("require") is not None or r.get("when") is not None)]
    if not rules:
        return []
    by_doc: dict[str, list[dict]] = {}
    for r in src.get("records") or []:
        by_doc.setdefault(r["document_id"], []).append(r)
    out: list[dict] = []
    for did, recs in by_doc.items():
        recs.sort(key=lambda r: r["entry_index"])
        info = (src.get("documents") or {}).get(did) or {}
        pm = dict(info.get("paper_metadata") or {})
        for k in ("title", "doi"):
            pm.setdefault(k, ((recs[0].get("paper") or {}) if recs else {}).get(k))
        doc = {"paper": pm}
        for rule in rules:
            unit = rule.get("unit", "entry")
            targets = [(None, did)] if unit == "paper" or unit.startswith("paper.") else [(r, did) for r in recs]
            for rec, _d in targets:
                for ctx, row_path in _units(rule, rec, doc, pspec, recs):
                    if rule.get("where") is not None and holds(rule["where"], ctx) is not True:
                        continue
                    cond = rule.get("require") if rule.get("require") is not None else rule.get("when")
                    r_ = holds(cond, ctx)
                    fired = (r_ is False) if rule.get("require") is not None else (r_ is True)
                    if not fired:
                        continue
                    term = _first_term(cond)
                    path = (f"{row_path}.{term}" if row_path and term else row_path or (term if unit == "entry" else (term if term.startswith("paper.") else f"paper.{term}") if term and unit == "paper" else None))
                    out.append({"record_id": rec["id"] if rec else None, "document_id": did, "path": path, "code": rule["code"],
                                "severity": rule.get("severity") or "error",
                                "message": (rule.get("message") or f"{rule['code']}: {_describe(cond, ctx)}")[:500],
                                "detail": {"rule": rule["code"], "unit": unit, "values": {k: _resolve(k, ctx) for k in cond if not k.startswith("$")}}})
    return out


# ── what a builder may propose ────────────────────────────────────────────────

_PAIRS = (("_low", "_high"), ("_lower", "_upper"), ("_min", "_max"), ("_start", "_end"), ("_from", "_to"))
_UNIT_INTERVAL = re.compile(r"(^|_)(p|p_value|pvalue|p_val|share|proportion|fraction|rate|prob|probability)$")
_COUNTS = re.compile(r"(^|_)(n|n_obs|nobs|obs|observations|sample_size|participants|n_participants|n_subjects|count)$")


def suggest(pspec: dict) -> list[dict]:
    """Rules the field names and types suggest, for the builder to offer — never applied by
    themselves: an ordered pair (``ci_low ≤ ci_high``), a probability or share in [0, 1], a
    count at least 1, a year within reason. Only for fields the preset gives no ``range``."""
    out: list[dict] = []
    for unit, fields in units_of(pspec).items():
        nums = {n: f for n, f in fields.items() if f.get("type") in ("number", "integer") and not (unit == "paper" and n in IDENTITY)}
        for lo_s, hi_s in _PAIRS:
            for n in nums:
                if n.endswith(lo_s):
                    stem = n[: -len(lo_s)]; hi = stem + hi_s
                    if hi in nums:
                        out.append({"code": f"{stem or 'range'}_order".strip("_"), "severity": "error", "unit": unit,
                                    "require": {n: {"lte": {"field": hi}}}, "message": f"{n} must not exceed {hi}"})
        for n, f in nums.items():
            if f.get("range"):
                continue
            if _UNIT_INTERVAL.search(n):
                out.append({"code": f"{n}_in_unit_interval", "severity": "error", "unit": unit, "require": {n: {"between": [0, 1]}},
                            "message": f"{n} lies between 0 and 1"})
            elif _COUNTS.search(n):
                out.append({"code": f"{n}_positive", "severity": "warn", "unit": unit, "require": {n: {"gte": 1}}, "message": f"{n} is at least 1"})
            elif n == "year" or n.endswith("_year"):
                out.append({"code": f"{n}_plausible", "severity": "warn", "unit": unit, "require": {n: {"between": [1900, 2100]}},
                            "message": f"{n} is a plausible year"})
    seen: set[str] = set(); uniq = []
    for r in out:
        if r["code"] in seen:
            r["code"] = f"{r['code']}_{r['unit'].replace('.', '_')}"
        seen.add(r["code"]); uniq.append(r)
    return uniq
