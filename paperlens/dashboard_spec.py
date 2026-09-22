"""The dashboard grammar: building blocks (the TEMPLATE REGISTRY), the validator that every
dashboard passes through — whether a model proposed it, a person edited it or we generated
it — and the deterministic default dashboard that needs no model at all.

    dashboard = {grammar, dataset_id, questions[{id,text}], blocks[…], unanswered[{question,reason}],
                 filters{verified_only, exclude_flagged}, theme{vibe}}
    block     = {id, type: figure|table|stat, template, title, answers[question ids],
                 main_message, unit, bindings{slot: {column | columns, agg?}},
                 transform{filter[{column, op, value}], sort{by, dir}, limit}, options{…},
                 sufficiency{status, n_rows, reason}, caveats[], origin: llm|user|default, layout{w}}

A template declares SLOTS: which columns it needs, of which type and role (the roles come
from ``analysis_table.catalogue``). The registry is served to the browser verbatim, so the
icons, the slot editors and this validator all read the same declaration. ``validate`` never
raises: it repairs what it can (a misspelt template, a column given by its label), drops what
it cannot, and says what it did — a proposal is a draft for a person to review, not a contract.
``sufficiency`` is always computed here from the data, never taken from the model.
"""
from __future__ import annotations

import difflib
import re
from typing import Any

GRAMMAR = 1
REGISTRY_VERSION = 2
MAX_BLOCKS = 12
AGGS = ("count", "mean", "median", "sum", "min", "max")
FILTER_OPS = ("eq", "ne", "in", "gt", "gte", "lt", "lte", "between", "not_null")
_NUM = ["number", "integer"]


def _slot(id_, label, *, required=False, types=None, roles=None, agg=None, multi=False, default=None, max_distinct=None, help_=""):
    return {"id": id_, "label": label, "required": required, "types": types, "roles": roles, "agg": agg,
            "multi": multi, "default": default, "max_distinct": max_distinct, "help": help_}


_LABEL = _slot("label", "Label", roles=["label", "identifier", "dimension"], multi=True, default=["_study"],
               help_="what names a row: the study, optionally with what tells its rows apart")
# 4 = the colours of the series palette that stay distinguishable in ANY pairing (theme.css)
_COLOR = _slot("color", "Colour", roles=["dimension"], max_distinct=4, help_="a category with at most 4 values")

REGISTRY: list[dict] = [
    {"id": "forest", "type": "figure", "label": "Forest plot", "icon": "forest", "mode": "rows", "min_rows": 2,
     "description": "One line per row: an estimate with its interval, around a reference line. Use for effect sizes per study or condition.",
     "slots": [_slot("x", "Estimate", required=True, types=_NUM, roles=["measure"]),
               _slot("lower", "Lower bound", types=_NUM, roles=["bound", "measure"]),
               _slot("upper", "Upper bound", types=_NUM, roles=["bound", "measure"]), _LABEL, _COLOR],
     "options": [{"id": "reference_line", "type": "number", "default": 0, "label": "Reference line"},
                 {"id": "max_rows", "type": "integer", "default": 60, "label": "Rows shown"}]},
    {"id": "scatter", "type": "figure", "label": "Scatter plot", "icon": "scatter", "mode": "rows", "min_rows": 3,
     "description": "One point per row: two measures against each other. Use to compare two quantities (e.g. human+AI against human alone).",
     "slots": [_slot("x", "Horizontal", required=True, types=_NUM, roles=["measure", "bound"]),
               _slot("y", "Vertical", required=True, types=_NUM, roles=["measure", "bound"]), _COLOR, _LABEL],
     "options": [{"id": "diagonal", "type": "boolean", "default": False, "label": "Draw the y = x line (same scale on both axes)"},
                 {"id": "log_x", "type": "boolean", "default": False, "label": "Log scale (horizontal)"}, {"id": "log_y", "type": "boolean", "default": False, "label": "Log scale (vertical)"}, {"id": "percent_scale", "type": "boolean", "default": False, "label": "Show proportions as percentages", "help": "for studies that report 0–1 where others report 0–100"}]},
    {"id": "dot_strip", "type": "figure", "label": "Dot strip", "icon": "dot_strip", "mode": "rows", "min_rows": 2,
     "description": "One dot per row, a measure spread out by a category. Use for the distribution of a value per group (loadings by item, effect sizes by task type).",
     "slots": [_slot("x", "Value", required=True, types=_NUM, roles=["measure", "bound"]),
               _slot("y", "Group", required=True, roles=["dimension", "identifier"], max_distinct=40), _COLOR, _LABEL],
     "options": [{"id": "reference_line", "type": "number", "default": None, "label": "Reference line"}, {"id": "log_x", "type": "boolean", "default": False, "label": "Log scale (horizontal)"}, {"id": "percent_scale", "type": "boolean", "default": False, "label": "Show proportions as percentages", "help": "for studies that report 0–1 where others report 0–100"}]},
    {"id": "bar", "type": "figure", "label": "Bar chart", "icon": "bar", "mode": "groups", "min_rows": 1,
     "description": "One bar per category: a count, or an aggregate of a measure. Use for 'how many' and 'how large on average, by group'.",
     "slots": [_slot("y", "Category", required=True, roles=["dimension", "identifier"], max_distinct=30),
               _slot("x", "Value", types=_NUM, roles=["measure", "bound"], agg="mean", help_="leave empty to count rows"), _COLOR],
     "options": [{"id": "max_bars", "type": "integer", "default": 20, "label": "Bars shown"}, {"id": "percent_scale", "type": "boolean", "default": False, "label": "Show proportions as percentages", "help": "for studies that report 0–1 where others report 0–100"}]},
    {"id": "histogram", "type": "figure", "label": "Histogram", "icon": "histogram", "mode": "groups", "min_rows": 5,
     "description": "The distribution of one measure in bins. Use for 'how are the values spread'.",
     "slots": [_slot("x", "Value", required=True, types=_NUM, roles=["measure", "bound"])],
     "options": [{"id": "bins", "type": "integer", "default": 20, "label": "Bins"}, {"id": "percent_scale", "type": "boolean", "default": False, "label": "Show proportions as percentages", "help": "for studies that report 0–1 where others report 0–100"}]},
    {"id": "heatmap", "type": "figure", "label": "Heat map", "icon": "heatmap", "mode": "groups", "min_rows": 2,
     "description": "A grid of two categories with an aggregated value in each cell. Use for item-by-factor loadings or factor-by-factor correlations.",
     "slots": [_slot("x", "Columns", required=True, roles=["dimension", "identifier"], max_distinct=40),
               _slot("y", "Rows", required=True, roles=["dimension", "identifier"], max_distinct=60),
               _slot("value", "Cell value", required=True, types=_NUM, roles=["measure", "bound"], agg="mean")],
     "options": []},
    {"id": "summary_table", "type": "table", "label": "Summary table", "icon": "summary_table", "mode": "groups", "min_rows": 1,
     "description": "Per category: number of rows and studies, mean, median, minimum and maximum of a measure.",
     "slots": [_slot("group", "Group by", required=True, roles=["dimension", "identifier"], max_distinct=60),
               _slot("value", "Measure", required=True, types=_NUM, roles=["measure", "bound"])],
     "options": [{"id": "percent_scale", "type": "boolean", "default": False, "label": "Show proportions as percentages", "help": "for studies that report 0–1 where others report 0–100"}]},
    {"id": "rows_table", "type": "table", "label": "Rows", "icon": "rows_table", "mode": "rows", "min_rows": 1,
     "description": "The rows themselves with chosen columns; every cell traces to its evidence. Use as the data behind the figures.",
     "slots": [_slot("columns", "Columns", multi=True, help_="leave empty for a sensible selection")],
     "options": [{"id": "max_rows", "type": "integer", "default": 100, "label": "Rows shown"}]},
    {"id": "stat_count", "type": "stat", "label": "Count (how many)", "icon": "stat", "mode": "groups", "min_rows": 0,
     "description": "One number that COUNTS: how many rows, or how many different values a column has (e.g. how many studies, how many experiments). Ids are counted per paper, so two papers that both have an experiment \"1\" give two.",
     "slots": [_slot("column", "Distinct values of", roles=["dimension", "identifier", "label"], help_="leave empty to count rows")],
     "options": [{"id": "note", "type": "string", "default": "", "label": "Caption under the number", "inline": True, "help": "e.g. “distinct papers”; empty = written from the column names"}]},
    {"id": "stat_aggregate", "type": "stat", "label": "Statistic of a measure (mean, median …)", "icon": "stat", "mode": "groups", "min_rows": 1,
     "description": "One number that SUMMARISES a numeric column: its mean, median, sum, minimum or maximum (e.g. the median sample size).",
     "slots": [_slot("value", "Measure", required=True, types=_NUM, roles=["measure", "bound"], agg="median")],
     "options": [{"id": "percent_scale", "type": "boolean", "default": False, "label": "Show proportions as percentages", "help": "for studies that report 0–1 where others report 0–100"}, {"id": "note", "type": "string", "default": "", "label": "Caption under the number", "inline": True, "help": "e.g. “distinct papers”; empty = written from the column names"}]},
    {"id": "stat_share", "type": "stat", "label": "Share (percent of rows)", "icon": "stat", "mode": "groups", "min_rows": 1,
     "description": "One percentage: the share of rows where a column has a given value (e.g. verified rows).",
     "slots": [_slot("column", "Column", required=True, roles=["dimension"])],
     "options": [{"id": "equals", "type": "string", "default": "verified", "label": "Value"}, {"id": "note", "type": "string", "default": "", "label": "Caption under the number", "inline": True, "help": "e.g. “distinct papers”; empty = written from the column names"}]},
]
_BY_ID = {t["id"]: t for t in REGISTRY}
_SYNONYMS = {"forest_plot": "forest", "forestplot": "forest", "coefplot": "forest", "scatterplot": "scatter", "scatter_plot": "scatter",
             "strip": "dot_strip", "dotplot": "dot_strip", "dot_plot": "dot_strip", "stripplot": "dot_strip", "bar_chart": "bar",
             "barchart": "bar", "column": "bar", "hist": "histogram", "heat_map": "heatmap", "matrix": "heatmap", "table": "rows_table",
             "raw_table": "rows_table", "summary": "summary_table", "count": "stat_count", "kpi": "stat_aggregate", "stat": "stat_aggregate",
             "share": "stat_share", "percentage": "stat_share"}


def registry() -> dict:
    return {"version": REGISTRY_VERSION, "grammar": GRAMMAR, "templates": REGISTRY, "aggs": list(AGGS), "filter_ops": list(FILTER_OPS),
            "vibes": VIBES, "fonts": FONTS}


# ── validation ────────────────────────────────────────────────────────────────
def _cols(table: dict) -> dict[str, dict]:
    return {c["name"]: c for c in table.get("columns") or []}


def _find_column(name: Any, cols: dict[str, dict]) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    if name in cols:
        return name
    low = {k.lower(): k for k in cols}
    if name.lower() in low:
        return low[name.lower()]
    by_label = {str(c.get("label") or "").lower(): k for k, c in cols.items()}
    if name.lower() in by_label:
        return by_label[name.lower()]
    close = difflib.get_close_matches(name.lower(), list(low) + list(by_label), n=1, cutoff=0.85)
    return (low.get(close[0]) or by_label.get(close[0])) if close else None


def _fits(col: dict, slot: dict) -> str | None:
    """None when the column fits the slot, else why not."""
    if slot.get("types") and col.get("type") not in slot["types"]:
        return f"{col['label']} is {col.get('type')}, the slot needs {' or '.join(slot['types'])}"
    if slot.get("roles") and not set(col.get("roles") or []) & set(slot["roles"]):
        return f"{col['label']} cannot serve as {' / '.join(slot['roles'])}"
    if slot.get("max_distinct") and (col.get("distinct") or 0) > slot["max_distinct"]:
        return f"{col['label']} has {col.get('distinct')} distinct values (at most {slot['max_distinct']})"
    if not col.get("n"):
        return f"{col['label']} is empty in this dataset"
    return None


def _rows_matching(table: dict, filters: list[dict], need: list[str]) -> int:
    idx = {c["name"]: k for k, c in enumerate(table.get("columns") or [])}
    n = 0
    for row in table.get("rows") or []:
        v = row["v"]
        get = lambda name: v[idx[name]] if name in idx else None   # noqa: E731
        if all(get(c) not in (None, "") for c in need) and all(_passes(get(f["column"]), f) for f in filters):
            n += 1
    return n


def _passes(a: Any, f: dict) -> bool:
    op, b = f["op"], f.get("value")
    same = lambda x, y: x == y or str(x) == str(y)   # noqa: E731
    try:
        if op == "eq": return same(a, b)
        if op == "ne": return not same(a, b)
        if op == "in": return any(same(a, x) for x in (b if isinstance(b, list) else [b]))
        if op == "not_null": return a not in (None, "")
        if a is None: return False
        if op == "gt": return a > b
        if op == "gte": return a >= b
        if op == "lt": return a < b
        if op == "lte": return a <= b
        if op == "between": return isinstance(b, list) and len(b) == 2 and b[0] <= a <= b[1]
    except TypeError:
        return False
    return True


def _slug(s: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(s or "block").lower()).strip("-")[:24] or "block"
    out, k = base, 2
    while out in taken:
        out, k = f"{base}-{k}", k + 1
    taken.add(out)
    return out


# ── the look of a dashboard: a vibe (palette + surfaces, theme.css), a font, own colours ─────
VIBES = [{"id": "clean", "label": "Clean", "description": "the platform look: white cards, a bright palette"},
         {"id": "journal", "label": "Journal", "description": "print-like: serif type, muted ink colours, flat cards"},
         {"id": "warm", "label": "Warm", "description": "paper background, earthy colours"},
         {"id": "night", "label": "Night", "description": "dark surface, luminous colours"}]
FONTS = [{"id": "sans", "label": "Sans (default)"}, {"id": "serif", "label": "Serif"},
         {"id": "system", "label": "System"}, {"id": "mono", "label": "Monospace"}]
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def clean_theme(theme: Any) -> dict:
    """{vibe, font, colors{mark, series[≤8]}}: ids from the lists above, colours as #rrggbb.
    Anything else is dropped (the values end up in a style attribute)."""
    theme = theme if isinstance(theme, dict) else {}
    colors = theme.get("colors") if isinstance(theme.get("colors"), dict) else {}
    series = [c.lower() if isinstance(c, str) and _HEX.match(c) else None for c in (colors.get("series") or [])[:8]] if isinstance(colors.get("series"), list) else []
    while series and series[-1] is None:
        series.pop()
    mark = colors.get("mark").lower() if isinstance(colors.get("mark"), str) and _HEX.match(colors["mark"]) else None
    return {"vibe": theme.get("vibe") if theme.get("vibe") in {v["id"] for v in VIBES} else None,
            "font": theme.get("font") if theme.get("font") in {f["id"] for f in FONTS} else None,
            "colors": {"mark": mark, "series": series} if (mark or series) else {}}


def _option(decl: dict, value: Any) -> Any:
    """A block option in its declared type, else the default."""
    try:
        if value is None or isinstance(value, (dict, list)):
            return decl["default"]
        if decl["type"] == "string":
            return re.sub(r"\s+", " ", str(value)).strip()[:120]
        if decl["type"] == "boolean":
            return bool(value)
        return int(value) if decl["type"] == "integer" else float(value)
    except (TypeError, ValueError):
        return decl["default"]


def clean_column_labels(labels: Any, tables: dict[str, dict]) -> dict:
    """{column name: the name the dashboard shows for it} (the user renames variables: "Paper
    title" → "Paper"). Display only, for columns that exist."""
    known = {c["name"]: c.get("label") for t in tables.values() for c in t["columns"]}
    out = {}
    for name, lab in (labels.items() if isinstance(labels, dict) else []):
        lab = re.sub(r"\s+", " ", str(lab)).strip()[:60] if isinstance(lab, (str, int, float)) else ""
        if name in known and lab and lab != known[name] and len(out) < 80:
            out[name] = lab
    return out


def clean_value_labels(labels: Any, tables: dict[str, dict]) -> dict:
    """{column: {coded value: readable label}} for columns that exist; how "advice_first" is
    written on an axis, in a legend or a table. Display only: filters keep the coded values."""
    known = {c["name"] for t in tables.values() for c in t["columns"]}
    out: dict[str, dict] = {}
    for col, m in (labels.items() if isinstance(labels, dict) else []):
        if col not in known or not isinstance(m, dict) or len(out) >= 40:
            continue
        pairs = {str(v)[:120]: re.sub(r"\s+", " ", str(lab)).strip()[:60] for v, lab in list(m.items())[:60]
                 if isinstance(lab, (str, int, float)) and str(lab).strip()}
        pairs = {v: lab for v, lab in pairs.items() if lab != v}
        if pairs:
            out[col] = pairs
    return out


def validate(spec: Any, tables: dict[str, dict], *, default_unit: str | None = None, origin: str | None = None) -> tuple[dict, dict]:
    """(clean dashboard, report{dropped[], repairs[], unanswered[]}). ``tables`` maps unit id →
    the built analysis table (``analysis_table.build``). Never raises."""
    report: dict[str, list] = {"dropped": [], "repairs": [], "unanswered": []}
    spec = spec if isinstance(spec, dict) else {}
    questions = []
    for k, q in enumerate(spec.get("questions") or []):
        text = q.get("text") if isinstance(q, dict) else q
        if isinstance(text, str) and text.strip():
            questions.append({"id": (q.get("id") if isinstance(q, dict) and q.get("id") else f"q{k + 1}"), "text": text.strip()[:400]})
    qids = {q["id"] for q in questions}
    taken: set[str] = set()
    blocks = []
    default_unit = default_unit if default_unit in tables else next(iter(tables), None)

    for k, b in enumerate(spec.get("blocks") or []):
        if not isinstance(b, dict):
            report["dropped"].append({"index": k, "reason": "not an object"}); continue
        if b.get("type") == "summary":
            report["dropped"].append({"index": k, "reason": "written summaries are not available yet"}); continue
        tid = str(b.get("template") or "").strip().lower().replace(" ", "_").replace("-", "_")
        if tid not in _BY_ID:
            guess = _SYNONYMS.get(tid) or next(iter(difflib.get_close_matches(tid, list(_BY_ID), n=1, cutoff=0.7)), None)
            if not guess:
                report["dropped"].append({"index": k, "reason": f"unknown template {b.get('template')!r}"}); continue
            report["repairs"].append(f"block {k + 1}: template {b.get('template')!r} read as {guess!r}"); tid = guess
        tpl = _BY_ID[tid]
        bindings_in = b.get("bindings") if isinstance(b.get("bindings"), dict) else {}
        named = [x for v in bindings_in.values() if isinstance(v, dict) for x in ([v.get("column")] + list(v.get("columns") or []))]
        unit = b.get("unit") if b.get("unit") in tables else None
        if unit is None:                                    # the unit that knows most of the bound columns
            unit = max(tables, key=lambda u: sum(1 for n in named if _find_column(n, _cols(tables[u]))), default=default_unit) if tables else None
            if b.get("unit"):
                report["repairs"].append(f"block {k + 1}: unit {b.get('unit')!r} read as {unit!r}")
        if unit is None:
            report["dropped"].append({"index": k, "reason": "no data"}); continue
        table, cols = tables[unit], _cols(tables[unit])

        bindings, problems = {}, []
        for slot in tpl["slots"]:
            given = bindings_in.get(slot["id"])
            given = {"column": given} if isinstance(given, str) else given if isinstance(given, dict) else {}
            names = list(given.get("columns") or []) if slot["multi"] else []
            if given.get("column"):
                names = names + [given["column"]] if slot["multi"] else [given["column"]]
            found = []
            for n in names:
                real = _find_column(n, cols)
                why = _fits(cols[real], slot) if real else f"no column {n!r}"
                if real and not why:
                    if real != n:
                        report["repairs"].append(f"block {k + 1}: column {n!r} read as {real!r}")
                    found.append(real)
                else:
                    (problems if slot["required"] else report["repairs"]).append(
                        f"{slot['label']}: {why}" if slot["required"] else f"block {k + 1}: {slot['label']} dropped ({why})")
            if slot["multi"]:
                found = found or [c for c in (slot["default"] or []) if c in cols]
                if found:
                    bindings[slot["id"]] = {"columns": list(dict.fromkeys(found))}
            elif found:
                bind = {"column": found[0]}
                if slot.get("agg"):
                    bind["agg"] = given.get("agg") if given.get("agg") in AGGS else slot["agg"]
                bindings[slot["id"]] = bind
            elif slot["required"] and not problems:
                problems.append(f"{slot['label']} is not chosen")

        filters = []
        tr = b.get("transform") if isinstance(b.get("transform"), dict) else {}
        for f in tr.get("filter") or []:
            real = _find_column((f or {}).get("column"), cols) if isinstance(f, dict) else None
            if real and f.get("op") in FILTER_OPS:
                val = f.get("value")
                opts = {str(o.get("label")).lower(): o.get("value") for o in cols[real].get("options") or [] if isinstance(o, dict)}
                fix = lambda x: opts.get(str(x).lower(), x)   # noqa: E731  (a label given for an enum value)
                filters.append({"column": real, "op": f["op"], "value": [fix(x) for x in val] if isinstance(val, list) else fix(val)})
            elif isinstance(f, dict):
                report["repairs"].append(f"block {k + 1}: a filter on {f.get('column')!r} was dropped")
        sort = tr.get("sort") if isinstance(tr.get("sort"), dict) else None
        transform = {"filter": filters}
        if sort and sort.get("by"):
            transform["sort"] = {"by": str(sort["by"]), "dir": "asc" if sort.get("dir") == "asc" else "desc"}
        if isinstance(tr.get("limit"), int) and tr["limit"] > 0:
            transform["limit"] = tr["limit"]

        need = [v["column"] for s, v in bindings.items() if "column" in v and next(x for x in tpl["slots"] if x["id"] == s)["required"]]
        n_rows = _rows_matching(table, filters, need) if not problems else 0
        if problems:
            status, reason = "insufficient", "; ".join(problems)
        elif n_rows < tpl["min_rows"]:
            status, reason = "insufficient", f"only {n_rows} row{'s' if n_rows != 1 else ''} with these values (needs {tpl['min_rows']})"
        elif n_rows < 3 * max(1, tpl["min_rows"]) and tpl["type"] == "figure":
            status, reason = "partial", f"{n_rows} rows: read with care"
        else:
            status, reason = "ok", ""
        model_reason = (b.get("sufficiency") or {}).get("reason") if isinstance(b.get("sufficiency"), dict) else None
        opts_in = b.get("options") if isinstance(b.get("options"), dict) else {}
        options = {o["id"]: _option(o, opts_in.get(o["id"])) for o in tpl["options"]}
        blocks.append({
            "id": _slug(b.get("id") or b.get("title") or tid, taken), "type": tpl["type"], "template": tid,
            "title": str(b.get("title") or tpl["label"])[:160],
            "answers": [a for a in (b.get("answers") or []) if a in qids],
            "main_message": re.sub(r"\s+", " ", str(b.get("main_message") or ""))[:400],
            "unit": unit, "bindings": bindings, "transform": transform, "options": options,
            "sufficiency": {"status": status, "n_rows": n_rows, "reason": reason or (str(model_reason)[:200] if model_reason and status != "ok" else "")},
            "caveats": [str(c)[:240] for c in (b.get("caveats") or []) if isinstance(c, str) and c.strip()][:4],
            "origin": b.get("origin") if b.get("origin") in ("llm", "user", "default") else (origin or "user"),
            "layout": {"w": 2 if (b.get("layout") or {}).get("w") == 2 or tid in ("forest", "rows_table", "heatmap", "summary_table") else 1},
        })
        if len(blocks) >= MAX_BLOCKS:
            break

    answered = {a for b in blocks if b["sufficiency"]["status"] != "insufficient" for a in b["answers"]}
    given = {u.get("question"): u.get("reason") for u in spec.get("unanswered") or [] if isinstance(u, dict)}
    report["unanswered"] = [{"question": q["id"], "reason": str(given.get(q["id"]) or "no block answers this question")[:240]}
                            for q in questions if q["id"] not in answered]
    f_in = spec.get("filters") if isinstance(spec.get("filters"), dict) else {}
    clean = {"grammar": GRAMMAR, "dataset_id": spec.get("dataset_id"),
             "title": re.sub(r"\s+", " ", str(spec.get("title") or "")).strip()[:120],     # proposed; the user may rename
             "questions": questions, "blocks": blocks,
             "unanswered": report["unanswered"],
             "filters": {"verified_only": bool(f_in.get("verified_only")), "exclude_flagged": bool(f_in.get("exclude_flagged"))},
             "value_labels": clean_value_labels(spec.get("value_labels"), tables),
             "column_labels": clean_column_labels(spec.get("column_labels"), tables),
             "context": str(spec.get("context") or "").strip()[:2000],      # the user's notes to the planner
             "theme": clean_theme(spec.get("theme"))}
    return clean, report


# ── the default dashboard: no model needed ───────────────────────────────────
def default_dashboard(tables: dict[str, dict], default_unit: str, *, preset_default: list | None = None) -> dict:
    """A sensible first dashboard from the column roles alone: key numbers, a forest plot per
    derived effect size, a scatter of the first two measures, a bar of the first category, the
    rows. A preset may ship its own block list (``display.analysis.default_dashboard``)."""
    if preset_default:
        return {"blocks": preset_default}
    t = tables[default_unit]
    cols = [c for c in t["columns"] if c.get("n")]
    role = lambda r: [c for c in cols if r in (c.get("roles") or [])]   # noqa: E731
    tell = [c["name"] for c in cols if c["scope"] in ("child", "row") and c["type"] == "string" and c.get("distinct", 0) > 1][:2]
    label = {"columns": ["_study"] + tell}
    blocks: list[dict] = [
        {"template": "stat_count", "title": "Studies", "unit": default_unit, "bindings": {"column": {"column": "_study"}}},
        {"template": "stat_count", "title": t["unit"]["label"], "unit": default_unit, "bindings": {}},
        {"template": "stat_share", "title": "Rows verified by a human", "unit": default_unit,
         "bindings": {"column": {"column": "_status"}}, "options": {"equals": "verified"}},
    ]
    mains = [c for c in cols if c.get("derived") and c["derived"]["output"] not in ("lo", "hi")]
    for c in mains[:2]:
        blocks.append({"template": "forest", "title": c["label"], "unit": default_unit,
                       "main_message": "Each line is one estimate with its 95% interval, computed from the extracted values.",
                       "bindings": {"x": {"column": c["name"]}, "lower": {"column": f"{c['name']}_lo"}, "upper": {"column": f"{c['name']}_hi"}, "label": label},
                       "transform": {"filter": [{"column": c["name"], "op": "not_null"}], "sort": {"by": "x", "dir": "desc"}}})
    names = {c["name"] for c in cols}
    if not mains and {"es", "es_ci_lo", "es_ci_hi"} <= names:
        blocks.append({"template": "forest", "title": "Effect sizes with 95% intervals", "unit": default_unit,
                       "main_message": "Correlations with intervals computed from r and n (Fisher's z).",
                       "bindings": {"x": {"column": "es"}, "lower": {"column": "es_ci_lo"}, "upper": {"column": "es_ci_hi"}, "label": label},
                       "transform": {"filter": [{"column": "es_ci_lo", "op": "not_null"}], "sort": {"by": "x", "dir": "desc"}}})
    # the measures worth a default figure: the row's own values first (a loading, a mean), real
    # numbers before counts; sample sizes (N, n, N_Human …) are context, not the finding
    depth = {"row": 0, "child": 1, "entry": 2}
    is_n = lambda c: bool(re.match(r"^n($|_)", c["name"], re.I))   # noqa: E731
    measures = sorted((c for c in role("measure") if c["scope"] not in ("system", "derived") and not is_n(c)),
                      key=lambda c: (depth.get(c["scope"], 3), c["type"] != "number"))
    dims = [c for c in role("dimension") if c["scope"] != "system" and 2 <= c.get("distinct", 0) <= 12]
    idents = [c for c in cols if "identifier" in (c.get("roles") or []) and "dimension" in (c.get("roles") or []) and c["scope"] == "row"]
    grid = idents[:1] + [c for c in cols if c["scope"] == "row" and "dimension" in (c.get("roles") or []) and c not in idents[:1]]
    if measures and idents and len(grid) >= 2:                # a matrix: item × factor, factor × factor
        blocks.append({"template": "heatmap", "title": f"{measures[0]['label']} by {grid[0]['label']} and {grid[1]['label']}", "unit": default_unit,
                       "bindings": {"y": {"column": grid[0]["name"]}, "x": {"column": grid[1]["name"]}, "value": {"column": measures[0]["name"], "agg": "mean"}}})
    # measures whose meaning differs per row are only comparable within one metric: the default
    # figures keep the most frequent one (and say so in the title)
    def one_metric(c: dict) -> tuple[list, str]:
        by = c.get("unit_by")
        if not by or by not in names:
            return [], ""
        idx = [k for k, x in enumerate(t["columns"]) if x["name"] in (by, c["name"])]
        bi, ci = (idx[0], idx[1]) if t["columns"][idx[0]]["name"] == by else (idx[1], idx[0])
        seen: dict = {}
        for row in t.get("rows") or []:
            if row["v"][ci] is not None and row["v"][bi] not in (None, ""):
                seen[row["v"][bi]] = seen.get(row["v"][bi], 0) + 1
        if len(seen) < 2:
            return [], ""
        top = max(seen, key=seen.get)
        return [{"column": by, "op": "in", "value": [top]}], f" ({top})"

    if measures and (idents or dims):
        g = (idents or dims)[0]
        flt, suffix = one_metric(measures[0])
        blocks.append({"template": "dot_strip", "title": f"{measures[0]['label']} by {g['label']}{suffix}", "unit": default_unit,
                       "transform": {"filter": flt},
                       "bindings": {"x": {"column": measures[0]["name"]}, "y": {"column": g["name"]}, "label": label}})
    if len(measures) >= 2 and not idents:
        a, c2 = measures[0], measures[1]
        same_scale = a.get("min") is not None and c2.get("min") is not None and a["min"] <= c2["max"] and c2["min"] <= a["max"]
        flt, suffix = one_metric(measures[0])
        blocks.append({"template": "scatter", "title": f"{measures[1]['label']} against {measures[0]['label']}{suffix}", "unit": default_unit,
                       "options": {"diagonal": bool(same_scale or flt)}, "transform": {"filter": flt},
                       "bindings": {"x": {"column": measures[0]["name"]}, "y": {"column": measures[1]["name"]},
                                    **({"color": {"column": dims[0]["name"]}} if dims and dims[0].get("distinct", 99) <= 4 else {}), "label": label}})
    if dims:
        blocks.append({"template": "bar", "title": f"Rows by {dims[0]['label']}", "unit": default_unit, "bindings": {"y": {"column": dims[0]["name"]}}})
    if measures:
        flt, suffix = one_metric(measures[0])
        blocks.append({"template": "histogram", "title": f"Distribution of {measures[0]['label']}{suffix}", "unit": default_unit,
                       "transform": {"filter": flt}, "bindings": {"x": {"column": measures[0]["name"]}}})
    blocks.append({"template": "rows_table", "title": f"{t['unit']['label']}: the rows", "unit": default_unit, "bindings": {}})
    return {"blocks": blocks}


# ── the proposal prompt ───────────────────────────────────────────────────────
_EXAMPLE = """{"title": "Does AI assistance improve human decisions?", "blocks": [
  {"template": "forest", "title": "Effect of AI assistance per study", "answers": ["q1"],
   "main_message": "Most estimates lie to the right of zero: assistance tends to help.",
   "unit": "conditions.measures",
   "bindings": {"x": {"column": "g_team_vs_human"}, "lower": {"column": "g_team_vs_human_lo"},
                "upper": {"column": "g_team_vs_human_hi"}, "label": {"columns": ["_study", "Condition_Name"]}},
   "transform": {"filter": [{"column": "g_team_vs_human", "op": "not_null"}], "sort": {"by": "x", "dir": "desc"}},
   "caveats": ["Performance metrics differ between studies."]},
  {"template": "bar", "title": "Studies per task type", "answers": ["q2"], "main_message": "Which tasks the evidence covers.",
   "unit": "entries", "bindings": {"y": {"column": "Task_Type"}}}],
 "value_labels": {"Task_Type": {"clin_diag": "Clinical diagnosis", "legal_qa": "Legal questions"}},
 "unanswered": [{"question": "q3", "reason": "no column records participants' experience"}]}"""


def _column_line(c: dict) -> str:
    bits = [c["name"], c.get("label") or "", c.get("type") or "", "/".join(c.get("roles") or []) or "-", f"n={c.get('n', 0)}", f"distinct={c.get('distinct', 0)}"]
    if c.get("min") is not None:
        bits.append(f"range {c['min']:g}..{c['max']:g}")
    if c.get("options"):
        # a coded value comes with what the codebook says it means, so it can be given a short label
        def _opt(o) -> str:                                  # options are plain values or {value, label}
            if not isinstance(o, dict):
                return str(o)
            meaning = str(o.get("label") or "")
            return str(o.get("value")) + (f" (= {meaning[:70]})" if meaning and meaning != str(o.get("value")) else "")
        bits.append("values: " + ", ".join(_opt(o) for o in c["options"][:12]))
    elif c.get("samples") and c.get("type") not in _NUM:
        bits.append("e.g. " + "; ".join(str(x)[:40] for x in c["samples"][:3]))
    if c.get("derived"):
        bits.append("COMPUTED: " + c["derived"]["text"])
    if c.get("unit_by"):
        bits.append(f"MEANING DIFFERS PER ROW, named by column {c['unit_by']}: only comparable within one value of it")
    return " | ".join(bits)


def build_prompt(questions: list[dict], tables: dict[str, dict], default_unit: str, *, previous: dict | None = None,
                 feedback: str | None = None, keep: list[str] | None = None, context: str | None = None) -> str:
    """Questions + the columns of every row unit + the building blocks → a prompt that asks for
    a dashboard in our grammar. Sample VALUES are included (five rows per unit) so the model
    can tell what a column holds; nothing else of the dataset leaves."""
    import json
    parts = ["You design a dashboard that answers research questions from a dataset extracted from scientific papers.",
             "Return EXACTLY ONE JSON object and nothing else.", "", "# QUESTIONS"]
    parts += [f"{q['id']}: {q['text']}" for q in questions] or ["(none given: propose an overview of the dataset)"]
    if (context or "").strip():
        parts += ["", "# CONTEXT AND REQUESTS FROM THE USER (background, audience, what to emphasise or leave out; follow it where the data allows)",
                  context.strip()[:2000]]
    parts += ["", "# DATA", f"The dataset can be laid out in these row units; the default is {default_unit!r}. A block uses ONE unit.",
              "Columns are listed as: name | label | type | roles | n filled | distinct values | details."]
    for uid, t in tables.items():
        cols = [c for c in t["columns"] if c.get("n") and (c.get("roles") or c["name"] == "_status")]
        parts += ["", f"## unit {uid!r}: one row per {t['unit']['label']} ({t['n_rows']} rows)"] + [_column_line(c) for c in cols]
        idx = [k for k, c in enumerate(t["columns"]) if c in cols][:14]
        for row in (t.get("rows") or [])[:5]:
            parts.append("  sample: " + json.dumps({t["columns"][k]["name"]: row["v"][k] for k in idx if row["v"][k] not in (None, "")}, ensure_ascii=False)[:420])
    parts += ["", "# BUILDING BLOCKS (templates). Slots marked * are required; a slot accepts columns of the named types/roles."]
    for tpl in REGISTRY:
        slots = ", ".join(f"{s['id']}{'*' if s['required'] else ''} ({'/'.join(s['types'] or s['roles'] or ['any'])}"
                          f"{', aggregated: ' + '|'.join(AGGS) if s.get('agg') else ''}{', several columns' if s['multi'] else ''})" for s in tpl["slots"])
        parts.append(f"- {tpl['id']} [{tpl['type']}]: {tpl['description']} Slots: {slots}")
    parts += ["", "# RULES",
              "- Use ONLY the templates and the column NAMES listed above; never invent a column.",
              "- Each block answers one or more questions (\"answers\": [question ids]) and states in \"main_message\" what a reader should take from it. The message describes the intent; it contains NO numbers (you have not seen the results).",
              "- Prefer per-row templates (forest, scatter, dot_strip) where they fit: every point then shows its own evidence.",
              "- Use computed columns (marked COMPUTED) for effect sizes with intervals; bind their _lo and _hi columns to lower and upper.",
              f"- Filters use ops {', '.join(FILTER_OPS)} on listed columns, e.g. to keep one performance metric when metrics differ.",
              "- If the data cannot answer a question, do not force a block: list the question under \"unanswered\" with the reason.",
              "- Give the dashboard a \"title\": at most 10 words, naming the subject the questions are about (not \"Dashboard\", no numbers, no results).",
              "- \"value_labels\": for every categorical column you bind whose values are codes (snake_case, abbreviations, e.g. advice_first), give each value a short human-readable label (at most 4 words, e.g. \"AI advice first\"). Keys are the exact values as listed; never merge or rename categories, never label numbers.",
              "- Start with two or three key numbers (stat_count, stat_share, stat_aggregate), then the figures, then one rows_table. At most 8 blocks.",
              "- A column marked MEANING DIFFERS PER ROW mixes metrics (accuracy, time, scores …). When a block plots it, add a filter on the named column to keep ONE metric (say which in the title), or use a computed standardised column instead. Never aggregate it across metrics.",
              "- Add a short caveat to a block when its values are not comparable across studies.",
              "", "# OUTPUT (shape and an example)",
              '{"title", "blocks": [{"template", "title", "answers": [], "main_message", "unit", "bindings": {slot: {"column": name, "agg"?: …} | {"columns": [names]}}, '
              '"transform": {"filter": [{"column", "op", "value"}], "sort": {"by": slot, "dir": "asc|desc"}}, "options": {}, "caveats": []}], '
              '"value_labels": {column name: {value: label}}, "unanswered": [{"question": id, "reason"}]}', "", _EXAMPLE]
    if previous:
        kept = [b for b in previous.get("blocks") or [] if b.get("id") in (keep or [])]
        parts += ["", "# REVISION", "This is your earlier proposal. Revise it according to the feedback.",
                  json.dumps({"blocks": [{k: b.get(k) for k in ("id", "template", "title", "answers", "unit", "bindings")} for b in previous.get("blocks") or []]}, ensure_ascii=False)[:6000],
                  f"Feedback from the user: {feedback or '(none)'}"]
        if kept:
            parts.append("These blocks are LOCKED and will be kept as they are; do not repeat them: " + ", ".join(b["id"] for b in kept))
    return "\n".join(parts)
