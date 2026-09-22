"""What moving a dashboard from one set of data to another would do, block by block.

The owner of a published dashboard sees this before updating it to a newer dataset release:
for every block the rows and studies before → after, and a verdict —

  broken           the block cannot be drawn any more (a column is gone, too little data);
  needs attention  it draws, but something the author settled no longer fits: a colour slot that
                   now has too many categories, a filter value that vanished, a new category
                   without a label, proportions next to percentages, one metric become several;
  changed          the numbers behind it changed;
  unchanged.

Everything is computed from the spec and the two sets of analysis tables; nothing is drawn.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import dashboard_spec

_ORDER = {"unchanged": 0, "changed": 1, "attention": 2, "broken": 3}
_NUM = ("number", "integer")


def _idx(table: dict) -> dict[str, int]:
    return {c["name"]: k for k, c in enumerate(table.get("columns") or [])}


def _columns_of(block: dict) -> list[str]:
    out: list[str] = []
    for b in (block.get("bindings") or {}).values():
        if isinstance(b, dict):
            out += [b["column"]] if b.get("column") else []
            out += [c for c in b.get("columns") or [] if isinstance(c, str)]
    return list(dict.fromkeys(out))


def _block_rows(table: dict, block: dict, dash_filters: dict) -> list[dict]:
    """The rows a block draws: its required columns present, its filters and the dashboard's
    filters applied (the same rule the validator counts with)."""
    tpl = dashboard_spec._BY_ID.get(block.get("template")) or {"slots": []}   # noqa: SLF001
    required = {s["id"] for s in tpl["slots"] if s.get("required")}
    need = [b["column"] for s, b in (block.get("bindings") or {}).items() if s in required and isinstance(b, dict) and b.get("column")]
    filters = (block.get("transform") or {}).get("filter") or []
    idx, recs = _idx(table), table.get("records") or []
    out = []
    for row in table.get("rows") or []:
        get = lambda name: row["v"][idx[name]] if name in idx else None   # noqa: E731
        status = recs[row["r"]]["status"] if row["r"] < len(recs) else None
        if dash_filters.get("verified_only") and status != "verified":
            continue
        if dash_filters.get("exclude_flagged") and status == "flagged":
            continue
        if all(get(c) not in (None, "") for c in need) and all(dashboard_spec._passes(get(f["column"]), f) for f in filters):   # noqa: SLF001
            out.append(row)
    return out


def _facts(table: dict | None, block: dict | None, dash_filters: dict) -> dict | None:
    if table is None or block is None:
        return None
    rows, idx, recs = _block_rows(table, block, dash_filters), _idx(table), table.get("records") or []
    cols = [c for c in _columns_of(block) if c in idx]
    values = [[row["v"][idx[c]] for c in cols] for row in rows]
    digest = hashlib.sha256(json.dumps(sorted(values, key=lambda v: json.dumps(v, default=str)), default=str).encode("utf-8")).hexdigest()
    return {"n_rows": len(rows), "n_studies": len({recs[r["r"]]["paper"] for r in rows if r["r"] < len(recs)}), "digest": digest,
            "rows": rows, "idx": idx}


# the Python twin of proportionPapers() in static/dash/chart.js (kept in step by a shared-vector test)
def proportion_papers(rows: list[dict], records: list[dict], k: int) -> set | None:
    """Studies that report a quantity within 0–1 next to studies that report it on 0–100: the
    papers (indices) on the proportion scale, or None when the scales are not mixed."""
    by: dict[int, list[float]] = {}
    for row in rows:
        v = row["v"][k]
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            by.setdefault(records[row["r"]]["paper"], []).append(v)
    prop, pct = set(), 0
    for paper, vals in by.items():
        lo, hi = min(vals), max(vals)
        if lo >= 0 and hi <= 1 and any(float(v) != int(v) for v in vals):
            prop.add(paper)
        elif lo >= 0 and 1 < hi <= 100:
            pct += 1
    return prop if prop and pct else None


def _distinct(facts: dict, column: str) -> set[str]:
    k = facts["idx"].get(column)
    return set() if k is None else {str(r["v"][k]) for r in facts["rows"] if r["v"][k] not in (None, "")}


def _attention(block: dict, table_b: dict, a: dict | None, b: dict, value_labels: dict) -> list[str]:
    why: list[str] = []
    cols_b = {c["name"]: c for c in table_b.get("columns") or []}
    label = lambda n: (cols_b.get(n) or {}).get("label") or n   # noqa: E731
    # a filter that names values which no longer occur
    for f in (block.get("transform") or {}).get("filter") or []:
        if f.get("op") in ("eq", "in") and f.get("column") in cols_b:
            k = b["idx"][f["column"]]
            present = {str(r["v"][k]) for r in table_b.get("rows") or [] if r["v"][k] not in (None, "")}
            gone = [str(x) for x in (f["value"] if isinstance(f.get("value"), list) else [f.get("value")]) if str(x) not in present]
            if gone:
                why.append(f"the filter on {label(f['column'])} names {', '.join(gone[:3])}, which no longer occurs")
    for slot, bnd in (block.get("bindings") or {}).items():
        col = cols_b.get((bnd or {}).get("column")) if isinstance(bnd, dict) else None
        if not col:
            continue
        name = col["name"]
        if "dimension" in (col.get("roles") or []) and name in value_labels and a is not None:
            fresh = sorted(_distinct(b, name) - _distinct(a, name) - set(value_labels[name]))
            if fresh:
                why.append(f"{label(name)} has new values without a label: {', '.join(fresh[:4])}")
        if col.get("type") in _NUM and not col.get("derived") and "percent_scale" in (block.get("options") or {}) \
                and not (block.get("options") or {}).get("percent_scale"):
            mixed = proportion_papers(b["rows"], table_b.get("records") or [], b["idx"][name])
            before = proportion_papers(a["rows"], a["table"].get("records") or [], a["idx"][name]) if a and name in a["idx"] else None
            if mixed and not before:
                why.append(f"{label(name)}: some studies now report it as a proportion (0–1) next to others on 0–100")
        by = col.get("unit_by")
        if by and by in b["idx"] and a is not None and by in a["idx"] and len(_distinct(a, by)) == 1 and len(_distinct(b, by)) > 1:
            why.append(f"{label(name)} now mixes metrics ({', '.join(sorted(_distinct(b, by))[:3])})")
    cap = (block.get("options") or {}).get("max_rows") or (block.get("options") or {}).get("max_bars")
    if isinstance(cap, int) and a is not None and a["n_rows"] <= cap < b["n_rows"] and block.get("template") == "rows_table":
        why.append(f"more rows ({b['n_rows']}) than the table shows ({cap})")
    return why


def preview(spec: dict, tables_a: dict[str, dict], default_a: str, tables_b: dict[str, dict], default_b: str, *,
            changes: dict | None = None) -> dict:
    """``spec`` drawn over A (what the public page shows now) and over B (what it would show)."""
    clean_a, rep_a = dashboard_spec.validate(spec, tables_a, default_unit=default_a)
    clean_b, rep_b = dashboard_spec.validate(spec, tables_b, default_unit=default_b)
    a_by = {b["id"]: b for b in clean_a["blocks"]}
    b_by = {b["id"]: b for b in clean_b["blocks"]}
    dash_filters = clean_b.get("filters") or {}
    labels = clean_b.get("value_labels") or {}
    repairs_a = set(rep_a["repairs"])
    blocks = []
    for bid in list(dict.fromkeys([b["id"] for b in clean_a["blocks"]] + [b["id"] for b in clean_b["blocks"]])):
        ba, bb = a_by.get(bid), b_by.get(bid)
        fa = _facts(tables_a.get((ba or {}).get("unit")), ba, dash_filters)
        if fa is not None:
            fa["table"] = tables_a[ba["unit"]]
        fb = _facts(tables_b.get((bb or {}).get("unit")), bb, dash_filters)
        status, reasons = "unchanged", []
        ok_a = ba is not None and ba["sufficiency"]["status"] != "insufficient"
        if bb is None:
            status, reasons = "broken", ["the block cannot be built from the new data"]
        elif bb["sufficiency"]["status"] == "insufficient" and ok_a:
            status, reasons = "broken", [bb["sufficiency"]["reason"] or "not enough data"]
        else:
            k = clean_b["blocks"].index(bb) + 1
            fresh = [r.split(": ", 1)[-1] for r in rep_b["repairs"] if r.startswith(f"block {k}:") and r not in repairs_a]
            reasons = fresh + _attention(bb, tables_b[bb["unit"]], fa if ok_a else None, fb, labels)
            if reasons:
                status = "attention"
            elif fa is None or (fa["n_rows"], fa["n_studies"]) != (fb["n_rows"], fb["n_studies"]):
                status = "changed"
            elif fa["digest"] != fb["digest"]:
                status, reasons = "changed", ["values were edited"]
        ref = bb or ba
        blocks.append({"id": bid, "title": ref.get("title"), "template": ref.get("template"), "type": ref.get("type"), "status": status,
                       "reasons": reasons, "before": {"rows": fa["n_rows"], "studies": fa["n_studies"]} if fa else None,
                       "after": {"rows": fb["n_rows"], "studies": fb["n_studies"]} if fb else None})
    qa = {u["question"] for u in clean_a.get("unanswered") or []}
    qb = {u["question"] for u in clean_b.get("unanswered") or []}
    text = {q["id"]: q["text"] for q in clean_b.get("questions") or []}
    da, db = tables_a[default_a]["dataset"], tables_b[default_b]["dataset"]
    cols_a = {c["name"] for t in tables_a.values() for c in t["columns"]}
    cols_b = {c["name"] for t in tables_b.values() for c in t["columns"]}
    summary: dict[str, Any] = {s: sum(1 for b in blocks if b["status"] == s) for s in _ORDER}
    return {"blocks": blocks, "summary": summary, "changes": changes,
            "papers": [da.get("n_papers"), db.get("n_papers")], "credibility": [da.get("credibility"), db.get("credibility")],
            "columns_removed": sorted(cols_a - cols_b), "columns_added": sorted(cols_b - cols_a),
            "questions": {"newly_unanswered": [text.get(q, q) for q in qb - qa], "newly_answered": [text.get(q, q) for q in qa - qb]}}
