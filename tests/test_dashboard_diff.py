"""The update preview: what moving a dashboard from one set of data to another does to each
block. Pure functions over analysis tables, so the tables here are written by hand."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import dashboard_diff as dd  # noqa: E402

COLS = [{"name": "_study", "label": "Study", "type": "string", "scope": "system", "roles": ["label", "identifier", "dimension"]},
        {"name": "Design", "label": "Design", "type": "enum", "scope": "entry", "roles": ["dimension"]},
        {"name": "Metric", "label": "Metric class", "type": "enum", "scope": "row", "roles": ["dimension"]},
        {"name": "Human", "label": "Mean human", "type": "number", "scope": "row", "roles": ["measure"], "unit_by": "Metric"},
        {"name": "AI", "label": "Mean AI", "type": "number", "scope": "row", "roles": ["measure"], "unit_by": "Metric"}]


def table(rows: list[tuple]) -> dict:
    """rows: (paper index, design, metric, human, ai)"""
    papers = sorted({r[0] for r in rows})
    cols = []
    for k, c in enumerate(COLS):
        vals = [(f"S{r[0]}",) + r[1:] for r in rows]
        present = [v[k] for v in vals if v[k] not in (None, "")]
        cols.append({**c, "n": len(present), "distinct": len(set(map(str, present))), "options": None,
                     **({"min": min(present), "max": max(present)} if c["type"] == "number" and present else {})})
    return {"dataset": {"n_papers": len(papers), "credibility": {"tier": "ai_only", "label": "AI-only"}},
            "unit": {"id": "m", "label": "Measures", "level": "row"}, "units": [{"id": "m", "label": "Measures", "level": "row", "default": True}],
            "columns": cols, "papers": [{"study": f"S{p}"} for p in range(max(papers) + 1)],
            "records": [{"id": f"r{p}", "paper": p, "status": "unverified"} for p in range(max(papers) + 1)],
            "rows": [{"r": r[0], "p": f"m[{i}]", "v": [f"S{r[0]}", *r[1:]]} for i, r in enumerate(rows)]}


A = [(0, "Within", "Accuracy", 70, 60), (0, "Within", "Accuracy", 72, 61), (1, "Between", "Accuracy", 80, 75), (1, "Between", "Accuracy", 81, 70),
     (2, "Mixed", "Accuracy", 60, 66), (2, "Mixed", "Accuracy", 62, 64)]
SPEC = {"value_labels": {"Design": {"Within": "Within subjects", "Between": "Between subjects", "Mixed": "Mixed"}}, "blocks": [
    {"id": "sc", "template": "scatter", "title": "AI against human", "unit": "m",
     "bindings": {"x": {"column": "Human"}, "y": {"column": "AI"}, "color": {"column": "Design"}},
     "transform": {"filter": [{"column": "Metric", "op": "in", "value": ["Accuracy"]}]}},
    {"id": "n", "template": "stat_count", "title": "Studies", "unit": "m", "bindings": {"column": {"column": "_study"}}},
    {"id": "bar", "template": "bar", "title": "Rows by design", "unit": "m", "bindings": {"y": {"column": "Design"}}}]}


def _by(prev: dict) -> dict:
    return {b["id"]: b for b in prev["blocks"]}


def test_counts_and_unchanged() -> None:
    same = dd.preview(SPEC, {"m": table(A)}, "m", {"m": table(A)}, "m")
    assert same["summary"] == {"unchanged": 3, "changed": 0, "attention": 0, "broken": 0}
    more = dd.preview(SPEC, {"m": table(A)}, "m", {"m": table(A + [(3, "Within", "Accuracy", 90, 88), (3, "Within", "Accuracy", 91, 80)])}, "m")
    b = _by(more)
    assert b["sc"]["status"] == "changed" and b["sc"]["before"] == {"rows": 6, "studies": 3} and b["sc"]["after"] == {"rows": 8, "studies": 4}
    assert more["papers"] == [3, 4] and more["summary"]["changed"] == 3
    edited = dd.preview(SPEC, {"m": table(A)}, "m", {"m": table([(0, "Within", "Accuracy", 71, 60)] + A[1:])}, "m")
    assert _by(edited)["sc"] == {**_by(edited)["sc"], "status": "changed", "reasons": ["values were edited"]} and _by(edited)["n"]["status"] == "unchanged"


def test_what_needs_attention() -> None:
    # a fifth design: the colour slot takes 4, and the new value has no label
    grown = A + [(3, "Crossover", "Accuracy", 66, 65), (4, "Factorial", "Accuracy", 55, 50)]
    b = _by(dd.preview(SPEC, {"m": table(A)}, "m", {"m": table(grown)}, "m"))
    assert b["sc"]["status"] == "attention" and any("at most 4" in r for r in b["sc"]["reasons"])
    assert b["bar"]["status"] == "attention" and "new values without a label: Crossover, Factorial" in b["bar"]["reasons"][0]
    # a filter value that vanished; a metric that became several
    renamed = [(p, d, "Acc.", h, a) for p, d, _m, h, a in A]
    b = _by(dd.preview(SPEC, {"m": table(A)}, "m", {"m": table(renamed)}, "m"))
    assert b["sc"]["status"] == "broken"                                                  # nothing passes the filter any more
    nofilter = {**SPEC, "blocks": [{**SPEC["blocks"][0], "transform": {}}]}
    mixed = A + [(3, "Within", "Time", 300, 250), (3, "Within", "Time", 280, 240)]
    b = _by(dd.preview(nofilter, {"m": table(A)}, "m", {"m": table(mixed)}, "m"))
    assert b["sc"]["status"] == "attention" and any("now mixes metrics (Accuracy, Time)" in r for r in b["sc"]["reasons"])
    # proportions next to percentages
    prop = A + [(3, "Within", "Accuracy", 0.71, 0.64), (3, "Within", "Accuracy", 0.8, 0.7)]
    b = _by(dd.preview(SPEC, {"m": table(A)}, "m", {"m": table(prop)}, "m"))
    assert any("as a proportion (0–1)" in r for r in b["sc"]["reasons"])
    on = {**SPEC, "blocks": [{**SPEC["blocks"][0], "options": {"percent_scale": True}}]}
    assert not any("proportion" in r for r in _by(dd.preview(on, {"m": table(A)}, "m", {"m": table(prop)}, "m"))["sc"]["reasons"])


def test_a_removed_column_breaks_the_block() -> None:
    t = table(A); k = [c["name"] for c in t["columns"]].index("AI")
    t["columns"].pop(k)
    for row in t["rows"]:
        row["v"].pop(k)
    out = dd.preview(SPEC, {"m": table(A)}, "m", {"m": t}, "m")
    assert _by(out)["sc"]["status"] == "broken" and out["columns_removed"] == ["AI"] and _by(out)["n"]["status"] == "unchanged"


def test_proportion_papers_matches_the_javascript_twin() -> None:
    """The same vectors as static/dash/chart.js proportionPapers(): per study all values within
    0–1 (and not all whole numbers) = proportion; above 1 up to 100 = percentage; mixed only when both occur."""
    recs = [{"paper": p} for p in range(4)]
    rows = lambda vals: [{"r": p, "v": [v]} for p, v in vals]   # noqa: E731
    assert dd.proportion_papers(rows([(0, 0.5), (0, 0.9), (1, 55), (1, 80)]), recs, 0) == {0}
    assert dd.proportion_papers(rows([(0, 0.5), (1, 0.7)]), recs, 0) is None               # only proportions
    assert dd.proportion_papers(rows([(0, 55), (1, 80)]), recs, 0) is None                 # only percentages
    assert dd.proportion_papers(rows([(0, 0), (0, 1), (1, 80)]), recs, 0) is None          # 0/1 counts are not proportions
    assert dd.proportion_papers(rows([(0, 0.5), (1, 300)]), recs, 0) is None               # 300 is not a percentage
